"""The demo database's writer: demo sessions in, demo sessions out.

After the build, creating a session at start and deleting it on leave are the only
writes to the demo database. They never go through `connect()` (its pragmas turn WAL
back on) or `migrate()` (it writes on every call). The writer opens the file `mode=rw`,
which never creates a file, and writes under the process-wide `WRITE_LOCK`.

The file stays in DELETE journal mode, so readers block a commit for a moment
(read-only connections have a busy timeout). A crash mid-commit leaves a hot journal:
read-only connections then fail (closed) until the next start rebuilds.

A demo session lasts 8 hours from the start, with no idle touch: the request side is
read only, so the row never changes. Expiry uses the real auth clock.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from app.auth import clock
from app.auth.sessions import TOKEN_BYTES, purge_expired, token_hash
from app.db.connect import BUSY_TIMEOUT_MS, write_transaction
from app.demo.context import DemoGuardError

DEMO_USER_ID = 900  # ids are per database; keep the demo user's away from the real ones
LIFETIME = timedelta(hours=8)


def open_rw(db_file: Path) -> sqlite3.Connection:
    """A raw read-write connection to an existing demo file. Rows are `sqlite3.Row`.

    The path is the resolved folder plus the name, and a link in the name's place is
    refused, so a planted `demo.db` link cannot point the writer at another file.
    """
    path = db_file.parent.resolve() / db_file.name
    if path.is_symlink():
        raise DemoGuardError("demo database is a link")
    conn = sqlite3.connect(
        f"{path.as_uri()}?mode=rw",
        uri=True,
        isolation_level=None,
        check_same_thread=False,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def write(db_file: Path) -> Iterator[sqlite3.Connection]:
    """One write transaction on the demo file. Closed on exit."""
    conn = open_rw(db_file)
    try:
        with write_transaction(conn):
            yield conn
    finally:
        conn.close()


def create_session(db_file: Path, user_id: int = DEMO_USER_ID) -> str:
    """Store a demo session for the demo user and return the cookie token."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = clock.now()
    with write(db_file) as conn:
        purge_expired(conn)
        conn.execute(
            """INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token_hash(token), user_id, clock.iso(now), clock.iso(now), clock.iso(now + LIFETIME)),
        )
    return token


def delete_session(db_file: Path, token: str | None) -> None:
    if not token:
        return
    with write(db_file) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
