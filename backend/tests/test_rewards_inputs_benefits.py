"""Rewards inputs, chunk C (docs/plans/rewards-inputs.md sections 3.2, 6 and 8):
benefit edits, the overlay rules 1 to 11, the routes, and the safety rules on
them.

Fake samples only. Household today is 2026-10-20. Summit card: Dining credit
(monthly $10, used January to August, $6 in September, a $20 December, with a
counted value), Yearly travel credit (annual $100, enroll by Oct 31), Airline
fee credit (semi-annual $50), a free night and a status perk. Cobalt card:
Streaming credit (quarterly $15, used in Q1 to Q3) and the Fuel bonus cap.
Orchard card: Wireless credit (monthly $5, auto).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import get_args

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
from app.api.rewards_models import BenefitField
from app.config import Settings
from app.db.connect import connect, read_connection, write_connection, write_transaction
from app.importer.models import KEY_MAX, KEY_PATTERN, RewardBenefit
from app.importer.sanitize import load_private_rules, scan_text
from app.logging import JsonFormatter
from app.main import walk_routes
from app.services import rewards_inputs as ri
from app.services import rewards_match as rm
from app.services import rewards_overlay as ov
from app.services.balances import clear_cache
from app.services.overrides import UNKNOWN_CATEGORY
from app.state import readers

TODAY = date(2026, 10, 20)
H = {"Origin": PROD_ORIGIN}
NOW = "2026-10-20T12:00:00+00:00"
PHONE = INJECTED_VALUES[6]
REWARD_TABLES = (
    "reward_balances",
    "reward_app_programs",
    "reward_benefit_edits",
    "reward_match_terms",
    "reward_credit_decisions",
    "reward_cycle_marks",
)
LOUNGE = {
    "name": "Lounge credit",
    "kind": "credit",
    "account_key": "summit-card",
    "cadence": "quarterly",
    "amount": "25.00",
}


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


def add(c: TestClient, **body):
    return c.post("/api/rewards/benefits", json=body, headers=H)


def patch(c: TestClient, key: str, **body):
    return c.patch(f"/api/rewards/benefits/{key}", json=body, headers=H)


def edit(c: TestClient, key: str, **fields) -> dict:
    r = patch(c, key, set=fields)
    assert r.status_code == 200, r.text
    return r.json()


def resolve(c: TestClient, key: str, field: str, choice: str):
    return c.post(
        f"/api/rewards/benefits/{key}/resolve", json={"field": field, "choice": choice}, headers=H
    )


def move(c: TestClient, key: str, to_key: str):
    return c.post(f"/api/rewards/benefits/{key}/move", json={"to_key": to_key}, headers=H)


def keep_both(c: TestClient, key: str, other_key: str):
    return c.post(
        f"/api/rewards/benefits/{key}/keep-both", json={"other_key": other_key}, headers=H
    )


def page(c: TestClient) -> dict:
    r = c.get("/api/rewards")
    assert r.status_code == 200
    return r.json()


def ben(body: dict, key: str) -> dict | None:
    return next((b for b in body["benefits"] if b["key"] == key), None)


def fee_credits(body: dict, account_key: str) -> dict[str, dict]:
    """The card's credits on its fee rows, by key."""
    return {
        c["key"]: c
        for row in body["fees"]["rows"]
        if row["account_key"] == account_key
        for c in row["credits"]
    }


def amounts(b: dict) -> list[tuple[str, int]]:
    """(start, amount) of every cycle shown, past and this one."""
    cycles = [*b["past_cycles"], *([b["cycle"]] if b["cycle"] else [])]
    return [(c["start"], c["amount_cents"]) for c in cycles]


def q(settings: Settings, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with read_connection(settings.db_path) as conn:
        return conn.execute(sql, params).fetchall()


def edit_row(settings: Settings, key: str) -> dict | None:
    rows = q(settings, "SELECT * FROM reward_benefit_edits WHERE benefit_key = ?", (key,))
    if not rows:
        return None
    row = dict(rows[0])
    row["fields"], row["base"] = json.loads(row["fields_json"]), json.loads(row["base_json"])
    return row


def events(settings: Settings) -> list[sqlite3.Row]:
    return q(
        settings,
        "SELECT entity, entity_key, detail_json FROM events WHERE kind = 'rewards_edit' "
        "ORDER BY id",
    )


def details(settings: Settings) -> list[dict]:
    return [json.loads(e["detail_json"]) for e in events(settings)]


def counts(settings: Settings) -> dict[str, int]:
    return {t: q(settings, f"SELECT count(*) FROM {t}")[0][0] for t in REWARD_TABLES}


def chat_rewards(settings: Settings) -> dict:
    with read_connection(settings.db_path) as conn:
        r = readers.rewards(readers.monthly_runs(conn))
    assert r is not None
    return r.model_dump(mode="json", exclude_none=True)


def send(settings: Settings, change: Callable[[list[dict]], list[dict]]) -> None:
    """The chat's next monthly run: the same rewards with `change` applied to
    the benefits. Each call is a newer run."""

    def benefits(data: dict) -> dict:
        return data | {"benefits": sorted(change(data["benefits"]), key=lambda b: b["key"])}

    send_all(settings, benefits)


def send_all(settings: Settings, change: Callable[[dict], dict]) -> None:
    """The chat's next monthly run: the same rewards with `change` applied to
    all of them. Each call is a newer run."""
    data = change(chat_rewards(settings))
    n = q(settings, "SELECT count(*) FROM import_runs WHERE run_id LIKE '2026-10-15%'")[0][0]
    run_id = "2026-10-15" if n == 0 else f"2026-10-15-{n + 1}"
    with write_connection(settings.db_path) as w:
        w.execute(
            """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at,
                   schema_version)
               VALUES (?, '2026-10-15', ?, 'monthly', 'imported', '2026-10-20T00:00:00+00:00',
                   '1.3')""",
            (run_id, n + 1),
        )
        w.execute(
            "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, ?, ?)",
            (run_id, "rewards", f"{run_id}-rewards".ljust(64, "0")[:64], json.dumps(data)),
        )


def changed(key: str, **fields) -> Callable[[list[dict]], list[dict]]:
    """A send() change: one benefit with these fields (None drops a field)."""

    def change(benefits: list[dict]) -> list[dict]:
        for b in benefits:
            if b["key"] == key:
                for f, v in fields.items():
                    if v is None:
                        b.pop(f, None)
                    else:
                        b[f] = v
        return benefits

    return change


def dropped(*keys: str) -> Callable[[list[dict]], list[dict]]:
    return lambda benefits: [b for b in benefits if b["key"] not in keys]


def add_inputs(settings: Settings, key: str, *, term: str = "DINING CREDIT", offset: int = 0):
    """A match word, a credit choice and a cycle mark on the key (chunk D2 writes
    these; here they go in by hand)."""
    with write_connection(settings.db_path) as w:
        txn_key = w.execute(
            "SELECT txn_key FROM transactions ORDER BY txn_key LIMIT 1 OFFSET ?", (offset,)
        ).fetchone()[0]
        by = (None, NOW)
        w.execute(
            """INSERT INTO reward_match_terms (benefit_key, term, updated_by, updated_at)
               VALUES (?, ?, ?, ?)""",
            (key, term, *by),
        )
        guard = {"account_key": "summit-card", "txn_date": "2026-09-12", "amount_cents": 1_000}
        w.execute(
            """INSERT INTO reward_credit_decisions (txn_key, decision, benefit_key, cycle_start,
                   guard_json, updated_by, updated_at)
               VALUES (?, 'link', ?, '2026-09-01', ?, ?, ?)""",
            (txn_key, key, json.dumps(guard), *by),
        )
        w.execute(
            """INSERT INTO reward_cycle_marks (benefit_key, cycle_start, used_cents, used_on,
                   updated_by, updated_at)
               VALUES (?, '2026-10-01', ?, '2026-10-05', ?, ?)""",
            (key, 1_000, *by),
        )


def held(settings: Settings, key: str) -> dict[str, int]:
    return {
        t: q(settings, f"SELECT count(*) FROM {t} WHERE benefit_key = ?", (key,))[0][0]
        for t in REWARD_TABLES[2:]
    }


# --------------------------------------------------------------------------
# The new fields, and rule 1 (no edit row: shown as sent)
# --------------------------------------------------------------------------


def test_new_benefit_fields_with_no_app_rows(samples: Settings, admin: TestClient) -> None:
    body = page(admin)
    assert body["hidden"] == []
    for b in body["benefits"]:
        assert (b["origin"], b["edited_fields"], b["notices"]) == ("chat", [], [])
        assert (b["hidden"], b["match_off"], b["terms_count"]) == (False, False, 0)


def test_the_editable_fields_agree() -> None:
    assert get_args(BenefitField) == ov.EDITABLE_FIELDS
    assert set(ov.EDITABLE_FIELDS) | set(ov.FIXED_FIELDS) == set(RewardBenefit.model_fields)


def test_calendar_cycles() -> None:
    assert ov.calendar_cycles("semi_annual", 2026) == [
        {"start": "2026-01-01", "end": "2026-06-30"},
        {"start": "2026-07-01", "end": "2026-12-31"},
    ]
    feb = ov.calendar_cycles("monthly", 2028)[1]
    assert feb == {"start": "2028-02-01", "end": "2028-02-29"}
    assert len(ov.calendar_cycles("quarterly", 2027)) == 4
    assert ov.calendar_cycles("annual", 2027) == [{"start": "2027-01-01", "end": "2027-12-31"}]


# --------------------------------------------------------------------------
# Rule 2: change and reset
# --------------------------------------------------------------------------


def test_change_and_reset(samples: Settings, admin: TestClient) -> None:
    before = ben(page(admin), "dining-credit")
    out = edit(admin, "dining-credit", note="Use it on Fridays", how_to_use="Order ahead")
    assert out["edited_fields"] == ["how_to_use", "note"] and out["origin"] == "chat"
    assert (out["note"], out["how_to_use"]) == ("Use it on Fridays", "Order ahead")
    assert out == ben(page(admin), "dining-credit")  # the fresh piece is what the page shows
    row = edit_row(samples, "dining-credit")
    assert row is not None and (row["origin"], row["account_key"]) == ("chat", "summit-card")
    assert row["fields"] == {"how_to_use": "Order ahead", "note": "Use it on Fridays"}
    assert row["base"] == {"how_to_use": "Order from the partner delivery app", "note": None}
    # the chat's own value is not an edit
    out = edit(admin, "dining-credit", how_to_use="Order from the partner delivery app")
    assert out["edited_fields"] == ["note"]
    assert edit_row(samples, "dining-credit")["fields"] == {"note": "Use it on Fridays"}
    # a null clears a field the chat sends
    out = edit(admin, "dining-credit", how_to_use=None)
    assert out["how_to_use"] is None and out["edited_fields"] == ["how_to_use", "note"]
    # reset one field
    r = patch(admin, "dining-credit", reset=["how_to_use"])
    assert r.status_code == 200 and r.json()["edited_fields"] == ["note"]
    assert r.json()["how_to_use"] == before["how_to_use"]
    # "Use the chat's" for every field: the row goes
    r = admin.delete("/api/rewards/benefits/dining-credit/edits", headers=H)
    assert r.status_code == 200 and r.json() == before
    assert edit_row(samples, "dining-credit") is None
    r = admin.delete("/api/rewards/benefits/dining-credit/edits", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ri.NOTHING
    assert ben(page(admin), "dining-credit") == before


@pytest.mark.parametrize(
    ("key", "body", "status", "detail"),
    [
        ("dining-credit", {"set": {"key": "x"}}, 422, None),
        ("dining-credit", {"set": {"account_key": "cobalt-card"}}, 422, None),
        ("dining-credit", {"set": {"kind": "other"}}, 422, None),
        ("dining-credit", {"set": {"cadence": "annual"}}, 422, None),
        ("dining-credit", {"set": {"cycles": []}}, 422, None),
        ("dining-credit", {"reset": ["kind"]}, 422, None),
        ("dining-credit", {"set": {"note": "A"}, "reset": ["note"]}, 422, ri.SET_AND_RESET),
        ("dining-credit", {"set": {"amount": None}}, 422, ri.BAD_FIT),
        ("dining-credit", {"set": {"program_key": "lantern-hotels"}}, 422, ri.BAD_FIT),
        ("anniversary-night", {"set": {"amount": "5.00"}}, 422, ri.BAD_FIT),
        ("anniversary-night", {"set": {"program_key": "app-nope"}}, 422, ri.UNKNOWN_PROGRAM),
        ("fuel-bonus", {"set": {"category_keys": ["no-such"]}}, 422, UNKNOWN_CATEGORY),
        ("fuel-bonus", {"set": {"reported_spent": None}}, 422, ri.BAD_FIT),
        ("fuel-bonus", {"match_off": True}, 422, ri.MATCH_OFF_CREDITS),
        ("dining-credit", {}, 422, ri.NOTHING),
        ("dining-credit", {"set": {}}, 422, ri.NOTHING),
        ("dining-credit", {"amount_from": "2026-06-01"}, 422, ri.AMOUNT_FROM),
        ("dining-credit", {"set": {"amount": "-1.00"}}, 422, None),
        ("dining-credit", {"set": {"amount": "12"}}, 422, None),
        ("dining-credit", {"set": {"name": "\u200b\u200d"}}, 422, None),
        ("no-such-benefit", {"set": {"note": "A"}}, 404, ri.NO_BENEFIT),
        ("NOPE", {"set": {"note": "A"}}, 422, None),
    ],
)
def test_change_rules(
    samples: Settings, admin: TestClient, key: str, body: dict, status: int, detail
) -> None:
    before = page(admin)
    r = patch(admin, key, **body)
    assert r.status_code == status, r.text
    if detail is not None:
        assert r.json()["detail"] == detail
    assert counts(samples)["reward_benefit_edits"] == 0 and events(samples) == []
    assert page(admin) == before


def test_category_keys_are_sorted_and_checked(samples: Settings, admin: TestClient) -> None:
    out = admin.patch(
        "/api/rewards/benefits/fuel-bonus",
        json={"set": {"category_keys": ["groceries", "fuel", "groceries"]}},
        headers=H,
    )
    assert out.status_code == 200, out.text
    assert edit_row(samples, "fuel-bonus")["fields"] == {"category_keys": ["fuel", "groceries"]}
    cap = next(c for c in page(admin)["caps"] if c["key"] == "fuel-bonus")
    assert [c["key"] for c in cap["categories"]] == ["fuel", "groceries"]


# --------------------------------------------------------------------------
# Rule 3: the chat moves under an edit
# --------------------------------------------------------------------------


def test_the_chat_moves_under_an_edit(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", note="Mine")
    send(samples, lambda bs: bs)  # the chat sends the same: quiet
    body = page(admin)
    b = ben(body, "dining-credit")
    assert (b["note"], b["edited_fields"], b["notices"]) == ("Mine", ["note"], [])
    assert body["notices"] == []
    # the chat moves: the owner's stays, with a notice
    send(samples, changed("dining-credit", note="Theirs"))
    body = page(admin)
    b = ben(body, "dining-credit")
    assert (b["note"], b["edited_fields"]) == ("Mine", ["note"])
    assert b["notices"] == [{"kind": "benefit_chat_changed", "fields": ["note"]}]
    assert body["notices"] == [
        {
            "kind": "benefit_chat_changed",
            "text": "The chat changed the note since your edit.",
            "key": "dining-credit",
            "other_key": None,
            "fields": ["note"],
        }
    ]
    # Keep mine: base becomes the chat's value, the notice goes
    r = resolve(admin, "dining-credit", "note", "mine")
    assert r.status_code == 200, r.text
    assert (r.json()["note"], r.json()["notices"]) == ("Mine", [])
    assert edit_row(samples, "dining-credit")["base"] == {"note": "Theirs"}
    assert page(admin)["notices"] == []
    # the chat moves again, and Use the chat's drops the edit
    send(samples, changed("dining-credit", note="Theirs again"))
    assert ben(page(admin), "dining-credit")["notices"] != []
    r = resolve(admin, "dining-credit", "note", "chat")
    assert r.status_code == 200
    assert (r.json()["note"], r.json()["edited_fields"], r.json()["notices"]) == (
        "Theirs again",
        [],
        [],
    )
    assert edit_row(samples, "dining-credit") is None
    assert [d["action"] for d in details(samples)] == ["change", "keep_mine", "use_chat"]


def test_the_chat_catching_up_ends_the_edit(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", note="Mine", how_to_use="Order ahead")
    send(samples, changed("dining-credit", note="Mine"))
    body = page(admin)
    b = ben(body, "dining-credit")
    assert (b["note"], b["edited_fields"], b["notices"]) == ("Mine", ["how_to_use"], [])
    assert body["notices"] == []


def test_a_caught_up_field_goes_at_the_next_write(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", note="Mine", how_to_use="Order ahead")
    send(samples, changed("dining-credit", note="Mine"))
    # With no write on the row, a third value brings the owner's back, with a notice.
    send(samples, changed("dining-credit", note="Theirs"))
    b = ben(page(admin), "dining-credit")
    assert (b["note"], b["edited_fields"]) == ("Mine", ["how_to_use", "note"])
    assert b["notices"] == [{"kind": "benefit_chat_changed", "fields": ["note"]}]
    assert resolve(admin, "dining-credit", "note", "chat").status_code == 200
    # A write on the row drops a field the chat caught up on.
    edit(admin, "dining-credit", note="Mine")
    send(samples, changed("dining-credit", note="Mine"))
    assert patch(admin, "dining-credit", hidden=True).status_code == 200
    assert edit_row(samples, "dining-credit")["fields"] == {"how_to_use": "Order ahead"}
    send(samples, changed("dining-credit", note="Theirs"))
    b = next(h for h in page(admin)["hidden"] if h["key"] == "dining-credit")
    r = patch(admin, "dining-credit", hidden=False)
    assert (r.json()["note"], r.json()["edited_fields"], r.json()["notices"]) == (
        "Theirs",
        ["how_to_use"],
        [],
    )


def test_notices_name_fields_as_the_drawer_does(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", counted_value="80.00", auto=True)
    send(samples, changed("dining-credit", counted_value="90.00", auto=False))
    texts = [n["text"] for n in page(admin)["notices"]]
    assert texts == [
        "The chat changed whether it loads by itself since your edit.",
        "The chat changed the yearly value since your edit.",
    ]


@pytest.mark.parametrize(
    ("key", "field", "choice", "status", "detail"),
    [
        ("dining-credit", "name", "mine", 422, ri.NOT_EDITED),
        ("dining-credit", "kind", "mine", 422, None),
        ("dining-credit", "note", "maybe", 422, None),
        ("no-such-benefit", "note", "mine", 404, ri.NO_BENEFIT),
    ],
)
def test_resolve_rules(
    samples: Settings, admin: TestClient, key: str, field: str, choice: str, status: int, detail
) -> None:
    edit(admin, "dining-credit", note="Mine")
    r = resolve(admin, key, field, choice)
    assert r.status_code == status, r.text
    if detail is not None:
        assert r.json()["detail"] == detail
    assert edit_row(samples, "dining-credit")["fields"] == {"note": "Mine"}


# --------------------------------------------------------------------------
# Rule 4: amounts
# --------------------------------------------------------------------------


def test_an_amount_change_starts_this_cycle_by_default(
    samples: Settings, admin: TestClient
) -> None:
    before = page(admin)
    out = edit(admin, "streaming-credit", amount="20.00")
    assert out["amount_cents"] == 2_000 and out["edited_fields"] == ["amount"]
    assert amounts(out) == [
        ("2026-01-01", 1_500),
        ("2026-04-01", 1_500),
        ("2026-07-01", 1_500),
        ("2026-10-01", 2_000),
    ]
    assert edit_row(samples, "streaming-credit")["amount_from"] == "2026-10-01"
    body = page(admin)
    assert body["tiles"]["credits_left_cents"] == before["tiles"]["credits_left_cents"] + 500
    assert out["amount_from"] == "2026-10-01"
    # a start date further back
    r = patch(admin, "streaming-credit", amount_from="2026-04-01")
    assert r.status_code == 200, r.text
    assert [a for _, a in amounts(r.json())] == [1_500, 2_000, 2_000, 2_000]
    assert r.json()["amount_from"] == "2026-04-01"
    # with the amount, in one change, from an earlier day
    r = patch(admin, "streaming-credit", set={"amount": "25.00"}, amount_from="2026-01-01")
    assert [a for _, a in amounts(r.json())] == [2_500, 2_500, 2_500, 2_500]
    # resetting the amount takes its start date too
    r = patch(admin, "streaming-credit", reset=["amount"])
    assert r.status_code == 200 and edit_row(samples, "streaming-credit") is None
    assert page(admin) == before
    # Fees count the year's cycles at their own amounts (the airline credit, on a card
    # with a fee: $50 in the first half, $60 from July)
    assert fee_credits(before, "summit-card")["airline-fee-credit"]["cents"] == 10_000
    out = edit(admin, "airline-fee-credit", amount="60.00")
    assert amounts(out) == [("2026-01-01", 5_000), ("2026-07-01", 6_000)]
    assert fee_credits(page(admin), "summit-card")["airline-fee-credit"]["cents"] == 11_000


def test_a_cycle_with_its_own_amount_keeps_it(samples: Settings, admin: TestClient) -> None:
    before = fee_credits(page(admin), "summit-card")["dining-credit"]
    out = edit(admin, "dining-credit", amount="12.00")
    got = dict(amounts(out))
    assert got["2026-09-01"] == 1_000 and got["2026-10-01"] == 1_200
    after = fee_credits(page(admin), "summit-card")["dining-credit"]
    # counted_value stays the Fees number; the cycles' sum moves (Oct, Nov at $12, a $20 Dec)
    assert after["cents"] == before["cents"] == 10_000 and after["counted"] is True
    assert after["full_cents"] == before["full_cents"] + 400


def test_the_chat_changes_the_amount_under_an_edit(samples: Settings, admin: TestClient) -> None:
    edit(admin, "streaming-credit", amount="20.00")
    send(samples, changed("streaming-credit", amount="18.00"))
    b = ben(page(admin), "streaming-credit")
    # earlier cycles keep the chat's amount, the owner's starts in October
    assert [a for _, a in amounts(b)] == [1_800, 1_800, 1_800, 2_000]
    assert b["notices"] == [{"kind": "benefit_chat_changed", "fields": ["amount"]}]
    r = resolve(admin, "streaming-credit", "amount", "chat")
    assert r.status_code == 200
    assert [a for _, a in amounts(r.json())] == [1_800] * 4
    assert edit_row(samples, "streaming-credit") is None
    # the chat sends the owner's amount: no longer an edit, every cycle the chat's
    edit(admin, "streaming-credit", amount="20.00")
    send(samples, changed("streaming-credit", amount="20.00"))
    b = ben(page(admin), "streaming-credit")
    assert [a for _, a in amounts(b)] == [2_000] * 4 and b["edited_fields"] == []
    assert b["amount_from"] is None


def test_an_app_amount_change_pins_amount_before(samples: Settings, admin: TestClient) -> None:
    key = add(admin, **LOUNGE | {"cadence": "monthly", "amount": "10.00"}).json()["key"]
    r = patch(admin, key, set={"amount": "15.00"}, amount_from="2026-07-01")
    assert r.status_code == 200, r.text
    got = dict(amounts(r.json()))
    assert got["2026-06-01"] == 1_000 and got["2026-07-01"] == 1_500
    assert got["2026-10-01"] == 1_500 and r.json()["amount_cents"] == 1_500
    row = edit_row(samples, key)
    assert (row["amount_before"], row["amount_from"]) == ("10.00", "2026-07-01")
    assert row["fields"]["amount"] == "15.00" and row["base"] == {}
    r = patch(admin, key, amount_from="2026-09-01")
    assert dict(amounts(r.json()))["2026-08-01"] == 1_000
    full = fee_credits(page(admin), "summit-card")[key]["full_cents"]
    assert full == 8 * 1_000 + 4 * 1_500
    # no amount change, no start date
    other = add(admin, **LOUNGE | {"name": "Spa credit"}).json()["key"]
    r = patch(admin, other, amount_from="2026-09-01")
    assert r.status_code == 422 and r.json()["detail"] == ri.AMOUNT_FROM


def test_a_second_amount_change_keeps_the_first_start(samples: Settings, admin: TestClient) -> None:
    out = edit(admin, "streaming-credit", amount="20.00")
    r = patch(admin, "streaming-credit", amount_from="2026-04-01")
    assert [a for _, a in amounts(r.json())] == [1_500, 2_000, 2_000, 2_000]
    # a typo fix with no start keeps the first change's start
    r = patch(admin, "streaming-credit", set={"amount": "25.00"})
    assert r.status_code == 200, r.text
    assert [a for _, a in amounts(r.json())] == [1_500, 2_500, 2_500, 2_500]
    assert r.json()["amount_from"] == "2026-04-01"
    # the drawer sends the start it shows, which is that same day
    r = patch(admin, "streaming-credit", set={"amount": "30.00"}, amount_from="2026-04-01")
    assert [a for _, a in amounts(r.json())] == [1_500, 3_000, 3_000, 3_000]
    # a later start would put Q2 and Q3 back to the chat's $15: refused
    before = page(admin)
    r = patch(admin, "streaming-credit", set={"amount": "35.00"}, amount_from="2026-10-01")
    assert r.status_code == 422 and r.json()["detail"] == ri.AMOUNT_LATER
    assert page(admin) == before
    assert edit_row(samples, "streaming-credit")["amount_from"] == "2026-04-01"
    # an earlier one is fine
    r = patch(admin, "streaming-credit", set={"amount": "35.00"}, amount_from="2026-01-01")
    assert [a for _, a in amounts(r.json())] == [3_500] * 4
    assert out["amount_from"] == "2026-10-01"


def test_a_second_app_amount_change_keeps_the_amount_before(
    samples: Settings, admin: TestClient
) -> None:
    key = add(admin, **LOUNGE | {"cadence": "monthly", "amount": "10.00"}).json()["key"]
    assert ben(page(admin), key)["amount_from"] is None
    r = patch(admin, key, set={"amount": "15.00"}, amount_from="2026-07-01")
    assert r.json()["amount_from"] == "2026-07-01"
    r = patch(admin, key, set={"amount": "16.00"})  # a typo fix
    got = dict(amounts(r.json()))
    assert (got["2026-06-01"], got["2026-07-01"], got["2026-10-01"]) == (1_000, 1_600, 1_600)
    row = edit_row(samples, key)
    assert (row["amount_before"], row["amount_from"]) == ("10.00", "2026-07-01")
    r = patch(admin, key, set={"amount": "17.00"}, amount_from="2026-09-01")
    assert r.status_code == 422 and r.json()["detail"] == ri.AMOUNT_LATER
    # back to the amount before any change: no start is kept, and one sent is let go
    r = patch(admin, key, set={"amount": "10.00"}, amount_from="2026-10-01")
    assert r.status_code == 200, r.text
    assert {a for _, a in amounts(r.json())} == {1_000} and r.json()["amount_from"] is None
    row = edit_row(samples, key)
    assert (row["amount_before"], row["amount_from"]) == (None, None)


def test_typing_the_chats_amount_back_takes_no_start(samples: Settings, admin: TestClient) -> None:
    before = ben(page(admin), "streaming-credit")
    edit(admin, "streaming-credit", amount="20.00")
    # as the drawer sends it: the typed amount with the start it shows
    r = patch(admin, "streaming-credit", set={"amount": "15.00"}, amount_from="2026-10-01")
    assert r.status_code == 200, r.text
    assert r.json() == before and edit_row(samples, "streaming-credit") is None
    # a start with no amount and no change is still refused
    r = patch(admin, "streaming-credit", amount_from="2026-10-01")
    assert r.status_code == 422 and r.json()["detail"] == ri.AMOUNT_FROM


# --------------------------------------------------------------------------
# Rule 5: end
# --------------------------------------------------------------------------


def test_an_end_trims_cycles_and_fees(samples: Settings, admin: TestClient) -> None:
    before = page(admin)
    out = edit(admin, "streaming-credit", ends_on="2026-05-15")
    assert out["group"] == "ended" and out["ends_on"] == "2026-05-15" and out["cycle"] is None
    assert [(c["start"], c["end"]) for c in out["past_cycles"]] == [
        ("2026-01-01", "2026-03-31"),
        ("2026-04-01", "2026-05-15"),
    ]
    body = page(admin)
    assert body["tiles"]["credits_left_cents"] == before["tiles"]["credits_left_cents"] - 1_500
    # undo: Use the chat's on ends_on
    r = patch(admin, "streaming-credit", reset=["ends_on"])
    assert r.status_code == 200 and r.json() == ben(before, "streaming-credit")
    # Fees stop counting after the end (the chat ends the airline credit on Dec 31)
    out = edit(admin, "airline-fee-credit", ends_on="2026-05-15")
    assert out["group"] == "ended" and edit_row(samples, "airline-fee-credit")["base"] == {
        "ends_on": "2026-12-31"
    }
    credit = fee_credits(page(admin), "summit-card")["airline-fee-credit"]
    assert (credit["cents"], credit["full_cents"]) == (5_000, 5_000)
    assert patch(admin, "airline-fee-credit", reset=["ends_on"]).status_code == 200
    assert page(admin) == before
    # an end today keeps today's cycle to today
    out = edit(admin, "travel-credit", ends_on="2026-10-20")
    assert out["cycle"]["end"] == "2026-10-20" and out["deadline"] == "2026-10-20"
    assert out["enroll_by"] == "2026-10-20"


def test_an_end_you_set_cuts_a_counted_value(samples: Settings, admin: TestClient) -> None:
    before = fee_credits(page(admin), "summit-card")["dining-credit"]
    assert (before["cents"], before["full_cents"], before["counted"]) == (10_000, 13_000, True)
    edit(admin, "dining-credit", ends_on="2026-03-31")
    after = fee_credits(page(admin), "summit-card")["dining-credit"]
    # at most the cycles up to the end: January to March at $10
    assert (after["cents"], after["full_cents"], after["counted"]) == (3_000, 3_000, True)
    # an end late in the year leaves the yearly value as it was ($110 of cycles)
    edit(admin, "dining-credit", ends_on="2026-11-30")
    assert fee_credits(page(admin), "summit-card")["dining-credit"]["cents"] == 10_000
    assert patch(admin, "dining-credit", reset=["ends_on"]).status_code == 200
    # the chat's own end inside the year leaves its counted value whole (as in R1)
    send(samples, changed("dining-credit", ends_on="2026-03-31"))
    assert fee_credits(page(admin), "summit-card")["dining-credit"]["cents"] == 10_000
    # an app credit's end is the owner's
    body = LOUNGE | {"counted_value": "80.00", "ends_on": "2026-04-15"}
    key = add(admin, **body).json()["key"]
    credit = fee_credits(page(admin), "summit-card")[key]
    assert (credit["cents"], credit["full_cents"]) == (5_000, 5_000)


# --------------------------------------------------------------------------
# Rule 6: hide
# --------------------------------------------------------------------------


def test_a_hidden_benefit_leaves_every_reader(samples: Settings, admin: TestClient) -> None:
    before = page(admin)
    checks = admin.get("/api/checks").json()["rewards"]
    line = admin.get("/api/dashboard").json()["credits_ending"]
    assert before["tiles"]["next_deadline"]["key"] == "travel-credit"
    assert line == {"count": 1, "left_cents": 1_000, "first_end": "2026-10-31"}
    assert "travel-credit" in fee_credits(before, "summit-card")
    assert [g["key"] for g in checks["gaps"] if g["kind"] == "cap"] == ["fuel-bonus"]

    r = patch(admin, "travel-credit", hidden=True)
    assert (
        r.status_code == 200 and r.json()["hidden"] is True and r.json()["key"] == "travel-credit"
    )
    body = page(admin)
    assert ben(body, "travel-credit") is None
    assert body["hidden"] == [
        {
            "key": "travel-credit",
            "name": "Yearly travel credit",
            "kind": "credit",
            "account_key": "summit-card",
            "account_name": ben(before, "travel-credit")["account_name"],
            "origin": "chat",
        }
    ]
    tiles, old = body["tiles"], before["tiles"]
    assert tiles["credits_left_cents"] == old["credits_left_cents"] - 10_000
    assert tiles["credits_left_count"] == old["credits_left_count"] - 1
    assert tiles["next_deadline"]["key"] == "dining-credit"
    # Fees: hidden benefits leave (plan 13, default 3), so the card's net fee goes up
    assert "travel-credit" not in fee_credits(body, "summit-card")
    assert body["fees"]["credits_cents"] == before["fees"]["credits_cents"] - 10_000
    assert body["fees"]["net_cents"] == before["fees"]["net_cents"] + 10_000
    row = edit_row(samples, "travel-credit")
    assert (row["hidden"], row["fields"]) == (1, {})

    # the Dashboard line and the Checks cap lines
    assert patch(admin, "dining-credit", hidden=True).status_code == 200
    assert admin.get("/api/dashboard").json()["credits_ending"] is None
    assert patch(admin, "fuel-bonus", hidden=True).status_code == 200
    body = page(admin)
    assert body["caps"] == [] and [h["key"] for h in body["hidden"]] == [
        "dining-credit",
        "fuel-bonus",
        "travel-credit",
    ]
    checks = admin.get("/api/checks").json()["rewards"]
    assert [g for g in checks["gaps"] if g["kind"] == "cap"] == []

    # it stays hidden when the chat sends it again
    send(samples, lambda bs: bs)
    assert {h["key"] for h in page(admin)["hidden"]} == {
        "dining-credit",
        "fuel-bonus",
        "travel-credit",
    }
    # Show again: back as before, and the empty rows go
    for key in ("dining-credit", "fuel-bonus", "travel-credit"):
        r = patch(admin, key, hidden=False)
        assert r.status_code == 200 and r.json()["hidden"] is False
    assert page(admin) == before | {"source_run_id": "2026-10-15"}
    assert counts(samples)["reward_benefit_edits"] == 0


def test_a_hidden_benefit_keeps_its_edits(samples: Settings, admin: TestClient) -> None:
    edit(admin, "travel-credit", note="Book by spring")
    r = patch(admin, "travel-credit", hidden=True)
    assert r.json()["note"] == "Book by spring" and r.json()["edited_fields"] == ["note"]
    r = patch(admin, "travel-credit", hidden=False)
    assert r.json()["note"] == "Book by spring"
    assert edit_row(samples, "travel-credit")["hidden"] == 0


# --------------------------------------------------------------------------
# Rule 7: add
# --------------------------------------------------------------------------


def test_an_added_benefit_gets_calendar_cycles(
    samples: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = page(admin)
    r = add(admin, **LOUNGE, how_to_use="Show the card at the desk")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["key"] == "app-lounge-credit" and out["origin"] == "app"
    assert (out["edited_fields"], out["notices"]) == ([], [])
    assert out["cycle"]["start"] == "2026-10-01" and out["cycle"]["end"] == "2026-12-31"
    assert [c["start"] for c in out["past_cycles"]] == ["2026-01-01", "2026-04-01", "2026-07-01"]
    assert out["group"] == "open" and out["amount_cents"] == 2_500
    body = page(admin)
    assert ben(body, "app-lounge-credit") == out
    assert fee_credits(body, "summit-card")["app-lounge-credit"]["cents"] == 10_000
    assert body["tiles"]["credits_left_cents"] == before["tiles"]["credits_left_cents"] + 2_500
    row = edit_row(samples, "app-lounge-credit")
    assert (row["origin"], row["account_key"], row["base"]) == ("app", "summit-card", {})
    assert row["fields"] == LOUNGE | {
        "key": "app-lounge-credit",
        "how_to_use": "Show the card at the desk",
    }  # no cycles: they come at read time
    assert details(samples) == [
        {
            "action": "add",
            "fields": ["amount", "cadence", "how_to_use", "kind", "name"],
        }
    ]
    # "left out means ended" never touches it, and it rolls into the new year
    send(samples, lambda bs: bs)
    assert ben(page(admin), "app-lounge-credit") is not None
    on(monkeypatch, date(2027, 2, 1))
    b = ben(page(admin), "app-lounge-credit")
    assert b["cycle"]["start"] == "2027-01-01" and b["past_cycles"] == []


def test_multi_year_and_one_time_store_their_cycles(samples: Settings, admin: TestClient) -> None:
    cycles = [
        {"start": "2027-01-01", "end": "2027-12-31", "amount": "60.00"},
        {"start": "2025-01-01", "end": "2026-12-31"},
    ]
    r = add(
        admin, **LOUNGE | {"name": "Global entry credit", "cadence": "multi_year"}, cycles=cycles
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["cycle"]["start"] == "2025-01-01" and out["cycle"]["amount_cents"] == 2_500
    row = edit_row(samples, out["key"])
    assert row["fields"]["cycles"] == [cycles[1], cycles[0]]  # in start order
    r = add(
        admin,
        **LOUNGE | {"name": "Welcome credit", "cadence": "one_time"},
        cycles=[{"start": "2026-06-01", "end": "2026-12-31"}],
    )
    assert r.status_code == 201 and r.json()["cycle"]["end"] == "2026-12-31"


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (LOUNGE | {"cadence": "multi_year"}, ri.NEEDS_CYCLES),
        (LOUNGE | {"cycles": [{"start": "2026-01-01", "end": "2026-03-31"}]}, ri.NO_CYCLES),
        (
            LOUNGE
            | {
                "cadence": "one_time",
                "cycles": [{"start": "2026-01-01", "end": "2026-12-31", "used": "25.00"}],
            },
            None,
        ),
        (
            LOUNGE
            | {
                "cadence": "one_time",
                "cycles": [{"start": "2026-01-01", "end": "2026-12-31", "used_on": "2026-02-01"}],
            },
            None,
        ),
        (
            LOUNGE
            | {
                "cadence": "one_time",
                "cycles": [
                    {"start": "2026-01-01", "end": "2026-06-30"},
                    {"start": "2026-01-01", "end": "2026-12-31"},
                ],
            },
            ri.CYCLE_STARTS,
        ),
        (
            LOUNGE
            | {"cadence": "one_time", "cycles": [{"start": "2026-06-01", "end": "2026-01-01"}]},
            ri.BAD_FIT,
        ),
        (LOUNGE | {"account_key": "harbor-checking"}, ri.NOT_A_CARD),
        (LOUNGE | {"account_key": "no-such-card"}, ri.NOT_A_CARD),
        ({k: v for k, v in LOUNGE.items() if k != "cadence"}, ri.BAD_FIT),
        ({k: v for k, v in LOUNGE.items() if k != "amount"}, ri.BAD_FIT),
        (LOUNGE | {"kind": "free_night"}, ri.BAD_FIT),
        (
            {"name": "Free night", "kind": "free_night", "account_key": "summit-card"}
            | {"program_key": "no-such-program"},
            ri.UNKNOWN_PROGRAM,
        ),
        (
            {"name": "Cap", "kind": "earning_cap", "account_key": "cobalt-card"}
            | {"cap": "100.00", "earn_before": "2x", "earn_after": "1x", "category_keys": ["x"]},
            UNKNOWN_CATEGORY,
        ),
        (LOUNGE | {"key": "app-mine"}, None),
        (LOUNGE | {"name": "   "}, None),
        (LOUNGE | {"name": "\u200b"}, None),  # nothing that shows
        (LOUNGE | {"name": "\u2060 \ufeff"}, None),
        (LOUNGE | {"amount": "25"}, None),
        (LOUNGE | {"kind": "bonus"}, None),
    ],
)
def test_add_rules(samples: Settings, admin: TestClient, body: dict, detail) -> None:
    r = add(admin, **body)
    assert r.status_code == 422, r.text
    if detail is not None:
        assert r.json()["detail"] == detail
    assert counts(samples)["reward_benefit_edits"] == 0 and events(samples) == []


def test_other_kinds_can_be_added(samples: Settings, admin: TestClient) -> None:
    r = add(
        admin,
        name="Bonus night",
        kind="free_night",
        account_key="summit-card",
        program_key="lantern-hotels",
        ends_on="2027-06-30",
    )
    assert r.status_code == 201, r.text
    assert (r.json()["program_name"], r.json()["group"]) == ("Lantern Hotels", "open")
    r = add(
        admin,
        name="Grocery bonus",
        kind="earning_cap",
        account_key="cobalt-card",
        cap="1500.00",
        earn_before="3x",
        earn_after="1x",
        category_keys=["groceries"],
    )
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "earning_cap"  # writes give back any kind
    body = page(admin)
    assert {c["key"]: c["origin"] for c in body["caps"]} == {
        "fuel-bonus": "chat",
        "app-grocery-bonus": "app",
    }
    assert [p["origin"] for p in body["perks"]] == ["chat"]


def test_a_name_must_show(samples: Settings, admin: TestClient) -> None:
    for name in ("\u200b", "\u2060\ufeff", " \u200c "):
        r = admin.post("/api/rewards/programs", json={"name": name, "kind": "points"}, headers=H)
        assert r.status_code == 422, r.text
        assert add(admin, **LOUNGE | {"name": name}).status_code == 422
    # a name with a format character in it still shows, so it is fine
    assert add(admin, **LOUNGE | {"name": "Lounge\u200b credit"}).status_code == 201
    assert counts(samples)["reward_app_programs"] == 0


def test_a_dollar_amount_in_the_name_still_makes_a_valid_key(
    samples: Settings, admin: TestClient
) -> None:
    r = add(admin, **LOUNGE | {"name": "$300 travel credit"})
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "app-travel-credit"
    long = "Premium airport lounge and travel partner benefit credit"
    keys = [add(admin, **LOUNGE | {"name": long}).json()["key"] for _ in range(3)]
    assert len(set(keys)) == 3
    for key in keys:
        assert len(key) <= KEY_MAX and re.fullmatch(KEY_PATTERN, key), key
    assert keys[1].endswith("-2") and keys[2].endswith("-3")


# --------------------------------------------------------------------------
# Rules 8 and 9: the chat drops a key or changes its kind
# --------------------------------------------------------------------------


def test_a_dropped_key_is_listed_and_never_shown(samples: Settings, admin: TestClient) -> None:
    dining = next(b for b in chat_rewards(samples)["benefits"] if b["key"] == "dining-credit")
    edit(admin, "dining-credit", note="Mine")
    add_inputs(samples, "dining-credit")
    send(samples, dropped("dining-credit"))
    body = page(admin)
    assert ben(body, "dining-credit") is None and body["hidden"] == []
    assert body["orphans"] == [
        {
            "kind": "benefit",
            "key": "dining-credit",
            "holds": ["edits", "terms", "choices", "marks"],
            "reason": "dropped",
            "unit": None,
        }
    ]
    assert "dining-credit" not in fee_credits(body, "summit-card")
    r = patch(admin, "dining-credit", set={"note": "Again"})
    assert r.status_code == 404 and r.json()["detail"] == ri.NO_BENEFIT
    r = admin.delete("/api/rewards/benefits/dining-credit/edits", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ri.NOT_SHOWN
    # the chat sends it again: the edit is back
    send(samples, lambda bs: [*bs, dining])
    body = page(admin)
    assert ben(body, "dining-credit")["note"] == "Mine" and body["orphans"] == []


def test_delete_edits_on_a_dropped_key(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", note="Mine")
    add_inputs(samples, "dining-credit")
    send(samples, dropped("dining-credit"))
    r = admin.delete("/api/rewards/benefits/dining-credit", headers=H)
    assert r.status_code == 204
    assert held(samples, "dining-credit") == dict.fromkeys(REWARD_TABLES[2:], 0)
    assert page(admin)["orphans"] == []
    assert details(samples)[-1] == {
        "action": "delete_orphan",
        "edits": 1,
        "terms": 1,
        "choices": 1,
        "marks": 1,
    }
    r = admin.delete("/api/rewards/benefits/dining-credit", headers=H)
    assert r.status_code == 404


def test_a_hidden_only_orphan_stays_quiet(samples: Settings, admin: TestClient) -> None:
    assert patch(admin, "travel-credit", hidden=True).status_code == 200
    send(samples, dropped("travel-credit"))
    body = page(admin)
    assert body["orphans"] == [] and body["hidden"] == [] and body["notices"] == []
    # with a match word it is listed
    with write_connection(samples.db_path) as w:
        w.execute(
            """INSERT INTO reward_match_terms (benefit_key, term, updated_by, updated_at)
               VALUES ('travel-credit', 'TRAVEL CREDIT', NULL, ?)""",
            (NOW,),
        )
    assert page(admin)["orphans"] == [
        {
            "kind": "benefit",
            "key": "travel-credit",
            "holds": ["terms"],
            "reason": "dropped",
            "unit": None,
        }
    ]


def test_a_kind_change_skips_an_edit_that_no_longer_fits(
    samples: Settings, admin: TestClient
) -> None:
    edit(admin, "dining-credit", amount="12.00", note="Mine")
    edit(admin, "travel-credit", note="Still fits")
    as_other = {"kind": "other", "cadence": None, "amount": None, "cycles": None}
    send(samples, lambda bs: changed("travel-credit", **as_other)(bs))
    send(samples, changed("dining-credit", **as_other))
    body = page(admin)
    dining = ben(body, "dining-credit")
    assert (dining["kind"], dining["note"], dining["amount_cents"]) == ("other", None, None)
    assert dining["edited_fields"] == []
    assert dining["notices"] == [{"kind": "benefit_no_fit", "fields": ["amount", "note"]}]
    assert [n["text"] for n in body["notices"]] == ["Your edit no longer fits this benefit."]
    # an edit that still fits the new kind stays
    travel = ben(body, "travel-credit")
    assert (travel["note"], travel["notices"]) == ("Still fits", [])
    # Delete edit
    r = admin.delete("/api/rewards/benefits/dining-credit/edits", headers=H)
    assert r.status_code == 200 and r.json()["notices"] == []
    assert edit_row(samples, "dining-credit") is None and page(admin)["notices"] == []


def test_an_edit_that_no_longer_fits_can_still_be_hidden(
    samples: Settings, admin: TestClient
) -> None:
    edit(admin, "dining-credit", amount="12.00", note="Mine")
    as_other = {"kind": "other", "cadence": None, "amount": None, "cycles": None}
    send(samples, changed("dining-credit", **as_other))
    r = patch(admin, "dining-credit", hidden=True)
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] is True and r.json()["note"] is None  # the chat's item
    assert [h["key"] for h in page(admin)["hidden"]] == ["dining-credit"]
    row = edit_row(samples, "dining-credit")
    assert (row["hidden"], row["fields"]) == (1, {"amount": "12.00", "note": "Mine"})
    assert patch(admin, "dining-credit", hidden=False).status_code == 200
    # a field change says what to do first
    for body in (
        {"set": {"note": "Lounge only"}},
        {"set": {"ends_on": "2026-10-20"}},
        {"amount_from": "2026-10-01"},
    ):
        r = patch(admin, "dining-credit", **body)
        assert r.status_code == 422 and r.json()["detail"] == ri.NO_FIT_FIRST, body
    assert edit_row(samples, "dining-credit")["fields"] == {"amount": "12.00", "note": "Mine"}
    # a reset that makes it fit again is fine
    r = patch(admin, "dining-credit", reset=["amount"])
    assert r.status_code == 200, r.text
    assert (r.json()["note"], r.json()["notices"]) == ("Mine", [])


def test_one_stale_edit_never_hides_the_rest(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", note="Mine")
    # (a) the chat renames the program an app free night and a program edit name
    night = add(
        admin,
        name="Spring night",
        kind="free_night",
        account_key="summit-card",
        program_key="lantern-hotels",
    ).json()["key"]
    edit(admin, "anniversary-night", program_key="summit-points", note="Book early")
    edit(admin, "hotel-status", note="Ask at the desk")

    def renamed(data: dict) -> dict:
        text = json.dumps(data).replace("lantern-hotels", "lantern-rr")
        return json.loads(text.replace("summit-points", "summit-pts"))

    send_all(samples, renamed)
    body = page(admin)
    assert ben(body, "dining-credit")["note"] == "Mine"
    assert ben(body, night) is None
    night_out = ben(body, "anniversary-night")  # the chat's item, the edit skipped
    assert (night_out["note"], night_out["program_name"]) == (None, "Lantern Hotels")
    perk = next(p for p in body["perks"] if p["key"] == "hotel-status")
    assert perk["note"] == "Ask at the desk"  # an edit that still fits stays
    assert [(n["kind"], n["key"], n["fields"]) for n in body["notices"]] == [
        ("benefit_no_fit", "anniversary-night", ["note", "program_key"]),
        ("benefit_app_no_fit", night, []),
    ]
    # (b) the chat drops reported_spent and as_of under a reported_spent edit
    edit(admin, "fuel-bonus", reported_spent="120.00")
    send(samples, changed("fuel-bonus", reported_spent=None, as_of=None))
    body = page(admin)
    cap = next(c for c in body["caps"] if c["key"] == "fuel-bonus")
    assert (cap["reported_spent_cents"], cap["reported_as_of"]) == (None, None)
    assert ("benefit_no_fit", "fuel-bonus", ["reported_spent"]) in [
        (n["kind"], n["key"], n["fields"]) for n in body["notices"]
    ]
    assert ben(body, "dining-credit")["note"] == "Mine"
    # it fits again once the date is there too
    r = patch(admin, "fuel-bonus", set={"as_of": "2026-09-30"})
    assert r.status_code == 200, r.text
    cap = next(c for c in page(admin)["caps"] if c["key"] == "fuel-bonus")
    assert (cap["reported_spent_cents"], cap["reported_as_of"]) == (12_000, "2026-09-30")
    # the app free night goes with Delete
    assert admin.delete(f"/api/rewards/benefits/{night}", headers=H).status_code == 204
    assert [(n["kind"], n["key"]) for n in page(admin)["notices"]] == [
        ("benefit_no_fit", "anniversary-night"),
        ("benefit_chat_changed", "fuel-bonus"),  # the chat dropped reported_spent since
    ]


def test_move_to_carries_everything(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", note="Mine")
    add_inputs(samples, "dining-credit")

    def renamed(bs: list[dict]) -> list[dict]:
        dining = next(b for b in bs if b["key"] == "dining-credit")
        return [*dropped("dining-credit")(bs), dining | {"key": "dining-credit-new"}]

    send(samples, renamed)
    assert [o["key"] for o in page(admin)["orphans"]] == ["dining-credit"]
    r = move(admin, "dining-credit", "dining-credit-new")
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["key"], out["note"], out["edited_fields"], out["notices"]) == (
        "dining-credit-new",
        "Mine",
        ["note"],
        [],
    )
    assert held(samples, "dining-credit") == dict.fromkeys(REWARD_TABLES[2:], 0)
    assert held(samples, "dining-credit-new") == dict.fromkeys(REWARD_TABLES[2:], 1)
    body = page(admin)
    assert body["orphans"] == [] and ben(body, "dining-credit-new")["terms_count"] == 1
    assert details(samples)[-1] == {
        "action": "move",
        "benefit_deleted": False,
        "edits": 1,
        "terms": 1,
        "choices": 1,
        "marks": 1,
    }


@pytest.mark.parametrize(
    ("setup", "to_key", "status", "detail"),
    [
        ("edit", "dining-credit-new", 409, ri.EDIT_CLASH),
        ("term", "dining-credit-new", 409, ri.TERM_CLASH),
        ("mark", "dining-credit-new", 409, ri.MARK_CLASH),
        (None, "travel-credit", 200, None),
        (None, "streaming-credit", 422, ri.OTHER_BENEFIT),
        (None, "anniversary-night", 422, ri.OTHER_BENEFIT),
        (None, "no-such-benefit", 404, ri.NO_BENEFIT),
        (None, "dining-credit", 422, ri.SAME_BENEFIT),
    ],
)
def test_move_rules(
    samples: Settings, admin: TestClient, setup: str | None, to_key: str, status: int, detail
) -> None:
    edit(admin, "dining-credit", note="Mine")
    add_inputs(samples, "dining-credit")

    def renamed(bs: list[dict]) -> list[dict]:
        dining = next(b for b in bs if b["key"] == "dining-credit")
        return [*dropped("dining-credit")(bs), dining | {"key": "dining-credit-new"}]

    send(samples, renamed)
    if setup == "edit":
        edit(admin, "dining-credit-new", how_to_use="Order ahead")
    elif setup == "term":
        add_inputs(samples, "dining-credit-new", term="dining credit", offset=1)
    elif setup == "mark":
        add_inputs(samples, "dining-credit-new", term="DINING", offset=1)
    before = {k: held(samples, k) for k in ("dining-credit", "dining-credit-new", "travel-credit")}
    r = move(admin, "dining-credit", to_key)
    assert r.status_code == status, r.text
    if detail is not None:
        assert r.json()["detail"] == detail
        assert {k: held(samples, k) for k in before} == before  # nothing moved or dropped


def test_only_orphans_and_app_benefits_move(samples: Settings, admin: TestClient) -> None:
    r = move(admin, "streaming-credit", "dining-credit")
    assert r.status_code == 422 and r.json()["detail"] == ri.CHAT_BENEFIT
    r = move(admin, "app-nope", "dining-credit")
    assert r.status_code == 404
    # a key no run ever sent cannot say its card and kind
    add_inputs(samples, "ghost-credit")
    r = move(admin, "ghost-credit", "dining-credit")
    assert r.status_code == 422 and r.json()["detail"] == ri.UNKNOWN_BENEFIT
    assert held(samples, "ghost-credit")["reward_match_terms"] == 1


def test_a_move_must_fit_the_benefit_it_goes_to(samples: Settings, admin: TestClient) -> None:
    edit(admin, "dining-credit", note="Mine")
    edit(admin, "fuel-bonus", reported_spent="999.00")

    def renamed(bs: list[dict]) -> list[dict]:
        fuel = next(b for b in bs if b["key"] == "fuel-bonus")
        new = {k: v for k, v in fuel.items() if k not in ("reported_spent", "as_of")}
        return [*dropped("fuel-bonus")(bs), new | {"key": "fuel-bonus-new"}]

    send(samples, renamed)
    before = page(admin)
    assert [o["key"] for o in before["orphans"]] == ["fuel-bonus"]
    r = move(admin, "fuel-bonus", "fuel-bonus-new")
    assert r.status_code == 422 and r.json()["detail"] == ri.MOVE_NO_FIT
    assert edit_row(samples, "fuel-bonus") is not None
    assert edit_row(samples, "fuel-bonus-new") is None
    assert page(admin) == before and before["notices"] == []


# --------------------------------------------------------------------------
# Program references: an app program a benefit names
# --------------------------------------------------------------------------


def test_a_program_a_benefit_names_moves_with_it(samples: Settings, admin: TestClient) -> None:
    r = admin.post(
        "/api/rewards/programs", json={"name": "Lantern Hotels", "kind": "points"}, headers=H
    )
    assert r.status_code == 201, r.text
    program = r.json()["key"]
    assert program == "app-lantern-hotels"
    night = add(
        admin,
        name="Spring night",
        kind="free_night",
        account_key="summit-card",
        program_key=program,
    ).json()["key"]
    edit(admin, "anniversary-night", program_key=program)
    edit(admin, "dining-credit", note="Mine")
    # Delete is refused while a benefit names it
    before = counts(samples)
    r = admin.delete(f"/api/rewards/programs/{program}", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ri.PROGRAM_IN_USE
    assert counts(samples) == before
    # "Use the chat's" on the duplicate moves the benefits to the chat's program
    r = admin.post(
        f"/api/rewards/programs/{program}/move", json={"to_key": "lantern-hotels"}, headers=H
    )
    assert r.status_code == 200, r.text
    body = page(admin)
    assert body["notices"] == []
    assert ben(body, night)["program_name"] == "Lantern Hotels"
    assert edit_row(samples, night)["fields"]["program_key"] == "lantern-hotels"
    assert edit_row(samples, "anniversary-night") is None  # the chat's own program
    assert ben(body, "dining-credit")["note"] == "Mine"
    assert json.loads(events(samples)[-1]["detail_json"]) == {
        "action": "move",
        "balances": 0,
        "program_deleted": True,
        "benefits": 2,
    }
    # with nothing naming it, an app program deletes as before
    r = admin.post(
        "/api/rewards/programs", json={"name": "Lantern Air", "kind": "miles"}, headers=H
    )
    assert admin.delete(f"/api/rewards/programs/{r.json()['key']}", headers=H).status_code == 204


# --------------------------------------------------------------------------
# Rules 10 and 11: app keys from the chat, and duplicates
# --------------------------------------------------------------------------


def test_the_chat_sends_an_app_key(samples: Settings, admin: TestClient) -> None:
    key = add(admin, **LOUNGE).json()["key"]
    add_inputs(samples, key)
    sent = {
        "key": key,
        "account_key": "summit-card",
        "name": "Lounge pass",
        "kind": "other",
    }
    send(samples, lambda bs: [*bs, sent])
    body = page(admin)
    b = ben(body, key)
    assert (b["name"], b["origin"], b["kind"]) == ("Lounge credit", "app", "credit")
    assert b["notices"] == [{"kind": "benefit_app_key", "fields": []}]
    assert body["notices"] == [
        {
            "kind": "benefit_app_key",
            "text": "The chat now sends this benefit too. Yours is shown.",
            "key": key,
            "other_key": None,
            "fields": [],
        }
    ]
    # Use the chat's: the app row goes, words, choices and marks carry over by key
    assert admin.delete(f"/api/rewards/benefits/{key}", headers=H).status_code == 204
    body = page(admin)
    b = ben(body, key)
    assert (b["name"], b["origin"], b["terms_count"]) == ("Lounge pass", "chat", 1)
    assert body["notices"] == []
    assert held(samples, key) == {
        "reward_benefit_edits": 0,
        "reward_match_terms": 1,
        "reward_credit_decisions": 1,
        "reward_cycle_marks": 1,
    }
    assert details(samples)[-1] == {"action": "delete", "terms": 0, "choices": 0, "marks": 0}


def test_a_taken_over_chat_key_is_no_duplicate(samples: Settings, admin: TestClient) -> None:
    key = add(admin, **LOUNGE).json()["key"]
    sent = {"key": key, "account_key": "summit-card", "name": "Lounge pass", "kind": "other"}
    send(samples, lambda bs: [*bs, sent])
    # a second app item like the first: the chat's key the app took over is not compared
    second = add(admin, **LOUNGE).json()["key"]
    assert second == "app-lounge-credit-2"
    assert [n["kind"] for n in page(admin)["notices"]] == ["benefit_app_key"]
    r = keep_both(admin, second, key)
    assert r.status_code == 422 and r.json()["detail"] == ri.NOT_ALIKE


def test_match_off_is_kept_for_credits(samples: Settings, admin: TestClient) -> None:
    r = patch(admin, "streaming-credit", match_off=True)
    assert r.status_code == 200 and r.json()["match_off"] is True
    row = edit_row(samples, "streaming-credit")
    assert (row["match_off"], row["fields"], row["hidden"]) == (1, {}, 0)
    assert ben(page(admin), "streaming-credit")["match_off"] is True
    r = patch(admin, "streaming-credit", match_off=False)
    assert r.json()["match_off"] is False and edit_row(samples, "streaming-credit") is None


def test_delete_takes_an_app_benefits_inputs(samples: Settings, admin: TestClient) -> None:
    key = add(admin, **LOUNGE).json()["key"]
    add_inputs(samples, key)
    assert admin.delete(f"/api/rewards/benefits/{key}", headers=H).status_code == 204
    assert held(samples, key) == dict.fromkeys(REWARD_TABLES[2:], 0)
    assert ben(page(admin), key) is None
    assert details(samples)[-1] == {"action": "delete", "terms": 1, "choices": 1, "marks": 1}
    r = admin.delete("/api/rewards/benefits/dining-credit", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ri.CHAT_DELETE
    assert admin.delete("/api/rewards/benefits/app-nope", headers=H).status_code == 404


def test_a_duplicate_gives_a_notice(samples: Settings, admin: TestClient) -> None:
    same_name = {"name": "dining CREDIT", "cadence": "monthly", "amount": "12.00"}
    key = add(admin, **LOUNGE | same_name).json()["key"]
    assert key == "app-dining-credit"
    notice = {
        "kind": "benefit_duplicate",
        "text": "The chat now sends a benefit like this one. Fees may count it twice.",
        "key": key,
        "other_key": "dining-credit",
        "fields": [],
    }
    body = page(admin)
    assert body["notices"] == [notice]
    assert ben(body, key)["notices"] == [{"kind": "benefit_duplicate", "fields": []}]
    assert ben(body, "dining-credit")["notices"] == []
    # the same cadence and amount is alike too, on the same card and kind only
    food = add(admin, **LOUNGE | {"name": "Food credit", "cadence": "monthly", "amount": "10.00"})
    assert [n["other_key"] for n in page(admin)["notices"] if n["key"] == food.json()["key"]] == [
        "dining-credit"
    ]
    other_card = LOUNGE | {"name": "Dining credit", "account_key": "orchard-card"}
    assert add(admin, **other_card).status_code == 201
    other_kind = {"name": "Dining credit", "kind": "other", "account_key": "summit-card"}
    assert add(admin, **other_kind).status_code == 201
    assert {n["key"] for n in page(admin)["notices"]} == {key, food.json()["key"]}
    # Keep both
    r = keep_both(admin, key, "travel-credit")
    assert r.status_code == 422 and r.json()["detail"] == ri.NOT_ALIKE
    r = keep_both(admin, "dining-credit", key)
    assert r.status_code == 422 and r.json()["detail"] == ri.APP_EDIT
    r = keep_both(admin, key, "dining-credit")
    assert r.status_code == 200 and r.json()["notices"] == []
    assert edit_row(samples, key)["dup_dismissed_key"] == "dining-credit"
    # Use the chat's: words, choices and marks go to the chat's item, the app row goes
    add_inputs(samples, food.json()["key"])
    r = move(admin, food.json()["key"], "dining-credit")
    assert r.status_code == 200 and r.json()["key"] == "dining-credit"
    assert r.json()["terms_count"] == 1
    assert edit_row(samples, food.json()["key"]) is None
    assert page(admin)["notices"] == []
    assert details(samples)[-1] == {
        "action": "move",
        "benefit_deleted": True,
        "edits": 0,
        "terms": 1,
        "choices": 1,
        "marks": 1,
    }


def test_a_hidden_benefit_is_no_duplicate(samples: Settings, admin: TestClient) -> None:
    key = add(admin, **LOUNGE | {"name": "Dining credit", "cadence": "monthly"}).json()["key"]
    assert [n["key"] for n in page(admin)["notices"]] == [key]
    assert patch(admin, "dining-credit", hidden=True).status_code == 200
    assert page(admin)["notices"] == []
    assert patch(admin, "dining-credit", hidden=False).status_code == 200
    assert patch(admin, key, hidden=True).status_code == 200
    assert page(admin)["notices"] == []


# --------------------------------------------------------------------------
# A merged item that breaks the model: the whole overlay falls back
# --------------------------------------------------------------------------


def test_a_broken_app_row_is_left_out_alone(samples: Settings, admin: TestClient) -> None:
    before = page(admin)
    edit(admin, "dining-credit", note="Mine")
    body = {"key": "app-broken", "account_key": "summit-card", "name": "Broken", "kind": "credit"}
    with write_connection(samples.db_path) as w:
        w.execute(
            """INSERT INTO reward_benefit_edits (benefit_key, origin, account_key, fields_json,
                   hidden, updated_at)
               VALUES ('app-broken', 'app', 'summit-card', ?, 1, ?)""",
            (json.dumps(body), NOW),  # a credit with no cadence or amount, hidden too
        )
    add_inputs(samples, "app-broken")
    out = page(admin)
    assert out["notices"] == [
        {
            "kind": "benefit_app_no_fit",
            "text": ov.APP_NO_FIT_TEXT,
            "key": "app-broken",
            "other_key": None,
            "fields": [],
        }
    ]
    assert ben(out, "dining-credit")["note"] == "Mine"  # the rest still shows
    assert ben(out, "app-broken") is None and out["hidden"] == [] and out["orphans"] == []
    assert out["fees"] == before["fees"]
    # it cannot be changed or moved to, only deleted
    r = patch(admin, "app-broken", hidden=False)
    assert r.status_code == 422 and r.json()["detail"] == ri.BAD_FIT
    assert admin.delete("/api/rewards/benefits/app-broken", headers=H).status_code == 204
    assert held(samples, "app-broken") == dict.fromkeys(REWARD_TABLES[2:], 0)
    assert page(admin)["notices"] == []


# --------------------------------------------------------------------------
# Before the chat's first 1.3 run, and who can write
# --------------------------------------------------------------------------

WRITES = [
    ("POST", "/api/rewards/benefits", LOUNGE),
    ("PATCH", "/api/rewards/benefits/dining-credit", {"set": {"note": "A"}}),
    ("PATCH", "/api/rewards/benefits/dining-credit", {}),
    ("POST", "/api/rewards/benefits/dining-credit/resolve", {"field": "note", "choice": "mine"}),
    ("DELETE", "/api/rewards/benefits/dining-credit/edits", None),
    ("DELETE", "/api/rewards/benefits/app-lounge-credit", None),
    ("POST", "/api/rewards/benefits/dining-credit/move", {"to_key": "travel-credit"}),
    ("POST", "/api/rewards/benefits/app-lounge-credit/keep-both", {"other_key": "dining-credit"}),
]


def test_every_benefit_write_is_409_before_the_first_13_run(
    settings: Settings, admin: TestClient
) -> None:
    for method, path, body in WRITES:
        r = admin.request(method, path, json=body, headers=H)
        assert r.status_code == 409, (method, path, r.text)
        assert r.json()["detail"] == "Rewards start with the chat's first export."
    assert counts(settings) == dict.fromkeys(REWARD_TABLES, 0)
    assert events(settings) == []
    assert page(admin)["hidden"] == []


def test_a_member_cannot_change_benefits(samples: Settings, member: TestClient) -> None:
    for method, path, body in WRITES:
        r = member.request(method, path, json=body, headers=H)
        assert r.status_code == 403, (method, path)
        assert r.json()["detail"] == "Only an admin can do this."
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0)
    assert events(samples) == []
    assert page(member)["benefits"]  # reading stays open


def test_every_rewards_route_needs_a_session(prod_app, prod: TestClient) -> None:
    routes = {
        (path, m)
        for path, methods, _ in walk_routes(prod_app)
        if path.startswith("/api/rewards")
        for m in methods - {"HEAD", "OPTIONS"}
    }
    assert routes == {
        ("/api/rewards", "GET"),
        ("/api/rewards/balances", "POST"),
        ("/api/rewards/balances/{balance_id}", "DELETE"),
        ("/api/rewards/programs/{key}/balances", "GET"),
        ("/api/rewards/programs", "POST"),
        ("/api/rewards/programs/{key}", "PATCH"),
        ("/api/rewards/programs/{key}", "DELETE"),
        ("/api/rewards/programs/{key}/move", "POST"),
        ("/api/rewards/programs/{key}/keep-both", "POST"),
        ("/api/rewards/benefits", "POST"),
        ("/api/rewards/benefits/{key}", "PATCH"),
        ("/api/rewards/benefits/{key}", "DELETE"),
        ("/api/rewards/benefits/{key}/resolve", "POST"),
        ("/api/rewards/benefits/{key}/edits", "DELETE"),
        ("/api/rewards/benefits/{key}/move", "POST"),
        ("/api/rewards/benefits/{key}/keep-both", "POST"),
        # chunk D2 (test_rewards_choices)
        ("/api/rewards/review", "GET"),
        ("/api/rewards/benefits/{key}/terms", "GET"),
        ("/api/rewards/benefits/{key}/terms", "PUT"),
        ("/api/rewards/terms/preview", "POST"),
        ("/api/rewards/benefits/{key}/cycles/{start}/mark", "PUT"),
        ("/api/rewards/benefits/{key}/cycles/{start}/mark", "DELETE"),
        ("/api/rewards/credits/{txn_key}", "PUT"),
        ("/api/rewards/credits/{txn_key}", "DELETE"),
        # chunk E (test_rewards_files)
        ("/api/rewards/benefit-files/preview", "POST"),
        ("/api/rewards/benefit-files/apply", "POST"),
        ("/api/rewards/benefit-files/template", "GET"),
    }
    for path, method in routes:
        url = path.replace("{key}", "dining-credit").replace("{balance_id}", "1")
        url = url.replace("{start}", "2026-10-01").replace("{txn_key}", "a" * 64)
        r = prod.request(method, url, headers=H)
        assert r.status_code == 401, (method, path)


# --------------------------------------------------------------------------
# Safety: typed text, events, transactions and logs
# --------------------------------------------------------------------------


def _flagged() -> list[str]:
    """The injected values the rewards text scan refuses, with the private lists."""
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    return [v for v in INJECTED_VALUES if scan_text(v, private, "rewards")]


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
        key = add(c, **LOUNGE).json()["key"]
        for value in flagged:
            bodies = [
                LOUNGE | {"name": f"Lounge {value}"},
                LOUNGE | {"note": f"Ask {value}"},
                LOUNGE | {"how_to_use": value},
                {"name": "Cap", "kind": "earning_cap", "account_key": "cobalt-card"}
                | {"cap": "100.00", "earn_before": value, "earn_after": "1x"}
                | {"category_keys": ["fuel"]},
            ]
            for body in bodies:
                r = add(c, **body)
                assert r.status_code == 422, (value, r.text)
                assert value not in r.text and value.replace(" ", "") not in r.text
                assert "Remove that part" in r.json()["detail"]
            sets = [
                (key, {"name": f"Lounge {value}"}),
                ("dining-credit", {"note": value}),
                ("dining-credit", {"how_to_use": f"Ask {value}"}),
                ("fuel-bonus", {"earn_after": value}),
                ("hotel-status", {"status_tier": value}),
            ]
            for target, fields in sets:
                r = patch(c, target, set=fields)
                assert r.status_code == 422, (value, target, r.text)
                assert value not in r.text and value.replace(" ", "") not in r.text
        # key-like values give 404 or 422 and are never echoed
        for value in ("check-car-123", "mystery-account"):
            for r in (
                move(c, key, value),
                keep_both(c, key, value),
                patch(c, "anniversary-night", set={"program_key": value}),
                add(c, **LOUNGE | {"account_key": value}),
            ):
                assert r.status_code in (404, 422) and value not in r.text
    with read_connection(settings.db_path) as conn:
        stored = _text_columns(conn)
        dump = "\n".join(conn.iterdump())
    for value in [*flagged, "check-car-123", "mystery-account"]:
        assert value not in stored and value not in dump, value
    assert counts(settings)["reward_benefit_edits"] == 1


def test_a_benefit_key_made_from_a_name_is_scanned_too(
    use_private_rules: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = use_private_rules
    add_users(settings)
    import_samples(settings)
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    names = {"Exámple Private Service": "example-private-service"}
    for name, slug in names.items():
        assert scan_text(name, private, "rewards") == [] and ri.slug(name) == slug
    app = app_for(monkeypatch, dev=False, BUDGET_PRIVATE_RULES=str(settings.private_rules))
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        for name, slug in names.items():
            r = add(c, **LOUNGE | {"name": name})
            assert r.status_code == 422 and r.json()["detail"].startswith("The name looks like")
            assert name not in r.text and slug not in r.text
    assert counts(settings) == dict.fromkeys(REWARD_TABLES, 0)
    assert events(settings) == []


EVENT_FIELDS = {
    "action",
    "fields",
    "reset",
    "hidden",
    "match_off",
    "edits",
    "terms",
    "choices",
    "marks",
    "benefit_deleted",
}
ACTIONS = {
    "add",
    "change",
    "keep_mine",
    "use_chat",
    "reset",
    "delete",
    "delete_orphan",
    "move",
    "keep_both",
}
FIELD_NAMES = set(RewardBenefit.model_fields) | {"amount_from"}


def test_benefit_events_hold_no_values(samples: Settings, admin: TestClient) -> None:
    key = add(
        admin, **LOUNGE | {"name": "Dining credit", "cadence": "monthly"}, note="Riley's"
    ).json()["key"]
    assert patch(admin, key, set={"amount": "31.37"}, amount_from="2026-08-01").status_code == 200
    assert keep_both(admin, key, "dining-credit").status_code == 200
    edit(admin, "dining-credit", note="Pool with Riley", amount="12.34")
    send(samples, changed("dining-credit", note="Chat note"))
    assert resolve(admin, "dining-credit", "note", "mine").status_code == 200
    assert resolve(admin, "dining-credit", "amount", "chat").status_code == 200
    assert patch(admin, "dining-credit", hidden=True, match_off=True).status_code == 200
    assert patch(admin, "dining-credit", reset=["note"], hidden=False).status_code == 200
    edit(admin, "dining-credit", note="Again")
    assert admin.delete("/api/rewards/benefits/dining-credit/edits", headers=H).status_code == 200
    assert move(admin, key, "dining-credit").status_code == 200
    rows = events(samples)
    assert [(r["entity"], r["entity_key"]) for r in rows] == [
        ("benefit", key),
        ("benefit", key),
        ("benefit", key),
        ("benefit", "dining-credit"),
        ("benefit", "dining-credit"),
        ("benefit", "dining-credit"),
        ("benefit", "dining-credit"),
        ("benefit", "dining-credit"),
        ("benefit", "dining-credit"),
        ("benefit", "dining-credit"),
        ("benefit", key),
    ]
    assert [json.loads(r["detail_json"])["action"] for r in rows] == [
        "add",
        "change",
        "keep_both",
        "change",
        "keep_mine",
        "use_chat",
        "change",
        "change",
        "change",
        "reset",
        "move",
    ]
    for r in rows:
        detail = json.loads(r["detail_json"])
        assert set(detail) <= EVENT_FIELDS and detail["action"] in ACTIONS
        assert set(detail.get("fields", [])) <= FIELD_NAMES
        assert set(detail.get("reset", [])) <= FIELD_NAMES
        for v in detail.values():
            assert isinstance(v, bool | int | list) or v in ACTIONS
    text = "\n".join(r["detail_json"] for r in rows)
    for value in ("31.37", "12.34", "Riley", "Chat note", "Again", "2026-08-01", "Dining credit"):
        assert value not in text


def test_benefit_writes_outside_a_write_transaction_raise(samples: Settings) -> None:
    kw = {"today": TODAY, "user_id": None, "now": NOW}
    calls = [
        lambda c: ri.add_benefit(c, LOUNGE, private=None, **kw),
        lambda c: ri.update_benefit(c, "dining-credit", changes={"note": "A"}, private=None, **kw),
        lambda c: ri.resolve_benefit(c, "dining-credit", "note", "mine", **kw),
        lambda c: ri.reset_benefit(c, "dining-credit", **kw),
        lambda c: ri.delete_benefit(c, "app-x", **kw),
        lambda c: ri.move_benefit(c, "app-x", "dining-credit", **kw),
        lambda c: ri.keep_both_benefits(c, "app-x", "dining-credit", **kw),
    ]
    conn = connect(samples.db_path)
    try:
        for call in calls:
            assert not conn.in_transaction
            with pytest.raises(RuntimeError):
                call(conn)
        with write_transaction(conn):
            assert ri.add_benefit(conn, LOUNGE, private=None, **kw) == "app-lounge-credit"
    finally:
        conn.close()
    assert counts(samples)["reward_benefit_edits"] == 1


def test_logs_hold_no_values_on_the_benefit_routes(
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
        key = add(c, **LOUNGE | {"name": "Lantern lounge"}, note="Pool with Riley").json()["key"]
        patch(c, key, set={"amount": "31.37"}, amount_from="2026-08-01")
        patch(c, "dining-credit", set={"note": "Corner cafe"})
        patch(c, "dining-credit", set={"note": f"Ask {PHONE}"})  # 422
        patch(c, "dining-credit", set={"amount": "12"})  # 422
        resolve(c, "dining-credit", "note", "mine")
        patch(c, "dining-credit", hidden=True)
        c.delete("/api/rewards/benefits/dining-credit/edits", headers=H)
        keep_both(c, key, "check-car-123")
        move(c, key, "dining-credit")
        c.delete(f"/api/rewards/benefits/{key}", headers=H)
        c.get("/api/rewards")
    fmt = JsonFormatter()
    text = capsys.readouterr().out + "\n".join(fmt.format(r) for r in caplog.records)
    assert '"route": "/api/rewards/benefits/{key}"' in text
    assert '"route": "/api/rewards/benefits/{key}/resolve"' in text
    for value in (
        "31.37",
        "Lantern lounge",
        "Pool with Riley",
        "Corner cafe",
        PHONE,
        "check-car-123",
        key,
        "dining-credit",
    ):
        assert value not in text, value


# --------------------------------------------------------------------------
# Dates at the calendar's end never take the page down
# --------------------------------------------------------------------------


def test_a_cycle_past_2099_is_refused(samples: Settings, admin: TestClient) -> None:
    r = add(
        admin,
        **LOUNGE | {"name": "Welcome credit", "cadence": "one_time"},
        cycles=[{"start": "2026-06-01", "end": "9999-12-31"}],
    )
    assert r.status_code == 422 and "9999" not in r.text
    assert page(admin)["notices"] == []


def test_a_stored_cycle_at_the_calendars_end_still_shows_the_page(
    samples: Settings, admin: TestClient
) -> None:
    body = LOUNGE | {
        "key": "app-far-credit",
        "name": "Far credit",
        "cadence": "one_time",
        "cycles": [{"start": "2026-06-01", "end": "9999-12-31", "amount": "25.00"}],
    }
    with write_connection(samples.db_path) as w:
        w.execute(
            """INSERT INTO reward_benefit_edits (benefit_key, origin, account_key, fields_json,
                   updated_at)
               VALUES ('app-far-credit', 'app', 'summit-card', ?, ?)""",
            (json.dumps(body), NOW),  # written before the 2099 cap, or by hand
        )
    for url in ("/api/rewards", "/api/rewards/review", "/api/dashboard", "/api/checks"):
        assert admin.get(url).status_code == 200, url
    assert rm._plus("9999-12-31", 7) == "9999-12-31"
    assert rm._plus("0001-01-01", -7) == "0001-01-01"
