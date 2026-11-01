"""Response models for the Phase 2 API, so /openapi.json is fully typed.

Money is integer cents, signed from the net worth view (assets positive, owed
negative) unless a field says positive. Dates are ISO `YYYY-MM-DD`, months `YYYY-MM`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.api.ai_models import AiReview
from app.api.p4_models import GroupContents
from app.api.rewards_models import CreditsEnding, RewardsCheck
from app.api.trips_models import NextTrip, TripsCheck
from app.api.utility_models import (
    UtilityPastDue,
    UtilityPaymentNoBill,
    UtilitySummary,
    UtilityWarning,
)
from app.importer.models import CashflowEventStatus

RangeKey = Literal["1M", "3M", "6M", "YTD", "1Y", "3Y", "ALL"]
# Only the Investments value chart takes 1D and 7D.
InvestmentRangeKey = Literal["1D", "7D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "ALL"]
GroupKey = Literal[
    "cash", "credit_cards", "investments", "retirement", "real_estate", "vehicles", "loans", "other"
]
SeriesKey = Literal[
    "networth",
    "cash",
    "credit_cards",
    "investments",
    "retirement",
    "real_estate",
    "vehicles",
    "loans",
]
BalanceSourceOut = Literal["statement", "valuation", "manual", "estimate", "tracked"]


class Out(BaseModel):
    model_config = {"extra": "forbid"}


# ---- shared ----


class RangeOut(Out):
    key: RangeKey
    start: str
    end: str


class BalancePoint(Out):
    date: str
    balance_cents: int


class Equity(Out):
    value_cents: int
    loan_cents: int = Field(description="Signed: money owed on the secured loans, negative.")
    equity_cents: int
    underwater: bool


class EstimateOut(Out):
    kind: str = Field(description="price (Phase 5); other estimators add their own kinds.")
    based_on: str = Field(description="Date of the real balance or statement it starts from.")
    through: str | None = Field(description="Newest price or index date used.")
    note: str = Field(description="Tooltip: how it was made and from what date.")


class EstimatedAccount(Out):
    account_key: str
    display_name: str
    kind: str = Field(description="price, home, vehicle or loan.")
    based_on: str = Field(description="Date of the real balance it starts from.")
    note: str


class AccountRow(Out):
    account_key: str
    display_name: str
    institution: str
    owner: str
    owner_label: str
    type: str
    subtype: str | None
    group: GroupKey
    nw_group: str | None
    is_asset: bool
    include_in_net_worth: bool
    tracked_by: Literal["statements", "payments"]
    active: bool
    dormant: bool = Field(description="The chat marks it not in use.")
    balance_cents: int | None = Field(description="None: no balance, or tracked by payments.")
    as_of: str | None
    source: BalanceSourceOut | None = Field(description="None for no balance or a closed account.")
    stale: bool
    missing: bool
    change_cents: int | None = Field(
        description="End minus the first balance in range. An account opened in range "
        "counts from its first balance, not from 0."
    )
    sparkline: list[BalancePoint]
    equity: Equity | None
    estimate: EstimateOut | None = Field(
        description="Set when balance_cents is a computed estimate (est. badge). Never stored."
    )


class TransactionRow(Out):
    txn_key: str
    txn_date: str
    post_date: str | None
    account_key: str
    account_name: str
    description: str
    merchant: str | None
    category_key: str
    category_name: str | None
    cardholder: str | None
    cardholder_label: str | None = Field(description="The person's display name.")
    amount_cents: int
    is_transfer: bool
    is_reimbursable: bool


class TransactionsOut(Out):
    rows: list[TransactionRow]
    next_cursor: str | None


# ---- GET /api/accounts ----


class AccountGroup(Out):
    group: GroupKey
    label: str
    is_liability: bool
    total_cents: int
    change_cents: int
    change_pct: str | None = Field(
        description='Like "4.2", against the group at the start. None when that is 0.'
    )
    estimated: bool = Field(description="A counted account's end balance is an estimate.")
    accounts: list[AccountRow]


class SummaryPart(Out):
    key: str
    label: str
    cents: int
    is_liability: bool


class AccountsSummary(Out):
    assets_cents: int
    liabilities_cents: int = Field(description="Positive: money owed.")
    net_worth_cents: int
    by_type: list[SummaryPart]
    by_owner: list[SummaryPart] = Field(description="By nw_group.")


class AccountsNotes(Out):
    partial: bool
    missing: list[str]
    stale: list[str]
    tracked: list[str]
    excluded: list[str] = Field(
        description="Statement-tracked accounts with include_in_net_worth false."
    )
    estimated: list[EstimatedAccount] = Field(
        description="Accounts in net worth whose end balance is a computed estimate."
    )


class AccountsOut(Out):
    as_of: str
    range: RangeOut
    groups: list[AccountGroup]
    summary: AccountsSummary
    notes: AccountsNotes


# ---- GET /api/networth/series ----


class SeriesPoint(Out):
    date: str
    value_cents: int
    partial: bool
    estimated: bool = Field(description="At least one account in it is an estimate that day.")


class SeriesOut(Out):
    series: SeriesKey
    range: RangeOut
    points: list[SeriesPoint] = Field(description="One point per day.")
    start_cents: int = Field(
        description="end_cents - change_cents. Accounts missing at the start count from "
        "their first point."
    )
    end_cents: int
    change_cents: int
    change_pct: str | None = Field(description='Like "4.2". None when start is 0.')
    estimated_accounts: list[EstimatedAccount] = Field(
        description="Accounts in the series whose end balance is an estimate."
    )


# ---- GET /api/accounts/{account_key} ----


class DetailPoint(Out):
    date: str
    balance_cents: int
    estimated: bool = Field(description="A computed estimate (est.), never stored.")
    real: bool = Field(description="A stored statement, valuation or balance on this day.")


class StatementOut(Out):
    period_start: str
    period_end: str
    opening_cents: int | None
    closing_cents: int
    balance_cents: int | None
    net_flows_cents: int | None = Field(description="Investment statements. Positive: money in.")
    market_change_cents: int | None = Field(description="Investment statements. Positive: a gain.")
    txn_count: int
    reconciled: bool


class HoldingOut(Out):
    key: str
    name: str
    symbol: str | None
    quantity: str | None = Field(description="Decimal string.")
    price: str | None = Field(description="Decimal string.")
    value_cents: int
    asset_class: str | None


class Terms(Out):
    apr_milli_pct: int | None
    rate_type: str | None
    payoff_date: str | None
    priority: int | None
    policy_note: str | None
    credit_limit_cents: int | None
    original_cents: int | None = Field(
        description="The loan amount when it was made (format 1.4, loans and mortgages)."
    )
    min_payment_cents: int | None
    autopay: str | None


class AccountAbout(Out):
    """Registry facts from the export. Screens only, never sent to the AI."""

    custodian: str | None
    advisor: str | None = Field(description='Shown as "Managed by".')
    servicer: str | None
    notes: str | None = Field(
        description="The chat's status text. Screens only, never sent to the AI."
    )
    opened_on: str | None
    closed_on: str | None


class FlowPeriod(Out):
    period_start: str = Field(description="The first statement's real start.")
    period_end: str
    statements: int
    gaps: int = Field(
        description="Breaks between the statements (one is missing). Return is None when above 0."
    )
    split_statements: int = Field(description="Statements with net_flows and market_change.")
    net_flows_cents: int | None = Field(description="Money in, net. Negative: more came out.")
    market_change_cents: int | None = Field(description="Positive: a gain.")
    return_pct: str | None = Field(
        description="Market change over each opening, chained. One decimal. None unless every "
        "statement has the split and an opening above 0."
    )
    fees_paid_cents: int | None = Field(description="Positive: fees taken. None: no fees given.")
    fee_statements: int = Field(description="Statements that give fees.")
    fee_months: int = Field(description="Months those statements cover. A quarter is 3.")
    fees_per_month_cents: int | None
    dividends_cents: int | None = Field(description="Dividend rows. None when there are none.")


class InvestmentSummary(Out):
    year: int = Field(description="Household year.")
    latest: FlowPeriod = Field(description="Newest current statement.")
    ytd: FlowPeriod | None = Field(description="Statements ending in the household year.")


class AccountDetailOut(Out):
    account: AccountRow
    points: list[DetailPoint] = Field(description="Daily, days with a balance only.")
    statements: list[StatementOut] = Field(description="Newest first, current versions.")
    investment: InvestmentSummary | None = Field(
        description="Investment types with statement numbers only. Ignores the range."
    )
    recent_transactions: list[TransactionRow] = Field(description="The 20 newest.")
    holdings: list[HoldingOut] = Field(description="From the latest statement with holdings.")
    terms: Terms
    about: AccountAbout


# ---- GET /api/dashboard ----


class NetWorthBehind(Out):
    account_key: str
    name: str
    reason: Literal["no_balance", "overdue"]
    as_of: str | None = Field(description="The last balance's date (overdue only).")
    source: Literal["statement", "manual", "valuation", "estimate"] | None = Field(
        description="Where the last balance came from (overdue only)."
    )
    due: str | None = Field(
        description="When the next balance was due: one statement period later, or "
        "stale_after_days. None for no balance, or an account with neither."
    )


class DashboardNetWorth(Out):
    value_cents: int
    change_cents: int = Field(description="Versus 30 days earlier.")
    assets_cents: int
    liabilities_cents: int = Field(description="Positive: money owed.")
    as_of: str
    balances_through: str | None = Field(
        description="Newest balance date behind the total (statement, valuation or manual)."
    )
    partial: bool
    behind: list[NetWorthBehind] = Field(
        description="The accounts that make it partial: no balance first, then by name."
    )


class PlanMonth(Out):
    month: str
    income_cents: int
    spending_cents: int = Field(description="Planned spending. Includes saved and invested.")
    saved_invested_cents: int = Field(
        description="The plan's saved-invested group: the part of spending that is saved."
    )
    left_over_cents: int = Field(description="income - spending.")
    saved_pct: str | None = Field(
        description='(left over + saved and invested) / income, like "28.2".'
    )
    basis: Literal["plan"]


class CashflowMonth(Out):
    month: str
    income_cents: int
    spending_cents: int = Field(description="Includes saved and invested.")
    saved_invested_cents: int
    left_over_cents: int = Field(description="income - spending.")
    basis: Literal["plan", "actual"] = Field(
        description="actual: the app's numbers for a past month. plan: this month or later."
    )
    complete: bool = Field(description="An actual month whose statements are all in.")


class SpendingGroup(Out):
    key: str
    label: str
    cents: int = Field(description="Positive: planned amount.")
    note: str | None
    kind: Literal["spending", "saved_invested"] = Field(
        description="saved_invested: the Saved and invested bar, drawn apart from spending."
    )
    basis: Literal["plan", "actual"]
    contents: GroupContents | None = Field(
        description="What is in the group (E5): its bills that month when they make up at "
        "least half of it, else its categories."
    )


class ActualMonth(Out):
    month: str = Field(description="The latest complete month.")
    income_cents: int
    spending_cents: int = Field(description="Leaves out saved and invested.")
    saved_invested_cents: int
    left_over_cents: int = Field(description="income - spending - saved and invested.")
    savings_rate: str | None = Field(description='(income - spending) / income, like "28.2".')
    basis: Literal["actual"]


class UpcomingItem(Out):
    date: str
    kind: Literal["bill", "plan", "trip"]
    key: str = Field(description="Recurring key, plan key or trip item key.")
    name: str = Field(description="The bill, the plan, or the trip item's label.")
    amount_cents: int = Field(description="Positive: expected payment.")
    account_name: str | None
    trip_key: str | None = Field(description="kind trip: the trip, for the Travel link. Else null.")
    trip_name: str | None = Field(description="kind trip: the trip's name. Else null.")


class BudgetOut(Out):
    key: str
    label: str
    actual_cents: int | None = Field(description="As the chat reported it.")
    target_cents: int
    plan_cents: int | None
    note: str | None
    period_basis: Literal["calendar", "card_cycle"]
    cycle_note: str | None
    status: Literal["over", "on_target", "under", "no_actual"]
    statements_needed: bool


class NetWorthPart(Out):
    key: str
    label: str
    cents: int


class Debt(Out):
    account_key: str
    name: str
    owed_cents: int = Field(description="Positive.")
    apr_milli_pct: int | None
    payoff_date: str | None
    payoff_ahead: bool = Field(description="payoff_date is today or later (household date).")


class NextChange(Out):
    key: str
    effective_month: str
    label: str
    delta_cents: int
    status: CashflowEventStatus | None = Field(description="Format 1.4. Null shows no badge.")


class TodoOut(Out):
    key: str
    title: str
    subtitle: str | None
    status: Literal["open", "done"]
    due_date: str | None


class KeyNumberOut(Out):
    label: str
    value: int = Field(description="Integer cents.")


class ReviewOut(Out):
    summary: str
    key_numbers: list[KeyNumberOut]
    changes: list[str]
    anomalies: list[str]
    top_three: list[str]


class DashboardOut(Out):
    month: str
    updated: str | None
    complete_through: str | None = Field(
        description="Latest complete month (YYYY-MM): every needed statement is in. "
        "The same month Cash Flow, Budget and Statements use."
    )
    location_label: str | None
    net_worth: DashboardNetWorth
    plan_month: PlanMonth | None
    actual_month: ActualMonth | None = Field(description="Null until a month is complete.")
    cashflow: list[CashflowMonth] = Field(
        description="Up to 6 actual months before this month, then planned months, ascending."
    )
    cashflow_note: str | None
    spending_by_group: list[SpendingGroup] = Field(description="The plan, largest first.")
    actual_by_group: list[SpendingGroup] = Field(
        description="Actuals for actual_month, largest first."
    )
    upcoming: list[UpcomingItem] = Field(
        description="Unpaid bills, plan payments and trip payments (format 1.4) in the next "
        "30 days, by date."
    )
    utilities: list[UtilitySummary] = Field(
        description="One line per utility under upcoming bills: next bill expected and the "
        "12-month average bill. By utility_key."
    )
    credits_ending: CreditsEnding | None = Field(
        description="Card credits that are not auto and not used, ending within 14 days. "
        "Null when none, or before the first 1.3 run."
    )
    next_trip: NextTrip | None = Field(
        description="The first planning, booked or in-progress trip that has not ended. "
        "Null when none."
    )
    budgets: list[BudgetOut]
    networth_parts: list[NetWorthPart] = Field(description="By nw_group, largest first.")
    debts: list[Debt] = Field(description="Largest first.")
    next_changes: list[NextChange] = Field(description="The next 3 cash flow events.")
    todos: list[TodoOut]
    review: ReviewOut | None = Field(description="The chat's own review from the newest run.")
    ai_review: AiReview | None = Field(
        default=None,
        description="The AI monthly review (Phase 6), when AI is on and one is ready. "
        "The Dashboard shows it in place of the chat's review.",
    )


# ---- Phase 1 routes the shell uses ----


class MetaOut(Out):
    app_version: str
    schema_versions: list[str]
    household_today: str
    latest_run_id: str | None
    updated: str | None
    this_month: str | None
    complete_as_of: str | None
    latest_complete_month: str | None
    location_label: str | None


class CategoryOut(Out):
    category_key: str
    name: str
    emoji: str | None
    kind: str


class CategoryGroupOut(Out):
    group_key: str
    name: str
    categories: list[CategoryOut]


class CategoriesOut(Out):
    groups: list[CategoryGroupOut]


# ---- GET /api/checks ----

CheckStatus = Literal["match", "differs", "app_only", "chat_only"]


class CheckLine(Out):
    app_cents: int | None
    chat_cents: int | None
    diff_cents: int | None = Field(description="app - chat. Null when one side is missing.")
    status: CheckStatus = Field(description="match when |diff| <= 100 cents.")


class CheckTotal(Out):
    app_cents: int
    chat_cents: int
    diff_cents: int = Field(description="app - chat.")
    status: CheckStatus = Field(description="match when |diff| <= 100 cents.")


class CheckPart(CheckLine):
    key: str
    label: str


class CashflowCheck(Out):
    month: str
    complete: bool
    income: CheckLine
    spending: CheckLine = Field(description="Leaves out saved and invested.")
    saved_invested: CheckLine
    by_group: list[CheckPart] = Field(description="Spending by category group.")
    payment_basis_cents: int
    card_no_statement_cents: int
    uncategorized_cents: int = Field(description="Counted as spending by the app.")
    reimbursable_cents: int = Field(description="Left out by the app.")
    edited_out_count: int = Field(description="Rows hidden or left out by an app edit.")
    notes: list[str] = Field(description="What explains a gap. No amounts.")


class PlanMap(Out):
    key: str
    label: str
    measure: Literal["group", "saved_invested", "plan_installments", "unmapped"]
    maps_to: str


class UnpairedTransfer(Out):
    txn_key: str
    txn_date: str
    account_key: str
    account_name: str
    counterparty_account_key: str
    counterparty_name: str
    amount_cents: int = Field(description="Signed.")


class CardNoStatement(Out):
    account_key: str = Field(description="The card.")
    account_name: str
    month: str
    count: int
    cents: int = Field(description="Positive: counted as spending.")


class MissingStatement(Out):
    account_key: str
    name: str
    month: str


class WatchItem(Out):
    rule: Literal["min-payments", "no-payment-45d", "no-payment-month", "dormant-payment"]
    severity: Literal["high", "medium"]
    account_key: str
    account_name: str
    month: str | None
    message: str


class ChecksOut(Out):
    run_id: str
    as_of: str = Field(description="The run's net_worth.as_of. App numbers are for this day.")
    net_worth: CheckTotal
    assets: CheckTotal
    liabilities: CheckTotal = Field(description="Positive: money owed.")
    parts: list[CheckPart] = Field(
        description="By nw_group, secured loans netted into their asset's group. "
        "App parts first (assets largest first, then liabilities), then chat-only parts."
    )
    cashflow: list[CashflowCheck] = Field(
        description="The chat's cashflow_actual months next to the app's numbers."
    )
    plan_mapping: list[PlanMap] = Field(description="The newest plan's groups and measures.")
    plan_mapping_month: str | None
    unpaired_transfers: list[UnpairedTransfer]
    card_payments_no_statement: list[CardNoStatement]
    missing_statements: list[MissingStatement]
    payment_basis_watch: list[WatchItem]
    utility_past_due: list[UtilityPastDue] = Field(
        description="Utility bills past their due date with no payment, by due date. "
        "Only when the paying account's statements reach the due date."
    )
    utility_payments_no_bill: list[UtilityPaymentNoBill] = Field(
        description="Payments tagged with a utility_key that pair with no bill, from the "
        "first bill date to the newest one. By date."
    )
    utility_warnings: list[UtilityWarning] = Field(
        description="Utility bill import warnings as they stand now."
    )
    utility_bill_count: int = Field(description="Utility bills in the app, all utilities.")
    rewards: RewardsCheck | None = Field(
        description="Stale balances and the milestone and cap gaps against the chat's numbers. "
        "Null before the first 1.3 run."
    )
    trips: TripsCheck = Field(
        description="Trip keys the app has no trip for, and tagged rows next to trip items."
    )
    latest_complete_month: str | None
    notes: list[str]


# ---- GET /api/backups ----


class BackupFile(Out):
    name: str
    bytes: int
    created_at: str = Field(description="File time, UTC ISO.")
    kind: Literal["nightly", "pre_migrate"]


class BackupJob(Out):
    status: Literal["ok", "failed"]
    started_at: str
    finished_at: str | None
    file: str | None
    error: str | None = Field(description="A rule id like integrity_check, never a message.")


class BackupsOut(Out):
    backups: list[BackupFile] = Field(description="Newest first.")
    last_run: BackupJob | None
    last_success_at: str | None
