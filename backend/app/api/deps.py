"""Shared FastAPI dependencies: settings, a DB connection per request, the user.

In demo (the demo middleware set `scope["state"]["demo"]`) the settings are the demo
Settings, so the connection, the session lookup and every own `settings.db_path` open
follow on their own. The session cookie is the demo one and the lookup writes nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.auth.sessions import lookup_session
from app.auth.users import User
from app.auth.web import CHANGES_OFF, NOT_SIGNED_IN, cookie_name, demo_cookie_name
from app.config import Settings, get_settings
from app.db.connect import connect
from app.demo.context import DemoGuardError, in_demo


def is_demo(request: Request) -> bool:
    """True when the demo middleware sent this request to the demo database."""
    return bool(request.scope.get("state", {}).get("demo"))


def app_settings(request: Request) -> Settings:
    if is_demo(request):
        # Never the real Settings as a fallback.
        runtime = getattr(request.app.state, "demo", None)
        if runtime is None:
            raise DemoGuardError("demo request without demo settings")
        return runtime.settings
    return getattr(request.app.state, "settings", None) or get_settings()


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(app_settings(request).db_path)
    try:
        yield conn
    finally:
        conn.close()


def session_token(request: Request) -> str | None:
    settings = app_settings(request)
    name = demo_cookie_name(settings) if is_demo(request) else cookie_name(settings)
    return request.cookies.get(name)


def current_user(request: Request, conn: Annotated[sqlite3.Connection, Depends(get_db)]) -> User:
    user = lookup_session(conn, session_token(request), touch=not is_demo(request))
    if user is None:
        raise HTTPException(status_code=401, detail=NOT_SIGNED_IN)
    return user


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can do this.")
    return user


def refuse_in_demo(request: Request) -> None:
    """403 in demo, on top of the middleware's refusal. On a route's decorator it runs
    before the handler, so before any process-wide busy slot or running task is read."""
    if is_demo(request) or in_demo():
        raise HTTPException(status_code=403, detail=CHANGES_OFF)


# Router-level guard: every route on a router with this needs a session.
SIGNED_IN = [Depends(current_user)]
# Route-level: the AI runs and the demo start never run in demo.
NOT_IN_DEMO = [Depends(refuse_in_demo)]

Db = Annotated[sqlite3.Connection, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]
