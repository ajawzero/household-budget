"""Money in, market change, fees and dividends per investment account (gap audit A5, A6, 54).

Everything comes from current statements: their stored opening, net_flows,
market_change and fees, and their dividend rows. Two periods per account:
- latest: the newest current statement ending on or before household today
  ("this month" is the newest statement, never the calendar month).
- ytd: every current statement whose period_end is in the household year (the
  coverage rule, month_of(period_end)). A statement that crosses Jan 1 counts in
  full, and period_start shows its real start.

Signs stay as stored: net_flows positive is money in, market_change positive is
a gain. Fees follow the format's sign rule (section 4, money out is negative),
so fees_paid is minus their sum: a fee taken shows positive, a refund lowers it.

Return is chained (time-weighted): the product of (opening + market) / opening
over the statements, minus 1. Deposits do not move it. It is None unless every
statement has the split and an opening above 0, and the statements run on
with no gap. `gaps` counts the breaks (a statement starting more than a day
after the one before ended), so a screen can say the totals are short.

Dividends are rows with txn_type 'dividend' that are not hidden. In a reinvest
pair the positive dividend row counts and the reinvest row does not. Interest
rows are not counted.

This module stands alone: networth.py imports it, so it never imports networth
or investments.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.importer.models import INVESTMENT_TYPES

DAYS_PER_MONTH = Decimal("365.25") / 12
NO_ORDER = 1_000_000  # accounts with no display_order sort last, as on the Accounts page


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _pct(ratio: Decimal) -> str:
    """A ratio as a percent string with one decimal, half up ("2.1")."""
    return str((ratio * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _months(row: sqlite3.Row) -> int:
    """Whole months a statement covers, at least 1: a month is 1, a quarter is 3."""
    days = (date.fromisoformat(row["period_end"]) - date.fromisoformat(row["period_start"])).days
    return max(1, _round(Decimal(days + 1) / DAYS_PER_MONTH))


def _is_split(row: sqlite3.Row) -> bool:
    """The statement splits money in from market change."""
    return row["net_flows_cents"] is not None and row["market_change_cents"] is not None


def _dividends(conn: sqlite3.Connection, keys: list[str], since: str | None) -> int | None:
    marks = ", ".join("?" for _ in keys)
    sql = f"""SELECT count(*) AS n, coalesce(sum(amount_cents), 0) AS cents FROM v_transactions
              WHERE statement_key IN ({marks}) AND txn_type = 'dividend' AND hidden = 0"""
    args: list[str] = list(keys)
    if since is not None:
        sql += " AND txn_date >= ?"
        args.append(since)
    r = conn.execute(sql, args).fetchone()
    return r["cents"] if r["n"] else None


def _gaps(rows: list[sqlite3.Row]) -> int:
    """Breaks between statements in order: one starts more than a day after the last ended."""
    ends = [date.fromisoformat(r["period_end"]) for r in rows]
    starts = [date.fromisoformat(r["period_start"]) for r in rows]
    return sum(1 for i in range(1, len(rows)) if (starts[i] - ends[i - 1]).days > 1)


def period(conn: sqlite3.Connection, rows: list[sqlite3.Row], since: str | None = None) -> dict:
    """One period's sums over `rows` (current statements, oldest first).
    `since` limits the dividend rows to that date and later (the ytd column)."""
    split = [r for r in rows if _is_split(r)]
    gaps = _gaps(rows)
    chained = None
    if not gaps and len(split) == len(rows) and all((r["opening_cents"] or 0) > 0 for r in rows):
        growth = Decimal(1)
        for r in rows:
            growth *= Decimal(r["opening_cents"] + r["market_change_cents"]) / r["opening_cents"]
        chained = _pct(growth - 1)
    fee_rows = [r for r in rows if r["fees_cents"] is not None]
    fees_paid = -sum(r["fees_cents"] for r in fee_rows) if fee_rows else None
    fee_months = sum(_months(r) for r in fee_rows)
    return {
        "period_start": min(r["period_start"] for r in rows),
        "period_end": max(r["period_end"] for r in rows),
        "statements": len(rows),
        "gaps": gaps,
        "split_statements": len(split),
        "net_flows_cents": sum(r["net_flows_cents"] for r in split) if split else None,
        "market_change_cents": sum(r["market_change_cents"] for r in split) if split else None,
        "return_pct": chained,
        "fees_paid_cents": fees_paid,
        "fee_statements": len(fee_rows),
        "fee_months": fee_months,
        "fees_per_month_cents": (
            _round(Decimal(fees_paid) / fee_months) if fees_paid is not None else None
        ),
        "dividends_cents": _dividends(conn, [r["statement_key"] for r in rows], since),
    }


def _has_numbers(p: dict | None) -> bool:
    if p is None:
        return False
    keys = ("net_flows_cents", "market_change_cents", "fees_paid_cents", "dividends_cents")
    return any(p[k] is not None for k in keys)


def investment_summary(conn: sqlite3.Connection, key: str, today: date) -> dict | None:
    """The account drawer's money in and market panel. None for a non-investment
    account, one with no statements, or statements with no numbers to show."""
    a = conn.execute("SELECT type FROM accounts WHERE account_key = ?", (key,)).fetchone()
    if a is None or a["type"] not in INVESTMENT_TYPES:
        return None
    rows = conn.execute(
        """SELECT statement_key, period_start, period_end, opening_cents, net_flows_cents,
                  market_change_cents, fees_cents
           FROM statements WHERE account_key = ? AND is_current = 1 AND period_end <= ?
           ORDER BY period_end, period_start""",
        (key, today.isoformat()),
    ).fetchall()
    if not rows:
        return None
    jan1 = f"{today.year}-01-01"
    this_year = [r for r in rows if r["period_end"] >= jan1]
    latest = period(conn, [rows[-1]])
    ytd = period(conn, this_year, since=jan1) if this_year else None
    if not (_has_numbers(latest) or _has_numbers(ytd)):
        return None
    return {"year": today.year, "latest": latest, "ytd": ytd}


def flows_page(conn: sqlite3.Connection, today: date) -> dict:
    """GET /api/investments/flows: each investment account with statements this year.
    Accounts left out of net worth are listed too: the numbers come from statements."""
    out = []
    for a in conn.execute(
        """SELECT account_key, display_name, type, closed_on FROM accounts
           WHERE tracked_by = 'statements'
           ORDER BY account_group, coalesce(display_order, ?), display_name""",
        (NO_ORDER,),
    ):
        if a["type"] not in INVESTMENT_TYPES:
            continue
        summary = investment_summary(conn, a["account_key"], today)
        if summary is None or summary["ytd"] is None:
            continue
        out.append(
            {
                "account_key": a["account_key"],
                "display_name": a["display_name"],
                "closed": a["closed_on"] is not None and a["closed_on"] <= today.isoformat(),
                **summary,
            }
        )
    return {"as_of": today.isoformat(), "year": today.year, "accounts": out}
