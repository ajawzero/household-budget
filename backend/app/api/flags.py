"""Flags & To do under More (PLAN 8.2): GET /api/flags, /api/todos, /api/pending.

Read-only in v1. The chat owns these lists (PLAN 14.4 item 14).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SIGNED_IN, Db
from app.api.p4_models import FlagsOut, PendingListOut, TodosOut
from app.services.plans import flags_page, pending_page, todos_page

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/flags", response_model=FlagsOut)
def get_flags(conn: Db, include_archived: bool = False) -> dict:
    return flags_page(conn, include_archived)


@router.get("/todos", response_model=TodosOut)
def get_todos(conn: Db) -> dict:
    return todos_page(conn)


@router.get("/pending", response_model=PendingListOut)
def get_pending(conn: Db) -> dict:
    return pending_page(conn)
