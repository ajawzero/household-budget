"""GET /api/dashboard?month=YYYY-MM (default: the newest monthly run's this_month)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.ai import review as ai_review
from app.ai import settings as ai_settings
from app.api.ai import review_visible
from app.api.deps import SIGNED_IN, Db, app_settings
from app.api.models import DashboardOut
from app.api.transactions import MONTH_QUERY
from app.services.dashboard import dashboard

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(
    request: Request, conn: Db, month: Annotated[str | None, Query(pattern=MONTH_QUERY)] = None
) -> dict:
    out = dashboard(conn, month)
    # AI off or no key: no AI review. A spend-limit pause keeps the stored one.
    st = ai_settings.status(conn, app_settings(request))
    out["ai_review"] = ai_review.latest_review(conn) if review_visible(st) else None
    return out
