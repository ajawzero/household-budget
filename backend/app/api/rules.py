"""Merchant rules (PLAN 3.4, 5.6, 8.2): GET, POST, PATCH and DELETE /api/rules.

Admin and member can list, create, preview and change rules. Deleting a rule
is admin only. Pattern text goes through detect_sensitive and the private
lists; a match is a 422 that never echoes the text. Rules apply at read time.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response

from app.api.deps import SIGNED_IN, CurrentUser, Db, app_settings, require_admin
from app.api.txn_models import (
    RuleIn,
    RuleOut,
    RulePatchIn,
    RulePreviewIn,
    RulePreviewOut,
    RulesOut,
)
from app.auth.clock import iso
from app.auth.clock import now as clock_now
from app.auth.users import User
from app.db.connect import write_transaction
from app.importer.runner import private_rules_for
from app.services import rules
from app.services.overrides import EditError, check_text, require_category

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

RuleId = Annotated[int, Path(ge=1, le=1 << 31)]


def _http(exc: EditError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _pattern(request: Request, kind: str, pattern: str) -> str:
    p = rules.clean_pattern(kind, pattern)
    check_text("pattern", [p], private_rules_for(app_settings(request)))
    if len(p) > 64:
        raise EditError(422, "The match text is too long.")
    return p


def _one(conn: Db, rule_id: int) -> dict:
    found = next((r for r in rules.list_rules(conn) if r["id"] == rule_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail=rules.NOT_FOUND)
    return found


@router.get("/rules", response_model=RulesOut)
def list_rules(conn: Db) -> dict:
    return {"rules": rules.list_rules(conn)}


@router.post("/rules/preview", response_model=RulePreviewOut)
def preview_rule(request: Request, conn: Db, body: RulePreviewIn) -> dict:
    try:
        pattern = _pattern(request, body.match_kind, body.pattern)
    except EditError as exc:
        raise _http(exc) from None
    return rules.preview(conn, body.match_kind, pattern, body.category_key)


@router.post("/rules", response_model=RuleOut, status_code=201)
def create_rule(request: Request, conn: Db, user: CurrentUser, body: RuleIn) -> dict:
    try:
        pattern = _pattern(request, body.match_kind, body.pattern)
        require_category(conn, body.category_key)
        with write_transaction(conn):
            rule_id = rules.create_rule(
                conn,
                kind=body.match_kind,
                pattern=pattern,
                category_key=body.category_key,
                from_txn_key=body.created_from_txn_key,
                user_id=user.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _one(conn, rule_id)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def patch_rule(
    request: Request, conn: Db, user: CurrentUser, rule_id: RuleId, body: RulePatchIn
) -> dict:
    changes = {f: getattr(body, f) for f in body.model_fields_set}
    if not changes or any(v is None for v in changes.values()):
        raise HTTPException(status_code=422, detail="Nothing to change.")
    try:
        if "pattern" in changes or "match_kind" in changes:
            kind = changes.get("match_kind") or rules.get_rule(conn, rule_id)["match_kind"]
            pattern = changes.get("pattern") or rules.get_rule(conn, rule_id)["pattern"]
            changes["pattern"] = _pattern(request, kind, pattern)
        if "category_key" in changes:
            require_category(conn, changes["category_key"])
        with write_transaction(conn):
            rules.update_rule(conn, rule_id, changes, user_id=user.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return _one(conn, rule_id)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    conn: Db, rule_id: RuleId, admin: Annotated[User, Depends(require_admin)]
) -> Response:
    try:
        with write_transaction(conn):
            rules.delete_rule(conn, rule_id, user_id=admin.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return Response(status_code=204)
