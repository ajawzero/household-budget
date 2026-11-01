"""Rewards (docs/plans/phase-7.md section 4, docs/plans/rewards-inputs.md
section 6).

GET    /api/rewards                               any signed-in user
POST   /api/rewards/balances                      any signed-in user
DELETE /api/rewards/balances/{id}                 any signed-in user
GET    /api/rewards/programs/{key}/balances       any signed-in user
POST   /api/rewards/programs                      admin
PATCH  /api/rewards/programs/{key}                admin
DELETE /api/rewards/programs/{key}[?unit=]       admin
POST   /api/rewards/programs/{key}/move           admin
POST   /api/rewards/programs/{key}/keep-both      admin
POST   /api/rewards/benefits                      admin
PATCH  /api/rewards/benefits/{key}                admin
POST   /api/rewards/benefits/{key}/resolve        admin
DELETE /api/rewards/benefits/{key}/edits          admin
DELETE /api/rewards/benefits/{key}                admin
POST   /api/rewards/benefits/{key}/move           admin
POST   /api/rewards/benefits/{key}/keep-both      admin
GET    /api/rewards/review                        any signed-in user
GET    /api/rewards/benefits/{key}/terms          any signed-in user
PUT    /api/rewards/benefits/{key}/terms          admin
POST   /api/rewards/terms/preview                 admin
PUT    /api/rewards/benefits/{key}/cycles/{start}/mark   any signed-in user
DELETE /api/rewards/benefits/{key}/cycles/{start}/mark   any signed-in user
PUT    /api/rewards/credits/{txn_key}             any signed-in user
DELETE /api/rewards/credits/{txn_key}             any signed-in user
POST   /api/rewards/benefit-files/preview         admin
POST   /api/rewards/benefit-files/apply           admin
GET    /api/rewards/benefit-files/template        any signed-in user

The page is the chat's rewards (the newest monthly run's `rewards`, format
1.3) with the app's inputs on top, plus card rows for spend and `recurring`
plus the registry for fees. Before the first 1.3 run, source_run_id is null,
the lists are empty and every write is a 409. Writes run in one write
transaction and give back the fresh piece. Typed text is scanned with the
rewards rules and the private lists, and a match never echoes the text. A
match word to preview goes in the body, never the URL, so no log holds it.
A benefit file is read from the body with a 256 KB cap, chunked bodies too,
and is never written to disk or logged.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import config
from app.api.deps import SIGNED_IN, CurrentUser, Db, app_settings, require_admin
from app.api.rewards_models import (
    BalanceHistoryOut,
    BalancesIn,
    BalancesSavedOut,
    BalanceUnit,
    BenefitFileOut,
    BenefitFilePreviewOut,
    BenefitFileSavedOut,
    BenefitIn,
    BenefitKeepBothIn,
    BenefitOut,
    BenefitPatchIn,
    CreditChoiceOut,
    CreditIn,
    FileProblemsOut,
    KeepBothIn,
    MarkIn,
    MoveIn,
    ProgramIn,
    ProgramOut,
    ProgramPatchIn,
    ResolveIn,
    RewardsOut,
    RewardsReviewOut,
    TermPreviewIn,
    TermPreviewOut,
    TermsIn,
    TermsOut,
)
from app.api.transactions import TxnKeyPath
from app.auth.clock import iso
from app.auth.clock import now as clock_now
from app.auth.users import User
from app.db.connect import write_transaction
from app.importer.models import Key, to_cents
from app.importer.runner import private_rules_for
from app.services import rewards_files as files
from app.services import rewards_inputs as inputs
from app.services.overrides import EditError
from app.services.rewards import benefit_views, program_views, review_page, rewards_page

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

ProgramKey = Annotated[Key, Path()]
BenefitKey = Annotated[Key, Path()]
BalanceId = Annotated[int, Path(ge=1, le=1 << 31)]
CycleStart = Annotated[date, Path()]
Admin = Annotated[User, Depends(require_admin)]
NOT_SHOWN = "The program is not shown."
BENEFIT_NOT_SHOWN = "The benefit is not shown."


def _file_body(model: type[files.BenefitFile]) -> dict:
    """The request body for the OpenAPI schema. The route reads it as bytes."""
    schema = {"application/json": {"schema": model.model_json_schema()}}
    return {"requestBody": {"required": True, "content": schema}}


def _http(exc: EditError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _program(conn: Db, key: str) -> dict:
    found = program_views(conn, {key}).get(key)
    if found is None:  # the overlay could not show it (its notice says so)
        raise HTTPException(status_code=404, detail=NOT_SHOWN)
    return found


def _benefit(conn: Db, key: str) -> dict:
    found = benefit_views(conn, {key}).get(key)
    if found is None:  # the overlay could not show it (its notice says so)
        raise HTTPException(status_code=404, detail=BENEFIT_NOT_SHOWN)
    return found


def _history(conn: Db, key: str, *, after_delete: bool = False) -> dict:
    found = inputs.balance_history(conn, key)
    if found is None and after_delete:  # the last entry of an orphan key went
        return {"program_key": key, "program": None, "entries": [], "chat": None}
    if found is None:
        raise HTTPException(status_code=404, detail=inputs.NO_PROGRAM)
    return found


@router.get("/rewards", response_model=RewardsOut)
def get_rewards(conn: Db) -> dict:
    return rewards_page(conn)


# --------------------------------------------------------------------------
# Balances (any signed-in user)
# --------------------------------------------------------------------------


@router.post("/rewards/balances", response_model=BalancesSavedOut, status_code=201)
def post_balances(conn: Db, user: CurrentUser, body: BalancesIn) -> dict:
    try:
        with write_transaction(conn):
            keys = inputs.add_balances(
                conn,
                [(e.program_key, e.balance, e.as_of) for e in body.entries],
                today=config.household_today(),
                user_id=user.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    views = program_views(conn, set(keys))
    return {"saved": len(body.entries), "programs": [views[k] for k in keys if k in views]}


@router.delete("/rewards/balances/{balance_id}", response_model=BalanceHistoryOut)
def delete_balance(conn: Db, user: CurrentUser, balance_id: BalanceId) -> dict:
    try:
        with write_transaction(conn):
            key = inputs.delete_balance(conn, balance_id, user_id=user.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return _history(conn, key, after_delete=True)


@router.get("/rewards/programs/{key}/balances", response_model=BalanceHistoryOut)
def get_balances(conn: Db, key: ProgramKey) -> dict:
    return _history(conn, key)


# --------------------------------------------------------------------------
# App programs (admin)
# --------------------------------------------------------------------------


@router.post("/rewards/programs", response_model=ProgramOut, status_code=201)
def post_program(request: Request, conn: Db, admin: Admin, body: ProgramIn) -> dict:
    try:
        with write_transaction(conn):
            key = inputs.add_program(
                conn,
                body.model_dump(),
                private=private_rules_for(app_settings(request)),
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _program(conn, key)


@router.patch("/rewards/programs/{key}", response_model=ProgramOut)
def patch_program(
    request: Request, conn: Db, admin: Admin, key: ProgramKey, body: ProgramPatchIn
) -> dict:
    try:
        with write_transaction(conn):
            inputs.update_program(
                conn,
                key,
                body.changes(),
                private=private_rules_for(app_settings(request)),
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _program(conn, key)


@router.delete("/rewards/programs/{key}", status_code=204)
def delete_program(
    conn: Db,
    admin: Admin,
    key: ProgramKey,
    unit: Annotated[
        BalanceUnit | None,
        Query(
            description='"Delete balances" in Needs a look: only the stranded entries in this '
            "unit. A 409 when none are left. Left out: delete an app program."
        ),
    ] = None,
) -> Response:
    try:
        with write_transaction(conn):
            inputs.delete_program(conn, key, unit=unit, user_id=admin.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return Response(status_code=204)


@router.post("/rewards/programs/{key}/move", response_model=ProgramOut)
def move_program(conn: Db, admin: Admin, key: ProgramKey, body: MoveIn) -> dict:
    try:
        with write_transaction(conn):
            inputs.move_balances(conn, key, body.to_key, user_id=admin.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return _program(conn, body.to_key)


@router.post("/rewards/programs/{key}/keep-both", response_model=ProgramOut)
def keep_both(conn: Db, admin: Admin, key: ProgramKey, body: KeepBothIn) -> dict:
    try:
        with write_transaction(conn):
            inputs.keep_both(conn, key, body.other_key, user_id=admin.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return _program(conn, key)


# --------------------------------------------------------------------------
# Benefits (admin)
# --------------------------------------------------------------------------


@router.post("/rewards/benefits", response_model=BenefitOut, status_code=201)
def post_benefit(request: Request, conn: Db, admin: Admin, body: BenefitIn) -> dict:
    try:
        with write_transaction(conn):
            key = inputs.add_benefit(
                conn,
                body.values(),
                private=private_rules_for(app_settings(request)),
                today=config.household_today(),
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, key)


@router.patch("/rewards/benefits/{key}", response_model=BenefitOut)
def patch_benefit(
    request: Request, conn: Db, admin: Admin, key: BenefitKey, body: BenefitPatchIn
) -> dict:
    try:
        with write_transaction(conn):
            inputs.update_benefit(
                conn,
                key,
                changes=body.set.changes() if body.set is not None else None,
                reset=body.reset or (),
                hidden=body.hidden,
                match_off=body.match_off,
                amount_from=body.amount_from,
                private=private_rules_for(app_settings(request)),
                today=config.household_today(),
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, key)


@router.post("/rewards/benefits/{key}/resolve", response_model=BenefitOut)
def resolve_benefit(conn: Db, admin: Admin, key: BenefitKey, body: ResolveIn) -> dict:
    try:
        with write_transaction(conn):
            inputs.resolve_benefit(
                conn,
                key,
                body.field,
                body.choice,
                today=config.household_today(),
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, key)


@router.delete("/rewards/benefits/{key}/edits", response_model=BenefitOut)
def reset_benefit(conn: Db, admin: Admin, key: BenefitKey) -> dict:
    try:
        with write_transaction(conn):
            inputs.reset_benefit(
                conn, key, today=config.household_today(), user_id=admin.id, now=iso(clock_now())
            )
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, key)


@router.delete("/rewards/benefits/{key}", status_code=204)
def delete_benefit(conn: Db, admin: Admin, key: BenefitKey) -> Response:
    try:
        with write_transaction(conn):
            inputs.delete_benefit(
                conn, key, today=config.household_today(), user_id=admin.id, now=iso(clock_now())
            )
    except EditError as exc:
        raise _http(exc) from None
    return Response(status_code=204)


@router.post("/rewards/benefits/{key}/move", response_model=BenefitOut)
def move_benefit(conn: Db, admin: Admin, key: BenefitKey, body: MoveIn) -> dict:
    try:
        with write_transaction(conn):
            inputs.move_benefit(
                conn,
                key,
                body.to_key,
                today=config.household_today(),
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, body.to_key)


@router.post("/rewards/benefits/{key}/keep-both", response_model=BenefitOut)
def keep_both_benefits(conn: Db, admin: Admin, key: BenefitKey, body: BenefitKeepBothIn) -> dict:
    try:
        with write_transaction(conn):
            inputs.keep_both_benefits(
                conn,
                key,
                body.other_key,
                today=config.household_today(),
                user_id=admin.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, key)


# --------------------------------------------------------------------------
# Credit matching: the review, match words, cycle marks and credit choices
# --------------------------------------------------------------------------


@router.get("/rewards/review", response_model=RewardsReviewOut)
def get_review(conn: Db) -> dict:
    return review_page(conn)


@router.get("/rewards/benefits/{key}/terms", response_model=TermsOut)
def get_terms(request: Request, conn: Db, key: BenefitKey) -> dict:
    try:
        return inputs.terms_view(conn, key, private=private_rules_for(app_settings(request)))
    except EditError as exc:
        raise _http(exc) from None


@router.put("/rewards/benefits/{key}/terms", response_model=TermsOut)
def put_terms(request: Request, conn: Db, admin: Admin, key: BenefitKey, body: TermsIn) -> dict:
    private = private_rules_for(app_settings(request))
    try:
        with write_transaction(conn):
            inputs.set_terms(
                conn, key, body.terms, private=private, user_id=admin.id, now=iso(clock_now())
            )
        return inputs.terms_view(conn, key, private=private)
    except EditError as exc:
        raise _http(exc) from None


@router.post("/rewards/terms/preview", response_model=TermPreviewOut)
def preview_term(request: Request, conn: Db, admin: Admin, body: TermPreviewIn) -> dict:
    try:
        return inputs.term_preview(
            conn,
            body.benefit_key,
            body.term,
            private=private_rules_for(app_settings(request)),
            today=config.household_today(),
        )
    except EditError as exc:
        raise _http(exc) from None


@router.put("/rewards/benefits/{key}/cycles/{start}/mark", response_model=BenefitOut)
def put_mark(conn: Db, user: CurrentUser, key: BenefitKey, start: CycleStart, body: MarkIn) -> dict:
    try:
        with write_transaction(conn):
            inputs.set_mark(
                conn,
                key,
                start,
                to_cents(body.used),
                body.used_on,
                today=config.household_today(),
                user_id=user.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, key)


@router.delete("/rewards/benefits/{key}/cycles/{start}/mark", response_model=BenefitOut)
def delete_mark(conn: Db, user: CurrentUser, key: BenefitKey, start: CycleStart) -> dict:
    try:
        with write_transaction(conn):
            inputs.delete_mark(conn, key, start, user_id=user.id, now=iso(clock_now()))
    except EditError as exc:
        raise _http(exc) from None
    return _benefit(conn, key)


@router.put("/rewards/credits/{txn_key}", response_model=CreditChoiceOut)
def put_credit(conn: Db, user: CurrentUser, txn_key: TxnKeyPath, body: CreditIn) -> dict:
    try:
        with write_transaction(conn):
            inputs.set_choice(
                conn,
                txn_key,
                body.decision,
                benefit_key=body.benefit_key,
                cycle_start=body.cycle_start,
                reason=body.reason,
                today=config.household_today(),
                user_id=user.id,
                now=iso(clock_now()),
            )
        return inputs.choice_view(conn, txn_key)
    except EditError as exc:
        raise _http(exc) from None


@router.delete("/rewards/credits/{txn_key}", status_code=204)
def delete_credit(conn: Db, user: CurrentUser, txn_key: TxnKeyPath) -> Response:
    try:
        with write_transaction(conn):
            inputs.delete_choice(
                conn,
                txn_key,
                today=config.household_today(),
                user_id=user.id,
                now=iso(clock_now()),
            )
    except EditError as exc:
        raise _http(exc) from None
    return Response(status_code=204)


# --------------------------------------------------------------------------
# Benefit files (docs/plans/rewards-inputs.md section 5)
# --------------------------------------------------------------------------


async def _file_bytes(request: Request) -> bytes:
    """The body, at most 256 KB. The stream is read with a byte cap, so a
    chunked body with no length is cut off too. Nothing is kept or logged."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > files.FILE_MAX_BYTES:
        raise HTTPException(status_code=413, detail=files.TOO_BIG)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > files.FILE_MAX_BYTES:
            raise HTTPException(status_code=413, detail=files.TOO_BIG)
    return bytes(body)


def _problems(exc: files.FileProblems) -> JSONResponse:
    problems = [{"where": p.where, "rule": p.rule, "message": p.message} for p in exc.problems]
    return JSONResponse({"detail": files.HAS_PROBLEMS, "problems": problems}, status_code=422)


@router.post(
    "/rewards/benefit-files/preview",
    response_model=BenefitFilePreviewOut,
    openapi_extra=_file_body(files.BenefitFile),
)
async def preview_benefit_file(request: Request, conn: Db, admin: Admin) -> dict:
    raw = await _file_bytes(request)
    private = private_rules_for(app_settings(request))
    try:
        return await run_in_threadpool(
            files.preview, conn, raw, private=private, today=config.household_today()
        )
    except EditError as exc:
        raise _http(exc) from None


@router.post(
    "/rewards/benefit-files/apply",
    response_model=BenefitFileSavedOut,
    responses={422: {"model": FileProblemsOut, "description": "The file has problems."}},
    openapi_extra=_file_body(files.BenefitFileApply),
)
async def apply_benefit_file(request: Request, conn: Db, admin: Admin) -> dict | JSONResponse:
    raw = await _file_bytes(request)
    private = private_rules_for(app_settings(request))

    def write() -> dict:
        with write_transaction(conn):
            return files.apply(
                conn,
                raw,
                private=private,
                today=config.household_today(),
                user_id=admin.id,
                now=iso(clock_now()),
            )

    try:
        return await run_in_threadpool(write)
    except files.FileProblems as exc:
        return _problems(exc)
    except EditError as exc:
        raise _http(exc) from None


@router.get(
    "/rewards/benefit-files/template",
    response_model=BenefitFileOut,
    response_model_exclude_none=True,
)
def get_benefit_file_template(
    conn: Db, account_key: Annotated[Key, Query(description="A credit or store card.")]
) -> dict:
    try:
        return files.template(conn, account_key, today=config.household_today())
    except EditError as exc:
        raise _http(exc) from None
