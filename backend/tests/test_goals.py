"""Goals page (format 1.4 goals, docs/plans/format-14.md sections 3.8 and 7): GET /api/goals,
the sum of each goal's account balances today, and the start year kept off the Dashboard
and out of the AI (PLAN 3.8)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import config
from app.ai.facts import ensure_facts
from app.ai.planner import rewards_brief
from app.ai.safe import SAFE_VIEWS
from app.config import Settings
from app.db.connect import connect, read_connection
from app.importer.models import Goal
from app.services.balances import Estimate, LatestBalance, clear_cache, latest_balances
from app.services.goals import goal_out, goals_page

TODAY = date(2026, 10, 20)
GOAL_ONLY = frozenset({"goals", "start_year", "target_date"})


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def _keys(obj: object) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_goals_on_the_samples(samples: Settings, admin: TestClient) -> None:
    r = admin.get("/api/goals")
    assert r.status_code == 200
    page = r.json()
    assert [g["key"] for g in page["goals"]] == ["college-jamie", "emergency-fund"]
    assert page["source_run_id"] == "2026-10-12"
    college, fund = page["goals"]

    conn = connect(samples.db_path)
    try:
        balances = latest_balances(conn, TODAY)
        assert goals_page(conn) == page  # the service is the API body
    finally:
        conn.close()

    now = balances["kids-529"].balance_cents
    assert now is not None and now > 0
    assert college == {
        "key": "college-jamie",
        "name": "College, older child",
        "kind": "college",
        "person_name": "Jamie",
        "start_year": 2034,
        "target_date": None,
        "note": None,
        "target_cents": 12_000_000,
        "now_cents": now,
        "to_go_cents": 12_000_000 - now,
        "reached": False,
        "as_of": "2026-09-30",
        "stale": False,
        "accounts": [
            {
                "account_key": "kids-529",
                "display_name": "Kids 529 (Bluestem)",
                "balance_cents": now,
                "as_of": "2026-09-30",
                "stale": False,
                "missing": False,
                "estimate": None,
            }
        ],
    }
    saved = balances["harbor-savings"].balance_cents
    assert saved is not None
    assert (fund["kind"], fund["person_name"], fund["start_year"]) == ("save_up", None, None)
    assert (fund["target_date"], fund["note"]) == ("2027-12-31", "Six months of bills")
    assert (fund["target_cents"], fund["now_cents"]) == (3_000_000, saved)
    assert fund["to_go_cents"] == 3_000_000 - saved and fund["reached"] is False
    assert [a["account_key"] for a in fund["accounts"]] == ["harbor-savings"]


# ---- goal_out rules, on a fake History ----


class FakeHistory:
    def __init__(self, balances: dict[str, LatestBalance]) -> None:
        self.accounts = {key: {"display_name": f"Name of {key}"} for key in balances}
        self._balances = balances

    def at_end(self, key: str) -> LatestBalance:
        return self._balances[key]


def _goal(accounts: list[str], target: str = "1000.00", **extra: object) -> Goal:
    return Goal.model_validate(
        {"key": "g", "name": "Goal", "kind": "save_up", "accounts": accounts, "target": target}
        | extra
    )


def _bal(key: str, cents: int | None, as_of: str | None = "2026-09-30", **kw) -> LatestBalance:
    source = kw.pop("source", "statement" if cents is not None else None)
    return LatestBalance(key, cents, as_of, source, **kw)


def _out(goal: Goal, *balances: LatestBalance) -> dict:
    h = FakeHistory({b.account_key: b for b in balances})
    return goal_out(goal, h, {"jamie": "Jamie"})  # type: ignore[arg-type]


def test_goal_out_rules() -> None:
    # Two accounts sum. as_of is the oldest date; stale when any one is.
    out = _out(
        _goal(["a", "b"]),
        _bal("a", 40_000, "2026-09-30"),
        _bal("b", 20_000, "2026-08-31", stale=True),
    )
    assert (out["now_cents"], out["to_go_cents"], out["reached"]) == (60_000, 40_000, False)
    assert (out["as_of"], out["stale"]) == ("2026-08-31", True)
    assert [a["display_name"] for a in out["accounts"]] == ["Name of a", "Name of b"]

    # One missing: now is the other, and that account says missing.
    out = _out(_goal(["a", "b"]), _bal("a", 40_000), _bal("b", None, None))
    assert out["now_cents"] == 40_000
    assert [a["missing"] for a in out["accounts"]] == [False, True]

    # All missing: no now, no to go, never a silent 0.
    out = _out(_goal(["a"]), _bal("a", None, None))
    assert (out["now_cents"], out["to_go_cents"], out["reached"]) == (None, None, False)
    assert (out["as_of"], out["stale"]) == (None, False)

    # A card tracked by payments: not counted, not missing.
    out = _out(_goal(["a", "card"]), _bal("a", 40_000), _bal("card", None, None, source="tracked"))
    assert out["now_cents"] == 40_000
    assert out["accounts"][1]["missing"] is False

    # Closed counts 0 (its close date is its as of). Not open yet counts 0 with no date.
    out = _out(
        _goal(["a", "shut", "new"]),
        _bal("a", 40_000),
        _bal("shut", 0, "2026-06-30", source="closed"),
        _bal("new", None, None, opened=False),
    )
    assert out["now_cents"] == 40_000
    assert [a["balance_cents"] for a in out["accounts"]] == [40_000, 0, 0]
    assert [a["missing"] for a in out["accounts"]] == [False, False, False]
    assert out["as_of"] == "2026-06-30"

    # An estimate passes through with its note.
    est = Estimate("price", "2026-09-30", "2026-10-17", "Estimated from prices.")
    out = _out(_goal(["a"]), _bal("a", 45_000, "2026-10-17", source="estimate", estimate=est))
    assert out["accounts"][0]["estimate"] == {
        "kind": "price",
        "based_on": "2026-09-30",
        "through": "2026-10-17",
        "note": "Estimated from prices.",
    }

    # At or above the target: reached, 0 to go.
    out = _out(_goal(["a"], "400.00"), _bal("a", 40_000))
    assert (out["to_go_cents"], out["reached"]) == (0, True)
    out = _out(_goal(["a"], "300.00"), _bal("a", 40_000))
    assert (out["to_go_cents"], out["reached"]) == (0, True)

    # A key gone from the registry: missing, with a humanized name.
    out = _out(_goal(["a", "old-savings"]), _bal("a", 40_000))
    assert out["accounts"][1] == {
        "account_key": "old-savings",
        "display_name": "Old savings",
        "balance_cents": None,
        "as_of": None,
        "stale": False,
        "missing": True,
        "estimate": None,
    }

    # College: the child's name, or the humanized key when the person is not listed.
    college = {"kind": "college", "person": "jamie", "start_year": 2034}
    out = _out(_goal(["a"], **college), _bal("a", 40_000))
    assert (out["person_name"], out["start_year"], out["target_date"]) == ("Jamie", 2034, None)
    out = _out(_goal(["a"], **(college | {"person": "riley-k"})), _bal("a", 40_000))
    assert out["person_name"] == "Riley k"


def test_no_goals_on_a_13_run(samples_v13: Settings, admin: TestClient) -> None:
    assert admin.get("/api/goals").json() == {"goals": [], "source_run_id": None}


def test_goals_need_sign_in(samples: Settings, prod: TestClient) -> None:
    r = prod.get("/api/goals")
    assert (r.status_code, r.json()) == (401, {"detail": "Not signed in."})


def test_a_member_sees_goals(samples: Settings, member: TestClient) -> None:
    assert member.get("/api/goals").status_code == 200


def test_goals_and_the_start_year_stay_off_the_dashboard_and_out_of_the_ai(
    samples: Settings, admin: TestClient
) -> None:
    """PLAN 3.8: the start year shows on the Goals page only. The Dashboard and the AI
    (facts, the trip planner's brief, the Ask SQL views) never read goals."""
    goals = admin.get("/api/goals").json()["goals"]
    assert {g["start_year"] for g in goals} == {2034, None}  # the samples carry one
    names = {g["name"] for g in goals} | {g["key"] for g in goals}

    dash = admin.get("/api/dashboard")
    assert dash.status_code == 200
    assert not GOAL_ONLY & _keys(dash.json())
    assert not any(name in dash.text for name in names)

    ensure_facts(samples.db_path, None)
    with read_connection(samples.db_path) as conn:
        rows = list(conn.execute("SELECT kind, key, json FROM ai_facts"))
        brief = rewards_brief(conn)
        columns = {c["name"] for v in SAFE_VIEWS for c in conn.execute(f"PRAGMA table_info({v})")}
    assert rows
    assert not any("goal" in r["kind"] for r in rows)
    facts = [json.loads(r["json"]) for r in rows]
    assert not GOAL_ONLY & (_keys(facts) | _keys(brief) | columns)
    text = json.dumps(facts) + json.dumps(brief)
    assert not any(name in text for name in names)
    assert not any("goal" in v for v in SAFE_VIEWS)
