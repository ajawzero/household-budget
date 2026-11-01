"""Phase 4 Recurring, Plans, Debt, Flags & To do, and Dashboard additions (PLAN 3.9, 8.2).

Golden values come from the sample household's 1.1 monthly run. Today is
fixed at 2026-10-20.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import config
from app.ai.facts import ensure_facts
from app.db.connect import connect
from app.importer.models import Flag, Recurring
from app.services import plans as plans_service
from app.services import recurring as rec
from app.services.balances import clear_cache
from app.services.coverage import coverage_state
from app.services.plans import carries_rate, debt_page, plan_out, plans_page, step_downs
from app.state import readers

TODAY = date(2026, 10, 20)
D = date.fromisoformat


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def item(cadence: str = "monthly", amount: str = "10.00", **kw) -> Recurring:
    return Recurring.model_validate(
        {
            "key": kw.pop("key", "test-item"),
            "name": "Test item",
            "category_key": "subscriptions",
            "amount": amount,
            "cadence": cadence,
            "status": kw.pop("status", "active"),
            **kw,
        }
    )


# --------------------------------------------------------------------------
# Monthly equivalents and due dates (unit)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cadence", "kw", "per_year", "monthly"),
    [
        ("weekly", {}, "52", 4333),
        ("biweekly", {}, "26", 2167),
        ("every_n_weeks", {"interval_n": 3}, "17.33", 1444),
        ("semi_monthly", {}, "24", 2000),
        ("monthly", {}, "12", 1000),
        ("monthly", {"active_months": [4, 5, 6, 7, 8, 9, 10]}, "7", 583),
        ("monthly", {"active_months": list(range(1, 11))}, "10", 833),
        ("quarterly", {}, "4", 333),
        ("semester", {"active_months": [3, 8]}, "2", 167),
        ("semi_annual", {}, "2", 167),
        ("annual", {}, "1", 83),
        ("weekly", {"active_months": [6, 7, 8]}, "13", 1083),
    ],
)
def test_monthly_equivalent(cadence, kw, per_year, monthly) -> None:
    it = item(cadence, **kw)
    assert rec.per_year_text(rec.occurrences_per_year(it)) == per_year
    assert rec.monthly_equivalent(it)[0] == monthly


@pytest.mark.parametrize(
    ("cadence", "kw", "per_year"),
    [
        ("quarterly", {"active_months": [4, 5, 6, 7, 8, 9, 10], "next_date": "2026-04-15"}, 3),
        ("semi_annual", {"active_months": [3, 8], "next_date": "2026-03-03"}, 1),
        ("annual", {"active_months": [6, 7, 8], "next_date": "2026-07-01"}, 1),
        ("annual", {"active_months": [6, 7, 8], "next_date": "2026-01-01"}, 0),
        ("semester", {"active_months": [3, 8], "next_date": "2026-03-03"}, 2),
        ("monthly", {"active_months": [4, 5, 6], "next_date": "2026-04-02"}, 3),
    ],
)
def test_monthly_equivalent_matches_due_dates(cadence, kw, per_year) -> None:
    """With active_months, the count a year equals the dates the calendar shows."""
    it = item(cadence, **kw)
    assert rec.occurrences_per_year(it) == per_year
    assert len(rec.due_dates(it, D("2026-01-01"), D("2026-12-31"))) == per_year


def test_net_monthly_equivalent_uses_the_credit() -> None:
    fee = item("annual", "250.00", credit_offset="100.00")
    assert rec.monthly_equivalent(fee) == (2083, 1250)


def _dates(it: Recurring, start: str, end: str, last: str | None = None) -> list[str]:
    return [d.isoformat() for d in rec.due_dates(it, D(start), D(end), last)]


def test_due_dates_by_cadence() -> None:
    assert _dates(item("monthly", next_date="2026-10-31"), "2026-01-01", "2026-04-30") == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30",
    ]  # fmt: skip
    assert _dates(item("weekly", next_date="2026-10-16"), "2026-09-01", "2026-09-30") == [
        "2026-09-04", "2026-09-11", "2026-09-18", "2026-09-25",
    ]  # fmt: skip
    every3 = item("every_n_weeks", interval_n=3, next_date="2026-10-06")
    assert _dates(every3, "2026-09-01", "2026-10-31") == ["2026-09-15", "2026-10-06", "2026-10-27"]
    assert _dates(item("semi_monthly", next_date="2026-10-20"), "2026-02-01", "2026-02-28") == [
        "2026-02-05", "2026-02-20",
    ]  # fmt: skip
    quarterly = item("quarterly", next_date="2026-11-05")
    assert _dates(quarterly, "2026-01-01", "2026-12-31") == [
        "2026-02-05", "2026-05-05", "2026-08-05", "2026-11-05",
    ]  # fmt: skip
    annual = item("annual", next_date="2027-03-01")
    assert _dates(annual, "2026-01-01", "2027-12-31") == ["2026-03-01", "2027-03-01"]
    semester = item("semester", active_months=[3, 8], next_date="2027-03-03")
    assert _dates(semester, "2026-01-01", "2026-12-31") == ["2026-03-03", "2026-08-03"]


def test_due_dates_bounds_and_status() -> None:
    seasonal = item("monthly", active_months=[4, 10], next_date="2026-10-15")
    assert _dates(seasonal, "2026-01-01", "2026-12-31") == ["2026-04-15", "2026-10-15"]
    ending = item("monthly", status="ending", next_date="2026-11-02", active_through="2026-12-31")
    assert _dates(ending, "2026-11-01", "2027-02-28") == ["2026-11-02", "2026-12-02"]
    bounded = item("monthly", next_date="2026-10-01", start_date="2026-08-15",
                   end_date="2026-11-30")  # fmt: skip
    assert _dates(bounded, "2026-07-01", "2026-12-31") == ["2026-09-01", "2026-10-01",
                                                          "2026-11-01"]  # fmt: skip
    assert _dates(item("monthly", status="paused", next_date="2026-10-01"), "2026-01-01",
                  "2026-12-31") == []  # fmt: skip
    assert _dates(item("monthly", status="ended", next_date="2026-10-01"), "2026-01-01",
                  "2026-12-31") == []  # fmt: skip
    # No date from the chat: the newest paid row anchors it.
    assert _dates(item("monthly"), "2026-09-01", "2026-09-30", last="2026-08-12") == ["2026-09-12"]
    assert _dates(item("monthly"), "2026-09-01", "2026-09-30") == []


# --------------------------------------------------------------------------
# Paid matching against the samples
# --------------------------------------------------------------------------


def test_recurring_month_view(samples, admin: TestClient) -> None:
    r = admin.get("/api/recurring", params={"month": "2026-09"}).json()
    assert r["view"] == "monthly" and r["source_run_id"] == "2026-10-12"
    paid = {d["key"]: d for d in r["complete"]}
    assert set(paid) == {
        "city-power",
        "cloudtunes",
        "fibernet-internet",
        "gym",
        "mortgage",
        "personal-subscription",
        "savings-transfer",
        "town-water",
        "wave-mobile",
    }
    assert (paid["city-power"]["expected_cents"], paid["city-power"]["paid_cents"]) == (
        15000,
        17425,
    )  # variable bill
    assert paid["cloudtunes"]["credit_offset_cents"] == 1099
    open_ = [(d["key"], d["due_date"], d["state"]) for d in r["upcoming"]]
    # Neither item has a tagged payment, so nothing can be checked: no payment
    # history yet, not missed (E-0).
    assert open_ == [
        ("kids-allowance", "2026-09-04", "no_history"),
        ("kids-allowance", "2026-09-11", "no_history"),
        ("lawn-care", "2026-09-15", "no_history"),
        ("kids-allowance", "2026-09-18", "no_history"),
        ("kids-allowance", "2026-09-25", "no_history"),
    ]
    assert r["summary"] == {
        "expected_cents": 225097,
        "paid_cents": 215693,
        "remaining_cents": 12500,
        "count": 14,
        "paid_count": 9,
    }
    aug = admin.get("/api/recurring", params={"month": "2026-08"}).json()
    # Semester bill in its active month. The Aug 4 row carries its recurring_key,
    # so it pays the Aug 3 due date.
    soccer = next(d for d in aug["complete"] if d["key"] == "youth-soccer")
    assert (soccer["due_date"], soccer["paid_date"], soccer["paid_cents"]) == (
        "2026-08-03",
        "2026-08-04",
        12000,
    )


def test_paid_window_and_waiting(samples, db) -> None:
    cov = coverage_state(db)
    gym = next(i for i in readers.recurring(readers.monthly_runs(db)) if i.key == "gym")
    # The gym is charged on the 2nd. Due on the 10th is 8 days off: outside the
    # 7-day monthly window, so it is not paid, and checking covers the window.
    # It has payment history, so it is missed.
    late = gym.model_copy(update={"next_date": "2026-11-10"})
    (due,) = rec.match(db, [late], D("2026-09-01"), D("2026-09-30"))
    assert due.txn is None and rec.due_state(due, TODAY, cov) == "missed"
    near = gym.model_copy(update={"next_date": "2026-11-09"})
    (due,) = rec.match(db, [near], D("2026-09-01"), D("2026-09-30"))
    assert due.txn is not None and due.txn["txn_date"] == "2026-09-02"
    # A card bill due Sep 20: the card's statements end Sep 10, so it waits.
    card = item("monthly", key="card-bill", next_date="2026-10-20", account_key="summit-card")
    (due,) = rec.match(db, [card], D("2026-09-01"), D("2026-09-30"))
    assert rec.due_state(due, TODAY, cov) == "waiting"
    (due,) = rec.match(db, [card], D("2026-10-01"), D("2026-10-31"))
    assert rec.due_state(due, TODAY, cov) == "upcoming"


def test_no_history_for_a_yearly_bill_with_no_tagged_payment(samples, db) -> None:
    # The E-0 case: a yearly bill anchored on its renewal date has a past due
    # date the card's statements cover, but nothing was ever tagged with it.
    fee = item("annual", key="yearly-fee", renewal_date="2027-02-15", account_key="summit-card")
    (due,) = rec.match(db, [fee], D("2026-02-01"), D("2026-02-28"))
    assert due.txn is None and due.date == D("2026-02-15") and due.first_paid is None
    assert rec.due_state(due, TODAY, coverage_state(db)) == "no_history"


def test_no_history_before_the_first_tagged_payment(samples, db) -> None:
    cov = coverage_state(db)
    ps = next(
        i for i in readers.recurring(readers.monthly_runs(db)) if i.key == "personal-subscription"
    )
    assert rec.first_paid(db)["personal-subscription"] == "2026-06-03"
    (may,) = rec.match(db, [ps], D("2026-05-01"), D("2026-05-31"))
    assert may.txn is None and rec.due_state(may, TODAY, cov) == "no_history"
    (jun,) = rec.match(db, [ps], D("2026-06-01"), D("2026-06-30"))
    assert rec.due_state(jun, TODAY, cov) == "paid"


def test_no_history_when_the_window_opens_before_history(samples, db) -> None:
    # Monthly window: 7 days. Due Jan 7 opens Dec 31, 2025, before the history.
    cov = coverage_state(db)
    it = item("monthly", key="early", account_key="maple-joint-checking")
    assert rec.due_state(
        rec.Due(it, D("2026-01-07"), 1000, first_paid="2025-12-20"), TODAY, cov
    ) == ("no_history")
    assert rec.due_state(
        rec.Due(it, D("2026-01-08"), 1000, first_paid="2025-12-20"), TODAY, cov
    ) == ("missed")


def test_one_row_pays_one_due_date(samples, db) -> None:
    """Two weekly due dates near one row: only the closest is paid."""
    weekly = item("weekly", key="gym", next_date="2026-10-01")  # Thursdays; gym paid Sep 2
    dues = rec.match(db, [weekly], D("2026-08-25"), D("2026-09-12"))
    paid = [(d.date.isoformat(), d.txn["txn_date"]) for d in dues if d.txn is not None]
    assert paid == [("2026-09-03", "2026-09-02")]


def test_recurring_all_view(samples, admin: TestClient) -> None:
    r = admin.get("/api/recurring", params={"view": "all"}).json()
    items = {i["key"]: i for i in r["items"]}
    assert len(items) == 17
    assert (
        items["lawn-care"]["monthly_equivalent_cents"],
        items["lawn-care"]["active_months"],
    ) == (
        4958,
        [4, 5, 6, 7, 8, 9, 10],
    )
    assert items["kids-allowance"]["monthly_equivalent_cents"] == 4333
    assert items["summit-annual-fee"]["net_monthly_equivalent_cents"] == 1250
    assert items["cloudtunes"]["net_monthly_equivalent_cents"] == 0
    assert items["house-cleaning"]["counted"] is False  # paused
    assert items["house-cleaning"]["occurrences_per_year"] == "17.33"
    assert items["house-cleaning"]["next_due"] is None
    assert r["items"][-1]["key"] == "house-cleaning"  # inactive items last
    assert items["city-power"]["next_due"] == "2026-11-18"  # Oct 18 is past
    assert items["town-water"]["next_due"] == "2026-10-22"
    assert items["wave-mobile"]["preferred_account_name"] == "Summit card"
    assert items["mortgage"]["last_paid_date"] == "2026-09-01"
    # Notes, rail and renewal (gap audit A1).
    assert items["wave-mobile"]["notes"] == "Paying with the Summit card adds phone protection"
    assert items["house-cleaning"]["payment_rail"] == "check"
    assert items["summit-annual-fee"]["renews_on"] == "2027-03-01"
    assert items["mortgage"]["renews_on"] is None
    # Last paid, from any tagged row (A2).
    fee, soccer = items["summit-annual-fee"], items["youth-soccer"]
    assert (fee["last_paid_date"], fee["last_paid_cents"]) == ("2026-03-02", 25000)
    assert (soccer["last_paid_date"], soccer["last_paid_cents"]) == ("2026-08-04", 12000)
    assert items["city-power"]["last_paid_cents"] == 17425  # variable bill
    assert items["kids-allowance"]["last_paid_date"] is None
    # End dates (A3).
    assert items["mortgage"]["ends_on"] == "2049-06-01"
    assert items["gym"]["ends_on"] == "2026-12-31"  # ending: active_through
    assert items["house-cleaning"]["ends_on"] is None  # paused
    assert items["fibernet-internet"]["ends_on"] is None
    # Cost cutting flags tied to the bill (B3).
    assert items["fibernet-internet"]["cost_cut"] == {
        "key": "fibernet-rate",
        "title": "Ask Fibernet for a lower rate",
        "status": "open",
        "due_date": None,
    }
    assert items["gym"]["cost_cut"]["key"] == "gym-ending"
    assert items["cloudtunes"]["cost_cut"] is None  # its flag is kind subscription
    # Totals are counted bills only: savings and transfers are left out (gap audit C3).
    counted = [i for i in r["items"] if i["counted"] and i["section_key"] != "savings"]
    assert r["totals"] == {
        "monthly_equivalent_cents": sum(i["monthly_equivalent_cents"] for i in counted),
        "net_monthly_equivalent_cents": sum(i["net_monthly_equivalent_cents"] for i in counted),
        "count": 13,
    }
    assert r["totals"]["monthly_equivalent_cents"] == 211638  # with music lessons, 6000
    assert r["summary"] is None and r["upcoming"] == []


def test_recurring_dates_use_the_household_date(
    samples, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "household_today", lambda: date(2027, 3, 2))
    clear_cache()
    items = {
        i["key"]: i for i in admin.get("/api/recurring", params={"view": "all"}).json()["items"]
    }
    fee = items["summit-annual-fee"]
    assert (fee["renewal_date"], fee["renews_on"]) == ("2027-03-01", None)
    assert items["gym"]["ends_on"] is None  # past its last date
    monkeypatch.setattr(config, "household_today", lambda: date(2027, 1, 1))
    clear_cache()
    items = {
        i["key"]: i for i in admin.get("/api/recurring", params={"view": "all"}).json()["items"]
    }
    assert items["gym"]["ends_on"] is None
    assert items["mortgage"]["ends_on"] == "2049-06-01"


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"end_date": "2027-06-01"}, "2027-06-01"),
        ({"end_date": "2026-09-30"}, None),  # passed
        ({"end_date": "2026-10-20"}, "2026-10-20"),  # today counts
        ({"status": "ending", "active_through": "2026-12-31"}, "2026-12-31"),
        (
            {"status": "ending", "active_through": "2026-12-31", "end_date": "2026-11-30"},
            "2026-11-30",
        ),
        (
            {"status": "ending", "active_through": "2026-11-15", "end_date": "2027-06-01"},
            "2026-11-15",
        ),
        ({"status": "paused", "end_date": "2027-06-01"}, None),
        ({"status": "ended", "end_date": "2027-06-01"}, None),
        ({"active_through": "2026-12-31"}, None),  # active: active_through does not end it
        ({}, None),
    ],
)
def test_ends_on(kw, expected) -> None:
    assert rec.ends_on(item(**kw), TODAY) == expected


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"renewal_date": "2027-03-01"}, "2027-03-01"),
        ({"renewal_date": "2026-10-20"}, "2026-10-20"),  # today counts
        ({"renewal_date": "2026-09-30"}, None),  # passed
        ({"status": "paused", "renewal_date": "2027-03-01"}, None),
        ({"status": "ended", "renewal_date": "2027-03-01"}, None),
        # An ending item that stops before it would renew does not renew.
        ({"status": "ending", "active_through": "2026-12-31", "renewal_date": "2027-03-01"}, None),
        (
            {"status": "ending", "active_through": "2027-06-30", "renewal_date": "2027-03-01"},
            "2027-03-01",
        ),
        ({"end_date": "2027-01-31", "renewal_date": "2027-03-01"}, None),
        ({}, None),
    ],
)
def test_renews_on(kw, expected) -> None:
    assert rec.renews_on(item(**kw), TODAY) == expected


def flag(key: str, **kw) -> Flag:
    return Flag.model_validate(
        {
            "key": key,
            "severity": "low",
            "status": kw.pop("status", "open"),
            "kind": kw.pop("kind", "cost_cutting"),
            "title": f"Title {key}",
            "created_on": kw.pop("created_on", "2026-10-01"),
            "related_kind": kw.pop("related_kind", "recurring"),
            "related_key": kw.pop("related_key", "bill"),
            **kw,
        }
    )


def test_cost_cuts() -> None:
    left_out = [
        flag("other-kind", kind="subscription"),
        flag("resolved", status="resolved", resolved_on="2026-10-10"),
        flag("archived", archived=True),
        flag("on-account", related_kind="account", related_key="bill"),
        flag("no-link", related_kind=None, related_key=None),
        flag("info", action="info"),  # format 1.4: nothing to do
    ]
    assert rec.cost_cuts(left_out) == {}
    # An open info flag never beats a watching act flag: it is skipped before ranking.
    cuts = rec.cost_cuts(
        [flag("info-open", action="info"), flag("act-watch", status="watching", action="act")]
    )
    assert cuts["bill"].key == "act-watch"
    # Open beats a newer watching flag.
    cuts = rec.cost_cuts(
        [flag("open-old", created_on="2026-09-01"), flag("watch-new", status="watching")]
    )
    assert cuts["bill"].key == "open-old"
    # The newest open flag wins.
    cuts = rec.cost_cuts(
        [
            flag("open-new", created_on="2026-10-05"),
            flag("open-old", created_on="2026-09-01"),
            flag("other-bill", related_key="other"),
        ]
    )
    assert (cuts["bill"].key, cuts["other"].key) == ("open-new", "other-bill")
    assert rec.cost_cuts([flag("watch", status="watching")])["bill"].status == "watching"


def test_new_recurring_fields_stay_out_of_ai_facts(samples) -> None:
    """Notes, rail, renewal, end and cost cut fields never reach Ask (facts.py picks by
    name); keep and tag do (format 1.4)."""
    from app.ai.facts import RECURRING_FIELDS, build
    from app.db.connect import connect

    kept_out = {"notes", "payment_rail", "renews_on", "ends_on", "cost_cut", "review_by"}
    # Gap audit C: sections, by month, changes and destinations stay out too.
    kept_out |= {"group_key", "section_key", "by_month_cents", "change"}
    kept_out |= {"previous_monthly_equivalent_cents", "to_account_key", "to_account_name"}
    assert not kept_out & set(RECURRING_FIELDS)
    assert {"keep", "tag"} <= set(RECURRING_FIELDS)
    conn = connect(samples.db_path)
    try:
        facts = {(k, key): v for k, key, v in build(conn, None)}
    finally:
        conn.close()
    items = facts[("recurring", "all")]["items"]
    assert items and all(not kept_out & set(it) for it in items)
    assert all({"keep", "tag"} <= set(it) for it in items)
    assert "sections" not in facts[("recurring", "all")]
    assert "phone protection" not in repr(facts)  # a sample note


def test_recurring_calendar(samples, admin: TestClient) -> None:
    c = admin.get("/api/recurring/calendar", params={"month": "2026-09"}).json()
    assert (c["first_weekday"], c["days_in_month"]) == (2, 30)  # Sep 1 2026 is a Tuesday
    days = {d["date"]: d for d in c["days"]}
    assert [i["key"] for i in days["2026-09-18"]["items"]] == ["city-power", "kids-allowance"]
    assert days["2026-09-18"]["expected_cents"] == 16000
    assert admin.get("/api/recurring/calendar", params={"month": "26-09"}).status_code == 422
    assert admin.get("/api/recurring", params={"view": "list"}).status_code == 422


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


def test_plans_page(samples, admin: TestClient) -> None:
    p = admin.get("/api/plans").json()
    assert p["active_count"] == 4
    assert p["total_deferred_cents"] == 62000  # 120 + 300 + 200; the promo has no remaining
    assert p["remaining_unknown"] == ["promo-mattress"]
    assert p["monthly_total_cents"] == 37000
    assert p["shortest"]["key"] == "plan-sofa"  # Nov, with 2 payments left
    assert p["longest"]["key"] == "promo-mattress"
    plans = {x["key"]: x for c in p["cards"] for x in c["plans"]}
    # "N of M" matches the installments billed on the statements.
    assert (plans["device-plan"]["progress_label"], plans["device-plan"]["billed_count"]) == (
        "8 of 12",
        8,
    )
    assert (plans["plan-sofa"]["progress_label"], plans["plan-sofa"]["billed_count"]) == (
        "4 of 6",
        4,
    )
    assert (
        plans["plan-sofa"]["fees_posted_cents"] == 2000
        and plans["plan-sofa"]["monthly_cost_cents"] == 10500
    )
    assert plans["bramble-deferred"]["components"][1]["trip_key"] == "ski-weekend"
    assert [
        (s["month"], s["monthly_cents"], s["drop_cents"], s["ended"]) for s in p["step_downs"]
    ] == [
        ("2026-10", 37000, 0, []),
        ("2026-11", 37000, 0, []),
        ("2026-12", 22500, 14500, ["bramble-deferred", "plan-sofa"]),
        ("2027-01", 22500, 0, []),
        ("2027-02", 15000, 7500, ["device-plan"]),
        ("2027-03", 15000, 0, []),
        ("2027-04", 0, 15000, ["promo-mattress"]),
    ]
    assert [(w["plan_key"], w["kind"], w["days_left"], w["level"]) for w in p["warnings"]] == [
        ("bramble-deferred", "pay_before", 39, "high"),
        ("promo-mattress", "promo_expiry", 162, "low"),
    ]
    assert [c["account_key"] for c in p["cards"]] == [
        "bramble-store-card",
        "cobalt-card",
        "orchard-card",
        "summit-card",
        "old-store-card",  # no active plan: last
    ]
    assert (plans["towne-coats"]["status"], plans["towne-coats"]["warning"]) == ("paid_off", None)
    towne = p["cards"][-1]
    assert (towne["monthly_cents"], towne["remaining_cents"]) == (0, 0)
    # Format 1.4: the chat's note and when a plan was paid off.
    assert plans["promo-mattress"]["note"] == "Pay it off before the promo ends"
    coats = plans["towne-coats"]
    assert (coats["note"], coats["paid_off_on"], coats["paid_off_early"]) == (
        "Paid off a month early",
        "2026-05-18",
        True,
    )
    assert {k for k, x in plans.items() if x["note"]} == {"promo-mattress", "towne-coats"}
    assert [k for k, x in plans.items() if x["paid_off_early"]] == ["towne-coats"]
    # Only refund rows count: the sofa's +$600 plan creation is an adjustment.
    assert {k: x["refund_cents"] for k, x in plans.items()} == dict.fromkeys(plans, 0)


def _refunds(db) -> dict[str, int]:
    return {x["key"]: x["refund_cents"] for c in plans_page(db)["cards"] for x in c["plans"]}


def test_plan_refunds(samples, db) -> None:
    """A refund row that carries a plan's key counts on that plan; a hidden one does not.
    The installment and fee counts do not move."""
    key = db.execute(
        "SELECT txn_key FROM transactions WHERE description = 'TOWNE OUTFITTERS RETURN'"
    ).fetchone()["txn_key"]
    db.execute("UPDATE transactions SET plan_key = 'plan-sofa' WHERE txn_key = ?", (key,))
    got = _refunds(db)
    assert got["plan-sofa"] == 4500 and sum(got.values()) == 4500
    sofa = next(x for c in plans_page(db)["cards"] for x in c["plans"] if x["key"] == "plan-sofa")
    assert (sofa["billed_count"], sofa["fees_posted_cents"]) == (4, 2000)
    db.execute(
        """INSERT INTO transaction_overrides (txn_key, hidden, updated_at)
           VALUES (?, 1, '2026-10-20T00:00:00+00:00')""",
        (key,),
    )
    assert _refunds(db)["plan-sofa"] == 0


@pytest.mark.parametrize(
    ("paid_off_on", "end_month", "early"),
    [
        ("2026-05-18", "2026-06", True),
        ("2026-06-30", "2026-06", False),  # in the end month
        ("2026-07-02", "2026-06", False),
        ("2026-05-18", None, False),
        (None, "2026-06", False),
    ],
)
def test_paid_off_early_is_by_month(
    samples, db, paid_off_on: str | None, end_month: str | None, early: bool
) -> None:
    coats = next(
        p for p in readers.installment_plans(readers.monthly_runs(db)) if p.key == "towne-coats"
    )
    p = coats.model_copy(update={"paid_off_on": paid_off_on, "end_month": end_month})
    out = plan_out(p, {}, {}, TODAY)
    assert (out["paid_off_on"], out["paid_off_early"], out["refund_cents"]) == (
        paid_off_on,
        early,
        0,
    )


def test_cards_with_no_active_plan_sort_last(
    samples, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = readers.installment_plans

    def paid_bramble(runs):
        return [
            p.model_copy(update={"status": "paid_off"}) if p.key == "bramble-deferred" else p
            for p in real(runs)
        ]

    monkeypatch.setattr(plans_service.readers, "installment_plans", paid_bramble)
    p = admin.get("/api/plans").json()
    assert [c["account_key"] for c in p["cards"]] == [
        "cobalt-card",
        "orchard-card",
        "summit-card",
        "bramble-store-card",  # Bramble before Towne: both cleared, then by name
        "old-store-card",
    ]


def test_step_downs_start_mid_plan(samples, db) -> None:
    plans = readers.installment_plans(readers.monthly_runs(db))
    s = step_downs(plans, "2027-02")
    assert [(x["month"], x["monthly_cents"]) for x in s] == [
        ("2027-02", 15000),
        ("2027-03", 15000),
        ("2027-04", 0),
    ]
    assert step_downs([], "2026-10") == []


# --------------------------------------------------------------------------
# Debt
# --------------------------------------------------------------------------


def test_debt_page(samples, db, admin: TestClient) -> None:
    d = admin.get("/api/debt").json()
    assert [r["account_key"] for r in d["ledger"]] == [
        "cobalt-card",
        "summit-card",
        "orchard-card",
        "van-loan",
        "pinecrest-mortgage",
    ]  # cobalt has priority 1, the rest have none: highest rate first
    assert d["total_debt_cents"] == sum(r["owed_cents"] for r in d["ledger"])
    # Summit is on full autopay, orchard on statement autopay, cobalt is a promo.
    assert d["highest_rate"]["account_key"] == "van-loan"
    assert d["cheapest"]["account_key"] == "pinecrest-mortgage"
    rows = {r["account_key"]: r for r in d["ledger"]}
    mortgage = rows["pinecrest-mortgage"]
    assert (mortgage["required_cents"], mortgage["paying_cents"]) == (150000, 150000)
    assert mortgage["policy_note"] == "Do not prepay"
    summit = rows["summit-card"]
    assert summit["required_cents"] == 3500  # newest statement's minimum due
    assert summit["plans_remaining_cents"] == 20000
    assert summit["utilization_pct"] == "4.2"
    assert rows["cobalt-card"]["priority"] == 1
    assert d["paying_month"] == "2026-08"
    assert d["tracked_cards"] == [
        {"account_key": "bramble-store-card", "name": "Bramble store card", "autopay": "minimum"}
    ]
    assert d["payoff_sequence"][0]["key"] == "promo-mattress"
    db.execute("UPDATE accounts SET priority = 1 WHERE account_key = 'orchard-card'")
    db.execute("UPDATE accounts SET priority = 2 WHERE account_key = 'cobalt-card'")
    clear_cache()
    assert debt_page(db)["ledger"][0]["account_key"] == "orchard-card"


def test_debt_interest(samples, db, admin: TestClient) -> None:
    rows = {r["account_key"]: r for r in admin.get("/api/debt").json()["ledger"]}

    def interest(key: str) -> tuple:
        return rows[key]["interest_cents"], rows[key]["interest_ytd_cents"]

    # Aug 2026 (the paying month) and Jan 1 to Oct 20, from the mortgage's interest rows.
    aug, ytd = 92256, 836086
    assert interest("pinecrest-mortgage") == (aug, ytd)
    assert interest("summit-card") == (0, 0)
    assert interest("van-loan") == (None, None)  # no statements: not a false $0
    key = db.execute(
        """SELECT txn_key FROM transactions WHERE account_key = 'pinecrest-mortgage'
           AND txn_type = 'interest' AND txn_date = '2026-08-15'"""
    ).fetchone()["txn_key"]
    db.execute(
        """INSERT INTO transaction_overrides (txn_key, hidden, updated_at)
           VALUES (?, 1, '2026-10-20T00:00:00+00:00')""",
        (key,),
    )
    clear_cache()
    mortgage = {r["account_key"]: r for r in debt_page(db)["ledger"]}["pinecrest-mortgage"]
    assert (mortgage["interest_cents"], mortgage["interest_ytd_cents"]) == (0, ytd - aug)


def test_debt_payoff_ahead(samples, db, admin: TestClient) -> None:
    rows = {r["account_key"]: r for r in admin.get("/api/debt").json()["ledger"]}
    assert rows["pinecrest-mortgage"]["payoff_ahead"] is True  # 2049-06-01
    assert rows["summit-card"]["payoff_ahead"] is False  # no payoff date

    def ahead(payoff: str) -> bool:
        db.execute(
            "UPDATE accounts SET payoff_date = ? WHERE account_key = 'pinecrest-mortgage'",
            (payoff,),
        )
        clear_cache()
        ledger = debt_page(db)["ledger"]
        return {r["account_key"]: r for r in ledger}["pinecrest-mortgage"]["payoff_ahead"]

    assert ahead("2026-10-19") is False  # the day before today
    assert ahead("2026-10-20") is True  # today counts as ahead


def test_debt_statement_servicer_and_notes(samples, admin: TestClient) -> None:
    rows = {r["account_key"]: r for r in admin.get("/api/debt").json()["ledger"]}

    def statement(key: str) -> tuple:
        r = rows[key]
        return r["statement_end"], r["statement_owed_cents"], r["statement_due_date"]

    assert statement("summit-card") == ("2026-09-10", 43241, "2026-10-05")
    assert statement("orchard-card") == ("2026-09-30", 31618, "2026-10-25")
    assert statement("van-loan") == (None, None, None)
    assert rows["van-loan"]["servicer"] == "Meadow Loan Servicing"
    assert rows["pinecrest-mortgage"]["servicer"] == "Pinecrest Mortgage"
    assert rows["summit-card"]["servicer"] is None
    assert rows["cobalt-card"]["notes"] == "0% promo on the mattress until March 2027"
    assert rows["summit-card"]["notes"] is None


def test_new_debt_fields_stay_out_of_the_ai(samples) -> None:
    ensure_facts(samples.db_path, None)
    conn = connect(samples.db_path)
    try:
        debt = json.loads(
            conn.execute("SELECT json FROM ai_facts WHERE kind = 'debt'").fetchone()["json"]
        )
        blob = " ".join(r["json"] for r in conn.execute("SELECT json FROM ai_facts"))
    finally:
        conn.close()
    new = {
        "interest_cents",
        "interest_ytd_cents",
        "payoff_ahead",
        "statement_end",
        "statement_owed_cents",
        "statement_due_date",
        "servicer",
        "notes",
        "original_cents",
        "paid_down_pct",
        "rate_changes",
    }
    assert debt["ledger"] and all(not new & set(r) for r in debt["ledger"])
    assert "Meadow Loan Servicing" not in blob
    assert "0% promo on the mattress until March 2027" not in blob


@pytest.mark.parametrize(
    ("debt_type", "autopay", "rate_type", "apr", "carried"),
    [
        ("credit_card", "full", "variable", 24990, False),
        ("credit_card", "statement", "variable", 24990, False),
        ("store_card", "statement", "fixed", 24990, False),
        ("credit_card", "minimum", "variable", 24990, True),
        ("credit_card", "none", "variable", 24990, True),
        ("credit_card", None, "variable", 24990, True),
        ("loan", "full", "fixed", 6490, True),
        ("mortgage", "full", "fixed", 4500, True),
        ("credit_card", "minimum", "promo", 21990, False),
        ("loan", "full", "fixed", None, False),
    ],
)
def test_carries_rate(debt_type, autopay, rate_type, apr, carried) -> None:
    d = {"type": debt_type, "autopay": autopay, "rate_type": rate_type, "apr_milli_pct": apr}
    assert carries_rate(d) is carried


def test_rate_tiles_follow_autopay(samples, db) -> None:
    db.execute("UPDATE accounts SET autopay = 'minimum' WHERE account_key = 'summit-card'")
    clear_cache()
    assert debt_page(db)["highest_rate"]["account_key"] == "summit-card"
    db.execute("UPDATE accounts SET apr_milli_pct = NULL")  # nothing rated: both tiles None
    clear_cache()
    d = debt_page(db)
    assert (d["highest_rate"], d["cheapest"]) == (None, None)


# --------------------------------------------------------------------------
# Flags, to do, pending
# --------------------------------------------------------------------------


def test_flags_todos_pending(samples, admin: TestClient) -> None:
    f = admin.get("/api/flags").json()
    keys = [x["key"] for x in f["flags"]]
    assert "hsa-claim-paid" not in keys and f["archived_count"] == 1
    # Format 1.4 added seven flags (one per new kind) to the monthly sample.
    assert keys == [
        "bramble-promo-ends",
        "unknown-vendor",
        "home-insurance-renewal",
        "summit-fee-review",
        "promo-mattress-ends",
        "gym-ending",
        "old-plan-rollover",
        "roof-repair",
        "health-claim",
        "fibernet-rate",
        "withholding-check",
        "cloudtunes-credit",
        # Info flags not resolved come after the others (format 1.4), by date.
        "kids-college-gift",
        "savings-milestone",
        "lake-weekend-costs",
        "library-fees",
    ]
    first = f["flags"][0]
    assert (first["related_label"], first["overdue"]) == ("Bramble winter gear", False)
    labels = {x["key"]: x["related_label"] for x in f["flags"]}
    assert labels["unknown-vendor"] == "Uncategorized"
    assert labels["gym-ending"] == "Ironworks Gym"
    assert labels["summit-fee-review"] == "Summit card"
    assert labels["fibernet-rate"] == "Fibernet"
    assert labels["roof-repair"] is None  # the new flags name nothing
    # The two open info flags sit only in their own count.
    assert f["counts"] == {
        "high": 2,
        "medium": 6,
        "low": 6,
        "open": 9,
        "watching": 3,
        "resolved": 2,
        "info": 2,
    }
    every = admin.get("/api/flags", params={"include_archived": "true"}).json()
    assert len(every["flags"]) == 17
    assert [x["key"] for x in every["flags"] if x["archived"]] == ["hsa-claim-paid"]
    t = admin.get("/api/todos").json()
    assert [x["key"] for x in t["todos"]] == ["check-car-value", "file-clinic-claim"]
    assert (t["open_count"], t["done_count"]) == (1, 1)
    p = admin.get("/api/pending").json()
    assert [x["key"] for x in p["items"]] == [
        "apply-gym-cancel",
        "confirm-bramble-payment",
        "apply-water-budget",
    ]
    assert p["counts"] == {"open": 1, "waiting": 1, "done": 1}


def test_lists_on_empty_db(admin: TestClient) -> None:
    for url in (
        "/api/flags",
        "/api/todos",
        "/api/pending",
        "/api/plans",
        "/api/debt",
        "/api/goals",
    ):
        r = admin.get(url)
        assert r.status_code == 200, url
    assert admin.get("/api/flags").json()["flags"] == []
    assert admin.get("/api/goals").json() == {"goals": [], "source_run_id": None}
    assert admin.get("/api/recurring").json()["summary"]["count"] == 0
    assert admin.get("/api/cashflow").json()["latest_complete_month"] is None
    assert admin.get("/api/budgets").json()["month"] == "2026-10"
    assert admin.get("/api/statements/coverage").json()["accounts"] == []


# --------------------------------------------------------------------------
# Dashboard: upcoming bills and plan payments
# --------------------------------------------------------------------------


def test_dashboard_upcoming(samples, admin: TestClient) -> None:
    up = admin.get("/api/dashboard").json()["upcoming"]
    assert all("2026-10-20" <= u["date"] <= "2026-11-19" for u in up)
    assert [u["date"] for u in up] == sorted(u["date"] for u in up)
    head = [(u["date"], u["kind"], u["key"], u["amount_cents"]) for u in up[:7]]
    assert head == [
        ("2026-10-22", "bill", "town-water", 6000),
        ("2026-10-22", "plan", "promo-mattress", 15000),  # Cobalt closes on the 22nd
        ("2026-10-23", "bill", "kids-allowance", 1000),
        ("2026-10-28", "bill", "savings-transfer", 20000),
        ("2026-10-30", "bill", "kids-allowance", 1000),
        ("2026-10-30", "bill", "music-lessons", 8000),
        ("2026-10-31", "plan", "device-plan", 7500),
    ]
    sofa = next(u for u in up if u["key"] == "plan-sofa")
    assert (sofa["date"], sofa["amount_cents"]) == ("2026-11-10", 10500)  # payment + fee
    after = up[up.index(sofa) + 1]  # format 1.4: a trip payment sorts after plans that day
    assert (after["date"], after["kind"], after["key"]) == ("2026-11-10", "trip", "rental-car")
    assert (after["trip_key"], after["trip_name"]) == ("ski-weekend", "Ski weekend")
    assert not any(u["key"] == "house-cleaning" for u in up)  # paused
