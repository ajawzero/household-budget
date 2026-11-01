"""Category suggestions (PLAN 7.1, 7.7).

- Merchant rules apply first (in v_transactions), so only rows still
  uncategorized reach `txn_categorize_safe`. Rows already suggested (pending,
  approved or rejected) are skipped: a rejected suggestion is never repeated.
- Rows are grouped by merchant (or the normalized description when the
  merchant is weak), and sent in chunks of CHUNK merchants to the categorize
  model with a category enum. The key it returns is matched without regard
  to case. "uncategorized" means no suggestion.
- Suggestions go to `category_suggestions`. Nothing changes until a person
  approves one. Approving writes a category override, logged as an edit
  `via 'suggestion'`. The model has no write tool.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pydantic
from pydantic import BaseModel, Field

from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai.postprocess import Scrub, filter_text, load_last4s
from app.ai.pricing import estimate_micro_usd
from app.ai.prompts import CATEGORIZE_RULES
from app.ai.safe import AiConnection, ai_connection, run
from app.ai.tools import usd
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.importer.runner import private_rules_for
from app.services.overrides import EditError, apply_edit, require_category

CHUNK = 50
MAX_TOKENS = 8_000
MAX_CHUNKS = 10  # one run sends at most 500 merchants
NONE_KEY = "uncategorized"


class Suggestion(BaseModel):
    item: int = Field(description="The item number from the input.")
    category_key: str = Field(description="One key from the category list.")
    confidence: int = Field(description="0 to 100.")
    reason: str = Field(description="A few plain words.")


class SuggestionsOut(BaseModel):
    suggestions: list[Suggestion]


def output_format(keys: list[str]) -> dict:
    """SuggestionsOut's JSON schema with category_key limited to `keys`, the enum.
    The reply is still parsed as a plain string and matched without case."""
    fmt = ai_client.json_format(SuggestionsOut)
    fmt["schema"]["$defs"]["Suggestion"]["properties"]["category_key"]["enum"] = keys
    return fmt


def _iso() -> str:
    return ai_settings.iso(datetime.now(UTC))


def pending_items(conn: AiConnection, suggested: set[str]) -> list[dict]:
    """Merchants of uncategorized rows with no suggestion yet, newest first."""
    rows = run(
        conn,
        """SELECT txn_key, txn_date, amount_cents, txn_type, account_name, merchant, desc_norm
           FROM txn_categorize_safe ORDER BY txn_date DESC, txn_key""",
    )
    items: dict[str, dict] = {}
    for r in rows:
        if r["txn_key"] in suggested:
            continue
        label = (r["merchant"] or "").strip() or (r["desc_norm"] or "").strip()
        if not label:
            continue
        key = label.casefold()
        item = items.get(key)
        if item is None:
            item = items[key] = {
                "label": label,
                "example_amount": usd(r["amount_cents"]),
                "type": r["txn_type"],
                "account": r["account_name"],
                "txn_keys": [],
            }
        item["txn_keys"].append(r["txn_key"])
    return list(items.values())


def category_options(conn: AiConnection) -> list[dict]:
    return [
        {"key": r["category_key"], "name": r["name"], "group": r["group_name"]}
        for r in run(
            conn,
            """SELECT category_key, name, group_name FROM categories_safe
               WHERE active = 1 AND kind IN ('spending', 'income', 'reimbursement')
               ORDER BY group_name, name""",
        )
    ]


def _store(db_path: Any, rows: list[tuple], model: str) -> int:
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            n = 0
            for txn_key, key, confidence, reason in rows:
                cur = conn.execute(
                    """INSERT INTO category_suggestions (txn_key, suggested_category_key,
                           confidence, reason, model, status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?)
                       ON CONFLICT (txn_key) DO NOTHING""",
                    (txn_key, key, confidence, reason, model, _iso()),
                )
                n += cur.rowcount
            return n
    finally:
        conn.close()


async def suggest(settings: Settings, user_id: int | None) -> dict:
    """Ask the categorize model about the uncategorized merchants. Returns counts."""
    db_path = settings.db_path
    conn = connect(db_path)
    try:
        st = ai_settings.status(conn, settings)
        if not st["available"]:
            raise ai_client.AiError(ai_client.unavailable_text(conn, st["reason"]), "unavailable")
        s = ai_settings.load(conn)
        suggested = {r[0] for r in conn.execute("SELECT txn_key FROM category_suggestions")}
    finally:
        conn.close()

    private = private_rules_for(settings)

    def _read() -> tuple[list[dict], list[dict], Scrub]:
        with ai_connection(db_path) as ai:
            found = pending_items(ai, suggested), category_options(ai)
        return *found, Scrub(private, load_last4s(db_path))

    items, options, scrub = await asyncio.to_thread(_read)
    if not items:
        return {"merchants": 0, "suggested": 0}
    keys = {o["key"].casefold(): o["key"] for o in options}
    fmt = output_format([o["key"] for o in options] + [NONE_KEY])
    model = s.categorize_model
    effort = ai_settings.EFFORT["categorize"]
    stored = 0
    sent = 0
    for start in range(0, min(len(items), CHUNK * MAX_CHUNKS), CHUNK):
        chunk = items[start : start + CHUNK]
        payload = scrub.json(
            {
                "categories": options + [{"key": NONE_KEY, "name": "None fits", "group": "-"}],
                "items": [
                    {
                        "item": i,
                        "merchant": it["label"],
                        "example_amount": it["example_amount"],
                        "type": it["type"],
                        "account": it["account"],
                    }
                    for i, it in enumerate(chunk)
                ],
            }
        )

        async def request(client: Any, payload: dict = payload) -> Any:
            return await client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=CATEGORIZE_RULES,
                messages=[{"role": "user", "content": json.dumps(payload, sort_keys=True)}],
                output_config={"effort": effort, "format": fmt},
            )

        estimate = estimate_micro_usd(model, len(json.dumps(payload)) // 3 + 2_000, MAX_TOKENS)
        info = ai_client.CallInfo("categorize", model, effort, user_id, estimate_micro=estimate)
        try:
            message = await ai_client.call(settings, info, request)
        except pydantic.ValidationError, ValueError:
            continue  # a chunk whose output broke the schema is dropped
        sent += len(chunk)
        out = ai_client.parse_json(message, SuggestionsOut)
        if message.stop_reason != "end_turn" or out is None:
            continue
        rows = []
        for sug in out.suggestions:
            if not 0 <= sug.item < len(chunk):
                continue
            key = keys.get(sug.category_key.strip().casefold())
            if key is None or key == NONE_KEY:
                continue
            confidence = max(0, min(100, sug.confidence))
            reason = filter_text(sug.reason, private)[:120] or None
            rows += [(t, key, confidence, reason) for t in chunk[sug.item]["txn_keys"]]
        stored += await asyncio.to_thread(_store, db_path, rows, model)
    return {"merchants": sent, "suggested": stored}


# --------------------------------------------------------------------------
# Listing, approving, rejecting
# --------------------------------------------------------------------------


def listing(conn: sqlite3.Connection) -> list[dict]:
    """Pending suggestions whose rows are still uncategorized and visible.

    For people, not the model: rows carry the statement text the Transactions
    page shows, for rows with no merchant."""
    rows = conn.execute(
        """SELECT s.id, s.txn_key, s.suggested_category_key, s.confidence, s.reason, s.model,
                  s.created_at, t.txn_date, t.amount_cents, t.merchant, t.account_name,
                  v.description, c.name AS category_name, c.emoji AS category_emoji
           FROM category_suggestions AS s
           JOIN transactions_safe AS t ON t.txn_key = s.txn_key
           JOIN v_transactions AS v ON v.txn_key = s.txn_key
           JOIN categories AS c ON c.category_key = s.suggested_category_key
           WHERE s.status = 'pending' AND t.category_kind = 'uncategorized'
           ORDER BY t.txn_date DESC, s.id"""
    ).fetchall()
    return [
        {
            "id": r["id"],
            "txn_key": r["txn_key"],
            "txn_date": r["txn_date"],
            "amount_cents": r["amount_cents"],
            "merchant": r["merchant"],
            "description": r["description"],
            "account_name": r["account_name"],
            "category_key": r["suggested_category_key"],
            "category_name": r["category_name"],
            "category_emoji": r["category_emoji"],
            "confidence": r["confidence"],
            "reason": r["reason"],
            "model": r["model"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


STALE_TEXT = "That transaction changed since the suggestion. Refresh the list."


class DecideError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def decide(conn: sqlite3.Connection, ids: list[int], approve: bool, user_id: int) -> int:
    """Approve (writes the category override) or reject pending suggestions, all or none.
    The caller holds no transaction. Returns how many changed."""
    ids = list(dict.fromkeys(ids))
    now = _iso()
    with write_transaction(conn):
        rows = conn.execute(
            f"""SELECT id, txn_key, suggested_category_key, status FROM category_suggestions
                WHERE id IN ({", ".join("?" for _ in ids)})""",
            ids,
        ).fetchall()
        if len(rows) != len(ids):
            raise DecideError(404, "Suggestion not found.")
        if any(r["status"] != "pending" for r in rows):
            raise DecideError(409, "That suggestion was already decided.")
        if approve:
            # The row must still be visible and uncategorized: a later edit or rule wins.
            keys = [r["txn_key"] for r in rows]
            still = {
                k
                for (k,) in conn.execute(
                    f"""SELECT v.txn_key FROM v_transactions AS v
                        JOIN categories AS c ON c.category_key = v.category_key
                        WHERE v.txn_key IN ({", ".join("?" for _ in keys)})
                          AND v.hidden = 0 AND c.kind = 'uncategorized'""",
                    keys,
                )
            }
            if still != set(keys):
                raise DecideError(409, STALE_TEXT)
            for r in rows:
                try:
                    require_category(conn, r["suggested_category_key"])
                    apply_edit(
                        conn,
                        [r["txn_key"]],
                        {"category_key": r["suggested_category_key"]},
                        user_id=user_id,
                        via="suggestion",
                        now=now,
                    )
                except EditError as exc:
                    raise DecideError(exc.status, exc.message) from None
        conn.executemany(
            """UPDATE category_suggestions SET status = ?, decided_by = ?, decided_at = ?
               WHERE id = ?""",
            [("approved" if approve else "rejected", user_id, now, r["id"]) for r in rows],
        )
    return len(rows)
