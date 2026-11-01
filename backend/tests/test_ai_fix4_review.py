"""Phase 6 review fixes, fourth pass: the monthly review. A failure the job
will not try again (the last transient try, or a final failure) is a failed
job run and shows in /api/ai/status, so the admin can write it now. The job
holds GUARD only once the month is due, so its check every few minutes never
turns an admin's run away. Fake client only (ai_fakes.py)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from ai_fakes import FakeClient, parsed, server_error
from test_ai_features import H
from test_ai_fix_facts import _reviews, enable, good_review

from app import config
from app.ai import client as ai_client
from app.ai import review
from app.ai import settings as ai_settings
from app.config import get_settings
from app.db.connect import connect, write_connection
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


def _age(minutes: int) -> None:
    """Move the month's review row back in time."""
    when = ai_settings.iso(datetime.now(UTC) - timedelta(minutes=minutes))
    with write_connection(get_settings().db_path) as w:
        w.execute("UPDATE ai_reviews SET created_at = ?", (when,))


def _tick(job: Job) -> None:
    """One scheduler tick for the review job, and wait for its thread."""
    job.last_started = None
    asyncio.run(Scheduler([job], get_settings().db_path).tick())
    job.thread.join(10)


def _job_runs() -> list[tuple[str, dict]]:
    conn = connect(get_settings().db_path)
    rows = conn.execute(
        "SELECT status, summary_json FROM job_runs WHERE job = 'ai_review' ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["status"], json.loads(r["summary_json"])) for r in rows]


def _failed_note(admin) -> dict | None:
    return admin.get("/api/ai/status", headers=H).json()["review_failed"]


# --------------------------------------------------------------------------
# P25: the job's last try is flagged
# --------------------------------------------------------------------------


def test_the_last_transient_try_is_a_failed_job_run(key, samples, fake, no_sleep, admin) -> None:
    enable()
    job = ai_review_job(get_settings())
    for _ in range(3):
        assert _failed_note(admin) is None  # none yet, or the job tries again
        fake.script = [server_error(), server_error()]
        _tick(job)
        _age(61)
    note = {"month": "2026-08", "reason": "transient", "tries": 3, "rewrite": False}
    retried = ("ok", {"status": "failed", "reason": "transient"})
    assert _job_runs() == [retried, retried, ("failed", note)]
    assert _failed_note(admin) == note
    # The job stops; the admin writes it now, and the note goes.
    _tick(job)
    assert len(_job_runs()) == 3
    fake.script = [parsed(good_review())]
    assert admin.post("/api/ai/review/run", headers=H).json() == {"status": "ready"}
    assert _failed_note(admin) is None and _reviews() == [("2026-08", "ready")]


def test_a_final_failure_is_a_failed_job_run(key, samples, fake, admin) -> None:
    enable()
    fake.script = [parsed(good_review(), stop_reason="refusal")]
    with pytest.raises(JobFailed) as exc:
        review.review_job_run(get_settings())
    note = {"month": "2026-08", "reason": "refusal", "tries": None, "rewrite": False}
    assert exc.value.summary == note
    assert _failed_note(admin) == note


def test_no_note_for_an_older_month(key, samples, fake, admin) -> None:
    enable()
    fake.script = [parsed(good_review(), stop_reason="refusal")]
    with pytest.raises(JobFailed):
        review.review_job_run(get_settings())
    with write_connection(get_settings().db_path) as w:
        w.execute("UPDATE ai_reviews SET month = '2026-07'")
    assert _failed_note(admin) is None


# --------------------------------------------------------------------------
# GUARD: the job takes it only once the month is due
# --------------------------------------------------------------------------


def test_a_manual_run_during_the_jobs_check_writes_the_review(
    key, samples, fake, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable()
    s = get_settings()
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s)["status"] == "ready"
    real = review._reviewed
    held, answers = [], []

    def check_with_click(db_path, month: str, *rest) -> bool:
        # An admin clicks "Write the review now" while the job checks the month.
        held.append(review.GUARD.locked())
        answers.append(admin.post("/api/ai/review/run", headers=H).json())
        return real(db_path, month, *rest)

    monkeypatch.setattr(review, "_reviewed", check_with_click)
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s) is None  # nothing due for the job
    assert held == [False]
    assert answers == [{"status": "ready"}]
    assert len(fake.requests) == 2 and fake.script == []
    assert not review.GUARD.locked()


def test_the_job_checks_again_once_it_holds_guard(
    key, samples, fake, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable()
    s = get_settings()
    real = review._reviewed
    answers = []

    def check_then_click(db_path, month: str, *rest) -> bool:
        found = real(db_path, month, *rest)
        if not answers:  # the month is due; an admin writes it before the job's call
            answers.append(admin.post("/api/ai/review/run", headers=H).json())
        return found

    monkeypatch.setattr(review, "_reviewed", check_then_click)
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s) is None  # written meanwhile: no second call
    assert answers == [{"status": "ready"}]
    assert len(fake.requests) == 1 and _reviews() == [("2026-08", "ready")]
    assert not review.GUARD.locked()
