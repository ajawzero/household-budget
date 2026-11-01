"""Every model call goes through `call` (PLAN 7.5, 7.6).

Before the call: AI must be available (setting on, key set, no spend-limit
block) and the caps must allow the call's estimate. After the call: one
ai_usage row with the tokens, cost and request id. On an error: one ai_usage
row with the error class (and, for a stream that broke, the usage it reported
plus an estimate of the output it streamed), then `AiError` with a plain
message for the person.

Retries are ours, not the SDK's (`max_retries=0`):
- a spend limit (HTTP 400 "You have reached your specified ..." or HTTP 429
  with error_code `enforced_spend_limit_reached`) or a billing problem (HTTP
  402 `billing_error`, or a 400 about the credit balance): no retry. AI turns
  off until 00:00 UTC on the 1st of next month or until an admin clears the
  pause, and Settings says why;
- a 429 with retry-after: one retry after the wait (at most 30 seconds);
- 5xx, 529, an `overloaded_error` or `api_error` event inside a stream,
  timeouts and dropped or stalled connections: one retry after 2 seconds;
- anything else: no retry.
A retry happens only while nothing has been sent to the person yet, no text
has arrived from the failed stream and it ran no web search (a search is
billed, and the retry would run it again).

Streams use TIMEOUT_SECONDS between reads. Non-streaming calls wait for the
whole reply, so they get LONG_TIMEOUT_SECONDS, with CONNECT_TIMEOUT_SECONDS to
connect (the SDK's own default).

Errors are logged by class only. The key never leaves the client object.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pydantic

from app.ai import ledger
from app.ai import settings as ai_settings
from app.ai.pricing import PRICES, Tokens, cost_micro_usd, tokens_of
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.demo.context import DemoRefused, in_demo

log = logging.getLogger("app.ai")

TIMEOUT_SECONDS = 180.0
# The SDK's own timeout for a non-streaming call. It applies it only while the
# client keeps the SDK default, which this one does not, so we pass it.
LONG_TIMEOUT_SECONDS = 600.0
CONNECT_TIMEOUT_SECONDS = 5.0
LONG_TIMEOUT = httpx2.Timeout(LONG_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
# Streamed output estimated at this many characters a token, as Ask does when
# the person leaves mid-stream.
CHARS_PER_TOKEN = 3
MAX_RETRY_AFTER = 30.0
RETRY_PAUSE = 2.0

ClientFactory = Callable[[Settings], Any]


def default_factory(settings: Settings) -> Any:
    key = settings.anthropic_api_key
    if key is None:  # status() already said no, this is a guard
        raise AiError("AI is off: no API key is set.", "unavailable")
    return anthropic.AsyncAnthropic(
        api_key=key.get_secret_value(), max_retries=0, timeout=TIMEOUT_SECONDS
    )


_factory: ClientFactory = default_factory


def set_client_factory(factory: ClientFactory | None) -> None:
    """Tests install a fake client here. None restores the real one."""
    global _factory
    _factory = factory or default_factory


def make_client(settings: Settings) -> Any:
    # Here, not in default_factory: a test fake replaces that.
    if in_demo():
        raise DemoRefused("ai client refused in demo")
    return _factory(settings)


class AiError(Exception):
    """A call did not happen or failed. `message` is shown to the person as is."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind  # unavailable, cap, spend_limit, failed


def json_format(model_cls: type[pydantic.BaseModel]) -> dict:
    """The output_config "format" for structured output, built the way the SDK's
    messages.parse builds it. Calls use messages.create with it, so a reply that
    does not parse is still recorded with its real tokens and cost."""
    schema = anthropic.transform_schema(model_cls.model_json_schema())
    return {"type": "json_schema", "schema": schema}


def parse_json[M: pydantic.BaseModel](message: Any, model_cls: type[M]) -> M | None:
    """The reply's text as `model_cls`, or None when it does not parse."""
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    try:
        return model_cls.model_validate_json(text)
    except pydantic.ValidationError:
        return None


UNAVAILABLE_TEXT = {
    "off": "AI is turned off in Settings.",
    "no_key": "AI is off: no API key is set on the server.",
    "spend_limit": "AI is paused: the Claude Console spend limit was reached.",
    "billing": "AI is paused: the Claude Console account has no credit or a billing problem.",
}
FAILED_TEXT = "Claude did not answer just now. Try again in a minute."
KEY_TEXT = "The Claude API key was not accepted. An admin needs to check it."


@dataclass(frozen=True)
class CallInfo:
    feature: str
    model: str
    effort: str | None
    user_id: int | None = None
    thread_id: str | None = None
    estimate_micro: int = 0
    new_question: bool = False
    # False only for `cli ai-cache-check`, the check run before AI is turned on.
    require_enabled: bool = True
    # The trip plan a planner call belongs to.
    plan_id: str | None = None
    # The first call of a plan or follow-up: its row keeps counting toward the
    # daily question limit after the plan is deleted. Only the first attempt.
    question: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _error_parts(exc: anthropic.APIStatusError) -> tuple[str, str | None]:
    """(message, error_code) from the error body. Used only to classify."""
    body = exc.body if isinstance(exc.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    message = err.get("message") if isinstance(err.get("message"), str) else ""
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    code = details.get("error_code") if isinstance(details.get("error_code"), str) else None
    return message, code


def spend_limit_reason(exc: BaseException) -> str | None:
    """'workspace_limit', 'org_limit' or 'tier_cap' when the error is a spend limit,
    'billing' when it is a billing problem (402, or a 400 about the credit balance)."""
    if isinstance(exc, anthropic.APIStatusError) and (
        exc.status_code == 402 or exc.type == "billing_error"
    ):
        return "billing"
    if isinstance(exc, anthropic.BadRequestError):
        message, _ = _error_parts(exc)
        if message.startswith("You have reached your specified workspace"):
            return "workspace_limit"
        if message.startswith("You have reached your specified"):
            return "org_limit"
        if "credit balance" in message.lower():
            return "billing"
    if isinstance(exc, anthropic.RateLimitError):
        _, code = _error_parts(exc)
        if code == "enforced_spend_limit_reached":
            return "tier_cap"
    return None


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    value = exc.response.headers.get("retry-after")
    try:
        return min(float(value), MAX_RETRY_AFTER) if value is not None else None
    except ValueError:
        return None


def _transient(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            anthropic.InternalServerError,
            anthropic.ServiceUnavailableError,  # 503
            anthropic.DeadlineExceededError,  # 504
            anthropic.OverloadedError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
        ),
    ):
        return True
    # An error event inside a stream comes as a plain APIStatusError with the
    # stream's status, 200. Its type says what it is.
    return isinstance(exc, anthropic.APIStatusError) and exc.type in (
        "overloaded_error",
        "api_error",
    )


def pause_text(reason: str | None) -> str:
    """The person's text for a pause (`Block.reason`)."""
    return UNAVAILABLE_TEXT["billing" if reason == "billing" else "spend_limit"]


def unavailable_text(conn: sqlite3.Connection, reason: str) -> str:
    """The person's text for a status reason. A billing pause says so."""
    if reason == "spend_limit":
        block = ai_settings.load_block(conn)
        return pause_text(block.reason if block else None)
    return UNAVAILABLE_TEXT[reason]


class _Counted:
    """A stream that counts the characters of text, tool input and thinking it
    yields. The SDK keeps partial tool input only as raw JSON, so the snapshot
    alone cannot count it."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self.chars = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    async def __aiter__(self) -> AsyncIterator[Any]:
        async for event in self._stream:
            if event.type == "text":
                self.chars += len(event.text)
            elif event.type == "input_json":
                self.chars += len(event.partial_json)
            elif event.type == "thinking":
                self.chars += len(event.thinking)
            yield event


class _Attempt:
    """`client.messages` as one attempt's `request` sees it.

    Non-streaming calls get LONG_TIMEOUT. An opened stream is kept, because
    its final message carries no request id, and after a break its snapshot
    holds the usage and text that arrived before it, and it has counted the
    output it streamed.
    """

    def __init__(self, messages: Any) -> None:
        self._messages = messages
        self.stream_obj: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)

    async def create(self, **params: Any) -> Any:
        params.setdefault("timeout", LONG_TIMEOUT)
        return await self._messages.create(**params)

    async def parse(self, **params: Any) -> Any:
        params.setdefault("timeout", LONG_TIMEOUT)
        return await self._messages.parse(**params)

    @contextlib.asynccontextmanager
    async def stream(self, **params: Any) -> AsyncIterator[Any]:
        async with self._messages.stream(**params) as stream:
            self.stream_obj = _Counted(stream)
            yield self.stream_obj

    def request_id(self) -> str | None:
        return getattr(self.stream_obj, "request_id", None)

    def snapshot(self) -> Any:
        """The stream's message so far, or None before message_start."""
        if self.stream_obj is None:
            return None
        try:
            return self.stream_obj.current_message_snapshot
        except AssertionError:  # the SDK asserts when nothing has arrived yet
            return None

    def got_text(self) -> bool:
        """Text arrived. Ask may hold it unsent (it sends at sentence ends), so a
        retry would show it twice."""
        snap = self.snapshot()
        return snap is not None and any(
            getattr(b, "type", "") == "text" and b.text for b in snap.content
        )

    def searched(self) -> tuple[int, int, int]:
        """(web searches started, searches that returned results, the results'
        characters) in the stream so far. The usage counts searches only at
        message_delta. A started search may have run and been billed, so no
        retry after one; only returned results are counted as billed."""
        snap = self.snapshot()
        started, found, chars = 0, 0, 0
        for b in snap.content if snap is not None else []:
            kind = getattr(b, "type", "")
            if kind == "server_tool_use" and getattr(b, "name", "") == "web_search":
                started += 1
            elif kind == "web_search_tool_result" and isinstance(b.content, list):
                found += 1
                for r in b.content:
                    for field in ("encrypted_content", "title", "url"):
                        chars += len(getattr(r, field, None) or "")
        return started, found, chars


def _priced(info: CallInfo, message: Any, tokens: Tokens | None = None) -> tuple[Tokens, int]:
    """Tokens (from the message's usage unless given) and cost, priced for the
    model that served it."""
    if tokens is None:
        tokens = tokens_of(message.usage)
    served = getattr(message, "model", None) or info.model
    return tokens, cost_micro_usd(served if served in PRICES else info.model, tokens)


def _failed_row(
    info: CallInfo, attempt: _Attempt, exc: BaseException, started: float, request_id: str | None
) -> ledger.UsageRow:
    """A failed call's row. A stream that broke counts the usage it reported.
    Until message_delta (it sets stop_reason) that holds message_start's
    numbers: about 1 output token, the first prompt's input and no searches.
    So the output this attempt streamed is estimated from its characters, and
    the web searches it ran come from its result blocks, with their results'
    characters as input. Hidden thinking cannot be counted."""
    snap = attempt.snapshot()
    if snap is None:
        tokens, cost = Tokens(), 0
    else:
        tokens = tokens_of(snap.usage)
        if snap.stop_reason is None:
            _started, searches, chars = attempt.searched()
            tokens = replace(
                tokens,
                input=tokens.input + chars // CHARS_PER_TOKEN,
                output=max(tokens.output, attempt.stream_obj.chars // CHARS_PER_TOKEN),
                web_searches=max(tokens.web_searches, searches),
            )
        tokens, cost = _priced(info, snap, tokens)
    return ledger.UsageRow(
        info.feature,
        info.model,
        info.effort,
        info.user_id,
        info.thread_id,
        tokens=tokens,
        cost_micro_usd=cost,
        request_id=request_id or attempt.request_id(),
        latency_ms=int((time.monotonic() - started) * 1000),
        error=type(exc).__name__,
        plan_id=info.plan_id,
        question=info.question,
    )


def preflight(db_path: Path, settings: Settings, info: CallInfo) -> None:
    """Raise AiError unless AI is available and the caps allow this call.
    Raises DemoRefused in demo (the planner checks here without `call`)."""
    if in_demo():
        raise DemoRefused("ai call refused in demo")
    conn = connect(db_path)
    try:
        st = ai_settings.status(conn, settings)
        if st["reason"] == "off" and not info.require_enabled:
            if not st["key_present"]:
                raise AiError(UNAVAILABLE_TEXT["no_key"], "unavailable")
            block = ai_settings.load_block(conn)
            if block is not None:
                raise AiError(pause_text(block.reason), "unavailable")
        elif not st["available"]:
            raise AiError(unavailable_text(conn, st["reason"]), "unavailable")
        s = ai_settings.load(conn)
        try:
            ledger.check_caps(
                conn, s, info.feature, info.estimate_micro, _now(), new_question=info.new_question
            )
        except ledger.CapReached as cap:
            raise AiError(cap.message, "cap") from None
    finally:
        conn.close()


def _record(db_path: Path, row: ledger.UsageRow, block: str | None = None) -> None:
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            ledger.record(conn, row, _now())
            if block is not None:
                ai_settings.set_block(conn, block)  # type: ignore[arg-type]
    finally:
        conn.close()


async def call(
    settings: Settings,
    info: CallInfo,
    request: Callable[[Any], Awaitable[Any]],
    *,
    can_retry: Callable[[], bool] = lambda: True,
    record_cancel: bool = False,
) -> Any:
    """Run `request(client)` (it returns the final Message) under the rules above.
    `client` has only `messages`, tracked per attempt by `_Attempt`.
    `record_cancel`: a call cancelled mid-stream (the app stopping) writes its
    row, as a failed stream does. Ask records its own.
    Raises DemoRefused in demo, before any database opens."""
    if in_demo():
        raise DemoRefused("ai call refused in demo")
    db_path = settings.db_path
    await asyncio.to_thread(preflight, db_path, settings, info)
    client = make_client(settings)
    retried = False

    def retry_ok(attempt: _Attempt) -> bool:
        started = attempt.searched()[0]
        return not retried and can_retry() and not attempt.got_text() and not started

    while True:
        started = time.monotonic()
        attempt = _Attempt(client.messages)
        if retried:
            info = replace(info, question=False)
        try:
            message = await request(SimpleNamespace(messages=attempt))
        except asyncio.CancelledError as exc:
            if record_cancel:
                row = _failed_row(info, attempt, exc, started, None)
                try:
                    await asyncio.to_thread(_record, db_path, row)
                except Exception as err:
                    log.error("ai usage not recorded", extra={"exc_class": type(err).__name__})
            raise
        except anthropic.APIStatusError as exc:
            block = spend_limit_reason(exc)
            row = _failed_row(info, attempt, exc, started, exc.request_id)
            await asyncio.to_thread(_record, db_path, row, block)
            log.warning("ai call failed", extra={"exc_class": type(exc).__name__})
            if block is not None:
                raise AiError(pause_text(block), "spend_limit") from None
            if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
                raise AiError(KEY_TEXT, "failed") from None
            wait = None
            if isinstance(exc, anthropic.RateLimitError):
                wait = _retry_after(exc)
            elif _transient(exc):
                wait = RETRY_PAUSE
            if wait is not None and retry_ok(attempt):
                retried = True
                await asyncio.sleep(wait)
                continue
            raise AiError(FAILED_TEXT, "failed") from None
        except (
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            # A stream that drops or stalls after it started raises the raw error.
            httpx2.TransportError,
        ) as exc:
            row = _failed_row(info, attempt, exc, started, None)
            await asyncio.to_thread(_record, db_path, row)
            log.warning("ai call failed", extra={"exc_class": type(exc).__name__})
            if retry_ok(attempt):
                retried = True
                await asyncio.sleep(RETRY_PAUSE)
                continue
            raise AiError(FAILED_TEXT, "failed") from None
        except (pydantic.ValidationError, ValueError) as exc:
            # The API answered but the output did not parse. The call still happened.
            row = _failed_row(info, attempt, exc, started, None)
            await asyncio.to_thread(_record, db_path, row)
            raise
        latency = int((time.monotonic() - started) * 1000)
        tokens, cost = _priced(info, message)
        if tokens.inference_geo not in (None, "global"):
            log.warning("ai call not on global routing", extra={"geo": tokens.inference_geo})
        row = ledger.UsageRow(
            info.feature,
            info.model,
            info.effort,
            info.user_id,
            info.thread_id,
            tokens=tokens,
            cost_micro_usd=cost,
            stop_reason=getattr(message, "stop_reason", None),
            # A streamed final message has no request id. The stream has it.
            request_id=getattr(message, "_request_id", None) or attempt.request_id(),
            latency_ms=latency,
            plan_id=info.plan_id,
            question=info.question,
        )
        await asyncio.to_thread(_record, db_path, row)
        return message
