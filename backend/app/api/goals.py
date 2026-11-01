"""Goals (format 1.4): GET /api/goals. Read only: the chat owns the goals."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SIGNED_IN, Db
from app.api.goals_models import GoalsOut
from app.services.goals import goals_page

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/goals", response_model=GoalsOut)
def get_goals(conn: Db) -> dict:
    return goals_page(conn)
