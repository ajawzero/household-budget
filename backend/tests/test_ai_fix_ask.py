"""Phase 6 review fixes for Ask, the AI routes and the Dashboard: the last scrub
on what a follow-up sends again, a typed last four, the household context pinned
per thread, Stop and disconnects (the call is cancelled and logged, the slot
held until then, no event-loop stall), keepalives, background runs, review
gating, and tests for cap estimates, the scrub wiring and thread rollover.
Fake clients only (ai_fakes.py and the slow ones here)."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import anyio
import pytest
from ai_fakes import FakeClient, message, parsed, text, thinking, tool_use
from api_helpers import PROD_ORIGIN
from test_ai_features import TODAY, asked, enable, events, good_review

from app import config
from app.ai import ask
from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai.cache_check import cache_check
from app.ai.postprocess import PRIVATE_LABEL
from app.api import ai as ai_api
from app.auth.users import get_user
from app.config import get_settings
from app.db import connect as db_connect
from app.db.connect import connect, write_connection

H = {"Origin": PROD_ORIGIN}
CANARY = "fake-canary-key-for-tests-5d1e"
NAME = "Glowbox Plus"
RUNNING = {"status": "running", "started": True}


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
    monkeypatch.setenv("ANTHROPIC_API_KEY", CANARY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


def use(client: object) -> None:
    ai_client.set_client_factory(lambda _s: client)


@pytest.fixture
def rules_file(settings, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A private rules file the test can change (it is read on every use)."""
    path = tmp_path / "private_rules.json"
    write_rules(path)
    monkeypatch.setenv("BUDGET_PRIVATE_RULES", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def write_rules(path, *extra: str) -> None:
    path.write_text(
        json.dumps(
            {
                "personal_subscriptions": ["EXAMPLE PRIVATE SERVICE", *extra],
                "employer_terms": ["EXAMPLE EMPLOYER CO"],
            }
        )
    )


def db_rows(sql: str, *args) -> list[tuple]:
    conn = connect(get_settings().db_path)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def a_last4() -> str:
    return db_rows("SELECT last4 FROM accounts WHERE last4 IS NOT NULL ORDER BY account_key")[0][0]


def admin_user():
    conn = connect(get_settings().db_path)
    try:
        (uid,) = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        return get_user(conn, uid)
    finally:
        conn.close()


def wait_for(check, seconds: float = 5.0) -> None:
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


# --------------------------------------------------------------------------
# privacy[0], spec-docs[1]: what a follow-up sends again goes through the scrub
# --------------------------------------------------------------------------


def test_a_name_made_private_later_is_not_sent_again(key, rules_file, samples, admin, fake) -> None:
    enable()
    with write_connection(get_settings().db_path) as w:
        w.execute(
            "UPDATE transactions SET merchant = ? WHERE rowid IN (SELECT rowid FROM "
            "transactions WHERE merchant IS NOT NULL AND amount_cents < 0 LIMIT 2)",
            (NAME,),
        )
    fake.script = [
        message([thinking(), tool_use("search_transactions", {"text": "glowbox"})], "tool_use"),
        message([thinking(), text(f"You paid {NAME} twice.")]),
    ]
    first = events(asked(admin, f"What did we pay {NAME}?"))
    tid = first[-1]["thread_id"]
    assert NAME in json.dumps(fake.requests[1][1]["messages"])  # not private yet

    write_rules(rules_file, NAME)  # the owner puts it on the private list
    fake.script = [message([text("Okay.")])]
    second = events(asked(admin, "Anything else this month?", tid))
    assert second[0]["thread_id"] == tid and second[0]["new"] is False
    sent = fake.requests[-1][1]
    body = json.dumps(sent)
    assert "Glowbox" not in body and PRIVATE_LABEL in body
    # The question, the tool result and the answer are all still there, scrubbed.
    m = sent["messages"]
    assert [x["role"] for x in m] == ["user", "assistant", "user", "assistant", "user"]
    assert m[0]["content"][0]["text"].endswith(f"What did we pay {PRIVATE_LABEL}?")
    assert m[3]["content"] == [text(f"You paid {PRIVATE_LABEL} twice.")]
    # What earlier turns send changed, so their thinking blocks are left out.
    assert all(b["type"] != "thinking" for x in m for b in x["content"])
    # The stored rows are not edited.
    stored = db_rows("SELECT content_json FROM ai_messages WHERE thread_id = ?", tid)
    assert any(NAME in r[0] for r in stored)


def test_a_follow_up_resends_thinking_while_nothing_changed(key, samples, admin, fake) -> None:
    enable()
    fake.script = [
        message([thinking(), text("First.")]),
        message([thinking(), text("Second.")]),
        message([text("Third.")]),
    ]
    tid = events(asked(admin, "One?"))[-1]["thread_id"]
    events(asked(admin, "Two?", tid))
    events(asked(admin, "Three?", tid))
    m2, m3 = fake.requests[1][1]["messages"], fake.requests[2][1]["messages"]
    assert m3[: len(m2)] == m2  # append-only
    assert m3[1]["content"][0] == thinking() and m3[3]["content"][0] == thinking()


def test_ask_tool_results_are_scrubbed(key, use_private_rules, samples, admin, fake) -> None:
    enable()
    with write_connection(get_settings().db_path) as w:
        w.execute("UPDATE transactions SET merchant = 'Example Private Service'")
        w.execute(
            "UPDATE accounts SET display_name = display_name || ' ending ' || last4 "
            "WHERE last4 IS NOT NULL"
        )
    fake.script = [
        message(
            [tool_use("search_transactions", {"limit": 100, "text": "example private"})],
            "tool_use",
        ),
        message([text("Done.")]),
    ]
    events(asked(admin, "What did we pay?"))
    result = fake.requests[1][1]["messages"][2]["content"][0]["content"]
    assert "Example Private" not in result and PRIVATE_LABEL in result
    decoded = json.dumps(json.loads(result), ensure_ascii=False)
    assert re.search(r"ending \d{4}", decoded) is None and "ending …" in decoded


# --------------------------------------------------------------------------
# privacy[4]: a typed last four is masked
# --------------------------------------------------------------------------


def test_a_typed_last_four_is_masked(key, samples, admin, fake) -> None:
    enable()
    last4 = a_last4()
    fake.script = [message([text("Use the account name.")])]
    tid = events(asked(admin, f"What is on the card ending {last4}?"))[-1]["thread_id"]
    sent = fake.requests[0][1]["messages"][0]["content"][0]["text"]
    assert last4 not in sent and sent.endswith("What is on the card ending …?")
    # Stored and listed without it too.
    assert all(last4 not in r[0] for r in db_rows("SELECT content_json FROM ai_messages"))
    assert db_rows("SELECT title FROM ai_threads WHERE thread_id = ?", tid)[0][0].endswith("…?")


# --------------------------------------------------------------------------
# cost[0]: the household context is pinned per thread
# --------------------------------------------------------------------------


def test_follow_ups_keep_the_threads_system_prompt(key, samples, admin, fake, monkeypatch) -> None:
    enable()
    fake.script = [
        message([thinking(), text("First.")]),
        message([text("Second.")]),
        message([text("New.")]),
    ]
    tid = events(asked(admin, "Net worth?"))[-1]["thread_id"]
    # Midnight passes: the estimates, and so the household context, change.
    monkeypatch.setattr(config, "household_today", lambda: TODAY + timedelta(days=1))
    events(asked(admin, "And now?", tid))
    events(asked(admin, "A new thread?"))
    first, follow_up, fresh = (p for _, p in fake.requests)
    assert follow_up["system"] == first["system"]  # byte for byte
    assert follow_up["tools"] == first["tools"]
    assert follow_up["messages"][1]["content"][0] == thinking()
    assert fresh["system"] != first["system"]  # the context did change that day


def test_a_thread_without_its_pin_starts_over(key, samples, admin, fake) -> None:
    enable()
    fake.script = [message([text("First.")]), message([text("Second.")])]
    tid = events(asked(admin, "Net worth?"))[-1]["thread_id"]
    ask._PINS.clear()  # a restart
    ev = events(asked(admin, "And now?", tid))
    assert ev[0]["new"] is True and ev[0]["thread_id"] != tid
    assert ev[0]["notice"] == ask.NEW_THREAD_TEXT
    assert len(fake.requests[1][1]["messages"]) == 1


# --------------------------------------------------------------------------
# cost[1], backend-robustness[3]: Stop or a disconnect
# --------------------------------------------------------------------------


class SlowClient:
    """Streams a start and one sentence, then waits for `gate` (or a cancel)."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.gate: asyncio.Event | None = None
        self.cancelled = 0
        self.messages = self

    def stream(self, **params):
        outer = self

        class Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            def __aiter__(self):
                return self.events()

            async def events(self):
                usage = SimpleNamespace(input_tokens=12_000, output_tokens=1)
                yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage))
                await asyncio.sleep(outer.delay)
                yield SimpleNamespace(type="text", text="Working on it. ")
                if outer.gate is None:
                    return
                try:
                    await outer.gate.wait()
                except asyncio.CancelledError:
                    outer.cancelled += 1
                    raise

            async def get_final_message(self):
                return message([text("Working on it. Done.")], output_tokens=50)

        return Ctx()


def test_a_disconnect_cancels_the_call_and_holds_the_slot(key, samples, admin) -> None:
    enable()
    s = get_settings()
    user = admin_user()
    slow = SlowClient()
    use(slow)
    release = threading.Event()

    def hold_write_lock() -> None:  # an import holding the app's write lock
        with db_connect.WRITE_LOCK:
            release.wait(1.0)

    async def main() -> None:
        slow.gate = asyncio.Event()
        ai_api._asking[user.id] = time.monotonic()
        p = ask.prepare(s, user, "How much did we spend?", None)
        got_text = asyncio.Event()

        async def consume() -> None:
            async for line in ai_api.ask_events(s, user.id, p):
                if '"type": "text"' in line:
                    got_text.set()

        holder = threading.Thread(target=hold_write_lock)
        async with anyio.create_task_group() as tg:  # how Starlette runs the stream
            tg.start_soon(consume)
            await got_text.wait()
            holder.start()
            await asyncio.sleep(0.05)
            tg.cancel_scope.cancel()  # the browser went away
        # The event loop does not wait on the write lock.
        t0 = time.monotonic()
        await asyncio.sleep(0.05)
        assert time.monotonic() - t0 < 0.5
        assert slow.cancelled == 1  # the model call was cancelled
        assert user.id in ai_api._asking  # the slot waits for the cleanup
        release.set()
        for _ in range(250):
            if user.id not in ai_api._asking:
                break
            await asyncio.sleep(0.02)
        assert user.id not in ai_api._asking
        holder.join()
        assert db_rows("SELECT closed FROM ai_threads WHERE thread_id = ?", p.thread_id) == [(1,)]

    try:
        asyncio.run(main())
    finally:
        ai_client.set_client_factory(None)
    # The cancelled call is logged with what is known of it.
    rows = db_rows(
        "SELECT feature, input_tokens, output_tokens, cost_micro_usd, error FROM ai_usage"
    )
    assert len(rows) == 1
    feature, tokens_in, tokens_out, cost, error = rows[0]
    assert (feature, tokens_in, error) == ("ask", 12_000, "CancelledError")
    assert tokens_out >= 1 and cost > 0


# --------------------------------------------------------------------------
# backend-robustness[5]: keepalives, and the review and suggestions in the background
# --------------------------------------------------------------------------


def test_a_quiet_stream_sends_keepalives(key, samples, admin, monkeypatch) -> None:
    enable()
    monkeypatch.setattr(ai_api, "KEEPALIVE_SECONDS", 0.02)
    use(SlowClient(delay=0.3))
    try:
        r = asked(admin, "Slow?")
    finally:
        ai_client.set_client_factory(None)
    ev = events(r)
    assert ev[-1]["type"] == "done"
    assert ": keepalive\n\n" in r.text
    assert r.text.index(": keepalive") < r.text.index('"type": "text"')


class GatedClient:
    """messages.create and messages.parse wait for `gate`, then answer from `script`."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.gate = threading.Event()
        self.calls = 0
        self.messages = self

    async def create(self, **params):
        self.calls += 1
        while not self.gate.is_set():
            await asyncio.sleep(0.01)
        return self.script.pop(0)

    parse = create


def test_the_review_runs_in_the_background(key, samples, admin, monkeypatch) -> None:
    enable()
    monkeypatch.setattr(ai_api, "RUN_WAIT_SECONDS", 0.05)
    gated = GatedClient([parsed(good_review())])
    use(gated)
    timer = threading.Timer(3.0, gated.gate.set)  # never hangs, even if it does not return
    timer.start()
    try:
        assert admin.post("/api/ai/review/run", headers=H).json() == RUNNING
        status = admin.get("/api/ai/status").json()
        assert status["running_review"] is True and status["running_suggestions"] is False
        # One run at a time: a second click joins the first.
        assert admin.post("/api/ai/review/run", headers=H).json() == RUNNING
        # The run builds the facts before it calls Claude: wait for that call.
        wait_for(lambda: gated.calls >= 1)
        assert gated.calls == 1
        gated.gate.set()
        wait_for(lambda: not admin.get("/api/ai/status").json()["running_review"])
        done = admin.get("/api/ai/status").json()["last_review_result"]
        assert done["status"] == "ready" and done["finished_at"]
        assert admin.get("/api/ai/review").json()["review"]["actions"]
    finally:
        timer.cancel()
        ai_client.set_client_factory(None)


def test_suggestions_run_in_the_background(key, samples, admin, monkeypatch) -> None:
    from app.ai import categorize

    enable()
    monkeypatch.setattr(ai_api, "RUN_WAIT_SECONDS", 0.05)
    out = categorize.SuggestionsOut(
        suggestions=[
            categorize.Suggestion(item=0, category_key="dining", confidence=80, reason="A cafe.")
        ]
    )
    gated = GatedClient([parsed(out, model="claude-haiku-5-5")])
    use(gated)
    timer = threading.Timer(3.0, gated.gate.set)
    timer.start()
    try:
        assert admin.post("/api/category-suggestions/run", headers=H).json() == {"started": True}
        assert admin.get("/api/ai/status").json()["running_suggestions"] is True
        gated.gate.set()
        wait_for(lambda: not admin.get("/api/ai/status").json()["running_suggestions"])
        done = admin.get("/api/ai/status").json()["last_suggestions_result"]
        assert (done["status"], done["merchants"], done["suggested"]) == ("done", 1, 1)
    finally:
        timer.cancel()
        ai_client.set_client_factory(None)


def test_a_quick_refusal_still_answers_the_post(key, samples, admin) -> None:
    # AI off: the run ends at once, so the POST says why, as before.
    assert admin.post("/api/category-suggestions/run", headers=H).status_code == 409
    r = admin.post("/api/ai/review/run", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ai_client.UNAVAILABLE_TEXT["off"]
    last = admin.get("/api/ai/status").json()["last_suggestions_result"]
    assert last["status"] == "unavailable"


# --------------------------------------------------------------------------
# spec-docs[4], frontend-ux[6]: when the stored review shows
# --------------------------------------------------------------------------


def _store_review() -> None:
    from app.services.coverage import coverage_state

    s = get_settings()
    with write_connection(s.db_path) as conn:
        month = coverage_state(conn).latest_complete_month()
        conn.execute(
            """INSERT INTO ai_reviews (run_id, month, model, summary, changes_json,
                   anomalies_json, actions_json, created_at, status)
               VALUES ('r1', ?, 'claude-opus-5-5', 'A steady month.', '[]', '[]',
                   '["One.", "Two.", "Three."]', '2026-10-20T00:00:00Z', 'ready')""",
            (month,),
        )


def test_no_key_hides_the_review(samples, member) -> None:
    enable()
    _store_review()
    assert member.get("/api/ai/status").json()["reason"] == "no_key"
    assert member.get("/api/dashboard").json()["ai_review"] is None
    assert member.get("/api/ai/review").json()["review"] is None


def test_a_spend_limit_pause_keeps_the_stored_review(key, samples, member) -> None:
    enable()
    _store_review()
    with write_connection(get_settings().db_path) as conn:
        ai_settings.set_block(conn, "workspace_limit")
    assert member.get("/api/ai/status").json()["reason"] == "spend_limit"
    assert member.get("/api/dashboard").json()["ai_review"]["summary"] == "A steady month."
    assert member.get("/api/ai/review").json()["review"] is not None
    # The setting off hides it.
    with write_connection(get_settings().db_path) as conn:
        ai_settings.save(conn, ai_settings.AiSettings(enabled=False), None)
    assert member.get("/api/dashboard").json()["ai_review"] is None


# --------------------------------------------------------------------------
# spec-docs[0]: cap estimates for Ask's later steps and the cache check
# --------------------------------------------------------------------------


def test_asks_next_step_is_refused_by_its_grown_estimate(key, samples, admin, fake) -> None:
    enable(daily_cap_cents=10)  # the first estimate is $0.044
    fake.script = [
        message(
            [tool_use("cashflow", {})], "tool_use", input_tokens=100, output_tokens=50, read=200_000
        ),
        message([text("Done.")]),
    ]
    ev = events(asked(admin, "Big?"))
    assert len(fake.requests) == 1
    assert ev[-1]["type"] == "error" and ev[-1]["kind"] == "cap"


def test_the_cache_check_is_refused_by_its_estimate(key, samples, fake) -> None:
    enable(daily_cap_cents=1)
    fake.script = [message([text("x")]), message([text("x")])]
    with pytest.raises(ai_client.AiError) as err:
        asyncio.run(cache_check(get_settings()))
    assert err.value.kind == "cap" and fake.requests == []


# --------------------------------------------------------------------------
# spec-docs[8]: a thread rolls over after 10 turns or about 80k prompt tokens
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("column", "value"), [("turn_count", 10), ("prompt_tokens", 80_000)])
def test_a_long_thread_rolls_over(key, samples, admin, fake, column, value) -> None:
    enable()
    fake.script = [message([text("One.")]), message([text("Two.")])]
    tid = events(asked(admin, "First?"))[-1]["thread_id"]
    with write_connection(get_settings().db_path) as w:
        w.execute(f"UPDATE ai_threads SET {column} = ? WHERE thread_id = ?", (value, tid))
    ev = events(asked(admin, "Second?", tid))
    assert ev[0]["new"] is True and ev[0]["thread_id"] != tid
    assert ev[0]["notice"] == ask.NEW_THREAD_TEXT
    assert len(fake.requests[1][1]["messages"]) == 1


def test_prompt_tokens_and_turns_are_recorded(key, samples, admin, fake) -> None:
    enable()
    fake.script = [message([text("One.")], input_tokens=500, read=4000, write=100)]
    tid = events(asked(admin, "First?"))[-1]["thread_id"]
    assert db_rows("SELECT turn_count, prompt_tokens FROM ai_threads WHERE thread_id = ?", tid) == [
        (1, 4600)
    ]
