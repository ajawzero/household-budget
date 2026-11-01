"""Utilities (PLAN 4.12, 8.2): GET /api/utilities and GET /api/utilities/{utility_key}.

GET /api/utilities                      one summary per utility, for the picker
GET /api/utilities/{key}?from=&to=      bills with paid status, year over year rows,
                                        cost per unit and the credits running total.
                                        from and to are YYYY-MM months on period_end.
                                        Both are optional (default: every bill).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from app.api.deps import SIGNED_IN, Db
from app.api.transactions import MONTH_QUERY
from app.api.utility_models import UtilitiesOut, UtilityDetailOut
from app.services import utilities

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

NO_UTILITY = "No bills for this utility."
MonthQ = Annotated[str | None, Query(pattern=MONTH_QUERY)]
UTILITY_PATH = r"^[a-z][a-z0-9-]{0,39}$"  # a key, loosely; unknown keys get 404


@router.get("/utilities", response_model=UtilitiesOut)
def get_utilities(conn: Db) -> dict:
    return {"utilities": utilities.summaries(conn)}


@router.get("/utilities/{utility_key}", response_model=UtilityDetailOut)
def get_utility(
    conn: Db,
    utility_key: Annotated[str, Path(pattern=UTILITY_PATH)],
    from_: Annotated[str | None, Query(alias="from", pattern=MONTH_QUERY)] = None,
    to: MonthQ = None,
) -> dict:
    if from_ and to and from_ > to:
        raise HTTPException(status_code=422, detail="from is after to.")
    out = utilities.detail(conn, utility_key, from_, to)
    if out is None:
        raise HTTPException(status_code=404, detail=NO_UTILITY)
    return out
