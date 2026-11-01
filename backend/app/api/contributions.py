"""401k and HSA contributions vs the IRS limits (PLAN 3.3, 8.2).

GET /api/contributions?year=                admin and member
PUT /api/contribution-limits                admin: add, change or confirm a limit
PUT /api/contributions/people/{person_key}  admin: catch-up, HSA coverage, lead time
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app import config
from app.api.deps import SIGNED_IN, Db, require_admin
from app.api.p5_models import (
    ContributionsOut,
    LimitIn,
    LimitRow,
    PersonSettingsIn,
    PersonSettingsOut,
)
from app.auth.clock import iso
from app.auth.clock import now as clock_now
from app.auth.users import User
from app.db.connect import write_transaction
from app.services import contributions
from app.services.overrides import EditError

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/contributions", response_model=ContributionsOut)
def get_contributions(
    conn: Db, year: Annotated[int | None, Query(ge=2020, le=2100)] = None
) -> dict:
    today = config.household_today()
    return contributions.contributions_page(conn, year or today.year, today)


@router.put("/contribution-limits", response_model=LimitRow)
def put_limit(conn: Db, body: LimitIn, admin: Annotated[User, Depends(require_admin)]) -> dict:
    try:
        with write_transaction(conn):
            return contributions.set_limit(
                conn,
                year=body.year,
                kind=body.kind,
                limit_cents=body.limit_cents,
                confirm=body.confirm,
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.put("/contributions/people/{person_key}", response_model=PersonSettingsOut)
def put_person(
    conn: Db,
    person_key: str,
    body: PersonSettingsIn,
    admin: Annotated[User, Depends(require_admin)],
) -> dict:
    try:
        with write_transaction(conn):
            s = contributions.set_person(
                conn,
                person_key,
                k401_catch_up=body.k401_catch_up,
                hsa_coverage=body.hsa_coverage,
                hsa_catch_up=body.hsa_catch_up,
                election_lead_checks=body.election_lead_checks,
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    return s.out()
