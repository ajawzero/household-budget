"""GET /api/checks?run_id=: app numbers vs the chat's, statement basis (PLAN 8.2, Checks)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SIGNED_IN, Db
from app.api.models import ChecksOut
from app.importer.order import is_run_id
from app.services.checks import checks

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

NO_RUN = "No imported run with net worth numbers."


@router.get("/checks", response_model=ChecksOut)
def get_checks(conn: Db, run_id: Annotated[str | None, Query(max_length=40)] = None) -> dict:
    """Default run: the newest imported monthly run that has a net_worth section."""
    if run_id is not None and not is_run_id(run_id):
        raise HTTPException(status_code=404, detail=NO_RUN)
    out = checks(conn, run_id)
    if out is None:
        raise HTTPException(status_code=404, detail=NO_RUN)
    return out
