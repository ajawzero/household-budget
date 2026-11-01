"""Import runs: list, detail and the admin Retry (PLAN 8.2, Imports)."""

from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import SIGNED_IN, Db, app_settings, require_admin
from app.importer.order import is_run_id, run_order_key
from app.importer.runner import retry_run
from app.importer.validate import rule_message

router = APIRouter(prefix="/api/imports", dependencies=SIGNED_IN)
log = logging.getLogger("app.api")

RUN_NOT_FOUND = "Run not found."


def _issues(text: str | None) -> list[dict]:
    """[{rule, where}] from import_runs JSON, as [{loc, rule, message}]."""
    out = []
    for i in json.loads(text) if text else []:
        out.append(
            {"loc": i.get("where", ""), "rule": i["rule"], "message": rule_message(i["rule"])}
        )
    return out


def run_out(row: sqlite3.Row) -> dict:
    errors = _issues(row["error_json"])
    return {
        "run_id": row["run_id"],
        "mode": row["mode"],
        "schema_version": row["schema_version"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "counts": json.loads(row["counts_json"]) if row["counts_json"] else {},
        "warning_count": len(json.loads(row["warnings_json"])) if row["warnings_json"] else 0,
        "error": errors[0] if errors else None,
    }


def _find(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    if not is_run_id(run_id):
        raise HTTPException(status_code=404, detail=RUN_NOT_FOUND)
    row = conn.execute("SELECT * FROM import_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=RUN_NOT_FOUND)
    return row


@router.get("")
def list_imports(conn: Db) -> dict:
    rows = conn.execute("SELECT * FROM import_runs").fetchall()
    rows.sort(key=lambda r: run_order_key(r["run_id"]), reverse=True)
    return {"runs": [run_out(r) for r in rows]}


@router.get("/{run_id}")
def get_import(run_id: str, conn: Db) -> dict:
    row = _find(conn, run_id)
    out = run_out(row)
    out["errors"] = _issues(row["error_json"])
    out["warnings"] = _issues(row["warnings_json"])
    out["statements"] = [
        {
            "statement_key": s["statement_key"],
            "account_key": s["account_key"],
            "period_end": s["period_end"],
            "is_current": bool(s["is_current"]),
            "txn_count": s["txn_count"],
            "reconciled": bool(s["reconciled"]),
        }
        for s in conn.execute(
            """SELECT statement_key, account_key, period_end, is_current, txn_count, reconciled
               FROM statements WHERE run_id = ? ORDER BY account_key, period_end""",
            (run_id,),
        )
    ]
    return out


@router.post("/{run_id}/retry", status_code=202, dependencies=[Depends(require_admin)])
def post_retry(run_id: str, request: Request, conn: Db) -> JSONResponse:
    _find(conn, run_id)
    result = retry_run(run_id, settings=app_settings(request))
    log.info("retry %s: %s", run_id, result.status)
    return JSONResponse({"status": result.status}, status_code=202)
