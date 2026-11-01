"""Phase 5 paychecks, contributions and IRS limits (PLAN 3.3, 3.9).

Sample numbers: Alex is paid every other Friday from Jan 9 with 252.00 to the
401k (6%), 50.00 employee and 25.00 employer to the HSA, a bonus in March, and
a 1,200.00 prior-employer deferral. Sam's stubs have no 401k or HSA. The kids
529 gets 200.00 on the 5th of each month and two gifts (500.00 and 250.00).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest
from api_helpers import MEMBER, MEMBER_PW, PROD_ORIGIN, login
from fastapi.testclient import TestClient

from app import config
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.importer.apply import apply_contributions, apply_income
from app.importer.models import ContributionTombstone, PaycheckTombstone
from app.importer.runner import reapply
from app.services import contributions as c
from app.services.fingerprint import fingerprint

TODAY = date(2026, 10, 20)
H = {"Origin": PROD_ORIGIN}
REMAINING = ["2026-10-02", "2026-10-16", "2026-10-30", "2026-11-13", "2026-11-27"]
REMAINING += ["2026-12-11", "2026-12-25"]


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture
def conn(samples: Settings):
    db = connect(samples.db_path)
    yield db
    db.close()


def plan(page: dict, person: str, kind: str) -> dict:
    p = next(x for x in page["people"] if x["person_key"] == person)
    return next(x for x in p["plans"] if x["kind"] == kind)


# --------------------------------------------------------------------------
# Import and reapply
# --------------------------------------------------------------------------


def test_import_fills_paychecks_and_contributions(conn: sqlite3.Connection) -> None:
    n_alex = conn.execute("SELECT count(*) FROM paychecks WHERE person_key = 'alex'").fetchone()[0]
    n_sam = conn.execute("SELECT count(*) FROM paychecks WHERE person_key = 'sam'").fetchone()[0]
    assert (n_alex, n_sam) == (21, 18)  # 19 regular, a bonus and a small adjustment advice
    stub = conn.execute(
        "SELECT * FROM paychecks WHERE person_key = 'alex' AND pay_date = '2026-09-18'"
    ).fetchone()
    assert (stub["k401_cents"], stub["k401_milli_pct"], stub["ytd_k401_cents"]) == (
        25200,
        6000,
        508800,
    )
    assert (stub["hsa_employee_cents"], stub["hsa_employer_cents"]) == (5000, 2500)
    rows = conn.execute(
        "SELECT kind, source, count(*), sum(amount_cents), count(paycheck_key) "
        "FROM contributions GROUP BY kind, source ORDER BY kind, source"
    ).fetchall()
    # Linked to the day's paycheck when there is exactly one. Jun 12 has two advices.
    assert [tuple(r) for r in rows] == [
        ("401k_employee", "current_employer", 20, 508800, 19),
        ("401k_employee", "prior_employer", 1, 120000, 0),
        ("401k_employer", "current_employer", 20, 254400, 19),
        ("529", "gift", 2, 75000, 0),
        ("529", "personal", 9, 180000, 0),
    ]


def test_reapply_rebuilds_the_tables_from_stored_runs(
    conn: sqlite3.Connection, samples: Settings
) -> None:
    before = fingerprint(conn)
    with write_transaction(conn):  # as after the Phase 5 migration: tables empty
        conn.execute("DELETE FROM contributions")
        conn.execute("DELETE FROM paychecks")
    counts = reapply(samples)
    assert counts["tables"]["paychecks"]["inserted"] == 39
    assert counts["tables"]["contributions"]["inserted"] == 52  # 41 401k, 11 kids 529
    assert fingerprint(conn) == before
    assert reapply(samples)["changes"] == 0


def test_tombstones_delete(conn: sqlite3.Connection) -> None:
    with write_transaction(conn):
        apply_income(
            conn,
            [
                PaycheckTombstone(
                    person="alex", pay_date="2026-09-18", kind="regular", seq=1, deleted=True
                )
            ],
            "test-run",
        )
        apply_contributions(
            conn,
            [
                ContributionTombstone(
                    person="alex",
                    account_key="alex-401k",
                    kind="401k_employee",
                    source="prior_employer",
                    date="2026-01-05",
                    deleted=True,
                )
            ],
            "test-run",
        )
    assert (
        conn.execute("SELECT count(*) FROM paychecks WHERE pay_date = '2026-09-18'").fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM contributions WHERE source = 'prior_employer'"
        ).fetchone()[0]
        == 0
    )
    # The contribution rows of that day lose their paycheck link.
    assert (
        conn.execute(
            "SELECT count(*) FROM contributions WHERE date = '2026-09-18' AND paycheck_key IS NULL"
        ).fetchone()[0]
        == 2
    )


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dates", "cadence"),
    [
        (["2026-09-04", "2026-09-18", "2026-10-02"], "biweekly"),
        (["2026-09-04", "2026-09-11", "2026-09-18"], "weekly"),
        (["2026-08-15", "2026-08-31", "2026-09-15", "2026-09-30"], "semimonthly"),
        (["2026-02-15", "2026-02-28", "2026-03-15"], "semimonthly"),
        (["2026-07-25", "2026-08-25", "2026-09-25"], "monthly"),
        (["2026-09-04", "2026-09-10", "2026-09-30"], None),
        (["2026-09-04"], None),
        # Weekend shifts, a missed stub and month-end monthly pay (review, Oct 21).
        (["2026-07-15", "2026-07-31", "2026-08-14", "2026-08-31", "2026-09-15"], "semimonthly"),
        (["2026-08-07", "2026-08-21", "2026-09-18", "2026-10-02"], "biweekly"),
        (["2026-08-07", "2026-08-20", "2026-09-04", "2026-09-18"], "biweekly"),
        (["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30"], "monthly"),
        (["2026-05-29", "2026-06-30", "2026-07-31", "2026-08-31"], "monthly"),
        (["2026-07-24", "2026-08-25", "2026-09-25"], "monthly"),
    ],
)
def test_infer_cadence(dates: list[str], cadence: str | None) -> None:
    assert c.infer_cadence([date.fromisoformat(d) for d in dates]) == cadence


def test_schedule_dates() -> None:
    # Oct 31 is a Saturday paid as the 31st; Nov 15 is a Sunday, so Friday Nov 13.
    semi = c.schedule_dates(date(2026, 10, 31), "semimonthly", date(2026, 12, 31))
    assert [d.isoformat() for d in semi] == [
        "2026-11-13",
        "2026-11-30",
        "2026-12-15",
        "2026-12-31",
    ]
    # A shifted date counts from the date it stands for: Aug 14 is the 15th.
    shifted = c.schedule_dates(date(2026, 8, 14), "semimonthly", date(2026, 9, 15))
    assert [d.isoformat() for d in shifted] == ["2026-08-31", "2026-09-15"]
    monthly = c.schedule_dates(date(2026, 1, 30), "monthly", date(2026, 4, 30), 31)
    assert [d.isoformat() for d in monthly] == ["2026-02-27", "2026-03-31", "2026-04-30"]
    month_end = c.schedule_dates(date(2026, 9, 30), "monthly", date(2026, 12, 31), 31)
    assert [d.isoformat() for d in month_end] == ["2026-10-30", "2026-11-30", "2026-12-31"]
    assert c.schedule_dates(date(2026, 12, 25), "biweekly", date(2026, 12, 31)) == []
    assert c.infer_schedule([date(2026, 6, 30), date(2026, 7, 31)]) == c.Schedule("monthly", 31)


# --------------------------------------------------------------------------
# GET /api/contributions
# --------------------------------------------------------------------------


def test_limits_are_seeded_unconfirmed(admin: TestClient, samples) -> None:
    page = admin.get("/api/contributions?year=2026").json()
    limits = {x["kind"]: x for x in page["limits"]}
    assert {k: v["limit_cents"] for k, v in limits.items()} == {
        "k401_deferral": 2450000,
        "k401_catchup_50": 800000,
        "k401_catchup_60_63": 1125000,
        "k415c_total": 7200000,
        "hsa_self": 440000,
        "hsa_family": 875000,
        "hsa_catchup_55": 100000,
    }
    assert not any(x["confirmed"] for x in page["limits"])
    assert limits["hsa_family"]["includes_employer"] is True
    assert limits["k401_deferral"]["source_url"].startswith("https://www.irs.gov/")


def test_401k_ytd_projection_and_election(admin: TestClient, samples) -> None:
    page = admin.get("/api/contributions").json()
    assert page["year"] == 2026 and page["as_of"] == "2026-10-20"
    assert [p["person_key"] for p in page["people"]] == ["alex"]  # Sam has no 401k or HSA
    alex = page["people"][0]
    assert alex["schedule"] == {"cadence": "biweekly", "last_pay_date": "2026-09-18"}
    assert alex["settings_set"] is False
    k = plan(page, "alex", "k401")
    ytd = 19 * 25200 + 30000 + 120000  # stubs (regular and bonus) plus the prior employer
    assert k["ytd_cents"] == ytd and k["ytd_as_of"] == "2026-09-18"
    assert k["ytd_parts"] == {
        "stubs_cents": 508800,
        "employer_cents": None,
        "prior_employer_cents": 120000,
        "personal_cents": 0,
    }
    assert (k["sum_of_checks_cents"], k["checks_counted"], k["cross_check_diff_cents"]) == (
        508800,
        20,
        0,
    )
    assert (k["limit_cents"], k["room_cents"], k["limit_confirmed"]) == (
        2450000,
        2450000 - ytd,
        False,
    )
    # Seven pay dates after the Sep 18 stub. Two are past but not imported yet.
    assert k["remaining_dates"] == REMAINING and k["checks_left"] == 7
    assert (k["per_check_cents"], k["per_check_pct"]) == (25200, "6.0")
    assert k["projected_cents"] == ytd + 7 * 25200
    assert k["status"] == "under"
    # Lead time 1: the next check (Oct 30) is too soon, so Nov 13.
    assert (k["change_on"], k["checks_before_change"], k["checks_from_change"]) == (
        "2026-11-13",
        3,
        4,
    )
    needed = (2450000 - ytd - 3 * 25200) // 4
    assert k["needed_per_check_cents"] == needed
    assert k["needed_pct"] == "103.9"  # of 4,200.00 gross
    assert "more than the gross pay" in k["notes"][-1]
    prior = k["ytd_parts"]["prior_employer_cents"]
    assert prior > 0  # 415(c) leaves the prior employer out
    assert k["k415c"] == {
        "employee_cents": ytd - prior,
        "employer_cents": 254400,
        "total_cents": ytd - prior + 254400,
        "limit_cents": 7200000,
        "room_cents": 7200000 - (ytd - prior) - 254400,
    }


def test_hsa_needs_coverage_then_uses_family_and_catch_up(admin: TestClient, samples) -> None:
    h = plan(admin.get("/api/contributions").json(), "alex", "hsa")
    assert h["ytd_cents"] == 19 * 7500 and h["ytd_parts"]["employer_cents"] == 19 * 2500
    assert (h["limit_cents"], h["status"]) == (None, "no_limit")
    assert "Set HSA coverage in Settings to see the limit." in h["notes"]
    body = {
        "k401_catch_up": "60-63",
        "hsa_coverage": "family",
        "hsa_catch_up": True,
        "election_lead_checks": 0,
    }
    r = admin.put("/api/contributions/people/alex", json=body, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["hsa_coverage"] == "family"
    page = admin.get("/api/contributions").json()
    h = plan(page, "alex", "hsa")
    assert (h["limit_cents"], h["limit_base_cents"], h["limit_catch_up_cents"]) == (
        975000,
        875000,
        100000,
    )
    assert h["shared"] is True and h["per_check_employer_cents"] == 2500
    # Lead time 0: change on the next check after today (Oct 30), 5 checks from then.
    assert (h["change_on"], h["checks_before_change"], h["checks_from_change"]) == (
        "2026-10-30",
        2,
        5,
    )
    total = (975000 - 19 * 7500 - 2 * 7500) // 5
    assert h["needed_per_check_cents"] == total - 2500  # the employee's part
    k = plan(page, "alex", "k401")
    assert k["limit_cents"] == 2450000 + 1125000  # the 60 to 63 catch-up replaces the 50+
    assert page["people"][0]["settings_set"] is True
    assert admin.put("/api/contributions/people/nobody", json=body, headers=H).status_code == 404


def test_status_over_and_at_limit(admin: TestClient, conn: sqlite3.Connection) -> None:
    ytd = 19 * 25200 + 30000 + 120000
    projected = ytd + 7 * 25200
    for limit, status in ((projected - 1, "over"), (projected + 100, "at_limit")):
        r = admin.put(
            "/api/contribution-limits",
            json={"year": 2026, "kind": "k401_deferral", "limit_cents": limit},
            headers=H,
        )
        assert r.status_code == 200, r.text
        assert r.json()["confirmed"] is True
        k = plan(admin.get("/api/contributions").json(), "alex", "k401")
        assert k["status"] == status and k["limit_confirmed"] is True
    assert k["projected_room_cents"] == 100


def test_year_without_limits(admin: TestClient, samples) -> None:
    page = admin.get("/api/contributions?year=2027").json()
    assert page["limits"] == [] and page["limits_found"] is False
    k = plan(page, "alex", "k401")
    assert k["status"] == "no_limit" and k["ytd_cents"] == 0
    assert c.NO_YEAR_NOTE in k["notes"]
    assert admin.get("/api/contributions?year=1999").status_code == 422


def test_limit_put_is_admin_only_and_logs_no_amounts(
    prod: TestClient, conn: sqlite3.Connection
) -> None:
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    body = {"year": 2027, "kind": "hsa_self", "limit_cents": 450000}
    assert prod.get("/api/contributions").status_code == 200
    assert prod.put("/api/contribution-limits", json=body, headers=H).status_code == 403
    assert prod.put("/api/contributions/people/alex", json={}, headers=H).status_code == 403


def test_limit_put_adds_a_year_and_unconfirms(admin: TestClient, conn: sqlite3.Connection) -> None:
    body = {"year": 2027, "kind": "hsa_self", "limit_cents": 450000}
    r = admin.put("/api/contribution-limits", json=body, headers=H)
    assert r.status_code == 200 and r.json()["confirmed"] is True
    r = admin.put("/api/contribution-limits", json={**body, "confirm": False}, headers=H)
    assert r.json()["confirmed"] is False and r.json()["includes_employer"] is True
    bad = admin.put("/api/contribution-limits", json={**body, "kind": "ira"}, headers=H)
    assert bad.status_code == 422
    assert admin.put("/api/contribution-limits", json=body).status_code == 403  # no Origin
    details = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT detail_json FROM events WHERE entity = 'contribution_limits' ORDER BY id"
        )
    ]
    assert details == [{"confirmed": True}, {"confirmed": False}]


# --------------------------------------------------------------------------
# IRA and 529 (no limits)
# --------------------------------------------------------------------------

KIDS_529 = {
    "person_key": "alex",
    "person": "Alex",
    "account_key": "kids-529",
    "account_name": "Kids 529 (Bluestem)",
    "kind": "529",
    "label": "529",
    "ytd_cents": 255000,
    "gift_cents": 75000,
    "count": 11,
    "last_date": "2026-09-05",
    "last_cents": 20000,
}


def test_ira_and_529_money_this_year(admin: TestClient, conn: sqlite3.Connection) -> None:
    rows = (
        ("sam-529", "529", "personal", "2026-02-10", 10000),
        ("sam-529", "529", "personal", "2026-04-10", 15000),
        ("sam-529", "529", "gift", "2026-04-10", 5000),  # same day: 2 deposits
        ("sam-529", "529", "personal", "2026-05-01", -2500),  # a correction nets out
        ("sam-ira", "ira", "personal", "2026-03-01", 600000),
        ("sam-529", "529", "personal", "2025-12-20", 99900),  # last year
    )
    with write_transaction(conn):
        for account, kind, source, day, cents in rows:
            conn.execute(
                """INSERT INTO contributions (contribution_key, person_key, account_key, kind,
                       source, date, amount_cents, run_id)
                   VALUES (?, 'sam', ?, ?, ?, ?, ?, '2026-10-14')""",
                (f"test-{account}-{source}-{day}", account, kind, source, day, cents),
            )
    sam_529 = {
        "person_key": "sam",
        "person": "Sam",
        "account_key": "sam-529",
        "account_name": "sam-529",  # not in the registry: the key
        "kind": "529",
        "label": "529",
        "ytd_cents": 27500,
        "gift_cents": 5000,
        "count": 4,
        "last_date": "2026-05-01",
        "last_cents": -2500,
    }
    sam_ira = {
        **sam_529,
        "account_key": "sam-ira",
        "account_name": "sam-ira",
        "kind": "ira",
        "label": "IRA",
        "ytd_cents": 600000,
        "gift_cents": 0,
        "count": 1,
        "last_date": "2026-03-01",
        "last_cents": 600000,
    }
    assert c.other_contributions(conn, 2026) == [KIDS_529, sam_529, sam_ira]
    page = admin.get("/api/contributions").json()
    assert page["other"] == [KIDS_529, sam_529, sam_ira]
    last_year = admin.get("/api/contributions?year=2025").json()["other"]
    assert [(o["account_key"], o["ytd_cents"]) for o in last_year] == [("sam-529", 99900)]


def test_same_day_gift_and_deposit_add_up(conn: sqlite3.Connection) -> None:
    with write_transaction(conn):
        conn.execute(
            """INSERT INTO contributions (contribution_key, person_key, account_key, kind,
                   source, date, amount_cents, run_id)
               VALUES ('test-gift', 'alex', 'kids-529', '529', 'gift', '2026-09-05', 10000,
                       '2026-10-14')"""
        )
    (row,) = c.other_contributions(conn, 2026)
    assert (row["count"], row["last_date"], row["last_cents"]) == (12, "2026-09-05", 30000)
    assert row["gift_cents"] == 85000
