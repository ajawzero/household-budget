"""Phase 6 review fixes in the call wrapper: billing pause, mid-stream errors,
request ids, the non-streaming timeout and US-only pricing (PLAN 7.2, 7.6).

Most tests use the fake client. A few drive the real SDK over an
httpx2.MockTransport (no network) to check that the fake's shapes are right.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import date
from decimal import Decimal

import anthropic
import httpx2
import pytest
from ai_fakes import (
    Broken,
    FakeClient,
    bad_request,
    billing_error,
    dropped,
    message,
    parsed,
    stalled,
    stream_error,
    text,
    thinking,
)
from api_helpers import PROD_ORIGIN
from fastapi.testclient import TestClient

from app import config
from app.ai import client as ai_client
from app.ai import pricing, review
from app.ai import settings as ai_settings
from app.ai.pricing import Tokens, cost_micro_usd
from app.config import Settings, get_settings
from app.db.connect import connect, write_connection

TODAY = date(2026, 10, 20)
H = {"Origin": PROD_ORIGIN}
SONNET = "claude-sonnet-5-5"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake key, and an address where nothing listens, in case anything escapes."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-tests")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def fake_sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(ai_client.asyncio, "sleep", fake_sleep)
    return waits


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


def enable() -> None:
    with write_connection(get_settings().db_path) as conn:
        ai_settings.save(conn, ai_settings.AiSettings(enabled=True), None)


def rows() -> list[sqlite3.Row]:
    conn = connect(get_settings().db_path)
    out = conn.execute("SELECT * FROM ai_usage ORDER BY id").fetchall()
    conn.close()
    return out


def create_call(feature: str = "review") -> object:
    info = ai_client.CallInfo(feature, "claude-opus-5-5", "high")

    async def request(client):
        return await client.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[])

    return asyncio.run(ai_client.call(get_settings(), info, request))


def stream_call(can_retry=lambda: True) -> object:
    """A streamed call shaped like Ask's request."""
    info = ai_client.CallInfo("ask", SONNET, "medium")

    async def request(client):
        async with client.messages.stream(model=SONNET, max_tokens=10, messages=[]) as stream:
            async for _event in stream:
                pass
            return await stream.get_final_message()

    return asyncio.run(ai_client.call(get_settings(), info, request, can_retry=can_retry))


def started(*content: dict, input_tokens: int = 5000) -> object:
    """What the SDK holds after message_start and `content`: input usage, 1 output token."""
    return message(list(content), None, SONNET, input_tokens=input_tokens, output_tokens=1)


# --------------------------------------------------------------------------
# Billing problems pause AI like a spend limit (cost[3])
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        billing_error(),
        bad_request("Your credit balance is too low to access the Anthropic API."),
    ],
)
def test_a_billing_problem_pauses_ai(key, settings, db, fake, no_sleep, error) -> None:
    enable()
    fake.script = [error]
    with pytest.raises(ai_client.AiError) as e:
        create_call()
    assert e.value.kind == "spend_limit" and no_sleep == []
    assert e.value.message == ai_client.UNAVAILABLE_TEXT["billing"]
    block = ai_settings.load_block(db)
    assert block is not None and block.reason == "billing"
    st = ai_settings.status(db, get_settings())
    assert st["reason"] == "spend_limit"
    assert st["blocked_reason"] == ai_settings.BLOCK_TEXT["billing"]
    # The next call is not made, and says why.
    with pytest.raises(ai_client.AiError) as e:
        create_call()
    assert e.value.kind == "unavailable"
    assert e.value.message == ai_client.UNAVAILABLE_TEXT["billing"]
    assert len(fake.requests) == 1


def good_review() -> review.ReviewOut:
    return review.ReviewOut(
        summary="A steady month.",
        changes=["Spending fell a little."],
        anomalies=[],
        actions=["Pay the store card.", "Check the van loan.", "Move $500 to savings."],
    )


def test_a_billing_pause_skips_the_review_until_cleared(key, samples, fake) -> None:
    enable()
    s = get_settings()
    fake.script = [billing_error()]
    assert review.review_job_run(s) is None  # skipped, not stored as failed
    assert review.review_job_run(s) is None  # still paused: no call
    assert len(fake.requests) == 1
    with write_connection(s.db_path) as conn:
        ai_settings.clear_block(conn)
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s)["status"] == "ready"


# --------------------------------------------------------------------------
# Errors inside a stream (cost[4], spec-docs[3], backend-robustness[1], [2])
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["overloaded_error", "api_error"])
def test_an_error_event_before_any_text_is_retried(key, settings, db, fake, no_sleep, kind) -> None:
    enable()
    fake.script = [
        Broken(stream_error(kind), started(thinking())),
        message([thinking(), text("All good.")], input_tokens=5000, output_tokens=40),
    ]
    final = stream_call()
    assert final.content[-1].text == "All good."
    assert len(fake.requests) == 2 and no_sleep == [ai_client.RETRY_PAUSE]
    failed, ok = rows()
    assert (failed["error"], failed["input_tokens"], failed["output_tokens"]) == (
        "APIStatusError",
        5000,
        1,
    )
    assert failed["cost_micro_usd"] == 5000 * 2 + 1 * 10
    assert ok["error"] is None


def test_an_error_after_text_arrived_is_not_retried(key, settings, db, fake, no_sleep) -> None:
    """Ask sends text at sentence ends, so text can have arrived but not been sent."""
    enable()
    fake.script = [
        Broken(stream_error(), started(text("You spent"))),
        message([text("Again.")]),
    ]
    with pytest.raises(ai_client.AiError) as e:
        stream_call(can_retry=lambda: True)
    assert e.value.kind == "failed" and len(fake.requests) == 1 and no_sleep == []


def test_a_dropped_stream_is_recorded_and_retried(key, settings, db, fake, no_sleep) -> None:
    enable()
    fake.script = [Broken(dropped(), started(thinking())), message([text("All good.")])]
    stream_call()
    assert len(fake.requests) == 2
    failed, ok = rows()
    assert (failed["error"], failed["input_tokens"]) == ("RemoteProtocolError", 5000)
    assert failed["cost_micro_usd"] > 0
    assert (failed["request_id"], ok["request_id"]) == ("req_fake_1", "req_fake_2")


def test_a_stall_after_text_ends_the_call_with_a_row(key, settings, db, fake, no_sleep) -> None:
    enable()
    fake.script = [Broken(stalled(), started(text("Hello there"))), message([text("x")])]
    with pytest.raises(ai_client.AiError) as e:
        stream_call()
    assert e.value.message == ai_client.FAILED_TEXT and len(fake.requests) == 1
    (row,) = rows()
    assert (row["error"], row["input_tokens"]) == ("ReadTimeout", 5000)


def test_a_drop_before_message_start_counts_zero(key, settings, db, fake, no_sleep) -> None:
    enable()
    fake.script = [Broken(dropped()), Broken(dropped())]
    with pytest.raises(ai_client.AiError):
        stream_call()
    assert [(r["error"], r["input_tokens"], r["cost_micro_usd"]) for r in rows()] == [
        ("RemoteProtocolError", 0, 0),
        ("RemoteProtocolError", 0, 0),
    ]


# --------------------------------------------------------------------------
# Through Ask: retries, clean ends, request ids, the billing pause
# --------------------------------------------------------------------------


def events(r) -> list[dict]:
    assert r.status_code == 200, r.text
    return [
        json.loads(line[len("data: ") :])
        for chunk in r.text.split("\n\n")
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]


def asked(client: TestClient, question: str) -> list[dict]:
    return events(client.post("/api/ask", json={"question": question}, headers=H))


def test_ask_retries_an_overload_and_records_request_ids(
    key, samples, admin, fake, no_sleep
) -> None:
    enable()
    fake.script = [
        Broken(stream_error(), started(thinking())),
        message([thinking(), text("All good.")]),
    ]
    ev = asked(admin, "How are we doing?")
    assert ev[-1]["type"] == "done" and ev[-1]["text"] == "All good."
    assert ev[-1]["thread_closed"] is False and len(fake.requests) == 2
    assert [(r["error"], r["request_id"]) for r in rows()] == [
        ("APIStatusError", "req_fake_1"),
        (None, "req_fake_2"),
    ]


def test_ask_ends_cleanly_when_the_stream_drops_after_text(
    key, samples, admin, fake, no_sleep
) -> None:
    enable()
    fake.script = [Broken(dropped(), started(text("You are doing well. More soon")))]
    ev = asked(admin, "How are we doing?")
    assert ev[-1] == {"type": "error", "message": ai_client.FAILED_TEXT, "kind": "failed"}
    assert "text" in [e["type"] for e in ev] and len(fake.requests) == 1
    (row,) = rows()
    assert (row["feature"], row["error"], row["input_tokens"]) == (
        "ask",
        "RemoteProtocolError",
        5000,
    )


def test_ask_says_a_billing_pause(key, samples, admin, fake, no_sleep) -> None:
    enable()
    fake.script = [billing_error()]
    ev = asked(admin, "How are we doing?")
    assert ev[-1] == {
        "type": "error",
        "message": ai_client.UNAVAILABLE_TEXT["billing"],
        "kind": "spend_limit",
    }


# --------------------------------------------------------------------------
# The non-streaming timeout (cost[6])
# --------------------------------------------------------------------------


def test_review_and_categorize_get_the_long_timeout(key, samples, fake) -> None:
    enable()
    fake.script = [parsed(good_review())]
    assert review.review_job_run(get_settings())["status"] == "ready"
    kind, params = fake.requests[0]
    assert kind == "create" and params["timeout"] == ai_client.LONG_TIMEOUT
    assert params["timeout"].read == ai_client.LONG_TIMEOUT_SECONDS == 600

    async def request(client):
        return await client.messages.parse(model="claude-haiku-5-5", max_tokens=10, messages=[])

    fake.script = [message([text("{}")], model="claude-haiku-5-5")]
    info = ai_client.CallInfo("categorize", "claude-haiku-5-5", "low")
    asyncio.run(ai_client.call(get_settings(), info, request))
    assert fake.requests[1][0] == "parse" and fake.requests[1][1]["timeout"].read == 600


# --------------------------------------------------------------------------
# US-only inference costs 1.1x (cost[7])
# --------------------------------------------------------------------------


def test_us_only_inference_costs_more() -> None:
    t = Tokens(input=1000, write_5m=4000, cache_read=10_000, output=500)
    us = Tokens(input=1000, write_5m=4000, cache_read=10_000, output=500, inference_geo="us")
    glob = Tokens(input=1000, write_5m=4000, cache_read=10_000, output=500, inference_geo="global")
    base = cost_micro_usd(SONNET, t)
    assert cost_micro_usd(SONNET, glob) == base
    assert cost_micro_usd(SONNET, us) == int(Decimal(base) * Decimal("1.1"))
    # One rounding, after the multiplier: 4.8 x 1.1 = 5.28 gives 5, not 5 x 1.1 = 5.5 gives 6.
    assert cost_micro_usd("claude-haiku-5-5", Tokens(input=48, inference_geo="us")) == 5
    msg = message([text("ok")], geo="us")
    assert pricing.tokens_of(msg.usage).inference_geo == "us"


def test_a_us_only_call_is_priced_and_logged(key, settings, db, fake, no_sleep, caplog) -> None:
    enable()
    fake.script = [
        message([text("ok")], model="claude-opus-5-5", input_tokens=100, output_tokens=10, geo="us")
    ]
    with caplog.at_level(logging.WARNING, logger="app.ai"):
        create_call()
    (row,) = rows()
    assert row["cost_micro_usd"] == int((100 * 4 + 10 * 20) * Decimal("1.1"))
    assert [getattr(r, "geo", None) for r in caplog.records if r.name == "app.ai"] == ["us"]


# --------------------------------------------------------------------------
# The real SDK over a mock transport: the shapes the fake copies
# --------------------------------------------------------------------------


def _sse(*events: dict) -> list[bytes]:
    return [f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events]


MESSAGE_START = {
    "type": "message_start",
    "message": {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": SONNET,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 5000, "output_tokens": 1},
    },
}
ANSWER = (
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Ok."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 30}},
    {"type": "message_stop"},
)
OVERLOADED = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}


class _Server:
    """Scripted HTTP replies. Each step: a list of SSE chunks, optionally ending
    in an exception the body raises (a dropped connection), or a JSON body."""

    def __init__(self, steps: list) -> None:
        self.steps = list(steps)
        self.seen: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        n = len(self.seen)
        step = self.steps.pop(0)
        headers = {"request-id": f"req_real_{n}"}
        if isinstance(step, dict):
            return httpx2.Response(200, headers=headers, json=step)

        async def body():
            for chunk in step:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        headers["content-type"] = "text/event-stream"
        return httpx2.Response(200, headers=headers, content=body())


@pytest.fixture
def server() -> _Server:
    srv = _Server([])

    def factory(_s: Settings) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key="fake-key-for-tests",
            base_url="http://127.0.0.1:9",
            max_retries=0,
            timeout=ai_client.TIMEOUT_SECONDS,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(srv.handle)),
        )

    ai_client.set_client_factory(factory)
    yield srv
    ai_client.set_client_factory(None)


def test_real_sdk_stream_request_id_and_mid_stream_overload(
    key, settings, db, server, no_sleep
) -> None:
    enable()
    server.steps = [_sse(MESSAGE_START, OVERLOADED), _sse(MESSAGE_START, *ANSWER)]
    final = stream_call()
    assert final.content[0].text == "Ok." and len(server.seen) == 2
    assert [(r["error"], r["input_tokens"], r["request_id"]) for r in rows()] == [
        ("APIStatusError", 5000, "req_real_1"),
        (None, 5000, "req_real_2"),
    ]


def test_real_sdk_dropped_stream(key, settings, db, server, no_sleep) -> None:
    enable()
    drop = httpx2.RemoteProtocolError("peer closed connection", request=None)
    server.steps = [_sse(MESSAGE_START) + [drop], _sse(MESSAGE_START, *ANSWER)]
    stream_call()
    assert [(r["error"], r["input_tokens"], r["request_id"]) for r in rows()] == [
        ("RemoteProtocolError", 5000, "req_real_1"),
        (None, 5000, "req_real_2"),
    ]


def test_real_sdk_create_gets_the_long_timeout(key, settings, db, server) -> None:
    enable()
    reply = message([text("ok")], model="claude-opus-5-5").to_dict()
    server.steps = [reply]
    create_call()
    timeout = server.seen[0].extensions["timeout"]
    assert timeout["read"] == ai_client.LONG_TIMEOUT_SECONDS
    assert rows()[0]["request_id"] == "req_real_1"
