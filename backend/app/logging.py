"""JSON line logs with the no-values rule (PLAN 5.5).

- One JSON object per line on stdout: ts, level, logger, msg, plus any
  `extra` fields a caller passes (method, route, status, duration_ms, ...).
- Logs never hold amounts, balances, merchant names, descriptions, search text,
  request or response bodies, keys, or Claude prompts and answers. Callers log
  ids, counts, rule ids and route templates only.
- Exceptions are reduced to their class and `module:line`. The message and the
  traceback text are never written, since they may hold a value.
- The `anthropic`, `httpx`, `httpcore`, `httpx2`, `httpcore2` (the SDK's HTTP layer)
  and `asyncio` loggers stay at WARNING.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

PINNED_WARNING = ("anthropic", "httpx", "httpcore", "httpx2", "httpcore2", "asyncio")
# uvicorn's own loggers: their handlers are removed so lines go through ours.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# LogRecord attributes that are not caller extras (color_message is uvicorn's).
_STANDARD = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


def exception_site(tb: TracebackType | None) -> str:
    """`module:line` of the innermost frame. Never the message."""
    if tb is None:
        return ""
    while tb.tb_next is not None:
        tb = tb.tb_next
    module = tb.tb_frame.f_globals.get("__name__") or tb.tb_frame.f_code.co_filename
    return f"{module}:{tb.tb_lineno}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                out[key] = value if isinstance(value, (str, int, bool, type(None))) else str(value)
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, _, tb = record.exc_info
            out["exc_class"] = exc_type.__name__
            out["at"] = exception_site(tb)
        return json.dumps(out, ensure_ascii=False)


class StdoutHandler(logging.Handler):
    """Writes one line to whatever `sys.stdout` is at the time (tests swap it)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stdout.write(self.format(record) + "\n")
            sys.stdout.flush()
        except Exception:  # never let logging break a request
            self.handleError(record)


def configure_logging(level: int | str = logging.INFO) -> None:
    """Send every log line through the JSON formatter. Safe to call more than once."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, StdoutHandler):
            root.removeHandler(h)
    handler = StdoutHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # Access lines come from RequestLogMiddleware, with route templates only.
    logging.getLogger("uvicorn.access").disabled = True
    pin_third_party()


def pin_third_party() -> None:
    for name in PINNED_WARNING:
        logging.getLogger(name).setLevel(logging.WARNING)


pin_third_party()


# --------------------------------------------------------------------------
# Request log and the global exception handler (ASGI middleware)
# --------------------------------------------------------------------------

_http_log = logging.getLogger("app.http")
_error_log = logging.getLogger("app.error")
_ERROR_BODY = b'{"detail":"Something went wrong."}'


def route_template(scope: Scope) -> str:
    """The matched route's template (`/api/imports/{run_id}`), never the raw path."""
    path = getattr(scope.get("route"), "path", None)
    if isinstance(path, str):
        return path or "/"
    return "(unmatched)"


class RequestLogMiddleware:
    """Logs method, route template, status and duration (and `"demo": true` for a demo
    request, no value). Catches any exception.

    An unhandled exception is logged as its class and `module:line` only, and
    the client gets a plain 500. It is not raised further, so no other layer
    writes its message or traceback.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                status = message["status"]
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            _error_log.error(
                "unhandled error",
                extra={
                    "exc_class": type(exc).__name__,
                    "at": exception_site(exc.__traceback__),
                    "route": route_template(scope),
                },
            )
            status = 500
            if not started:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(_ERROR_BODY)).encode()),
                            (b"cache-control", b"no-store"),
                            (b"x-content-type-options", b"nosniff"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": _ERROR_BODY})
        finally:
            extra = {
                "method": scope.get("method"),
                "route": route_template(scope),
                "status": status,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
            # Set in place by the demo middleware; its ContextVars are reset by now.
            if scope.get("state", {}).get("demo"):
                extra["demo"] = True
            _http_log.info("request", extra=extra)
