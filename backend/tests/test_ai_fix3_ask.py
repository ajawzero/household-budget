"""Phase 6 re-verification fixes for Ask: tool results re-scrubbed value by value
(the same bytes when nothing changed, non-ASCII private names caught), a reply
that was only thinking, null months and dates, the category a flow means, the
request id of a cancelled call, a cancel during the queue drain, the slot of a
long stream, and the rollover values. Fake clients only (ai_fakes.py and the
slow ones here)."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from ai_fakes import FakeClient, message, text, thinking, tool_use
from test_ai_features import TODAY, asked, enable, events
from test_ai_fix_ask import SlowClient, a_last4, admin_user, db_rows, use, write_rules
from test_ai_fix_facts import call

from app import config
from app.ai import ask, tools
from app.ai import client as ai_client
from app.ai.facts import ensure_facts
from app.ai.postprocess import PRIVATE_LABEL, Scrub
from app.ai.safe import ai_connection
from app.api import ai as ai_api
from app.config import Settings, get_settings
from app.db.connect import write_connection
from app.importer.sanitize import PrivateRules


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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-canary-key-for-tests-3a9c")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


@pytest.fixture
def rules_file(settings, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A private rules file the test can change (it is read on every use)."""
    path = tmp_path / "private_rules.json"
    write_rules(path)
    monkeypatch.setenv("BUDGET_PRIVATE_RULES", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


@pytest.fixture
def ai(samples: Settings):
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        yield conn


ACCENTED = "Gl\u00f6wbox Plus"  # a private name with a letter outside ASCII


def said(t: str) -> dict:
    return {"type": "thinking", "thinking": t, "signature": "fake-signature"}


# --------------------------------------------------------------------------
# N7, N17, N20, P6: stored tool results are re-scrubbed as values, not as text
# --------------------------------------------------------------------------


def test_a_non_ascii_name_made_private_later_is_not_sent_again(
    key, rules_file, samples, admin, fake
) -> None:
    enable()
    with write_connection(get_settings().db_path) as w:
        w.execute("UPDATE transactions SET merchant = ? WHERE amount_cents < 0", (ACCENTED,))
    # The name is only in the tool result and the thinking, never in the question.
    search = tool_use("search_transactions", {"limit": 3, "direction": "out"})
    fake.script = [
        message([said("Search recent rows."), search], "tool_use"),
        message([said(f"The rows are all {ACCENTED}."), text("Three charges.")]),
    ]
    tid = events(asked(admin, "What were the last charges?"))[-1]["thread_id"]
    first = fake.requests[1][1]["messages"][2]["content"][0]["content"]
    assert json.loads(first)["rows"][0]["merchant"] == ACCENTED  # not private yet

    write_rules(rules_file, ACCENTED)  # put on the private list afterwards
    fake.script = [message([text("Okay.")])]
    ev = events(asked(admin, "Anything else?", tid))
    assert ev[0]["thread_id"] == tid
    sent = fake.requests[-1][1]
    for body in (json.dumps(sent), json.dumps(sent, ensure_ascii=False)):
        assert "wbox" not in body  # neither the name nor its \u escape
    result = sent["messages"][2]["content"][0]["content"]
    assert PRIVATE_LABEL in json.loads(result)["rows"][0]["merchant"]
    # What was sent before changed, so no thinking block goes again.
    assert all(b["type"] != "thinking" for m in sent["messages"] for b in m["content"])


@pytest.mark.parametrize("ascii_only", [True, False])
def test_a_stored_result_is_scrubbed_however_it_was_written(ascii_only: bool) -> None:
    """Rows written with \\u escapes (before this fix) and as is (now) both lose the name."""
    rows = {"rows": [{"merchant": ACCENTED, "amount": "-12.00"}], "total_matching": 1}
    stored = json.dumps(rows, separators=(",", ":"), sort_keys=True, ensure_ascii=ascii_only)
    history = [
        {"role": "user", "content": [text("Q?")]},
        {"role": "assistant", "content": [tool_use("search_transactions", {})]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": stored}],
        },
    ]
    scrub = Scrub(PrivateRules(subscriptions=(ACCENTED,)))
    out = ask.to_send(history, scrub)[2]["content"][0]["content"]
    assert "wbox" not in out and json.loads(out)["rows"][0]["merchant"] == PRIVATE_LABEL
    assert out == tools.dump(json.loads(out))  # one serializer


def test_an_unchanged_scrub_resends_tool_results_byte_for_byte(key, samples, admin, fake) -> None:
    enable()
    last4 = a_last4()
    assert not last4.startswith("0")
    with write_connection(get_settings().db_path) as w:
        w.execute(
            "UPDATE accounts SET display_name = display_name || ' ending ' || last4 "
            "WHERE last4 IS NOT NULL"
        )
    fake.script = [
        message(
            [
                tool_use("search_transactions", {"limit": 50}, id="toolu_1"),
                # A number equal to a last four is left alone in a result.
                tool_use("search_transactions", {"offset": int(last4)}, id="toolu_2"),
            ],
            "tool_use",
        ),
        message([thinking(), text("Done.")]),
        message([text("Again.")]),
    ]
    tid = events(asked(admin, "Recent charges?"))[-1]["thread_id"]
    events(asked(admin, "And now?", tid))
    in_turn, follow_up = fake.requests[1][1]["messages"], fake.requests[2][1]["messages"]
    assert follow_up[: len(in_turn)] == in_turn  # the same bytes, so cache and thinking hold
    assert follow_up[3]["content"][0] == thinking()
    first, second = (json.loads(r["content"]) for r in in_turn[2]["content"])
    assert any("ending …" in row["account"] for row in first["rows"])
    assert second["offset"] == int(last4)


# --------------------------------------------------------------------------
# N23: a reply that was only thinking is never sent empty
# --------------------------------------------------------------------------


def _roles_ok(sent: list[dict]) -> None:
    assert all(m["content"] for m in sent)
    assert all(a["role"] != b["role"] for a, b in zip(sent, sent[1:], strict=False))


def test_a_thinking_only_reply_is_left_out_when_thinking_is_dropped() -> None:
    q1, q2 = text("Q1?"), text("Q2?")
    history = [
        {"role": "user", "content": [q1]},
        {"role": "assistant", "content": [thinking()]},
        {"role": "user", "content": [q2]},
    ]
    sent = ask.to_send(history, Scrub(None), drop_thinking=2)
    _roles_ok(sent)
    assert sent == [{"role": "user", "content": [q1, q2]}]
    # After a tool result too.
    result = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}"}
    history = [
        {"role": "user", "content": [q1]},
        {"role": "assistant", "content": [tool_use("cashflow", {})]},
        {"role": "user", "content": [result]},
        {"role": "assistant", "content": [thinking()]},
        {"role": "user", "content": [q2]},
    ]
    sent = ask.to_send(history, Scrub(None), drop_thinking=4)
    _roles_ok(sent)
    assert sent[-1] == {"role": "user", "content": [result, q2]}
    # Nothing dropped: sent as stored.
    assert ask.to_send(history, Scrub(None)) == history


def test_a_thinking_only_answer_then_a_new_private_name(
    key, rules_file, samples, admin, fake
) -> None:
    enable()
    fake.script = [message([thinking()]), message([text("Okay.")])]
    tid = events(asked(admin, f"Did we pay {ACCENTED}?"))[-1]["thread_id"]
    write_rules(rules_file, ACCENTED)
    assert events(asked(admin, "Anything else?", tid))[-1]["type"] == "done"
    sent = fake.requests[-1][1]["messages"]
    _roles_ok(sent)
    assert "wbox" not in json.dumps(sent)


# --------------------------------------------------------------------------
# N1: a null month or date is the same as leaving it out
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("budget_status", {"month": None}),
        ("cashflow", {"start_month": "2026-08", "end_month": None}),
        ("net_worth_history", {"start_month": None, "end_month": None}),
        ("search_transactions", {"text": "a", "start_date": None, "end_date": None}),
        ("spending_summary", {"start_month": "2026-08", "end_month": None}),
    ],
)
def test_a_null_month_or_date_is_left_out(ai, name: str, args: dict) -> None:
    call(ai, name, args)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("budget_status", {"month": "2026-13"}),
        ("cashflow", {"start_month": "2026-8"}),
        ("search_transactions", {"start_date": "2026-02-30"}),
        ("search_transactions", {"end_date": 20}),
    ],
)
def test_a_bad_month_or_date_is_still_a_tool_error(ai, name: str, args: dict) -> None:
    ran = tools.execute(ai, name, args)
    assert ran.is_error and ran.content.startswith(f"Bad input for {name}")


def test_ask_survives_a_null_month(key, samples, admin, fake) -> None:
    enable()
    fake.script = [
        message([tool_use("budget_status", {"month": None})], "tool_use"),
        message([text("Done.")]),
    ]
    ev = events(asked(admin, "Budget?"))
    assert ev[-1]["type"] == "done" and ev[-1]["thread_closed"] is False
    result = fake.requests[1][1]["messages"][2]["content"][0]
    assert result["is_error"] is False


# --------------------------------------------------------------------------
# N4: a category name is read for the flow asked about
# --------------------------------------------------------------------------


def test_interest_in_spending_is_card_interest(ai) -> None:
    aug = {"start_month": "2026-08", "end_month": "2026-08"}
    for name in ("Interest", "interest"):
        r = call(ai, "spending_summary", {**aug, "category": name})
        assert r["matched_categories"] == ["Card interest"]
    income = call(ai, "spending_summary", {**aug, "category": "Interest", "flow": "income"})
    assert income["matched_categories"] == ["Interest"]
    # A category no row of the flow can have is still found, not an error.
    paycheck = call(ai, "spending_summary", {**aug, "category": "Paycheck"})
    assert paycheck["matched_categories"] == ["Paycheck"]
    # Search has no flow: the exact name still wins.
    s = call(ai, "search_transactions", {"category": "Interest"})
    assert s["matched_categories"] == ["Interest"]


# --------------------------------------------------------------------------
# N18, N33: a cancelled call's row has its request id
# --------------------------------------------------------------------------


class SlowWithId(SlowClient):
    def stream(self, **params):
        ctx = super().stream(**params)
        ctx.request_id = "req_slow_1"  # like the SDK's open stream
        return ctx


def test_a_cancelled_call_logs_its_request_id(key, samples, admin) -> None:
    enable()
    s = get_settings()
    user = admin_user()
    slow = SlowWithId()
    use(slow)

    async def main() -> None:
        slow.gate = asyncio.Event()
        ai_api._asking[user.id] = time.monotonic()
        p = ask.prepare(s, user, "How much?", None)
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
    assert db_rows("SELECT error, request_id FROM ai_usage") == [("CancelledError", "req_slow_1")]


# --------------------------------------------------------------------------
# N21, P14, P19 (cost[1]): a cancel that lands while the turn drains its queue
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ticks", range(7))
@pytest.mark.parametrize("between_steps", [False, True])
def test_a_cancel_during_the_queue_drain_stops_the_turn(
    key, samples, admin, fake, monkeypatch, ticks: int, between_steps: bool
) -> None:
    """Disconnect an active response, including while its latest step is done.

    Cancelling that completed step does nothing. The response consumer must be
    cancelled so ask_events can cancel a pending step or close a yielded turn.
    """
    enable()
    s = get_settings()
    user = admin_user()
    fake.script = [
        message([tool_use("search_transactions", {"limit": 1})], "tool_use"),
        message([text("A second call, made after the cancel.")]),
    ]
    real_call = ai_client.call
    holder: dict = {}

    async def call_then_cancel(*a, **k):
        out = await real_call(*a, **k)
        if "armed" not in holder:  # the first call has ended: cancel a few ticks later
            holder["armed"] = True
            if between_steps:
                await holder["status_yielded"].wait()
            loop = asyncio.get_running_loop()

            def later(n: int) -> None:
                if n:
                    loop.call_soon(later, n - 1)
                else:
                    holder["cancel_accepted"] = holder["consumer"].cancel()

            loop.call_soon(later, ticks)
        return out

    monkeypatch.setattr(ai_client, "call", call_then_cancel)
    real_when_settled = ask.when_settled

    def watch_cleanup(prepared, free) -> None:
        def freed() -> None:
            free()
            holder["settled"].set()

        real_when_settled(prepared, freed)

    monkeypatch.setattr(ask, "when_settled", watch_cleanup)

    async def main() -> ask.Prepared:
        holder["status_yielded"] = asyncio.Event()
        holder["settled"] = asyncio.Event()
        until_cancel = asyncio.Event()
        ai_api._asking[user.id] = time.monotonic()
        p = ask.prepare(s, user, "How many?", None)

        async def consume() -> None:
            async with aclosing(ai_api.ask_events(s, user.id, p)) as stream:
                async for line in stream:
                    if between_steps and '"type": "status"' in line:
                        holder["status_yielded"].set()
                        # Hold the response at a yield, as during an ASGI send.
                        # The disconnect must close this already-yielded turn.
                        await until_cancel.wait()

        holder["consumer"] = asyncio.create_task(consume())
        async with asyncio.timeout(10):
            await asyncio.wait({holder["consumer"]})
            await holder["settled"].wait()
        assert holder["cancel_accepted"] and holder["consumer"].cancelled()
        assert p.settling is not None and p.settling.done()
        assert user.id not in ai_api._asking
        return p

    p = asyncio.run(main())
    assert len(fake.requests) == 1  # no model call after the cancel
    assert db_rows("SELECT closed FROM ai_threads WHERE thread_id = ?", p.thread_id) == [(1,)]


# --------------------------------------------------------------------------
# N22, P14: a long stream that never goes quiet keeps its slot
# --------------------------------------------------------------------------


class ChattyClient:
    """Streams a sentence every `gap` seconds, `count` times."""

    def __init__(self, count: int, gap: float) -> None:
        self.count, self.gap = count, gap
        self.messages = self

    def stream(self, **params):
        outer = self

        class Ctx:
            request_id = "req_chatty"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            def __aiter__(self):
                return self.events()

            async def events(self):
                usage = SimpleNamespace(input_tokens=1_000, output_tokens=1)
                yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage))
                for _ in range(outer.count):
                    await asyncio.sleep(outer.gap)
                    yield SimpleNamespace(type="text", text="More. ")

            async def get_final_message(self):
                return message([text("More. " * outer.count)])

        return Ctx()


def test_a_long_chatty_stream_keeps_its_slot(key, samples, admin, monkeypatch) -> None:
    enable()
    monkeypatch.setattr(ai_api, "ASK_SLOT_SECONDS", 0.3)
    monkeypatch.setattr(ai_api, "KEEPALIVE_SECONDS", 0.2)  # never quiet that long
    s = get_settings()
    user = admin_user()
    use(ChattyClient(count=16, gap=0.05))

    async def main() -> list[float]:
        ai_api._asking[user.id] = time.monotonic()  # what post_ask does
        p = ask.prepare(s, user, "Long answer?", None)
        ages = []
        async for _line in ai_api.ask_events(s, user.id, p):
            # post_ask's own check: a second question now would be refused.
            ages.append(time.monotonic() - ai_api._asking[user.id])
        return ages

    try:
        ages = asyncio.run(main())
    finally:
        ai_client.set_client_factory(None)
    assert len(ages) > 10
    assert max(ages) < ai_api.ASK_SLOT_SECONDS
    assert user.id not in ai_api._asking  # freed at the end


# --------------------------------------------------------------------------
# N34, P32: the rollover values are pinned (PLAN 7.3)
# --------------------------------------------------------------------------


def test_a_thread_under_the_rollover_values_goes_on(key, samples, admin, fake) -> None:
    enable()
    fake.script = [message([text("One.")]), message([text("Two.")])]
    tid = events(asked(admin, "First?"))[-1]["thread_id"]
    with write_connection(get_settings().db_path) as w:
        w.execute(
            "UPDATE ai_threads SET turn_count = ?, prompt_tokens = ? WHERE thread_id = ?",
            (9, 79_999, tid),
        )
    ev = events(asked(admin, "Second?", tid))
    assert ev[0]["new"] is False and ev[0]["thread_id"] == tid
    assert len(fake.requests[1][1]["messages"]) == 3
