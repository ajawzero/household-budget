"""Steps 1 and 2 of the import (PLAN 4.11): poll the import folder.

- Poll every 60 seconds and at startup (the scheduler calls `poll_once`).
- Skip names that start with '.' or '_', end in '.partial', or are not run ids.
- A run is ready when its manifest parses and every listed file has the listed
  size and sha256. If it is not ready, wait. It is imported (and so fails with
  the real rule ids) only once the folder looks the same on two polls in a row.
- Known runs: same manifest, nothing to do. A different manifest for an
  imported run is rejected as "run-changed". Waiting runs are tried again
  after any later run imports.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.config import Settings, get_settings, household_today
from app.db.connect import connect
from app.importer.models import load_manifest
from app.importer.order import is_run_id, run_order_key
from app.importer.runner import FAILED, IMPORTED, WAITING, ImportResult, import_folder
from app.importer.sanitize import PrivateRules
from app.importer.verify import FileProblem, manifest_sha256, open_root, read_relative

log = logging.getLogger("app.importer")

NOT_READY = "not-ready"  # result status while the stability wait runs


def skip_name(name: str) -> bool:
    return name.startswith((".", "_")) or name.endswith(".partial")


def candidate_folders(import_dir: Path) -> list[Path]:
    """Run folders to look at, in run order. Other names are skipped (and counted)."""
    if not import_dir.is_dir():
        return []
    found, other = [], 0
    for p in import_dir.iterdir():
        if skip_name(p.name):
            continue
        if not is_run_id(p.name) or not (p.is_dir() or p.is_symlink()):
            other += 1
            continue
        found.append(p)
    if other:
        log.info("import poll skipped %d entries that are not run folders", other)
    return sorted(found, key=lambda p: run_order_key(p.name))


def folder_signature(folder: Path) -> tuple:
    """(path, size, mtime) for every entry, without following links."""
    out = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            out.append((p.relative_to(folder).as_posix(), st.st_size, st.st_mtime_ns))
    return tuple(sorted(out))


def is_ready(folder: Path) -> bool:
    """The manifest parses and every listed file matches its size and sha256."""
    try:
        root = open_root(folder)
    except FileProblem:
        return False
    try:
        manifest, issues = load_manifest(read_relative(root, "manifest.json"))
        if manifest is None:
            return False
        for f in manifest.files:
            data = read_relative(root, f.path)
            if len(data) != f.bytes or hashlib.sha256(data).hexdigest() != f.sha256:
                return False
        return True
    except FileProblem:
        return False
    finally:
        os.close(root)


@dataclass
class _Known:
    status: str
    manifest_sha256: str | None


class ImportPoller:
    """Keeps the stability state between polls. One instance per process."""

    def __init__(
        self,
        settings: Settings | None = None,
        private: PrivateRules | None = None,
        today: Callable[[], date] = household_today,
    ) -> None:
        self.settings = settings or get_settings()
        self.private = private
        self.today = today
        self._last_signature: dict[str, tuple] = {}
        self._reported_changed: dict[str, str | None] = {}

    def poll_once(self) -> list[ImportResult]:
        """Look at the import folder once and import what is ready."""
        results: list[ImportResult] = []
        conn = connect(self.settings.db_path)
        try:
            for folder in candidate_folders(self.settings.import_dir):
                r = self._consider(conn, folder)
                if r is not None:
                    results.append(r)
        finally:
            conn.close()
        return results

    def _known(self, conn, run_id: str) -> _Known | None:
        row = conn.execute(
            "SELECT status, manifest_sha256 FROM import_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _Known(row["status"], row["manifest_sha256"]) if row else None

    def _imported_since(self, conn, run_id: str) -> bool:
        """True when any run imported after this run was last put on waiting (event order)."""
        row = conn.execute(
            """SELECT (SELECT max(id) FROM events WHERE kind = 'import'),
                      (SELECT max(id) FROM events
                       WHERE kind = 'import_waiting' AND entity_key = ?)""",
            (run_id,),
        ).fetchone()
        return row[0] is not None and (row[1] is None or row[0] > row[1])

    def _consider(self, conn, folder: Path) -> ImportResult | None:
        run_id = folder.name
        known = self._known(conn, run_id)
        msha = manifest_sha256(folder)
        if known is not None:
            same = msha == known.manifest_sha256
            if known.status in (IMPORTED, "purged"):
                if msha is None or same or self._reported_changed.get(run_id) == msha:
                    return None
                self._reported_changed[run_id] = msha
                return self._import(conn, folder)  # rejected as run-changed
            elif known.status == FAILED and same:
                return None
            elif known.status == WAITING and same and not self._imported_since(conn, run_id):
                return None

        if not is_ready(folder):
            sig = folder_signature(folder)
            if self._last_signature.get(run_id) != sig:
                self._last_signature[run_id] = sig
                return ImportResult(run_id, NOT_READY)
            # Unchanged since the last poll: import it so it fails with the real rule ids.
        self._last_signature.pop(run_id, None)
        return self._import(conn, folder)

    def _import(self, conn, folder: Path) -> ImportResult:
        return import_folder(
            folder, conn=conn, settings=self.settings, private=self.private, today=self.today()
        )
