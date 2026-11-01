"""Home, vehicle and vehicle loan values (PLAN 3.9, 6.3, 8.2).

GET    /api/valuations                          admin and member
GET    /api/vehicles                            admin and member
POST   /api/vehicles/{key}/mileage              any signed-in user ("Update mileage")
DELETE /api/vehicles/{key}/mileage/{reading_id} any signed-in user
PUT    /api/vehicles/{key}/settings             admin
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app import config
from app.api.deps import SIGNED_IN, CurrentUser, Db, require_admin
from app.api.p5_models import MileageIn, ValuationsOut, VehicleOut, VehicleSettingsIn, VehiclesOut
from app.auth.clock import iso
from app.auth.clock import now as clock_now
from app.auth.users import User
from app.db.connect import write_transaction
from app.services import valuations
from app.services.overrides import EditError

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


def _http(exc: EditError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/valuations", response_model=ValuationsOut)
def get_valuations(conn: Db) -> dict:
    return valuations.valuations_page(conn, config.household_today())


@router.get("/vehicles", response_model=VehiclesOut)
def get_vehicles(conn: Db) -> dict:
    return valuations.vehicles_page(conn, config.household_today())


@router.post("/vehicles/{key}/mileage", response_model=VehicleOut, status_code=201)
def post_mileage(conn: Db, key: str, body: MileageIn, user: CurrentUser) -> dict:
    today = config.household_today()
    try:
        with write_transaction(conn):
            valuations.add_reading(
                conn,
                key,
                reading_date=body.reading_date or today,
                miles=body.miles,
                today=today,
                user_id=user.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return valuations.vehicle(conn, key, today)


@router.delete("/vehicles/{key}/mileage/{reading_id}", response_model=VehicleOut)
def delete_mileage(conn: Db, key: str, reading_id: int, user: CurrentUser) -> dict:
    try:
        with write_transaction(conn):
            valuations.delete_reading(conn, key, reading_id, user_id=user.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return valuations.vehicle(conn, key, config.household_today())


@router.put("/vehicles/{key}/settings", response_model=VehicleOut)
def put_settings(
    conn: Db, key: str, body: VehicleSettingsIn, admin: Annotated[User, Depends(require_admin)]
) -> dict:
    today = config.household_today()
    try:
        with write_transaction(conn):
            valuations.set_settings(
                conn, key, body.model_dump(), today=today, user_id=admin.id, now=iso(clock_now())
            )
    except EditError as exc:
        raise _http(exc) from None
    return valuations.vehicle(conn, key, today)
