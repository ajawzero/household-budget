"""SQLite connections, pragmas and the process-wide write lock (PLAN 2.2).

- Every connection: WAL, synchronous NORMAL, foreign_keys ON, busy_timeout 5000.
- Connections run in autocommit mode (`isolation_level=None`). Writes use
  `write_transaction(conn)`, which holds `WRITE_LOCK` and runs BEGIN IMMEDIATE.
- Readers open their own connections (`read_connection()`).

The AI read-only connection with its authorizer comes in Phase 6.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings
from app.demo.context import DemoGuardError, current_guard

BUSY_TIMEOUT_MS = 5000

# One writer at a time, across the whole process (API, import poll, jobs).
WRITE_LOCK = threading.RLock()

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
)


def db_path() -> Path:
    return get_settings().db_path


def connect(path: str | Path | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with the standard pragmas. Rows are `sqlite3.Row`.

    `readonly=True` opens with `mode=ro` and sets `query_only`.

    In a demo request or the demo build (app/demo/context.py) only the guarded file
    opens, and read only unless the guard is writable. Any other file raises
    `DemoGuardError` before a folder or file is made.
    """
    target = Path(path) if path is not None else db_path()
    guard = current_guard()
    if guard is not None:
        if target.resolve() != guard.path:
            raise DemoGuardError("database open refused in demo")
        readonly = readonly or not guard.writable
    if readonly:
        conn = sqlite3.connect(
            f"{target.resolve().as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
            check_same_thread=False,
            timeout=BUSY_TIMEOUT_MS / 1000,
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            target, isolation_level=None, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000
        )
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        if readonly and pragma.startswith("PRAGMA journal_mode"):
            continue  # a read-only connection cannot change the journal mode
        conn.execute(pragma)
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


@contextmanager
def read_connection(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """A short-lived connection for reads. Closed on exit."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Hold the write lock and run one BEGIN IMMEDIATE transaction.

    Commits on success, rolls back on any exception (and re-raises).
    """
    with WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


@contextmanager
def write_connection(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection and run one write transaction on it. Closed on exit."""
    conn = connect(path)
    try:
        with write_transaction(conn):
            yield conn
    finally:
        conn.close()
