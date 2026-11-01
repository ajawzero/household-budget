"""Read the imported utility bills (format 1.2, migration 0007).

The tables hold the current version of each bill: the newest run in run
order wins per utility_key and period_end, and tombstones delete. These
readers only read and shape rows. Payment pairing, year over year math and
the API live with the Utilities view.

- `list_bills(conn, utility_key=None)`: every bill with its lines and
  credits, sorted by utility_key, then period_end. Bills dated before 2026
  are included (history goes back to 2021).
- `list_utilities(conn)`: one summary per utility_key, for the picker.
- `bill_warnings(bills)`: the import warnings as they stand now (a 1 or 2
  cent total difference, days that do not match the period, a period that
  overlaps the bill before it). Computed from the current rows, so a later
  run that fixes a bill clears its warning.

Amounts are integer cents. Usage is integer micro-units of the line's unit.
No account number, meter number, service address or customer name is stored.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.importer.models import utility_days_ok, utility_total_rule


@dataclass(frozen=True)
class UtilityLine:
    service: str  # electric, gas, water, sewer, stormwater, other
    usage_micro: int
    unit: str  # kwh, therms, gallons, ccf, none
    charges_cents: int
    fixed_charges_cents: int | None


@dataclass(frozen=True)
class UtilityCredit:
    label: str
    amount_cents: int  # always negative


@dataclass(frozen=True)
class UtilityBill:
    bill_key: str  # '<utility_key>:<period_end>'
    utility_key: str
    provider: str
    bill_date: str
    period_start: str
    period_end: str
    days: int
    total_cents: int
    due_date: str | None
    paid_from: str | None  # account key
    heating_degree_days: int | None
    estimated_read: bool | None
    note: str | None
    lines: tuple[UtilityLine, ...]
    credits: tuple[UtilityCredit, ...]

    def line(self, service: str) -> UtilityLine | None:
        return next((ln for ln in self.lines if ln.service == service), None)

    @property
    def charges_cents(self) -> int:
        return sum(ln.charges_cents for ln in self.lines)

    @property
    def credits_cents(self) -> int:
        return sum(c.amount_cents for c in self.credits)


@dataclass(frozen=True)
class UtilityInfo:
    utility_key: str
    provider: str  # from the newest bill
    bill_count: int
    first_period_end: str
    last_period_end: str
    last_bill_date: str
    services: tuple[str, ...]  # every service seen, sorted


@dataclass(frozen=True, order=True)
class BillWarning:
    bill_key: str
    rule: str  # utility-total-rounding, utility-days, utility-overlap


def list_bills(conn: sqlite3.Connection, utility_key: str | None = None) -> list[UtilityBill]:
    where, params = ("WHERE utility_key = ?", (utility_key,)) if utility_key else ("", ())
    bills = conn.execute(
        f"""SELECT bill_key, utility_key, provider, bill_date, period_start, period_end, days,
                   total_cents, due_date, paid_from_account_key, heating_degree_days,
                   estimated_read, note
            FROM utility_bills {where} ORDER BY utility_key, period_end""",
        params,
    ).fetchall()
    keys = [b["bill_key"] for b in bills]
    lines: dict[str, list[UtilityLine]] = {k: [] for k in keys}
    credits: dict[str, list[UtilityCredit]] = {k: [] for k in keys}
    for r in conn.execute(
        """SELECT bill_key, service, usage_micro, unit, charges_cents, fixed_charges_cents
           FROM utility_bill_lines ORDER BY bill_key, service"""
    ):
        if r["bill_key"] in lines:
            lines[r["bill_key"]].append(
                UtilityLine(
                    r["service"],
                    r["usage_micro"],
                    r["unit"],
                    r["charges_cents"],
                    r["fixed_charges_cents"],
                )
            )
    for r in conn.execute(
        "SELECT bill_key, label, amount_cents FROM utility_bill_credits ORDER BY bill_key, seq"
    ):
        if r["bill_key"] in credits:
            credits[r["bill_key"]].append(UtilityCredit(r["label"], r["amount_cents"]))
    return [
        UtilityBill(
            bill_key=b["bill_key"],
            utility_key=b["utility_key"],
            provider=b["provider"],
            bill_date=b["bill_date"],
            period_start=b["period_start"],
            period_end=b["period_end"],
            days=b["days"],
            total_cents=b["total_cents"],
            due_date=b["due_date"],
            paid_from=b["paid_from_account_key"],
            heating_degree_days=b["heating_degree_days"],
            estimated_read=None if b["estimated_read"] is None else bool(b["estimated_read"]),
            note=b["note"],
            lines=tuple(lines[b["bill_key"]]),
            credits=tuple(credits[b["bill_key"]]),
        )
        for b in bills
    ]


def list_utilities(conn: sqlite3.Connection) -> list[UtilityInfo]:
    """One row per utility_key, sorted by key."""
    by_key: dict[str, list[UtilityBill]] = {}
    for b in list_bills(conn):
        by_key.setdefault(b.utility_key, []).append(b)
    return [
        UtilityInfo(
            utility_key=key,
            provider=bills[-1].provider,
            bill_count=len(bills),
            first_period_end=bills[0].period_end,
            last_period_end=bills[-1].period_end,
            last_bill_date=max(b.bill_date for b in bills),
            services=tuple(sorted({ln.service for b in bills for ln in b.lines})),
        )
        for key, bills in sorted(by_key.items())
    ]


def bill_warnings(bills: list[UtilityBill]) -> list[BillWarning]:
    """The import warnings for these bills, sorted. Rejects never reach the tables."""
    out = []
    last_end: dict[str, str] = {}
    for b in sorted(bills, key=lambda b: (b.utility_key, b.period_end)):
        if utility_total_rule(b.charges_cents, b.credits_cents, b.total_cents) is not None:
            out.append(BillWarning(b.bill_key, "utility-total-rounding"))
        if not utility_days_ok(b.period_start, b.period_end, b.days):
            out.append(BillWarning(b.bill_key, "utility-days"))
        before = last_end.get(b.utility_key)
        if before is not None and b.period_start < before:
            out.append(BillWarning(b.bill_key, "utility-overlap"))
        last_end[b.utility_key] = b.period_end
    return sorted(out)
