"""Nightly backup job, its schedule, `cli backup` and GET /api/backups (PLAN 2.2, 5.8)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
from datetime import UTC, date, datetime, timedelta

import pytest
from api_helpers import MEMBER, MEMBER_PW, PROD_ORIGIN, login
from fastapi.testclient import TestClient

from app import cli
from app.config import Settings
from app.db.connect import connect
from app.jobs import backup
from app.jobs.backup import (
    KEEP_NIGHTLY,
    BackupFailed,
    BackupSchedule,
    last_slot,
    run_backup,
)
from app.jobs.scheduler import Scheduler, backup_job, record_run

# 2026-10-21 03:15 in Chicago (CDT, UTC-5) is 08:15 UTC.
SLOT_UTC = datetime(2026, 10, 21, 8, 15, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def job_rows(settings: Settings) -> list[dict]:
    with connect(settings.db_path) as conn:
        return [
            dict(r) for r in conn.execute("SELECT * FROM job_runs WHERE job = 'backup' ORDER BY id")
        ]


def _counts(path) -> tuple[int, int]:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        return (
            conn.execute("SELECT count(*) FROM transactions").fetchone()[0],
            conn.execute("SELECT count(*) FROM import_runs").fetchone()[0],
        )
    finally:
        conn.close()


def test_backup_copies_checks_and_locks_down(samples: Settings) -> None:
    summary = run_backup(samples.db_path, samples.backup_dir, date(2026, 10, 21))
    target = samples.backup_dir / "budget-2026-10-21.db"
    assert summary == {
        "file": target.name,
        "bytes": target.stat().st_size,
        "integrity": "ok",
        "deleted": 0,
    }
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert _counts(target) == _counts(samples.db_path)
    assert backup.integrity_ok(target)
    assert not list(samples.backup_dir.glob(".*"))  # no temp file left
    # Same day again: replaced, not duplicated.
    run_backup(samples.db_path, samples.backup_dir, date(2026, 10, 21))
    assert sorted(p.name for p in samples.backup_dir.glob("budget-*.db")) == [target.name]


def test_keeps_newest_14_nightly_and_never_touches_pre_migrate(samples: Settings) -> None:
    d = samples.backup_dir
    d.mkdir(exist_ok=True)
    start = date(2026, 9, 1)
    for i in range(20):
        (d / f"budget-{start + timedelta(days=i)}.db").write_bytes(b"old")
    for n in range(1, 8):
        (d / f"pre-migrate-{n:04d}.db").write_bytes(b"pre")
    (d / "notes.txt").write_text("not a backup")
    summary = run_backup(samples.db_path, d, date(2026, 10, 21))
    nightly = sorted(p.name for p in d.glob("budget-*.db"))
    assert len(nightly) == KEEP_NIGHTLY
    assert nightly[-1] == "budget-2026-10-21.db"
    assert nightly[0] == f"budget-{start + timedelta(days=7)}.db"
    assert summary["deleted"] == 7
    assert len(list(d.glob("pre-migrate-*.db"))) == 7  # migrate.py owns these
    assert (d / "notes.txt").exists()


def test_failed_integrity_deletes_the_copy_and_records(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "integrity_ok", lambda path: False)
    with pytest.raises(BackupFailed) as caught:
        run_backup(samples.db_path, samples.backup_dir, date(2026, 10, 21))
    assert caught.value.summary == {"file": "budget-2026-10-21.db", "error": "integrity_check"}
    assert list(samples.backup_dir.glob("budget-*")) == []
    assert list(samples.backup_dir.glob(".budget-*")) == []

    fc = FakeClock(SLOT_UTC)
    asyncio.run(Scheduler([backup_job(samples, fc)], samples.db_path, clock=fc).tick())
    rows = job_rows(samples)
    assert [r["status"] for r in rows] == ["failed"]
    assert json.loads(rows[0]["summary_json"]) == {
        "file": "budget-2026-10-21.db",
        "error": "integrity_check",
    }


def test_integrity_check_on_a_damaged_file(tmp_path) -> None:
    bad = tmp_path / "budget-2026-10-21.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4000)
    assert backup.integrity_ok(bad) is False


def test_last_slot_in_chicago_time() -> None:
    assert last_slot(SLOT_UTC) == SLOT_UTC
    assert last_slot(SLOT_UTC - timedelta(minutes=1)) == SLOT_UTC - timedelta(days=1)
    assert last_slot(SLOT_UTC + timedelta(hours=23)) == SLOT_UTC
    # After fall back (Nov 1), 03:15 CST is 09:15 UTC.
    assert last_slot(datetime(2026, 11, 2, 12, 0, tzinfo=UTC)) == datetime(
        2026, 11, 2, 9, 15, tzinfo=UTC
    )
    # Spring forward day (Mar 14, 2027): 03:15 CDT exists, 08:15 UTC.
    assert last_slot(datetime(2027, 3, 14, 9, 0, tzinfo=UTC)) == datetime(
        2027, 3, 14, 8, 15, tzinfo=UTC
    )


def test_startup_rule_success_since_last_slot(samples: Settings) -> None:
    now = SLOT_UTC + timedelta(hours=6)
    assert BackupSchedule(samples.db_path)(now) is True  # never ran
    record_run(samples.db_path, "backup", now, now - timedelta(hours=5), "ok", {})
    assert BackupSchedule(samples.db_path)(now) is False  # 04:15 local, after today's slot
    record_run(samples.db_path, "backup", now, now - timedelta(hours=1), "failed", {})
    assert BackupSchedule(samples.db_path)(now) is False  # only successes count


def test_startup_after_missed_slot(samples: Settings) -> None:
    # Last success 03:16 yesterday. Down over 03:15 today, back at 04:00 (24.7 h later):
    # today's copy is made at startup, not skipped until tomorrow night.
    now = SLOT_UTC + timedelta(minutes=45)
    last = SLOT_UTC - timedelta(days=1) + timedelta(minutes=1)
    record_run(samples.db_path, "backup", last, last, "ok", {})
    assert BackupSchedule(samples.db_path)(now) is True


def test_startup_before_slot_waits_for_slot(samples: Settings) -> None:
    # Last success 03:16 yesterday, restart at 02:00 today: not due yet, 03:15 runs it.
    now = SLOT_UTC - timedelta(hours=1, minutes=15)
    last = SLOT_UTC - timedelta(days=1) + timedelta(minutes=1)
    record_run(samples.db_path, "backup", last, last, "ok", {})
    sched = BackupSchedule(samples.db_path)
    assert sched(now) is False
    assert sched(SLOT_UTC + timedelta(seconds=30)) is True


def test_startup_rule_over_26_hours(samples: Settings) -> None:
    now = SLOT_UTC + timedelta(hours=6)
    record_run(samples.db_path, "backup", now, now - timedelta(hours=27), "ok", {})
    assert BackupSchedule(samples.db_path)(now) is True


def test_scheduler_runs_at_startup_then_each_0315(samples: Settings) -> None:
    fc = FakeClock(SLOT_UTC - timedelta(hours=10))  # Oct 20, 22:15 local
    sched = Scheduler([backup_job(samples, fc)], samples.db_path, clock=fc)
    assert asyncio.run(sched.tick()) == ["backup"]  # no success yet
    assert [p.name for p in samples.backup_dir.glob("budget-*.db")] == ["budget-2026-10-20.db"]
    for _ in range(3):
        fc.now += timedelta(hours=3)  # up to 07:15 UTC = 02:15 local
        assert asyncio.run(sched.tick()) == []
    fc.now = SLOT_UTC - timedelta(minutes=1)
    assert asyncio.run(sched.tick()) == []
    fc.now = SLOT_UTC + timedelta(seconds=30)
    assert asyncio.run(sched.tick()) == ["backup"]
    fc.now += timedelta(seconds=60)
    assert asyncio.run(sched.tick()) == []
    assert sorted(p.name for p in samples.backup_dir.glob("budget-*.db")) == [
        "budget-2026-10-20.db",
        "budget-2026-10-21.db",
    ]
    assert [r["status"] for r in job_rows(samples)] == ["ok", "ok"]
    # A restart soon after: the last success is recent, so nothing at startup.
    again = Scheduler([backup_job(samples, fc)], samples.db_path, clock=fc)
    assert asyncio.run(again.tick()) == []


def test_cli_backup(samples: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["backup"]) == 0
    out = capsys.readouterr().out
    assert "Wrote budget-" in out and "integrity ok" in out
    rows = job_rows(samples)
    assert [r["status"] for r in rows] == ["ok"]
    assert len(list(samples.backup_dir.glob("budget-*.db"))) == 1


def test_cli_backup_failure(
    samples: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(backup, "integrity_ok", lambda path: False)
    assert cli.main(["backup"]) == 1
    assert "failed the integrity check" in capsys.readouterr().err
    assert [r["status"] for r in job_rows(samples)] == ["failed"]


def test_backups_api(samples: Settings, admin: TestClient) -> None:
    empty = admin.get("/api/backups").json()
    assert empty["last_run"] is None and empty["last_success_at"] is None
    assert empty["backups"] == []
    samples.backup_dir.mkdir(exist_ok=True)
    (samples.backup_dir / "pre-migrate-0001.db").write_bytes(b"x" * 10)
    os.utime(samples.backup_dir / "pre-migrate-0001.db", (1_700_000_000, 1_700_000_000))
    assert cli.main(["backup"]) == 0
    d = admin.get("/api/backups").json()
    names = [b["name"] for b in d["backups"]]
    assert names[0].startswith("budget-") and names[-1] == "pre-migrate-0001.db"
    first = d["backups"][0]
    assert first["kind"] == "nightly" and first["bytes"] > 0
    assert first["created_at"].endswith("+00:00")
    assert d["backups"][-1] == {
        "name": "pre-migrate-0001.db",
        "bytes": 10,
        "created_at": "2023-11-14T22:13:20+00:00",
        "kind": "pre_migrate",
    }
    assert d["last_run"]["status"] == "ok" and d["last_run"]["file"] == names[0]
    assert d["last_run"]["error"] is None
    assert d["last_success_at"] == d["last_run"]["finished_at"]


def test_backups_api_is_admin_only(samples: Settings, prod: TestClient) -> None:
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    r = prod.get("/api/backups")
    assert r.status_code == 403
    assert prod.get("/api/checks").status_code == 200  # members can read Checks
