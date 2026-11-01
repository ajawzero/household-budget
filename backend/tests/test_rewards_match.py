"""Rewards inputs, chunk D2 core (docs/plans/rewards-inputs.md sections 3.3, 4
and 8): statement credits matched to card credits, choices with the read-time
re-find, cycle use from one source, coverage, and the rows to review.

Fake samples only. Household today is 2026-10-20. Chunk D1's rows: summit
DINING CREDIT +10.00 on the 12th, January to August; cobalt STREAMING CREDIT
on 05-04 and 08-03 (cobalt's rows start in May); orchard WIRELESS CREDIT on
the 5th, January to September; one summit STATEMENT CREDIT +25.00 that matches
nothing, and the TOWNE OUTFITTERS RETURN. Each credit has an offsetting
purchase with another merchant. Summit's statements run to 2026-09-10. Edge
cases use test-local rows and the pure `match`.
"""

from __future__ import annotations

import calendar
import hashlib
import itertools
import json
import random
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from bad_cases import _add_cents, edit_csv, read_json, write_json
from conftest import SAMPLES
from fastapi.testclient import TestClient

from app import config
from app.config import Settings
from app.db.connect import read_connection, write_connection
from app.importer.apply import apply_rows, load_stored_run
from app.importer.keys import statement_key
from app.importer.models import CsvRow, RewardBenefit, to_cents
from app.importer.runner import import_folder, reapply
from app.services import rewards as rw
from app.services import rewards_inputs as ri
from app.services import rewards_match as rm
from app.services import rewards_overlay as ov
from app.services.balances import clear_cache
from app.services.overrides import EditError
from app.state import readers

TODAY = date(2026, 10, 20)
NOW = "2026-10-20T12:00:00+00:00"
SUMMIT = "summit-card"
END = "2026-09-10"  # the summit statement that holds the August credits
STMT = f"{SUMMIT}:{END}"
FIX = "2026-10-16"  # in pure tests: the correction now current for each chosen row's statement
DINING = "dining-credit"
AIRLINE = "airline-fee-credit"
TRAVEL = "travel-credit"
WIRELESS = "wireless-credit"
REWARD_TABLES = (
    "reward_match_terms",
    "reward_credit_decisions",
    "reward_cycle_marks",
    "reward_benefit_edits",
)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


# --------------------------------------------------------------------------
# Pure matching: helpers
# --------------------------------------------------------------------------


def months(numbers, cents: int = 1_000, year: int = 2026) -> tuple[rm.Cycle, ...]:
    return tuple(
        rm.Cycle(f"{year}-{m:02d}-01", f"{year}-{m:02d}-{calendar.monthrange(year, m)[1]}", cents)
        for m in numbers
    )


def benefit(
    key: str = "dining",
    name: str = "Dining credit",
    cycles: tuple[rm.Cycle, ...] | None = None,
    card: str = SUMMIT,
    ends_on: str | None = None,
    matches: bool = True,
    cents: int = 1_000,
) -> rm.Benefit:
    return rm.Benefit(key, card, name, ends_on, cycles or months(range(1, 13), cents), matches)


def crow(
    key: str,
    day: str,
    cents: int = 1_000,
    description: str = "DINING CREDIT",
    card: str = SUMMIT,
    merchant: str | None = "Dining Credit",
    desc_norm: str | None = None,
    trip_key: str | None = None,
    run: str | None = None,
) -> rm.CreditRow:
    norm = desc_norm or description[:32]
    return rm.CreditRow(
        key, card, day, cents, description, merchant, merchant, norm, trip_key, run, f"{card}:{END}"
    )


def fresh(key: str, day: str, *args, **kwargs) -> rm.CreditRow:
    """A row the correction FIX wrote."""
    return crow(key, day, *args, run=FIX, **kwargs)


def choice(
    txn_key: str,
    decision: str = "link",
    benefit_key: str | None = "dining",
    cycle_start: str | None = None,
    guard: dict | None = None,
) -> dict:
    link = decision == "link"
    return {
        "txn_key": txn_key,
        "decision": decision,
        "reason": None if link else "other",
        "benefit_key": benefit_key if link else None,
        "cycle_start": cycle_start,
        "guard_json": json.dumps(guard or {}),
    }


DINING_WORDS = {"dining": ("DINING CREDIT",)}


def saved(decisions, rows) -> list[dict]:
    """Choices as set_choice saves them: one with an empty guard gets its row's
    (ri.choice_guard): the imported identity, row_sig, and near, txn_key ->
    row_sig of the rows on its card within 7 days, its own too."""
    out = []
    for d in decisions:
        row = next((r for r in rows if r.txn_key == d["txn_key"]), None)
        if json.loads(d["guard_json"]) or row is None:
            out.append(d)
            continue
        lo, hi = (str(date.fromisoformat(row.txn_date) + timedelta(days=n)) for n in (-7, 7))
        guard = {
            "account_key": row.account_key,
            "txn_date": row.txn_date,
            "amount_cents": row.cents,
            "desc_norm": row.desc_norm,
            "merchant": row.imported_merchant,
            "run_id": row.run_id,
            "row_sig": rm.row_sig(row.description, row.imported_merchant),
            "near": {
                r.txn_key: rm.row_sig(r.description, r.imported_merchant)
                for r in rows
                if r.account_key == row.account_key and lo <= r.txn_date <= hi
            },
        }
        out.append(d | {"guard_json": json.dumps(guard)})
    return out


def run(
    benefits: list[rm.Benefit],
    rows: list[rm.CreditRow],
    terms: dict | None = None,
    decisions: list[dict] = (),
    purchases: list[rm.Purchase] = (),
    live: tuple[str, ...] = (),
    held: dict[str, rm.Held] | None = None,
) -> rm.Match:
    """Each choice's key holds its row when one is given; a row that is no
    longer a candidate, with the guard's row_sig, when it is in `live`; else
    nothing: the correction FIX, now its statement's current run, removed it.
    `held` overrides any of them."""
    picks = saved(decisions, rows)
    by_key = {r.txn_key: r for r in rows}
    now: dict[str, rm.Held] = {}
    for d in picks:
        key, row = d["txn_key"], by_key.get(d["txn_key"])
        if row is not None:
            sig = rm.row_sig(row.description, row.imported_merchant)
            now[key] = rm.Held(row.statement_key or STMT, FIX, sig)
        else:
            sig = json.loads(d["guard_json"]).get("row_sig") if key in live else None
            now[key] = rm.Held(STMT, FIX, sig)
    found = rm.Rows(tuple(rows), tuple(purchases), {}, now | (held or {}))
    words = DINING_WORDS if terms is None else terms
    return rm.match(benefits, words, picks, found, TODAY)


def where(m: rm.Match) -> dict[str, tuple[str, str]]:
    """txn_key -> (benefit key, cycle start)."""
    return {r.txn_key: at for at, rows in m.cycles.items() for r in rows}


def groups(m: rm.Match) -> dict[str, str]:
    return {x.txn_key: x.group for x in m.review}


# --------------------------------------------------------------------------
# Pure matching: words, home cycles, spill
# --------------------------------------------------------------------------


def test_words_count_a_row_in_its_home_cycle_on_its_own_card() -> None:
    cobalt = benefit("cobalt-dining", card="cobalt-card")
    m = run(
        [benefit(), cobalt],
        [crow("a", "2026-03-12"), crow("b", "2026-03-14", card="cobalt-card")],
        terms={"dining": ("DINING CREDIT",), "cobalt-dining": ("DINING CREDIT",)},
    )
    assert where(m) == {"a": ("dining", "2026-03-01"), "b": ("cobalt-dining", "2026-03-01")}
    assert m.review == () and m.flags == ()


def test_words_read_the_imported_description_only() -> None:
    long = "ISSUER STATEMENT CREDIT ADJUSTMENT DINING CREDIT"
    rows = [
        # desc_norm and the merchant hold the word; the description does not
        crow("a", "2026-03-12", description="SQ *CAFE", desc_norm="DINING CREDIT"),
        crow("b", "2026-04-12", description=long),  # past character 32
        crow("c", "2026-05-12", description="dining credit"),  # ASCII case ignored
    ]
    assert "DINING CREDIT" not in rows[1].desc_norm
    m = run([benefit()], rows)
    assert where(m) == {"b": ("dining", "2026-04-01"), "c": ("dining", "2026-05-01")}
    assert groups(m) == {"a": "review"}


def test_the_longest_word_wins_and_a_tie_is_ambiguous() -> None:
    dining, resy = benefit(), benefit("resy", "Resy credit", cents=5_000)
    dunkin = benefit("dunkin", "Dunkin credit", cents=700)
    terms = {"dining": ("DINING",), "resy": ("RESY DINING",), "dunkin": ("DUNKIN",)}
    rows = [
        crow("resy-row", "2026-03-18", description="RESY DINING CREDIT"),
        crow("dining-row", "2026-03-12", description="DINING CREDIT"),
        crow("tie", "2026-03-20", description="DUNKIN DINING"),
    ]
    m = run([dining, resy, dunkin], rows, terms=terms)
    assert where(m) == {"resy-row": ("resy", "2026-03-01"), "dining-row": ("dining", "2026-03-01")}
    assert m.review == (rm.ReviewItem("tie", "review", ("dining", "dunkin")),)
    # A benefit scores its longest word: DINING CREDIT now beats RESY DINING.
    terms["dining"] = ("DINING", "DINING CREDIT")
    m = run([dining, resy, dunkin], rows, terms=terms)
    assert where(m)["resy-row"] == ("dining", "2026-03-01")


def test_partials_sum_and_twins_go_over() -> None:
    rows = [
        crow("four", "2026-03-10", 400),
        crow("six", "2026-03-20", 600),
        crow("twin-1", "2026-05-14"),
        crow("twin-2", "2026-05-14"),
    ]
    m = run([benefit()], rows)
    assert [r.txn_key for r in m.cycles[("dining", "2026-03-01")]] == ["four", "six"]
    # twins well after April's end plus 7 days: both stay in May
    assert [r.txn_key for r in m.cycles[("dining", "2026-05-01")]] == ["twin-1", "twin-2"]


def test_spill_moves_only_when_a_cycle_is_over_and_the_earlier_one_has_room() -> None:
    b = benefit()
    # March is over: its row from the first 7 days moves to an empty February.
    m = run([b], [crow("early", "2026-03-03"), crow("late", "2026-03-12")])
    assert where(m) == {"early": ("dining", "2026-02-01"), "late": ("dining", "2026-03-01")}
    assert m.earlier == {}
    # February is full: nothing moves, and March stays over.
    m = run(
        [b], [crow("feb", "2026-02-12"), crow("early", "2026-03-03"), crow("late", "2026-03-12")]
    )
    assert where(m)["early"] == where(m)["late"] == ("dining", "2026-03-01")
    # March is not over: nothing moves, and the row offers February.
    m = run([b], [crow("early", "2026-03-03")])
    assert where(m) == {"early": ("dining", "2026-03-01")}
    assert m.earlier == {"early": "2026-02-01"}
    # Only rows dated up to February's end plus 7 days move.
    m = run([b], [crow("mid", "2026-03-08"), crow("late", "2026-03-12")])
    assert where(m)["mid"] == ("dining", "2026-03-01")
    # In date order, each only when February stays at or under its amount.
    m = run([b], [crow("big", "2026-03-02", 1_500), crow("small", "2026-03-04", 500)])
    assert where(m) == {"big": ("dining", "2026-03-01"), "small": ("dining", "2026-02-01")}


def test_pinned_rows_never_move_and_offer_nothing() -> None:
    rows = [crow("early", "2026-03-03"), crow("late", "2026-03-12")]
    m = run([benefit()], rows, decisions=[choice("early", cycle_start="2026-03-01")])
    assert where(m) == {"early": ("dining", "2026-03-01"), "late": ("dining", "2026-03-01")}
    assert m.pinned == {"early"} and m.earlier == {}


def test_a_7_day_late_post_counts_and_an_8_day_one_flags() -> None:
    lounge = rm.Benefit(
        "lounge", SUMMIT, "Lounge credit", None, (rm.Cycle("2026-03-01", "2026-03-31", 5_000),)
    )
    rows = [
        crow("seven", "2026-04-07", 2_500, "LOUNGE CREDIT"),
        crow("eight", "2026-04-08", 2_500, "LOUNGE CREDIT"),
    ]
    m = run([lounge], rows, terms={"lounge": ("LOUNGE",)})
    assert where(m) == {"seven": ("lounge", "2026-03-01")}
    assert m.flags == (rm.RowFlag("no_cycle", "eight", "lounge"),)
    assert m.review == ()  # a flagged row is not also to review


def test_a_benefit_that_ended_more_than_7_days_before_takes_nothing() -> None:
    ended = benefit(cycles=months(range(1, 7)), ends_on="2026-06-30")
    m = run([ended], [crow("seven", "2026-07-07"), crow("eight", "2026-07-08")])
    assert where(m) == {"seven": ("dining", "2026-06-01")}  # a late post for June
    assert groups(m) == {"eight": "review"}
    # ends_on cuts the last cycle, as the page does
    [cut] = rm.benefits_from(
        [
            RewardBenefit.model_validate(
                {
                    "key": "dining",
                    "account_key": SUMMIT,
                    "name": "Dining credit",
                    "kind": "credit",
                    "cadence": "quarterly",
                    "amount": "15.00",
                    "ends_on": "2026-05-15",
                    "cycles": [
                        {"start": "2026-07-01", "end": "2026-09-30"},
                        {"start": "2026-04-01", "end": "2026-06-30", "amount": "20.00"},
                    ],
                }
            )
        ],
        match_off=(),
    )
    assert cut.cycles == (rm.Cycle("2026-04-01", "2026-05-15", 2_000),)


def test_a_benefit_takes_nothing_from_before_it_starts() -> None:
    """A multi-year credit that began last year takes last year's rows. Other
    credits on the card take nothing dated before their first cycle, and rows
    before this household year never flag or wait for review."""
    entry = rm.Benefit(
        "entry", SUMMIT, "Entry fee credit", None, (rm.Cycle("2025-12-01", "2029-11-30", 10_000),)
    )
    terms = {"dining": ("DINING CREDIT",), "entry": ("ENTRY FEE",)}
    rows = [
        crow("dining-dec", "2025-12-12"),  # dining's cycles start in January
        crow("entry-dec", "2025-12-14", 8_500, "ENTRY FEE CREDIT"),
        crow("both-dec", "2025-12-20", 1_500, "ENTRY FEE DINING CREDIT"),  # dining had not started
        crow("mar", "2026-03-12"),
    ]
    m = run([benefit(), entry], rows, terms=terms)
    assert where(m) == {
        "entry-dec": ("entry", "2025-12-01"),
        "both-dec": ("entry", "2025-12-01"),
        "mar": ("dining", "2026-03-01"),
    }
    assert m.flags == () and m.review == ()
    # This year, before a credit's first cycle: to review, never "no cycle".
    lounge = rm.Benefit(
        "lounge", SUMMIT, "Lounge pass", None, (rm.Cycle("2026-06-01", "2026-12-31", 5_000),)
    )
    rows = [
        crow("march", "2026-03-05", 2_500, "LOUNGE"),
        crow("july", "2026-07-05", 2_500, "LOUNGE"),
    ]
    m = run([lounge], rows, terms={"lounge": ("LOUNGE",)})
    assert where(m) == {"july": ("lounge", "2026-06-01")}
    assert m.flags == () and groups(m) == {"march": "review"}
    # Before this year, a tie and a row no cycle holds are left alone.
    gap = rm.Benefit(
        "gap",
        SUMMIT,
        "Gap credit",
        None,
        (rm.Cycle("2025-10-01", "2025-10-31", 5_000), rm.Cycle("2026-01-01", "2026-12-31", 5_000)),
    )
    twin = replace(entry, key="twin", name="Twin fee credit")
    terms = {"entry": ("ENTRY FEE",), "twin": ("ENTRY FEE",), "gap": ("GAP CREDIT",)}
    rows = [
        crow("tie", "2025-12-20", description="ENTRY FEE"),
        crow("between", "2025-11-20", 900, "GAP CREDIT"),
    ]
    m = run([entry, twin, gap], rows, terms=terms)
    assert m.cycles == {} and m.flags == () and m.review == ()
    # This year, the same rows flag and wait for review.
    rows = [
        crow("tie", "2026-02-20", description="ENTRY FEE"),
        crow("late", "2026-01-20", 900, "GAP"),
    ]
    gap = replace(gap, cycles=gap.cycles[:1])
    m = run([entry, twin, gap], rows, terms=terms | {"gap": ("GAP",)})
    assert m.flags == (rm.RowFlag("no_cycle", "late", "gap"),)
    assert m.review == (rm.ReviewItem("tie", "review", ("entry", "twin")),)


def test_after_the_year_turns_last_years_choices_stay_quiet() -> None:
    """In January the chat sends only the new year's cycles. Choices and rows
    from last year count nowhere and flag nothing."""
    today = date(2027, 1, 15)
    dining = benefit(cycles=months(range(1, 13), year=2027))
    rows = rm.Rows(
        (
            crow("aug", "2026-08-12"),
            crow("dec", "2026-12-28", 500, "LANTERN CREDIT"),
            crow("nov", "2026-11-03", 500, "LANTERN CREDIT"),
            crow("jan", "2027-01-12"),
        ),
        (),
        {},
        {},
    )
    decisions = [
        choice("aug"),  # Count it, in August
        choice("dec", cycle_start="2026-12-01"),  # Count in December instead
        choice("nov", benefit_key="not-shown"),  # a credit that takes no rows
    ]
    m = rm.match([dining], DINING_WORDS, saved(decisions, rows.credits), rows, today)
    assert where(m) == {"jan": ("dining", "2027-01-01")}
    assert m.flags == () and m.review == () and m.decided == frozenset()


def test_coverage_runs_through_the_cycle_end_plus_7_days() -> None:
    august = rm.Cycle("2026-08-01", "2026-08-31", 1_000)
    assert rm.is_covered(("2026-07-11", "2026-09-07"), august)
    assert not rm.is_covered(("2026-07-11", "2026-09-06"), august)
    assert rm.is_covered(("2026-08-01", "2026-09-30"), august)
    assert not rm.is_covered(("2026-08-02", "2026-09-30"), august)
    assert not rm.is_covered(None, august)  # no statements


def test_match_off_benefits_take_nothing() -> None:
    m = run([benefit(matches=False)], [crow("a", "2026-03-12")])
    assert m.cycles == {} and groups(m) == {"a": "review"}
    m = run([benefit(matches=False)], [crow("a", "2026-03-12")], decisions=[choice("a")])
    assert m.cycles == {} and m.review == ()  # a choice holds it; it counts nowhere
    assert m.flags == (rm.RowFlag("off", "a", "dining"),)  # and it shows, with Delete


# --------------------------------------------------------------------------
# Pure matching: choices and the re-find
# --------------------------------------------------------------------------


def test_choices_come_first() -> None:
    dining, stay = benefit(), benefit("stay", "Stay credit", cents=5_000)
    rows = [
        crow("count", "2026-04-20", 2_500, "STATEMENT CREDIT"),
        crow("drop", "2026-05-12"),
        crow("pin", "2026-06-03"),
        crow("old-cycle", "2026-07-12", 500, "STATEMENT CREDIT"),
        crow("nowhere", "2026-08-12"),
        crow("other-card", "2026-08-20", card="cobalt-card"),
    ]
    decisions = [
        choice("count", benefit_key="stay"),  # Count it: its home cycle
        choice("drop", "not_benefit"),  # Not a benefit: its word loses
        choice("pin", cycle_start="2026-05-01"),  # Count in May instead
        choice("old-cycle", benefit_key="stay", cycle_start="2025-07-01"),  # gone: home cycle
        choice("nowhere", benefit_key="not-shown"),  # a benefit that takes no rows
        choice("other-card"),  # dining is on another card
    ]
    m = run([dining, stay], rows, decisions=decisions)
    assert where(m) == {
        "count": ("stay", "2026-04-01"),
        "pin": ("dining", "2026-05-01"),
        "old-cycle": ("stay", "2026-07-01"),
    }
    assert m.pinned == {"pin"} and m.review == ()
    # The cycles the words would have put drop, pin and nowhere in are decided.
    assert m.decided == {
        ("dining", "2026-05-01"),
        ("dining", "2026-06-01"),
        ("dining", "2026-08-01"),
    }
    # A link to a credit that takes no rows counts nowhere, and shows so it can be deleted.
    assert m.flags == (
        rm.RowFlag("off", "nowhere", "not-shown"),
        rm.RowFlag("off", "other-card", "dining"),
    )
    # With no choices the words take three of them, and the rest wait for review.
    m = run([dining, stay], rows)
    assert set(where(m)) == {"drop", "pin", "nowhere"}
    assert groups(m) == {"count": "review", "old-cycle": "review"}


GUARD = {
    "account_key": SUMMIT,
    "txn_date": "2026-03-12",
    "amount_cents": 1_000,
    "desc_norm": "DINING CREDT",
    "merchant": "Dining Credit",
    "row_sig": rm.row_sig("DINING CREDT", "Dining Credit"),
    "near": {"typo": rm.row_sig("DINING CREDT", "Dining Credit")},
}


def test_a_choice_refinds_its_row_after_a_correction() -> None:
    stay = benefit("stay", "Stay credit", cents=5_000)
    fixed = fresh("fixed", "2026-03-12")  # the typo fixed: a new key, the same merchant
    m = run([benefit(), stay], [fixed], decisions=[choice("typo", benefit_key="stay", guard=GUARD)])
    assert where(m) == {"fixed": ("stay", "2026-03-01")} and m.flags == ()
    # the closest of two rows wins: the same amount on the same day
    near = fresh("near", "2026-03-14", 900)
    m = run(
        [benefit(), stay], [near, fixed], decisions=[choice("typo", "link", "stay", guard=GUARD)]
    )
    assert where(m)["fixed"] == ("stay", "2026-03-01")


BAD_GUARDS = (
    {k: v for k, v in GUARD.items() if k != "row_sig"},
    {k: v for k, v in GUARD.items() if k != "near"},
    GUARD | {"near": ["typo"]},  # the list an earlier draft saved
    GUARD | {"near": {"typo": None}},
    GUARD | {"row_sig": None},
)


@pytest.mark.parametrize(
    ("rows", "decisions", "lost"),
    [
        # nothing within 7 days of the guard: a twin a month later is not it
        ([fresh("next-month", "2026-04-12")], [choice("typo", guard=GUARD)], ["typo"]),
        ([fresh("nine-days", "2026-03-21")], [choice("typo", guard=GUARD)], ["typo"]),
        # a tie is lost, never a guess
        (
            [fresh("twin-1", "2026-03-12"), fresh("twin-2", "2026-03-12")],
            [choice("typo", guard=GUARD)],
            ["typo"],
        ),
        # two choices on one row: both lost
        (
            [fresh("fixed", "2026-03-12")],
            [choice("typo-1", guard=GUARD), choice("typo-2", guard=GUARD)],
            ["typo-1", "typo-2"],
        ),
        # a row with a choice of its own is not a candidate
        (
            [fresh("fixed", "2026-03-12")],
            [choice("typo", guard=GUARD), choice("fixed", "not_benefit")],
            ["typo"],
        ),
        # only the guard's card
        (
            [fresh("fixed", "2026-03-12", card="cobalt-card")],
            [choice("typo", guard=GUARD)],
            ["typo"],
        ),
        # neither desc_norm nor merchant matches
        ([fresh("fixed", "2026-03-12", merchant="Cafe")], [choice("typo", guard=GUARD)], ["typo"]),
        # a guard with no date
        ([fresh("fixed", "2026-03-12")], [choice("typo", guard={"account_key": SUMMIT})], ["typo"]),
        # a guard that cannot prove anything: no row_sig, no well formed near map
        *(
            ([fresh("fixed", "2026-03-12")], [choice("typo", guard=guard)], ["typo"])
            for guard in BAD_GUARDS
        ),
    ],
)
def test_a_choice_that_cannot_find_its_row_is_lost(rows, decisions, lost) -> None:
    m = run([benefit()], rows, decisions=decisions)
    assert m.flags == tuple(rm.RowFlag("lost", k, decisions[0]["benefit_key"]) for k in lost)
    assert not m.pinned


def test_a_choice_on_a_live_row_that_is_no_longer_a_candidate_does_nothing() -> None:
    # hidden or made a transfer: live, with the guard's row_sig, but not a candidate
    m = run([benefit()], [], decisions=[choice("hidden", guard=GUARD)], live=("hidden",))
    assert m.flags == () and m.cycles == {}
    # it never jumps to a row like it
    m = run(
        [benefit()],
        [fresh("twin", "2026-03-12")],
        decisions=[choice("hidden", "link", "stay", guard=GUARD)],
        live=("hidden",),
    )
    assert where(m) == {"twin": ("dining", "2026-03-01")} and m.flags == ()


def test_a_key_that_holds_another_row_that_is_not_a_candidate_re_finds() -> None:
    """Codex D2 review: a replace shifts a same-day transfer with the same
    desc_norm onto the chosen row's key, and the typo fix gives the chosen row
    a fresh key. The key's row has another row_sig, so the choice re-finds,
    never sits there doing nothing."""
    stay = benefit("stay", "Stay credit", cents=5_000)
    transfer = {"typo": rm.Held(STMT, FIX, rm.row_sig("DINING CREDT XFER", "Dining Credit"))}
    pick = [choice("typo", benefit_key="stay", guard=GUARD)]
    m = run([benefit(), stay], [fresh("fixed", "2026-03-12")], decisions=pick, held=transfer)
    assert m.refound == {"fixed": "typo"} and where(m) == {"fixed": ("stay", "2026-03-01")}
    assert m.flags == ()
    # With nothing to re-find, it is lost, never quiet.
    m = run([benefit(), stay], [], decisions=pick, held=transfer)
    assert m.flags == (rm.RowFlag("lost", "typo", "stay"),)


def test_a_guard_that_proves_nothing_never_counts_its_live_row() -> None:
    """The key still holds a live candidate, but a guard with no row_sig or no
    well formed near map cannot say it is the same row: lost, and the word
    counts the row as if no choice were there."""
    stay = benefit("stay", "Stay credit", cents=5_000)
    row = crow("typo", "2026-03-12", description="DINING CREDT")
    good = run(
        [benefit(), stay],
        [row],
        {"dining": ("DINING",)},
        [choice("typo", "link", "stay", guard=GUARD)],
    )
    assert where(good) == {"typo": ("stay", "2026-03-01")} and good.flags == ()
    for guard in BAD_GUARDS:
        for d in (
            choice("typo", "link", "stay", guard=guard),
            choice("typo", "not_benefit", guard=guard),
        ):
            m = run([benefit(), stay], [row], {"dining": ("DINING",)}, [d])
            assert m.flags == (rm.RowFlag("lost", "typo", d["benefit_key"]),), guard
            assert where(m) == {"typo": ("dining", "2026-03-01")} and m.decided == frozenset()
        # live but not a candidate: lost too, never quiet
        m = run([benefit()], [], decisions=[choice("typo", guard=guard)], live=("typo",))
        assert m.flags == (rm.RowFlag("lost", "typo", "dining"),), guard


def test_the_refind_takes_only_rows_the_replace_wrote() -> None:
    """Only rows in the chosen key's statement written by its current run (the
    replace that dropped or rewrote the row), when that run comes after the
    guard's. A twin live all along, a row an earlier correction added, and a
    row from another statement are never it."""
    guard = GUARD | {"desc_norm": "DINING CREDIT", "run_id": "2026-09-12"}
    gone = choice("gone", "not_benefit", guard=guard)
    lost = (rm.RowFlag("lost", "gone", None),)
    twin = crow("twin", "2026-03-12", run="2026-09-12")  # live all along: the same run
    older = crow("older", "2026-03-12", run="2026-08-12")
    new = fresh("new", "2026-03-13")
    m = run([benefit()], [twin], decisions=[gone])
    assert m.flags == lost and where(m) == {"twin": ("dining", "2026-03-01")}
    assert run([benefit()], [older], decisions=[gone]).flags == lost
    m = run([benefit()], [twin, new], decisions=[gone])
    assert m.flags == () and m.refound == {"new": "gone"}
    assert where(m) == {"twin": ("dining", "2026-03-01")}  # new is not a benefit
    # A row an earlier correction added after the save: a later run than the
    # guard's, but not the replace that dropped the row.
    added = crow("added", "2026-03-13", run="2026-10-14")
    assert run([benefit()], [added], decisions=[gone]).flags == lost
    # The same run in another statement on the card.
    other = replace(new, statement_key=f"{SUMMIT}:2026-04-10")
    assert run([benefit()], [other], decisions=[gone]).flags == lost
    # Run order is (date, seq): -10 comes after -2.
    later = crow("later", "2026-03-12", run="2026-09-12-10")
    m = run(
        [benefit()],
        [later],
        decisions=[choice("gone", guard=guard | {"run_id": "2026-09-12-2"})],
        held={"gone": rm.Held(STMT, "2026-09-12-10")},
    )
    assert m.refound == {"later": "gone"}
    # No replace since the save, no current run, a bad run in the guard, or a row
    # with no run: lost.
    same = {"gone": rm.Held(STMT, "2026-09-12")}
    assert run([benefit()], [twin], decisions=[gone], held=same).flags == lost
    assert (
        run([benefit()], [new], decisions=[gone], held={"gone": rm.Held(STMT, None)}).flags == lost
    )
    m = run([benefit()], [new], decisions=[choice("gone", guard=guard | {"run_id": "soon"})])
    assert [f.kind for f in m.flags] == ["lost"]
    assert run([benefit()], [crow("no-run", "2026-03-12")], decisions=[gone]).flags == lost
    # A key load_rows knows nothing of (no row at all): lost.
    m = rm.match([benefit()], DINING_WORDS, [gone], rm.Rows((new,)), TODAY)
    assert m.flags == lost


def test_a_lost_choice_stays_lost_when_a_twin_is_decided() -> None:
    """Rows with a choice of their own are ranked too, so deciding one of two
    tied twins never hands the other to the lost choice."""
    guard = GUARD | {"desc_norm": "LANTERN CREDIT", "merchant": "Lantern", "amount_cents": 3_300}
    old = choice("old", "not_benefit", guard=guard | {"run_id": "2026-09-12"})
    lost = (rm.RowFlag("lost", "old", None),)

    def lantern(key: str, day: str = "2026-03-12") -> rm.CreditRow:
        return crow(key, day, 3_300, "LANTERN CREDIT", merchant="Lantern", run="2026-10-16")

    x, y = lantern("x"), lantern("y")
    m = run([benefit()], [x, y], decisions=[old])
    assert m.flags == lost and groups(m) == {"x": "review", "y": "review"}
    m = run([benefit()], [x, y], decisions=[old, choice("x")])  # a member counts x
    assert m.flags == lost  # y is no guess
    assert where(m) == {"x": ("dining", "2026-03-01")} and groups(m) == {"y": "review"}
    # A decided row that ranks best keeps the choice lost, even with a worse row free.
    near = lantern("near", "2026-03-15")
    m = run([benefit()], [x, near], decisions=[old, choice("x")])
    assert m.flags == lost and groups(m) == {"near": "review"}


ALPHA = "DINING CREDIT MONTHLY ADJUSTMENT ALPHA"
BRAVO = "DINING CREDIT MONTHLY ADJUSTMENT BRAVO"


def test_a_key_proves_its_row_only_with_the_same_signature() -> None:
    """Codex D2 finding 1. A statement replace that drops the first of two rows
    with the same date, amount and desc_norm hands its key to the second. The
    guard's row_sig (full description and merchant) tells them apart: the
    choice is lost, never inherited."""
    assert ALPHA[:32] == BRAVO[:32] and ALPHA != BRAVO
    stay = benefit("stay", "Stay credit", cents=5_000)
    alpha = crow("k1", "2026-03-20", description=ALPHA)
    pick = saved([choice("k1", benefit_key="stay")], [alpha])  # Different benefit
    m = run([benefit(), stay], [alpha], decisions=pick)
    assert where(m) == {"k1": ("stay", "2026-03-01")} and m.flags == ()
    lost = (rm.RowFlag("lost", "k1", "stay"),)
    # The replace drops alpha, and bravo takes its key: lost, and the word counts bravo.
    bravo = crow("k1", "2026-03-20", description=BRAVO)
    m = run([benefit(), stay], [bravo], decisions=pick)
    assert m.flags == lost and where(m) == {"k1": ("dining", "2026-03-01")}
    assert m.decided == frozenset()  # a lost choice moves nothing
    # A merchant change under the same key proves nothing either.
    m = run([benefit(), stay], [replace(alpha, imported_merchant="Lantern")], decisions=pick)
    assert m.flags == lost
    # True twins: the same description and merchant. The survivor keeps the choice.
    m = run([benefit(), stay], [crow("k1", "2026-03-20", description=ALPHA)], decisions=pick)
    assert where(m) == {"k1": ("stay", "2026-03-01")} and m.flags == ()
    # The edited merchant is not the imported one: it never matters.
    m = run([benefit(), stay], [replace(alpha, merchant="My cafe")], decisions=pick)
    assert m.flags == ()


def test_a_typo_fix_re_finds_even_when_a_twin_shifts_onto_the_key() -> None:
    """Codex D2 review. ALPHA and BRAVO share their first 32 characters, so
    they hold k1 and k2. A fix inside ALPHA's first 32 characters gives it a
    fresh key, and BRAVO shifts onto k1. k1 holds another row now, so the
    choice re-finds: BRAVO sits on a near key, and the fixed row is taken."""
    stay = benefit("stay", "Stay credit", cents=5_000)
    alpha = crow("k1", "2026-03-20", description=ALPHA, run="2026-09-12")
    bravo = crow("k2", "2026-03-20", description=BRAVO, run="2026-09-12")
    pick = saved([choice("k1", benefit_key="stay")], [alpha, bravo])
    fixed = ALPHA.replace("ADJUSTMENT", "ADJUSTMENX")
    assert fixed[:32] != ALPHA[:32]
    shifted = replace(bravo, txn_key="k1", run_id=FIX)
    m = run(
        [benefit(), stay], [shifted, fresh("k3", "2026-03-20", description=fixed)], decisions=pick
    )
    assert m.flags == () and m.refound == {"k3": "k1"}
    assert where(m) == {"k3": ("stay", "2026-03-01"), "k1": ("dining", "2026-03-01")}


def test_the_refind_skips_a_near_row_that_only_moved_keys() -> None:
    """Codex D2 review. X (a typo) and Y are live on the same day when X is
    decided. The fix gives X the same desc_norm as Y, and X comes first in the
    file: the fixed X takes Y's key and Y moves to a fresh one. Y was live when
    the choice was saved (its row_sig is in the near map), so the choice never
    moves to it. The fixed X sits on a near key: lost, never a guess."""
    stay = benefit("stay", "Stay credit", cents=5_000)
    typo = ALPHA.replace("DINING", "DINNG")
    x = crow("kx", "2026-03-20", description=typo, run="2026-09-12")
    y = crow("ky", "2026-03-20", description=BRAVO, run="2026-09-12")
    pick = saved([choice("kx", benefit_key="stay")], [x, y])
    moved_y = fresh("ky2", "2026-03-20", description=BRAVO)
    fixed_x = fresh("ky", "2026-03-20", description=ALPHA)
    m = run([benefit(), stay], [fixed_x, moved_y], decisions=pick)
    assert m.flags == (rm.RowFlag("lost", "kx", "stay"),) and m.refound == {}
    assert where(m) == {"ky": ("dining", "2026-03-01"), "ky2": ("dining", "2026-03-01")}
    # Y's own choice follows Y to its fresh key: its own row_sig is no bar.
    both = saved([choice("kx", benefit_key="stay"), choice("ky", "not_benefit")], [x, y])
    m = run([benefit(), stay], [fixed_x, moved_y], decisions=both)
    assert m.refound == {"ky2": "ky"} and m.flags == (rm.RowFlag("lost", "kx", "stay"),)
    assert where(m) == {"ky": ("dining", "2026-03-01")}
    # A true twin of the chosen row in the near map: the moved row cannot be told apart.
    twin = crow("kt", "2026-03-21", description=typo, run="2026-09-12")
    pick = saved([choice("kx", benefit_key="stay")], [x, twin])
    m = run([benefit(), stay], [fresh("kx2", "2026-03-20", description=typo)], decisions=pick)
    assert m.flags == (rm.RowFlag("lost", "kx", "stay"),)


def test_the_refind_skips_rows_live_when_the_choice_was_saved() -> None:
    """Codex D2 finding 2. A replace that drops the chosen credit and fixes a
    nearby credit's notes gives the nearby one a later run. It was live when
    the choice was saved (the guard's near list), so it is never the chosen
    row's correction. A fresh key from a fixed description still is."""
    stay = benefit("stay", "Stay credit", cents=5_000)
    old = crow("old", "2026-03-12", run="2026-09-12")
    nearby = crow("nearby", "2026-03-15", run="2026-09-12")
    pick = saved([choice("old", benefit_key="stay")], [old, nearby])
    assert sorted(json.loads(pick[0]["guard_json"])["near"]) == ["nearby", "old"]
    noted = replace(nearby, run_id="2026-10-16")  # a notes fix: a later run wrote it
    m = run([benefit(), stay], [noted], decisions=pick)
    assert m.flags == (rm.RowFlag("lost", "old", "stay"),) and m.refound == {}
    assert where(m) == {"nearby": ("dining", "2026-03-01")}
    # The fixed description is a fresh key, not in the list: the choice follows it.
    fixed = crow("fixed", "2026-03-12", description="DINING CREDIT MAR", run="2026-10-16")
    m = run([benefit(), stay], [noted, fixed], decisions=pick)
    assert m.flags == () and m.refound == {"fixed": "old"}
    assert where(m) == {"fixed": ("stay", "2026-03-01"), "nearby": ("dining", "2026-03-01")}


def test_a_choice_that_moves_its_row_decides_where_it_was() -> None:
    """Codex D2 finding 3. A choice that puts its row anywhere but its natural
    place (the one word winner's home cycle, a link's own home cycle) decides
    that place, so the overlay reads it from rows, never the chat's used."""
    card = "orchard-card"
    wireless = benefit("wireless", "Wireless credit", card=card, cents=500)
    phone = benefit("phone", "Phone credit", card=card, cents=1_000)
    words = {"wireless": ("WIRELESS CREDIT",)}

    def row(key: str, day: str) -> rm.CreditRow:
        return crow(key, day, 500, "WIRELESS CREDIT", card, "Wireless Credit")

    rows = [row("jan", "2026-01-05"), row("feb", "2026-02-05")]
    jan, feb = ("wireless", "2026-01-01"), ("wireless", "2026-02-01")
    for terms, d, placed, decided in [
        # Count in January instead: February is decided, with words or without.
        (words, choice("feb", benefit_key="wireless", cycle_start=jan[1]), jan, {feb}),
        ({}, choice("feb", benefit_key="wireless", cycle_start=jan[1]), jan, {feb}),
        # Another benefit, Not a benefit, a credit that takes no rows: the word's cycle.
        (words, choice("feb", benefit_key="phone"), ("phone", "2026-02-01"), {feb}),
        (words, choice("feb", "not_benefit"), None, {feb}),
        (words, choice("feb", benefit_key="not-shown"), None, {feb}),
        # Where the row goes anyway: nothing is decided.
        (words, choice("feb", benefit_key="wireless"), feb, set()),
        (words, choice("feb", benefit_key="wireless", cycle_start=feb[1]), feb, set()),
        ({}, choice("feb", benefit_key="phone"), ("phone", "2026-02-01"), set()),
        ({}, choice("feb", "not_benefit"), None, set()),
    ]:
        m = run([wireless, phone], rows, terms, [d])
        assert where(m).get("feb") == placed, d
        assert m.decided == decided, d


def test_a_spill_that_moves_a_row_out_decides_the_cycle() -> None:
    """The same rule for a spill: a row moved back a cycle leaves a cycle that
    reads from rows, even when none are left."""
    amounts = (
        rm.Cycle("2026-01-01", "2026-01-31", 1_000),
        rm.Cycle("2026-02-01", "2026-02-28", 500),
    )
    m = run([benefit(cycles=amounts)], [crow("early", "2026-02-03")])  # over February's $5
    assert where(m) == {"early": ("dining", "2026-01-01")}
    assert m.cycles.get(("dining", "2026-02-01")) is None
    assert m.decided == {("dining", "2026-02-01")}
    assert run([benefit(cycles=amounts)], [crow("late", "2026-02-12", 500)]).decided == set()


def test_a_choice_that_stops_a_spill_decides_the_cycle_it_filled() -> None:
    """Codex D2 review. Two $5 wireless rows early in February: the first
    spills back to January, a late post. A choice that moves the second (or
    the first) leaves February under its amount, so nothing spills and
    January goes empty. The words alone fill January, so it is decided: the
    chat's January used would count the late post a second time."""
    card = "orchard-card"
    wireless = benefit("wireless", "Wireless credit", card=card, cents=500)
    phone = benefit("phone", "Phone credit", card=card, cents=1_000)
    words = {"wireless": ("WIRELESS CREDIT",)}

    def row(key: str, day: str) -> rm.CreditRow:
        return crow(key, day, 500, "WIRELESS CREDIT", card, "Wireless Credit")

    rows = [row("f1", "2026-02-02"), row("f2", "2026-02-05")]
    jan, feb = ("wireless", "2026-01-01"), ("wireless", "2026-02-01")
    m = run([wireless, phone], rows, words)
    assert where(m) == {"f1": jan, "f2": feb} and m.decided == {feb}
    for d in (
        choice("f2", benefit_key="phone"),
        choice("f2", "not_benefit"),
        choice("f1", benefit_key="phone"),
        choice("f1", "not_benefit"),
    ):
        m = run([wireless, phone], rows, words, [d])
        assert m.cycles.get(jan) is None and jan in m.decided, d
    # A choice that keeps the spill decides nothing new.
    m = run([wireless, phone], rows, words, [choice("f2", benefit_key="wireless")])
    assert where(m) == {"f1": jan, "f2": feb} and m.decided == {feb}


def test_a_choice_that_settles_a_tie_decides_every_tied_cycle() -> None:
    """Codex D2 review. A row whose words tie waits for review, and the chat
    may have counted it for either benefit. Linking it to one decides the
    other's cycle; Not a benefit decides both."""
    card = "orchard-card"
    x = benefit("x", "Alpha credit", card=card, cents=500)
    y = benefit("y", "Bravo credit", card=card, cents=500)
    words = {"x": ("ALPHA",), "y": ("BRAVO",)}
    rows = [crow("r", "2026-03-05", 500, "ALPHA BRAVO CREDIT", card, "Alpha Bravo")]
    m = run([x, y], rows, words)
    assert m.review == (rm.ReviewItem("r", "review", ("x", "y")),) and m.decided == set()
    m = run([x, y], rows, words, [choice("r", benefit_key="y")])
    assert where(m) == {"r": ("y", "2026-03-01")} and m.decided == {("x", "2026-03-01")}
    m = run([x, y], rows, words, [choice("r", "not_benefit")])
    assert where(m) == {} and m.decided == {("x", "2026-03-01"), ("y", "2026-03-01")}


# --------------------------------------------------------------------------
# Pure matching: the rows to review
# --------------------------------------------------------------------------


def test_unmatched_rows_go_to_returns_trips_or_review() -> None:
    towne = {"description": "TOWNE OUTFITTERS RETURN", "merchant": "Towne Outfitters"}
    rows = [
        crow("return", "2026-06-29", 4_500, **towne),
        crow("not-exact", "2026-06-29", 4_499, **towne),
        crow("too-old", "2026-09-30", 4_500, **towne),  # the purchase is 102 days before
        crow("trip", "2026-09-09", 1_005, "POINTS FOR TRAVEL", trip_key="day-trip-sep26"),
        crow("other", "2026-08-18", 2_500, "STATEMENT CREDIT", merchant="Statement Credit"),
        crow("last-year", "2025-12-20", 2_500, "STATEMENT CREDIT"),
        crow("no-credits", "2026-08-18", 2_500, "STATEMENT CREDIT", card="bramble-store-card"),
    ]
    purchases = [
        rm.Purchase(SUMMIT, "2026-06-20", 4_500, "Towne Outfitters"),
        rm.Purchase("cobalt-card", "2026-06-20", 4_499, "Towne Outfitters"),  # another card
        rm.Purchase(SUMMIT, "2026-06-25", 4_499, "Other Store"),  # another merchant
    ]
    m = run([benefit()], rows, purchases=purchases)
    assert groups(m) == {
        "return": "returns",
        "not-exact": "review",
        "trip": "trips",
        "other": "review",
        "too-old": "review",
    }
    assert [x.txn_key for x in m.review] == ["not-exact", "return", "other", "trip", "too-old"]


def test_the_best_guess() -> None:
    dining = benefit()
    travel = rm.Benefit(
        TRAVEL,
        SUMMIT,
        "Yearly travel credit",
        None,
        (rm.Cycle("2026-01-01", "2026-12-31", 10_000),),
    )
    airline = rm.Benefit(
        AIRLINE,
        SUMMIT,
        "Airline fee credit",
        None,
        (rm.Cycle("2026-01-01", "2026-06-30", 5_000), rm.Cycle("2026-07-01", "2026-12-31", 5_000)),
    )
    benefits = [dining, travel, airline]
    used = {("dining", "2026-08-01"): 1_000}
    row = crow("x", "2026-08-18", 2_500, "STATEMENT CREDIT")
    # Room of at least the amount on two, no name words in common: the first key.
    assert rm.best_guess(row, benefits, used) == (AIRLINE, "2026-07-01")
    # Room equal to the amount wins.
    assert rm.best_guess(row, benefits, used | {(TRAVEL, "2026-01-01"): 7_500}) == (
        TRAVEL,
        "2026-01-01",
    )
    # Then the most name words in common. Generic and cadence words do not count.
    yearly = crow("y", "2026-08-18", 2_500, "YEARLY TRAVEL CREDIT")
    assert rm.best_guess(yearly, benefits, used) == (TRAVEL, "2026-01-01")
    assert rm.name_words("Yearly travel credit") == {"TRAVEL"}
    # An ambiguous row picks among the benefits its words tie on.
    assert rm.best_guess(row, benefits, used, options=("dining",)) == ("dining", "2026-08-01")
    # No benefit on the card, or no cycle at the date: no guess.
    assert rm.best_guess(crow("z", "2026-08-18", card="cobalt-card"), benefits, used) is None
    assert rm.best_guess(row, [benefit(cycles=months([1]))], used) is None
    # match_off benefits are never a guess.
    assert rm.best_guess(row, [benefit(matches=False)], used) is None


def test_matching_is_deterministic() -> None:
    rows = [
        crow("a", "2026-03-03"),
        crow("b", "2026-03-12"),
        crow("c", "2026-04-02", 600),
        crow("d", "2026-08-18", 2_500, "STATEMENT CREDIT"),
        crow("e", "2026-03-12", description="DUNKIN DINING"),
    ]
    terms = {"dining": ("DINING CREDIT",), "dunkin": ("DUNKIN",), "other": ("DINING",)}
    benefits = [benefit(), benefit("dunkin", "Dunkin credit"), benefit("other", "Other")]
    decisions = [choice("c", cycle_start="2026-03-01"), choice("gone", guard=GUARD)]
    want = run(benefits, rows, terms, decisions)
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert run(benefits[::-1], shuffled, terms, decisions[::-1]) == want


def test_match_words_are_checked() -> None:
    assert rm.check_term("  Dining   credit ") == "Dining credit"
    assert rm.check_term("CLEAR") == "CLEAR"
    generic = ["STATEMENT CREDIT", "credit", "Card Credits!", "CASH BACK REWARD", "---"]
    # Matching is by substring, so a piece of a generic word claims every credit line.
    generic += ["EDIT", "CRED", "REDIT", "POINT", "STATEMEN", "REFUN", "ADJ", "T CREDIT"]
    generic += ["STATEMENT CR", "nt cred", "T-CREDIT", "S BACK"]
    for term in generic:
        with pytest.raises(EditError) as e:
            rm.check_term(term)
        assert (e.value.status, e.value.message) == (422, rm.TERM_GENERIC), term
    # Words that cannot sit inside a line made only of generic words.
    for term in ("RED CARD", "AIR CREDIT", "CREDIT ONE", "DINING CREDIT", "TRAVEL CR STATEMENT"):
        assert rm.check_term(term) == term
    for bad in ("ab", "x" * 41, "DINING\tCREDIT", "CAFÉ CREDIT", "DUNKIN\x00"):
        with pytest.raises(EditError) as e:
            rm.check_term(bad)
        assert (e.value.status, e.value.message) == (422, rm.TERM_FORMAT)
        assert bad not in e.value.message


# --------------------------------------------------------------------------
# On the samples: helpers
# --------------------------------------------------------------------------


def insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def q(settings: Settings, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with read_connection(settings.db_path) as conn:
        return conn.execute(sql, params).fetchall()


def effective(settings: Settings) -> ov.Effective:
    with read_connection(settings.db_path) as conn:
        eff = ov.effective_rewards(conn, readers.monthly_runs(conn))
    assert eff is not None
    return eff


def page_cycles(settings: Settings, key: str) -> dict[str, dict]:
    with read_connection(settings.db_path) as conn:
        page = rw.rewards_page(conn)
    b = next(b for b in page["benefits"] if b["key"] == key)
    return {c["start"]: c for c in [*b["past_cycles"], *([b["cycle"]] if b["cycle"] else [])]}


def add_words(settings: Settings, words: dict[str, list[str]]) -> None:
    with write_connection(settings.db_path) as w:
        for key, terms in words.items():
            for term in terms:
                insert(
                    w, "reward_match_terms", {"benefit_key": key, "term": term, "updated_at": NOW}
                )


def edit_row(settings: Settings, key: str, card: str, **cols: int) -> None:
    row = {"benefit_key": key, "origin": "chat", "account_key": card, "updated_at": NOW}
    with write_connection(settings.db_path) as w:
        insert(w, "reward_benefit_edits", row | cols)


def mark(settings: Settings, key: str, start: str, cents: int, used_on: str | None = None) -> None:
    row = {"benefit_key": key, "cycle_start": start, "used_cents": cents, "used_on": used_on}
    with write_connection(settings.db_path) as w:
        insert(w, "reward_cycle_marks", row | {"updated_at": NOW})


def key_of(settings: Settings, description: str, day: str, card: str = SUMMIT) -> str:
    rows = q(
        settings,
        """SELECT txn_key FROM v_transactions
           WHERE account_key = ? AND description = ? AND txn_date = ? AND amount_cents > 0""",
        (card, description, day),
    )
    assert len(rows) == 1, (description, day)
    return rows[0][0]


def decide(
    settings: Settings,
    txn_key: str,
    decision: str,
    benefit_key: str | None = None,
    cycle_start: str | None = None,
    reason: str | None = None,
) -> None:
    """A credit choice, with its guard from the live row (like the route)."""
    with write_connection(settings.db_path) as w:
        guard = ri.choice_guard(w, txn_key)
        assert guard is not None
        row = {
            "txn_key": txn_key,
            "decision": decision,
            "reason": reason,
            "benefit_key": benefit_key,
            "cycle_start": cycle_start,
            "guard_json": json.dumps(guard, sort_keys=True),
            "updated_at": NOW,
        }
        insert(w, "reward_credit_decisions", row)


_ROW_IDS = itertools.count()


def add_row(
    settings: Settings, card: str, day: str, cents: int, description: str, merchant: str, **cols
) -> str:
    """A test-local statement row. Returns its txn_key."""
    key = hashlib.sha256(f"test-row-{next(_ROW_IDS)}".encode()).hexdigest()
    hidden = cols.pop("hidden", False)
    row = {
        "txn_key": key,
        "statement_key": f"{card}:test",
        "account_key": card,
        "txn_date": day,
        "amount_cents": cents,
        "description": description,
        "desc_norm": description[:32],
        "merchant": merchant,
        "category_key": "dining",
        "txn_type": "refund",
        "occurrence_n": 1,
        "run_id": "2026-10-12",
    } | cols
    with write_connection(settings.db_path) as w:
        insert(w, "transactions", row)
        if hidden:
            insert(w, "transaction_overrides", {"txn_key": key, "hidden": 1, "updated_at": NOW})
    return key


def review_groups(eff: ov.Effective) -> dict[str, str]:
    return {x.txn_key: x.group for x in eff.meta.review}


# --------------------------------------------------------------------------
# On the samples: cycle use (plan 3.3)
# --------------------------------------------------------------------------


def test_summit_dining_cycles_read_from_your_statements(
    samples: Settings, admin: TestClient
) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    eff = effective(samples)
    for m in range(1, 9):
        at = (DINING, f"2026-{m:02d}-01")
        assert eff.meta.cycle_sources[at] == "rows"
        [key] = eff.meta.cycle_rows[at]
        assert eff.meta.credit_rows[key].txn_date == f"2026-{m:02d}-12"
    dining = next(b for b in eff.rewards.benefits if b.key == DINING)
    used = {c.start: (c.used, c.used_on) for c in dining.cycles}
    assert used["2026-01-01"] == ("10.00", "2026-01-12")
    assert used["2026-08-01"] == ("10.00", "2026-08-12")  # the chat's $10 and a row's $10: $10

    r = admin.get("/api/rewards")
    assert r.status_code == 200
    b = next(b for b in r.json()["benefits"] if b["key"] == DINING)
    cycles = {c["start"]: c for c in [*b["past_cycles"], b["cycle"]]}
    aug = cycles["2026-08-01"]
    assert {k: aug[k] for k in ("used_cents", "state", "left_cents", "used_on")} == {
        "used_cents": 1_000,
        "state": "done",
        "left_cents": 0,
        "used_on": "2026-08-12",
    }
    assert (aug["source"], aug["chat_used_cents"], aug["covered"], aug["over_cents"]) == (
        "rows",
        1_000,
        True,
        0,
    )
    assert aug["rows"] == [
        {
            "txn_key": key_of(samples, "DINING CREDIT", "2026-08-12"),
            "date": "2026-08-12",
            "merchant": "Dining Credit",
            "description": "DINING CREDIT",
            "cents": 1_000,
            "earlier_start": None,
        }
    ]
    # September: the chat's $6, and summit's rows stop at 2026-09-10.
    sep = cycles["2026-09-01"]
    assert (sep["source"], sep["used_cents"], sep["covered"], sep["rows"]) == (
        "chat",
        600,
        False,
        [],
    )
    # October: nothing yet, and not covered: "Waiting for rows", never "Not used".
    october = cycles["2026-10-01"]
    assert (october["source"], october["used_cents"], october["covered"]) == (None, 0, False)
    # STATEMENT CREDIT waits for review. The TOWNE return sits in "Looks like returns".
    found = review_groups(eff)
    assert found[key_of(samples, "STATEMENT CREDIT", "2026-08-18")] == "review"
    assert found[key_of(samples, "TOWNE OUTFITTERS RETURN", "2026-06-29")] == "returns"
    assert not {eff.meta.credit_rows[k].description for k in found} & {"DINING CREDIT"}


def test_a_smaller_row_wins_and_the_chats_number_stays(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    decide(samples, key_of(samples, "DINING CREDIT", "2026-03-12"), "not_benefit", reason="other")
    six = add_row(samples, SUMMIT, "2026-03-20", 600, "DINING CREDIT", "Dining Credit")
    march = page_cycles(samples, DINING)["2026-03-01"]
    assert (march["source"], march["used_cents"], march["used_on"]) == ("rows", 600, "2026-03-20")
    assert march["chat_used_cents"] == 1_000  # "The chat says $10"
    assert march["state"] == "expired" and [r["txn_key"] for r in march["rows"]] == [six]


def test_the_covered_chat_only_flag(samples: Settings) -> None:
    add_words(samples, {AIRLINE: ["AIRLINE FEE"], "streaming-credit": ["STREAMING CREDIT"]})
    eff = effective(samples)
    # Summit's rows cover January to June plus 7 days; the chat says $50 used, no row matches.
    assert eff.meta.chat_only == [(AIRLINE, "2026-01-01")]
    assert eff.meta.cycle_sources[(AIRLINE, "2026-01-01")] == "chat"
    airline = next(b for b in eff.rewards.benefits if b.key == AIRLINE)
    assert airline.cycles[0].used == "50.00"  # the chat's value stands
    # Cobalt's rows start in May: the chat-only February use is not covered, so no flag.
    assert ("streaming-credit", "2026-01-01") not in eff.meta.covered
    assert eff.meta.cycle_sources[("streaming-credit", "2026-01-01")] == "chat"
    assert eff.meta.cycle_sources[("streaming-credit", "2026-04-01")] == "rows"
    # "This credit never shows on statements": no flag.
    edit_row(samples, AIRLINE, SUMMIT, match_off=1)
    eff = effective(samples)
    assert eff.meta.chat_only == [] and (AIRLINE, "2026-01-01") in eff.meta.covered


def test_no_words_reads_as_the_chat_sent_it(samples: Settings) -> None:
    eff = effective(samples)
    assert eff.rewards == eff.chat
    assert eff.meta.cycle_rows == {} and eff.meta.chat_only == [] and eff.meta.flags == []
    # every chat credit row waits for review, the TOWNE return as a return
    found = review_groups(eff)
    descriptions = {eff.meta.credit_rows[k].description for k, g in found.items() if g == "review"}
    assert descriptions == {
        "DINING CREDIT",
        "STREAMING CREDIT",
        "WIRELESS CREDIT",
        "STATEMENT CREDIT",
    }
    assert len(found) == 21
    # Each one has a best guess. Room leaves out the chat's used, which counts
    # this very row: August's dining credit has room for it.
    dining_row = key_of(samples, "DINING CREDIT", "2026-08-12")
    guess = next(x.guess for x in eff.meta.review if x.txn_key == dining_row)
    assert guess == (DINING, "2026-08-01")


def test_rows_that_never_count(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT", "CEDAR CAFE"]})
    day = "2026-10-12"
    never = {
        "negative": add_row(samples, SUMMIT, day, -1_000, "DINING CREDIT", "Dining Credit"),
        "purchase": add_row(
            samples, SUMMIT, day, 1_000, "DINING CREDIT", "Dining Credit", txn_type="purchase"
        ),
        "transfer": add_row(
            samples, SUMMIT, day, 1_000, "DINING CREDIT", "Dining Credit", is_transfer=1
        ),
        "pre_history": add_row(
            samples, SUMMIT, day, 1_000, "DINING CREDIT", "Dining Credit", pre_history=1
        ),
        "removed": add_row(
            samples, SUMMIT, day, 1_000, "DINING CREDIT", "Dining Credit", removed_at=NOW
        ),
        "hidden": add_row(
            samples, SUMMIT, day, 1_000, "DINING CREDIT", "Dining Credit", hidden=True
        ),
    }
    other_card = add_row(samples, "cobalt-card", day, 1_000, "DINING CREDIT", "Dining Credit")
    eff = effective(samples)
    assert (DINING, "2026-10-01") not in eff.meta.cycle_rows
    counted = {k for keys in eff.meta.cycle_rows.values() for k in keys}
    assert not counted & {*never.values(), other_card}
    assert not set(never.values()) & set(eff.meta.credit_rows)
    assert review_groups(eff)[other_card] == "review"  # cobalt's own credits may claim it
    # The CEDAR CAFE purchases a credit offsets never match its word.
    assert [
        eff.meta.credit_rows[k].description for k in eff.meta.cycle_rows[(DINING, "2026-01-01")]
    ] == ["DINING CREDIT"]


def test_partials_sum_to_done(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    four = add_row(samples, SUMMIT, "2026-10-09", 400, "DINING CREDIT", "Dining Credit")
    six = add_row(samples, SUMMIT, "2026-10-14", 600, "DINING CREDIT", "Dining Credit")
    october = page_cycles(samples, DINING)["2026-10-01"]
    assert (october["state"], october["used_cents"], october["left_cents"]) == ("done", 1_000, 0)
    assert (october["source"], october["over_cents"], october["used_on"]) == (
        "rows",
        0,
        "2026-10-14",
    )
    assert [r["txn_key"] for r in october["rows"]] == [four, six]
    with read_connection(samples.db_path) as conn:
        assert rw.credits_ending(conn) is None  # dining was the one credit ending soon


def test_twins_go_over(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    twins = [
        add_row(samples, SUMMIT, "2026-10-10", 1_000, "DINING CREDIT", "Dining Credit")
        for _ in range(2)
    ]
    october = page_cycles(samples, DINING)["2026-10-01"]
    assert (october["state"], october["used_cents"], october["left_cents"]) == ("done", 2_000, 0)
    assert october["over_cents"] == 1_000  # "More than the credit by $10"
    assert sorted(r["txn_key"] for r in october["rows"]) == sorted(twins)


def test_returns_need_the_exact_amount_and_trip_rows_group(samples: Settings) -> None:
    near = add_row(
        samples, SUMMIT, "2026-06-30", 4_499, "TOWNE OUTFITTERS RETURN", "Towne Outfitters"
    )
    trip = add_row(
        samples,
        SUMMIT,
        "2026-09-09",
        1_005,
        "POINTS FOR TRAVEL",
        "Summit Travel",
        trip_key="day-trip-sep26",
    )
    eff = effective(samples)
    found = review_groups(eff)
    assert found[key_of(samples, "TOWNE OUTFITTERS RETURN", "2026-06-29")] == "returns"
    assert found[near] == "review" and found[trip] == "trips"
    assert all(x.guess is not None for x in eff.meta.review)  # each has "Count it"


def test_hidden_benefits_still_claim_rows(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    edit_row(samples, DINING, SUMMIT, hidden=1)
    # October's dining credit has room for exactly this row, but it is hidden.
    near = add_row(samples, SUMMIT, "2026-10-12", 1_000, "DINING CRED", "Dining Credit")
    eff = effective(samples)
    assert {x.txn_key: x.guess for x in eff.meta.review}[near] == (AIRLINE, "2026-07-01")
    assert DINING not in {b.key for b in eff.rewards.benefits}
    assert [b.key for b in eff.meta.hidden] == [DINING]
    assert eff.meta.cycle_sources[(DINING, "2026-08-01")] == "rows"
    rows = {eff.meta.credit_rows[x.txn_key].description for x in eff.meta.review}
    assert "DINING CREDIT" not in rows  # never flagged
    assert all(x.guess is None or x.guess[0] != DINING for x in eff.meta.review)  # never a guess


def test_one_source_per_cycle(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    mark(samples, DINING, "2026-08-01", 0)  # Mark not used, over a row and the chat's $10
    mark(samples, DINING, "2026-10-01", 1_000, "2026-10-05")  # Mark used
    mark(samples, DINING, "2025-12-01", 1_000)  # a cycle the chat does not send
    cycles = page_cycles(samples, DINING)
    aug = cycles["2026-08-01"]
    assert (aug["source"], aug["used_cents"], aug["used_on"], aug["state"]) == (
        "set",
        0,
        None,
        "expired",
    )
    assert aug["chat_used_cents"] == 1_000 and len(aug["rows"]) == 1  # the row still shows
    october = cycles["2026-10-01"]
    assert (october["source"], october["used_cents"], october["used_on"]) == (
        "set",
        1_000,
        "2026-10-05",
    )
    assert "2025-12-01" not in cycles
    assert q(samples, "SELECT count(*) FROM reward_cycle_marks")[0][0] == 3  # kept, ignored
    with read_connection(samples.db_path) as conn:
        assert rw.credits_ending(conn) is None  # October is used now
    # A mark works on a benefit with no words too.
    mark(samples, WIRELESS, "2026-10-01", 500, "2026-10-05")
    assert page_cycles(samples, WIRELESS)["2026-10-01"]["source"] == "set"


def test_card_spend_totals_do_not_change(samples: Settings) -> None:
    cards = ("summit-card", "cobalt-card", "orchard-card")

    def spend() -> tuple:
        with read_connection(samples.db_path) as conn:
            page = rw.rewards_page(conn)
            return (
                [rw.card_spend(conn, card, 2026) for card in cards],
                page["milestones"],
                page["caps"],
                rw.checks(conn)["gaps"],
            )

    before = spend()
    add_words(
        samples,
        {DINING: ["DINING CREDIT"], "streaming-credit": ["STREAMING"], WIRELESS: ["WIRELESS"]},
    )
    decide(samples, key_of(samples, "STATEMENT CREDIT", "2026-08-18"), "link", AIRLINE)
    mark(samples, DINING, "2026-09-01", 1_000)
    assert spend() == before


# --------------------------------------------------------------------------
# On the samples: choices
# --------------------------------------------------------------------------


def test_count_it_not_a_benefit_and_a_pinned_cycle(samples: Settings) -> None:
    add_words(samples, {WIRELESS: ["WIRELESS CREDIT"]})
    statement = key_of(samples, "STATEMENT CREDIT", "2026-08-18")
    towne = key_of(samples, "TOWNE OUTFITTERS RETURN", "2026-06-29")
    jan = key_of(samples, "WIRELESS CREDIT", "2026-01-05", "orchard-card")
    feb = key_of(samples, "WIRELESS CREDIT", "2026-02-05", "orchard-card")
    eff = effective(samples)
    assert {x.txn_key: x.guess for x in eff.meta.review}[statement] == (AIRLINE, "2026-07-01")
    assert eff.meta.earlier[feb] == "2026-01-01"  # "Count in January instead"

    decide(samples, statement, "link", AIRLINE)  # Count it
    decide(samples, towne, "not_benefit", reason="return")  # Not a benefit
    decide(samples, feb, "link", WIRELESS, cycle_start="2026-01-01")  # Count in January instead
    eff = effective(samples)
    assert not {statement, towne} & set(review_groups(eff))
    assert eff.meta.flags == []
    assert eff.meta.cycle_rows[(AIRLINE, "2026-07-01")] == (statement,)
    airline = page_cycles(samples, AIRLINE)["2026-07-01"]
    assert (airline["source"], airline["used_cents"], airline["state"]) == (
        "rows",
        2_500,
        "partial",
    )
    # February's row now counts for January, which goes over. February is decided:
    # 0 from rows, never the chat's $5, which counts that same row.
    assert eff.meta.cycle_rows[(WIRELESS, "2026-01-01")] == (jan, feb)
    assert eff.meta.over[(WIRELESS, "2026-01-01")] == 500
    assert eff.meta.cycle_sources[(WIRELESS, "2026-02-01")] == "rows"
    assert feb not in eff.meta.earlier  # a pinned row offers nothing


def summit_correction(
    tmp_path: Path,
    run_id: str,
    source_run: str,
    period_end: str,
    edit: Callable[[list[dict]], None],
    fix: Callable[[dict], None] | None = None,
) -> Path:
    """A correction run with one summit statement from a sample run, its rows
    changed by `edit` and its totals by `fix`."""
    run = tmp_path / run_id
    shutil.copytree(SAMPLES / "2026-10-14", run)
    for old in (run / "transactions").iterdir():
        old.unlink()
    name = f"transactions/summit-card-{period_end}.csv"
    shutil.copy(SAMPLES / source_run / name, run / name)
    src = read_json(SAMPLES / source_run / "snapshot.json")
    snap = read_json(run / "snapshot.json")
    snap["run_id"] = run_id
    snap["statements"] = [
        s for s in src["statements"] if (s["account_key"], s["period_end"]) == (SUMMIT, period_end)
    ]
    snap["balances"] = [
        b for b in src["balances"] if (b["account_key"], b["as_of"]) == (SUMMIT, period_end)
    ]
    if fix is not None:
        fix(snap["statements"][0])
    write_json(run / "snapshot.json", snap)
    manifest = read_json(run / "manifest.json")
    manifest["run_id"] = run_id
    manifest["files"] = [f for f in manifest["files"] if f["path"] == "snapshot.json"]
    manifest["files"].append({"path": name, "bytes": 0, "rows": 0, "sha256": ""})
    write_json(run / "manifest.json", manifest)
    edit_csv(run, name, edit)  # refreshes the manifest
    return run


def _describe(old: str, new: str) -> Callable[[list[dict]], None]:
    def edit(rows: list[dict]) -> None:
        for r in rows:
            if r["description"] == old:
                r["description"] = new

    return edit


def test_a_choice_follows_its_row_through_a_correction(samples: Settings, tmp_path: Path) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    typo = summit_correction(
        tmp_path,
        "2026-10-16",
        "2026-10-12",
        "2026-09-10",
        _describe("DINING CREDIT", "DINING CREDT"),
    )
    assert import_folder(typo, settings=samples, today=TODAY).status == "imported"
    old = key_of(samples, "DINING CREDT", "2026-08-12")
    assert review_groups(effective(samples))[old] == "review"  # the typo matches no word
    decide(samples, old, "link", TRAVEL)  # Different benefit: the travel credit

    # The fix is a new row (a later run wrote it), so the choice follows it.
    fix = _describe("DINING CREDIT", "DINING CREDIT AUG")
    fixed = summit_correction(tmp_path, "2026-10-18", "2026-10-12", "2026-09-10", fix)
    assert import_folder(fixed, settings=samples, today=TODAY).status == "imported"
    new = key_of(samples, "DINING CREDIT AUG", "2026-08-12")
    assert (
        new != old
        and q(samples, "SELECT removed_at FROM transactions WHERE txn_key = ?", (old,))[0][0]
    )
    eff = effective(samples)
    assert eff.meta.cycle_rows[(TRAVEL, "2026-01-01")] == (new,)  # the choice followed its row
    assert (DINING, "2026-08-01") not in eff.meta.cycle_rows  # the word did not take it back
    assert eff.meta.flags == [] and new not in review_groups(eff)
    # Found at read time: the stored choice is unchanged.
    assert [r[0] for r in q(samples, "SELECT txn_key FROM reward_credit_decisions")] == [old]


def test_a_deleted_row_with_a_twin_a_month_later_is_lost(samples: Settings, tmp_path: Path) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    july = key_of(samples, "DINING CREDIT", "2026-07-12")
    decide(samples, july, "link", TRAVEL)

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
    result = import_folder(run_dir, settings=samples, today=TODAY)
    assert result.status == "imported", result.issues
    eff = effective(samples)
    assert eff.meta.flags == [rm.RowFlag("lost", july, TRAVEL)]  # "A credit choice lost its row"
    assert (TRAVEL, "2026-01-01") not in eff.meta.cycle_rows  # no jump to the twin
    august = key_of(samples, "DINING CREDIT", "2026-08-12")
    assert eff.meta.cycle_rows[(DINING, "2026-08-01")] == (august,)
    assert eff.meta.cycle_sources[(DINING, "2026-07-01")] == "chat"


def test_reapply_leaves_choices_as_they_were(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    decide(samples, key_of(samples, "STATEMENT CREDIT", "2026-08-18"), "link", AIRLINE)
    towne = key_of(samples, "TOWNE OUTFITTERS RETURN", "2026-06-29")
    decide(samples, towne, "not_benefit", reason="return")
    mark(samples, DINING, "2026-10-01", 1_000, "2026-10-05")

    def state() -> tuple:
        eff = effective(samples)
        tables = {
            t: [tuple(r) for r in q(samples, f"SELECT * FROM {t} ORDER BY 1, 2")]
            for t in REWARD_TABLES
        }
        return tables, eff.rewards, eff.meta.cycle_rows, eff.meta.cycle_sources, eff.meta.review

    before = state()
    reapply(samples)
    assert state() == before


# --------------------------------------------------------------------------
# On the samples: statement replaces and moved rows (Codex review of D2)
# --------------------------------------------------------------------------


def summit_rows(settings: Settings) -> list[CsvRow]:
    """Summit's 2026-09-10 statement rows, as the 2026-10-12 run sent them."""
    with read_connection(settings.db_path) as conn:
        return list(load_stored_run(conn, "2026-10-12").rows[statement_key(SUMMIT, END)])


def replace_summit(settings: Settings, run_id: str, rows: list[CsvRow]) -> None:
    """A statement replace through apply_rows, as Codex reproduced it: the run
    becomes the statement's current version, as apply_run makes it, and the
    rows change. No totals, run sections or rewards."""
    skey = statement_key(SUMMIT, END)
    with write_connection(settings.db_path) as w:
        snapshot = load_stored_run(w, "2026-10-12").snapshot
        [st] = [s for s in snapshot.statements if (s.account_key, s.period_end) == (SUMMIT, END)]
        w.execute(
            """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at)
               VALUES (?, ?, 1, 'correction', 'imported', ?)""",
            (run_id, run_id, NOW),
        )
        [current] = w.execute(
            "SELECT * FROM statements WHERE statement_key = ? AND is_current = 1", (skey,)
        ).fetchall()
        w.execute("UPDATE statements SET is_current = 0 WHERE statement_key = ?", (skey,))
        insert(w, "statements", dict(current) | {"run_id": run_id})
        apply_rows(w, st, skey, rows, run_id, NOW)


def current_run(settings: Settings) -> str:
    [(run,)] = q(
        settings,
        "SELECT run_id FROM statements WHERE statement_key = ? AND is_current = 1",
        (statement_key(SUMMIT, END),),
    )
    return run


def with_cells(row: CsvRow, **cells: str) -> CsvRow:
    return CsvRow.model_validate(row.model_dump() | cells)


def aug_credit(rows: list[CsvRow]) -> CsvRow:
    [row] = [r for r in rows if (r.txn_date, r.description) == ("2026-08-12", "DINING CREDIT")]
    return row


def removed(settings: Settings, txn_key: str) -> bool:
    [(at,)] = q(settings, "SELECT removed_at FROM transactions WHERE txn_key = ?", (txn_key,))
    return at is not None


def test_a_surviving_credit_never_inherits_a_deleted_rows_choice(samples: Settings) -> None:
    """Codex D2 finding 1, through apply_rows. Two same-day $10 credits share
    their first 32 characters, so their keys differ only by occurrence. A
    replace that drops the first hands its key to the second. The choice saved
    on the first is lost, never inherited."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    base = summit_rows(samples)
    alpha = with_cells(aug_credit(base), txn_date="2026-08-20", description=ALPHA)
    bravo = with_cells(alpha, description=BRAVO)
    replace_summit(samples, "2026-10-16", [*base, alpha, bravo])
    first, second = key_of(samples, ALPHA, "2026-08-20"), key_of(samples, BRAVO, "2026-08-20")
    decide(samples, first, "link", TRAVEL)  # Different benefit
    assert effective(samples).meta.cycle_rows[(TRAVEL, "2026-01-01")] == (first,)

    replace_summit(samples, "2026-10-18", [*base, bravo])  # the correction drops alpha
    assert key_of(samples, BRAVO, "2026-08-20") == first and removed(samples, second)
    eff = effective(samples)
    assert eff.meta.flags == [rm.RowFlag("lost", first, TRAVEL)]  # "A credit choice lost its row"
    assert (TRAVEL, "2026-01-01") not in eff.meta.cycle_rows
    assert first in eff.meta.cycle_rows[(DINING, "2026-08-01")]  # bravo counts by its word


def test_true_twins_keep_the_choice_on_the_surviving_key(samples: Settings) -> None:
    """Twins with the same full description and merchant. When a replace drops
    one, the survivor holds the first key with the same row_sig: the choice
    stays."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    base = summit_rows(samples)
    twin = with_cells(aug_credit(base), txn_date="2026-08-20", description="DINING CREDIT EXTRA")
    replace_summit(samples, "2026-10-16", [*base, twin, twin])
    first, second = [
        r[0]
        for r in q(
            samples,
            "SELECT txn_key FROM transactions WHERE description = ? ORDER BY occurrence_n",
            ("DINING CREDIT EXTRA",),
        )
    ]
    decide(samples, first, "link", TRAVEL)
    replace_summit(samples, "2026-10-18", [*base, twin])
    assert removed(samples, second) and not removed(samples, first)
    eff = effective(samples)
    assert eff.meta.flags == [] and eff.meta.cycle_rows[(TRAVEL, "2026-01-01")] == (first,)


def test_a_notes_fix_never_hands_a_deleted_rows_choice_to_a_nearby_credit(
    samples: Settings,
) -> None:
    """Codex D2 finding 2, through apply_rows. The correction that drops the
    chosen credit also fixes a nearby credit's notes, so a later run wrote it.
    It was live when the choice was saved (the guard's near list), so the
    choice is lost, never moved to it."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    base = summit_rows(samples)
    aug = aug_credit(base)
    rest = [r for r in base if r is not aug]
    nearby = with_cells(aug, txn_date="2026-08-15")
    # The first correction adds the nearby credit and fixes the chosen one's
    # notes: the same run last wrote both.
    replace_summit(samples, "2026-10-16", [*rest, with_cells(aug, notes="Dining credit"), nearby])
    chosen = key_of(samples, "DINING CREDIT", "2026-08-12")
    decide(samples, chosen, "link", TRAVEL)
    # The second drops the chosen credit and fixes only the nearby one's notes.
    replace_summit(samples, "2026-10-18", [*rest, with_cells(nearby, notes="Late dining credit")])
    near = key_of(samples, "DINING CREDIT", "2026-08-15")
    assert q(samples, "SELECT run_id FROM transactions WHERE txn_key = ?", (near,))[0][0] == (
        "2026-10-18"
    )
    eff = effective(samples)
    assert eff.meta.flags == [rm.RowFlag("lost", chosen, TRAVEL)]
    assert (TRAVEL, "2026-01-01") not in eff.meta.cycle_rows
    assert eff.meta.cycle_rows[(DINING, "2026-08-01")] == (near,)


def live_keys(settings: Settings, description: str, day: str) -> list[str]:
    """The live keys of summit rows with this description and date, by occurrence."""
    return [
        r[0]
        for r in q(
            settings,
            """SELECT txn_key FROM transactions WHERE account_key = ? AND description = ?
               AND txn_date = ? AND removed_at IS NULL ORDER BY occurrence_n""",
            (SUMMIT, description, day),
        )
    ]


TYPO = "DINNG CREDIT MONTHLY ADJUSTMENT ALPHA"


def test_a_typo_fix_onto_a_twins_key_never_hands_the_choice_to_the_twin(
    samples: Settings,
) -> None:
    """Codex D2 review, through apply_rows. X (a typo) and Y are same-day $10
    credits; X comes first in the file. the owner decides X only. The fix gives X
    Y's desc_norm, so the fixed X takes Y's key and Y moves to a fresh one. Y
    was live when the choice was saved, so the choice never moves to it: lost.
    Y's own choice follows Y."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    base = summit_rows(samples)
    x = with_cells(aug_credit(base), txn_date="2026-08-20", description=TYPO)
    y = with_cells(x, description=BRAVO)
    replace_summit(samples, "2026-10-16", [*base, x, y])
    [kx], [ky] = live_keys(samples, TYPO, "2026-08-20"), live_keys(samples, BRAVO, "2026-08-20")
    decide(samples, kx, "link", TRAVEL)  # Different benefit
    decide(samples, ky, "not_benefit", reason="other")
    replace_summit(samples, "2026-10-18", [*base, with_cells(x, description=ALPHA), y])
    assert live_keys(samples, ALPHA, "2026-08-20") == [ky] and removed(samples, kx)
    [moved] = live_keys(samples, BRAVO, "2026-08-20")
    eff = effective(samples)
    assert eff.meta.flags == [rm.RowFlag("lost", kx, TRAVEL)]
    assert (TRAVEL, "2026-01-01") not in eff.meta.cycle_rows
    assert moved not in {k for ks in eff.meta.cycle_rows.values() for k in ks}  # not a benefit
    assert ky in eff.meta.cycle_rows[(DINING, "2026-08-01")]  # the fixed X counts by its word
    # Without Y's choice, Y counts by its word too, and X's choice still never moves to it.
    with write_connection(samples.db_path) as w:
        w.execute("DELETE FROM reward_credit_decisions WHERE txn_key = ?", (ky,))
    eff = effective(samples)
    assert eff.meta.flags == [rm.RowFlag("lost", kx, TRAVEL)]
    assert set(eff.meta.cycle_rows[(DINING, "2026-08-01")]) >= {ky, moved}


def test_a_correction_never_hands_a_choice_to_a_row_it_did_not_write(samples: Settings) -> None:
    """Codex D2 review. The re-find takes only rows in the chosen key's
    statement written by its current run (the replace that dropped the row).
    A credit an earlier correction added after the save, and one from another
    statement, stay where their words put them."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    base = summit_rows(samples)
    aug = aug_credit(base)
    rest = [r for r in base if r is not aug]
    chosen = key_of(samples, "DINING CREDIT", "2026-08-12")
    decide(samples, chosen, "not_benefit", reason="return")
    later = with_cells(aug, txn_date="2026-08-15")
    replace_summit(samples, "2026-10-16", [*base, later])  # adds a credit after the save
    other = add_row(
        samples, SUMMIT, "2026-08-16", 1_000, "DINING CREDIT", "Dining Credit", run_id="2026-10-18"
    )  # another statement, by the dropping run
    replace_summit(samples, "2026-10-18", [*rest, later])  # drops the chosen credit
    added = key_of(samples, "DINING CREDIT", "2026-08-15")
    assert removed(samples, chosen) and current_run(samples) == "2026-10-18"
    eff = effective(samples)
    assert eff.meta.flags == [rm.RowFlag("lost", chosen, None)]
    assert set(eff.meta.cycle_rows[(DINING, "2026-08-01")]) == {added, other}


def test_the_refind_trusts_only_the_replace_that_dropped_the_row(samples: Settings) -> None:
    """What the re-find cannot tell (plan 4.4 and 12). A later correction of
    the same statement makes a re-found choice lost: the fixed row keeps the
    run that wrote it, which is no longer the current one. A credit added
    after the save that the dropping replace also rewrites is taken for the
    corrected row: telling them apart needs the run that first wrote each
    row, which the importer does not keep."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    base = summit_rows(samples)
    aug = aug_credit(base)
    rest = [r for r in base if r is not aug]
    chosen = key_of(samples, "DINING CREDIT", "2026-08-12")
    decide(samples, chosen, "link", TRAVEL)
    fixed = with_cells(aug, description="DINING CREDIT AUG")
    replace_summit(samples, "2026-10-16", [*rest, fixed])
    new = key_of(samples, "DINING CREDIT AUG", "2026-08-12")
    assert effective(samples).meta.cycle_rows[(TRAVEL, "2026-01-01")] == (new,)
    replace_summit(samples, "2026-10-18", [*rest, fixed])  # fixed is unchanged: it keeps its run
    eff = effective(samples)
    assert eff.meta.flags == [rm.RowFlag("lost", chosen, TRAVEL)]
    assert (TRAVEL, "2026-01-01") not in eff.meta.cycle_rows

    # The gap: a credit added after the save, rewritten by the replace that drops the chosen row.
    with write_connection(samples.db_path) as w:
        w.execute("DELETE FROM reward_credit_decisions")
    replace_summit(samples, "2026-10-20", base)
    chosen = key_of(samples, "DINING CREDIT", "2026-08-12")
    decide(samples, chosen, "link", TRAVEL)
    later = with_cells(aug, txn_date="2026-08-15")
    replace_summit(samples, "2026-10-21", [*base, later])
    replace_summit(samples, "2026-10-22", [*rest, with_cells(later, notes="Late credit")])
    eff = effective(samples)
    assert eff.meta.cycle_rows[(TRAVEL, "2026-01-01")] == (
        key_of(samples, "DINING CREDIT", "2026-08-15"),
    )


def test_a_transfer_shifted_onto_the_chosen_key_never_silences_the_choice(
    samples: Settings,
) -> None:
    """Codex D2 review. X (a typo) and T, a same-day transfer with the same
    desc_norm but other text, hold k1 and k2. The fix gives X a fresh key and
    T shifts onto k1. k1 holds another row now, so the choice re-finds the
    fixed X. A transfer with X's very text is X as far as a choice can tell:
    the choice then does nothing (plan 4.4)."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    base = summit_rows(samples)
    x = with_cells(aug_credit(base), txn_date="2026-08-20", description=TYPO)
    t = with_cells(x, description=TYPO.replace("ALPHA", "XFER"), is_transfer="true")
    replace_summit(samples, "2026-10-16", [*base, x, t])
    [k1] = live_keys(samples, TYPO, "2026-08-20")
    decide(samples, k1, "link", TRAVEL)
    replace_summit(samples, "2026-10-18", [*base, with_cells(x, description=ALPHA), t])
    [fixed] = live_keys(samples, ALPHA, "2026-08-20")
    assert live_keys(samples, t.description, "2026-08-20") == [k1]
    eff = effective(samples)
    assert eff.meta.flags == [] and eff.meta.cycle_rows[(TRAVEL, "2026-01-01")] == (fixed,)

    # The same text: nothing to tell T from X, so the choice holds T and does nothing.
    with write_connection(samples.db_path) as w:
        w.execute("DELETE FROM reward_credit_decisions")
    x = with_cells(x, txn_date="2026-08-22")
    twin = with_cells(x, is_transfer="true")
    replace_summit(samples, "2026-10-20", [*base, x, twin])
    [k1, _] = live_keys(samples, TYPO, "2026-08-22")
    decide(samples, k1, "link", TRAVEL)
    replace_summit(samples, "2026-10-21", [*base, with_cells(x, description=ALPHA), twin])
    [fixed] = live_keys(samples, ALPHA, "2026-08-22")
    eff = effective(samples)
    assert eff.meta.flags == [] and (TRAVEL, "2026-01-01") not in eff.meta.cycle_rows
    assert fixed in eff.meta.cycle_rows[(DINING, "2026-08-01")]


def used_cents(eff: ov.Effective, key: str) -> dict[str, int]:
    """cycle start -> the cents a benefit's cycle counts as used."""
    b = next(b for b in eff.rewards.benefits if b.key == key)
    return {c.start: to_cents(c.used or "0.00") for c in b.cycles or []}


def test_moving_a_row_never_counts_it_twice(samples: Settings) -> None:
    """Codex D2 finding 3. Pinning February's $5 wireless row to January gave
    January $10 from rows and February $5 from the chat: one row counted
    twice. February is decided now: 0 from rows, "The chat says $5", and no
    chat-only flag."""
    add_words(samples, {WIRELESS: ["WIRELESS CREDIT"]})
    feb = key_of(samples, "WIRELESS CREDIT", "2026-02-05", "orchard-card")
    before = used_cents(effective(samples), WIRELESS)
    decide(samples, feb, "link", WIRELESS, cycle_start="2026-01-01")  # Count in January instead
    eff = effective(samples)
    after = used_cents(eff, WIRELESS)
    assert (after["2026-01-01"], after["2026-02-01"]) == (1_000, 0)
    assert sum(after.values()) == sum(before.values())
    at = (WIRELESS, "2026-02-01")
    assert eff.meta.cycle_sources[at] == "rows" and at not in eff.meta.chat_only
    february = page_cycles(samples, WIRELESS)["2026-02-01"]
    assert (february["source"], february["used_cents"], february["used_on"]) == ("rows", 0, None)
    assert (february["chat_used_cents"], february["state"], february["rows"]) == (
        500,
        "expired",
        [],
    )


def test_not_a_benefit_on_a_matched_row_stops_the_chat_fallback(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"]})
    decide(samples, key_of(samples, "DINING CREDIT", "2026-08-12"), "not_benefit", reason="other")
    eff = effective(samples)
    at = (DINING, "2026-08-01")
    assert at in eff.meta.covered and eff.meta.chat_used[at] == 1_000
    assert eff.meta.cycle_sources[at] == "rows" and eff.meta.chat_only == []  # no chat-only flag
    aug = page_cycles(samples, DINING)["2026-08-01"]
    assert (aug["source"], aug["used_cents"], aug["chat_used_cents"]) == ("rows", 0, 1_000)


def credits_used(eff: ov.Effective) -> int:
    """The cents used across every credit's cycles."""
    return sum(sum(used_cents(eff, b.key).values()) for b in eff.rewards.benefits if b.cycles)


def test_a_choice_that_stops_a_spill_never_counts_the_late_post_twice(samples: Settings) -> None:
    """Codex D2 review. July's credit posts late, on Aug 3: August holds two
    rows, so the spill moves Aug 3 back to July. A choice on Aug 12 stops the
    spill, and July goes empty. The words alone fill July, so it is decided:
    0 from rows, never the chat's $10, which counts the Aug 3 row that August
    now reads."""
    add_words(samples, {DINING: ["DINING CREDIT"]})
    july = key_of(samples, "DINING CREDIT", "2026-07-12")
    with write_connection(samples.db_path) as w:
        w.execute("UPDATE transactions SET txn_date = '2026-08-03' WHERE txn_key = ?", (july,))
    aug = key_of(samples, "DINING CREDIT", "2026-08-12")
    eff = effective(samples)
    assert eff.meta.cycle_rows[(DINING, "2026-07-01")] == (july,)
    assert eff.meta.cycle_rows[(DINING, "2026-08-01")] == (aug,)
    base, total = sum(used_cents(eff, DINING).values()), credits_used(eff)

    decide(samples, aug, "not_benefit", reason="other")
    eff = effective(samples)
    used = used_cents(eff, DINING)
    assert (used["2026-07-01"], used["2026-08-01"]) == (0, 1_000)
    assert eff.meta.cycle_sources[(DINING, "2026-07-01")] == "rows" and eff.meta.chat_only == []
    assert sum(used.values()) == base - 1_000 and credits_used(eff) == total - 1_000

    with write_connection(samples.db_path) as w:
        w.execute("DELETE FROM reward_credit_decisions")
    decide(samples, aug, "link", TRAVEL)  # Different benefit
    eff = effective(samples)
    assert used_cents(eff, DINING)["2026-07-01"] == 0
    assert used_cents(eff, TRAVEL)["2026-01-01"] == 1_000
    assert credits_used(eff) == total  # the same rows, never more


def test_a_decided_cycle_statements_do_not_cover_waits_for_rows(samples: Settings) -> None:
    """Codex D2 review. Orchard's statements end 2026-09-30, so September is
    not covered. Counting its row in August instead decides September: it
    waits for rows (no source, used 0), never "Not used" and never the chat's
    $5, which counts the row August now reads. The word preview agrees."""
    add_words(samples, {WIRELESS: ["WIRELESS CREDIT"]})
    sep = key_of(samples, "WIRELESS CREDIT", "2026-09-05", "orchard-card")
    decide(samples, sep, "link", WIRELESS, cycle_start="2026-08-01")  # Count in August instead
    eff = effective(samples)
    at = (WIRELESS, "2026-09-01")
    assert at in eff.meta.chat_used and at not in eff.meta.covered
    assert at not in eff.meta.cycle_sources and eff.meta.chat_only == []
    shown = page_cycles(samples, WIRELESS)["2026-09-01"]
    assert (shown["source"], shown["used_cents"], shown["used_on"], shown["covered"]) == (
        None,
        0,
        None,
        False,
    )
    assert shown["chat_used_cents"] == 500 and shown["rows"] == []
    assert page_cycles(samples, WIRELESS)["2026-08-01"]["used_cents"] == 1_000

    # Not a benefit, before the benefit has words: the chat's $5 stands. The
    # word would decide September, so the preview says it waits for rows.
    with write_connection(samples.db_path) as w:
        w.execute("DELETE FROM reward_credit_decisions")
        w.execute("DELETE FROM reward_match_terms")
    decide(samples, sep, "not_benefit", reason="other")
    assert page_cycles(samples, WIRELESS)["2026-09-01"]["source"] == "chat"
    with read_connection(samples.db_path) as conn:
        found = ri.term_preview(conn, WIRELESS, "WIRELESS CREDIT", private=None, today=TODAY)
    [change] = [c for c in found["cycles"] if c["start"] == "2026-09-01"]
    assert (change["used_cents_before"], change["source_before"]) == (500, "chat")
    assert (change["used_cents_after"], change["source_after"]) == (0, None)
    add_words(samples, {WIRELESS: ["WIRELESS CREDIT"]})
    shown = page_cycles(samples, WIRELESS)["2026-09-01"]
    assert (shown["source"], shown["used_cents"]) == (None, 0)


def test_a_decided_cycle_keeps_the_chats_use_after_the_statements_end(samples: Settings) -> None:
    """Codex D2 review. Summit's statements end 2026-09-10. The chat says
    September's dining credit was used on 09-14, after them, so that use
    cannot be a row a choice moved. Not a benefit on a Sep 3 return keeps the
    chat's $6, on the page and in the word preview."""
    sep = add_row(samples, SUMMIT, "2026-09-03", 400, "DINING CREDIT RETURN", "Dining Credit")
    decide(samples, sep, "not_benefit", reason="return")
    at = (DINING, "2026-09-01")
    assert page_cycles(samples, DINING)["2026-09-01"]["source"] == "chat"
    with read_connection(samples.db_path) as conn:
        found = ri.term_preview(conn, DINING, "DINING CREDIT", private=None, today=TODAY)
    assert "2026-09-01" not in {c["start"] for c in found["cycles"] if c["benefit_key"] == DINING}
    add_words(samples, {DINING: ["DINING CREDIT"]})
    eff = effective(samples)
    assert eff.meta.chat_used_on[at] == "2026-09-14" and at not in eff.meta.covered
    shown = page_cycles(samples, DINING)["2026-09-01"]
    assert (shown["source"], shown["used_cents"], shown["used_on"]) == ("chat", 600, "2026-09-14")
    # Without the choice the row wins, as rows always do.
    with write_connection(samples.db_path) as w:
        w.execute("DELETE FROM reward_credit_decisions")
    shown = page_cycles(samples, DINING)["2026-09-01"]
    assert (shown["source"], shown["used_cents"]) == ("rows", 400)


def test_a_reassignment_never_raises_a_benefits_combined_use(samples: Settings) -> None:
    add_words(samples, {DINING: ["DINING CREDIT"], WIRELESS: ["WIRELESS CREDIT"]})
    eff = effective(samples)
    before = {k: sum(used_cents(eff, k).values()) for k in (DINING, WIRELESS)}
    total = credits_used(eff)
    orchard = "orchard-card"
    for key, (description, day, card), choice_args in [
        (DINING, ("DINING CREDIT", "2026-08-12", SUMMIT), ("link", DINING, "2026-07-01")),
        (DINING, ("DINING CREDIT", "2026-03-12", SUMMIT), ("link", DINING, "2026-04-01")),
        (DINING, ("DINING CREDIT", "2026-06-12", SUMMIT), ("link", TRAVEL)),
        (DINING, ("DINING CREDIT", "2026-05-12", SUMMIT), ("not_benefit", None, None, "other")),
        (DINING, ("DINING CREDIT", "2026-04-12", SUMMIT), ("link", DINING)),
        (WIRELESS, ("WIRELESS CREDIT", "2026-02-05", orchard), ("link", WIRELESS, "2026-01-01")),
        (
            WIRELESS,
            ("WIRELESS CREDIT", "2026-09-05", orchard),
            ("not_benefit", None, None, "other"),
        ),
    ]:
        decide(samples, key_of(samples, description, day, card), *choice_args)
        eff = effective(samples)
        combined = sum(used_cents(eff, key).values())
        assert combined <= before[key], (key, day, choice_args)
        assert credits_used(eff) <= total, (key, day, choice_args)  # across every credit
        with write_connection(samples.db_path) as w:
            w.execute("DELETE FROM reward_credit_decisions")


def test_the_word_preview_reads_decided_cycles_as_the_page_does(samples: Settings) -> None:
    """Not a benefit on March's wireless row, before the benefit has words: the
    chat's $5 stands. A word would make March decided, so the preview says it
    goes to 0 from rows, as the page then shows."""
    march = key_of(samples, "WIRELESS CREDIT", "2026-03-05", "orchard-card")
    decide(samples, march, "not_benefit", reason="other")
    assert page_cycles(samples, WIRELESS)["2026-03-01"]["source"] == "chat"
    with read_connection(samples.db_path) as conn:
        found = ri.term_preview(conn, WIRELESS, "WIRELESS CREDIT", private=None, today=TODAY)
    [change] = [c for c in found["cycles"] if c["start"] == "2026-03-01"]
    assert (change["used_cents_before"], change["source_before"]) == (500, "chat")
    assert (change["used_cents_after"], change["source_after"]) == (0, "rows")
    add_words(samples, {WIRELESS: ["WIRELESS CREDIT"]})
    shown = page_cycles(samples, WIRELESS)["2026-03-01"]
    assert (shown["source"], shown["used_cents"]) == ("rows", 0)
