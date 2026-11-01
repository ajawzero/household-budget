"""Response models for the Phase 4 screens: Cash Flow, Budget, Recurring, Plans,
Debt, Flags & To do, and the statement coverage grid (PLAN 8.2).

Money is integer cents. Spending-side amounts are positive when money is spent
(refunds net them down). Signed transaction amounts keep the import sign
(negative = money out). Months are `YYYY-MM`, dates `YYYY-MM-DD`. Percent
strings look like "28.2".
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.importer.models import CashflowEventStatus, FlagAction, FlagKind, RecurringTag
from app.services.budget_targets import MAX_CENTS, MAX_CHANGES


class Out(BaseModel):
    model_config = {"extra": "forbid"}


class In(BaseModel):
    """Like txn_models.In: unknown fields are refused."""

    model_config = {"extra": "forbid"}


MonthStatus = Literal["complete", "partial", "current", "future"]
LineStatus = Literal["over", "on_target", "under", "no_plan", "no_actual"]
Measure = Literal["group", "saved_invested", "plan_installments", "unmapped", "unplanned"]


# ---- shared cash flow pieces ----


class Actuals(Out):
    income_cents: int = Field(description="Signed sum of income rows.")
    spending_cents: int = Field(description="Positive = spent. Leaves out saved and invested.")
    saved_invested_cents: int = Field(description="Net moves into household assets.")
    left_over_cents: int = Field(description="income - spending - saved and invested.")
    savings_rate: str | None = Field(description="(income - spending) / income. None at 0.")
    payment_basis_cents: int = Field(description="Part of spending: cards tracked by payments.")
    card_no_statement_cents: int = Field(
        description='Part of spending: "Card spending (no statement)".'
    )
    uncategorized_cents: int = Field(description="Part of spending: uncategorized rows.")
    reimbursable_cents: int = Field(description="Left out of spending: reimbursable charges.")
    count: int = Field(description="Rows counted in income, spending or saved and invested.")


class PlanValues(Out):
    income_cents: int
    spending_cents: int = Field(description="Planned spending. Includes saved and invested.")
    saved_invested_cents: int
    left_over_cents: int = Field(description="Planned income - planned spending.")
    saved_pct: str | None = Field(description="(left over + saved and invested) / income.")


class CashflowMonthOut(Actuals):
    month: str
    status: MonthStatus = Field(
        description="complete, partial (past but not complete), current (this month, "
        "show plan values labeled plan), or future."
    )
    complete: bool
    plan: PlanValues | None


class KpiOut(Actuals):
    month: str = Field(description="The latest complete month.")


class CashflowTotals(Out):
    months: int = Field(description="Complete months counted.")
    income_cents: int
    spending_cents: int
    saved_invested_cents: int
    left_over_cents: int
    savings_rate: str | None


class CashflowAverages(Out):
    months: int
    income_cents: int
    spending_cents: int
    saved_invested_cents: int
    left_over_cents: int


class PlanLine(Out):
    key: str = Field(description="Plan group key, or an unplanned category group key.")
    label: str
    measure: Measure
    plan_cents: int | None
    actual_cents: int | None = Field(description="Null for an unmapped plan group.")
    diff_cents: int | None = Field(description="actual - plan.")
    status: LineStatus


class PlanVsActual(Out):
    month: str
    complete: bool
    planned_income_cents: int
    actual_income_cents: int
    planned_spending_cents: int = Field(description="Includes saved and invested.")
    actual_spending_cents: int = Field(description="Actual spending + saved and invested.")
    diff_cents: int = Field(description="actual - planned.")
    status: LineStatus
    lines: list[PlanLine]


class TrackedCardMonth(Out):
    month: str
    cents: int
    payments_cents: int = Field(description="Payment-basis rows.")
    statement_cents: int = Field(description="Rows of a statement the chat used.")


class TrackedCard(Out):
    account_key: str
    name: str
    months: list[TrackedCardMonth]
    total_cents: int
    avg_cents: int | None = Field(
        description="Average a month over the complete months in range. Null with none."
    )
    last_payment_date: str | None = Field(
        description="Newest payment to the card dated in or before the last month in range: "
        "payment basis or a normal card payment. Null with none."
    )
    last_payment_cents: int | None = Field(
        description="Positive. The payments on that date added up."
    )


class MonthCents(Out):
    month: str
    cents: int


class TrackedCards(Out):
    label: str = Field(description='Like "Sam\'s cards".')
    basis: Literal["payment basis"]
    cards: list[TrackedCard]
    months: list[MonthCents] = Field(description="All cards per month.")
    total_cents: int
    avg_cents: int | None = Field(
        description="All cards, average a month over the complete months in range."
    )
    avg_months: int = Field(description="Complete months in range behind the averages.")


class WaitingReimbursement(Out):
    txn_key: str
    txn_date: str
    merchant: str
    account_name: str
    amount_cents: int = Field(description="Signed, negative.")


# ---- GET /api/cashflow ----


class CashflowOut(Out):
    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    current_month: str
    latest_complete_month: str | None
    kpi: KpiOut | None = Field(description="Latest complete month, for the KPI row.")
    months: list[CashflowMonthOut]
    totals: CashflowTotals = Field(description="Complete months only.")
    averages: CashflowAverages | None = Field(description="Complete months only.")
    plan_vs_actual: list[PlanVsActual] = Field(description="Every month in range with a plan.")
    tracked_cards: TrackedCards | None
    waiting_reimbursement: list[WaitingReimbursement]
    waiting_reimbursement_cents: int = Field(description="Positive total.")

    model_config = {"extra": "forbid", "populate_by_name": True}


# ---- GET /api/cashflow/breakdown ----


class BreakdownItem(Out):
    key: str = Field(
        description="Category key, group key, or a lowercased merchant label. "
        "'card-no-statement' for card payments with no statement."
    )
    label: str
    emoji: str | None
    group_key: str | None
    group_label: str | None
    cents: int
    count: int
    payment_basis_cents: int
    share_pct: str | None = Field(description="Of total_cents. None when the total is 0 or less.")


class BreakdownOut(Out):
    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    by: Literal["category", "group", "merchant"]
    flow: Literal["spending", "income"]
    complete: bool
    partial_months: list[str]
    total_cents: int
    saved_invested_cents: int = Field(description="For a separate bar. 0 for income.")
    items: list[BreakdownItem] = Field(description="Largest first.")

    model_config = {"extra": "forbid", "populate_by_name": True}


# ---- GET /api/cashflow/category/{key} ----


class CategoryMonth(Out):
    month: str
    cents: int
    count: int
    complete: bool
    status: MonthStatus
    budget_cents: int | None = Field(description="The chat's calendar budget for the category.")
    group_plan_cents: int | None = Field(description="The plan for the category's group.")


class MerchantTotal(Out):
    label: str
    cents: int
    count: int


class CategoryDetailOut(Out):
    category_key: str
    name: str
    emoji: str | None
    kind: str
    group_key: str
    group_label: str
    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    months: list[CategoryMonth]
    total_cents: int
    average_cents: int | None = Field(description="Complete months only.")
    top_merchants: list[MerchantTotal] = Field(description="Top 5 in the range.")

    model_config = {"extra": "forbid", "populate_by_name": True}


# ---- GET /api/cashflow/plan ----


class PlanBillLine(Out):
    key: str = Field(description="The recurring item key.")
    name: str
    cents: int = Field(description="Its net cents for the month (the by-month amount).")
    change: Literal["new", "rate_up", "rate_down"] | None = Field(
        description="The item's change since the update before (C6), as on Recurring."
    )
    previous_cents: int | None = Field(
        description="Its by-month cents for this month in that earlier run. Only for rate_up "
        "and rate_down, and only when it moved the same way."
    )


class GroupContents(Out):
    """What is in a plan group (E5), for the line under its bar."""

    kind: Literal["bills", "transfers", "categories"] = Field(
        description="bills or transfers: the group's plan lines that month, when they make up "
        "at least half of it. categories: its spending categories otherwise."
    )
    names: list[str] = Field(
        description="Up to 3. Bills and transfers largest first; categories in list order."
    )
    more: int = Field(description="How many more are not named.")


class PlanGroupOut(Out):
    key: str
    label: str
    cents: int
    kind: Literal["spending", "saved_invested"]
    lines: list[PlanBillLine] = Field(
        description="The counted recurring bills in this group that month, largest first. "
        "Empty for Plan It installments."
    )
    other_cents: int = Field(
        description="Everything else: cents minus the lines. Negative when the bills are more "
        "than the plan."
    )
    contents: GroupContents | None = Field(
        description="What is in the group. Null for Plan It installments and a group with "
        "nothing to name."
    )


class PlanMappingOut(Out):
    key: str
    label: str
    measure: Literal["group", "saved_invested", "plan_installments", "unmapped"]
    maps_to: str


class PlanMonthOut(PlanValues):
    month: str
    status: MonthStatus
    note: str | None
    by_group: list[PlanGroupOut]
    mapping: list[PlanMappingOut]
    vs_actual: PlanVsActual | None = Field(description="Null for future months.")


class EventOut(Out):
    key: str
    effective_month: str
    delta_cents: int
    label: str
    reason: str | None
    linked_kind: str | None
    linked_key: str | None
    upcoming: bool
    status: CashflowEventStatus | None = Field(
        description="Format 1.4: confirmed, expected, target or suggested. Null when the "
        "run leaves it out (no badge)."
    )


class PhaseOut(Out):
    key: str
    label: str
    start_month: str
    end_month: str | None
    surplus_cents: int
    reason: str | None
    note: str | None
    current: bool


class CashflowPlanOut(Out):
    current_month: str
    source_run_id: str | None
    months: list[PlanMonthOut]
    events: list[EventOut] = Field(description="By effective month.")
    phases: list[PhaseOut] = Field(description="By start month.")


# ---- GET /api/income ----


class StreamOut(Out):
    key: str
    person: str
    person_label: str
    label: str
    kind: Literal["paycheck", "benefit", "draw", "bonus"]
    schedule: str
    expected_net_cents: int
    start_month: str | None
    end_month: str | None
    note: str | None
    active: bool


class StreamMonth(Out):
    key: str
    deposits_cents: int = Field(description="Income rows with recurring_key = the stream key.")
    deposit_count: int


class PaycheckSum(Out):
    person: str
    person_label: str
    count: int
    gross_cents: int
    net_cents: int


class IncomeMonth(Out):
    month: str
    status: MonthStatus
    complete: bool
    income_cents: int = Field(description="Cash flow income.")
    other_income_cents: int = Field(description="Income rows not tied to a stream.")
    streams: list[StreamMonth]
    paychecks: list[PaycheckSum]


class PaycheckOut(Out):
    person: str
    person_label: str
    pay_date: str
    kind: Literal["regular", "bonus", "draw"]
    seq: int
    gross_cents: int
    net_cents: int


class IncomeOut(Out):
    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    source_run_id: str | None
    streams: list[StreamOut]
    months: list[IncomeMonth]
    paychecks: list[PaycheckOut] = Field(description="In range, by pay date.")

    model_config = {"extra": "forbid", "populate_by_name": True}


# ---- GET /api/budgets ----


class BudgetCategory(Out):
    category_key: str
    name: str
    emoji: str | None
    actual_cents: int
    target_cents: int | None = Field(description="Only when the chat sent a calendar budget.")
    remaining_cents: int | None
    status: Literal["over", "on_target", "under", "no_plan"]


class BudgetEdit(Out):
    by: str | None = Field(description="Who changed it. Null when that user is gone.")
    at: str = Field(description="The household date of the change.")
    conflict: bool = Field(description="The chat sent another number since the edit.")


class BudgetGroup(Out):
    key: str
    label: str
    kind: Literal["spending", "saved_invested", "plan_installments"]
    plan_cents: int | None = Field(
        description='The plan, labeled "plan", with edits on top. Null: unplanned.'
    )
    actual_cents: int
    remaining_cents: int | None = Field(description="plan - actual. Negative = over.")
    status: Literal["over", "on_target", "under", "no_plan"]
    categories: list[BudgetCategory]
    chat_plan_cents: int | None = Field(description="The chat's number. Null: it sends none.")
    source: Literal["chat", "edited"]
    editable: bool = Field(description="The signed-in user can change it this month.")
    edit: BudgetEdit | None = Field(description="Set only when source is edited.")


class BudgetAddable(Out):
    key: str
    label: str
    kind: Literal["spending", "saved_invested"]


class BudgetTotals(Out):
    plan_cents: int | None
    chat_plan_cents: int | None = Field(description="The sum of the chat's group targets.")
    actual_cents: int = Field(description="Spending + saved and invested.")
    remaining_cents: int | None


class ChatBudget(Out):
    key: str
    label: str
    period_basis: Literal["calendar", "card_cycle"]
    cycle_note: str | None
    category_key: str | None
    scope_owner: str | None
    scope_cards: list[str] = Field(description="Card names.")
    target_cents: int
    plan_cents: int | None
    reported_actual_cents: int | None = Field(description="As the chat reported it.")
    actual_source: Literal["statement", "baseline"] | None
    status: Literal["over", "on_target", "under", "no_actual"]
    statements_needed: list[str] = Field(description="Account names.")
    note: str | None


class BudgetsOut(Out):
    month: str
    latest_complete_month: str | None
    current_month: str
    complete: bool
    note: str | None = Field(description='"Statements not in yet" for an incomplete month.')
    plan_source: Literal["plan"] | None
    planned_income_cents: int | None
    planned_spending_cents: int | None = Field(description="With edits on top.")
    chat_planned_spending_cents: int | None
    actual_income_cents: int
    totals: BudgetTotals
    groups: list[BudgetGroup] = Field(description="Plan order, then unplanned largest first.")
    tracked_cards: TrackedCards | None
    chat_budgets: list[ChatBudget] = Field(description="The chat's budgets for the month.")
    statements_needed: list[str]
    can_edit: bool = Field(
        description="The signed-in user can change targets for the month: an admin, on the "
        "current month or a later one the chat planned."
    )
    edit_note: str | None = Field(
        description="Why not, when can_edit is false: a past month, no chat plan, or not an admin."
    )
    later_months: list[str] = Field(
        description="Later months the chat planned, which also_later saves too. Empty when "
        "can_edit is false."
    )
    addable_groups: list[BudgetAddable] = Field(
        description="Groups that can get a first target but are not in groups: the chat left "
        "them out and nothing was spent. Empty when can_edit is false."
    )
    conflicts: int = Field(
        description="Edited groups the chat changed since. 0 on a month that takes no edits."
    )
    edits_notice: str | None = Field(description="Set when the edits could not be shown.")


# ---- PUT /api/budgets/targets ----

GroupKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
TargetMonth = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


class TargetChangeIn(In):
    group_key: GroupKey
    target_cents: int | None = Field(
        ge=0, le=MAX_CENTS, description="The new target. Null: use the chat's (the edit goes)."
    )
    seen_chat_cents: int | None = Field(
        description="The chat's number the page showed (chat_plan_cents). A 409 when it moved."
    )


class BudgetTargetsIn(In):
    month: TargetMonth
    changes: list[TargetChangeIn] = Field(
        min_length=1, max_length=MAX_CHANGES, description="One per group. All save or none do."
    )
    also_later: bool = Field(
        default=False,
        description="Also save each change for the later months the chat already planned.",
    )


# ---- GET /api/recurring and /api/recurring/calendar ----

Cadence = Literal[
    "weekly",
    "biweekly",
    "every_n_weeks",
    "semi_monthly",
    "monthly",
    "quarterly",
    "semester",
    "semi_annual",
    "annual",
]
RecurringStatus = Literal["active", "paused", "ending", "ended"]


class RecurringBase(Out):
    key: str
    name: str
    category_key: str
    category_name: str | None
    category_emoji: str | None
    account_key: str | None
    account_name: str | None
    cadence: Cadence
    status: RecurringStatus
    variable: bool


class DueOut(RecurringBase):
    due_date: str
    expected_cents: int = Field(description="Positive, per occurrence.")
    credit_offset_cents: int | None
    state: Literal["paid", "upcoming", "waiting", "no_history", "missed"] = Field(
        description=(
            "waiting: past due, but the paying account's statements are not in yet. "
            "no_history: past due and unpaid, and no tagged payment goes back that far "
            "(none yet, before the item's first one, or before Jan 1, 2026). "
            "missed: the same, for an item with payment history."
        )
    )
    paid_cents: int | None = Field(description="Positive.")
    paid_date: str | None
    txn_key: str | None


class RecurringCostCut(Out):
    key: str
    title: str
    status: Literal["open", "watching"]
    due_date: str | None


class RecurringSection(Out):
    key: str = Field(
        description="The plan group of the item's category (a category group key), "
        "`<group>.<tag>` for a tag section, or `savings` for transfers and allowances."
    )
    label: str
    kind: Literal["bills", "tag", "savings"]
    group_key: str | None = Field(
        description="Tag sections only: the plan group they follow. Null for the others."
    )
    group_label: str | None = Field(description="Tag sections only: that group's name.")


class RecurringItem(RecurringBase):
    amount_cents: int
    credit_offset_cents: int | None
    credit_note: str | None
    interval_n: int | None
    active_months: list[int] | None
    occurrences_per_year: str = Field(description='Like "12" or "17.33".')
    monthly_equivalent_cents: int
    net_monthly_equivalent_cents: int = Field(description="After credit_offset.")
    counted: bool = Field(description="False for paused and ended items (not in totals).")
    next_due: str | None = Field(description="Computed, today or later.")
    next_date: str | None = Field(description="As the chat sent it.")
    start_date: str | None
    end_date: str | None
    renewal_date: str | None
    renews_on: str | None = Field(
        description="renewal_date when it is today or later (household date) and the item "
        "runs until then: null for paused, ended or ending-before-renewal items."
    )
    ends_on: str | None = Field(
        description="Last date it runs: end_date, or active_through for an ending item, "
        "whichever is earlier. Null for paused and ended items, and once the date has "
        "passed (household date)."
    )
    active_through: str | None
    review_by: str | None
    payment_rail: str | None
    preferred_account_key: str | None
    preferred_account_name: str | None
    owner: str | None
    owner_label: str | None
    notes: str | None
    keep: Literal[True] | None = Field(
        description="True when the household chose to keep the bill after a review "
        "(format 1.4). Null otherwise."
    )
    tag: RecurringTag | None = Field(
        description="streaming, hobbies or software (format 1.4). Never on the personal "
        "subscription. A tagged bill's section is its tag section."
    )
    last_paid_date: str | None
    last_paid_cents: int | None
    cost_cut: RecurringCostCut | None = Field(
        description="The chat's unresolved cost cutting flag on this item (related_kind "
        "recurring, not archived, not an info flag). Open first, then the newest."
    )
    group_key: str | None = Field(description="The category's group: the plan group.")
    section_key: str = Field(
        description="Key of its section in `sections`: the plan group, or `<group>.<tag>` "
        "for a tagged bill."
    )
    by_month_cents: list[int] = Field(
        description="Net cents in each month, January to December: the yearly net cost "
        "spread evenly over the months it runs (active_months, else all 12)."
    )
    change: Literal["new", "rate_up", "rate_down"] | None = Field(
        description="Against the monthly run before the newest one that has `recurring`: "
        "new (key not there), or a higher or lower monthly equivalent (not for variable "
        "bills). Null with no earlier run, and for paused and ended items."
    )
    previous_monthly_equivalent_cents: int | None = Field(
        description="The monthly equivalent in that earlier run, for rate_up and rate_down."
    )
    to_account_key: str | None = Field(
        description="Savings section only: the account the newest paying row moved money to."
    )
    to_account_name: str | None


class RecurringSummary(Out):
    expected_cents: int
    paid_cents: int
    remaining_cents: int = Field(description="Expected amount of the unpaid dates.")
    count: int
    paid_count: int


class RecurringTotals(Out):
    """Counted bills only: the savings section (transfers and allowances) is left out."""

    monthly_equivalent_cents: int
    net_monthly_equivalent_cents: int
    count: int


class RecurringOut(Out):
    view: Literal["monthly", "all"]
    month: str
    source_run_id: str | None
    summary: RecurringSummary | None = Field(description="Monthly view only.")
    upcoming: list[DueOut] = Field(description="Monthly view: not paid yet.")
    complete: list[DueOut] = Field(description="Monthly view: paid.")
    items: list[RecurringItem] = Field(description="All view only.")
    totals: RecurringTotals | None = Field(description="All view only.")
    sections: list[RecurringSection] = Field(
        default_factory=list,
        description="All view only: the sections in display order, bills first by "
        "category group order, each followed by its tag sections (Streaming, Hobbies, Apps "
        "and software), then savings. Only sections with items.",
    )


class CalendarDay(Out):
    date: str
    items: list[DueOut]
    expected_cents: int


class RecurringCalendarOut(Out):
    month: str
    today: str
    first_weekday: int = Field(description="Weekday of the 1st, 0 = Sunday.")
    days_in_month: int
    days: list[CalendarDay] = Field(description="Only days with a due date.")


# ---- GET /api/plans ----


class PlanComponentOut(Out):
    merchant: str
    amount_cents: int
    trip_key: str | None
    trip_name: str | None = Field(
        description="The trip's name, or null when the app has no trip with that key."
    )


class PlanWarning(Out):
    kind: Literal["pay_before", "promo_expiry"]
    date: str
    days_left: int = Field(description="Negative once the date has passed.")
    level: Literal["high", "medium", "low"]


class InstallmentPlanOut(Out):
    key: str
    name: str
    kind: Literal[
        "plan_it", "pay_over_time", "promo_0pct", "deferred_interest", "apple_installment"
    ]
    kind_label: str
    status: Literal["active", "paid_off", "closed"]
    account_key: str
    account_name: str
    created_on: str
    original_cents: int
    monthly_payment_cents: int | None
    monthly_fee_cents: int | None
    monthly_cost_cents: int = Field(description="Payment + fee.")
    total_fees_cents: int | None
    payments_total: int | None
    payments_left: int | None
    payments_made: int | None
    progress_label: str | None = Field(description='"8 of 12".')
    remaining_cents: int | None = Field(description="Information only. Never net worth.")
    end_month: str | None
    promo_expiry: str | None
    pay_before: str | None
    included_in_spending_plan: bool
    components: list[PlanComponentOut]
    billed_count: int = Field(description="Installment rows billed on the card's statements.")
    fees_posted_cents: int = Field(description="Plan fee rows on the card's statements.")
    warning: PlanWarning | None
    note: str | None = Field(description="Format 1.4: the chat's note on the plan.")
    paid_off_on: str | None = Field(description="Format 1.4. Only on a paid off or closed plan.")
    paid_off_early: bool = Field(description="paid_off_on falls in a month before end_month.")
    refund_cents: int = Field(
        description="Refund rows with this plan's key, hidden rows left out. 0 when none."
    )


class PlanCard(Out):
    account_key: str
    account_name: str
    plans: list[InstallmentPlanOut]
    remaining_cents: int = Field(description="Active plans.")
    monthly_cents: int = Field(description="Active plans.")


class PlanBrief(Out):
    key: str
    name: str
    end_month: str | None
    payments_left: int | None


class StepDown(Out):
    month: str
    monthly_cents: int
    drop_cents: int = Field(description="Drop from the month before.")
    ended: list[str] = Field(description="Plan keys whose last month was the month before.")
    plan_count: int


class PlanWarningRow(PlanWarning):
    plan_key: str
    name: str
    account_name: str


class PlansOut(Out):
    source_run_id: str | None
    today: str
    active_count: int
    total_deferred_cents: int = Field(description="Sum of remaining over active plans.")
    remaining_unknown: list[str] = Field(description="Active plan keys with no remaining.")
    monthly_total_cents: int
    shortest: PlanBrief | None
    longest: PlanBrief | None
    open_ended: list[str] = Field(description="Active plans with no end month.")
    cards: list[PlanCard]
    step_downs: list[StepDown] = Field(description="This month to the month after the last end.")
    warnings: list[PlanWarningRow] = Field(description="Pay-before and promo dates, soonest first.")


# ---- GET /api/debt ----

RATED = (
    "Rated debts that carry their rate: cards on statement or full autopay and promo rates"
    " left out."
)


class DebtTarget(Out):
    kind: Literal["promo", "payoff_date"] = Field(
        description="promo: the earliest 0% promo or deferred interest date ahead."
        " payoff_date: the chat's payoff date, when ahead."
    )
    date: str
    by_month: str = Field(
        description="The last month a payment counts: the date's month, or for a promo the"
        " promo month (the month before when the date is not a month end)."
    )
    monthly_cents: int = Field(
        description="The level monthly payment that pays it off by by_month. When by_month is"
        " the balance's month or earlier, the whole balance."
    )
    on_track: bool | None = Field(
        description="The pace pays it off by by_month. None: no pace, or no payment month left."
    )


class DebtProjection(Out):
    """Rules in services/payoff.py. Screens only, never the AI."""

    from_month: str = Field(description="The month of the balance. The first payment is after.")
    pace_cents: int | None = Field(
        description="A month toward the balance: payments less new charges, interest included."
        " Zero or less: not going down. None: nothing to go on."
    )
    pace_basis: Literal["statements", "payments"] | None
    pace_from: str | None = Field(description="The pace window's first month.")
    pace_through: str | None = Field(description="The pace window's last month.")
    promo_until: str | None = Field(
        description="A promo rate is 0% through this date's month, then the APR. None with a"
        " promo rate: 0% all along."
    )
    promo_month: str | None = Field(
        description="The last 0% month of a promo rate (rules in services/payoff.py)."
    )
    months: int | None = Field(description="Payments to payoff at the pace. None: not in 50 years.")
    payoff_month: str | None
    interest_to_go_cents: int | None = Field(description="Interest from here to payoff.")
    last_payment_cents: int | None = Field(description="The payment in the payoff month.")
    early_cents: int | None = Field(
        description="One payment in the month before the payoff month that clears it."
    )
    targets: list[DebtTarget]


class DebtRateChange(Out):
    effective_on: str
    apr_milli_pct: int = Field(description="Thousandths of a percent, like apr_milli_pct.")


class DebtRow(Out):
    account_key: str
    name: str
    institution: str
    type: str
    owed_cents: int = Field(description="Positive.")
    apr_milli_pct: int | None
    rate_type: str | None
    priority: int | None
    required_cents: int | None = Field(description="min_payment, else the newest minimum due.")
    paying_cents: int | None = Field(description="Paid in during paying_month.")
    credit_limit_cents: int | None
    utilization_pct: str | None
    original_cents: int | None = Field(
        description="The loan amount when it was made (format 1.4, loans and mortgages)."
    )
    paid_down_pct: str | None = Field(
        description='(original - owed) / original, like "18.4". None with no original, or when'
        " more is owed than the original."
    )
    rate_changes: list[DebtRateChange] = Field(
        description="Scheduled rates that count, oldest first: a variable rate with a known APR"
        " only, from the first month after the balance's month (rules in services/payoff.py)."
        " Empty: none."
    )
    payoff_date: str | None
    payoff_ahead: bool = Field(description="payoff_date is today or later (household date).")
    policy_note: str | None
    autopay: str | None
    plans_remaining_cents: int | None = Field(description="Active installment plans on it.")
    interest_cents: int | None = Field(
        description="Interest charged in paying_month: -sum of txn_type interest rows on this"
        " account. None when no statement ends that month."
    )
    interest_ytd_cents: int | None = Field(
        description="Interest from Jan 1 of the household year to today. None when no"
        " statement ends this year."
    )
    statement_end: str | None = Field(description="The newest statement's period end.")
    statement_owed_cents: int | None = Field(
        description="Owed on the newest statement (-closing). Positive."
    )
    statement_due_date: str | None = Field(description="The newest statement's due date.")
    servicer: str | None
    notes: str | None = Field(description="The chat's note. Screens only, never the AI.")
    projection: DebtProjection | None = Field(
        description="None for a card paid in full each month (statement or full autopay)."
    )


class DebtPick(Out):
    account_key: str
    name: str
    apr_milli_pct: int | None
    owed_cents: int


class TrackedDebt(Out):
    account_key: str
    name: str
    autopay: str | None


class PayoffStepOut(Out):
    key: str
    month: str | None
    when_label: str
    action: str


class DebtOut(Out):
    as_of: str
    paying_month: str | None = Field(description="The latest complete month.")
    total_debt_cents: int
    highest_rate: DebtPick | None = Field(description=RATED)
    cheapest: DebtPick | None = Field(description=RATED)
    ledger: list[DebtRow] = Field(description="By priority (unset last), then rate.")
    tracked_cards: list[TrackedDebt] = Field(description='"Tracked by payments": no balance.')
    payoff_sequence: list[PayoffStepOut]
    source_run_id: str | None


# ---- GET /api/flags, /api/todos, /api/pending ----


class FlagUpdateOut(Out):
    on: str = Field(description="YYYY-MM-DD.")
    text: str


class FlagOut(Out):
    key: str
    severity: Literal["high", "medium", "low"]
    status: Literal["open", "watching", "resolved"]
    kind: FlagKind
    action: FlagAction = Field(
        description="What to do. A flag the chat sent with no action reads as act. "
        "info: nothing to do."
    )
    title: str
    body: str | None
    updates: list[FlagUpdateOut] = Field(
        description="Dated notes, newest first (same day: as stored). Empty when none."
    )
    related_kind: str | None
    related_key: str | None
    related_label: str | None = Field(
        description="The related item's name. For a trip, null when the app has no such trip."
    )
    due_date: str | None
    overdue: bool = Field(description="Past due and not resolved. Never on an info flag.")
    created_on: str
    resolved_on: str | None
    archived: bool


class FlagCounts(Out):
    high: int = Field(description="Not archived. Leaves out info flags not resolved.")
    medium: int = Field(description="Not archived. Leaves out info flags not resolved.")
    low: int = Field(description="Not archived. Leaves out info flags not resolved.")
    open: int = Field(description="Not archived, not info.")
    watching: int = Field(description="Not archived, not info.")
    resolved: int = Field(description="Not archived, info flags included.")
    info: int = Field(description="Info flags not resolved and not archived. In no other count.")


class FlagsOut(Out):
    source_run_id: str | None
    flags: list[FlagOut] = Field(
        description="Unresolved first, by severity, status and due date; then unresolved "
        "info flags; then resolved."
    )
    archived_count: int
    counts: FlagCounts = Field(description="Not archived.")


class TodoItem(Out):
    key: str
    title: str
    subtitle: str | None
    status: Literal["open", "done"]
    due_date: str | None
    overdue: bool
    flag_key: str | None
    flag_title: str | None


class TodosOut(Out):
    source_run_id: str | None
    todos: list[TodoItem] = Field(description="Open first, by due date.")
    open_count: int
    done_count: int


class PendingOut(Out):
    key: str
    kind: Literal["apply", "confirm"]
    subject: str
    detail: str | None
    status: Literal["open", "waiting", "done"]
    added_on: str
    updated_on: str | None


class PendingCounts(Out):
    open: int
    waiting: int
    done: int


class PendingListOut(Out):
    source_run_id: str | None
    items: list[PendingOut]
    counts: PendingCounts


# ---- GET /api/statements/coverage ----


class CoverageCell(Out):
    month: str
    status: Literal["present", "missing", "not_expected", "tracked"]
    period_end: str | None
    reconciled: bool | None


class CoverageAccount(Out):
    account_key: str
    name: str
    institution: str
    owner_label: str
    type: str
    statement_cadence: Literal["monthly", "quarterly", "report", "none"]
    tracked_by: Literal["statements", "payments"]
    in_cashflow: bool
    active: bool
    covered_from: str | None
    covered_through: str | None
    gap_after: str | None = Field(description="Period end where the first gap starts.")
    cells: list[CoverageCell]


class CoverageOut(Out):
    months: list[str]
    accounts: list[CoverageAccount]
    complete_months: list[str]
    latest_complete_month: str | None
    today: str
