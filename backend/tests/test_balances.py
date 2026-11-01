"""Latest balance rules and net worth (PLAN 3.9, Phase 1 subset)."""

from __future__ import annotations

import csv
import json
from datetime import date

import pytest
from conftest import SAMPLE_RUNS, SAMPLES

from app.importer.models import to_cents
from app.importer.runner import import_folder
from app.services.balances import History, accounts_with_balances, latest_balances, net_worth

SEPT_30 = date(2026, 9, 30)
TODAY = date(2026, 10, 20)


@pytest.fixture
def conn(settings, db):
    for run in SAMPLE_RUNS:
        assert import_folder(SAMPLES / run, settings=settings).status == "imported"
    return db


def test_latest_balances_from_samples(conn) -> None:
    b = latest_balances(conn, TODAY)
    # money owed is negative, using the statement balance that includes plan balances
    assert (b["orchard-card"].balance_cents, b["orchard-card"].source) == (-61618, "statement")
    assert b["pinecrest-mortgage"].balance_cents == -24486086
    assert b["maple-joint-checking"].balance_cents == 3739800
    # the 06-30 valuation was retracted, the 09-30 one is newest
    assert (b["family-car"].as_of, b["family-car"].source) == ("2026-09-30", "valuation")
    # closed before today: 0, never a stale card balance
    assert (b["old-store-card"].balance_cents, b["old-store-card"].source) == (0, "closed")
    assert not any(x.missing for x in b.values())


def test_net_worth_matches_sample_check_section(conn) -> None:
    snap = json.loads((SAMPLES / "2026-10-12" / "snapshot.json").read_text())
    expected = snap["net_worth"]
    # Statement basis, like the Checks page: the chat's numbers have no app estimates.
    nw = History(conn, SEPT_30, SEPT_30, estimates=False).net_worth()
    assert nw.value_cents == to_cents(expected["value"])
    assert nw.assets_cents == to_cents(expected["assets"])
    assert nw.liabilities_cents == to_cents(expected["liabilities"])
    # Plain nw_group sums. The chat's parts net the mortgage into its home's group.
    groups = dict(nw.groups)
    groups["home-equity"] += groups.pop("mortgage")
    groups["vehicle-equity"] += groups.pop("vehicle-loans")
    assert groups == {p["key"]: to_cents(p["amount"]) for p in expected["parts"]}
    assert sum(nw.groups.values()) == nw.value_cents
    assert not nw.missing


def test_groups_sum_to_net_worth_on_other_dates(conn) -> None:
    for day in (date(2026, 3, 31), date(2026, 6, 30), TODAY):
        nw = net_worth(conn, day)
        assert sum(nw.groups.values()) == nw.value_cents == nw.assets_cents - nw.liabilities_cents


def _account(
    db,
    key: str,
    cadence: str = "monthly",
    is_asset: int = 1,
    closed_on: str | None = None,
    stale_after_days: int | None = None,
) -> None:
    db.execute("INSERT OR IGNORE INTO people VALUES ('pat', 'Pat', NULL, 'adult', 'r', 'r')")
    db.execute(
        """INSERT INTO accounts (account_key, display_name, institution, type, owner_person_key,
               ownership, account_group, nw_group, is_asset, include_in_net_worth, in_cashflow,
               statement_cadence, stale_after_days, closed_on, first_run_id, run_id)
           VALUES (?, 'A', 'Bank', 'checking', 'pat', 'individual', 'cash', 'cash', ?, 1, 1,
                   ?, ?, ?, 'r', 'r')""",
        (key, is_asset, cadence, stale_after_days, closed_on),
    )


def _balance(db, key: str, as_of: str, source: str, cents: int) -> None:
    db.execute(
        "INSERT INTO balances (account_key, as_of, source, balance_cents, run_id) "
        "VALUES (?, ?, ?, ?, 'r')",
        (key, as_of, source, cents),
    )


def test_tie_order_and_stored_estimates(db) -> None:
    _account(db, "acct-a")
    _balance(db, "acct-a", "2026-10-01", "valuation", 300)
    _balance(db, "acct-a", "2026-10-01", "manual", 200)
    _balance(db, "acct-a", "2026-10-01", "estimate", 999)
    assert latest_balances(db, TODAY)["acct-a"].source == "manual"  # estimate ranks last
    _balance(db, "acct-a", "2026-10-01", "statement", 100)
    b = latest_balances(db, TODAY)["acct-a"]
    assert (b.balance_cents, b.source) == (100, "statement")
    # A loan with only the monthly run's estimate has a balance, not "No balance".
    _account(db, "loan-b")
    _balance(db, "loan-b", "2026-09-30", "estimate", -1500)
    b = latest_balances(db, TODAY)["loan-b"]
    assert (b.balance_cents, b.source) == (-1500, "estimate")
    # a newer date beats source order
    _balance(db, "acct-a", "2026-10-05", "valuation", 500)
    assert latest_balances(db, TODAY)["acct-a"].balance_cents == 500
    # nothing after the date counts
    assert latest_balances(db, date(2026, 10, 1))["acct-a"].balance_cents == 100


def test_missing_closed_and_stale(db) -> None:
    _account(db, "no-balance")
    _account(db, "closed-card", is_asset=0, closed_on="2026-08-01")
    _balance(db, "closed-card", "2026-07-31", "statement", -5000)
    _account(db, "old-checking")
    _balance(db, "old-checking", "2026-08-01", "statement", 1000)
    _account(db, "fresh-checking")
    _balance(db, "fresh-checking", "2026-10-01", "statement", 2000)
    _account(db, "house", cadence="none", stale_after_days=400)
    _balance(db, "house", "2026-01-01", "valuation", 40000)
    b = latest_balances(db, TODAY)
    assert b["no-balance"].missing and b["no-balance"].balance_cents is None
    assert b["closed-card"].balance_cents == 0
    assert latest_balances(db, date(2026, 7, 31))["closed-card"].balance_cents == -5000
    assert b["old-checking"].stale and not b["fresh-checking"].stale
    assert not b["house"].stale
    nw = net_worth(db, TODAY)
    assert nw.missing == ("no-balance",)
    assert nw.stale == ("old-checking",)
    assert nw.partial
    assert nw.value_cents == 1000 + 2000 + 40000


def test_card_with_credit_balance_nets_down(db) -> None:
    _account(db, "cash-a")
    _balance(db, "cash-a", "2026-10-01", "statement", 10000)
    _account(db, "card-a", is_asset=0)
    _balance(db, "card-a", "2026-10-01", "statement", 2500)  # a credit balance
    nw = net_worth(db, date(2026, 10, 2))
    assert nw.liabilities_cents == -2500
    assert nw.value_cents == 12500


def _stmt_balance_on(run: str, account: str, period_end: str, day: str) -> int:
    """PLAN 3.9 from the sample files: balance (else closing) minus rows posted after day."""
    snap = json.loads((SAMPLES / run / "snapshot.json").read_text())
    st = next(
        s
        for s in snap["statements"]
        if (s["account_key"], s["period_end"]) == (account, period_end)
    )
    total = to_cents(st.get("balance") or st["closing"])
    path = SAMPLES / run / "transactions" / f"{account}-{period_end}.csv"
    with path.open(newline="") as f:
        after = sum(
            to_cents(r["amount"])
            for r in csv.DictReader(f)
            if (r["post_date"] or r["txn_date"]) > day
        )
    return total - after


def test_balance_between_closes_uses_covering_statement(conn) -> None:
    """A card payment between the card close (08-10) and the checking close (08-31)
    is counted once in the Aug 31 net worth (PLAN 3.9, section 11)."""
    day = date(2026, 8, 31)
    b = latest_balances(conn, day)
    summit = _stmt_balance_on("2026-10-12", "summit-card", "2026-09-10", "2026-08-31")
    assert (b["summit-card"].balance_cents, b["summit-card"].as_of) == (summit, "2026-08-31")
    aug_close = _stmt_balance_on("2026-10-06-2", "summit-card", "2026-08-10", "2026-08-10")
    assert summit != aug_close  # the Aug 25 payment and later charges are applied
    # the statement that ends on the day: its closing (no rows posted after it)
    maple = _stmt_balance_on("2026-10-06-2", "maple-joint-checking", "2026-08-31", "2026-08-31")
    assert b["maple-joint-checking"].balance_cents == maple
    # investments are not row-level: the latest statement row on or before the day
    assert b["lakeview-brokerage"].as_of == "2026-08-31"
    nw = net_worth(conn, day)
    assert sum(nw.groups.values()) == nw.value_cents
    assert nw.groups["credit-cards"] == sum(
        b[k].balance_cents for k in ("summit-card", "orchard-card", "cobalt-card", "old-store-card")
    )


def test_card_tracked_by_payments_has_no_balance(conn) -> None:
    b = latest_balances(conn, TODAY)["bramble-store-card"]
    assert (b.balance_cents, b.source, b.missing) == (None, "tracked", False)
    # even on a day its one statement covers
    assert latest_balances(conn, date(2026, 9, 1))["bramble-store-card"].source == "tracked"
    nw = net_worth(conn, TODAY)
    assert "bramble-store-card" not in nw.missing


def test_accounts_with_balances_rows(conn) -> None:
    rows = accounts_with_balances(conn, TODAY)
    assert len(rows) == 16
    store = next(r for r in rows if r["account_key"] == "bramble-store-card")
    assert (store["tracked_by"], store["balance_source"]) == ("payments", "tracked")
    assert store["include_in_net_worth"] == 0
    card = next(r for r in rows if r["account_key"] == "summit-card")
    assert card["balance_cents"] == -63241 and card["balance_source"] == "statement"
    assert "run_id" not in card and card["last4"] == "8812"
