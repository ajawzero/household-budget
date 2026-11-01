"""Scheduler loop with a fake clock (PLAN 2.2), and the Phase 1 import poll job."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import UTC, datetime, timedelta

import pytest
from api_helpers import SAMPLES, app_for
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.connect import connect
from app.db.migrate import migrate
from app.jobs.scheduler import Job, Scheduler, import_poll_job, last_success
from app.main import create_app


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def job_rows(settings: Settings) -> list[dict]:
    with connect(settings.db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM job_runs ORDER BY id")]


@pytest.fixture
def db_ready(settings: Settings) -> Settings:
    migrate(settings=settings)
    return settings


def test_due_at_startup_then_every_interval(db_ready: Settings) -> None:
    fc = FakeClock()
    calls: list[datetime] = []
    job = Job("count", lambda: calls.append(fc.now) or {"n": len(calls)}, timedelta(seconds=60))
    sched = Scheduler([job], db_ready.db_path, clock=fc)

    assert asyncio.run(sched.tick()) == ["count"]  # at startup
    fc.now += timedelta(seconds=30)
    assert asyncio.run(sched.tick()) == []
    fc.now += timedelta(seconds=30)
    assert asyncio.run(sched.tick()) == ["count"]
    rows = job_rows(db_ready)
    assert [r["job"] for r in rows] == ["count", "count"]
    assert rows[0]["status"] == "ok" and json.loads(rows[1]["summary_json"]) == {"n": 2}
    assert rows[0]["started_at"] == "2026-10-20T12:00:00+00:00"
    with connect(db_ready.db_path) as conn:
        assert last_success(conn, "count") == fc.now
        assert last_success(conn, "other") is None


def test_jobs_run_one_at_a_time_in_order(db_ready: Settings) -> None:
    fc = FakeClock()
    log: list[str] = []

    def slow(name: str):  # noqa: ANN202
        def run() -> None:
            log.append(f"{name} start")
            time.sleep(0.02)
            log.append(f"{name} end")

        return run

    jobs = [Job("a", slow("a"), timedelta(minutes=1)), Job("b", slow("b"), timedelta(minutes=1))]
    asyncio.run(Scheduler(jobs, db_ready.db_path, clock=fc).tick())
    assert log == ["a start", "a end", "b start", "b end"]


def test_quiet_run_writes_no_row_and_failure_is_recorded(db_ready: Settings) -> None:
    fc = FakeClock()

    def boom() -> dict:
        raise ValueError("secret value 1234.56")

    jobs = [
        Job("quiet", lambda: None, timedelta(minutes=1)),
        Job("boom", boom, timedelta(minutes=1)),
        Job("after", lambda: {"ok": 1}, timedelta(minutes=1)),
    ]
    assert asyncio.run(Scheduler(jobs, db_ready.db_path, clock=fc).tick()) == [
        "quiet",
        "boom",
        "after",
    ]
    rows = job_rows(db_ready)
    assert [(r["job"], r["status"]) for r in rows] == [("boom", "failed"), ("after", "ok")]
    assert json.loads(rows[0]["summary_json"]) == {"error": "ValueError"}
    assert "1234.56" not in json.dumps(rows)


def test_run_forever_with_fake_sleep(db_ready: Settings) -> None:
    fc = FakeClock()
    runs: list[datetime] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fc.now += timedelta(seconds=seconds)
        if len(sleeps) == 5:
            raise asyncio.CancelledError

    hourly = Job("hourly", lambda: runs.append(fc.now) or None, timedelta(minutes=2))
    sched = Scheduler([hourly], db_ready.db_path, clock=fc, sleep=fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sched.run_forever())
    assert sleeps == [60] * 5
    start = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    assert runs == [start, start + timedelta(minutes=2), start + timedelta(minutes=4)]


def test_import_poll_job(db_ready: Settings) -> None:
    for run in SAMPLES.iterdir():
        shutil.copytree(run, db_ready.import_dir / run.name)
    job = import_poll_job(db_ready)
    assert job.name == "import_poll" and job.every == timedelta(seconds=60)
    fc = FakeClock()
    sched = Scheduler([job], db_ready.db_path, clock=fc)
    asyncio.run(sched.tick())
    rows = job_rows(db_ready)
    assert len(rows) == 1 and rows[0]["status"] == "ok"
    summary = json.loads(rows[0]["summary_json"])
    assert summary["runs"] == 4 and summary["statuses"] == {"imported": 4}
    assert summary["changes"] > 0
    fc.now += timedelta(seconds=60)
    asyncio.run(sched.tick())
    assert len(job_rows(db_ready)) == 1  # nothing new: no row
    with connect(db_ready.db_path) as conn:
        statuses = {r[0] for r in conn.execute("SELECT status FROM import_runs")}
    assert statuses == {"imported"}


def test_lifespan_starts_the_poll(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    for run in SAMPLES.iterdir():
        shutil.copytree(run, settings.import_dir / run.name)
    app = create_app(app_for(monkeypatch, dev=False).state.settings, start_scheduler=True)
    with TestClient(app, base_url="https://budget.home.example") as c:
        assert c.get("/healthz").status_code == 200
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if len(job_rows(settings)) >= 2:
                break
            time.sleep(0.05)
    # The poll, then the backup (no success yet, so it runs at startup). The
    # index job may run after them; tests never let it reach FHFA.
    rows = [(r["job"], r["status"]) for r in job_rows(settings)]
    assert rows[:2] == [("import_poll", "ok"), ("backup", "ok")]
    assert ("hpi", "ok") not in rows
    assert [p.name for p in settings.backup_dir.glob("budget-*.db")] != []
    with connect(settings.db_path) as conn:
        statuses = [r[0] for r in conn.execute("SELECT status FROM import_runs")]
    assert statuses == ["imported"] * 4
