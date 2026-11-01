"""Utilities: payment pairing, per-day and year over year math, the API, Dashboard and Checks.

Made-up bills only. The Dec 2026 vs Dec 2025 case is a unit test, since the
samples stop in Sep 2026.
"""

from __future__ import annotations

from datetime import date, timedelta
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db.connect import write_connection
from app.services import utilities as u
from app.services.balances import clear_cache
from app.services.utility_bills import UtilityBill, UtilityCredit, UtilityLine

TODAY = date(2026, 10, 20)
M = 1_000_000  # micro-units per unit


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def bill(
    period_start: str,
    period_end: str,
    *,
    key: str = "city-power",
    bill_date: str | None = None,
    total: int = 10_000,
    days: int | None = None,
    lines: tuple[UtilityLine, ...] = (),
    credits: tuple[UtilityCredit, ...] = (),
    hdd: int | None = None,
    due: str | None = None,
    paid_from: str | None = "maple-joint-checking",
) -> UtilityBill:
    start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    return UtilityBill(
        bill_key=f"{key}:{period_end}",
        utility_key=key,
        provider="Example Utility",
        bill_date=bill_date or period_end,
        period_start=period_start,
        period_end=period_end,
        days=days if days is not None else (end - start).days,
        total_cents=total,
        due_date=due,
        paid_from=paid_from,
        heating_degree_days=hdd,
        estimated_read=None,
        note=None,
        lines=lines,
        credits=credits,
    )


def line(service: str, usage: int | str, unit: str, charges: int, fixed: int | None = None):
    return UtilityLine(service, int(Fraction(usage) * M), unit, charges, fixed)


def pay(day: str, cents: int, key: str = "city-power", n: int = 0) -> u.Payment:
    return u.Payment(f"t-{day}-{n}", day, cents, "maple-joint-checking", key)


def paired(bills, payments) -> dict[str, tuple[str, bool]]:
    return {
        k: (p.payment.txn_date, p.amount_differs)
        for k, p in u.pair_payments(bills, payments).items()
    }


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------


def test_exact_payment_pairs_with_its_bill() -> None:
    bills = [bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=14_001)]
    assert paired(bills, [pay("2026-02-18", 14_001)]) == {
        "city-power:2026-02-01": ("2026-02-18", False)
    }


def test_exact_amount_wins_over_a_nearer_payment_of_another_amount() -> None:
    bills = [bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=15_000)]
    pays = [pay("2026-02-06", 14_000), pay("2026-02-25", 15_000)]
    assert paired(bills, pays) == {"city-power:2026-02-01": ("2026-02-25", False)}


def test_nearest_exact_payment_wins() -> None:
    bills = [bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=15_000)]
    pays = [pay("2026-02-10", 15_000), pay("2026-03-01", 15_000)]
    assert paired(bills, pays) == {"city-power:2026-02-01": ("2026-02-10", False)}


def test_two_bills_same_amount_pair_in_date_order() -> None:
    bills = [
        bill("2025-12-04", "2026-01-03", bill_date="2026-01-06", total=10_000),
        bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=10_000),
    ]
    # Jan 30 is within the window of both bills (5 days early for Feb).
    pays = [pay("2026-01-30", 10_000), pay("2026-02-10", 10_000)]
    assert paired(bills, pays) == {
        "city-power:2026-01-03": ("2026-01-30", False),
        "city-power:2026-02-01": ("2026-02-10", False),
    }
    # One payment for two bills of the same amount: it pays the nearer bill.
    assert paired(bills, [pay("2026-02-10", 10_000)]) == {
        "city-power:2026-02-01": ("2026-02-10", False)
    }


def test_late_payment_still_pairs_inside_the_window() -> None:
    bills = [
        bill("2025-12-04", "2026-01-03", bill_date="2026-01-06", total=10_000),
        bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=10_000),
    ]
    # Jan's bill paid 37 days late, Feb's 16 days after its bill date.
    pays = [pay("2026-02-12", 10_000), pay("2026-02-20", 10_000)]
    assert paired(bills, pays) == {
        "city-power:2026-01-03": ("2026-02-12", False),
        "city-power:2026-02-01": ("2026-02-20", False),
    }


def test_window_edges() -> None:
    b = [bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=10_000)]
    assert paired(b, [pay("2026-03-16", 10_000)]) != {}  # 40 days after
    assert paired(b, [pay("2026-03-17", 10_000)]) == {}  # 41 days after
    assert paired(b, [pay("2026-01-30", 10_000)]) != {}  # 5 days before
    assert paired(b, [pay("2026-01-29", 10_000)]) == {}  # 6 days before


def test_partial_payments_pair_one_and_leave_the_other() -> None:
    bills = [bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=15_000)]
    pays = [pay("2026-02-10", 10_000), pay("2026-02-15", 5_000)]
    out = u.pair_payments(bills, pays)
    assert len(out) == 1
    p = out["city-power:2026-02-01"]
    assert (p.payment.txn_date, p.amount_differs, p.gap_days) == ("2026-02-10", True, 6)


def test_one_payment_pays_one_bill() -> None:
    bills = [
        bill("2025-12-04", "2026-01-03", bill_date="2026-01-06", total=10_000),
        bill("2026-01-03", "2026-02-01", bill_date="2026-02-04", total=12_000),
    ]
    out = u.pair_payments(bills, [pay("2026-02-06", 12_000)])
    assert list(out) == ["city-power:2026-02-01"]


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


class _Cov:
    def __init__(self, through: str | None) -> None:
        self.through = through

    def covered_through(self, account_key: str) -> str | None:
        return self.through


def _ctx(through: str | None) -> u.Ctx:
    return u.Ctx(TODAY, _Cov(through), {}, {})  # type: ignore[arg-type]


# Tagged payments seen from the Jan 2026 bill on, paid from joint checking.
HIST = u.PayHistory("2026-01-07", "maple-joint-checking")


def test_bill_status() -> None:
    b = bill("2026-08-04", "2026-09-03", bill_date="2026-09-06", due="2026-09-26")
    assert u.bill_status(b, None, _ctx("2026-09-30"), HIST) == "past_due"
    assert u.bill_status(b, None, _ctx("2026-09-20"), HIST) == "not_paid"  # statements not in yet
    assert u.bill_status(b, None, _ctx(None), HIST) == "not_paid"
    # No paid_from and no recurring item: the newest tagged payment's account stands in.
    no_account = bill("2026-08-04", "2026-09-03", due="2026-09-26", paid_from=None)
    assert u.bill_status(no_account, None, _ctx("2026-09-30"), HIST) == "past_due"
    # With no account known at all, a bill is never past due.
    unknown = u.PayHistory("2026-01-07", None)
    assert u.bill_status(no_account, None, _ctx("2026-09-30"), unknown) == "not_paid"
    future = bill("2026-09-03", "2026-10-05", due="2026-10-28")
    assert u.bill_status(future, None, _ctx("2026-10-31"), HIST) == "not_paid"
    old = bill("2025-10-05", "2025-11-03", due="2025-11-26")
    assert u.bill_status(old, None, _ctx("2026-09-30"), HIST) == "no_history"
    # No due date: the bill date + 40 days.
    undated = bill("2026-07-05", "2026-08-04", bill_date="2026-08-07")
    assert u.effective_due(undated) == "2026-09-16"
    p = u.Paid(pay("2026-09-18", 9_000), True, 12)
    assert u.bill_status(b, p, _ctx(None), HIST) == "amount_differs"


def test_bills_before_the_payment_history() -> None:
    covered = _ctx("2026-09-30")
    # Billed in Dec 2025 and due in Jan 2026: paid before the checking history starts.
    dec = bill("2025-11-05", "2025-12-04", bill_date="2025-12-07")
    assert u.effective_due(dec) == "2026-01-16"
    assert u.bill_status(dec, None, covered, HIST) == "no_history"
    # Older than the oldest paid bill: its payment was sent before tagging started.
    mar = bill("2026-02-04", "2026-03-05", bill_date="2026-03-08", due="2026-03-28")
    late_tags = u.PayHistory("2026-09-06", "maple-joint-checking")
    assert u.bill_status(mar, None, covered, late_tags) == "no_history"
    # No paid bill yet: nothing to check against.
    assert u.bill_status(mar, None, covered, u.PayHistory(None, None)) == "no_history"
    # Once an older bill is paid, a missing payment is past due.
    assert u.bill_status(mar, None, covered, HIST) == "past_due"


# --------------------------------------------------------------------------
# Math
# --------------------------------------------------------------------------


def test_change_pct_and_unit_cost() -> None:
    assert u.change_pct(110, 100) == "10.0"
    assert u.change_pct(Fraction(21), Fraction(20)) == "5.0"
    assert u.change_pct(1, 0) is None and u.change_pct(None, 5) is None
    assert u.change_pct(0, 6) == "-100.0"
    assert u.unit_cost_millicents(14_879, 719 * M, "kwh") == 20_694  # 20.694 cents per kWh
    assert u.unit_cost_millicents(1_000, 0, "kwh") is None
    assert u.unit_cost_millicents(1_000, 1 * M, "none") is None
    assert u.year_before(date(2028, 2, 29)) == date(2027, 2, 28)


def test_next_bill_date_uses_the_median_gap() -> None:
    bills = [
        bill("2026-01-03", "2026-02-01", bill_date="2026-02-04"),
        bill("2026-02-01", "2026-03-03", bill_date="2026-03-06"),  # 30 days
        bill("2026-03-03", "2026-04-05", bill_date="2026-04-08"),  # 33
        bill("2026-04-05", "2026-05-05", bill_date="2026-05-08"),  # 30
    ]
    assert u.next_bill_date(bills) == "2026-06-07"
    assert u.next_bill_date(bills[:1]) is None
    # Even count: the two middle gaps averaged, half rounds up. Gaps 30, 33 -> 31.5 -> 32.
    assert u.next_bill_date(bills[1:]) == "2026-06-09"


def test_next_bill_date_uses_the_last_12_bill_dates() -> None:
    """12 bill dates are 11 gaps. An older 13th date must not move the median."""
    gaps = [33] + [30, 33] * 5 + [30]  # oldest first: the last 11 are six 30s and five 33s
    day = date(2025, 9, 1)
    dates = [day]
    for g in gaps:
        day = day + timedelta(days=g)
        dates.append(day)
    bills = [bill(d.isoformat(), d.isoformat(), bill_date=d.isoformat()) for d in dates]
    assert u.next_bill_date(bills) == (dates[-1] + timedelta(days=30)).isoformat()


def test_next_bill_is_late_only_after_an_import_without_it() -> None:
    """Bills come with the monthly imports, so a passed date alone is not late."""
    assert not u.next_bill_late("2026-10-06", None)
    assert not u.next_bill_late(None, "2026-11-12")
    assert not u.next_bill_late("2026-10-06", "2026-10-12")  # 6 days after: may not be in yet
    assert u.next_bill_late("2026-10-06", "2026-10-13")  # 7 days after and still no bill


def test_dec_2026_vs_dec_2025_per_day() -> None:
    """the owner's test case: fewer days this December, so per-day numbers tell the story."""
    dec25 = bill(
        "2025-11-04",
        "2025-12-05",  # 31 days
        total=16_000,
        hdd=850,
        lines=(
            line("electric", 620, "kwh", 9_000, 2_000),
            line("gas", 124, "therms", 7_000, 1_250),
        ),
    )
    nov25 = bill("2025-10-05", "2025-11-04", total=12_000)
    dec26 = bill(
        "2026-11-05",
        "2026-12-04",  # 29 days
        total=16_530,
        hdd=790,
        lines=(
            line("electric", 609, "kwh", 9_200, 2_000),
            line("gas", 116, "therms", 7_330, 1_250),
        ),
    )
    bills = [nov25, dec25, dec26]
    last = u.last_year_bill(dec26, bills)
    assert last is dec25  # 1 day from Dec 4, 2025; Nov 4 is 30 days off
    row = u.yoy_row(dec26, last)
    assert row["days"] == {"value": 29, "last_year": 31, "change_pct": "-6.5"}
    elec, gas = row["services"]
    # Total kWh is down, but per day it is up 5%: 609 / 29 = 21.0, 620 / 31 = 20.0.
    assert elec["usage_micro"]["change_pct"] == "-1.8"
    assert elec["per_day_micro"] == {"value": 21 * M, "last_year": 20 * M, "change_pct": "5.0"}
    assert elec["per_degree_day_micro"] is None
    # Therms: 116 / 29 = 4.0 and 124 / 31 = 4.0 per day, flat.
    assert gas["usage_micro"]["change_pct"] == "-6.5"
    assert gas["per_day_micro"] == {"value": 4 * M, "last_year": 4 * M, "change_pct": "0.0"}
    # Per degree day: 116 / 790 vs 124 / 850, up 0.7%.
    assert gas["per_degree_day_micro"] == {
        "value": 146_835,
        "last_year": 145_882,
        "change_pct": "0.7",
    }
    assert row["heating_degree_days"]["change_pct"] == "-7.1"
    assert row["total_cents"] == {"value": 16_530, "last_year": 16_000, "change_pct": "3.3"}
    # Total per day: 570 vs 516.13 cents a day.
    assert row["total_per_day_cents"] == {"value": 570, "last_year": 516, "change_pct": "10.4"}
    assert row["last_year_bill_key"] == "city-power:2025-12-05"


def test_no_last_year_bill_outside_20_days() -> None:
    a = bill("2025-08-01", "2025-09-01")
    b = bill("2026-09-01", "2026-09-22")  # 21 days after Sep 1, 2025
    assert u.last_year_bill(b, [a, b]) is None
    row = u.yoy_row(b, None)
    assert row["total_cents"] == {"value": 10_000, "last_year": None, "change_pct": None}


def test_change_from_the_bill_before() -> None:
    aug = bill("2026-07-05", "2026-08-04", lines=(line("electric", 500, "kwh", 7_000),))
    sep = bill("2026-08-04", "2026-09-04", lines=(line("electric", 600, "kwh", 8_000),))
    assert u.previous_bill(sep, [aug, sep]) is aug
    row = u.yoy_row(sep, None, aug)
    assert row["prev_bill_key"] == "city-power:2026-08-04"
    assert (row["prev_period_start"], row["prev_period_end"]) == ("2026-07-05", "2026-08-04")
    (elec,) = row["services"]
    assert elec["usage_vs_prev"] == {"previous": 500 * M, "change_pct": "20.0"}
    # Per day: 600 / 31 vs 500 / 30, up 16.1%.
    assert elec["per_day_vs_prev"] == {"previous": 16_666_667, "change_pct": "16.1"}

    # The first bill has no bill before it.
    assert u.previous_bill(aug, [aug, sep]) is None
    first = u.yoy_row(aug, None, None)
    assert first["prev_bill_key"] is None and first["prev_period_end"] is None
    assert first["services"][0]["usage_vs_prev"] == {"previous": None, "change_pct": None}

    # A bill before that ends 21 days before this one starts is a missing bill: no change.
    gap = bill("2026-06-13", "2026-07-14", lines=(line("electric", 500, "kwh", 7_000),))
    late = bill("2026-08-04", "2026-09-04", lines=(line("electric", 600, "kwh", 8_000),))
    assert u.previous_bill(late, [gap, late]) is None
    row = u.yoy_row(late, None, u.previous_bill(late, [gap, late]))
    assert row["prev_bill_key"] is None
    assert row["services"][0]["usage_vs_prev"]["change_pct"] is None
    assert row["services"][0]["per_day_vs_prev"]["change_pct"] is None

    # Another unit (gallons to ccf) is not comparable.
    q1 = bill(
        "2025-12-01",
        "2026-03-01",
        key="town-water",
        lines=(line("water", 9_000, "gallons", 3_000),),
    )
    q2 = bill(
        "2026-03-01", "2026-06-01", key="town-water", lines=(line("water", 12, "ccf", 3_000),)
    )
    w = u.yoy_row(q2, None, u.previous_bill(q2, [q1, q2]))
    assert w["prev_bill_key"] == "town-water:2026-03-01"
    assert w["services"][0]["usage_vs_prev"] == {"previous": None, "change_pct": None}
    assert w["services"][0]["per_day_vs_prev"]["previous"] is None


def test_quarterly_water_year_over_year() -> None:
    def water(start: str, end: str, gallons: int, unit: str = "gallons") -> UtilityBill:
        usage = gallons if unit == "gallons" else Fraction(gallons, 748)
        return bill(
            start,
            end,
            key="town-water",
            total=6_500,
            lines=(
                line("sewer", usage, unit, 3_000, 1_000),
                line("stormwater", 0, "none", 500),
                line("water", usage, unit, 3_000, 1_000),
            ),
        )

    bills = [
        water("2024-12-01", "2025-03-01", 9_000),  # 90 days
        water("2025-03-01", "2025-06-01", 10_000),
        water("2025-12-01", "2026-03-01", 9_200),  # 90 days
        water("2026-03-01", "2026-06-01", 11_960, unit="ccf"),  # meter now reads ccf
    ]
    q1 = u.yoy_row(bills[2], u.last_year_bill(bills[2], bills))
    assert q1["last_year_bill_key"] == "town-water:2025-03-01"
    w = next(s for s in q1["services"] if s["service"] == "water")
    assert w["per_day_micro"]["change_pct"] == "2.2"  # 9200 vs 9000 over 90 days
    assert w["per_degree_day_micro"] is None
    storm = next(s for s in q1["services"] if s["service"] == "stormwater")
    assert storm["usage_micro"] == {"value": 0, "last_year": 0, "change_pct": None}
    # A unit change makes usage not comparable; charges still are.
    q2 = u.yoy_row(bills[3], u.last_year_bill(bills[3], bills))
    w2 = next(s for s in q2["services"] if s["service"] == "water")
    assert w2["usage_micro"]["last_year"] is None and w2["per_day_micro"]["change_pct"] is None
    assert w2["charges_cents"]["change_pct"] == "0.0"
    cost = u.cost_point(bills[2])["services"]
    storm_cost = next(s for s in cost if s["service"] == "stormwater")
    assert storm_cost["cost_per_unit_millicents"] is None
    water_cost = next(s for s in cost if s["service"] == "water")
    # 30.00 / 9200 gallons = 0.326 cents a gallon; 20.00 without the fixed part.
    assert water_cost["cost_per_unit_millicents"] == 326
    assert water_cost["cost_per_unit_ex_fixed_millicents"] == 217
    # Trailing 12 months from Jun 1, 2026: Mar 2026 and Jun 2026 only (no bills in between).
    assert [b.period_end for b in u.trailing_year(bills)] == ["2026-03-01", "2026-06-01"]


def test_credits_running_total() -> None:
    bills = [
        bill("2026-06-04", "2026-07-05", credits=(UtilityCredit("Smart thermostat credit", -219),)),
        bill(
            "2026-07-05",
            "2026-08-04",
            credits=(UtilityCredit("A", -500), UtilityCredit("Smart thermostat credit", -219)),
        ),
    ]
    assert [c["running_cents"] for c in u.credits_running(bills)] == [-219, -719, -938]


# --------------------------------------------------------------------------
# API, Dashboard and Checks on the samples
# --------------------------------------------------------------------------


def test_utilities_list(samples, member: TestClient) -> None:
    r = member.get("/api/utilities")
    assert r.status_code == 200
    rows = {x["utility_key"]: x for x in r.json()["utilities"]}
    assert list(rows) == ["city-power", "town-water"]
    cp = rows["city-power"]
    assert (cp["bill_count"], cp["first_period_end"], cp["last_period_end"]) == (
        25,
        "2024-09-02",
        "2026-09-03",
    )
    assert cp["services"] == [
        {"service": "electric", "unit": "kwh"},
        {"service": "gas", "unit": "therms"},
    ]
    assert cp["bills_in_avg"] == 12 and cp["last_status"] == "paid"
    assert cp["avg_bill_cents"] == round(cp["total_12m_cents"] / 12)
    assert cp["next_bill_date"] == "2026-10-06"
    # The newest monthly import (Oct 12) is 6 days after Oct 6: not late yet.
    assert (cp["next_bill_late"], cp["last_import_date"]) == (False, "2026-10-12")
    water = rows["town-water"]
    assert (water["bills_in_avg"], water["next_bill_date"]) == (4, "2026-12-07")
    assert {s["unit"] for s in water["services"]} == {"gallons", "none"}


def test_utility_detail(samples, admin: TestClient) -> None:
    d = admin.get("/api/utilities/city-power").json()
    bills = d["bills"]
    assert len(bills) == 25 and d["from"] is None
    assert [b["period_end"] for b in bills] == sorted(b["period_end"] for b in bills)
    for b in bills:
        assert b["charges_cents"] + b["credits_cents"] - b["total_cents"] in (-2, -1, 0, 1, 2)
        no_due = (date.fromisoformat(b["bill_date"]) + timedelta(days=40)).isoformat()
        assert b["due_by"] == (b["due_date"] or no_due)
        if b["bill_date"] >= "2026-01-01":
            assert b["status"] == "paid", b["bill_key"]
            assert b["payment"]["amount_cents"] == b["total_cents"]
            assert b["payment"]["account_name"] == "Joint checking (Maple)"
        else:
            assert (b["status"], b["payment"]) == ("no_history", None), b["bill_key"]
    assert len(d["yoy"]) == len(d["cost"]) == 25
    sep = d["yoy"][-1]
    assert (sep["bill_key"], sep["last_year_bill_key"]) == (
        "city-power:2026-09-03",
        "city-power:2025-09-03",
    )
    assert d["yoy"][0]["last_year_bill_key"] is None  # the first bill has no year before it
    # Bill before: the sample periods are contiguous, so each bill has the one before it.
    assert d["yoy"][0]["prev_bill_key"] is None
    for i in range(1, len(bills)):
        assert d["yoy"][i]["prev_bill_key"] == bills[i - 1]["bill_key"]
    sep_line = next(ln for ln in bills[-1]["lines"] if ln["service"] == "electric")
    aug_line = next(ln for ln in bills[-2]["lines"] if ln["service"] == "electric")
    sep_elec = next(s for s in sep["services"] if s["service"] == "electric")
    assert sep_elec["usage_vs_prev"] == {
        "previous": aug_line["usage_micro"],
        "change_pct": u.change_pct(sep_line["usage_micro"], aug_line["usage_micro"]),
    }
    assert d["credits"][-1]["running_cents"] == d["credits_total_cents"] < 0
    assert [w["rule"] for w in d["warnings"]] == ["utility-total-rounding"]

    ranged = admin.get("/api/utilities/city-power?from=2026-01&to=2026-09").json()
    assert ranged["from"] == "2026-01" and ranged["to"] == "2026-09"
    assert [b["period_end"][:7] for b in ranged["bills"]][0] == "2026-01"
    assert len(ranged["bills"]) == 9
    # A range still finds last year's bills outside it.
    assert all(r["last_year_bill_key"] for r in ranged["yoy"])
    # And the bill before the first one in range.
    assert ranged["yoy"][0]["prev_bill_key"] is not None
    assert ranged["yoy"][0]["prev_period_end"] == ranged["bills"][0]["period_start"]
    assert ranged["warnings"] == []

    water = admin.get("/api/utilities/town-water").json()
    assert [b["status"] for b in water["bills"]] == ["no_history", "paid", "paid", "paid"]
    assert all(r["last_year_bill_key"] is None for r in water["yoy"])
    assert water["yoy"][0]["prev_bill_key"] is None
    assert water["yoy"][1]["prev_bill_key"] == water["bills"][0]["bill_key"]


def test_utility_detail_errors(samples, admin: TestClient) -> None:
    assert admin.get("/api/utilities/no-such-utility").status_code == 404
    assert admin.get("/api/utilities/city-power?from=2026-13").status_code == 422
    assert admin.get("/api/utilities/Bad_Key").status_code == 422
    assert admin.get("/api/utilities/city-power?from=2026-09&to=2026-01").status_code == 422


def test_utilities_need_sign_in(samples, prod: TestClient) -> None:
    assert prod.get("/api/utilities").status_code == 401
    assert prod.get("/api/utilities/city-power").status_code == 401


def test_dashboard_utility_lines(samples, admin: TestClient) -> None:
    d = admin.get("/api/dashboard").json()
    lines = {x["utility_key"]: x for x in d["utilities"]}
    assert set(lines) == {"city-power", "town-water"}
    assert lines["city-power"]["next_bill_date"] == "2026-10-06"
    assert lines["city-power"]["avg_bill_cents"] > 0


def test_checks_utility_lists(samples, admin: TestClient) -> None:
    c = admin.get("/api/checks").json()
    assert c["utility_past_due"] == []
    assert c["utility_bill_count"] == 29  # 25 power bills and 4 water bills
    assert [w["bill_key"] for w in c["utility_warnings"]] == ["city-power:2025-07-05"]
    # Town water is paid monthly in the samples but billed quarterly: six payments have no bill.
    no_bill = c["utility_payments_no_bill"]
    assert {r["utility_key"] for r in no_bill} == {"town-water"}
    assert [r["txn_date"][5:7] for r in no_bill] == ["01", "02", "04", "05", "07", "08"]


def test_checks_past_due_when_a_payment_is_missing(samples, admin: TestClient) -> None:
    with write_connection(samples.db_path) as conn:
        conn.execute(
            "DELETE FROM transactions WHERE recurring_key = ? AND txn_date = ?",
            ("city-power", "2026-09-18"),
        )
    clear_cache()
    c = admin.get("/api/checks").json()
    assert [(r["bill_key"], r["due_date"]) for r in c["utility_past_due"]] == [
        ("city-power:2026-09-03", "2026-09-26")
    ]
    assert c["utility_past_due"][0]["account_name"] == "Joint checking (Maple)"
    d = admin.get("/api/utilities/city-power?from=2026-09").json()
    assert d["bills"][0]["status"] == "past_due"


def test_untagged_payments_before_tagging_are_not_past_due(samples, admin: TestClient) -> None:
    """Payments sent before format 1.2 may carry no utility tag. Those bills are not past due."""
    with write_connection(samples.db_path) as conn:
        conn.execute(
            "UPDATE transactions SET recurring_key = NULL WHERE recurring_key = ? AND txn_date < ?",
            ("city-power", "2026-09-01"),
        )
    clear_cache()
    assert admin.get("/api/checks").json()["utility_past_due"] == []
    d = admin.get("/api/utilities/city-power?from=2026-01").json()
    statuses = {b["period_end"][:7]: b["status"] for b in d["bills"]}
    assert statuses["2026-09"] == "paid"
    assert {statuses[m] for m in statuses if m < "2026-09"} == {"no_history"}
