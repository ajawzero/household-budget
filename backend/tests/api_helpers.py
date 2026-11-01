"""Helpers for the API, auth, log and scheduler tests.

Imported as a plain module (`from api_helpers import ...`), like bad_cases.
The fixtures that use them (prod, dev, admin, member, samples) are in conftest.py.
Test users are fictional: `alex` (admin) and `sam` (member).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import clock
from app.auth.users import create_user
from app.config import Settings, get_settings
from app.db.connect import write_connection
from app.db.migrate import migrate
from app.importer.runner import import_path
from app.main import create_app

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "samples" / "data"
TODAY = date(2026, 10, 20)

PROD_URL = "https://budget.home.example"
PROD_ORIGIN = PROD_URL
DEV_URL = "http://localhost:8000"
DEV_ORIGIN = "http://localhost:5173"

ADMIN, ADMIN_PW = "alex", "admin-test-pass-1"
MEMBER, MEMBER_PW = "sam", "member-test-pass-2"


def app_for(monkeypatch: pytest.MonkeyPatch, *, dev: bool, **env: str) -> FastAPI:
    """A fresh app over the `settings` fixture paths, in dev or prod mode."""
    monkeypatch.setenv("BUDGET_DEV", "1" if dev else "0")
    monkeypatch.delenv("BUDGET_ALLOWED_HOSTS", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return create_app(get_settings(), start_scheduler=False)


def add_users(settings: Settings) -> None:
    migrate(settings=settings)
    with write_connection(settings.db_path) as conn:
        create_user(conn, ADMIN, "Alex Example", ADMIN_PW, "admin")
        create_user(conn, MEMBER, "Sam Example", MEMBER_PW, "member")


def import_samples(settings: Settings, source: Path = SAMPLES) -> None:
    """Copy the sample runs (or the runs in `source`) into the import folder and import them."""
    for run in source.iterdir():
        if run.is_dir():
            shutil.copytree(run, settings.import_dir / run.name)
    results = import_path(settings.import_dir, settings=settings, today=TODAY)
    assert all(r.ok for r in results), [(r.run_id, r.status) for r in results]


def login(client: TestClient, username: str, password: str, origin: str) -> httpx.Response:
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": origin},
    )


def client_for(app: FastAPI, *, dev: bool) -> Iterator[TestClient]:
    with TestClient(app, base_url=DEV_URL if dev else PROD_URL) as c:
        yield c


# The demo tests' "real" household (demo plan section 9): the sample import, so every demo
# URL names a real row too, with canary names and amounts and the rows the samples lack.
# A demo response must never hold any of it. The check is case-sensitive: the house price
# index series ids hold a lowercase `austin` in every database.
CANARY = "Austin"
CANARY_CENTS = 7_340_291
CANARY_ID = "a" * 32  # a thread and a trip plan; the samples have neither
_NAME_COLUMNS = (
    ("accounts", "display_name"),
    ("transactions", "description"),
    ("transactions", "merchant"),
    ("trips", "name"),
    ("trip_items", "label"),
    ("utility_bills", "provider"),
    ("utility_bill_credits", "label"),
    ("people", "display_name"),
    ("holdings", "holding_name"),
    ("users", "display_name"),
)


def plant_canaries(settings: Settings, demo_user_id: int) -> None:
    """Turn an imported sample database into the canary one. Keys are made from the text
    (`importer/keys.py`), so the text changes here, after the import, and every key stays."""
    at = "2026-10-19T17:00:00+00:00"
    with write_connection(settings.db_path) as conn:
        for table, column in _NAME_COLUMNS:
            conn.execute(f"UPDATE {table} SET {column} = '{CANARY} ' || {column}")
        # The chat's sections hold the names the pages show (programs, benefits, budgets).
        for old, new in (
            ("Sample Town", CANARY),
            ('"name":"', f'"name":"{CANARY} '),
            ('"display_name":"', f'"display_name":"{CANARY} '),
        ):
            conn.execute("UPDATE run_sections SET json = replace(json, ?, ?)", (old, new))
        conn.execute(
            """UPDATE transactions SET amount_cents = ? WHERE txn_key IN (
                 SELECT txn_key FROM transactions WHERE account_key = 'harbor-checking'
                 ORDER BY txn_date DESC LIMIT 3)""",
            (-CANARY_CENTS,),
        )
        conn.execute(
            "UPDATE balances SET balance_cents = ? WHERE account_key = 'harbor-checking'",
            (CANARY_CENTS,),
        )
        # A house price curve, so the seeded "FHFA HPI, Austin, TX" label shows.
        for start, level in (("2026-01-01", 500), ("2026-04-01", 510)):
            conn.execute(
                """INSERT INTO index_values (series_id, period_start, freq, value_micro,
                       fetched_at) VALUES ('fhfa:exp:austin:sa', ?, 'q', ?, ?)""",
                (start, level * 1_000_000, at),
            )
        # A real user with the demo user's id: a real read for the demo user finds rows.
        conn.execute(
            """INSERT INTO users (id, username, display_name, password_hash, role, active,
                   created_at) VALUES (?, 'canary', ?, '!', 'member', 1, ?)""",
            (demo_user_id, f"{CANARY} Canary", at),
        )
        for thread_id, user_id in ((CANARY_ID, demo_user_id), ("b" * 32, 1)):
            conn.execute(
                """INSERT INTO ai_threads (thread_id, user_id, created_at, updated_at, model,
                       effort, title) VALUES (?, ?, ?, ?, 'claude-sonnet-5-5', 'medium', ?)""",
                (thread_id, user_id, at, at, f"{CANARY} question"),
            )
            conn.execute(
                """INSERT INTO ai_messages (thread_id, seq, role, kind, content_json,
                       created_at) VALUES (?, 1, 'user', 'question', ?, ?)""",
                (thread_id, json.dumps([{"type": "text", "text": f"{CANARY}?"}]), at),
            )
        request = {
            "where": f"{CANARY} weekend",
            "month": "2026-11",
            "nights": 2,
            "people": ["alex"],
            "guests": 0,
            "home_airport": None,
            "interests": [],
            "note": "",
            "pace": "balanced",
            "lean": "value",
        }
        conn.execute(
            """INSERT INTO trip_plans (plan_id, created_by, created_at, updated_at, title,
                   status, request_json, trip_key, model)
               VALUES (?, ?, ?, ?, ?, 'ready', ?, NULL, 'claude-opus-5-5')""",
            (CANARY_ID, demo_user_id, at, at, f"{CANARY} weekend", json.dumps(request)),
        )


class FakeClock:
    """Stands in for `app.auth.clock.now` (session expiry, throttles)."""

    def __init__(self) -> None:
        self.now = clock.now()

    def __call__(self):  # noqa: ANN204
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)
