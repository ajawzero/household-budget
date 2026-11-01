"""The read-only AI connection and its authorizer (PLAN 2.2, 5.7).

Every byte the AI features send to Anthropic is read on this connection. It is
opened `mode=ro` with `query_only`, and `conn.set_authorizer` allows:

- a top-level read only of a view in SAFE_VIEWS (the *_safe views of
  migration 0008). A direct read of a table or of a v_* view is denied;
- reads inside a view definition (SQLite reports them with the view, or a CTE
  inside it, as the source). The exact (table, column, source) reads are
  recorded when the connection opens, by preparing `SELECT * FROM <view>` for
  every safe view, and only those are allowed. A CTE that borrows a safe view's
  name to read another column is denied;
- SQL functions on an allow list, plus those the safe views use;
- nothing else: no writes, PRAGMA, ATTACH or schema reads.

SQLite reports a CTE by its name, the same way as a view, so `run` also rejects
SQL with a WITH clause. It rejects NATURAL joins too: SQLite reports no read for
the columns a NATURAL join compares. And it rejects SQL that names any table or
view other than a safe view, before SQLite sees it. Callers get an
AiConnection, which has no `execute`, so every read goes through `run`. Tool SQL is fixed code with
parameters. The model never writes SQL (PLAN 7.4).

Note for tool SQL: `count(*)` and `count(1)` on a view make SQLite read the base
table directly, which the authorizer denies. Count a column instead.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.db.connect import connect

SAFE_VIEWS = frozenset(
    {
        "people_safe",
        "accounts_safe",
        "categories_safe",
        "transactions_safe",
        "cashflow_rows_safe",
        "txn_categorize_safe",
        "ai_facts_safe",
    }
)

ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "coalesce",
        "count",
        "date",
        "group_concat",
        "ifnull",
        "iif",
        "instr",
        "json_extract",
        "length",
        "like",
        "lower",
        "max",
        "min",
        "round",
        "substr",
        "sum",
        "total",
        "trim",
        "upper",
    }
)

_REJECT = re.compile(r"\b(with|natural)\b", re.IGNORECASE)

_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*|"[^"]*"|`[^`]*`|\[[^\]]*\]')

Read = tuple[str, str, str]  # (table, column, source)


class AuthorizerDenied(sqlite3.DatabaseError):
    """Raised by `run` when the authorizer denied part of a query."""


class _Authorizer:
    def __init__(self, reads: frozenset[Read], functions: frozenset[str]) -> None:
        self.reads = reads
        self.functions = functions
        self.denied = 0

    def __call__(
        self, action: int, a1: str | None, a2: str | None, db: str | None, src: str | None
    ):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if src is None and a1 in SAFE_VIEWS and db == "main":
                return sqlite3.SQLITE_OK
            if src is not None and (a1, a2, src) in self.reads:
                return sqlite3.SQLITE_OK
        elif action == sqlite3.SQLITE_FUNCTION:
            if a2 is not None and a2.lower() in self.functions:
                return sqlite3.SQLITE_OK
        self.denied += 1
        return sqlite3.SQLITE_DENY


def _record_view_reads(conn: sqlite3.Connection) -> tuple[frozenset[Read], frozenset[str]]:
    """(reads, functions) SQLite reports while preparing each safe view."""
    reads: set[Read] = set()
    functions: set[str] = set()

    def record(action: int, a1: str | None, a2: str | None, db: str | None, src: str | None):
        if action == sqlite3.SQLITE_READ and src is not None and a1 is not None:
            reads.add((a1, a2 or "", src))
        elif action == sqlite3.SQLITE_FUNCTION and a2:
            functions.add(a2.lower())
        return sqlite3.SQLITE_OK

    views = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall()
    }
    missing = SAFE_VIEWS - views
    if missing:
        raise RuntimeError("The database is missing safe views. Run the migrations.")
    conn.set_authorizer(record)
    try:
        for view in sorted(SAFE_VIEWS):
            conn.execute(f"EXPLAIN SELECT * FROM {view}").fetchall()
    finally:
        conn.set_authorizer(None)
    return frozenset(reads), frozenset(functions)


class AiConnection:
    """The read-only AI connection. Read only with `run`; there is no `execute`."""

    def __init__(self, conn: sqlite3.Connection, blocked: frozenset[str]) -> None:
        self._conn = conn
        # Every table and view name that is not a safe view (lower case).
        self.blocked = blocked

    def close(self) -> None:
        self._conn.close()


def open_ai_connection(path: str | Path | None = None) -> AiConnection:
    """A new read-only connection that can read the *_safe views only."""
    conn = connect(path, readonly=True)
    try:
        reads, view_functions = _record_view_reads(conn)
        names = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        blocked = frozenset(r[0].lower() for r in names) - SAFE_VIEWS
        conn.set_authorizer(_Authorizer(reads, ALLOWED_FUNCTIONS | view_functions))
    except BaseException:
        conn.close()
        raise
    return AiConnection(conn, blocked | {"sqlite_master", "sqlite_schema", "sqlite_temp_master"})


@contextmanager
def ai_connection(path: str | Path | None = None) -> Iterator[AiConnection]:
    conn = open_ai_connection(path)
    try:
        yield conn
    finally:
        conn.close()


def run(conn: AiConnection, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    """Run one parameterized query on the AI connection. No CTEs or NATURAL joins."""
    if not isinstance(conn, AiConnection):
        raise TypeError("run() takes the AI connection.")
    if _REJECT.search(sql):
        raise ValueError("Tool SQL may not use WITH or NATURAL.")
    names = {m.group(0).strip('"`[]').lower() for m in _NAME.finditer(sql)}
    if names & conn.blocked:
        raise AuthorizerDenied("The query names something other than a safe view.")
    try:
        return conn._conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.DatabaseError as exc:
        if "not authorized" in str(exc).lower() or "prohibited" in str(exc).lower():
            raise AuthorizerDenied("The query read something other than a safe view.") from None
        raise
