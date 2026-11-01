"""API tests (PLAN 5.1, 5.2, 8.2 and 11): auth on every route, origin and host
rules, cookie modes, throttles, admin-only routes, docs and the P1 payloads."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from api_helpers import (
    ADMIN,
    ADMIN_PW,
    DEV_ORIGIN,
    MEMBER,
    MEMBER_PW,
    PROD_ORIGIN,
    PROD_URL,
    TODAY,
    FakeClock,
    add_users,
    app_for,
    client_for,
    login,
)
from fastapi.testclient import TestClient

from app.auth import clock
from app.config import Settings
from app.db.connect import connect
from app.main import PUBLIC_PATHS, walk_routes
from app.services.balances import net_worth

RUN = "2026-10-12"


def sample_path(path: str) -> str:
    return path.replace("{run_id}", RUN)


# --------------------------------------------------------------------------
# Auth on every route
# --------------------------------------------------------------------------


def test_every_private_route_needs_a_session(prod_app, prod: TestClient) -> None:
    routes = list(walk_routes(prod_app))
    paths = {p for p, _, _ in routes}
    assert {"/api/accounts", "/api/transactions", "/api/imports/{run_id}/retry"} <= paths
    checked = 0
    for path, methods, _ in routes:
        for method in methods - {"HEAD", "OPTIONS"}:
            r = prod.request(method, sample_path(path), headers={"Origin": PROD_ORIGIN})
            if path in PUBLIC_PATHS:
                assert r.status_code != 401, (method, path)
            else:
                assert r.status_code == 401, (method, path, r.status_code)
                assert r.json() == {"detail": "Not signed in."}
                checked += 1
    assert checked >= 10


def test_public_routes_are_only_health_and_login() -> None:
    assert PUBLIC_PATHS == {"/healthz", "/api/auth/login"}


def test_healthz(prod: TestClient) -> None:
    r = prod.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "db": "ok"}


def test_bad_session_cookie_is_401(prod: TestClient) -> None:
    prod.cookies.set("__Host-budget_session", "not-a-token")
    assert prod.get("/api/auth/me").status_code == 401


# --------------------------------------------------------------------------
# Hosts, Cloudflare headers, security headers
# --------------------------------------------------------------------------


def test_host_rules_in_prod(prod: TestClient) -> None:
    assert prod.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
    assert prod.get("/healthz", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert prod.get("/api/meta", headers={"Host": "127.0.0.1:8000"}).status_code == 400
    assert prod.get("/healthz", headers={"Host": "localhost:8000"}).status_code == 400


def test_host_rules_in_dev(dev: TestClient) -> None:
    assert dev.get("/healthz", headers={"Host": "localhost:5173"}).status_code == 200
    assert dev.get("/api/meta", headers={"Host": "127.0.0.1:8000"}).status_code == 401
    assert dev.get("/healthz", headers={"Host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("header", ["Cf-Connecting-Ip", "Cf-Ray"])
def test_cloudflare_headers_are_refused(prod: TestClient, header: str) -> None:
    r = prod.get("/healthz", headers={header: "x"})
    assert r.status_code == 403


def test_security_headers(prod: TestClient) -> None:
    r = prod.get("/api/meta")
    assert r.headers["content-security-policy"].startswith("default-src 'self'")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert prod.get("/healthz").headers["cache-control"] == "no-store"


def test_no_slash_redirects(admin: TestClient) -> None:
    r = admin.get("/api/meta/", follow_redirects=False)
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Origin rules
# --------------------------------------------------------------------------


def _login_body() -> dict:
    return {"username": ADMIN, "password": ADMIN_PW}


def test_unsafe_methods_need_exact_origin(prod: TestClient) -> None:
    url = "/api/auth/login"
    assert prod.post(url, json=_login_body()).status_code == 403  # no Origin
    for bad in (
        "https://evil.home.example",
        "http://budget.home.example",
        "https://budget.home.example:443",
        DEV_ORIGIN,
        "null",
        "",
    ):
        r = prod.post(url, json=_login_body(), headers={"Origin": bad})
        assert r.status_code == 403, bad
        assert r.json() == {"detail": "Request origin not allowed."}
    r = prod.post(
        url, json=_login_body(), headers={"Origin": PROD_ORIGIN, "Sec-Fetch-Site": "same-site"}
    )
    assert r.status_code == 403
    r = prod.post(
        url, json=_login_body(), headers={"Origin": PROD_ORIGIN, "Sec-Fetch-Site": "same-origin"}
    )
    assert r.status_code == 200


def test_origin_checked_on_every_unsafe_method(admin: TestClient) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        r = admin.request(method, "/api/auth/logout")
        assert r.status_code == 403, method
    # Still signed in: the refused logout did nothing.
    assert admin.get("/api/auth/me").status_code == 200


def test_public_port_origin(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy on another HTTPS port: the browser Origin has the port."""
    add_users(settings)
    app = app_for(monkeypatch, dev=False, BUDGET_PUBLIC_PORT="1443")
    url = "https://budget.home.example:1443"
    with TestClient(app, base_url=url) as c:
        assert c.get("/healthz").status_code == 200  # Host with :1443 is trusted
        assert login(c, ADMIN, ADMIN_PW, url).status_code == 200
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        for bad in (
            "https://budget.home.example:8443",
            "http://budget.home.example:1443",
        ):
            assert login(c, ADMIN, ADMIN_PW, bad).status_code == 403, bad


def test_dev_origins(dev: TestClient) -> None:
    for origin in (DEV_ORIGIN, "http://127.0.0.1:5173"):
        r = login(dev, ADMIN, ADMIN_PW, origin)
        assert r.status_code == 200, origin
    assert login(dev, ADMIN, ADMIN_PW, "http://localhost:3000").status_code == 403


# --------------------------------------------------------------------------
# Login, cookies, logout, password
# --------------------------------------------------------------------------


def test_login_contract_and_prod_cookie(prod: TestClient, settings: Settings) -> None:
    r = login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN)
    assert r.status_code == 200
    assert r.json() == {
        "user": {"username": ADMIN, "display_name": "Alex Example", "role": "admin"}
    }
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-budget_session=")
    lower = cookie.lower()
    for flag in ("secure", "httponly", "samesite=lax", "path=/"):
        assert flag in lower, flag
    assert "domain=" not in lower
    token = cookie.split("=", 1)[1].split(";", 1)[0]
    assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) == 32
    with connect(settings.db_path) as conn:
        hashes = [row[0] for row in conn.execute("SELECT token_hash FROM sessions")]
    assert hashes == [hashlib.sha256(token.encode()).hexdigest()]
    me = prod.get("/api/auth/me").json()
    assert {k: me[k] for k in ("username", "display_name", "role")} == r.json()["user"]
    assert me["demo"] is False and me["household_today"] is None


def test_dev_cookie(dev: TestClient) -> None:
    r = login(dev, MEMBER, MEMBER_PW, DEV_ORIGIN)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("budget_session=")
    lower = cookie.lower()
    assert "secure" not in lower
    assert "httponly" in lower and "samesite=lax" in lower and "path=/" in lower
    assert dev.get("/api/auth/me").json()["role"] == "member"


def test_wrong_password_and_unknown_user(prod: TestClient, settings: Settings) -> None:
    for user, pw in ((ADMIN, "wrong-password-x"), ("nobody", "whatever-pass")):
        r = login(prod, user, pw, PROD_ORIGIN)
        assert r.status_code == 401
        assert r.json() == {"detail": "Wrong username or password."}
        assert "set-cookie" not in r.headers
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, detail_json FROM events WHERE kind = 'login_failed' ORDER BY id"
        ).fetchall()
    details = [json.loads(r["detail_json"]) for r in rows]
    assert details == [{"username": ADMIN}, {"username": "unknown"}]
    assert rows[1]["user_id"] is None
    assert all("wrong-password" not in r["detail_json"] for r in rows)


def test_validation_errors_never_echo_input(prod: TestClient) -> None:
    secret = "hunter2-secret-value"
    r = prod.post(
        "/api/auth/login",
        json={"username": ADMIN, "password": secret, "x": secret},
        headers={"Origin": PROD_ORIGIN},
    )
    assert r.status_code == 422
    assert secret not in r.text
    r = prod.post(
        "/api/auth/login", json={"username": 5, "password": 4321}, headers={"Origin": PROD_ORIGIN}
    )
    assert r.status_code == 422
    assert "4321" not in r.text


def test_unknown_keys_are_not_echoed(admin: TestClient) -> None:
    secret = "hunter2-secret-key"
    r = admin.post(
        "/api/rewards/balances",
        json={"entries": [], secret: 1},
        headers={"Origin": PROD_ORIGIN},
    )
    assert r.status_code == 422
    assert secret not in r.text
    assert {"loc": ["body", "(unknown field)"], "type": "extra_forbidden"} in r.json()["errors"]


def test_logout(admin: TestClient, settings: Settings) -> None:
    r = admin.post("/api/auth/logout", headers={"Origin": PROD_ORIGIN})
    assert r.status_code == 204
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert admin.get("/api/auth/me").status_code == 401


def test_password_change(prod_app, settings: Settings) -> None:
    other = TestClient(prod_app, base_url=PROD_URL)
    assert login(other, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    with TestClient(prod_app, base_url=PROD_URL) as c:
        assert login(c, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
        url, h = "/api/auth/password", {"Origin": PROD_ORIGIN}
        r = c.post(
            url, json={"current_password": "nope-nope-nope", "new_password": "x" * 12}, headers=h
        )
        assert r.status_code == 400
        r = c.post(url, json={"current_password": MEMBER_PW, "new_password": "short"}, headers=h)
        assert r.status_code == 400
        r = c.post(
            url, json={"current_password": MEMBER_PW, "new_password": "new-pass-abcde"}, headers=h
        )
        assert r.status_code == 204
        assert c.get("/api/auth/me").status_code == 200  # this session stays
        assert other.get("/api/auth/me").status_code == 401  # other sessions end
        assert login(c, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 401
        assert login(c, MEMBER, "new-pass-abcde", PROD_ORIGIN).status_code == 200


# --------------------------------------------------------------------------
# Throttles and session timeouts (fake clock)
# --------------------------------------------------------------------------


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fc = FakeClock()
    monkeypatch.setattr(clock, "now", fc)
    return fc


def test_username_lockout(prod: TestClient, fake_clock: FakeClock) -> None:
    for _ in range(5):
        assert login(prod, ADMIN, "wrong-password-x", PROD_ORIGIN).status_code == 401
        fake_clock.advance(seconds=10)
    r = login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN)
    assert r.status_code == 429
    assert r.json() == {"detail": "Too many attempts. Try again later."}
    # Other users are not locked.
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    fake_clock.advance(minutes=14)
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 429
    fake_clock.advance(minutes=1, seconds=1)
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200


def test_four_failures_do_not_lock(prod: TestClient, fake_clock: FakeClock) -> None:
    for _ in range(4):
        assert login(prod, ADMIN, "wrong-password-x", PROD_ORIGIN).status_code == 401
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200


def test_unknown_username_locks_the_same_way(prod: TestClient, fake_clock: FakeClock) -> None:
    """A lock must not show which usernames exist: unknown names lock after 5 failures."""
    statuses = []
    for name in ("nobody", ADMIN):
        for _ in range(5):
            assert login(prod, name, "wrong-password-x", PROD_ORIGIN).status_code == 401
            fake_clock.advance(seconds=10)
        r = login(prod, name, "wrong-password-x", PROD_ORIGIN)
        statuses.append((r.status_code, r.json()))
    assert statuses[0] == statuses[1] == (429, {"detail": "Too many attempts. Try again later."})
    fake_clock.advance(minutes=16)
    assert login(prod, "nobody", "wrong-password-x", PROD_ORIGIN).status_code == 401


def test_global_throttle(prod: TestClient, fake_clock: FakeClock) -> None:
    for i in range(21):
        assert login(prod, f"nobody{i}", "whatever-pass", PROD_ORIGIN).status_code == 401
        fake_clock.advance(seconds=1)
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 429
    fake_clock.advance(seconds=61)
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200  # 1 a minute
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 429
    fake_clock.advance(hours=1)
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200


def test_session_idle_timeout(prod: TestClient, fake_clock: FakeClock) -> None:
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
    fake_clock.advance(days=13)
    assert prod.get("/api/auth/me").status_code == 200  # use extends the idle window
    fake_clock.advance(days=13)
    assert prod.get("/api/auth/me").status_code == 200
    fake_clock.advance(days=14, seconds=1)
    assert prod.get("/api/auth/me").status_code == 401


def test_session_absolute_timeout(
    prod: TestClient, fake_clock: FakeClock, settings: Settings
) -> None:
    assert login(prod, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
    for _ in range(3):
        fake_clock.advance(days=9)
        assert prod.get("/api/auth/me").status_code == 200
    fake_clock.advance(days=3, seconds=1)  # 30 days since login
    assert prod.get("/api/auth/me").status_code == 401
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


# --------------------------------------------------------------------------
# Docs
# --------------------------------------------------------------------------


def test_docs_off_outside_dev(admin: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert admin.get(path).status_code == 404, path


def test_docs_on_in_dev(dev: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert dev.get(path).status_code == 200, path


# --------------------------------------------------------------------------
# Imports and retry
# --------------------------------------------------------------------------

RUN_FIELDS = {
    "run_id",
    "mode",
    "schema_version",
    "status",
    "started_at",
    "finished_at",
    "counts",
    "warning_count",
    "error",
}


def test_imports_list(samples: Settings, admin: TestClient) -> None:
    runs = admin.get("/api/imports").json()["runs"]
    assert [r["run_id"] for r in runs] == ["2026-10-14", "2026-10-12", "2026-10-06-2", "2026-10-06"]
    for r in runs:
        assert set(r) == RUN_FIELDS
        assert r["status"] == "imported"
        assert r["error"] is None
        assert isinstance(r["counts"], dict) and "changes" in r["counts"]
        assert isinstance(r["warning_count"], int)
    assert {r["mode"] for r in runs} == {"backfill", "monthly", "correction"}


def test_import_detail(samples: Settings, member: TestClient) -> None:
    d = member.get(f"/api/imports/{RUN}").json()
    assert RUN_FIELDS <= set(d)
    assert {"warnings", "statements", "errors"} <= set(d)
    assert d["statements"], "the monthly run has statements"
    for s in d["statements"]:
        assert set(s) == {
            "statement_key",
            "account_key",
            "period_end",
            "is_current",
            "txn_count",
            "reconciled",
        }
        assert isinstance(s["is_current"], bool) and isinstance(s["reconciled"], bool)
    for w in d["warnings"]:
        assert set(w) == {"loc", "rule", "message"}
    assert member.get("/api/imports/2030-01-01").status_code == 404
    assert member.get("/api/imports/not-a-run").status_code == 404


def test_failed_run_shows_rule_and_location(settings: Settings, admin: TestClient) -> None:
    import shutil

    from api_helpers import SAMPLES

    from app.importer.runner import import_folder

    dest = settings.import_dir / "2026-10-12"
    shutil.copytree(SAMPLES / "2026-10-12", dest)
    (dest / "extra.txt").write_text("x")
    assert import_folder(dest, settings=settings, today=TODAY).status == "failed"
    runs = admin.get("/api/imports").json()["runs"]
    assert len(runs) == 1
    err = runs[0]["error"]
    assert runs[0]["status"] == "failed"
    assert err is not None and {"loc", "rule"} <= set(err)
    d = admin.get("/api/imports/2026-10-12").json()
    assert all({"loc", "rule", "message"} == set(e) for e in d["errors"])


def test_retry_is_admin_only(samples: Settings, prod_app) -> None:
    with TestClient(prod_app, base_url=PROD_URL) as c:
        assert login(c, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
        r = c.post(f"/api/imports/{RUN}/retry", headers={"Origin": PROD_ORIGIN})
        assert r.status_code == 403
    with TestClient(prod_app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        r = c.post(f"/api/imports/{RUN}/retry", headers={"Origin": PROD_ORIGIN})
        assert r.status_code == 202
        assert r.json() == {"status": "unchanged"}
        r = c.post("/api/imports/2030-01-01/retry", headers={"Origin": PROD_ORIGIN})
        assert r.status_code == 404


def test_retry_imports_a_failed_run_once_fixed(settings: Settings, admin: TestClient) -> None:
    import shutil

    from api_helpers import SAMPLES

    from app.importer.runner import import_path

    for run in ("2026-10-06", "2026-10-06-2"):
        shutil.copytree(SAMPLES / run, settings.import_dir / run)
    bad = settings.import_dir / RUN
    shutil.copytree(SAMPLES / RUN, bad)
    (bad / "extra.txt").write_text("x")
    import_path(settings.import_dir, settings=settings, today=TODAY)
    assert admin.get(f"/api/imports/{RUN}").json()["status"] == "failed"
    (bad / "extra.txt").unlink()
    r = admin.post(f"/api/imports/{RUN}/retry", headers={"Origin": PROD_ORIGIN})
    assert r.status_code == 202 and r.json() == {"status": "imported"}
    d = admin.get(f"/api/imports/{RUN}").json()
    assert d["status"] == "imported" and d["error"] is None


# --------------------------------------------------------------------------
# Meta, accounts, categories
# --------------------------------------------------------------------------


def test_meta(samples: Settings, admin: TestClient) -> None:
    m = admin.get("/api/meta").json()
    assert set(m) == {
        "app_version",
        "schema_versions",
        "household_today",
        "latest_run_id",
        "updated",
        "this_month",
        "complete_as_of",
        "latest_complete_month",
        "location_label",
    }
    assert m["schema_versions"] == ["1.0", "1.1", "1.2", "1.3", "1.4"]
    assert m["latest_run_id"] == "2026-10-14"
    assert m["this_month"] is not None and len(m["this_month"]) == 7
    assert m["updated"] is not None


def test_meta_on_empty_db(admin: TestClient) -> None:
    m = admin.get("/api/meta").json()
    assert m["latest_run_id"] is None and m["this_month"] is None and m["updated"] is None


def test_accounts(samples: Settings, admin: TestClient) -> None:
    """The P1 route without a range now returns the Phase 2 shape (YTD).
    Field-level checks are in test_networth.py."""
    d = admin.get("/api/accounts").json()
    assert set(d) == {"as_of", "range", "groups", "summary", "notes"}
    assert d["range"]["key"] == "YTD"
    rows = [a for g in d["groups"] for a in g["accounts"]]
    store = next(a for a in rows if a["tracked_by"] == "payments")
    assert (store["balance_cents"], store["source"]) == (None, "tracked")
    owed = [a for a in rows if not a["is_asset"] and a["balance_cents"]]
    assert owed and all(a["balance_cents"] < 0 for a in owed)
    with connect(samples.db_path) as conn:
        nw = net_worth(conn)
    s = d["summary"]
    assert (s["assets_cents"], s["liabilities_cents"], s["net_worth_cents"]) == (
        nw.assets_cents,
        nw.liabilities_cents,
        nw.value_cents,
    )
    assert s["net_worth_cents"] == s["assets_cents"] - s["liabilities_cents"]


def test_categories(admin: TestClient, settings: Settings) -> None:
    groups = admin.get("/api/categories").json()["groups"]
    with connect(settings.db_path) as conn:
        n_groups = conn.execute("SELECT count(*) FROM category_groups").fetchone()[0]
        n_cats = conn.execute("SELECT count(*) FROM categories WHERE active = 1").fetchone()[0]
    assert len(groups) == n_groups
    assert sum(len(g["categories"]) for g in groups) == n_cats
    for g in groups:
        assert set(g) == {"group_key", "name", "categories"}
        for c in g["categories"]:
            assert set(c) == {"category_key", "name", "emoji", "kind"}


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

TXN_FIELDS = {
    "txn_key",
    "txn_date",
    "post_date",
    "account_key",
    "account_name",
    "description",
    "merchant",
    "category_key",
    "category_name",
    "cardholder",
    "cardholder_label",
    "amount_cents",
    "is_transfer",
    "is_reimbursable",
}


def _all_rows(client: TestClient, **params: object) -> list[dict]:
    rows, cursor, pages = [], None, 0
    while True:
        q = dict(params)
        if cursor:
            q["cursor"] = cursor
        d = client.get("/api/transactions", params=q).json()
        rows += d["rows"]
        cursor = d["next_cursor"]
        pages += 1
        assert pages < 1000
        if cursor is None:
            return rows


def _live(settings: Settings, where: str = "1=1", params: tuple = ()) -> list[sqlite3.Row]:
    with connect(settings.db_path) as conn:
        return conn.execute(
            f"""SELECT t.*, a.owner_person_key FROM v_transactions t
                JOIN accounts a USING (account_key) WHERE hidden = 0 AND {where}
                ORDER BY txn_date DESC, txn_key DESC""",
            params,
        ).fetchall()


def test_transactions_payload(samples: Settings, admin: TestClient) -> None:
    d = admin.get("/api/transactions").json()
    assert set(d) == {"rows", "next_cursor"}
    assert 0 < len(d["rows"]) <= 100
    for r in d["rows"]:
        assert set(r) == TXN_FIELDS
        if r["cardholder"] is not None:
            # a display name ("Sam"), never the raw key ("sam")
            assert r["cardholder_label"] and r["cardholder_label"] != r["cardholder"]
        assert isinstance(r["amount_cents"], int)
        assert isinstance(r["is_transfer"], bool) and isinstance(r["is_reimbursable"], bool)
    dates = [(r["txn_date"], r["txn_key"]) for r in d["rows"]]
    assert dates == sorted(dates, reverse=True)


def test_transactions_cursor_walks_every_row_once(samples: Settings, admin: TestClient) -> None:
    rows = _all_rows(admin, limit=7)
    keys = [r["txn_key"] for r in rows]
    assert len(keys) == len(set(keys))
    assert keys == [r["txn_key"] for r in _live(samples)]
    assert keys == [r["txn_key"] for r in _all_rows(admin, limit=500)]


def test_transactions_filters(samples: Settings, admin: TestClient) -> None:
    live = _live(samples)
    account = live[0]["account_key"]
    got = _all_rows(admin, account=account, limit=50)
    assert [r["txn_key"] for r in got] == [
        r["txn_key"] for r in live if r["account_key"] == account
    ]
    category = live[0]["category_key"]
    got = _all_rows(admin, category=category)
    assert got and all(r["category_key"] == category for r in got)
    assert len(got) == sum(1 for r in live if r["category_key"] == category)
    month = live[-1]["txn_date"][:7]
    got = _all_rows(admin, month=month)
    assert got and all(r["txn_date"].startswith(month) for r in got)
    assert len(got) == sum(1 for r in live if r["txn_date"].startswith(month))
    owners = {r["cardholder_person_key"] or r["owner_person_key"] for r in live}
    for owner in owners:
        got = _all_rows(admin, owner=owner)
        assert len(got) == sum(
            1 for r in live if (r["cardholder_person_key"] or r["owner_person_key"]) == owner
        )
    both = _all_rows(admin, account=account, month=live[0]["txn_date"][:7])
    assert all(r["account_key"] == account for r in both)
    assert admin.get("/api/transactions", params={"account": "no-such-account"}).json() == {
        "rows": [],
        "next_cursor": None,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"month": "2026-13"},
        {"month": "2026-1"},
        {"account": "Bad Key"},
        {"cursor": "x" * 600},
    ],
)
def test_transactions_bad_params(samples: Settings, admin: TestClient, params: dict) -> None:
    r = admin.get("/api/transactions", params=params)
    assert r.status_code == 422
    for v in params.values():
        if len(str(v)) > 2:
            assert str(v) not in r.text


@pytest.mark.parametrize(
    "cursor", ["!!!", "bm90LWpzb24", base64.urlsafe_b64encode(b'["x","y"]').decode()]
)
def test_transactions_bad_cursor(samples: Settings, admin: TestClient, cursor: str) -> None:
    r = admin.get("/api/transactions", params={"cursor": cursor})
    assert r.status_code == 400
    assert r.json() == {"detail": "Invalid cursor."}


# --------------------------------------------------------------------------
# Static files (SPA)
# --------------------------------------------------------------------------


def test_spa_static_files(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Budget</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "outside.txt").write_text("secret")
    add_users(settings)
    app = app_for(monkeypatch, dev=False, BUDGET_STATIC_DIR=str(static))
    for c in client_for(app, dev=False):
        assert "Budget" in c.get("/").text
        assert "Budget" in c.get("/accounts/detail").text
        r = c.get("/assets/app.js")
        assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
        assert "secret" not in c.get("/..%2Foutside.txt").text
        assert c.get("/api/nope").status_code == 404
        assert c.get("/api/accounts").status_code == 401
        assert c.get("/healthz").json() == {"ok": True, "db": "ok"}
        assert c.post("/", headers={"Origin": PROD_ORIGIN}).status_code == 405
