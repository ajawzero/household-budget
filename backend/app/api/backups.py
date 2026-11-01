"""GET /api/backups (admin only): backup files and the last backup job (PLAN 5.8)."""

from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from app.api.deps import SIGNED_IN, Db, app_settings, require_admin
from app.api.models import BackupsOut
from app.jobs.backup import JOB_NAME, list_backups
from app.jobs.scheduler import last_success

router = APIRouter(prefix="/api", dependencies=[*SIGNED_IN, Depends(require_admin)])


@router.get("/backups", response_model=BackupsOut)
def get_backups(request: Request, conn: Db) -> dict:
    row = conn.execute(
        "SELECT * FROM job_runs WHERE job = ? ORDER BY id DESC LIMIT 1", (JOB_NAME,)
    ).fetchone()
    last_run = None
    if row is not None:
        summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
        last_run = {
            "status": "ok" if row["status"] == "ok" else "failed",
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "file": summary.get("file"),
            "error": summary.get("error"),
        }
    success = last_success(conn, JOB_NAME)
    return {
        "backups": [asdict(b) for b in list_backups(app_settings(request).backup_dir)],
        "last_run": last_run,
        "last_success_at": success.isoformat(timespec="seconds") if success else None,
    }
