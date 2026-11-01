"""Import one run folder end to end (PLAN 4.11 steps 3 to 8).

Entry points:
- `import_folder(folder, ...)`: one run folder. Used by the poll, the CLI and Retry.
- `import_path(path, ...)`: a run folder or a folder of runs, in run order.
- `retry_run(run_id, ...)`: the admin Retry button (re-reads it from the import folder).
- `reapply(...)`: empty the imported tables and replay every stored run.

Statuses in import_runs:
- imported: applied.
- failed: rejected. error_json lists rule ids and locations only.
- waiting: rejected (not imported) only because account or person keys are
  unknown. An earlier run that has not arrived yet may add them, so the run
  is tried again after any later successful import: by the poll, and by
  `import_path`, which also looks in the given folder's sibling run folders
  and in the import folder. Runs that arrive in any order still import. A run
  whose keys are truly unknown stays waiting, shown as not imported with its
  rule ids.

Errors and logs never hold values from the files.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from app.config import Settings, get_settings, household_today
from app.db.connect import connect, write_transaction
from app.importer.apply import ApplyStats, applied_runs, apply_run, replay, store_sections
from app.importer.models import Issue
from app.importer.order import is_run_id, parse_run_id, run_order_key
from app.importer.sanitize import EMPTY_PRIVATE_RULES, PrivateRules, load_private_rules
from app.importer.validate import (
    DEPENDENCY_RULES,
    ValidRun,
    load_registry,
    rule_message,
    validate_run,
)
from app.importer.verify import manifest_sha256, verify_run
from app.services.fingerprint import diff_counts, imported_state

log = logging.getLogger("app.importer")

# Result statuses. The first four are also import_runs statuses.
IMPORTED, FAILED, WAITING = "imported", "failed", "waiting"
UNCHANGED = "unchanged"  # already imported with the same manifest: 0 changes
SKIPPED = "skipped"  # failed before with the same manifest; use Retry
DRY_RUN = "dry-run"


@dataclass
class ImportResult:
    run_id: str
    status: str
    issues: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def changes(self) -> int:
        return int(self.counts.get("changes", 0))

    @property
    def ok(self) -> bool:
        return self.status in (IMPORTED, UNCHANGED, DRY_RUN)


class _DryRunRollback(Exception):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def private_rules_for(settings: Settings) -> PrivateRules:
    return (
        load_private_rules(settings.private_rules)
        if settings.private_rules
        else (EMPTY_PRIVATE_RULES)
    )


def issues_json(issues: Iterable[Issue]) -> str:
    return json.dumps([{"rule": i.rule, "where": i.where} for i in issues])


def _event(conn: sqlite3.Connection, kind: str, run_id: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO events (at, kind, entity, entity_key, detail_json) VALUES (?, ?, ?, ?, ?)",
        (utc_now(), kind, "import_run", run_id, json.dumps(detail, sort_keys=True)),
    )


def _record_rejected(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    issues: list[Issue],
    msha: str | None,
    mode: str | None,
    version: str | None,
    started: str,
) -> None:
    rid = parse_run_id(run_id)
    with write_transaction(conn):
        conn.execute(
            """INSERT INTO import_runs (run_id, run_date, run_seq, mode, schema_version,
                   manifest_sha256, status, started_at, finished_at, error_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (run_id) DO UPDATE SET
                 mode = excluded.mode, schema_version = excluded.schema_version,
                 manifest_sha256 = excluded.manifest_sha256, status = excluded.status,
                 started_at = excluded.started_at, finished_at = excluded.finished_at,
                 counts_json = NULL, warnings_json = NULL, error_json = excluded.error_json""",
            (
                run_id,
                rid.day.isoformat(),
                rid.seq,
                mode,
                version,
                msha,
                status,
                started,
                utc_now(),
                issues_json(issues),
            ),
        )
        _event(conn, "import_" + status, run_id, {"rules": sorted({i.rule for i in issues})})
    log.warning("import %s %s: %s", run_id, status, ",".join(sorted({i.rule for i in issues})))


def _apply(conn: sqlite3.Connection, run: ValidRun, *, dry_run: bool) -> ImportResult:
    """Steps 6 and 7 in one BEGIN IMMEDIATE transaction under the write lock."""
    rid = parse_run_id(run.run_id)
    now = utc_now()
    result = ImportResult(run.run_id, DRY_RUN if dry_run else IMPORTED)
    try:
        with write_transaction(conn):
            before = imported_state(conn)
            conn.execute(
                """INSERT INTO import_runs (run_id, run_date, run_seq, mode, schema_version,
                       manifest_sha256, status, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'importing', ?)
                   ON CONFLICT (run_id) DO UPDATE SET
                     mode = excluded.mode, schema_version = excluded.schema_version,
                     manifest_sha256 = excluded.manifest_sha256, status = 'importing',
                     started_at = excluded.started_at, error_json = NULL""",
                (
                    run.run_id,
                    rid.day.isoformat(),
                    rid.seq,
                    run.mode,
                    run.schema_version,
                    run.manifest_sha256,
                    now,
                ),
            )
            store_sections(conn, run)
            me = run_order_key(run.run_id)
            if any(run_order_key(r) > me for r in applied_runs(conn)):
                stats: ApplyStats = replay(conn, now)[run.run_id]  # arrived out of order
            else:
                stats = apply_run(conn, run, now)
            conn.execute(
                "UPDATE import_runs SET status = 'imported' WHERE run_id = ?", (run.run_id,)
            )
            diff = diff_counts(before, imported_state(conn))
            txns = diff["transactions"]
            result.counts = {
                "inserted": txns["inserted"],
                "updated": txns["updated"],
                "removed": txns["removed"],
                "superseded": stats.superseded,
                "drift": stats.drift,
                "statements": stats.statements,
                "applied": stats.applied,
                "replaced": stats.replaced,
                "rows": stats.rows,
                "rekeyed": stats.rekeyed,
                "orphaned": stats.orphaned,
                "changes": sum(sum(c.values()) for c in diff.values()),
                "tables": {t: c for t, c in sorted(diff.items()) if any(c.values())},
            }
            result.warnings = sorted(set(run.warnings) | set(stats.warnings))
            conn.execute(
                """UPDATE import_runs SET finished_at = ?, counts_json = ?, warnings_json = ?
                   WHERE run_id = ?""",
                (
                    utc_now(),
                    json.dumps(result.counts, sort_keys=True),
                    issues_json(result.warnings),
                    run.run_id,
                ),
            )
            _event(conn, "import", run.run_id, {"changes": result.counts["changes"]})
            if dry_run:
                raise _DryRunRollback
    except _DryRunRollback:
        pass
    return result


def import_folder(
    folder: Path,
    *,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    private: PrivateRules | None = None,
    today: date | None = None,
    dry_run: bool = False,
    retry: bool = False,
) -> ImportResult:
    """Import one run folder (steps 3 to 8). Never raises for bad input."""
    settings = settings or get_settings()
    own = conn is None
    conn = conn or connect(settings.db_path)
    try:
        return _import_folder(conn, Path(folder), settings, private, today, dry_run, retry)
    finally:
        if own:
            conn.close()


def _import_folder(
    conn: sqlite3.Connection,
    folder: Path,
    settings: Settings,
    private: PrivateRules | None,
    today: date | None,
    dry_run: bool,
    retry: bool,
) -> ImportResult:
    run_id = folder.name
    if not is_run_id(run_id):
        return ImportResult("(invalid)", FAILED, [Issue("run-id", "(folder name)")])
    started = utc_now()
    msha = manifest_sha256(folder)
    old = conn.execute(
        "SELECT status, manifest_sha256 FROM import_runs WHERE run_id = ?", (run_id,)
    ).fetchone()

    # Step 3: skip known runs, reject a changed manifest.
    if old is not None and old["status"] in (IMPORTED, "purged"):
        if msha is None or msha == old["manifest_sha256"]:
            return ImportResult(run_id, UNCHANGED, counts={"changes": 0})
        issue = Issue("run-changed", "manifest.json")
        if not dry_run:
            seen = conn.execute(
                """SELECT 1 FROM events WHERE kind = 'import_rejected' AND entity_key = ?
                   AND detail_json = ?""",
                (
                    run_id,
                    json.dumps({"manifest_sha256": msha, "rule": "run-changed"}, sort_keys=True),
                ),
            ).fetchone()
            if seen is None:
                with write_transaction(conn):
                    _event(
                        conn,
                        "import_rejected",
                        run_id,
                        {"rule": "run-changed", "manifest_sha256": msha},
                    )
                log.warning("import %s rejected: run-changed", run_id)
        return ImportResult(run_id, FAILED, [issue])
    if (
        old is not None
        and old["status"] == FAILED
        and not retry
        and not dry_run
        and msha == old["manifest_sha256"]
    ):
        return ImportResult(run_id, SKIPPED)

    try:
        private = private if private is not None else private_rules_for(settings)
        verified, issues = verify_run(folder, today or household_today(), private)
        mode = version = None
        if verified is not None:
            mode = verified.manifest.mode
            version = verified.manifest.schema_version
            valid, issues = validate_run(verified, load_registry(conn, run_id), private)
        if issues:
            rules = {i.rule for i in issues}
            status = WAITING if rules <= DEPENDENCY_RULES else FAILED
            if not dry_run:
                _record_rejected(conn, run_id, status, issues, msha, mode, version, started)
            return ImportResult(run_id, status, issues)
        assert valid is not None
        result = _apply(conn, valid, dry_run=dry_run)
    except Exception as exc:  # never log str(exc): it may hold a value
        tb = exc.__traceback__
        while tb is not None and tb.tb_next is not None:
            tb = tb.tb_next
        where = f"{tb.tb_frame.f_code.co_filename.rsplit('/', 1)[-1]}:{tb.tb_lineno}" if tb else ""
        log.error("import %s stopped: %s at %s", run_id, type(exc).__name__, where)
        issues = [Issue("internal", type(exc).__name__)]
        if not dry_run:
            _record_rejected(conn, run_id, FAILED, issues, msha, None, None, started)
        return ImportResult(run_id, FAILED, issues)
    if not dry_run:
        log.info("import %s imported: %d changes", run_id, result.changes)
    return result


def retry_waiting(
    conn: sqlite3.Connection,
    folders: dict[str, Path],
    settings: Settings,
    private: PrivateRules | None,
    today: date | None,
) -> list[ImportResult]:
    """Retry waiting runs (in run order) until none of them imports."""
    results: list[ImportResult] = []
    while True:
        waiting = [
            r["run_id"]
            for r in conn.execute("SELECT run_id FROM import_runs WHERE status = 'waiting'")
        ]
        progress = False
        for run_id in sorted(waiting, key=run_order_key):
            if run_id not in folders:
                continue
            r = _import_folder(conn, folders[run_id], settings, private, today, False, True)
            results.append(r)
            progress |= r.status == IMPORTED
        if not progress:
            return results


def _is_run_folder(path: Path) -> bool:
    return is_run_id(path.name) or (path / "manifest.json").exists()


def _child_runs(root: Path) -> list[Path]:
    """Run folders directly inside `root`, in run order.

    Names that start with '.' or '_', end in '.partial', or are not run ids are skipped.
    """
    found = [
        p
        for p in root.iterdir()
        if p.is_dir()
        and not p.name.startswith((".", "_"))
        and not p.name.endswith(".partial")
        and is_run_id(p.name)
    ]
    return sorted(found, key=lambda p: run_order_key(p.name))


def run_folders(path: Path) -> list[Path]:
    """`path` itself if it is a run folder, else its run folders in run order."""
    return [path] if _is_run_folder(path) else _child_runs(path)


def retry_folders(path: Path, given: list[Path], settings: Settings) -> dict[str, Path]:
    """Where waiting runs may be found: the given folders, the sibling run folders
    of a given run folder, and the import folder. Only waiting runs are read."""
    out: dict[str, Path] = {}
    roots = [settings.import_dir]
    if _is_run_folder(path):
        roots.insert(0, path.parent)
    for root in roots:
        if root.is_dir():
            for f in _child_runs(root):
                out.setdefault(f.name, f)
    out.update({f.name: f for f in given})
    return out


def import_path(
    path: Path,
    *,
    settings: Settings | None = None,
    private: PrivateRules | None = None,
    today: date | None = None,
    dry_run: bool = False,
    retry: bool = False,
) -> list[ImportResult]:
    """Import a run folder or a folder of runs, in run order. Waiting runs are retried."""
    settings = settings or get_settings()
    private = private if private is not None else private_rules_for(settings)
    conn = connect(settings.db_path)
    try:
        folders = run_folders(Path(path))
        results = [
            _import_folder(conn, f, settings, private, today, dry_run, retry) for f in folders
        ]
        if not dry_run and any(r.status == IMPORTED for r in results):
            retried = retry_waiting(
                conn, retry_folders(Path(path), folders, settings), settings, private, today
            )
            latest = {r.run_id: r for r in results + retried}
            given = {r.run_id for r in results}
            results = [latest[r.run_id] for r in results]
            # waiting runs from other folders that now imported are reported too
            results += sorted(
                (r for k, r in latest.items() if k not in given and r.status == IMPORTED),
                key=lambda r: run_order_key(r.run_id),
            )
        return results
    finally:
        conn.close()


def retry_run(
    run_id: str,
    *,
    settings: Settings | None = None,
    private: PrivateRules | None = None,
    today: date | None = None,
) -> ImportResult:
    """The admin Retry: import `<import_dir>/<run_id>` again, even if it failed before."""
    settings = settings or get_settings()
    if not is_run_id(run_id):
        return ImportResult("(invalid)", FAILED, [Issue("run-id", "(folder name)")])
    folder = settings.import_dir / run_id
    if not folder.is_dir():
        return ImportResult(run_id, FAILED, [Issue("file-missing", "(folder)")])
    return import_folder(folder, settings=settings, private=private, today=today, retry=True)


def reapply(settings: Settings | None = None, *, dry_run: bool = False) -> dict:
    """Empty the imported tables and replay every stored run in run order.

    Overrides, users, settings and AI tables are kept. Returns the change counts.
    """
    settings = settings or get_settings()
    conn = connect(settings.db_path)
    counts: dict = {}
    try:
        with write_transaction(conn):
            before = imported_state(conn)
            replay(conn, utc_now())
            diff = diff_counts(before, imported_state(conn))
            counts = {
                "runs": len(applied_runs(conn)),
                "changes": sum(sum(c.values()) for c in diff.values()),
                "tables": {t: c for t, c in sorted(diff.items()) if any(c.values())},
            }
            if dry_run:
                raise _DryRunRollback
    except _DryRunRollback:
        pass
    finally:
        conn.close()
    log.info("reapply: %d runs, %d changes", counts["runs"], counts["changes"])
    return counts


def describe(result: ImportResult) -> list[str]:
    """Plain text lines for one result. Rule ids, locations and messages only."""
    c = result.counts
    if result.status in (IMPORTED, DRY_RUN):
        head = (
            f"{result.run_id}  {result.status}  {c['changes']} changes "
            f"(rows +{c['inserted']} ~{c['updated']} -{c['removed']}, "
            f"statements {c['applied']} applied, {c['superseded']} superseded, "
            f"{c['drift']} drift)"
        )
    elif result.status == UNCHANGED:
        head = f"{result.run_id}  unchanged  0 changes"
    elif result.status == SKIPPED:
        head = f"{result.run_id}  skipped  failed before with the same manifest (use --retry)"
    elif result.status == WAITING:
        head = f"{result.run_id}  waiting  not imported. {rule_message('waiting-earlier-runs')}"
    else:
        head = f"{result.run_id}  {result.status}"
    lines = [head]
    for i in result.issues:
        lines.append(f"  error    {i.rule}  {i.where}  {rule_message(i.rule)}")
    for w in result.warnings:
        lines.append(f"  warning  {w.rule}  {w.where}  {rule_message(w.rule)}")
    return lines
