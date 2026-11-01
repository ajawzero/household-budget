"""Privacy rescan at startup and from the CLI (PLAN 5.8, docs/plans/privacy-rescan.md).

- At startup, after migrations and before the app serves pages, the sha256 of
  the private rules file is compared with the one stored in `settings`.
  No file, or the same hash: nothing. No stored hash (first start): the rescan
  runs and rolls back, so it only lists, then the hash is stored. A new hash:
  the rescan runs and the hash is stored in the same write.
- A failed rescan keeps the old hash, so the next start tries again. The app
  still starts.
- `cli privacy-rescan` and `cli purge-run` use the same functions.
- Every run is a `job_runs` row: counts, names and run ids only.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.config import Settings
from app.db.connect import connect, write_transaction
from app.importer import rescan as rs
from app.importer.runner import private_rules_for
from app.importer.sanitize import PrivateRules, PrivateRulesError
from app.jobs.scheduler import iso, record_run, utc_now

log = logging.getLogger("app.jobs")


class _Rollback(Exception):
    def __init__(self, result: Any) -> None:
        super().__init__("rollback")
        self.result = result


def _rebuild_facts(settings: Settings, private: PrivateRules) -> None:
    """Best effort: the next Ask rebuilds them too (the rescan cleared them)."""
    from app.ai.facts import ensure_facts

    try:
        ensure_facts(settings.db_path, private)
    except Exception as exc:  # the type only: a message may hold a value
        log.warning("ai facts not rebuilt after the privacy rescan", extra={"error": _name(exc)})


def _name(exc: BaseException) -> str:
    return type(exc).__name__


def _record(settings: Settings, job: str, started: datetime, status: str, summary: dict) -> None:
    """The job_runs row, best effort: a database that cannot take it must not
    stop the app from starting. The failure is logged by type only."""
    try:
        record_run(settings.db_path, job, started, utc_now(), status, summary)
    except Exception as exc:
        log.error("job run not recorded", extra={"job": job, "error": _name(exc)})


def run_rescan(settings: Settings, private: PrivateRules, *, dry_run: bool = False) -> rs.Report:
    """Rescan in one write. Stores the rules hash unless `dry_run` (then it
    rolls back). Records a job_runs row either way. Raises RescanError."""
    started = utc_now()
    conn = connect(settings.db_path)
    try:
        with write_transaction(conn):
            report = rs.rescan(conn, private, iso(started))
            if dry_run:
                raise _Rollback(report)
            rs.store_hash(conn, private.digest, iso(started))
    except _Rollback as rb:
        report = rb.result
    except rs.RescanError as exc:
        _record(settings, rs.JOB_NAME, started, "failed", exc.summary)
        raise
    except Exception as exc:  # the type only: a message may hold a value
        _record(settings, rs.JOB_NAME, started, "failed", {"error": _name(exc)})
        raise rs.RescanError(_name(exc)) from None
    finally:
        conn.close()
    mode = "list" if dry_run else "rescan"
    _record(settings, rs.JOB_NAME, started, "ok", {"mode": mode, **report.summary()})
    if not dry_run and report.found:
        _rebuild_facts(settings, private)
    return report


def run_purge(settings: Settings, run_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Purge one run. `dry_run`: count, then roll back, record nothing."""
    started = utc_now()
    conn = connect(settings.db_path)
    try:
        with write_transaction(conn):
            out = rs.purge_run(conn, run_id, iso(started))
            if dry_run:
                raise _Rollback(out)
    except _Rollback as rb:
        return rb.result
    finally:
        conn.close()
    _record(settings, rs.PURGE_JOB, started, "ok", out)
    _rebuild_facts(settings, private_rules_for(settings))
    return out


def startup_check(settings: Settings) -> str:
    """The startup trigger. Returns what happened: 'off', 'same', 'stored',
    'listed', 'cleaned' or 'failed'. Never raises: the app still starts, and
    the old hash stays, so the next start tries again."""
    try:
        return _startup_check(settings)
    except PrivateRulesError:
        log.error("privacy rescan skipped: the private rules file cannot be read")
    except rs.RescanError as exc:
        log.error("privacy rescan failed", extra={"reason": exc.reason})
    except Exception as exc:  # the type only: a message may hold a value
        log.error("privacy rescan failed", extra={"reason": _name(exc)})
    return "failed"


def _startup_check(settings: Settings) -> str:
    if not settings.private_rules:
        return "off"
    private = private_rules_for(settings)
    conn = connect(settings.db_path)
    try:
        stored = rs.stored_hash(conn)
    finally:
        conn.close()
    if stored == private.digest:
        return "same"
    if private and stored is not None:
        report = run_rescan(settings, private)
        outcome = "cleaned"
    else:
        if private:  # first start: list only, then remember the file
            report = run_rescan(settings, private, dry_run=True)
        conn = connect(settings.db_path)
        try:
            with write_transaction(conn):
                rs.store_hash(conn, private.digest, iso(utc_now()))
        finally:
            conn.close()
        if not private:
            return "stored"
        outcome = "listed"
    log.info(
        "privacy rescan",
        extra={
            "outcome": outcome,
            "masked": sum(report.masked.values()),
            "listed": sum(report.listed.values()),
            "deleted": sum(report.deleted.values()),
            "purge": sorted(report.purge),
        },
    )
    return outcome
