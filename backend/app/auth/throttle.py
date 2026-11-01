"""Login throttles (PLAN 5.1), counted from `events` so they survive a restart.

- 5 failures for one username within 15 minutes lock that username for 15
  minutes from the fifth failure. Attempts while locked are not checked.
- Over 20 failures across all usernames in the last hour: 1 attempt a minute.

Failures for unknown usernames are stored as `unknown` in events and count
toward the global limit. So that a lock never shows which usernames exist, an
unknown name is also locked after 5 failures, the same as a real one. That
count lives in memory only, keyed by an HMAC of the name with a per-process
random key, so the name itself is never kept. It resets on restart.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

from app.auth import clock

USER_FAILURES = 5
USER_WINDOW = timedelta(minutes=15)
USER_LOCK = timedelta(minutes=15)
GLOBAL_FAILURES = 20
GLOBAL_WINDOW = timedelta(hours=1)
GLOBAL_SPACING = timedelta(minutes=1)

FAILED = "login_failed"
ATTEMPT_KINDS = ("login", "login_failed")


def _locked(times: list[datetime], now: datetime) -> bool:
    n = USER_FAILURES
    for i in range(n - 1, len(times)):
        if times[i] - times[i - n + 1] <= USER_WINDOW and times[i] + USER_LOCK > now:
            return True
    return False


def user_locked(conn: sqlite3.Connection, user_id: int) -> bool:
    now = clock.now()
    since = clock.iso(now - USER_WINDOW - USER_LOCK)
    times = [
        datetime.fromisoformat(r["at"])
        for r in conn.execute(
            "SELECT at FROM events WHERE kind = ? AND user_id = ? AND at > ? ORDER BY at",
            (FAILED, user_id, since),
        )
    ]
    return _locked(times, now)


# ---- unknown usernames (memory only) ----

_UNKNOWN_KEY = secrets.token_bytes(32)
_UNKNOWN_MAX_NAMES = 1000
_unknown: dict[str, list[datetime]] = {}
_unknown_guard = threading.Lock()


def _tag(username: str) -> str:
    name = username.strip().lower().encode("utf-8")
    return hmac.new(_UNKNOWN_KEY, name, hashlib.sha256).hexdigest()


def _prune(now: datetime) -> None:
    cutoff = now - USER_WINDOW - USER_LOCK
    for tag in list(_unknown):
        _unknown[tag] = [t for t in _unknown[tag] if t > cutoff]
        if not _unknown[tag]:
            del _unknown[tag]


def record_unknown_failure(username: str) -> None:
    now = clock.now()
    with _unknown_guard:
        _prune(now)
        if len(_unknown) >= _UNKNOWN_MAX_NAMES:
            _unknown.pop(next(iter(_unknown)))
        _unknown.setdefault(_tag(username), []).append(now)


def unknown_locked(username: str) -> bool:
    now = clock.now()
    with _unknown_guard:
        return _locked(sorted(_unknown.get(_tag(username), [])), now)


def reset_unknown() -> None:
    """Forget unknown-name failures (tests)."""
    with _unknown_guard:
        _unknown.clear()


def global_throttled(conn: sqlite3.Connection) -> bool:
    now = clock.now()
    failures = conn.execute(
        "SELECT count(*) FROM events WHERE kind = ? AND at > ?",
        (FAILED, clock.iso(now - GLOBAL_WINDOW)),
    ).fetchone()[0]
    if failures <= GLOBAL_FAILURES:
        return False
    recent = conn.execute(
        "SELECT 1 FROM events WHERE kind IN (?, ?) AND at > ? LIMIT 1",
        (*ATTEMPT_KINDS, clock.iso(now - GLOBAL_SPACING)),
    ).fetchone()
    return recent is not None
