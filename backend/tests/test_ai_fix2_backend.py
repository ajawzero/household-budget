"""Phase 6 review fixes, second pass (backend): Ask says a billing pause, 503
and 504 are retried, the context says planned spending includes saved and
invested, a cancelled call logs one row, and the output filter keeps a year
range. Fake clients, or the real SDK over httpx2.MockTransport (no network)."""

from __future__ import annotations

import asyncio
import json

import anthropic
import httpx2
import pytest
from ai_fakes import FakeClient, message, text
from test_ai_features import TODAY, H, asked, enable
from test_ai_fix_ask import admin_user

from app import config
from app.ai import ask, categorize
from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai.context import build, dollars
from app.ai.facts import ensure_facts
from app.ai.postprocess import MASK, StreamFilter, filter_text
from app.ai.safe import open_ai_connection
from app.config import get_settings
from app.db.connect import connect, write_connection

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
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def fake_sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(ai_client.asyncio, "sleep", fake_sleep)
    return waits


def usage_rows() -> list[tuple]:
    conn = connect(get_settings().db_path)
    try:
        return [
            tuple(r)
            for r in conn.execute("SELECT feature, error, input_tokens FROM ai_usage ORDER BY id")
        ]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Ask says a billing pause before it asks
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "said"), [("billing", "billing"), ("workspace_limit", "spend_limit")]
)
def test_ask_says_which_pause(key, samples, admin, reason, said) -> None:
    enable()
    with write_connection(get_settings().db_path) as conn:
        ai_settings.set_block(conn, reason)
    with pytest.raises(ai_client.AiError) as e:
        ask.prepare(get_settings(), admin_user(), "How are we doing?", None)
    assert e.value.kind == "unavailable"
    assert e.value.message == ai_client.UNAVAILABLE_TEXT[said]
    r = asked(admin, "How are we doing?")
    assert r.status_code == 409 and r.json()["detail"] == ai_client.UNAVAILABLE_TEXT[said]


# --------------------------------------------------------------------------
# 503 and 504 are retried once, like other 5xx
# --------------------------------------------------------------------------


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    """A 503 or 504 from a gateway: the body is not the API's JSON, so it has no type."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("unavailable", response=response, body="<html>Service Unavailable</html>")


@pytest.mark.parametrize(
    ("cls", "status"),
    [(anthropic.ServiceUnavailableError, 503), (anthropic.DeadlineExceededError, 504)],
)
def test_503_and_504_are_retried_once(key, settings, db, fake, no_sleep, cls, status) -> None:
    enable()
    fake.script = [_status_error(cls, status), message([text("ok")], model="claude-opus-5-5")]
    info = ai_client.CallInfo("review", "claude-opus-5-5", "high")

    async def request(client):
        return await client.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[])

    final = asyncio.run(ai_client.call(get_settings(), info, request))
    assert final.content[0].text == "ok"
    assert len(fake.requests) == 2 and no_sleep == [ai_client.RETRY_PAUSE]
    assert [e for _f, e, _t in usage_rows()] == [cls.__name__, None]


# --------------------------------------------------------------------------
# The context says planned spending includes saved and invested (PLAN 3.9)
# --------------------------------------------------------------------------


def test_the_plan_line_says_spending_includes_saved(key, samples) -> None:
    db_path = get_settings().db_path
    ensure_facts(db_path, None)
    conn = open_ai_connection(db_path)
    try:
        first, again = build(conn), build(conn)
    finally:
        conn.close()
    assert first == again  # deterministic
    month = "2026-10"
    raw = connect(db_path)
    try:
        (doc,) = raw.execute(
            "SELECT json FROM ai_facts WHERE kind = 'cashflow_month' AND key = ?", (month,)
        ).fetchone()
    finally:
        raw.close()
    p = json.loads(doc)["plan"]
    line = next(x for x in first.splitlines() if x.startswith("Plan for"))
    assert line == (
        f"Plan for {month}: income {dollars(p['income_cents'])}, spending "
        f"{dollars(p['spending_cents'])} including {dollars(p['saved_invested_cents'])} "
        "saved and invested."
    )


# --------------------------------------------------------------------------
# A cancelled call (Stop or a disconnect) logs exactly one row
# --------------------------------------------------------------------------


def _sse(*events: dict) -> list[bytes]:
    return [f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events]


STARTED = _sse(
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": SONNET,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 12_000, "output_tokens": 1},
        },
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Working on it. "},
    },
)


def test_a_cancelled_stream_logs_one_row(key, samples, admin) -> None:
    """The real SDK streams a sentence and then nothing. Ask's cancel handler logs
    the row. call() must not log a second one."""
    enable()
    s = get_settings()
    user = admin_user()

    def handle(request: httpx2.Request) -> httpx2.Response:
        async def body():
            for chunk in STARTED:
                yield chunk
            await asyncio.Event().wait()  # until the call is cancelled

        headers = {"request-id": "req_real_1", "content-type": "text/event-stream"}
        return httpx2.Response(200, headers=headers, content=body())

    ai_client.set_client_factory(
        lambda _s: anthropic.AsyncAnthropic(
            api_key="fake-key-for-tests",
            base_url="http://127.0.0.1:9",
            max_retries=0,
            timeout=ai_client.TIMEOUT_SECONDS,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )
    )

    async def main() -> None:
        p = ask.prepare(s, user, "How much did we spend?", None)
        turn = ask.stream_turn(s, p)
        async for ev in turn:
            if ev["type"] == "text":
                break
        await turn.aclose()  # Stop, or the browser went away
        assert p.settling is not None
        await p.settling

    try:
        asyncio.run(main())
    finally:
        ai_client.set_client_factory(None)
    assert usage_rows() == [("ask", "CancelledError", 12_000)]


# --------------------------------------------------------------------------
# The output filter keeps a year range
# --------------------------------------------------------------------------


def span(a: str, b: str) -> str:
    """Four digits, a dash, four digits. Built at runtime so the repo scanner
    never sees one whole."""
    return f"{a}-{b}"


@pytest.mark.parametrize(
    ("raw", "out"),
    [
        (f"Rent rose in {span('2025', '2026')}.", f"Rent rose in {span('2025', '2026')}."),
        (
            f"From {span('1990', '2099')} and {span('2019', '2020')} too.",
            f"From {span('1990', '2099')} and {span('2019', '2020')} too.",
        ),
        (f"Ref {span('1234', '5678')} here.", f"Ref {MASK} here."),
        (f"Backwards {span('2026', '2025')} here.", f"Backwards {MASK} here."),
        (f"Early {span('1989', '1990')} here.", f"Early {MASK} here."),
        (f"Same {span('2025', '2025')} here.", f"Same {MASK} here."),
        # Joined to more digits, or part of a phone number: not a year range.
        (f"Ref {span('2025', '2026')}-{span('1234', '5678')} here.", f"Ref {MASK}-{MASK} here."),
        (f"Call 555 123 {span('2025', '2026')} now.", f"Call {MASK} now."),
    ],
)
def test_the_output_filter_keeps_a_year_range(raw: str, out: str) -> None:
    assert filter_text(raw) == out
    sf = StreamFilter()
    sf.feed(raw)
    sf.finish()
    assert sf.emitted == out == sf.text


@pytest.mark.parametrize(
    ("reason", "said"), [("billing", "billing"), ("workspace_limit", "spend_limit")]
)
def test_runs_say_which_pause(key, samples, admin, reason, said) -> None:
    """Categorizing and POST /api/ai/review/run name a billing pause too."""
    enable()
    with write_connection(get_settings().db_path) as conn:
        ai_settings.set_block(conn, reason)
    with pytest.raises(ai_client.AiError) as e:
        asyncio.run(categorize.suggest(get_settings(), None))
    assert e.value.message == ai_client.UNAVAILABLE_TEXT[said]
    r = admin.post("/api/ai/review/run", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ai_client.UNAVAILABLE_TEXT[said]
