"""The AI ledger: usage rows, caps and the cost page (PLAN 7.5, 7.6).

- Every model call writes one `ai_usage` row: the four token counts, the cost,
  the stop reason, the request id, latency and the thread. A failed call
  writes a row with the error class, and the tokens and cost a stream reported
  before it broke (zero when nothing came). The cost includes the US-only
  multiplier when the call ran there. The region is not stored; a call that
  was not on global routing logs it.
- The caps are checked **before** every call, with an estimate of that call:
    monthly  (UTC month, like Console billing) settings.monthly_cap_cents.
             Ask stops `ask_reserve_cents` below it, so the review and
             categorizing still run.
    daily    (the household's day) settings.daily_cap_cents and
             settings.daily_questions (Ask questions and trip plan turns,
             household-wide).
- The usage page shows month-to-date cost, today's counts, the cache hit rate
  and the last 20 calls (metadata only).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, time

from app.ai.pricing import Tokens
from app.ai.settings import AiSettings, iso
from app.config import HOUSEHOLD_TZ

CENT = 10_000  # micro-USD per cent
WARN_AT = (50, 80)  # percent of the monthly cap


class CapReached(Exception):  # noqa: N818 - a stop, not a crash
    """A cap would be passed. `message` is shown to the person as is."""

    def __init__(self, message: str, cap: str) -> None:
        super().__init__(message)
        self.message = message
        self.cap = cap


def month_start_utc(now: datetime) -> datetime:
    now = now.astimezone(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def household_day_start_utc(now: datetime) -> datetime:
    """00:00 of the household's day (America/Chicago), in UTC."""
    local = now.astimezone(HOUSEHOLD_TZ)
    return datetime.combine(local.date(), time(0), HOUSEHOLD_TZ).astimezone(UTC)


def spent_since(conn: sqlite3.Connection, since: datetime) -> int:
    row = conn.execute(
        "SELECT coalesce(sum(cost_micro_usd), 0) FROM ai_usage WHERE at >= ?", (iso(since),)
    ).fetchone()
    return int(row[0])


def questions_since(conn: sqlite3.Connection, since: datetime) -> int:
    """Ask questions plus trip plan requests and follow-ups: they share the daily
    limit. A deleted plan's requests are gone, so its turns count by the usage
    row of each one's first call (`question`), which a delete keeps."""
    row = conn.execute(
        """SELECT (SELECT count(*) FROM ai_messages
                   WHERE kind = 'question' AND created_at >= ?)
                + (SELECT count(*) FROM trip_plan_turns
                   WHERE kind = 'request' AND created_at >= ?)
                + (SELECT count(*) FROM ai_usage
                   WHERE question = 1 AND plan_id IS NULL AND at >= ?)""",
        (iso(since), iso(since), iso(since)),
    ).fetchone()
    return int(row[0])


def _next_month_label(now: datetime) -> str:
    now = now.astimezone(UTC)
    month = 1 if now.month == 12 else now.month + 1
    return datetime(2000, month, 1).strftime("%b 1")


# Features that stop `ask_reserve_cents` below the monthly cap, so the monthly
# review keeps room. The name goes in the message.
RESERVED = {"ask": "Ask", "plan_research": "The trip planner", "plan_write": "The trip planner"}


def check_caps(
    conn: sqlite3.Connection,
    s: AiSettings,
    feature: str,
    estimate_micro: int,
    now: datetime,
    *,
    new_question: bool = False,
) -> None:
    """Raise CapReached when this call (at `estimate_micro`) would pass a cap."""
    month = spent_since(conn, month_start_utc(now))
    day = spent_since(conn, household_day_start_utc(now))
    monthly = s.monthly_cap_cents * CENT
    if feature in RESERVED:
        ask_limit = max(0, monthly - s.ask_reserve_cents * CENT)
        if month + estimate_micro > ask_limit:
            raise CapReached(
                f"{RESERVED[feature]} has used its share of this month's AI budget. It starts "
                f"again on {_next_month_label(now)}. The monthly review still runs.",
                "monthly_ask",
            )
    if month + estimate_micro > monthly:
        raise CapReached(
            f"This month's AI budget is used up. It starts again on {_next_month_label(now)}.",
            "monthly",
        )
    if day + estimate_micro > s.daily_cap_cents * CENT:
        raise CapReached("Today's AI budget is used up. Try again tomorrow.", "daily")
    if new_question and questions_since(conn, household_day_start_utc(now)) >= s.daily_questions:
        raise CapReached(
            f"The household has asked {s.daily_questions} questions today, the daily limit. "
            "Ask again tomorrow.",
            "daily_questions",
        )


@dataclass(frozen=True)
class UsageRow:
    feature: str
    model: str
    effort: str | None
    user_id: int | None = None
    thread_id: str | None = None
    tokens: Tokens = Tokens()
    cost_micro_usd: int = 0
    stop_reason: str | None = None
    request_id: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    plan_id: str | None = None
    question: bool = False


def record(conn: sqlite3.Connection, row: UsageRow, now: datetime) -> None:
    """Write one ai_usage row. The caller holds the write transaction. A plan
    deleted while its call ran (the rescan can) leaves plan_id empty: the cost
    is still recorded."""
    t = row.tokens
    conn.execute(
        """INSERT INTO ai_usage (at, user_id, feature, model, effort, input_tokens,
               cache_creation_input_tokens, cache_read_input_tokens, output_tokens,
               cost_micro_usd, stop_reason, request_id, latency_ms, thread_id, error,
               web_searches, plan_id, question)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   (SELECT plan_id FROM trip_plans WHERE plan_id = ?), ?)""",
        (
            iso(now),
            row.user_id,
            row.feature,
            row.model,
            row.effort,
            t.input,
            t.cache_write,
            t.cache_read,
            t.output,
            row.cost_micro_usd,
            row.stop_reason,
            row.request_id,
            row.latency_ms,
            row.thread_id,
            row.error,
            t.web_searches,
            row.plan_id,
            int(row.question),
        ),
    )


def last_cost(conn: sqlite3.Connection, feature: str) -> int:
    """The cost of the newest successful call of a feature (the next call's estimate)."""
    row = conn.execute(
        """SELECT cost_micro_usd FROM ai_usage WHERE feature = ? AND error IS NULL
           ORDER BY id DESC LIMIT 1""",
        (feature,),
    ).fetchone()
    return int(row[0]) if row else 0


def _pct(part: int, whole: int) -> str | None:
    if whole <= 0:
        return None
    return f"{part * 100 / whole:.1f}"


def usage_page(conn: sqlite3.Connection, s: AiSettings, now: datetime) -> dict:
    """Settings, AI: the month's cost, today's counts, cache hit rate, the last 20 calls."""
    month_start = month_start_utc(now)
    day_start = household_day_start_utc(now)
    m = conn.execute(
        """SELECT coalesce(sum(cost_micro_usd), 0) AS cost, count(*) AS calls,
                  coalesce(sum(input_tokens), 0) AS input,
                  coalesce(sum(cache_creation_input_tokens), 0) AS written,
                  coalesce(sum(cache_read_input_tokens), 0) AS read,
                  coalesce(sum(output_tokens), 0) AS output
           FROM ai_usage WHERE at >= ?""",
        (iso(month_start),),
    ).fetchone()
    by_feature = {
        r["feature"]: int(r["cost"])
        for r in conn.execute(
            """SELECT feature, coalesce(sum(cost_micro_usd), 0) AS cost FROM ai_usage
               WHERE at >= ? GROUP BY feature""",
            (iso(month_start),),
        )
    }
    d = conn.execute(
        """SELECT coalesce(sum(cost_micro_usd), 0) AS cost, count(*) AS calls
           FROM ai_usage WHERE at >= ?""",
        (iso(day_start),),
    ).fetchone()
    prompt = int(m["input"]) + int(m["written"]) + int(m["read"])
    monthly = s.monthly_cap_cents * CENT
    used_pct = int(m["cost"]) * 100 // monthly if monthly else 0
    warning = max((w for w in WARN_AT if used_pct >= w), default=None)
    calls = [
        {
            "at": r["at"],
            "feature": r["feature"],
            "model": r["model"],
            "effort": r["effort"],
            "input_tokens": r["input_tokens"],
            "cache_creation_input_tokens": r["cache_creation_input_tokens"],
            "cache_read_input_tokens": r["cache_read_input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost_micro_usd": r["cost_micro_usd"],
            "stop_reason": r["stop_reason"],
            "latency_ms": r["latency_ms"],
            "error": r["error"],
            "web_searches": r["web_searches"],
        }
        for r in conn.execute("SELECT * FROM ai_usage ORDER BY at DESC, id DESC LIMIT 20")
    ]
    return {
        "month": month_start.strftime("%Y-%m"),
        "month_cost_micro_usd": int(m["cost"]),
        "month_calls": int(m["calls"]),
        "month_by_feature_micro_usd": by_feature,
        "monthly_cap_cents": s.monthly_cap_cents,
        "month_used_pct": _pct(int(m["cost"]), monthly),
        "warning_pct": warning,
        "today_cost_micro_usd": int(d["cost"]),
        "today_calls": int(d["calls"]),
        "today_questions": questions_since(conn, day_start),
        "daily_cap_cents": s.daily_cap_cents,
        "daily_questions": s.daily_questions,
        "cache_hit_pct": _pct(int(m["read"]), prompt),
        "calls": calls,
    }
