"""Server-side sessions (PLAN 5.1).

- The cookie holds 32 random bytes (URL-safe text). Only its sha256 is stored.
- Idle timeout 14 days, absolute timeout 30 days. `expires_at` is the sooner of
  the two and moves forward as the session is used.
- Expired rows are deleted during lookup and login. Logout deletes the row.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from app.auth import clock
from app.auth.users import User, get_user
from app.db.connect import write_transaction

TOKEN_BYTES = 32
IDLE = timedelta(days=14)
ABSOLUTE = timedelta(days=30)
TOUCH_EVERY = timedelta(minutes=5)  # skip a write on every request


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expires(created: datetime, last_seen: datetime) -> str:
    return clock.iso(min(last_seen + IDLE, created + ABSOLUTE))


def purge_expired(conn: sqlite3.Connection) -> None:
    """Delete expired sessions. Call inside a write transaction."""
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (clock.iso(clock.now()),))


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    """Store a new session and return the cookie token. Call inside a write transaction."""
    purge_expired(conn)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = clock.now()
    conn.execute(
        """INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (token_hash(token), user_id, clock.iso(now), clock.iso(now), _expires(now, now)),
    )
    return token


def lookup_session(
    conn: sqlite3.Connection, token: str | None, *, touch: bool = True
) -> User | None:
    """The user for a cookie token, or None. Extends the idle timeout when used.

    `touch=False` (demo, a read-only connection) writes nothing: no idle touch, and an
    expired row gives None without the purge.
    """
    if not token or len(token) > 128:
        return None
    th = token_hash(token)
    row = conn.execute(
        "SELECT user_id, created_at, last_seen_at, expires_at FROM sessions WHERE token_hash = ?",
        (th,),
    ).fetchone()
    if row is None:
        return None
    now = clock.now()
    if row["expires_at"] <= clock.iso(now):
        if touch:
            with write_transaction(conn):
                purge_expired(conn)
        return None
    user = get_user(conn, row["user_id"])
    if user is None:
        return None
    last_seen = datetime.fromisoformat(row["last_seen_at"])
    if touch and now - last_seen >= TOUCH_EVERY:
        created = datetime.fromisoformat(row["created_at"])
        with write_transaction(conn):
            conn.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                (clock.iso(now), _expires(created, now), th),
            )
    return user


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if not token:
        return
    with write_transaction(conn):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


def delete_user_sessions(
    conn: sqlite3.Connection, user_id: int, keep_token: str | None = None
) -> int:
    """Delete a user's sessions, except `keep_token`. Call inside a write transaction."""
    keep = token_hash(keep_token) if keep_token else ""
    cur = conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep)
    )
    return cur.rowcount
