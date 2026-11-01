"""Travel (docs/plans/phase-7.md section 4): GET /api/trips and GET /api/trips/{key}.

Read only, from the trip tables (format 1.3 history: the newest run per trip
key, tombstones removed). Tagged rows are a check only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from app.api.deps import SIGNED_IN, Db
from app.api.trips_models import TripOut, TripsOut
from app.services.trips import trip_detail, trips_page

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

TRIP_KEY = r"^[a-z][a-z0-9_-]{0,63}$"
NOT_FOUND = "No trip with that key."


@router.get("/trips", response_model=TripsOut)
def get_trips(conn: Db) -> dict:
    return trips_page(conn)


@router.get("/trips/{key}", response_model=TripOut)
def get_trip(conn: Db, key: Annotated[str, Path(pattern=TRIP_KEY)]) -> dict:
    t = trip_detail(conn, key)
    if t is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return t
