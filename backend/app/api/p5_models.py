"""Request and response models for contributions and valuations (Phase 5).

Money is integer cents, signed from the net worth view (owed is negative)
unless a field says positive. Rates are integer milli-percent (12% = 12_000).
Index levels are integer micro-units (527.20 = 527_200_000). Dates are ISO
`YYYY-MM-DD`. Percent strings have one decimal place ("6.0").
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

LimitKind = Literal[
    "k401_deferral",
    "k401_catchup_50",
    "k401_catchup_60_63",
    "k415c_total",
    "hsa_self",
    "hsa_family",
    "hsa_catchup_55",
]
CatchUp = Literal["none", "50", "60-63"]
HsaCoverage = Literal["none", "self", "family"]
PlanStatus = Literal["over", "at_limit", "under", "no_limit", "no_schedule", "no_data"]
Cadence = Literal["weekly", "biweekly", "semimonthly", "monthly"]


class Out(BaseModel):
    model_config = {"extra": "forbid"}


class In(BaseModel):
    model_config = {"extra": "forbid"}


# ---- GET /api/contributions ----


class LimitRow(Out):
    year: int
    kind: LimitKind
    label: str
    limit_cents: int
    includes_employer: bool
    source_url: str | None
    confirmed: bool = Field(description="False until an admin confirms it in Settings.")
    confirmed_at: str | None
    updated_at: str


class PersonSettingsOut(Out):
    k401_catch_up: CatchUp = Field(description="Which 401k catch-up applies this year.")
    hsa_coverage: HsaCoverage | None = Field(description="None: not set yet, so no HSA limit.")
    hsa_catch_up: bool = Field(description="Age 55 or over: 1,000 more HSA room.")
    election_lead_checks: int = Field(description="Pay periods ahead an election change takes.")
    updated_at: str | None


class ScheduleOut(Out):
    cadence: Cadence | None = Field(description="Read from regular pay stubs. None: unknown.")
    last_pay_date: str | None


class YtdParts(Out):
    stubs_cents: int = Field(description="Newest stub YTD, else the per-check sum.")
    employer_cents: int | None = Field(description="HSA only: the employer part of the stubs.")
    prior_employer_cents: int
    personal_cents: int


class K415c(Out):
    employee_cents: int = Field(description="Employee deferrals up to the base limit.")
    employer_cents: int
    total_cents: int
    limit_cents: int
    room_cents: int


class PlanRow(Out):
    kind: Literal["k401", "hsa"]
    label: str
    ytd_cents: int
    ytd_as_of: str | None = Field(description="Pay date of the stub the YTD comes from.")
    ytd_parts: YtdParts
    sum_of_checks_cents: int = Field(description="Per-check amounts up to that stub.")
    checks_counted: int
    cross_check_diff_cents: int | None = Field(description="Stub YTD minus the per-check sum.")
    limit_cents: int | None
    limit_base_cents: int | None
    limit_catch_up_cents: int | None
    limit_confirmed: bool
    room_cents: int | None = Field(description="Limit minus YTD.")
    per_check_cents: int | None = Field(
        description="Newest regular check. HSA: employee plus employer."
    )
    per_check_employer_cents: int | None = Field(description="HSA only.")
    per_check_pct: str | None = Field(description="401k election on the newest regular check.")
    checks_left: int | None = Field(description="Pay dates after the newest stub to Dec 31.")
    remaining_dates: list[str]
    projected_cents: int | None = Field(description="YTD plus checks left times per check.")
    projected_room_cents: int | None = Field(description="Limit minus projected. Negative: over.")
    status: PlanStatus
    change_on: str | None = Field(description="The pay date to change the election for.")
    checks_before_change: int | None
    checks_from_change: int | None
    needed_per_check_cents: int | None = Field(
        description="Employee amount per check from change_on on that lands at the limit."
    )
    needed_pct: str | None = Field(description="401k only: needed_per_check as % of gross.")
    shared: bool = Field(description="HSA family coverage: the limit is shared.")
    k415c: K415c | None = Field(description="Shown only when employer rows exist.")
    notes: list[str]


class PersonContributions(Out):
    person_key: str
    display_name: str
    settings: PersonSettingsOut
    settings_set: bool = Field(description="False: defaults, never confirmed in Settings.")
    schedule: ScheduleOut
    plans: list[PlanRow]


class HsaFamilyOut(Out):
    ytd_cents: int
    projected_cents: int | None
    limit_cents: int
    room_cents: int


class OtherContribution(Out):
    person_key: str
    person: str
    account_key: str
    account_name: str = Field(description="The account key when the registry has no name.")
    kind: Literal["ira", "529"]
    label: str
    ytd_cents: int
    gift_cents: int = Field(description="The part with source gift.")
    count: int = Field(description="Deposits. A correction counts too.")
    last_date: str
    last_cents: int = Field(description="Every deposit on last_date added up.")


class ContributionsOut(Out):
    year: int
    as_of: str
    limits: list[LimitRow]
    limits_found: bool = Field(description="False: no IRS limits for this year yet.")
    people: list[PersonContributions]
    hsa_family: HsaFamilyOut | None = Field(
        description="Household line when two people share family coverage."
    )
    other: list[OtherContribution] = Field(
        description="IRA and 529 money put in this year. No limits."
    )


class LimitIn(In):
    year: int = Field(ge=2020, le=2100)
    kind: LimitKind
    limit_cents: int = Field(ge=0, le=10_000_000)
    confirm: bool = Field(default=True, description="Mark it confirmed (false clears it).")


class PersonSettingsIn(In):
    k401_catch_up: CatchUp = "none"
    hsa_coverage: HsaCoverage | None = None
    hsa_catch_up: bool = False
    election_lead_checks: int = Field(default=1, ge=0, le=6)


# ---- GET /api/valuations ----


class RealValue(Out):
    value_cents: int
    as_of: str
    source: str = Field(description="statement, manual, valuation or estimate (the chat's own).")
    method: str | None = Field(description="comps, kbb, report or manual for valuations.")
    label: str = Field(description='Like "KBB value" or "statement balance".')
    source_note: str | None = Field(
        description="The chat's note on the value, like the comps it used. Free text, screens only."
    )


class EstimateValue(Out):
    value_cents: int
    method: Literal["fhfa_hpi", "depreciation", "amortization"]
    based_on: str = Field(description="Date of the real value it starts from.")
    through: str | None = Field(
        description="Index period start, newest mileage or month end, or last payment."
    )
    note: str = Field(description="Tooltip: how it was made and from what date.")


class HomeInputs(Out):
    series_id: str
    index_through: str
    index_through_label: str = Field(description='Like "2026 Q2".')
    index_base_micro: int = Field(description="Index on the valuation date.")
    index_now_micro: int = Field(description="Index on the month end used.")
    step: str = Field(description="The month end the estimate runs to.")
    projected: bool = Field(description="Past the newest index value, on its past-year trend.")


class VehicleInputs(Out):
    step: str = Field(description="The month end the depreciation runs to.")
    chassis_cents: int
    conversion_cents: int
    chassis_rate_milli_pct: int
    conversion_rate_milli_pct: int | None
    mileage_factor_milli: int | None = Field(description="1000 = no change.")
    excess_miles: int | None = Field(description="Miles over pace. Negative: under.")
    mileage_date: str | None
    chassis_floored: bool = Field(description="The conversion was worth more than the valuation.")


class LoanInputs(Out):
    apr_milli_pct: int
    payments: int
    paid_cents: int = Field(description="Positive.")
    interest_cents: int = Field(description="Positive: interest added since the real balance.")
    last_payment: str | None


class InputsOut(Out):
    home: HomeInputs | None
    vehicle: VehicleInputs | None
    loan: LoanInputs | None


class EquityOut(Out):
    value_cents: int
    loan_cents: int = Field(description="Signed: owed is negative.")
    equity_cents: int
    underwater: bool
    estimated: bool = Field(description="The value or a loan is an estimate.")


class ValuationItem(Out):
    account_key: str
    display_name: str
    kind: Literal["home", "vehicle", "loan"]
    secured_by: str | None
    value_cents: int | None = Field(description="Shown value: the estimate if any, else real.")
    stale: bool
    real: RealValue | None
    estimate: EstimateValue | None
    inputs: InputsOut
    note: str | None = Field(description="Why there is no estimate, when that needs saying.")
    equity: EquityOut | None


class IndexStatus(Out):
    series_id: str | None
    label: str | None
    through: str | None = Field(description="Start of the newest index period.")
    through_label: str | None
    trend_milli_pct: int | None = Field(description="Past-year change used after `through`.")
    last_fetch_at: str | None
    last_success_at: str | None
    last_run_status: Literal["ok", "failed"] | None
    error: str | None = Field(description="Error code of the last run, never a message.")
    schedule: str
    attribution: str


class ValuationsOut(Out):
    as_of: str
    index: IndexStatus
    items: list[ValuationItem]


# ---- GET /api/vehicles ----


class VehicleSettingsOut(Out):
    enabled: bool
    model_year: int | None
    depreciation_milli_pct: int | None = Field(description="None: the default.")
    expected_miles_year: int | None
    mileage_milli_pct: int | None = Field(description="% of chassis value per 1,000 miles.")
    conversion_value_cents: int | None
    conversion_value_date: str | None
    conversion_milli_pct: int | None
    updated_at: str | None


class EffectiveSettings(Out):
    depreciation_milli_pct: int
    rate_source: Literal["set", "model_year", "default"]
    expected_miles_year: int
    mileage_milli_pct: int
    conversion_milli_pct: int | None


class ReadingOut(Out):
    id: int
    reading_date: str
    miles: int
    entered_at: str


class VehicleLoan(Out):
    account_key: str
    display_name: str
    balance_cents: int | None
    estimated: bool


class VehicleOut(Out):
    account_key: str
    display_name: str
    value_cents: int | None
    real: RealValue | None
    estimate: EstimateValue | None
    parts: VehicleInputs | None
    note: str | None
    settings: VehicleSettingsOut
    effective: EffectiveSettings
    readings: list[ReadingOut] = Field(description="Newest first.")
    loans: list[VehicleLoan]
    equity: EquityOut | None


class VehiclesOut(Out):
    as_of: str
    vehicles: list[VehicleOut]


class MileageIn(In):
    miles: int = Field(ge=0, le=2_000_000)
    reading_date: date | None = Field(default=None, description="Default: today.")


class VehicleSettingsIn(In):
    enabled: bool = True
    model_year: int | None = Field(default=None, ge=1950, le=2100)
    depreciation_milli_pct: int | None = Field(default=None, ge=0, le=50_000)
    expected_miles_year: int | None = Field(default=None, ge=0, le=100_000)
    mileage_milli_pct: int | None = Field(default=None, ge=0, le=5_000)
    conversion_value_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    conversion_value_date: date | None = None
    conversion_milli_pct: int | None = Field(default=None, ge=0, le=50_000)
