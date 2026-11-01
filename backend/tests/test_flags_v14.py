"""Format 1.4 flags on the Flags page (docs/plans/format-14.md sections 3.1 and 7): the
action (left out reads act), dated updates newest first, and info flags in their own
group and count, never overdue."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient
from test_state_v14 import _add_run

from app import config
from app.config import Settings
from app.services.balances import clear_cache

TODAY = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def _check_counts(counts: dict[str, int]) -> None:
    """Severity counts and status counts cover the same flags; info sits outside both."""
    assert counts["high"] + counts["medium"] + counts["low"] == (
        counts["open"] + counts["watching"] + counts["resolved"]
    )


def test_actions_and_updates_on_the_14_sample(samples: Settings, admin: TestClient) -> None:
    r = admin.get("/api/flags")
    assert r.status_code == 200
    page = r.json()
    flags = {f["key"]: f for f in page["flags"]}
    actions = {k: f["action"] for k, f in flags.items()}
    assert (actions["gym-ending"], actions["cloudtunes-credit"]) == ("act", "act")  # left out
    assert actions["fibernet-rate"] == "ask"
    assert actions["health-claim"] == "reimburse"
    assert (actions["kids-college-gift"], actions["savings-milestone"]) == ("info", "info")
    assert [u["on"] for u in flags["bramble-promo-ends"]["updates"]] == [
        "2026-10-10",
        "2026-09-20",
    ]
    # Two notes on one day keep their stored order.
    assert [u["text"] for u in flags["unknown-vendor"]["updates"]] == [
        "Asked the card issuer about the charge",
        "The issuer opened a review",
    ]
    assert [u["on"] for u in flags["old-plan-rollover"]["updates"]] == [
        "2026-10-10",
        "2026-09-15",
        "2026-09-01",
        "2026-08-25",
    ]
    assert flags["roof-repair"]["updates"] == []
    # Its date has passed, but there is nothing to do.
    assert flags["kids-college-gift"]["due_date"] == "2026-10-01"
    assert flags["kids-college-gift"]["overdue"] is False
    assert page["counts"] == {
        "high": 2,
        "medium": 6,
        "low": 6,
        "open": 9,
        "watching": 3,
        "resolved": 2,
        "info": 2,
    }
    _check_counts(page["counts"])
    unresolved = [f["key"] for f in page["flags"] if f["status"] != "resolved"]
    assert unresolved[-2:] == ["kids-college-gift", "savings-milestone"]
    assert [f["key"] for f in page["flags"]][-2:] == ["lake-weekend-costs", "library-fees"]


def _flag(key: str, **kw: object) -> dict:
    return {
        "key": key,
        "severity": "low",
        "status": "open",
        "kind": "other",
        "title": f"Flag {key}",
        "created_on": "2026-09-01",
        **kw,
    }


def test_an_info_flag_is_never_overdue(db: sqlite3.Connection, admin: TestClient) -> None:
    _add_run(
        db,
        "2026-10-12",
        "monthly",
        flags=[
            _flag("a", action="act", due_date="2026-10-01"),
            _flag("b", action="info", due_date="2026-10-01"),
            _flag("c", action="info", status="watching"),
            _flag("d", action="info", status="resolved", resolved_on="2026-10-05"),
        ],
    )
    page = admin.get("/api/flags").json()
    assert [f["key"] for f in page["flags"]] == ["a", "b", "c", "d"]
    overdue = {f["key"]: f["overdue"] for f in page["flags"]}
    assert overdue == {"a": True, "b": False, "c": False, "d": False}
    assert page["counts"] == {
        "high": 0,
        "medium": 0,
        "low": 2,
        "open": 1,
        "watching": 0,
        "resolved": 1,
        "info": 2,
    }
    _check_counts(page["counts"])
