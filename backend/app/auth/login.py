"""Login, logout and password change, with events and throttles (PLAN 5.1).

Events: `login` and `login_failed`. detail_json holds the username only when it
exists, otherwise `unknown`. Never a password.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

from app.auth import clock
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.sessions import create_session, delete_user_sessions
from app.auth.throttle import (
    global_throttled,
    record_unknown_failure,
    unknown_locked,
    user_locked,
)
from app.auth.users import User, find_user, password_hash_of, store_password_hash
from app.db.connect import write_transaction

log = logging.getLogger("app.auth")

OK, WRONG, THROTTLED = "ok", "wrong", "throttled"


@dataclass(frozen=True)
class LoginResult:
    status: str
    user: User | None = None
    token: str | None = None


def record_event(
    conn: sqlite3.Connection, kind: str, user_id: int | None, detail: dict | None = None
) -> None:
    """Insert one events row. Call inside a write transaction."""
    conn.execute(
        "INSERT INTO events (at, user_id, kind, entity, entity_key, detail_json) "
        "VALUES (?, ?, ?, 'user', ?, ?)",
        (
            clock.iso(clock.now()),
            user_id,
            kind,
            str(user_id) if user_id is not None else None,
            json.dumps(detail, sort_keys=True) if detail is not None else None,
        ),
    )


def login(conn: sqlite3.Connection, username: str, password: str) -> LoginResult:
    found = find_user(conn, username)
    user, stored = found if found else (None, None)
    locked = user_locked(conn, user.id) if user is not None else unknown_locked(username)
    if global_throttled(conn) or locked:
        log.warning("login throttled")
        return LoginResult(THROTTLED)
    ok = verify_password(stored, password)  # runs a dummy check for unknown users
    with write_transaction(conn):
        if not ok or user is None:
            record_event(
                conn,
                "login_failed",
                user.id if user else None,
                {"username": user.username if user else "unknown"},
            )
            if user is None:
                record_unknown_failure(username)
            log.info("login failed")
            return LoginResult(WRONG)
        if stored and needs_rehash(stored):
            store_password_hash(conn, user.id, hash_password(password))
        token = create_session(conn, user.id)
        record_event(conn, "login", user.id, {"username": user.username})
    log.info("login ok", extra={"user_id": user.id})
    return LoginResult(OK, user, token)


def change_password(
    conn: sqlite3.Connection, user: User, current: str, new: str, keep_token: str | None
) -> str:
    """OK, WRONG or THROTTLED. Other sessions of the user are signed out on success."""
    if user_locked(conn, user.id):
        return THROTTLED
    if not verify_password(password_hash_of(conn, user.id), current):
        with write_transaction(conn):
            record_event(
                conn, "login_failed", user.id, {"username": user.username, "via": "password_change"}
            )
        return WRONG
    new_hash = hash_password(new)
    with write_transaction(conn):
        store_password_hash(conn, user.id, new_hash)
        delete_user_sessions(conn, user.id, keep_token=keep_token)
        record_event(conn, "password_changed", user.id, {"username": user.username})
    log.info("password changed", extra={"user_id": user.id})
    return OK
