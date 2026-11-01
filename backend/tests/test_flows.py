"""Money in, market change, fees and dividends per investment account (gap audit A5, A6, 54).

Sample numbers: the brokerage gets 500.00 each month (Jan to Sep) and has
dividend and reinvest pairs in Mar, Jun and Sep. The 401k has quarterly
statements with fees (money out, negative) inside its market change. The kids
529 has quarterly statements and no fees. Synthetic tests build a few
accounts and statements by hand.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest
from api_helpers import MEMBER, MEMBER_PW, PROD_ORIGIN, login
from fastapi.testclient import TestClient
from test_networth import _account

from app import config
from app.ai.facts import ensure_facts
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.services import flows, networth
from app.services.balances import clear_cache

TODAY = date(2026, 10, 20)
BROKER = "lakeview-brokerage"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


@pytest.fixture
def conn(samples: Settings):
    c = connect(samples.db_path)
    yield c
    c.close()


def summary(conn: sqlite3.Connection, key: str, on: date = TODAY) -> dict | None:
    return flows.investment_summary(conn, key, on)


# --------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------


def test_brokerage_last_statement_and_this_year(conn: sqlite3.Connection) -> None:
    s = summary(conn, BROKER)
    assert s is not None and s["year"] == 2026
    latest, ytd = s["latest"], s["ytd"]
    assert (latest["period_start"], latest["period_end"]) == ("2026-09-01", "2026-09-30")
    assert (latest["net_flows_cents"], latest["market_change_cents"]) == (50_000, 62_490)
    assert latest["return_pct"] == "1.1"
    assert (latest["statements"], latest["split_statements"]) == (1, 1)
    assert (ytd["period_start"], ytd["period_end"]) == ("2026-01-01", "2026-09-30")
    assert (ytd["statements"], ytd["split_statements"]) == (9, 9)
    assert (ytd["net_flows_cents"], ytd["market_change_cents"]) == (450_000, 109_128)
    assert ytd["return_pct"] == "2.1"
    # No fees on brokerage statements: the row is hidden.
    assert latest["fees_paid_cents"] is None and ytd["fees_paid_cents"] is None
    assert (ytd["fee_statements"], ytd["fee_months"], ytd["fees_per_month_cents"]) == (0, 0, None)
    # Dividends: the Sep 15 row, then 59.38 + 48.63 + 66.33. Reinvest rows do not count.
    assert latest["dividends_cents"] == 6633
    assert ytd["dividends_cents"] == 17_434


def test_401k_return_is_chained_and_fees_are_money_out(conn: sqlite3.Connection) -> None:
    s = summary(conn, "alex-401k")
    assert s is not None
    latest, ytd = s["latest"], s["ytd"]
    assert (latest["period_start"], latest["period_end"]) == ("2026-07-01", "2026-09-30")
    assert latest["return_pct"] == "3.1"
    assert (ytd["net_flows_cents"], ytd["market_change_cents"]) == (763_200, -79_787)
    # Chained: each quarter's market change over its opening. The simple ratio
    # over the Jan 1 balance would say -0.9.
    assert ytd["return_pct"] == "-1.1"
    assert networth.pct(-79_787, 8_500_000) == "-0.9"
    # Stored fees are -21.35, -21.71 and -22.95: a fee taken shows as a positive amount.
    assert (ytd["fees_paid_cents"], ytd["fee_statements"], ytd["fee_months"]) == (6601, 3, 9)
    assert ytd["fees_per_month_cents"] == 733
    assert (latest["fees_paid_cents"], latest["fee_months"]) == (2295, 3)
    assert latest["fees_per_month_cents"] == 765
    assert latest["dividends_cents"] is None and ytd["dividends_cents"] is None


def test_kids_529(conn: sqlite3.Connection) -> None:
    s = summary(conn, "kids-529")
    assert s is not None
    ytd = s["ytd"]
    # 200.00 a month for 9 months, plus gifts of 500.00 and 250.00.
    assert (ytd["statements"], ytd["net_flows_cents"]) == (3, 255_000)
    assert ytd["market_change_cents"] == 38_850 - 23_987 + 69_185
    assert (ytd["return_pct"], s["latest"]["return_pct"]) == ("4.3", "3.4")
    assert ytd["fees_paid_cents"] is None and ytd["dividends_cents"] is None


def test_not_an_investment_account(conn: sqlite3.Connection) -> None:
    assert summary(conn, "summit-card") is None
    assert summary(conn, "harbor-savings") is None
    assert summary(conn, "no-such-account") is None


def test_next_year_has_no_ytd_and_keeps_the_last_statement(conn: sqlite3.Connection) -> None:
    s = summary(conn, BROKER, date(2027, 1, 10))
    assert s is not None and s["year"] == 2027
    assert s["ytd"] is None
    assert s["latest"]["period_end"] == "2026-09-30"


def test_a_statement_after_today_is_left_out(conn: sqlite3.Connection) -> None:
    s = summary(conn, BROKER, date(2026, 9, 29))
    assert s is not None
    assert s["latest"]["period_end"] == "2026-08-31"
    assert s["ytd"]["statements"] == 8


def test_hidden_dividend_row_is_left_out(conn: sqlite3.Connection) -> None:
    key = conn.execute(
        """SELECT txn_key FROM transactions WHERE account_key = ? AND txn_type = 'dividend'
           AND txn_date = '2026-09-15'""",
        (BROKER,),
    ).fetchone()[0]
    with write_transaction(conn):
        conn.execute(
            """INSERT INTO transaction_overrides (txn_key, hidden, updated_at)
               VALUES (?, 1, '2026-10-20T00:00:00Z')""",
            (key,),
        )
    s = summary(conn, BROKER)
    assert s is not None
    assert s["ytd"]["dividends_cents"] == 10_801
    assert s["latest"]["dividends_cents"] is None


def test_account_detail_carries_the_summary(conn: sqlite3.Connection) -> None:
    d = networth.account_detail(conn, BROKER, "1M")
    # The panel ignores the drawer's range.
    assert d["investment"] == summary(conn, BROKER)
    assert d["statements"][0]["opening_cents"] is not None
    assert d["statements"][0]["net_flows_cents"] == 50_000


# --------------------------------------------------------------------------
# Synthetic
# --------------------------------------------------------------------------


def _stmt(db, key, start, end, opening, *, flows=None, market=None, fees=None) -> None:
    db.execute(
        """INSERT OR IGNORE INTO import_runs (run_id, run_date, run_seq, mode, status, started_at)
           VALUES ('r', '2026-10-01', 1, 'monthly', 'imported', '2026-10-01T00:00:00Z')"""
    )
    closing = (opening or 0) + (flows or 0) + (market or 0)
    db.execute(
        """INSERT INTO statements (statement_key, run_id, is_current, account_key,
               period_start, period_end, opening_cents, closing_cents, fees_cents,
               net_flows_cents, market_change_cents, txn_count, reconciled, content_sha256)
           VALUES (?, 'r', 1, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 'x')""",
        (f"{key}:{end}", key, start, end, opening, closing, fees, flows, market),
    )


def _fund(db, key="fund") -> str:
    _account(db, key, group="investments", type="brokerage", cadence="monthly")
    return key


def test_a_statement_without_the_split(db) -> None:
    key = _fund(db)
    _stmt(db, key, "2026-01-01", "2026-01-31", 100_000, flows=1000, market=2000)
    _stmt(db, key, "2026-02-01", "2026-02-28", 103_000)  # point in time, no split
    ytd = summary(db, key)["ytd"]
    assert (ytd["statements"], ytd["split_statements"]) == (2, 1)
    assert (ytd["net_flows_cents"], ytd["market_change_cents"]) == (1000, 2000)
    assert ytd["return_pct"] is None
    # The newest statement has no numbers, but this year does: the summary shows.
    latest = summary(db, key)["latest"]
    assert latest["net_flows_cents"] is None and latest["return_pct"] is None


def test_no_numbers_at_all_is_none(db) -> None:
    key = _fund(db)
    _stmt(db, key, "2026-01-01", "2026-01-31", 100_000)
    assert summary(db, key) is None


def test_a_statement_across_jan_1_counts_in_full(db) -> None:
    key = _fund(db)
    _stmt(db, key, "2025-11-15", "2025-12-14", 100_000, flows=500, market=1000)
    _stmt(db, key, "2025-12-15", "2026-01-14", 101_500, flows=500, market=1015)
    ytd = summary(db, key)["ytd"]
    assert ytd["statements"] == 1
    assert (ytd["period_start"], ytd["period_end"]) == ("2025-12-15", "2026-01-14")
    assert ytd["net_flows_cents"] == 500
    assert ytd["return_pct"] == "1.0"


def test_a_missing_statement_is_a_gap(db) -> None:
    key = _fund(db)
    _stmt(db, key, "2026-01-01", "2026-01-31", 100_000, flows=1000, market=1000)
    # February is missing.
    _stmt(db, key, "2026-03-01", "2026-03-31", 104_000, flows=1000, market=1040)
    _stmt(db, key, "2026-04-01", "2026-04-30", 106_040, flows=1000, market=0)
    ytd = summary(db, key)["ytd"]
    assert (ytd["statements"], ytd["gaps"]) == (3, 1)
    # The sums are still given (the screen says they are short); the return is not.
    assert (ytd["net_flows_cents"], ytd["market_change_cents"]) == (3000, 2040)
    assert ytd["return_pct"] is None
    assert summary(db, key)["latest"]["gaps"] == 0


def test_opening_zero_has_no_return(db) -> None:
    key = _fund(db)
    _stmt(db, key, "2026-01-01", "2026-01-31", 0, flows=50_000, market=100)
    p = summary(db, key)["ytd"]
    assert p["net_flows_cents"] == 50_000 and p["return_pct"] is None


def test_fee_sign_and_months(db) -> None:
    key = _fund(db)
    # A month with a fee, and a refund the next month: 15.00 - 2.00 = 13.00 paid.
    _stmt(db, key, "2026-01-15", "2026-02-14", 100_000, flows=0, market=0, fees=-1500)
    _stmt(db, key, "2026-02-15", "2026-03-14", 100_000, flows=0, market=0, fees=200)
    _stmt(db, key, "2026-03-15", "2026-04-14", 100_000, flows=0, market=0)  # no fees given
    ytd = summary(db, key)["ytd"]
    assert ytd["fees_paid_cents"] == 1300
    assert (ytd["fee_statements"], ytd["fee_months"], ytd["statements"]) == (2, 2, 3)
    assert ytd["fees_per_month_cents"] == 650
    rows = db.execute("SELECT * FROM statements WHERE account_key = ?", (key,)).fetchall()
    assert flows._months(rows[0]) == 1  # Jan 15 to Feb 14
    assert ytd["gaps"] == 0  # each statement starts the day after the last one ended
    q = _fund(db, "quarterly")
    _stmt(db, q, "2026-07-01", "2026-09-30", 100_000, flows=0, market=0, fees=-1000)
    (row,) = db.execute("SELECT * FROM statements WHERE account_key = 'quarterly'").fetchall()
    assert flows._months(row) == 3  # 92 days
    assert summary(db, q)["ytd"]["fees_per_month_cents"] == 333


def test_flows_page_lists_this_years_investment_accounts(db) -> None:
    a = _fund(db, "a-fund")
    _stmt(db, a, "2026-01-01", "2026-01-31", 100_000, flows=100, market=100)
    old = _fund(db, "b-old-fund")  # only last year's statements: not listed
    _stmt(db, old, "2025-01-01", "2025-01-31", 100_000, flows=100, market=100)
    closed = _fund(db, "c-closed-fund")
    db.execute("UPDATE accounts SET closed_on = '2026-03-01' WHERE account_key = ?", (closed,))
    _stmt(db, closed, "2026-02-01", "2026-02-28", 100_000, flows=-100_100, market=100)
    _account(db, "chk")
    _stmt(db, "chk", "2026-01-01", "2026-01-31", 100_000, flows=100, market=100)
    page = flows.flows_page(db, TODAY)
    assert (page["as_of"], page["year"]) == ("2026-10-20", 2026)
    assert [(r["account_key"], r["closed"]) for r in page["accounts"]] == [
        ("a-fund", False),
        ("c-closed-fund", True),
    ]


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_flows_api_matches_the_drawer(samples: Settings, prod: TestClient) -> None:
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    r = prod.get("/api/investments/flows")
    assert r.status_code == 200
    body = r.json()
    assert (body["as_of"], body["year"]) == ("2026-10-20", 2026)
    # Accounts page order: investments group first, then retirement.
    assert [a["account_key"] for a in body["accounts"]] == [BROKER, "kids-529", "alex-401k"]
    for a in body["accounts"]:
        assert a["closed"] is False
        detail = prod.get(f"/api/accounts/{a['account_key']}").json()
        assert detail["investment"]["ytd"] == a["ytd"]
        assert detail["investment"]["latest"] == a["latest"]
    card = prod.get("/api/accounts/summit-card").json()
    assert card["investment"] is None
    assert card["statements"][0]["market_change_cents"] is None


# --------------------------------------------------------------------------
# AI facts stay as they were
# --------------------------------------------------------------------------


def _keys(obj) -> list[str]:
    if isinstance(obj, dict):
        return list(obj) + [k for v in obj.values() for k in _keys(v)]
    if isinstance(obj, list):
        return [k for v in obj for k in _keys(v)]
    return []


def test_new_fields_never_reach_the_ai(samples: Settings) -> None:
    """Holdings facts pick their fields, so gain on cost and the account's note
    and dormant flag stay out. Contribution facts keep their old keys."""
    note = "Gift money only. Keep it in the age based fund."
    c = connect(samples.db_path)
    try:
        with write_transaction(c):
            c.execute(
                "UPDATE accounts SET notes = ?, dormant = 1 WHERE account_key = 'kids-529'",
                (note,),
            )
        ensure_facts(samples.db_path, None)
        facts = {
            (r["kind"], r["key"]): json.loads(r["json"])
            for r in c.execute("SELECT kind, key, json FROM ai_facts")
        }
    finally:
        c.close()
    blob = json.dumps(list(facts.values()))
    assert note not in blob
    holdings = [v for (kind, _), v in facts.items() if kind == "holding"]
    assert any(h.get("account_name") == "Kids 529 (Bluestem)" for h in holdings)
    new = {"cost_basis_cents", "gain_cents", "gain_pct", "gain_share_pct", "dormant", "notes"}
    assert not new & set(_keys(holdings))
    others = [o for v in facts.values() if isinstance(v, dict) for o in v.get("other", [])]
    assert others, "the kids 529 shows in the contribution facts"
    assert all(
        set(o) == {"person", "account", "kind", "ytd_cents", "count", "last_date"} for o in others
    )
