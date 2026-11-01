"""Login, logout, me and password change (PLAN 5.1).

Demo (docs/plans/demo-mode.md section 4): login is always real and clears a demo
cookie it is sent. Logout in demo is leaving demo: the demo writer deletes the demo
session (the request connection is read only). `/me` says whether this browser is in
demo, whether demo can start, and the pinned date in demo.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUser, Db, app_settings, current_user, is_demo, session_token
from app.auth.login import OK, THROTTLED, change_password, login
from app.auth.passwords import MAX_LENGTH, password_problem
from app.auth.sessions import delete_session
from app.auth.web import (
    clear_demo_cookie,
    clear_session_cookie,
    demo_cookie_name,
    set_session_cookie,
)
from app.config import household_today
from app.demo import store as demo_store

router = APIRouter(prefix="/api/auth")

WRONG_LOGIN = "Wrong username or password."
TOO_MANY = "Too many attempts. Try again later."


class LoginBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=MAX_LENGTH)]


class PasswordBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    current_password: Annotated[str, Field(min_length=1, max_length=MAX_LENGTH)]
    new_password: Annotated[str, Field(min_length=1, max_length=MAX_LENGTH)]


class UserOut(BaseModel):
    username: str
    display_name: str
    role: str


class MeOut(UserOut):
    demo: bool = Field(description="This browser is in demo mode (the fake sample household).")
    demo_available: bool = Field(description="An admin can start demo mode.")
    household_today: str | None = Field(
        description="In demo, the pinned household date (YYYY-MM-DD). None outside demo."
    )


class LoginOut(BaseModel):
    user: UserOut


@router.post("/login", response_model=LoginOut)
def post_login(
    body: LoginBody,
    request: Request,
    response: Response,
    conn: Db,
) -> dict:
    result = login(conn, body.username, body.password)
    if result.status == THROTTLED:
        raise HTTPException(status_code=429, detail=TOO_MANY)
    if result.status != OK or result.user is None or result.token is None:
        raise HTTPException(status_code=401, detail=WRONG_LOGIN)
    settings = app_settings(request)  # never demo: the middleware skips the login
    set_session_cookie(response, result.token, settings)
    if demo_cookie_name(settings) in request.cookies:
        clear_demo_cookie(response, settings)
    return {"user": result.user.public()}


@router.post("/logout", status_code=204, dependencies=[Depends(current_user)])
def post_logout(request: Request, conn: Db) -> Response:
    settings = app_settings(request)
    response = Response(status_code=204)
    if is_demo(request):
        demo_store.delete_session(settings.db_path, session_token(request))
        clear_demo_cookie(response, settings)
    else:
        delete_session(conn, session_token(request))
    clear_session_cookie(response, settings)
    return response


@router.get("/me", response_model=MeOut)
def get_me(request: Request, user: CurrentUser) -> dict:
    demo = is_demo(request)
    runtime = getattr(request.app.state, "demo", None)
    return {
        **user.public(),
        "demo": demo,
        "demo_available": bool(runtime is not None and runtime.available),
        "household_today": household_today().isoformat() if demo else None,
    }


@router.post("/password", status_code=204)
def post_password(
    body: PasswordBody,
    request: Request,
    user: CurrentUser,
    conn: Db,
) -> Response:
    problem = password_problem(body.new_password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    status = change_password(
        conn, user, body.current_password, body.new_password, keep_token=session_token(request)
    )
    if status == THROTTLED:
        raise HTTPException(status_code=429, detail=TOO_MANY)
    if status != OK:
        raise HTTPException(status_code=400, detail="Current password is wrong.")
    return Response(status_code=204)
