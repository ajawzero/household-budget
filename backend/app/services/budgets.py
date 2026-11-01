"""GET /api/budgets?month= (PLAN 8.2 Budget, Q4.1 default).

- Calendar months. Group targets come from the chat's plan for the month
  (`cashflow_plan.by_group`, labeled "plan"). Actuals use the cash flow rules
  (app/services/cashflow.py) on the plan basis, the same as plan vs actual
  on Cash Flow: a plan in the spending plan counts by what it billed this
  month (installments plus plan fees) under Plan It installments, and its
  purchase leaves its category group. Totals are the sum of the groups.
- Category rows show actuals. A category gets a target only when the chat
  sent a calendar budget for it (`budgets` with category_key).
- Remaining = plan - actual. Status: over when actual > plan, under when
  actual < 90% of plan, else on_target.
- The chat's budgets for the month are listed as reported (cycle based ones
  labeled with their cycle), with "statements needed" names.
- The page opens on the latest complete month. An incomplete month says
  "Statements not in yet".
- Group targets are the effective ones: the chat's plan with the owner's edits on
  top (app/services/budget_targets.py, through cashflow.plan_view). Each group
  says where its number came from: chat_plan_cents (the chat's number, null
  when it sends none), source chat or edited, and for an edited group who
  changed it, the household date and whether the chat sent another number
  since (a conflict). planned_spending_cents and its chat twin give the live
  left-over preview with planned_income_cents. Totals carry the chat's sum too.
- Editing: an admin, on the current month or a later month the chat planned
  (can_edit, for the signed-in user; the route passes the role and the PUT
  checks it again). A past month, a month with no chat plan and a member say
  why in edit_note, in that order. later_months lists the later months the
  chat planned, which "also later" saves too (empty when can_edit is false).
  Spending groups and Saved and invested are editable, Plan It installments
  never. addable_groups lists the editable groups with no row in groups (the
  chat left them out and nothing was spent), so they can get a first target.
  conflicts counts the groups with a conflict on a month that takes edits (0
  otherwise), for any role.
  edits_notice is set when the edits could not be laid on top: the page then
  shows the chat's plan.
"""

from __future__ import annotations

import sqlite3

from app.importer.models import to_cents
from app.services import budget_targets as bt
from app.services import cashflow as cf
from app.services.coverage import coverage_state, current_month
from app.services.dashboard import budget_status
from app.state import readers

NOT_IN_YET = "Statements not in yet"


def budgets_page(conn: sqlite3.Connection, month: str | None, *, is_admin: bool = False) -> dict:
    state = coverage_state(conn)
    latest = state.latest_complete_month()
    now = current_month()
    month = month or latest or now
    runs, edits = bt.runs_and_edits(conn)
    lab = cf.labels(conn)
    complete = state.month_complete(month)
    plans = cf.included_plan_keys(readers.installment_plans(runs))
    act = cf.actuals(cf.load_rows(conn, month, month), [month], plans)[month]
    view = cf.plan_view(runs, month, edits)
    plan = view.plan
    sent = bt.amounts(view.chat)
    month_ok = month >= now and view.chat is not None
    can_edit = month_ok and is_admin
    if month < now:
        edit_note = bt.PAST_TEXT
    elif view.chat is None:
        edit_note = bt.NO_PLAN_TEXT
    else:
        edit_note = None if is_admin else bt.ADMIN_TEXT
    allowed = bt.editable_keys(conn)

    def target_fields(key: str) -> dict:
        source, edit = bt.provenance(conn, view, key)
        return {
            "chat_plan_cents": sent.get(key),
            "source": source,
            "editable": can_edit and key in allowed,
            "edit": edit,
        }

    chat_budgets = readers.for_month(runs, "budgets", month)
    cat_targets = {
        b.category_key: to_cents(b.target)
        for b in chat_budgets
        if b.category_key and b.period_basis == "calendar"
    }

    def cat_rows(group: str) -> list[dict]:
        keys = {
            k
            for k in set(act.by_category) | set(act.plan_by_category)
            if cf.report_category_group(lab, k) == group and act.plan_category(k)
        }
        keys |= {k for k in cat_targets if cf.report_category_group(lab, k) == group}
        rows = []
        for k in keys:
            name, emoji = lab.category(k)
            actual = act.plan_category(k)
            target = cat_targets.get(k)
            rows.append(
                {
                    "category_key": k,
                    "name": name,
                    "emoji": emoji,
                    "actual_cents": actual,
                    "target_cents": target,
                    "remaining_cents": None if target is None else target - actual,
                    "status": "no_plan" if target is None else budget_status(actual, target),
                }
            )
        rows.sort(key=lambda r: (-r["actual_cents"], r["name"]))
        return rows

    groups = []
    seen = set()
    if plan is not None:
        for g in plan.by_group:
            seen.add(g.key)
            target = to_cents(g.amount)
            if g.key == cf.SAVED_INVESTED:
                kind, actual, cats = "saved_invested", act.saved, []
            elif g.key == cf.PLAN_INSTALLMENTS:
                kind, actual, cats = "plan_installments", act.plan_billed, []
            else:
                kind, actual, cats = "spending", act.plan_group(g.key), cat_rows(g.key)
            groups.append(
                {
                    "key": g.key,
                    "label": lab.group(g.key),
                    "kind": kind,
                    "plan_cents": target,
                    "actual_cents": actual,
                    "remaining_cents": target - actual,
                    "status": budget_status(actual, target),
                    "categories": cats,
                    **target_fields(g.key),
                }
            )
    unplanned = act.plan_groups()
    if act.plan_billed:
        unplanned[cf.PLAN_INSTALLMENTS] = act.plan_billed
    for key, cents in sorted(unplanned.items(), key=lambda kv: (-kv[1], kv[0])):
        if key in seen or cents == 0:
            continue
        installments = key == cf.PLAN_INSTALLMENTS
        groups.append(
            {
                "key": key,
                "label": lab.group(key),
                "kind": "plan_installments" if installments else "spending",
                "plan_cents": None,
                "actual_cents": cents,
                "remaining_cents": None,
                "status": "no_plan",
                "categories": [] if installments else cat_rows(key),
                **target_fields(key),
            }
        )
    if cf.SAVED_INVESTED not in seen and act.saved:
        groups.append(
            {
                "key": cf.SAVED_INVESTED,
                "label": lab.group(cf.SAVED_INVESTED),
                "kind": "saved_invested",
                "plan_cents": None,
                "actual_cents": act.saved,
                "remaining_cents": None,
                "status": "no_plan",
                "categories": [],
                **target_fields(cf.SAVED_INVESTED),
            }
        )
    # Groups that can get a first target but have no row above: the chat left
    # them out and nothing was spent yet. The drawer offers them too.
    addable = []
    if can_edit:
        shown = {g["key"] for g in groups}
        order = dict(conn.execute("SELECT group_key, sort FROM category_groups").fetchall())
        for key in sorted(
            allowed - shown, key=lambda k: (k == cf.SAVED_INVESTED, order.get(k, 0), k)
        ):
            kind = "saved_invested" if key == cf.SAVED_INVESTED else "spending"
            addable.append({"key": key, "label": lab.group(key), "kind": kind})
    planned = sum(g["plan_cents"] or 0 for g in groups)
    actual_total = act.plan_total
    cycle = []
    needed: set[str] = set()
    for b in chat_budgets:
        actual = to_cents(b.reported_actual) if b.reported_actual is not None else None
        target = to_cents(b.target)
        names = [lab.account(k) or k for k in b.statements_needed or []]
        needed.update(names)
        cycle.append(
            {
                "key": b.key,
                "label": b.label,
                "period_basis": b.period_basis,
                "cycle_note": b.cycle_note,
                "category_key": b.category_key,
                "scope_owner": b.scope.owner if b.scope else None,
                "scope_cards": [lab.account(k) or k for k in (b.scope.cards or [])]
                if b.scope
                else [],
                "target_cents": target,
                "plan_cents": to_cents(b.plan) if b.plan is not None else None,
                "reported_actual_cents": actual,
                "actual_source": b.actual_source,
                "status": budget_status(actual, target),
                "statements_needed": names,
                "note": b.note,
            }
        )
    note = None if complete else NOT_IN_YET
    return {
        "month": month,
        "latest_complete_month": latest,
        "current_month": current_month(),
        "complete": complete,
        "note": note,
        "plan_source": "plan" if plan is not None else None,
        "planned_income_cents": to_cents(plan.planned_income) if plan else None,
        "planned_spending_cents": to_cents(plan.planned_spending) if plan else None,
        "chat_planned_spending_cents": to_cents(view.chat.planned_spending) if view.chat else None,
        "actual_income_cents": act.income,
        "totals": {
            "plan_cents": planned if plan is not None else None,
            "chat_plan_cents": sum(sent.values()) if view.chat is not None else None,
            "actual_cents": actual_total,
            "remaining_cents": planned - actual_total if plan is not None else None,
        },
        "can_edit": can_edit,
        "edit_note": edit_note,
        "later_months": bt.later_plan_months(runs, month, edits) if can_edit else [],
        "addable_groups": addable,
        "conflicts": sum(1 for g in view.groups.values() if g.conflict) if month_ok else 0,
        "edits_notice": view.notice,
        "groups": groups,
        "tracked_cards": cf.tracked_cards(conn, lab, [month], {month: complete}),
        "chat_budgets": cycle,
        "statements_needed": sorted(needed),
    }
