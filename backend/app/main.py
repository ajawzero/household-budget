"""The FastAPI app (PLAN 2.2, 5.1, 5.2, 5.5).

Run: `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log`
or `uv run python -m app.main` (same defaults).

- Lifespan: JSON logs, umask 077, tighten db and backup folder modes, run
  migrations, the privacy rescan check (`jobs/privacy.py`), end trip plans a
  restart cut off (`ai/planner.py`), check the demo folders and delete the old
  demo database (`demo/runtime.py`), then start the scheduler loop (import
  poll every 60 s, nightly backup at 03:15 America/Chicago).
- Demo mode: `app.state.demo` holds the per-app demo state, and `DemoMiddleware`
  sends a request with the demo cookie to the demo database.
- Docs, ReDoc and the OpenAPI schema are off unless BUDGET_DEV=1.
- `redirect_slashes=False`: the app never builds an http:// redirect behind TLS.
- Serves the built frontend from BUDGET_STATIC_DIR, or backend/app/static when
  it holds an index.html, with an SPA fallback to index.html.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from app.ai import planner
from app.api import (
    accounts,
    ai,
    auth,
    backups,
    budgets,
    cashflow,
    categories,
    checks,
    contributions,
    dashboard,
    debt,
    demo,
    flags,
    goals,
    health,
    imports,
    investments,
    meta,
    networth,
    plans,
    prices,
    recurring,
    rewards,
    rules,
    statements,
    transactions,
    trips,
    utilities,
    valuations,
)
from app.auth.web import DemoMiddleware, SecurityMiddleware
from app.config import Settings, get_settings
from app.demo.runtime import DemoRuntime
from app.jobs.privacy import startup_check
from app.jobs.scheduler import TICK_SECONDS, Scheduler, default_jobs
from app.logging import RequestLogMiddleware, configure_logging

log = logging.getLogger("app.main")

DEFAULT_STATIC = Path(__file__).resolve().parent / "static"
# Routes that need no session. Static files (the SPA route) are public too.
PUBLIC_PATHS = frozenset({"/healthz", "/api/auth/login"})
SPA_ROUTE_NAME = "spa"


def static_root(settings: Settings) -> Path | None:
    root = settings.static_dir or DEFAULT_STATIC
    return root.resolve() if (root / "index.html").is_file() else None


def secure_folders(settings: Settings) -> None:
    """umask 077, and no group or other access on the db and backup folders."""
    os.umask(0o077)
    for folder in (settings.db_path.parent, settings.backup_dir):
        if folder.is_dir() and folder.stat().st_mode & 0o077:
            folder.chmod(0o700)


def create_app(settings: Settings | None = None, *, start_scheduler: bool = True) -> FastAPI:
    settings = settings or get_settings()
    runtime = DemoRuntime(settings)  # a Settings copy only: no file is read here

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from app.db.migrate import migrate

        configure_logging()
        secure_folders(settings)
        version = await asyncio.to_thread(migrate, settings=settings)
        log.info("started", extra={"db_version": version, "dev": settings.dev})
        await asyncio.to_thread(startup_check, settings)
        await asyncio.to_thread(planner.startup_reset, settings.db_path)
        await asyncio.to_thread(runtime.start)
        task = None
        if start_scheduler:
            jobs = default_jobs(settings)
            tick = min([TICK_SECONDS, *(j.every.total_seconds() for j in jobs if j.every)])
            task = asyncio.create_task(
                Scheduler(jobs, settings.db_path, tick_seconds=tick).run_forever()
            )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            log.info("stopped")

    dev = settings.dev
    app = FastAPI(
        title="Budget",
        lifespan=lifespan,
        redirect_slashes=False,
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings
    app.state.demo = runtime

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Locations and error types only. Never the input, which may be a password.
        # An unknown key is input too, so it never goes back in the location.
        errors = []
        for e in exc.errors():
            loc = [str(p) for p in e.get("loc", ())]
            if e.get("type") == "extra_forbidden" and loc:
                loc[-1] = "(unknown field)"
            errors.append({"loc": loc, "type": e.get("type", "")})
        return JSONResponse({"detail": "Invalid request.", "errors": errors}, status_code=422)

    routers = (
        health,
        ai,
        auth,
        demo,
        meta,
        imports,
        accounts,
        networth,
        dashboard,
        transactions,
        rules,
        categories,
        checks,
        cashflow,
        budgets,
        recurring,
        debt,
        flags,
        goals,
        statements,
        backups,
        investments,
        prices,
        contributions,
        valuations,
        utilities,
        rewards,
        trips,
        plans,
    )
    for module in routers:
        app.include_router(module.router)

    root = static_root(settings)
    if root is not None:
        add_spa(app, root)

    # The last one added runs first: request log, then the security checks, then demo.
    app.add_middleware(DemoMiddleware, runtime=runtime, settings=settings)
    app.add_middleware(SecurityMiddleware, settings=settings)
    app.add_middleware(RequestLogMiddleware)
    return app


def add_spa(app: FastAPI, root: Path) -> None:
    """Serve built files, and index.html for any other GET outside /api."""
    index = root / "index.html"

    @app.get("/{full_path:path}", name=SPA_ROUTE_NAME, include_in_schema=False)
    def spa(full_path: str) -> Response:
        if full_path == "api" or full_path.startswith("api/") or full_path == "healthz":
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            target = (root / full_path).resolve()
            if target.is_relative_to(root) and target.is_file():
                cache = (
                    "public, max-age=31536000, immutable"
                    if full_path.startswith("assets/")
                    else "no-cache"
                )
                return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def walk_routes(app: FastAPI) -> Iterator[tuple[str, frozenset[str], str]]:
    """(path, methods, name) for every route, including those in included routers."""

    def walk(routes: Iterable[object]) -> Iterator[tuple[str, frozenset[str], str]]:
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:  # FastAPI keeps included routers as one branch
                yield from walk(inner.routes)
            elif isinstance(getattr(r, "path", None), str):
                yield r.path, frozenset(getattr(r, "methods", None) or ()), getattr(r, "name", "")

    yield from walk(app.routes)


def __getattr__(name: str) -> FastAPI:
    """`app.main:app` is built on first use, so importing this module reads no settings.

    Logging is set up here too, so uvicorn's own startup lines are JSON as well.
    """
    if name == "app":
        global app
        configure_logging()
        app = create_app()
        return app
    raise AttributeError(name)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="app.main", description="Run the budget app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    configure_logging()
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
        log_config=None,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
