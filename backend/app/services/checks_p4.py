"""Phase 4 additions to Checks (PLAN 3.9, 8.2 Checks).

- Monthly cash flow: the app's income, spending and saved and invested next
  to the chat's `cashflow_actual`, per group too, with what explains a gap.
  Phase 7 moved fuel (Lifestyle) and vehicle registration (Insurance) into
  Transportation. Each month whose `cashflow_actual` has no `transportation`
  group compares those three groups as one line, with a note saying why.
  Older months show no false gaps even in a run with newer groups too.
- The plan mapping: which measure each plan group is compared with.
- Unpaired transfer legs: a row marked as a transfer to another household
  account whose current statements (with rows) cover that date, and no row on
  that account with the opposite amount within 7 days. Legs whose other side
  is not imported yet are skipped.
- Payments to statement-tracked cards with no statement (flow card_no_stmt).
- Missing statements (the statement grid's missing cells).
- Payment-basis watch for cards tracked by payments. Each card is read only
  through the days its paying accounts' statements cover:
  - min-payments: autopay `minimum`, and every payment in two months in a
    row is minimum-size (the card's min_payment, else 40.00 or less).
  - no-payment-45d: autopay `minimum`, and no payment for 45 days or more.
  - no-payment-month: a card that had a payment or statement in each of the
    3 months before has neither in a month.
  - dormant-payment: any payment to a card that is dormant, inactive or closed.

Notes and messages hold account names and months, never amounts.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app.importer.models import CashflowActual, to_cents
from app.services import cashflow as cf
from app.services.coverage import (
    HISTORY_START,
    CoverageState,
    add_months,
    month_end,
    month_of,
    months_between,
)

PAIR_DAYS = 7
DEFAULT_MIN_PAYMENT_CENTS = 4000
NO_PAYMENT_DAYS = 45
REGULAR_MONTHS = 3
MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


def month_label(month: str) -> str:
    return f"{MONTH_NAMES[int(month[5:7]) - 1]} {month[:4]}"


def day_label(day: str) -> str:
    return f"{MONTH_NAMES[int(day[5:7]) - 1]} {int(day[8:10])}"


def date_label(day: str) -> str:
    """Like "Sep 30, 2026"."""
    return f"{day_label(day)}, {day[:4]}"


def line(app: int | None, chat: int | None) -> dict:
    from app.services.checks import line as base

    return base(app, chat)


# --------------------------------------------------------------------------
# Monthly cash flow
# --------------------------------------------------------------------------


def _edited_out(conn: sqlite3.Connection, month: str) -> int:
    row = conn.execute(
        """SELECT count(*) FROM v_transactions v JOIN accounts a ON a.account_key = v.account_key
           WHERE a.in_cashflow = 1 AND v.pre_history = 0 AND substr(v.txn_date, 1, 7) = ?
             AND (v.hidden = 1 OR (COALESCE(v.exclude_from_budget, 0) = 1
                                   AND COALESCE(v.imported_exclude_from_budget, 0) = 0))""",
        (month,),
    ).fetchone()
    return int(row[0])


def _short_accounts(state: CoverageState, month: str) -> list[str]:
    """Names of needed accounts whose statements stop before the month ends."""
    out = []
    end = month_end(month).isoformat()
    for key, a in state.accounts.items():
        if not a["in_cashflow"] or a["tracked_by"] != "statements":
            continue
        if a["closed_on"] and a["closed_on"] < month + "-01":
            continue
        if a["opened_on"] and a["opened_on"] > end:
            continue
        through = state.covered_through(key)
        if through is None or through < end:
            when = f"reach only {day_label(through)}" if through else "are not in"
            out.append(f"{a['display_name']} statements {when}.")
    return out


FOLDED_GROUPS = ("lifestyle", "insurance", "transportation")
FOLDED_KEY = "lifestyle-insurance-transportation"
# Short, so it fits one line on a phone. The month's note says why.
FOLDED_LABEL = "Lifestyle, Insurance, Transportation"


def _fold(groups: dict[str, int]) -> dict[str, int]:
    """The three groups as one FOLDED_KEY entry, when any of them is there."""
    if not any(k in groups for k in FOLDED_GROUPS):
        return groups
    out = {k: v for k, v in groups.items() if k not in FOLDED_GROUPS}
    out[FOLDED_KEY] = sum(groups.get(k, 0) for k in FOLDED_GROUPS)
    return out


def cashflow_checks(
    conn: sqlite3.Connection, chat_months: list[CashflowActual], state: CoverageState
) -> list[dict]:
    if not chat_months:
        return []
    lab = cf.labels(conn)
    months = sorted(c.month for c in chat_months)
    acts = cf.actuals(
        cf.load_rows(conn, months[0], months[-1]), months_between(months[0], months[-1])
    )
    out = []
    for c in sorted(chat_months, key=lambda c: c.month):
        a = acts[c.month]
        complete = state.month_complete(c.month)
        chat_groups = {g.key: to_cents(g.amount) for g in c.by_group}
        app_groups = {k: v for k, v in a.by_group.items() if v}
        if "transportation" not in chat_groups:
            chat_groups, app_groups = _fold(chat_groups), _fold(app_groups)
        keys = sorted(set(chat_groups) | set(app_groups))
        groups = [
            {
                "key": k,
                "label": FOLDED_LABEL if k == FOLDED_KEY else lab.group(k),
                **line(app_groups.get(k, 0), chat_groups.get(k)),
            }
            for k in keys
        ]
        notes = []
        name = month_label(c.month)
        if FOLDED_KEY in chat_groups or FOLDED_KEY in app_groups:
            notes.append(
                f"{name}: groups changed in 1.3. This month's actual groups have no "
                "Transportation, so Lifestyle, Insurance and Transportation are compared "
                "as one line."
            )
        if not complete:
            notes.append(f"{name} is not complete in the app.")
            notes += _short_accounts(state, c.month)
        if a.payment_basis:
            notes.append(f"{name} counts card payments on a payment basis.")
        if a.card_no_statement:
            notes.append(f"{name} counts card payments with no statement as spending.")
        if a.uncategorized:
            notes.append(f"{name} has uncategorized rows. The app counts them as spending.")
        if a.reimbursable:
            notes.append(f"{name} has charges waiting on reimbursement. They are left out.")
        edited = _edited_out(conn, c.month)
        if edited:
            notes.append(f"{name} has {edited} rows hidden or left out by an edit in the app.")
        out.append(
            {
                "month": c.month,
                "complete": complete,
                "income": line(a.income, to_cents(c.income)),
                "spending": line(a.spending, to_cents(c.spending)),
                "saved_invested": line(
                    a.saved, to_cents(c.saved_invested) if c.saved_invested is not None else None
                ),
                "by_group": groups,
                "payment_basis_cents": a.payment_basis,
                "card_no_statement_cents": a.card_no_statement,
                "uncategorized_cents": a.uncategorized,
                "reimbursable_cents": a.reimbursable,
                "edited_out_count": edited,
                "notes": notes,
            }
        )
    return out


# --------------------------------------------------------------------------
# Transfers and card payments
# --------------------------------------------------------------------------


def unpaired_transfers(conn: sqlite3.Connection, state: CoverageState) -> list[dict]:
    rows = conn.execute(
        """SELECT txn_key, txn_date, account_key, counterparty_account_key, amount_cents,
                  is_transfer, description
           FROM v_transactions WHERE pre_history = 0 ORDER BY txn_date, txn_key"""
    ).fetchall()
    by_account: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_account.setdefault(r["account_key"], []).append(r)
    with_rows = {
        k: [(s["period_start"], s["period_end"]) for s in stmts if s["txn_count"] > 0]
        for k, stmts in state.statements.items()
    }
    used: set[str] = set()
    out = []
    for leg in rows:
        other = leg["counterparty_account_key"]
        if not leg["is_transfer"] or other is None or leg["txn_key"] in used:
            continue
        if other not in state.accounts or other == leg["account_key"]:
            continue
        d = leg["txn_date"]
        if not any(s <= d <= e for s, e in with_rows.get(other, [])):
            continue
        day = date.fromisoformat(d)
        best = None
        for r in by_account.get(other, []):
            if r["txn_key"] in used or r["amount_cents"] != -leg["amount_cents"]:
                continue
            if r["counterparty_account_key"] not in (None, leg["account_key"]):
                continue
            gap = abs((date.fromisoformat(r["txn_date"]) - day).days)
            if gap <= PAIR_DAYS and (best is None or gap < best[0]):
                best = (gap, r)
        if best is not None:
            used.update((leg["txn_key"], best[1]["txn_key"]))
            continue
        out.append(
            {
                "txn_key": leg["txn_key"],
                "txn_date": d,
                "account_key": leg["account_key"],
                "account_name": state.accounts[leg["account_key"]]["display_name"],
                "counterparty_account_key": other,
                "counterparty_name": state.accounts[other]["display_name"],
                "amount_cents": leg["amount_cents"],
            }
        )
    return out


def card_payments_no_statement(conn: sqlite3.Connection, state: CoverageState) -> list[dict]:
    return [
        {
            "account_key": r["card"],
            "account_name": state.accounts[r["card"]]["display_name"]
            if r["card"] in state.accounts
            else r["card"],
            "month": r["month"],
            "count": r["n"],
            "cents": r["cents"],
        }
        for r in conn.execute(
            """SELECT counterparty_account_key AS card, month, count(*) AS n,
                      sum(out_cents) AS cents
               FROM v_cashflow_rows WHERE flow = 'card_no_stmt'
               GROUP BY counterparty_account_key, month ORDER BY month, card"""
        )
    ]


# --------------------------------------------------------------------------
# Payment-basis watch
# --------------------------------------------------------------------------


def _watch(rule: str, severity: str, a: sqlite3.Row, month: str | None, message: str) -> dict:
    return {
        "rule": rule,
        "severity": severity,
        "account_key": a["account_key"],
        "account_name": a["display_name"],
        "month": month,
        "message": message,
    }


def payment_basis_watch(conn: sqlite3.Connection, state: CoverageState) -> list[dict]:
    out: list[dict] = []
    cash_accounts = [
        k
        for k, a in state.accounts.items()
        if a["type"] in ("checking", "savings") and a["in_cashflow"]
    ]
    for key, a in state.accounts.items():
        if a["tracked_by"] != "payments":
            continue
        pays = conn.execute(
            """SELECT txn_date, amount_cents, account_key FROM v_transactions
               WHERE counterparty_account_key = ? AND amount_cents < 0 AND txn_type = 'payment'
               ORDER BY txn_date, txn_key""",
            (key,),
        ).fetchall()
        name = a["display_name"]
        dormant = bool(a["dormant"]) or not a["active"] or a["closed_on"] is not None
        if dormant:
            for m in sorted({month_of(p["txn_date"]) for p in pays}):
                out.append(
                    _watch(
                        "dormant-payment",
                        "high",
                        a,
                        m,
                        f"A payment went to {name} in {month_label(m)}. This card is not in use.",
                    )
                )
            continue
        payers = sorted({p["account_key"] for p in pays}) or cash_accounts
        throughs = [state.covered_through(k) for k in payers]
        if not throughs or any(t is None for t in throughs):
            continue
        seen = min(t for t in throughs if t is not None)
        first = max(HISTORY_START, month_of(a["opened_on"]) if a["opened_on"] else HISTORY_START)
        last = month_of(seen) if month_end(month_of(seen)).isoformat() == seen else None
        last = last or add_months(month_of(seen), -1)
        months = months_between(first, last) if first <= last else []
        by_month: dict[str, list[int]] = {}
        for p in pays:
            if p["txn_date"] <= seen:
                by_month.setdefault(month_of(p["txn_date"]), []).append(-p["amount_cents"])
        stmts = state.statements.get(key, [])

        def has_statement(m: str, _stmts: list = stmts) -> bool:
            s, e = m + "-01", month_end(m).isoformat()
            return any(x["period_start"] <= e and x["period_end"] >= s for x in _stmts)

        if a["autopay"] == "minimum":
            limit = a["min_payment_cents"] or DEFAULT_MIN_PAYMENT_CENTS
            small = [m for m in months if by_month.get(m) and max(by_month[m]) <= limit]
            for m in small:
                if add_months(m, -1) in small:
                    out.append(
                        _watch(
                            "min-payments",
                            "medium",
                            a,
                            m,
                            f"{name} had only minimum-size payments in "
                            f"{month_label(add_months(m, -1))} and {month_label(m)}.",
                        )
                    )
            last_pay = max((p["txn_date"] for p in pays if p["txn_date"] <= seen), default=None)
            since = last_pay or (a["opened_on"] if a["opened_on"] else HISTORY_START + "-01")
            gap = date.fromisoformat(seen) - date.fromisoformat(max(since, HISTORY_START + "-01"))
            if gap.days >= NO_PAYMENT_DAYS:
                when = f"since {day_label(last_pay)}" if last_pay else "at all"
                out.append(
                    _watch(
                        "no-payment-45d",
                        "high",
                        a,
                        None,
                        f"No payment to {name} {when}, through {day_label(seen)}.",
                    )
                )
        for m in months:
            if by_month.get(m) or has_statement(m):
                continue
            before = [add_months(m, -i) for i in range(1, REGULAR_MONTHS + 1)]
            if all(b >= first and (by_month.get(b) or has_statement(b)) for b in before):
                out.append(
                    _watch(
                        "no-payment-month",
                        "medium",
                        a,
                        m,
                        f"No payment to {name} and no statement in {month_label(m)}.",
                    )
                )
    out.sort(key=lambda w: (w["account_name"], w["month"] or "", w["rule"]))
    return out
