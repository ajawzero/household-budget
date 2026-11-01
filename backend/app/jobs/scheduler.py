"""A small asyncio scheduler (PLAN 2.2).

- One task, started in FastAPI's lifespan. It wakes every 60 seconds and runs
  each due job, one at a time, in a worker thread (jobs use the DB write lock).
  A `background` job (the monthly review, whose API call can take minutes)
  gets its own thread instead, so it never holds up the others. A tick skips
  it while its last run is still going.
- Each run that has something to report is written to `job_runs`. A job that
  returns None (for example an import poll that found nothing) is not written,
  so the table does not grow by one row a minute.
- An interval job is due when it has not run yet in this process ("at
  startup") or its interval has passed. A job with its own `is_due` (the
  nightly backup) decides for itself, using `last_success` from job_runs.
- A job that raises JobFailed is recorded as failed with the exception's
  summary. Any other exception is recorded with its class name only.
- The clock and the sleep are injectable, so tests run with a fake clock.

Jobs: the import poll (Phase 1), the nightly backup (Phase 3, jobs/backup.py),
the daily price job (Phase 5, market/job.py), the weekly house price index
job (Phase 5, valuations/hpi.py), and the monthly review and Ask thread
cleanup (Phase 6, ai/review.py and ai/cleanup.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.db.connect import connect, write_transaction
from app.logging import exception_site

log = logging.getLogger("app.jobs")

TICK_SECONDS = 60

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]
JobFn = Callable[[], dict | None]
DueFn = Callable[[datetime], bool]


class JobFailed(Exception):
    """Raise from a job to record status failed with this summary (no values in it)."""

    def __init__(self, summary: dict) -> None:
        super().__init__("job failed")
        self.summary = summary


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    name: str
    run: JobFn
    every: timedelta | None = None
    is_due: DueFn | None = None
    background: bool = False
    last_started: datetime | None = field(default=None, init=False)
    thread: threading.Thread | None = field(default=None, init=False)

    def due(self, now: datetime) -> bool:
        if self.is_due is not None:
            return self.is_due(now)
        if self.every is None:
            raise ValueError(f"job {self.name} needs every or is_due")
        return self.last_started is None or now - self.last_started >= self.every


def last_success(conn: sqlite3.Connection, job: str) -> datetime | None:
    """When `job` last finished with status ok, from job_runs."""
    row = conn.execute(
        "SELECT max(finished_at) FROM job_runs WHERE job = ? AND status = 'ok'", (job,)
    ).fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


class Scheduler:
    def __init__(
        self,
        jobs: list[Job],
        db_path: Path,
        *,
        clock: Clock = utc_now,
        sleep: Sleep = asyncio.sleep,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.jobs = jobs
        self.db_path = db_path
        self.clock = clock
        self.sleep = sleep
        self.tick_seconds = tick_seconds

    async def run_forever(self) -> None:
        """Tick, then sleep, until cancelled. A failing job never stops the loop."""
        while True:
            await self.tick()
            await self.sleep(self.tick_seconds)

    async def tick(self) -> list[str]:
        """Run every due job once, one at a time. Returns the names that ran."""
        ran = []
        for job in self.jobs:
            now = self.clock()
            if job.thread is not None and job.thread.is_alive():
                continue  # a background run from an earlier tick is still going
            if job.due(now):
                job.last_started = now
                if job.background:
                    job.thread = threading.Thread(
                        target=self.run_job, args=(job, now), name=f"job-{job.name}", daemon=True
                    )
                    job.thread.start()
                else:
                    await asyncio.to_thread(self.run_job, job, now)
                ran.append(job.name)
        return ran

    def run_job(self, job: Job, started: datetime) -> None:
        status, summary = "ok", None
        try:
            summary = job.run()
        except JobFailed as exc:
            status, summary = "failed", exc.summary
            log.error("job failed", extra={"job": job.name, "exc_class": type(exc).__name__})
        except Exception as exc:  # log the class and place only, never the message
            status = "failed"
            summary = {"error": type(exc).__name__}
            log.error(
                "job failed",
                extra={
                    "job": job.name,
                    "exc_class": type(exc).__name__,
                    "at": exception_site(exc.__traceback__),
                },
            )
        if summary is None:
            return
        self.record(job.name, started, status, summary)

    def record(self, job: str, started: datetime, status: str, summary: dict) -> None:
        record_run(self.db_path, job, started, self.clock(), status, summary)


def record_run(
    db_path: Path, job: str, started: datetime, finished: datetime, status: str, summary: dict
) -> None:
    """Write one job_runs row."""
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            conn.execute(
                """INSERT INTO job_runs (job, started_at, finished_at, status, summary_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (job, iso(started), iso(finished), status, json.dumps(summary, sort_keys=True)),
            )
    finally:
        conn.close()
    log.info("job ran", extra={"job": job, "status": status})


# --------------------------------------------------------------------------
# Phase 1 jobs
# --------------------------------------------------------------------------


def import_poll_job(settings: Settings) -> Job:
    """Poll the import folder (PLAN 4.11 steps 1 and 2). One poller per process."""
    from app.importer.scan import ImportPoller

    poller = ImportPoller(settings)

    def run() -> dict | None:
        results = poller.poll_once()
        if not results:
            return None
        statuses = Counter(r.status for r in results)
        return {
            "runs": len(results),
            "statuses": dict(sorted(statuses.items())),
            "changes": sum(r.changes for r in results),
        }

    return Job("import_poll", run, timedelta(seconds=settings.import_poll_seconds))


# --------------------------------------------------------------------------
# Phase 3 jobs
# --------------------------------------------------------------------------


def backup_job(settings: Settings, clock: Clock = utc_now) -> Job:
    """Nightly backup at 03:15 America/Chicago, and at startup when overdue."""
    from app.jobs.backup import JOB_NAME, BackupSchedule, backup_now

    return Job(
        JOB_NAME,
        lambda: backup_now(settings, clock()),
        is_due=BackupSchedule(settings.db_path),
    )


# --------------------------------------------------------------------------
# Phase 5 jobs
# --------------------------------------------------------------------------


def price_job(settings: Settings, clock: Clock = utc_now) -> Job:
    """Prices at 06:30 America/Chicago, Tuesday to Saturday. Never without a Tiingo key."""
    from app.market.job import JOB_NAME, price_job_parts

    run, schedule = price_job_parts(settings)
    return Job(JOB_NAME, lambda: run(clock()), is_due=schedule)


def hpi_job(settings: Settings, clock: Clock = utc_now) -> Job:
    """FHFA house price index, Wednesdays 06:45 America/Chicago (valuations/hpi.py)."""
    from app.valuations.hpi import JOB_NAME, hpi_job_parts

    run, schedule = hpi_job_parts(settings.db_path)
    return Job(JOB_NAME, lambda: run(clock()), is_due=schedule)


# --------------------------------------------------------------------------
# Phase 6 jobs
# --------------------------------------------------------------------------


def ai_review_job(settings: Settings) -> Job:
    """The monthly review, once per monthly run, when AI is available (ai/review.py).
    In the background: its call can take minutes."""
    from app.ai.review import JOB_NAME, review_job_run

    return Job(JOB_NAME, lambda: review_job_run(settings), timedelta(minutes=5), background=True)


def ai_cleanup_job(settings: Settings) -> Job:
    """Ask threads older than the retention setting, daily (ai/cleanup.py)."""
    from app.ai.cleanup import JOB_NAME, cleanup_job_run

    return Job(JOB_NAME, lambda: cleanup_job_run(settings.db_path), timedelta(hours=24))


def default_jobs(settings: Settings) -> list[Job]:
    return [
        import_poll_job(settings),
        backup_job(settings),
        price_job(settings),
        hpi_job(settings),
        ai_review_job(settings),
        ai_cleanup_job(settings),
    ]
