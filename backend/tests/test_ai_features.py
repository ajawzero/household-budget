"""Phase 6 features through the API: Ask (SSE), threads, the monthly review,
category suggestions, thread cleanup, the cache check and the key canary.
Fake client only (ai_fakes.py)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from ai_fakes import FakeClient, message, parsed, server_error, text, thinking, tool_use
from api_helpers import ADMIN, ADMIN_PW, MEMBER, MEMBER_PW, PROD_ORIGIN, login
from fastapi.testclient import TestClient

from app import config
from app.ai import ask, categorize, cleanup, review
from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai.cache_check import cache_check
from app.config import get_settings
from app.db.connect import connect, write_connection

TODAY = date(2026, 10, 20)
CANARY = "fake-canary-key-for-tests-7f3a"
H = {"Origin": PROD_ORIGIN}
# A public test card number, built at runtime so the repo scanner never sees it whole.
CARD = " ".join(["4111"] + ["1111"] * 3)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set before the app is built (Settings are frozen)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", CANARY)
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


def enable(**fields) -> None:
    s = get_settings()
    with write_connection(s.db_path) as conn:
        ai_settings.save(conn, ai_settings.AiSettings(enabled=True, **fields), None)


def events(r) -> list[dict]:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line[len("data: ") :])
        for chunk in r.text.split("\n\n")
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]


def asked(client: TestClient, question: str, thread_id: str | None = None):
    body = {"question": question}
    if thread_id:
        body["thread_id"] = thread_id
    return client.post("/api/ask", json=body, headers=H)


# --------------------------------------------------------------------------
# Ask
# --------------------------------------------------------------------------


def test_ask_answers_with_a_tool(key, samples, admin: TestClient, fake: FakeClient) -> None:
    enable()
    fake.script = [
        message(
            [thinking(), tool_use("spending_summary", {"start_month": "2026-08"})],
            "tool_use",
            input_tokens=500,
            write=4000,
            output_tokens=80,
        ),
        message(
            [thinking(), text("You spent **$2,947.39** in August — a calm month.")],
            input_tokens=200,
            read=4000,
            write=600,
            output_tokens=60,
        ),
    ]
    ev = events(asked(admin, "How much did we spend in August?"))
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "thread" and kinds[-1] == "done"
    assert {"type": "status", "tool": "spending_summary", "label": "Adding up spending"} in ev
    streamed = "".join(e["text"] for e in ev if e["type"] == "text")
    done = ev[-1]
    assert done["text"] == "You spent **$2,947.39** in August, a calm month."
    assert streamed == done["text"]
    assert done["cost_micro_usd"] > 0 and done["thread_closed"] is False
    detail = admin.get(f"/api/ask/threads/{done['thread_id']}").json()
    assert detail["cost_micro_usd"] == done["cost_micro_usd"]

    # The request layout (PLAN 7.3).
    (k1, first), (k2, second) = fake.requests
    assert (k1, k2) == ("stream", "stream")
    assert first["model"] == "claude-sonnet-5-5" and first["output_config"] == {"effort": "medium"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "tool_choice" not in first and "temperature" not in first and "thinking" not in first
    assert [t["name"] for t in first["tools"]] == sorted(t["name"] for t in first["tools"])
    rules, context = first["system"]
    assert "cache_control" not in rules and context["cache_control"] == {"type": "ephemeral"}
    assert "Household context" in context["text"] and "Today is" not in context["text"]
    q = first["messages"][0]["content"][0]["text"]
    assert q == "Today is 2026-10-20. Asked by Alex. How much did we spend in August?"
    # The second call sends the first response back unchanged, then the tool result.
    assert second["messages"][0] == first["messages"][0]
    assert second["messages"][1]["content"][0] == thinking()
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["is_error"] is False
    assert json.loads(result["content"])["sum_amount"] == "2947.39"

    # Two usage rows, both linked to the thread.
    conn = connect(get_settings().db_path)
    rows = conn.execute(
        "SELECT feature, thread_id, cache_read_input_tokens FROM ai_usage"
    ).fetchall()
    assert [(r[0], r[1] == done["thread_id"], r[2]) for r in rows] == [
        ("ask", True, 0),
        ("ask", True, 4000),
    ]
    kinds = [r[0] for r in conn.execute("SELECT kind FROM ai_messages ORDER BY seq")]
    assert kinds == ["question", "assistant", "tool_results", "assistant"]
    conn.close()


def test_a_follow_up_resends_the_thread_unchanged(key, samples, member, fake) -> None:
    enable()
    fake.script = [message([text("First answer.")]), message([text("Second answer.")])]
    first = events(asked(member, "What is our net worth?"))
    tid = first[-1]["thread_id"]
    second = events(asked(member, "And last month?", tid))
    assert second[0]["thread_id"] == tid and second[0]["new"] is False
    m1, m2 = fake.requests[0][1]["messages"], fake.requests[1][1]["messages"]
    assert m2[: len(m1)] == m1  # append-only: the earlier turn is byte-for-byte the same
    assert m2[len(m1)] == {"role": "assistant", "content": [text("First answer.")]}
    # A member never sees the cost.
    assert second[-1]["cost_micro_usd"] is None
    detail = member.get(f"/api/ask/threads/{tid}").json()
    assert detail["cost_micro_usd"] is None
    assert [t["question"] for t in detail["turns"]] == ["What is our net worth?", "And last month?"]
    assert [t["answer"] for t in detail["turns"]] == ["First answer.", "Second answer."]
    assert member.get("/api/ask/threads").json()["threads"][0]["thread_id"] == tid


def test_threads_belong_to_their_person(key, samples, prod, fake) -> None:
    from api_helpers import ADMIN, ADMIN_PW, MEMBER, MEMBER_PW, login

    enable()
    fake.script = [message([text("Hi.")])]
    login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN)
    tid = events(asked(prod, "Hello?"))[-1]["thread_id"]
    prod.post("/api/auth/logout", headers=H)
    login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN)
    assert prod.get(f"/api/ask/threads/{tid}").status_code == 404
    assert prod.get("/api/ask/threads").json()["threads"] == []


def test_question_checks(key, use_private_rules, samples, admin, fake) -> None:
    enable()
    r = asked(admin, f"Is card {CARD} paid off?")
    assert r.status_code == 422 and "4111" not in r.text
    assert asked(admin, "x" * 2001).status_code == 422
    assert asked(admin, "   ").status_code == 422
    fake.script = [message([text("Nothing found.")])]
    events(asked(admin, "How much on Example Private Service?"))
    sent = fake.requests[0][1]["messages"][0]["content"][0]["text"]
    assert "personal subscription" in sent and "Example Private" not in sent
    assert fake.requests[0][1]["messages"]  # only the accepted question reached the client
    assert len(fake.requests) == 1


def test_off_no_key_and_caps(samples, admin, fake, monkeypatch) -> None:
    # Off by default: 409, and nothing is called.
    r = asked(admin, "Hello?")
    assert r.status_code == 409 and r.json()["detail"] == ai_client.UNAVAILABLE_TEXT["off"]
    enable()
    assert asked(admin, "Hello?").json()["detail"] == ai_client.UNAVAILABLE_TEXT["no_key"]
    assert fake.requests == []


def test_one_question_at_a_time_per_person(key, samples, admin, fake, monkeypatch) -> None:
    import time

    from app.api import ai as ai_api

    enable()
    conn = connect(get_settings().db_path)
    (uid,) = conn.execute("SELECT id FROM users WHERE username = ?", (ADMIN,)).fetchone()
    conn.close()
    monkeypatch.setitem(ai_api._asking, uid, time.monotonic())
    r = asked(admin, "Second tab?")
    assert r.status_code == 409 and r.json()["detail"] == ai_api.BUSY_TEXT
    # A slot that never finished is ignored after a while.
    monkeypatch.setitem(ai_api._asking, uid, time.monotonic() - ai_api.ASK_SLOT_SECONDS - 1)
    fake.script = [message([text("Hi.")])]
    events(asked(admin, "Again?"))
    assert uid not in ai_api._asking  # freed when the stream ends
    assert fake.requests and len(fake.requests) == 1


def test_a_tiny_daily_cap_blocks_the_next_question(key, samples, admin, fake) -> None:
    enable(daily_cap_cents=5)
    # The first estimate (12,000 in, 2,000 out on Sonnet) is $0.044: under $0.05.
    fake.script = [message([text("Hi.")], input_tokens=50_000)]
    events(asked(admin, "First?"))
    r = asked(admin, "Second?")  # $0.10 spent, so the next one is refused before any call
    assert r.status_code == 429 and "Today's AI budget is used up" in r.json()["detail"]
    assert len(fake.requests) == 1


def test_errors_and_refusals_end_the_thread(key, samples, admin, fake, monkeypatch) -> None:
    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(ai_client.asyncio, "sleep", no_sleep)
    enable()
    fake.script = [server_error(), server_error()]
    ev = events(asked(admin, "Hello?"))
    assert ev[-1] == {"type": "error", "message": ai_client.FAILED_TEXT, "kind": "failed"}
    tid = ev[0]["thread_id"]
    fake.script = [message([text("")], "refusal")]
    ev2 = events(asked(admin, "Again?", tid))
    assert ev2[0]["thread_id"] != tid and ev2[0]["new"] is True  # the old thread was closed
    assert ev2[-1]["notice"] == ask.NOTICE["refusal"] and ev2[-1]["thread_closed"] is True


def test_too_many_steps(key, samples, admin, fake) -> None:
    enable()
    fake.script = [
        message([tool_use("cashflow", {}, id=f"toolu_{i}")], "tool_use") for i in range(6)
    ]
    ev = events(asked(admin, "Loop forever?"))
    assert ev[-1]["notice"] == ask.NOTICE["too_many_steps"]
    assert len(fake.requests) == ask.MAX_CALLS


def test_streamed_text_is_filtered(key, use_private_rules, samples, admin, fake) -> None:
    enable()
    fake.script = [message([text(f"Card {CARD} paid Example Private Service.")])]
    ev = events(asked(admin, "What paid what?"))
    body = json.dumps(ev)
    assert "4111" not in body and "Example Private" not in body
    assert ev[-1]["text"] == "Card … paid personal subscription."


def test_logs_hold_no_prompt_answer_or_key(key, samples, admin, fake, caplog) -> None:
    enable()
    fake.script = [message([text("Answer with Glacier Grocers inside.")])]
    with caplog.at_level(logging.DEBUG):
        events(asked(admin, "Where did we shop at Glacier Grocers?"))
        admin.get("/api/settings/ai")
        admin.get("/api/ai/usage")
    assert "Glacier" not in caplog.text and CANARY not in caplog.text


def test_the_key_never_leaves_the_server(key, samples, admin, fake) -> None:
    enable()
    fake.script = [message([text("Hi.")])]
    bodies = [
        admin.get("/api/ai/status").text,
        admin.get("/api/settings/ai").text,
        admin.get("/api/ai/usage").text,
        admin.get("/api/dashboard").text,
        json.dumps(events(asked(admin, "Hi?"))),
    ]
    assert all(CANARY not in b for b in bodies)


TIINGO_CANARY = "fake-tiingo-canary-for-tests-91c2"


@pytest.fixture
def tiingo_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", TIINGO_CANARY)
    get_settings.cache_clear()


def test_canary_keys_are_in_no_response_or_log(
    key, tiingo_key, samples, prod_app, admin, fake, caplog
) -> None:
    """PLAN 5.4: with fake keys set, call every GET route that takes no path
    parameter and run every job. Neither key shows up anywhere. (The network is
    blocked in tests, so the price and index jobs fail fast.)"""
    from app.jobs.scheduler import default_jobs

    enable()
    fake.script = [parsed(good_review()), message([text("Hi.")])]
    bodies = []
    with caplog.at_level(logging.DEBUG):
        paths = sorted(
            path
            for path, ops in prod_app.openapi()["paths"].items()
            if "get" in ops and "{" not in path
        )
        assert "/api/ai/status" in paths and "/api/settings/ai" in paths
        for path in paths:
            bodies.append(admin.get(path).text)
        for job in default_jobs(get_settings()):
            try:
                job.run()
            except Exception as exc:  # a failed job is fine here; its text is checked too
                bodies.append(repr(exc))
        bodies.append(json.dumps(events(asked(admin, "Hi?"))))
    assert fake.script == []  # the review job and the question both called the client
    for canary in (CANARY, TIINGO_CANARY):
        assert all(canary not in b for b in bodies)
        assert canary not in caplog.text


# --------------------------------------------------------------------------
# Settings routes
# --------------------------------------------------------------------------


def test_settings_are_admin_only(key, samples, admin, member) -> None:
    assert member.get("/api/settings/ai").status_code == 403
    assert member.get("/api/ai/usage").status_code == 403
    status = member.get("/api/ai/status").json()
    assert status["available"] is False and status["reason"] == "off"


def test_admin_turns_ai_on_and_sets_caps(key, samples, admin) -> None:
    body = admin.get("/api/settings/ai").json()
    assert body["settings"]["enabled"] is False
    assert {p["model"] for p in body["prices"]} == set(body["models"])
    new = {**body["settings"], "enabled": True, "daily_cap_cents": 1}
    r = admin.put("/api/settings/ai", json=new, headers=H)
    assert r.status_code == 200 and r.json()["status"]["available"] is True
    assert (
        admin.put(
            "/api/settings/ai", json={**new, "ask_model": "claude-made-up"}, headers=H
        ).status_code
        == 422
    )
    assert admin.put("/api/settings/ai", json={**new, "extra": 1}, headers=H).status_code == 422


def test_clear_block(key, samples, admin) -> None:
    enable()
    with write_connection(get_settings().db_path) as conn:
        ai_settings.set_block(conn, "org_limit")
    assert admin.get("/api/ai/status").json()["reason"] == "spend_limit"
    r = admin.post("/api/settings/ai/clear-block", headers=H)
    assert r.json()["status"]["available"] is True


# --------------------------------------------------------------------------
# The monthly review
# --------------------------------------------------------------------------


def good_review() -> review.ReviewOut:
    return review.ReviewOut(
        summary="A steady month — spending was close to plan.",
        changes=["Spending fell a little."],
        anomalies=[],
        actions=["Pay the store card.", "Check the van loan.", "Move $500 to savings."],
    )


def test_the_review_runs_once_per_monthly_run(key, samples, member, fake) -> None:
    enable()
    s = get_settings()
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s)["status"] == "ready"
    assert review.review_job_run(s) is None  # already done
    kind, params = fake.requests[0]
    assert kind == "create" and params["model"] == "claude-opus-5-5"
    assert params["output_config"]["effort"] == "high"
    assert params["output_config"]["format"]["type"] == "json_schema"
    data = json.loads(params["messages"][0]["content"])
    assert data["month"] == "2026-08" and data["cashflow"]["spending"] == "2947.39"
    d = member.get("/api/dashboard").json()
    assert d["ai_review"]["summary"] == "A steady month, spending was close to plan."
    assert len(d["ai_review"]["actions"]) == 3
    # Off: the Dashboard falls back to the chat's review.
    with write_connection(s.db_path) as conn:
        ai_settings.save(conn, ai_settings.AiSettings(enabled=False), None)
    assert member.get("/api/dashboard").json()["ai_review"] is None


def test_a_review_with_two_actions_is_thrown_away(key, samples, admin, fake) -> None:
    enable()
    bad = good_review().model_copy(update={"actions": ["One.", "Two."]})
    fake.script = [parsed(bad)]
    assert admin.post("/api/ai/review/run", headers=H).json()["status"] == "failed"
    assert admin.get("/api/ai/review").json()["review"] is None
    fake.script = [parsed(good_review())]
    assert admin.post("/api/ai/review/run", headers=H).json()["status"] == "ready"


def test_the_review_does_not_run_when_off(samples, fake) -> None:
    assert review.review_job_run(get_settings()) is None
    assert fake.requests == []


def test_a_review_that_does_not_parse_still_counts_its_cost(key, samples, admin, fake) -> None:
    enable()
    fake.script = [message([text('{"summary": "cut off')], "max_tokens", "claude-opus-5-5")]
    assert admin.post("/api/ai/review/run", headers=H).json()["status"] == "failed"
    conn = connect(get_settings().db_path)
    row = conn.execute("SELECT feature, cost_micro_usd, error FROM ai_usage").fetchone()
    conn.close()
    assert row[0] == "review" and row[1] > 0 and row[2] is None


def test_a_paused_review_is_skipped_and_tried_again(key, samples, admin, fake) -> None:
    from ai_fakes import bad_request

    enable()
    fake.script = [bad_request("You have reached your specified workspace API usage limits.")]
    r = admin.post("/api/ai/review/run", headers=H).json()
    assert r["status"] == "skipped" and "spend limit" in r["message"]
    with write_connection(get_settings().db_path) as conn:
        ai_settings.clear_block(conn)
    fake.script = [parsed(good_review())]
    assert review.review_job_run(get_settings())["status"] == "ready"


def test_the_dashboard_never_shows_an_older_months_review(key, samples, member, fake) -> None:
    enable()
    s = get_settings()
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s)["status"] == "ready"
    with write_connection(s.db_path) as conn:
        conn.execute("UPDATE ai_reviews SET month = '2026-07'")
    assert member.get("/api/dashboard").json()["ai_review"] is None


# --------------------------------------------------------------------------
# Category suggestions
# --------------------------------------------------------------------------


def test_suggestions_need_approval(key, samples, admin, fake) -> None:
    enable()
    s = get_settings()
    conn = connect(s.db_path)
    (txn_key,) = conn.execute(
        """SELECT v.txn_key FROM v_transactions v JOIN categories c USING (category_key)
           WHERE c.kind = 'uncategorized' AND v.hidden = 0 AND v.pre_history = 0"""
    ).fetchone()
    fake.script = [
        parsed(
            categorize.SuggestionsOut(
                suggestions=[
                    categorize.Suggestion(
                        item=0, category_key="DINING", confidence=80, reason="A cafe name."
                    ),
                    categorize.Suggestion(
                        item=7, category_key="dining", confidence=80, reason="Out of range."
                    ),
                ]
            ),
            model="claude-haiku-5-5",
        )
    ]
    r = admin.post("/api/category-suggestions/run", headers=H)
    assert r.json() == {"merchants": 1, "suggested": 1}
    params = fake.requests[0][1]
    assert params["model"] == "claude-haiku-5-5" and params["output_config"]["effort"] == "low"
    assert params["output_config"]["format"]["schema"]["additionalProperties"] is False
    payload = json.loads(params["messages"][0]["content"])
    assert {c["key"] for c in payload["categories"]} >= {"dining", "uncategorized"}
    # Nothing changed yet.
    cat = conn.execute("SELECT category_key FROM v_transactions WHERE txn_key = ?", (txn_key,))
    assert cat.fetchone()[0] != "dining"
    # A member decides. (One client: signing in as the member replaces the admin.)
    member = admin
    assert login(member, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    (sug,) = member.get("/api/category-suggestions").json()["suggestions"]
    assert (sug["txn_key"], sug["category_key"]) == (txn_key, "dining")
    assert member.post(f"/api/category-suggestions/{sug['id']}/approve", headers=H).json() == {
        "changed": 1
    }
    row = conn.execute(
        "SELECT category_key, category_source FROM v_transactions WHERE txn_key = ?", (txn_key,)
    ).fetchone()
    assert tuple(row) == ("dining", "override")
    ev = conn.execute("SELECT via, to_value FROM transaction_events WHERE txn_key = ?", (txn_key,))
    assert tuple(ev.fetchone()) == ("suggestion", "dining")
    again = member.post(f"/api/category-suggestions/{sug['id']}/reject", headers=H)
    assert again.status_code == 409
    assert member.get("/api/category-suggestions").json()["suggestions"] == []
    # Running again sends nothing: the row is no longer uncategorized.
    assert login(admin, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
    assert admin.post("/api/category-suggestions/run", headers=H).json()["merchants"] == 0
    conn.close()


def _suggest(db_path, category: str = "dining") -> tuple[int, str]:
    """A pending suggestion for the first uncategorized row, written directly."""
    with write_connection(db_path) as conn:
        (txn_key,) = conn.execute(
            """SELECT v.txn_key FROM v_transactions v JOIN categories c USING (category_key)
               WHERE c.kind = 'uncategorized' AND v.hidden = 0 AND v.pre_history = 0"""
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO category_suggestions (txn_key, suggested_category_key, confidence,
                   model, created_at) VALUES (?, ?, 80, 'claude-haiku-5-5', '2026-10-20')""",
            (txn_key, category),
        )
        return cur.lastrowid, txn_key


def test_approving_after_the_row_changed_is_refused(key, samples, admin) -> None:
    from app.services.overrides import apply_edit

    enable()
    s = get_settings()
    sid, txn_key = _suggest(s.db_path)
    with write_connection(s.db_path) as conn:
        apply_edit(conn, [txn_key], {"category_key": "groceries"}, user_id=1, via="edit", now="x")
    r = admin.post(f"/api/category-suggestions/{sid}/approve", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == categorize.STALE_TEXT
    conn = connect(s.db_path)
    cat = conn.execute("SELECT category_key FROM v_transactions WHERE txn_key = ?", (txn_key,))
    assert cat.fetchone()[0] == "groceries"
    conn.close()


def test_approving_an_inactive_category_is_refused(key, samples, admin) -> None:
    enable()
    s = get_settings()
    sid, _ = _suggest(s.db_path)
    with write_connection(s.db_path) as conn:
        conn.execute("UPDATE categories SET active = 0 WHERE category_key = 'dining'")
    assert admin.post(f"/api/category-suggestions/{sid}/approve", headers=H).status_code == 422


def test_running_suggestions_is_admin_only(key, samples, member) -> None:
    enable()
    assert member.post("/api/category-suggestions/run", headers=H).status_code == 403


# --------------------------------------------------------------------------
# Cleanup and the cache check
# --------------------------------------------------------------------------


def test_old_threads_are_deleted(key, samples) -> None:
    s = get_settings()
    now = datetime(2026, 10, 20, tzinfo=UTC)
    old = ai_settings.iso(now - timedelta(days=91))
    new = ai_settings.iso(now - timedelta(days=89))
    with write_connection(s.db_path) as conn:
        # Thread a started 91 days ago and is still in use: it goes anyway.
        for tid, at, last in (("a" * 32, old, new), ("b" * 32, new, new)):
            conn.execute(
                """INSERT INTO ai_threads (thread_id, user_id, created_at, updated_at, model,
                       effort, title) VALUES (?, NULL, ?, ?, 'claude-sonnet-5-5', 'medium', 'q')""",
                (tid, at, last),
            )
            conn.execute(
                """INSERT INTO ai_messages (thread_id, seq, role, kind, content_json, created_at)
                   VALUES (?, 1, 'user', 'question', '[]', ?)""",
                (tid, at),
            )
        conn.execute(
            """INSERT INTO ai_usage (at, feature, model, thread_id) VALUES
               (?, 'ask', 'claude-sonnet-5-5', ?)""",
            (old, "a" * 32),
        )
    assert cleanup.delete_old_threads(s.db_path, now) == 1
    conn = connect(s.db_path)
    assert [r[0] for r in conn.execute("SELECT thread_id FROM ai_threads")] == ["b" * 32]
    assert conn.execute("SELECT count(*) FROM ai_messages").fetchone()[0] == 1
    assert conn.execute("SELECT thread_id FROM ai_usage").fetchone()[0] is None
    conn.close()


def test_cache_check_runs_with_ai_off(key, samples, fake) -> None:
    fake.script = [
        message([text("Fifteen.")], input_tokens=50, write=5000),
        message([text("Fifteen.")], input_tokens=50, read=5000),
    ]
    result = asyncio.run(cache_check(get_settings()))
    assert result["ok"] is True
    assert [c["cache_read_input_tokens"] for c in result["calls"]] == [0, 5000]
    one, two = (p for _, p in fake.requests)
    assert one == two  # the same request twice


def test_cache_check_needs_a_key(samples, fake) -> None:
    with pytest.raises(ai_client.AiError):
        asyncio.run(cache_check(get_settings()))
