"""Start demo mode (docs/plans/demo-mode.md section 4): POST /api/demo/start.

A real admin only. Leaving demo is POST /api/auth/logout, which branches on demo.

In order:
1. Make sure the demo database is ready (built under the lock if not). On any
   failure: 503, and the real session stays.
2. Delete the real session row.
3. Create a demo session through the demo writer. On a failure the browser is just
   signed out: a 503 that clears the real cookie and sets no demo cookie.
4. 204: clear the real cookie, set the demo cookie.

Always registered, so the OpenAPI schema never depends on the settings.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import NOT_IN_DEMO, SIGNED_IN, Db, app_settings, require_admin, session_token
from app.auth.sessions import delete_session
from app.auth.users import User
from app.auth.web import clear_session_cookie, set_demo_cookie
from app.demo import store

log = logging.getLogger("app.demo")

router = APIRouter(prefix="/api/demo", dependencies=SIGNED_IN)

UNAVAILABLE = "Demo is not available right now."


@router.post("/start", status_code=204, dependencies=NOT_IN_DEMO)
def start_demo(request: Request, conn: Db, _: Annotated[User, Depends(require_admin)]) -> Response:
    """Switch this browser to the demo: the real session ends, a demo session starts.
    Sync, so the build (about half a second) runs in a worker thread."""
    settings = app_settings(request)
    try:
        ready = request.app.state.demo.ensure_ready()
    except Exception as exc:  # the class only: a reason could name a folder
        log.warning("demo start refused", extra={"exc_class": type(exc).__name__})
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from None
    delete_session(conn, session_token(request))
    try:
        token = store.create_session(ready.path)
    except Exception as exc:
        log.error("demo session failed", extra={"exc_class": type(exc).__name__})
        failed = JSONResponse({"detail": UNAVAILABLE}, status_code=503)
        clear_session_cookie(failed, settings)
        return failed
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    set_demo_cookie(response, token, settings)
    log.info("demo started")
    return response
