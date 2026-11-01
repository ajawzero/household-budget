"""Phase 6 review fixes: an approved suggestion on the activity timeline, bounded
bulk ids, and no Anthropic env from the shell inside tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from api_helpers import PROD_ORIGIN

from app.config import get_settings
from app.db.connect import write_connection

H = {"Origin": PROD_ORIGIN}


def _suggest(db_path) -> tuple[int, str]:
    """A pending suggestion for the first uncategorized row, written directly."""
    with write_connection(db_path) as conn:
        (txn_key,) = conn.execute(
            """SELECT v.txn_key FROM v_transactions v JOIN categories c USING (category_key)
               WHERE c.kind = 'uncategorized' AND v.hidden = 0 AND v.pre_history = 0"""
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO category_suggestions (txn_key, suggested_category_key, confidence,
                   model, created_at) VALUES (?, 'dining', 80, 'claude-haiku-5-5', '2026-10-20')""",
            (txn_key,),
        )
        return cur.lastrowid, txn_key


def test_approved_suggestion_shows_on_the_timeline(samples, admin) -> None:
    sid, txn_key = _suggest(get_settings().db_path)
    r = admin.post(f"/api/category-suggestions/{sid}/approve", headers=H)
    assert r.json() == {"changed": 1}
    r = admin.get(f"/api/transactions/{txn_key}/events")
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    assert [(e["kind"], e["via"]) for e in events] == [
        ("imported", "import"),
        ("category_changed", "suggestion"),
    ]
    assert events[1]["to_value"] == "dining" and events[1]["user"]


@pytest.mark.parametrize("bad", [0, -1, (1 << 31) + 1, 1 << 63, 1 << 70])
def test_bulk_ids_out_of_range_are_422(samples, admin, bad: int) -> None:
    r = admin.post(
        "/api/category-suggestions/bulk", json={"ids": [bad], "action": "approve"}, headers=H
    )
    assert r.status_code == 422, r.text


def test_bulk_id_in_range_but_unknown_is_404(samples, admin) -> None:
    r = admin.post(
        "/api/category-suggestions/bulk", json={"ids": [1 << 31], "action": "reject"}, headers=H
    )
    assert r.status_code == 404


@pytest.fixture(scope="module")
def shell_env() -> Iterator[None]:
    """Anthropic names in the shell. Module scope, so it runs before conftest's autouse
    fixture, like a key exported before pytest starts."""
    names = {
        "ANTHROPIC_API_KEY": "fake-shell-key-not-real",
        "BUDGET_ANTHROPIC_API_KEY": "fake-shell-key-not-real",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
        "ANTHROPIC_AUTH_TOKEN": "fake-shell-token-not-real",
    }
    with pytest.MonkeyPatch.context() as mp:
        for name, value in names.items():
            mp.setenv(name, value)
        yield


def test_shell_anthropic_env_is_cleared(shell_env) -> None:
    left = [n for n in os.environ if n.startswith(("ANTHROPIC_", "BUDGET_ANTHROPIC_"))]
    assert left == []
    assert get_settings().anthropic_api_key is None
