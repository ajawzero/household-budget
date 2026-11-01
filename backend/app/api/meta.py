"""GET /api/meta: app version, supported format versions and the chat's meta state."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from app.api.deps import SIGNED_IN, Db
from app.api.models import MetaOut
from app.config import household_today
from app.importer.models import KNOWN_VERSIONS
from app.importer.order import run_order_key
from app.services.coverage import coverage_state
from app.state.readers import newest_section_json

APP_VERSION = "0.1.0"

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


def latest_imported_run(conn: sqlite3.Connection) -> str | None:
    runs = [
        r["run_id"]
        for r in conn.execute("SELECT run_id FROM import_runs WHERE status = 'imported'")
    ]
    return max(runs, key=run_order_key) if runs else None


@router.get("/meta", response_model=MetaOut)
def get_meta(conn: Db) -> dict:
    meta = newest_section_json(conn, "meta") or {}
    return {
        "app_version": APP_VERSION,
        "schema_versions": list(KNOWN_VERSIONS),
        "household_today": household_today().isoformat(),
        "latest_run_id": latest_imported_run(conn),
        "updated": meta.get("updated"),
        "this_month": meta.get("this_month"),
        "complete_as_of": meta.get("complete_as_of"),
        "latest_complete_month": coverage_state(conn).latest_complete_month(),
        "location_label": meta.get("location_label"),
    }
