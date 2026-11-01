"""GET /api/statements/coverage?from=&to= (PLAN 8.2 Imports, Statements).

The account x month grid, expected by cadence. Default range: Jan 2026 to
this month. At most 36 months.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SIGNED_IN, Db
from app.api.p4_models import CoverageOut
from app.api.transactions import MONTH_QUERY
from app.services import cashflow as cf
from app.services.coverage import grid

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/statements/coverage", response_model=CoverageOut)
def get_coverage(
    conn: Db,
    from_: Annotated[str | None, Query(alias="from", pattern=MONTH_QUERY)] = None,
    to: Annotated[str | None, Query(pattern=MONTH_QUERY)] = None,
) -> dict:
    try:
        months = cf.resolve_months(from_ or "2026-01", to, span=cf.MAX_MONTHS)
    except cf.RangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return grid(conn, months[0], months[-1])
