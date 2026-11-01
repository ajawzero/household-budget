"""Demo state for one request or the demo build: the `connect()` guard and the pinned date.

Two ContextVars, set only by the demo middleware (one demo request) and the demo build.
There is no module-level "demo on" flag: jobs run from contexts made before any request
(the scheduler task) or in new threads, so they never see these and stay on the real data.

Stdlib only, so `config.py`, `db/connect.py` and the outbound code can import it.

A new `threading.Thread` starts with an empty context on a GIL build of Python
(`sys.flags.thread_inherit_context` is 0). A free-threaded build would hand a thread
started from a demo request the demo context. The image is a GIL build.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# The day the sample runs are built around. Demo "today".
DEMO_TODAY = date(2026, 10, 20)


class DemoGuardError(RuntimeError):
    """A database open in demo named a file other than the demo database."""


class DemoRefused(RuntimeError):
    """An AI call or an outbound fetch in demo. Not an OSError, so the transports
    cannot turn it into a quiet network error."""


@dataclass(frozen=True, slots=True)
class Guard:
    """The one database file `connect()` may open. `writable` only in the build."""

    path: Path
    writable: bool


_GUARD: ContextVar[Guard | None] = ContextVar("demo_guard", default=None)
_PINNED_TODAY: ContextVar[date | None] = ContextVar("demo_pinned_today", default=None)


def current_guard() -> Guard | None:
    return _GUARD.get()


def in_demo() -> bool:
    """True in a demo request and in the demo build."""
    return _GUARD.get() is not None


def pinned_date() -> date | None:
    return _PINNED_TODAY.get()


@contextmanager
def guarded(db_file: Path, *, writable: bool = False) -> Iterator[Guard]:
    """Allow `connect()` to open `db_file` only, read only unless `writable`.

    The allowed path is the resolved folder plus the file name, never the resolved
    file: a `demo.db` link to the real database then resolves elsewhere and is refused.
    """
    guard = Guard(db_file.parent.resolve() / db_file.name, writable)
    token = _GUARD.set(guard)
    try:
        yield guard
    finally:
        _GUARD.reset(token)


@contextmanager
def cleared() -> Iterator[None]:
    """No guard and no pin inside. Every request that is not demo runs under it: uvicorn
    can start the next request on a keep-alive connection from a copy of a demo
    request's context (when the demo request resumed reading the socket)."""
    guard, day = _GUARD.set(None), _PINNED_TODAY.set(None)
    try:
        yield
    finally:
        _PINNED_TODAY.reset(day)
        _GUARD.reset(guard)


@contextmanager
def pinned(day: date = DEMO_TODAY) -> Iterator[None]:
    """`household_now()` and `household_today()` give `day` inside."""
    token = _PINNED_TODAY.set(day)
    try:
        yield
    finally:
        _PINNED_TODAY.reset(token)
