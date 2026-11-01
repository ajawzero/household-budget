"""Phase 6 fix pass 3 in the call wrapper (PLAN 7.2, 7.6):
- N15: non-streaming calls connect within 5 s and read for 600 s. Streams keep
  the client's own timeout.
- N16: a stream that breaks before message_delta counts the output it streamed
  (text, tool input, thinking), each attempt on its own.
- cost[2]: the pre-call estimate says it is not an upper bound.

The fake client covers the rules. The real SDK over an httpx2.MockTransport (no
network) checks the timeout it sends and the events it streams.
"""

from __future__ import annotations

import asyncio
import json

import anthropic
import httpx2
import pytest
from ai_fakes import (
    Broken,
    FakeClient,
    dropped,
    message,
    stream_error,
    text,
    thinking,
    tool_use,
)
from fastapi.testclient import TestClient
from test_ai_fix_client import (
    ANSWER,
    MESSAGE_START,
    OVERLOADED,
    SONNET,
    TODAY,
    _Server,
    _sse,
    asked,
    create_call,
    enable,
    rows,
    started,
    stream_call,
)

from app import config
from app.ai import client as ai_client
from app.ai import pricing
from app.ai.pricing import Tokens
from app.config import Settings, get_settings

ARGS = {"text": "x" * 3000}
TOOL_CHARS = len(json.dumps(ARGS))


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


# --------------------------------------------------------------------------
# N15: non-streaming timeouts
# --------------------------------------------------------------------------


def test_create_and_parse_connect_in_5_seconds_streams_keep_theirs(
    key, settings, db, fake, no_sleep
) -> None:
    enable()
    fake.script = [
        message([text("ok")], model="claude-opus-5-5"),
        message([text("{}")], model="claude-haiku-5-5"),
        message([text("ok")]),
    ]
    create_call()

    async def parse_request(client):
        return await client.messages.parse(model="claude-haiku-5-5", max_tokens=10, messages=[])

    info = ai_client.CallInfo("categorize", "claude-haiku-5-5", "low")
    asyncio.run(ai_client.call(get_settings(), info, parse_request))
    stream_call()
    (k1, p1), (k2, p2), (k3, p3) = fake.requests
    assert (k1, k2, k3) == ("create", "parse", "stream")
    for t in (p1["timeout"], p2["timeout"]):
        assert (t.connect, t.read, t.write, t.pool) == (5.0, 600.0, 600.0, 600.0)
    assert "timeout" not in p3


def test_real_sdk_create_connects_in_5_seconds(key, settings, db, server, no_sleep) -> None:
    enable()
    server.steps = [
        message([text("ok")], model="claude-opus-5-5").to_dict(),
        _sse(MESSAGE_START, *ANSWER),
    ]
    create_call()
    stream_call()
    created, streamed = (r.extensions["timeout"] for r in server.seen)
    assert created == {"connect": 5.0, "read": 600.0, "write": 600.0, "pool": 600.0}
    assert streamed == dict.fromkeys(("connect", "read", "write", "pool"), 180.0)


# --------------------------------------------------------------------------
# N16: a stream that breaks counts the output it streamed
# --------------------------------------------------------------------------


def test_a_break_after_tool_input_and_thinking_counts_them(
    key, settings, db, fake, no_sleep
) -> None:
    enable()
    fake.script = [
        Broken(
            stream_error(),
            started(thinking("t" * 600), tool_use("search_transactions", ARGS)),
        ),
        message([text("All good.")], input_tokens=5000, output_tokens=40),
    ]
    stream_call()
    assert len(fake.requests) == 2 and no_sleep == [ai_client.RETRY_PAUSE]
    failed, ok = rows()
    out = (600 + TOOL_CHARS) // 3
    assert (failed["error"], failed["input_tokens"], failed["output_tokens"]) == (
        "APIStatusError",
        5000,
        out,
    )
    assert failed["cost_micro_usd"] == 5000 * 2 + out * 10
    assert (ok["error"], ok["input_tokens"], ok["output_tokens"]) == (None, 5000, 40)


def test_each_attempt_counts_only_its_own_output(key, settings, db, fake, no_sleep) -> None:
    enable()
    small = {"text": "y" * 300}
    fake.script = [
        Broken(stream_error(), started(tool_use("search_transactions", ARGS))),
        Broken(dropped(), started(tool_use("search_transactions", small))),
    ]
    with pytest.raises(ai_client.AiError):
        stream_call()
    assert [(r["error"], r["output_tokens"]) for r in rows()] == [
        ("APIStatusError", TOOL_CHARS // 3),
        ("RemoteProtocolError", len(json.dumps(small)) // 3),
    ]


def test_a_break_after_message_delta_keeps_the_reported_output(
    key, settings, db, fake, no_sleep
) -> None:
    """message_delta sets stop_reason and the real output count. No estimate then."""
    enable()
    done = message([text("y" * 300)], "end_turn", SONNET, input_tokens=5000, output_tokens=30)
    fake.script = [Broken(dropped(), done)]
    with pytest.raises(ai_client.AiError):
        stream_call()
    (row,) = rows()
    assert (row["output_tokens"], row["cost_micro_usd"]) == (30, 5000 * 2 + 30 * 10)


def test_ask_counts_tool_input_from_a_broken_attempt(
    key, samples, admin: TestClient, fake, no_sleep
) -> None:
    enable()
    fake.script = [
        Broken(stream_error(), started(tool_use("search_transactions", ARGS))),
        message([text("All good.")]),
    ]
    ev = asked(admin, "How are we doing?")
    assert ev[-1]["type"] == "done" and len(fake.requests) == 2
    failed, ok = rows()
    assert (failed["feature"], failed["error"], failed["output_tokens"]) == (
        "ask",
        "APIStatusError",
        TOOL_CHARS // 3,
    )
    assert ok["error"] is None


THINK_START = {
    "type": "content_block_start",
    "index": 0,
    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
}
THINK = {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "thinking_delta", "thinking": "t" * 600},
}
TOOL_START = {
    "type": "content_block_start",
    "index": 1,
    "content_block": {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "search_transactions",
        "input": {},
    },
}
# Cut off mid-string: the SDK's parsed `input` drops it, so only the events count it.
PARTIAL = '{"text": "' + "x" * 3000
TOOL_PART = {
    "type": "content_block_delta",
    "index": 1,
    "delta": {"type": "input_json_delta", "partial_json": PARTIAL},
}


def test_real_sdk_break_after_tool_input_counts_it(key, settings, db, server, no_sleep) -> None:
    enable()
    server.steps = [
        _sse(
            MESSAGE_START,
            THINK_START,
            THINK,
            {"type": "content_block_stop", "index": 0},
            TOOL_START,
            TOOL_PART,
            OVERLOADED,
        ),
        _sse(MESSAGE_START, *ANSWER),
    ]
    final = stream_call()
    assert final.content[0].text == "Ok." and len(server.seen) == 2
    out = (600 + len(PARTIAL)) // 3
    assert [(r["error"], r["input_tokens"], r["output_tokens"]) for r in rows()] == [
        ("APIStatusError", 5000, out),
        (None, 5000, 30),
    ]


# --------------------------------------------------------------------------
# cost[2]: the estimate is not an upper bound, and says so
# --------------------------------------------------------------------------


def test_the_estimate_says_it_is_not_an_upper_bound() -> None:
    prompt, out = 80_000, 2_000
    estimate = pricing.estimate_micro_usd(SONNET, prompt, out)
    cold = pricing.cost_micro_usd(SONNET, Tokens(write_5m=prompt, output=out))
    assert estimate < cold
    doc = " ".join((pricing.estimate_micro_usd.__doc__ or "").split())
    assert "conservative" not in doc and "not an upper bound" in doc
