"""AI routes (PLAN 7, 8.2).

- GET /api/ai/status: whether AI features may run (any signed-in user), and
  how the review and suggestion runs started here are going, and the latest
  complete month's review when it failed for good (`review_failed`). The frontend
  hides every AI feature unless `available`. The stored review shows while the
  setting is on and the key is set, also during a spend-limit pause (no calls
  are made then).
- GET/PUT /api/settings/ai, POST /api/settings/ai/clear-block, GET /api/ai/usage:
  admin only.
- POST /api/ask: server-sent events for one question. Checks run before the
  stream starts: a refused question is a 422, AI off a 409, a cap a 429.
- GET /api/ask/threads[/{id}]: the person's own threads, filtered for display.
- GET /api/ai/review, POST /api/ai/review/run (admin): the monthly review.
- GET /api/category-suggestions, POST /api/category-suggestions/run (admin),
  POST /api/category-suggestions/{id}/approve|reject, POST .../bulk.

Demo: every route that calls the model or changes the AI settings is refused
(`NOT_IN_DEMO`), and the status shows none of this process's real runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.ai import ask, categorize, ledger, pricing, review
from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.api.ai_models import (
    AiReviewOut,
    AiSettingsOut,
    AiStatus,
    AiUsage,
    AskIn,
    Out,
    RunReviewOut,
    SuggestionBulkIn,
    SuggestionDecisionOut,
    SuggestionsOut,
    ThreadOut,
    ThreadsOut,
)
from app.api.deps import (
    NOT_IN_DEMO,
    SIGNED_IN,
    CurrentUser,
    Db,
    app_settings,
    is_demo,
    require_admin,
)
from app.auth.users import User
from app.config import Settings
from app.db.connect import write_transaction
from app.importer.runner import private_rules_for

log = logging.getLogger("app.ai")

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

Admin = Annotated[User, Depends(require_admin)]
ThreadId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
SuggestionId = Annotated[int, Path(ge=1, le=1 << 31)]

AI_STATUS_CODE = {"unavailable": 409, "cap": 429, "spend_limit": 409, "failed": 503}


def _now() -> datetime:
    return datetime.now(UTC)


class RunResult(Out):
    status: str = Field(
        description="The review: ready, failed, skipped, done_already, no_run, "
        "no_complete_month, unavailable or running (another review is being written). "
        "Suggestions: done, unavailable, cap, "
        "spend_limit or failed."
    )
    reason: str | None = None
    message: str | None = Field(default=None, description="Why, to show as is.")
    merchants: int | None = Field(default=None, description="Suggestions: merchants sent.")
    suggested: int | None = Field(default=None, description="Suggestions: new ones stored.")
    finished_at: str


class ReviewFailed(Out):
    month: str
    reason: str = Field(
        description="transient (after 3 tries), schema, max_tokens, refusal or error."
    )
    tries: int | None = Field(description="Transient tries this month.")
    rewrite: bool = Field(
        default=False,
        description="The month shows its review from before a correction, and writing "
        "it again failed.",
    )


class AiStatusOut(AiStatus):
    running_review: bool = Field(description="A review started here is being written.")
    running_suggestions: bool = Field(description="A suggestions run started here is going.")
    last_review_result: RunResult | None = Field(
        description="How the newest review started here ended (since the server started)."
    )
    last_suggestions_result: RunResult | None
    review_failed: ReviewFailed | None = Field(
        description="The latest complete month's review failed and the job will not try "
        "it again. An admin can write it now."
    )


# The two run POSTs leave out empty fields: `started` is there only while the
# run goes on in the background, the counts only once it has ended.
STARTED = "Only while the run goes on in the background. GET /api/ai/status says when it ends."


class RunReviewStartOut(RunReviewOut):
    started: bool | None = Field(default=None, description=STARTED + ' Status is "running".')


class SuggestionRunStartOut(Out):
    merchants: int | None = Field(default=None, description="Merchants sent to the model.")
    suggested: int | None = Field(
        default=None, description="New suggestions stored (one per transaction)."
    )
    started: bool | None = Field(default=None, description=STARTED)


def review_visible(st: dict) -> bool:
    """The stored review shows while the setting is on and the key is set. A
    spend-limit pause keeps it: no calls are made then."""
    return bool(st["enabled"] and st["key_present"])


# --------------------------------------------------------------------------
# Status, settings, usage
# --------------------------------------------------------------------------


@router.get("/ai/status", response_model=AiStatusOut)
def ai_status(request: Request, conn: Db) -> dict:
    real = not is_demo(request)  # the runs are this process's real ones
    return {
        **ai_settings.status(conn, app_settings(request)),
        "running_review": real and _runs["review"].running(),
        "running_suggestions": real and _runs["suggestions"].running(),
        "last_review_result": _runs["review"].last if real else None,
        "last_suggestions_result": _runs["suggestions"].last if real else None,
        "review_failed": review.failed_review(conn),
    }


@router.get("/settings/ai", response_model=AiSettingsOut)
def get_ai_settings(request: Request, conn: Db, _: Admin) -> dict:
    return {
        "settings": ai_settings.load(conn),
        "status": ai_settings.status(conn, app_settings(request)),
        "effort": dict(ai_settings.EFFORT),
        "models": list(pricing.MODELS),
        "prices": pricing.price_table(),
        "prices_checked_on": pricing.CHECKED_ON,
        "prices_source": pricing.SOURCE_URL,
    }


@router.put("/settings/ai", response_model=AiSettingsOut, dependencies=NOT_IN_DEMO)
def put_ai_settings(request: Request, conn: Db, user: Admin, body: ai_settings.AiSettings) -> dict:
    with write_transaction(conn):
        ai_settings.save(conn, body, user.id)
    return get_ai_settings(request, conn, user)


@router.post("/settings/ai/clear-block", response_model=AiSettingsOut, dependencies=NOT_IN_DEMO)
def clear_ai_block(request: Request, conn: Db, user: Admin) -> dict:
    """After the Console limit is raised: let AI run again before the 1st."""
    with write_transaction(conn):
        ai_settings.clear_block(conn)
    return get_ai_settings(request, conn, user)


@router.get("/ai/usage", response_model=AiUsage)
def ai_usage(conn: Db, _: Admin) -> dict:
    return ledger.usage_page(conn, ai_settings.load(conn), _now())


# --------------------------------------------------------------------------
# Ask
# --------------------------------------------------------------------------


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# One question at a time per person, so parallel requests cannot all pass the
# cap check before any of them is counted. The app runs one worker. The slot is
# held until the turn has ended, also when the browser leaves mid-answer (the
# model call is cancelled and logged first). An entry older than
# ASK_SLOT_SECONDS is ignored (a stream that never started); a running stream
# renews it with every event and keepalive.
ASK_SLOT_SECONDS = 300
_asking: dict[int, float] = {}
BUSY_TEXT = "Wait for the answer to finish, then ask again."

# While a turn waits (a slow or hung call can take minutes), an SSE comment
# goes out this often, so a proxy does not close a quiet stream.
KEEPALIVE_SECONDS = 15.0
KEEPALIVE = ": keepalive\n\n"


async def _next(turn: AsyncIterator[dict]) -> dict | None:
    try:
        return await anext(turn)
    except StopAsyncIteration:
        return None


async def _close(turn: AsyncGenerator[dict]) -> None:
    await turn.aclose()


async def ask_events(
    settings: Settings, user_id: int, prepared: ask.Prepared
) -> AsyncIterator[str]:
    """The turn as SSE lines, with a keepalive comment while nothing comes. Each
    step of the turn runs in its own task, so the wait can time out."""
    turn = ask.stream_turn(settings, prepared)
    slot = [_asking.get(user_id)]
    pending: asyncio.Task | None = None
    ended = False
    try:
        while True:
            pending = asyncio.create_task(_next(turn))
            while True:
                done, _ = await asyncio.wait({pending}, timeout=KEEPALIVE_SECONDS)
                if done:
                    break
                slot[0] = _asking[user_id] = time.monotonic()  # still busy, not stale
                yield KEEPALIVE
            event = pending.result()
            if event is None:
                ended = True
                return
            slot[0] = _asking[user_id] = time.monotonic()
            yield _sse(event)
    finally:
        # No await here: on a disconnect this code runs cancelled.
        def free() -> None:
            if _asking.get(user_id) == slot[0]:
                del _asking[user_id]

        def settled(_: object = None) -> None:
            ask.when_settled(prepared, free)

        if pending is not None and not pending.done():
            pending.cancel()  # the turn cancels its model call and closes its thread
            pending.add_done_callback(settled)
        elif not ended:  # the browser left at a yield
            ask.background(_close(turn)).add_done_callback(settled)
        else:
            settled()


@router.post("/ask", response_class=StreamingResponse, dependencies=NOT_IN_DEMO)
async def post_ask(request: Request, user: CurrentUser, body: AskIn) -> StreamingResponse:
    settings = app_settings(request)
    started = _asking.get(user.id)
    if started is not None and time.monotonic() - started < ASK_SLOT_SECONDS:
        raise HTTPException(status_code=409, detail=BUSY_TEXT)
    _asking[user.id] = time.monotonic()
    try:
        prepared = await asyncio.to_thread(
            ask.prepare, settings, user, body.question, body.thread_id
        )
    except ask.QuestionError as exc:
        _asking.pop(user.id, None)
        raise HTTPException(status_code=422, detail=exc.message) from None
    except ai_client.AiError as exc:
        _asking.pop(user.id, None)
        raise HTTPException(status_code=AI_STATUS_CODE[exc.kind], detail=exc.message) from None
    except BaseException:
        _asking.pop(user.id, None)
        raise

    return StreamingResponse(
        ask_events(settings, user.id, prepared),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/ask/threads", response_model=ThreadsOut)
def ask_threads(request: Request, conn: Db, user: CurrentUser) -> dict:
    return {"threads": ask.threads(conn, user, private_rules_for(app_settings(request)))}


@router.get("/ask/threads/{thread_id}", response_model=ThreadOut)
def ask_thread(request: Request, conn: Db, user: CurrentUser, thread_id: ThreadId) -> dict:
    found = ask.thread_detail(conn, user, thread_id, private_rules_for(app_settings(request)))
    if found is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return found


# --------------------------------------------------------------------------
# The monthly review
# --------------------------------------------------------------------------


@router.get("/ai/review", response_model=AiReviewOut)
def ai_review(request: Request, conn: Db) -> dict:
    if not review_visible(ai_settings.status(conn, app_settings(request))):
        return {"review": None}
    return {"review": review.latest_review(conn)}


# --------------------------------------------------------------------------
# Runs in the background: the review and category suggestions
# --------------------------------------------------------------------------

# A POST starts the run and answers within RUN_WAIT_SECONDS: with the outcome
# when the run has ended by then (a refusal, a cap, a quick failure), else as
# running. The run goes on in the background, holding no lock while it waits
# on the network, and GET /api/ai/status says when it ends and how. One run of
# each kind at a time: a POST while one runs waits on that one.
RUN_WAIT_SECONDS = 5.0


@dataclass
class _Run:
    task: asyncio.Task | None = None
    last: dict | None = None  # the newest outcome (RunResult)

    def running(self) -> bool:
        t = self.task
        return t is not None and not t.done() and not t.get_loop().is_closed()


_runs = {"review": _Run(), "suggestions": _Run()}


async def _review_outcome(settings: Settings, user_id: int) -> dict:
    result = await review.write_review(settings, force=True, user_id=user_id)
    return {k: result.get(k) for k in ("status", "reason", "message")}


async def _suggestions_outcome(settings: Settings, user_id: int) -> dict:
    try:
        counts = await categorize.suggest(settings, user_id)
    except ai_client.AiError as exc:
        return {"status": exc.kind, "message": exc.message, "merchants": 0, "suggested": 0}
    return {"status": "done", **counts}


async def _finished(kind: str, work: Awaitable[dict]) -> dict:
    try:
        outcome = await work
    except Exception as exc:  # never the message: it can hold data
        log.error("ai run failed", extra={"exc_class": type(exc).__name__})
        outcome = {"status": "failed", "message": ai_client.FAILED_TEXT}
    outcome["finished_at"] = ai_settings.iso(_now())
    _runs[kind].last = outcome
    return outcome


async def _start(kind: str, work: Callable[[], Awaitable[dict]]) -> dict | None:
    """The run's outcome if it ends within RUN_WAIT_SECONDS, else None."""
    run = _runs[kind]
    if not run.running():
        run.task = asyncio.create_task(_finished(kind, work()))
    done, _ = await asyncio.wait({run.task}, timeout=RUN_WAIT_SECONDS)
    return done.pop().result() if done else None


@router.post(
    "/ai/review/run",
    response_model=RunReviewStartOut,
    response_model_exclude_none=True,
    dependencies=NOT_IN_DEMO,
)
async def run_review(request: Request, conn: Db, user: Admin) -> dict:
    """Write (or rewrite) the review of the newest monthly run: the outcome, or
    {"status": "running", "started": true} while it goes on in the background.
    While the review job is writing one, nothing starts: {"status": "running"}
    with a message, and no "started"."""
    settings = app_settings(request)
    result = await _start("review", lambda: _review_outcome(settings, user.id))
    if result is None:
        return {"status": "running", "started": True}
    if result["status"] == "unavailable":
        raise HTTPException(
            status_code=409, detail=ai_client.unavailable_text(conn, result["reason"])
        )
    return {
        "status": result["status"],
        "reason": result.get("reason"),
        "message": result.get("message"),
    }


# --------------------------------------------------------------------------
# Category suggestions
# --------------------------------------------------------------------------


@router.get("/category-suggestions", response_model=SuggestionsOut)
def list_suggestions(conn: Db) -> dict:
    return {"suggestions": categorize.listing(conn)}


@router.post(
    "/category-suggestions/run",
    response_model=SuggestionRunStartOut,
    response_model_exclude_none=True,
    dependencies=NOT_IN_DEMO,
)
async def run_suggestions(request: Request, user: Admin) -> dict:
    """Suggest categories for uncategorized merchants: the counts, or
    {"started": true} while it goes on in the background."""
    settings = app_settings(request)
    result = await _start("suggestions", lambda: _suggestions_outcome(settings, user.id))
    if result is None:
        return {"started": True}
    if result["status"] != "done":
        raise HTTPException(
            status_code=AI_STATUS_CODE.get(result["status"], 503), detail=result["message"]
        )
    return {"merchants": result["merchants"], "suggested": result["suggested"]}


def _decide(conn: Db, ids: list[int], approve: bool, user: User) -> dict:
    try:
        return {"changed": categorize.decide(conn, ids, approve, user.id)}
    except categorize.DecideError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.post("/category-suggestions/{suggestion_id}/approve", response_model=SuggestionDecisionOut)
def approve_suggestion(conn: Db, user: CurrentUser, suggestion_id: SuggestionId) -> dict:
    return _decide(conn, [suggestion_id], True, user)


@router.post("/category-suggestions/{suggestion_id}/reject", response_model=SuggestionDecisionOut)
def reject_suggestion(conn: Db, user: CurrentUser, suggestion_id: SuggestionId) -> dict:
    return _decide(conn, [suggestion_id], False, user)


@router.post("/category-suggestions/bulk", response_model=SuggestionDecisionOut)
def bulk_suggestions(conn: Db, user: CurrentUser, body: SuggestionBulkIn) -> dict:
    return _decide(conn, body.ids, body.action == "approve", user)
