"""Response and request models for the Phase 5 market API (PLAN 6.2, 8.2).

Money is integer cents. Quantities and prices are integer micro-units
(1.5 shares = 1_500_000). Dates are ISO `YYYY-MM-DD`. Percent strings have one
decimal place ("23.4") unless a field says otherwise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.api.models import InvestmentRangeKey, InvestmentSummary

PriceMode = Literal["api", "constant", "statement"]
ModeSource = Literal["rule", "override"]
# market/classes.py CLASS_CHOICES, in the same order.
ClassChoice = Literal[
    "us-stock", "intl-stock", "bond", "cash", "target-date", "balanced", "real-estate", "other"
]


class Out(BaseModel):
    model_config = {"extra": "forbid"}


# ---- GET /api/investments/holdings ----


class HoldingRow(Out):
    account_key: str
    account_name: str
    holding_key: str
    name: str
    symbol: str | None = Field(description="The ticker priced with (api), else the printed one.")
    asset_class: str | None
    mode: PriceMode
    mode_source: ModeSource
    statement_date: str
    statement_quantity_micro: int | None
    statement_price_micro: int | None
    statement_value_cents: int
    quantity_micro: int | None = Field(description="Shares now, after splits since the statement.")
    price_micro: int | None = Field(
        description="Newest close (api), 1.00 (constant), else printed."
    )
    price_date: str | None = Field(description="Date of price_micro. None for constant.")
    value_cents: int
    estimated: bool = Field(description="Value moved with prices since the statement (est.).")
    price_missing: bool = Field(description="An api holding with no close to estimate from.")
    price_stale: bool = Field(description='Show "price as of <date>" in the warning color.')
    share_of_account_pct: str | None
    concentrated: bool = Field(description="Over 20% of its account (cash left out).")
    cost_basis_cents: int | None
    gain_cents: int | None = Field(description="Value minus cost basis. None for cash or no basis.")
    gain_pct: str | None = Field(description="Gain over a basis above 0.")


class HoldingGroup(Out):
    key: str = Field(description="account_key, or the asset class key.")
    label: str
    total_cents: int
    statement_date: str | None = Field(description="Account groups only.")
    estimated: bool
    holdings: list[HoldingRow] = Field(description="Largest first.")
    account_type: str | None = Field(description="Account groups only.")
    notes: str | None = Field(description="Account groups only: the chat's note on the account.")
    dormant: bool = Field(description="Account groups only.")
    cost_basis_cents: int | None = Field(description="Holdings with a gain only.")
    gain_cents: int | None = Field(description="Sum over holdings with a basis. None if none.")
    gain_pct: str | None
    gain_share_pct: str | None = Field(
        description="Gain as a share of the total. Only when every non-cash holding has a basis."
    )
    basis_holdings: int = Field(description="Holdings with a gain.")
    no_basis_holdings: int = Field(description="Non-cash holdings with no cost basis.")


class HoldingsOut(Out):
    as_of: str
    group_by: Literal["account", "class"]
    total_cents: int
    estimated: bool
    concentrated: int = Field(description="How many holdings are flagged.")
    groups: list[HoldingGroup] = Field(description="Largest first.")


# ---- GET /api/investments/allocation ----


class AllocationSlice(Out):
    key: str
    label: str
    value_cents: int
    pct: str | None


class AllocationAccount(Out):
    account_key: str
    display_name: str
    value_cents: int
    pct: str | None = Field(description="Share of all holdings.")
    classes: list[AllocationSlice] = Field(description="Within the account, largest first.")


class AllocationOut(Out):
    as_of: str
    total_cents: int
    estimated: bool
    classes: list[AllocationSlice] = Field(description="Largest first.")
    accounts: list[AllocationAccount] = Field(description="Largest first.")


# ---- GET /api/investments/history ----


class HistoryAccount(Out):
    account_key: str
    display_name: str


class HistoryPoint(Out):
    date: str
    value_cents: int
    partial: bool
    estimated: bool = Field(description="At least one account is a price estimate that day.")


class StatementPoint(Out):
    date: str
    account_key: str
    value_cents: int
    source: Literal["statement", "manual", "valuation"]


class EstimatedSegment(Out):
    account_key: str
    start: str
    end: str
    based_on: str = Field(description="Statement date the estimate starts from.")
    note: str = Field(description="Tooltip text.")


class InvestmentRangeOut(Out):
    key: InvestmentRangeKey
    start: str
    end: str


class InvestmentsHistoryOut(Out):
    range: InvestmentRangeOut
    accounts: list[HistoryAccount]
    points: list[HistoryPoint] = Field(description="Daily total, investments and retirement.")
    statement_points: list[StatementPoint]
    estimated_segments: list[EstimatedSegment]
    latest_price_date: str | None


# ---- GET /api/investments/flows ----


class AccountFlows(InvestmentSummary):
    account_key: str
    display_name: str
    closed: bool = Field(description="Closed on or before household today.")


class InvestmentFlowsOut(Out):
    as_of: str
    year: int
    accounts: list[AccountFlows] = Field(
        description="Investment accounts with statements this year, in Accounts page order."
    )


# ---- GET /api/prices/status ----


class PriceRun(Out):
    status: Literal["ok", "failed"]
    started_at: str
    finished_at: str | None
    symbols: int
    updated: int
    tiingo_calls: int
    yahoo_calls: int
    stopped: list[Literal["tiingo", "yahoo"]] = Field(description="Providers that hit a 429.")


class SymbolStatus(Out):
    symbol: str
    holdings: int = Field(description="Api holdings priced with this symbol.")
    last_date: str | None
    source: Literal["tiingo", "yahoo", "constant"] | None
    stale: bool = Field(description="No close in the last 5 days, or none at all.")
    error: str | None = Field(description="Error code from the last run.")
    error_label: str | None


class SanityFlag(Out):
    account_key: str
    account_name: str
    holding_key: str
    name: str
    symbol: str
    statement_date: str
    price_date: str
    gap_pct: str = Field(description='Provider close vs the statement price, like "-1.25".')


class SanityOut(Out):
    checked: int
    unchecked: int = Field(description="No stored close on the statement date yet.")
    flagged: list[SanityFlag] = Field(description="Gaps over 1%, newest statement first.")


class PricesStatusOut(Out):
    tiingo_key_set: bool = Field(description="Whether TIINGO_API_KEY is set. Never the key.")
    schedule: str
    last_run: PriceRun | None
    last_success_at: str | None
    symbols: list[SymbolStatus]
    sanity: SanityOut


# ---- GET/PUT /api/symbols ----


class SymbolOverride(Out):
    price_mode: PriceMode
    symbol: str | None
    note: str | None
    updated_at: str


class SymbolRow(Out):
    account_key: str
    account_name: str
    holding_key: str
    name: str
    printed_symbol: str | None = Field(description="As the newest statement printed it.")
    cusip: str | None
    asset_class: str | None = Field(description="As the newest statement gave it.")
    class_pick: ClassChoice | None = Field(
        description="An admin's pick. It wins over asset_class for grouping, never for value."
    )
    last_statement_date: str
    rule_mode: PriceMode
    mode: PriceMode = Field(description="In use: the override, else the rule.")
    symbol: str | None = Field(description="Ticker in use (api only).")
    override: SymbolOverride | None


class SymbolsOut(Out):
    tiingo_key_set: bool
    rows: list[SymbolRow]


class SymbolIn(BaseModel):
    model_config = {"extra": "forbid"}

    account_key: str = Field(min_length=1, max_length=40)
    holding_key: str = Field(min_length=1, max_length=40)
    price_mode: PriceMode | None = Field(description="None removes the override.")
    symbol: str | None = Field(default=None, max_length=12)
    note: str | None = Field(default=None, max_length=200)


class ClassIn(BaseModel):
    model_config = {"extra": "forbid"}

    account_key: str = Field(min_length=1, max_length=40)
    holding_key: str = Field(min_length=1, max_length=40)
    asset_class: ClassChoice | None = Field(description="None goes back to the statement's class.")
