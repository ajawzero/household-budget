"""Demo mode safety layers: the demo Settings allowlist, the connect() guard, the clock pin,
the AI and outbound refusals, and the process-wide state a demo request must not see."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from ai_fakes import FakeClient, message, text
from api_helpers import TODAY
from conftest import SAMPLES
from fastapi.testclient import TestClient
from test_trip_planner import done, enable, research, start, written

from app import config
from app.ai import client as ai_client
from app.ai import context, planner
from app.ai.context import household_context
from app.ai.facts import ensure_facts
from app.ai.safe import ai_connection
from app.api import meta
from app.config import HOUSEHOLD_TZ, Settings, get_settings
from app.db.connect import connect, read_connection, write_connection, write_transaction
from app.db.migrate import migrate
from app.demo.context import (
    DEMO_TODAY,
    DemoGuardError,
    DemoRefused,
    current_guard,
    guarded,
    in_demo,
    pinned,
    pinned_date,
)
from app.demo.settings import COPIED, SET, demo_settings
from app.market import http
from app.market.job import run_prices
from app.valuations import hpi
from app.valuations.hpi import run_hpi
from app.valuations.hpi import urllib_lines as real_urllib_lines  # taken before conftest blocks it

CANARY = "fake-canary-key-for-demo-tests"


def _demo(real: Settings) -> Settings:
    return demo_settings(
        real, db_file=real.demo_dir / "demo.db", backup_dir=real.demo_dir / "backups"
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain_db(path: Path) -> Path:
    """A small database in DELETE journal mode, made outside any guard."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER) STRICT")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    return path


# --------------------------------------------------------------------------
# Demo Settings
# --------------------------------------------------------------------------


def test_allowlist_sorts_every_field_once() -> None:
    assert COPIED | SET == set(Settings.model_fields)
    assert not COPIED & SET


def test_demo_settings_set_fields(settings: Settings) -> None:
    demo = _demo(settings)
    assert set(demo.__dict__) == set(Settings.model_fields)  # no stray update keys
    assert demo.db_path == settings.demo_dir / "demo.db"
    assert demo.import_dir == SAMPLES == settings.demo_runs
    assert demo.backup_dir == settings.demo_dir / "backups"
    for name in ("db_path", "import_dir", "backup_dir"):
        assert isinstance(getattr(demo, name), Path), name
    assert demo.private_rules is None
    assert demo.anthropic_api_key is None and demo.tiingo_api_key is None
    for name in COPIED:
        assert getattr(demo, name) == getattr(settings, name), name
    assert settings.db_path != demo.db_path  # the real Settings is unchanged


def test_demo_settings_take_str_paths_as_paths(settings: Settings) -> None:
    demo = demo_settings(settings, db_file="x/demo.db", backup_dir="x/backups")  # type: ignore[arg-type]
    assert isinstance(demo.db_path, Path) and isinstance(demo.backup_dir, Path)


@pytest.mark.parametrize(
    ("anthropic", "tiingo"),
    [
        ("ANTHROPIC_API_KEY", "TIINGO_API_KEY"),
        ("BUDGET_ANTHROPIC_API_KEY", "BUDGET_TIINGO_API_KEY"),
    ],
)
def test_env_canaries_never_reach_demo_settings(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anthropic: str, tiingo: str
) -> None:
    rules = tmp_path / "canary_rules.json"
    monkeypatch.setenv(anthropic, CANARY)
    monkeypatch.setenv(tiingo, CANARY)
    monkeypatch.setenv("BUDGET_PRIVATE_RULES", str(rules))
    get_settings.cache_clear()
    real = get_settings()
    assert real.anthropic_api_key is not None and real.tiingo_api_key is not None
    assert real.private_rules == rules
    demo = _demo(real)
    assert demo.anthropic_api_key is None and demo.tiingo_api_key is None
    assert demo.private_rules is None
    values = repr(
        [
            v.get_secret_value() if hasattr(v, "get_secret_value") else v
            for v in demo.__dict__.values()
        ]
    )
    assert CANARY not in values and "canary_rules" not in values


# --------------------------------------------------------------------------
# connect() guard
# --------------------------------------------------------------------------


def test_guard_is_off_by_default() -> None:
    assert current_guard() is None and not in_demo() and pinned_date() is None


def test_guard_refuses_the_real_path_and_makes_nothing(settings: Settings) -> None:
    demo_file = settings.demo_dir / "demo.db"
    with guarded(demo_file):
        assert in_demo()
        for readonly in (False, True):
            with pytest.raises(DemoGuardError) as err:
                connect(settings.db_path, readonly=readonly)
            assert str(settings.db_path.parent) not in str(err.value)
            assert "budget.db" not in str(err.value)
    assert not settings.db_path.parent.exists()
    assert not settings.demo_dir.exists()
    assert current_guard() is None  # reset on exit
    conn = connect(settings.db_path)  # the real path opens again outside
    conn.close()


def test_guard_refuses_the_default_path(settings: Settings) -> None:
    with guarded(settings.demo_dir / "demo.db"):
        with pytest.raises(DemoGuardError):
            connect()
        with pytest.raises(DemoGuardError), read_connection():
            pass
        with pytest.raises(DemoGuardError), write_connection():
            pass
    assert not settings.db_path.parent.exists()


def test_guard_opens_the_demo_file_read_only(settings: Settings) -> None:
    demo_file = _plain_db(settings.demo_dir / "demo.db")
    before = _sha(demo_file)
    with guarded(demo_file):
        conn = connect(demo_file)  # asked for read-write
        try:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert conn.execute("SELECT x FROM t").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO t VALUES (2)")
            with pytest.raises(sqlite3.OperationalError), write_transaction(conn):
                pass
        finally:
            conn.close()
        with pytest.raises(sqlite3.OperationalError), write_connection(demo_file):
            pass
    assert _sha(demo_file) == before
    assert sorted(p.name for p in settings.demo_dir.iterdir()) == ["demo.db"]


def test_guard_matches_the_demo_file_through_a_linked_folder(
    settings: Settings, tmp_path: Path
) -> None:
    demo_file = _plain_db(settings.demo_dir / "demo.db")
    link = tmp_path / "demo-link"
    link.symlink_to(settings.demo_dir)
    with guarded(demo_file):
        connect(link / "demo.db").close()
    with guarded(link / "demo.db"):
        connect(demo_file).close()


def test_guard_refuses_a_demo_file_linked_to_the_real_one(settings: Settings) -> None:
    real = _plain_db(settings.db_path)
    before = _sha(real)
    settings.demo_dir.mkdir()
    demo_file = settings.demo_dir / "demo.db"
    demo_file.symlink_to(real)
    with guarded(demo_file):
        for readonly in (False, True):
            with pytest.raises(DemoGuardError):
                connect(demo_file, readonly=readonly)
    assert _sha(real) == before


def test_build_guard_allows_read_write_on_its_temp_file_only(settings: Settings) -> None:
    tmp_file = settings.demo_dir / "demo-build.db"
    settings.demo_dir.mkdir()
    with guarded(tmp_file, writable=True) as guard:
        assert guard.writable and in_demo()
        conn = connect(tmp_file)
        try:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 0
            with write_transaction(conn):
                conn.execute("CREATE TABLE t (x INTEGER) STRICT")
        finally:
            conn.close()
        for other in (settings.demo_dir / "demo.db", settings.db_path):
            with pytest.raises(DemoGuardError):
                connect(other)
        with pytest.raises(DemoGuardError):
            connect()
        # Read only when asked, still.
        conn = connect(tmp_file, readonly=True)
        try:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            conn.close()
    assert not (settings.demo_dir / "demo.db").exists()
    assert not settings.db_path.parent.exists()


def test_guard_never_reaches_a_new_thread(settings: Settings) -> None:
    """Jobs run in their own threads. They must not inherit a demo request's context."""
    seen: list[object] = []

    def job() -> None:
        seen.extend([current_guard(), pinned_date()])

    with guarded(settings.demo_dir / "demo.db"), pinned():
        t = threading.Thread(target=job)
        t.start()
        t.join()
    assert seen == [None, None]


# --------------------------------------------------------------------------
# Clock pin
# --------------------------------------------------------------------------


def test_demo_today_is_the_samples_today() -> None:
    assert DEMO_TODAY == TODAY == date(2026, 10, 20)


def test_pin_moves_household_today_inside_only() -> None:
    before = datetime.now(HOUSEHOLD_TZ).date()
    outside = config.household_today()
    after = datetime.now(HOUSEHOLD_TZ).date()
    assert outside in (before, after)
    with pinned():
        assert config.household_today() == DEMO_TODAY
        assert meta.household_today() == DEMO_TODAY  # a bound import follows too
        now = config.household_now()
        assert now.date() == DEMO_TODAY and now.tzinfo is HOUSEHOLD_TZ
    assert pinned_date() is None
    assert config.household_today() in (before, datetime.now(HOUSEHOLD_TZ).date())


# --------------------------------------------------------------------------
# AI client refusals
# --------------------------------------------------------------------------


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake key, set before any app is built. Nothing can reach Anthropic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", CANARY)
    get_settings.cache_clear()


@pytest.fixture
def ai_on(settings: Settings, key: None) -> Settings:
    """The real test database with AI on and a key: a call there goes through."""
    s = get_settings()
    migrate(settings=s)
    enable()
    return s


@pytest.fixture
def fake() -> Iterator[FakeClient]:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


def _info() -> ai_client.CallInfo:
    return ai_client.CallInfo("review", "claude-opus-5-5", "high")


async def _create(client):  # noqa: ANN001, ANN202
    return await client.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[])


def _usage_rows(s: Settings) -> int:
    conn = connect(s.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0]
    finally:
        conn.close()


def test_make_client_refuses_in_demo_with_the_fake_installed(ai_on: Settings, fake) -> None:
    assert ai_client.make_client(ai_on) is fake  # the fake replaces default_factory
    with guarded(ai_on.demo_dir / "demo.db"), pytest.raises(DemoRefused):
        ai_client.make_client(ai_on)


def test_call_refuses_in_demo_with_no_request_and_no_row(ai_on: Settings, fake) -> None:
    fake.script = [message([text("ok")], model="claude-opus-5-5")]
    with guarded(ai_on.demo_dir / "demo.db"), pytest.raises(DemoRefused):
        asyncio.run(ai_client.call(ai_on, _info(), _create))
    assert fake.requests == [] and _usage_rows(ai_on) == 0
    asyncio.run(ai_client.call(ai_on, _info(), _create))  # the same call goes outside demo
    assert len(fake.requests) == 1 and _usage_rows(ai_on) == 1


def test_preflight_refuses_in_demo_before_any_connect(
    ai_on: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[object] = []
    monkeypatch.setattr(ai_client, "connect", lambda *a, **k: opened.append(a))
    demo_file = ai_on.demo_dir / "demo.db"
    with guarded(demo_file), pytest.raises(DemoRefused):
        ai_client.preflight(demo_file, ai_on, _info())
    assert opened == []


# --------------------------------------------------------------------------
# Outbound refusals
# --------------------------------------------------------------------------

TIINGO_URL = "https://api.tiingo.com/tiingo/daily/VTI/prices"


class _Recorder:
    """Stands in for the opener: records each URL and answers with a network error."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def open(self, req, timeout=None):  # noqa: ANN001, ANN201
        self.urls.append(req.full_url)
        raise urllib.error.URLError("recorded")


@pytest.fixture
def opener(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(http, "_OPENER", rec)
    return rec


def test_open_url_refuses_in_demo_and_is_no_network_error(
    settings: Settings, opener: _Recorder
) -> None:
    with guarded(settings.demo_dir / "demo.db"):
        with pytest.raises(DemoRefused):
            http.open_url(urllib.request.Request(TIINGO_URL), 1.0)
        with pytest.raises(DemoRefused):  # not turned into NetworkError
            http.urllib_transport(TIINGO_URL, {}, 1.0)
    assert opener.urls == []
    with pytest.raises(http.NetworkError):  # outside demo it reaches the opener
        http.urllib_transport(TIINGO_URL, {}, 1.0)
    assert opener.urls == [TIINGO_URL]


def test_the_index_download_refuses_in_demo(settings: Settings, opener: _Recorder) -> None:
    assert hpi.urllib_lines is not real_urllib_lines  # conftest blocks the module's copy
    with guarded(settings.demo_dir / "demo.db"), pytest.raises(DemoRefused):  # not FetchError
        next(real_urllib_lines(hpi.MASTER_URL, {}, 1.0))
    assert opener.urls == []


def test_the_price_and_index_runs_refuse_any_transport_in_demo(
    settings: Settings, db: sqlite3.Connection
) -> None:
    fetched: list[str] = []

    def fetch(url: str, headers: object, timeout: float):  # noqa: ANN202
        fetched.append(url)
        raise AssertionError("no fetch in demo")

    now = datetime.now(UTC)
    with guarded(settings.demo_dir / "demo.db"):
        with pytest.raises(DemoRefused):
            run_prices(db, CANARY, now, transport=fetch, sleep=lambda _s: None)
        with pytest.raises(DemoRefused):
            run_hpi(db, now, source=fetch)
    assert fetched == []
    assert run_prices(db, CANARY, now, transport=fetch)["symbols"] == 0  # no symbols, no fetch


# --------------------------------------------------------------------------
# Process-wide state
# --------------------------------------------------------------------------


class _HeldStreams(FakeClient):
    """The fake client, with each stream held until `gate` is set: a plan still running."""

    def __init__(self, script: list) -> None:
        super().__init__(script)
        self.gate = threading.Event()
        stream = self.messages.stream

        @contextlib.asynccontextmanager
        async def held(**params):  # noqa: ANN202
            while not self.gate.is_set():
                await asyncio.sleep(0.01)
            async with stream(**params) as s:
                yield s

        self.messages.stream = held


def test_the_running_plan_is_hidden_in_demo(
    key: None, samples: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    enable()
    held = _HeldStreams([research(), written()])
    ai_client.set_client_factory(lambda _s: held)
    timer = threading.Timer(5.0, held.gate.set)  # never hangs
    timer.start()
    try:
        plan_id = start(admin)
        assert admin.get("/api/trip-plans").json()["running_plan_id"] == plan_id
        assert planner.running_plan() == plan_id
        with guarded(samples.demo_dir / "demo.db"):
            assert planner.running_plan() is None
        assert planner.running_plan() == plan_id
        held.gate.set()
        done(admin)
    finally:
        timer.cancel()
        ai_client.set_client_factory(None)


def test_the_context_cache_keys_on_the_database_file(
    samples: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo database can have the real one's facts signature: two files, two entries."""
    monkeypatch.setattr(context, "_CACHE", {})
    other = tmp_path / "other" / "budget.db"
    migrate(db_path=other, backup_dir=tmp_path / "other-backups", settings=samples)
    sig = ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as ai:
        full = household_context(ai, sig, samples.db_path)
    with ai_connection(other) as ai:
        empty = household_context(ai, sig, other)
    assert full != empty
    monkeypatch.setattr(context, "build", lambda _conn: pytest.fail("built again"))
    same_file = tmp_path / "other" / ".." / "other" / "budget.db"
    assert household_context(None, sig, same_file) == empty  # type: ignore[arg-type]
