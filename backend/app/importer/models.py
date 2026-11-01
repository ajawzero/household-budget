"""Strict Pydantic models for import format v1 (the source of truth).

`scripts/gen_schema.py` writes `schema/import-v1.schema.json` from these models.
Formatted strings (money, dates, keys, ...) are validated here and carry an
`x-rule` id in the JSON Schema, so the app and `schema/check_run.py` report the
same rule ids. Fields marked with `NO_SCAN` (`"x-scan": false`) hold formatted
values and are skipped by the redaction scan. Every other string is scanned.

Errors never carry the bad value: use `issues_from_error`.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    WithJsonSchema,
    model_validator,
)
from pydantic_core import PydanticCustomError

# --------------------------------------------------------------------------
# Rule ids. Shared with schema/check_run.py and documented in schema/README.md.
# Messages are plain and never include the bad value.
# --------------------------------------------------------------------------

RULES: dict[str, str] = {
    # Run folder and manifest
    "run-id": "Run id must be YYYY-MM-DD or YYYY-MM-DD-N with N of 2 or more.",
    "run-id-mismatch": "Run id must equal the folder name and match in every file.",
    "run-id-future": "Run date is more than 1 day ahead of today.",
    "run-changed": "A run with this id was already imported with a different manifest.",
    "mode-mismatch": "Mode must match between manifest.json and snapshot.json.",
    "manifest-missing": "manifest.json is missing.",
    "manifest-rows": "CSV files need a rows count. snapshot.json has none.",
    "path-not-allowed": "Path is not allowed. Use snapshot.json or transactions/<name>.csv.",
    "symlink": "Symbolic links are not allowed.",
    "file-missing": "A required or listed file is missing.",
    "file-unlisted": "File is not listed in manifest.json.",
    "file-too-large": "File is larger than 20 MB.",
    "size-mismatch": "File size does not match manifest.json.",
    "hash-mismatch": "File sha256 does not match manifest.json.",
    "rows-mismatch": "CSV row count does not match manifest.json.",
    # File format
    "json": "File is not valid JSON, or its top level is not an object.",
    "encoding": "File must be UTF-8 without a byte order mark.",
    "line-endings": "Use LF line endings and end the file with a newline.",
    "version-format": "schema_version must look like 1.0.",
    "version-major": "This schema major version is not supported.",
    "version-minor": "This schema minor version is newer than the app knows. Update the app first.",
    "schema": "Field is missing, unknown, or has the wrong type or value.",
    "key-format": "Key must be lowercase words joined by dashes, 40 characters at most, "
    "with no run of 3 or more digits except 401k, 403b and 529.",
    "money-format": "Money must be a decimal string with 2 places, like -12.34.",
    "quantity-format": "Quantity must be a decimal string with up to 6 places.",
    "rate-format": "Rate must be a percent string with up to 3 places, like 24.99.",
    "date-format": "Date must be a real date as YYYY-MM-DD.",
    "month-format": "Month must be YYYY-MM.",
    "timestamp-format": "Timestamp must be ISO 8601 with seconds and a UTC offset.",
    "last4-format": "last4 must be 3 or 4 digits.",
    "text-format": "Text must be 1 to 500 characters with no control characters.",
    "hash-format": "sha256 must be 64 lowercase hex characters.",
    "cusip-format": "CUSIP must be 9 characters with a valid check digit.",
    "symbol-format": "Symbol must be 1 to 12 uppercase letters, digits, dots or dashes.",
    "confidence-format": "Confidence must be blank or a decimal from 0 to 1.",
    "bool-format": "Boolean must be true, false or blank.",
    "date-order": "Start date is after end date.",
    "reconcile-note": "A statement with reconciled false needs a reconcile_note.",
    "nw-group": "An account included in net worth needs an nw_group.",
    "tracked-net-worth": "An account tracked by payments is never included in net worth.",
    "money-sign": "This amount must be zero or more.",
    "active-months": "active_months must be month numbers 1 to 12, ascending, with no repeats.",
    "recurring-interval": "interval_n is required for cadence every_n_weeks "
    "and only allowed there.",
    "plan-dates": "A promo_0pct plan needs promo_expiry. "
    "A deferred_interest plan needs pay_before. "
    "paid_off_on is only for a paid_off or closed plan, on or after created_on.",
    "plan-payments": "payments_left cannot be more than payments_total.",
    "plan-components": "The component amounts must add up to the plan's original amount.",
    "flag-resolved": "A flag with status resolved needs resolved_on.",
    "flag-related": "related_kind and related_key go together.",
    # 1.2 utility bills
    "utility-date": "Utility bill dates must be on or after 2000-01-01.",
    "utility-period": "The service period must end after it starts.",
    "utility-unit": "The unit does not fit the service.",
    "utility-fixed": "fixed_charges cannot be more than charges.",
    "utility-credit-sign": "A credit amount must be negative.",
    "utility-total": "The line charges plus the credits differ from total by more than 2 cents.",
    # warnings: the run is imported and the warning is shown
    "utility-total-rounding": "The line charges plus the credits differ from total "
    "by 1 or 2 cents, likely rounding on the bill.",
    "utility-days": "days does not equal period_end minus period_start.",
    "utility-overlap": "This bill's period overlaps the bill before it for the same utility.",
    # 1.3 rewards and trips
    "reward-as-of": "A balance needs as_of and source, and reported_spent needs as_of. "
    "None of them is allowed alone.",
    "reward-value-range": "cpp_low and cpp_high go together, low not more than high, "
    "and never on cash back.",
    "reward-benefit-fields": "A benefit has only the fields of its kind, and every field "
    "its kind requires.",
    "reward-program-unknown": "program_key is not a program in this run's rewards.programs.",
    "reward-allocation": "Allocations are for timeshare programs only, and used plus "
    "pulled_forward cannot be more than allocated.",
    "trip-dates": "A trip has start_date and end_date, or month alone, "
    "and no date before 2000-01-01.",
    "trip-estimate": "estimate_low and estimate_high go together, low not more than high.",
    "trip-item-amount": "posted, booked and excluded items need amount. estimate items need "
    "both estimate fields and no amount. tbd items have neither.",
    "trip-tender": "Tender points needs points and program_key. Tender points_credit needs amount.",
    "trip-iou-settled": "A settled IOU needs settled_on. Other IOUs cannot have it.",
    "trip-item-unknown": "item_key is not an item in the same trip.",
    # warnings: the run is imported and the warning is shown
    "trip-unknown": "This names a trip that no run so far defines.",
    "reward-stale": "This balance or reported_spent is more than 90 days older than the run.",
    # 1.4 state fields and goals
    "recurring-keep": "keep is only for an active or paused item.",
    "recurring-tag-private": "The personal subscription never carries a tag.",
    "reward-done-on": "done_on is only for a redemption with status done.",
    "goal-fields": "A college goal needs a child as person, start_year, and only 529 or custodial "
    "accounts. start_year is for college goals only, target_date for save_up goals only.",
    "debt-terms": "original is only for loan and mortgage accounts and must be above zero. "
    "A rate_changes list with items needs apr and rate_type variable on the same account.",
    "trip-due": "due_on is only for a booked item.",
    "trip-maybe": "maybe is only for an estimate or tbd item.",
    # warnings: the run is imported and the warning is shown
    "recurring-no-date": "This active item has no next_date, start_date or renewal_date, "
    "so it has no due date.",
    # Sections and lists
    "section-missing": "A section required for this mode is missing.",
    "section-not-allowed": "This section is not allowed in this mode. "
    "State sections are for monthly runs only.",
    "section-version": "A section, field or value needs a newer schema_version than the run "
    "declares.",
    "key-duplicate": "Two items in this list have the same identity.",
    "sort-order": "List is not sorted by its identity.",
    "paycheck-seq": "Paycheck seq must be 1, 2, ... per person, pay_date and kind, "
    "in order of gross amount.",
    # CSV
    "csv-header": "CSV header must match the format exactly, in order.",
    "csv-format": "CSV row has the wrong number of fields.",
    "csv-sort": "CSV rows must be sorted by txn_date, amount, description, then the other columns.",
    "csv-empty": "A CSV file must have at least one row.",
    "csv-orphan": "CSV file name does not match a statement in snapshot.json.",
    "csv-missing": "Statement has rows but no CSV file.",
    # Registry and references
    "account-unknown": "Account key is not in an earlier run and not in new_accounts.",
    "new-account-details": "A key in new_accounts must also be in accounts.",
    "person-unknown": "Person key is not in people in this or an earlier run.",
    "category-unknown": "category_key is not in categories.json.",
    # Statements and history
    "txn-count": "txn_count does not match the CSV rows.",
    "txn-sum": "txn_sum does not match the sum of the CSV rows.",
    "statement-opening": "A statement with rows needs an opening balance.",
    "statement-unbalanced": "Statement does not reconcile from opening to closing.",
    "statement-credits": "credits does not equal the sum of positive rows.",
    "statement-debits": "debits does not equal the sum of negative rows.",
    "balance-statement": "A balance with source statement has no matching statement in this run.",
    "statement-balance-missing": "Statement has no balance row with source statement in this run.",
    "holdings-missing": "Statement has has_holdings true but the run has no holdings section.",
    "tracked-balance": "An account tracked by payments cannot have balances from statements.",
    "tracked-payment": "A row to a card tracked by payments with is_transfer false must be "
    "txn_type payment with a spending category.",
    "balance-closing": "A statement balance does not equal the statement balance or closing.",
    "holding-statement": "Holding has no matching statement with has_holdings in this run.",
    "date-window": "Row date is outside the statement window.",
    "refund-income": "A refund cannot use an income category.",
    # Redaction (see schema/redaction.json)
    "digits-run": "Text has a run of 5 or more digits.",
    "digits-split": "Text has digit groups that together hold 8 or more digits.",
    "luhn": "Text has a number that looks like a card number.",
    "phone": "Text has a phone number.",
    "split-account": "Text has a split account number (4 digits, a dash, 4 digits).",
    "masked-number": "Text has a masked number form.",
    "email": "Text has an email address.",
    "street-address": "Text has a street address.",
    "stock-plan": "Holding names a stock plan, ESPP, RSU or unvested item.",
    "private-subscription": "Text matches the private subscription list.",
    "private-employer": "Text matches the employer terms list.",
    "booking-code": "Text has a booking code, confirmation code, record locator or flight number.",
}

# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------

CURRENT_VERSION = "1.4"
KNOWN_VERSIONS = ("1.0", "1.1", "1.2", "1.3", "1.4")
SchemaVersion = Literal["1.0", "1.1", "1.2", "1.3", "1.4"]
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+")


def version_tuple(value: str) -> tuple[int, int]:
    """'1.10' -> (1, 10), so versions compare as numbers."""
    major, minor = value.split(".")
    return int(major), int(minor)


def version_rule(value: object) -> str | None:
    """Return a rule id if `schema_version` is not supported, else None."""
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        return "version-format"
    major, _ = version_tuple(value)
    known_major = {int(v.split(".")[0]) for v in KNOWN_VERSIONS}
    if major not in known_major:
        return "version-major"
    if value not in KNOWN_VERSIONS:
        return "version-minor"
    return None


def version_message(value: object) -> str:
    """A clear message for an unsupported version (no data values, only the version)."""
    rule = version_rule(value)
    if rule is None:
        return "Schema version is supported."
    known = ", ".join(KNOWN_VERSIONS)
    if rule == "version-minor":
        return (
            f"Schema version {value} is newer than this app knows ({known}). Update the app first."
        )
    if rule == "version-major":
        return f"Schema version {value} has an unknown major version. This app knows {known}."
    return f"schema_version must look like 1.0. This app knows {known}."


# --------------------------------------------------------------------------
# Formatted string types
# --------------------------------------------------------------------------


class NoScan:
    """Marker in Annotated metadata: value is formatted and not scanned for redaction."""

    def __repr__(self) -> str:
        return "NO_SCAN"


NO_SCAN = NoScan()

_KEY_BODY = (
    r"[a-z](?:[a-z]|[0-9](?![0-9]{2}))*"
    r"(?:-(?:401k|403b|529|(?:[a-z]|[0-9](?![0-9]{2}))+))*"
)
KEY_PATTERN = rf"^{_KEY_BODY}$"
KEY_MAX = 40
MONEY_PATTERN = r"^-?[0-9]+\.[0-9]{2}$"
QUANTITY_PATTERN = r"^-?[0-9]+(?:\.[0-9]{1,6})?$"
RATE_PATTERN = r"^[0-9]{1,3}(?:\.[0-9]{1,3})?$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
MONTH_PATTERN = r"^[0-9]{4}-(?:0[1-9]|1[0-2])$"
TIMESTAMP_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
RUN_ID_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:-(?:[2-9]|[1-9][0-9]+))?$"
LAST4_PATTERN = r"^[0-9]{3,4}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
CUSIP_PATTERN = r"^[0-9A-Z]{8}[0-9]$"
SYMBOL_PATTERN = r"^[A-Z][A-Z0-9.-]{0,11}$"
TEXT_MAX = 500
TEXT_PATTERN = r"^[^\u0000-\u001f\u007f]+$"
OPT_TEXT_PATTERN = r"^[^\u0000-\u001f\u007f]*$"
CONFIDENCE_PATTERN = r"^(?:|0(?:\.[0-9]{1,2})?|1(?:\.0{1,2})?)$"
BOOL_PATTERN = r"^(?:true|false|)$"
OPT_KEY_PATTERN = rf"^(?:|{_KEY_BODY})$"
OPT_DATE_PATTERN = r"^(?:|[0-9]{4}-[0-9]{2}-[0-9]{2})$"
MANIFEST_PATH_PATTERN = r"^(?:snapshot\.json|transactions/[a-z0-9-]+\.csv)$"


def _real_date(v: str) -> bool:
    try:
        _date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _real_timestamp(v: str) -> bool:
    try:
        _datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def cusip_valid(v: str) -> bool:
    """CUSIP check digit (modulus 10, double-add-double)."""
    if not re.fullmatch(CUSIP_PATTERN, v):
        return False
    total = 0
    for i, ch in enumerate(v[:8]):
        n = int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10
        if i % 2 == 1:
            n *= 2
        total += n // 10 + n % 10
    return (10 - total % 10) % 10 == int(v[8])


def _fmt(
    rule: str,
    pattern: str,
    *,
    scan: bool = False,
    max_length: int | None = None,
    extra: Callable[[str], bool] | None = None,
    fmt: str | None = None,
) -> Any:
    """Build an Annotated str type with a rule id, a JSON Schema and a scan flag."""
    rx = re.compile(pattern)

    def check(v: str) -> str:
        if (
            rx.fullmatch(v) is None
            or (max_length is not None and len(v) > max_length)
            or (extra is not None and v != "" and not extra(v))
        ):
            raise PydanticCustomError(rule, RULES[rule])
        return v

    schema: dict[str, Any] = {"type": "string", "pattern": pattern, "x-rule": rule}
    if max_length is not None:
        schema["maxLength"] = max_length
    if fmt is not None:
        schema["format"] = fmt
    if not scan:
        schema["x-scan"] = False
    meta: list[Any] = [AfterValidator(check), WithJsonSchema(schema)]
    if not scan:
        meta.append(NO_SCAN)
    return Annotated[str, *meta]


Key = _fmt("key-format", KEY_PATTERN, scan=True, max_length=KEY_MAX)
Money = _fmt("money-format", MONEY_PATTERN)
Quantity = _fmt("quantity-format", QUANTITY_PATTERN)
Rate = _fmt("rate-format", RATE_PATTERN)
Date = _fmt("date-format", DATE_PATTERN, extra=_real_date, fmt="date")
Month = _fmt("month-format", MONTH_PATTERN)
Timestamp = _fmt("timestamp-format", TIMESTAMP_PATTERN, extra=_real_timestamp, fmt="date-time")
RunIdStr = _fmt("run-id", RUN_ID_PATTERN, extra=lambda v: _real_date(v[:10]))
Last4 = _fmt("last4-format", LAST4_PATTERN)
Sha256 = _fmt("hash-format", SHA256_PATTERN)
Cusip = _fmt("cusip-format", CUSIP_PATTERN, extra=cusip_valid)
Symbol = _fmt("symbol-format", SYMBOL_PATTERN)
Text = _fmt("text-format", TEXT_PATTERN, scan=True, max_length=TEXT_MAX)
ManifestPath = _fmt("path-not-allowed", MANIFEST_PATH_PATTERN)
# CSV cells (always strings; blank means unknown or none)
CsvText = _fmt("text-format", OPT_TEXT_PATTERN, scan=True, max_length=TEXT_MAX)
CsvKey = _fmt("key-format", OPT_KEY_PATTERN, scan=True, max_length=KEY_MAX)
CsvDate = _fmt("date-format", OPT_DATE_PATTERN, extra=_real_date, fmt="date")
CsvBool = _fmt("bool-format", BOOL_PATTERN)
CsvConfidence = _fmt("confidence-format", CONFIDENCE_PATTERN)


def _check_amount(v: str) -> str:
    if re.fullmatch(MONEY_PATTERN, v) is None:
        raise PydanticCustomError("money-format", RULES["money-format"])
    if v.startswith("-"):
        raise PydanticCustomError("money-sign", RULES["money-sign"])
    return v


# Money that must be zero or more (1.1 sections). "x-sign" tells check_run.py.
Amount = Annotated[
    str,
    AfterValidator(_check_amount),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": MONEY_PATTERN,
            "x-rule": "money-format",
            "x-scan": False,
            "x-sign": "nonnegative",
        }
    ),
    NO_SCAN,
]
MonthNumber = Annotated[int, Field(ge=1, le=12)]


def _check_usage(v: str) -> str:
    if re.fullmatch(QUANTITY_PATTERN, v) is None:
        raise PydanticCustomError("quantity-format", RULES["quantity-format"])
    if v.startswith("-"):
        raise PydanticCustomError("money-sign", RULES["money-sign"])
    return v


# Quantity that must be zero or more (1.2 utility usage). Same "x-sign" as Amount.
Usage = Annotated[
    str,
    AfterValidator(_check_usage),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": QUANTITY_PATTERN,
            "x-rule": "quantity-format",
            "x-scan": False,
            "x-sign": "nonnegative",
        }
    ),
    NO_SCAN,
]


def _only_true(v: bool) -> bool:
    if v is not True:
        raise PydanticCustomError("schema", RULES["schema"])
    return v


# true or left out (1.4 recurring keep, trip item maybe). Not Literal[True]: pydantic
# takes 1 for it, and the checker does not. The JSON Schema is the same.
OnlyTrue = Annotated[
    bool, AfterValidator(_only_true), WithJsonSchema({"const": True, "type": "boolean"})
]

# --------------------------------------------------------------------------
# Number helpers (integer cents, micro-units, milli-percent)
# --------------------------------------------------------------------------


def _scaled(value: str, places: int) -> int:
    neg = value.startswith("-")
    whole, _, frac = value.lstrip("-").partition(".")
    n = int(whole) * 10**places + int((frac + "0" * places)[:places] or "0")
    return -n if neg else n


def to_cents(value: str) -> int:
    """'-12.34' -> -1234. The value must already match MONEY_PATTERN."""
    return _scaled(value, 2)


def to_micro(value: str) -> int:
    """'1.5' -> 1500000. Quantities and prices."""
    return _scaled(value, 6)


def to_milli_pct(value: str) -> int:
    """'24.99' -> 24990 (milli-percent)."""
    return _scaled(value, 3)


def _unscaled(n: int, places: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    return f"{sign}{n // 10**places}.{n % 10**places:0{places}d}"


def cents_to_str(cents: int) -> str:
    """-1234 -> '-12.34'."""
    return _unscaled(cents, 2)


def micro_to_str(micro: int) -> str:
    """1500000 -> '1.5' (trailing zeros dropped, at least one place)."""
    s = _unscaled(micro, 6).rstrip("0")
    return s + "0" if s.endswith(".") else s


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

Mode = Literal["monthly", "backfill", "correction"]
PersonRole = Literal["adult", "child", "trust", "joint"]
AccountType = Literal[
    "checking",
    "savings",
    "credit_card",
    "store_card",
    "loan",
    "mortgage",
    "heloc",
    "brokerage",
    "retirement",
    "529",
    "hsa",
    "custodial",
    "nqdc",
    "property",
    "vehicle",
    "other",
]
Ownership = Literal["individual", "joint", "trust", "custodial"]
AccountGroup = Literal[
    "cash",
    "credit_cards",
    "investments",
    "retirement",
    "real_estate",
    "vehicles",
    "loans",
    "other",
]
Cadence = Literal["monthly", "quarterly", "report", "none"]
Autopay = Literal["minimum", "statement", "full", "none"]
TrackedBy = Literal["statements", "payments"]
RateType = Literal["fixed", "variable", "promo"]
BalanceSource = Literal["statement", "valuation", "manual", "estimate"]
ValuationMethod = Literal["comps", "kbb", "report", "manual"]
PaycheckKind = Literal["regular", "bonus", "draw"]
ContributionKind = Literal[
    "401k_employee", "401k_employer", "hsa_employee", "hsa_employer", "ira", "529"
]
ContributionSource = Literal["current_employer", "prior_employer", "personal", "gift"]
StreamKind = Literal["paycheck", "benefit", "draw", "bonus"]
PeriodBasis = Literal["calendar", "card_cycle"]
ActualSource = Literal["statement", "baseline"]
TodoStatus = Literal["open", "done"]
# 1.1
RecurringCadence = Literal[
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
PaymentRail = Literal["card", "ach", "bill_pay", "check", "cash", "payroll"]
PlanKind = Literal[
    "plan_it", "pay_over_time", "promo_0pct", "deferred_interest", "apple_installment"
]
PlanStatus = Literal["active", "paid_off", "closed"]
FlagSeverity = Literal["high", "medium", "low"]
FlagStatus = Literal["open", "watching", "resolved"]
FlagKind = Literal[
    "debt",
    "tax",
    "budget",
    "subscription",
    "fraud",
    "travel",
    "rewards",
    "cost_cutting",
    "milestone",
    # 1.4 (VALUE_SINCE)
    "retirement",
    "investment",
    "insurance",
    "medical",
    "payroll",
    "home",
    "other",
]
FlagRelatedKind = Literal[
    "account", "recurring", "plan", "budget", "todo", "pending", "trip", "category", "person"
]
# 1.4. A flag with no action counts as act.
FlagAction = Literal["act", "ask", "reimburse", "info"]
RecurringTag = Literal["streaming", "hobbies", "software"]
CashflowEventStatus = Literal["confirmed", "expected", "target", "suggested"]
PendingKind = Literal["apply", "confirm"]
# 1.2
UtilityService = Literal["electric", "gas", "water", "sewer", "stormwater", "other"]
UtilityUnit = Literal["kwh", "therms", "gallons", "ccf", "none"]
# Units each service may use. stormwater and other take any unit, `none` included.
UTILITY_UNITS: dict[str, frozenset[str]] = {
    "electric": frozenset({"kwh"}),
    "gas": frozenset({"therms"}),
    "water": frozenset({"gallons", "ccf"}),
    "sewer": frozenset({"gallons", "ccf"}),
    "stormwater": frozenset({"kwh", "therms", "gallons", "ccf", "none"}),
    "other": frozenset({"kwh", "therms", "gallons", "ccf", "none"}),
}
UTILITY_EARLIEST = "2000-01-01"
UTILITY_ROUNDING_CENTS = 2  # off by 1 or 2 cents is a warning, more is rejected
# 1.3
RewardKind = Literal["points", "miles", "cash_back", "timeshare"]
RewardGroup = Literal["primary", "secondary"]
RewardSource = Literal["statement", "manual"]
BenefitKind = Literal["credit", "earning_cap", "status_perk", "free_night", "other"]
BenefitCadence = Literal["monthly", "quarterly", "semi_annual", "annual", "multi_year", "one_time"]
MilestoneStatus = Literal["open", "met", "missed"]
RedemptionStatus = Literal["ready", "consider", "done"]
# 1.4
GoalKind = Literal["college", "save_up"]
TripStatus = Literal["planning", "booked", "in_progress", "complete", "final"]
TripItemKind = Literal[
    "flight",
    "lodging",
    "rental_car",
    "gas",
    "food",
    "activities",
    "tickets",
    "shopping",
    "insurance",
    "parking",
    "transit",
    "cash",
    "supplies",
    "fees",
    "other",
]
TripItemStatus = Literal["posted", "booked", "estimate", "tbd", "excluded"]
TripTender = Literal["cash", "points", "free_night", "points_credit", "covered"]
IouDirection = Literal["owed_to_us", "we_owe"]
IouStatus = Literal["open", "settled", "waived"]
TRIP_EARLIEST = "2000-01-01"
REWARD_STALE_DAYS = 90  # a balance or reported_spent older than this warns
# Benefit fields that belong to one kind (rule reward-benefit-fields). The rest
# (counted_value, auto, enrolled, rolls_over, enroll_by, ends_on, how_to_use,
# note) are allowed on every kind.
BENEFIT_FIELDS: dict[str, frozenset[str]] = {
    "credit": frozenset({"cadence", "amount", "cycles"}),
    "earning_cap": frozenset(
        {"cap", "earn_before", "earn_after", "category_keys", "reported_spent", "as_of"}
    ),
    "status_perk": frozenset({"program_key", "status_tier"}),
    "free_night": frozenset({"program_key"}),
    "other": frozenset(),
}
BENEFIT_REQUIRED: dict[str, tuple[str, ...]] = {
    "credit": ("cadence", "amount"),
    "earning_cap": ("cap", "earn_before", "earn_after", "category_keys"),
    "status_perk": ("program_key", "status_tier"),
    "free_night": ("program_key",),
    "other": (),
}
PendingStatus = Literal["open", "waiting", "done"]
TxnType = Literal[
    "purchase",
    "refund",
    "payment",
    "fee",
    "interest",
    "transfer",
    "deposit",
    "withdrawal",
    "dividend",
    "reinvest",
    "buy",
    "sell",
    "contribution",
    "plan_fee",
    "adjustment",
]

# Account types whose statements reconcile from rows (or credits and debits).
CASH_LIKE_TYPES = frozenset(
    {"checking", "savings", "credit_card", "store_card", "loan", "mortgage", "heloc"}
)
# Account types whose statements reconcile with net_flows and market_change.
INVESTMENT_TYPES = frozenset({"brokerage", "retirement", "529", "hsa", "custodial", "nqdc"})
# Account types a college goal may hold (1.4, rule goal-fields).
COLLEGE_ACCOUNT_TYPES = frozenset({"529", "custodial"})
# Account types that may carry `original`, the amount when the loan was made (1.4, debt-terms).
DEBT_ORIGINAL_TYPES = frozenset({"loan", "mortgage"})
# Dates that anchor a recurring item's due date (1.4 warning recurring-no-date).
RECURRING_DATE_FIELDS = ("next_date", "start_date", "renewal_date")
# The name of the personal subscription (README 5.6). The privacy rescan writes it over
# a private name (sanitize.SUBSCRIPTION_TEXT), and it never carries a tag (1.4).
PERSONAL_SUBSCRIPTION = "Personal subscription"


def is_personal_subscription(name: str) -> bool:
    """The name is the personal subscription's, ignoring case and extra spaces."""
    return " ".join(name.split()).casefold() == PERSONAL_SUBSCRIPTION.casefold()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


def _tombstone_tag(v: Any) -> str:
    if isinstance(v, dict):
        return "tombstone" if v.get("deleted") is True else "item"
    return "tombstone" if getattr(v, "deleted", False) is True else "item"


def _with_tombstone(item: type, tomb: type) -> Any:
    return Annotated[
        Annotated[item, Tag("item")] | Annotated[tomb, Tag("tombstone")],
        Discriminator(_tombstone_tag),
    ]


# ---- manifest.json ----


class Generator(Strict):
    skill: Text
    version: Text


class ManifestFile(Strict):
    path: ManifestPath
    sha256: Sha256
    bytes: int = Field(ge=0)
    rows: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _rows_for_csv(self) -> ManifestFile:
        is_csv = self.path.endswith(".csv")
        if is_csv != (self.rows is not None):
            raise PydanticCustomError("manifest-rows", RULES["manifest-rows"])
        return self


class Manifest(Strict):
    schema_version: SchemaVersion
    run_id: RunIdStr
    mode: Mode
    created_at: Timestamp
    generator: Generator
    files: list[ManifestFile]


# ---- registry ----


class Person(Strict):
    key: Key
    display_name: Text
    aliases: list[Text] | None = None
    role: PersonRole


class RateChange(Strict):
    """A scheduled APR on a variable-rate account (1.4). The identity is effective_on."""

    effective_on: Date
    apr: Rate


class Account(Strict):
    key: Key
    display_name: Text
    institution: Text
    type: AccountType
    owner: Key
    ownership: Ownership
    group: AccountGroup
    is_asset: bool
    include_in_net_worth: bool
    statement_cadence: Cadence
    nw_group: Key | None = None
    nw_group_label: Text | None = None
    subtype: Text | None = None
    custodian: Text | None = None
    advisor: Text | None = None
    in_cashflow: bool | None = None
    tracked_by: TrackedBy | None = None  # None means statements
    stale_after_days: int | None = Field(default=None, ge=1)
    last4: Last4 | None = None
    opened_on: Date | None = None
    closed_on: Date | None = None
    active: bool | None = None
    dormant: bool | None = None
    display_order: int | None = Field(default=None, ge=0)
    apr: Rate | None = None
    rate_type: RateType | None = None
    credit_limit: Money | None = None
    min_payment: Money | None = None
    autopay: Autopay | None = None
    cycle_close_day: int | None = Field(default=None, ge=1, le=31)
    servicer: Text | None = None
    payoff_date: Date | None = None
    priority: int | None = Field(default=None, ge=1)
    policy_note: Text | None = None
    secured_by: Key | None = None
    annual_fee: Money | None = None
    fee_renews_on: Date | None = None
    notes: Text | None = None
    # 1.4. The order of rate_changes is checked in snapshot_issues (_state_v14_issues).
    original: Amount | None = None
    rate_changes: list[RateChange] | None = Field(default=None, max_length=4)

    @model_validator(mode="after")
    def _nw_group(self) -> Account:
        if self.include_in_net_worth and self.nw_group is None:
            raise PydanticCustomError("nw-group", RULES["nw-group"])
        if self.tracked_by == "payments" and self.include_in_net_worth:
            raise PydanticCustomError("tracked-net-worth", RULES["tracked-net-worth"])
        return self

    @model_validator(mode="after")
    def _debt_terms(self) -> Account:
        """On the item as sent, not the stored apr or rate_type. [] is fine anywhere."""
        if self.original is not None and (
            self.type not in DEBT_ORIGINAL_TYPES or to_cents(self.original) == 0
        ):
            _raise("debt-terms")
        if self.rate_changes and (self.apr is None or self.rate_type != "variable"):
            _raise("debt-terms")
        return self


class NewAccount(Strict):
    key: Key
    opened_on: Date | None = None


# ---- history ----


class Statement(Strict):
    account_key: Key
    period_start: Date
    period_end: Date
    txn_count: int = Field(ge=0)
    closing: Money
    reconciled: bool
    opening: Money | None = None
    balance: Money | None = None
    statement_date: Date | None = None
    due_date: Date | None = None
    credits: Money | None = None
    debits: Money | None = None
    fees: Money | None = None
    interest: Money | None = None
    min_due: Money | None = None
    credit_limit: Money | None = None
    txn_sum: Money | None = None
    net_flows: Money | None = None
    market_change: Money | None = None
    reconcile_note: Text | None = None
    ocr: bool | None = None
    has_holdings: bool | None = None
    source_sha256: Sha256 | None = None
    flags_text: Text | None = None

    @model_validator(mode="after")
    def _checks(self) -> Statement:
        if self.period_start > self.period_end:
            raise PydanticCustomError("date-order", RULES["date-order"])
        if not self.reconciled and self.reconcile_note is None:
            raise PydanticCustomError("reconcile-note", RULES["reconcile-note"])
        return self


class Balance(Strict):
    account_key: Key
    as_of: Date
    source: BalanceSource
    balance: Money
    method: ValuationMethod | None = None
    source_note: Text | None = None


class BalanceTombstone(Strict):
    account_key: Key
    as_of: Date
    source: BalanceSource
    deleted: Literal[True]


class Holding(Strict):
    account_key: Key
    period_end: Date
    key: Key
    name: Text
    value: Money
    symbol: Symbol | None = None
    cusip: Cusip | None = None
    quantity: Quantity | None = None
    price: Quantity | None = None
    cost_basis: Money | None = None
    asset_class: Key | None = None


class Paycheck(Strict):
    person: Key
    pay_date: Date
    kind: PaycheckKind
    seq: int = Field(ge=1)
    gross: Money
    net: Money
    period_start: Date | None = None
    period_end: Date | None = None
    k401: Money | None = None
    k401_pct: Rate | None = None
    hsa_employee: Money | None = None
    hsa_employer: Money | None = None
    pretax_health: Money | None = None
    ytd_k401: Money | None = None
    ytd_hsa_employee: Money | None = None
    ytd_hsa_employer: Money | None = None


class PaycheckTombstone(Strict):
    person: Key
    pay_date: Date
    kind: PaycheckKind
    seq: int = Field(ge=1)
    deleted: Literal[True]


class IncomeStream(Strict):
    key: Key
    person: Key
    label: Text
    kind: StreamKind
    schedule: Text
    expected_net: Money
    start_month: Month | None = None
    end_month: Month | None = None
    note: Text | None = None


class Income(Strict):
    paychecks: list[_with_tombstone(Paycheck, PaycheckTombstone)]
    income_streams: list[IncomeStream] | None = None


class Contribution(Strict):
    person: Key
    account_key: Key
    kind: ContributionKind
    source: ContributionSource
    date: Date
    amount: Money


class ContributionTombstone(Strict):
    person: Key
    account_key: Key
    kind: ContributionKind
    source: ContributionSource
    date: Date
    deleted: Literal[True]


# ---- check ----


class GroupAmount(Strict):
    """One amount per group key. Lists of these are sorted by key."""

    key: Key
    amount: Money


class NetWorth(Strict):
    as_of: Date
    value: Money
    assets: Money
    liabilities: Money
    parts: list[GroupAmount]


class CashflowActual(Strict):
    month: Month
    income: Money
    spending: Money
    by_group: list[GroupAmount]
    saved_invested: Money | None = None


# ---- state (1.0) ----


class Meta(Strict):
    updated: Date
    this_month: Month
    complete_as_of: Date | None = None
    location_label: Text | None = None


class CashflowPlan(Strict):
    month: Month
    planned_income: Money
    planned_spending: Money
    by_group: list[GroupAmount]
    note: Text | None = None


class CashflowEvent(Strict):
    key: Key
    effective_month: Month
    delta: Money
    label: Text
    reason: Text | None = None
    linked_kind: Key | None = None
    linked_key: Key | None = None
    status: CashflowEventStatus | None = None  # 1.4. Left out shows no badge.


class CashflowPhase(Strict):
    key: Key
    label: Text
    start_month: Month
    surplus: Money
    end_month: Month | None = None
    reason: Text | None = None
    note: Text | None = None


class BudgetScope(Strict):
    owner: Key | None = None
    cards: list[Key] | None = None
    merchant: Text | None = None
    exclusions: list[Text] | None = None


class Budget(Strict):
    key: Key
    month: Month
    label: Text
    target: Money
    category_key: Key | None = None
    scope: BudgetScope | None = None
    period_basis: PeriodBasis = "calendar"
    cycle_note: Text | None = None
    plan: Money | None = None
    reported_actual: Money | None = None
    actual_source: ActualSource | None = None
    statements_needed: list[Key] | None = None
    note: Text | None = None


class PayoffStep(Strict):
    key: Key
    when_label: Text
    action: Text
    month: Month | None = None


class Todo(Strict):
    key: Key
    title: Text
    status: TodoStatus
    subtitle: Text | None = None
    due_date: Date | None = None
    flag_key: Key | None = None


class KeyNumber(Strict):
    label: Text
    value: Money


class Review(Strict):
    summary: Text
    key_numbers: list[KeyNumber] = Field(default_factory=list)
    changes: list[Text] = Field(default_factory=list)
    anomalies: list[Text] = Field(default_factory=list)
    top_three: list[Text] = Field(default_factory=list, max_length=3)


# ---- state (1.1) ----


def _raise(rule: str) -> None:
    raise PydanticCustomError(rule, RULES[rule])


class Recurring(Strict):
    """A recurring bill or subscription. `amount` is per occurrence."""

    key: Key
    name: Text
    category_key: Key
    amount: Amount
    cadence: RecurringCadence
    status: RecurringStatus
    interval_n: int | None = Field(default=None, ge=2, le=52)
    active_months: list[MonthNumber] | None = Field(default=None, min_length=1, max_length=12)
    next_date: Date | None = None
    start_date: Date | None = None
    end_date: Date | None = None
    renewal_date: Date | None = None
    active_through: Date | None = None
    review_by: Date | None = None
    account_key: Key | None = None
    payment_rail: PaymentRail | None = None
    preferred_account_key: Key | None = None
    owner: Key | None = None
    variable: bool | None = None
    credit_offset: Amount | None = None
    credit_note: Text | None = None
    notes: Text | None = None
    # 1.4. No tag on the personal subscription (recurring-tag-private) is a run-level
    # check, so the privacy rescan and replay can re-validate stored runs.
    keep: OnlyTrue | None = None
    tag: RecurringTag | None = None

    @model_validator(mode="after")
    def _checks(self) -> Recurring:
        if (self.cadence == "every_n_weeks") != (self.interval_n is not None):
            _raise("recurring-interval")
        months = self.active_months
        if months is not None and months != sorted(set(months)):
            _raise("active-months")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            _raise("date-order")
        if self.keep is not None and self.status not in ("active", "paused"):
            _raise("recurring-keep")
        return self


class PlanComponent(Strict):
    merchant: Text
    amount: Amount
    trip_key: Key | None = None


class InstallmentPlan(Strict):
    """A card installment plan. `remaining` is information only, never net worth."""

    key: Key
    account_key: Key
    kind: PlanKind
    name: Text
    created_on: Date
    original: Amount
    status: PlanStatus
    monthly_payment: Amount | None = None
    monthly_fee: Amount | None = None
    total_fees: Amount | None = None
    payments_total: int | None = Field(default=None, ge=1)
    payments_left: int | None = Field(default=None, ge=0)
    remaining: Amount | None = None
    end_month: Month | None = None
    promo_expiry: Date | None = None
    pay_before: Date | None = None
    included_in_spending_plan: bool | None = None
    components: list[PlanComponent] | None = None
    # 1.4
    note: Text | None = None
    paid_off_on: Date | None = None

    @model_validator(mode="after")
    def _checks(self) -> InstallmentPlan:
        if (
            (self.kind == "promo_0pct" and self.promo_expiry is None)
            or (self.kind == "deferred_interest" and self.pay_before is None)
            or (
                self.paid_off_on is not None
                and (self.status == "active" or self.paid_off_on < self.created_on)
            )
        ):
            _raise("plan-dates")
        if (
            self.payments_total is not None
            and self.payments_left is not None
            and self.payments_left > self.payments_total
        ):
            _raise("plan-payments")
        if self.components and sum(to_cents(c.amount) for c in self.components) != to_cents(
            self.original
        ):
            _raise("plan-components")
        return self


class FlagUpdate(Strict):
    """One dated note on a flag (1.4). The identity is on, then text."""

    on: Date
    text: Text


class Flag(Strict):
    key: Key
    severity: FlagSeverity
    status: FlagStatus
    kind: FlagKind
    title: Text
    created_on: Date
    body: Text | None = None
    related_kind: FlagRelatedKind | None = None
    related_key: Key | None = None
    due_date: Date | None = None
    resolved_on: Date | None = None
    archived: bool | None = None
    # 1.4. The updates' order and dates need meta, so they are checked in
    # snapshot_issues (_state_v14_issues).
    action: FlagAction | None = None
    updates: list[FlagUpdate] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def _checks(self) -> Flag:
        if (self.related_kind is None) != (self.related_key is None):
            _raise("flag-related")
        if self.status == "resolved" and self.resolved_on is None:
            _raise("flag-resolved")
        if self.resolved_on and self.created_on > self.resolved_on:
            _raise("date-order")
        return self


class PendingItem(Strict):
    key: Key
    kind: PendingKind
    subject: Text
    status: PendingStatus
    added_on: Date
    detail: Text | None = None
    updated_on: Date | None = None

    @model_validator(mode="after")
    def _checks(self) -> PendingItem:
        if self.updated_on and self.added_on > self.updated_on:
            _raise("date-order")
        return self


# ---- history (1.2): utility bills ----


def utility_total_rule(charges_cents: int, credits_cents: int, total_cents: int) -> str | None:
    """None when lines plus credits equal total, else the warning or reject rule id."""
    off = abs(charges_cents + credits_cents - total_cents)
    if off == 0:
        return None
    return "utility-total-rounding" if off <= UTILITY_ROUNDING_CENTS else "utility-total"


def utility_days_ok(period_start: str, period_end: str, days: int) -> bool:
    """City Power counts days as period_end minus period_start."""
    return (_date.fromisoformat(period_end) - _date.fromisoformat(period_start)).days == days


class UtilityLine(Strict):
    """One service on a bill. `charges` is everything billed for it."""

    service: UtilityService
    usage: Usage
    unit: UtilityUnit
    charges: Amount
    fixed_charges: Amount | None = None

    @model_validator(mode="after")
    def _checks(self) -> UtilityLine:
        if self.unit not in UTILITY_UNITS[self.service]:
            _raise("utility-unit")
        if self.fixed_charges is not None and to_cents(self.fixed_charges) > to_cents(self.charges):
            _raise("utility-fixed")
        return self


class UtilityCredit(Strict):
    label: Text
    amount: Money

    @model_validator(mode="after")
    def _checks(self) -> UtilityCredit:
        if to_cents(self.amount) >= 0:
            _raise("utility-credit-sign")
        return self


class UtilityBill(Strict):
    """One utility bill (City Power gas and electric, a water bill). Never a statement.

    No account number, meter number, service address or customer name.
    """

    utility_key: Key
    provider: Text
    bill_date: Date
    period_start: Date
    period_end: Date
    days: int = Field(ge=1)
    total: Amount
    lines: list[UtilityLine] = Field(min_length=1)
    due_date: Date | None = None
    paid_from: Key | None = None
    heating_degree_days: int | None = Field(default=None, ge=0)
    estimated_read: bool | None = None
    credits: list[UtilityCredit] | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def _checks(self) -> UtilityBill:
        dates = (self.bill_date, self.period_start, self.period_end, self.due_date)
        if any(d is not None and d < UTILITY_EARLIEST for d in dates):
            _raise("utility-date")
        if self.period_start >= self.period_end:
            _raise("utility-period")
        if self.total_rule() == "utility-total":
            _raise("utility-total")
        return self

    def total_rule(self) -> str | None:
        return utility_total_rule(
            sum(to_cents(ln.charges) for ln in self.lines),
            sum(to_cents(c.amount) for c in self.credits or []),
            to_cents(self.total),
        )


class UtilityBillTombstone(Strict):
    utility_key: Key
    period_end: Date
    deleted: Literal[True]


# ---- state (1.3): rewards ----

Year = Annotated[int, Field(ge=2000, le=2100)]


def _together(*values: object) -> bool:
    """True when the values are all set or all left out."""
    return len({v is None for v in values}) == 1


class RewardAllocation(Strict):
    """One year of a timeshare's points."""

    year: Year
    allocated: Usage
    used: Usage
    pulled_forward: Usage | None = None
    expires_on: Date | None = None
    book_by: Date | None = None

    @model_validator(mode="after")
    def _checks(self) -> RewardAllocation:
        spent = to_micro(self.used) + to_micro(self.pulled_forward or "0")
        if spent > to_micro(self.allocated):
            _raise("reward-allocation")
        return self


class RewardProgram(Strict):
    """A points, miles, cash back or timeshare program. The balance is the chat's
    newest: from a statement (as_of = period end) or given by the owner (manual)."""

    key: Key
    name: Text
    kind: RewardKind
    group: RewardGroup
    balance: Usage | None = None
    as_of: Date | None = None
    source: RewardSource | None = None
    owner: Key | None = None
    earned_on: list[Key] | None = None
    status_tier: Text | None = None
    status_via: Key | None = None
    cpp_low: Usage | None = None
    cpp_high: Usage | None = None
    expires_on: Date | None = None
    best_uses: list[Text] | None = Field(default=None, max_length=5)
    avoid: list[Text] | None = Field(default=None, max_length=5)
    note: Text | None = None
    allocations: list[RewardAllocation] | None = None

    @model_validator(mode="after")
    def _checks(self) -> RewardProgram:
        if not _together(self.balance, self.as_of, self.source):
            _raise("reward-as-of")
        if not _together(self.cpp_low, self.cpp_high):
            _raise("reward-value-range")
        if self.cpp_low is not None and self.cpp_high is not None:
            if self.kind == "cash_back" or to_micro(self.cpp_low) > to_micro(self.cpp_high):
                _raise("reward-value-range")
        if self.allocations is not None and self.kind != "timeshare":
            _raise("reward-allocation")
        return self


class BenefitCycle(Strict):
    """One cycle of a credit. `amount` only when it differs from the benefit's."""

    start: Date
    end: Date
    amount: Amount | None = None
    used: Amount | None = None
    used_on: Date | None = None

    @model_validator(mode="after")
    def _checks(self) -> BenefitCycle:
        if self.start > self.end:
            _raise("date-order")
        return self


class RewardBenefit(Strict):
    """A card benefit: a credit, an earning cap, a status perk, a free night."""

    key: Key
    account_key: Key
    name: Text
    kind: BenefitKind
    # credit
    cadence: BenefitCadence | None = None
    amount: Amount | None = None
    cycles: list[BenefitCycle] | None = None
    # every kind
    counted_value: Amount | None = None
    auto: bool | None = None
    enrolled: bool | None = None
    rolls_over: bool | None = None
    enroll_by: Date | None = None
    ends_on: Date | None = None
    how_to_use: Text | None = None
    note: Text | None = None
    # earning_cap
    cap: Amount | None = None
    earn_before: Text | None = None
    earn_after: Text | None = None
    category_keys: list[Key] | None = None
    reported_spent: Amount | None = None
    as_of: Date | None = None
    # status_perk and free_night
    program_key: Key | None = None
    status_tier: Text | None = None

    @model_validator(mode="after")
    def _checks(self) -> RewardBenefit:
        own = BENEFIT_FIELDS[self.kind]
        other = frozenset().union(*BENEFIT_FIELDS.values()) - own
        if any(getattr(self, f) is not None for f in sorted(other)) or any(
            getattr(self, f) is None for f in BENEFIT_REQUIRED[self.kind]
        ):
            _raise("reward-benefit-fields")
        if not _together(self.reported_spent, self.as_of):
            _raise("reward-as-of")
        return self


class RewardMilestone(Strict):
    """A card spend milestone. Two thresholds on one card are two items."""

    key: Key
    account_key: Key
    year: Year
    threshold: Amount
    reward: Text
    status: MilestoneStatus
    reported_spent: Amount | None = None
    as_of: Date | None = None
    met_on: Date | None = None
    plan: Text | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def _checks(self) -> RewardMilestone:
        if not _together(self.reported_spent, self.as_of):
            _raise("reward-as-of")
        return self


class RewardRoute(Strict):
    """Which card to use for which spending."""

    key: Key
    spend: Text
    account_key: Key
    display_order: int = Field(ge=0)
    earn: Text | None = None
    note: Text | None = None


class RewardRedemption(Strict):
    key: Key
    program_key: Key
    title: Text
    status: RedemptionStatus
    points_needed: Usage | None = None
    trip_key: Key | None = None
    note: Text | None = None
    done_on: Date | None = None  # 1.4

    @model_validator(mode="after")
    def _checks(self) -> RewardRedemption:
        if self.done_on is not None and self.status != "done":
            _raise("reward-done-on")
        return self


class Rewards(Strict):
    """The chat's current view of rewards (state, monthly runs only). Every list
    is sorted by key. The newest monthly run wins whole: items left out ended."""

    programs: list[RewardProgram]
    benefits: list[RewardBenefit]
    milestones: list[RewardMilestone]
    routing: list[RewardRoute]
    redemptions: list[RewardRedemption]
    highlights: list[Text] | None = Field(default=None, max_length=5)


# ---- state (1.4): goals ----


# The child and account type checks need the run's people and accounts, so they live
# in snapshot_issues (_state_v14_issues), with the same rule id.
class Goal(Strict):
    """A household goal (state, monthly runs only). `target` is the round target, or the
    total college cost in today's dollars. Progress is the balances of its accounts."""

    key: Key
    name: Text
    kind: GoalKind
    accounts: list[Key] = Field(min_length=1, max_length=10)
    target: Amount
    person: Key | None = None
    start_year: Year | None = None
    target_date: Date | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def _checks(self) -> Goal:
        college = self.kind == "college"
        if (
            college
            and (self.person is None or self.start_year is None or self.target_date is not None)
        ) or (not college and self.start_year is not None):
            _raise("goal-fields")
        return self


# ---- history (1.3): trips ----


def trip_item_amount_ok(status: str, amount: object, low: object, high: object) -> bool:
    """posted, booked and excluded need amount. estimate needs both estimate fields
    and no amount. tbd has neither."""
    if status in ("posted", "booked", "excluded"):
        return amount is not None
    if status == "estimate":
        return amount is None and low is not None and high is not None
    return amount is None and low is None and high is None  # tbd


class TripItem(Strict):
    """One cost of a trip. `amount` is cash for the whole household: for points and
    free nights the taxes and fees, for points_credit the size of the credit."""

    key: Key
    kind: TripItemKind
    label: Text
    status: TripItemStatus
    tender: TripTender
    amount: Amount | None = None
    estimate_low: Amount | None = None
    estimate_high: Amount | None = None
    points: Usage | None = None
    program_key: Key | None = None  # not checked: backfill runs carry no rewards
    account_key: Key | None = None
    person: Key | None = None
    note: Text | None = None
    # 1.4. due_on: the day a booked amount is charged or due. maybe: a cost not booked
    # that may be added; the trip totals leave it out.
    due_on: Date | None = None
    maybe: OnlyTrue | None = None

    @model_validator(mode="after")
    def _checks(self) -> TripItem:
        if not trip_item_amount_ok(self.status, self.amount, self.estimate_low, self.estimate_high):
            _raise("trip-item-amount")
        if not _together(self.estimate_low, self.estimate_high) or (
            self.estimate_low is not None
            and self.estimate_high is not None
            and to_cents(self.estimate_low) > to_cents(self.estimate_high)
        ):
            _raise("trip-estimate")
        if (self.tender == "points" and (self.points is None or self.program_key is None)) or (
            self.tender == "points_credit" and self.amount is None
        ):
            _raise("trip-tender")
        if self.due_on is not None and self.status != "booked":
            _raise("trip-due")
        if self.maybe is not None and self.status not in ("estimate", "tbd"):
            _raise("trip-maybe")
        return self


class TripIou(Strict):
    """Money owed between the household and someone else on a trip. The
    counterparty is a role, like relative or friend couple, never a name."""

    key: Key
    direction: IouDirection
    counterparty: Text
    amount: Amount
    status: IouStatus
    settled_on: Date | None = None
    item_key: Key | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def _checks(self) -> TripIou:
        if (self.status == "settled") != (self.settled_on is not None):
            _raise("trip-iou-settled")
        return self


class Trip(Strict):
    """A trip (history): sent while it changes, kept by the app once final."""

    key: Key
    name: Text
    status: TripStatus
    items: list[TripItem]
    start_date: Date | None = None
    end_date: Date | None = None
    month: Month | None = None
    people: list[Key] | None = None
    guests: int | None = Field(default=None, ge=0, le=20)
    vehicle: Text | None = None
    estimate_low: Amount | None = None
    estimate_high: Amount | None = None
    ious: list[TripIou] | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def _checks(self) -> Trip:
        ranged = self.start_date is not None and self.end_date is not None
        dates = [d for d in (self.start_date, self.end_date) if d is not None]
        if (
            not _together(self.start_date, self.end_date)
            or ranged == (self.month is not None)
            or any(d < TRIP_EARLIEST for d in dates)
            or (self.month is not None and self.month < TRIP_EARLIEST[:7])
        ):
            _raise("trip-dates")
        if ranged and self.start_date > self.end_date:  # type: ignore[operator]
            _raise("date-order")
        if not _together(self.estimate_low, self.estimate_high) or (
            self.estimate_low is not None
            and self.estimate_high is not None
            and to_cents(self.estimate_low) > to_cents(self.estimate_high)
        ):
            _raise("trip-estimate")
        return self


class TripTombstone(Strict):
    key: Key
    deleted: Literal[True]


# ---- snapshot.json ----


class Snapshot(Strict):
    schema_version: SchemaVersion
    run_id: RunIdStr
    mode: Mode
    # registry
    people: list[Person] | None = None
    accounts: list[Account] | None = None
    new_accounts: list[NewAccount] | None = None
    # history
    statements: list[Statement] | None = None
    balances: list[_with_tombstone(Balance, BalanceTombstone)] | None = None
    holdings: list[Holding] | None = None
    income: Income | None = None
    contributions: list[_with_tombstone(Contribution, ContributionTombstone)] | None = None
    # history (1.2)
    utility_bills: list[_with_tombstone(UtilityBill, UtilityBillTombstone)] | None = None
    # history (1.3)
    trips: list[_with_tombstone(Trip, TripTombstone)] | None = None
    # check
    net_worth: NetWorth | None = None
    cashflow_actual: list[CashflowActual] | None = None
    # state
    meta: Meta | None = None
    cashflow_plan: list[CashflowPlan] | None = None
    cashflow_events: list[CashflowEvent] | None = None
    cashflow_phases: list[CashflowPhase] | None = None
    budgets: list[Budget] | None = None
    payoff_sequence: list[PayoffStep] | None = None
    todos: list[Todo] | None = None
    review: Review | None = None
    # state (1.1)
    recurring: list[Recurring] | None = None
    installment_plans: list[InstallmentPlan] | None = None
    flags: list[Flag] | None = None
    pending: list[PendingItem] | None = None
    # state (1.3)
    rewards: Rewards | None = None
    # state (1.4)
    goals: list[Goal] | None = None


# ---- transaction CSV row ----

CSV_HEADER: tuple[str, ...] = (
    "txn_date",
    "post_date",
    "amount",
    "description",
    "merchant",
    "category_key",
    "cardholder",
    "txn_type",
    "is_transfer",
    "counterparty_account_key",
    "is_reimbursable",
    "exclude_from_budget",
    "plan_key",
    "recurring_key",
    "trip_key",
    "hsa_eligible",
    "needs_review",
    "confidence",
    "notes",
)


class CsvRow(Strict):
    """One CSV row. Every cell is a string. Blank means unknown or none."""

    txn_date: Date
    post_date: CsvDate
    amount: Money
    description: Text
    merchant: CsvText
    category_key: Key
    cardholder: CsvKey
    txn_type: TxnType
    is_transfer: CsvBool
    counterparty_account_key: CsvKey
    is_reimbursable: CsvBool
    exclude_from_budget: CsvBool
    plan_key: CsvKey
    recurring_key: CsvKey
    trip_key: CsvKey
    hsa_eligible: CsvBool
    needs_review: CsvBool
    confidence: CsvConfidence
    notes: CsvText

    @property
    def amount_cents(self) -> int:
        return to_cents(self.amount)


def csv_bool(cell: str) -> bool | None:
    """'true' -> True, 'false' -> False, '' -> None."""
    return {"true": True, "false": False}.get(cell)


def csv_sort_key(row: CsvRow | dict[str, str]) -> tuple:
    """CSV rows are sorted by txn_date, amount (numeric), description, then the other columns."""
    get = row.__getitem__ if isinstance(row, dict) else lambda f: getattr(row, f)
    rest = tuple(get(f) for f in CSV_HEADER if f not in ("txn_date", "amount", "description"))
    return (get("txn_date"), to_cents(get("amount")), get("description"), *rest)


# --------------------------------------------------------------------------
# Sections per mode
# --------------------------------------------------------------------------

REGISTRY_SECTIONS = ("people", "accounts", "new_accounts")
HISTORY_SECTIONS = (
    "statements",
    "balances",
    "holdings",
    "income",
    "contributions",
    "utility_bills",
    "trips",
)
CHECK_SECTIONS = ("net_worth", "cashflow_actual")
STATE_SECTIONS = (
    "meta",
    "cashflow_plan",
    "cashflow_events",
    "cashflow_phases",
    "budgets",
    "payoff_sequence",
    "todos",
    "review",
    "recurring",
    "installment_plans",
    "flags",
    "pending",
    "rewards",
    "goals",
)
# income.income_streams is state too, nested inside the history section `income`.
STATE_SUBSECTIONS = (("income", "income_streams"),)

REQUIRED_SECTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "1.0": {
        "monthly": (
            "meta",
            "people",
            "accounts",
            "statements",
            "balances",
            "holdings",
            "income",
            "net_worth",
            "cashflow_actual",
            "cashflow_plan",
            "cashflow_events",
            "budgets",
            "todos",
            "review",
        ),
        "backfill": ("people", "accounts", "statements", "balances", "holdings", "income"),
        "correction": ("statements",),
    }
}
# 1.1 adds four state sections to the monthly list. Backfill and correction are unchanged.
REQUIRED_SECTIONS["1.1"] = {
    **REQUIRED_SECTIONS["1.0"],
    "monthly": (
        *REQUIRED_SECTIONS["1.0"]["monthly"],
        "recurring",
        "installment_plans",
        "flags",
        "pending",
    ),
}
# 1.2 adds the optional history section utility_bills. No mode requires it.
REQUIRED_SECTIONS["1.2"] = dict(REQUIRED_SECTIONS["1.1"])
# 1.3 adds rewards (state, required in monthly runs) and trips (history, never required).
REQUIRED_SECTIONS["1.3"] = {
    **REQUIRED_SECTIONS["1.2"],
    "monthly": (*REQUIRED_SECTIONS["1.2"]["monthly"], "rewards"),
}
# 1.4 adds optional fields and values in existing sections, and goals (state, never required).
REQUIRED_SECTIONS["1.4"] = dict(REQUIRED_SECTIONS["1.3"])
# The first schema version that has each later section. Older runs may not carry it.
SECTION_SINCE: dict[str, str] = {
    "recurring": "1.1",
    "installment_plans": "1.1",
    "flags": "1.1",
    "pending": "1.1",
    "utility_bills": "1.2",
    "rewards": "1.3",
    "trips": "1.3",
    "goals": "1.4",
}
# The first schema version that has each later field, as a dotted path of field names.
# Lists on the way are walked item by item: 'trips.items.due_on' is every trip item's
# due_on. A field counts when present, even as null. Older runs may not carry it.
FIELD_SINCE: dict[str, str] = {
    "accounts.original": "1.4",
    "accounts.rate_changes": "1.4",
    "cashflow_events.status": "1.4",
    "flags.action": "1.4",
    "flags.updates": "1.4",
    "installment_plans.note": "1.4",
    "installment_plans.paid_off_on": "1.4",
    "recurring.keep": "1.4",
    "recurring.tag": "1.4",
    "rewards.redemptions.done_on": "1.4",
    "trips.items.due_on": "1.4",
    "trips.items.maybe": "1.4",
}
# The first schema version that has each later value of a field (same paths).
VALUE_SINCE: dict[str, dict[str, str]] = {
    "flags.kind": dict.fromkeys(
        ("retirement", "investment", "insurance", "medical", "payroll", "home", "other"), "1.4"
    ),
}

# --------------------------------------------------------------------------
# Issues
# --------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Issue:
    """A problem found in a run: a rule id and where. Never the value."""

    rule: str
    where: str

    @property
    def message(self) -> str:
        return RULES.get(self.rule, RULES["schema"])


_TAGS = {"item", "tombstone"}


def format_loc(loc: tuple[str | int, ...], prefix: str = "") -> str:
    """('statements', 3, 'closing') -> 'statements[3].closing'. Drops union tags."""
    out = prefix
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part in _TAGS or "[" in part or " " in part:
            continue  # union branch tag or pydantic internal name
        else:
            out += f".{part}" if out and not out.endswith((":", ".")) else part
    return out


def issues_from_error(exc: ValidationError, prefix: str = "") -> list[Issue]:
    """Map a ValidationError to rule ids and locations. Input values are dropped.

    An unknown field name is part of the location. If that name itself looks
    sensitive, the location is hidden.
    """
    # local import: sanitize imports models
    from app.importer.sanitize import detect_sensitive, location_section

    issues = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        rule = err["type"] if err["type"] in RULES else "schema"
        where = format_loc(err["loc"], prefix)
        if detect_sensitive(where, location_section(where)):
            where = f"{prefix}(hidden name)"
        issues.append(Issue(rule, where))
    return sorted(set(issues))


def _parse_json_bytes(data: bytes, where: str) -> tuple[dict | None, list[Issue]]:
    if data.startswith(b"\xef\xbb\xbf"):
        return None, [Issue("encoding", where)]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, [Issue("encoding", where)]
    issues = []
    if "\r" in text or not text.endswith("\n"):
        issues.append(Issue("line-endings", where))
    try:
        obj = json.loads(text)
    except ValueError:
        return None, [*issues, Issue("json", where)]
    if not isinstance(obj, dict):
        return None, [*issues, Issue("json", where)]
    return obj, issues


def _version_issue(obj: dict, where: str) -> list[Issue]:
    rule = version_rule(obj.get("schema_version"))
    return [Issue(rule, f"{where}:schema_version")] if rule else []


def load_manifest(data: bytes, where: str = "manifest.json") -> tuple[Manifest | None, list[Issue]]:
    """Parse manifest.json bytes. Checks the version before the strict model."""
    obj, issues = _parse_json_bytes(data, where)
    if obj is None:
        return None, issues
    if v := _version_issue(obj, where):
        return None, issues + v
    try:
        return Manifest.model_validate(obj), issues
    except ValidationError as exc:
        return None, issues + issues_from_error(exc, f"{where}:")


def load_snapshot(data: bytes, where: str = "snapshot.json") -> tuple[Snapshot | None, list[Issue]]:
    """Parse snapshot.json bytes, then run the single-file checks (`snapshot_issues`)."""
    obj, issues = _parse_json_bytes(data, where)
    if obj is None:
        return None, issues
    if v := _version_issue(obj, where):
        return None, issues + v
    try:
        snap = Snapshot.model_validate(obj)
    except ValidationError as exc:
        return None, issues + issues_from_error(exc, f"{where}:")
    return snap, issues + snapshot_issues(snap, where)


def parse_csv(data: bytes, where: str) -> tuple[list[CsvRow], list[Issue]]:
    """Parse one transaction CSV. Locations look like '<where>:row 3.amount' (1-based rows)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return [], [Issue("encoding", where)]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [], [Issue("encoding", where)]
    issues: list[Issue] = []
    if "\r" in text or not text.endswith("\n"):
        issues.append(Issue("line-endings", where))
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None or tuple(header) != CSV_HEADER:
        return [], [*issues, Issue("csv-header", where)]
    rows: list[CsvRow] = []
    raw_rows: list[dict[str, str]] = []
    for n, cells in enumerate(reader, start=1):
        if len(cells) != len(CSV_HEADER):
            issues.append(Issue("csv-format", f"{where}:row {n}"))
            continue
        record = dict(zip(CSV_HEADER, cells, strict=True))
        try:
            rows.append(CsvRow.model_validate(record))
            raw_rows.append(record)
        except ValidationError as exc:
            issues += issues_from_error(exc, f"{where}:row {n}.")
    if not rows and not issues:
        issues.append(Issue("csv-empty", where))
    if not issues:
        keys = [csv_sort_key(r) for r in raw_rows]
        for i in range(1, len(keys)):
            if keys[i] < keys[i - 1]:
                issues.append(Issue("csv-sort", f"{where}:row {i + 1}"))
                break
    return rows, issues


# --------------------------------------------------------------------------
# Single-file structural checks
# --------------------------------------------------------------------------


# Modes that may not carry state sections (PLAN 4.3, 4.9).
NO_STATE_MODES = frozenset({"backfill", "correction"})


def since_nodes(root: BaseModel, path: str, where: str) -> list[tuple[BaseModel, str]]:
    """The objects a FIELD_SINCE or VALUE_SINCE path ends in, with their locations.

    Lists on the way are walked item by item: 'trips.items.due_on' gives
    (item, 'snapshot.json:trips[0].items[1]') for each trip item. An object without
    the next field (a tombstone, or a field no model has yet) is skipped.
    check_run.since_nodes walks the raw JSON the same way.
    """
    nodes: list[tuple[BaseModel, str]] = [(root, f"{where}:")]
    for part in path.split(".")[:-1]:
        found: list[tuple[BaseModel, str]] = []
        for obj, loc in nodes:
            if part not in type(obj).model_fields:
                continue
            value = getattr(obj, part)
            here = f"{loc}{part}" if loc.endswith(":") else f"{loc}.{part}"
            if isinstance(value, list):
                found += [
                    (item, f"{here}[{i}]")
                    for i, item in enumerate(value)
                    if isinstance(item, BaseModel)
                ]
            elif isinstance(value, BaseModel):
                found.append((value, here))
        nodes = found
    return nodes


def section_issues(snap: Snapshot, where: str = "snapshot.json") -> list[Issue]:
    """Required sections per mode, no state sections in backfill or correction runs, and
    no section, field or value newer than the run's schema_version. A section or field
    counts when present, even as null (model_fields_set). The location is the field, never
    the value.

    Import only: replay, reapply, the privacy rescan and the state readers never call
    it, so stored runs are not gated again."""
    issues = []
    old = version_tuple(snap.schema_version)
    for name in REQUIRED_SECTIONS[snap.schema_version][snap.mode]:
        if getattr(snap, name) is None:
            issues.append(Issue("section-missing", f"{where}:{name}"))
    for name, since in SECTION_SINCE.items():
        if name in snap.model_fields_set and old < version_tuple(since):
            issues.append(Issue("section-version", f"{where}:{name}"))
    for path, since in FIELD_SINCE.items():
        if old < version_tuple(since):
            attr = path.rsplit(".", 1)[1]
            for obj, loc in since_nodes(snap, path, where):
                if attr in obj.model_fields_set:
                    issues.append(Issue("section-version", f"{loc}.{attr}"))
    for path, values in VALUE_SINCE.items():
        attr = path.rsplit(".", 1)[1]
        for obj, loc in since_nodes(snap, path, where):
            value = getattr(obj, attr, None)
            since = values.get(value) if isinstance(value, str) else None
            if since is not None and old < version_tuple(since):
                issues.append(Issue("section-version", f"{loc}.{attr}"))
    if snap.mode in NO_STATE_MODES:
        for name in STATE_SECTIONS:
            if getattr(snap, name) is not None:
                issues.append(Issue("section-not-allowed", f"{where}:{name}"))
        if snap.income is not None and snap.income.income_streams is not None:
            issues.append(Issue("section-not-allowed", f"{where}:income.income_streams"))
    return issues


def _ident(fields: tuple[str, ...]) -> Callable[[Any], tuple]:
    return lambda item: tuple(getattr(item, f) for f in fields)


# Identity of each list item. Lists must be sorted by it, with no duplicates.
IDENTITY: dict[str, tuple[str, ...]] = {
    "people": ("key",),
    "accounts": ("key",),
    "new_accounts": ("key",),
    "statements": ("account_key", "period_end"),
    "balances": ("account_key", "as_of", "source"),
    "holdings": ("account_key", "period_end", "key"),
    "income.paychecks": ("person", "pay_date", "kind", "seq"),
    "income.income_streams": ("key",),
    "contributions": ("person", "account_key", "kind", "source", "date"),
    "cashflow_actual": ("month",),
    "cashflow_plan": ("month",),
    "cashflow_events": ("key",),
    "cashflow_phases": ("key",),
    "budgets": ("month", "key"),
    "payoff_sequence": ("key",),
    "todos": ("key",),
    "recurring": ("key",),
    "installment_plans": ("key",),
    "flags": ("key",),
    "pending": ("key",),
    "utility_bills": ("utility_key", "period_end"),
    "rewards.programs": ("key",),
    "rewards.benefits": ("key",),
    "rewards.milestones": ("key",),
    "rewards.routing": ("key",),
    "rewards.redemptions": ("key",),
    "trips": ("key",),
    "goals": ("key",),
}


def section_items(snap: Snapshot, name: str) -> list | None:
    """Get a list section by its IDENTITY name. '<section>.<list>' is a list inside
    a section, like 'income.paychecks' or 'rewards.programs'."""
    if "." in name:
        section, inner = name.split(".", 1)
        parent = getattr(snap, section)
        return getattr(parent, inner) if parent is not None else None
    return getattr(snap, name)


def snapshot_issues(snap: Snapshot, where: str = "snapshot.json") -> list[Issue]:
    """Checks that need only snapshot.json: sections, identity order, paycheck seq."""
    issues = section_issues(snap, where)
    for name, fields in IDENTITY.items():
        items = section_items(snap, name)
        if not items:
            continue
        ids = [_ident(fields)(it) for it in items]
        for i in range(1, len(ids)):
            if ids[i] == ids[i - 1] or ids[i] in ids[:i]:
                issues.append(Issue("key-duplicate", f"{where}:{name}[{i}]"))
                break
        for i in range(1, len(ids)):
            if ids[i] < ids[i - 1]:
                issues.append(Issue("sort-order", f"{where}:{name}[{i}]"))
                break
    for loc, groups in _group_lists(snap):
        keys = [g.key for g in groups]
        if len(set(keys)) != len(keys):
            issues.append(Issue("key-duplicate", f"{where}:{loc}"))
        elif keys != sorted(keys):
            issues.append(Issue("sort-order", f"{where}:{loc}"))
    if snap.income:
        issues += _paycheck_seq_issues(snap.income, where)
    issues += _utility_list_issues(snap, where)
    issues += _rewards_trips_list_issues(snap, where)
    issues += _state_v14_issues(snap, where)
    return issues


def _utility_list_issues(snap: Snapshot, where: str) -> list[Issue]:
    """Lines sorted by service, one per service. Credits sorted by label, then amount."""
    issues = []
    for i, b in enumerate(snap.utility_bills or []):
        if not isinstance(b, UtilityBill):
            continue
        lists = [("lines", [(ln.service,) for ln in b.lines])]
        if b.credits:
            lists.append(("credits", [(c.label, to_cents(c.amount)) for c in b.credits]))
        for name, ids in lists:
            loc = f"{where}:utility_bills[{i}].{name}"
            if len(set(ids)) != len(ids):
                issues.append(Issue("key-duplicate", loc))
            elif ids != sorted(ids):
                issues.append(Issue("sort-order", loc))
    return issues


def _order_issues(loc: str, ids: list[tuple]) -> list[Issue]:
    if len(set(ids)) != len(ids):
        return [Issue("key-duplicate", loc)]
    if ids != sorted(ids):
        return [Issue("sort-order", loc)]
    return []


def _rewards_trips_list_issues(snap: Snapshot, where: str) -> list[Issue]:
    """Lists inside rewards and trips items: sorted, no duplicates. And references
    inside the section: program_key in programs, an IOU's item_key in its trip."""
    issues = []
    r = snap.rewards
    if r is not None:
        for i, p in enumerate(r.programs):
            loc = f"{where}:rewards.programs[{i}]"
            issues += _order_issues(f"{loc}.earned_on", [(k,) for k in p.earned_on or []])
            issues += _order_issues(f"{loc}.allocations", [(a.year,) for a in p.allocations or []])
        programs = {p.key for p in r.programs}
        for i, b in enumerate(r.benefits):
            loc = f"{where}:rewards.benefits[{i}]"
            issues += _order_issues(f"{loc}.cycles", [(c.start,) for c in b.cycles or []])
            issues += _order_issues(f"{loc}.category_keys", [(k,) for k in b.category_keys or []])
            if b.program_key is not None and b.program_key not in programs:
                issues.append(Issue("reward-program-unknown", f"{loc}.program_key"))
        for i, x in enumerate(r.redemptions):
            if x.program_key not in programs:
                loc = f"{where}:rewards.redemptions[{i}].program_key"
                issues.append(Issue("reward-program-unknown", loc))
    for i, t in enumerate(snap.trips or []):
        if not isinstance(t, Trip):
            continue
        loc = f"{where}:trips[{i}]"
        issues += _order_issues(f"{loc}.people", [(k,) for k in t.people or []])
        issues += _order_issues(f"{loc}.items", [(it.key,) for it in t.items])
        issues += _order_issues(f"{loc}.ious", [(o.key,) for o in t.ious or []])
        items = {it.key for it in t.items}
        for j, o in enumerate(t.ious or []):
            if o.item_key is not None and o.item_key not in items:
                issues.append(Issue("trip-item-unknown", f"{loc}.ious[{j}].item_key"))
    return issues


def _state_v14_issues(snap: Snapshot, where: str) -> list[Issue]:
    """Format 1.4 checks that need more than one item. Flag updates: sorted by on, then
    text, and each on from the flag's created_on to meta.updated. No tag on the personal
    subscription (here, not in the model, so the privacy rescan can re-validate a run
    whose name it masked). Goals: accounts sorted, and a college goal's person is a child
    in this run's people and its accounts are 529 or custodial accounts in this run's
    accounts (a monthly run sends the full registry). An account's rate_changes: sorted
    by effective_on, no repeats."""
    issues: list[Issue] = []
    for i, a in enumerate(snap.accounts or []):
        loc = f"{where}:accounts[{i}].rate_changes"
        issues += _order_issues(loc, [(rc.effective_on,) for rc in a.rate_changes or []])
    updated = snap.meta.updated if snap.meta is not None else None
    for i, f in enumerate(snap.flags or []):
        loc = f"{where}:flags[{i}].updates"
        issues += _order_issues(loc, [(u.on, u.text) for u in f.updates or []])
        for j, u in enumerate(f.updates or []):
            if u.on < f.created_on or (updated is not None and u.on > updated):
                issues.append(Issue("date-order", f"{loc}[{j}].on"))
    for i, r in enumerate(snap.recurring or []):
        if r.tag is not None and is_personal_subscription(r.name):
            issues.append(Issue("recurring-tag-private", f"{where}:recurring[{i}].tag"))
    roles = {p.key: p.role for p in snap.people or []}
    types = {a.key: a.type for a in snap.accounts or []}
    for i, g in enumerate(snap.goals or []):
        loc = f"{where}:goals[{i}]"
        issues += _order_issues(f"{loc}.accounts", [(k,) for k in g.accounts])
        if g.kind == "college" and (
            roles.get(g.person or "") != "child"
            or any(types.get(k) not in COLLEGE_ACCOUNT_TYPES for k in g.accounts)
        ):
            issues.append(Issue("goal-fields", loc))
    return issues


RECURRING_WARNING_RULES = ("recurring-no-date",)


def recurring_warnings(snap: Snapshot, where: str = "snapshot.json") -> list[Issue]:
    """recurring-no-date: an active item with no next_date, start_date or renewal_date,
    so it has no due date. A warning, not a reject, and not version gated."""
    return [
        Issue("recurring-no-date", f"{where}:recurring[{i}]")
        for i, r in enumerate(snap.recurring or [])
        if r.status == "active" and not any(getattr(r, f) for f in RECURRING_DATE_FIELDS)
    ]


REWARD_WARNING_RULES = ("reward-stale", "trip-unknown")


def reward_warnings(snap: Snapshot, where: str = "snapshot.json") -> list[Issue]:
    """reward-stale: a balance or reported_spent dated more than REWARD_STALE_DAYS
    before the run date. A warning, not a reject."""
    if snap.rewards is None:
        return []
    run_day = _date.fromisoformat(snap.run_id[:10])
    limit = (run_day - timedelta(days=REWARD_STALE_DAYS)).isoformat()
    out = []
    for name in ("programs", "benefits", "milestones"):
        for i, item in enumerate(getattr(snap.rewards, name)):
            if item.as_of is not None and item.as_of < limit:
                out.append(Issue("reward-stale", f"{where}:rewards.{name}[{i}].as_of"))
    return out


def trip_references(snap: Snapshot, where: str = "snapshot.json") -> Iterator[tuple[str, str]]:
    """(trip key, location) for every trip_key outside `trips` in snapshot.json:
    plan components, flags about a trip and redemptions. CSV rows are the caller's."""
    for i, p in enumerate(snap.installment_plans or []):
        for j, c in enumerate(p.components or []):
            if c.trip_key:
                yield c.trip_key, f"{where}:installment_plans[{i}].components[{j}].trip_key"
    for i, f in enumerate(snap.flags or []):
        if f.related_kind == "trip" and f.related_key:
            yield f.related_key, f"{where}:flags[{i}].related_key"
    if snap.rewards is not None:
        for i, x in enumerate(snap.rewards.redemptions):
            if x.trip_key:
                yield x.trip_key, f"{where}:rewards.redemptions[{i}].trip_key"


def defined_trips(snap: Snapshot) -> set[str]:
    """Trip keys a run defines: its trips, tombstones left out."""
    return {t.key for t in snap.trips or [] if isinstance(t, Trip)}


UTILITY_WARNING_RULES = ("utility-total-rounding", "utility-days", "utility-overlap")


def utility_warnings(snap: Snapshot, where: str = "snapshot.json") -> list[Issue]:
    """Warnings, not rejects: a 1 or 2 cent total difference, days that do not match
    the period, and a period that overlaps the bill before it (same utility, in this run).
    """
    out = []
    prev: dict[str, str] = {}  # utility_key -> period_end of the bill before
    for i, b in enumerate(snap.utility_bills or []):
        if not isinstance(b, UtilityBill):
            continue
        loc = f"{where}:utility_bills[{i}]"
        if b.total_rule() == "utility-total-rounding":
            out.append(Issue("utility-total-rounding", loc))
        if not utility_days_ok(b.period_start, b.period_end, b.days):
            out.append(Issue("utility-days", loc))
        before = prev.get(b.utility_key)
        if before is not None and b.period_start < before:
            out.append(Issue("utility-overlap", loc))
        prev[b.utility_key] = b.period_end
    return out


def _group_lists(snap: Snapshot) -> Iterator[tuple[str, list[GroupAmount]]]:
    if snap.net_worth:
        yield "net_worth.parts", snap.net_worth.parts
    for name in ("cashflow_actual", "cashflow_plan"):
        for i, item in enumerate(getattr(snap, name) or []):
            yield f"{name}[{i}].by_group", item.by_group


def _paycheck_seq_issues(income: Income, where: str) -> list[Issue]:
    groups: dict[tuple, list[tuple[int, int]]] = {}
    for p in income.paychecks:
        if isinstance(p, Paycheck):
            groups.setdefault((p.person, p.pay_date, p.kind), []).append((p.seq, to_cents(p.gross)))
    for members in groups.values():
        members.sort()
        seqs = [s for s, _ in members]
        grosses = [g for _, g in members]
        if seqs != list(range(1, len(seqs) + 1)) or grosses != sorted(grosses):
            return [Issue("paycheck-seq", f"{where}:income.paychecks")]
    return []


# --------------------------------------------------------------------------
# Text walk for the redaction scan
# --------------------------------------------------------------------------


def _has_noscan(meta: Any) -> bool:
    return any(isinstance(m, NoScan) for m in meta)


def _split(ann: Any) -> tuple[Any, tuple]:
    """Unwrap Annotated: return (inner type, metadata)."""
    if get_origin(ann) is Annotated:
        args = get_args(ann)
        inner, meta = _split(args[0])
        return inner, tuple(args[1:]) + meta
    return ann, ()


def _is_union(ann: Any) -> bool:
    return get_origin(ann) in (Union, UnionType)


def _matches(ann: Any, value: Any) -> bool:
    inner, _ = _split(ann)
    origin = get_origin(inner) or inner
    if origin is Literal:
        return value in get_args(inner)
    return isinstance(origin, type) and isinstance(value, origin)


FieldPath = tuple[str | int, ...]


def _is_text(meta: tuple) -> bool:
    """Free text (Text, CsvText), not a key or a fixed word."""
    return any(
        isinstance(m, WithJsonSchema)
        and isinstance(m.json_schema, dict)
        and m.json_schema.get("x-rule") == "text-format"
        for m in meta
    )


def _walk(
    value: Any, ann: Any, meta: tuple, path: FieldPath
) -> Iterator[tuple[FieldPath, str, tuple]]:
    if value is None:
        return
    if isinstance(value, BaseModel):
        yield from _fields(value, path)
        return
    ann, more = _split(ann)
    meta = meta + more
    if _is_union(ann):  # e.g. `Money | None`: follow the branch that holds the value
        branch = next((a for a in get_args(ann) if _matches(a, value)), None)
        if branch is not None:
            yield from _walk(value, branch, meta, path)
        return
    if _has_noscan(meta):
        return
    if isinstance(value, str):
        yield path, value, meta
        return
    if isinstance(value, list):
        args = get_args(ann)
        elem = args[0] if args else Any
        for i, v in enumerate(value):
            yield from _walk(v, elem, (), (*path, i))


def _fields(model: BaseModel, path: FieldPath) -> Iterator[tuple[FieldPath, str, tuple]]:
    for name, field in type(model).model_fields.items():
        yield from _walk(
            getattr(model, name), field.annotation, tuple(field.metadata), (*path, name)
        )


def _loc(prefix: str, path: FieldPath) -> str:
    """('trips', 0, 'note') -> 'trips[0].note'."""
    loc = prefix
    for part in path:
        loc = f"{loc}[{part}]" if isinstance(part, int) else f"{loc}.{part}" if loc else part
    return loc


def iter_text(model: BaseModel, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield (location, text) for every string that the redaction scan must check.

    Formatted fields (money, dates, hashes, last4, CUSIP, ...) are skipped.
    Keys and free text are included.
    """
    for path, text, _ in _fields(model, ()):
        yield _loc(prefix, path), text


def text_paths(model: BaseModel) -> Iterator[tuple[FieldPath, str, bool]]:
    """Like `iter_text`, as (path, text, is_text). The path indexes the model's
    JSON dump. is_text: free text the privacy rescan may mask; False for keys
    and fixed words, which it never masks."""
    for path, text, meta in _fields(model, ()):
        yield path, text, _is_text(meta)


__all__ = [n for n in dir() if not n.startswith("_")]
