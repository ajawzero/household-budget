"""Phase 6 regression fixes for Ask: Month and Day take only YYYY-MM and
YYYY-MM-DD (a null is still left out), and the row of a call the person left
counts streamed thinking the way a broken stream's row does. Fake clients only."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from ai_fakes import Broken, FakeClient, stream_error, text, thinking, tool_use
from test_ai_features import TODAY, asked, enable, events
from test_ai_fix_ask import admin_user, db_rows, use
from test_ai_fix_client import started

from app import config
from app.ai import ask, tools
from app.ai import client as ai_client
from app.ai.facts import ensure_facts
from app.ai.safe import ai_connection
from app.api import ai as ai_api
from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture(autouse=True)
def no_slots_left() -> None:
    yield
    ai_api._asking.clear()


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake key, set before the app is built. Nothing can reach Anthropic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-canary-key-for-tests-4b1d")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


@pytest.fixture
def ai(samples: Settings):
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        yield conn


# --------------------------------------------------------------------------
# regress:ask-tools: strict Month and Day
# --------------------------------------------------------------------------

BASIC_DAY = "2026" + "0801"  # built here: the scanner rejects long digit runs
ARABIC_MONTH = "٢٠٢٦-08"  # digits outside ASCII


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("search_transactions", {"start_date": BASIC_DAY}),
        ("search_transactions", {"end_date": BASIC_DAY}),
        ("search_transactions", {"start_date": "2026-W31-6"}),
        ("search_transactions", {"end_date": "2026W316"}),
        ("budget_status", {"month": "2026-08\n"}),
        ("spending_summary", {"start_month": "2026-08\n", "end_month": "2026-08"}),
        ("cashflow", {"start_month": "2026-08", "end_month": "2026-08\n"}),
        ("net_worth_history", {"start_month": "2026-08\n"}),
        ("spending_summary", {"start_month": ARABIC_MONTH}),
    ],
)
def test_a_month_or_date_in_another_form_is_a_tool_error(ai, name: str, args: dict) -> None:
    ran = tools.execute(ai, name, args)
    assert ran.is_error and ran.content.startswith(f"Bad input for {name}")


# --------------------------------------------------------------------------
# regress:client: the person left mid-stream, thinking counted once
# --------------------------------------------------------------------------

THOUGHT = "t" * 600
ARGS = {"text": "groceries"}
SENTENCE = "We spent a lot on groceries this month. "
STREAMED = len(THOUGHT) + len(json.dumps(ARGS)) + len(SENTENCE)


class LeftMidStream:
    """Streams thinking, tool input and a sentence, like ai_fakes, then waits
    until it is cancelled."""

    def __init__(self) -> None:
        self.cancelled = 0
        self.messages = self

    def stream(self, **params):
        outer = self

        class Ctx:
            request_id = "req_left_1"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            def __aiter__(self):
                return self.events()

            async def events(self):
                usage = SimpleNamespace(input_tokens=5000, output_tokens=1)
                yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage))
                yield SimpleNamespace(type="thinking", thinking=THOUGHT)
                yield SimpleNamespace(type="input_json", partial_json=json.dumps(ARGS))
                yield SimpleNamespace(type="text", text=SENTENCE)
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    outer.cancelled += 1
                    raise

        return Ctx()


def left_mid_stream() -> None:
    s = get_settings()
    user = admin_user()
    slow = LeftMidStream()
    use(slow)

    async def main() -> None:
        ai_api._asking[user.id] = time.monotonic()
        p = ask.prepare(s, user, "How much did we spend?", None)
        got_text = asyncio.Event()

        async def consume() -> None:
            async for line in ai_api.ask_events(s, user.id, p):
                if '"type": "text"' in line:
                    got_text.set()

        task = asyncio.create_task(consume())
        await got_text.wait()
        task.cancel()  # the browser went away
        await asyncio.wait({task})
        for _ in range(250):
            if user.id not in ai_api._asking:
                break
            await asyncio.sleep(0.02)
        assert user.id not in ai_api._asking

    try:
        asyncio.run(main())
    finally:
        ai_client.set_client_factory(None)
    assert slow.cancelled == 1


def test_a_call_the_person_left_counts_streamed_thinking_once(key, samples, admin) -> None:
    enable()
    left_mid_stream()
    assert db_rows("SELECT error, input_tokens, output_tokens FROM ai_usage") == [
        ("CancelledError", 5000, STREAMED // ai_client.CHARS_PER_TOKEN)
    ]


def test_left_and_broken_count_the_same_output(key, samples, admin, fake) -> None:
    enable()
    partial = started(thinking(THOUGHT), tool_use("search_transactions", ARGS), text(SENTENCE))
    fake.script = [Broken(stream_error(), partial)]
    assert events(asked(admin, "How much did we spend?"))[-1]["type"] == "error"
    left_mid_stream()
    broke, left = db_rows("SELECT error, input_tokens, output_tokens FROM ai_usage ORDER BY id")
    assert broke[0] == "APIStatusError" and left[0] == "CancelledError"
    assert broke[1:] == left[1:] == (5000, STREAMED // ai_client.CHARS_PER_TOKEN)
