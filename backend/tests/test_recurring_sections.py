"""Recurring sections, by-month amounts, changes since the last run, savings
destinations and plan lines (gap audit C). Fake samples only."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from bad_cases import MONTHLY, copy_samples, read_json, refresh_manifest, write_json
from conftest import SAMPLE_RUNS
from fastapi.testclient import TestClient

from app import config
from app.config import Settings
from app.db.connect import read_connection
from app.db.migrate import migrate
from app.importer.models import Recurring
from app.importer.runner import IMPORTED, import_folder
from app.services import recurring as rec
from app.services.balances import clear_cache

TODAY = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def item(cadence: str = "monthly", amount: str = "10.00", **kw) -> Recurring:
    return Recurring.model_validate(
        {
            "key": kw.pop("key", "test-item"),
            "name": "Test item",
            "category_key": kw.pop("category_key", "subscriptions"),
            "amount": amount,
            "cadence": cadence,
            "status": kw.pop("status", "active"),
            **kw,
        }
    )


# ---- by month ----


def test_by_month_spreads_the_year_over_the_months_it_runs() -> None:
    assert rec.by_month(item()) == [1000] * 12
    school = item(active_months=[1, 2, 3, 4, 5, 9, 10, 11, 12])
    assert rec.by_month(school) == [1000] * 5 + [0, 0, 0] + [1000] * 4
    fee = item("annual", "120.00", credit_offset="60.00")
    assert rec.by_month(fee) == [500] * 12  # net of the credit, a twelfth a month
    soccer = item("semester", "120.00", active_months=[3, 8])
    assert rec.by_month(soccer) == [0, 0, 12_000, 0, 0, 0, 0, 12_000, 0, 0, 0, 0]
    weekly = item("weekly", "10.00")
    assert rec.by_month(weekly) == [4333] * 12


def test_by_month_adds_up_to_the_yearly_cost() -> None:
    for i in (
        item("weekly", "10.00", active_months=[6, 7, 8]),
        item("quarterly", "120.00", next_date="2026-04-15", active_months=[4, 5, 6, 7, 8, 9, 10]),
        item("every_n_weeks", "95.00", interval_n=3),
    ):
        _, net = rec.monthly_equivalent(i)
        assert abs(sum(rec.by_month(i)) - 12 * net) <= 12  # a cent of rounding a month


# ---- runs in a month ----


@pytest.mark.parametrize(
    ("kw", "month", "runs"),
    [
        ({}, "2026-10", True),
        ({"status": "paused"}, "2026-10", False),
        ({"status": "ended"}, "2026-10", False),
        ({"start_date": "2026-11-02"}, "2026-10", False),
        ({"start_date": "2026-10-31"}, "2026-10", True),
        ({"end_date": "2026-09-30"}, "2026-10", False),
        ({"end_date": "2026-10-01"}, "2026-10", True),
        ({"status": "ending", "active_through": "2026-09-15"}, "2026-10", False),
        ({"status": "ending", "active_through": "2026-12-31"}, "2026-10", True),
        # An ended item ran up to its end date, so past plan months keep it.
        ({"status": "ended", "end_date": "2026-10-05"}, "2026-10", True),
        ({"status": "ended", "end_date": "2026-09-30"}, "2026-10", False),
    ],
)
def test_runs_in(kw: dict, month: str, runs: bool) -> None:
    assert rec.runs_in(item(**kw), month) is runs


# ---- changes since the last run ----


def test_changes() -> None:
    before = [
        item(key="same"),
        item(key="up", amount="10.00"),
        item(key="down", amount="10.00"),
        item(key="to-annual", amount="10.00"),
        item(key="utility", amount="60.00", variable=True),
    ]
    now = [
        item(key="same"),
        item(key="up", amount="12.00"),
        item(key="down", amount="8.00"),
        item("annual", "150.00", key="to-annual"),  # 1250 a month, was 1000
        item(key="utility", amount="75.00", variable=True),
        item(key="fresh"),
        item(key="fresh-but-ended", status="ended"),
        item(key="up-but-paused", amount="99.00", status="paused"),
    ]
    assert rec.changes(now, before) == {
        "up": ("rate_up", 1000),
        "down": ("rate_down", 1000),
        "to-annual": ("rate_up", 1000),
        "fresh": ("new", None),
    }
    assert rec.changes(now, None) == {}  # no earlier run, no badges


# ---- the All view on the samples ----


def test_sections_on_the_samples(samples, admin: TestClient) -> None:
    r = admin.get("/api/recurring", params={"view": "all"}).json()
    items = {i["key"]: i for i in r["items"]}
    assert [(s["key"], s["kind"]) for s in r["sections"]][-1] == ("savings", "savings")
    labels = {s["key"]: s["label"] for s in r["sections"]}
    assert labels["savings"] == "Savings and transfers"
    # Tagged bills have tag sections (format 1.4): test_recurring_v14 checks them.
    assert {s["kind"] for s in r["sections"][:-1]} == {"bills", "tag"}
    assert set(labels) == {i["section_key"] for i in r["items"]}
    # Transfers and allowances are savings; everything else is its category group.
    assert items["savings-transfer"]["section_key"] == "savings"
    assert items["savings-transfer"]["group_key"] == "saved-invested"
    assert items["kids-allowance"]["section_key"] == "savings"
    assert items["kids-allowance"]["group_key"] == "kids"
    assert items["youth-soccer"]["section_key"] == "kids"
    assert items["mortgage"]["section_key"] == "housing"
    assert items["summit-annual-fee"]["section_key"] == "fees"
    # Bills sections follow the category group order.
    order = [s["key"] for s in r["sections"]]
    assert order.index("housing") < order.index("kids") < order.index("fees")
    # The destination of a transfer, from the newest paying row.
    assert items["savings-transfer"]["to_account_key"] == "harbor-savings"
    assert items["savings-transfer"]["to_account_name"]
    assert items["kids-allowance"]["to_account_key"] is None  # no paying rows
    assert items["mortgage"]["to_account_key"] is None  # bills never get one
    # By month on a school-year bill.
    assert items["music-lessons"]["by_month_cents"] == [8000] * 5 + [0, 0, 0] + [8000] * 4
    # One monthly run in the samples: no badges.
    assert all(i["change"] is None for i in r["items"])
    # The cancelled sample stays in the list, not counted.
    assert items["reelhouse"]["status"] == "ended"
    assert items["reelhouse"]["counted"] is False


def test_monthly_view_has_no_sections(samples, admin: TestClient) -> None:
    r = admin.get("/api/recurring", params={"month": "2026-10"}).json()
    assert r["sections"] == []


def _later_monthly(data: Path, run_id: str, edit) -> None:
    """A later monthly run made from the sample monthly run."""
    dst = data / run_id
    shutil.copytree(data / MONTHLY, dst)
    snap = read_json(dst / "snapshot.json")
    snap.update(run_id=run_id)
    snap["meta"]["updated"] = run_id
    edit(snap)
    write_json(dst / "snapshot.json", snap)
    manifest = read_json(dst / "manifest.json")
    manifest.update(run_id=run_id)
    write_json(dst / "manifest.json", manifest)
    refresh_manifest(dst)


def test_badges_against_the_run_before(tmp_path: Path, settings: Settings) -> None:
    data = copy_samples(tmp_path)

    def edit(snap: dict) -> None:
        for r in snap["recurring"]:
            if r["key"] == "wave-mobile":
                r["amount"] = "70.00"  # rate up
            if r["key"] == "town-water":
                r["amount"] = "90.00"  # variable: no badge
        snap["recurring"].append(
            {
                "key": "zoo-pass",
                "name": "Zoo pass",
                "category_key": "memberships",
                "amount": "120.00",
                "cadence": "annual",
                "status": "active",
            }
        )

    _later_monthly(data, "2026-10-15", edit)
    migrate(settings=settings)
    statuses = [import_folder(data / r, settings=settings).status for r in SAMPLE_RUNS]
    assert statuses + [import_folder(data / "2026-10-15", settings=settings).status] == (
        [IMPORTED] * (len(SAMPLE_RUNS) + 1)
    )
    with read_connection(settings.db_path) as conn:
        out = {i["key"]: i for i in rec.all_view(conn, "2026-10")["items"]}
    assert (out["zoo-pass"]["change"], out["zoo-pass"]["previous_monthly_equivalent_cents"]) == (
        "new",
        None,
    )
    wave = out["wave-mobile"]
    assert (wave["change"], wave["previous_monthly_equivalent_cents"]) == ("rate_up", 6500)
    assert out["town-water"]["change"] is None
    assert out["mortgage"]["change"] is None


# ---- plan lines ----


def test_plan_lines_carry_the_change(tmp_path: Path, settings: Settings) -> None:
    """E7f: the C6 badge on plan lines, with the old amount on the line's own basis."""
    data = copy_samples(tmp_path)

    def edit(snap: dict) -> None:
        for r in snap["recurring"]:
            if r["key"] == "wave-mobile":
                r["amount"] = "70.00"  # rate up
            if r["key"] == "summit-annual-fee":
                r["amount"] = "300.00"  # rate up, with a credit offset
            if r["key"] == "lawn-care":
                r["active_months"] = [4, 5, 6, 7, 8, 9, 10, 11]  # October did not move
        snap["recurring"].append(
            {
                "key": "zoo-pass",
                "name": "Zoo pass",
                "category_key": "memberships",
                "amount": "120.00",
                "cadence": "annual",
                "status": "active",
            }
        )

    _later_monthly(data, "2026-10-15", edit)
    migrate(settings=settings)
    for r in [*SAMPLE_RUNS, "2026-10-15"]:
        assert import_folder(data / r, settings=settings).status == IMPORTED
    with read_connection(settings.db_path) as conn:
        lines = {x["key"]: x for v in rec.plan_lines(conn, "2026-10").values() for x in v}
    got = {k: (lines[k]["change"], lines[k]["previous_cents"], lines[k]["cents"]) for k in lines}
    assert got["wave-mobile"] == ("rate_up", 6500, 7000)
    # The net by-month amount (the line's basis), not the gross monthly equivalent.
    assert got["summit-annual-fee"] == ("rate_up", 1250, 1667)
    assert got["lawn-care"] == ("rate_up", None, 8500)
    with read_connection(settings.db_path) as conn:
        nov = {x["key"]: x for v in rec.plan_lines(conn, "2026-11").values() for x in v}
    # The earlier run had no November charge: no "from $0.00".
    assert (nov["lawn-care"]["change"], nov["lawn-care"]["previous_cents"]) == ("rate_up", None)
    assert got["zoo-pass"] == ("new", None, 1000)
    assert got["mortgage"][:2] == (None, None)
    assert got["town-water"][:2] == (None, None)  # variable


def test_plan_lines(samples, db) -> None:
    every_line = [x for v in rec.plan_lines(db, "2026-10").values() for x in v]
    # No earlier run with a change: no badge on any line.
    assert {(x["change"], x["previous_cents"]) for x in every_line} == {(None, None)}
    oct_ = rec.plan_lines(db, "2026-10")
    housing = {r["key"]: r["cents"] for r in oct_["housing"]}
    assert housing["mortgage"] == 150_000
    assert housing["lawn-care"] == 8500  # in season: its full amount
    assert [r["cents"] for r in oct_["housing"]] == sorted(
        (r["cents"] for r in oct_["housing"]), reverse=True
    )
    assert {r["key"] for r in oct_["saved-invested"]} == {"savings-transfer"}
    assert "kids-allowance" in {r["key"] for r in oct_["kids"]}  # plan group: kids
    assert "music-lessons" in {r["key"] for r in oct_["kids"]}  # starts Oct 30
    every = {r["key"] for lines in oct_.values() for r in lines}
    assert "house-cleaning" not in every  # paused
    assert "reelhouse" not in every  # ended
    sep = rec.plan_lines(db, "2026-09")
    assert "music-lessons" not in {r["key"] for r in sep.get("kids", [])}  # not started
    aug = rec.plan_lines(db, "2026-08")
    assert "reelhouse" in {r["key"] for r in aug["lifestyle"]}  # ended Sep 12, ran in Aug
    jan = rec.plan_lines(db, "2027-01")
    assert "lawn-care" not in {r["key"] for r in jan["housing"]}  # out of season
    assert "gym" not in {r["key"] for lines in jan.values() for r in lines}  # ended Dec 31


def test_a_transfer_between_cash_flow_accounts_is_not_saved(samples) -> None:
    """Saved and invested lists a transfer only when cash flow counts it as saved."""
    from app.db.connect import connect

    conn = connect(samples.db_path)
    try:
        conn.execute(
            "UPDATE transactions SET counterparty_account_key = 'maple-joint-checking'"
            " WHERE recurring_key = 'savings-transfer'"
        )
        lines = rec.plan_lines(conn, "2026-10")
    finally:
        conn.close()
    assert "savings-transfer" not in {r["key"] for v in lines.values() for r in v}


def test_totals_leave_savings_out(samples, admin: TestClient) -> None:
    r = admin.get("/api/recurring", params={"view": "all"}).json()
    bills = [i for i in r["items"] if i["counted"] and i["section_key"] != "savings"]
    assert r["totals"]["count"] == len(bills)
    assert r["totals"]["net_monthly_equivalent_cents"] == sum(
        i["net_monthly_equivalent_cents"] for i in bills
    )
