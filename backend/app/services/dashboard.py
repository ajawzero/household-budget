"""GET /api/dashboard (PLAN 8.2).

The tiles show the chat's plan for `month` (labeled "plan") and, from Phase 4,
the app's actuals for the latest complete month (`actual_month`). Cash flow
bars hold actual months (up to 6 before this month) and then the planned
months. Upcoming lists unpaid bills, plan payments and trip payments (1.4) in the next
30 days. Under it, one line per utility: next bill expected and the 12-month average bill.

The plan's spending includes the saved and invested moves:
left over = planned income - planned spending, and by_group (with
`saved-invested`) adds up to planned spending. % saved counts those moves as
saved: (left over + saved and invested) / income. Each spending bar, plan or
actual, says what is in its group (cashflow.group_contents, E5), from the plan
lines of the month it shows.

State comes from the newest monthly run per section; month-keyed sections use
the month rule in app/state/readers.py. Net worth uses the same daily balance
math as the Accounts page.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.importer.models import to_cents
from app.services import budget_targets, rewards, trips, utilities
from app.services import cashflow as cf
from app.services.balances import History, NetWorth, history, next_due
from app.services.coverage import HISTORY_START, add_months, coverage_state, current_month
from app.services.networth import by_owner, change_of, humanize, pct, today
from app.services.plans import upcoming_plan_payments
from app.services.recurring import plan_lines, upcoming_bills
from app.state import readers

NEXT_CHANGES = 3
ACTUAL_BAR_MONTHS = 6
UPCOMING_DAYS = 30
NET_WORTH_CHANGE_DAYS = 30
SAVED_INVESTED = "saved-invested"
# Plan group keys that are not category groups.
PLAN_GROUP_LABELS = {
    "plan-installments": "Plan It installments",
    SAVED_INVESTED: "Saved and invested",
}


def budget_status(actual: int | None, target: int) -> str:
    """over if actual > target, under if actual < 90% of target, else on_target."""
    if actual is None:
        return "no_actual"
    if actual > target:
        return "over"
    if actual * 10 < target * 9:
        return "under"
    return "on_target"


def _plan_out(plan) -> dict:
    income = to_cents(plan.planned_income)
    spending = to_cents(plan.planned_spending)
    saved = sum(to_cents(g.amount) for g in plan.by_group if g.key == SAVED_INVESTED)
    return {
        "month": plan.month,
        "income_cents": income,
        "spending_cents": spending,
        "saved_invested_cents": saved,
        "left_over_cents": income - spending,
        "basis": "plan",
        "complete": False,
    }


def _actual_bar(a: cf.MonthActual, complete: bool) -> dict:
    return {
        "month": a.month,
        "income_cents": a.income,
        "spending_cents": a.spending + a.saved,
        "saved_invested_cents": a.saved,
        "left_over_cents": a.left_over,
        "basis": "actual",
        "complete": complete,
    }


def _actuals(
    conn: sqlite3.Connection, lab: cf.Labels
) -> tuple[dict | None, list[dict], list[dict]]:
    """(actual_month tile, actual bars, actual spending by group). Each group says
    what is in it from that month's plan lines (E5)."""
    state = coverage_state(conn)
    latest = state.latest_complete_month()
    now = current_month()
    first = max(HISTORY_START, add_months(now, -ACTUAL_BAR_MONTHS))
    months = cf.months_between(first, add_months(now, -1))
    span = [*months, *([latest] if latest else [])] or [now]
    start, end = min(span), max(span)
    acts = cf.actuals(cf.load_rows(conn, start, end), cf.months_between(start, end))
    bars = [_actual_bar(acts[m], state.month_complete(m)) for m in months if acts[m].count]
    if latest is None:
        return None, bars, []
    a = acts[latest]
    bills = plan_lines(conn, latest)
    tile = {
        "month": latest,
        "income_cents": a.income,
        "spending_cents": a.spending,
        "saved_invested_cents": a.saved,
        "left_over_cents": a.left_over,
        "savings_rate": pct(a.income - a.spending, a.income),
        "basis": "actual",
    }
    groups = [
        {
            "key": k,
            "label": lab.group(k),
            "cents": v,
            "note": None,
            "kind": "spending",
            "basis": "actual",
            "contents": cf.group_contents(lab, k, bills.get(k, []), v),
        }
        for k, v in a.by_group.items()
    ]
    if a.saved:
        groups.append(
            {
                "key": SAVED_INVESTED,
                "label": PLAN_GROUP_LABELS[SAVED_INVESTED],
                "cents": a.saved,
                "note": None,
                "kind": "saved_invested",
                "basis": "actual",
                "contents": cf.group_contents(
                    lab, SAVED_INVESTED, bills.get(SAVED_INVESTED, []), a.saved
                ),
            }
        )
    groups.sort(key=lambda g: (-g["cents"], g["key"]))
    return tile, bars, groups


def _upcoming(conn: sqlite3.Connection) -> list[dict]:
    start = today()
    end = start + timedelta(days=UPCOMING_DAYS)
    out = [
        {
            "date": b["due_date"],
            "kind": "bill",
            "key": b["key"],
            "name": b["name"],
            "amount_cents": b["expected_cents"],
            "account_name": b["account_name"],
            "trip_key": None,
            "trip_name": None,
        }
        for b in upcoming_bills(conn, start, end)
    ]
    out += [
        {**p, "trip_key": None, "trip_name": None} for p in upcoming_plan_payments(conn, start, end)
    ]
    out += trips.upcoming_payments(conn, start, end)
    out.sort(key=lambda u: (u["date"], u["kind"], u["name"].casefold(), u["key"]))
    return out


def _behind(h: History, nw: NetWorth) -> list[dict]:
    """The accounts that make net worth partial, and why: no balance yet, or a balance
    past its next statement (and when that was due): what to upload next."""
    out = []
    for key in nw.missing:
        a = h.accounts[key]
        out.append(
            {
                "account_key": key,
                "name": a["display_name"],
                "reason": "no_balance",
                "as_of": None,
                "source": None,
                "due": None,
            }
        )
    for key in nw.stale:
        a, b = h.accounts[key], h.at_end(key)
        assert b.as_of is not None
        out.append(
            {
                "account_key": key,
                "name": a["display_name"],
                "reason": "overdue",
                "as_of": b.as_of,
                "source": b.source,
                "due": next_due(a, b.as_of),
            }
        )
    out.sort(key=lambda x: (x["reason"] != "no_balance", x["name"].casefold(), x["account_key"]))
    return out


def _net_worth(conn: sqlite3.Connection) -> tuple[dict, list[dict], list[dict]]:
    """(net worth tile, Where the net worth is, Debt balances), as of today."""
    end = today()
    h = history(conn, end - timedelta(days=NET_WORTH_CHANGE_DAYS), end)
    nw = h.net_worth()
    dates = [h.at_end(k).as_of for k in h.net_worth_keys()]
    tile = {
        "value_cents": nw.value_cents,
        "change_cents": change_of(h, h.net_worth_keys()),
        "assets_cents": nw.assets_cents,
        "liabilities_cents": nw.liabilities_cents,
        "as_of": nw.as_of,
        "balances_through": max((d for d in dates if d), default=None),
        "partial": nw.partial,
        "behind": _behind(h, nw),
    }
    parts = [{"key": p["key"], "label": p["label"], "cents": p["cents"]} for p in by_owner(h)]
    debts = []
    for key in h.net_worth_keys():
        a = h.accounts[key]
        cents = h.at_end(key).balance_cents
        if a["is_asset"] or cents is None or cents >= 0:
            continue
        debts.append(
            {
                "account_key": key,
                "name": a["display_name"],
                "owed_cents": -cents,
                "apr_milli_pct": a["apr_milli_pct"],
                "payoff_date": a["payoff_date"],
                "payoff_ahead": bool(a["payoff_date"]) and a["payoff_date"] >= end.isoformat(),
            }
        )
    debts.sort(key=lambda d: (-d["owed_cents"], d["account_key"]))
    return tile, parts, debts


def dashboard(conn: sqlite3.Connection, month: str | None = None) -> dict:
    runs, edits = budget_targets.runs_and_edits(conn)
    meta = readers.meta(runs)
    month = month or (meta.this_month if meta else today().strftime("%Y-%m"))

    plan = cf.plan_for(runs, month, edits)
    plan_month = None
    if plan is not None:
        plan_month = _plan_out(plan)
        del plan_month["complete"]
        plan_month["saved_pct"] = pct(
            plan_month["left_over_cents"] + plan_month["saved_invested_cents"],
            plan_month["income_cents"],
        )

    lab = cf.labels(conn)
    actual_month, cashflow, actual_by_group = _actuals(conn, lab)
    now = current_month()
    for m in readers.plan_months(runs):
        found = cf.plan_for(runs, m, edits)
        if found is not None and m >= now:
            cashflow.append(_plan_out(found))

    group_names = {
        r["group_key"]: r["name"]
        for r in conn.execute("SELECT group_key, name FROM category_groups")
    }
    bills = plan_lines(conn, month) if plan else {}
    spending = sorted(
        (
            {
                "key": g.key,
                "label": group_names.get(g.key) or PLAN_GROUP_LABELS.get(g.key) or humanize(g.key),
                "cents": to_cents(g.amount),
                "note": None,
                "kind": "saved_invested" if g.key == SAVED_INVESTED else "spending",
                "basis": "plan",
                "contents": cf.group_contents(lab, g.key, bills.get(g.key, []), to_cents(g.amount)),
            }
            for g in (plan.by_group if plan else [])
        ),
        key=lambda g: (-g["cents"], g["key"]),
    )

    budgets = []
    for b in readers.for_month(runs, "budgets", month):
        actual = to_cents(b.reported_actual) if b.reported_actual is not None else None
        target = to_cents(b.target)
        budgets.append(
            {
                "key": b.key,
                "label": b.label,
                "actual_cents": actual,
                "target_cents": target,
                "plan_cents": to_cents(b.plan) if b.plan is not None else None,
                "note": b.note,
                "period_basis": b.period_basis,
                "cycle_note": b.cycle_note,
                "status": budget_status(actual, target),
                "statements_needed": bool(b.statements_needed),
            }
        )

    events = sorted(
        (e for e in readers.newest(runs, "cashflow_events") or [] if e.effective_month >= month),
        key=lambda e: (e.effective_month, e.key),
    )
    review = readers.newest(runs, "review")
    tile, parts, debts = _net_worth(conn)
    return {
        "month": month,
        "updated": meta.updated if meta else None,
        "complete_through": coverage_state(conn).latest_complete_month(),
        "location_label": meta.location_label if meta else None,
        "net_worth": tile,
        "plan_month": plan_month,
        "actual_month": actual_month,
        "cashflow": cashflow,
        "cashflow_note": plan.note if plan else None,
        "spending_by_group": spending,
        "actual_by_group": actual_by_group,
        "upcoming": _upcoming(conn),
        "utilities": utilities.summaries(conn),
        "credits_ending": rewards.credits_ending(conn),
        "next_trip": trips.next_trip(conn),
        "budgets": budgets,
        "networth_parts": parts,
        "debts": debts,
        "next_changes": [
            {
                "key": e.key,
                "effective_month": e.effective_month,
                "label": e.label,
                "delta_cents": to_cents(e.delta),
                "status": e.status,
            }
            for e in events[:NEXT_CHANGES]
        ],
        "todos": [
            {
                "key": t.key,
                "title": t.title,
                "subtitle": t.subtitle,
                "status": t.status,
                "due_date": t.due_date,
            }
            for t in readers.newest(runs, "todos") or []
        ],
        "review": None
        if review is None
        else {
            "summary": review.summary,
            "key_numbers": [
                {"label": k.label, "value": to_cents(k.value)} for k in review.key_numbers
            ],
            "changes": list(review.changes),
            "anomalies": list(review.anomalies),
            "top_three": list(review.top_three),
        },
    }
