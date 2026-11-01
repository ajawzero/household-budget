"""Cash flow from v_cashflow_rows (PLAN 3.9, 8.2 Cash Flow).

Measures per month (calendar month of txn_date):
- Income: the signed sum of `income` rows.
- Spending: the sum of `out_cents` (money spent is positive) over `spending`
  and `card_no_stmt` rows. Refunds and card credits net in their category.
  Payment-basis rows count in their own category. A card payment with no
  statement counts under "Card spending (no statement)".
- Saved and invested: the sum of `out_cents` over `saved` rows (net, so it
  can be negative when money comes back out of savings).
- Left over = income - spending - saved and invested.
- Savings rate = (income - spending) / income.
- Reimbursable charges are left out. One shows as "Waiting on reimbursement"
  until a reimbursement row (category kind `reimbursement`, money in) of the
  same amount arrives on or after its date, within 180 days.

The plan: planned spending includes saved and invested.
Plan left over = planned income - planned spending. Planned spending is
compared with actual spending + saved and invested. Each plan group maps to
one measure: a category group, Saved and invested, or Plan It installments.

Plan basis (plan vs actual and Budget): the chat's plan pays for a plan in
the spending plan (`included_in_spending_plan`) through its monthly
installments, not at purchase. So on those two views the purchase and the
plan fee rows of such a plan leave their category groups, and the Plan It
installments line is the billed installment rows plus those fees. The
totals are the sum of the lines, so they add up. Everywhere else (the KPIs,
bars, breakdown, Checks) a plan counts as spending at purchase (PLAN 14.15).

Plan lines (gap audit C2): each plan group lists the counted recurring bills
in it for that month (recurring.plan_lines, largest first). Everything else
is the group's plan minus those bills, so it is negative when the bills are
more than the plan. Plan It installments and groups with no bills have no
lines: everything else is the whole group. Each line carries the item's C6
change (recurring.plan_lines, E7f). Contents (E5), the line under each
Dashboard bar and Plan group row: the top 3 lines by size and how many more,
when the lines make up at least half of the group's amount. Otherwise the
group names its active spending categories in list order (so Lifestyle names
groceries and dining, not its one subscription), and with no such categories
it names its lines after all. Plan It installments, and a group with neither,
name nothing.

Complete months come from app/services/coverage.py. Totals and averages use
complete months only.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.importer.models import CashflowPlan, GroupAmount, InstallmentPlan, to_cents
from app.importer.order import run_order_key
from app.services import budget_targets as bt
from app.services.coverage import (
    HISTORY_START,
    CoverageState,
    add_months,
    coverage_state,
    current_month,
    months_between,
)
from app.services.networth import humanize, pct
from app.services.recurring import plan_lines
from app.state import readers

SAVED_INVESTED = "saved-invested"
PLAN_INSTALLMENTS = "plan-installments"
CARD_NO_STMT_KEY = "card-no-statement"
CARD_NO_STMT_LABEL = "Card spending (no statement)"
PLAN_GROUP_LABELS = {
    PLAN_INSTALLMENTS: "Plan It installments",
    SAVED_INVESTED: "Saved and invested",
}
SPENDING_FLOWS = ("spending", "card_no_stmt")
REIMBURSE_DAYS = 180
MAX_MONTHS = 36
DEFAULT_MONTHS = 12
TOP_MERCHANTS = 5


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


@dataclass
class Labels:
    categories: dict[str, sqlite3.Row]
    groups: dict[str, sqlite3.Row]
    accounts: dict[str, sqlite3.Row]
    people: dict[str, str]

    def category(self, key: str) -> tuple[str, str | None]:
        if key == CARD_NO_STMT_KEY:
            return CARD_NO_STMT_LABEL, None
        c = self.categories.get(key)
        return (c["name"], c["emoji"]) if c else (humanize(key), None)

    def group(self, key: str) -> str:
        if key == CARD_NO_STMT_KEY:
            return CARD_NO_STMT_LABEL
        g = self.groups.get(key)
        return g["name"] if g else PLAN_GROUP_LABELS.get(key) or humanize(key)

    def account(self, key: str | None) -> str | None:
        if key is None:
            return None
        a = self.accounts.get(key)
        return a["display_name"] if a else key


def labels(conn: sqlite3.Connection) -> Labels:
    return Labels(
        {r["category_key"]: r for r in conn.execute("SELECT * FROM categories")},
        {r["group_key"]: r for r in conn.execute("SELECT * FROM category_groups")},
        {r["account_key"]: r for r in conn.execute("SELECT * FROM accounts")},
        {r["person_key"]: r["display_name"] for r in conn.execute("SELECT * FROM people")},
    )


# --------------------------------------------------------------------------
# Rows and monthly actuals
# --------------------------------------------------------------------------

ROW_COLUMNS = """txn_key, txn_date, month, account_key, amount_cents, out_cents, flow,
    payment_basis, category_key, category_group, category_kind, merchant, description,
    counterparty_account_key, recurring_key, plan_key, txn_type"""


def load_rows(conn: sqlite3.Connection, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"""SELECT {ROW_COLUMNS} FROM v_cashflow_rows
            WHERE month BETWEEN ? AND ? ORDER BY txn_date, txn_key""",
        (start, end),
    ).fetchall()


def report_category(r: sqlite3.Row) -> tuple[str, str]:
    """(category key, group key) a spending row reports under."""
    if r["flow"] == "card_no_stmt":
        return CARD_NO_STMT_KEY, CARD_NO_STMT_KEY
    return r["category_key"], r["category_group"] or "not-spending"


def report_category_group(lab: Labels, key: str) -> str:
    """The group a reporting category key belongs to."""
    if key == CARD_NO_STMT_KEY:
        return key
    c = lab.categories.get(key)
    return c["group_key"] if c else "not-spending"


def merchant_label(r: sqlite3.Row, lab: Labels) -> str:
    """The shown merchant. Payments to a card or loan show the account's name."""
    if r["flow"] == "card_no_stmt" or r["payment_basis"]:
        return lab.account(r["counterparty_account_key"]) or r["description"]
    if r["merchant"]:
        return r["merchant"]
    return lab.account(r["counterparty_account_key"]) or r["description"]


@dataclass
class MonthActual:
    month: str
    income: int = 0
    spending: int = 0
    saved: int = 0
    payment_basis: int = 0
    card_no_statement: int = 0
    uncategorized: int = 0
    reimbursable: int = 0
    count: int = 0
    by_group: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)
    # Plan basis: spending rows of plans in the spending plan (purchase and
    # plan fee), and what those plans billed this month (installments + fees).
    plan_by_group: dict[str, int] = field(default_factory=dict)
    plan_by_category: dict[str, int] = field(default_factory=dict)
    plan_billed: int = 0

    @property
    def left_over(self) -> int:
        return self.income - self.spending - self.saved

    def plan_group(self, key: str) -> int:
        """A group's spending on the plan basis."""
        return self.by_group.get(key, 0) - self.plan_by_group.get(key, 0)

    def plan_category(self, key: str) -> int:
        return self.by_category.get(key, 0) - self.plan_by_category.get(key, 0)

    def plan_groups(self) -> dict[str, int]:
        keys = set(self.by_group) | set(self.plan_by_group)
        return {k: self.plan_group(k) for k in keys}

    @property
    def plan_total(self) -> int:
        """Spending + saved and invested on the plan basis (the sum of the lines)."""
        return self.spending + self.saved - sum(self.plan_by_group.values()) + self.plan_billed


def included_plan_keys(plans: list[InstallmentPlan]) -> frozenset[str]:
    """Plans whose monthly payment is in the spending plan."""
    return frozenset(p.key for p in plans if p.included_in_spending_plan)


def actuals(
    rows: list[sqlite3.Row], months: list[str], included_plans: frozenset[str] = frozenset()
) -> dict[str, MonthActual]:
    out = {m: MonthActual(m) for m in months}
    for r in rows:
        a = out.get(r["month"])
        if a is None:
            continue
        flow = r["flow"]
        if r["plan_key"] in included_plans:
            _plan_basis(a, r, flow)
        if flow == "income":
            a.income += r["amount_cents"]
        elif flow in SPENDING_FLOWS:
            a.spending += r["out_cents"]
            cat, grp = report_category(r)
            a.by_category[cat] = a.by_category.get(cat, 0) + r["out_cents"]
            a.by_group[grp] = a.by_group.get(grp, 0) + r["out_cents"]
            if r["payment_basis"]:
                a.payment_basis += r["out_cents"]
            if flow == "card_no_stmt":
                a.card_no_statement += r["out_cents"]
            if r["category_kind"] == "uncategorized" and flow == "spending":
                a.uncategorized += r["out_cents"]
        elif flow == "saved":
            a.saved += r["out_cents"]
        elif flow == "reimbursable":
            a.reimbursable += r["out_cents"]
            continue
        else:
            continue
        a.count += 1
    return out


def _plan_basis(a: MonthActual, r: sqlite3.Row, flow: str) -> None:
    """Track a row of a plan in the spending plan (see the module notes)."""
    if flow in SPENDING_FLOWS:
        cat, grp = report_category(r)
        a.plan_by_category[cat] = a.plan_by_category.get(cat, 0) + r["out_cents"]
        a.plan_by_group[grp] = a.plan_by_group.get(grp, 0) + r["out_cents"]
        if r["txn_type"] == "plan_fee":
            a.plan_billed += r["out_cents"]
    elif flow == "transfer" and r["txn_type"] == "adjustment" and r["amount_cents"] < 0:
        a.plan_billed += r["out_cents"]  # a monthly installment billed on the card


def month_status(month: str, complete: bool) -> str:
    now = current_month()
    if month > now:
        return "future"
    if month == now:
        return "current"
    return "complete" if complete else "partial"


def actual_out(a: MonthActual) -> dict:
    return {
        "income_cents": a.income,
        "spending_cents": a.spending,
        "saved_invested_cents": a.saved,
        "left_over_cents": a.left_over,
        "savings_rate": pct(a.income - a.spending, a.income),
        "payment_basis_cents": a.payment_basis,
        "card_no_statement_cents": a.card_no_statement,
        "uncategorized_cents": a.uncategorized,
        "reimbursable_cents": a.reimbursable,
        "count": a.count,
    }


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


def plan_values(plan: CashflowPlan) -> dict:
    income = to_cents(plan.planned_income)
    spending = to_cents(plan.planned_spending)
    saved = sum(to_cents(g.amount) for g in plan.by_group if g.key == SAVED_INVESTED)
    left = income - spending
    return {
        "income_cents": income,
        "spending_cents": spending,
        "saved_invested_cents": saved,
        "left_over_cents": left,
        "saved_pct": pct(left + saved, income),
    }


def plan_view(runs: list[readers.StateRun], month: str, edits: bt.Edits) -> bt.PlanView:
    """The chat's plan for `month` with the owner's target edits on top, and where each
    edited number came from (app/services/budget_targets.py). The one reader of
    the cashflow_plan section: every screen goes through here or plan_for, so
    they all show the same numbers."""
    found = readers.for_month(runs, "cashflow_plan", month)
    rows = edits.for_month(month)
    # The month's plan from each run imported after an edit was saved, so the
    # overlay can tell when the chat caught up on an edit and then moved on.
    since = [
        (run.finished_at, readers.for_month([run], "cashflow_plan", month))
        for run in runs
        if run.finished_at is not None and any(run.finished_at > r.updated_at for r in rows)
    ]
    caught = bt.caught_up(rows, [(at, p[0]) for at, p in since if p])
    return bt.effective_plan(found[0] if found else None, rows, caught)


def plan_for(runs: list[readers.StateRun], month: str, edits: bt.Edits) -> CashflowPlan | None:
    """The plan every screen shows for `month` (see plan_view)."""
    return plan_view(runs, month, edits).plan


def line_status(actual: int | None, plan: int | None) -> str:
    if plan is None:
        return "no_plan"
    if actual is None:
        return "no_actual"
    if actual > plan:
        return "over"
    if actual * 10 < plan * 9:
        return "under"
    return "on_target"


def plan_mapping(lab: Labels, plan: CashflowPlan) -> list[dict]:
    out = []
    for g in plan.by_group:
        if g.key == SAVED_INVESTED:
            measure, target = "saved_invested", "Actual saved and invested"
        elif g.key == PLAN_INSTALLMENTS:
            measure, target = "plan_installments", "Installments and plan fees billed"
        elif g.key in lab.groups:
            measure, target = "group", f"Spending in {lab.group(g.key)}"
        else:
            measure, target = "unmapped", "Nothing. This plan group has no matching measure."
        out.append({"key": g.key, "label": lab.group(g.key), "measure": measure, "maps_to": target})
    return out


def plan_vs_actual(lab: Labels, plan: CashflowPlan, actual: MonthActual, complete: bool) -> dict:
    """Plan vs actual on the plan basis. The lines add up to the actual total."""
    lines = []
    planned_keys = set()
    for m in plan_mapping(lab, plan):
        key = m["key"]
        planned_keys.add(key)
        plan_cents = next(to_cents(g.amount) for g in plan.by_group if g.key == key)
        if m["measure"] == "saved_invested":
            act: int | None = actual.saved
        elif m["measure"] == "plan_installments":
            act = actual.plan_billed
        elif m["measure"] == "group":
            act = actual.plan_group(key)
        else:
            act = None
        lines.append(
            {
                "key": key,
                "label": m["label"],
                "measure": m["measure"],
                "plan_cents": plan_cents,
                "actual_cents": act,
                "diff_cents": None if act is None else act - plan_cents,
                "status": line_status(act, plan_cents),
            }
        )
    unplanned = actual.plan_groups()
    if actual.plan_billed:
        unplanned[PLAN_INSTALLMENTS] = actual.plan_billed
    if actual.saved:
        unplanned[SAVED_INVESTED] = actual.saved
    for key, cents in sorted(unplanned.items(), key=lambda kv: (-kv[1], kv[0])):
        if key in planned_keys or cents == 0:
            continue
        lines.append(
            {
                "key": key,
                "label": lab.group(key),
                "measure": "unplanned",
                "plan_cents": None,
                "actual_cents": cents,
                "diff_cents": None,
                "status": "no_plan",
            }
        )
    values = plan_values(plan)
    act_total = actual.plan_total
    return {
        "month": actual.month,
        "complete": complete,
        "planned_income_cents": values["income_cents"],
        "actual_income_cents": actual.income,
        "planned_spending_cents": values["spending_cents"],
        "actual_spending_cents": act_total,
        "diff_cents": act_total - values["spending_cents"],
        "status": line_status(act_total, values["spending_cents"]),
        "lines": lines,
    }


# --------------------------------------------------------------------------
# Waiting on reimbursement
# --------------------------------------------------------------------------


def reimbursements(conn: sqlite3.Connection, lab: Labels) -> list[dict]:
    """Reimbursable charges with no matching reimbursement yet, oldest first."""
    charges = conn.execute(
        f"""SELECT {ROW_COLUMNS} FROM v_cashflow_rows
            WHERE flow = 'reimbursable' ORDER BY txn_date, txn_key"""
    ).fetchall()
    credits = conn.execute(
        f"""SELECT {ROW_COLUMNS} FROM v_cashflow_rows
            WHERE flow = 'reimbursement' AND amount_cents > 0 ORDER BY txn_date, txn_key"""
    ).fetchall()
    used: set[str] = set()
    waiting = []
    for c in charges:
        last = (date.fromisoformat(c["txn_date"]) + timedelta(days=REIMBURSE_DAYS)).isoformat()
        match = next(
            (
                r
                for r in credits
                if r["txn_key"] not in used
                and r["amount_cents"] == -c["amount_cents"]
                and c["txn_date"] <= r["txn_date"] <= last
            ),
            None,
        )
        if match is not None:
            used.add(match["txn_key"])
            continue
        waiting.append(
            {
                "txn_key": c["txn_key"],
                "txn_date": c["txn_date"],
                "merchant": merchant_label(c, lab),
                "account_name": lab.account(c["account_key"]) or c["account_key"],
                "amount_cents": c["amount_cents"],
            }
        )
    return waiting


# --------------------------------------------------------------------------
# Cards tracked by payments ("Sam's cards", PLAN 3.9)
# --------------------------------------------------------------------------


def tracked_cards(
    conn: sqlite3.Connection,
    lab: Labels,
    months: list[str],
    complete: dict[str, bool] | None = None,
) -> dict | None:
    """Spending on cards tracked by payments, per card and month, payment basis.

    Per card: payment-basis rows whose counterparty is the card, plus the
    spending rows of any statement the chat used for it, by txn_date month.
    Averages a month use the complete months in range only (`complete`), the
    same rule as the KPI averages. The last payment is the newest payment of
    any kind to the card dated in or before the last month in range, so a
    month where the chat used the card's statement still counts.
    """
    cards = [a for a in lab.accounts.values() if a["tracked_by"] == "payments"]
    if not cards or not months:
        return None
    keys = [a["account_key"] for a in cards]
    marks = ", ".join("?" for _ in keys)
    got: dict[tuple[str, str], list[int]] = {}
    for r in conn.execute(
        f"""SELECT counterparty_account_key AS card, month, out_cents, 'payment' AS part
            FROM v_cashflow_rows
            WHERE payment_basis = 1 AND flow = 'spending' AND counterparty_account_key IN ({marks})
              AND month BETWEEN ? AND ?
            UNION ALL
            SELECT account_key AS card, month, out_cents, 'statement' AS part
            FROM v_cashflow_rows
            WHERE flow IN ('spending', 'card_no_stmt') AND account_key IN ({marks})
              AND month BETWEEN ? AND ?""",
        (*keys, months[0], months[-1], *keys, months[0], months[-1]),
    ):
        cell = got.setdefault((r["card"], r["month"]), [0, 0])
        cell[0 if r["part"] == "payment" else 1] += r["out_cents"]
    last_pay: dict[str, tuple[str, int]] = {}
    for r in conn.execute(
        f"""SELECT counterparty_account_key AS card, txn_date, -amount_cents AS cents
            FROM v_transactions
            WHERE counterparty_account_key IN ({marks}) AND amount_cents < 0
              AND txn_type = 'payment' AND hidden = 0 AND substr(txn_date, 1, 7) <= ?
            ORDER BY txn_date, txn_key""",
        (*keys, months[-1]),
    ):
        seen = last_pay.get(r["card"])
        if seen and seen[0] == r["txn_date"]:
            last_pay[r["card"]] = (seen[0], seen[1] + r["cents"])  # same day: add them up
        else:
            last_pay[r["card"]] = (r["txn_date"], r["cents"])
    done = {m for m in months if complete and complete.get(m)}
    n = len(done)
    owners = sorted({a["owner_person_key"] for a in cards})
    label = (
        f"{lab.people.get(owners[0], humanize(owners[0]))}'s cards"
        if len(owners) == 1
        else "Cards tracked by payments"
    )
    out_cards = []
    for a in sorted(cards, key=lambda a: a["display_name"]):
        per = []
        for m in months:
            pay, stmt = got.get((a["account_key"], m), [0, 0])
            per.append(
                {"month": m, "cents": pay + stmt, "payments_cents": pay, "statement_cents": stmt}
            )
        paid = last_pay.get(a["account_key"])
        out_cards.append(
            {
                "account_key": a["account_key"],
                "name": a["display_name"],
                "months": per,
                "total_cents": sum(p["cents"] for p in per),
                "avg_cents": _avg(sum(p["cents"] for p in per if p["month"] in done), n)
                if n
                else None,
                "last_payment_date": paid[0] if paid else None,
                "last_payment_cents": paid[1] if paid else None,
            }
        )
    totals = [
        {"month": m, "cents": sum(c["months"][i]["cents"] for c in out_cards)}
        for i, m in enumerate(months)
    ]
    return {
        "label": label,
        "basis": "payment basis",
        "cards": out_cards,
        "months": totals,
        "total_cents": sum(t["cents"] for t in totals),
        "avg_cents": _avg(sum(t["cents"] for t in totals if t["month"] in done), n) if n else None,
        "avg_months": n,
    }


# --------------------------------------------------------------------------
# Ranges
# --------------------------------------------------------------------------


class RangeError(ValueError):
    pass


def resolve_months(
    start: str | None, end: str | None, default_end: str | None = None, span: int = DEFAULT_MONTHS
) -> list[str]:
    end = end or default_end or current_month()
    start = start or max(HISTORY_START, add_months(end, -(span - 1)))
    if start > end:
        raise RangeError("The start month is after the end month.")
    months = months_between(start, end)
    if len(months) > MAX_MONTHS:
        raise RangeError(f"Ask for {MAX_MONTHS} months or fewer.")
    return months


def _avg(total: int, n: int) -> int:
    return int((Decimal(total) / n).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# GET /api/cashflow
# --------------------------------------------------------------------------


def cashflow(conn: sqlite3.Connection, start: str | None, end: str | None) -> dict:
    months = resolve_months(start, end)
    state = coverage_state(conn)
    runs, edits = bt.runs_and_edits(conn)
    lab = labels(conn)
    plans = included_plan_keys(readers.installment_plans(runs))
    acts = actuals(load_rows(conn, months[0], months[-1]), months, plans)
    complete = state.complete_months(months)
    out_months = []
    pva = []
    for m in months:
        plan = plan_for(runs, m, edits)
        out_months.append(
            {
                "month": m,
                "status": month_status(m, complete[m]),
                "complete": complete[m],
                **actual_out(acts[m]),
                "plan": plan_values(plan) if plan else None,
            }
        )
        if plan:
            pva.append(plan_vs_actual(lab, plan, acts[m], complete[m]))
    done = [acts[m] for m in months if complete[m]]
    n = len(done)
    totals = {
        "months": n,
        "income_cents": sum(a.income for a in done),
        "spending_cents": sum(a.spending for a in done),
        "saved_invested_cents": sum(a.saved for a in done),
    }
    totals["left_over_cents"] = (
        totals["income_cents"] - totals["spending_cents"] - totals["saved_invested_cents"]
    )
    totals["savings_rate"] = pct(
        totals["income_cents"] - totals["spending_cents"], totals["income_cents"]
    )
    averages = None
    if n:
        averages = {
            "months": n,
            "income_cents": _avg(totals["income_cents"], n),
            "spending_cents": _avg(totals["spending_cents"], n),
            "saved_invested_cents": _avg(totals["saved_invested_cents"], n),
            "left_over_cents": _avg(totals["left_over_cents"], n),
        }
    latest = state.latest_complete_month()
    kpi = None
    if latest and latest in acts:
        kpi = {"month": latest, **actual_out(acts[latest])}
    waiting = [w for w in reimbursements(conn, lab) if w["txn_date"][:7] <= months[-1]]
    return {
        "from": months[0],
        "to": months[-1],
        "current_month": current_month(),
        "latest_complete_month": latest,
        "kpi": kpi,
        "months": out_months,
        "totals": totals,
        "averages": averages,
        "plan_vs_actual": pva,
        "tracked_cards": tracked_cards(conn, lab, months, complete),
        "waiting_reimbursement": waiting,
        "waiting_reimbursement_cents": -sum(w["amount_cents"] for w in waiting),
    }


# --------------------------------------------------------------------------
# GET /api/cashflow/breakdown
# --------------------------------------------------------------------------


def default_range(conn: sqlite3.Connection, state: CoverageState | None = None) -> str:
    state = state or coverage_state(conn)
    return state.latest_complete_month() or current_month()


def breakdown(
    conn: sqlite3.Connection, start: str | None, end: str | None, by: str, flow: str = "spending"
) -> dict:
    state = coverage_state(conn)
    default = default_range(conn, state)
    months = resolve_months(start or (None if end else default), end or default, span=1)
    lab = labels(conn)
    rows = load_rows(conn, months[0], months[-1])
    flows = SPENDING_FLOWS if flow == "spending" else ("income",)
    items: dict[str, dict] = {}
    saved = 0
    for r in rows:
        if r["flow"] == "saved":
            saved += r["out_cents"]
        if r["flow"] not in flows:
            continue
        cents = r["out_cents"] if flow == "spending" else r["amount_cents"]
        cat, grp = report_category(r)
        if by == "category":
            key, label = cat, lab.category(cat)[0]
        elif by == "group":
            key, label = grp, lab.group(grp)
        else:
            label = merchant_label(r, lab)
            key = label.casefold()
        item = items.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "emoji": lab.category(cat)[1] if by == "category" else None,
                "group_key": grp if by == "category" else (grp if by == "group" else None),
                "group_label": lab.group(grp) if by != "merchant" else None,
                "cents": 0,
                "count": 0,
                "payment_basis_cents": 0,
            },
        )
        item["cents"] += cents
        item["count"] += 1
        if r["payment_basis"]:
            item["payment_basis_cents"] += cents
    total = sum(i["cents"] for i in items.values())
    ranked = sorted(items.values(), key=lambda i: (-i["cents"], i["label"].casefold(), i["key"]))
    for i in ranked:
        i["share_pct"] = pct(i["cents"], total) if total > 0 else None
    complete = state.complete_months(months)
    return {
        "from": months[0],
        "to": months[-1],
        "by": by,
        "flow": flow,
        "complete": all(complete.values()),
        "partial_months": [m for m in months if not complete[m]],
        "total_cents": total,
        "saved_invested_cents": saved if flow == "spending" else 0,
        "items": ranked,
    }


# --------------------------------------------------------------------------
# GET /api/cashflow/category/{key}
# --------------------------------------------------------------------------


def category_detail(
    conn: sqlite3.Connection, key: str, start: str | None, end: str | None
) -> dict | None:
    lab = labels(conn)
    if key != CARD_NO_STMT_KEY and key not in lab.categories:
        return None
    state = coverage_state(conn)
    default_end = default_range(conn, state)
    months = resolve_months(start, end, default_end=default_end)
    cat = lab.categories.get(key)
    kind = cat["kind"] if cat else "spending"
    group_key = cat["group_key"] if cat else CARD_NO_STMT_KEY
    income = kind == "income"
    rows = load_rows(conn, months[0], months[-1])
    per = {m: [0, 0] for m in months}
    merchants: dict[str, dict] = {}
    for r in rows:
        if r["flow"] not in (("income",) if income else SPENDING_FLOWS):
            continue
        if report_category(r)[0] != key:
            continue
        cents = r["amount_cents"] if income else r["out_cents"]
        per[r["month"]][0] += cents
        per[r["month"]][1] += 1
        label = merchant_label(r, lab)
        m = merchants.setdefault(label.casefold(), {"label": label, "cents": 0, "count": 0})
        m["cents"] += cents
        m["count"] += 1
    runs, edits = bt.runs_and_edits(conn)
    complete = state.complete_months(months)
    out_months = []
    for m in months:
        plan = plan_for(runs, m, edits)
        group_plan = None
        if plan is not None:
            group_plan = next(
                (to_cents(g.amount) for g in plan.by_group if g.key == group_key), None
            )
        budget = next(
            (
                to_cents(b.target)
                for b in readers.for_month(runs, "budgets", m)
                if b.category_key == key and b.period_basis == "calendar"
            ),
            None,
        )
        out_months.append(
            {
                "month": m,
                "cents": per[m][0],
                "count": per[m][1],
                "complete": complete[m],
                "status": month_status(m, complete[m]),
                "budget_cents": budget,
                "group_plan_cents": group_plan,
            }
        )
    done = [o["cents"] for o in out_months if o["complete"]]
    name, emoji = lab.category(key)
    return {
        "category_key": key,
        "name": name,
        "emoji": emoji,
        "kind": kind,
        "group_key": group_key,
        "group_label": lab.group(group_key),
        "from": months[0],
        "to": months[-1],
        "months": out_months,
        "total_cents": sum(o["cents"] for o in out_months),
        "average_cents": _avg(sum(done), len(done)) if done else None,
        "top_merchants": sorted(merchants.values(), key=lambda m: (-m["cents"], m["label"]))[
            :TOP_MERCHANTS
        ],
    }


# --------------------------------------------------------------------------
# GET /api/cashflow/plan
# --------------------------------------------------------------------------


CONTENTS_SHOWN = 3


def group_contents(lab: Labels, key: str, lines: list[dict], cents: int) -> dict | None:
    """What is in a plan group (E5), whose amount is cents. See the module rules."""
    if key == PLAN_INSTALLMENTS:
        return None
    cats = sorted(
        (
            c
            for c in lab.categories.values()
            if c["group_key"] == key and c["kind"] == "spending" and c["active"]
        ),
        key=lambda c: (c["sort"], c["name"].casefold(), c["category_key"]),
    )
    if lines and (2 * sum(x["cents"] for x in lines) >= cents or not cats):
        kind = "transfers" if key == SAVED_INVESTED else "bills"
        names = [x["name"] for x in lines]
    elif cats:
        kind, names = "categories", [c["name"] for c in cats]
    else:
        return None
    return {
        "kind": kind,
        "names": names[:CONTENTS_SHOWN],
        "more": max(0, len(names) - CONTENTS_SHOWN),
    }


def plan_group(lab: Labels, g: GroupAmount, bills: dict[str, list[dict]]) -> dict:
    """One plan group with its bills for the month. See the module rules."""
    cents = to_cents(g.amount)
    lines = [] if g.key == PLAN_INSTALLMENTS else bills.get(g.key, [])
    return {
        "key": g.key,
        "label": lab.group(g.key),
        "cents": cents,
        "kind": "saved_invested" if g.key == SAVED_INVESTED else "spending",
        "lines": lines,
        "other_cents": cents - sum(x["cents"] for x in lines),
        "contents": group_contents(lab, g.key, lines, cents),
    }


def plan_page(conn: sqlite3.Connection) -> dict:
    runs, edits = bt.runs_and_edits(conn)
    lab = labels(conn)
    state = coverage_state(conn)
    plans = included_plan_keys(readers.installment_plans(runs))
    months = []
    for m in readers.plan_months(runs):
        plan = plan_for(runs, m, edits)
        if plan is None:
            continue
        acts = actuals(load_rows(conn, m, m), [m], plans)[m]
        complete = state.month_complete(m)
        bills = plan_lines(conn, m)
        months.append(
            {
                "month": m,
                "status": month_status(m, complete),
                **plan_values(plan),
                "note": plan.note,
                "by_group": [plan_group(lab, g, bills) for g in plan.by_group],
                "mapping": plan_mapping(lab, plan),
                "vs_actual": plan_vs_actual(lab, plan, acts, complete)
                if m <= current_month()
                else None,
            }
        )
    now = current_month()
    events = sorted(
        readers.newest(runs, "cashflow_events") or [], key=lambda e: (e.effective_month, e.key)
    )
    phases = sorted(readers.cashflow_phases(runs), key=lambda p: (p.start_month, p.key))
    return {
        "current_month": now,
        "source_run_id": readers.section_source(runs, "cashflow_plan"),
        "months": months,
        "events": [
            {
                "key": e.key,
                "effective_month": e.effective_month,
                "delta_cents": to_cents(e.delta),
                "label": e.label,
                "reason": e.reason,
                "linked_kind": e.linked_kind,
                "linked_key": e.linked_key,
                "upcoming": e.effective_month >= now,
                "status": e.status,
            }
            for e in events
        ],
        "phases": [
            {
                "key": p.key,
                "label": p.label,
                "start_month": p.start_month,
                "end_month": p.end_month,
                "surplus_cents": to_cents(p.surplus),
                "reason": p.reason,
                "note": p.note,
                "current": p.start_month <= now and (p.end_month is None or now <= p.end_month),
            }
            for p in phases
        ],
    }


# --------------------------------------------------------------------------
# GET /api/income
# --------------------------------------------------------------------------


def all_paychecks(conn: sqlite3.Connection) -> list[dict]:
    """Every paycheck from imported runs: newest run per identity, tombstones applied."""
    rows = conn.execute(
        """SELECT s.run_id, s.json FROM run_sections s JOIN import_runs r ON r.run_id = s.run_id
           WHERE r.status = 'imported' AND s.section = 'income'"""
    ).fetchall()
    found: dict[tuple, dict | None] = {}
    for r in sorted(rows, key=lambda r: run_order_key(r["run_id"])):
        for p in json.loads(r["json"]).get("paychecks", []):
            ident = (p["person"], p["pay_date"], p["kind"], p["seq"])
            found[ident] = None if p.get("deleted") else p
    return [p for _, p in sorted(found.items()) if p is not None]


def income_page(conn: sqlite3.Connection, start: str | None, end: str | None) -> dict:
    state = coverage_state(conn)
    months = resolve_months(start, end)
    lab = labels(conn)
    runs = readers.monthly_runs(conn)
    streams = readers.income_streams(runs)
    complete = state.complete_months(months)
    rows = [r for r in load_rows(conn, months[0], months[-1]) if r["flow"] == "income"]
    checks = [p for p in all_paychecks(conn) if months[0] <= p["pay_date"][:7] <= months[-1]]
    stream_keys = {s.key for s in streams}
    out_months = []
    for m in months:
        mrows = [r for r in rows if r["month"] == m]
        per_stream = []
        for s in streams:
            dep = [r for r in mrows if r["recurring_key"] == s.key]
            per_stream.append(
                {
                    "key": s.key,
                    "deposits_cents": sum(r["amount_cents"] for r in dep),
                    "deposit_count": len(dep),
                }
            )
        by_person: dict[str, dict] = {}
        for p in checks:
            if p["pay_date"][:7] != m:
                continue
            e = by_person.setdefault(
                p["person"],
                {
                    "person": p["person"],
                    "person_label": lab.people.get(p["person"], humanize(p["person"])),
                    "count": 0,
                    "gross_cents": 0,
                    "net_cents": 0,
                },
            )
            e["count"] += 1
            e["gross_cents"] += to_cents(p["gross"])
            e["net_cents"] += to_cents(p["net"])
        out_months.append(
            {
                "month": m,
                "status": month_status(m, complete[m]),
                "complete": complete[m],
                "income_cents": sum(r["amount_cents"] for r in mrows),
                "other_income_cents": sum(
                    r["amount_cents"] for r in mrows if r["recurring_key"] not in stream_keys
                ),
                "streams": per_stream,
                "paychecks": [by_person[k] for k in sorted(by_person)],
            }
        )
    now = current_month()
    return {
        "from": months[0],
        "to": months[-1],
        "source_run_id": readers.section_source(runs, "income"),
        "streams": [
            {
                "key": s.key,
                "person": s.person,
                "person_label": lab.people.get(s.person, humanize(s.person)),
                "label": s.label,
                "kind": s.kind,
                "schedule": s.schedule,
                "expected_net_cents": to_cents(s.expected_net),
                "start_month": s.start_month,
                "end_month": s.end_month,
                "note": s.note,
                "active": (s.start_month is None or s.start_month <= now)
                and (s.end_month is None or now <= s.end_month),
            }
            for s in streams
        ],
        "months": out_months,
        "paychecks": [
            {
                "person": p["person"],
                "person_label": lab.people.get(p["person"], humanize(p["person"])),
                "pay_date": p["pay_date"],
                "kind": p["kind"],
                "seq": p["seq"],
                "gross_cents": to_cents(p["gross"]),
                "net_cents": to_cents(p["net"]),
            }
            for p in sorted(checks, key=lambda p: (p["pay_date"], p["person"], p["kind"], p["seq"]))
        ],
    }
