"""Merchant rules (PLAN 3.4): "merchant equals" or "description contains" -> category.

Rules apply at read time in v_transactions and are never written into
`transactions`. Precedence: override > rule > imported value. Among rules,
"merchant equals" wins over "description contains", then the longer pattern,
then the newest rule. Matching ignores ASCII case.

Creating, changing or removing a rule logs a `category_changed` event (via
'rule') on every live row whose shown category it changed.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.services.overrides import EditError, log_event

MATCH_KINDS = ("merchant_equals", "description_contains")
MIN_CONTAINS = 3
_WS = re.compile(r"\s+")
DUPLICATE = "A rule with this match already exists."
NOT_FOUND = "Rule not found."
TOO_SHORT = "A description rule needs at least 3 characters."


def clean_pattern(kind: str, pattern: str) -> str:
    p = _WS.sub(" ", pattern).strip()
    if kind == "description_contains" and len(p) < MIN_CONTAINS:
        raise EditError(422, TOO_SHORT)
    if not p:
        raise EditError(422, "The match text is empty.")
    return p


def _match_sql(kind: str) -> str:
    if kind == "merchant_equals":
        return "upper(merchant) = upper(?)"
    return "instr(upper(description), upper(?)) > 0"


def categories_of(conn: sqlite3.Connection, matches: list[tuple[str, str]]) -> dict[str, str]:
    """txn_key -> shown category, for live rows matching any (kind, pattern)."""
    if not matches:
        return {}
    where = " OR ".join(f"({_match_sql(k)})" for k, _ in matches)
    rows = conn.execute(
        f"SELECT txn_key, category_key FROM v_transactions WHERE {where}",
        [p for _, p in matches],
    )
    return {r["txn_key"]: r["category_key"] for r in rows}


def log_effects(
    conn: sqlite3.Connection,
    before: dict[str, str],
    matches: list[tuple[str, str]],
    *,
    rule_id: int,
    user_id: int,
    now: str,
) -> int:
    """Log category changes between `before` and now, for rows the rule's matches cover."""
    after = categories_of(conn, matches)
    n = 0
    for key in sorted(before.keys() | after.keys()):
        if key in before and key in after and before[key] != after[key]:
            log_event(
                conn,
                key,
                now,
                "category_changed",
                "rule",
                user_id=user_id,
                from_value=before[key],
                to_value=after[key],
                rule_id=rule_id,
            )
            n += 1
    return n


def get_rule(conn: sqlite3.Connection, rule_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM merchant_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise EditError(404, NOT_FOUND)
    return row


def _duplicate(conn: sqlite3.Connection, kind: str, pattern: str, skip: int | None) -> bool:
    row = conn.execute(
        "SELECT id FROM merchant_rules WHERE match_kind = ? AND upper(pattern) = upper(?)",
        (kind, pattern),
    ).fetchone()
    return row is not None and row["id"] != skip


def preview(conn: sqlite3.Connection, kind: str, pattern: str, category: str | None) -> dict:
    """How many live rows a rule would match, and how many it would change."""
    rows = conn.execute(
        f"""SELECT category_key, category_source FROM v_transactions
            WHERE {_match_sql(kind)}""",
        (pattern,),
    ).fetchall()
    free = [r for r in rows if r["category_source"] != "override"]
    return {
        "matches": len(rows),
        "edited": len(rows) - len(free),
        "would_change": sum(1 for r in free if category and r["category_key"] != category),
    }


def create_rule(
    conn: sqlite3.Connection,
    *,
    kind: str,
    pattern: str,
    category_key: str,
    from_txn_key: str | None,
    user_id: int,
    now: str,
) -> int:
    if _duplicate(conn, kind, pattern, None):
        raise EditError(409, DUPLICATE)
    if from_txn_key is not None:
        live = conn.execute(
            "SELECT 1 FROM v_transactions WHERE txn_key = ?", (from_txn_key,)
        ).fetchone()
        if live is None:
            raise EditError(404, "Transaction not found.")
    matches = [(kind, pattern)]
    before = categories_of(conn, matches)
    cur = conn.execute(
        """INSERT INTO merchant_rules (match_kind, pattern, category_key, created_from_txn_key,
               created_by, created_at, updated_by, updated_at, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (kind, pattern, category_key, from_txn_key, user_id, now, user_id, now),
    )
    rule_id = int(cur.lastrowid or 0)
    log_effects(conn, before, matches, rule_id=rule_id, user_id=user_id, now=now)
    return rule_id


def update_rule(
    conn: sqlite3.Connection,
    rule_id: int,
    changes: dict[str, Any],
    *,
    user_id: int,
    now: str,
) -> None:
    old = get_rule(conn, rule_id)
    kind = changes.get("match_kind", old["match_kind"])
    pattern = changes.get("pattern", old["pattern"])
    if ("match_kind" in changes or "pattern" in changes) and _duplicate(
        conn, kind, pattern, rule_id
    ):
        raise EditError(409, DUPLICATE)
    matches = [(old["match_kind"], old["pattern"]), (kind, pattern)]
    before = categories_of(conn, matches)
    values = {
        "match_kind": kind,
        "pattern": pattern,
        "category_key": changes.get("category_key", old["category_key"]),
        "active": int(changes.get("active", bool(old["active"]))),
    }
    conn.execute(
        f"""UPDATE merchant_rules SET {", ".join(f"{c} = ?" for c in values)},
                updated_by = ?, updated_at = ? WHERE id = ?""",
        (*values.values(), user_id, now, rule_id),
    )
    log_effects(conn, before, matches, rule_id=rule_id, user_id=user_id, now=now)


def delete_rule(conn: sqlite3.Connection, rule_id: int, *, user_id: int, now: str) -> None:
    old = get_rule(conn, rule_id)
    matches = [(old["match_kind"], old["pattern"])]
    before = categories_of(conn, matches)
    conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))
    log_effects(conn, before, matches, rule_id=rule_id, user_id=user_id, now=now)


def list_rules(conn: sqlite3.Connection) -> list[dict]:
    """Every rule with the number of live rows it decides now."""
    counts = dict(
        conn.execute(
            """SELECT rule_id, count(*) FROM v_transactions
               WHERE rule_id IS NOT NULL GROUP BY rule_id"""
        ).fetchall()
    )
    out = []
    for r in conn.execute(
        """SELECT m.*, c.name AS category_name, c.emoji AS category_emoji,
                  u.display_name AS created_by_name
           FROM merchant_rules m
           LEFT JOIN categories c ON c.category_key = m.category_key
           LEFT JOIN users u ON u.id = m.created_by
           ORDER BY m.match_kind = 'merchant_equals' DESC, upper(m.pattern), m.id"""
    ):
        out.append(
            {
                "id": r["id"],
                "match_kind": r["match_kind"],
                "pattern": r["pattern"],
                "category_key": r["category_key"],
                "category_name": r["category_name"],
                "category_emoji": r["category_emoji"],
                "active": bool(r["active"]),
                "applied_count": int(counts.get(r["id"], 0)),
                "created_from_txn_key": r["created_from_txn_key"],
                "created_by": r["created_by_name"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return out
