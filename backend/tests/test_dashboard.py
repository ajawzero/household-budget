"""Dashboard payload and state readers (PLAN 3.7, 8.2)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from test_networth import _account, _balance

from app import config
from app.importer.models import to_cents
from app.services.balances import clear_cache, next_due
from app.services.dashboard import budget_status, dashboard
from app.state import readers

TODAY = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


@pytest.mark.parametrize(
    ("actual", "target", "status"),
    [
        (None, 100, "no_actual"),
        (101, 100, "over"),
        (100, 100, "on_target"),
        (90, 100, "on_target"),
        (89, 100, "under"),
        (0, 0, "on_target"),
        (21250, 30000, "under"),
    ],
)
def test_budget_status_rule(actual, target, status) -> None:
    assert budget_status(actual, target) == status


def test_dashboard_from_samples(samples, admin: TestClient) -> None:
    d = admin.get("/api/dashboard").json()
    assert d["month"] == "2026-10"
    assert (d["updated"], d["complete_through"], d["location_label"]) == (
        "2026-10-12",
        "2026-08",  # the latest complete month, as on Cash Flow and Budget
        "Sample Town",
    )
    nw = d["net_worth"]
    assert nw["value_cents"] == nw["assets_cents"] - nw["liabilities_cents"]
    assert nw["as_of"] == TODAY.isoformat() and nw["partial"] is False and nw["behind"] == []
    assert sum(p["cents"] for p in d["networth_parts"]) == nw["value_cents"]
    assert nw["balances_through"] == "2026-09-30"  # newest balance, not today
    accounts = admin.get("/api/accounts?range=1M").json()
    home = next(
        a for g in accounts["groups"] for a in g["accounts"] if a["account_key"] == "family-home"
    )
    parts = {p["key"]: p["cents"] for p in d["networth_parts"]}
    assert parts["home-equity"] == home["equity"]["equity_cents"]
    # Planned spending includes the 700.00 saved and invested.
    assert d["plan_month"] == {
        "month": "2026-10",
        "income_cents": 1100000,
        "spending_cents": 860000,
        "saved_invested_cents": 70000,
        "left_over_cents": 240000,
        "saved_pct": "28.2",  # (2400 left over + 700 saved) / 11000
        "basis": "plan",
    }
    # Bars: actual months (up to 6 before this month), then the planned months.
    bars = d["cashflow"]
    assert [(c["month"], c["basis"], c["complete"]) for c in bars] == [
        ("2026-04", "actual", True),
        ("2026-05", "actual", True),
        ("2026-06", "actual", True),
        ("2026-07", "actual", True),
        ("2026-08", "actual", True),
        ("2026-09", "actual", False),  # Summit and Cobalt statements end before Sep 30
        ("2026-10", "plan", False),
    ]
    assert bars[-1]["saved_invested_cents"] == 70000
    aug = bars[4]
    assert aug["spending_cents"] == 294739 + 70000  # bar spending includes saved and invested
    assert aug["left_over_cents"] == aug["income_cents"] - aug["spending_cents"]
    assert d["actual_month"] == {
        "month": "2026-08",
        "income_cents": 1036266,
        "spending_cents": 294739,
        "saved_invested_cents": 70000,
        "left_over_cents": 671527,
        "savings_rate": "71.6",
        "basis": "actual",
    }
    actual_groups = {g["key"]: (g["cents"], g["kind"]) for g in d["actual_by_group"]}
    assert actual_groups["saved-invested"] == (70000, "saved_invested")
    assert sum(c for c, k in actual_groups.values() if k == "spending") == 294739
    assert d["cashflow_note"] == "Sample plan for October"
    groups = d["spending_by_group"]
    assert [g["cents"] for g in groups] == sorted((g["cents"] for g in groups), reverse=True)
    assert sum(g["cents"] for g in groups) == d["plan_month"]["spending_cents"]
    kinds = {g["key"]: g["kind"] for g in groups}
    assert kinds.pop("saved-invested") == "saved_invested"
    assert set(kinds.values()) == {"spending"}
    labels = {g["key"]: g["label"] for g in groups}
    assert labels["housing"] == "Housing and utilities"
    assert labels["saved-invested"] == "Saved and invested"
    budgets = {b["key"]: b for b in d["budgets"]}
    assert budgets["dining"]["status"] == "no_actual"
    assert budgets["sams-cards"]["actual_cents"] == 21250
    assert budgets["sams-cards"]["status"] == "under"
    assert budgets["sams-cards"]["statements_needed"] is True
    assert budgets["sams-cards"]["period_basis"] == "card_cycle"
    debts = d["debts"]
    assert debts[0]["account_key"] == "pinecrest-mortgage"
    assert all(x["owed_cents"] > 0 for x in debts)
    assert [x["owed_cents"] for x in debts] == sorted(
        (x["owed_cents"] for x in debts), reverse=True
    )
    # Only the mortgage has a payoff date, and it is ahead (2049-06-01).
    assert [x["payoff_ahead"] for x in debts] == [True] + [False] * (len(debts) - 1)
    # The next three changes; trim-dining is a format 1.4 sample event (target).
    assert [c["key"] for c in d["next_changes"]] == [
        "plan-sofa-ends",
        "device-plan-ends",
        "trim-dining",
    ]
    assert d["next_changes"][0]["delta_cents"] == 10500
    assert [c["status"] for c in d["next_changes"]] == ["confirmed", "expected", "target"]
    assert [t["status"] for t in d["todos"]] == ["open", "done"]
    # The snapshot's net worth includes the kids 529 ($21,890.48 on Sep 30).
    assert d["review"]["key_numbers"] == [{"label": "Net worth", "value": to_cents("571291.84")}]
    assert len(d["review"]["top_three"]) == 3


def test_dashboard_groups_say_what_is_in_them(samples, admin: TestClient) -> None:
    """E5: each bar names what is in its group, from that month's plan lines."""
    d = admin.get("/api/dashboard").json()
    plan = {g["key"]: g["contents"] for g in d["spending_by_group"]}
    oct_ = next(
        m for m in admin.get("/api/cashflow/plan").json()["months"] if m["month"] == "2026-10"
    )
    assert plan == {g["key"]: g["contents"] for g in oct_["by_group"]}
    actual = {g["key"]: g["contents"] for g in d["actual_by_group"]}
    assert actual["kids"] == {
        "kind": "bills",
        "names": ["Youth Soccer Club", "Kids allowance"],
        "more": 0,
    }
    assert actual["discretionary"]["kind"] == "categories"
    assert actual["saved-invested"]["kind"] == "transfers"


def test_dashboard_other_months(samples, admin: TestClient) -> None:
    later = admin.get("/api/dashboard", params={"month": "2027-01"}).json()
    assert later["plan_month"] is None and later["budgets"] == []
    assert [c["key"] for c in later["next_changes"]] == [
        "device-plan-ends",
        "trim-dining",
        "switch-phone-plan",
    ]
    assert [c["status"] for c in later["next_changes"]] == ["expected", "target", "suggested"]
    earlier = admin.get("/api/dashboard", params={"month": "2026-09"}).json()
    assert earlier["plan_month"] is None  # no run had this_month on or before September


def test_net_worth_names_the_accounts_behind(db, admin: TestClient) -> None:
    """Each account that makes net worth partial, and why: no balance yet first, then
    each overdue balance with its date and when the next one was due."""
    _account(db, "zz-checking")
    _balance(db, "zz-checking", "2026-08-31", 1000)  # monthly: due Sep 30, stale after Oct 16
    _account(db, "fresh-savings")
    _balance(db, "fresh-savings", "2026-09-30", 2000)
    _account(db, "a-brokerage", group="investments", cadence="quarterly")
    _balance(db, "a-brokerage", "2026-05-31", 3000)  # quarterly: due Aug 31
    _account(db, "house", group="real_estate", cadence="none", stale_after_days=30)
    _balance(db, "house", "2026-09-01", 9000, "valuation")
    _account(db, "new-card", is_asset=0)  # no balance yet
    _account(db, "pension", group="retirement", cadence="none", stale_after_days=60)
    _balance(db, "pension", "2026-06-30", 4000, "estimate")  # a stored estimate
    nw = dashboard(db)["net_worth"]
    assert nw["partial"] is True
    assert nw["behind"] == [
        {
            "account_key": "new-card",
            "name": "new-card",
            "reason": "no_balance",
            "as_of": None,
            "source": None,
            "due": None,
        },
        {
            "account_key": "a-brokerage",
            "name": "a-brokerage",
            "reason": "overdue",
            "as_of": "2026-05-31",
            "source": "statement",
            "due": "2026-08-31",
        },
        {
            "account_key": "house",
            "name": "house",
            "reason": "overdue",
            "as_of": "2026-09-01",
            "source": "valuation",
            "due": "2026-10-01",
        },
        {
            "account_key": "pension",
            "name": "pension",
            "reason": "overdue",
            "as_of": "2026-06-30",
            "source": "estimate",
            "due": "2026-08-29",
        },
        {
            "account_key": "zz-checking",
            "name": "zz-checking",
            "reason": "overdue",
            "as_of": "2026-08-31",
            "source": "statement",
            "due": "2026-09-30",
        },
    ]
    # The API takes every stored source, a stored estimate too.
    db.commit()
    r = admin.get("/api/dashboard")
    assert r.status_code == 200 and r.json()["net_worth"]["behind"] == nw["behind"]


def test_next_due_steps_one_statement_period() -> None:
    monthly = {"statement_cadence": "monthly", "stale_after_days": None}
    assert next_due(monthly, "2026-01-31") == "2026-02-28"
    assert next_due(monthly, "2026-12-15") == "2027-01-15"
    assert next_due(monthly, "2026-01-30") == "2026-02-28"
    # A month-end statement's next one ends on the next month's last day.
    assert next_due(monthly, "2026-09-30") == "2026-10-31"
    assert next_due(monthly, "2026-02-28") == "2026-03-31"
    assert next_due(monthly, "2028-02-29") == "2028-03-31"
    assert next_due(monthly, "2026-10-30") == "2026-11-30"
    quarterly = {"statement_cadence": "quarterly", "stale_after_days": 10}
    assert next_due(quarterly, "2026-11-30") == "2027-02-28"
    assert next_due(quarterly, "2026-09-30") == "2026-12-31"
    assert next_due(quarterly, "2026-06-15") == "2026-09-15"
    assert next_due({"statement_cadence": "none", "stale_after_days": 45}, "2026-09-01") == (
        "2026-10-16"
    )
    assert next_due({"statement_cadence": "none", "stale_after_days": None}, "2026-09-01") is None


def test_dashboard_on_empty_db(admin: TestClient) -> None:
    d = admin.get("/api/dashboard").json()
    assert d["month"] == "2026-10"
    assert d["plan_month"] is None and d["review"] is None and d["updated"] is None
    assert d["net_worth"]["value_cents"] == 0 and d["debts"] == []


# --------------------------------------------------------------------------
# Month-keyed state: past months keep the plan they had at the time
# --------------------------------------------------------------------------


def _run(db, run_id: str, mode: str = "monthly", status: str = "imported", **sections) -> None:
    db.execute(
        """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at)
           VALUES (?, ?, 1, ?, ?, '2026-10-01T00:00:00+00:00')""",
        (run_id, run_id[:10], mode, status),
    )
    for name, value in sections.items():
        db.execute(
            "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, 'x', ?)",
            (run_id, name, json.dumps(value)),
        )


def _plan(month: str, income: str) -> dict:
    return {
        "month": month,
        "planned_income": income,
        "planned_spending": "100.00",
        "by_group": [],
    }


def _meta(month: str) -> dict:
    return {"updated": f"{month}-05", "this_month": month}


def test_month_keyed_sections_use_the_plan_of_the_time(db) -> None:
    _run(
        db,
        "2026-10-05",
        meta=_meta("2026-10"),
        cashflow_plan=[_plan("2026-10", "1.00"), _plan("2026-11", "2.00")],
    )
    _run(
        db,
        "2026-11-05",
        meta=_meta("2026-11"),
        cashflow_plan=[_plan("2026-10", "9.00"), _plan("2026-11", "3.00")],
    )
    _run(db, "2026-11-20", mode="backfill", meta=_meta("2026-12"))  # never state
    _run(db, "2026-11-25", status="failed", meta=_meta("2026-12"))  # never state
    runs = readers.monthly_runs(db)
    assert [r.run_id for r in runs] == ["2026-11-05", "2026-10-05"]
    assert readers.for_month(runs, "cashflow_plan", "2026-10")[0].planned_income == "1.00"
    assert readers.for_month(runs, "cashflow_plan", "2026-11")[0].planned_income == "3.00"
    assert readers.for_month(runs, "cashflow_plan", "2026-09") == []
    assert readers.plan_months(runs) == ["2026-10", "2026-11"]
    assert readers.meta(runs).this_month == "2026-11"
    d = dashboard(db)
    assert d["month"] == "2026-11"
    assert [c["income_cents"] for c in d["cashflow"]] == [100, 300]


def test_newest_run_wins_per_section(db) -> None:
    review = {"summary": "Old.", "key_numbers": [], "changes": [], "anomalies": [], "top_three": []}
    _run(db, "2026-10-05", meta=_meta("2026-10"), review=review)
    _run(db, "2026-10-05-10", meta=_meta("2026-10"))  # newer by run order, no review
    _run(db, "2026-10-05-2", meta=_meta("2026-10"), review={**review, "summary": "Newer."})
    runs = readers.monthly_runs(db)
    assert runs[0].run_id == "2026-10-05-10"
    assert readers.newest(runs, "review").summary == "Newer."
    assert readers.newest(runs, "todos") is None
