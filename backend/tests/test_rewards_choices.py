"""Rewards inputs, chunk D2 routes (docs/plans/rewards-inputs.md sections 4, 6
and 8): the review list, match words with suggested and seen words and the
preview, cycle marks and credit choices, the counts on the page and in Checks,
and the safety rules on them.

Fake samples only. Household today is 2026-10-20. Summit card: Dining credit
(monthly $10; DINING CREDIT +10.00 rows on the 12th, January to August), Yearly
travel credit (annual $100), Airline fee credit (semi-annual $50, the chat says
January's is used), one STATEMENT CREDIT +25.00 on 2026-08-18 and the TOWNE
OUTFITTERS RETURN. Orchard card: Wireless credit (monthly $5, WIRELESS CREDIT
on the 5th). Cobalt card: Streaming credit (quarterly $15). Summit's
statements run to 2026-09-10.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from api_helpers import ADMIN, ADMIN_PW, PROD_ORIGIN, PROD_URL, add_users, app_for, import_samples
from api_helpers import login as sign_in
from bad_cases import _add_cents
from fastapi.testclient import TestClient
from test_rewards_inputs_benefits import LOUNGE, _flagged, _text_columns, send_all
from test_rewards_inputs_benefits import add as add_benefit
from test_rewards_inputs_benefits import patch as patch_benefit
from test_rewards_match import (
    _describe,
    add_row,
    insert,
    key_of,
    q,
    summit_correction,
)

from app import config
from app.ai.facts import signature
from app.config import Settings
from app.db.connect import connect, read_connection, write_connection, write_transaction
from app.importer.models import SHA256_PATTERN
from app.importer.runner import import_folder, reapply
from app.logging import JsonFormatter
from app.main import walk_routes
from app.services import rewards as rw
from app.services import rewards_inputs as ri
from app.services import rewards_match as rm
from app.services.balances import clear_cache

TODAY = date(2026, 10, 20)
NOW = "2026-10-20T12:00:00+00:00"
H = {"Origin": PROD_ORIGIN}
SUMMIT = "summit-card"
DINING = "dining-credit"
AIRLINE = "airline-fee-credit"
TRAVEL = "travel-credit"
WIRELESS = "wireless-credit"
NO_KEY = "f" * 64  # a txn_key no row has
REWARD_TABLES = (
    "reward_balances",
    "reward_app_programs",
    "reward_benefit_edits",
    "reward_match_terms",
    "reward_credit_decisions",
    "reward_cycle_marks",
)
ZERO = {
    "to_review": 0,
    "ambiguous": 0,
    "returns": 0,
    "trips": 0,
    "over": 0,
    "chat_says_used": 0,
    "needs_a_look": 0,
    "benefit_notes": 0,
    "program_notes": 0,
}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def put_terms(c: TestClient, key: str, terms: list[str]):
    return c.put(f"/api/rewards/benefits/{key}/terms", json={"terms": terms}, headers=H)


def words(c: TestClient, key: str, *terms: str) -> dict:
    r = put_terms(c, key, list(terms))
    assert r.status_code == 200, r.text
    return r.json()


def preview(c: TestClient, key: str, term: str):
    return c.post("/api/rewards/terms/preview", json={"benefit_key": key, "term": term}, headers=H)


def mark(c: TestClient, key: str, start: str, used: str, used_on: str | None = None):
    body = {"used": used} | ({"used_on": used_on} if used_on else {})
    return c.put(f"/api/rewards/benefits/{key}/cycles/{start}/mark", json=body, headers=H)


def clear_mark(c: TestClient, key: str, start: str):
    return c.delete(f"/api/rewards/benefits/{key}/cycles/{start}/mark", headers=H)


def choose(c: TestClient, txn_key: str, decision: str = "link", **body):
    return c.put(f"/api/rewards/credits/{txn_key}", json={"decision": decision, **body}, headers=H)


def unchoose(c: TestClient, txn_key: str):
    return c.delete(f"/api/rewards/credits/{txn_key}", headers=H)


def review(c: TestClient) -> dict:
    r = c.get("/api/rewards/review")
    assert r.status_code == 200
    return r.json()


def page(c: TestClient) -> dict:
    r = c.get("/api/rewards")
    assert r.status_code == 200
    return r.json()


def cycles(c: TestClient, key: str) -> dict[str, dict]:
    b = next(b for b in page(c)["benefits"] if b["key"] == key)
    return {x["start"]: x for x in [*b["past_cycles"], *([b["cycle"]] if b["cycle"] else [])]}


def listed(body: dict, group: str = "credits") -> dict[str, dict]:
    return {x["txn_key"]: x for x in body[group]}


def events(settings: Settings) -> list[sqlite3.Row]:
    return q(
        settings,
        "SELECT entity, entity_key, detail_json FROM events WHERE kind = 'rewards_edit' "
        "ORDER BY id",
    )


def counts(settings: Settings) -> dict[str, int]:
    return {t: q(settings, f"SELECT count(*) FROM {t}")[0][0] for t in REWARD_TABLES}


def choices(settings: Settings) -> list[str]:
    return [r[0] for r in q(settings, "SELECT txn_key FROM reward_credit_decisions")]


def lost(body: dict) -> list[str]:
    """The txn_keys of the choices that lost their row."""
    return [x["txn_key"] for x in body["needs_a_look"] if x["kind"] == "lost_choice"]


def statement_credit(settings: Settings) -> str:
    return key_of(settings, "STATEMENT CREDIT", "2026-08-18")


def towne(settings: Settings) -> str:
    return key_of(settings, "TOWNE OUTFITTERS RETURN", "2026-06-29")


# --------------------------------------------------------------------------
# Acceptance (plan 11.4) and the review list
# --------------------------------------------------------------------------


def test_summit_dining_reads_from_your_statements(samples: Settings, admin: TestClient) -> None:
    cards = ("summit-card", "cobalt-card", "orchard-card")

    def spend() -> list:
        with read_connection(samples.db_path) as conn:
            return [rw.card_spend(conn, card, 2026) for card in cards]

    before, before_page = spend(), page(admin)
    assert before_page["review_counts"]["to_review"] == 20  # every credit row waits at first

    terms = words(admin, DINING, "DINING CREDIT")
    assert terms["terms"] == ["DINING CREDIT"] and terms["seen"] is None  # saved, so not offered
    found = cycles(admin, DINING)
    for m in range(1, 9):
        c = found[f"2026-{m:02d}-01"]
        day = f"2026-{m:02d}-12"
        assert (c["source"], c["used_cents"], c["used_on"]) == ("rows", 1_000, day)
        assert c["chat_used_cents"] == 1_000  # the chat's $10 and a row's $10 show $10
        assert [r["date"] for r in c["rows"]] == [day]
    # STATEMENT CREDIT waits for review with a guess. TOWNE sits in the returns group.
    body = review(admin)
    statement = listed(body)[statement_credit(samples)]
    assert statement["guess"] == {
        "benefit_key": AIRLINE,
        "benefit_name": "Airline fee credit",
        "cycle_start": "2026-07-01",
    }
    assert (statement["note"], statement["options"]) == (None, [])
    assert list(listed(body, "returns")) == [towne(samples)]
    assert not any(x["description"] == "DINING CREDIT" for x in body["credits"])
    assert body["counts"] == ZERO | {"to_review": 12, "returns": 1}
    assert page(admin)["review_counts"] == body["counts"]
    assert admin.get("/api/checks").json()["rewards"]["counts"] == body["counts"]
    assert spend() == before  # card_spend totals do not change


def test_the_review_list_and_what_needs_a_look(samples: Settings, admin: TestClient) -> None:
    body = review(admin)
    assert body["source_run_id"] == "2026-10-12"
    assert len(body["credits"]) == 20 and body["trips"] == [] and body["needs_a_look"] == []
    dates = [x["date"] for x in body["credits"]]
    assert dates == sorted(dates)
    first = body["credits"][0]
    assert set(first) == {
        "txn_key",
        "date",
        "account_key",
        "account_name",
        "merchant",
        "description",
        "cents",
        "trip_key",
        "options",
        "note",
        "guess",
    }
    # Trip-tagged rows group apart.
    trip = add_row(
        samples,
        SUMMIT,
        "2026-09-09",
        1_005,
        "POINTS FOR TRAVEL",
        "Summit Travel",
        trip_key="day-trip-sep26",
    )
    assert list(listed(review(admin), "trips")) == [trip]

    # A tie: "Matches more than one benefit. Pick one."
    words(admin, DINING, "DINING CREDIT")
    words(admin, TRAVEL, "DINING CREDIT")
    body = review(admin)
    tied = [x for x in body["credits"] if x["options"]]
    assert len(tied) == 8 and body["counts"]["ambiguous"] == 8
    assert {x["note"] for x in tied} == {"Matches more than one benefit. Pick one."}
    assert {tuple(x["options"]) for x in tied} == {(DINING, TRAVEL)}
    assert {x["guess"]["benefit_key"] for x in tied} == {DINING}  # the room equal to the amount
    words(admin, TRAVEL)

    # Needs a look: a lost choice, a row no cycle holds, a chat-only cycle.
    guard = {"account_key": SUMMIT, "txn_date": "2026-04-02", "amount_cents": 4_321}
    guard |= {"desc_norm": "GONE CREDIT", "merchant": "Gone"}
    with write_connection(samples.db_path) as w:
        insert(
            w,
            "reward_credit_decisions",
            {
                "txn_key": NO_KEY,
                "decision": "link",
                "benefit_key": TRAVEL,
                "guard_json": json.dumps(guard),
                "updated_at": NOW,
            },
        )
    cycle = {"start": "2026-03-01", "end": "2026-04-30"}
    key = add_benefit(
        admin, **LOUNGE | {"name": "Lounge pass", "cadence": "multi_year", "cycles": [cycle]}
    ).json()["key"]
    words(admin, key, "LOUNGE")
    lounge = add_row(samples, SUMMIT, "2026-06-15", 2_500, "LOUNGE CREDIT", "Lounge")
    words(admin, AIRLINE, "AIRLINE FEE")
    body = review(admin)
    assert body["needs_a_look"] == [
        {
            "kind": "lost_choice",
            "text": "A credit choice lost its row.",
            "benefit_key": TRAVEL,
            "benefit_name": "Yearly travel credit",
            "cycle_start": None,
            "txn_key": NO_KEY,
            "date": "2026-04-02",
            "cents": 4_321,
        },
        {
            "kind": "no_cycle",
            "text": "Matches a benefit, but no cycle holds its date.",
            "benefit_key": key,
            "benefit_name": "Lounge pass",
            "cycle_start": None,
            "txn_key": lounge,
            "date": "2026-06-15",
            "cents": 2_500,
        },
        {
            "kind": "chat_only",
            "text": "The chat says used, no credit row matches.",
            "benefit_key": AIRLINE,
            "benefit_name": "Airline fee credit",
            "cycle_start": "2026-01-01",
            "txn_key": None,
            "date": "2026-03-14",
            "cents": 5_000,
        },
    ]
    assert lounge not in listed(body)
    assert body["counts"] | {"to_review": 0} == ZERO | {
        "returns": 1,
        "trips": 1,
        "chat_says_used": 1,
        "needs_a_look": 2,  # the lost choice and the row no cycle holds; chat_only counts apart
    }
    # Over: twins in October.
    for _ in range(2):
        add_row(samples, SUMMIT, "2026-10-10", 1_000, "DINING CREDIT", "Dining Credit")
    assert review(admin)["counts"]["over"] == 1
    # Notices and orphans count as needing a look too. The page lists them.
    twin = LOUNGE | {"name": "Dining credit", "cadence": "monthly", "amount": "10.00"}
    assert add_benefit(admin, **twin).status_code == 201
    with write_connection(samples.db_path) as w:
        insert(
            w,
            "reward_match_terms",
            {"benefit_key": "gone-credit", "term": "GONE", "updated_at": NOW},
        )
    body, shown = review(admin), page(admin)
    # They live on the Benefits tab, so they count apart from the review's Needs a look.
    assert (body["counts"]["needs_a_look"], body["counts"]["benefit_notes"]) == (2, 2)
    assert len(body["needs_a_look"]) == 3
    assert [n["kind"] for n in shown["notices"]] == ["benefit_duplicate"]
    assert shown["orphans"] == [
        {
            "kind": "benefit",
            "key": "gone-credit",
            "holds": ["terms"],
            "reason": "dropped",
            "unit": None,
        }
    ]
    # Hidden benefits: their rows never flag and their cycles leave the counts.
    assert patch_benefit(admin, key, hidden=True).status_code == 200
    assert patch_benefit(admin, AIRLINE, hidden=True).status_code == 200
    assert patch_benefit(admin, DINING, hidden=True).status_code == 200
    body = review(admin)
    assert [x["kind"] for x in body["needs_a_look"]] == ["lost_choice"]
    assert (body["counts"]["over"], body["counts"]["chat_says_used"]) == (0, 0)
    assert lounge not in listed(body)  # a hidden benefit's words still claim it


def test_before_the_first_13_run(settings: Settings, admin: TestClient) -> None:
    body = review(admin)
    assert body == {
        "source_run_id": None,
        "needs_a_look": [],
        "credits": [],
        "returns": [],
        "trips": [],
        "counts": ZERO,
    }
    assert page(admin)["review_counts"] == ZERO
    assert admin.get(f"/api/rewards/benefits/{DINING}/terms").status_code == 404
    writes = [
        put_terms(admin, DINING, ["DINING CREDIT"]),
        preview(admin, DINING, "DINING CREDIT"),
        mark(admin, DINING, "2026-10-01", "10.00"),
        clear_mark(admin, DINING, "2026-10-01"),
        choose(admin, NO_KEY, benefit_key=DINING),
        choose(admin, NO_KEY, "not_benefit", reason="other"),
        unchoose(admin, NO_KEY),
    ]
    for r in writes:
        assert r.status_code == 409, (r.request.method, r.request.url, r.text)
        assert r.json()["detail"] == "Rewards start with the chat's first export."
    assert counts(settings) == dict.fromkeys(REWARD_TABLES, 0) and events(settings) == []


# --------------------------------------------------------------------------
# Choices (plan 8, "Choices")
# --------------------------------------------------------------------------


def test_count_it_not_a_benefit_and_a_pinned_cycle(samples: Settings, member: TestClient) -> None:
    statement, return_row = statement_credit(samples), towne(samples)
    guess = listed(review(member))[statement]["guess"]

    r = choose(member, statement, benefit_key=guess["benefit_key"])  # Count it
    assert r.status_code == 200, r.text
    assert r.json() == {
        "txn_key": statement,
        "decision": "link",
        "benefit_key": AIRLINE,
        "cycle_start": None,
        "reason": None,
        "counted_in": {"benefit_key": AIRLINE, "cycle_start": "2026-07-01"},
    }
    july = cycles(member, AIRLINE)["2026-07-01"]
    assert (july["source"], july["used_cents"], july["state"]) == ("rows", 2_500, "partial")
    assert statement not in listed(review(member))

    r = choose(member, return_row, "not_benefit", reason="return")  # Not a benefit
    assert r.status_code == 200 and r.json()["counted_in"] is None
    assert r.json()["reason"] == "return" and r.json()["benefit_key"] is None
    body = review(member)
    assert body["returns"] == [] and body["counts"]["returns"] == 0

    # "Count in January instead": a pinned cycle.
    with write_connection(samples.db_path) as w:  # words are an admin's; set here by hand
        insert(
            w,
            "reward_match_terms",
            {"benefit_key": WIRELESS, "term": "WIRELESS CREDIT", "updated_at": NOW},
        )
    feb = key_of(samples, "WIRELESS CREDIT", "2026-02-05", "orchard-card")
    [row] = cycles(member, WIRELESS)["2026-02-01"]["rows"]
    assert (row["txn_key"], row["earlier_start"]) == (feb, "2026-01-01")
    r = choose(member, feb, benefit_key=WIRELESS, cycle_start="2026-01-01")
    assert r.json()["counted_in"] == {"benefit_key": WIRELESS, "cycle_start": "2026-01-01"}
    found = cycles(member, WIRELESS)
    assert [x["txn_key"] for x in found["2026-01-01"]["rows"]][-1] == feb
    assert found["2026-01-01"]["over_cents"] == 500
    # February is decided: 0 from rows, never the chat's $5, which counts the same row.
    february = found["2026-02-01"]
    assert (february["source"], february["used_cents"], february["chat_used_cents"]) == (
        "rows",
        0,
        500,
    )
    assert review(member)["counts"]["over"] == 1

    # A second choice on a row replaces the first. Clear undoes it.
    r = choose(member, return_row, benefit_key=TRAVEL)
    assert r.json()["counted_in"] == {"benefit_key": TRAVEL, "cycle_start": "2026-01-01"}
    assert sorted(choices(samples)) == sorted([statement, return_row, feb])
    assert unchoose(member, return_row).status_code == 204
    assert list(listed(review(member), "returns")) == [return_row]
    r = unchoose(member, return_row)
    assert r.status_code == 404 and r.json()["detail"] == "No choice on that row."
    assert [json.loads(e["detail_json"]) for e in events(samples)] == [
        {"action": "link", "replaced": False},
        {"action": "not_benefit", "replaced": False},
        {"action": "link", "replaced": False, "pinned": True},
        {"action": "link", "replaced": True},
        {"action": "clear"},
    ]


def test_choice_rules(samples: Settings, admin: TestClient) -> None:
    statement = statement_credit(samples)
    purchase = q(
        samples,
        """SELECT txn_key FROM v_transactions WHERE account_key = ? AND txn_date = '2026-08-12'
           AND description = 'CEDAR CAFE'""",
        (SUMMIT,),
    )[0][0]
    hidden = add_row(samples, SUMMIT, "2026-08-20", 700, "DINING CREDIT", "Dining", hidden=True)
    checking = add_row(samples, "maple-joint-checking", "2026-08-20", 700, "REFUND", "Shop")
    transfer = add_row(samples, SUMMIT, "2026-08-21", 700, "CREDIT", "Shop", is_transfer=1)
    removed = add_row(samples, SUMMIT, "2026-08-22", 700, "CREDIT", "Shop", removed_at=NOW)
    assert patch_benefit(admin, AIRLINE, match_off=True).status_code == 200
    assert patch_benefit(admin, TRAVEL, hidden=True).status_code == 200
    n_events = len(events(samples))
    for txn_key, body, status, detail in [
        (NO_KEY, {"benefit_key": DINING}, 404, "No statement row with that key."),
        (removed, {"benefit_key": DINING}, 404, "No statement row with that key."),
        (purchase, {"benefit_key": DINING}, 422, "This row is not a credit on a card."),
        (hidden, {"benefit_key": DINING}, 422, "This row is not a credit on a card."),
        (checking, {"benefit_key": DINING}, 422, "This row is not a credit on a card."),
        (transfer, {"benefit_key": DINING}, 422, "This row is not a credit on a card."),
        (statement, {"benefit_key": WIRELESS}, 422, "Pick a credit on this row's card."),
        (statement, {"benefit_key": "anniversary-night"}, 422, "Pick a credit on this row's card."),
        (statement, {"benefit_key": "fuel-bonus"}, 422, "Pick a credit on this row's card."),
        (statement, {"benefit_key": "no-such-credit"}, 422, "Pick a credit on this row's card."),
        (statement, {"benefit_key": TRAVEL}, 422, "Pick a credit on this row's card."),
        (
            statement,
            {"benefit_key": AIRLINE},
            422,
            "That credit never shows on statements. Turn matching on first.",
        ),
        (
            statement,
            {"benefit_key": DINING, "cycle_start": "2026-08-15"},
            422,
            "Pick a cycle of that credit.",
        ),
        (
            statement,
            {"benefit_key": DINING, "cycle_start": "2025-12-01"},
            422,
            "Pick a cycle of that credit.",
        ),
    ]:
        r = choose(admin, txn_key, **body)
        assert (r.status_code, r.json()["detail"]) == (status, detail), (body, r.text)
    # The body must fit the decision. Errors name places, never values.
    for body in [
        {"decision": "link"},
        {"decision": "link", "benefit_key": DINING, "reason": "other"},
        {"decision": "not_benefit"},
        {"decision": "not_benefit", "reason": "other", "benefit_key": DINING},
        {"decision": "not_benefit", "reason": "other", "cycle_start": "2026-08-01"},
        {"decision": "not_benefit", "reason": "gift"},
        {"decision": "maybe"},
        {"decision": "link", "benefit_key": DINING, "note": "x"},
        {"decision": "link", "benefit_key": "Dining Credit"},
    ]:
        r = admin.put(f"/api/rewards/credits/{statement}", json=body, headers=H)
        assert r.status_code == 422 and r.json()["detail"] == "Invalid request.", body
    for bad in ("STATEMENT", "a" * 63, "A" * 64):
        assert choose(admin, bad, benefit_key=DINING).status_code == 422
    assert unchoose(admin, NO_KEY).status_code == 404
    assert choices(samples) == [] and len(events(samples)) == n_events


def test_a_choice_on_a_row_a_word_claims(samples: Settings, admin: TestClient) -> None:
    words(admin, DINING, "DINING CREDIT")
    aug = key_of(samples, "DINING CREDIT", "2026-08-12")
    r = choose(admin, aug, benefit_key=TRAVEL)  # Different benefit
    assert r.json()["counted_in"] == {"benefit_key": TRAVEL, "cycle_start": "2026-01-01"}
    found = cycles(admin, DINING)["2026-08-01"]
    # Decided: the chat's $10 counts the row that went to travel, so it reads 0 from rows.
    assert (found["source"], found["used_cents"], found["rows"]) == ("rows", 0, [])
    assert found["chat_used_cents"] == 1_000 and review(admin)["counts"]["chat_says_used"] == 0
    assert choose(admin, aug, "not_benefit", reason="other").json()["counted_in"] is None
    assert unchoose(admin, aug).status_code == 204
    assert cycles(admin, DINING)["2026-08-01"]["source"] == "rows"


@pytest.mark.parametrize("action", ["put", "delete"])
def test_a_choice_follows_its_row_through_a_correction(
    samples: Settings, admin: TestClient, tmp_path: Path, action: str
) -> None:
    words(admin, DINING, "DINING CREDIT")
    typo = summit_correction(
        tmp_path,
        "2026-10-16",
        "2026-10-12",
        "2026-09-10",
        _describe("DINING CREDIT", "DINING CREDT"),
    )
    assert import_folder(typo, settings=samples, today=TODAY).status == "imported"
    old = key_of(samples, "DINING CREDT", "2026-08-12")
    assert listed(review(admin))[old]["description"] == "DINING CREDT"
    assert choose(admin, old, benefit_key=TRAVEL).status_code == 200  # Different benefit
    [guard] = q(samples, "SELECT guard_json FROM reward_credit_decisions")
    saved = json.loads(guard[0])
    assert saved["run_id"] == "2026-10-16"  # the run that wrote the row
    # row_sig proves the key still holds this row; near lists the rows live then.
    assert saved["row_sig"] == rm.row_sig("DINING CREDT", "Dining Credit")
    assert old in saved["near"] and statement_credit(samples) in saved["near"]
    assert key_of(samples, "DINING CREDIT", "2026-07-12") not in saved["near"]  # a month before

    fix = _describe("DINING CREDIT", "DINING CREDIT AUG")  # a new row: a later run wrote it
    fixed = summit_correction(tmp_path, "2026-10-18", "2026-10-12", "2026-09-10", fix)
    assert import_folder(fixed, settings=samples, today=TODAY).status == "imported"
    new = key_of(samples, "DINING CREDIT AUG", "2026-08-12")
    assert new != old and choices(samples) == [old]
    travel = cycles(admin, TRAVEL)["2026-01-01"]
    assert [x["txn_key"] for x in travel["rows"]] == [new]  # the choice followed its row
    assert lost(review(admin)) == []  # the row counts for travel; August reads 0 from rows

    if action == "put":  # a write on the new row replaces the old choice, never a lost one
        r = choose(admin, new, "not_benefit", reason="other")
        assert r.status_code == 200 and choices(samples) == [new]
        assert json.loads(events(samples)[-1]["detail_json"])["replaced"] is True
    else:  # a delete on the new row clears the old choice
        assert unchoose(admin, new).status_code == 204 and choices(samples) == []
        assert cycles(admin, DINING)["2026-08-01"]["source"] == "rows"
    assert lost(review(admin)) == []
    assert cycles(admin, TRAVEL)["2026-01-01"]["rows"] == []


def test_a_saved_guard_holds_the_imported_identity_and_hashes_only(
    samples: Settings, admin: TestClient
) -> None:
    """Plan section 2: guard_json holds the overrides-style identity plus
    row_sig and near, and those two hold sha256 hex only. The full description
    is in no value: desc_norm is its first 32 characters, like txn_key's."""
    long = "DINING CREDIT MONTHLY ADJUSTMENT ALPHA"
    key = add_row(samples, SUMMIT, "2026-08-20", 1_000, long, "Dining Credit")
    assert choose(admin, key, benefit_key=TRAVEL).status_code == 200
    [(raw,)] = q(samples, "SELECT guard_json FROM reward_credit_decisions")
    guard = json.loads(raw)
    assert set(guard) == {*ri.GUARD_FIELDS, *ri.GUARD_HASHES}
    hashes = [guard["row_sig"], *guard["near"], *guard["near"].values()]
    assert all(isinstance(h, str) and re.fullmatch(SHA256_PATTERN, h) for h in hashes)
    assert guard["row_sig"] == rm.row_sig(long, "Dining Credit") == guard["near"][key]
    assert statement_credit(samples) in guard["near"]  # 08-18, within 7 days
    assert guard["desc_norm"] == long[:32] and long not in raw


def test_a_correction_that_brings_back_the_old_row_loses_the_choice(
    samples: Settings, admin: TestClient, tmp_path: Path
) -> None:
    """The old row comes back with its old run, like a twin that was live all
    along. The choice is lost (Delete), never a guess, and the word counts the
    row again."""
    words(admin, DINING, "DINING CREDIT")
    typo = summit_correction(
        tmp_path,
        "2026-10-16",
        "2026-10-12",
        "2026-09-10",
        _describe("DINING CREDIT", "DINING CREDT"),
    )
    assert import_folder(typo, settings=samples, today=TODAY).status == "imported"
    old = key_of(samples, "DINING CREDT", "2026-08-12")
    assert choose(admin, old, benefit_key=TRAVEL).status_code == 200
    back = summit_correction(tmp_path, "2026-10-18", "2026-10-12", "2026-09-10", lambda rows: None)
    assert import_folder(back, settings=samples, today=TODAY).status == "imported"
    assert lost(review(admin)) == [old]
    assert cycles(admin, TRAVEL)["2026-01-01"]["rows"] == []
    assert cycles(admin, DINING)["2026-08-01"]["source"] == "rows"


def test_a_choice_on_a_dropped_twin_never_moves_to_the_other(
    samples: Settings, admin: TestClient, tmp_path: Path
) -> None:
    """Two same-day DINING CREDIT rows in August. The second is marked Not a
    benefit, then the chat drops it. The first was live all along, so the
    choice is lost, and August keeps its row."""
    words(admin, DINING, "DINING CREDIT")

    def twin(rows: list[dict]) -> None:  # the credit and the purchase it offsets, twice
        extra = [
            dict(r)
            for r in rows
            if r["txn_date"] == "2026-08-12" and r["description"] in ("DINING CREDIT", "CEDAR CAFE")
        ]
        assert len(extra) == 2
        rows.extend(extra)

    def fix(st: dict) -> None:
        st["credits"] = _add_cents(st["credits"], 1_000)
        st["debits"] = _add_cents(st["debits"], -1_000)  # debits are negative
        st["txn_count"] += 2

    run_dir = summit_correction(tmp_path, "2026-10-16", "2026-10-12", "2026-09-10", twin, fix)
    assert import_folder(run_dir, settings=samples, today=TODAY).status == "imported"
    rows = q(
        samples,
        """SELECT txn_key, run_id FROM v_transactions WHERE account_key = ?
           AND txn_date = '2026-08-12' AND description = 'DINING CREDIT'""",
        (SUMMIT,),
    )
    first = next(r[0] for r in rows if r[1] == "2026-10-12")
    second = next(r[0] for r in rows if r[1] == "2026-10-16")
    assert cycles(admin, DINING)["2026-08-01"]["over_cents"] == 1_000
    assert choose(admin, second, "not_benefit", reason="other").status_code == 200

    back = summit_correction(tmp_path, "2026-10-18", "2026-10-12", "2026-09-10", lambda rows: None)
    assert import_folder(back, settings=samples, today=TODAY).status == "imported"
    assert lost(review(admin)) == [second]
    aug = cycles(admin, DINING)["2026-08-01"]
    assert (aug["source"], [x["txn_key"] for x in aug["rows"]]) == ("rows", [first])


def test_a_deleted_row_with_a_twin_a_month_later_is_lost(
    samples: Settings, member: TestClient, tmp_path: Path
) -> None:
    july = key_of(samples, "DINING CREDIT", "2026-07-12")
    assert choose(member, july, benefit_key=TRAVEL).status_code == 200

    def drop(rows: list[dict]) -> None:  # the credit and the purchase it offsets
        rows[:] = [
            r
            for r in rows
            if not (
                r["txn_date"] == "2026-07-12"
                and r["description"] in ("DINING CREDIT", "CEDAR CAFE")
            )
        ]

    def fix(st: dict) -> None:
        st["credits"] = _add_cents(st["credits"], -1_000)
        st["debits"] = _add_cents(st["debits"], 1_000)
        st["txn_count"] -= 2

    run_dir = summit_correction(tmp_path, "2026-10-16", "2026-10-06-2", "2026-08-10", drop, fix)
    assert import_folder(run_dir, settings=samples, today=TODAY).status == "imported"
    body = review(member)
    assert [(x["kind"], x["txn_key"], x["date"], x["cents"]) for x in body["needs_a_look"]] == [
        ("lost_choice", july, "2026-07-12", 1_000)
    ]
    assert body["counts"]["needs_a_look"] == 1
    assert cycles(member, TRAVEL)["2026-01-01"]["rows"] == []  # no jump to the twin
    august = key_of(samples, "DINING CREDIT", "2026-08-12")
    assert august in listed(body)
    r = choose(member, july, benefit_key=TRAVEL)
    assert r.status_code == 404  # the row is gone: only Delete
    assert unchoose(member, july).status_code == 204  # Delete
    assert review(member)["needs_a_look"] == [] and choices(samples) == []


def test_reapply_leaves_choices_marks_and_words(samples: Settings, admin: TestClient) -> None:
    words(admin, DINING, "DINING CREDIT")
    assert choose(admin, statement_credit(samples), benefit_key=AIRLINE).status_code == 200
    assert choose(admin, towne(samples), "not_benefit", reason="return").status_code == 200
    assert mark(admin, DINING, "2026-10-01", "10.00", "2026-10-05").status_code == 200

    def state() -> tuple:
        tables = {
            t: [tuple(r) for r in q(samples, f"SELECT * FROM {t} ORDER BY 1, 2")]
            for t in REWARD_TABLES
        }
        return tables, page(admin), review(admin)

    before = state()
    reapply(samples)
    clear_cache()
    assert state() == before


# --------------------------------------------------------------------------
# Cycle marks
# --------------------------------------------------------------------------


def test_mark_used_mark_not_used_and_clear(samples: Settings, member: TestClient) -> None:
    r = mark(member, DINING, "2026-10-01", "10.00", "2026-10-05")  # Mark used
    assert r.status_code == 200, r.text
    october = r.json()["cycle"]
    assert (october["source"], october["used_cents"], october["used_on"]) == (
        "set",
        1_000,
        "2026-10-05",
    )
    assert october["state"] == "done" and r.json()["group"] == "used"
    r = mark(member, DINING, "2026-08-01", "0.00")  # Mark not used, over the chat's $10
    aug = next(c for c in r.json()["past_cycles"] if c["start"] == "2026-08-01")
    assert (aug["source"], aug["used_cents"], aug["state"], aug["chat_used_cents"]) == (
        "set",
        0,
        "expired",
        1_000,
    )
    r = mark(member, DINING, "2026-10-01", "4.00")  # a second mark replaces the first
    assert r.json()["cycle"]["used_cents"] == 400 and r.json()["cycle"]["used_on"] is None
    assert q(samples, "SELECT count(*) FROM reward_cycle_marks")[0][0] == 2

    r = clear_mark(member, DINING, "2026-10-01")
    assert r.status_code == 200 and r.json()["cycle"]["source"] is None
    r = clear_mark(member, DINING, "2026-10-01")
    assert r.status_code == 404 and r.json()["detail"] == "No mark on that cycle."
    assert [json.loads(e["detail_json"]) for e in events(samples)] == [
        {"action": "set", "used": True},
        {"action": "set", "used": False},
        {"action": "replace", "used": True},
        {"action": "clear"},
    ]
    assert {e["entity"] for e in events(samples)} == {"mark"}


def test_mark_rules(samples: Settings, admin: TestClient) -> None:
    assert patch_benefit(admin, DINING, set={"ends_on": "2026-08-31"}).status_code == 200
    n = len(events(samples))
    for key, start, used, used_on, status, detail in [
        ("no-such-credit", "2026-10-01", "10.00", None, 404, "No benefit with that key."),
        ("anniversary-night", "2026-10-01", "10.00", None, 422, "Only credits have cycles."),
        (WIRELESS, "2026-10-15", "5.00", None, 404, "No cycle of this credit starts on that day."),
        (DINING, "2026-10-01", "10.00", None, 404, "No cycle of this credit starts on that day."),
        (WIRELESS, "2026-10-01", "5.00", "2026-10-21", 422, "The date cannot be in the future."),
        (
            WIRELESS,
            "2026-10-01",
            "0.00",
            "2026-10-02",
            422,
            "Leave the date out when the credit was not used.",
        ),
    ]:
        r = mark(admin, key, start, used, used_on)
        assert (r.status_code, r.json()["detail"]) == (status, detail), (key, start)
    for start, body in [
        ("2026-10-01", {"used": "5"}),
        ("2026-10-01", {"used": "-5.00"}),
        ("2026-10-01", {"used": "5.00", "used_on": "soon"}),
        ("2026-10-01", {"used": "5.00", "note": "x"}),
        ("october", {"used": "5.00"}),
    ]:
        r = admin.put(f"/api/rewards/benefits/{WIRELESS}/cycles/{start}/mark", json=body, headers=H)
        assert r.status_code == 422 and r.json()["detail"] == "Invalid request.", body
    assert clear_mark(admin, "no-such-credit", "2026-10-01").status_code == 404
    assert len(events(samples)) == n and counts(samples)["reward_cycle_marks"] == 0
    # A hidden credit takes marks, and a mark on a cycle the chat dropped can be cleared.
    assert patch_benefit(admin, WIRELESS, hidden=True).status_code == 200
    r = mark(admin, WIRELESS, "2026-10-01", "5.00", "2026-10-05")
    assert r.status_code == 200 and r.json()["hidden"] is True
    with write_connection(samples.db_path) as w:
        insert(
            w,
            "reward_cycle_marks",
            {
                "benefit_key": WIRELESS,
                "cycle_start": "2025-12-01",
                "used_cents": 0,
                "updated_at": NOW,
            },
        )
    assert clear_mark(admin, WIRELESS, "2025-12-01").status_code == 200


# --------------------------------------------------------------------------
# Match words and the preview
# --------------------------------------------------------------------------


def test_saved_suggested_and_seen_words(samples: Settings, member: TestClient) -> None:
    r = member.get(f"/api/rewards/benefits/{DINING}/terms")
    assert r.status_code == 200
    assert r.json() == {
        "benefit_key": DINING,
        "terms": [],
        "suggested": ["DINING"],
        "seen": {"term": "DINING CREDIT", "count": 8},  # January to August
        "match_off": False,
    }
    wireless = member.get(f"/api/rewards/benefits/{WIRELESS}/terms").json()
    assert wireless["seen"] == {"term": "WIRELESS CREDIT", "count": 9}
    airline = member.get(f"/api/rewards/benefits/{AIRLINE}/terms").json()
    assert (airline["suggested"], airline["seen"]) == (["AIRLINE", "FEE"], None)
    for key, status in (("anniversary-night", 422), ("no-such-credit", 404)):
        assert member.get(f"/api/rewards/benefits/{key}/terms").status_code == status


def test_seen_words_drop_digits_and_skip_bad_ones(samples: Settings, admin: TestClient) -> None:
    # The chat's next run says travel's $100 was used on Sep 3. Card credits near it:
    def used(data: dict) -> dict:
        for b in data["benefits"]:
            if b["key"] == TRAVEL:
                b["cycles"][0] |= {"used": "100.00", "used_on": "2026-09-03"}
        return data

    send_all(samples, used)
    add_row(samples, SUMMIT, "2026-09-01", 10_000, "TRAVEL CR 0901 STATEMENT", "Travel")
    add_row(samples, SUMMIT, "2026-09-05", 10_000, "STATEMENT CREDIT 4417", "Credit")
    add_row(samples, SUMMIT, "2026-09-07", 10_000, "TRAVEL CR LATE", "Travel")  # 4 days off
    seen = admin.get(f"/api/rewards/benefits/{TRAVEL}/terms").json()["seen"]
    assert seen == {"term": "TRAVEL CR STATEMENT", "count": 1}  # STATEMENT CREDIT is generic
    words(admin, TRAVEL, "travel cr statement")
    assert admin.get(f"/api/rewards/benefits/{TRAVEL}/terms").json()["seen"] is None
    # Rows 4 days from each of dining's used days count for none of them. Counted,
    # DINING CR LATE would tie DINING CREDIT and win on its name.
    for m in range(1, 9):
        add_row(samples, SUMMIT, f"2026-{m:02d}-16", 1_000, "DINING CR LATE", "Dining")
    seen = admin.get(f"/api/rewards/benefits/{DINING}/terms").json()["seen"]
    assert seen == {"term": "DINING CREDIT", "count": 8}


def test_put_words(samples: Settings, admin: TestClient) -> None:
    body = words(admin, DINING, "dining credit", "  Corner   Cafe ")
    assert body["terms"] == ["Corner Cafe", "dining credit"] and body["suggested"] == ["DINING"]
    assert next(b for b in page(admin)["benefits"] if b["key"] == DINING)["terms_count"] == 2
    assert [r[0] for r in q(samples, "SELECT term FROM reward_match_terms ORDER BY 1")] == [
        "Corner Cafe",
        "dining credit",
    ]
    assert cycles(admin, DINING)["2026-03-01"]["source"] == "rows"  # case is ignored
    body = words(admin, DINING, "DINING")
    assert body["terms"] == ["DINING"] and body["suggested"] == []
    for terms, detail in [
        (["STATEMENT CREDIT"], "Use a word from the credit line, like the merchant."),
        (["Card credits!"], "Use a word from the credit line, like the merchant."),
        (["ab"], "A match word is 3 to 40 plain letters, digits, spaces or marks."),
        (["x" * 41], "A match word is 3 to 40 plain letters, digits, spaces or marks."),
        (["CAFÉ CREDIT"], "A match word is 3 to 40 plain letters, digits, spaces or marks."),
        (["Dunkin", "DUNKIN"], "Each match word can be saved once."),
    ]:
        r = put_terms(admin, DINING, terms)
        assert (r.status_code, r.json()["detail"]) == (422, detail), terms
    for terms in ([f"WORD{chr(65 + n)}" for n in range(11)], ["DINING\tCREDIT"], [" "]):
        assert put_terms(admin, DINING, terms).json()["detail"] == "Invalid request."
    assert put_terms(admin, "anniversary-night", ["NIGHT"]).status_code == 422
    assert put_terms(admin, "no-such-credit", ["NIGHT"]).status_code == 404
    ten = [f"WORD{chr(65 + n)}" for n in range(10)]
    assert words(admin, DINING, *ten)["terms"] == ten
    assert words(admin, DINING)["terms"] == []  # 0 clears them
    assert [json.loads(e["detail_json"]) for e in events(samples)] == [
        {"action": "replace", "terms": 2, "added": 2, "removed": 0},
        {"action": "replace", "terms": 1, "added": 1, "removed": 2},
        {"action": "replace", "terms": 10, "added": 10, "removed": 1},
        {"action": "replace", "terms": 0, "added": 0, "removed": 10},
    ]
    assert {(e["entity"], e["entity_key"]) for e in events(samples)} == {("terms", DINING)}
    # Suggested words leave out cadence, filler and generic words.
    key = add_benefit(admin, **LOUNGE | {"name": "Monthly Resy dining credit"}).json()["key"]
    terms = admin.get(f"/api/rewards/benefits/{key}/terms").json()
    assert (terms["suggested"], terms["seen"]) == (["RESY", "DINING"], None)
    # Pieces of generic words are never suggested: they would claim every credit line.
    key = add_benefit(admin, **LOUNGE | {"name": "Point edit Resy credit"}).json()["key"]
    assert admin.get(f"/api/rewards/benefits/{key}/terms").json()["suggested"] == ["RESY"]


def test_preview_a_word(samples: Settings, admin: TestClient) -> None:
    r = preview(admin, DINING, "dining credit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["benefit_key"], body["matches"], body["elsewhere"]) == (DINING, 8, 0)
    assert [(x["date"], x["now_key"], x["after_key"], x["tie"]) for x in body["rows"]] == [
        (f"2026-{m:02d}-12", None, DINING, False) for m in range(1, 9)
    ]
    assert body["cycles"] == [
        {
            "benefit_key": DINING,
            "benefit_name": "Dining credit",
            "start": f"2026-{m:02d}-01",
            "used_cents_before": 1_000,
            "used_cents_after": 1_000,
            "source_before": "chat",
            "source_after": "rows",
        }
        for m in range(1, 9)
    ]
    assert counts(samples)["reward_match_terms"] == 0 and events(samples) == []  # nothing saved

    words(admin, DINING, "DINING CREDIT")
    same = preview(admin, DINING, "DINING CREDIT").json()  # the saved state reads the same
    assert same["cycles"] == [] and {(x["now_key"], x["after_key"]) for x in same["rows"]} == {
        (DINING, DINING)
    }
    # The longest word keeps the rows: they count for dining now and would stay there.
    shorter = preview(admin, TRAVEL, "DINING").json()
    assert (shorter["matches"], shorter["elsewhere"], shorter["cycles"]) == (8, 8, [])
    assert {(x["now_key"], x["after_key"], x["tie"]) for x in shorter["rows"]} == {
        (DINING, DINING, False)
    }
    assert {(x["now_name"], x["after_name"]) for x in shorter["rows"]} == {
        ("Dining credit", "Dining credit")
    }
    tie = preview(admin, TRAVEL, "DINING CREDIT").json()  # a tie: they wait for review
    assert tie["elsewhere"] == 8
    assert {(x["now_key"], x["after_key"], x["tie"]) for x in tie["rows"]} == {(DINING, None, True)}
    assert [(c["start"], c["source_before"], c["source_after"]) for c in tie["cycles"]] == [
        (f"2026-{m:02d}-01", "rows", "chat") for m in range(1, 9)
    ]
    # A mark wins over rows, so its cycle does not change.
    add_row(samples, SUMMIT, "2026-09-02", 10_000, "TRAVEL CR SUMMIT", "Travel")
    assert mark(admin, TRAVEL, "2026-01-01", "0.00").status_code == 200
    taken = preview(admin, TRAVEL, "TRAVEL CR").json()
    assert taken["matches"] == 1 and taken["cycles"] == []
    assert [(x["now_key"], x["after_key"]) for x in taken["rows"]] == [(None, TRAVEL)]
    for term, detail in [
        ("CREDIT", "Use a word from the credit line, like the merchant."),
        ("STATEMENT CR", "Use a word from the credit line, like the merchant."),
        ("EDIT", "Use a word from the credit line, like the merchant."),
        ("ab", "A match word is 3 to 40 plain letters, digits, spaces or marks."),
    ]:
        r = preview(admin, DINING, term)
        assert (r.status_code, r.json()["detail"]) == (422, detail)
    assert preview(admin, "anniversary-night", "NIGHT").status_code == 422
    assert preview(admin, "no-such-credit", "NIGHT").status_code == 404
    r = admin.post("/api/rewards/terms/preview", json={"benefit_key": DINING}, headers=H)
    assert r.status_code == 422


def test_match_off_drives_matching(samples: Settings, admin: TestClient) -> None:
    words(admin, DINING, "DINING CREDIT")
    assert review(admin)["counts"]["to_review"] == 12
    assert patch_benefit(admin, DINING, match_off=True).status_code == 200
    assert {cycles(admin, DINING)[f"2026-{m:02d}-01"]["source"] for m in range(1, 9)} == {"chat"}
    assert review(admin)["counts"]["to_review"] == 20  # its rows wait for review again
    terms = admin.get(f"/api/rewards/benefits/{DINING}/terms").json()
    assert terms["match_off"] is True and terms["terms"] == ["DINING CREDIT"]
    assert patch_benefit(admin, DINING, match_off=False).status_code == 200
    assert cycles(admin, DINING)["2026-08-01"]["source"] == "rows"


def test_a_link_to_a_credit_that_takes_no_rows_shows(samples: Settings, admin: TestClient) -> None:
    statement = statement_credit(samples)
    assert choose(admin, statement, benefit_key=AIRLINE).status_code == 200
    assert review(admin)["needs_a_look"] == []
    assert patch_benefit(admin, AIRLINE, match_off=True).status_code == 200
    body = review(admin)
    assert statement not in listed(body) and cycles(admin, AIRLINE)["2026-07-01"]["rows"] == []
    assert body["needs_a_look"] == [
        {
            "kind": "choice_off",
            "text": "Your choice counts it for a credit that takes no statement rows.",
            "benefit_key": AIRLINE,
            "benefit_name": "Airline fee credit",
            "cycle_start": None,
            "txn_key": statement,
            "date": "2026-08-18",
            "cents": 2_500,
        }
    ]
    assert body["counts"]["needs_a_look"] == 1
    assert admin.get("/api/checks").json()["rewards"]["counts"]["needs_a_look"] == 1
    assert unchoose(admin, statement).status_code == 204  # Delete
    body = review(admin)
    assert body["needs_a_look"] == [] and statement in listed(body)
    # A link to a key the chat dropped shows as an orphan, not twice.
    with write_connection(samples.db_path) as w:
        w.execute("UPDATE reward_benefit_edits SET match_off = 0 WHERE benefit_key = ?", (AIRLINE,))
    assert choose(admin, statement, benefit_key=AIRLINE).status_code == 200
    with write_connection(samples.db_path) as w:
        w.execute("UPDATE reward_credit_decisions SET benefit_key = 'gone-credit'")
    body, shown = review(admin), page(admin)
    assert body["needs_a_look"] == [] and body["counts"]["benefit_notes"] == 1
    assert [o["key"] for o in shown["orphans"]] == ["gone-credit"]


def test_a_tie_with_a_hidden_credit_offers_only_shown_ones(
    samples: Settings, admin: TestClient
) -> None:
    words(admin, DINING, "DINING CREDIT")
    words(admin, TRAVEL, "DINING CREDIT")
    words(admin, AIRLINE, "DINING CREDIT")
    assert patch_benefit(admin, TRAVEL, hidden=True).status_code == 200
    body = review(admin)
    tied = [x for x in body["credits"] if x["description"] == "DINING CREDIT"]
    assert len(tied) == 8 and {tuple(x["options"]) for x in tied} == {(AIRLINE, DINING)}
    # One shown credit left: a plain row to review, with it as the guess.
    assert patch_benefit(admin, AIRLINE, hidden=True).status_code == 200
    body = review(admin)
    tied = [x for x in body["credits"] if x["description"] == "DINING CREDIT"]
    assert len(tied) == 8 and body["counts"]["ambiguous"] == 0
    assert {(tuple(x["options"]), x["note"]) for x in tied} == {((), None)}
    assert {x["guess"]["benefit_key"] for x in tied} == {DINING}
    july = next(x for x in tied if x["date"] == "2026-07-12")
    r = choose(admin, july["txn_key"], benefit_key=july["guess"]["benefit_key"])  # Count it
    assert r.status_code == 200 and r.json()["counted_in"]["benefit_key"] == DINING
    # Hidden credits only: their words claim the rows, quietly.
    assert patch_benefit(admin, DINING, hidden=True).status_code == 200
    body = review(admin)
    assert not any(x["description"] == "DINING CREDIT" for x in body["credits"])
    assert body["needs_a_look"] == [] and body["counts"]["ambiguous"] == 0


def test_needs_a_look_counts_by_where_it_shows(samples: Settings, admin: TestClient) -> None:
    """Checks links each count where its items are: the review, the Benefits
    tab, or the Points tab."""
    r = admin.post(
        "/api/rewards/programs", json={"name": "Harbor Miles", "kind": "miles"}, headers=H
    )
    assert r.status_code == 201, r.text
    want = ZERO | {"to_review": 20, "returns": 1, "program_notes": 1}
    assert review(admin)["counts"] == want and review(admin)["needs_a_look"] == []
    assert page(admin)["review_counts"] == want
    assert admin.get("/api/checks").json()["rewards"]["counts"] == want


def test_move_and_delete_carry_words_choices_and_marks(
    samples: Settings, admin: TestClient
) -> None:
    key = add_benefit(admin, **LOUNGE).json()["key"]
    words(admin, key, "DINING CREDIT")
    statement = statement_credit(samples)
    assert choose(admin, statement, benefit_key=key).status_code == 200
    assert mark(admin, key, "2026-10-01", "25.00", "2026-10-02").status_code == 200
    lounge = cycles(admin, key)
    assert lounge["2026-07-01"]["used_cents"] == 1_000 + 1_000 + 2_500
    assert cycles(admin, DINING)["2026-08-01"]["source"] == "chat"

    r = admin.post(f"/api/rewards/benefits/{key}/move", json={"to_key": DINING}, headers=H)
    assert r.status_code == 200, r.text
    dining = cycles(admin, DINING)
    assert (dining["2026-03-01"]["source"], dining["2026-03-01"]["used_cents"]) == ("rows", 1_000)
    aug = dining["2026-08-01"]  # its row and the STATEMENT CREDIT choice
    assert (aug["used_cents"], aug["over_cents"]) == (3_500, 2_500)
    assert (dining["2026-10-01"]["source"], dining["2026-10-01"]["used_cents"]) == ("set", 2_500)
    assert admin.get(f"/api/rewards/benefits/{DINING}/terms").json()["terms"] == ["DINING CREDIT"]

    # Delete an app credit: its words go, and the rows they claimed come back.
    key = add_benefit(admin, **LOUNGE | {"name": "Outfitters credit"}).json()["key"]
    words(admin, key, "TOWNE")
    assert review(admin)["returns"] == []
    assert admin.delete(f"/api/rewards/benefits/{key}", headers=H).status_code == 204
    assert list(listed(review(admin), "returns")) == [towne(samples)]
    assert (
        q(samples, "SELECT count(*) FROM reward_match_terms WHERE benefit_key = ?", (key,))[0][0]
        == 0
    )


# --------------------------------------------------------------------------
# Safety: access, typed text, events, logs, the AI
# --------------------------------------------------------------------------


D2_ROUTES = {
    ("/api/rewards/review", "GET"),
    ("/api/rewards/benefits/{key}/terms", "GET"),
    ("/api/rewards/benefits/{key}/terms", "PUT"),
    ("/api/rewards/terms/preview", "POST"),
    ("/api/rewards/benefits/{key}/cycles/{start}/mark", "PUT"),
    ("/api/rewards/benefits/{key}/cycles/{start}/mark", "DELETE"),
    ("/api/rewards/credits/{txn_key}", "PUT"),
    ("/api/rewards/credits/{txn_key}", "DELETE"),
}


def test_the_new_routes_need_a_session(prod_app, prod: TestClient) -> None:
    routes = {
        (path, m)
        for path, methods, _ in walk_routes(prod_app)
        if path.startswith("/api/rewards")
        for m in methods - {"HEAD", "OPTIONS"}
    }
    assert routes >= D2_ROUTES
    for path, method in D2_ROUTES:
        url = path.replace("{key}", DINING).replace("{start}", "2026-10-01")
        r = prod.request(method, url.replace("{txn_key}", NO_KEY), headers=H)
        assert r.status_code == 401, (method, path)


def test_members_decide_and_mark_but_never_set_words(samples: Settings, member: TestClient) -> None:
    for r in (put_terms(member, DINING, ["DINING CREDIT"]), preview(member, DINING, "DINING")):
        assert r.status_code == 403 and r.json()["detail"] == "Only an admin can do this."
    assert counts(samples)["reward_match_terms"] == 0
    assert member.get(f"/api/rewards/benefits/{DINING}/terms").status_code == 200
    assert review(member)["counts"]["to_review"] == 20
    assert choose(member, statement_credit(samples), benefit_key=AIRLINE).status_code == 200
    assert unchoose(member, statement_credit(samples)).status_code == 204
    assert mark(member, DINING, "2026-10-01", "10.00").status_code == 200
    assert clear_mark(member, DINING, "2026-10-01").status_code == 200


def test_sensitive_words_are_422_and_never_stored(
    use_private_rules: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = use_private_rules
    add_users(settings)
    import_samples(settings)
    flagged = _flagged()
    assert len(flagged) >= 12 and "QX7B2K" in flagged and "EXAMPLE PRIVATE SERVICE" in flagged
    app = app_for(monkeypatch, dev=False, BUDGET_PRIVATE_RULES=str(settings.private_rules))
    with TestClient(app, base_url=PROD_URL) as c:
        assert sign_in(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        for value in flagged:
            for r in (
                put_terms(c, DINING, [value]),
                put_terms(c, DINING, ["DINING CREDIT", value]),
                preview(c, DINING, value),
            ):
                assert r.status_code == 422, (value, r.text)
                assert value not in r.text and value.replace(" ", "") not in r.text
        # The scan itself refuses these: they pass the match word rules.
        for value in ("QX7B2K", "EXAMPLE PRIVATE SERVICE"):
            assert rm.check_term(value) == value
            assert put_terms(c, DINING, [value]).json()["detail"].startswith("This text looks")
        words(c, DINING, "DINING CREDIT")  # a clean word still saves
    with read_connection(settings.db_path) as conn:
        stored = _text_columns(conn)
        dump = "\n".join(conn.iterdump())
    for value in flagged:
        assert value not in stored and value not in dump, value
    assert [r[0] for r in q(settings, "SELECT term FROM reward_match_terms")] == ["DINING CREDIT"]


EVENT_FIELDS = {"action", "terms", "added", "removed", "used", "replaced", "pinned"}
ACTIONS = {"replace", "set", "clear", "link", "not_benefit"}


def test_events_hold_no_values(samples: Settings, admin: TestClient) -> None:
    statement = statement_credit(samples)
    words(admin, DINING, "DINING CREDIT", "CORNER CAFE")
    preview(admin, TRAVEL, "LANTERN")
    assert (
        choose(admin, statement, benefit_key=AIRLINE, cycle_start="2026-07-01").status_code == 200
    )
    assert choose(admin, towne(samples), "not_benefit", reason="return").status_code == 200
    assert unchoose(admin, towne(samples)).status_code == 204
    assert mark(admin, DINING, "2026-10-01", "7.31", "2026-10-03").status_code == 200
    assert mark(admin, DINING, "2026-10-01", "0.00").status_code == 200
    assert clear_mark(admin, DINING, "2026-10-01").status_code == 200
    rows = events(samples)
    assert [(r["entity"], r["entity_key"]) for r in rows] == [
        ("terms", DINING),
        ("credit", statement),  # a hash, never a value
        ("credit", towne(samples)),
        ("credit", towne(samples)),
        ("mark", DINING),
        ("mark", DINING),
        ("mark", DINING),
    ]
    for r in rows:
        detail = json.loads(r["detail_json"])
        assert set(detail) <= EVENT_FIELDS and detail["action"] in ACTIONS
        for v in detail.values():
            assert isinstance(v, bool | int) or v in ACTIONS
    text = "\n".join(r["detail_json"] for r in rows)
    for value in ("DINING", "CORNER", "LANTERN", "7.31", "731", "2026-10-03", "2026-07-01"):
        assert value not in text, value
    for value in ("return", "2500", "25.00", "AIRLINE"):
        assert value not in text, value


def test_writes_outside_a_write_transaction_raise(samples: Settings) -> None:
    statement = statement_credit(samples)
    kw = {"user_id": None, "now": NOW}
    start = date(2026, 10, 1)
    calls = [
        lambda c: ri.set_terms(c, DINING, ["DINING CREDIT"], private=None, **kw),
        lambda c: ri.set_mark(c, DINING, start, 1_000, None, today=TODAY, **kw),
        lambda c: ri.delete_mark(c, DINING, start, **kw),
        lambda c: ri.set_choice(
            c,
            statement,
            "link",
            benefit_key=AIRLINE,
            cycle_start=None,
            reason=None,
            today=TODAY,
            **kw,
        ),
        lambda c: ri.delete_choice(c, statement, today=TODAY, **kw),
    ]
    conn = connect(samples.db_path)
    try:
        for call in calls:
            assert not conn.in_transaction
            with pytest.raises(RuntimeError):
                call(conn)
        with write_transaction(conn):
            ri.set_terms(conn, DINING, ["DINING CREDIT"], private=None, **kw)
    finally:
        conn.close()
    assert counts(samples)["reward_match_terms"] == 1


def test_reward_writes_leave_the_ai_facts_signature_alone(
    samples: Settings, admin: TestClient
) -> None:
    conn = connect(samples.db_path)
    try:
        before = signature(conn)
    finally:
        conn.close()
    words(admin, DINING, "DINING CREDIT")
    assert choose(admin, statement_credit(samples), benefit_key=AIRLINE).status_code == 200
    assert choose(admin, towne(samples), "not_benefit", reason="return").status_code == 200
    assert mark(admin, DINING, "2026-10-01", "10.00", "2026-10-05").status_code == 200
    conn = connect(samples.db_path)
    try:
        assert signature(conn) == before
    finally:
        conn.close()


def test_logs_hold_no_values_on_the_choice_routes(
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
        assert sign_in(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        statement = statement_credit(settings)
        preview(c, DINING, "LANTERN TERRACE")
        preview(c, DINING, "QX7B2K")
        put_terms(c, DINING, ["CORNER CAFE"])
        put_terms(c, DINING, ["STATEMENT CREDIT"])  # 422
        c.get(f"/api/rewards/benefits/{DINING}/terms")
        c.get("/api/rewards/review")
        choose(c, statement, benefit_key=AIRLINE, cycle_start="2026-07-01")
        choose(c, statement, benefit_key="no-such-credit")  # 422
        unchoose(c, statement)
        mark(c, DINING, "2026-10-01", "7.31", "2026-10-03")
        clear_mark(c, DINING, "2026-10-01")
    fmt = JsonFormatter()
    text = capsys.readouterr().out + "\n".join(fmt.format(r) for r in caplog.records)
    for route in (
        "/api/rewards/terms/preview",
        "/api/rewards/benefits/{key}/terms",
        "/api/rewards/review",
        "/api/rewards/credits/{txn_key}",
        "/api/rewards/benefits/{key}/cycles/{start}/mark",
    ):
        assert f'"route": "{route}"' in text, route
    for value in (
        "LANTERN TERRACE",
        "QX7B2K",
        "CORNER CAFE",
        "STATEMENT CREDIT",
        statement,
        "no-such-credit",
        "7.31",
        "2026-10-03",
        "2026-10-01",
        DINING,
        AIRLINE,
    ):
        assert value not in text, value
