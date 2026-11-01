"""AI settings and the off switch (PLAN 7.2, 7.5, 7.7).

Stored in the `settings` table:
- key `ai`: the admin-editable settings (AiSettings). Missing fields use the defaults.
- key `ai.blocked`: set by the app when the API reports a spend limit or a
  billing problem. AI stays off until `until` (00:00 UTC on the 1st of next
  month) or until an admin clears it.

AI is available only when the setting is on, ANTHROPIC_API_KEY is set, and no
spend-limit or billing block is in force. It is off by default and stays off
until the owner approves Phase 6.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.pricing import MODELS
from app.config import Settings

Feature = Literal["ask", "review", "categorize", "planner"]
BlockReason = Literal["workspace_limit", "org_limit", "tier_cap", "billing"]

# Effort per feature (PLAN 7.2). Pinned per Ask thread with its model. The trip
# planner's two calls (research with web search, then the plan) each have their own.
EFFORT: dict[str, str] = {
    "ask": "medium",
    "review": "high",
    "categorize": "low",
    "plan_research": "medium",
    "plan_write": "high",
}

SETTINGS_KEY = "ai"
BLOCK_KEY = "ai.blocked"

ModelId = Literal["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
assert set(ModelId.__args__) == set(MODELS)  # type: ignore[attr-defined]


class AiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    ask_model: ModelId = "claude-sonnet-5-5"
    review_model: ModelId = "claude-opus-5-5"
    categorize_model: ModelId = "claude-haiku-5-5"
    # Both trip planner calls (docs/plans/trip-planner.md).
    planner_model: ModelId = "claude-opus-5-5"
    # Caps in cents (PLAN 7.5). The monthly cap follows UTC months, like Console billing.
    monthly_cap_cents: int = Field(default=2500, ge=0, le=100_000)
    # Ask and the trip planner stop this much below the monthly cap, so the review and
    # categorizing still run.
    ask_reserve_cents: int = Field(default=200, ge=0, le=100_000)
    # Daily caps, household-wide, by the household's day (America/Chicago).
    daily_cap_cents: int = Field(default=400, ge=0, le=100_000)
    daily_questions: int = Field(default=40, ge=0, le=1000)
    thread_retention_days: int = Field(default=90, ge=1, le=3650)


class Block(BaseModel):
    until: str  # UTC ISO
    reason: BlockReason
    at: str


def _now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def load(conn: sqlite3.Connection) -> AiSettings:
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    if row is None:
        return AiSettings()
    data = json.loads(row["value_json"])
    # Unknown keys from an older version are dropped, not fatal.
    return AiSettings.model_validate(
        {k: v for k, v in data.items() if k in AiSettings.model_fields}
    )


def save(conn: sqlite3.Connection, value: AiSettings, user_id: int | None) -> None:
    """Write the settings. The caller holds the write transaction."""
    conn.execute(
        """INSERT INTO settings (key, value_json, updated_by, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,
             updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        (SETTINGS_KEY, value.model_dump_json(), user_id, iso(_now())),
    )


def load_block(conn: sqlite3.Connection, now: datetime | None = None) -> Block | None:
    """The spend-limit or billing block in force, or None (an expired one is ignored)."""
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (BLOCK_KEY,)).fetchone()
    if row is None:
        return None
    block = Block.model_validate_json(row["value_json"])
    if datetime.fromisoformat(block.until) <= (now or _now()):
        return None
    return block


def next_month_utc(now: datetime) -> datetime:
    """00:00 UTC on the first day of the month after `now`."""
    now = now.astimezone(UTC)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


def set_block(conn: sqlite3.Connection, reason: BlockReason, now: datetime | None = None) -> Block:
    """Turn AI off until the 1st of next month 00:00 UTC. The caller holds the write lock."""
    now = now or _now()
    block = Block(until=iso(next_month_utc(now)), reason=reason, at=iso(now))
    conn.execute(
        """INSERT INTO settings (key, value_json, updated_by, updated_at) VALUES (?, ?, NULL, ?)
           ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,
             updated_by = NULL, updated_at = excluded.updated_at""",
        (BLOCK_KEY, block.model_dump_json(), iso(now)),
    )
    return block


def clear_block(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (BLOCK_KEY,))


BLOCK_TEXT = {
    "workspace_limit": "The Claude Console workspace reached its spend limit.",
    "org_limit": "The Claude Console organization reached its spend limit.",
    "tier_cap": "The Claude Console organization reached its usage tier's monthly cap.",
    "billing": "The Claude Console account is out of credit or has a billing problem.",
}


def status(conn: sqlite3.Connection, settings: Settings, now: datetime | None = None) -> dict:
    """Whether AI can run, and why not. Never reveals the key."""
    s = load(conn)
    block = load_block(conn, now)
    key_present = settings.anthropic_api_key is not None
    if not s.enabled:
        reason = "off"
    elif not key_present:
        reason = "no_key"
    elif block is not None:
        reason = "spend_limit"
    else:
        reason = None
    return {
        "enabled": s.enabled,
        "key_present": key_present,
        "available": reason is None,
        "reason": reason,
        "blocked_until": block.until if block else None,
        "blocked_reason": BLOCK_TEXT[block.reason] if block else None,
        "thread_retention_days": s.thread_retention_days,
    }


def model_for(s: AiSettings, feature: Feature) -> str:
    return {
        "ask": s.ask_model,
        "review": s.review_model,
        "categorize": s.categorize_model,
        "planner": s.planner_model,
    }[feature]
