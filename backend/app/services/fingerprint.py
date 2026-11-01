"""Fingerprint of the imported data (PLAN 4.11, idempotency guarantee).

A sha256 over the imported, current rows only: people, accounts, current
statements, live transactions, non-estimate balances, holdings, paychecks,
contributions, utility bills with their lines and credits, trips with their
people, items and IOUs, the newest version of each state section (rewards
included), and the app's own inputs: overrides, the rewards inputs
(migration 0010) and the budget target edits (0014). Imports never write
the inputs, and `cli reapply` keeps them, so a restore drill sees a loss.
Timestamps, run ids and losing statement versions are left out, so the same
runs give the same fingerprint in any arrival order, and after `cli reapply`.

`imported_state` is also what the importer diffs to count changes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from app.importer.models import STATE_SECTIONS
from app.importer.order import run_order_key

# table -> (key columns, WHERE clause). Bookkeeping columns are left out.
_BOOKKEEPING = {"run_id", "first_run_id", "removed_at", "is_current", "updated_at", "updated_by"}
TABLES: dict[str, tuple[tuple[str, ...], str]] = {
    "people": (("person_key",), ""),
    "accounts": (("account_key",), ""),
    "statements": (("statement_key",), "WHERE is_current = 1"),
    "transactions": (("txn_key",), "WHERE removed_at IS NULL"),
    "balances": (("account_key", "as_of", "source"), ""),
    "holdings": (("statement_key", "holding_key"), ""),
    "paychecks": (("paycheck_key",), ""),
    "contributions": (("contribution_key",), ""),
    "utility_bills": (("bill_key",), ""),
    "utility_bill_lines": (("bill_key", "service"), ""),
    "utility_bill_credits": (("bill_key", "seq"), ""),
    "trips": (("trip_key",), ""),
    "trip_people": (("trip_key", "person_key"), ""),
    "trip_items": (("trip_key", "item_key"), ""),
    "trip_ious": (("trip_key", "iou_key"), ""),
    "transaction_overrides": (("txn_key",), ""),
    "reward_balances": (("program_key", "as_of"), ""),
    "reward_app_programs": (("program_key",), ""),
    "reward_benefit_edits": (("benefit_key",), ""),
    "reward_match_terms": (("benefit_key", "term"), ""),
    "reward_credit_decisions": (("txn_key",), ""),
    "reward_cycle_marks": (("benefit_key", "cycle_start"), ""),
    "budget_target_edits": (("kind", "target_key", "month"), ""),
}

State = dict[str, dict[tuple, tuple]]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] not in _BOOKKEEPING]


def imported_state(conn: sqlite3.Connection) -> State:
    """table -> {key tuple: row tuple} for the rows the fingerprint covers."""
    out: State = {}
    for table, (keys, where) in TABLES.items():
        cols = _columns(conn, table)
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} {where}").fetchall()
        idx = [cols.index(k) for k in keys]
        out[table] = {tuple(r[i] for i in idx): tuple(r) for r in rows}
    out["state"] = {(name,): (sha,) for name, sha in newest_state(conn).items()}
    return out


def newest_state(conn: sqlite3.Connection) -> dict[str, str]:
    """State section -> sha256 of its newest version (newest imported monthly run that has it).

    State comes from monthly runs only (PLAN 3.7). Other modes are rejected if
    they carry state, and are ignored here too.
    """
    rows = conn.execute(
        """SELECT s.run_id, s.section, s.sha256, s.json FROM run_sections s
           JOIN import_runs r ON r.run_id = s.run_id
           WHERE r.status = 'imported' AND r.mode = 'monthly' AND s.section IN ({})""".format(
            ", ".join("?" for _ in (*STATE_SECTIONS, "income"))
        ),
        (*STATE_SECTIONS, "income"),
    ).fetchall()
    out: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: run_order_key(r["run_id"])):
        if r["section"] == "income":
            streams = json.loads(r["json"]).get("income_streams")
            if streams is not None:
                text = json.dumps(streams, sort_keys=True, separators=(",", ":"))
                out["income.income_streams"] = hashlib.sha256(text.encode()).hexdigest()
        else:
            out[r["section"]] = r["sha256"]
    return out


def diff_counts(before: State, after: State) -> dict[str, dict[str, int]]:
    """Per table: rows inserted, updated and removed between two states."""
    out: dict[str, dict[str, int]] = {}
    for table in after.keys() | before.keys():
        b, a = before.get(table, {}), after.get(table, {})
        out[table] = {
            "inserted": sum(1 for k in a if k not in b),
            "updated": sum(1 for k in a if k in b and a[k] != b[k]),
            "removed": sum(1 for k in b if k not in a),
        }
    return out


def _default(v: Any) -> Any:
    raise TypeError("unexpected type in fingerprint")


def fingerprint(conn: sqlite3.Connection) -> str:
    """sha256 hex over the imported, current rows (see module doc)."""
    h = hashlib.sha256()
    for table, rows in sorted(imported_state(conn).items()):
        h.update(table.encode())
        for key in sorted(rows, key=lambda k: json.dumps(k, default=_default)):
            h.update(json.dumps(rows[key], separators=(",", ":"), default=_default).encode())
            h.update(b"\n")
    return h.hexdigest()
