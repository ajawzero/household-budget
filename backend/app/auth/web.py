"""Cookie modes and request checks (PLAN 5.1 and 5.2).

`SecurityMiddleware` runs on every request, before routing:
1. A request that carries `Cf-Connecting-Ip` or `Cf-Ray` gets 403.
2. Trusted hosts: BUDGET_ALLOWED_HOSTS. `localhost` and `127.0.0.1` are added
   in dev. In production `127.0.0.1` is accepted for `/healthz` only.
3. Unsafe methods (POST, PUT, PATCH, DELETE) need an `Origin` that exactly
   matches an allowed origin, and `Sec-Fetch-Site: same-origin` when the
   browser sends it. A missing `Origin` is rejected.
4. Security headers on every response, and `Cache-Control: no-store` on /api.

`DemoMiddleware` runs after it and sends a request with the demo cookie to the demo
database (docs/plans/demo-mode.md section 5).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from starlette.requests import HTTPConnection
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.sessions import ABSOLUTE
from app.config import Settings
from app.demo.context import DEMO_TODAY, cleared, guarded, pinned
from app.demo.store import LIFETIME as DEMO_LIFETIME

if TYPE_CHECKING:
    from app.demo.runtime import DemoRuntime

COOKIE_DEV = "budget_session"
COOKIE_PROD = "__Host-budget_session"
DEMO_COOKIE_DEV = "budget_demo"
DEMO_COOKIE_PROD = "__Host-budget_demo"
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
DEV_HOSTS = ("localhost", "127.0.0.1")
HEALTH_PATH = "/healthz"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CLOUDFLARE_HEADERS = (b"cf-connecting-ip", b"cf-ray")
NOT_SIGNED_IN = "Not signed in."
CHANGES_OFF = "Changes are off in demo."
LOGIN_PATH = "/api/auth/login"
# The only changes a demo request may send: leaving demo, and two read-only POSTs.
DEMO_POSTS = frozenset({"/api/auth/logout", "/api/transactions/search", "/api/rules/preview"})

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)


# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------


def cookie_name(settings: Settings) -> str:
    return COOKIE_DEV if settings.dev else COOKIE_PROD


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        cookie_name(settings),
        token,
        max_age=int(ABSOLUTE.total_seconds()),
        path="/",
        secure=not settings.dev,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        cookie_name(settings), path="/", secure=not settings.dev, httponly=True, samesite="lax"
    )


# The demo cookie: its own name (the demo Settings copies `dev`, so `cookie_name` would
# give the real one), 8 hours, and the same attributes, or the browser drops a
# `__Host-` delete.
def demo_cookie_name(settings: Settings) -> str:
    return DEMO_COOKIE_DEV if settings.dev else DEMO_COOKIE_PROD


def set_demo_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        demo_cookie_name(settings),
        token,
        max_age=int(DEMO_LIFETIME.total_seconds()),
        path="/",
        secure=not settings.dev,
        httponly=True,
        samesite="lax",
    )


def clear_demo_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        demo_cookie_name(settings), path="/", secure=not settings.dev, httponly=True, samesite="lax"
    )


# --------------------------------------------------------------------------
# Hosts and origins
# --------------------------------------------------------------------------


def allowed_hosts(settings: Settings) -> frozenset[str]:
    hosts = {h.lower() for h in settings.allowed_hosts}
    if settings.dev:
        hosts.update(DEV_HOSTS)
    return frozenset(hosts)


def allowed_origins(settings: Settings) -> frozenset[str]:
    origins = {f"https://{h.lower()}" for h in settings.allowed_hosts}
    if settings.public_port and settings.public_port != 443:
        origins.update(
            f"https://{h.lower()}:{settings.public_port}" for h in settings.allowed_hosts
        )
    if settings.dev:
        origins.update(DEV_ORIGINS)
    return frozenset(origins)


def _host_name(raw: str) -> str:
    raw = raw.strip().lower()
    if raw.startswith("["):  # [::1]:8000
        return raw.split("]", 1)[0] + "]"
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def _headers(scope: Scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers") or []}


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.dev = settings.dev
        self.hosts = allowed_hosts(settings)
        self.origins = allowed_origins(settings)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        headers = _headers(scope)
        problem = self.check(scope["method"], path, headers)
        no_store = path.startswith("/api/") or path == "/api" or path == HEALTH_PATH

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                hs = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {h for h, _ in SECURITY_HEADERS}
                ]
                hs.extend(SECURITY_HEADERS)
                if no_store:
                    hs = [(k, v) for k, v in hs if k.lower() != b"cache-control"]
                    hs.append((b"cache-control", b"no-store"))
                message = {**message, "headers": hs}
            await send(message)

        if problem is not None:
            status, detail = problem
            body = json.dumps({"detail": detail}).encode()
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send_with_headers)

    def check(self, method: str, path: str, headers: dict[bytes, bytes]) -> tuple[int, str] | None:
        """(status, message) when the request must be refused, else None."""
        if any(h in headers for h in CLOUDFLARE_HEADERS):
            return 403, "Forbidden."
        host = _host_name(headers.get(b"host", b"").decode("latin-1"))
        if host not in self.hosts and not (host == "127.0.0.1" and path == HEALTH_PATH):
            return 400, "Invalid host."
        if method.upper() in UNSAFE_METHODS:
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin not in self.origins:
                return 403, "Request origin not allowed."
            site = headers.get(b"sec-fetch-site")
            if site is not None and site.decode("latin-1").lower() != "same-origin":
                return 403, "Request origin not allowed."
        return None


# --------------------------------------------------------------------------
# Demo routing (docs/plans/demo-mode.md sections 4 and 5)
# --------------------------------------------------------------------------


async def _send_detail(
    send: Send, status: int, detail: str, extra: list[tuple[bytes, bytes]] | None = None
) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send(
        {"type": "http.response.start", "status": status, "headers": headers + (extra or [])}
    )
    await send({"type": "http.response.body", "body": body})


class DemoMiddleware:
    """Sends a request that carries the demo cookie to the demo database.

    Only `/api/` paths, never the login (always real), `/healthz` or static files. The
    cookie's name is enough, whatever its value (a bad one gets 401), so a request with
    both cookies is demo. Then, in order:
    1. Demo not ready (no build checked since this app started, or the file is gone):
       401, both cookies cleared.
    2. A change (any method but GET and HEAD, except the `DEMO_POSTS`): 403.
    3. `scope["state"]["demo"]`, the `connect()` guard on the demo file (read only) and
       the pinned date, for the rest of the request. Any 401 also clears both cookies:
       demo ended, so the password is needed, even in a browser that got both cookies.
    Every other request runs with no guard and no pin (`cleared`).

    Added before `SecurityMiddleware`, so it runs after it: the host and origin checks
    come first, and its answers get the security headers and `no-store`.
    """

    def __init__(self, app: ASGIApp, runtime: DemoRuntime, settings: Settings) -> None:
        self.app = app
        self.runtime = runtime
        self.cookie = demo_cookie_name(settings)
        ended = Response()
        clear_demo_cookie(ended, settings)
        clear_session_cookie(ended, settings)
        self.clear = [(k, v) for k, v in ended.raw_headers if k == b"set-cookie"]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "") if scope["type"] == "http" else ""
        if (
            not path.startswith("/api/")
            or path == LOGIN_PATH
            or self.cookie not in HTTPConnection(scope).cookies
        ):
            with cleared():
                await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["demo"] = True  # in place: the request log reads it
        ready = self.runtime.ready
        if ready is None or not ready.path.is_file():
            await _send_detail(send, 401, NOT_SIGNED_IN, self.clear)
            return
        if not self.allows(scope["method"], path):
            await _send_detail(send, 403, CHANGES_OFF)
            return

        async def send_demo(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 401:
                message = {**message, "headers": [*message.get("headers", []), *self.clear]}
            await send(message)

        with guarded(ready.path), pinned(DEMO_TODAY):
            await self.app(scope, receive, send_demo)

    @staticmethod
    def allows(method: str, path: str) -> bool:
        method = method.upper()
        return method in ("GET", "HEAD") or (method == "POST" and path in DEMO_POSTS)
