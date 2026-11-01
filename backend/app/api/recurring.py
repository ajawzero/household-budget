"""Recurring and plans (PLAN 8.2 Recurring): GET /api/recurring,
/api/recurring/calendar and /api/plans. Default month: this month."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import SIGNED_IN, Db
from app.api.p4_models import PlansOut, RecurringCalendarOut, RecurringOut
from app.api.transactions import MONTH_QUERY
from app.services import recurring
from app.services.coverage import current_month
from app.services.plans import plans_page

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

MonthQ = Annotated[str | None, Query(pattern=MONTH_QUERY)]


@router.get("/recurring", response_model=RecurringOut)
def get_recurring(
    conn: Db, view: Literal["monthly", "all"] = "monthly", month: MonthQ = None
) -> dict:
    month = month or current_month()
    if view == "all":
        return recurring.all_view(conn, month)
    return recurring.month_view(conn, month)


@router.get("/recurring/calendar", response_model=RecurringCalendarOut)
def get_calendar(conn: Db, month: MonthQ = None) -> dict:
    return recurring.calendar(conn, month or current_month())


@router.get("/plans", response_model=PlansOut)
def get_plans(conn: Db) -> dict:
    return plans_page(conn)
