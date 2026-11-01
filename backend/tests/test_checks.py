"""Checks: app numbers vs the chat's on a statement basis (PLAN 8.2, Checks)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import SAMPLES
from fastapi.testclient import TestClient

from app import config
from app.importer.models import to_cents
from app.services.balances import clear_cache, latest_balances
from app.services.checks import MATCH_CENTS, checks, line

TODAY = date(2026, 10, 20)
RUN = "2026-10-12"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def _chat() -> dict:
    return json.loads((SAMPLES / RUN / "snapshot.json").read_text())


def test_line_rule() -> None:
    assert line(1000, 1000)["status"] == "match"
    assert line(1000 + MATCH_CENTS, 1000) == {
        "app_cents": 1100,
        "chat_cents": 1000,
        "diff_cents": 100,
        "status": "match",
    }
    assert line(1000, 1000 + MATCH_CENTS + 1)["status"] == "differs"
    assert line(1000, 1000 + MATCH_CENTS + 1)["diff_cents"] == -101
    assert line(None, 5)["status"] == "chat_only" and line(None, 5)["diff_cents"] is None
    assert line(5, None)["status"] == "app_only"


def test_samples_match_on_a_statement_basis(samples, member: TestClient) -> None:
    r = member.get("/api/checks")
    assert r.status_code == 200
    c = r.json()
    chat = _chat()
    assert (c["run_id"], c["as_of"]) == (RUN, "2026-09-30")
    for name, key in (("net_worth", "value"), ("assets", "assets"), ("liabilities", "liabilities")):
        assert c[name] == {
            "app_cents": to_cents(chat["net_worth"][key]),
            "chat_cents": to_cents(chat["net_worth"][key]),
            "diff_cents": 0,
            "status": "match",
        }, name
    assert c["liabilities"]["app_cents"] > 0  # positive: money owed
    parts = {p["key"]: p for p in c["parts"]}
    assert set(parts) == {p["key"] for p in chat["net_worth"]["parts"]}
    assert {p["status"] for p in c["parts"]} == {"match"}
    assert parts["home-equity"]["label"] == "Home equity"
    assert sum(p["app_cents"] for p in c["parts"]) == c["net_worth"]["app_cents"]
    assert [m["month"] for m in c["cashflow"]] == ["2026-08", "2026-09"]
    # Phase 4: the app's cash flow matches the chat's for both months.
    for got, actual in zip(c["cashflow"], chat["cashflow_actual"], strict=True):
        for name, key in (
            ("income", "income"),
            ("spending", "spending"),
            ("saved_invested", "saved_invested"),
        ):
            cents = to_cents(actual[key])
            assert got[name] == {
                "app_cents": cents,
                "chat_cents": cents,
                "diff_cents": 0,
                "status": "match",
            }, (got["month"], name)
        assert {g["status"] for g in got["by_group"]} == {"match"}
    assert c["cashflow"][0]["complete"] is True and c["cashflow"][1]["complete"] is False
    assert c["notes"][0].startswith("App numbers use the stored balances")
    assert member.get("/api/checks", params={"run_id": RUN}).json() == c


def test_unknown_or_unsuitable_run_is_404(samples, admin: TestClient) -> None:
    for run_id in ("2026-10-14", "2026-10-06", "2030-01-01", "not-a-run"):
        r = admin.get("/api/checks", params={"run_id": run_id})
        assert r.status_code == 404, run_id
        assert r.json() == {"detail": "No imported run with net worth numbers."}


def test_empty_db_is_404(admin: TestClient) -> None:
    assert admin.get("/api/checks").status_code == 404


def test_differences_and_notes(samples, db) -> None:
    """The chat lists the mortgage on its own and is 5.00 off on net worth."""
    chat = _chat()["net_worth"]
    parts = {p["key"]: to_cents(p["amount"]) for p in chat["parts"]}
    mortgage = latest_balances(db, date(2026, 9, 30))["pinecrest-mortgage"].balance_cents
    assert mortgage < 0
    parts["home-equity"] -= mortgage
    parts["mortgage"] = mortgage
    parts["cash-and-misc"] += 500
    changed = {
        **chat,
        "value": f"{(to_cents(chat['value']) + 500) / 100:.2f}",
        "parts": [{"key": k, "amount": f"{v / 100:.2f}"} for k, v in sorted(parts.items())],
    }
    db.execute(
        "UPDATE run_sections SET json = ? WHERE run_id = ? AND section = 'net_worth'",
        (json.dumps(changed), RUN),
    )
    c = checks(db)
    assert c["net_worth"]["diff_cents"] == -500 and c["net_worth"]["status"] == "differs"
    assert c["assets"]["status"] == "match"
    by_key = {p["key"]: p for p in c["parts"]}
    assert by_key["cash-and-misc"]["status"] == "differs"
    assert by_key["home-equity"]["diff_cents"] == mortgage
    assert by_key["mortgage"] == {
        "key": "mortgage",
        "label": "Mortgage",
        "app_cents": None,
        "chat_cents": mortgage,
        "diff_cents": None,
        "status": "chat_only",
    }
    assert c["parts"][-1]["key"] == "mortgage"  # chat-only parts come last
    assert any(
        n == "The chat lists Mortgage on its own. The app counts it inside Home equity."
        for n in c["notes"]
    )
    for n in c["notes"]:  # notes never carry amounts
        assert str(-mortgage)[:5] not in n and f"{-mortgage / 100:,.2f}"[:6] not in n


def test_missing_account_note(samples, db) -> None:
    db.execute(
        "UPDATE accounts SET opened_on = NULL WHERE account_key = 'family-car'",
    )
    db.execute("DELETE FROM balances WHERE account_key = 'family-car'")
    clear_cache()
    c = checks(db)
    car = next(
        r["display_name"]
        for r in db.execute("SELECT display_name FROM accounts WHERE account_key = 'family-car'")
    )
    assert f"{car} has no balance on Sep 30, 2026. The app total leaves it out." in c["notes"]
    assert c["net_worth"]["status"] == "differs"


def test_worked_back_note(samples, db) -> None:
    """On a day inside a card's cycle, its balance is worked back from the later close."""
    chat = {**_chat()["net_worth"], "as_of": "2026-09-05"}
    db.execute(
        "UPDATE run_sections SET json = ? WHERE run_id = ? AND section = 'net_worth'",
        (json.dumps(chat), RUN),
    )
    c = checks(db)
    assert c["as_of"] == "2026-09-05"
    name = db.execute(
        "SELECT display_name FROM accounts WHERE account_key = 'summit-card'"
    ).fetchone()[0]
    assert (
        f"{name}: the balance on Sep 5, 2026 is worked back from the statement that closed "
        "Sep 10, 2026." in c["notes"]
    )
