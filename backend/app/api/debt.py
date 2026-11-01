"""GET /api/debt (PLAN 8.2 Accounts, Debt tab)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SIGNED_IN, Db
from app.api.p4_models import DebtOut
from app.services.plans import debt_page

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/debt", response_model=DebtOut)
def get_debt(conn: Db) -> dict:
    return debt_page(conn)
