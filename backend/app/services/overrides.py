"""Transaction edits (PLAN 3.4, 4.11 step 6, 5.6).

Edits live in `transaction_overrides`, one row per txn_key. A null field means
"use the imported value" (or the rule's category). Each edit stores a guard:
the row's desc_norm, imported merchant, amount and date at edit time.

- `apply_edit` writes one or many rows' overrides and logs one
  `transaction_events` row per effective change. The caller holds the write
  transaction, so a bulk edit is all or nothing.
- `reattach_overrides` runs inside the importer when a winning statement
  version replaces an older one. An override whose row the new version
  dropped moves to a row whose key is new in this replace, has no override,
  and has the same desc_norm or merchant. The best match must be unique.
  Otherwise the override is orphaned and listed under "Edits that lost their
  transaction". A kept key always keeps its override: its date, amount and
  desc_norm are part of the key, so only the merchant can differ.

Event values are category keys or "true" or "false". Typed text (merchant,
note, tags) is never copied into events or logs: those events carry no values.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.importer.sanitize import PrivateRules, scan_text

EDIT_FIELDS = (
    "category_key",
    "merchant",
    "is_transfer",
    "is_reimbursable",
    "exclude_from_budget",
    "hidden",
    "notes",
    "tags",
)
FLAG_FIELDS = ("is_transfer", "is_reimbursable", "exclude_from_budget", "hidden")
# Edit field -> transaction_overrides column.
COLUMNS = {f: ("tags_json" if f == "tags" else f) for f in EDIT_FIELDS}
FLAG_EVENT = {
    "is_transfer": "transfer_changed",
    "is_reimbursable": "reimbursable_changed",
    "exclude_from_budget": "exclude_changed",
    "hidden": "hidden_changed",
}
TEXT_LABELS = {
    "merchant": "The merchant name",
    "notes": "The note",
    "tags": "A tag",
    "name": "The name",
}
SENSITIVE = (
    "{label} looks like it holds an account or card number, a phone number, an email, "
    "an address or a private name. Remove that part and try again."
)
NOT_FOUND = "Transaction not found."
UNKNOWN_CATEGORY = "Unknown category."
CHUNK = 500


class EditError(Exception):
    """A plain message safe to show. Never holds the typed text."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def check_text(
    field: str,
    values: Iterable[str],
    private: PrivateRules | None,
    section: str | None = None,
) -> None:
    """detect_sensitive plus the private lists. Raises EditError(422) without the value.
    `section` adds that snapshot section's rules (booking-code for rewards)."""
    for v in values:
        if scan_text(v, private, section):
            raise EditError(422, SENSITIVE.format(label=TEXT_LABELS.get(field, "This text")))


def check_changes(
    conn: sqlite3.Connection, changes: Mapping[str, Any], private: PrivateRules | None
) -> None:
    for field in ("merchant", "notes"):
        if changes.get(field) is not None:
            check_text(field, [changes[field]], private)
    if changes.get("tags") is not None:
        check_text("tags", changes["tags"], private)
    if changes.get("category_key") is not None:
        require_category(conn, changes["category_key"])


def require_category(conn: sqlite3.Connection, key: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM categories WHERE category_key = ? AND active = 1", (key,)
    ).fetchone()
    if row is None:
        raise EditError(422, UNKNOWN_CATEGORY)


# --------------------------------------------------------------------------
# Reading effective values
# --------------------------------------------------------------------------


def _chunks(keys: Sequence[str]) -> Iterable[Sequence[str]]:
    for i in range(0, len(keys), CHUNK):
        yield keys[i : i + CHUNK]


def effective(conn: sqlite3.Connection, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Effective edit-field values per live key, as the API shows them."""
    out: dict[str, dict[str, Any]] = {}
    for part in _chunks(keys):
        marks = ", ".join("?" for _ in part)
        for r in conn.execute(
            f"""SELECT txn_key, category_key, merchant, is_transfer, is_reimbursable,
                       exclude_from_budget, hidden, notes, tags_json
                FROM v_transactions WHERE txn_key IN ({marks})""",
            list(part),
        ):
            out[r["txn_key"]] = {
                "category_key": r["category_key"],
                "merchant": r["merchant"],
                **{f: bool(r[f]) for f in FLAG_FIELDS},
                "notes": r["notes"],
                "tags": json.loads(r["tags_json"]) if r["tags_json"] else [],
            }
    return out


def _override_rows(conn: sqlite3.Connection, keys: Sequence[str]) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    for part in _chunks(keys):
        marks = ", ".join("?" for _ in part)
        for r in conn.execute(
            f"SELECT * FROM transaction_overrides WHERE txn_key IN ({marks})", list(part)
        ):
            out[r["txn_key"]] = r
    return out


def _guard_rows(conn: sqlite3.Connection, keys: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in _chunks(keys):
        marks = ", ".join("?" for _ in part)
        for r in conn.execute(
            f"""SELECT txn_key, desc_norm, merchant, amount_cents, txn_date FROM transactions
                WHERE removed_at IS NULL AND txn_key IN ({marks})""",
            list(part),
        ):
            out[r["txn_key"]] = guard_json(r)
    return out


def guard_json(row: Mapping[str, Any]) -> str:
    """The guard stored with an override: the row's imported identity at edit time."""
    return json.dumps(
        {
            "desc_norm": row["desc_norm"],
            "merchant": row["merchant"],
            "amount_cents": row["amount_cents"],
            "txn_date": row["txn_date"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


def _bool_text(v: bool) -> str:
    return "true" if v else "false"


def change_events(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[tuple]:
    """(kind, from_value, to_value) for each effective change between two states."""
    out: list[tuple] = []
    if before["category_key"] != after["category_key"]:
        out.append(("category_changed", before["category_key"], after["category_key"]))
    if before["merchant"] != after["merchant"]:
        out.append(("merchant_changed", None, None))  # typed text is never kept
    for f in FLAG_FIELDS:
        if before[f] != after[f]:
            out.append((FLAG_EVENT[f], _bool_text(before[f]), _bool_text(after[f])))
    if before["notes"] != after["notes"]:
        if before["notes"] is None:
            kind = "note_added"
        elif after["notes"] is None:
            kind = "note_removed"
        else:
            kind = "note_changed"
        out.append((kind, None, None))
    if before["tags"] != after["tags"]:
        out.append(("tags_changed", None, None))  # typed text is never kept
    return out


def log_event(
    conn: sqlite3.Connection,
    txn_key: str,
    at: str,
    kind: str,
    via: str,
    *,
    user_id: int | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    prev_txn_key: str | None = None,
    rule_id: int | None = None,
) -> None:
    conn.execute(
        """INSERT INTO transaction_events
               (txn_key, at, user_id, kind, via, from_value, to_value, prev_txn_key, rule_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (txn_key, at, user_id, kind, via, from_value, to_value, prev_txn_key, rule_id),
    )


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------


def _stored(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in FLAG_FIELDS:
        return int(bool(value))
    if field == "tags":
        return json.dumps(list(value), ensure_ascii=False) if value else None
    return value


@dataclass(frozen=True)
class EditResult:
    updated: int  # rows whose effective values changed
    events: int


def apply_edit(
    conn: sqlite3.Connection,
    keys: Sequence[str],
    changes: Mapping[str, Any],
    *,
    user_id: int,
    via: str,
    now: str,
) -> EditResult:
    """Set (or clear, with None) override fields on live rows. The caller holds the write
    transaction and has run `check_changes`. Raises EditError(404) when a key is not live,
    before anything is written."""
    keys = list(dict.fromkeys(keys))
    before = effective(conn, keys)
    if len(before) != len(keys):
        raise EditError(404, NOT_FOUND)
    old = _override_rows(conn, keys)
    guards = _guard_rows(conn, keys)
    cols = [COLUMNS[f] for f in EDIT_FIELDS]
    for key in keys:
        prev = old.get(key)
        values = {c: (prev[c] if prev is not None else None) for c in cols}
        for field, value in changes.items():
            values[COLUMNS[field]] = _stored(field, value)
        if any(v is not None for v in values.values()):
            conn.execute(
                f"""INSERT INTO transaction_overrides
                        (txn_key, {", ".join(cols)}, guard_json, updated_by, updated_at)
                    VALUES (?, {", ".join("?" for _ in cols)}, ?, ?, ?)
                    ON CONFLICT (txn_key) DO UPDATE SET
                      {", ".join(f"{c} = excluded.{c}" for c in cols)},
                      guard_json = excluded.guard_json,
                      updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                (key, *values.values(), guards[key], user_id, now),
            )
        elif prev is not None:
            conn.execute("DELETE FROM transaction_overrides WHERE txn_key = ?", (key,))
    after = effective(conn, keys)
    updated = events = 0
    for key in keys:
        changed = change_events(before[key], after[key])
        updated += bool(changed)
        for kind, frm, to in changed:
            log_event(conn, key, now, kind, via, user_id=user_id, from_value=frm, to_value=to)
            events += 1
    return EditResult(updated=updated, events=events)


# --------------------------------------------------------------------------
# Import: re-key or orphan (PLAN 4.11 step 6)
# --------------------------------------------------------------------------


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _same_text(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.casefold() == b.casefold()


def rank_candidate(guard: Mapping[str, Any], cand: Mapping[str, Any]) -> tuple | None:
    """How well a live row fits a guard (an edit's or a credit choice's). Higher is
    better. None when neither desc_norm nor merchant matches. `merchant` is the
    imported merchant on both sides."""
    same_desc = guard.get("desc_norm") == cand["desc_norm"]
    same_merchant = _same_text(guard.get("merchant"), cand["merchant"])
    if not (same_desc or same_merchant):
        return None
    return (
        same_desc,
        guard.get("amount_cents") == cand["amount_cents"],
        same_merchant,
        -_days(guard.get("txn_date") or cand["txn_date"], cand["txn_date"]),
    )


def _last_event_kind(conn: sqlite3.Connection, txn_key: str) -> str | None:
    row = conn.execute(
        "SELECT kind FROM transaction_events WHERE txn_key = ? ORDER BY id DESC LIMIT 1",
        (txn_key,),
    ).fetchone()
    return row["kind"] if row else None


def reattach_overrides(
    conn: sqlite3.Connection,
    dropped: Mapping[str, Mapping[str, Any]],
    fresh: Mapping[str, Mapping[str, Any]],
    now: str,
) -> tuple[int, int]:
    """Move or orphan the overrides of rows a statement replace dropped.

    dropped: txn_key -> the old row (desc_norm, merchant, amount_cents, txn_date).
    fresh: txn_key -> the new row's values, for keys that were not live before.
    Returns (moved, orphaned). Runs inside the import transaction.
    """
    if not dropped:
        return 0, 0
    overrides = _override_rows(conn, sorted(dropped))
    if not overrides:
        return 0, 0
    taken = set(_override_rows(conn, sorted(fresh)))
    moved = orphaned = 0
    order = sorted(overrides, key=lambda k: (dropped[k]["txn_date"], k))
    for old_key in order:
        ov = overrides[old_key]
        guard = json.loads(ov["guard_json"]) if ov["guard_json"] else dict(dropped[old_key])
        ranked = sorted(
            (
                (rank, key)
                for key, cand in fresh.items()
                if key not in taken and (rank := rank_candidate(guard, cand)) is not None
            ),
            reverse=True,
        )
        unique = ranked and (len(ranked) == 1 or ranked[0][0] != ranked[1][0])
        if not unique:
            # Logged once, so a replay does not repeat it.
            if _last_event_kind(conn, old_key) != "orphaned":
                log_event(conn, old_key, now, "orphaned", "import")
            orphaned += 1
            continue
        new_key = ranked[0][1]
        taken.add(new_key)
        conn.execute(
            "UPDATE transaction_overrides SET txn_key = ?, guard_json = ? WHERE txn_key = ?",
            (new_key, guard_json(fresh[new_key]), old_key),
        )
        conn.execute(
            "UPDATE transaction_events SET txn_key = ? WHERE txn_key = ?", (new_key, old_key)
        )
        log_event(conn, new_key, now, "rekeyed", "import", prev_txn_key=old_key)
        moved += 1
    return moved, orphaned


# --------------------------------------------------------------------------
# Reads: activity timeline and orphaned edits
# --------------------------------------------------------------------------


def timeline(conn: sqlite3.Connection, txn_key: str) -> list[dict] | None:
    """The row's activity, oldest first, led by a synthesized 'imported' entry.

    None when the key is unknown (not live and never edited).
    """
    row = conn.execute(
        """SELECT t.run_id, r.finished_at FROM transactions t
           LEFT JOIN import_runs r ON r.run_id = t.run_id WHERE t.txn_key = ?""",
        (txn_key,),
    ).fetchone()
    events = conn.execute(
        """SELECT e.*, u.display_name FROM transaction_events e
           LEFT JOIN users u ON u.id = e.user_id WHERE e.txn_key = ? ORDER BY e.id""",
        (txn_key,),
    ).fetchall()
    if row is None and not events:
        return None
    out: list[dict] = []
    if row is not None:
        out.append(
            {
                "id": None,
                "at": row["finished_at"],
                "kind": "imported",
                "via": "import",
                "from_value": None,
                "to_value": None,
                "user": None,
                "rule_id": None,
                "prev_txn_key": None,
                "run_id": row["run_id"],
            }
        )
    for e in events:
        out.append(
            {
                "id": e["id"],
                "at": e["at"],
                "kind": e["kind"],
                "via": e["via"],
                "from_value": e["from_value"],
                "to_value": e["to_value"],
                "user": e["display_name"],
                "rule_id": e["rule_id"],
                "prev_txn_key": e["prev_txn_key"],
                "run_id": None,
            }
        )
    return out


def _override_out(o: sqlite3.Row) -> dict:
    return {
        "category_key": o["category_key"],
        "merchant": o["merchant"],
        **{f: (bool(o[f]) if o[f] is not None else None) for f in FLAG_FIELDS},
        "notes": o["notes"],
        "tags": json.loads(o["tags_json"]) if o["tags_json"] else [],
    }


def orphaned_edits(conn: sqlite3.Connection) -> list[dict]:
    """Overrides whose transaction is not live: "Edits that lost their transaction"."""
    rows = conn.execute(
        """SELECT o.*, t.statement_key, t.account_key, t.removed_at,
                  a.display_name AS account_name, u.display_name AS updated_by_name,
                  (SELECT max(e.at) FROM transaction_events e
                   WHERE e.txn_key = o.txn_key AND e.kind = 'orphaned') AS orphaned_at
           FROM transaction_overrides o
           LEFT JOIN transactions t ON t.txn_key = o.txn_key
           LEFT JOIN accounts a ON a.account_key = t.account_key
           LEFT JOIN users u ON u.id = o.updated_by
           WHERE t.txn_key IS NULL OR t.removed_at IS NOT NULL
           ORDER BY o.updated_at DESC, o.txn_key"""
    ).fetchall()
    out = []
    for o in rows:
        guard = json.loads(o["guard_json"]) if o["guard_json"] else {}
        out.append(
            {
                "txn_key": o["txn_key"],
                "txn_date": guard.get("txn_date"),
                "amount_cents": guard.get("amount_cents"),
                "desc_norm": guard.get("desc_norm"),
                "imported_merchant": guard.get("merchant"),
                "account_key": o["account_key"],
                "account_name": o["account_name"],
                "statement_key": o["statement_key"],
                "removed_at": o["removed_at"],
                "orphaned_at": o["orphaned_at"],
                "override": _override_out(o),
                "updated_at": o["updated_at"],
                "updated_by": o["updated_by_name"],
            }
        )
    return out
