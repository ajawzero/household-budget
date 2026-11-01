"""Phase 2 balance series, Accounts page, series and account detail (PLAN 3.9, 8.2).

Synthetic tests build a few accounts by hand. Sample tests use the fictional
runs in samples/data with household today fixed.
"""

from __future__ import annotations

import json
from datetime import date
from typing import get_args

import pytest
from api_helpers import PROD_ORIGIN
from conftest import SAMPLE_RUNS, SAMPLES
from fastapi.testclient import TestClient
from test_balances import _stmt_balance_on

from app import config
from app.api.models import InvestmentRangeKey, RangeKey
from app.importer.models import to_cents
from app.importer.runner import import_folder
from app.services import networth
from app.services.balances import History, clear_cache

TODAY = date(2026, 10, 20)
SEPT_30 = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


@pytest.fixture
def conn(settings, db):
    for run in SAMPLE_RUNS:
        assert import_folder(SAMPLES / run, settings=settings).status == "imported"
    return db


# --------------------------------------------------------------------------
# Synthetic accounts
# --------------------------------------------------------------------------


def _account(db, key, *, group="cash", is_asset=1, cadence="monthly", **cols) -> None:
    db.execute("INSERT OR IGNORE INTO people VALUES ('pat', 'Pat', NULL, 'adult', 'r', 'r')")
    db.execute(
        """INSERT INTO accounts (account_key, display_name, institution, type, owner_person_key,
               ownership, account_group, nw_group, is_asset, include_in_net_worth, in_cashflow,
               statement_cadence, stale_after_days, opened_on, closed_on, tracked_by,
               first_run_id, run_id)
           VALUES (?, ?, 'Bank', ?, 'pat', 'individual', ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'r', 'r')""",
        (
            key,
            key,
            cols.get("type", "checking" if is_asset else "credit_card"),
            group,
            group,
            is_asset,
            cols.get("include", 1),
            cadence,
            cols.get("stale_after_days"),
            cols.get("opened_on"),
            cols.get("closed_on"),
            cols.get("tracked_by", "statements"),
        ),
    )


def _balance(db, key, as_of, cents, source="statement") -> None:
    db.execute(
        "INSERT INTO balances (account_key, as_of, source, balance_cents, run_id) "
        "VALUES (?, ?, ?, ?, 'r')",
        (key, as_of, source, cents),
    )


def _on(h: History, key: str, day: str):
    return h.daily(key)[h.days.index(day)]


def test_missing_before_first_point_and_change_from_first_point(db) -> None:
    _account(db, "steady")
    _balance(db, "steady", "2026-09-30", 1000)
    _balance(db, "steady", "2026-10-10", 1500)
    _account(db, "late")  # no opened_on: missing until its first balance
    _balance(db, "late", "2026-10-05", 700)
    _balance(db, "late", "2026-10-15", 900)
    h = History(db, date(2026, 10, 1), date(2026, 10, 20))
    assert _on(h, "late", "2026-10-04").missing
    assert _on(h, "late", "2026-10-05").balance_cents == 700
    assert h.change("late") == 200  # from its first point, not from 0
    assert h.change("steady") == 500
    pts = networth.series_points(h, ["steady", "late"])
    assert [p["partial"] for p in pts[:4]] == [True] * 4
    assert not pts[4]["partial"] and pts[4]["value_cents"] == 1700
    assert pts[0]["value_cents"] == 1000  # the missing account adds nothing
    assert pts[-1]["value_cents"] == 2400
    assert networth.change_of(h, ["steady", "late"]) == 700


def test_not_open_yet_counts_as_zero(db) -> None:
    _account(db, "new-savings", opened_on="2026-10-05")
    _balance(db, "new-savings", "2026-10-05", 5000)
    h = History(db, date(2026, 10, 1), date(2026, 10, 20))
    first = _on(h, "new-savings", "2026-10-01")
    assert not first.missing and first.value_cents == 0
    assert h.change("new-savings") == 5000  # a new account's change is its balance
    assert not any(p["partial"] for p in networth.series_points(h, ["new-savings"]))


def test_carry_forward_and_stale(db) -> None:
    _account(db, "monthly")
    _balance(db, "monthly", "2026-08-01", 1000)  # monthly: fresh through 08-01 + 46 days
    _account(db, "house", group="real_estate", cadence="none", stale_after_days=30)
    _balance(db, "house", "2026-09-01", 9000, "valuation")
    h = History(db, date(2026, 9, 1), date(2026, 10, 20))
    assert _on(h, "monthly", "2026-09-16").balance_cents == 1000
    assert not _on(h, "monthly", "2026-09-16").stale
    assert _on(h, "monthly", "2026-09-17").stale
    assert not _on(h, "house", "2026-10-01").stale and _on(h, "house", "2026-10-02").stale
    assert _on(h, "house", "2026-10-20").balance_cents == 9000
    pts = networth.series_points(h, ["monthly", "house"])
    assert not pts[0]["partial"] and pts[-1]["partial"]
    nw = h.net_worth()
    assert nw.stale == ("house", "monthly") and nw.partial


def test_closed_is_zero_after_closing(db) -> None:
    _account(db, "old-card", is_asset=0, closed_on="2026-10-10")
    _balance(db, "old-card", "2026-09-30", -2500)
    h = History(db, date(2026, 10, 1), date(2026, 10, 20))
    assert _on(h, "old-card", "2026-10-10").balance_cents == -2500
    assert _on(h, "old-card", "2026-10-11").balance_cents == 0
    assert h.change("old-card") == 2500


def test_tracked_by_payments_left_out(db) -> None:
    _account(db, "store", is_asset=0, include=0, tracked_by="payments", cadence="none")
    _account(db, "cash")
    _balance(db, "cash", "2026-10-01", 100)
    h = History(db, date(2026, 10, 1), date(2026, 10, 20))
    assert h.net_worth_keys() == ["cash"]
    assert h.change("store") is None
    assert all(b.source == "tracked" and not b.missing for b in h.daily("store"))


def test_source_order_on_a_tie(db) -> None:
    _account(db, "acct")
    _balance(db, "acct", "2026-10-01", 300, "valuation")
    _balance(db, "acct", "2026-10-01", 200, "manual")
    h = History(db, date(2026, 10, 1), date(2026, 10, 2))
    assert _on(h, "acct", "2026-10-02").source == "manual"


# --------------------------------------------------------------------------
# Ranges
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "end", "start"),
    [
        ("1M", date(2026, 10, 20), date(2026, 9, 20)),
        ("3M", date(2026, 5, 31), date(2026, 2, 28)),
        ("6M", date(2026, 8, 31), date(2026, 2, 28)),
        ("YTD", date(2026, 10, 20), date(2026, 1, 1)),
        ("1Y", date(2026, 10, 20), date(2025, 10, 20)),
        ("1M", date(2026, 1, 15), date(2025, 12, 15)),
        ("3Y", date(2026, 10, 20), date(2023, 10, 20)),
        ("3Y", date(2028, 2, 29), date(2025, 2, 28)),
        ("1D", date(2026, 10, 20), date(2026, 10, 19)),
        ("7D", date(2026, 3, 3), date(2026, 2, 24)),
    ],
)
def test_range_starts(db, key, end, start) -> None:
    r = networth.resolve_range(db, key, end)
    assert (r["start"], r["end"]) == (start, end)


def test_all_starts_at_earliest_point(conn) -> None:
    first = conn.execute("SELECT min(as_of) AS d FROM balances").fetchone()["d"]
    r = networth.resolve_range(conn, "ALL")
    assert r["start"] <= date.fromisoformat(first) and r["end"] == TODAY
    d = networth.networth_series(conn, "ALL")
    assert d["points"][0]["date"] == r["start"].isoformat()


def test_all_on_empty_db_is_today(db) -> None:
    assert networth.resolve_range(db, "ALL")["start"] == TODAY
    d = networth.networth_series(db, "ALL")
    assert d["points"] == [
        {"date": TODAY.isoformat(), "value_cents": 0, "partial": False, "estimated": False}
    ]
    assert d["change_pct"] is None
    page = networth.accounts_page(db)
    assert page["groups"] == [] and page["summary"]["net_worth_cents"] == 0


def test_ranges_start_at_earliest_point(conn) -> None:
    """1Y would start before any data; it starts at the first balance instead."""
    first = networth.earliest_point(conn)
    assert first is not None and first > date(2025, 10, 20)
    assert networth.resolve_range(conn, "1Y")["start"] == first
    d = networth.networth_series(conn, "1Y")
    assert d["points"][0]["date"] == first.isoformat()


@pytest.mark.parametrize(
    ("key", "anchor", "start"),
    [
        ("1D", date(2026, 10, 16), date(2026, 10, 15)),  # Friday's close: Thursday on
        ("7D", date(2026, 10, 19), date(2026, 10, 12)),
        ("1D", None, date(2026, 10, 19)),  # nothing priced: from today
        ("1D", date(2026, 10, 25), date(2026, 10, 19)),  # never after the end
        ("1M", date(2026, 10, 16), date(2026, 9, 20)),  # month ranges keep today
    ],
)
def test_day_ranges_count_back_from_the_anchor(db, key, anchor, start) -> None:
    r = networth.resolve_range(db, key, date(2026, 10, 20), anchor=anchor)
    assert (r["start"], r["end"]) == (start, date(2026, 10, 20))


def test_3y_starts_at_earliest_point_like_all(conn) -> None:
    """The samples hold under three years, so 3Y reads like ALL."""
    three = networth.resolve_range(conn, "3Y")
    assert three["start"] == networth.resolve_range(conn, "ALL")["start"]


def test_range_keys_match_the_api() -> None:
    assert get_args(RangeKey) == networth.RANGE_KEYS
    assert get_args(InvestmentRangeKey) == networth.INVESTMENT_RANGE_KEYS


def test_series_has_one_point_per_day(conn) -> None:
    for key in networth.RANGE_KEYS:
        d = networth.networth_series(conn, key)
        start = date.fromisoformat(d["range"]["start"])
        assert len(d["points"]) == (TODAY - start).days + 1
        assert d["points"][-1]["date"] == TODAY.isoformat()
        assert d["end_cents"] - d["start_cents"] == d["change_cents"]


# --------------------------------------------------------------------------
# Samples: golden totals and consistency
# --------------------------------------------------------------------------


def _expected_nw():
    return json.loads((SAMPLES / "2026-10-12" / "snapshot.json").read_text())["net_worth"]


def test_golden_totals_at_sept_30(conn, monkeypatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: SEPT_30)
    expected = _expected_nw()
    page = networth.accounts_page(conn, "YTD")
    s = page["summary"]
    assert s["net_worth_cents"] == to_cents(expected["value"])
    assert s["assets_cents"] == to_cents(expected["assets"])
    assert s["liabilities_cents"] == to_cents(expected["liabilities"])
    # By owner nets the mortgage into "Home equity" (its home's group), as the chat's parts do.
    parts = {p["key"]: to_cents(p["amount"]) for p in expected["parts"]}
    assert {p["key"]: p["cents"] for p in s["by_owner"]} == parts
    assert sum(p["cents"] for p in s["by_type"]) == s["net_worth_cents"]
    assert sum(g["total_cents"] for g in page["groups"]) == s["net_worth_cents"]
    series = networth.networth_series(conn, "YTD")
    assert series["end_cents"] == to_cents(expected["value"])
    assert series["points"][-1]["date"] == "2026-09-30"


def test_group_series_add_up_to_net_worth(conn) -> None:
    page = networth.accounts_page(conn, "YTD")
    total = networth.networth_series(conn, "YTD")
    groups = {g["group"]: g for g in page["groups"]}
    parts = [networth.networth_series(conn, "YTD", key) for key in networth.SERIES_KEYS[1:]]
    for i in (0, 100, -1):
        assert (
            sum(p["points"][i]["value_cents"] for p in parts) == total["points"][i]["value_cents"]
        )
    assert sum(p["change_cents"] for p in parts) == total["change_cents"]
    h = networth.history_for(conn, networth.resolve_range(conn, "YTD"))
    for p in parts:
        if p["series"] in groups:
            assert p["end_cents"] == groups[p["series"]]["total_cents"]
            # Rows and groups count a new account from its first balance; the
            # series counts it from 0 (review, Oct 21: the van bought in August).
            new = sum(
                (h.change(k) or 0) - (h.row_change(k) or 0)
                for k in networth.series_keys(h, p["series"])
            )
            assert p["change_cents"] == groups[p["series"]]["change_cents"] + new
            if p["series"] in ("vehicles", "loans"):
                assert new != 0


def test_series_between_closes_matches_statement_files(conn) -> None:
    """Aug 31: the card's balance comes from its Sept statement minus later rows."""
    h = History(conn, date(2026, 8, 1), date(2026, 9, 30))
    expected = _stmt_balance_on("2026-10-12", "summit-card", "2026-09-10", "2026-08-31")
    assert _on(h, "summit-card", "2026-08-31").balance_cents == expected
    # the day before and after differ by that day's posted rows only
    assert _on(h, "summit-card", "2026-09-10").balance_cents == _stmt_balance_on(
        "2026-10-12", "summit-card", "2026-09-10", "2026-09-10"
    )


def test_daily_matches_one_day_rule(conn) -> None:
    """The cached range math equals a fresh one-day computation on every 7th day."""
    h = History(conn, date(2026, 1, 1), TODAY)
    for i in range(0, len(h.days), 7):
        day = date.fromisoformat(h.days[i])
        one = History(conn, day, day)
        for key in h.accounts:
            assert h.daily(key)[i] == one.at_end(key), (key, day)


def test_account_rows(conn) -> None:
    page = networth.accounts_page(conn, "YTD")
    assert [g["group"] for g in page["groups"]] == [
        g for g, _ in networth.GROUPS if g in {g["group"] for g in page["groups"]}
    ]
    rows = {a["account_key"]: a for g in page["groups"] for a in g["accounts"]}
    assert len(rows) == 16
    store = rows["bramble-store-card"]
    assert store["source"] == "tracked" and store["balance_cents"] is None
    assert store["change_cents"] is None and store["sparkline"] == []
    assert page["notes"]["tracked"] == ["bramble-store-card"]
    assert "bramble-store-card" not in page["notes"]["missing"]
    home = rows["family-home"]["equity"]
    mortgage = rows["pinecrest-mortgage"]["balance_cents"]
    assert home["loan_cents"] == mortgage < 0
    assert home["equity_cents"] == rows["family-home"]["balance_cents"] + mortgage
    assert home["underwater"] is False
    assert rows["family-car"]["equity"] is None  # no secured loan
    assert rows["old-store-card"]["balance_cents"] == 0
    assert rows["old-store-card"]["source"] is None
    assert rows["summit-card"]["owner_label"] == "Joint"
    for a in rows.values():
        assert a["dormant"] is False  # no sample account is dormant
        assert len(a["sparkline"]) <= networth.SPARKLINE_MAX
        dates = [p["date"] for p in a["sparkline"]]
        assert dates == sorted(set(dates))
        assert all("2026-01-01" <= d <= TODAY.isoformat() for d in dates)


def test_underwater_equity(conn) -> None:
    conn.execute("UPDATE balances SET balance_cents = 100 WHERE account_key = 'family-home'")
    page = networth.accounts_page(conn, "1M")
    home = next(
        a for g in page["groups"] for a in g["accounts"] if a["account_key"] == "family-home"
    )
    assert home["equity"]["underwater"] is True


def test_account_detail(conn) -> None:
    d = networth.account_detail(conn, "lakeview-brokerage", "YTD")
    ends = [s["period_end"] for s in d["statements"]]
    assert ends == sorted(ends, reverse=True)
    assert (
        d["holdings"]
        and sum(x["value_cents"] for x in d["holdings"]) == d["statements"][0]["closing_cents"]
    )
    assert all(x["quantity"] is None or "e" not in x["quantity"].lower() for x in d["holdings"])
    assert d["points"][-1] == {
        "date": TODAY.isoformat(),
        "balance_cents": 5659128,
        "estimated": False,
        "real": False,
    }
    assert len(d["recent_transactions"]) <= 20
    assert all(t["account_key"] == "lakeview-brokerage" for t in d["recent_transactions"])
    assert d["statements"][0]["market_change_cents"] == 62490
    assert d["investment"]["ytd"]["net_flows_cents"] == 450000
    card = networth.account_detail(conn, "summit-card", "1M")
    assert card["terms"]["apr_milli_pct"] == 24990
    assert card["investment"] is None
    assert card["statements"][0]["net_flows_cents"] is None
    with pytest.raises(KeyError):
        networth.account_detail(conn, "no-such-account")


def test_account_detail_about(conn) -> None:
    """The chat's registry facts, sent raw: the screen hides a custodian like the institution."""
    about = networth.account_detail(conn, "cobalt-card", "1M")["about"]
    assert about["notes"] == "0% promo on the mattress until March 2027"
    retirement = networth.account_detail(conn, "alex-401k")["about"]
    assert retirement["notes"] == "New pre-tax and Roth money goes to the target date fund."
    assert retirement["custodian"] == "Granite Trust Bank" and retirement["advisor"] is None
    broker = networth.account_detail(conn, "lakeview-brokerage")["about"]
    assert broker["advisor"] == "Birch Lane Advisors"
    assert broker["custodian"] == "Lakeview Investments"
    mortgage = networth.account_detail(conn, "pinecrest-mortgage")["about"]
    assert mortgage["notes"] == "Fixed rate for the full term. Extra cash goes to savings first."
    assert mortgage["servicer"] == "Pinecrest Mortgage"
    assert networth.account_detail(conn, "van-loan")["about"]["servicer"] == (
        "Meadow Loan Servicing"
    )
    closed = networth.account_detail(conn, "old-store-card")["about"]
    assert closed["closed_on"] == "2026-07-20"
    summit = networth.account_detail(conn, "summit-card")["about"]
    assert summit == {
        "custodian": None,
        "advisor": None,
        "servicer": None,
        "notes": None,
        "opened_on": "2017-09-15",
        "closed_on": None,
    }
    savings = networth.account_detail(conn, "harbor-savings")
    assert savings["account"]["is_asset"] is True
    assert (savings["terms"]["apr_milli_pct"], savings["terms"]["rate_type"]) == (4100, "variable")


def test_dormant_marker(conn) -> None:
    conn.execute("UPDATE accounts SET dormant = 1 WHERE account_key = 'lakeview-brokerage'")
    clear_cache()
    page = networth.accounts_page(conn, "YTD")
    rows = {a["account_key"]: a for g in page["groups"] for a in g["accounts"]}
    assert rows["lakeview-brokerage"]["dormant"] is True
    assert {k for k, a in rows.items() if a["dormant"]} == {"lakeview-brokerage"}
    # A marker only: the balance and the totals do not move.
    assert rows["lakeview-brokerage"]["balance_cents"] == 5659128
    assert networth.account_detail(conn, "lakeview-brokerage")["account"]["dormant"] is True


def test_micro_and_pct_strings() -> None:
    assert networth.micro_str(1500000) == "1.5"
    assert networth.micro_str(138288048) == "138.288048"
    assert networth.micro_str(0) == "0"
    assert networth.micro_str(None) is None
    assert networth.pct(42, 1000) == "4.2"
    assert networth.pct(-5, 1000) == "-0.5"
    assert networth.pct(1, 0) is None
    assert networth.pct(10, -100) == "10.0"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

NEW_ROUTES = (
    "/api/accounts?range=1M",
    "/api/accounts/summit-card",
    "/api/networth/series",
    "/api/dashboard",
)


def test_new_routes_need_a_session(prod: TestClient) -> None:
    for path in NEW_ROUTES:
        r = prod.get(path, headers={"Origin": PROD_ORIGIN})
        assert r.status_code == 401 and r.json() == {"detail": "Not signed in."}, path


def test_api_shapes(samples, admin: TestClient) -> None:
    for path in NEW_ROUTES:
        assert admin.get(path).status_code == 200, path
    d = admin.get("/api/accounts", params={"range": "ALL"}).json()
    assert d["range"]["key"] == "ALL" and d["as_of"] == TODAY.isoformat()
    s = admin.get("/api/networth/series", params={"range": "3M", "series": "loans"}).json()
    assert s["series"] == "loans" and s["end_cents"] < 0
    assert set(s["points"][0]) == {"date", "value_cents", "partial", "estimated"}


def test_api_404_and_bad_params(samples, admin: TestClient) -> None:
    r = admin.get("/api/accounts/nope")
    assert r.status_code == 404 and r.json() == {"detail": "No such account."}
    for path, params in (
        ("/api/accounts", {"range": "2W"}),
        ("/api/networth/series", {"series": "boats"}),
        ("/api/dashboard", {"month": "2026-13"}),
        ("/api/accounts/summit-card", {"range": "all"}),
        ("/api/accounts", {"range": "1D"}),
        ("/api/accounts/summit-card", {"range": "7D"}),
        ("/api/networth/series", {"range": "1D"}),
    ):
        assert admin.get(path, params=params).status_code == 422, (path, params)


def test_openapi_is_typed(dev: TestClient) -> None:
    schema = dev.get("/openapi.json").json()
    for path in ("/api/accounts", "/api/accounts/{account_key}", "/api/networth/series"):
        ok = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]
        assert "$ref" in ok["schema"], path
    assert "AccountRow" in schema["components"]["schemas"]
    assert "DashboardOut" in schema["components"]["schemas"]


def test_cli_openapi(settings, tmp_path, capsys, monkeypatch) -> None:
    from app import cli

    monkeypatch.setenv("BUDGET_DEV", "0")
    config.get_settings.cache_clear()
    assert cli.main(["openapi"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert "/api/dashboard" in printed["paths"]
    out = tmp_path / "openapi.json"
    assert cli.main(["openapi", "--out", str(out)]) == 0
    assert json.loads(out.read_text()) == printed


def test_summary_statement_without_rows_is_not_walked_back(conn) -> None:
    """A quarterly savings statement with no rows gives only its closing point.

    Its closing is not pushed back over the quarter (PLAN 3.9: only
    transaction-level statements cover the days inside a period).
    """
    stmts = conn.execute(
        """SELECT period_start, period_end, closing_cents, txn_count FROM statements
           WHERE account_key = 'harbor-savings' AND is_current = 1 ORDER BY period_end"""
    ).fetchall()
    assert len(stmts) >= 2 and all(s["txn_count"] == 0 for s in stmts)
    q1, q2 = stmts[0], stmts[1]
    h = History(conn, date(2026, 1, 1), TODAY)
    assert _on(h, "harbor-savings", q1["period_start"]).balance_cents != q1["closing_cents"]
    assert _on(h, "harbor-savings", q1["period_end"]).balance_cents == q1["closing_cents"]
    # the day after Q1 ends carries Q1's closing, not Q2's
    day_after = h.days[h.days.index(q1["period_end"]) + 1]
    assert _on(h, "harbor-savings", day_after).balance_cents == q1["closing_cents"]
    assert _on(h, "harbor-savings", q2["period_end"]).balance_cents == q2["closing_cents"]


def test_excluded_accounts_are_noted(db) -> None:
    _account(db, "chk")
    _balance(db, "chk", "2026-10-01", 100)
    _account(db, "kid", include=0)
    _balance(db, "kid", "2026-10-01", 900)
    page = networth.accounts_page(db, "1M")
    cash = page["groups"][0]
    assert cash["total_cents"] == 100
    assert {a["account_key"]: a["balance_cents"] for a in cash["accounts"]} == {
        "chk": 100,
        "kid": 900,
    }
    assert page["notes"]["excluded"] == ["kid"]


def test_equity_part_matches_account_equity(conn) -> None:
    """By owner "Home equity" equals the home row's equity line."""
    page = networth.accounts_page(conn, "YTD")
    home = next(
        a for g in page["groups"] for a in g["accounts"] if a["account_key"] == "family-home"
    )
    parts = {p["key"]: p for p in page["summary"]["by_owner"]}
    assert parts["home-equity"]["cents"] == home["equity"]["equity_cents"]
    assert "mortgage" not in parts
    assert sum(p["cents"] for p in parts.values()) == page["summary"]["net_worth_cents"]


# --------------------------------------------------------------------------
# Opening balance rule (PLAN 3.9: a statement's opening is a point on period_start)
# --------------------------------------------------------------------------


def _statement(db, key, start, end, opening, closing, balance=None, txn_count=0) -> None:
    db.execute(
        """INSERT OR IGNORE INTO import_runs (run_id, run_date, run_seq, mode, status, started_at)
           VALUES ('r', '2026-10-01', 1, 'monthly', 'imported', '2026-10-01T00:00:00Z')"""
    )
    db.execute(
        """INSERT INTO statements (statement_key, run_id, is_current, account_key,
               period_start, period_end, opening_cents, closing_cents, balance_cents,
               txn_count, reconciled, content_sha256)
           VALUES (?, 'r', 1, ?, ?, ?, ?, ?, ?, ?, 1, 'x')""",
        (f"{key}:{end}", key, start, end, opening, closing, balance, txn_count),
    )
    _balance(db, key, end, closing if balance is None else balance)


def test_opening_is_a_point_on_period_start(db) -> None:
    _account(db, "fund", group="investments", type="brokerage", cadence="quarterly")
    _statement(db, "fund", "2026-07-01", "2026-09-30", 1000, 1300)
    _account(db, "chk", cadence="none")  # never stale
    _balance(db, "chk", "2026-06-30", 500)
    h = History(db, date(2026, 6, 30), date(2026, 10, 20))
    assert _on(h, "fund", "2026-06-30").missing  # before the first period
    first = _on(h, "fund", "2026-07-01")
    assert (first.balance_cents, first.as_of, first.source) == (1000, "2026-07-01", "statement")
    assert _on(h, "fund", "2026-09-29").balance_cents == 1000  # carries to the close
    assert _on(h, "fund", "2026-09-30").balance_cents == 1300
    pts = networth.series_points(h, ["fund", "chk"])
    assert pts[0]["partial"] and not any(p["partial"] for p in pts[1:])
    assert pts[1]["value_cents"] == 1500  # no jump at the first close
    assert pts[-1]["value_cents"] - pts[1]["value_cents"] == 300
    assert h.change("fund") == 300  # from the opening, not from the close
    assert networth.earliest_point(db) == date(2026, 6, 30)


def test_opening_never_beats_a_real_balance_on_its_day(db) -> None:
    _account(db, "fund", group="investments", type="brokerage", cadence="quarterly")
    _statement(db, "fund", "2026-04-01", "2026-06-30", 800, 950)
    _statement(db, "fund", "2026-07-01", "2026-09-30", 1000, 1300)  # disagrees with 950
    _balance(db, "fund", "2026-04-01", 820, "valuation")
    h = History(db, date(2026, 4, 1), date(2026, 9, 30))
    assert _on(h, "fund", "2026-04-01").balance_cents == 820  # valuation beats opening
    assert _on(h, "fund", "2026-04-01").source == "valuation"
    assert _on(h, "fund", "2026-06-30").balance_cents == 950  # the close stands
    assert _on(h, "fund", "2026-07-01").balance_cents == 1000  # next opening, next day


def test_opening_skipped_when_balance_includes_plans(db) -> None:
    """A card's opening leaves out plan balances, so it is no balance point."""
    _account(db, "card", is_asset=0)
    _statement(db, "card", "2026-09-01", "2026-09-30", -200, -300, balance=-900)
    h = History(db, date(2026, 9, 1), date(2026, 9, 30))
    assert _on(h, "card", "2026-09-01").missing
    assert _on(h, "card", "2026-09-30").balance_cents == -900


def test_opening_point_in_time_report_has_none(db) -> None:
    _account(db, "pension", group="retirement", type="retirement", cadence="report")
    _statement(db, "pension", "2026-09-30", "2026-09-30", None, 4000)
    h = History(db, date(2026, 9, 1), date(2026, 9, 30))
    assert _on(h, "pension", "2026-09-29").missing
    assert _on(h, "pension", "2026-09-30").balance_cents == 4000


def test_samples_series_has_no_jump_at_first_close(conn) -> None:
    """The quarterly 401k and savings and the monthly brokerage count from Jan 1.

    Before the opening rule they were missing until their first close, so the
    YTD series jumped by their whole balance on Jan 31 and Mar 31.
    """
    h = History(conn, date(2026, 1, 1), SEPT_30)
    for key in ("alex-401k", "harbor-savings", "lakeview-brokerage"):
        assert not any(b.missing for b in h.daily(key)), key
    assert _on(h, "alex-401k", "2026-01-01").balance_cents == to_cents("85000.00")
    assert _on(h, "alex-401k", "2026-03-30").balance_cents == to_cents("85000.00")
    assert _on(h, "alex-401k", "2026-03-31").balance_cents == to_cents("85423.00")
    pts = networth.series_points(h, h.net_worth_keys())
    steps = [abs(b["value_cents"] - a["value_cents"]) for a, b in zip(pts, pts[1:], strict=False)]
    assert max(steps) < to_cents("20000")  # was 53k and 103k
    # The car's old valuation is no longer partial: its estimate keeps it current.
    stale = {k for k in h.net_worth_keys() for b in h.daily(k) if b.value_cents is None or b.stale}
    assert stale == set()
    real = History(conn, date(2026, 1, 1), SEPT_30, estimates=False)
    assert {k for k in real.net_worth_keys() for b in real.daily(k) if b.stale} == {"family-car"}
    assert h.net_worth().value_cents == to_cents(_expected_nw()["value"])


def test_opening_rule_is_idempotent(conn, settings) -> None:
    before = networth.networth_series(conn, "YTD")
    for run in SAMPLE_RUNS:
        assert import_folder(SAMPLES / run, settings=settings).status == "unchanged"
    clear_cache()
    assert networth.networth_series(conn, "YTD") == before
    h1 = History(conn, date(2026, 1, 1), TODAY)
    h2 = History(conn, date(2026, 1, 1), TODAY)
    assert all(h1.daily(k) == h2.daily(k) for k in h1.accounts)


# --------------------------------------------------------------------------
# Equity rule: a secured loan nets into its asset's nw_group
# --------------------------------------------------------------------------


def _secured_pair(db, label: str) -> None:
    _account(db, "car", group="vehicles", type="vehicle", cadence="none")
    _balance(db, "car", "2026-10-01", 20000, "valuation")
    _account(db, "auto-loan", group="loans", is_asset=0, type="loan")
    _balance(db, "auto-loan", "2026-10-01", -15000)
    _account(db, "card", group="credit_cards", is_asset=0)
    _balance(db, "card", "2026-10-01", -100)
    db.execute(
        "UPDATE accounts SET nw_group = 'garage', nw_group_label = ? WHERE account_key = 'car'",
        (label,),
    )
    db.execute(
        """UPDATE accounts SET nw_group = 'auto-loan', nw_group_label = 'Auto loan',
           secured_by_account_key = 'car' WHERE account_key = 'auto-loan'"""
    )
    # a card is never netted, even if it points at the car
    db.execute("UPDATE accounts SET secured_by_account_key = 'car' WHERE account_key = 'card'")


@pytest.mark.parametrize("label", ["Vehicle equity", "Garage"])
def test_secured_loan_nets_into_its_asset_group(db, label) -> None:
    _secured_pair(db, label)
    page = networth.accounts_page(db, "1M")
    parts = {p["key"]: p for p in page["summary"]["by_owner"]}
    assert set(parts) == {"garage", "credit_cards"}
    assert parts["garage"]["cents"] == 5000 and parts["garage"]["label"] == label
    assert not parts["garage"]["is_liability"]
    assert sum(p["cents"] for p in parts.values()) == page["summary"]["net_worth_cents"]
    # Accounts page groups stay by type: the loan still shows under Loans
    groups = {g["group"]: [a["account_key"] for a in g["accounts"]] for g in page["groups"]}
    assert groups["loans"] == ["auto-loan"] and groups["vehicles"] == ["car"]
    by_type = {p["key"]: p["cents"] for p in page["summary"]["by_type"]}
    assert by_type["loans"] == -15000 and by_type["vehicles"] == 20000
    car = next(a for g in page["groups"] for a in g["accounts"] if a["account_key"] == "car")
    assert car["equity"]["equity_cents"] == parts["garage"]["cents"]


def test_loan_secured_by_asset_outside_net_worth_stays_apart(db) -> None:
    _secured_pair(db, "Garage")
    db.execute("UPDATE accounts SET include_in_net_worth = 0 WHERE account_key = 'car'")
    page = networth.accounts_page(db, "1M")
    parts = {p["key"]: p["cents"] for p in page["summary"]["by_owner"]}
    assert parts == {"auto-loan": -15000, "credit_cards": -100}


def test_loan_outside_net_worth_is_not_in_the_equity_line(db) -> None:
    """The row's equity line and the By owner part use the same loans."""
    _secured_pair(db, "Garage")
    db.execute("UPDATE accounts SET include_in_net_worth = 0 WHERE account_key = 'auto-loan'")
    page = networth.accounts_page(db, "1M")
    parts = {p["key"]: p["cents"] for p in page["summary"]["by_owner"]}
    car = next(a for g in page["groups"] for a in g["accounts"] if a["account_key"] == "car")
    assert parts["garage"] == 20000
    assert car["equity"] is None


def test_group_change_pct(conn) -> None:
    page = networth.accounts_page(conn, "YTD")
    for g in page["groups"]:
        start = g["total_cents"] - g["change_cents"]
        assert g["change_pct"] == networth.pct(g["change_cents"], start)
    cash = next(g for g in page["groups"] if g["group"] == "cash")
    assert cash["change_pct"] is not None
