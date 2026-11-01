"""Prices and symbol overrides (PLAN 6.2, 8.2).

GET /api/prices/status   admin and member: key set or not (never the key),
                         last run, per-symbol newest close and source, error
                         codes (never values), and the 1% sanity check
GET /api/symbols         admin and member: every holding with its price mode
PUT /api/symbols         admin only: set or remove one override
PUT /api/symbols/class   admin only: set or remove one class pick
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import SIGNED_IN, Db, app_settings, require_admin
from app.api.market_models import ClassIn, PricesStatusOut, SymbolIn, SymbolRow, SymbolsOut
from app.auth.clock import iso
from app.auth.clock import now as clock_now
from app.auth.users import User
from app.config import household_today
from app.db.connect import write_transaction
from app.importer.runner import private_rules_for
from app.services import prices
from app.services.overrides import EditError

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


def _key_set(request: Request) -> bool:
    return app_settings(request).tiingo_api_key is not None


@router.get("/prices/status", response_model=PricesStatusOut)
def get_status(request: Request, conn: Db) -> dict:
    return prices.status(conn, _key_set(request), household_today())


@router.get("/symbols", response_model=SymbolsOut)
def get_symbols(request: Request, conn: Db) -> dict:
    return {"tiingo_key_set": _key_set(request), "rows": prices.symbol_rows(conn)}


@router.put("/symbols", response_model=SymbolRow)
def put_symbol(
    request: Request,
    conn: Db,
    body: SymbolIn,
    admin: Annotated[User, Depends(require_admin)],
) -> dict:
    try:
        with write_transaction(conn):
            prices.set_override(
                conn,
                account_key=body.account_key,
                holding_key=body.holding_key,
                price_mode=body.price_mode,
                symbol=body.symbol,
                note=body.note,
                user_id=admin.id,
                now=iso(clock_now()),
                private=private_rules_for(app_settings(request)),
            )
    except EditError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    return _row(conn, body.account_key, body.holding_key)


@router.put("/symbols/class", response_model=SymbolRow)
def put_class(
    conn: Db,
    body: ClassIn,
    admin: Annotated[User, Depends(require_admin)],
) -> dict:
    try:
        with write_transaction(conn):
            prices.set_class(
                conn,
                account_key=body.account_key,
                holding_key=body.holding_key,
                asset_class=body.asset_class,
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    return _row(conn, body.account_key, body.holding_key)


def _row(conn: sqlite3.Connection, account_key: str, holding_key: str) -> dict:
    return next(
        r
        for r in prices.symbol_rows(conn)
        if r["account_key"] == account_key and r["holding_key"] == holding_key
    )
