"""Home, vehicle and vehicle loan estimates for the balance engine (PLAN 3.9).

Estimator interface (app/services/balances.py): `signature(conn)` and
`estimate(conn, h)`. Computed on request, never stored.

For each day, the account's real series (`h.real_daily`) gives the latest
real balance and its date d0 (statement, manual, valuation, or the monthly
run's own stored estimate). An estimate only covers days after d0, so a real
balance on the same day always wins, and `History(..., estimates=False)`
(the Checks page) never sees one. One exception: a vehicle with a camper
conversion also gets an estimate on d0, because the KBB value leaves the
conversion out (`Estimate.adds_part`).

- Home (type property): V0 * I(t) / I(d0), with the FHFA index curve
  (app/valuations/hpi.py) and t the latest month end on or before the day.
- Vehicle (type vehicle): app/valuations/vehicles.py.
- Vehicle loan (type loan, secured by a vehicle): app/valuations/loans.py.
  Daily, not monthly: payments land on their own dates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import date

from app.services.balances import Estimate, History
from app.valuations import hpi, loans, vehicles

METHOD_LABELS = {
    "kbb": "KBB value",
    "comps": "valuation",
    "report": "report value",
    "manual": "value",
}
SOURCE_LABELS = {
    "statement": "statement balance",
    "manual": "balance",
    "valuation": "valuation",
    "estimate": "budget chat's estimate",
}


def day_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.strftime('%b')} {d.day}"


@dataclass(frozen=True)
class Found:
    """One computed estimate with what went into it."""

    kind: str  # home, vehicle, loan
    cents: int  # signed like balances: owed is negative
    estimate: Estimate
    base_cents: int
    detail: dict  # inputs for GET /api/valuations (no typed text)


def estimate_kind(accounts: dict[str, dict], key: str) -> str | None:
    a = accounts[key]
    if a["tracked_by"] != "statements":
        return None
    if a["type"] == "property":
        return "home"
    if a["type"] == "vehicle":
        return "vehicle"
    secured = accounts.get(a["secured_by_account_key"] or "")
    if a["type"] == "loan" and secured is not None and secured["type"] == "vehicle":
        return "loan"
    return None


class Estimator:
    """Loads the inputs once, then answers per account and day."""

    def __init__(self, conn: sqlite3.Connection, accounts: dict[str, dict]) -> None:
        self.accounts = accounts
        self.kinds = {k: kind for k in accounts if (kind := estimate_kind(accounts, k))}
        self.curve = hpi.load_curve(conn) if "home" in self.kinds.values() else None
        self.settings = vehicles.load_settings(conn)
        self.readings = vehicles.load_readings(conn)
        loan_keys = [k for k, kind in self.kinds.items() if kind == "loan"]
        self.payments = loans.load_payments(conn, loan_keys)
        self.methods = {
            (r["account_key"], r["as_of"]): (r["source"], r["method"])
            for r in conn.execute(
                """SELECT account_key, as_of, source, method FROM balances
                   ORDER BY account_key, as_of,
                     CASE source WHEN 'statement' THEN 0 WHEN 'manual' THEN 1
                                 WHEN 'valuation' THEN 2 ELSE 3 END DESC"""
            )
        }

    def base_label(self, key: str, d0: str) -> str:
        source, method = self.methods.get((key, d0), ("statement", None))
        if source == "valuation" and method in METHOD_LABELS:
            return METHOD_LABELS[method]
        return SOURCE_LABELS.get(source, "balance")

    def base_phrase(self, key: str, d0: str) -> str:
        """ "the Sep 30 valuation", or for a stored estimate "the Sep 30 balance
        (itself an estimate from the budget chat)"."""
        source, _ = self.methods.get((key, d0), ("statement", None))
        if source == "estimate":
            return f"the {day_label(d0)} balance (itself an estimate from the budget chat)"
        return f"the {day_label(d0)} {self.base_label(key, d0)}"

    def settings_for(self, key: str) -> vehicles.VehicleSettings:
        return self.settings.get(key) or vehicles.VehicleSettings(key)

    def at(self, key: str, d0: str, base: int, day: str) -> Found | None:
        kind = self.kinds.get(key)
        if kind is None or day < d0 or (day == d0 and kind != "vehicle"):
            return None
        if kind == "home":
            return self._home(key, d0, base, day)
        if kind == "vehicle":
            return self._vehicle(key, d0, base, day)
        return self._loan(key, d0, base, day)

    def _home(self, key: str, d0: str, base: int, day: str) -> Found | None:
        c = self.curve
        if c is None:
            return None
        start = date.fromisoformat(d0)
        step = vehicles.month_step(date.fromisoformat(day), start)
        if step <= start:
            return None
        i0, i1 = c.at(start), c.at(step)
        cents = round(base * i1 / i0)
        if cents == base:
            return None
        note = (
            f"Estimated from {self.base_phrase(key, d0)}, "
            f"moved by the {c.short} through {c.through_label}."
        )
        if c.projected(step):
            note += " Months after that follow its past-year trend."
        est = Estimate("home", d0, c.last_start, note)
        detail = {
            "series_id": c.series_id,
            "index_through": c.last_start,
            "index_through_label": c.through_label,
            "index_base_micro": round(i0 * 1_000_000),
            "index_now_micro": round(i1 * 1_000_000),
            "step": step.isoformat(),
            "projected": c.projected(step),
        }
        return Found("home", cents, est, base, detail)

    def _vehicle(self, key: str, d0: str, base: int, day: str) -> Found | None:
        s = self.settings_for(key)
        source, method = self.methods.get((key, d0), ("statement", None))
        if s.has_conversion and not (source == "valuation" and method == "kbb"):
            # Only a plain KBB value leaves the conversion out. Any other value
            # (manual, comps) is the whole vehicle, so adding it would count twice.
            s = replace(s, conversion_value_cents=None, conversion_value_date=None)
        v = vehicles.estimate(
            base, date.fromisoformat(d0), date.fromisoformat(day), s, self.readings.get(key, [])
        )
        if v is None:
            return None
        note = vehicles.note(v, day_label(d0), self.base_label(key, d0))
        through = v.mileage.now_date if v.mileage else v.step
        est = Estimate(
            "vehicle",
            d0,
            max(through, v.step),
            note,
            adds_part=v.conversion_rate_milli_pct is not None,
        )
        detail = {
            "step": v.step,
            "chassis_cents": v.chassis_cents,
            "conversion_cents": v.conversion_cents,
            "chassis_rate_milli_pct": v.chassis_rate_milli_pct,
            "conversion_rate_milli_pct": v.conversion_rate_milli_pct,
            "mileage_factor_milli": round(v.mileage.factor * 1000) if v.mileage else None,
            "excess_miles": v.mileage.excess_miles if v.mileage else None,
            "mileage_date": v.mileage.now_date if v.mileage else None,
            "chassis_floored": v.chassis_floored,
        }
        return Found("vehicle", v.value_cents, est, base, detail)

    def _loan(self, key: str, d0: str, base: int, day: str) -> Found | None:
        apr = self.accounts[key]["apr_milli_pct"]
        if apr is None:
            return None
        owed0 = max(-base, 0)
        r = loans.amortize(
            owed0, date.fromisoformat(d0), date.fromisoformat(day), apr, self.payments.get(key, [])
        )
        if r.owed_cents == owed0:
            return None
        n = r.payments
        paid = f"{n} payment{'s' if n != 1 else ''} since" if n else "no payments since"
        note = (
            f"Estimated from {self.base_phrase(key, d0)}, "
            f"{vehicles.pct_label(apr)}% APR and {paid}. Payments count once the "
            "checking statement is in, so this can run high until then."
        )
        est = Estimate("loan", d0, r.last_payment, note)
        detail = {
            "apr_milli_pct": apr,
            "payments": n,
            "paid_cents": r.paid_cents,
            "interest_cents": r.interest_cents,
            "last_payment": r.last_payment,
        }
        return Found("loan", -r.owed_cents, est, base, detail)


def found_for(est: Estimator, h: History, key: str, index: int) -> Found | None:
    """The estimate for one account on day `index` of the range, from its real series."""
    real = h.real_daily(key)[index]
    if (
        real.balance_cents is None
        or real.as_of is None
        or real.source in ("closed", "tracked")
        or not real.opened
    ):
        return None
    return est.at(key, real.as_of, real.balance_cents, h.days[index])


# --------------------------------------------------------------------------
# Estimator interface (app/services/balances.py)
# --------------------------------------------------------------------------


def signature(conn: sqlite3.Connection) -> tuple:
    rows = [
        conn.execute("SELECT count(*), max(fetched_at) FROM index_values").fetchone(),
        conn.execute(
            "SELECT count(*), group_concat(account_key || ':' || enabled || ':' || "
            "coalesce(model_year, '') || ':' || coalesce(depreciation_milli_pct, '') || ':' || "
            "coalesce(expected_miles_year, '') || ':' || coalesce(mileage_milli_pct, '') || ':' || "
            "coalesce(conversion_value_cents, '') || ':' || coalesce(conversion_value_date, '') "
            "|| ':' || coalesce(conversion_milli_pct, ''), ',') FROM "
            "(SELECT * FROM vehicle_settings ORDER BY account_key)"
        ).fetchone(),
        conn.execute(
            "SELECT count(*), max(entered_at), coalesce(sum(id), 0), "
            "coalesce(sum(miles), 0) FROM vehicle_readings"
        ).fetchone(),
    ]
    return tuple(tuple(r) for r in rows)


def estimate(conn: sqlite3.Connection, h: History) -> dict[str, dict[str, tuple[int, Estimate]]]:
    est = Estimator(conn, h.accounts)
    out: dict[str, dict[str, tuple[int, Estimate]]] = {}
    for key in est.kinds:
        days: dict[str, tuple[int, Estimate]] = {}
        for i, day in enumerate(h.days):
            f = found_for(est, h, key, i)
            if f is not None:
                days[day] = (f.cents, f.estimate)
        if days:
            out[key] = days
    return out
