"""Logs hold no values (PLAN 5.5, 11): no amounts, descriptions, merchant names,
account names or injected bad values, during sample imports and API calls.
Exceptions log the class and `module:line` only."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import date
from pathlib import Path

import pytest
from api_helpers import (
    ADMIN,
    ADMIN_PW,
    PROD_ORIGIN,
    PROD_URL,
    SAMPLES,
    add_users,
    app_for,
    import_samples,
    login,
)
from bad_cases import BAD_CASES, INJECTED_VALUES
from fastapi.testclient import TestClient

from app import config
from app.config import Settings
from app.importer.runner import import_folder
from app.logging import JsonFormatter, configure_logging

MONEY = re.compile(r"^-?\d+\.\d{2}$")
# A budget target edit: Lifestyle in the October sample plan, and a new target.
SEEN = 365_000
TARGET = 345_678


def sample_values() -> set[str]:
    """Descriptions, merchants, amounts and account names from the sample runs."""
    values: set[str] = set()
    for f in SAMPLES.rglob("*.csv"):
        for row in csv.DictReader(io.StringIO(f.read_text(encoding="utf-8"))):
            values.update(v for v in (row["description"], row["merchant"]) if v)
            values.add(row["amount"].lstrip("-"))

    def walk(node: object, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if MONEY.match(node):
                values.add(node.lstrip("-"))
            elif key in ("display_name", "name", "holding_name", "label", "title"):
                values.add(node)

    for f in SAMPLES.rglob("snapshot.json"):
        walk(json.loads(f.read_text(encoding="utf-8")))
    # Very short strings ("0.00", "Fee") would match by accident, not by leak.
    return {v for v in values if len(v) >= 5 and v not in ("10.00", "0.00")}


def captured(caplog: pytest.LogCaptureFixture, stdout: str) -> str:
    fmt = JsonFormatter()
    return stdout + "\n".join(fmt.format(r) for r in caplog.records)


def test_logs_hold_no_values(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.DEBUG)
    add_users(settings)
    app = app_for(monkeypatch, dev=False)
    with TestClient(app, base_url=PROD_URL) as c:  # lifespan installs the JSON handler
        logging.getLogger().setLevel(logging.DEBUG)
        import_samples(settings)
        assert login(c, ADMIN, "a-wrong-password", PROD_ORIGIN).status_code == 401
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        for path in (
            "/api/meta",
            "/api/imports",
            "/api/imports/2026-10-12",
            "/api/accounts",
            "/api/categories",
            "/api/dashboard",
            "/api/checks",
            "/api/backups",
            "/api/transactions?limit=5",
            "/api/transactions?month=2026-09&account=maple-joint-checking",
            "/api/transactions?cursor=bad!",
            "/api/cashflow",
            "/api/cashflow/breakdown?by=merchant",
            "/api/cashflow/category/dining",
            "/api/cashflow/plan",
            "/api/income",
            "/api/budgets?month=2026-10",
            "/api/recurring?view=all",
            "/api/recurring/calendar?month=2026-09",
            "/api/plans",
            "/api/debt",
            "/api/flags",
            "/api/todos",
            "/api/pending",
            "/api/goals",
            "/api/statements/coverage",
            "/api/rewards",
            "/api/trips",
            "/api/trips/lake-weekend",
            "/api/trips/no-such-trip",
            "/api/nope",
        ):
            c.get(path)
        rows = c.get("/api/transactions", params={"limit": 500}).json()["rows"]
        assert rows
        # A budget target edit (admin): the log names the route, never the amounts.
        monkeypatch.setattr(config, "household_today", lambda: date(2026, 10, 20))
        change = {"group_key": "lifestyle", "target_cents": TARGET, "seen_chat_cents": SEEN}
        r = c.put(
            "/api/budgets/targets",
            json={"month": "2026-10", "changes": [change]},
            headers={"Origin": PROD_ORIGIN},
        )
        assert r.status_code == 200, r.text
    # Bad runs with injected values, imported directly (each in its own folder).
    for case in BAD_CASES:
        run_dir = case.builder(tmp_path / case.case_id)
        import_folder(run_dir, settings=settings, today=date(2026, 10, 20))

    text = captured(caplog, capsys.readouterr().out)
    assert '"route": "/api/imports/{run_id}"' in text  # template, not the raw path
    assert '2026-10-12", "status"' not in text
    assert "month=" not in text and "cursor=" not in text
    assert "a-wrong-password" not in text and ADMIN_PW not in text
    leaks = sorted(v for v in sample_values() | set(INJECTED_VALUES) if v in text)
    assert leaks == []
    assert len(sample_values()) > 50
    assert "import 2026-10-14 imported" in text and '"logger": "app.http"' in text
    assert '"logger": "app.auth"' in text
    assert '"route": "/api/budgets/targets"' in text
    for cents in (TARGET, SEEN):
        assert str(cents) not in text and f"{cents / 100:.2f}" not in text
    for row in rows:
        assert row["description"] not in text
        assert f"{abs(row['amount_cents']) / 100:.2f}" not in text
    # Every stdout line is one JSON object.
    for line in capsys.readouterr().out.splitlines():
        json.loads(line)


def test_stdout_lines_are_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    logging.getLogger("app.test").info("hello %s", "run", extra={"count": 3})
    lines = capsys.readouterr().out.strip().splitlines()
    obj = json.loads(lines[-1])
    assert obj["msg"] == "hello run" and obj["count"] == 3 and obj["logger"] == "app.test"
    assert set(obj) >= {"ts", "level", "logger", "msg"}


def test_exception_logs_class_and_place_only(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "Lakeside Grocer 4321.09"
    add_users(settings)
    app = app_for(monkeypatch, dev=False)

    @app.get("/api/boom")
    def boom() -> None:
        raise ValueError(secret)

    with TestClient(app, base_url=PROD_URL, raise_server_exceptions=True) as c:
        r = c.get("/api/boom")
        assert r.status_code == 500
        assert r.json() == {"detail": "Something went wrong."}
        assert secret not in r.text
    text = captured(caplog, capsys.readouterr().out)
    assert secret not in text and "Traceback" not in text
    assert '"exc_class": "ValueError"' in text
    assert re.search(r'"at": "tests?[._a-z]*test_logs:\d+"|"at": "test_logs:\d+"', text)


def test_exc_info_is_reduced(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    try:
        raise RuntimeError("secret detail 99.99")
    except RuntimeError:
        logging.getLogger("app.test").exception("failed")
    out = capsys.readouterr().out
    assert "secret detail" not in out and "Traceback" not in out
    obj = json.loads(out.strip().splitlines()[-1])
    assert obj["exc_class"] == "RuntimeError" and ":" in obj["at"]


def test_third_party_loggers_pinned() -> None:
    configure_logging(logging.DEBUG)
    for name in ("anthropic", "httpx", "httpcore", "asyncio"):
        assert logging.getLogger(name).level == logging.WARNING
    assert logging.getLogger("uvicorn.access").disabled


def test_demo_requests_are_marked_in_the_log(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    add_users(settings)
    app = app_for(monkeypatch, dev=False)
    h = {"Origin": PROD_ORIGIN}
    with TestClient(app, base_url=PROD_URL) as c:
        logging.getLogger().setLevel(logging.DEBUG)
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        assert c.get("/api/accounts").status_code == 200
        assert c.post("/api/demo/start", headers=h).status_code == 204
        for path in ("/api/accounts", "/api/dashboard", "/api/trips/lake-weekend"):
            assert c.get(path).status_code == 200, path
        rows = c.get("/api/transactions", params={"limit": 500}).json()["rows"]
        assert rows
        assert c.put("/api/settings/ai", json={}, headers=h).status_code == 403
        assert c.post("/api/auth/logout", headers=h).status_code == 204
        assert c.get("/api/accounts").status_code == 401

    text = captured(caplog, capsys.readouterr().out)
    requests = {
        (o["method"], o["route"], o["status"], o.get("demo"))
        for o in (json.loads(line) for line in text.splitlines() if line.startswith("{"))
        if o.get("logger") == "app.http"
    }
    assert {r for r in requests if r[3] is not None} == {
        ("GET", "/api/accounts", 200, True),
        ("GET", "/api/dashboard", 200, True),
        ("GET", "/api/trips/{key}", 200, True),
        ("GET", "/api/transactions", 200, True),
        ("PUT", "(unmatched)", 403, True),  # refused before routing
        ("POST", "/api/auth/logout", 204, True),
    }
    assert {r[:3] for r in requests if r[3] is None} == {
        ("POST", "/api/auth/login", 200),
        ("GET", "/api/accounts", 200),
        ("POST", "/api/demo/start", 204),
        ("GET", "/api/accounts", 401),
    }
    leaks = sorted(v for v in sample_values() if v in text)
    assert leaks == []
    for row in rows:
        assert row["description"] not in text
