"""Rewards inputs, chunk B (docs/plans/rewards-inputs.md sections 3.1, 6 and 8):
balance entries, app programs, the overlay rules for both, the routes, and the
safety rules on them.

Fake samples only. Household today is 2026-10-20. The samples' Harbor Miles
balance is 42,000 as of Oct 1 (given to the chat), Lantern Hotels 12,000 as of
Jun 30 (stale), Pine Cash Back 37.42 from a statement, and Shared Vacation
Club is a timeshare.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date

import pytest
from api_helpers import (
    ADMIN,
    ADMIN_PW,
    PROD_ORIGIN,
    PROD_URL,
    add_users,
    app_for,
    import_samples,
    login,
)
from bad_cases import INJECTED_VALUES, PRIVATE_RULES_EXAMPLE
from fastapi.testclient import TestClient

from app import config
from app.config import Settings
from app.db.connect import connect, read_connection, write_connection, write_transaction
from app.importer.models import KEY_MAX, KEY_PATTERN
from app.importer.sanitize import load_private_rules, scan_text
from app.logging import JsonFormatter
from app.main import walk_routes
from app.services import rewards_inputs as ri
from app.services import rewards_overlay as ov
from app.services import trips
from app.services.balances import clear_cache
from app.services.overrides import EditError, check_text
from app.state import readers

TODAY = date(2026, 10, 20)
M = 1_000_000
H = {"Origin": PROD_ORIGIN}
NOW = "2026-10-20T12:00:00+00:00"
NEWER_RUN = "2026-10-19"
PHONE = INJECTED_VALUES[6]
REWARD_TABLES = (
    "reward_balances",
    "reward_app_programs",
    "reward_benefit_edits",
    "reward_match_terms",
    "reward_credit_decisions",
    "reward_cycle_marks",
)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def on(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(config, "household_today", lambda: day)
    clear_cache()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def entry(key: str, balance: int | str, as_of: str | None = None) -> dict:
    e = {"program_key": key, "balance": str(balance)}
    return e | ({"as_of": as_of} if as_of else {})


def post_balances(c: TestClient, *entries: dict):
    return c.post("/api/rewards/balances", json={"entries": list(entries)}, headers=H)


def add_program(c: TestClient, **body):
    return c.post("/api/rewards/programs", json=body, headers=H)


def page(c: TestClient) -> dict:
    r = c.get("/api/rewards")
    assert r.status_code == 200
    return r.json()


def prog(body: dict, key: str) -> dict:
    return next(p for p in body["programs"] if p["key"] == key)


def history(c: TestClient, key: str) -> dict:
    r = c.get(f"/api/rewards/programs/{key}/balances")
    assert r.status_code == 200, r.text
    return r.json()


def q(settings: Settings, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with read_connection(settings.db_path) as conn:
        return conn.execute(sql, params).fetchall()


def events(settings: Settings) -> list[sqlite3.Row]:
    return q(
        settings,
        "SELECT entity, entity_key, detail_json FROM events WHERE kind = 'rewards_edit' "
        "ORDER BY id",
    )


def chat_rewards(settings: Settings) -> dict:
    with read_connection(settings.db_path) as conn:
        r = readers.rewards(readers.monthly_runs(conn))
    assert r is not None
    return r.model_dump(mode="json", exclude_none=True)


def add_monthly_run(settings: Settings, run_id: str, **sections: object) -> None:
    """A newer stored monthly run with only these sections (like test_rewards)."""
    with write_connection(settings.db_path) as w:
        w.execute(
            """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at,
                   schema_version)
               VALUES (?, ?, 1, 'monthly', 'imported', '2026-10-20T00:00:00+00:00', '1.3')""",
            (run_id, run_id[:10]),
        )
        for name, value in sections.items():
            w.execute(
                "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, ?, ?)",
                (run_id, name, f"{run_id}-{name}".ljust(64, "0")[:64], json.dumps(value)),
            )


def send_programs(settings: Settings, change) -> None:
    """The chat's next run: the same rewards with `change` applied to the programs."""
    data = chat_rewards(settings)
    data["programs"] = sorted(change(data["programs"]), key=lambda p: p["key"])
    add_monthly_run(settings, NEWER_RUN, rewards=data)


def balance_id(c: TestClient, key: str, as_of: str) -> int:
    return next(e["id"] for e in history(c, key)["entries"] if e["as_of"] == as_of)


STATEMENT = {"source": "statement"}


def orphan(key: str, reason: str, unit: str) -> dict:
    return {"kind": "program", "key": key, "holds": ["balances"], "reason": reason, "unit": unit}


def units(settings: Settings, key: str) -> list[tuple[str, str]]:
    """(as_of, unit) of a key's entries, oldest first."""
    rows = q(
        settings,
        "SELECT as_of, unit FROM reward_balances WHERE program_key = ? ORDER BY as_of",
        (key,),
    )
    return [tuple(r) for r in rows]


# --------------------------------------------------------------------------
# The new page fields
# --------------------------------------------------------------------------


def test_new_fields_with_no_app_rows(samples: Settings, admin: TestClient) -> None:
    body = page(admin)
    assert body["notices"] == [] and body["orphans"] == []
    assert {p["origin"] for p in body["programs"]} == {"chat"}
    assert {p["chat_balance_micro"] for p in body["programs"]} == {None}
    sources = {p["key"]: p["source"] for p in body["programs"]}
    assert sources == {
        "harbor-miles": "manual",
        "lantern-hotels": "manual",
        "pine-cash-back": "statement",
        "shared-vacation-club": None,
        "summit-points": "statement",
    }


def test_no_rewards_before_a_13_run(admin: TestClient) -> None:
    body = page(admin)
    assert body["source_run_id"] is None
    assert body["notices"] == [] and body["orphans"] == []
    r = admin.get("/api/rewards/programs/harbor-miles/balances")
    assert r.status_code == 404


def test_a_failed_overlay_shows_its_notice(
    samples: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(chat, inputs, meta) -> dict:
        data = chat.model_dump(mode="json", exclude_none=True)
        data["programs"][0]["balance"] = "not a number"
        return data

    monkeypatch.setattr(ov, "_merge", broken)
    body = page(admin)
    assert body["notices"] == [
        {
            "kind": "overlay_failed",
            "text": "Your edits could not be shown. Nothing was lost.",
            "key": None,
            "other_key": None,
            "fields": [],
        }
    ]
    assert prog(body, "harbor-miles")["balance_micro"] == 42_000 * M  # the chat's view


# --------------------------------------------------------------------------
# Balances: which one wins
# --------------------------------------------------------------------------


def test_a_newer_app_entry_wins_and_every_reader_reads_it(
    samples: Settings, admin: TestClient
) -> None:
    before = page(admin)
    r = post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15"))
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["saved"] == 1 and [p["key"] for p in saved["programs"]] == ["harbor-miles"]
    assert saved["programs"][0]["source"] == "app"

    body = page(admin)
    harbor = prog(body, "harbor-miles")
    assert harbor["balance_micro"] == 52_000 * M and harbor["as_of"] == "2026-10-15"
    assert harbor["source"] == "app" and harbor["origin"] == "chat"
    assert harbor["chat_balance_micro"] is None and harbor["stale"] is False
    # the value range: 1.0 to 1.4 cents a mile
    assert (harbor["value_low_cents"], harbor["value_high_cents"]) == (52_000, 72_800)
    row = next(x for x in body["value_table"]["rows"] if x["key"] == "harbor-miles")
    assert (row["low_cents"], row["high_cents"], row["balance_micro"]) == (
        52_000,
        72_800,
        52_000 * M,
    )
    old = before["value_table"]
    assert body["value_table"]["low_cents"] == old["low_cents"] + 10_000
    assert body["value_table"]["high_cents"] == old["high_cents"] + 14_000
    assert body["tiles"]["value_low_cents"] == before["tiles"]["value_low_cents"] + 10_000
    # the redemption "have" line: 52,000 is enough for the 50,000 flights
    ski = next(x for x in body["redemptions"] if x["key"] == "ski-flights")
    assert ski["have_micro"] == 52_000 * M and ski["enough"] is True
    # everything else is as before
    assert prog(body, "summit-points") == prog(before, "summit-points")
    # the model keeps the chat's words: provenance is in meta only
    with read_connection(samples.db_path) as conn:
        eff = ov.effective_rewards(conn, readers.monthly_runs(conn))
    assert eff is not None
    model = next(p for p in eff.rewards.programs if p.key == "harbor-miles")
    assert (model.balance, model.as_of, model.source) == (str(52_000), "2026-10-15", "manual")
    chat = next(p for p in eff.chat.programs if p.key == "harbor-miles")
    assert (chat.balance, chat.source) == (str(42_000), "manual")
    assert eff.meta.provenance == {("balance", "harbor-miles"): "app"}

    h = history(admin, "harbor-miles")
    assert [(e["as_of"], e["balance_micro"], e["shown"]) for e in h["entries"]] == [
        ("2026-10-15", 52_000 * M, True)
    ]
    assert h["chat"] == {"balance_micro": 42_000 * M, "as_of": "2026-10-01", "source": "manual"}
    assert h["program"]["source"] == "app"


def test_stale_and_checks_stale_read_the_winner(samples: Settings, admin: TestClient) -> None:
    def stale_keys() -> set[str]:
        return {s["key"] for s in admin.get("/api/checks").json()["rewards"]["stale"]}

    assert prog(page(admin), "lantern-hotels")["stale"] is True
    assert stale_keys() == {"lantern-hotels"}
    assert post_balances(admin, entry("lantern-hotels", 12_500, "2026-10-10")).status_code == 201
    assert prog(page(admin), "lantern-hotels")["stale"] is False
    assert stale_keys() == set()
    # an app program with an old entry is stale on both
    key = add_program(admin, name="Lantern Air", kind="miles").json()["key"]
    assert post_balances(admin, entry(key, 9_000, "2026-06-01")).status_code == 201
    assert prog(page(admin), key)["stale"] is True
    stale = admin.get("/api/checks").json()["rewards"]["stale"]
    assert {"key": key, "name": "Lantern Air", "what": "balance", "as_of": "2026-06-01"} in stale


def test_an_older_entry_loses_and_a_newer_chat_run_wins(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 41_000, "2026-09-15")).status_code == 201
    harbor = prog(page(admin), "harbor-miles")
    assert (harbor["balance_micro"], harbor["source"]) == (42_000 * M, "manual")
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    assert prog(page(admin), "harbor-miles")["source"] == "app"

    def newer(programs: list[dict]) -> list[dict]:
        harbor = next(p for p in programs if p["key"] == "harbor-miles")
        harbor |= {"balance": str(45_000), "as_of": "2026-10-18", "source": "statement"}
        return programs

    send_programs(samples, newer)
    body = page(admin)
    assert body["source_run_id"] == NEWER_RUN
    harbor = prog(body, "harbor-miles")
    assert (harbor["balance_micro"], harbor["as_of"], harbor["source"]) == (
        45_000 * M,
        "2026-10-18",
        "statement",
    )
    assert harbor["chat_balance_micro"] is None
    h = history(admin, "harbor-miles")
    assert [(e["as_of"], e["shown"]) for e in h["entries"]] == [
        ("2026-10-15", False),
        ("2026-09-15", False),
    ]
    assert h["chat"]["balance_micro"] == 45_000 * M


def test_the_same_day_goes_to_the_app_with_the_chats_number(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 43_000, "2026-10-01")).status_code == 201
    harbor = prog(page(admin), "harbor-miles")
    assert (harbor["balance_micro"], harbor["source"]) == (43_000 * M, "app")
    assert harbor["chat_balance_micro"] == 42_000 * M
    # the same number on the same day: no note. The second entry replaces the first.
    assert post_balances(admin, entry("harbor-miles", 42_000, "2026-10-01")).status_code == 201
    harbor = prog(page(admin), "harbor-miles")
    assert (harbor["source"], harbor["chat_balance_micro"]) == ("app", None)
    assert [e["shown"] for e in history(admin, "harbor-miles")["entries"]] == [True]
    assert [json.loads(e["detail_json"])["action"] for e in events(samples)] == ["add", "replace"]


def test_delete_falls_back_to_the_chat(samples: Settings, admin: TestClient) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    bid = balance_id(admin, "harbor-miles", "2026-10-15")
    r = admin.delete(f"/api/rewards/balances/{bid}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["entries"] == [] and r.json()["program"]["source"] == "manual"
    harbor = prog(page(admin), "harbor-miles")
    assert (harbor["balance_micro"], harbor["as_of"]) == (42_000 * M, "2026-10-01")
    r = admin.delete(f"/api/rewards/balances/{bid}", headers=H)
    assert r.status_code == 404 and r.json()["detail"] == "No balance with that id."


def test_a_deleted_id_is_never_given_again(samples: Settings, admin: TestClient) -> None:
    # Codex: SQLite gave the deleted highest id to the next entry, so a stale
    # delete from an open page took a newer hotel balance.
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    old = balance_id(admin, "harbor-miles", "2026-10-15")
    assert admin.delete(f"/api/rewards/balances/{old}", headers=H).status_code == 200
    hotel = add_program(admin, name="Lantern Hotels", kind="points").json()["key"]
    assert post_balances(admin, entry(hotel, 9_000, "2026-10-16")).status_code == 201
    new = balance_id(admin, hotel, "2026-10-16")
    assert new != old
    r = admin.delete(f"/api/rewards/balances/{old}", headers=H)  # the stale delete
    assert r.status_code == 404 and r.json()["detail"] == ri.NO_BALANCE
    assert [e["id"] for e in history(admin, hotel)["entries"]] == [new]
    assert q(samples, "SELECT count(*) FROM reward_balances")[0][0] == 1


def test_entries_dated_later_wait_for_their_day(
    samples: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    on(monkeypatch, date(2026, 10, 25))
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-25")).status_code == 201
    assert prog(page(admin), "harbor-miles")["source"] == "app"
    on(monkeypatch, TODAY)  # the clock went back: the entry is not a candidate yet
    body = page(admin)
    assert prog(body, "harbor-miles")["source"] == "manual" and body["orphans"] == []


def test_cash_back_takes_cents(samples: Settings, admin: TestClient) -> None:
    assert post_balances(admin, entry("pine-cash-back", "40.5")).status_code == 201
    pine = prog(page(admin), "pine-cash-back")
    assert (pine["balance_micro"], pine["as_of"], pine["source"]) == (
        40_500_000,
        "2026-10-20",
        "app",
    )
    assert (pine["value_low_cents"], pine["value_high_cents"]) == (4_050, 4_050)


@pytest.mark.parametrize(
    ("entries", "status", "detail"),
    [
        ([entry("harbor-miles", 52_000, "2026-10-21")], 422, ri.FUTURE),
        ([entry("shared-vacation-club", 500)], 422, ri.TIMESHARE),
        ([entry("harbor-miles", f"{52_000}.5")], 422, ri.WHOLE),
        ([entry("harbor-miles", f"{52_000}.00")], 422, ri.WHOLE),
        ([entry("no-such-program", 500)], 404, ri.NO_PROGRAM),
        (
            [entry("harbor-miles", 1, "2026-10-02"), entry("harbor-miles", 2, "2026-10-02")],
            422,
            ri.ONE_A_DAY,
        ),
        # one good entry and one bad: nothing is written
        ([entry("summit-points", 1), entry("pine-cash-back", 500, "2026-10-30")], 422, ri.FUTURE),
        ([entry("pine-cash-back", "37.425")], 422, None),
        ([entry("pine-cash-back", "-5")], 422, None),
        ([entry("pine-cash-back", "1e3")], 422, None),
        ([entry("harbor-miles", 1, "1999-12-31")], 422, None),
        ([entry("Harbor-Miles", 1)], 422, None),
        ([entry("harbor-miles", "1" * 11)], 422, None),
        ([], 422, None),
        ([entry("summit-points", i) for i in range(1, 52)], 422, None),
        ([entry("summit-points", 1) | {"note": "x"}], 422, None),
    ],
)
def test_balance_rules(
    samples: Settings, admin: TestClient, entries: list[dict], status: int, detail: str | None
) -> None:
    r = post_balances(admin, *entries)
    assert r.status_code == status, r.text
    if detail is not None:
        assert r.json()["detail"] == detail
    assert q(samples, "SELECT count(*) FROM reward_balances")[0][0] == 0
    assert events(samples) == []


def test_fifty_entries_go_in(samples: Settings, admin: TestClient) -> None:
    days = [f"2026-08-{d:02d}" for d in range(1, 32)] + [f"2026-09-{d:02d}" for d in range(1, 20)]
    r = post_balances(admin, *(entry("summit-points", 1_000 + i, d) for i, d in enumerate(days)))
    assert r.status_code == 201 and r.json()["saved"] == 50
    assert q(samples, "SELECT count(*) FROM reward_balances")[0][0] == 50
    assert prog(page(admin), "summit-points")["source"] == "statement"  # Sep 30 is newer


def test_a_dropped_program_keeps_its_entries_as_an_orphan(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("pine-cash-back", "40.00")).status_code == 201
    send_programs(samples, lambda ps: [p for p in ps if p["key"] != "pine-cash-back"])
    body = page(admin)
    assert "pine-cash-back" not in {p["key"] for p in body["programs"]}
    assert "pine-cash-back" not in {x["key"] for x in body["value_table"]["rows"]}
    assert body["orphans"] == [orphan("pine-cash-back", "dropped", "cash")]
    r = post_balances(admin, entry("pine-cash-back", "41.00"))
    assert r.status_code == 404 and r.json()["detail"] == ri.NO_PROGRAM
    h = history(admin, "pine-cash-back")
    assert h["program"] is None and h["chat"] is None and len(h["entries"]) == 1
    # Move to: dollars never turn into points, even when they are whole
    r = admin.post(
        "/api/rewards/programs/pine-cash-back/move", json={"to_key": "summit-points"}, headers=H
    )
    assert r.status_code == 422 and r.json()["detail"] == ri.OTHER_UNIT
    assert q(samples, "SELECT program_key FROM reward_balances")[0][0] == "pine-cash-back"
    key = add_program(admin, name="Pine Rewards", kind="cash_back").json()["key"]
    r = admin.post("/api/rewards/programs/pine-cash-back/move", json={"to_key": key}, headers=H)
    assert r.status_code == 200, r.text
    assert (r.json()["key"], r.json()["balance_micro"], r.json()["source"]) == (key, 40 * M, "app")
    assert page(admin)["orphans"] == []


def test_dropped_miles_move_to_points_or_miles_only(samples: Settings, admin: TestClient) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    data = chat_rewards(samples)  # the chat drops the program and what points at it
    for name in ("programs", "benefits", "redemptions"):
        data[name] = [
            x for x in data[name] if "harbor-miles" not in (x["key"], x.get("program_key"))
        ]
    add_monthly_run(samples, NEWER_RUN, rewards=data)
    before = page(admin)
    assert before["source_run_id"] == NEWER_RUN
    assert before["orphans"] == [orphan("harbor-miles", "dropped", "whole")]
    r = admin.post(
        "/api/rewards/programs/harbor-miles/move", json={"to_key": "pine-cash-back"}, headers=H
    )
    assert r.status_code == 422 and r.json()["detail"] == ri.OTHER_UNIT
    assert page(admin)["tiles"] == before["tiles"]  # no miles turned into dollars
    # an app program in miles cannot go to cash back either
    key = add_program(admin, name="Lantern Air", kind="miles").json()["key"]
    assert post_balances(admin, entry(key, 9_000)).status_code == 201
    r = admin.post(
        f"/api/rewards/programs/{key}/move", json={"to_key": "pine-cash-back"}, headers=H
    )
    assert r.status_code == 422 and r.json()["detail"] == ri.OTHER_UNIT
    assert q(samples, "SELECT count(*) FROM reward_app_programs")[0][0] == 1
    # miles to points is the same unit
    r = admin.post(
        "/api/rewards/programs/harbor-miles/move", json={"to_key": "summit-points"}, headers=H
    )
    assert r.status_code == 200, r.text
    assert (r.json()["balance_micro"], r.json()["source"]) == (52_000 * M, "app")


def test_an_orphans_unit_is_the_one_it_was_typed_in(samples: Settings, admin: TestClient) -> None:
    assert post_balances(admin, entry("pine-cash-back", "40.25")).status_code == 201
    assert units(samples, "pine-cash-back") == [("2026-10-20", "cash")]
    data = chat_rewards(samples)
    pine = next(p for p in data["programs"] if p["key"] == "pine-cash-back")
    pine["kind"] = "points"  # an older run calls it points: older runs are never read
    add_monthly_run(samples, "2026-10-18", rewards=data)
    send_programs(samples, lambda ps: [p for p in ps if p["key"] != "pine-cash-back"])
    body = page(admin)
    assert body["source_run_id"] == NEWER_RUN
    assert body["orphans"] == [orphan("pine-cash-back", "dropped", "cash")]
    r = admin.post(
        "/api/rewards/programs/pine-cash-back/move", json={"to_key": "summit-points"}, headers=H
    )
    assert r.status_code == 422 and r.json()["detail"] == ri.OTHER_UNIT
    key = add_program(admin, name="Pine Rewards", kind="cash_back").json()["key"]
    r = admin.post("/api/rewards/programs/pine-cash-back/move", json={"to_key": key}, headers=H)
    assert r.status_code == 200 and r.json()["balance_micro"] == 40_250_000
    assert units(samples, key) == [("2026-10-20", "cash")]


def test_deleting_an_orphans_last_entry(samples: Settings, member: TestClient) -> None:
    assert post_balances(member, entry("pine-cash-back", "40.00")).status_code == 201
    send_programs(samples, lambda ps: [p for p in ps if p["key"] != "pine-cash-back"])
    bid = balance_id(member, "pine-cash-back", "2026-10-20")
    r = member.delete(f"/api/rewards/balances/{bid}", headers=H)
    assert r.status_code == 200
    assert r.json() == {
        "program_key": "pine-cash-back",
        "program": None,
        "entries": [],
        "chat": None,
    }
    assert page(member)["orphans"] == []
    r = member.get("/api/rewards/programs/pine-cash-back/balances")
    assert r.status_code == 404


def test_delete_balances_on_an_orphan(samples: Settings, admin: TestClient) -> None:
    assert post_balances(admin, entry("pine-cash-back", "40.00")).status_code == 201
    send_programs(samples, lambda ps: [p for p in ps if p["key"] != "pine-cash-back"])
    # "Delete balances" names the unit the page listed
    r = admin.delete("/api/rewards/programs/pine-cash-back?unit=whole", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ri.BALANCES_CHANGED
    r = admin.delete("/api/rewards/programs/pine-cash-back", headers=H)  # no unit: app programs
    assert r.status_code == 404 and r.json()["detail"] == ri.NO_PROGRAM
    assert q(samples, "SELECT count(*) FROM reward_balances")[0][0] == 1
    r = admin.delete("/api/rewards/programs/pine-cash-back?unit=cash", headers=H)
    assert r.status_code == 204
    assert q(samples, "SELECT count(*) FROM reward_balances")[0][0] == 0
    assert page(admin)["orphans"] == []
    r = admin.delete("/api/rewards/programs/pine-cash-back?unit=cash", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ri.BALANCES_CHANGED
    r = admin.delete("/api/rewards/programs/pine-cash-back?unit=gold", headers=H)
    assert r.status_code == 422
    assert json.loads(events(samples)[-1]["detail_json"]) == {
        "action": "delete_balances",
        "balances": 1,
    }


def test_no_entries_on_a_program_that_became_a_timeshare(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201

    def timeshare(programs: list[dict]) -> list[dict]:
        harbor = next(p for p in programs if p["key"] == "harbor-miles")
        for f in ("balance", "as_of", "source", "cpp_low", "cpp_high"):
            harbor.pop(f)
        harbor["kind"] = "timeshare"
        return programs

    send_programs(samples, timeshare)
    body = page(admin)
    harbor = prog(body, "harbor-miles")
    assert (harbor["balance_micro"], harbor["source"]) == (None, None)
    assert body["orphans"] == [orphan("harbor-miles", "timeshare", "whole")]
    ski = next(x for x in body["redemptions"] if x["key"] == "ski-flights")
    assert ski["have_micro"] is None
    # the entries keep the unit they were typed in: miles move to points
    r = admin.post(
        "/api/rewards/programs/harbor-miles/move", json={"to_key": "summit-points"}, headers=H
    )
    assert r.status_code == 200 and r.json()["balance_micro"] == 52_000 * M
    body = page(admin)
    assert body["orphans"] == [] and prog(body, "harbor-miles")["kind"] == "timeshare"


# --------------------------------------------------------------------------
# Units: each entry keeps the unit it was typed in
# --------------------------------------------------------------------------

HARBOR_CASH = 420 * M  # the chat's balance once Harbor Miles counts as cash back


def to_cash_back(programs: list[dict]) -> list[dict]:
    """The chat now counts Harbor Miles as cash back, with an old balance."""
    harbor = next(p for p in programs if p["key"] == "harbor-miles")
    for f in ("cpp_low", "cpp_high"):
        harbor.pop(f)
    harbor |= {"kind": "cash_back", "balance": "420.00", "as_of": "2026-06-30"}
    harbor["source"] = "statement"
    return programs


def test_the_unit_is_stored_on_add(samples: Settings, admin: TestClient) -> None:
    r = post_balances(
        admin,
        entry("harbor-miles", 52_000, "2026-10-15"),
        entry("summit-points", 85_000),
        entry("pine-cash-back", "40.50"),
    )
    assert r.status_code == 201, r.text
    rows = q(samples, "SELECT program_key, unit FROM reward_balances ORDER BY program_key")
    assert [tuple(x) for x in rows] == [
        ("harbor-miles", "whole"),
        ("pine-cash-back", "cash"),
        ("summit-points", "whole"),
    ]
    h = history(admin, "pine-cash-back")
    assert [(e["unit"], e["shown"]) for e in h["entries"]] == [("cash", True)]


def test_the_chat_switching_to_cash_back_strands_the_entries(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    send_programs(samples, to_cash_back)
    checks = admin.get("/api/checks").json()["rewards"]
    body = page(admin)
    harbor = prog(body, "harbor-miles")
    # the chat's old balance shows: the newer entry is in miles
    assert (harbor["kind"], harbor["balance_micro"], harbor["as_of"], harbor["source"]) == (
        "cash_back",
        HARBOR_CASH,
        "2026-06-30",
        "statement",
    )
    assert harbor["stale"] is True and harbor["chat_balance_micro"] is None
    assert (harbor["value_low_cents"], harbor["value_high_cents"]) == (42_000, 42_000)
    ski = next(x for x in body["redemptions"] if x["key"] == "ski-flights")
    assert ski["have_micro"] == HARBOR_CASH
    assert body["orphans"] == [orphan("harbor-miles", "unit", "whole")]
    assert body["review_counts"]["program_notes"] == 1
    stale = {s["key"]: s["as_of"] for s in checks["stale"]}
    assert stale["harbor-miles"] == "2026-06-30"
    with read_connection(samples.db_path) as conn:
        assert trips._names(conn).programs["harbor-miles"] == "Harbor Miles"
    h = history(admin, "harbor-miles")
    assert [(e["as_of"], e["balance_micro"], e["unit"], e["shown"]) for e in h["entries"]] == [
        ("2026-10-15", 52_000 * M, "whole", False)
    ]
    assert h["program"]["source"] == "statement"

    # a new cash back entry shows; the miles stay stranded
    assert post_balances(admin, entry("harbor-miles", "450.25", "2026-10-18")).status_code == 201
    harbor = prog(page(admin), "harbor-miles")
    assert (harbor["balance_micro"], harbor["source"], harbor["stale"]) == (
        450_250_000,
        "app",
        False,
    )
    assert page(admin)["orphans"] == [orphan("harbor-miles", "unit", "whole")]
    h = history(admin, "harbor-miles")
    assert [(e["unit"], e["shown"]) for e in h["entries"]] == [("cash", True), ("whole", False)]

    # "Delete balances" takes only the stranded miles
    r = admin.delete("/api/rewards/programs/harbor-miles?unit=whole", headers=H)
    assert r.status_code == 204
    assert units(samples, "harbor-miles") == [("2026-10-18", "cash")]
    assert json.loads(events(samples)[-1]["detail_json"]) == {
        "action": "delete_balances",
        "balances": 1,
    }
    after = page(admin)
    assert after["orphans"] == [] and prog(after, "harbor-miles")["source"] == "app"
    r = admin.delete("/api/rewards/programs/harbor-miles", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ri.APP_ONLY

    # with the cash back entry gone too, the page is the one the stranded entry
    # was on: no reader ever read it
    bid = balance_id(admin, "harbor-miles", "2026-10-18")
    assert admin.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200

    def strip(b: dict) -> dict:
        return {k: v for k, v in b.items() if k not in ("orphans", "review_counts")}

    def strip_checks(c: dict) -> dict:  # the orphan counts in program_notes
        return c | {"counts": {k: v for k, v in c["counts"].items() if k != "program_notes"}}

    assert strip(page(admin)) == strip(body)
    assert strip_checks(admin.get("/api/checks").json()["rewards"]) == strip_checks(checks)
    assert checks["counts"]["program_notes"] == 1


def test_a_same_day_entry_replaces_the_stranded_one_and_its_unit(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    send_programs(samples, to_cash_back)
    assert post_balances(admin, entry("harbor-miles", "455.00", "2026-10-15")).status_code == 201
    assert units(samples, "harbor-miles") == [("2026-10-15", "cash")]
    body = page(admin)
    assert body["orphans"] == []
    harbor = prog(body, "harbor-miles")
    assert (harbor["balance_micro"], harbor["as_of"], harbor["source"]) == (
        455 * M,
        "2026-10-15",
        "app",
    )
    assert [json.loads(e["detail_json"])["action"] for e in events(samples)] == ["add", "replace"]


def test_stranded_entries_move_to_a_program_in_their_unit(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    # a benefit edit names the program: it still names it after the move
    r = admin.patch(
        "/api/rewards/benefits/anniversary-night",
        json={"set": {"program_key": "harbor-miles"}},
        headers=H,
    )
    assert r.status_code == 200, r.text
    send_programs(samples, to_cash_back)
    assert post_balances(admin, entry("harbor-miles", "450.25", "2026-10-18")).status_code == 201

    def move(to_key: str):
        return admin.post(
            "/api/rewards/programs/harbor-miles/move", json={"to_key": to_key}, headers=H
        )

    r = move("pine-cash-back")
    assert r.status_code == 422 and r.json()["detail"] == ri.OTHER_UNIT
    r = move("harbor-miles")
    assert r.status_code == 422 and r.json()["detail"] == ri.SAME_PROGRAM
    r = move("shared-vacation-club")
    assert r.status_code == 422 and r.json()["detail"] == ri.TIMESHARE
    assert post_balances(admin, entry("summit-points", 85_000, "2026-10-15")).status_code == 201
    r = move("summit-points")
    assert r.status_code == 409 and r.json()["detail"] == ri.SAME_DAY
    bid = balance_id(admin, "summit-points", "2026-10-15")
    assert admin.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200

    r = move("summit-points")
    assert r.status_code == 200, r.text
    assert (r.json()["key"], r.json()["balance_micro"], r.json()["source"]) == (
        "summit-points",
        52_000 * M,
        "app",
    )
    assert units(samples, "summit-points") == [("2026-10-15", "whole")]
    assert units(samples, "harbor-miles") == [("2026-10-18", "cash")]  # the shown one stays
    body = page(admin)
    assert body["orphans"] == []
    harbor = prog(body, "harbor-miles")
    assert (harbor["kind"], harbor["balance_micro"]) == ("cash_back", 450_250_000)
    assert json.loads(events(samples)[-1]["detail_json"]) == {
        "action": "move",
        "balances": 1,
        "program_deleted": False,
    }
    edit = q(samples, "SELECT fields_json FROM reward_benefit_edits")
    assert [json.loads(e[0]) for e in edit] == [{"program_key": "harbor-miles"}]
    # nothing stranded is left, and a chat program never moves itself
    r = move("summit-points")
    assert r.status_code == 422 and r.json()["detail"] == ri.APP_ONLY


def test_a_dropped_key_with_both_units_moves_one_unit_at_a_time(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    data = chat_rewards(samples)
    data["programs"] = to_cash_back(data["programs"])
    add_monthly_run(samples, "2026-10-18", rewards=data)
    assert post_balances(admin, entry("harbor-miles", "450.25", "2026-10-18")).status_code == 201
    for name in ("programs", "benefits", "redemptions"):  # the chat drops it
        data[name] = [
            x for x in data[name] if "harbor-miles" not in (x["key"], x.get("program_key"))
        ]
    add_monthly_run(samples, NEWER_RUN, rewards=data)
    assert page(admin)["orphans"] == [
        orphan("harbor-miles", "dropped", "cash"),
        orphan("harbor-miles", "dropped", "whole"),
    ]
    r = admin.post(
        "/api/rewards/programs/harbor-miles/move", json={"to_key": "summit-points"}, headers=H
    )
    assert r.status_code == 200 and r.json()["balance_micro"] == 52_000 * M
    assert units(samples, "harbor-miles") == [("2026-10-18", "cash")]
    assert page(admin)["orphans"] == [orphan("harbor-miles", "dropped", "cash")]
    r = admin.delete("/api/rewards/programs/harbor-miles?unit=whole", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ri.BALANCES_CHANGED
    r = admin.delete("/api/rewards/programs/harbor-miles?unit=cash", headers=H)
    assert r.status_code == 204
    assert units(samples, "harbor-miles") == [] and page(admin)["orphans"] == []


def test_delete_balances_after_the_page_changed_deletes_nothing_it_did_not_show(
    samples: Settings, admin: TestClient
) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    miles = chat_rewards(samples)
    cash = chat_rewards(samples)
    cash["programs"] = to_cash_back(cash["programs"])
    add_monthly_run(samples, "2026-10-18", rewards=cash)
    assert post_balances(admin, entry("harbor-miles", "450.25", "2026-10-18")).status_code == 201
    assert page(admin)["orphans"] == [orphan("harbor-miles", "unit", "whole")]
    # the admin asks to delete the miles; the next run counts it in miles again
    add_monthly_run(samples, NEWER_RUN, rewards=miles)
    assert page(admin)["orphans"] == [orphan("harbor-miles", "unit", "cash")]
    count = len(events(samples))
    r = admin.delete("/api/rewards/programs/harbor-miles?unit=whole", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ri.BALANCES_CHANGED
    assert units(samples, "harbor-miles") == [("2026-10-15", "whole"), ("2026-10-18", "cash")]
    assert len(events(samples)) == count
    harbor = prog(page(admin), "harbor-miles")
    assert (harbor["balance_micro"], harbor["source"]) == (52_000 * M, "app")
    # the cash entry the page lists now goes on its own
    r = admin.delete("/api/rewards/programs/harbor-miles?unit=cash", headers=H)
    assert r.status_code == 204
    assert units(samples, "harbor-miles") == [("2026-10-15", "whole")]
    assert page(admin)["orphans"] == []


def test_points_to_miles_is_the_same_unit(samples: Settings, admin: TestClient) -> None:
    assert post_balances(admin, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201

    def to_points(programs: list[dict]) -> list[dict]:
        next(p for p in programs if p["key"] == "harbor-miles")["kind"] = "points"
        return programs

    send_programs(samples, to_points)
    body = page(admin)
    harbor = prog(body, "harbor-miles")
    assert (harbor["kind"], harbor["balance_micro"], harbor["source"]) == (
        "points",
        52_000 * M,
        "app",
    )
    assert body["orphans"] == []
    assert units(samples, "harbor-miles") == [("2026-10-15", "whole")]


# --------------------------------------------------------------------------
# App programs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("Lantern Air", "lantern-air"),
        ("$300 travel credit", "travel-credit"),
        ("Área 51 Club", "area-club"),
        ("B2B  Rewards!!", "bb-rewards"),
        ("123", ""),
    ],
)
def test_slug(name: str, want: str) -> None:
    assert ri.slug(name) == want


def test_app_keys_fit_the_key_rule() -> None:
    long = "Very long program name with many words in it"
    first = ri.app_key(long, set(), "program")
    assert len(first) <= KEY_MAX and first.startswith("app-very-long")
    second = ri.app_key(long, {first}, "program")
    assert second.endswith("-2") and len(second) <= KEY_MAX
    keys = [first]
    for _ in range(12):
        keys.append(ri.app_key(long, set(keys), "program"))
    assert keys[-1].endswith("-13") and all(len(k) <= KEY_MAX for k in keys)
    assert ri.app_key("123", set(), "program") == "app-program"
    for k in [*keys, "app-program"]:
        assert re.fullmatch(KEY_PATTERN, k), k
    every = {"app-x"} | {f"app-x-{n}" for n in range(2, ri.SUFFIX_MAX + 1)}
    with pytest.raises(EditError) as err:
        ri.app_key("X", every, "program")
    assert err.value.status == 422


def test_add_a_program(samples: Settings, admin: TestClient) -> None:
    r = add_program(admin, name="  Lantern   Air ", kind="miles")
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["key"], p["name"], p["origin"], p["group"]) == (
        "app-lantern-air",
        "Lantern Air",
        "app",
        "secondary",
    )
    assert (p["balance_micro"], p["source"], p["value_low_cents"]) == (None, None, None)
    assert add_program(admin, name="Lantern Air", kind="points").json()["key"] == (
        "app-lantern-air-2"
    )
    r = add_program(
        admin,
        name="Harbor Lounge",
        kind="points",
        group="primary",
        owner_key="sam",
        cpp_low="0.8",
        cpp_high="1.1",
        expires_on="2027-06-30",
        note="Family pool",
    )
    assert r.status_code == 201, r.text
    lounge = r.json()
    assert (lounge["owner_name"], lounge["cpp_low"], lounge["cpp_high"]) == ("Sam", "0.8", "1.1")
    assert (lounge["expires_on"], lounge["note"], lounge["group"]) == (
        "2027-06-30",
        "Family pool",
        "primary",
    )
    assert post_balances(admin, entry(lounge["key"], 20_000)).status_code == 201
    body = page(admin)
    lounge = prog(body, "app-harbor-lounge")
    assert (lounge["value_low_cents"], lounge["value_high_cents"]) == (16_000, 22_000)
    assert lounge["source"] == "app" and lounge["origin"] == "app"
    keys = [p["key"] for p in body["programs"]]
    assert keys.index("app-harbor-lounge") < keys.index("app-lantern-air")  # primary first
    # trip program names read the app programs too
    with read_connection(samples.db_path) as conn:
        names = trips._names(conn).programs
    assert names["app-lantern-air"] == "Lantern Air"
    assert names["app-harbor-lounge"] == "Harbor Lounge"
    assert names["harbor-miles"] == "Harbor Miles"
    row = q(samples, "SELECT * FROM reward_app_programs WHERE program_key = 'app-harbor-lounge'")
    assert (row[0]["cpp_low_micro"], row[0]["cpp_high_micro"]) == (800_000, 1_100_000)
    assert row[0]["updated_by"] is not None


def test_a_new_key_never_takes_an_orphans_balances(samples: Settings, admin: TestClient) -> None:
    key = add_program(admin, name="Lantern Air", kind="miles").json()["key"]
    assert post_balances(admin, entry(key, 9_000)).status_code == 201
    with write_connection(samples.db_path) as w:  # the program goes, its entry stays
        w.execute("DELETE FROM reward_app_programs")
    assert page(admin)["orphans"] == [orphan(key, "dropped", "whole")]
    assert add_program(admin, name="Lantern Air", kind="miles").json()["key"] == key + "-2"
    # no run ever sent the key, but the entry keeps the unit it was typed in
    r = admin.post(
        f"/api/rewards/programs/{key}/move", json={"to_key": "pine-cash-back"}, headers=H
    )
    assert r.status_code == 422 and r.json()["detail"] == ri.OTHER_UNIT
    assert admin.delete(f"/api/rewards/programs/{key}?unit=whole", headers=H).status_code == 204
    assert page(admin)["orphans"] == []


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (
            {"name": "Pine Plus", "kind": "cash_back", "cpp_low": "1", "cpp_high": "1"},
            ri.FACE_VALUE,
        ),
        ({"name": "Lantern Air", "kind": "miles", "cpp_low": "1"}, ri.VALUE_RANGE),
        ({"name": "Lantern Air", "kind": "miles", "cpp_high": "1"}, ri.VALUE_RANGE),
        (
            {"name": "Lantern Air", "kind": "miles", "cpp_low": "1.5", "cpp_high": "1.2"},
            ri.VALUE_RANGE,
        ),
        ({"name": "Lantern Air", "kind": "miles", "owner_key": "nobody"}, ri.UNKNOWN_PERSON),
        ({"name": "Lantern Air", "kind": "timeshare"}, None),
        ({"name": "   ", "kind": "miles"}, None),
        ({"name": "x" * 65, "kind": "miles"}, None),
        ({"name": "Lantern\u0007Air", "kind": "miles"}, None),
        ({"name": "Lantern\u202eAir", "kind": "miles"}, None),
        ({"name": "Lantern Air", "kind": "miles", "note": "x" * 501}, None),
        ({"name": "Lantern Air", "kind": "miles", "cpp_low": "100", "cpp_high": "100"}, None),
        ({"name": "Lantern Air", "kind": "miles", "expires_on": "1999-01-01"}, None),
        ({"name": "Lantern Air", "kind": "miles", "key": "app-mine"}, None),
        ({"kind": "miles"}, None),
    ],
)
def test_program_rules(
    samples: Settings, admin: TestClient, body: dict, detail: str | None
) -> None:
    r = add_program(admin, **body)
    assert r.status_code == 422, r.text
    if detail is not None:
        assert r.json()["detail"] == detail
    assert q(samples, "SELECT count(*) FROM reward_app_programs")[0][0] == 0
    assert events(samples) == []


def test_change_a_program(samples: Settings, admin: TestClient) -> None:
    key = add_program(admin, name="Lantern Air", kind="miles", note="Family pool").json()["key"]

    def patch(body: dict):
        return admin.patch(f"/api/rewards/programs/{key}", json=body, headers=H)

    r = patch({"name": "Lantern Airways", "cpp_low": "1.1", "cpp_high": "1.3"})
    assert r.status_code == 200, r.text
    assert (r.json()["key"], r.json()["name"], r.json()["cpp_high"]) == (
        key,
        "Lantern Airways",
        "1.3",
    )
    r = patch({"note": None, "group": "primary"})
    assert (r.json()["note"], r.json()["group"], r.json()["cpp_low"]) == (None, "primary", "1.1")
    assert patch({"kind": "cash_back"}).json()["detail"] == ri.FACE_VALUE
    assert patch({}).json()["detail"] == ri.NOTHING
    for field in ("name", "kind", "group"):
        r = patch({field: None})
        assert r.status_code == 422 and r.json()["detail"] == ri.NOT_CLEARABLE
    r = admin.patch("/api/rewards/programs/harbor-miles", json={"name": "X"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ri.APP_ONLY
    r = admin.patch("/api/rewards/programs/app-nope", json={"name": "X"}, headers=H)
    assert r.status_code == 404
    assert [json.loads(e["detail_json"]) for e in events(samples)] == [
        {"action": "add", "fields": ["group", "kind", "name", "note"]},
        {"action": "change", "fields": ["cpp_high", "cpp_low", "name"]},
        {"action": "change", "fields": ["group", "note"]},
    ]


def test_a_kind_change_relabels_the_entries(samples: Settings, admin: TestClient) -> None:
    key = add_program(admin, name="Pine Plus", kind="cash_back").json()["key"]

    def patch(kind: str):
        return admin.patch(f"/api/rewards/programs/{key}", json={"kind": kind}, headers=H)

    def last_event() -> dict:
        return json.loads(events(samples)[-1]["detail_json"])

    assert post_balances(admin, entry(key, "12.50", "2026-10-01")).status_code == 201
    r = patch("points")  # cash to whole needs whole numbers
    assert r.status_code == 422 and r.json()["detail"] == ri.NOT_FITTING
    assert units(samples, key) == [("2026-10-01", "cash")]
    bid = balance_id(admin, key, "2026-10-01")
    assert admin.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200
    assert post_balances(admin, entry(key, "12.00", "2026-10-01")).status_code == 201
    r = patch("points")  # a kind picked by mistake: the entry was typed in points
    assert r.status_code == 200 and r.json()["balance_micro"] == 12 * M
    assert r.json()["source"] == "app"
    assert units(samples, key) == [("2026-10-01", "whole")]
    assert last_event() == {"action": "change", "fields": ["kind"], "balances": 1}
    assert [(e["unit"], e["shown"]) for e in history(admin, key)["entries"]] == [("whole", True)]
    # points to miles is the same unit: nothing to relabel
    r = patch("miles")
    assert r.status_code == 200 and r.json()["balance_micro"] == 12 * M
    assert units(samples, key) == [("2026-10-01", "whole")]
    assert last_event() == {"action": "change", "fields": ["kind"]}
    # and back to cash back: whole numbers fit
    r = patch("cash_back")
    assert r.status_code == 200 and r.json()["balance_micro"] == 12 * M
    assert units(samples, key) == [("2026-10-01", "cash")]
    assert page(admin)["orphans"] == []


def test_delete_a_program_takes_its_balances(samples: Settings, admin: TestClient) -> None:
    key = add_program(admin, name="Lantern Air", kind="miles").json()["key"]
    assert post_balances(admin, entry(key, 9_000)).status_code == 201
    assert admin.delete(f"/api/rewards/programs/{key}", headers=H).status_code == 204
    assert q(samples, "SELECT count(*) FROM reward_balances")[0][0] == 0
    body = page(admin)
    assert key not in {p["key"] for p in body["programs"]} and body["orphans"] == []
    assert admin.delete(f"/api/rewards/programs/{key}", headers=H).status_code == 404
    r = admin.delete("/api/rewards/programs/harbor-miles", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ri.APP_ONLY
    assert json.loads(events(samples)[-1]["detail_json"]) == {"action": "delete", "balances": 1}


def test_the_chat_sending_an_app_key(samples: Settings, admin: TestClient) -> None:
    key = add_program(admin, name="Lantern Air", kind="miles").json()["key"]
    assert post_balances(admin, entry(key, 15_000, "2026-10-15")).status_code == 201
    sent = {
        "key": key,
        "name": "Lantern Air Club",
        "kind": "points",
        "group": "secondary",
        "balance": str(16_000),
        "as_of": "2026-10-18",
        "source": "statement",
    }
    send_programs(samples, lambda ps: [*ps, sent])
    body = page(admin)
    p = prog(body, key)
    assert (p["name"], p["kind"], p["origin"]) == ("Lantern Air", "miles", "app")  # the app wins
    # the chat's newer balance still wins the balance
    assert (p["balance_micro"], p["source"]) == (16_000 * M, "statement")
    assert body["notices"] == [
        {
            "kind": "program_app_key",
            "text": ov.APP_KEY_TEXT,
            "key": key,
            "other_key": None,
            "fields": [],
        }
    ]
    with read_connection(samples.db_path) as conn:
        assert trips._names(conn).programs[key] == "Lantern Air"
    # "Use the chat's": the app program goes, its balances carry over by key
    assert admin.delete(f"/api/rewards/programs/{key}", headers=H).status_code == 204
    body = page(admin)
    p = prog(body, key)
    assert (p["name"], p["origin"], p["kind"]) == ("Lantern Air Club", "chat", "points")
    assert body["notices"] == []
    assert len(history(admin, key)["entries"]) == 1
    assert json.loads(events(samples)[-1]["detail_json"]) == {"action": "delete", "balances": 0}


def test_use_the_chats_in_another_unit_strands_the_entries(
    samples: Settings, admin: TestClient
) -> None:
    key = add_program(admin, name="Lantern Cash", kind="cash_back").json()["key"]
    assert post_balances(admin, entry(key, "12.50", "2026-10-15")).status_code == 201
    sent = {"key": key, "name": "Lantern Cash", "kind": "points", "group": "secondary"}
    send_programs(samples, lambda ps: [*ps, sent])
    body = page(admin)
    assert prog(body, key)["kind"] == "cash_back"  # the app wins
    assert [(n["kind"], n["fields"]) for n in body["notices"]] == [("program_app_key", ["unit"])]
    # "Use the chat's": the app program goes, the entry stays in its unit
    assert admin.delete(f"/api/rewards/programs/{key}", headers=H).status_code == 204
    assert q(samples, "SELECT count(*) FROM reward_app_programs")[0][0] == 0
    assert json.loads(events(samples)[-1]["detail_json"]) == {"action": "delete", "balances": 0}
    body = page(admin)
    p = prog(body, key)
    assert (p["kind"], p["origin"], p["balance_micro"], p["source"]) == (
        "points",
        "chat",
        None,
        None,
    )
    assert body["orphans"] == [orphan(key, "unit", "cash")]
    assert [(e["unit"], e["shown"]) for e in history(admin, key)["entries"]] == [("cash", False)]
    # a points entry shows; "Delete balances" takes only the cash one
    assert post_balances(admin, entry(key, 1_300, "2026-10-18")).status_code == 201
    assert prog(page(admin), key)["balance_micro"] == 1_300 * M
    assert admin.delete(f"/api/rewards/programs/{key}?unit=cash", headers=H).status_code == 204
    assert units(samples, key) == [("2026-10-18", "whole")]
    body = page(admin)
    assert body["orphans"] == [] and prog(body, key)["balance_micro"] == 1_300 * M


def send_app_key(settings: Settings, sent: dict) -> None:
    """The chat's next run also sends an app key, with a redemption on it."""
    data = chat_rewards(settings)
    data["programs"] = sorted([*data["programs"], sent], key=lambda p: p["key"])
    trip = {"key": "lantern-trip", "program_key": sent["key"], "title": "Lantern trip"}
    trip |= {"status": "consider", "points_needed": str(20_000)}
    data["redemptions"] = sorted([*data["redemptions"], trip], key=lambda x: x["key"])
    add_monthly_run(settings, NEWER_RUN, rewards=data)


def test_the_chats_balance_in_another_unit_never_counts_for_an_app_program(
    samples: Settings, admin: TestClient
) -> None:
    key = add_program(admin, name="Lantern Cash", kind="cash_back").json()["key"]
    assert post_balances(admin, entry(key, "12.50", "2026-06-01")).status_code == 201
    tiles = page(admin)["tiles"]
    # the chat sends the key as points, with a newer balance
    sent = {"key": key, "name": "Lantern Cash", "kind": "points", "group": "secondary"}
    send_app_key(samples, sent | {"balance": str(16_000), "as_of": "2026-10-18"} | STATEMENT)
    body = page(admin)
    p = prog(body, key)
    # the app's cash entry stays the balance: 16,000 points never read as dollars
    assert (p["kind"], p["balance_micro"], p["as_of"], p["source"]) == (
        "cash_back",
        12_500_000,
        "2026-06-01",
        "app",
    )
    assert (p["stale"], p["chat_balance_micro"]) == (True, None)
    assert (p["value_low_cents"], p["value_high_cents"]) == (1_250, 1_250)
    row = next(x for x in body["value_table"]["rows"] if x["key"] == key)
    assert (row["balance_micro"], row["low_cents"], row["high_cents"]) == (12_500_000, 1_250, 1_250)
    assert body["tiles"] == tiles
    trip = next(x for x in body["redemptions"] if x["key"] == "lantern-trip")
    assert trip["have_micro"] == 12_500_000
    assert [(n["kind"], n["fields"]) for n in body["notices"]] == [("program_app_key", ["unit"])]
    assert body["orphans"] == []
    stale = {s["key"]: s["as_of"] for s in admin.get("/api/checks").json()["rewards"]["stale"]}
    assert stale[key] == "2026-06-01"
    h = history(admin, key)
    assert h["chat"] is None
    assert [(e["unit"], e["shown"]) for e in h["entries"]] == [("cash", True)]
    # with no entry left, the program has no balance at all
    bid = balance_id(admin, key, "2026-06-01")
    assert admin.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200
    body = page(admin)
    p = prog(body, key)
    assert (p["balance_micro"], p["as_of"], p["source"], p["value_low_cents"]) == (
        None,
        None,
        None,
        None,
    )
    assert next(x for x in body["redemptions"] if x["key"] == "lantern-trip")["have_micro"] is None
    assert key not in {s["key"] for s in admin.get("/api/checks").json()["rewards"]["stale"]}


def test_the_chats_cash_never_counts_as_points_for_an_app_program(
    samples: Settings, admin: TestClient
) -> None:
    key = add_program(admin, name="Lantern Air", kind="points").json()["key"]
    assert post_balances(admin, entry(key, 9_000, "2026-10-15")).status_code == 201
    # the chat sends the key as cash back, for the same day
    sent = {"key": key, "name": "Lantern Air", "kind": "cash_back", "group": "secondary"}
    send_app_key(samples, sent | {"balance": "450.25", "as_of": "2026-10-15"} | STATEMENT)
    body = page(admin)
    p = prog(body, key)
    # no "The chat has 450.25 for the same day" note: that number is dollars
    assert (p["kind"], p["balance_micro"], p["source"], p["chat_balance_micro"]) == (
        "points",
        9_000 * M,
        "app",
        None,
    )
    assert [(n["kind"], n["fields"]) for n in body["notices"]] == [("program_app_key", ["unit"])]
    h = history(admin, key)
    assert h["chat"] is None
    assert [(e["unit"], e["shown"]) for e in h["entries"]] == [("whole", True)]
    bid = balance_id(admin, key, "2026-10-15")
    assert admin.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200
    p = prog(page(admin), key)
    assert (p["balance_micro"], p["source"], p["stale"]) == (None, None, False)
    trip = next(x for x in page(admin)["redemptions"] if x["key"] == "lantern-trip")
    assert trip["have_micro"] is None


def test_a_duplicate_name_gives_a_notice(samples: Settings, admin: TestClient) -> None:
    key = add_program(admin, name="harbor MILES", kind="miles").json()["key"]
    assert key == "app-harbor-miles"
    notice = {
        "kind": "program_duplicate",
        "text": ov.DUPLICATE_TEXT,
        "key": key,
        "other_key": "harbor-miles",
        "fields": [],
    }
    assert page(admin)["notices"] == [notice]
    r = admin.post(
        f"/api/rewards/programs/{key}/keep-both", json={"other_key": "summit-points"}, headers=H
    )
    assert r.status_code == 422 and r.json()["detail"] == ri.NOT_DUPLICATE
    r = admin.post(
        f"/api/rewards/programs/{key}/keep-both", json={"other_key": "harbor-miles"}, headers=H
    )
    assert r.status_code == 200 and r.json()["key"] == key
    assert page(admin)["notices"] == []
    r = admin.post(
        "/api/rewards/programs/harbor-miles/keep-both", json={"other_key": key}, headers=H
    )
    assert r.status_code == 422 and r.json()["detail"] == ri.APP_ONLY


def test_keep_both_covers_every_chat_program_with_the_name(
    samples: Settings, admin: TestClient
) -> None:
    def two(programs: list[dict]) -> list[dict]:
        harbor = next(p for p in programs if p["key"] == "harbor-miles")
        return [*programs, harbor | {"key": "harbor-miles-alex", "owner": "alex"}]

    send_programs(samples, two)
    key = add_program(admin, name="Harbor Miles", kind="miles").json()["key"]
    assert [n["other_key"] for n in page(admin)["notices"]] == ["harbor-miles", "harbor-miles-alex"]
    r = admin.post(
        f"/api/rewards/programs/{key}/keep-both", json={"other_key": "harbor-miles-alex"}, headers=H
    )
    assert r.status_code == 200
    assert page(admin)["notices"] == []
    r = admin.post(
        f"/api/rewards/programs/{key}/keep-both", json={"other_key": "harbor-miles"}, headers=H
    )
    assert r.status_code == 200
    assert page(admin)["notices"] == []

    def renamed(programs: list[dict]) -> list[dict]:  # the kept one takes a new name
        harbor = next(p for p in programs if p["key"] == "harbor-miles")
        harbor["name"] = "Harbor Air"
        return programs

    data = chat_rewards(samples)
    data["programs"] = renamed(data["programs"])
    add_monthly_run(samples, "2026-10-20", rewards=data)
    assert [n["other_key"] for n in page(admin)["notices"]] == ["harbor-miles-alex"]


def test_use_the_chats_on_a_timeshare_duplicate(samples: Settings, admin: TestClient) -> None:
    key = add_program(admin, name="Shared Vacation Club", kind="points").json()["key"]
    assert [n["other_key"] for n in page(admin)["notices"]] == ["shared-vacation-club"]

    def move():
        return admin.post(
            f"/api/rewards/programs/{key}/move", json={"to_key": "shared-vacation-club"}, headers=H
        )

    assert post_balances(admin, entry(key, 500)).status_code == 201
    r = move()
    assert r.status_code == 422 and r.json()["detail"] == ri.TIMESHARE
    bid = balance_id(admin, key, "2026-10-20")
    assert admin.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200
    # no balances: Use the chat's just deletes the app program
    r = move()
    assert r.status_code == 200 and r.json()["key"] == "shared-vacation-club"
    body = page(admin)
    assert body["notices"] == [] and key not in {p["key"] for p in body["programs"]}
    assert q(samples, "SELECT count(*) FROM reward_app_programs")[0][0] == 0


def test_use_the_chats_on_a_duplicate_moves_the_balances(
    samples: Settings, admin: TestClient
) -> None:
    key = add_program(admin, name="Harbor Miles", kind="miles").json()["key"]
    assert post_balances(admin, entry(key, 52_000, "2026-10-15")).status_code == 201
    r = admin.post(f"/api/rewards/programs/{key}/move", json={"to_key": "harbor-miles"}, headers=H)
    assert r.status_code == 200, r.text
    harbor = r.json()
    assert (harbor["key"], harbor["balance_micro"], harbor["source"]) == (
        "harbor-miles",
        52_000 * M,
        "app",
    )
    body = page(admin)
    assert key not in {p["key"] for p in body["programs"]} and body["notices"] == []
    assert q(samples, "SELECT count(*) FROM reward_app_programs")[0][0] == 0
    assert json.loads(events(samples)[-1]["detail_json"]) == {
        "action": "move",
        "balances": 1,
        "program_deleted": True,
    }
    # a second duplicate with an entry on the same day cannot move
    key = add_program(admin, name="Harbor Miles", kind="miles").json()["key"]
    assert post_balances(admin, entry(key, 53_000, "2026-10-15")).status_code == 201
    r = admin.post(f"/api/rewards/programs/{key}/move", json={"to_key": "harbor-miles"}, headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ri.SAME_DAY
    assert len(history(admin, key)["entries"]) == 1


@pytest.mark.parametrize(
    ("source", "to_key", "status", "detail"),
    [
        ("harbor-miles", "summit-points", 422, ri.APP_ONLY),
        ("app-nope", "summit-points", 404, ri.NO_PROGRAM),
        ("APP", "summit-points", 422, None),
        ("app-lantern-air", "app-lantern-air", 422, ri.SAME_PROGRAM),
        ("app-lantern-air", "no-such-program", 404, ri.NO_PROGRAM),
    ],
)
def test_move_rules(
    samples: Settings, admin: TestClient, source: str, to_key: str, status: int, detail
) -> None:
    assert add_program(admin, name="Lantern Air", kind="miles").status_code == 201
    r = admin.post(f"/api/rewards/programs/{source}/move", json={"to_key": to_key}, headers=H)
    assert r.status_code == status, r.text
    if detail is not None:
        assert r.json()["detail"] == detail
    assert q(samples, "SELECT count(*) FROM reward_app_programs")[0][0] == 1


# --------------------------------------------------------------------------
# Before the chat's first 1.3 run, and who can write
# --------------------------------------------------------------------------

WRITES = [
    ("POST", "/api/rewards/balances", {"entries": [entry("harbor-miles", 1)]}),
    ("DELETE", "/api/rewards/balances/1", None),
    ("POST", "/api/rewards/programs", {"name": "Lantern Air", "kind": "miles"}),
    ("PATCH", "/api/rewards/programs/app-lantern-air", {"name": "Lantern"}),
    ("PATCH", "/api/rewards/programs/app-lantern-air", {}),
    ("PATCH", "/api/rewards/programs/app-lantern-air", {"name": None}),
    ("DELETE", "/api/rewards/programs/app-lantern-air", None),
    ("POST", "/api/rewards/programs/app-lantern-air/move", {"to_key": "harbor-miles"}),
    ("POST", "/api/rewards/programs/app-lantern-air/keep-both", {"other_key": "harbor-miles"}),
]
ADMIN_WRITES = WRITES[2:]


def test_every_write_is_409_before_the_first_13_run(settings: Settings, admin: TestClient) -> None:
    for method, path, body in WRITES:
        r = admin.request(method, path, json=body, headers=H)
        assert r.status_code == 409, (method, path, r.text)
        assert r.json()["detail"] == "Rewards start with the chat's first export."
    for t in REWARD_TABLES:
        assert q(settings, f"SELECT count(*) FROM {t}")[0][0] == 0, t
    assert events(settings) == []


def test_a_member_enters_balances_but_not_programs(samples: Settings, member: TestClient) -> None:
    assert post_balances(member, entry("harbor-miles", 52_000, "2026-10-15")).status_code == 201
    assert history(member, "harbor-miles")["entries"][0]["shown"] is True
    bid = balance_id(member, "harbor-miles", "2026-10-15")
    assert member.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200
    for method, path, body in ADMIN_WRITES:
        r = member.request(method, path, json=body, headers=H)
        assert r.status_code == 403, (method, path)
        assert r.json()["detail"] == "Only an admin can do this."
    assert q(samples, "SELECT count(*) FROM reward_app_programs")[0][0] == 0
    assert [json.loads(e["detail_json"])["action"] for e in events(samples)] == ["add", "delete"]


def test_the_new_routes_need_a_session(prod_app, prod: TestClient) -> None:
    routes = {
        (path, m)
        for path, methods, _ in walk_routes(prod_app)
        if path.startswith("/api/rewards")
        for m in methods - {"HEAD", "OPTIONS"}
    }
    assert routes >= {  # chunk C adds the benefit routes (test_rewards_inputs_benefits)
        ("/api/rewards", "GET"),
        ("/api/rewards/balances", "POST"),
        ("/api/rewards/balances/{balance_id}", "DELETE"),
        ("/api/rewards/programs/{key}/balances", "GET"),
        ("/api/rewards/programs", "POST"),
        ("/api/rewards/programs/{key}", "PATCH"),
        ("/api/rewards/programs/{key}", "DELETE"),
        ("/api/rewards/programs/{key}/move", "POST"),
        ("/api/rewards/programs/{key}/keep-both", "POST"),
    }
    for path, method in routes:
        r = prod.request(
            method, path.replace("{key}", "harbor-miles").replace("{balance_id}", "1"), headers=H
        )
        assert r.status_code == 401, (method, path)


# --------------------------------------------------------------------------
# Safety: typed text, events, transactions and logs
# --------------------------------------------------------------------------


def _flagged() -> list[str]:
    """The injected values the rewards text scan refuses, with the private lists."""
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    return [v for v in INJECTED_VALUES if scan_text(v, private, "rewards")]


def test_check_text_takes_a_section() -> None:
    check_text("notes", ["Seat QX7B2K"], None)  # current callers: no section, no booking-code
    with pytest.raises(EditError) as err:
        check_text("notes", ["Seat QX7B2K"], None, section="rewards")
    assert err.value.status == 422 and "QX7B2K" not in err.value.message
    assert err.value.message.startswith("The note looks like")


def _text_columns(conn: sqlite3.Connection) -> str:
    out = []
    for t in (*REWARD_TABLES, "events"):
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})") if c[2] == "TEXT"]
        for row in conn.execute(f"SELECT {', '.join(cols)} FROM {t}"):
            out.extend(str(v) for v in row if v is not None)
    return "\n".join(out)


def test_sensitive_text_is_422_and_never_stored(
    use_private_rules: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = use_private_rules
    add_users(settings)
    import_samples(settings)
    flagged = _flagged()
    assert len(flagged) >= 12 and "QX7B2K" in flagged and "EXAMPLE PRIVATE SERVICE" in flagged
    app = app_for(monkeypatch, dev=False, BUDGET_PRIVATE_RULES=str(settings.private_rules))
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        key = add_program(c, name="Lantern Air", kind="miles").json()["key"]
        for value in flagged:
            for body in (
                {"name": f"Lantern {value}", "kind": "miles"},
                {"name": "Lantern Air", "kind": "miles", "note": f"Ask {value}"},
            ):
                r = add_program(c, **body)
                assert r.status_code == 422, (value, r.text)
                assert value not in r.text and value.replace(" ", "") not in r.text
                assert "Remove that part" in r.json()["detail"]
            for body in ({"name": f"Lantern {value}"}, {"note": value}):
                r = c.patch(f"/api/rewards/programs/{key}", json=body, headers=H)
                assert r.status_code == 422 and value not in r.text
        # the key-like values give 404 or 422 and are never echoed
        for value in ("check-car-123", "mystery-account"):
            r = post_balances(c, entry(value, 1))
            assert r.status_code in (404, 422) and value not in r.text
            r = c.post(f"/api/rewards/programs/{key}/move", json={"to_key": value}, headers=H)
            assert r.status_code in (404, 422) and value not in r.text
    with read_connection(settings.db_path) as conn:
        stored = _text_columns(conn)
        dump = "\n".join(conn.iterdump())
    for value in [*flagged, "check-car-123", "mystery-account"]:
        assert value not in stored and value not in dump, value
    assert q(settings, "SELECT count(*) FROM reward_app_programs")[0][0] == 1


def test_a_key_made_from_a_name_is_scanned_too(
    use_private_rules: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = use_private_rules
    add_users(settings)
    import_samples(settings)
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    # Each name passes the scan, but its slug spells a private term in ASCII.
    names = {
        "Ex\u00e1mple Private Service": "example-private-service",
        "Example1Private2Service": "exampleprivateservice",
        "EXAMPLE EMPLOYER C\u00d6": "example-employer-co",
    }
    for name, slug in names.items():
        assert scan_text(name, private, "rewards") == [], name
        assert ri.slug(name) == slug
    app = app_for(monkeypatch, dev=False, BUDGET_PRIVATE_RULES=str(settings.private_rules))
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        for name, slug in names.items():
            r = add_program(c, name=name, kind="points")
            assert r.status_code == 422, (name, r.text)
            assert r.json()["detail"].startswith("The name looks like")
            assert name not in r.text and slug not in r.text
    for t in REWARD_TABLES:
        assert q(settings, f"SELECT count(*) FROM {t}")[0][0] == 0, t
    assert events(settings) == []


EVENT_FIELDS = {"action", "fields", "balances", "program_deleted"}
ACTIONS = {"add", "replace", "delete", "change", "delete_balances", "move", "keep_both"}


def test_events_hold_no_values(samples: Settings, admin: TestClient) -> None:
    body = {"name": "Harbor Miles", "kind": "miles", "note": "Pool with Riley"}
    key = add_program(admin, **body, cpp_low="1.2", cpp_high="1.6").json()["key"]
    r = admin.post(
        f"/api/rewards/programs/{key}/keep-both", json={"other_key": "harbor-miles"}, headers=H
    )
    assert r.status_code == 200
    r = post_balances(admin, entry(key, 52_345, "2026-10-15"), entry("pine-cash-back", "61.37"))
    assert r.status_code == 201
    assert post_balances(admin, entry(key, 52_346, "2026-10-15")).status_code == 201
    patch = admin.patch(f"/api/rewards/programs/{key}", json={"name": "Harbor Pool"}, headers=H)
    assert patch.status_code == 200
    bid = balance_id(admin, "pine-cash-back", "2026-10-20")
    assert admin.delete(f"/api/rewards/balances/{bid}", headers=H).status_code == 200
    assert (
        admin.post(
            f"/api/rewards/programs/{key}/move", json={"to_key": "harbor-miles"}, headers=H
        ).status_code
        == 200
    )
    rows = events(samples)
    assert [(r["entity"], r["entity_key"]) for r in rows] == [
        ("program", key),
        ("program", key),
        ("balance", key),
        ("balance", "pine-cash-back"),
        ("balance", key),
        ("program", key),
        ("balance", "pine-cash-back"),
        ("program", key),
    ]
    for r in rows:
        detail = json.loads(r["detail_json"])
        assert set(detail) <= EVENT_FIELDS and detail["action"] in ACTIONS
        assert set(detail.get("fields", [])) <= set(ri.PROGRAM_FIELDS)
        for v in detail.values():
            assert isinstance(v, bool | int | list) or v in ACTIONS
    text = "\n".join(r["detail_json"] for r in rows)
    for value in (str(52_345), str(52_346), "61.37", "Harbor Pool", "Riley", "1.2", "1.6"):
        assert value not in text


def test_service_writes_outside_a_write_transaction_raise(samples: Settings) -> None:
    calls = [
        lambda c: ri.add_balances(
            c, [("harbor-miles", "1", None)], today=TODAY, user_id=None, now=NOW
        ),
        lambda c: ri.delete_balance(c, 1, user_id=None, now=NOW),
        lambda c: ri.add_program(
            c, {"name": "Lantern Air", "kind": "miles"}, private=None, user_id=None, now=NOW
        ),
        lambda c: ri.update_program(c, "app-x", {"name": "X"}, private=None, user_id=None, now=NOW),
        lambda c: ri.delete_program(c, "app-x", user_id=None, now=NOW),
        lambda c: ri.move_balances(c, "app-x", "harbor-miles", user_id=None, now=NOW),
        lambda c: ri.keep_both(c, "app-x", "harbor-miles", user_id=None, now=NOW),
    ]
    conn = connect(samples.db_path)
    try:
        for call in calls:
            assert not conn.in_transaction
            with pytest.raises(RuntimeError):
                call(conn)
        # inside write_transaction the same call works
        with write_transaction(conn):
            assert ri.add_balances(
                conn, [("harbor-miles", "1", None)], today=TODAY, user_id=None, now=NOW
            ) == ["harbor-miles"]
    finally:
        conn.close()
    assert q(samples, "SELECT count(*) FROM reward_balances")[0][0] == 1


def test_logs_hold_no_values_on_the_new_routes(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    add_users(settings)
    app = app_for(monkeypatch, dev=False)
    with TestClient(app, base_url=PROD_URL) as c:  # lifespan installs the JSON handler
        logging.getLogger().setLevel(logging.DEBUG)
        import_samples(settings)
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        key = add_program(c, name="Lantern Air", kind="miles", note="Pool with Riley").json()["key"]
        post_balances(c, entry(key, 52_345, "2026-10-15"), entry("pine-cash-back", "61.37"))
        post_balances(c, entry("harbor-miles", f"{52_345}.5"))  # 422
        post_balances(c, entry("check-car-123", 1))  # 404
        add_program(c, name="Lantern QX7B2K", kind="miles")  # 422
        c.get(f"/api/rewards/programs/{key}/balances")
        c.patch(f"/api/rewards/programs/{key}", json={"note": f"Ask {PHONE}"}, headers=H)
        bid = balance_id(c, key, "2026-10-15")
        c.delete(f"/api/rewards/balances/{bid}", headers=H)
        c.post(f"/api/rewards/programs/{key}/move", json={"to_key": "harbor-miles"}, headers=H)
        c.post(f"/api/rewards/programs/{key}/keep-both", json={"other_key": "x"}, headers=H)
        c.delete(f"/api/rewards/programs/{key}", headers=H)
        c.get("/api/rewards")
    fmt = JsonFormatter()
    text = capsys.readouterr().out + "\n".join(fmt.format(r) for r in caplog.records)
    assert '"route": "/api/rewards/programs/{key}/balances"' in text
    assert '"route": "/api/rewards/balances/{balance_id}"' in text
    for value in (
        str(52_345),
        "61.37",
        "Lantern Air",
        "Pool with Riley",
        "QX7B2K",
        PHONE,
        "check-car-123",
        key,
        f"balances/{bid}",
    ):
        assert value not in text, value
