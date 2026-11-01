"""Month coverage, complete months and the statement grid (PLAN 3.9, 8.2 Imports).

Coverage:
- An account is covered from its first current statement's start through the
  end of the last statement in the first gap-free run of its statements. Two
  statements are gap-free when the next one starts on or before the day after
  the previous one ends.
- A closed account whose statements have no gap is covered through its
  `closed_on` too: the last statement before closing is its final one.
- Cards tracked by payments are covered by their paying account, so they are
  never needed.

Complete months: a month M is complete when every needed account is covered
from the start of M (or the day it opened) through the end of M (or the day
it closed), and no needed account has an unreconciled statement that overlaps
M. Needed accounts: `in_cashflow`, `tracked_by` statements, open during M. An
inactive or dormant account with no `closed_on` is needed only up to its last
statement (the grid shows the months after it as not expected).
The current month is never complete. A month with no needed account is not
complete either.

Statement grid (account x month), each cell is one of:
- present: a current statement ends in that month
- missing: the cadence expects one, and it is more than 15 days overdue
- not_expected: before the account opened, after it closed, not a statement
  month for its cadence (quarterly), cadence `report` or `none`, or not due yet
- tracked: the account is tracked by payments and no statement ends that month
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.networth import today

HISTORY_START = "2026-01"  # PLAN 14.4 item 4: history starts Jan 2026
DUE_GRACE_DAYS = 15  # a statement is missing this long after its expected close
QUARTER_DEFAULT_MONTHS = (3, 6, 9, 12)


# --------------------------------------------------------------------------
# Month helpers
# --------------------------------------------------------------------------


def month_of(d: date | str) -> str:
    return (d.isoformat() if isinstance(d, date) else d)[:7]


def month_start(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


def month_end(month: str) -> date:
    y, m = int(month[:4]), int(month[5:7])
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


def add_months(month: str, n: int) -> str:
    total = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def months_between(start: str, end: str) -> list[str]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = add_months(m, 1)
    return out


def month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def current_month() -> str:
    return month_of(today())


# --------------------------------------------------------------------------
# Account coverage
# --------------------------------------------------------------------------


@dataclass
class Coverage:
    account_key: str
    covered_from: str | None = None
    covered_through: str | None = None
    gap_after: str | None = None  # period_end where the first gap starts
    unreconciled: list[tuple[str, str]] = field(default_factory=list)


def _accounts(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        r["account_key"]: r
        for r in conn.execute(
            """SELECT * FROM accounts
               ORDER BY account_group, display_order IS NULL, display_order, display_name"""
        )
    }


def _statements(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(
        """SELECT account_key, statement_key, period_start, period_end, reconciled, txn_count,
                  min_due_cents, due_date
           FROM statements WHERE is_current = 1
           ORDER BY account_key, period_start, period_end"""
    ):
        out.setdefault(r["account_key"], []).append(r)
    return out


def coverage_of(account: sqlite3.Row, statements: list[sqlite3.Row]) -> Coverage:
    cov = Coverage(account["account_key"])
    if not statements:
        return cov
    cov.covered_from = statements[0]["period_start"]
    end = statements[0]["period_end"]
    gap = False
    for s in statements[1:]:
        next_day = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        if s["period_start"] > next_day:
            cov.gap_after = end
            gap = True
            break
        end = max(end, s["period_end"])
    cov.covered_through = end
    if not gap and account["closed_on"] and account["closed_on"] > end:
        cov.covered_through = account["closed_on"]
    cov.unreconciled = [
        (s["period_start"], s["period_end"]) for s in statements if not s["reconciled"]
    ]
    return cov


def _needed_window(
    account: sqlite3.Row, month: str, last_end: str | None
) -> tuple[str, str] | None:
    """(from, through) dates the account must be covered for in `month`, or None."""
    if not account["in_cashflow"] or account["tracked_by"] != "statements":
        return None
    start, end = month_start(month).isoformat(), month_end(month).isoformat()
    opened, closed = account["opened_on"], account["closed_on"]
    if opened and opened > end:
        return None
    if closed and closed < start:
        return None
    if (not account["active"] or account["dormant"]) and not closed:
        if last_end is None or last_end < start:
            return None
        closed = min(last_end, end)
    return max(start, opened or start), min(end, closed or end)


@dataclass
class CoverageState:
    accounts: dict[str, sqlite3.Row]
    statements: dict[str, list[sqlite3.Row]]
    coverage: dict[str, Coverage]

    def month_complete(self, month: str) -> bool:
        if month >= current_month():
            return False
        needed = 0
        for key, a in self.accounts.items():
            stmts = self.statements.get(key, [])
            window = _needed_window(a, month, stmts[-1]["period_end"] if stmts else None)
            if window is None:
                continue
            needed += 1
            cov = self.coverage[key]
            if cov.covered_from is None or cov.covered_through is None:
                return False
            if cov.covered_from > window[0] or cov.covered_through < window[1]:
                return False
            start, end = month_start(month).isoformat(), month_end(month).isoformat()
            if any(s <= end and e >= start for s, e in cov.unreconciled):
                return False
        return needed > 0

    def complete_months(self, months: list[str]) -> dict[str, bool]:
        return {m: self.month_complete(m) for m in months}

    def latest_complete_month(self) -> str | None:
        m = add_months(current_month(), -1)
        while m >= HISTORY_START:
            if self.month_complete(m):
                return m
            m = add_months(m, -1)
        return None

    def covered_through(self, account_key: str) -> str | None:
        cov = self.coverage.get(account_key)
        return cov.covered_through if cov else None


def coverage_state(conn: sqlite3.Connection) -> CoverageState:
    accounts = _accounts(conn)
    statements = _statements(conn)
    coverage = {k: coverage_of(a, statements.get(k, [])) for k, a in accounts.items()}
    return CoverageState(accounts, statements, coverage)


# --------------------------------------------------------------------------
# Statement grid
# --------------------------------------------------------------------------


def _close_day(account: sqlite3.Row, statements: list[sqlite3.Row]) -> int:
    """The day of month a statement closes: cycle_close_day, else the newest
    statement's end day (31 when it ends on a month's last day)."""
    if account["cycle_close_day"]:
        return int(account["cycle_close_day"])
    if statements:
        end = date.fromisoformat(statements[-1]["period_end"])
        return 31 if end == month_end(month_of(end)) else end.day
    return 31


def expected_close(account: sqlite3.Row, statements: list[sqlite3.Row], month: str) -> date:
    day = _close_day(account, statements)
    last = month_end(month)
    return last if day >= last.day else date(last.year, last.month, day)


def _expected(account: sqlite3.Row, statements: list[sqlite3.Row], month: str) -> bool:
    """Does the cadence expect a statement to end in `month` (ignoring due dates)?"""
    cadence = account["statement_cadence"]
    if cadence == "monthly":
        return True
    if cadence == "quarterly":
        if statements:
            anchor = month_index(month_of(statements[-1]["period_end"]))
            return (month_index(month) - anchor) % 3 == 0
        return int(month[5:7]) in QUARTER_DEFAULT_MONTHS
    return False


def cell_status(
    account: sqlite3.Row, statements: list[sqlite3.Row], month: str, on: date
) -> tuple[str, sqlite3.Row | None]:
    ending = [s for s in statements if month_of(s["period_end"]) == month]
    if ending:
        return "present", ending[-1]
    if account["tracked_by"] == "payments":
        return "tracked", None
    start, end = month_start(month).isoformat(), month_end(month).isoformat()
    if account["opened_on"] and month_of(account["opened_on"]) > month:
        return "not_expected", None
    closed = account["closed_on"]
    if closed and closed < start:
        return "not_expected", None
    if (not account["active"] or account["dormant"]) and not closed:
        last = statements[-1]["period_end"] if statements else None
        if last is None or last < start:
            return "not_expected", None
    if not _expected(account, statements, month):
        return "not_expected", None
    close = expected_close(account, statements, month)
    if closed and closed < close.isoformat() and closed <= end:
        return "not_expected", None  # closed before this month's close
    if close + timedelta(days=DUE_GRACE_DAYS) > on:
        return "not_expected", None  # not due yet
    return "missing", None


def grid(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> dict:
    state = coverage_state(conn)
    on = today()
    end = end or current_month()
    start = start or HISTORY_START
    months = months_between(start, end)
    names = {r["person_key"]: r["display_name"] for r in conn.execute("SELECT * FROM people")}
    rows = []
    for key, a in state.accounts.items():
        stmts = state.statements.get(key, [])
        cells = []
        for m in months:
            status, s = cell_status(a, stmts, m, on)
            cells.append(
                {
                    "month": m,
                    "status": status,
                    "period_end": s["period_end"] if s else None,
                    "reconciled": bool(s["reconciled"]) if s else None,
                }
            )
        cov = state.coverage[key]
        rows.append(
            {
                "account_key": key,
                "name": a["display_name"],
                "institution": a["institution"],
                "owner_label": names.get(a["owner_person_key"], a["owner_person_key"]),
                "type": a["type"],
                "statement_cadence": a["statement_cadence"],
                "tracked_by": a["tracked_by"],
                "in_cashflow": bool(a["in_cashflow"]),
                "active": bool(a["active"]) and not a["closed_on"],
                "covered_from": cov.covered_from,
                "covered_through": cov.covered_through,
                "gap_after": cov.gap_after,
                "cells": cells,
            }
        )
    complete = state.complete_months(months)
    return {
        "months": months,
        "accounts": rows,
        "complete_months": [m for m in months if complete[m]],
        "latest_complete_month": state.latest_complete_month(),
        "today": on.isoformat(),
    }


def missing_cells(conn: sqlite3.Connection) -> list[dict]:
    """Every missing statement from HISTORY_START to this month, oldest first."""
    out = []
    g = grid(conn)
    for a in g["accounts"]:
        for c in a["cells"]:
            if c["status"] == "missing":
                out.append(
                    {"account_key": a["account_key"], "name": a["name"], "month": c["month"]}
                )
    out.sort(key=lambda x: (x["month"], x["name"]))
    return out
