"""Demo mode sessions and routing (demo plan sections 4 to 6): start, leave, the demo
cookie, the middleware's refusals, the route-level refusals, `/me` and the AI status."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from api_helpers import (
    ADMIN,
    ADMIN_PW,
    PROD_ORIGIN,
    PROD_URL,
    FakeClock,
    add_users,
    app_for,
    login,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api import ai as ai_api
from app.api import demo as demo_api
from app.api import health
from app.api.deps import app_settings, refuse_in_demo
from app.auth import clock, web
from app.auth.sessions import create_session, lookup_session, token_hash
from app.auth.users import find_user
from app.config import Settings, get_settings
from app.db.connect import connect, write_connection
from app.demo import build, store
from app.demo.context import DemoGuardError, current_guard, guarded, in_demo, pinned, pinned_date
from app.main import create_app, walk_routes

H = {"Origin": PROD_ORIGIN}
REAL = "__Host-budget_session"
DEMO = "__Host-budget_demo"
CHANGES_OFF = {"detail": "Changes are off in demo."}
NOT_SIGNED_IN = {"detail": "Not signed in."}
UNAVAILABLE = {"detail": "Demo is not available right now."}
# Every route that refuses demo itself, on top of the middleware.
REFUSING = {
    ("PUT", "/api/settings/ai"),
    ("POST", "/api/settings/ai/clear-block"),
    ("POST", "/api/ask"),
    ("POST", "/api/ai/review/run"),
    ("POST", "/api/category-suggestions/run"),
    ("POST", "/api/trip-plans"),
    ("POST", "/api/trip-plans/{plan_id}/follow-up"),
    ("POST", "/api/demo/start"),
}
PLAN_ID = "a" * 32


def start(client: TestClient) -> httpx.Response:
    return client.post("/api/demo/start", headers=H)


def set_cookies(r: httpx.Response) -> dict[str, str]:
    """Each Set-Cookie header by cookie name."""
    return {h.split("=", 1)[0]: h for h in r.headers.get_list("set-cookie")}


def plant(client: TestClient, name: str, value: str) -> None:
    """A cookie as the browser holds it (host-only), so a delete from the app removes it."""
    client.cookies.set(name, value, domain=httpx.URL(PROD_URL).host)


def cleared(header: str) -> bool:
    return "max-age=0" in header.lower()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ro(db_file: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{db_file.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _demo_sessions(db_file: Path) -> int:
    conn = _ro(db_file)
    try:
        return conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


def _real_rows(settings: Settings, token: str | None) -> int:
    with connect(settings.db_path) as conn:
        return conn.execute(
            "SELECT count(*) FROM sessions WHERE token_hash = ?", (token_hash(token or ""),)
        ).fetchone()[0]


def _count_builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real_build = build.build

    def counting(real: Settings) -> None:
        calls.append(1)
        real_build(real)

    monkeypatch.setattr(build, "build", counting)
    return calls


def _api_routes(routes: object) -> Iterator[APIRoute]:
    for r in routes:  # type: ignore[attr-defined]
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _api_routes(inner.routes)
        elif isinstance(r, APIRoute):
            yield r


@pytest.fixture
def demo(admin: TestClient) -> TestClient:
    """The admin's browser, switched to demo."""
    assert start(admin).status_code == 204
    return admin


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fc = FakeClock()
    monkeypatch.setattr(clock, "now", fc)
    return fc


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------


def test_start_switches_this_browser_to_demo(
    admin: TestClient, prod_app: FastAPI, settings: Settings
) -> None:
    real_token = admin.cookies.get(REAL)
    assert admin.get("/api/auth/me").json()["demo_available"] is True
    r = start(admin)
    assert r.status_code == 204
    sent = set_cookies(r)
    assert set(sent) == {REAL, DEMO}
    assert cleared(sent[REAL])
    lower = sent[DEMO].lower()
    for flag in ("secure", "httponly", "samesite=lax", "path=/"):
        assert flag in lower, flag
    assert "domain=" not in lower
    assert store.LIFETIME == timedelta(hours=8)
    assert f"max-age={int(store.LIFETIME.total_seconds())};" in lower
    token = sent[DEMO].split("=", 1)[1].split(";", 1)[0]
    assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) == 32
    assert admin.cookies.get(REAL) is None
    assert _real_rows(settings, real_token) == 0
    assert _demo_sessions(prod_app.state.demo.db_file) == 1
    assert admin.get("/api/auth/me").json() == {
        "username": "sample",
        "display_name": "Alex",
        "role": "member",
        "demo": True,
        "demo_available": True,
        "household_today": "2026-10-20",
    }


def test_start_needs_an_admin(member: TestClient) -> None:
    r = start(member)
    assert r.status_code == 403 and "set-cookie" not in r.headers
    assert member.get("/api/auth/me").json()["demo"] is False


def test_start_is_503_when_demo_is_off(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    add_users(settings)
    app = app_for(monkeypatch, dev=False, BUDGET_DEMO_RUNS=str(tmp_path / "no-runs"))
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        assert c.get("/api/auth/me").json()["demo_available"] is False
        r = start(c)
        assert r.status_code == 503 and r.json() == UNAVAILABLE
        assert "set-cookie" not in r.headers
        me = c.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["demo"] is False
    assert not (settings.demo_dir / "demo.db").exists()


def test_a_failed_build_keeps_the_real_session(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(real: Settings) -> None:
        raise RuntimeError("build failed")

    monkeypatch.setattr(build, "build", broken)
    r = start(admin)
    assert r.status_code == 503 and r.json() == UNAVAILABLE
    assert "set-cookie" not in r.headers
    assert admin.get("/api/auth/me").json()["demo"] is False


def test_a_failed_real_delete_keeps_the_real_session(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def broken(conn: sqlite3.Connection, token: str | None) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(demo_api, "delete_session", broken)
    real_token = admin.cookies.get(REAL)
    r = start(admin)
    assert r.status_code == 500 and "set-cookie" not in r.headers
    assert _real_rows(settings, real_token) == 1
    assert admin.get("/api/auth/me").json()["demo"] is False


def test_a_failed_demo_session_signs_the_browser_out(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch, settings: Settings, prod_app: FastAPI
) -> None:
    def broken(db_file: Path, user_id: int = store.DEMO_USER_ID) -> str:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "create_session", broken)
    real_token = admin.cookies.get(REAL)
    r = start(admin)
    assert r.status_code == 503 and r.json() == UNAVAILABLE
    sent = set_cookies(r)
    assert set(sent) == {REAL} and cleared(sent[REAL])
    assert _real_rows(settings, real_token) == 0
    assert _demo_sessions(prod_app.state.demo.db_file) == 0
    assert admin.get("/api/auth/me").status_code == 401


def test_two_starts_at_once_build_once(
    prod_app: FastAPI, prod: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_builds(monkeypatch)
    other = TestClient(prod_app, base_url=PROD_URL)
    clients = {"a": prod, "b": other}
    for c in clients.values():
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
    barrier = threading.Barrier(2)
    status: dict[str, int] = {}

    def go(name: str) -> None:
        barrier.wait()
        status[name] = start(clients[name]).status_code

    threads = [threading.Thread(target=go, args=(n,)) for n in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert status == {"a": 204, "b": 204}
    assert calls == [1]
    for c in clients.values():
        me = c.get("/api/auth/me").json()
        assert me["demo"] is True and me["username"] == "sample"
    assert _demo_sessions(prod_app.state.demo.db_file) == 2


def test_an_empty_premade_file_is_rebuilt(
    admin: TestClient, prod_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_builds(monkeypatch)
    db_file = prod_app.state.demo.db_file
    db_file.parent.mkdir(mode=0o700, exist_ok=True)
    db_file.write_bytes(b"")
    assert start(admin).status_code == 204
    assert calls == [1] and build.is_ready(db_file)
    assert admin.get("/api/auth/me").json()["demo"] is True


def test_after_a_restart_the_old_demo_cookie_is_401_and_cleared(
    demo: TestClient, prod_app: FastAPI
) -> None:
    token = demo.cookies.get(DEMO)
    assert token
    restarted = create_app(prod_app.state.settings, start_scheduler=False)
    with TestClient(restarted, base_url=PROD_URL) as c:
        assert not prod_app.state.demo.db_file.exists()
        for method, path in (("GET", "/api/auth/me"), ("POST", "/api/auth/logout")):
            plant(c, DEMO, token)
            r = c.request(method, path, headers=H)
            assert r.status_code == 401 and r.json() == NOT_SIGNED_IN, path
            assert cleared(set_cookies(r)[DEMO]), path
            assert c.cookies.get(DEMO) is None


# --------------------------------------------------------------------------
# Sessions and cookies
# --------------------------------------------------------------------------


def test_tokens_never_cross_databases(admin: TestClient, prod_app: FastAPI) -> None:
    real_token = admin.cookies.get(REAL)
    runtime = prod_app.state.demo
    runtime.ensure_ready()
    demo_token = store.create_session(runtime.db_file)

    as_real = TestClient(prod_app, base_url=PROD_URL)
    plant(as_real, REAL, demo_token)
    r = as_real.get("/api/auth/me")
    assert r.status_code == 401 and "set-cookie" not in r.headers

    as_demo = TestClient(prod_app, base_url=PROD_URL)
    plant(as_demo, DEMO, real_token)
    r = as_demo.get("/api/auth/me")
    assert r.status_code == 401 and r.json() == NOT_SIGNED_IN
    assert cleared(set_cookies(r)[DEMO])

    assert admin.get("/api/auth/me").json()["username"] == ADMIN


def test_both_cookies_mean_demo(admin: TestClient, prod_app: FastAPI) -> None:
    runtime = prod_app.state.demo
    runtime.ensure_ready()
    plant(admin, DEMO, store.create_session(runtime.db_file))
    me = admin.get("/api/auth/me").json()
    assert me["demo"] is True and me["username"] == "sample"


def test_a_bad_demo_cookie_is_demo_and_401(admin: TestClient, prod_app: FastAPI) -> None:
    prod_app.state.demo.ensure_ready()
    for value in ("", "not-a-token"):
        plant(admin, DEMO, value)
        r = admin.get("/api/auth/me")
        assert r.status_code == 401
        sent = set_cookies(r)
        assert cleared(sent[DEMO]) and cleared(sent[REAL])
    # Demo ended, so the real cookie goes too and the password is needed. A browser
    # only holds both after a start and a login race in two tabs.
    assert admin.cookies.get(REAL) is None
    assert admin.get("/api/auth/me").status_code == 401


def test_a_demo_file_removed_while_running_is_401_and_cleared(
    demo: TestClient, prod_app: FastAPI
) -> None:
    token = demo.cookies.get(DEMO)
    assert token
    prod_app.state.demo.db_file.unlink()
    for method, path in (("GET", "/api/auth/me"), ("POST", "/api/auth/logout")):
        plant(demo, DEMO, token)
        r = demo.request(method, path, headers=H)
        assert r.status_code == 401 and r.json() == NOT_SIGNED_IN, path
        assert cleared(set_cookies(r)[DEMO]), path
        assert demo.cookies.get(DEMO) is None


def test_login_clears_the_demo_cookie_and_is_real(demo: TestClient) -> None:
    r = login(demo, ADMIN, ADMIN_PW, PROD_ORIGIN)
    assert r.status_code == 200
    headers = r.headers.get_list("set-cookie")
    assert headers[0].startswith(f"{REAL}=")
    assert cleared(set_cookies(r)[DEMO])
    assert demo.cookies.get(DEMO) is None
    me = demo.get("/api/auth/me").json()
    assert me["username"] == ADMIN and me["demo"] is False


def test_login_with_a_stale_demo_cookie_works(prod: TestClient) -> None:
    plant(prod, DEMO, "stale")  # demo not ready: everything else is 401
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
    assert prod.get("/api/auth/me").json()["demo"] is False


def test_leaving_demo_deletes_the_demo_session(
    demo: TestClient, prod_app: FastAPI, settings: Settings
) -> None:
    db_file = prod_app.state.demo.db_file
    assert _demo_sessions(db_file) == 1
    r = demo.post("/api/auth/logout", headers=H)
    assert r.status_code == 204
    sent = set_cookies(r)
    assert cleared(sent[DEMO]) and cleared(sent[REAL])
    assert _demo_sessions(db_file) == 0
    conn = _ro(db_file)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()
    assert sorted(p.name for p in db_file.parent.iterdir()) == ["demo.db"]
    assert build.is_ready(db_file)
    assert demo.get("/api/auth/me").status_code == 401


def test_a_demo_session_is_never_touched_and_ends_after_eight_hours(
    fake_clock: FakeClock, admin: TestClient, prod_app: FastAPI
) -> None:
    assert start(admin).status_code == 204
    db_file = prod_app.state.demo.db_file
    before = _sha(db_file)
    fake_clock.advance(minutes=6)  # past the idle touch
    assert admin.get("/api/auth/me").status_code == 200
    assert _sha(db_file) == before
    fake_clock.advance(hours=7, minutes=53, seconds=59)  # 7:59:59 since the start
    assert admin.get("/api/auth/me").status_code == 200
    fake_clock.advance(seconds=2)
    r = admin.get("/api/auth/me")
    assert r.status_code == 401 and r.json() == NOT_SIGNED_IN
    assert cleared(set_cookies(r)[DEMO])
    assert _sha(db_file) == before  # no purge on the read-only side
    assert _demo_sessions(db_file) == 1


def test_lookup_without_touch_writes_nothing(
    settings: Settings, fake_clock: FakeClock, prod_app: FastAPI
) -> None:
    with write_connection(settings.db_path) as conn:
        found = find_user(conn, ADMIN)
        assert found is not None
        user = found[0]
        token = create_session(conn, user.id)
    conn = connect(settings.db_path)
    try:
        seen = conn.execute("SELECT last_seen_at FROM sessions").fetchone()[0]
        fake_clock.advance(minutes=6)
        assert lookup_session(conn, token, touch=False) == user
        assert conn.execute("SELECT last_seen_at FROM sessions").fetchone()[0] == seen
        fake_clock.advance(days=40)
        assert lookup_session(conn, token, touch=False) is None
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert lookup_session(conn, token) is None
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def test_healthz_with_a_demo_cookie_stays_real(
    demo: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    real_connect = health.connect

    def spy(path: Path, **kw: bool) -> sqlite3.Connection:
        seen.append((Path(path), in_demo(), pinned_date()))
        return real_connect(path, **kw)

    monkeypatch.setattr(health, "connect", spy)
    assert demo.cookies.get(DEMO)
    r = demo.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True, "db": "ok"}
    assert seen == [(settings.db_path, False, None)]


def test_a_request_that_is_not_demo_runs_with_no_guard_or_pin(prod_app: FastAPI) -> None:
    """uvicorn can start the next request on a keep-alive connection from a copy of a
    demo request's context. The middleware clears it for every request that is not demo."""
    seen: list[tuple[object, object]] = []

    async def inner(scope: dict, receive: object, send: object) -> None:
        seen.append((current_guard(), pinned_date()))

    middleware = web.DemoMiddleware(inner, prod_app.state.demo, prod_app.state.settings)
    demo_cookie = [(b"cookie", f"{DEMO}=x".encode())]
    scopes = [
        {"type": "http", "method": "GET", "path": "/api/auth/me", "headers": []},
        {"type": "http", "method": "POST", "path": "/api/auth/login", "headers": demo_cookie},
        {"type": "http", "method": "GET", "path": "/", "headers": demo_cookie},
        {"type": "lifespan"},
    ]
    with guarded(prod_app.state.demo.db_file), pinned():  # the context a demo request left
        for scope in scopes:
            asyncio.run(middleware(scope, None, None))  # the task starts from a copy
        assert current_guard() is not None and pinned_date() is not None
    assert seen == [(None, None)] * len(scopes)


def test_app_settings_in_demo_never_fall_back(prod_app: FastAPI) -> None:
    demo_request = Request({"type": "http", "app": prod_app, "state": {"demo": True}})
    assert app_settings(demo_request) is prod_app.state.demo.settings
    assert app_settings(demo_request).db_path == prod_app.state.demo.db_file
    assert app_settings(demo_request).anthropic_api_key is None
    real_request = Request({"type": "http", "app": prod_app, "state": {}})
    assert app_settings(real_request) is prod_app.state.settings
    bare = Request({"type": "http", "app": FastAPI(), "state": {"demo": True}})
    with pytest.raises(DemoGuardError):
        app_settings(bare)


def test_every_change_is_refused_in_demo(demo: TestClient, prod_app: FastAPI) -> None:
    allowed = {("POST", p) for p in web.DEMO_POSTS}
    checked = 0
    for path, methods, _ in walk_routes(prod_app):
        for method in methods - {"GET", "HEAD"}:
            if (method, path) in allowed or path == "/api/auth/login":
                continue
            url = path.replace("{", "").replace("}", "")  # refused before routing
            r = demo.request(method, url, json={}, headers=H)
            assert r.status_code == 403 and r.json() == CHANGES_OFF, (method, path)
            checked += 1
    assert checked >= 40
    r = demo.request("OPTIONS", "/api/accounts", headers=H)
    assert r.status_code == 403 and r.json() == CHANGES_OFF
    assert demo.post("/api/transactions/search", json={}, headers=H).status_code == 200
    body = {"match_kind": "description_contains", "pattern": "MARKET"}
    assert demo.post("/api/rules/preview", json=body, headers=H).status_code == 200
    assert demo.get("/api/auth/me").json()["demo"] is True


def test_security_checks_run_before_the_demo_checks(demo: TestClient) -> None:
    r = demo.post("/api/auth/password", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "Request origin not allowed."}
    r = demo.post("/api/auth/password", json={}, headers=H)
    assert r.status_code == 403 and r.json() == CHANGES_OFF
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"


# --------------------------------------------------------------------------
# Route-level refusals and the AI status
# --------------------------------------------------------------------------


def test_the_refusing_routes_carry_the_dependency(prod_app: FastAPI) -> None:
    found = {
        (method, route.path)
        for route in _api_routes(prod_app.routes)
        if any(d.call is refuse_in_demo for d in route.dependant.dependencies)
        for method in route.methods
    }
    assert found == REFUSING


def test_the_dependency_refuses_without_the_middleware(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        refuse_in_demo(Request({"type": "http", "state": {"demo": True}}))
    assert exc.value.status_code == 403 and exc.value.detail == CHANGES_OFF["detail"]
    with guarded(tmp_path / "demo.db"), pytest.raises(HTTPException):
        refuse_in_demo(Request({"type": "http", "state": {}}))
    refuse_in_demo(Request({"type": "http", "state": {}}))


def test_ai_routes_refuse_demo_before_the_busy_slot(
    demo: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for uid in (1, store.DEMO_USER_ID):  # the real admin's id and the demo user's
        monkeypatch.setitem(ai_api._asking, uid, time.monotonic())
    asking = dict(ai_api._asking)
    tasks = {k: run.task for k, run in ai_api._runs.items()}
    r = demo.post("/api/ask", json={"question": "Hi"}, headers=H)
    assert r.status_code == 403 and r.json() == CHANGES_OFF

    # With the middleware's refusal off, each route still refuses, before its body runs.
    monkeypatch.setattr(web.DemoMiddleware, "allows", staticmethod(lambda method, path: True))
    for method, path in sorted(REFUSING):
        body = {"question": "Hi"} if path == "/api/ask" else {}  # a valid ask would be 409
        r = demo.request(method, path.replace("{plan_id}", PLAN_ID), json=body, headers=H)
        assert r.status_code == 403 and r.json() == CHANGES_OFF, (method, path)
    assert ai_api._asking == asking
    assert {k: run.task for k, run in ai_api._runs.items()} == tasks
    assert demo.get("/api/auth/me").json()["demo"] is True


class _RealRun:
    """A review or suggestions run of the real household, going on in this process."""

    last = {"status": "done", "message": "canary-run-message", "finished_at": "2026-10-28"}

    def running(self) -> bool:
        return True


def test_ai_status_in_demo_shows_no_real_run_and_no_key(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "fake-canary-key-for-demo-status"
    monkeypatch.setenv("ANTHROPIC_API_KEY", canary)
    get_settings.cache_clear()
    add_users(settings)
    app = app_for(monkeypatch, dev=False)
    for kind in ("review", "suggestions"):
        monkeypatch.setitem(ai_api._runs, kind, _RealRun())
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        real = c.get("/api/ai/status").json()
        assert real["key_present"] is True and real["running_review"] is True
        assert real["last_review_result"]["message"] == "canary-run-message"
        assert start(c).status_code == 204
        r = c.get("/api/ai/status")
        assert r.status_code == 200
        st = r.json()
        assert st["key_present"] is False and st["enabled"] is False
        assert st["available"] is False
        assert st["running_review"] is False and st["running_suggestions"] is False
        assert st["last_review_result"] is None and st["last_suggestions_result"] is None
        assert canary not in r.text and "canary-run" not in r.text
