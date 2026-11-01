"""Build the demo database from the sample runs (demo plan section 3).

Built on the first demo start after an app start, never at startup, under a
process-wide lock, so a double tap or two tabs build once. In order:
1. Check the folders and the manifests (`generator.skill` is `make_samples`, the
   same check as `check.sh`), so a real export in the runs folder cannot become the demo.
2. Create a new, empty temp file (`O_EXCL`, no link followed). An empty file gets no
   pre-migrate copy, and the build passes its own never-made `backup_dir`: a migrate
   with the real `backup_dir` would copy into the real backups folder and prune real
   rollback copies.
3. With the `connect()` guard on the temp file and the date pinned, `migrate()` and
   `import_path()` with every argument given. One left out falls back to the real
   Settings, and the guard turns that into an error.
4. A raw writer adds the demo user, relabels the house price index rows (their labels
   name the real region), sets the import times to the run dates (the Imports list shows
   them), writes the `demo_build` marker with the fingerprint and turns WAL off.
5. `os.replace` the temp file into place.

Ready means the file is there in DELETE mode, at the newest migration, with the
marker. Never just "the file exists": a stray open could leave an empty file.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from app.config import Settings
from app.db.connect import WRITE_LOCK, write_transaction
from app.db.migrate import list_migrations, migrate, user_version
from app.demo.context import DEMO_TODAY, guarded, pinned
from app.demo.folders import (
    BUILD_DB,
    DEMO_DB,
    OWN_NAMES,
    demo_db_file,
    demo_dir_problem,
    make_demo_dir,
    remove_own_files,
    runs_problem,
)
from app.demo.settings import demo_settings
from app.demo.store import DEMO_USER_ID, open_rw
from app.importer.runner import IMPORTED, import_path, run_folders
from app.importer.sanitize import PrivateRules
from app.services.fingerprint import fingerprint

log = logging.getLogger("app.demo")

MAKE_SAMPLES = "make_samples"
MARKER_KEY = "demo_build"
BUILD_BACKUPS = "build-backups"
INDEX_LABEL = "Sample house price index"
# The fake terms of samples/private_rules.example.json. The file cannot ship under its
# own name (the scan blocks private_rules*), so the build holds them here. A test
# checks they equal the file.
SAMPLE_RULES = PrivateRules(
    subscriptions=("EXAMPLE PRIVATE SERVICE",),
    employer_terms=("EXAMPLE EMPLOYER CO",),
)
# The demo user: a member, so the admin screens and actions stay out of demo. Not the
# test admin's `alex`, so tests can tell demo from real by username.
DEMO_USERNAME, DEMO_DISPLAY_NAME, DEMO_ROLE = "sample", "Alex", "member"
# Not an argon2 hash, so it never verifies (an empty hash would fall back to the dummy).
UNUSABLE_HASH = "!"

_BUILD_NAMES = tuple(n for n in OWN_NAMES if n.startswith(BUILD_DB))
_DEMO_SIDE_NAMES = tuple(n for n in OWN_NAMES if n.startswith(f"{DEMO_DB}-"))
_LOCK = threading.Lock()  # process-wide: one build at a time


class DemoUnavailable(RuntimeError):
    """Demo cannot be built or used. The message is a value-free reason."""


def manifest_problem(runs: Path) -> str | None:
    """Why the runs are not all sample runs, or None. Every `*/manifest.json` (as
    `check.sh` reads them, `_x` names too) and every folder the import would read."""
    folders = run_folders(runs)
    if not folders:
        return "No sample runs found."
    manifests = {*runs.glob("*/manifest.json"), *(f / "manifest.json" for f in folders)}
    for manifest in sorted(manifests):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            skill = data.get("generator", {}).get("skill")
        except OSError, ValueError, AttributeError:
            skill = None
        if skill != MAKE_SAMPLES:
            return "A sample run was not made by make_samples."
    return None


def ready_marker(db_file: Path) -> str | None:
    """The marker fingerprint when `db_file` is a finished demo database, else None.

    Read only (`mode=ro`), so it never creates or changes a file.
    """
    path = db_file.parent.resolve() / db_file.name
    if path.is_symlink() or not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, isolation_level=None)
    except sqlite3.Error:
        return None
    try:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            return None
        if user_version(conn) != list_migrations()[-1].version:
            return None
        row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (MARKER_KEY,))
        found = row.fetchone()
        value = json.loads(found[0]).get("fingerprint") if found else None
        return value if isinstance(value, str) and value else None
    except sqlite3.Error, ValueError, AttributeError:
        return None
    finally:
        conn.close()


def is_ready(db_file: Path) -> bool:
    return ready_marker(db_file) is not None


def _finish(tmp: Path) -> None:
    """The last writes on the built temp file, then DELETE journal mode."""
    conn = open_rw(tmp)
    try:
        with write_transaction(conn):
            fp = fingerprint(conn)
            first = conn.execute("SELECT min(run_date) FROM import_runs").fetchone()[0]
            at = f"{first}T17:00:00+00:00"
            conn.execute(
                """INSERT INTO users (id, username, display_name, password_hash, role,
                       active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (DEMO_USER_ID, DEMO_USERNAME, DEMO_DISPLAY_NAME, UNUSABLE_HASH, DEMO_ROLE, at),
            )
            conn.execute("UPDATE index_series SET label = ?", (INDEX_LABEL,))
            # Real build times would sit after the demo "today". Noon on the run date.
            conn.execute(
                """UPDATE import_runs SET
                     started_at = run_date || printf('T17:%02d:00+00:00', run_seq),
                     finished_at = run_date || printf('T17:%02d:30+00:00', run_seq)"""
            )
            conn.execute(
                """UPDATE events SET at = coalesce(
                     (SELECT r.finished_at FROM import_runs r
                      WHERE events.entity = 'import_run' AND r.run_id = events.entity_key),
                     ?)""",
                (at,),
            )
            conn.execute(
                """INSERT INTO settings (key, value_json, updated_by, updated_at)
                   VALUES (?, ?, NULL, ?)""",
                (MARKER_KEY, json.dumps({"fingerprint": fp}), at),
            )
        if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise RuntimeError("demo database is not in DELETE journal mode")
    finally:
        conn.close()
    if any(Path(f"{tmp}{side}").exists() for side in ("-journal", "-wal", "-shm")):
        raise RuntimeError("demo database left side files")


def build(real: Settings) -> None:
    """Build `demo.db` in the demo folder from the sample runs. Raises on any problem;
    a `demo.db` already there is left as it was until the new one replaces it."""
    problem = demo_dir_problem(real) or runs_problem(real) or manifest_problem(real.demo_runs)
    if problem:
        raise DemoUnavailable(problem)
    folder = real.demo_dir
    make_demo_dir(folder)
    tmp = folder / BUILD_DB
    backups = folder / BUILD_BACKUPS
    remove_own_files(folder, _BUILD_NAMES)
    os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600))
    settings = demo_settings(real, db_file=tmp, backup_dir=backups)
    log.info("demo build", extra={"stage": "start"})
    try:
        with guarded(tmp, writable=True), pinned(DEMO_TODAY):
            migrate(
                db_path=tmp,
                backup_dir=backups,
                categories_file=real.categories_file,
                settings=settings,
            )
            if backups.exists():
                raise RuntimeError("demo build made a pre-migrate copy")
            if problem := manifest_problem(real.demo_runs):
                raise DemoUnavailable(problem)
            results = import_path(
                real.demo_runs, settings=settings, private=SAMPLE_RULES, today=DEMO_TODAY
            )
            if not results or any(r.status != IMPORTED for r in results):
                raise DemoUnavailable("A sample run did not import.")
            _finish(tmp)
        with WRITE_LOCK:  # no demo writer between the old file's side files and the swap
            remove_own_files(folder, _DEMO_SIDE_NAMES)
            os.replace(tmp, folder / DEMO_DB)
    finally:
        remove_own_files(folder, _BUILD_NAMES)
    log.info("demo build", extra={"stage": "done"})


def ensure_ready(real: Settings) -> str:
    """Build the demo database unless it is ready; return the marker fingerprint.

    Under the process-wide lock: a second caller waits, then finds it ready.
    """
    db_file = demo_db_file(real)
    with _LOCK:
        fp = ready_marker(db_file)
        if fp is None:
            build(real)
            fp = ready_marker(db_file)
        if fp is None:
            raise DemoUnavailable("The demo database is not ready.")
        return fp
