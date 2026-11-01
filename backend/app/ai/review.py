"""The monthly review (PLAN 7.1, 7.7).

One review per complete month: a short summary, what changed, anomalies, and
exactly three things to do. Shown on the Dashboard in place of the chat's
review, but only for the latest complete month.

- Inputs are computed from the *_safe views on the AI connection: the facts
  (cash flow, plan, budget, net worth, plans, accounts) and sums over
  `cashflow_rows_safe` (groups, top merchants, largest rows, new merchants).
  The Scrub pass runs over them before they are sent.
- One structured call (`messages.create` with ReviewOut's JSON schema) at the
  review model and effort. Output that breaks the schema (not exactly three
  actions, say) is thrown away, and the month's review is marked failed.
- Text goes through the output filter before it is stored.
- The `ai_review` job runs every few minutes, in its own thread so a long
  call never holds up the other jobs. When the latest complete month has no
  review, it writes one, whichever import (monthly or correction) completed
  the month. A pause or a cap skips it until later. A review that failed for
  a transient reason (connection, timeout, 5xx, overloaded, rate limited) is
  tried again by later runs, no sooner than RETRY_AFTER after the last try,
  up to MAX_TRIES tries for the month. Other failures (schema, max_tokens,
  refusal, other errors) are final for the job; an admin can run it again
  from Settings. A failed row's summary holds the failure's reason and tries
  as JSON, so this needs no new column. A failure the job will not try again
  is a failed job run, and /api/ai/status names it (`review_failed`).
- A correction (PLAN 7.1 item 2): each ready review remembers a hash of the
  month's inputs (MONTH_INPUTS) and the newest import seen with them, in the
  `settings` row INPUTS_KEY, so this needs no new column either. When a later
  import changes them, the review is due again, and the job writes it again
  like a first review: under GUARD, within the caps, with the same retries. An
  edit by hand counts only once an import follows it. A review stored before
  the hash existed takes the inputs at its next check as its hash, with no call.
- One review is written at a time in the process (GUARD): the job and an
  admin's run never both call. An admin's run takes GUARD first. The job
  takes it only once the month is due, so its check every few minutes never
  turns an admin's run away. Under GUARD it reads the newest run and the
  inputs again and checks again, so an import and an admin's run that came
  meanwhile never lead to a review written from older inputs.
- A run that fails never replaces a ready review. A correction run carries no
  chat review, so the chat's review is no newer than this one. A failed
  rewrite is kept beside the review's hash instead, with the same retries as
  a failed first review, and /api/ai/status names it when the job gives up
  (`review_failed` with `rewrite`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pydantic
from pydantic import BaseModel, Field

from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai.facts import ensure_facts
from app.ai.postprocess import Scrub, filter_text, load_last4s
from app.ai.pricing import estimate_micro_usd
from app.ai.prompts import REVIEW_RULES
from app.ai.safe import AiConnection, ai_connection, run
from app.ai.tools import _money, usd
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.importer.order import run_order_key
from app.importer.runner import private_rules_for
from app.jobs.scheduler import JobFailed
from app.services.coverage import coverage_state

log = logging.getLogger("app.ai")

JOB_NAME = "ai_review"
MAX_TOKENS = 8_000
# A failure from the connection, a timeout, a 5xx, an overload or a rate limit
# is transient: the job tries again, at least RETRY_AFTER apart, up to
# MAX_TRIES tries for the month. The call wrapper already retried once inside
# each try. (A spend-limit 429 is a pause, never a failure.)
TRANSIENT = "transient"
TRANSIENT_ERRORS = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "ServiceUnavailableError",
        "DeadlineExceededError",
        "OverloadedError",
        "RateLimitError",
    }
)
MAX_TRIES = 3
RETRY_AFTER = timedelta(hours=1)
# Held while a review is written, by the job's thread or an admin's run.
GUARD = threading.Lock()
RUNNING_TEXT = "A review is being written now. It shows on the Dashboard when it is done."
# Tokens for the rules and schema on top of the input JSON, for the estimate.
PROMPT_OVERHEAD = 2_000
# The settings row with {month: {"hash", "run_id"}} for each ready review: the
# hash of its inputs, and the newest import seen with that hash.
INPUTS_KEY = "ai.review_inputs"
# The inputs that belong to the month (and the months before it). The rest
# (today's net worth, plans, account notes) moves with every import and day.
MONTH_INPUTS = (
    "month",
    "previous_month",
    "cashflow",
    "cashflow_previous_month",
    "plan_vs_actual",
    "spending_by_group",
    "top_merchants",
    "largest_spending",
    "new_merchants",
    "budget",
)
MONTH_NET_WORTH = ("month_end", "previous_month_end")


class ReviewOut(BaseModel):
    """The review's shape. The count of actions is checked after the call, so a
    response that breaks it is still recorded in ai_usage before it is thrown away."""

    summary: str = Field(description="Two or three short sentences.")
    changes: list[str] = Field(description="Up to five changes since the month before.")
    anomalies: list[str] = Field(description="Up to five things that look odd. May be empty.")
    actions: list[str] = Field(description="Exactly three things to do next.")


def valid(out: object) -> bool:
    return (
        isinstance(out, ReviewOut)
        and bool(out.summary.strip())
        and len(out.actions) == 3
        and all(a.strip() for a in out.actions)
    )


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def _fact(conn: AiConnection, kind: str, key: str) -> dict | None:
    rows = run(conn, "SELECT json FROM ai_facts_safe WHERE kind = ? AND key = ?", [kind, key])
    return json.loads(rows[0]["json"]) if rows else None


def _months(conn: AiConnection) -> list[str]:
    return [
        r["key"]
        for r in run(
            conn, "SELECT key FROM ai_facts_safe WHERE kind = 'cashflow_month' ORDER BY key"
        )
    ]


def _cf(f: dict | None) -> dict | None:
    if f is None:
        return None
    return {
        "month": f["month"],
        "income": usd(f["income_cents"]),
        "spending": usd(f["spending_cents"]),
        "saved_invested": usd(f["saved_invested_cents"]),
        "left_over": usd(f["left_over_cents"]),
        "savings_rate_pct": f["savings_rate"],
        "card_spending_no_statement": usd(f["card_no_statement_cents"]),
        "uncategorized": usd(f["uncategorized_cents"]),
    }


SPEND = "flow IN ('spending', 'card_no_stmt')"
# plan_vs_actual and cashflow count spending on different bases (PLAN 3.9).
REVIEW_PLAN_ABOUT = (
    "plan_vs_actual is on the plan's basis: its planned_spending and "
    "actual_spending both include saved and invested, and count installments "
    "and plan fees billed in place of the purchases. cashflow.spending leaves "
    "saved and invested out, so never compare it with planned_spending. Compare "
    "plan with actual only through plan_vs_actual."
)


def inputs(conn: AiConnection) -> dict | None:
    """What the review is written from. None when no month is complete yet."""
    meta = _fact(conn, "meta", "latest") or {}
    month = meta.get("latest_complete_month")
    if not month:
        return None
    months = _months(conn)
    i = months.index(month) if month in months else -1
    prev = months[i - 1] if i > 0 else None
    prior3 = months[max(0, i - 3) : i] if i > 0 else []

    groups = run(
        conn,
        f"""SELECT coalesce(c.group_name, r.group_key) AS label,
                   sum(CASE WHEN r.month = ? THEN r.out_cents ELSE 0 END) AS this_month,
                   sum(CASE WHEN r.month = ? THEN r.out_cents ELSE 0 END) AS last_month,
                   sum(CASE WHEN r.month BETWEEN ? AND ? THEN r.out_cents ELSE 0 END) AS prior
            FROM cashflow_rows_safe AS r
            LEFT JOIN categories_safe AS c ON c.category_key = r.category_key
            WHERE r.{SPEND} AND r.month BETWEEN ? AND ?
            GROUP BY label ORDER BY this_month DESC, label""",
        [
            month,
            prev or "",
            prior3[0] if prior3 else "",
            prior3[-1] if prior3 else "",
            prior3[0] if prior3 else month,
            month,
        ],
    )
    merchants = run(
        conn,
        f"""SELECT coalesce(merchant_label, 'No merchant') AS label,
                   sum(out_cents) AS amount, count(txn_key) AS n
            FROM cashflow_rows_safe WHERE {SPEND} AND month = ?
            GROUP BY lower(label) ORDER BY amount DESC, lower(label) LIMIT 10""",
        [month],
    )
    largest = run(
        conn,
        f"""SELECT txn_date, merchant_label, category_name, account_name, out_cents
            FROM cashflow_rows_safe WHERE {SPEND} AND month = ?
            ORDER BY out_cents DESC, txn_date, txn_key LIMIT 8""",
        [month],
    )
    first_seen = (
        run(
            conn,
            f"""SELECT merchant_label AS label, sum(out_cents) AS amount
                FROM cashflow_rows_safe WHERE {SPEND} AND month = ?
                  AND merchant_label IS NOT NULL AND merchant_label NOT IN (
                      SELECT merchant_label FROM cashflow_rows_safe
                      WHERE {SPEND} AND month < ? AND merchant_label IS NOT NULL)
                GROUP BY label ORDER BY amount DESC, label LIMIT 10""",
            [month, month],
        )
        if i > 0
        else []
    )
    nw = _fact(conn, "networth", "networth") or {"points": []}
    by_month = {p["date"][:7]: p for p in nw["points"]}
    budget = _fact(conn, "budget", month)
    plans = _fact(conn, "plans", "all") or {}
    summary = _fact(conn, "networth_summary", "latest") or {}
    pva = _fact(conn, "plan_vs_actual", month)  # in cents, like every fact
    n3 = max(1, len(prior3))
    return {
        "month": month,
        "previous_month": prev,
        "cashflow": _cf(_fact(conn, "cashflow_month", month)),
        "cashflow_previous_month": _cf(_fact(conn, "cashflow_month", prev)) if prev else None,
        "plan_vs_actual": {"about": REVIEW_PLAN_ABOUT, **_money(pva)} if pva else None,
        "spending_by_group": [
            {
                "group": g["label"],
                "this_month": usd(g["this_month"]),
                "last_month": usd(g["last_month"]) if prev else None,
                "prior_3_month_average": usd(round(g["prior"] / n3)) if prior3 else None,
            }
            for g in groups
        ],
        "top_merchants": [
            {"merchant": m["label"], "amount": usd(m["amount"]), "count": m["n"]} for m in merchants
        ],
        "largest_spending": [
            {
                "date": r["txn_date"],
                "merchant": r["merchant_label"],
                "category": r["category_name"],
                "account": r["account_name"],
                "amount": usd(r["out_cents"]),
            }
            for r in largest
        ],
        "new_merchants": [{"merchant": m["label"], "amount": usd(m["amount"])} for m in first_seen],
        "budget": None
        if budget is None
        else [
            {
                "group": g["label"],
                "plan": usd(g["plan_cents"]),
                "actual": usd(g["actual_cents"]),
                "status": g["status"],
            }
            for g in budget["groups"]
        ],
        "net_worth": {
            "month_end": usd(by_month[month]["value_cents"]) if month in by_month else None,
            "previous_month_end": usd(by_month[prev]["value_cents"]) if prev in by_month else None,
            "latest": usd(summary.get("net_worth_cents")),
        },
        "plans": [
            {
                "name": p["name"],
                "monthly_cost": usd(p["monthly_cost_cents"]),
                "remaining": usd(p["remaining_cents"]),
                "end_month": p["end_month"],
                "pay_before": p["pay_before"],
            }
            for p in plans.get("plans", [])
        ],
        "account_notes": {
            "missing_statements": summary.get("missing", []),
            "stale_balances": summary.get("stale", []),
            "estimated_balances": summary.get("estimated", []),
        },
    }


def inputs_hash(data: dict) -> str:
    """A hash of the month's part of the inputs (MONTH_INPUTS), as sent."""
    part = {k: data.get(k) for k in MONTH_INPUTS}
    nw = data.get("net_worth") or {}
    part["net_worth"] = {k: nw.get(k) for k in MONTH_NET_WORTH}
    return hashlib.sha256(json.dumps(part, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------


def newest_run(conn: sqlite3.Connection) -> str | None:
    """The newest imported run of any mode (a correction can complete a month)."""
    rows = conn.execute("SELECT run_id FROM import_runs WHERE status = 'imported'").fetchall()
    ids = sorted((r["run_id"] for r in rows), key=run_order_key)
    return ids[-1] if ids else None


def _iso() -> str:
    return ai_settings.iso(datetime.now(UTC))


def _inputs_seen(conn: sqlite3.Connection) -> dict[str, dict]:
    """{month: {"hash", "run_id", "failed"?}} of the ready reviews (INPUTS_KEY).
    "failed" is the last failed try at rewriting it (_note_rewrite_failure).
    A broken value counts as none."""
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (INPUTS_KEY,)).fetchone()
    try:
        found = json.loads(row["value_json"]) if row else {}
    except ValueError:
        return {}
    if not isinstance(found, dict):
        return {}
    out = {}
    for m, v in found.items():
        if not (
            isinstance(v, dict)
            and isinstance(v.get("hash"), str)
            and isinstance(v.get("run_id"), str)
        ):
            continue
        entry = {"hash": v["hash"], "run_id": v["run_id"]}
        failed = v.get("failed")
        if isinstance(failed, dict) and all(
            isinstance(failed.get(k), str) for k in ("run_id", "hash", "reason", "at")
        ):
            entry["failed"] = failed
        out[m] = entry
    return out


def _save_seen(conn: sqlite3.Connection, month: str, digest: str, run_id: str) -> None:
    """Remember `month`'s inputs hash and the newest import seen with it (no
    failed try). The caller holds the write transaction."""
    seen = _inputs_seen(conn)
    seen[month] = {"hash": digest, "run_id": run_id}
    _write_seen(conn, seen)


def _note_rewrite_failure(
    conn: sqlite3.Connection, month: str, run_id: str, digest: str | None, reason: str | None
) -> None:
    """A failed try at writing `month`'s ready review again, from import `run_id`
    and inputs `digest`: kept beside the review's hash, with the transient tries
    so far for those. The caller holds the write transaction."""
    seen = _inputs_seen(conn)
    entry = seen.get(month)
    if entry is None or digest is None:
        return
    before = entry.get("failed") or {}
    note: dict[str, Any] = {"run_id": run_id, "hash": digest, "reason": reason or "error"}
    if reason == TRANSIENT:
        same = before.get("run_id") == run_id and before.get("hash") == digest
        tries = before.get("tries", 0) if same and before.get("reason") == TRANSIENT else 0
        note["tries"] = tries + 1
    note["at"] = _iso()
    entry["failed"] = note
    _write_seen(conn, seen)


def _write_seen(conn: sqlite3.Connection, seen: dict[str, dict]) -> None:
    """Write INPUTS_KEY, dropping months with no review."""
    kept = {r["month"] for r in conn.execute("SELECT month FROM ai_reviews")}
    seen = {m: v for m, v in seen.items() if m in kept}
    conn.execute(
        """INSERT INTO settings (key, value_json, updated_by, updated_at) VALUES (?, ?, NULL, ?)
           ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,
             updated_by = NULL, updated_at = excluded.updated_at""",
        (INPUTS_KEY, json.dumps(seen, sort_keys=True), _iso()),
    )


def _note(row: sqlite3.Row | None) -> dict:
    """A failed review's note: {"reason", "tries"}. A row without one counts as final."""
    if row is None or row["status"] != "failed":
        return {}
    try:
        note = json.loads(row["summary"])
    except ValueError:
        return {"reason": "error"}
    return note if isinstance(note, dict) else {"reason": "error"}


def _store(
    db_path: Any,
    run_id: str,
    month: str | None,
    model: str,
    out: ReviewOut | None,
    private,
    reason: str | None = None,
    started: str | None = None,
    digest: str | None = None,
) -> None:
    """Store the review, or a failed row whose summary is a note: the failure's
    reason and, for a transient one, the tries so far this month. A failed run
    never replaces a ready review: its failure goes beside the review's hash,
    unless the review was written at or after `started`. A ready review keeps
    `digest`, the hash of the inputs it was written from."""
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            old = conn.execute(
                "SELECT status, summary, created_at FROM ai_reviews WHERE month = ?", (month,)
            ).fetchone()
            if out is None and old is not None and old["status"] == "ready":
                if started is None or old["created_at"] < started:  # the same ISO format
                    _note_rewrite_failure(conn, month, run_id, digest, reason)
                return
            conn.execute("DELETE FROM ai_reviews WHERE month = ?", (month,))
            if out is None:
                note: dict[str, Any] = {"reason": reason or "error"}
                if reason == TRANSIENT:
                    before = _note(old)
                    tries = before.get("tries", 0) if before.get("reason") == TRANSIENT else 0
                    note["tries"] = tries + 1
                values = (json.dumps(note, sort_keys=True), "[]", "[]", "[]", "failed")
            else:
                values = (
                    filter_text(out.summary, private),
                    json.dumps([filter_text(x, private) for x in out.changes[:5]]),
                    json.dumps([filter_text(x, private) for x in out.anomalies[:5]]),
                    json.dumps([filter_text(x, private) for x in out.actions]),
                    "ready",
                )
            conn.execute(
                """INSERT INTO ai_reviews (run_id, month, model, summary, changes_json,
                       anomalies_json, actions_json, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    month,
                    model,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    _iso(),
                    values[4],
                ),
            )
            if out is not None and digest is not None and month is not None:
                _save_seen(conn, month, digest, run_id)
    finally:
        conn.close()


async def write_review(
    settings: Settings, *, force: bool = False, user_id: int | None = None
) -> dict:
    """Review the latest complete month, once (again with `force`). Any import can
    complete a month, so the job checks after each one. Returns what happened:
    {"status": "running"} while another review is being written. An admin's
    run (`force`) holds GUARD from the start; the job takes it in _write_review."""
    if force and not GUARD.acquire(blocking=False):
        return {"status": "running", "message": RUNNING_TEXT}
    try:
        return await _write_review(settings, force, user_id)
    finally:
        if force:
            GUARD.release()


async def _write_review(settings: Settings, force: bool, user_id: int | None) -> dict:
    started = _iso()
    db_path = settings.db_path
    conn = connect(db_path)
    try:
        run_id = newest_run(conn)
        if run_id is None:
            return {"status": "no_run"}
        st = ai_settings.status(conn, settings)
        if not st["available"]:
            return {"status": "unavailable", "reason": st["reason"]}
        s = ai_settings.load(conn)
    finally:
        conn.close()

    private = private_rules_for(settings)
    model = s.review_model
    found = await asyncio.to_thread(_read, db_path, private)
    if isinstance(found, dict):
        return found
    run_id, data = found
    if force:
        digest = inputs_hash(data)
        return await _call(settings, user_id, run_id, model, private, data, started, digest)
    if await asyncio.to_thread(_reviewed, db_path, data["month"], run_id, inputs_hash(data)):
        return {"status": "done_already"}
    # Due: the job takes GUARD only now. Under it, it reads the newest run and the
    # inputs again and checks again: an import and an admin's run may both have
    # come meanwhile, and the review must never be written from older inputs.
    if not GUARD.acquire(blocking=False):
        return {"status": "running", "message": RUNNING_TEXT}
    try:
        started = _iso()
        found = await asyncio.to_thread(_read, db_path, private)
        if isinstance(found, dict):
            return found
        run_id, data = found
        digest = inputs_hash(data)
        if await asyncio.to_thread(_reviewed, db_path, data["month"], run_id, digest):
            return {"status": "done_already"}
        return await _call(settings, user_id, run_id, model, private, data, started, digest)
    finally:
        GUARD.release()


def _read(db_path: Any, private) -> tuple[str, dict] | dict:
    """The newest run and the inputs as they are sent, read now (the run first, so
    the inputs are never older than it). A result instead when there is no run or
    no complete month."""
    conn = connect(db_path)
    try:
        run_id = newest_run(conn)
    finally:
        conn.close()
    if run_id is None:
        return {"status": "no_run"}
    ensure_facts(db_path, private)
    with ai_connection(db_path) as ai:
        found = inputs(ai)
    if found is None:
        return {"status": "no_complete_month"}
    return run_id, Scrub(private, load_last4s(db_path)).json(found)


async def _call(
    settings: Settings,
    user_id: int | None,
    run_id: str,
    model: str,
    private,
    data: dict,
    started: str,
    digest: str,
) -> dict:
    """The review's call, and its row. The caller holds GUARD."""
    db_path = settings.db_path
    effort = ai_settings.EFFORT["review"]

    content = json.dumps(data, sort_keys=True)

    async def request(client: Any) -> Any:
        return await client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=REVIEW_RULES,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": ai_client.json_format(ReviewOut)},
        )

    async def failed(reason: str) -> dict:
        await asyncio.to_thread(
            _store, db_path, run_id, data["month"], model, None, private, reason, started, digest
        )
        return {"status": "failed", "reason": reason}

    # The cap check assumes the whole input uncached and the longest reply.
    estimate = estimate_micro_usd(model, len(content) // 3 + PROMPT_OVERHEAD, MAX_TOKENS)
    info = ai_client.CallInfo("review", model, effort, user_id, estimate_micro=estimate)
    before = await asyncio.to_thread(_last_usage_id, db_path)
    try:
        message = await ai_client.call(settings, info, request)
    except ai_client.AiError as exc:
        # Paused or capped: not a failed review. The job tries again later.
        if exc.kind in ("unavailable", "cap", "spend_limit"):
            return {"status": "skipped", "reason": exc.kind, "message": exc.message}
        error = await asyncio.to_thread(_call_error, db_path, before)
        return await failed(TRANSIENT if error in TRANSIENT_ERRORS else "error")
    except pydantic.ValidationError, ValueError:
        # The response was not valid JSON for the schema. Nothing to keep.
        return await failed("schema")
    out = ai_client.parse_json(message, ReviewOut)
    if message.stop_reason != "end_turn":
        return await failed(message.stop_reason or "error")  # max_tokens, refusal
    if not valid(out):
        return await failed("schema")
    await asyncio.to_thread(
        _store, db_path, run_id, data["month"], model, out, private, None, None, digest
    )
    return {"status": "ready", "run_id": run_id, "month": data["month"]}


def _last_usage_id(db_path: Any) -> int:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT coalesce(max(id), 0) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()


def _call_error(db_path: Any, after_id: int) -> str | None:
    """The error class (ai_usage.error) of the review's last try after `after_id`.
    The call wrapper writes one row per try, failed or not."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            """SELECT error FROM ai_usage WHERE feature = 'review' AND id > ?
               ORDER BY id DESC LIMIT 1""",
            (after_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["error"] if row else None


def _reviewed(db_path: Any, month: str, run_id: str, digest: str) -> bool:
    """True when the month needs no review from the job now: it has one that no
    later import changed (`run_id` is the newest run, `digest` the inputs' hash
    now), its failure is final (not transient, or MAX_TRIES transient tries),
    or the last transient try was less than RETRY_AFTER ago."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT run_id, status, summary, created_at FROM ai_reviews WHERE month = ?",
            (month,),
        ).fetchone()
        if row is not None and row["status"] == "ready":
            return not _changed(conn, month, run_id, digest)
    finally:
        conn.close()
    if row is None:
        return False
    if _final(_note(row)):
        return True
    return datetime.fromisoformat(row["created_at"]) + RETRY_AFTER > datetime.now(UTC)


def _changed(conn: sqlite3.Connection, month: str, run_id: str, digest: str) -> bool:
    """True when an import (`run_id` is the newest) since the month's ready
    review was written, or last checked, changed its inputs (`digest` now). An
    import that leaves them alone is noted as seen, so an edit by hand counts
    only once a later import follows it. A review with no hash yet (stored
    before there was one) takes `digest`, with no call."""

    def due(seen: dict | None) -> bool | None:
        """True or False when `seen` decides it, None when `digest` must be saved."""
        if seen is not None and seen["run_id"] == run_id:
            return False  # no import since
        if seen is not None and seen["hash"] != digest:
            return not _waiting(seen.get("failed"), run_id, digest)
        return None

    found = due(_inputs_seen(conn).get(month))
    if found is not None:
        return found
    with write_transaction(conn):
        # Again under the write lock: an admin's run may have stored its hash.
        found = due(_inputs_seen(conn).get(month))
        if found is None:
            _save_seen(conn, month, digest, run_id)
    return bool(found)


def _waiting(failed: dict | None, run_id: str, digest: str) -> bool:
    """True when a rewrite from import `run_id` and inputs `digest` failed and
    is not to be tried yet: the failure is final, or the last transient try was
    less than RETRY_AFTER ago. A later import, or other inputs, start over."""
    if not failed or failed["run_id"] != run_id or failed["hash"] != digest:
        return False
    if _final(failed):
        return True
    return datetime.fromisoformat(failed["at"]) + RETRY_AFTER > datetime.now(UTC)


def _final(note: dict) -> bool:
    """No more tries by the job: a ready row ({}), a final failure, or MAX_TRIES
    transient tries."""
    return note.get("reason") != TRANSIENT or note.get("tries", MAX_TRIES) >= MAX_TRIES


def failed_review(conn: sqlite3.Connection) -> dict | None:
    """The latest complete month's review when it failed and the job will not try
    it again, for Settings, AI: {"month", "reason", "tries", "rewrite"} (tries
    for a transient failure only). `rewrite`: the month shows its review from
    before a correction, and writing it again failed. An admin can write it
    now. None otherwise."""
    month = coverage_state(conn).latest_complete_month()
    if month is None:
        return None
    row = conn.execute(
        "SELECT status, summary FROM ai_reviews WHERE month = ?", (month,)
    ).fetchone()
    note = _note(row)
    if note and _final(note):
        return {
            "month": month,
            "reason": note["reason"],
            "tries": note.get("tries"),
            "rewrite": False,
        }
    entry = _inputs_seen(conn).get(month) if row is not None and row["status"] == "ready" else None
    failed = entry.get("failed") if entry else None
    # A failed try on the review's own inputs (an admin's run) is not a rewrite.
    if failed and failed["hash"] != entry["hash"] and _final(failed):
        return {
            "month": month,
            "reason": failed["reason"],
            "tries": failed.get("tries"),
            "rewrite": True,
        }
    return None


def latest_review(conn: sqlite3.Connection) -> dict | None:
    """The ready review of the latest complete month, for the Dashboard. None while
    that month has none yet, so the Dashboard shows the chat's review, never an
    older month's AI review."""
    month = coverage_state(conn).latest_complete_month()
    if month is None:
        return None
    row = conn.execute(
        "SELECT * FROM ai_reviews WHERE status = 'ready' AND month = ?", (month,)
    ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "month": row["month"],
        "model": row["model"],
        "created_at": row["created_at"],
        "summary": row["summary"],
        "changes": json.loads(row["changes_json"]),
        "anomalies": json.loads(row["anomalies_json"]),
        "actions": json.loads(row["actions_json"]),
    }


def review_job_run(settings: Settings) -> dict | None:
    """The scheduler's job: a review when one is due. None when nothing ran, or
    while another review is being written. Raises JobFailed when the review
    failed and the job will not try it again (the last transient try, or a
    final failure), so job_runs says failed."""
    result = asyncio.run(write_review(settings))
    if result["status"] == "failed":
        conn = connect(settings.db_path)
        try:
            gave_up = failed_review(conn)
        finally:
            conn.close()
        if gave_up is not None:
            raise JobFailed(gave_up)
    if result["status"] in ("ready", "failed"):
        return result
    return None
