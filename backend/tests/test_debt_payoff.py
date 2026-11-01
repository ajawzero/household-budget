"""Debt payoff projection (gap audit D1): the steps, the pace and the Debt
tab's projections. Fake samples only."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.ai.facts import ensure_facts
from app.db.connect import connect
from app.services.balances import clear_cache
from app.services.coverage import add_months
from app.services.payoff import (
    Run,
    Terms,
    date_to_beat,
    month_interest,
    monthly_to_finish,
    payments_window,
    promo_last_month,
    rate_change_month,
    rate_steps,
    simulate,
    statement_pace,
)
from app.services.plans import _paid_down, debt_page

TODAY = date(2026, 10, 20)
CASES = Path(__file__).parents[2] / "frontend/src/features/accounts/debt/payoff-cases.json"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def _num(v: str | None) -> int | None:
    return None if v is None else int(v.replace("_", ""))


def _cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def _terms(c: dict) -> Terms:
    t = c["terms"]
    return Terms(
        owed_cents=_num(t["owed_cents"]),
        apr_milli_pct=_num(t["apr_milli_pct"]),
        promo=t["promo"],
        promo_month=t["promo_month"],
        from_month=t["from_month"],
        rate_changes=tuple(
            (c["effective_on"], _num(c["apr_milli_pct"])) for c in t.get("rate_changes", [])
        ),
    )


# --------------------------------------------------------------------------
# The steps
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_shared_cases(case: dict) -> None:
    """The same cases projection.test.ts runs, so the server and Try a payment agree."""
    t = _terms(case)
    run = asdict(simulate(t, _num(case["monthly_cents"])))
    want = {k: v if k == "months" else _num(v) for k, v in case["run"].items()}
    assert run == want
    assert monthly_to_finish(t, case["by_month"]) == _num(case["monthly_to_finish"])


def test_month_interest_rounds_half_up() -> None:
    assert month_interest(100_000, 12_000) == 1_000  # 12% a year is 1% a month
    assert month_interest(50, 12_000) == 1  # 0.5 cents rounds up
    assert month_interest(49, 12_000) == 0
    assert month_interest(100_000, 0) == 0


def test_monthly_to_finish_is_the_smallest_that_does() -> None:
    t = Terms(1_000_000, 6_000, False, None, "2026-09")
    need = monthly_to_finish(t, "2027-06")  # 9 payments, Oct to Jun
    assert simulate(t, need).months == 9
    assert simulate(t, need - 1).months == 10
    # A month already gone still means one payment: pay it all next month.
    assert monthly_to_finish(t, "2026-08") == 1_000_000 + month_interest(1_000_000, 6_000)


def test_no_payment_never_pays_off() -> None:
    t = Terms(100_000, 0, False, None, "2026-09")
    assert simulate(t, 0).months is None
    assert simulate(t, -500).months is None


def test_promo_rate_starts_after_the_promo_month() -> None:
    t = Terms(100_000, 24_000, True, "2026-10", "2026-09")
    assert t.rate("2026-10") == 0
    assert t.rate("2026-11") == 24_000
    run = simulate(t, 50_000)  # Oct at 0%; Nov 1_000 interest on 50_000; Dec 20 on 1_000
    assert (run.months, run.interest_cents, run.last_payment_cents) == (3, 1_020, 1_020)


def test_rate_change_month() -> None:
    assert rate_change_month("2026-11-01") == "2026-11"
    # Mid-month: a payment that month is charged part at the old rate.
    assert rate_change_month("2026-11-15") == "2026-12"
    assert rate_change_month("2026-12-31") == "2027-01"


def test_rate_steps_start_by_their_month() -> None:
    changes = (("2026-09-01", 1), ("2026-09-02", 2), ("2026-10-01", 3))
    assert rate_steps(changes) == [("2026-09", 1), ("2026-10", 2), ("2026-10", 3)]
    t = Terms(100_000, 19_240, False, None, "2026-08", changes)
    assert t.rate("2026-08") == 19_240
    assert t.rate("2026-09") == 1
    # Two changes that start in the same month: the later date wins.
    assert t.rate("2026-10") == 3
    # A step that started by from_month applies from month one (scheduled_rates sends
    # only the ones that count).
    assert Terms(100_000, 19_240, False, None, "2026-09", changes).rate("2026-10") == 3


def test_monthly_to_finish_bounds_by_the_highest_scheduled_rate() -> None:
    t = Terms(300_000, 19_240, False, None, "2026-09", (("2026-10-01", 24_990),))
    # One payment month at the new rate: the owed plus a month's interest at 24.99%.
    # Today's APR as the bound would give 304_810.
    assert monthly_to_finish(t, "2026-10") == 300_000 + month_interest(300_000, 24_990)
    assert monthly_to_finish(t, "2026-10") == 306_248


# --------------------------------------------------------------------------
# Pace
# --------------------------------------------------------------------------


def _stmts(*ends: tuple[str, int], txn_count: int = 1, interest: int | None = None) -> list:
    """Gap-free statements, (period_end, balance) each, each starting the day after the last."""
    out, start = [], None
    for end, balance in ends:
        out.append(
            {
                "period_start": start or end,
                "period_end": end,
                "balance_cents": balance,
                "interest_cents": interest,
                "txn_count": txn_count,
            }
        )
        start = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    return out


def test_statement_pace_counts_interest_and_new_charges() -> None:
    stmts = _stmts(
        ("2026-05-15", -1_100_000),
        ("2026-06-15", -1_000_000),
        ("2026-07-15", -990_000),
        ("2026-08-15", -980_000),
        ("2026-09-15", -970_000),
    )
    # Jun 15 to Sep 15: down 30_000, plus 3 x 5_000 interest. The May statement is too old.
    interest = [(d, -5_000, 0) for d in ("2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15")]
    assert statement_pace(stmts, interest) == (15_000, "2026-07", "2026-09")
    # A balance that grows gives a pace below zero.
    grows = _stmts(("2026-08-31", -10_000), ("2026-09-30", -12_000))
    assert statement_pace(grows, []) == (-2_000, "2026-09", "2026-09")


def test_statement_pace_needs_two_months() -> None:
    assert statement_pace(_stmts(("2026-09-15", -1_000)), []) is None
    same_month = _stmts(("2026-09-01", -1_000), ("2026-09-29", -900))
    assert statement_pace(same_month, []) is None
    too_old = _stmts(("2026-04-30", -1_000), ("2026-09-30", -900))
    assert statement_pace(too_old, []) is None


def test_statement_pace_counts_days_not_calendar_months() -> None:
    # A closing day that moves across a month end: Jun 1 to Sep 30 is four periods.
    stmts = _stmts(
        ("2026-06-01", -50_000),
        ("2026-06-30", -40_000),
        ("2026-07-30", -30_000),
        ("2026-08-31", -20_000),
        ("2026-09-30", -10_000),
    )
    assert statement_pace(stmts, []) == (10_000, "2026-06", "2026-09")


def test_statement_pace_uses_the_newest_gap_free_run() -> None:
    stmts = _stmts(("2026-06-15", -40_000), ("2026-07-15", -30_000))
    later = _stmts(("2026-08-15", -20_000), ("2026-09-15", -10_000))
    later[0]["period_start"] = "2026-07-20"  # Jul 16 to Jul 19 missing
    # The Jul 15 interest is before the run, so it is not in the pace either.
    interest = [("2026-07-15", -900, 0), ("2026-09-15", -500, 0)]
    assert statement_pace(stmts + later, interest) == (10_500, "2026-09", "2026-09")
    later[0]["period_start"] = "2026-08-20"
    assert statement_pace(stmts + later[1:], interest) is None


def test_statement_pace_falls_back_to_the_statement_interest() -> None:
    # Loan statements with no rows at all carry their interest on the statement.
    bare = _stmts(("2026-08-15", -100_000), ("2026-09-15", -99_000), txn_count=0, interest=-600)
    assert statement_pace(bare, []) == (1_600, "2026-09", "2026-09")
    # With rows, a hidden interest row is left out, not swapped for the statement's.
    rows = _stmts(("2026-08-15", -100_000), ("2026-09-15", -99_000), interest=-600)
    assert statement_pace(rows, [("2026-09-15", -600, 1)]) == (1_000, "2026-09", "2026-09")
    assert statement_pace(rows, [("2026-09-15", -600, 0)]) == (1_600, "2026-09", "2026-09")


def _acct(**kw) -> dict:
    return {
        "tracked_by": "statements",
        "opened_on": None,
        "closed_on": None,
        "active": 1,
        "dormant": 0,
        **kw,
    }


class _Cov:
    """accounts {key: row}, spans {key: [(period_start, period_end)]}."""

    def __init__(self, accounts: dict, spans: dict[str, list[tuple[str, str]]]) -> None:
        self.accounts = accounts
        self.statements = {
            k: [{"period_start": a, "period_end": b} for a, b in v] for k, v in spans.items()
        }


def test_payments_window() -> None:
    full = _Cov({"checking": _acct()}, {"checking": [("2026-01-01", "2026-09-30")]})
    assert payments_window({"checking"}, full, None, "2026-10") == ("2026-07", "2026-09")
    # No payers: the months still count, with nothing paid.
    assert payments_window(set(), full, None, "2026-10") == ("2026-07", "2026-09")
    # Covered to mid-September: September is left out.
    mid = _Cov({"checking": _acct()}, {"checking": [("2026-01-01", "2026-09-14")]})
    assert payments_window({"checking"}, mid, None, "2026-10") == ("2026-06", "2026-08")
    # Never this month, never the month the debt opened or before.
    ahead = _Cov({"checking": _acct()}, {"checking": [("2026-01-01", "2026-10-31")]})
    assert payments_window({"checking"}, ahead, "2026-08-01", "2026-10") == (
        "2026-09",
        "2026-09",
    )
    assert payments_window({"checking"}, full, "2026-09-05", "2026-10") is None


def test_payments_window_follows_each_payer_month_by_month() -> None:
    harbor = _acct()
    # An old account that paid until it closed in July holds nothing back after that.
    old = _acct(closed_on="2026-07-10", active=0)
    cov = _Cov(
        {"harbor": harbor, "old": old},
        {"harbor": [("2026-01-01", "2026-09-30")], "old": [("2026-01-01", "2026-07-10")]},
    )
    assert payments_window({"harbor", "old"}, cov, None, "2026-10") == ("2026-07", "2026-09")
    # Inactive with no close date: needed only up to its last statement.
    idle = _acct(active=0)
    cov = _Cov(
        {"harbor": harbor, "idle": idle},
        {"harbor": [("2026-01-01", "2026-09-30")], "idle": [("2026-01-01", "2026-06-30")]},
    )
    assert payments_window({"harbor", "idle"}, cov, None, "2026-10") == ("2026-07", "2026-09")
    # A missing February statement does not matter for Jul to Sep.
    gap = _Cov(
        {"harbor": harbor},
        {"harbor": [("2026-01-01", "2026-01-31"), ("2026-03-01", "2026-09-30")]},
    )
    assert payments_window({"harbor"}, gap, None, "2026-10") == ("2026-07", "2026-09")
    # History that starts in September: September alone, not three months.
    late = _Cov({"harbor": harbor}, {"harbor": [("2026-09-01", "2026-09-30")]})
    assert payments_window({"harbor"}, late, None, "2026-10") == ("2026-09", "2026-09")
    # Starting mid-month covers no month in full.
    mid = _Cov({"harbor": harbor}, {"harbor": [("2026-09-10", "2026-09-30")]})
    assert payments_window({"harbor"}, mid, None, "2026-10") is None


def test_promo_last_month() -> None:
    assert promo_last_month("2027-03-31") == "2027-03"
    # A mid-month date: a payment in March could land after it.
    assert promo_last_month("2027-03-05") == "2027-02"
    assert promo_last_month("2028-02-29") == "2028-02"


def test_dates_to_beat() -> None:
    t = Terms(500_000, 21_990, True, "2026-09", "2026-10")
    # No payment month left before the date: the whole balance, no verdict.
    due = date_to_beat(t, simulate(t, 100_000), "promo", "2026-10-31", "2026-10")
    assert (due["monthly_cents"], due["on_track"]) == (500_000, None)
    # No pace: the amount, no verdict.
    loan = Terms(1_000_000, 6_000, False, None, "2026-09")
    none = date_to_beat(loan, None, "payoff_date", "2027-06-01", "2027-06")
    assert (none["monthly_cents"], none["on_track"]) == (monthly_to_finish(loan, "2027-06"), None)
    late = date_to_beat(loan, Run(), "payoff_date", "2027-06-01", "2027-06")
    assert late["on_track"] is False


# --------------------------------------------------------------------------
# The Debt tab
# --------------------------------------------------------------------------


def _rows(client: TestClient) -> dict[str, dict]:
    return {r["account_key"]: r for r in client.get("/api/debt").json()["ledger"]}


def test_debt_tab_projections(samples, admin: TestClient) -> None:
    rows = _rows(admin)
    # Cards paid in full each month get none.
    assert rows["summit-card"]["projection"] is None
    assert rows["orchard-card"]["projection"] is None

    cobalt = rows["cobalt-card"]["projection"]
    # Jun 22 to Sep 22 statements: $250 paid each month, less new charges, at 0%.
    assert (cobalt["pace_basis"], cobalt["pace_from"], cobalt["pace_through"]) == (
        "statements",
        "2026-07",
        "2026-09",
    )
    assert cobalt["pace_cents"] == 10_624
    assert cobalt["promo_until"] == "2027-03-31"
    assert (cobalt["months"], cobalt["payoff_month"]) == (9, "2027-06")
    assert cobalt["promo_month"] == "2027-03"
    assert cobalt["targets"] == [
        {
            "kind": "promo",
            "date": "2027-03-31",
            "by_month": "2027-03",
            "monthly_cents": 15_596,
            "on_track": False,
        }
    ]

    van = rows["van-loan"]["projection"]
    # No statements: payments from Sam's checking in Sep, the first month after it opened
    # (Aug 1): $650 + $1,000.
    assert (van["pace_basis"], van["pace_from"], van["pace_through"]) == (
        "payments",
        "2026-09",
        "2026-09",
    )
    assert van["pace_cents"] == 165_000
    assert van["from_month"] == "2026-09"
    assert van["targets"] == []

    mortgage = rows["pinecrest-mortgage"]["projection"]
    # Principal plus interest from the statements: the $1,500 payment.
    assert (mortgage["pace_basis"], mortgage["pace_cents"]) == ("statements", 150_000)
    assert mortgage["payoff_month"] == "2047-11"
    assert mortgage["interest_to_go_cents"] > 0
    assert mortgage["targets"] == [
        {
            "kind": "payoff_date",
            "date": "2049-06-01",
            "by_month": "2049-06",
            "monthly_cents": 143_459,
            "on_track": True,
        }
    ]


def test_hidden_interest_is_left_out_of_the_pace(samples, db) -> None:
    key = db.execute(
        """SELECT txn_key FROM transactions WHERE account_key = 'pinecrest-mortgage'
           AND txn_type = 'interest' AND txn_date = '2026-08-15'"""
    ).fetchone()["txn_key"]
    db.execute(
        """INSERT INTO transaction_overrides (txn_key, hidden, updated_at)
           VALUES (?, 1, '2026-10-20T00:00:00+00:00')""",
        (key,),
    )
    clear_cache()
    rows = {r["account_key"]: r for r in debt_page(db)["ledger"]}
    assert rows["pinecrest-mortgage"]["projection"]["pace_cents"] == 150_000 - 30_752  # /3


def test_no_payments_means_no_pace(samples, db) -> None:
    for r in db.execute(
        "SELECT txn_key FROM transactions WHERE counterparty_account_key = 'van-loan'"
    ).fetchall():
        db.execute(
            """INSERT INTO transaction_overrides (txn_key, hidden, updated_at)
               VALUES (?, 1, '2026-10-20T00:00:00+00:00')""",
            (r["txn_key"],),
        )
    clear_cache()
    van = {r["account_key"]: r for r in debt_page(db)["ledger"]}["van-loan"]["projection"]
    assert (van["pace_cents"], van["months"], van["payoff_month"]) == (None, None, None)
    assert van["pace_basis"] == "payments"


def test_past_dates_are_not_targets(samples, db) -> None:
    db.execute(
        "UPDATE accounts SET payoff_date = '2026-10-19' WHERE account_key = 'pinecrest-mortgage'"
    )
    clear_cache()
    rows = {r["account_key"]: r for r in debt_page(db)["ledger"]}
    assert rows["pinecrest-mortgage"]["projection"]["targets"] == []


def test_a_card_carrying_a_balance_gets_one(samples, db) -> None:
    db.execute("UPDATE accounts SET autopay = 'minimum' WHERE account_key = 'orchard-card'")
    clear_cache()
    orchard = {r["account_key"]: r for r in debt_page(db)["ledger"]}["orchard-card"]["projection"]
    # The statement balance, plan balances included: down from 756.60 (Jun) to 616.18 (Sep).
    assert (orchard["pace_basis"], orchard["pace_cents"]) == ("statements", 4_681)
    assert (orchard["months"], orchard["payoff_month"]) == (15, "2027-12")


def _orchard(db) -> dict:
    clear_cache()
    return {r["account_key"]: r for r in debt_page(db)["ledger"]}["orchard-card"]


def test_a_scheduled_rate_moves_the_projection(samples, db) -> None:
    # Orchard: 19.24% variable, 19.99% from Nov 1, balance from Sep (format 1.4 samples).
    db.execute("UPDATE accounts SET autopay = 'minimum' WHERE account_key = 'orchard-card'")
    row = _orchard(db)
    assert row["rate_changes"] == [{"effective_on": "2026-11-01", "apr_milli_pct": 19_990}]
    p = row["projection"]
    assert (p["months"], p["payoff_month"]) == (15, "2027-12")
    # At a flat 19.24%: 8_149, 4_233 and 8_847.
    assert (p["interest_to_go_cents"], p["last_payment_cents"], p["early_cents"]) == (
        8_470,
        4_554,
        9_160,
    )


def test_a_fixed_rate_drops_the_schedule(samples, db) -> None:
    db.execute(
        """UPDATE accounts SET autopay = 'minimum', rate_type = 'fixed'
           WHERE account_key = 'orchard-card'"""
    )
    row = _orchard(db)
    # The stored schedule stays, but a fixed rate never uses it.
    assert db.execute(
        "SELECT rate_changes_json FROM accounts WHERE account_key = 'orchard-card'"
    ).fetchone()[0]
    assert row["rate_changes"] == []
    assert row["projection"]["interest_to_go_cents"] == 8_149


def test_a_past_rate_change_is_left_out(samples, db) -> None:
    db.execute(
        """UPDATE accounts SET autopay = 'minimum',
               rate_changes_json = '[{"apr":"19.99","effective_on":"2026-09-01"}]'
           WHERE account_key = 'orchard-card'"""
    )
    row = _orchard(db)
    assert row["rate_changes"] == []
    assert row["projection"]["interest_to_go_cents"] == 8_149
    # Sep 2 starts in Oct, after the balance's month: it counts.
    db.execute(
        """UPDATE accounts SET rate_changes_json = '[{"apr":"19.99","effective_on":"2026-09-02"}]'
           WHERE account_key = 'orchard-card'"""
    )
    row = _orchard(db)
    assert row["rate_changes"] == [{"effective_on": "2026-09-02", "apr_milli_pct": 19_990}]
    assert row["projection"]["interest_to_go_cents"] > 8_149


def test_an_estimated_loan_keeps_a_change_since_its_real_balance(samples, db) -> None:
    # The van loan's real balance is Sep 30. A payment on Oct 15 moves its estimate to
    # Oct, and a change on Oct 1 started after the real balance: it counts, from the
    # first projected month (Codex review).
    key = db.execute(
        """SELECT txn_key FROM transactions WHERE counterparty_account_key = 'van-loan'
           ORDER BY txn_date DESC"""
    ).fetchone()[0]
    db.execute("UPDATE transactions SET txn_date = '2026-10-15' WHERE txn_key = ?", (key,))
    db.execute(
        """UPDATE accounts SET rate_type = 'variable',
               rate_changes_json = '[{"apr":"7.49","effective_on":"2026-10-01"}]'
           WHERE account_key = 'van-loan'"""
    )
    clear_cache()
    van = {r["account_key"]: r for r in debt_page(db)["ledger"]}["van-loan"]
    p = van["projection"]
    assert p["from_month"] == "2026-10"
    assert van["rate_changes"] == [{"effective_on": "2026-10-01", "apr_milli_pct": 7_490}]
    flat = simulate(Terms(van["owed_cents"], 7_490, False, None, "2026-10"), p["pace_cents"])
    assert p["months"] == flat.months
    assert p["interest_to_go_cents"] == flat.interest_cents


def test_a_debt_still_owed_never_reads_fully_paid_down() -> None:
    assert _paid_down(4_200_000, 1_500) == "99.9"  # 99.96 would round to 100.0
    assert _paid_down(4_200_000, 4_200_000) == "0.0"
    assert _paid_down(4_200_000, 4_200_001) is None
    assert _paid_down(None, 1_500) is None


def test_paid_down_from_the_original(samples, admin: TestClient) -> None:
    rows = _rows(admin)
    pick = ("original_cents", "paid_down_pct")
    assert tuple(rows["van-loan"][k] for k in pick) == (4_200_000, "2.5")
    assert tuple(rows["pinecrest-mortgage"][k] for k in pick) == (30_000_000, "18.4")
    assert tuple(rows["cobalt-card"][k] for k in pick) == (None, None)
    assert rows["van-loan"]["rate_changes"] == []


def test_more_owed_than_the_original_has_no_paid_down(samples, db) -> None:
    db.execute("UPDATE accounts SET original_cents = 4_000_000 WHERE account_key = 'van-loan'")
    clear_cache()
    van = {r["account_key"]: r for r in debt_page(db)["ledger"]}["van-loan"]
    assert (van["original_cents"], van["paid_down_pct"]) == (4_000_000, None)


def test_the_drawer_shows_the_original(samples, admin: TestClient) -> None:
    assert admin.get("/api/accounts/van-loan").json()["terms"]["original_cents"] == 4_200_000
    assert admin.get("/api/accounts/summit-card").json()["terms"]["original_cents"] is None


def test_a_newer_balance_moves_the_start(samples, db) -> None:
    db.execute(
        """INSERT INTO balances (account_key, as_of, source, balance_cents, run_id)
           VALUES ('cobalt-card', '2026-10-10', 'manual', -100_000, 'test')"""
    )
    clear_cache()
    cobalt = {r["account_key"]: r for r in debt_page(db)["ledger"]}["cobalt-card"]["projection"]
    assert cobalt["from_month"] == "2026-10"
    assert cobalt["payoff_month"] == add_months("2026-10", cobalt["months"])


def test_interest_goes_by_posting_date(samples, db) -> None:
    # Charged Jun 15 (the older statement's closing day) but posted Jul 15: in the window.
    db.execute(
        """UPDATE transactions SET txn_date = '2026-06-15', post_date = '2026-07-15'
           WHERE account_key = 'pinecrest-mortgage' AND txn_type = 'interest'
           AND txn_date = '2026-07-15'"""
    )
    clear_cache()
    rows = {r["account_key"]: r for r in debt_page(db)["ledger"]}
    assert rows["pinecrest-mortgage"]["projection"]["pace_cents"] == 150_000


def test_no_pace_gives_no_verdict(samples, db) -> None:
    for r in db.execute(
        "SELECT txn_key FROM transactions WHERE counterparty_account_key = 'van-loan'"
    ).fetchall():
        db.execute(
            """INSERT INTO transaction_overrides (txn_key, hidden, updated_at)
               VALUES (?, 1, '2026-10-20T00:00:00+00:00')""",
            (r["txn_key"],),
        )
    db.execute("UPDATE accounts SET payoff_date = '2031-08-01' WHERE account_key = 'van-loan'")
    clear_cache()
    van = {r["account_key"]: r for r in debt_page(db)["ledger"]}["van-loan"]["projection"]
    assert van["pace_cents"] is None
    [target] = van["targets"]
    assert (target["kind"], target["on_track"]) == ("payoff_date", None)
    assert target["monthly_cents"] > 0


def test_projection_stays_out_of_the_ai(samples) -> None:
    ensure_facts(samples.db_path, None)
    conn = connect(samples.db_path)
    try:
        blob = " ".join(r["json"] for r in conn.execute("SELECT json FROM ai_facts"))
    finally:
        conn.close()
    words = ("projection", "pace_cents", "interest_to_go_cents", "monthly_to_finish", "paid_down")
    for word in words:
        assert word not in blob
