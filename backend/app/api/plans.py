"""The trip planner (docs/plans/trip-planner.md): /api/trip-plans.

Plans belong to the household: anyone signed in can make, read, change, link
and delete them. A plan runs in the background, one at a time. The page polls
GET /api/trip-plans/{plan_id} until its status is no longer running.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response

from app.ai import client as ai_client
from app.ai import ledger, planner
from app.ai import settings as ai_settings
from app.api.ai import AI_STATUS_CODE
from app.api.deps import NOT_IN_DEMO, SIGNED_IN, CurrentUser, Db, app_settings
from app.api.plans_models import FollowUpIn, PlanStarted, PlanView, TripLinkIn, TripPlansOut
from app.db.connect import write_transaction
from app.importer.runner import private_rules_for

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

PlanId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
NOT_FOUND = "Trip plan not found."


def _refused(exc: planner.PlanError | ai_client.AiError) -> HTTPException:
    if isinstance(exc, planner.PlanError):
        return HTTPException(status_code=exc.status, detail=exc.message)
    return HTTPException(status_code=AI_STATUS_CODE[exc.kind], detail=exc.message)


def _view(request: Request, conn: Db, plan_id: str) -> dict:
    view = planner.plan_view(conn, plan_id, private_rules_for(app_settings(request)))
    if view is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return view


@router.get("/trip-plans", response_model=TripPlansOut)
def get_trip_plans(request: Request, conn: Db) -> dict:
    settings = app_settings(request)
    s = ai_settings.load(conn)
    estimate = planner.estimate_micro(conn, s.planner_model)
    return {
        "plans": planner.list_plans(conn, private_rules_for(settings)),
        "running_plan_id": planner.running_plan(),
        "available": ai_settings.status(conn, settings)["available"],
        "estimate_cents": (estimate + ledger.CENT // 2) // ledger.CENT,
        "follow_up_limit": planner.FOLLOW_UP_LIMIT,
        "model": s.planner_model,
        "people": planner.travelers(conn),
    }


@router.post("/trip-plans", response_model=PlanStarted, dependencies=NOT_IN_DEMO)
async def post_trip_plan(request: Request, user: CurrentUser, body: planner.PlanRequest) -> dict:
    settings = app_settings(request)

    def prepare() -> str:
        return planner.new_plan(settings.db_path, settings, user.id, body)

    try:
        plan_id = await planner.start(settings, user.id, prepare)
    except (planner.PlanError, ai_client.AiError) as exc:
        raise _refused(exc) from None
    return {"plan_id": plan_id, "status": "running"}


@router.get("/trip-plans/{plan_id}", response_model=PlanView)
def get_trip_plan(request: Request, conn: Db, plan_id: PlanId) -> dict:
    return _view(request, conn, plan_id)


@router.post(
    "/trip-plans/{plan_id}/follow-up", response_model=PlanStarted, dependencies=NOT_IN_DEMO
)
async def post_trip_plan_follow_up(
    request: Request, user: CurrentUser, plan_id: PlanId, body: FollowUpIn
) -> dict:
    settings = app_settings(request)

    def prepare() -> None:
        planner.follow_up(settings.db_path, settings, user.id, plan_id, body.text)

    try:
        await planner.start(settings, user.id, prepare, plan_id)
    except (planner.PlanError, ai_client.AiError) as exc:
        raise _refused(exc) from None
    return {"plan_id": plan_id, "status": "running"}


@router.put("/trip-plans/{plan_id}/trip", response_model=PlanView)
def put_trip_plan_trip(request: Request, conn: Db, plan_id: PlanId, body: TripLinkIn) -> dict:
    try:
        with write_transaction(conn):
            found = planner.link_trip(conn, plan_id, body.trip_key)
    except planner.PlanError as exc:
        raise _refused(exc) from None
    if not found:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return _view(request, conn, plan_id)


@router.delete("/trip-plans/{plan_id}", status_code=204)
def delete_trip_plan(conn: Db, plan_id: PlanId) -> Response:
    try:
        with write_transaction(conn):
            found = planner.delete_plan(conn, plan_id)
    except planner.PlanError as exc:
        raise _refused(exc) from None
    if not found:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=204)
