"""Demo mode end to end (demo plan section 9): the canary walk over every GET, the write
walk over every change, a route that opens `settings.db_path` itself, jobs during a demo
request, and the clock of a demo and a real request at the same time.

The "real" database is the sample import with canary names and amounts
(`api_helpers.plant_canaries`), so a demo URL also names a real row: a leak shows as
"Austin", the canary amount or a 200 where demo has no row.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import itertools
import re
import shutil
import threading
import time
import urllib.error
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from ai_fakes import FakeClient, parsed
from api_helpers import (
    CANARY,
    CANARY_CENTS,
    CANARY_ID,
    MEMBER,
    MEMBER_PW,
    PROD_ORIGIN,
    PROD_URL,
    SAMPLES,
    TODAY,
    import_samples,
    login,
    plant_canaries,
)
from conftest import SAMPLE_RUNS
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from test_ai_features import good_review
from test_trip_planner import enable, research, written
from test_trip_planner import start as start_plan

from app import config
from app.ai import client as ai_client
from app.ai import planner, review
from app.api.deps import app_settings, require_admin
from app.auth import web
from app.config import HOUSEHOLD_TZ, Settings, get_settings, household_today
from app.db.connect import connect, read_connection
from app.demo.context import current_guard, in_demo, pinned_date
from app.demo.store import DEMO_USER_ID
from app.jobs.scheduler import Job, Scheduler, ai_review_job, import_poll_job
from app.main import walk_routes
from app.market import http
from app.services import networth
from app.services.fingerprint import fingerprint
from app.valuations import hpi

H = {"Origin": PROD_ORIGIN}
CHANGES_OFF = {"detail": "Changes are off in demo."}
ADMIN_ONLY_TEXT = {"detail": "Only an admin can do this."}
DEMO_TODAY_ISO = "2026-10-20"
# The demo user is a member: these GETs are 403 (the set is checked against the routes).
ADMIN_ONLY = {"/api/backups", "/api/settings/ai", "/api/ai/usage"}
# The samples have no Ask thread and no trip plan; the real database does (CANARY_ID).
NO_SAMPLE_ROW = {"/api/ask/threads/{thread_id}", "/api/trip-plans/{plan_id}"}
TXN_KEY = "<txn_key>"  # read from the database at run time

ACCOUNTS = (
    "harbor-checking",
    "harbor-savings",
    "maple-joint-checking",
    "bramble-store-card",
    "cobalt-card",
    "old-store-card",
    "orchard-card",
    "summit-card",
    "kids-529",
    "lakeview-brokerage",
    "pinecrest-mortgage",
    "van-loan",
    "family-home",
    "alex-401k",
    "camper-van",
    "family-car",
)
CARDS = ("bramble-store-card", "cobalt-card", "old-store-card", "orchard-card", "summit-card")
RANGES = networth.RANGE_KEYS
SERIES = (
    "networth",
    "cash",
    "credit_cards",
    "investments",
    "retirement",
    "real_estate",
    "vehicles",
    "loans",
)
SPAN = "from=2026-01&to=2026-09"

# Path parameters by (route, parameter), never by name alone: `{key}` is a category, a
# trip, a program and a benefit. A route parameter with no entry fails the walk.
PARAMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("/api/imports/{run_id}", "run_id"): SAMPLE_RUNS,
    ("/api/accounts/{account_key}", "account_key"): ACCOUNTS,
    ("/api/transactions/{txn_key}/events", "txn_key"): (TXN_KEY,),
    ("/api/cashflow/category/{key}", "key"): (
        "groceries",
        "dining",
        "transfer",
        "paycheck",
        "mortgage",
    ),
    ("/api/utilities/{utility_key}", "utility_key"): ("town-water", "city-power"),
    ("/api/rewards/programs/{key}/balances", "key"): (
        "harbor-miles",
        "summit-points",
        "lantern-hotels",
        "pine-cash-back",
        "shared-vacation-club",
    ),
    ("/api/rewards/benefits/{key}/terms", "key"): (
        "dining-credit",
        "airline-fee-credit",
        "streaming-credit",
        "travel-credit",
        "wireless-credit",
    ),
    ("/api/trips/{key}", "key"): ("beach-week", "lake-weekend", "ski-weekend"),
    ("/api/ask/threads/{thread_id}", "thread_id"): (CANARY_ID,),
    ("/api/trip-plans/{plan_id}", "plan_id"): (CANARY_ID,),
}
# Query variants by route; "" is the plain URL. A route not listed gets the plain URL.
QUERIES: dict[str, tuple[str, ...]] = {
    "/api/accounts": ("", *(f"range={r}" for r in RANGES)),
    "/api/accounts/{account_key}": ("", "range=ALL"),
    "/api/networth/series": ("", "range=ALL", *(f"series={s}" for s in SERIES)),
    "/api/dashboard": ("", "month=2026-09", "month=2026-10"),
    "/api/transactions": (
        "",
        "month=2026-09",
        "account=summit-card",
        "owner=alex",
        "category=groceries",
        "limit=5",
    ),
    "/api/transactions/summary": ("", "accounts=summit-card&months=2026-09"),
    "/api/checks": ("", "run_id=2026-10-12"),
    "/api/cashflow": ("", SPAN),
    "/api/cashflow/breakdown": ("", "by=group", "by=merchant", "flow=income", SPAN),
    "/api/cashflow/category/{key}": ("", SPAN),
    "/api/income": ("", SPAN),
    "/api/budgets": ("", "month=2026-09"),
    "/api/recurring": ("", "view=all", "month=2026-09"),
    "/api/recurring/calendar": ("", "month=2026-09"),
    "/api/flags": ("", "include_archived=true"),
    "/api/statements/coverage": ("", SPAN),
    "/api/investments/holdings": ("", "group_by=account", "group_by=class"),
    "/api/investments/history": ("", *(f"range={r}" for r in networth.INVESTMENT_RANGE_KEYS)),
    "/api/contributions": ("", "year=2025", "year=2026"),
    "/api/utilities/{utility_key}": ("", SPAN),
    "/api/rewards/benefit-files/template": tuple(f"account_key={c}" for c in CARDS),
}
# The change routes' path parameters. The middleware refuses before routing, but a real
# URL keeps the walk honest if it ever lets one through. `{key}` goes by the route.
WRITE_PARAMS = {
    "run_id": "2026-10-12",
    "txn_key": "0" * 64,
    "rule_id": "1",
    "person_key": "alex",
    "reading_id": "1",
    "balance_id": "1",
    "start": "2026-10-01",
    "plan_id": CANARY_ID,
    "suggestion_id": "1",
}
WRITE_KEYS = {
    "/api/vehicles/": "family-car",
    "/api/rewards/programs/": "harbor-miles",
    "/api/rewards/benefits/": "dining-credit",
}
_PARAM = re.compile(r"\{([a-z_]+)\}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_files(s: Settings) -> dict[str, str | None]:
    """The real database file and its WAL (the shared-memory file changes on reads)."""
    out = {}
    for suffix in ("", "-wal"):
        path = Path(f"{s.db_path}{suffix}")
        out[suffix] = _sha(path) if path.exists() else None
    return out


def _real_state(s: Settings) -> tuple[str, int, int]:
    with read_connection(s.db_path) as conn:
        counts = [
            conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("sessions", "events")
        ]
        return fingerprint(conn), *counts


def _api_routes(routes: object) -> Iterator[APIRoute]:
    for r in routes:  # type: ignore[attr-defined]
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _api_routes(inner.routes)
        elif isinstance(r, APIRoute):
            yield r


def _needs_admin(dependant: object) -> bool:
    return any(
        d.call is require_admin or _needs_admin(d)
        for d in dependant.dependencies  # type: ignore[attr-defined]
    )


def get_urls(app: FastAPI, txn_key: str) -> list[tuple[str, str]]:
    """(route, URL) for every API GET: each path parameter from PARAMS, each query variant."""
    routes = [path for path, methods, _ in walk_routes(app) if "GET" in methods]
    api = [p for p in routes if p.startswith("/api/")]
    assert {path for path, _ in PARAMS} <= set(api) and set(QUERIES) <= set(api)
    out = []
    for route in api:
        names = _PARAM.findall(route)
        missing = [n for n in names if (route, n) not in PARAMS]
        assert not missing, (route, missing)
        for values in itertools.product(*(PARAMS[(route, n)] for n in names)):
            path = route
            for name, value in zip(names, values, strict=True):
                path = path.replace(f"{{{name}}}", txn_key if value == TXN_KEY else value)
            for query in QUERIES.get(route, ("",)):
                out.append((route, f"{path}?{query}" if query else path))
    return out


def write_url(path: str) -> str:
    def value(m: re.Match[str]) -> str:
        name = m.group(1)
        if name == "key":
            return next(v for prefix, v in WRITE_KEYS.items() if path.startswith(prefix))
        return WRITE_PARAMS[name]

    return _PARAM.sub(value, path)


class _HeldPlan(FakeClient):
    """The fake client; a stream (the planner's research call) waits for `gate`."""

    def __init__(self, script: list) -> None:
        super().__init__(script)
        self.entered = threading.Event()
        self.gate = threading.Event()
        stream = self.messages.stream

        @contextlib.asynccontextmanager
        async def held(**params):  # noqa: ANN202
            self.entered.set()
            while not self.gate.is_set():
                await asyncio.sleep(0.01)
            async with stream(**params) as s:
                yield s

        self.messages.stream = held


class _Outbound:
    """Stands in for the opener and the index download: records, never connects."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def open(self, req, timeout=None):  # noqa: ANN001, ANN201
        self.urls.append(req.full_url)
        raise urllib.error.URLError("recorded")

    def lines(self, url, headers, timeout):  # noqa: ANN001, ANN201
        self.urls.append(url)
        raise hpi.FetchError("network")


@dataclass
class Probes:
    """Test-only GETs: the database a route gets, and a request held open."""

    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake key, set before the app is built (Settings are frozen). Nothing reaches Anthropic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-canary-key-for-demo-routes")
    get_settings.cache_clear()


@pytest.fixture
def fake() -> Iterator[FakeClient]:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


@pytest.fixture
def probes(prod_app: FastAPI) -> Probes:
    """Added before the client starts. Not signed in: the demo cookie alone routes them."""
    p = Probes()

    @prod_app.get("/api/test-probe/db")
    def probe_db(request: Request) -> dict:
        conn = connect(app_settings(request).db_path)
        try:
            files = [Path(r["file"]).name for r in conn.execute("PRAGMA database_list")]
            query_only = conn.execute("PRAGMA query_only").fetchone()[0]
        finally:
            conn.close()
        return {"files": files, "query_only": query_only}

    @prod_app.get("/api/test-probe/default-db")
    def probe_default_db() -> dict:
        connect().close()
        return {}

    @prod_app.get("/api/test-probe/hold")
    def probe_hold() -> dict:
        p.entered.set()
        assert p.release.wait(30)
        return {"today": household_today().isoformat(), "demo": in_demo()}

    return p


@pytest.fixture
def demo(admin: TestClient) -> TestClient:
    """The admin's browser, switched to demo."""
    assert admin.post("/api/demo/start", headers=H).status_code == 204
    return admin


@contextlib.contextmanager
def held_open(client: TestClient, probes: Probes) -> Iterator[dict[str, httpx.Response]]:
    """A demo request held open in another thread; its response is in the dict after."""
    out: dict[str, httpx.Response] = {}
    t = threading.Thread(target=lambda: out.update(r=client.get("/api/test-probe/hold")))
    t.start()
    try:
        assert probes.entered.wait(10)
        yield out
    finally:
        probes.release.set()
        t.join(30)


# --------------------------------------------------------------------------
# The canary walk
# --------------------------------------------------------------------------


def test_the_canary_walk(
    key: None,
    use_private_rules: Settings,
    prod_app: FastAPI,
    admin: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The review and the planner check their dates against today.
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    s = prod_app.state.settings
    import_samples(s)
    plant_canaries(s, DEMO_USER_ID)
    enable()
    with read_connection(s.db_path) as conn:
        txn_key = conn.execute(
            """SELECT txn_key FROM transactions WHERE account_key = 'summit-card'
               ORDER BY txn_date, txn_key LIMIT 1"""
        ).fetchone()[0]
    urls = get_urls(prod_app, txn_key)
    assert len(urls) >= 150
    admin_gets = {
        r.path
        for r in _api_routes(prod_app.routes)
        if "GET" in r.methods and _needs_admin(r.dependant)
    }
    assert admin_gets == ADMIN_ONLY

    held = _HeldPlan(
        [
            parsed(good_review().model_copy(update={"summary": f"{CANARY} was calm."})),
            research(),
            written(),
        ]
    )
    ai_client.set_client_factory(lambda _s: held)
    timer = threading.Timer(120, held.gate.set)  # never hangs
    timer.start()
    try:
        # Warm every process-wide cache as the real admin: a finished review (`_runs`), a
        # running plan, and every GET once. Each shows the canaries.
        assert admin.post("/api/ai/review/run", headers=H).json()["status"] == "ready"
        plan_id = start_plan(admin)
        assert held.entered.wait(10)
        leaky: set[str] = set()
        for route, url in urls:
            r = admin.get(url)
            want = 404 if route == "/api/ask/threads/{thread_id}" else 200  # another user's
            assert r.status_code == want, (url, r.status_code, r.text[:200])
            if CANARY in r.text:
                leaky.add(route)
        assert {
            "/api/accounts",
            "/api/meta",
            "/api/dashboard",
            "/api/transactions",
            "/api/trips",
            "/api/utilities",
            "/api/rewards",
            "/api/valuations",
            "/api/ask/threads",
            "/api/trip-plans",
            "/api/trip-plans/{plan_id}",
        } <= leaky
        assert str(CANARY_CENTS) in admin.get("/api/transactions?account=harbor-checking").text
        assert admin.get("/api/trip-plans").json()["running_plan_id"] == plan_id

        outbound = _Outbound()
        monkeypatch.setattr(http, "_OPENER", outbound)
        monkeypatch.setattr(hpi, "urllib_lines", outbound.lines)
        assert admin.post("/api/demo/start", headers=H).status_code == 204
        # After the start (it deletes the real session). No real connection is opened
        # again until the walk is done: closing the last one checkpoints the WAL.
        state = _real_state(s)
        files = _real_files(s)
        demo_file = prod_app.state.demo.db_file
        demo_sha = _sha(demo_file)
        calls = len(held.requests)

        for route, url in urls:
            r = admin.get(url)
            if route in ADMIN_ONLY:
                assert (r.status_code, r.json()) == (403, ADMIN_ONLY_TEXT), url
            elif route in NO_SAMPLE_ROW:
                assert r.status_code == 404, url
            else:
                assert r.status_code == 200, (url, r.status_code, r.text[:200])
            for canary in (CANARY, str(CANARY_CENTS), plan_id):
                assert canary not in r.text, (url, canary)

        assert _real_files(s) == files
        assert _sha(demo_file) == demo_sha
        assert len(held.requests) == calls
        assert outbound.urls == []
        assert _real_state(s) == state
        me = admin.get("/api/auth/me").json()
        assert me["demo"] is True and me["username"] == "sample"
        assert admin.get("/api/meta").json()["location_label"] == "Sample Town"
        assert admin.get("/api/trip-plans").json()["running_plan_id"] is None
    finally:
        held.gate.set()
        timer.cancel()
        end = time.monotonic() + 15
        while planner.running_plan() is not None and time.monotonic() < end:
            time.sleep(0.02)
        ai_client.set_client_factory(None)
    assert planner.running_plan() is None


# --------------------------------------------------------------------------
# The write walk
# --------------------------------------------------------------------------


def test_every_change_is_refused_and_the_files_stay(
    demo: TestClient, prod_app: FastAPI, settings: Settings
) -> None:
    allowed = {("POST", p) for p in web.DEMO_POSTS}
    bodies = {
        "/api/transactions/search": {},
        "/api/rules/preview": {"match_kind": "description_contains", "pattern": "MARKET"},
    }
    demo_file = prod_app.state.demo.db_file
    demo_sha = _sha(demo_file)
    files = _real_files(settings)
    refused = 0
    for path, methods, _ in walk_routes(prod_app):
        for method in sorted(methods - {"GET", "HEAD"}):
            if path == web.LOGIN_PATH or path == "/api/auth/logout":
                continue  # the login is never routed; leaving goes last
            r = demo.request(method, write_url(path), json=bodies.get(path, {}), headers=H)
            if (method, path) in allowed:
                assert r.status_code == 200, (method, path, r.text[:200])
            else:
                assert (r.status_code, r.json()) == (403, CHANGES_OFF), (method, path)
                refused += 1
    assert refused >= 45
    assert _sha(demo_file) == demo_sha
    assert _real_files(settings) == files

    r = demo.post("/api/auth/logout", headers=H)
    assert r.status_code == 204
    assert _sha(demo_file) != demo_sha  # the demo session row went
    conn = connect(demo_file, readonly=True)
    try:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    finally:
        conn.close()
    assert _real_files(settings) == files


# --------------------------------------------------------------------------
# A route that opens settings.db_path itself
# --------------------------------------------------------------------------


def test_a_route_that_opens_the_settings_path_gets_the_demo_file(
    probes: Probes, prod_app: FastAPI, admin: TestClient, settings: Settings
) -> None:
    r = admin.get("/api/test-probe/db")
    assert r.json() == {"files": [settings.db_path.name], "query_only": 0}
    assert admin.get("/api/test-probe/default-db").status_code == 200
    assert admin.post("/api/demo/start", headers=H).status_code == 204
    r = admin.get("/api/test-probe/db")
    assert r.json() == {"files": ["demo.db"], "query_only": 1}
    # No path: the real Settings' file. The guard refuses it before anything is opened.
    files = _real_files(settings)
    assert admin.get("/api/test-probe/default-db").status_code == 500
    assert _real_files(settings) == files


# --------------------------------------------------------------------------
# Jobs during a demo request
# --------------------------------------------------------------------------


def test_jobs_during_a_demo_request_write_the_real_database(
    key: None,
    probes: Probes,
    prod_app: FastAPI,
    demo: TestClient,
    fake: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)  # the review's facts
    s = prod_app.state.settings
    enable()
    for run in SAMPLES.iterdir():
        if run.is_dir():
            shutil.copytree(run, s.import_dir / run.name)
    fake.script = [parsed(good_review())]
    demo_file = prod_app.state.demo.db_file
    demo_sha = _sha(demo_file)
    seen: list[tuple[bool, object, object]] = []

    def probe() -> None:
        seen.append((in_demo(), pinned_date(), current_guard()))

    every = timedelta(minutes=1)
    jobs = [
        import_poll_job(s),  # through to_thread
        Job("probe", probe, every),
        ai_review_job(s),  # in its own thread
        Job("probe_thread", probe, every, background=True),
    ]
    sched = Scheduler(jobs, s.db_path)
    with held_open(demo, probes) as out:
        # A clean context, as the lifespan's scheduler task has: asyncio.run would copy
        # this thread's context otherwise.
        ran = contextvars.Context().run(asyncio.run, sched.tick())
        for job in jobs:
            if job.thread is not None:
                job.thread.join(30)
        assert demo.get("/api/auth/me").json()["demo"] is True  # demo still answers meanwhile
    assert ran == ["import_poll", "probe", review.JOB_NAME, "probe_thread"]
    assert seen == [(False, None, None)] * 2
    assert out["r"].json() == {"today": DEMO_TODAY_ISO, "demo": True}

    with read_connection(s.db_path) as conn:
        runs = [tuple(r) for r in conn.execute("SELECT job, status FROM job_runs ORDER BY id")]
        imported = conn.execute(
            "SELECT count(*) FROM import_runs WHERE status = 'imported'"
        ).fetchone()[0]
        reviews = [r[0] for r in conn.execute("SELECT status FROM ai_reviews")]
    assert runs == [("import_poll", "ok"), (review.JOB_NAME, "ok")]
    assert imported == len(SAMPLE_RUNS) and reviews == ["ready"]
    assert len(fake.requests) == 1
    assert _sha(demo_file) == demo_sha


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------


def test_demo_and_real_requests_at_the_same_time_see_their_own_date(
    probes: Probes, prod_app: FastAPI, demo: TestClient
) -> None:
    real = TestClient(prod_app, base_url=PROD_URL)  # another browser, on real data
    assert login(real, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    with held_open(demo, probes) as out:
        before = datetime.now(HOUSEHOLD_TZ).date().isoformat()
        got = real.get("/api/meta").json()["household_today"]
        after = datetime.now(HOUSEHOLD_TZ).date().isoformat()
        assert got in {before, after}
        assert demo.get("/api/meta").json()["household_today"] == DEMO_TODAY_ISO
        assert household_today().isoformat() in {before, after}  # this thread: real
    assert out["r"].json() == {"today": DEMO_TODAY_ISO, "demo": True}
    assert real.get("/api/auth/me").json()["demo"] is False
