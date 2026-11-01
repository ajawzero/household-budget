"""Phase 4 cash flow, coverage, budgets and Checks additions (PLAN 3.9, 8.2).

Golden values come from the fictional sample household (make_samples.py,
fixed seed). Today is fixed at 2026-10-20 unless a test moves it.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import SAMPLES
from fastapi.testclient import TestClient

from app import config
from app.importer.models import CashflowActual, GroupAmount, to_cents
from app.services import cashflow as cf
from app.services import checks_p4, coverage
from app.services import recurring as rec
from app.services.balances import clear_cache
from app.services.budgets import budgets_page
from app.services.checks import checks

TODAY = date(2026, 10, 20)
RUN = "2026-10-12"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    set_today(monkeypatch, TODAY)


def set_today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(config, "household_today", lambda: day)
    clear_cache()


def _chat() -> dict:
    return json.loads((SAMPLES / RUN / "snapshot.json").read_text())


def _flows(db, where: str, params: tuple = ()) -> list[tuple]:
    return [
        tuple(r)
        for r in db.execute(
            f"SELECT txn_date, flow FROM v_cashflow_rows WHERE {where} ORDER BY txn_date", params
        )
    ]


def _add_plan_run(db, month: str, groups: list[tuple[str, str]], income: str) -> None:
    """A fake older monthly run whose plan covers `month` (past months keep their plan)."""
    run_id = f"{month}-05"
    db.execute(
        """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at)
           VALUES (?, ?, 1, 'monthly', 'imported', '2026-09-01T00:00:00+00:00')""",
        (run_id, run_id),
    )
    total = sum(to_cents(a) for _, a in groups)
    sections = {
        "meta": {"updated": run_id, "this_month": month},
        "cashflow_plan": [
            {
                "month": month,
                "planned_income": income,
                "planned_spending": f"{total // 100}.{total % 100:02d}",
                "by_group": [{"key": k, "amount": a} for k, a in groups],
            }
        ],
    }
    for name, value in sections.items():
        db.execute(
            "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, 'x', ?)",
            (run_id, name, json.dumps(value)),
        )


# --------------------------------------------------------------------------
# The classification table (v_cashflow_rows, migration 0003)
# --------------------------------------------------------------------------


def test_card_payment_is_a_transfer_when_the_paid_cycle_is_in(samples, db) -> None:
    """A payment pays the cycle that closed before it. When that statement is in,
    the payment is a transfer even though the cycle holding its date is not."""
    db.execute("DELETE FROM statements WHERE statement_key = 'summit-card:2026-09-10'")
    summit = _flows(
        db, "account_key = 'maple-joint-checking' AND counterparty_account_key = 'summit-card'"
    )
    assert summit[-1] == ("2026-08-25", "transfer")  # pays the cycle that closed Aug 10
    assert {f for _, f in summit} == {"transfer"}
    orchard = _flows(
        db, "account_key = 'maple-joint-checking' AND counterparty_account_key = 'orchard-card'"
    )
    # Jan 20 pays a December cycle that was never imported. The January
    # statement covers the payment date, so it is still a transfer.
    assert orchard[0] == ("2026-01-20", "transfer")


def test_card_payment_with_no_statement_is_spending(samples, db) -> None:
    db.execute(
        "DELETE FROM statements WHERE account_key = 'summit-card' AND period_end >= '2026-08-01'"
    )
    summit = _flows(
        db, "account_key = 'maple-joint-checking' AND counterparty_account_key = 'summit-card'"
    )
    # The Aug 25 payment pays the cycle closing Aug 10 (removed), so it stands in
    # for the card's spending. Jul 25 pays the Jul 10 cycle, which is still in.
    assert summit[-2:] == [("2026-07-25", "transfer"), ("2026-08-25", "card_no_stmt")]
    acts = cf.actuals(cf.load_rows(db, "2026-08", "2026-08"), ["2026-08"])
    assert acts["2026-08"].card_no_statement > 0
    assert acts["2026-08"].by_group[cf.CARD_NO_STMT_KEY] == acts["2026-08"].card_no_statement
    b = cf.breakdown(db, "2026-08", "2026-08", "category")
    item = next(i for i in b["items"] if i["key"] == cf.CARD_NO_STMT_KEY)
    assert item["label"] == "Card spending (no statement)"
    m = cf.breakdown(db, "2026-08", "2026-08", "merchant")
    assert any(i["label"] == "Summit card" for i in m["items"])
    rows = checks_p4.card_payments_no_statement(db, coverage.coverage_state(db))
    assert [(r["account_key"], r["month"], r["count"]) for r in rows] == [
        ("summit-card", "2026-08", 1)
    ]
    detail = cf.category_detail(db, cf.CARD_NO_STMT_KEY, "2026-08", "2026-08")
    assert detail is not None and detail["months"][0]["cents"] == item["cents"]


def test_reimbursable_waits_until_paid_back(samples, db) -> None:
    clinic = _flows(db, "description IN ('FAMILY CLINIC', 'HSA REIMBURSEMENT')")
    assert clinic == [("2026-04-08", "reimbursable"), ("2026-05-03", "reimbursement")]
    lab = cf.labels(db)
    assert cf.reimbursements(db, lab) == []  # paid back on May 3
    db.execute("UPDATE transactions SET removed_at = 'x' WHERE description = 'HSA REIMBURSEMENT'")
    waiting = cf.reimbursements(db, lab)
    assert [(w["txn_date"], w["merchant"], w["amount_cents"]) for w in waiting] == [
        ("2026-04-08", "Family Clinic", -18000)
    ]
    acts = cf.actuals(cf.load_rows(db, "2026-04", "2026-04"), ["2026-04"])["2026-04"]
    assert acts.reimbursable == 18000  # left out of spending
    assert acts.by_category.get("medical") is None


def test_uncategorized_counts_as_spending(samples, db) -> None:
    acts = cf.actuals(cf.load_rows(db, "2026-07", "2026-07"), ["2026-07"])["2026-07"]
    assert acts.uncategorized == 1875
    assert acts.by_category["uncategorized"] == 1875


def _row(db, where: str) -> str:
    keys = [r[0] for r in db.execute(f"SELECT txn_key FROM v_transactions WHERE {where}")]
    assert keys, where
    return keys[0]


def _flow(db, key: str) -> str:
    return db.execute("SELECT flow FROM v_cashflow_rows WHERE txn_key = ?", (key,)).fetchone()[0]


def test_money_in_from_a_loan_is_a_transfer(samples, db) -> None:
    key = _row(
        db,
        """account_key = 'maple-joint-checking' AND counterparty_account_key = 'pinecrest-mortgage'
           AND substr(txn_date, 1, 7) = '2026-08'""",
    )
    assert _flow(db, key) == "spending"  # a payment out to the loan
    before = cf.actuals(cf.load_rows(db, "2026-08", "2026-08"), ["2026-08"])["2026-08"]
    # A draw or payout from the loan into checking.
    db.execute("UPDATE transactions SET amount_cents = 2000000 WHERE txn_key = ?", (key,))
    assert _flow(db, key) == "transfer"
    after = cf.actuals(cf.load_rows(db, "2026-08", "2026-08"), ["2026-08"])["2026-08"]
    assert after.spending > 0 and after.spending < before.spending


def _mark_transfer(db, key: str) -> None:
    db.execute(
        """INSERT INTO transaction_overrides (txn_key, is_transfer, updated_at)
           VALUES (?, 1, '2026-10-20T00:00:00+00:00')""",
        (key,),
    )


def test_marking_income_or_reimbursable_as_transfer(samples, db) -> None:
    pay = _row(
        db,
        """account_key = 'maple-joint-checking' AND amount_cents > 0
           AND category_key IN (SELECT category_key FROM categories WHERE kind = 'income')
           AND substr(txn_date, 1, 7) = '2026-08' AND COALESCE(is_transfer, 0) = 0""",
    )
    assert _flow(db, pay) == "income"
    before = cf.actuals(cf.load_rows(db, "2026-08", "2026-08"), ["2026-08"])["2026-08"]
    _mark_transfer(db, pay)
    assert _flow(db, pay) == "transfer"
    after = cf.actuals(cf.load_rows(db, "2026-08", "2026-08"), ["2026-08"])["2026-08"]
    amount = db.execute(
        "SELECT amount_cents FROM v_transactions WHERE txn_key = ?", (pay,)
    ).fetchone()[0]
    assert after.income == before.income - amount

    clinic = _row(db, "description = 'FAMILY CLINIC'")
    assert _flow(db, clinic) == "reimbursable"
    db.execute("UPDATE transactions SET removed_at = 'x' WHERE description = 'HSA REIMBURSEMENT'")
    assert len(cf.reimbursements(db, cf.labels(db))) == 1
    _mark_transfer(db, clinic)
    assert _flow(db, clinic) == "transfer"
    assert cf.reimbursements(db, cf.labels(db)) == []


def test_late_payment_to_a_closed_card_is_a_transfer(samples, db) -> None:
    key = _row(
        db,
        """account_key = 'maple-joint-checking' AND counterparty_account_key = 'summit-card'
           AND txn_date = '2026-08-25'""",
    )
    db.execute(
        """UPDATE transactions SET counterparty_account_key = 'old-store-card',
                  txn_date = '2026-09-05' WHERE txn_key = ?""",
        (key,),
    )
    # old-store-card: last statement Jul 15, closed Jul 20. Paid Sep 5, more
    # than 45 days after its last statement.
    assert _flow(db, key) == "transfer"
    db.execute("UPDATE accounts SET closed_on = NULL WHERE account_key = 'old-store-card'")
    assert _flow(db, key) == "transfer"  # inactive: no later cycle is expected
    db.execute("UPDATE accounts SET active = 1 WHERE account_key = 'old-store-card'")
    assert _flow(db, key) == "card_no_stmt"  # open and active, cycle not in


# --------------------------------------------------------------------------
# GET /api/cashflow: golden numbers
# --------------------------------------------------------------------------

INCOME = {  # golden income per month
    "2026-01": 1036273,
    "2026-02": 1036269,
    "2026-03": 1348288,
    "2026-04": 1036295,
    "2026-05": 1329418,
    "2026-06": 1040086,
    "2026-07": 1036293,
    "2026-08": 1036266,
    "2026-09": 1036269,
}
SPENDING = {  # golden spending per month
    "2026-01": 293525,
    "2026-02": 403501,
    "2026-03": 306566,
    "2026-04": 351672,
    "2026-05": 434130,
    "2026-06": 345736,
    "2026-07": 339715,
    "2026-08": 294739,
    "2026-09": 446512,  # includes 1,650.00 of van loan payments, 24.50 parking and toll
}
BASIS = {  # payment-basis part of spending
    "2026-01": 4210,
    "2026-02": 5575,
    "2026-03": 3890,
    "2026-04": 6120,
    "2026-05": 4745,
    "2026-06": 5230,
    "2026-07": 3980,
    "2026-08": 4415,
    "2026-09": 0,
}
SAVED = 70000  # every month: 500.00 to the brokerage and 200.00 to savings
SOFA_FEE = 500  # the Plan It fee billed in Aug
AUG_BILLED = 18000  # Aug installments billed: sofa 100 + 5 fee, device 75
# Aug on the plan basis: the sofa fee leaves Fees, the billed installments count.
AUG_PLAN_BASIS = SPENDING["2026-08"] + SAVED - SOFA_FEE + AUG_BILLED


def test_cashflow_golden(samples, admin: TestClient) -> None:
    c = admin.get("/api/cashflow").json()
    assert (c["from"], c["to"], c["current_month"]) == ("2026-01", "2026-10", "2026-10")
    assert c["latest_complete_month"] == "2026-08"
    months = {m["month"]: m for m in c["months"]}
    for month, income in INCOME.items():
        spending, saved, basis = SPENDING[month], SAVED, BASIS[month]
        m = months[month]
        assert (
            m["income_cents"],
            m["spending_cents"],
            m["saved_invested_cents"],
            m["payment_basis_cents"],
        ) == (income, spending, saved, basis), month
        assert m["left_over_cents"] == income - spending - saved
        assert m["status"] == ("partial" if month == "2026-09" else "complete")
        assert m["plan"] is None
    oct_ = months["2026-10"]
    assert oct_["status"] == "current" and oct_["income_cents"] == 0
    assert oct_["plan"] == {
        "income_cents": 1100000,
        "spending_cents": 860000,
        "saved_invested_cents": 70000,
        "left_over_cents": 240000,
        "saved_pct": "28.2",
    }
    assert months["2026-08"]["savings_rate"] == "71.6"
    assert c["kpi"]["month"] == "2026-08" and c["kpi"]["spending_cents"] == 294739
    done = [m for m in INCOME if m != "2026-09"]
    income, spending = sum(INCOME[m] for m in done), sum(SPENDING[m] for m in done)
    assert c["totals"] == {
        "months": 8,
        "income_cents": income,
        "spending_cents": spending,
        "saved_invested_cents": 8 * SAVED,
        "left_over_cents": income - spending - 8 * SAVED,
        "savings_rate": "68.9",
    }
    assert c["averages"]["income_cents"] == 1112399
    assert c["waiting_reimbursement"] == [] and c["waiting_reimbursement_cents"] == 0


def test_app_cash_flow_matches_the_chat(samples, db) -> None:
    """Monthly spending and income match the chat's cashflow_actual (Phase 4 done-when)."""
    for actual in _chat()["cashflow_actual"]:
        m = actual["month"]
        a = cf.actuals(cf.load_rows(db, m, m), [m])[m]
        assert a.income == to_cents(actual["income"])
        assert a.spending == to_cents(actual["spending"])
        assert a.saved == to_cents(actual["saved_invested"])
        assert {k: v for k, v in a.by_group.items() if v} == {
            g["key"]: to_cents(g["amount"]) for g in actual["by_group"]
        }


def test_tracked_cards_line(samples, admin: TestClient) -> None:
    """Payment basis per card: payments to it plus rows of a statement the chat used."""
    t = admin.get("/api/cashflow", params={"from": "2026-07", "to": "2026-09"}).json()[
        "tracked_cards"
    ]
    assert (t["label"], t["basis"]) == ("Sam's cards", "payment basis")
    (card,) = t["cards"]
    assert card["account_key"] == "bramble-store-card"
    assert [(m["month"], m["payments_cents"], m["statement_cents"]) for m in card["months"]] == [
        ("2026-07", 3980, 0),
        ("2026-08", 4415, 3299),  # Aug 24 payment (pays July) + the Aug 30 statement row
        ("2026-09", 0, 2150),  # the Sep 24 payment is a normal card payment
    ]
    assert [m["cents"] for m in t["months"]] == [3980, 7714, 2150]
    assert t["total_cents"] == card["total_cents"] == 13844
    # Averages use the complete months only: Jul and Aug (Sep is not in yet).
    assert card["avg_cents"] == t["avg_cents"] == 5847  # (3980 + 7714) / 2
    assert t["avg_months"] == 2
    # The last payment counts any payment to the card: Sep 24 is a normal card payment.
    assert (card["last_payment_date"], card["last_payment_cents"]) == ("2026-09-24", 5449)


def test_tracked_cards_average_and_last_payment(samples, admin: TestClient) -> None:
    def tracked(start: str, end: str) -> dict:
        r = admin.get("/api/cashflow", params={"from": start, "to": end})
        return r.json()["tracked_cards"]

    # No complete month in range: no average. No payment in range: the one before it.
    t = tracked("2026-10", "2026-10")
    (card,) = t["cards"]
    assert (t["avg_months"], t["avg_cents"], card["avg_cents"]) == (0, None, None)
    assert (card["last_payment_date"], card["last_payment_cents"]) == ("2026-09-24", 5449)
    # A past range reads as of its last month, not today.
    t = tracked("2026-01", "2026-03")
    (card,) = t["cards"]
    assert t["avg_months"] == 3
    assert card["avg_cents"] == t["avg_cents"] == 4558  # 13675 / 3, rounded
    assert (card["last_payment_date"], card["last_payment_cents"]) == ("2026-03-24", 3890)


def test_cashflow_ranges(samples, admin: TestClient) -> None:
    r = admin.get("/api/cashflow", params={"from": "2026-05", "to": "2026-03"})
    assert r.status_code == 422 and "after" in r.json()["detail"]
    r = admin.get("/api/cashflow", params={"from": "2023-01", "to": "2026-03"})
    assert r.status_code == 422
    assert admin.get("/api/cashflow", params={"to": "2026-13"}).status_code == 422
    one = admin.get("/api/cashflow", params={"from": "2026-08", "to": "2026-08"}).json()
    assert [m["month"] for m in one["months"]] == ["2026-08"]


# --------------------------------------------------------------------------
# Breakdown, category drawer, plan, income
# --------------------------------------------------------------------------


def test_breakdown(samples, admin: TestClient) -> None:
    b = admin.get("/api/cashflow/breakdown", params={"by": "group"}).json()
    assert (b["from"], b["to"], b["complete"]) == ("2026-08", "2026-08", True)
    chat = {g["key"]: to_cents(g["amount"]) for g in _chat()["cashflow_actual"][0]["by_group"]}
    assert {i["key"]: i["cents"] for i in b["items"]} == chat
    assert b["total_cents"] == 294739 and b["saved_invested_cents"] == 70000
    assert [i["cents"] for i in b["items"]] == sorted(
        (i["cents"] for i in b["items"]), reverse=True
    )
    assert b["items"][0]["share_pct"] == "59.8"  # housing 1762.76 of 2947.39
    cats = admin.get("/api/cashflow/breakdown").json()
    assert sum(i["cents"] for i in cats["items"]) == 294739
    clothing = next(i for i in cats["items"] if i["key"] == "clothing")
    assert clothing["payment_basis_cents"] == 4415 and clothing["emoji"]
    merchants = admin.get("/api/cashflow/breakdown", params={"by": "merchant"}).json()
    labels = {i["label"]: i for i in merchants["items"]}
    assert labels["Bramble store card"]["payment_basis_cents"] == 4415
    assert labels["Mortgage (Pinecrest)"]["cents"] == 150000  # loan payments show the account
    income = admin.get("/api/cashflow/breakdown", params={"flow": "income"}).json()
    assert income["total_cents"] == 1036266 and income["saved_invested_cents"] == 0
    assert income["items"][0]["key"] == "paycheck"
    partial = admin.get(
        "/api/cashflow/breakdown", params={"from": "2026-08", "to": "2026-09"}
    ).json()
    assert partial["complete"] is False and partial["partial_months"] == ["2026-09"]
    assert admin.get("/api/cashflow/breakdown", params={"by": "owner"}).status_code == 422


def test_category_drawer(samples, admin: TestClient) -> None:
    d = admin.get("/api/cashflow/category/dining", params={"to": "2026-10"}).json()
    assert (d["name"], d["group_key"], d["kind"]) == ("Dining", "lifestyle", "spending")
    assert [m["month"] for m in d["months"]][:1] == ["2026-01"] and len(d["months"]) == 10
    assert [m["cents"] for m in d["months"]] == [
        5134, 14927, 10629, 11974, 20287, 2719, 7329, 11552, 2477, 0,
    ]  # fmt: skip
    oct_ = d["months"][-1]
    assert (oct_["budget_cents"], oct_["group_plan_cents"], oct_["status"]) == (
        40000,
        365000,  # Lifestyle's plan after fuel moved to Transportation (Phase 7)
        "current",
    )
    assert d["average_cents"] == 10569  # Jan to Aug only (complete months)
    assert d["top_merchants"][0] == {"label": "Blue Door Bistro", "cents": 52916, "count": 9}
    default = admin.get("/api/cashflow/category/dining").json()
    assert (default["from"], default["to"]) == ("2026-01", "2026-08")
    assert admin.get("/api/cashflow/category/no-such").status_code == 404
    assert admin.get("/api/cashflow/category/Bad_Key").status_code == 422
    pay = admin.get(
        "/api/cashflow/category/paycheck", params={"from": "2026-09", "to": "2026-09"}
    ).json()
    assert pay["kind"] == "income" and pay["months"][0]["cents"] == 1036240


def test_plan_tab(samples, admin: TestClient) -> None:
    p = admin.get("/api/cashflow/plan").json()
    assert p["source_run_id"] == RUN
    (oct_,) = p["months"]
    assert (oct_["month"], oct_["status"], oct_["spending_cents"]) == ("2026-10", "current", 860000)
    assert sum(g["cents"] for g in oct_["by_group"]) == oct_["spending_cents"]
    measures = {m["key"]: m["measure"] for m in oct_["mapping"]}
    assert measures == {
        "housing": "group",
        "lifestyle": "group",
        "plan-installments": "plan_installments",
        "saved-invested": "saved_invested",
        "services": "group",
        "transportation": "group",
    }
    lines = {x["key"]: x for x in oct_["vs_actual"]["lines"]}
    # Nothing is billed in Oct yet (no Oct statements).
    assert lines["plan-installments"]["actual_cents"] == 0
    assert [e["key"] for e in p["events"]] == [
        "plan-sofa-ends",
        "device-plan-ends",
        "trim-dining",  # format 1.4 sample: a target event
        "switch-phone-plan",  # and a suggested one
    ]
    assert p["events"][0]["delta_cents"] == 10500 and p["events"][0]["upcoming"] is True
    # Format 1.4: each change says how sure it is.
    assert {e["key"]: e["status"] for e in p["events"]} == {
        "plan-sofa-ends": "confirmed",
        "device-plan-ends": "expected",
        "trim-dining": "target",
        "switch-phone-plan": "suggested",
    }
    assert [(x["key"], x["current"]) for x in p["phases"]] == [
        ("base", True),
        ("plans-done", False),
    ]
    assert p["phases"][1]["surplus_cents"] == 58000


def test_plan_groups_list_their_bills(samples, db, admin: TestClient) -> None:
    """Gap audit C2: each plan group's bills for the month, then everything else."""
    (oct_,) = admin.get("/api/cashflow/plan").json()["months"]
    bills = rec.plan_lines(db, "2026-10")
    groups = {g["key"]: g for g in oct_["by_group"]}
    for g in oct_["by_group"]:
        if g["key"] != "plan-installments":
            assert g["lines"] == bills.get(g["key"], [])
        assert g["cents"] == sum(x["cents"] for x in g["lines"]) + g["other_cents"]
    housing = groups["housing"]
    assert [x["key"] for x in housing["lines"]] == [
        "mortgage", "city-power", "lawn-care", "fibernet-internet", "town-water",
    ]  # fmt: skip
    assert housing["lines"][0] == {
        "key": "mortgage",
        "name": "Pinecrest Mortgage",
        "cents": 150_000,
        "change": None,
        "previous_cents": None,
    }
    assert housing["other_cents"] == 360_000 - 187_499
    # Transfers count in saved and invested.
    assert [x["key"] for x in groups["saved-invested"]["lines"]] == ["savings-transfer"]
    # Installments never list bills, and a group with no bills is all everything else.
    for key in ("plan-installments", "transportation"):
        assert groups[key]["lines"] == []
        assert groups[key]["other_cents"] == groups[key]["cents"]
    # Paused and ended items never appear.
    shown = {x["key"] for g in oct_["by_group"] for x in g["lines"]}
    assert not shown & {"house-cleaning", "reelhouse"}


def test_plan_lines_when_the_bills_are_more_than_the_plan(samples, db) -> None:
    _add_plan_run(db, "2026-08", AUG_PLAN, "10000.00")
    aug = next(m for m in cf.plan_page(db)["months"] if m["month"] == "2026-08")
    groups = {g["key"]: g for g in aug["by_group"]}
    housing = groups["housing"]
    assert housing["lines"] == rec.plan_lines(db, "2026-08")["housing"]
    assert housing["cents"] == 180_000
    assert housing["other_cents"] == 180_000 - sum(x["cents"] for x in housing["lines"]) < 0
    # An unmapped plan group has no bills.
    assert groups["travel-fund"]["lines"] == []
    assert groups["travel-fund"]["other_cents"] == 5000


def test_plan_groups_say_what_is_in_them(samples, admin: TestClient) -> None:
    """E5: the top 3 bills, or the categories when the bills are under half."""
    oct_ = next(
        m for m in admin.get("/api/cashflow/plan").json()["months"] if m["month"] == "2026-10"
    )
    groups = {g["key"]: g for g in oct_["by_group"]}
    assert groups["housing"]["contents"] == {
        "kind": "bills",
        "names": ["Pinecrest Mortgage", "City Power and Gas", "Green Thumb Lawn Care"],
        "more": 2,
    }
    # One small bill in a big group: the categories say more.
    assert groups["lifestyle"]["lines"] != []
    assert groups["lifestyle"]["contents"] == {
        "kind": "categories",
        "names": ["Groceries", "Dining", "Online shopping"],
        "more": 5,
    }
    assert groups["saved-invested"]["contents"] == {
        "kind": "transfers",
        "names": ["Transfer to Harbor Savings"],
        "more": 0,
    }
    assert groups["transportation"]["contents"]["kind"] == "categories"
    assert groups["plan-installments"]["contents"] is None


def test_group_contents_rules(samples, db) -> None:
    lab = cf.labels(db)
    lines = [{"key": k, "name": k.title(), "cents": 100} for k in ("b", "a", "d", "c")]
    # Input order (plan_lines sorts), capped at 3.
    assert cf.group_contents(lab, "housing", lines, 800) == {
        "kind": "bills",
        "names": ["B", "A", "D"],
        "more": 1,
    }
    # Under half of the group: its categories in list order.
    assert cf.group_contents(lab, "housing", lines, 801)["kind"] == "categories"
    assert cf.group_contents(lab, "plan-installments", lines, 400) is None
    assert cf.group_contents(lab, "saved-invested", lines, 400)["kind"] == "transfers"
    # No spending categories to name: the lines after all, else nothing.
    assert cf.group_contents(lab, "saved-invested", lines, 10_000)["kind"] == "transfers"
    assert cf.group_contents(lab, "saved-invested", [], 400) is None
    assert cf.group_contents(lab, "no-such-group", [], 400) is None
    assert cf.group_contents(lab, "not-spending", [], 400) is None
    db.execute("UPDATE categories SET active = 0 WHERE category_key = 'parking'")
    names = cf.group_contents(cf.labels(db), "transportation", [], 400)["names"]
    assert "Parking" not in names


def test_plan_lines_stay_out_of_the_ai(samples, db) -> None:
    from app.ai import facts

    _add_plan_run(db, "2026-08", AUG_PLAN, "10000.00")
    built = facts.build(db, None)
    keys = [k for _, _, row in built for k in _keys(row)]
    assert "other_cents" not in keys and "contents" not in keys
    # No plan bill line in any fact.
    assert not [row for _, _, row in built if _has_bill_line(row)]


def _keys(obj) -> list[str]:
    if isinstance(obj, dict):
        return list(obj) + [k for v in obj.values() for k in _keys(v)]
    if isinstance(obj, list):
        return [k for v in obj for k in _keys(v)]
    return []


def _has_bill_line(obj) -> bool:
    if isinstance(obj, dict):
        line = {"key", "name", "cents", "change", "previous_cents"}
        return set(obj) == line or any(map(_has_bill_line, obj.values()))
    if isinstance(obj, list):
        return any(map(_has_bill_line, obj))
    return False


def test_income_tab(samples, admin: TestClient) -> None:
    i = admin.get("/api/income", params={"from": "2026-09", "to": "2026-09"}).json()
    assert [s["key"] for s in i["streams"]] == ["alex-pay", "sam-pay"]
    assert i["streams"][0]["expected_net_cents"] == 293120 and i["streams"][0]["active"]
    (sep,) = i["months"]
    assert sep["income_cents"] == INCOME["2026-09"]
    assert sep["streams"] == [
        {"key": "alex-pay", "deposits_cents": 586240, "deposit_count": 2},
        {"key": "sam-pay", "deposits_cents": 450000, "deposit_count": 2},
    ]
    assert sep["other_income_cents"] == 29  # checking interest
    assert sep["paychecks"] == [
        {"person": "alex", "person_label": "Alex", "count": 2, "gross_cents": 840000,
         "net_cents": 586240},
        {"person": "sam", "person_label": "Sam", "count": 2, "gross_cents": 600000,
         "net_cents": 450000},
    ]  # fmt: skip
    assert [p["pay_date"] for p in i["paychecks"]] == [
        "2026-09-04", "2026-09-15", "2026-09-18", "2026-09-30",
    ]  # fmt: skip
    march = admin.get("/api/income", params={"from": "2026-03", "to": "2026-03"}).json()
    alex = next(p for p in march["months"][0]["paychecks"] if p["person"] == "alex")
    assert alex["count"] == 3  # two regular advices and the bonus


# --------------------------------------------------------------------------
# Plan vs actual and the Budget page
# --------------------------------------------------------------------------

AUG_PLAN = [
    ("housing", "1800.00"),
    ("lifestyle", "700.00"),
    ("plan-installments", "180.00"),
    ("saved-invested", "700.00"),
    ("travel-fund", "50.00"),
]


def test_plan_vs_actual_mapping(samples, db) -> None:
    _add_plan_run(db, "2026-08", AUG_PLAN, "10000.00")
    c = cf.cashflow(db, "2026-08", "2026-08")
    (pva,) = c["plan_vs_actual"]
    assert pva["planned_spending_cents"] == 343000
    # Plan basis: spending + saved and invested, with the sofa's 5.00 fee moved
    # out of Fees and into Plan It installments, which billed 180.00
    # (sofa 100 + 5 fee, device 75).
    assert pva["actual_spending_cents"] == AUG_PLAN_BASIS
    assert pva["diff_cents"] == pva["actual_spending_cents"] - pva["planned_spending_cents"]
    assert pva["diff_cents"] == 39239 and pva["status"] == "over"
    assert _line_sum(pva) == pva["actual_spending_cents"]
    lines = {x["key"]: x for x in pva["lines"]}
    assert lines["housing"] == {
        "key": "housing",
        "label": "Housing and utilities",
        "measure": "group",
        "plan_cents": 180000,
        "actual_cents": 176276,
        "diff_cents": -3724,
        "status": "on_target",
    }
    assert (lines["lifestyle"]["actual_cents"], lines["lifestyle"]["status"]) == (81490, "over")
    assert lines["plan-installments"]["actual_cents"] == 18000
    assert lines["saved-invested"]["actual_cents"] == 70000
    assert lines["travel-fund"]["measure"] == "unmapped"
    assert lines["travel-fund"]["actual_cents"] is None
    unplanned = [x["key"] for x in pva["lines"] if x["measure"] == "unplanned"]
    assert unplanned == ["discretionary", "kids", "services"]  # Fees held only the plan fee


def _line_sum(pva: dict) -> int:
    return sum(x["actual_cents"] or 0 for x in pva["lines"])


def test_plan_vs_actual_and_budget_add_up(samples, db) -> None:
    """Every month: the lines add up to the total, on Cash Flow and on Budget.
    A plan in the spending plan counts by what it billed, never at purchase."""
    for month in ("2026-02", "2026-04", "2026-06", "2026-08"):
        _add_plan_run(db, month, AUG_PLAN, "10000.00")
    c = cf.cashflow(db, "2026-01", "2026-09")
    pvas = {p["month"]: p for p in c["plan_vs_actual"]}
    for month, pva in pvas.items():
        assert _line_sum(pva) == pva["actual_spending_cents"], month
        b = budgets_page(db, month)
        assert sum(g["actual_cents"] for g in b["groups"]) == b["totals"]["actual_cents"], month
        assert b["totals"]["actual_cents"] == pva["actual_spending_cents"], month
        for g in b["groups"]:
            if g["categories"]:
                assert sum(x["actual_cents"] for x in g["categories"]) == g["actual_cents"]
    by_month = {m["month"]: m for m in c["months"]}
    # April: the 600.00 sofa purchase is on Plan It, so it leaves its group.
    apr = pvas["2026-04"]
    spent = by_month["2026-04"]["spending_cents"] + by_month["2026-04"]["saved_invested_cents"]
    assert apr["actual_spending_cents"] == spent - 60000 + 7500  # device billed 75.00
    assert {x["key"]: x["actual_cents"] for x in apr["lines"]}["plan-installments"] == 7500
    # Feb: the 900.00 device purchase leaves its group the same way.
    feb = pvas["2026-02"]
    spent = by_month["2026-02"]["spending_cents"] + by_month["2026-02"]["saved_invested_cents"]
    assert feb["actual_spending_cents"] == spent - 90000 + 7500
    # The sofa's first bill is Jun 10, so it adds nothing before June.
    jun = {x["key"]: x["actual_cents"] for x in pvas["2026-06"]["lines"]}
    assert jun["plan-installments"] == 18000


def test_budgets_page(samples, db, admin: TestClient) -> None:
    b = admin.get("/api/budgets").json()
    assert (b["month"], b["complete"], b["note"]) == ("2026-08", True, None)
    assert b["plan_source"] is None and b["totals"]["plan_cents"] is None
    assert b["totals"]["actual_cents"] == AUG_PLAN_BASIS
    assert sum(g["actual_cents"] for g in b["groups"]) == b["totals"]["actual_cents"]
    assert {g["status"] for g in b["groups"]} == {"no_plan"}
    assert b["chat_budgets"] == [] and b["tracked_cards"]["months"][0]["cents"] == 7714
    tracked = b["tracked_cards"]["cards"][0]
    assert (tracked["avg_cents"], b["tracked_cards"]["avg_months"]) == (7714, 1)
    assert tracked["last_payment_date"] == "2026-08-24"

    _add_plan_run(db, "2026-08", AUG_PLAN, "10000.00")
    b = budgets_page(db, "2026-08")
    groups = {g["key"]: g for g in b["groups"]}
    assert [g["key"] for g in b["groups"]][:5] == [k for k, _ in AUG_PLAN]
    assert groups["lifestyle"]["remaining_cents"] == -11490  # 700.00 planned, 814.90 spent
    assert groups["lifestyle"]["status"] == "over"
    assert sum(c["actual_cents"] for c in groups["lifestyle"]["categories"]) == 81490
    assert groups["saved-invested"]["kind"] == "saved_invested"
    assert groups["plan-installments"]["actual_cents"] == 18000
    assert groups["kids"]["plan_cents"] is None and groups["kids"]["status"] == "no_plan"
    assert b["totals"] == {
        "plan_cents": 343000,
        "chat_plan_cents": 343000,  # no edits: the chat's sum
        "actual_cents": 382239,
        "remaining_cents": -39239,
    }

    oct_ = admin.get("/api/budgets", params={"month": "2026-10"}).json()
    assert (oct_["complete"], oct_["note"]) == (False, "Statements not in yet")
    groups = {g["key"]: g for g in oct_["groups"]}
    assert groups["housing"]["plan_cents"] == 360000 and groups["housing"]["actual_cents"] == 0
    dining = next(c for c in groups["lifestyle"]["categories"] if c["category_key"] == "dining")
    assert (dining["target_cents"], dining["actual_cents"]) == (40000, 0)
    chat = {x["key"]: x for x in oct_["chat_budgets"]}
    assert chat["sams-cards"]["period_basis"] == "card_cycle"
    assert chat["sams-cards"]["cycle_note"] == "Cycle closes on the 22nd"
    assert chat["sams-cards"]["reported_actual_cents"] == 21250
    assert chat["sams-cards"]["statements_needed"] == ["Cobalt card"]
    assert chat["sams-cards"]["scope_cards"] == ["Cobalt card"]
    assert oct_["statements_needed"] == ["Cobalt card"]


# --------------------------------------------------------------------------
# Coverage and complete months
# --------------------------------------------------------------------------


def _cells(grid: dict, key: str) -> dict[str, str]:
    acct = next(a for a in grid["accounts"] if a["account_key"] == key)
    return {c["month"]: c["status"] for c in acct["cells"]}


def test_coverage_grid(samples, admin: TestClient) -> None:
    g = admin.get("/api/statements/coverage").json()
    assert g["months"][0] == "2026-01" and g["months"][-1] == "2026-10"
    assert g["complete_months"] == [f"2026-{m:02d}" for m in range(1, 9)]
    assert g["latest_complete_month"] == "2026-08"
    savings = _cells(g, "harbor-savings")  # quarterly
    assert [m for m, s in savings.items() if s == "present"] == ["2026-03", "2026-06", "2026-09"]
    assert {s for m, s in savings.items() if m not in ("2026-03", "2026-06", "2026-09")} == {
        "not_expected"
    }
    store = _cells(g, "bramble-store-card")  # tracked by payments
    assert store["2026-09"] == "present"
    assert {s for m, s in store.items() if m != "2026-09"} == {"tracked"}
    old = _cells(g, "old-store-card")  # closed Jul 20
    assert old["2026-07"] == "present" and old["2026-08"] == old["2026-10"] == "not_expected"
    cobalt = _cells(g, "cobalt-card")  # opened May 1
    assert cobalt["2026-04"] == "not_expected" and cobalt["2026-05"] == "present"
    assert _cells(g, "family-home")["2026-09"] == "not_expected"  # cadence none
    summit = next(a for a in g["accounts"] if a["account_key"] == "summit-card")
    assert (summit["covered_from"], summit["covered_through"]) == ("2025-12-11", "2026-09-10")
    assert _cells(g, "summit-card")["2026-10"] == "not_expected"  # closes Oct 10, not due yet
    assert not any(
        s == "missing" for a in g["accounts"] for s in _cells(g, a["account_key"]).values()
    )
    short = admin.get("/api/statements/coverage", params={"from": "2026-08", "to": "2026-09"})
    assert short.json()["months"] == ["2026-08", "2026-09"]
    assert admin.get("/api/statements/coverage", params={"from": "2022-01"}).status_code == 422


def test_coverage_after_the_grace_days(samples, db, monkeypatch) -> None:
    set_today(monkeypatch, date(2026, 10, 26))
    g = coverage.grid(db)
    assert _cells(g, "summit-card")["2026-10"] == "missing"  # closed Oct 10, 16 days ago
    assert _cells(g, "maple-joint-checking")["2026-10"] == "not_expected"
    missing = coverage.missing_cells(db)
    assert missing == [{"account_key": "summit-card", "name": "Summit card", "month": "2026-10"}]


def test_coverage_gap_quarterly_missing_and_unreconciled(samples, db) -> None:
    db.execute("DELETE FROM statements WHERE statement_key = 'orchard-card:2026-05-31'")
    db.execute("DELETE FROM statements WHERE statement_key = 'harbor-savings:2026-06-30'")
    db.execute(
        "UPDATE statements SET reconciled = 0 WHERE statement_key = ?",
        ("maple-joint-checking:2026-03-31",),
    )
    g = coverage.grid(db)
    orchard = next(a for a in g["accounts"] if a["account_key"] == "orchard-card")
    assert (orchard["covered_through"], orchard["gap_after"]) == ("2026-04-30", "2026-04-30")
    assert _cells(g, "orchard-card")["2026-05"] == "missing"
    assert _cells(g, "harbor-savings")["2026-06"] == "missing"  # quarterly, expected in June
    # Savings is not in cash flow, so its gap never blocks a month.
    assert g["complete_months"] == ["2026-01", "2026-02", "2026-04"]
    assert g["latest_complete_month"] == "2026-04"


def test_closed_and_inactive_accounts(samples, db) -> None:
    state = coverage.coverage_state(db)
    old = state.coverage["old-store-card"]
    assert old.covered_through == "2026-07-20"  # last statement Jul 15, closed Jul 20
    assert state.month_complete("2026-07")
    db.execute("UPDATE accounts SET closed_on = NULL WHERE account_key = 'old-store-card'")
    state = coverage.coverage_state(db)
    # Inactive with no closed_on: needed only up to its last statement (Jul 15).
    assert state.coverage["old-store-card"].covered_through == "2026-07-15"
    assert state.month_complete("2026-07") is True
    assert state.month_complete("2026-08") is True
    db.execute("UPDATE accounts SET active = 1 WHERE account_key = 'old-store-card'")
    assert coverage.coverage_state(db).month_complete("2026-07") is False
    assert state.month_complete("2026-10") is False  # this month is never complete


def test_dormant_card_with_no_new_statements(samples, db, admin: TestClient) -> None:
    """A dormant card is needed only through its last statement, and the grid agrees."""
    assert coverage.coverage_state(db).latest_complete_month() == "2026-08"
    db.execute("UPDATE accounts SET dormant = 1 WHERE account_key = 'orchard-card'")
    db.execute(
        "DELETE FROM statements WHERE account_key = 'orchard-card' AND period_end > '2026-05-31'"
    )
    state = coverage.coverage_state(db)
    assert state.latest_complete_month() == "2026-08"
    grid = admin.get("/api/statements/coverage").json()
    cells = _cells(grid, "orchard-card")
    assert cells["2026-05"] == "present"
    assert {cells[m] for m in ("2026-06", "2026-07", "2026-08")} == {"not_expected"}
    assert not [m for m in coverage.missing_cells(db) if m["account_key"] == "orchard-card"]
    # A gap before the last statement is still missing, and still blocks.
    db.execute(
        "DELETE FROM statements WHERE account_key = 'orchard-card' AND period_end LIKE '2026-03%'"
    )
    state = coverage.coverage_state(db)
    assert state.month_complete("2026-03") is False
    assert [
        m["month"] for m in coverage.missing_cells(db) if m["account_key"] == "orchard-card"
    ] == ["2026-03"]


# --------------------------------------------------------------------------
# Checks additions
# --------------------------------------------------------------------------


def test_checks_lists_on_samples(samples, member: TestClient) -> None:
    c = member.get("/api/checks").json()
    assert c["unpaired_transfers"] == []
    assert c["card_payments_no_statement"] == []
    assert c["missing_statements"] == []
    assert c["payment_basis_watch"] == []
    assert c["latest_complete_month"] == "2026-08"
    assert c["plan_mapping_month"] == "2026-10"
    assert [m["measure"] for m in c["plan_mapping"]] == [
        "group", "group", "plan_installments", "saved_invested", "group", "group",
    ]  # fmt: skip
    aug, sep = c["cashflow"]
    assert aug["notes"] == [
        "Aug 2026: groups changed in 1.3. This month's actual groups have no "
        "Transportation, so Lifestyle, Insurance and Transportation are compared as one line.",
        "Aug 2026 counts card payments on a payment basis.",
    ]
    assert sep["notes"][:3] == [
        "Sep 2026 is not complete in the app.",
        "Summit card statements reach only Sep 10.",
        "Cobalt card statements reach only Sep 22.",
    ]


def _chat_month(month: str, a: cf.MonthActual, groups: dict[str, int]) -> CashflowActual:
    def m(cents: int) -> str:
        return f"{'-' if cents < 0 else ''}{abs(cents) // 100}.{abs(cents) % 100:02d}"

    return CashflowActual(
        month=month,
        income=m(a.income),
        spending=m(a.spending),
        by_group=[GroupAmount(key=k, amount=m(v)) for k, v in sorted(groups.items())],
    )


def test_checks_fold_three_groups_for_runs_before_13(samples, db) -> None:
    """Phase 7: a run with no `transportation` group was sent before the chat's 1.3
    update, when fuel was Lifestyle and registration Insurance. Checks compares
    the three groups as one line, so the move shows no false gap."""
    a = cf.actuals(cf.load_rows(db, "2026-07", "2026-07"), ["2026-07"])["2026-07"]
    fuel = a.by_group["transportation"]
    assert fuel > 0  # July has a fuel row, now in Transportation
    old = {k: v for k, v in a.by_group.items() if v and k != "transportation"}
    old["lifestyle"] += fuel  # how the chat counted it before 1.3
    state = coverage.coverage_state(db)
    (july,) = checks_p4.cashflow_checks(db, [_chat_month("2026-07", a, old)], state)
    lines = {g["key"]: g for g in july["by_group"]}
    folded = lines[checks_p4.FOLDED_KEY]
    assert folded["label"] == "Lifestyle, Insurance, Transportation"
    assert folded["status"] == "match"
    assert july["notes"][0] == (
        "Jul 2026: groups changed in 1.3. This month's actual groups have no "
        "Transportation, so Lifestyle, Insurance and Transportation are compared as one line."
    )
    want = sum(a.by_group.get(k, 0) for k in checks_p4.FOLDED_GROUPS)
    assert (folded["app_cents"], folded["chat_cents"]) == (want, want)
    assert not {"lifestyle", "insurance", "transportation"} & set(lines)
    assert all(g["status"] == "match" for g in july["by_group"])

    # A run from the 1.3 chat has the group, so each group keeps its own line.
    new = {k: v for k, v in a.by_group.items() if v}
    (july,) = checks_p4.cashflow_checks(db, [_chat_month("2026-07", a, new)], state)
    keys = {g["key"] for g in july["by_group"]}
    assert {"lifestyle", "transportation"} <= keys and checks_p4.FOLDED_KEY not in keys
    assert all(g["status"] == "match" for g in july["by_group"])
    assert not any("groups changed" in n for n in july["notes"])


def test_checks_page_folds_an_old_style_run(samples, db, member: TestClient) -> None:
    """The same through /api/checks: the sample run's cash flow rewritten the way
    the chat sent it before 1.3 (no transportation group, its amount in Lifestyle)."""
    (raw,) = db.execute(
        "SELECT json FROM run_sections WHERE run_id = ? AND section = 'cashflow_actual'", (RUN,)
    ).fetchone()
    months = json.loads(raw)
    for m in months:
        groups = {g["key"]: to_cents(g["amount"]) for g in m["by_group"]}
        moved = groups.pop("transportation", 0)
        groups["lifestyle"] = groups.get("lifestyle", 0) + moved
        m["by_group"] = [
            {"key": k, "amount": f"{v // 100}.{v % 100:02d}"} for k, v in sorted(groups.items())
        ]
    db.execute(
        "UPDATE run_sections SET json = ? WHERE run_id = ? AND section = 'cashflow_actual'",
        (json.dumps(months), RUN),
    )
    c = member.get("/api/checks").json()
    for month in c["cashflow"]:
        keys = [g["key"] for g in month["by_group"]]
        assert checks_p4.FOLDED_KEY in keys
        assert not {"lifestyle", "insurance", "transportation"} & set(keys)
        assert any("groups changed in 1.3" in n for n in month["notes"])
    sep = c["cashflow"][1]
    folded = next(g for g in sep["by_group"] if g["key"] == checks_p4.FOLDED_KEY)
    assert folded["status"] == "match"  # the 24.50 of parking and toll is inside it


def test_checks_fold_each_month_independently(samples, db) -> None:
    months = ["2026-07", "2026-09"]
    acts = cf.actuals(cf.load_rows(db, months[0], months[-1]), months)
    old = {k: v for k, v in acts[months[0]].by_group.items() if v}
    old["lifestyle"] += old.pop("transportation")
    new = {k: v for k, v in acts[months[1]].by_group.items() if v}
    assert new["transportation"] > 0
    july, september = checks_p4.cashflow_checks(
        db,
        [
            _chat_month(months[0], acts[months[0]], old),
            _chat_month(months[1], acts[months[1]], new),
        ],
        coverage.coverage_state(db),
    )
    assert checks_p4.FOLDED_KEY in {g["key"] for g in july["by_group"]}
    assert all(g["status"] == "match" for g in july["by_group"])
    assert any("groups changed in 1.3" in n for n in july["notes"])
    assert "transportation" in {g["key"] for g in september["by_group"]}
    assert checks_p4.FOLDED_KEY not in {g["key"] for g in september["by_group"]}
    assert all(g["status"] == "match" for g in september["by_group"])
    assert not any("groups changed in 1.3" in n for n in september["notes"])


def test_unpaired_transfer_leg(samples, db) -> None:
    db.execute(
        """UPDATE transactions SET removed_at = 'x'
           WHERE account_key = 'lakeview-brokerage' AND txn_date = '2026-08-05'"""
    )
    rows = checks_p4.unpaired_transfers(db, coverage.coverage_state(db))
    assert [(r["account_key"], r["txn_date"], r["counterparty_account_key"]) for r in rows] == [
        ("maple-joint-checking", "2026-08-05", "lakeview-brokerage")
    ]
    assert rows[0]["amount_cents"] == -50000


def test_watch_minimum_payments(samples, db) -> None:
    db.execute(
        "UPDATE accounts SET min_payment_cents = 4500 WHERE account_key = 'bramble-store-card'"
    )
    w = checks_p4.payment_basis_watch(db, coverage.coverage_state(db))
    assert [(x["rule"], x["month"]) for x in w] == [("min-payments", "2026-08")]
    assert w[0]["message"] == (
        "Bramble store card had only minimum-size payments in Jul 2026 and Aug 2026."
    )


def test_watch_no_payment(samples, db) -> None:
    db.execute(
        """UPDATE transactions SET removed_at = 'x'
           WHERE counterparty_account_key = 'bramble-store-card' AND txn_date >= '2026-08-01'"""
    )
    db.execute("DELETE FROM statements WHERE account_key = 'bramble-store-card'")
    w = checks_p4.payment_basis_watch(db, coverage.coverage_state(db))
    assert [(x["rule"], x["month"], x["severity"]) for x in w] == [
        ("no-payment-45d", None, "high"),  # last payment Jul 24, checking is in to Sep 30
        ("no-payment-month", "2026-08", "medium"),  # Sep follows a month with nothing
    ]
    assert w[0]["message"] == "No payment to Bramble store card since Jul 24, through Sep 30."
    for x in w:  # messages never carry amounts
        assert "44.15" not in x["message"] and "4415" not in x["message"]


def test_watch_payment_to_dormant_card(samples, db) -> None:
    db.execute("UPDATE accounts SET dormant = 1 WHERE account_key = 'bramble-store-card'")
    w = checks_p4.payment_basis_watch(db, coverage.coverage_state(db))
    assert {x["rule"] for x in w} == {"dormant-payment"}
    assert [x["month"] for x in w] == [f"2026-{m:02d}" for m in range(1, 10)]


def test_checks_cash_flow_explains_gaps(samples, db) -> None:
    db.execute(
        "DELETE FROM statements WHERE account_key = 'summit-card' AND period_end >= '2026-08-01'"
    )
    key = db.execute(
        "SELECT txn_key FROM transactions WHERE description = 'ATM WITHDRAWAL' "
        "AND txn_date = '2026-08-09'"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO transaction_overrides (txn_key, hidden, updated_at) VALUES (?, 1, 'now')",
        (key,),
    )
    c = checks(db)
    aug = c["cashflow"][0]
    assert aug["spending"]["status"] == "differs"
    assert aug["card_no_statement_cents"] > 0 and aug["edited_out_count"] == 1
    assert "Aug 2026 counts card payments with no statement as spending." in aug["notes"]
    assert "Aug 2026 has 1 rows hidden or left out by an edit in the app." in aug["notes"]
    assert c["card_payments_no_statement"][0]["account_name"] == "Summit card"
