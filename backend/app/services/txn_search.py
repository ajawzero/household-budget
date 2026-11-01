"""Transaction search, summary and day totals (PLAN 8.2 Transactions).

Every query reads v_transactions, so overrides and rules are already applied.
Filters become one WHERE clause shared by the page, the day totals and the
summary. Search text is a bound parameter only. It is never logged.
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from typing import Any

from app.api.txn_models import TxnFilters
from app.services.overrides import COLUMNS, EDIT_FIELDS

BAD_CURSOR = "Invalid cursor."

FROM = """FROM v_transactions AS t
    JOIN accounts AS a ON a.account_key = t.account_key
    LEFT JOIN categories AS c ON c.category_key = t.category_key
    LEFT JOIN people AS ch ON ch.person_key = t.cardholder_person_key
    LEFT JOIN people AS ow ON ow.person_key = COALESCE(t.cardholder_person_key,
                                                      a.owner_person_key)"""

# sort -> (ORDER BY, the cursor's sort value expression, direction)
SORTS: dict[str, tuple[str, str, str]] = {
    "date_desc": ("t.txn_date DESC, t.txn_key DESC", "t.txn_date", "<"),
    "date_asc": ("t.txn_date ASC, t.txn_key ASC", "t.txn_date", ">"),
    "amount_desc": ("abs(t.amount_cents) DESC, t.txn_key DESC", "abs(t.amount_cents)", "<"),
    "amount_asc": ("abs(t.amount_cents) ASC, t.txn_key ASC", "abs(t.amount_cents)", ">"),
}


class CursorError(ValueError):
    pass


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _in(column: str, values: list[str], where: list[str], params: list[Any]) -> None:
    if values:
        where.append(f"{column} IN ({', '.join('?' for _ in values)})")
        params.extend(values)


def _flag(column: str, value: bool | None, where: list[str]) -> None:
    if value is not None:
        where.append(f"COALESCE({column}, 0) = {int(value)}")


def build_where(f: TxnFilters, q: str | None = None) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if f.hidden == "exclude":
        where.append("t.hidden = 0")
    elif f.hidden == "only":
        where.append("t.hidden = 1")
    if f.date_from:
        where.append("t.txn_date >= ?")
        params.append(f.date_from.isoformat())
    if f.date_to:
        where.append("t.txn_date <= ?")
        params.append(f.date_to.isoformat())
    _in("substr(t.txn_date, 1, 7)", f.months, where, params)
    _in("t.account_key", f.accounts, where, params)
    _in("t.category_key", f.categories, where, params)
    _in("c.group_key", f.category_groups, where, params)
    _in("COALESCE(t.cardholder_person_key, a.owner_person_key)", f.owners, where, params)
    if f.amount_min_cents is not None:
        where.append("abs(t.amount_cents) >= ?")
        params.append(f.amount_min_cents)
    if f.amount_max_cents is not None:
        where.append("abs(t.amount_cents) <= ?")
        params.append(f.amount_max_cents)
    if f.direction == "out":
        where.append("t.amount_cents < 0")
    elif f.direction == "in":
        where.append("t.amount_cents > 0")
    _flag("t.is_transfer", f.is_transfer, where)
    _flag("t.is_reimbursable", f.is_reimbursable, where)
    _flag("t.needs_review", f.needs_review, where)
    if f.uncategorized is not None:
        op = "=" if f.uncategorized else "IS NOT"
        where.append(f"c.kind {op} 'uncategorized'")
    if f.edited is not None:
        where.append(f"t.has_override = {int(f.edited)}")
    if f.recurring_key:
        where.append("t.recurring_key = ?")
        params.append(f.recurring_key)
    if f.plan_key:
        where.append("t.plan_key = ?")
        params.append(f.plan_key)
    if f.trip_key:
        where.append("t.trip_key = ?")
        params.append(f.trip_key)
    text = (q or "").strip()
    if text:
        like = _like(text)
        where.append(
            """(t.description LIKE ? ESCAPE '\\' OR t.merchant LIKE ? ESCAPE '\\'
                OR t.imported_merchant LIKE ? ESCAPE '\\' OR t.notes LIKE ? ESCAPE '\\'
                OR t.tags_json LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')"""
        )
        params.extend([like] * 6)
    return where or ["1 = 1"], params


# --------------------------------------------------------------------------
# Cursor
# --------------------------------------------------------------------------


def encode_cursor(sort: str, value: str | int, txn_key: str) -> str:
    raw = json.dumps([sort, value, txn_key], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, sort: str) -> tuple[str | int, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CursorError(BAD_CURSOR) from exc
    if not isinstance(value, list) or len(value) != 3 or value[0] != sort:
        raise CursorError(BAD_CURSOR)
    _, v, key = value
    want = str if sort.startswith("date") else int
    if type(v) is not want or not isinstance(key, str):
        raise CursorError(BAD_CURSOR)
    return v, key


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

ROW_COLUMNS = """t.*, a.display_name AS account_name, a.institution,
    c.name AS category_name, c.emoji AS category_emoji, c.group_key AS category_group,
    c.kind AS category_kind, ch.display_name AS cardholder_name,
    COALESCE(t.cardholder_person_key, a.owner_person_key) AS owner_key,
    ow.display_name AS owner_name,
    o.category_key AS o_category_key, o.merchant AS o_merchant, o.is_transfer AS o_is_transfer,
    o.is_reimbursable AS o_is_reimbursable, o.exclude_from_budget AS o_exclude_from_budget,
    o.hidden AS o_hidden, o.notes AS o_notes, o.tags_json AS o_tags_json,
    tp.name AS trip_name"""


def row_out(r: sqlite3.Row) -> dict:
    overridden = [f for f in EDIT_FIELDS if r[f"o_{COLUMNS[f]}"] is not None]
    return {
        "txn_key": r["txn_key"],
        "txn_date": r["txn_date"],
        "post_date": r["post_date"],
        "account_key": r["account_key"],
        "account_name": r["account_name"],
        "institution": r["institution"],
        "description": r["description"],
        "merchant": r["merchant"],
        "imported_merchant": r["imported_merchant"],
        "category_key": r["category_key"],
        "category_name": r["category_name"],
        "category_emoji": r["category_emoji"],
        "category_group": r["category_group"],
        "category_kind": r["category_kind"],
        "category_source": r["category_source"],
        "rule_id": r["rule_id"],
        "imported_category_key": r["imported_category_key"],
        "cardholder": r["cardholder_person_key"],
        "cardholder_label": r["cardholder_name"] or r["cardholder_person_key"],
        "owner": r["owner_key"],
        "owner_label": r["owner_name"] or r["owner_key"],
        "amount_cents": r["amount_cents"],
        "txn_type": r["txn_type"],
        "is_transfer": bool(r["is_transfer"]),
        "is_reimbursable": bool(r["is_reimbursable"]),
        "exclude_from_budget": bool(r["exclude_from_budget"]),
        "hidden": bool(r["hidden"]),
        "needs_review": bool(r["needs_review"]),
        "pre_history": bool(r["pre_history"]),
        "plan_key": r["plan_key"],
        "recurring_key": r["recurring_key"],
        "trip_key": r["trip_key"],
        "trip_name": r["trip_name"],
        "import_notes": r["import_notes"],
        "notes": r["notes"],
        "tags": json.loads(r["tags_json"]) if r["tags_json"] else [],
        "has_override": bool(r["has_override"]),
        "overridden": overridden,
    }


def fetch_rows(
    conn: sqlite3.Connection,
    where: list[str],
    params: list[Any],
    order: str = "t.txn_date DESC, t.txn_key DESC",
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = f"""SELECT {ROW_COLUMNS} {FROM}
              LEFT JOIN transaction_overrides AS o ON o.txn_key = t.txn_key
              LEFT JOIN trips AS tp ON tp.trip_key = t.trip_key
              WHERE {" AND ".join(where)} ORDER BY {order}"""
    if limit is not None:
        sql += " LIMIT ?"
        params = [*params, limit]
    return conn.execute(sql, params).fetchall()


def one_row(conn: sqlite3.Connection, txn_key: str) -> dict | None:
    rows = fetch_rows(conn, ["t.txn_key = ?"], [txn_key])
    return row_out(rows[0]) if rows else None


def search(
    conn: sqlite3.Connection,
    f: TxnFilters,
    *,
    q: str | None,
    sort: str,
    cursor: str | None,
    limit: int,
    include_summary: bool,
) -> dict:
    where, params = build_where(f, q)
    order, sort_expr, op = SORTS[sort]
    page_where, page_params = list(where), list(params)
    if cursor:
        value, key = decode_cursor(cursor, sort)
        page_where.append(f"({sort_expr} {op} ? OR ({sort_expr} = ? AND t.txn_key {op} ?))")
        page_params += [value, value, key]
    rows = fetch_rows(conn, page_where, page_params, order, limit + 1)
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        value = last["txn_date"] if sort.startswith("date") else abs(last["amount_cents"])
        next_cursor = encode_cursor(sort, value, last["txn_key"])
    return {
        "rows": [row_out(r) for r in page],
        "next_cursor": next_cursor,
        "days": day_totals(conn, where, params, sorted({r["txn_date"] for r in page})),
        "summary": summary(conn, where, params) if include_summary and not cursor else None,
    }


def day_totals(
    conn: sqlite3.Connection, where: list[str], params: list[Any], dates: list[str]
) -> list[dict]:
    if not dates:
        return []
    marks = ", ".join("?" for _ in dates)
    rows = conn.execute(
        f"""SELECT t.txn_date, sum(t.amount_cents) AS total, count(*) AS n {FROM}
            WHERE {" AND ".join(where)} AND t.txn_date IN ({marks})
            GROUP BY t.txn_date ORDER BY t.txn_date""",
        [*params, *dates],
    ).fetchall()
    return [{"date": r["txn_date"], "total_cents": r["total"], "count": r["n"]} for r in rows]


def summary(conn: sqlite3.Connection, where: list[str], params: list[Any]) -> dict:
    w = " AND ".join(where)
    r = conn.execute(
        f"""SELECT count(*) AS n, COALESCE(sum(t.amount_cents), 0) AS total,
                   avg(t.amount_cents) AS mean,
                   COALESCE(sum(CASE WHEN c.kind = 'income' AND COALESCE(t.is_transfer, 0) = 0
                                     THEN t.amount_cents END), 0) AS income,
                   COALESCE(sum(CASE WHEN c.kind = 'spending' AND COALESCE(t.is_transfer, 0) = 0
                                     THEN -t.amount_cents END), 0) AS spending,
                   min(t.txn_date) AS first, max(t.txn_date) AS last
            {FROM} WHERE {w}""",
        params,
    ).fetchone()
    big = conn.execute(
        f"""SELECT t.txn_key, t.txn_date, t.merchant, t.description, t.amount_cents {FROM}
            WHERE {w} AND COALESCE(t.is_transfer, 0) = 0
              AND COALESCE(c.kind, '') <> 'transfer'
            ORDER BY abs(t.amount_cents) DESC, t.txn_date DESC, t.txn_key DESC LIMIT 1""",
        params,
    ).fetchone()
    return {
        "count": r["n"],
        "total_cents": r["total"],
        "income_cents": r["income"],
        "spending_cents": r["spending"],
        "average_cents": round(r["mean"]) if r["mean"] is not None else None,
        "largest": dict(big) if big else None,
        "first_date": r["first"],
        "last_date": r["last"],
    }
