"""Shared fixtures: a temp database, import folder and backup folder per test."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from api_helpers import (
    ADMIN,
    ADMIN_PW,
    MEMBER,
    MEMBER_PW,
    PROD_ORIGIN,
    add_users,
    app_for,
    client_for,
    import_samples,
    login,
)
from bad_cases import v13_samples
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.throttle import reset_unknown
from app.config import Settings, get_settings
from app.db.connect import connect
from app.db.migrate import migrate
from app.importer.sanitize import PrivateRules, load_private_rules

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "samples" / "data"
PRIVATE_RULES_EXAMPLE = REPO / "samples" / "private_rules.example.json"
SAMPLE_RUNS = ("2026-10-06", "2026-10-06-2", "2026-10-12", "2026-10-14")


@pytest.fixture(autouse=True)
def _no_index_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches FHFA. A test that wants the index passes a fake source."""
    from app.valuations import hpi

    def blocked(url, headers, timeout):
        raise hpi.FetchError("network")

    monkeypatch.setattr(hpi, "urllib_lines", blocked)


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A key in the shell (a dev shell, a container with the env file) must not
    turn on the price job or AI inside tests. Every ANTHROPIC_ name goes too
    (base URL, auth token): the SDK reads them. Tests that want a key set it."""
    for name in ("TIINGO_API_KEY", "BUDGET_TIINGO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(("ANTHROPIC_", "BUDGET_ANTHROPIC_")):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _LoopbackOnly:
    """Stands in for the app's opener: only 127.0.0.1 and localhost get through."""

    def __init__(self, real: object) -> None:
        self.real = real

    def open(self, req, timeout=None):  # noqa: ANN001, ANN201
        import urllib.error
        from urllib.parse import urlsplit

        if urlsplit(req.full_url).hostname not in ("127.0.0.1", "localhost"):
            raise urllib.error.URLError("network blocked in tests")
        return self.real.open(req, timeout=timeout)


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every outbound GET goes through app.market.http._OPENER. Tests reach loopback only."""
    from app.market import http

    monkeypatch.setattr(http, "_OPENER", _LoopbackOnly(http._OPENER))


@pytest.fixture(autouse=True)
def _forget_unknown_login_failures() -> Iterator[None]:
    """Unknown-username lock counts live in memory. Start every test clean."""
    reset_unknown()
    yield
    reset_unknown()


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings pointing at tmp_path. Private rules unset unless a test sets them."""
    monkeypatch.setenv("BUDGET_DB_PATH", str(tmp_path / "db" / "budget.db"))
    monkeypatch.setenv("BUDGET_IMPORT_DIR", str(tmp_path / "import"))
    monkeypatch.setenv("BUDGET_BACKUP_DIR", str(tmp_path / "backups"))
    # Never the dev default: a test app's startup cleanup would delete the dev server's demo.db.
    monkeypatch.setenv("BUDGET_DEMO_DIR", str(tmp_path / "demo"))
    monkeypatch.setenv("BUDGET_DEMO_RUNS", str(SAMPLES))
    monkeypatch.delenv("BUDGET_PRIVATE_RULES", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    s.import_dir.mkdir(parents=True)
    yield s
    get_settings.cache_clear()


@pytest.fixture
def use_private_rules(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Same as `settings`, with BUDGET_PRIVATE_RULES set to the example file."""
    monkeypatch.setenv("BUDGET_PRIVATE_RULES", str(PRIVATE_RULES_EXAMPLE))
    get_settings.cache_clear()
    s = get_settings()
    yield s
    get_settings.cache_clear()


@pytest.fixture
def db(settings: Settings) -> Iterator[sqlite3.Connection]:
    """A migrated, empty database connection."""
    migrate(settings=settings)
    conn = connect(settings.db_path)
    yield conn
    conn.close()


@pytest.fixture
def private_rules() -> PrivateRules:
    return load_private_rules(PRIVATE_RULES_EXAMPLE)


def copy_run(run_id: str, dest_dir: Path, name: str | None = None) -> Path:
    """Copy one good sample run into dest_dir (optionally under another folder name)."""
    dest = dest_dir / (name or run_id)
    shutil.copytree(SAMPLES / run_id, dest)
    return dest


# --------------------------------------------------------------------------
# API fixtures (auth, API, logs and scheduler tests). Helpers: api_helpers.py
# --------------------------------------------------------------------------


@pytest.fixture
def prod_app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    add_users(settings)
    return app_for(monkeypatch, dev=False)


@pytest.fixture
def prod(prod_app: FastAPI) -> Iterator[TestClient]:
    yield from client_for(prod_app, dev=False)


@pytest.fixture
def dev(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    add_users(settings)
    yield from client_for(app_for(monkeypatch, dev=True), dev=True)


@pytest.fixture
def admin(prod: TestClient) -> TestClient:
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
    return prod


@pytest.fixture
def member(prod: TestClient) -> TestClient:
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    return prod


@pytest.fixture
def samples(settings: Settings) -> Settings:
    migrate(settings=settings)
    import_samples(settings)
    return settings


@pytest.fixture
def samples_v13(settings: Settings, tmp_path: Path) -> Settings:
    """The samples with the monthly run in its 1.3 form (bad_cases.strip_newer), which is
    the 1.3 monthly sample byte for byte. For goldens captured before format 1.4."""
    migrate(settings=settings)
    import_samples(settings, v13_samples(tmp_path / "v13"))
    return settings
