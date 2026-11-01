"""Phase 6 review fixes, third pass: the monthly review. One review at a time,
a failed run never drops a newer ready review, the job runs in the background,
transient tries are spaced, a 429 is transient, the plan basis is said, and
rows with no merchant are labelled. Fake client only (ai_fakes.py)."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from ai_fakes import FakeClient, parsed, rate_limited, server_error
from test_ai_features import H
from test_ai_fix_facts import _reviews, enable, good_review

from app import config
from app.ai import client as ai_client
from app.ai import review
from app.ai import settings as ai_settings
from app.ai.facts import ensure_facts
from app.ai.safe import ai_connection, run
from app.config import Settings, get_settings
from app.db.connect import connect, write_connection
from app.db.migrate import migrate
from app.jobs.scheduler import Job, JobFailed, Scheduler, ai_review_job

TODAY = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-canary-key-for-tests-7f3a")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skip(_s: float) -> None:
        return None

    monkeypatch.setattr(ai_client.asyncio, "sleep", skip)


def _row() -> tuple:
    conn = connect(get_settings().db_path)
    row = conn.execute("SELECT status, summary FROM ai_reviews").fetchone()
    conn.close()
    return row["status"], json.loads(row["summary"]) if row["status"] == "failed" else None


def _age(minutes: int) -> None:
    """Move the month's review row back in time."""
    when = ai_settings.iso(datetime.now(UTC) - timedelta(minutes=minutes))
    with write_connection(get_settings().db_path) as w:
        w.execute("UPDATE ai_reviews SET created_at = ?", (when,))


# --------------------------------------------------------------------------
# N26: one review at a time; a failed run keeps a newer ready review
# --------------------------------------------------------------------------


def test_a_manual_run_while_the_job_writes_reports_running(key, samples, fake, admin) -> None:
    enable()
    s = get_settings()
    answers = []

    def slow(_params: dict):
        # The job's call is in flight: "Write the review now" starts nothing.
        answers.append(admin.post("/api/ai/review/run", headers=H))
        return parsed(good_review())

    fake.script = [slow]
    assert review.review_job_run(s)["status"] == "ready"
    (r,) = answers
    assert r.status_code == 200
    assert r.json() == {"status": "running", "message": review.RUNNING_TEXT}
    assert len(fake.requests) == 1 and _reviews() == [("2026-08", "ready")]


def test_the_job_skips_while_a_manual_run_writes(key, samples, fake) -> None:
    enable()
    s = get_settings()
    job_results = []

    def slow(_params: dict):
        t = threading.Thread(target=lambda: job_results.append(review.review_job_run(s)))
        t.start()
        t.join(10)
        return parsed(good_review())

    fake.script = [slow]
    assert asyncio.run(review.write_review(s, force=True))["status"] == "ready"
    assert job_results == [None] and len(fake.requests) == 1
    assert not review.GUARD.locked()


def test_a_failed_run_keeps_a_ready_review_written_after_it_started(key, samples, fake) -> None:
    enable()
    s = get_settings()

    def other_run_wins(_params: dict):
        # A ready review lands while this run's call is out; then this run fails.
        conn = connect(s.db_path)
        run_id = review.newest_run(conn)
        conn.close()
        review._store(s.db_path, run_id, "2026-08", "claude-opus-5-5", good_review(), None)
        return parsed(good_review(), stop_reason="max_tokens")

    fake.script = [other_run_wins]
    r = asyncio.run(review.write_review(s, force=True))
    assert r == {"status": "failed", "reason": "max_tokens"}
    assert _reviews() == [("2026-08", "ready")]


# --------------------------------------------------------------------------
# N27: the review job never holds up the other jobs
# --------------------------------------------------------------------------


def test_the_review_job_runs_beside_the_other_jobs(key, samples, fake) -> None:
    enable()
    s = get_settings()
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    gate = threading.Event()
    waited = []

    def slow(_params: dict):
        # The review's call goes on until a later job in the same tick has run.
        waited.append(gate.wait(5))
        return parsed(good_review())

    fake.script = [slow]
    job = ai_review_job(s)
    other = Job("other", lambda: gate.set() or None, timedelta(minutes=1))
    sched = Scheduler([job, other], s.db_path, clock=lambda: now)
    assert asyncio.run(sched.tick()) == [review.JOB_NAME, "other"]
    job.thread.join(10)
    assert waited == [True] and _reviews() == [("2026-08", "ready")]
    conn = connect(s.db_path)
    rows = conn.execute("SELECT job, status, summary_json FROM job_runs").fetchall()
    conn.close()
    assert [(r[0], r[1], json.loads(r[2])["status"]) for r in rows] == [
        (review.JOB_NAME, "ok", "ready")
    ]


def test_a_background_job_runs_once_at_a_time(settings: Settings) -> None:
    migrate(settings=settings)
    clock = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]
    release = threading.Event()
    calls = []

    def slow() -> dict:
        calls.append(1)
        assert release.wait(5)
        return {"n": len(calls)}

    job = Job("slow", slow, timedelta(minutes=1), background=True)
    sched = Scheduler([job], settings.db_path, clock=lambda: clock[0])
    assert asyncio.run(sched.tick()) == ["slow"]
    clock[0] += timedelta(minutes=2)
    assert asyncio.run(sched.tick()) == []  # still going: not started again
    release.set()
    job.thread.join(5)
    clock[0] += timedelta(minutes=2)
    assert asyncio.run(sched.tick()) == ["slow"]
    job.thread.join(5)
    conn = connect(settings.db_path)
    rows = conn.execute("SELECT job, status, summary_json FROM job_runs ORDER BY id").fetchall()
    conn.close()
    assert [(r[0], r[1], json.loads(r[2])) for r in rows] == [
        ("slow", "ok", {"n": 1}),
        ("slow", "ok", {"n": 2}),
    ]


# --------------------------------------------------------------------------
# P25 and N28: transient tries are spaced; a plain 429 is transient
# --------------------------------------------------------------------------


def test_transient_tries_are_an_hour_apart(key, samples, fake, no_sleep) -> None:
    enable()
    s = get_settings()
    fake.script = [server_error(), server_error()]
    assert review.review_job_run(s) == {"status": "failed", "reason": "transient"}
    fake.script = [parsed(good_review())]
    calls = len(fake.requests)
    assert review.review_job_run(s) is None  # too soon: no call
    _age(30)
    assert review.review_job_run(s) is None
    assert len(fake.requests) == calls and _row() == ("failed", {"reason": "transient", "tries": 1})
    _age(61)
    assert review.review_job_run(s)["status"] == "ready"
    assert _row() == ("ready", None)


def test_three_spaced_tries_then_the_job_stops(key, samples, fake, no_sleep) -> None:
    enable()
    s = get_settings()
    for tries in (1, 2, 3):
        fake.script = [server_error(), server_error()]
        if tries < 3:
            assert review.review_job_run(s) == {"status": "failed", "reason": "transient"}
        else:  # the last try is a failed job run (test_ai_fix4_review)
            with pytest.raises(JobFailed):
                review.review_job_run(s)
        assert _row() == ("failed", {"reason": "transient", "tries": tries})
        _age(61)
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s) is None and fake.script != []


def test_a_rate_limit_is_transient(key, samples, fake, no_sleep) -> None:
    enable()
    s = get_settings()
    fake.script = [rate_limited()]  # no retry-after: the wrapper does not wait
    assert review.review_job_run(s) == {"status": "failed", "reason": "transient"}
    assert _row() == ("failed", {"reason": "transient", "tries": 1})
    _age(61)
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s)["status"] == "ready"


# --------------------------------------------------------------------------
# N3 and N5: the review input
# --------------------------------------------------------------------------


@pytest.fixture
def ai(samples: Settings):
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        yield conn


def test_the_review_input_says_plan_spending_includes_saved_and_invested(
    ai, samples: Settings
) -> None:
    month = "2026-08"
    with write_connection(samples.db_path) as w:
        (text,) = w.execute(
            "SELECT json FROM ai_facts WHERE kind = 'plan_vs_actual' AND key = '2026-10'"
        ).fetchone()
        w.execute("DELETE FROM ai_facts WHERE kind = 'plan_vs_actual' AND key = ?", (month,))
        w.execute(
            "INSERT INTO ai_facts (kind, key, json) VALUES ('plan_vs_actual', ?, ?)",
            (month, json.dumps({**json.loads(text), "month": month})),
        )
    data = review.inputs(ai)
    about = data["plan_vs_actual"]["about"]
    assert "include saved and invested" in about
    assert "cashflow.spending leaves saved and invested out" in about
    assert "planned_spending" in about and "actual_spending" in about
    assert "planned_spending" in data["plan_vs_actual"]


def test_rows_with_no_merchant_are_labelled_not_null(ai) -> None:
    data = review.inputs(ai)
    month = data["month"]
    (none,) = run(
        ai,
        f"""SELECT coalesce(sum(out_cents), 0) AS s FROM cashflow_rows_safe
            WHERE {review.SPEND} AND month = ? AND merchant_label IS NULL""",
        [month],
    )
    assert none["s"] > 0  # the sample month has rows with no merchant
    merchants = {m["merchant"]: m["amount"] for m in data["top_merchants"]}
    assert None not in merchants
    if "No merchant" in merchants:
        assert merchants["No merchant"] == review.usd(none["s"])
    assert None not in [m["merchant"] for m in data["new_merchants"]]
