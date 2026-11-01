"""GET /api/networth/series: daily net worth, or one account group, over a range."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SIGNED_IN, Db
from app.api.models import RangeKey, SeriesKey, SeriesOut
from app.services.networth import DEFAULT_RANGE, networth_series

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/networth/series", response_model=SeriesOut)
def get_series(
    conn: Db,
    range: Annotated[RangeKey, Query()] = DEFAULT_RANGE,
    series: Annotated[SeriesKey, Query()] = "networth",
) -> dict:
    return networth_series(conn, range, series)
