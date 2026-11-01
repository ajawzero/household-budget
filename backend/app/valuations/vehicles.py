"""Vehicle value estimate between KBB checks (PLAN 3.9, 6.3).

The chat's valuation V0 on d0 is the KBB value. KBB does not count a camper
conversion, so the conversion C (set in the app, valued on dc) is added here
and loses value at its own rate. This applies only when V0's method is `kbb`.
A `manual` or other valuation is taken as the whole vehicle, conversion
included, so C is not added (estimate.py), to avoid counting it twice:

    C(t)  = C * (1 - rc) ** (max(t - dc, 0) / 365.25)    0 with no conversion
    B(t)  = V0 * (1 - rb) ** ((t - d0) / 365.25) * mileage factor
    est   = B(t) + C(t)

t is the latest month end on or before the day (the estimate moves monthly),
never before d0. With a conversion the estimate also covers d0 itself
(V0 + C(d0)), because the stored KBB value leaves the conversion out.

Mileage (base vehicle only): miles over pace from the odometer on d0 to the
newest reading on or before the day,

    odometer(d0) = interpolated between the readings around d0 (the first
                   reading after d0 when there is none before it)
    excess = miles driven - expected miles a year * years between them
    factor = 1 - k * excess / 1000, kept between 0.80 and 1.10

Miles driven before d0 are already in the KBB value, so they never count.
k defaults to 0.5% per 1,000 miles. With no reading after d0 the factor is 1.

Defaults (the owner can change each one): base vehicle rate by age from the model
year (under 1 year 16%, 1 to 5 years 13%, 6 to 10 years 10%, older 7%), 12%
without a model year; 12,000 miles a year; conversion 8% a year.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_RATE_MILLI_PCT = 12_000
AGE_RATES = ((1, 16_000), (6, 13_000), (11, 10_000))  # (age below, rate)
OLD_RATE_MILLI_PCT = 7_000
DEFAULT_MILES_YEAR = 12_000
DEFAULT_MILEAGE_MILLI_PCT = 500  # 0.5% of chassis value per 1,000 miles over pace
DEFAULT_CONVERSION_MILLI_PCT = 8_000
FACTOR_MIN, FACTOR_MAX = 0.80, 1.10
YEAR_DAYS = 365.25


@dataclass(frozen=True)
class VehicleSettings:
    """Stored settings for one vehicle. None means "use the default"."""

    account_key: str
    enabled: bool = True
    model_year: int | None = None
    depreciation_milli_pct: int | None = None
    expected_miles_year: int | None = None
    mileage_milli_pct: int | None = None
    conversion_value_cents: int | None = None
    conversion_value_date: str | None = None
    conversion_milli_pct: int | None = None
    updated_at: str | None = None

    def rate(self, on: date) -> int:
        if self.depreciation_milli_pct is not None:
            return self.depreciation_milli_pct
        return age_rate(self.model_year, on)

    def rate_is_default(self) -> bool:
        return self.depreciation_milli_pct is None

    @property
    def miles_year(self) -> int:
        return DEFAULT_MILES_YEAR if self.expected_miles_year is None else self.expected_miles_year

    @property
    def mileage_rate(self) -> int:
        return (
            DEFAULT_MILEAGE_MILLI_PCT if self.mileage_milli_pct is None else self.mileage_milli_pct
        )

    @property
    def conversion_rate(self) -> int:
        if self.conversion_milli_pct is None:
            return DEFAULT_CONVERSION_MILLI_PCT
        return self.conversion_milli_pct

    @property
    def has_conversion(self) -> bool:
        return bool(self.conversion_value_cents) and self.conversion_value_date is not None


def age_rate(model_year: int | None, on: date) -> int:
    if model_year is None:
        return DEFAULT_RATE_MILLI_PCT
    age = on.year - model_year
    for below, rate in AGE_RATES:
        if age < below:
            return rate
    return OLD_RATE_MILLI_PCT


@dataclass(frozen=True)
class Reading:
    id: int
    reading_date: str
    miles: int
    entered_at: str


def load_settings(conn: sqlite3.Connection) -> dict[str, VehicleSettings]:
    out = {}
    for r in conn.execute("SELECT * FROM vehicle_settings"):
        out[r["account_key"]] = VehicleSettings(
            account_key=r["account_key"],
            enabled=bool(r["enabled"]),
            model_year=r["model_year"],
            depreciation_milli_pct=r["depreciation_milli_pct"],
            expected_miles_year=r["expected_miles_year"],
            mileage_milli_pct=r["mileage_milli_pct"],
            conversion_value_cents=r["conversion_value_cents"],
            conversion_value_date=r["conversion_value_date"],
            conversion_milli_pct=r["conversion_milli_pct"],
            updated_at=r["updated_at"],
        )
    return out


def load_readings(conn: sqlite3.Connection) -> dict[str, list[Reading]]:
    out: dict[str, list[Reading]] = {}
    for r in conn.execute("SELECT * FROM vehicle_readings ORDER BY account_key, reading_date"):
        out.setdefault(r["account_key"], []).append(
            Reading(r["id"], r["reading_date"], r["miles"], r["entered_at"])
        )
    return out


def month_step(day: date, floor: date) -> date:
    """The latest month end on or before `day`, but never before `floor`."""
    end = day.replace(day=1) - timedelta(days=1)
    nxt = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    step = day if day == nxt else end
    return max(step, floor)


def _decay(rate_milli_pct: int, days: int) -> float:
    return (1 - rate_milli_pct / 100_000) ** (days / YEAR_DAYS)


@dataclass(frozen=True)
class Mileage:
    base_date: str
    base_miles: int
    now_date: str
    now_miles: int
    excess_miles: int  # driven minus the expected pace; negative is under pace
    factor: float


def odometer_at(readings: list[Reading], day: date) -> tuple[str, float] | None:
    """(date, miles) of the odometer on `day`, interpolated between the readings
    around it. The first reading after `day` when there is none on or before it.
    None when no reading is on or after `day` (nothing to measure from)."""
    dates = [r.reading_date for r in readings]
    k = bisect_right(dates, day.isoformat())
    if k == len(readings):
        return None
    after = readings[k]
    if k == 0:
        return after.reading_date, float(after.miles)
    before = readings[k - 1]
    if before.reading_date == day.isoformat():
        return before.reading_date, float(before.miles)
    a, b = date.fromisoformat(before.reading_date), date.fromisoformat(after.reading_date)
    share = (day - a).days / (b - a).days
    return day.isoformat(), before.miles + (after.miles - before.miles) * share


def mileage(readings: list[Reading], d0: date, day: date, s: VehicleSettings) -> Mileage | None:
    """Miles over pace from the odometer on d0 to the newest reading on or before `day`."""
    if not readings:
        return None
    dates = [r.reading_date for r in readings]
    upto = bisect_right(dates, day.isoformat())
    if upto == 0:
        return None
    base = odometer_at(readings, d0)
    if base is None:
        return None
    base_date, base_miles = base
    now = readings[upto - 1]
    if now.reading_date <= base_date:
        return None
    days = (date.fromisoformat(now.reading_date) - date.fromisoformat(base_date)).days
    excess = (now.miles - base_miles) - s.miles_year * days / YEAR_DAYS
    factor = 1 - (s.mileage_rate / 100_000) * excess / 1000
    return Mileage(
        base_date,
        round(base_miles),
        now.reading_date,
        now.miles,
        round(excess),
        min(FACTOR_MAX, max(FACTOR_MIN, factor)),
    )


@dataclass(frozen=True)
class VehicleEstimate:
    value_cents: int
    step: str  # the month end the depreciation runs to
    chassis_cents: int  # the base vehicle (the KBB part)
    conversion_cents: int
    chassis_rate_milli_pct: int
    conversion_rate_milli_pct: int | None
    mileage: Mileage | None
    chassis_floored: bool  # kept for the API; always False now that V0 is KBB only


def conversion_at(s: VehicleSettings, t: date) -> float:
    """The conversion's value on `t` (0 without one)."""
    if not s.has_conversion:
        return 0.0
    dc = date.fromisoformat(s.conversion_value_date or "")
    return float(s.conversion_value_cents or 0) * _decay(s.conversion_rate, max((t - dc).days, 0))


def estimate(
    v0: int, d0: date, day: date, s: VehicleSettings, readings: list[Reading]
) -> VehicleEstimate | None:
    """The estimate on `day` from the KBB value V0 on d0. None when nothing moved yet.

    With a conversion there is always an estimate from d0 on: V0 alone leaves
    the conversion out."""
    if not s.enabled or day < d0 or (day == d0 and not s.has_conversion):
        return None
    step = month_step(day, d0)
    miles = mileage(readings, d0, day, s)
    if step <= d0 and miles is None and not s.has_conversion:
        return None
    rc = s.conversion_rate if s.has_conversion else None
    conv = conversion_at(s, step)
    rb = s.rate(d0)
    chassis = max(v0, 0) * _decay(rb, (step - d0).days) * (miles.factor if miles else 1.0)
    return VehicleEstimate(
        value_cents=round(chassis) + round(conv),
        step=step.isoformat(),
        chassis_cents=round(chassis),
        conversion_cents=round(conv),
        chassis_rate_milli_pct=rb,
        conversion_rate_milli_pct=rc,
        mileage=miles,
        chassis_floored=False,
    )


def pct_label(milli_pct: int) -> str:
    """12_000 -> "12", 7_500 -> "7.5"."""
    whole, frac = divmod(milli_pct, 1000)
    return f"{whole}" if not frac else f"{whole}.{frac:03d}".rstrip("0")


def note(est: VehicleEstimate, d0_label: str, method_label: str) -> str:
    """Like "Estimated from the Jul 15 KBB value plus the conversion. Base vehicle
    loses 10% a year, conversion 8% a year, 2,100 miles over pace." """
    if est.conversion_rate_milli_pct is not None:
        base = f"Estimated from the {d0_label} {method_label} plus the conversion"
        rates = (
            f"Base vehicle loses {pct_label(est.chassis_rate_milli_pct)}% a year, "
            f"conversion {pct_label(est.conversion_rate_milli_pct)}% a year"
        )
    else:
        base = f"Estimated from the {d0_label} {method_label}"
        rates = f"Loses {pct_label(est.chassis_rate_milli_pct)}% a year"
    if est.mileage is not None:
        n = abs(est.mileage.excess_miles)
        side = "over" if est.mileage.excess_miles >= 0 else "under"
        rates += f", {n:,} miles {side} pace"
    else:
        rates += ", no new mileage"
    return f"{base}. {rates}."
