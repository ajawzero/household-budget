"""The Budget page (PLAN 8.2 Budget, docs/plans/budget-targets.md).

GET /api/budgets?month=   any signed-in user. Default: the latest complete month.
                          can_edit and edit_note follow the user's role.
PUT /api/budgets/targets  admin only: change group targets for a month, and with
                          also_later the later months the chat planned. All
                          changes save or none do. Returns the page for the month.
                          Demo is refused by the demo middleware.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import SIGNED_IN, CurrentUser, Db, require_admin
from app.api.p4_models import BudgetsOut, BudgetTargetsIn
from app.api.transactions import MONTH_QUERY
from app.auth.clock import iso
from app.auth.clock import now as clock_now
from app.auth.users import User
from app.db.connect import write_transaction
from app.services import budget_targets as bt
from app.services.budgets import budgets_page
from app.services.overrides import EditError

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/budgets", response_model=BudgetsOut)
def get_budgets(
    conn: Db, user: CurrentUser, month: Annotated[str | None, Query(pattern=MONTH_QUERY)] = None
) -> dict:
    return budgets_page(conn, month, is_admin=user.is_admin)


@router.put("/budgets/targets", response_model=BudgetsOut)
def put_targets(
    conn: Db, body: BudgetTargetsIn, admin: Annotated[User, Depends(require_admin)]
) -> dict:
    changes = [bt.Change(c.group_key, c.target_cents, c.seen_chat_cents) for c in body.changes]
    try:
        with write_transaction(conn):
            bt.save(
                conn,
                month=body.month,
                changes=changes,
                also_later=body.also_later,
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    return budgets_page(conn, body.month, is_admin=True)
