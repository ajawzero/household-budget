"""Installment plans, debt and the chat's lists (PLAN 3.7, 3.9, 8.2).

Plans (`installment_plans`, format 1.1):
- "N of M": payments made = payments_total - payments_left.
- Monthly cost: monthly_payment + monthly_fee.
- Step-downs: from this month to the month after the last end month, the
  monthly cost of every active plan whose end_month is on or after that
  month. A plan with no end_month is listed under `open_ended` and left out.
- Total deferred: the sum of `remaining` over active plans (information
  only: never net worth). Plans with no `remaining` are listed apart.
- Shortest and longest: the active plans with the earliest and latest
  end_month.
- Cards: those with an active plan first, then by name. A card's remaining
  and monthly totals count active plans only.
- Warnings: deferred interest `pay_before` and 0% `promo_expiry` dates.
  high within 45 days, medium within 90, else low. Past dates are high.
- `billed_count` counts the plan's installment rows on the card (adjustment
  rows with money out). The UI shows it next to "N of M" as a check.
- Note and paid off (format 1.4): `note` as the chat wrote it. `paid_off_on`
  is set only on a paid off or closed plan; `paid_off_early` when its month is
  before `end_month`.
- `refund_cents`: refund rows (money back) that carry the plan's key, on any
  account, hidden rows left out. Information only: cash flow already counts them.

Debt: liability accounts in net worth with money owed today, ordered by
priority (unset last), then rate (highest first). Required = min_payment,
else the newest statement's minimum due. Paying = payments into the account
from household accounts in the latest complete month.
- Interest = -sum of the account's own `interest` rows (hidden rows left
  out, refunds net), in the latest complete month and from Jan 1 to today.
  Each is None until a statement ends in that month (or this year), so a
  missing statement is never a false $0.
- Statement = the newest statement: its end, its closing balance as owed
  (positive) and its due date.
- Rate tiles (Highest rate, Cheapest): rated debts that carry their rate.
  Cards on statement or full autopay are paid in full, and a promo rate is
  not the rate that sticks, so both are left out. Loans, the mortgage and a
  HELOC keep theirs on any autopay.
- Projection: the pace, payoff month and dates to beat for each debt that
  is not a card paid in full (services/payoff.py).
- Original (format 1.4, loans and mortgages): paid down = (original - owed) /
  original, None when more is owed than the original. Rate changes: the
  variable-rate schedule that counts for the projection (services/payoff.py).

Flags, to do and pending items are read-only (PLAN 14.4 item 14).
- Flags (format 1.4): a flag with no action reads as act. Info flags not
  resolved come after the others, are never overdue and sit in their own
  count, out of open, watching and severity. Updates newest first.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app.importer.models import Flag, InstallmentPlan, to_cents
from app.services.balances import history
from app.services.coverage import (
    add_months,
    coverage_state,
    current_month,
    expected_close,
    month_of,
    months_between,
)
from app.services.networth import humanize, pct, today
from app.services.payoff import projections, scheduled_rates
from app.services.trips import trip_names
from app.state import readers

KIND_LABELS = {
    "plan_it": "Plan It",
    "pay_over_time": "Pay Over Time",
    "promo_0pct": "0% promo",
    "deferred_interest": "Deferred interest",
    "apple_installment": "Installments",
}
WARN_HIGH_DAYS = 45
WARN_MEDIUM_DAYS = 90
CARD_TYPES = ("credit_card", "store_card")
PAID_IN_FULL = ("full", "statement")


def _cents(value: str | None) -> int | None:
    return to_cents(value) if value is not None else None


def monthly_cost(p: InstallmentPlan) -> int:
    return (_cents(p.monthly_payment) or 0) + (_cents(p.monthly_fee) or 0)


def warning_for(p: InstallmentPlan, on: date) -> dict | None:
    if p.status != "active":
        return None
    if p.kind == "deferred_interest" and p.pay_before:
        kind, when = "pay_before", p.pay_before
    elif p.kind == "promo_0pct" and p.promo_expiry:
        kind, when = "promo_expiry", p.promo_expiry
    else:
        return None
    days = (date.fromisoformat(when) - on).days
    level = "high" if days <= WARN_HIGH_DAYS else "medium" if days <= WARN_MEDIUM_DAYS else "low"
    return {"kind": kind, "date": when, "days_left": days, "level": level}


def _billed(conn: sqlite3.Connection) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT plan_key, txn_type, amount_cents, hidden FROM v_transactions
           WHERE plan_key IS NOT NULL"""
    ):
        e = out.setdefault(r["plan_key"], {"billed": 0, "fees": 0, "refunds": 0})
        if r["txn_type"] == "adjustment" and r["amount_cents"] < 0:
            e["billed"] += 1
        if r["txn_type"] == "plan_fee":
            e["fees"] += -r["amount_cents"]
        # A refund row the skill tagged with the plan's key (format 1.4 section 3.3).
        if r["txn_type"] == "refund" and r["amount_cents"] > 0 and not r["hidden"]:
            e["refunds"] += r["amount_cents"]
    return out


def plan_out(
    p: InstallmentPlan,
    accounts: dict[str, str],
    billed: dict,
    on: date,
    trips: dict[str, str] | None = None,
) -> dict:
    made = (
        p.payments_total - p.payments_left
        if p.payments_total is not None and p.payments_left is not None
        else None
    )
    b = billed.get(p.key, {"billed": 0, "fees": 0, "refunds": 0})
    return {
        "key": p.key,
        "name": p.name,
        "kind": p.kind,
        "kind_label": KIND_LABELS[p.kind],
        "status": p.status,
        "account_key": p.account_key,
        "account_name": accounts.get(p.account_key, p.account_key),
        "created_on": p.created_on,
        "original_cents": to_cents(p.original),
        "monthly_payment_cents": _cents(p.monthly_payment),
        "monthly_fee_cents": _cents(p.monthly_fee),
        "monthly_cost_cents": monthly_cost(p),
        "total_fees_cents": _cents(p.total_fees),
        "payments_total": p.payments_total,
        "payments_left": p.payments_left,
        "payments_made": made,
        "progress_label": f"{made} of {p.payments_total}" if made is not None else None,
        "remaining_cents": _cents(p.remaining),
        "end_month": p.end_month,
        "promo_expiry": p.promo_expiry,
        "pay_before": p.pay_before,
        "included_in_spending_plan": bool(p.included_in_spending_plan),
        "components": [
            {
                "merchant": c.merchant,
                "amount_cents": to_cents(c.amount),
                "trip_key": c.trip_key,
                "trip_name": (trips or {}).get(c.trip_key) if c.trip_key else None,
            }
            for c in p.components or []
        ],
        "billed_count": b["billed"],
        "fees_posted_cents": b["fees"],
        "warning": warning_for(p, on),
        "note": p.note,
        "paid_off_on": p.paid_off_on,
        "paid_off_early": bool(p.paid_off_on and p.end_month and p.paid_off_on[:7] < p.end_month),
        "refund_cents": b["refunds"],
    }


def step_downs(plans: list[InstallmentPlan], start: str) -> list[dict]:
    active = [p for p in plans if p.status == "active" and p.end_month is not None]
    if not active:
        return []
    last = max(p.end_month for p in active if p.end_month)
    out = []
    prev = None
    for m in months_between(start, add_months(max(last, start), 1)):
        running = [p for p in active if p.end_month and p.end_month >= m]
        cents = sum(monthly_cost(p) for p in running)
        ending = sorted(p.key for p in active if p.end_month == add_months(m, -1))
        out.append(
            {
                "month": m,
                "monthly_cents": cents,
                "drop_cents": (prev - cents) if prev is not None and prev > cents else 0,
                "ended": ending if prev is not None else [],
                "plan_count": len(running),
            }
        )
        prev = cents
    return out


def _left(r: dict) -> int:
    """payments_left for ordering; unknown sorts after every known count."""
    return r["payments_left"] if r["payments_left"] is not None else 10**6


def plans_page(conn: sqlite3.Connection) -> dict:
    runs = readers.monthly_runs(conn)
    plans = readers.installment_plans(runs)
    accounts = {r["account_key"]: r["display_name"] for r in conn.execute("SELECT * FROM accounts")}
    on = today()
    billed = _billed(conn)
    names = trip_names(conn)
    rows = [plan_out(p, accounts, billed, on, names) for p in plans]
    active = [r for r in rows if r["status"] == "active"]
    cards = []
    live = {r["account_key"] for r in active}
    # Cards with no active plan (all paid off or closed) go last, then by name.
    for key in sorted(
        {r["account_key"] for r in rows}, key=lambda k: (k not in live, accounts.get(k, k))
    ):
        mine = [r for r in rows if r["account_key"] == key]
        mine.sort(key=lambda r: (r["status"] != "active", r["end_month"] or "9999-99", r["key"]))
        cards.append(
            {
                "account_key": key,
                "account_name": accounts.get(key, key),
                "plans": mine,
                "remaining_cents": sum(r["remaining_cents"] or 0 for r in mine if r in active),
                "monthly_cents": sum(r["monthly_cost_cents"] for r in mine if r in active),
            }
        )
    ended = [r for r in active if r["end_month"]]
    by_end = sorted(ended, key=lambda r: (r["end_month"], _left(r), r["key"]))

    def brief(r: dict | None) -> dict | None:
        if r is None:
            return None
        return {
            "key": r["key"],
            "name": r["name"],
            "end_month": r["end_month"],
            "payments_left": r["payments_left"],
        }

    warnings = sorted(
        (
            {"plan_key": r["key"], "name": r["name"], "account_name": r["account_name"], **w}
            for r in rows
            if (w := r["warning"]) is not None
        ),
        key=lambda w: (w["date"], w["plan_key"]),
    )
    return {
        "source_run_id": readers.section_source(runs, "installment_plans"),
        "today": on.isoformat(),
        "active_count": len(active),
        "total_deferred_cents": sum(r["remaining_cents"] or 0 for r in active),
        "remaining_unknown": [r["key"] for r in active if r["remaining_cents"] is None],
        "monthly_total_cents": sum(r["monthly_cost_cents"] for r in active),
        "shortest": brief(by_end[0] if by_end else None),
        "longest": brief(
            max(ended, key=lambda r: (r["end_month"], -_left(r), r["key"])) if ended else None
        ),
        "open_ended": [r["key"] for r in active if not r["end_month"]],
        "cards": cards,
        "step_downs": step_downs(plans, current_month()),
        "warnings": warnings,
    }


def upcoming_plan_payments(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """Plan payments from start to end. A plan bills on its card's close day."""
    runs = readers.monthly_runs(conn)
    accts = {r["account_key"]: r for r in conn.execute("SELECT * FROM accounts")}
    cov = coverage_state(conn)
    out = []
    for p in readers.installment_plans(runs):
        cost = monthly_cost(p)
        if p.status != "active" or cost == 0:
            continue
        a = accts.get(p.account_key)
        stmts = cov.statements.get(p.account_key, [])
        if a is None:
            continue
        for m in months_between(start.isoformat()[:7], end.isoformat()[:7]):
            if p.end_month is not None and m > p.end_month:
                continue
            when = expected_close(a, stmts, m)
            if start <= when <= end:
                out.append(
                    {
                        "date": when.isoformat(),
                        "kind": "plan",
                        "key": p.key,
                        "name": p.name,
                        "amount_cents": cost,
                        "account_name": a["display_name"],
                    }
                )
    return out


# --------------------------------------------------------------------------
# Debt
# --------------------------------------------------------------------------


def paid_in_full(d: dict) -> bool:
    """A card on statement or full autopay: paid in full each month."""
    return d["type"] in CARD_TYPES and d["autopay"] in PAID_IN_FULL


def carries_rate(d: dict) -> bool:
    """True when the rate is what the debt costs, so it counts for the rate tiles."""
    if d["apr_milli_pct"] is None or d["rate_type"] == "promo":
        return False
    return not paid_in_full(d)


def _newest_statements(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """The newest current statement per account: the one the minimum due uses."""
    out: dict[str, sqlite3.Row] = {}
    for r in conn.execute(
        """SELECT account_key, period_end, due_date, closing_cents
           FROM statements WHERE is_current = 1
           ORDER BY account_key, period_start, period_end"""
    ):
        out[r["account_key"]] = r
    return out


def _interest(conn: sqlite3.Connection, month: str | None, on: date) -> dict[str, tuple[int, int]]:
    """{account: (interest in month, interest from Jan 1 to on)}. Charges are positive."""
    out: dict[str, tuple[int, int]] = {}
    for r in conn.execute(
        """SELECT account_key AS k,
                  sum(CASE WHEN substr(txn_date, 1, 7) = :month
                      THEN -amount_cents ELSE 0 END) AS m,
                  sum(CASE WHEN txn_date BETWEEN :jan1 AND :today
                      THEN -amount_cents ELSE 0 END) AS y
           FROM v_transactions
           WHERE txn_type = 'interest' AND hidden = 0
           GROUP BY account_key""",
        {"month": month, "jan1": f"{on.year}-01-01", "today": on.isoformat()},
    ):
        out[r["k"]] = (r["m"], r["y"])
    return out


def debt_page(conn: sqlite3.Connection) -> dict:
    on = today()
    jan1 = f"{on.year}-01-01"
    h = history(conn, on, on)
    cov = coverage_state(conn)
    month = cov.latest_complete_month()
    runs = readers.monthly_runs(conn)
    plans = readers.installment_plans(runs)
    plan_left: dict[str, int] = {}
    for p in plans:
        if p.status == "active" and p.remaining:
            plan_left[p.account_key] = plan_left.get(p.account_key, 0) + to_cents(p.remaining)
    min_due = {}
    for key, stmts in cov.statements.items():
        if stmts:
            min_due[key] = stmts[-1]["min_due_cents"]
    paying: dict[str, int] = {}
    if month:
        for r in conn.execute(
            """SELECT counterparty_account_key AS k, sum(-amount_cents) AS paid
               FROM v_transactions
               WHERE counterparty_account_key IS NOT NULL AND amount_cents < 0
                 AND substr(txn_date, 1, 7) = ?
               GROUP BY counterparty_account_key""",
            (month,),
        ):
            paying[r["k"]] = r["paid"]
    interest = _interest(conn, month, on)
    newest = _newest_statements(conn)
    ledger = []
    balance_months: dict[str, str] = {}
    anchor_months: dict[str, str] = {}  # the real balance's month, under any estimate
    for key, a in h.accounts.items():
        if a["is_asset"] or a["tracked_by"] == "payments":
            continue
        if not a["include_in_net_worth"]:
            continue
        b = h.at_end(key)
        cents = b.balance_cents
        owed = -cents if cents is not None else None
        if owed is None or owed <= 0:
            continue
        # A computed loan estimate counts the payments through its last one.
        est = b.estimate
        balance_months[key] = month_of(est.through or est.based_on if est else b.as_of)
        anchor_months[key] = month_of(est.based_on if est else b.as_of)
        required = a["min_payment_cents"]
        if required is None:
            required = min_due.get(key)
        # Interest is known for a month (or the year) only once a statement ends in it.
        ends = [s["period_end"] for s in cov.statements.get(key, [])]
        month_in = month is not None and any(e[:7] == month for e in ends)
        year_in = any(e >= jan1 for e in ends)
        int_month, int_year = interest.get(key, (0, 0))
        st = newest.get(key)
        ledger.append(
            {
                "account_key": key,
                "name": a["display_name"],
                "institution": a["institution"],
                "type": a["type"],
                "owed_cents": owed,
                "apr_milli_pct": a["apr_milli_pct"],
                "rate_type": a["rate_type"],
                "priority": a["priority"],
                "required_cents": required,
                "paying_cents": paying.get(key, 0) if month else None,
                "credit_limit_cents": a["credit_limit_cents"],
                "utilization_pct": pct(owed, a["credit_limit_cents"])
                if a["credit_limit_cents"]
                else None,
                "original_cents": a["original_cents"],
                "paid_down_pct": _paid_down(a["original_cents"], owed),
                "rate_changes": scheduled_rates(a, anchor_months[key]),
                "payoff_date": a["payoff_date"],
                "payoff_ahead": bool(a["payoff_date"]) and a["payoff_date"] >= on.isoformat(),
                "policy_note": a["policy_note"],
                "autopay": a["autopay"],
                "plans_remaining_cents": plan_left.get(key),
                "interest_cents": int_month if month_in else None,
                "interest_ytd_cents": int_year if year_in else None,
                "statement_end": st["period_end"] if st else None,
                "statement_owed_cents": -st["closing_cents"] if st else None,
                "statement_due_date": st["due_date"] if st else None,
                "servicer": a["servicer"],
                "notes": a["notes"],
            }
        )
    ledger.sort(
        key=lambda d: (
            d["priority"] is None,
            d["priority"] or 0,
            -(d["apr_milli_pct"] or 0),
            -d["owed_cents"],
            d["account_key"],
        )
    )
    rated = [d for d in ledger if carries_rate(d)]
    ahead = projections(conn, ledger, h.accounts, cov, plans, on, paid_in_full, balance_months)
    for d in ledger:
        d["projection"] = ahead[d["account_key"]]

    def pick(d: dict | None) -> dict | None:
        if d is None:
            return None
        return {
            "account_key": d["account_key"],
            "name": d["name"],
            "apr_milli_pct": d["apr_milli_pct"],
            "owed_cents": d["owed_cents"],
        }

    tracked = [
        {"account_key": k, "name": a["display_name"], "autopay": a["autopay"]}
        for k, a in h.accounts.items()
        if a["tracked_by"] == "payments" and a["active"] and not a["closed_on"]
    ]
    return {
        "as_of": on.isoformat(),
        "paying_month": month,
        "total_debt_cents": sum(d["owed_cents"] for d in ledger),
        "highest_rate": pick(
            max(rated, key=lambda d: (d["apr_milli_pct"], d["owed_cents"])) if rated else None
        ),
        "cheapest": pick(
            min(rated, key=lambda d: (d["apr_milli_pct"], -d["owed_cents"])) if rated else None
        ),
        "ledger": ledger,
        "tracked_cards": tracked,
        "payoff_sequence": [
            {"key": s.key, "month": s.month, "when_label": s.when_label, "action": s.action}
            for s in sorted(
                readers.payoff_sequence(runs), key=lambda s: (s.month or "9999-99", s.key)
            )
        ],
        "source_run_id": readers.section_source(runs, "payoff_sequence"),
    }


# --------------------------------------------------------------------------
# Flags, to do, pending
# --------------------------------------------------------------------------

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
STATUS_ORDER = {"open": 0, "watching": 1, "resolved": 2}


def _related_label(conn: sqlite3.Connection, runs: list, kind: str | None, key: str | None):
    if kind is None or key is None:
        return None
    if kind == "account":
        r = conn.execute(
            "SELECT display_name FROM accounts WHERE account_key = ?", (key,)
        ).fetchone()
        return r["display_name"] if r else None
    if kind == "category":
        r = conn.execute("SELECT name FROM categories WHERE category_key = ?", (key,)).fetchone()
        return r["name"] if r else None
    if kind == "person":
        r = conn.execute("SELECT display_name FROM people WHERE person_key = ?", (key,)).fetchone()
        return r["display_name"] if r else None
    if kind == "recurring":
        return next((i.name for i in readers.recurring(runs) if i.key == key), None)
    if kind == "plan":
        return next((p.name for p in readers.installment_plans(runs) if p.key == key), None)
    if kind == "todo":
        return next((t.title for t in readers.newest(runs, "todos") or [] if t.key == key), None)
    if kind == "pending":
        return next((p.subject for p in readers.pending(runs) if p.key == key), None)
    if kind == "trip":
        # None when the app has no such trip (never sent, or removed by a tombstone).
        return trip_names(conn).get(key)
    return humanize(key)


def _paid_down(original: int | None, owed: int) -> str | None:
    """Paid down from the original, one decimal. None with no original or more owed than
    it. A debt still owed never reads 100%: a rounded "100.0" becomes "99.9"."""
    if not original or owed > original:
        return None
    paid = pct(original - owed, original)
    return "99.9" if paid == "100.0" else paid


def _info(f: Flag) -> bool:
    """An info flag not resolved: its own group, out of the open, watching and severity
    counts (format 1.4)."""
    return f.action == "info" and f.status != "resolved"


def flags_page(conn: sqlite3.Connection, include_archived: bool = False) -> dict:
    runs = readers.monthly_runs(conn)
    flags = readers.flags(runs)
    on = today().isoformat()
    archived = [f for f in flags if f.archived]
    shown = flags if include_archived else [f for f in flags if not f.archived]
    shown = sorted(
        shown,
        key=lambda f: (
            STATUS_ORDER[f.status] == 2,
            _info(f),
            SEVERITY_ORDER[f.severity],
            STATUS_ORDER[f.status],
            f.due_date or "9999-12-31",
            f.key,
        ),
    )
    counts: dict[str, int] = {}
    for f in flags:
        if f.archived:
            continue
        if _info(f):
            counts["info"] = counts.get("info", 0) + 1
            continue
        counts[f.severity] = counts.get(f.severity, 0) + 1
        counts[f.status] = counts.get(f.status, 0) + 1
    return {
        "source_run_id": readers.section_source(runs, "flags"),
        "flags": [
            {
                "key": f.key,
                "severity": f.severity,
                "status": f.status,
                "kind": f.kind,
                "action": f.action or "act",
                "title": f.title,
                "body": f.body,
                # Newest first. The sort is stable, so one day's notes keep their stored order.
                "updates": [
                    {"on": u.on, "text": u.text}
                    for u in sorted(f.updates or [], key=lambda u: u.on, reverse=True)
                ],
                "related_kind": f.related_kind,
                "related_key": f.related_key,
                "related_label": _related_label(conn, runs, f.related_kind, f.related_key),
                "due_date": f.due_date,
                "overdue": bool(
                    f.due_date and f.due_date < on and f.status != "resolved" and not _info(f)
                ),
                "created_on": f.created_on,
                "resolved_on": f.resolved_on,
                "archived": bool(f.archived),
            }
            for f in shown
        ],
        "archived_count": len(archived),
        "counts": {
            k: counts.get(k, 0)
            for k in ("high", "medium", "low", "open", "watching", "resolved", "info")
        },
    }


def todos_page(conn: sqlite3.Connection) -> dict:
    runs = readers.monthly_runs(conn)
    todos = readers.newest(runs, "todos") or []
    flags = {f.key: f.title for f in readers.flags(runs)}
    on = today().isoformat()
    todos = sorted(todos, key=lambda t: (t.status == "done", t.due_date or "9999-12-31", t.key))
    return {
        "source_run_id": readers.section_source(runs, "todos"),
        "todos": [
            {
                "key": t.key,
                "title": t.title,
                "subtitle": t.subtitle,
                "status": t.status,
                "due_date": t.due_date,
                "overdue": bool(t.due_date and t.due_date < on and t.status != "done"),
                "flag_key": t.flag_key,
                "flag_title": flags.get(t.flag_key) if t.flag_key else None,
            }
            for t in todos
        ],
        "open_count": sum(1 for t in todos if t.status == "open"),
        "done_count": sum(1 for t in todos if t.status == "done"),
    }


def pending_page(conn: sqlite3.Connection) -> dict:
    runs = readers.monthly_runs(conn)
    items = sorted(
        readers.pending(runs),
        key=lambda p: (p.status == "done", p.kind, p.updated_on or p.added_on, p.key),
    )
    return {
        "source_run_id": readers.section_source(runs, "pending"),
        "items": [
            {
                "key": p.key,
                "kind": p.kind,
                "subject": p.subject,
                "detail": p.detail,
                "status": p.status,
                "added_on": p.added_on,
                "updated_on": p.updated_on,
            }
            for p in items
        ],
        "counts": {k: sum(1 for p in items if p.status == k) for k in ("open", "waiting", "done")},
    }
