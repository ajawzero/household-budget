"""GET /healthz: liveness and a DB check. No auth, no data."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import app_settings
from app.db.connect import connect

router = APIRouter()
log = logging.getLogger("app.health")


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    try:
        conn = connect(app_settings(request).db_path, readonly=True)
        try:
            conn.execute("SELECT count(*) FROM import_runs").fetchone()
        finally:
            conn.close()
    except Exception as exc:  # report a class only, never a message
        log.error("health check failed", extra={"exc_class": type(exc).__name__})
        return JSONResponse({"ok": False, "db": "error"}, status_code=503)
    return JSONResponse({"ok": True, "db": "ok"})
