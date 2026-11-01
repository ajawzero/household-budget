"""Transaction rows as the API shows them. Used by /api/transactions and account detail."""

from __future__ import annotations

import sqlite3


def query_rows(
    conn: sqlite3.Connection, where: list[str], params: list[object], limit: int
) -> list[sqlite3.Row]:
    """Live rows from v_transactions, newest first (txn_date, then txn_key)."""
    return conn.execute(
        f"""SELECT t.txn_key, t.txn_date, t.post_date, t.account_key,
                   a.display_name AS account_name, t.description, t.merchant,
                   t.category_key, c.name AS category_name,
                   t.cardholder_person_key, ch.display_name AS cardholder_name,
                   t.amount_cents, t.is_transfer, t.is_reimbursable
            FROM v_transactions AS t
            JOIN accounts AS a ON a.account_key = t.account_key
            LEFT JOIN categories AS c ON c.category_key = t.category_key
            LEFT JOIN people AS ch ON ch.person_key = t.cardholder_person_key
            WHERE {" AND ".join(where)}
            ORDER BY t.txn_date DESC, t.txn_key DESC
            LIMIT ?""",
        (*params, limit),
    ).fetchall()


def row_out(r: sqlite3.Row) -> dict:
    return {
        "txn_key": r["txn_key"],
        "txn_date": r["txn_date"],
        "post_date": r["post_date"],
        "account_key": r["account_key"],
        "account_name": r["account_name"],
        "description": r["description"],
        "merchant": r["merchant"],
        "category_key": r["category_key"],
        "category_name": r["category_name"],
        "cardholder": r["cardholder_person_key"],
        "cardholder_label": r["cardholder_name"] or r["cardholder_person_key"],
        "amount_cents": r["amount_cents"],
        "is_transfer": bool(r["is_transfer"]),
        "is_reimbursable": bool(r["is_reimbursable"]),
    }
