"""Cash Flow (PLAN 3.9, 8.2): GET /api/cashflow, /api/cashflow/breakdown,
/api/cashflow/category/{key}, /api/cashflow/plan and /api/income.

Months are `YYYY-MM` query values. A range is at most 36 months. Default
ranges: the 12 months up to this month (cash flow, income, category), and
the latest complete month (breakdown).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query

from app.api.deps import SIGNED_IN, Db
from app.api.p4_models import (
    BreakdownOut,
    CashflowOut,
    CashflowPlanOut,
    CategoryDetailOut,
    IncomeOut,
)
from app.api.transactions import MONTH_QUERY
from app.services import cashflow as cf

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

MonthQ = Annotated[str | None, Query(pattern=MONTH_QUERY)]
CATEGORY_PATH = r"^[a-z][a-z0-9-]{0,63}$"
NO_CATEGORY = "Unknown category."


def _range_error(exc: cf.RangeError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/cashflow", response_model=CashflowOut)
def get_cashflow(
    conn: Db,
    from_: Annotated[str | None, Query(alias="from", pattern=MONTH_QUERY)] = None,
    to: MonthQ = None,
) -> dict:
    try:
        return cf.cashflow(conn, from_, to)
    except cf.RangeError as exc:
        raise _range_error(exc) from None


@router.get("/cashflow/breakdown", response_model=BreakdownOut)
def get_breakdown(
    conn: Db,
    from_: Annotated[str | None, Query(alias="from", pattern=MONTH_QUERY)] = None,
    to: MonthQ = None,
    by: Literal["category", "group", "merchant"] = "category",
    flow: Literal["spending", "income"] = "spending",
) -> dict:
    try:
        return cf.breakdown(conn, from_, to, by, flow)
    except cf.RangeError as exc:
        raise _range_error(exc) from None


@router.get("/cashflow/category/{key}", response_model=CategoryDetailOut)
def get_category(
    conn: Db,
    key: Annotated[str, Path(pattern=CATEGORY_PATH)],
    from_: Annotated[str | None, Query(alias="from", pattern=MONTH_QUERY)] = None,
    to: MonthQ = None,
) -> dict:
    try:
        out = cf.category_detail(conn, key, from_, to)
    except cf.RangeError as exc:
        raise _range_error(exc) from None
    if out is None:
        raise HTTPException(status_code=404, detail=NO_CATEGORY)
    return out


@router.get("/cashflow/plan", response_model=CashflowPlanOut)
def get_plan(conn: Db) -> dict:
    return cf.plan_page(conn)


@router.get("/income", response_model=IncomeOut)
def get_income(
    conn: Db,
    from_: Annotated[str | None, Query(alias="from", pattern=MONTH_QUERY)] = None,
    to: MonthQ = None,
) -> dict:
    try:
        return cf.income_page(conn, from_, to)
    except cf.RangeError as exc:
        raise _range_error(exc) from None
