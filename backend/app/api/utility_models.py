"""Response models for utility bills (format 1.2, PLAN 4.12).

Money is integer cents. Bill totals and payments are positive; credits are
negative. Usage is integer micro-units of the line's unit (719 kWh =
719_000_000). Cost per unit is in thousandths of a cent (14.237 cents per kWh
= 14_237). Percent strings have one decimal ("-12.3"). Dates are ISO
`YYYY-MM-DD`, months `YYYY-MM`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Service = Literal["electric", "gas", "water", "sewer", "stormwater", "other"]
Unit = Literal["kwh", "therms", "gallons", "ccf", "none"]
BillStatus = Literal["paid", "amount_differs", "past_due", "not_paid", "no_history"]
WarningRule = Literal["utility-total-rounding", "utility-days", "utility-overlap"]


class Out(BaseModel):
    model_config = {"extra": "forbid"}


class ServiceUnit(Out):
    service: Service
    unit: Unit = Field(description="The unit on the service's newest line.")


class UtilitySummary(Out):
    utility_key: str
    provider: str = Field(description="From the newest bill.")
    bill_count: int
    first_period_end: str
    last_period_end: str
    last_bill_date: str
    last_total_cents: int = Field(description="Positive. The newest bill's total.")
    last_status: BillStatus = Field(description="The newest bill's status.")
    services: list[ServiceUnit] = Field(description="Every service seen, by service.")
    next_bill_date: str | None = Field(
        description="Expected: newest bill date + the median gap between the last bill dates. "
        "Null with one bill."
    )
    next_bill_late: bool = Field(
        description="True when a monthly import dated 7 or more days after next_bill_date "
        "has no newer bill. A passed date alone is not late: bills come with the imports."
    )
    last_import_date: str | None = Field(description="Run date of the newest monthly import.")
    avg_bill_cents: int | None = Field(
        description="Average total of the bills whose period ends in the 12 months up to "
        "the newest period end."
    )
    bills_in_avg: int
    total_12m_cents: int = Field(description="Sum of those bills.")


class UtilitiesOut(Out):
    utilities: list[UtilitySummary] = Field(description="By utility_key.")


class LineOut(Out):
    service: Service
    unit: Unit
    usage_micro: int
    charges_cents: int = Field(description="Positive. Everything billed for the service.")
    fixed_charges_cents: int | None = Field(description="Part of charges, when the bill splits it.")


class CreditOut(Out):
    label: str
    amount_cents: int = Field(description="Negative.")


class BillPayment(Out):
    txn_key: str
    txn_date: str
    amount_cents: int = Field(description="Positive: money paid.")
    account_key: str
    account_name: str
    amount_differs: bool = Field(description="True when the payment is not the bill total.")
    days_after_bill: int = Field(description="Payment date - bill date. Can be a few days below 0.")


class BillOut(Out):
    bill_key: str = Field(description="'<utility_key>:<period_end>'.")
    provider: str
    bill_date: str
    period_start: str
    period_end: str
    days: int
    total_cents: int = Field(description="Positive.")
    due_date: str | None
    due_by: str = Field(description="The due date, else the bill date + 40 days.")
    paid_from: str | None = Field(description="Account key from the bill.")
    paid_from_name: str | None
    heating_degree_days: int | None
    estimated_read: bool | None
    note: str | None
    lines: list[LineOut] = Field(description="By service.")
    credits: list[CreditOut]
    charges_cents: int = Field(description="Sum of the line charges.")
    credits_cents: int = Field(description="Sum of the credits. Zero or negative.")
    status: BillStatus = Field(
        description="paid; amount_differs (paired with a tagged payment of another amount); "
        "past_due (due date passed and the paying account's statements reach it); "
        "no_history (unpaid, and its payment window opens before Jan 1, 2026, or it is "
        "older than the utility's oldest paid bill, or no bill is paid yet); not_paid."
    )
    payment: BillPayment | None


class YoyValue(Out):
    value: int | None
    last_year: int | None
    change_pct: str | None = Field(
        description="(value - last_year) / last_year, one decimal, from exact numbers. "
        "Null with no last-year value or a last-year value of 0."
    )


class BillBeforeValue(Out):
    previous: int | None
    change_pct: str | None = Field(
        description="(value - previous) / previous, one decimal, from exact numbers. "
        "Null with no bill before, another unit, or a previous value of 0."
    )


class YoyService(Out):
    service: Service
    unit: Unit
    usage_micro: YoyValue
    per_day_micro: YoyValue = Field(description="Usage per day, micro-units, rounded.")
    per_degree_day_micro: YoyValue | None = Field(
        description="Gas only: usage per heating degree day, micro-units. Null for other services."
    )
    charges_cents: YoyValue
    usage_vs_prev: BillBeforeValue = Field(
        description="Usage against the bill before, micro-units. Same unit only."
    )
    per_day_vs_prev: BillBeforeValue = Field(description="Usage per day, micro-units, rounded.")


class YoyRow(Out):
    bill_key: str
    period_start: str
    period_end: str
    last_year_bill_key: str | None = Field(
        description="The bill whose period_end is closest to one year earlier, within 20 days."
    )
    last_year_period_start: str | None
    last_year_period_end: str | None
    prev_bill_key: str | None = Field(
        description="The bill just before this one by period_end, even outside the range, "
        "when it ends within 20 days of this bill's period_start."
    )
    prev_period_start: str | None
    prev_period_end: str | None
    days: YoyValue
    heating_degree_days: YoyValue
    total_cents: YoyValue
    total_per_day_cents: YoyValue = Field(description="Total per day, cents, rounded.")
    services: list[YoyService] = Field(
        description="This bill's lines. A service missing last year, or with another unit, "
        "has null last-year values."
    )


class CostService(Out):
    service: Service
    unit: Unit
    usage_micro: int
    charges_cents: int
    fixed_charges_cents: int | None
    cost_per_unit_millicents: int | None = Field(
        description="charges / usage, in thousandths of a cent. Null with no usage or unit none."
    )
    cost_per_unit_ex_fixed_millicents: int | None = Field(
        description="(charges - fixed charges) / usage. Null when the bill does not split "
        "out fixed charges."
    )


class CostPoint(Out):
    bill_key: str
    period_end: str
    services: list[CostService]


class CreditPoint(Out):
    bill_key: str
    bill_date: str
    period_end: str
    label: str
    amount_cents: int = Field(description="Negative.")
    running_cents: int = Field(description="Sum of the credits so far in the range. Negative.")


class UtilityWarning(Out):
    utility_key: str
    provider: str
    bill_key: str
    period_end: str
    rule: WarningRule
    message: str = Field(description="A plain sentence. No amounts.")


class UtilityDetailOut(Out):
    utility_key: str
    provider: str
    from_: str | None = Field(
        alias="from", serialization_alias="from", description="The from month asked for, if any."
    )
    to: str | None
    summary: UtilitySummary = Field(description="Over every bill, not just the range.")
    bills: list[BillOut] = Field(
        description="Bills whose period_end month is in range, oldest first."
    )
    yoy: list[YoyRow] = Field(description="One row per bill in range, same order.")
    cost: list[CostPoint] = Field(description="One point per bill in range, same order.")
    credits: list[CreditPoint] = Field(description="Every credit in range, in bill order.")
    credits_total_cents: int = Field(description="Sum of credits in range. Zero or negative.")
    warnings: list[UtilityWarning] = Field(description="Import warnings for bills in range.")


# ---- Checks ----


class UtilityPastDue(Out):
    utility_key: str
    provider: str
    bill_key: str
    bill_date: str
    period_end: str
    due_date: str = Field(description="The bill's due date, else bill date + 40 days.")
    total_cents: int = Field(description="Positive.")
    account_name: str | None = Field(description="The paying account, when known.")


class UtilityPaymentNoBill(Out):
    utility_key: str
    provider: str
    txn_key: str
    txn_date: str
    account_key: str
    account_name: str
    amount_cents: int = Field(description="Positive: money paid.")
