"""Nightly backup (PLAN 2.2, 5.8).

- 03:15 America/Chicago: `VACUUM INTO <backup_dir>/budget-YYYY-MM-DD.db` (the
  household date). The copy is written under a temp name first, then checked
  with `PRAGMA integrity_check`. Not ok: the copy is deleted and the run is
  recorded as failed. Ok: it is renamed into place (replacing a copy from
  earlier the same day).
- The newest 14 nightly copies are kept. `pre-migrate-*.db` copies belong to
  migrate.py (it keeps 5) and are never touched here.
- At startup the job runs if there is no success since the most recent 03:15
  local (so a restart that spans 03:15 makes up the missed copy), or the last
  success is over 26 hours old, or there is none. After that it runs each time
  the clock passes 03:15 local. 03:15
  exists on every day: the spring DST gap is 02:00 to 03:00.
- The copy is made under the write lock, so it never overlaps an import.
- Never back up by copying the live file. WAL mode makes that unsafe.
- `cli backup` runs it now and records it the same way.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from app.config import HOUSEHOLD_TZ, Settings
from app.db.connect import WRITE_LOCK, connect
from app.jobs.scheduler import JobFailed, last_success

JOB_NAME = "backup"
KEEP_NIGHTLY = 14
BACKUP_AT = time(3, 15)
STARTUP_MAX_AGE = timedelta(hours=26)
NIGHTLY = re.compile(r"^budget-(\d{4}-\d{2}-\d{2})\.db$")
PRE_MIGRATE = re.compile(r"^pre-migrate-\d{4}\.db$")


class BackupFailed(JobFailed):
    """The copy failed its integrity check. `summary` goes into job_runs."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    bytes: int
    created_at: str
    kind: str  # nightly or pre_migrate


def nightly_name(day: date) -> str:
    return f"budget-{day.isoformat()}.db"


def integrity_ok(path: Path) -> bool:
    """PRAGMA integrity_check on a file, opened read-only and immutable (no side files)."""
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return [tuple(r) for r in rows] == [("ok",)]


def prune(backup_dir: Path, keep: int = KEEP_NIGHTLY) -> list[str]:
    """Delete all but the newest `keep` nightly copies (by the date in the name)."""
    nightly = sorted(p.name for p in backup_dir.iterdir() if NIGHTLY.match(p.name))
    old = nightly[:-keep] if keep else nightly
    for name in old:
        (backup_dir / name).unlink()
    return old


def run_backup(db_path: Path, backup_dir: Path, day: date) -> dict:
    """Make the backup for household date `day`. Returns the job summary.

    Raises BackupFailed (copy deleted) when the integrity check is not ok.
    """
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = nightly_name(day)
    target = backup_dir / name
    tmp = backup_dir / f".{name}.partial"
    tmp.unlink(missing_ok=True)
    conn = connect(db_path)
    try:
        with WRITE_LOCK:
            conn.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        conn.close()
    os.chmod(tmp, 0o600)
    if not integrity_ok(tmp):
        tmp.unlink(missing_ok=True)
        raise BackupFailed({"file": name, "error": "integrity_check"})
    os.replace(tmp, target)
    deleted = prune(backup_dir)
    return {
        "file": name,
        "bytes": target.stat().st_size,
        "integrity": "ok",
        "deleted": len(deleted),
    }


def list_backups(backup_dir: Path) -> list[BackupFile]:
    """Nightly and pre-migrate copies, newest first. Other files are ignored."""
    if not backup_dir.is_dir():
        return []
    out = []
    for p in backup_dir.iterdir():
        kind = (
            "nightly"
            if NIGHTLY.match(p.name)
            else "pre_migrate"
            if PRE_MIGRATE.match(p.name)
            else None
        )
        if kind is None or not p.is_file():
            continue
        st = p.stat()
        created = datetime.fromtimestamp(st.st_mtime, UTC).isoformat(timespec="seconds")
        out.append(BackupFile(p.name, st.st_size, created, kind))
    out.sort(key=lambda b: (b.created_at, b.name), reverse=True)
    return out


def last_slot(now: datetime) -> datetime:
    """The most recent 03:15 America/Chicago at or before `now` (aware), in UTC."""
    local = now.astimezone(HOUSEHOLD_TZ)
    slot = datetime.combine(local.date(), BACKUP_AT, tzinfo=HOUSEHOLD_TZ)
    if slot > local:
        slot = datetime.combine(local.date() - timedelta(days=1), BACKUP_AT, tzinfo=HOUSEHOLD_TZ)
    return slot.astimezone(UTC)


class BackupSchedule:
    """When the backup is due. First check: no success since the last 03:15
    slot, or the last success is over 26 hours old (or there is none).
    After that: each time a 03:15 slot passes between two checks."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.checked_at: datetime | None = None

    def __call__(self, now: datetime) -> bool:
        previous, self.checked_at = self.checked_at, now
        if previous is None:
            conn = connect(self.db_path)
            try:
                last = last_success(conn, JOB_NAME)
            finally:
                conn.close()
            return last is None or last < last_slot(now) or now - last > STARTUP_MAX_AGE
        return previous < last_slot(now) <= now


def household_date(now: datetime) -> date:
    return now.astimezone(HOUSEHOLD_TZ).date()


def backup_now(settings: Settings, now: datetime) -> dict:
    return run_backup(settings.db_path, settings.backup_dir, household_date(now))
