"""Transactions (PLAN 3.4, 5.5, 5.6, 8.2).

- GET /api/transactions: live rows, URL filters, a date cursor (Phase 2 shape).
- POST /api/transactions/search: every filter in the body, free text included,
  so search text never lands in a URL or a log. Rows carry override markers.
- GET /api/transactions/summary: the summary panel for URL filters (no text).
- PATCH /api/transactions/{txn_key}, POST /api/transactions/bulk: overrides.
  Admin and member. Text goes through detect_sensitive and the private lists;
  a match is a 422 that never echoes the text.
- GET /api/transactions/{txn_key}/events: the activity timeline.
- GET /api/transactions/orphaned-edits: edits whose row is gone.

`owner` matches the cardholder when the row has one, else the account owner.
Hidden rows are left out unless a filter asks for them.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.api.deps import SIGNED_IN, CurrentUser, Db, app_settings
from app.api.models import TransactionsOut
from app.api.txn_models import (
    TXN_KEY_RE,
    OrphanedEditsOut,
    TxnBulkIn,
    TxnBulkOut,
    TxnEventsOut,
    TxnFilters,
    TxnPatchIn,
    TxnRowOut,
    TxnSearchIn,
    TxnSearchOut,
    TxnSummaryOut,
)
from app.auth.clock import iso
from app.auth.clock import now as clock_now
from app.db.connect import write_transaction
from app.importer.runner import private_rules_for
from app.services import txn_search
from app.services.overrides import (
    EditError,
    apply_edit,
    check_changes,
    orphaned_edits,
    timeline,
)
from app.services.transactions import query_rows, row_out

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
KEY_QUERY = r"^[a-z][a-z0-9_-]{0,63}$"
MONTH_QUERY = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
BAD_CURSOR = "Invalid cursor."


def encode_cursor(txn_date: str, txn_key: str) -> str:
    raw = json.dumps([txn_date, txn_key], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=BAD_CURSOR) from exc
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, str) for v in value)
        or not _DATE.match(value[0])
    ):
        raise HTTPException(status_code=400, detail=BAD_CURSOR)
    return value[0], value[1]


def _next_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:])
    return f"{year + 1:04d}-01" if mon == 12 else f"{year:04d}-{mon + 1:02d}"


@router.get("/transactions", response_model=TransactionsOut)
def get_transactions(
    conn: Db,
    account: Annotated[str | None, Query(pattern=KEY_QUERY)] = None,
    category: Annotated[str | None, Query(pattern=KEY_QUERY)] = None,
    month: Annotated[str | None, Query(pattern=MONTH_QUERY)] = None,
    owner: Annotated[str | None, Query(pattern=KEY_QUERY)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> dict:
    where = ["t.hidden = 0"]
    params: list[object] = []
    if account:
        where.append("t.account_key = ?")
        params.append(account)
    if category:
        where.append("t.category_key = ?")
        params.append(category)
    if month:
        where.append("t.txn_date >= ? AND t.txn_date < ?")
        params += [f"{month}-01", f"{_next_month(month)}-01"]
    if owner:
        where.append("COALESCE(t.cardholder_person_key, a.owner_person_key) = ?")
        params.append(owner)
    if cursor:
        c_date, c_key = decode_cursor(cursor)
        where.append("(t.txn_date < ? OR (t.txn_date = ? AND t.txn_key < ?))")
        params += [c_date, c_date, c_key]
    rows = query_rows(conn, where, params, limit + 1)
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1]["txn_date"], page[-1]["txn_key"]) if len(rows) > limit else None
    )
    return {
        "rows": [row_out(r) for r in page],
        "next_cursor": next_cursor,
    }


# --------------------------------------------------------------------------
# Phase 4: search, summary, edits, activity
# --------------------------------------------------------------------------

TxnKeyPath = Annotated[str, Path(pattern=TXN_KEY_RE)]
NO_CHANGES = "Nothing to change."


def _http(exc: EditError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/transactions/search", response_model=TxnSearchOut)
def search_transactions(conn: Db, body: TxnSearchIn) -> dict:
    try:
        return txn_search.search(
            conn,
            body,
            q=body.q,
            sort=body.sort,
            cursor=body.cursor,
            limit=body.limit,
            include_summary=body.include_summary,
        )
    except txn_search.CursorError as exc:
        raise HTTPException(status_code=400, detail=BAD_CURSOR) from exc


@router.get("/transactions/summary", response_model=TxnSummaryOut)
def transactions_summary(conn: Db, filters: Annotated[TxnFilters, Query()]) -> dict:
    where, params = txn_search.build_where(filters)
    return txn_search.summary(conn, where, params)


@router.get("/transactions/orphaned-edits", response_model=OrphanedEditsOut)
def get_orphaned_edits(conn: Db) -> dict:
    return {"rows": orphaned_edits(conn)}


def _edit(
    request: Request, conn: Db, user: CurrentUser, keys: list[str], body: TxnPatchIn, via: str
) -> int:
    changes = body.changes()
    if not changes:
        raise HTTPException(status_code=422, detail=NO_CHANGES)
    try:
        check_changes(conn, changes, private_rules_for(app_settings(request)))
        with write_transaction(conn):
            result = apply_edit(conn, keys, changes, user_id=user.id, via=via, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return result.updated


@router.patch("/transactions/{txn_key}", response_model=TxnRowOut)
def patch_transaction(
    request: Request, conn: Db, user: CurrentUser, txn_key: TxnKeyPath, body: TxnPatchIn
) -> dict:
    _edit(request, conn, user, [txn_key], body, "edit")
    row = txn_search.one_row(conn, txn_key)
    if row is None:  # pragma: no cover - apply_edit already checked
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return row


@router.post("/transactions/bulk", response_model=TxnBulkOut)
def bulk_edit(request: Request, conn: Db, user: CurrentUser, body: TxnBulkIn) -> dict:
    keys = list(dict.fromkeys(body.txn_keys))
    updated = _edit(request, conn, user, keys, body.changes, "bulk")
    return {"count": len(keys), "updated": updated}


@router.get("/transactions/{txn_key}/events", response_model=TxnEventsOut)
def transaction_events(conn: Db, txn_key: TxnKeyPath) -> dict:
    events = timeline(conn, txn_key)
    if events is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return {"txn_key": txn_key, "events": events}
