"""Phase 5 market data (PLAN 3.5, 3.9, 6.2): price clients, the daily job, price
modes and overrides, the computed price estimate, the sanity check and the API.

No test reaches the network. Every request goes through `FakeTransport`,
which answers by URL prefix (like respx routes) and records each call.
The sample symbols (BRDMKT, BNDIDX) are made up.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from api_helpers import MEMBER, MEMBER_PW, PROD_ORIGIN, TODAY, login
from fastapi.testclient import TestClient
from test_logs import captured

from app import config
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.jobs.scheduler import JobFailed, Scheduler, price_job
from app.market import estimate as price_estimate
from app.market import job as price_job_module
from app.market import tiingo, yahoo
from app.market.common import Bar, ProviderError
from app.market.http import HttpResponse, NetworkError
from app.market.job import PriceSchedule, last_slot, run_prices
from app.market.modes import api_symbols, resolve, rule_mode
from app.market.store import upsert_bars
from app.services.balances import History, clear_cache, history

KEY = "tiingo-test-key-abc123"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)  # 07:00 in Chicago, a Tuesday
H = {"Origin": PROD_ORIGIN}
BROKER = "lakeview-brokerage"
SEP30 = "2026-09-30"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeTransport:
    """Answers by URL prefix. A route's last answer repeats. Records every call."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, list[HttpResponse | Exception]]] = []
        self.calls: list[tuple[str, dict[str, str]]] = []

    def add(self, prefix: str, *answers: HttpResponse | Exception) -> FakeTransport:
        self.routes.append((prefix, list(answers)))
        return self

    def __call__(self, url: str, headers: object, timeout: float) -> HttpResponse:
        self.calls.append((url, dict(headers)))  # type: ignore[call-overload]
        for prefix, answers in self.routes:
            if url.startswith(prefix):
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError("unexpected request")

    def hosts(self) -> list[str]:
        return ["tiingo" if "tiingo" in u else "yahoo" for u, _ in self.calls]


def tiingo_url(symbol: str) -> str:
    return f"https://api.tiingo.com/tiingo/daily/{symbol.lower().replace('.', '-')}/prices"


def yahoo_url(symbol: str) -> str:
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?"


def tiingo_ok(*days: tuple[str, float] | tuple[str, float, float]) -> HttpResponse:
    body = [
        {
            "date": f"{d[0]}T00:00:00.000Z",
            "close": d[1],
            "adjClose": d[1],
            "splitFactor": d[2] if len(d) > 2 else 1.0,
            "divCash": 0.0,
        }
        for d in days
    ]
    return HttpResponse(200, json.dumps(body).encode())


def _open(day: str) -> int:
    """09:30 New York on that day, as Yahoo stamps a daily bar."""
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, 13, 30, tzinfo=UTC).timestamp())


def yahoo_ok(
    days: list[tuple[str, float | None]],
    splits: tuple[tuple[str, int, int], ...] = (),
    fund: bool = False,
) -> HttpResponse:
    body = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                        "instrumentType": "MUTUALFUND" if fund else "EQUITY",
                    },
                    "timestamp": [_open(d) for d, _ in days],
                    "events": {
                        "splits": {
                            str(_open(d)): {"date": _open(d), "numerator": n, "denominator": m}
                            for d, n, m in splits
                        }
                    },
                    "indicators": {
                        "quote": [{"close": [c for _, c in days]}],
                        "adjclose": [{"adjclose": [c for _, c in days]}],
                    },
                }
            ],
            "error": None,
        }
    }
    return HttpResponse(200, json.dumps(body).encode())


def no_sleep(_: float) -> None:
    return None


class Sleeps(list):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


def micro(value: str) -> int:
    return int(Decimal(value) * 1_000_000)


def prices(conn: sqlite3.Connection, symbol: str) -> list[tuple[str, int, str]]:
    return [
        (r["date"], r["close_micro"], r["source"])
        for r in conn.execute("SELECT * FROM prices WHERE symbol = ? ORDER BY date", (symbol,))
    ]


def put_prices(conn: sqlite3.Connection, symbol: str, bars: list[Bar], source: str = "tiingo"):
    with write_transaction(conn):
        upsert_bars(conn, symbol, bars, source, "2026-10-20T12:00:00+00:00")
    clear_cache()


@pytest.fixture
def conn(samples: Settings):
    c = connect(samples.db_path)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import prices as prices_api
    from app.jobs import scheduler

    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    # The route imports this function directly; the CLI reads the job's UTC clock.
    monkeypatch.setattr(prices_api, "household_today", lambda: TODAY)
    monkeypatch.setattr(scheduler, "utc_now", lambda: NOW)


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------


def test_tiingo_key_in_header_never_in_url() -> None:
    fake = FakeTransport().add(
        tiingo_url("BRK.B"), tiingo_ok(("2026-10-16", 450.5), ("2026-10-19", 452.25))
    )
    bars = tiingo.fetch("BRK.B", date(2026, 10, 14), date(2026, 10, 20), KEY, fake)
    assert [(b.date, b.close_micro) for b in bars] == [
        ("2026-10-16", 450_500_000),
        ("2026-10-19", 452_250_000),
    ]
    (url, headers) = fake.calls[0]
    assert url == (
        "https://api.tiingo.com/tiingo/daily/brk-b/prices?startDate=2026-10-14&endDate=2026-10-20"
    )
    assert KEY not in url and "token" not in url.lower()
    assert headers["Authorization"] == f"Token {KEY}"


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (HttpResponse(429, b""), "rate_limited"),
        (HttpResponse(503, b""), "server"),
        (HttpResponse(404, b'{"detail": "Error: Ticker not found"}'), "not_found"),
        (HttpResponse(200, b'{"detail": "Error: Ticker \'X\' not found"}'), "not_found"),
        (HttpResponse(403, b""), "auth"),
        (HttpResponse(200, b"<html>"), "bad_response"),
        (NetworkError("timeout"), "timeout"),
    ],
)
def test_tiingo_errors(answer: HttpResponse | Exception, code: str) -> None:
    fake = FakeTransport().add(tiingo_url("ABC"), answer)
    with pytest.raises(ProviderError) as exc:
        tiingo.fetch("ABC", date(2026, 10, 1), date(2026, 10, 20), KEY, fake)
    assert exc.value.code == code
    assert KEY not in str(exc.value)


def test_redirect_never_carries_the_key_to_another_host() -> None:
    """Local servers only: a 302 from "Tiingo" to another host is refused, so the
    other host never sees the Authorization header."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.market.http import urllib_transport

    seen: list[str | None] = []

    class Other(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args: object) -> None:
            pass

    other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
    target = f"http://localhost:{other.server_address[1]}/x"

    class First(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    first = ThreadingHTTPServer(("127.0.0.1", 0), First)
    for server in (other, first):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{first.server_address[1]}/"
        resp = urllib_transport(url, {"Authorization": f"Token {KEY}"}, 5.0)
        assert resp.status == 302
        assert seen == []
        with pytest.raises(ProviderError) as exc:
            tiingo.fetch("ABC", date(2026, 10, 1), date(2026, 10, 20), KEY, lambda *a: resp)
        assert exc.value.code == "bad_response"
    finally:
        for server in (other, first):
            server.shutdown()
            server.server_close()


def test_connect_timeout_maps_to_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    from app.market import http

    class Slow:
        def open(self, req, timeout=None):  # noqa: ANN001, ANN201
            raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(http, "_OPENER", Slow())
    with pytest.raises(NetworkError) as exc:
        http.urllib_transport("https://example.invalid/", {}, 1.0)
    assert exc.value.code == "timeout"


def test_yahoo_unadjusts_splits_and_skips_open_bars() -> None:
    # 2 for 1 split on Oct 15. Yahoo's closes are split adjusted, so the
    # Oct 14 close of 100 was really 200 that day.
    answer = yahoo_ok(
        [("2026-10-14", 100.0), ("2026-10-15", 101.0), ("2026-10-16", None), ("2026-10-20", 103.0)],
        splits=(("2026-10-15", 2, 1),),
    )
    fake = FakeTransport().add(yahoo_url("ABC"), answer)
    bars = yahoo.fetch("ABC", date(2026, 10, 10), date(2026, 10, 20), NOW, fake)
    # Oct 16 has a null close. Oct 20 is today before 4 PM New York: intraday.
    assert [(b.date, b.close_micro, b.split_factor_micro) for b in bars] == [
        ("2026-10-14", 200_000_000, 1_000_000),
        ("2026-10-15", 101_000_000, 2_000_000),
    ]
    url, headers = fake.calls[0]
    assert "Mozilla" in headers["User-Agent"] and "period1=" in url and "events=div%2Csplit" in url


def test_yahoo_keeps_todays_fund_nav_and_reports_errors() -> None:
    fund = yahoo_ok([("2026-10-19", 50.0), ("2026-10-20", 50.5)], fund=True)
    bars = yahoo.fetch("ABCDX", date(2026, 10, 15), date(2026, 10, 20), NOW, lambda *a: fund)
    assert [b.date for b in bars] == ["2026-10-19", "2026-10-20"]
    missing = HttpResponse(
        404, json.dumps({"chart": {"result": None, "error": {"code": "Not Found"}}}).encode()
    )
    with pytest.raises(ProviderError) as exc:
        yahoo.fetch("NOPE", date(2026, 10, 15), date(2026, 10, 20), NOW, lambda *a: missing)
    assert exc.value.code == "not_found"
    with pytest.raises(ProviderError) as exc:
        yahoo.fetch(
            "ABC", date(2026, 10, 15), date(2026, 10, 20), NOW, lambda *a: HttpResponse(429, b"")
        )
    assert exc.value.code == "rate_limited"


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


def test_rule_modes() -> None:
    assert rule_mode("broad-market", "BRDMKT", "us-stock").mode == "api"
    assert rule_mode("core", "SPAXX", None).mode == "constant"
    assert rule_mode("cash", None, "cash").mode == "constant"
    assert rule_mode("mm", None, "money-market").mode == "constant"
    assert rule_mode("target-date-trust", None, "target-date").mode == "statement"
    override = {"price_mode": "api", "symbol": "VFIAX"}
    assert resolve("target-date-trust", None, None, override).symbol == "VFIAX"
    assert resolve("x", None, None, {"price_mode": "api", "symbol": None}).mode == "statement"
    assert (
        resolve("x", "ABC", None, {"price_mode": "statement", "symbol": None}).mode == "statement"
    )


def test_api_symbols_from_samples(conn: sqlite3.Connection) -> None:
    # Bond index was sold in June but is still needed for its earlier statements.
    assert api_symbols(conn) == {"BNDIDX": "2026-01-31", "BRDMKT": "2026-01-31"}


# --------------------------------------------------------------------------
# The job
# --------------------------------------------------------------------------


def test_job_without_key_does_nothing(conn: sqlite3.Connection, samples: Settings) -> None:
    fake = FakeTransport()
    assert run_prices(conn, None, NOW, transport=fake, sleep=no_sleep) == {"skipped": "no_key"}
    assert fake.calls == []
    assert PriceSchedule(samples.db_path, has_key=False)(NOW) is False


def test_job_tiingo_first_then_yahoo_fallback(conn: sqlite3.Connection) -> None:
    fake = (
        FakeTransport()
        .add(tiingo_url("BNDIDX"), HttpResponse(404, b"{}"))
        .add(yahoo_url("BNDIDX"), yahoo_ok([("2026-10-16", 99.0), ("2026-10-19", 99.5)]))
        .add(tiingo_url("BRDMKT"), tiingo_ok(("2026-10-16", 258.0), ("2026-10-19", 259.0)))
    )
    sleeps = Sleeps()
    summary = run_prices(conn, KEY, NOW, transport=fake, sleep=sleeps)
    # BNDIDX: Tiingo 404, then Yahoo. BRDMKT: Tiingo only.
    assert fake.hosts() == ["tiingo", "yahoo", "tiingo"]
    assert sleeps == [1.5, 1.5]
    assert summary["sources"] == {"BNDIDX": "yahoo", "BRDMKT": "tiingo"}
    assert summary["errors"] == {} and summary["calls"] == {"tiingo": 2, "yahoo": 1}
    assert prices(conn, "BRDMKT") == [
        ("2026-10-16", 258_000_000, "tiingo"),
        ("2026-10-19", 259_000_000, "tiingo"),
    ]
    assert prices(conn, "BNDIDX")[-1] == ("2026-10-19", 99_500_000, "yahoo")
    # First run: from 7 days before the first statement (Jan 31) through today.
    first = fake.calls[0][0]
    assert "startDate=2026-01-24&endDate=2026-10-20" in first
    # Re-run: from the newest stored date minus 5 days, and nothing changes.
    again = (
        FakeTransport()
        .add(tiingo_url("BNDIDX"), HttpResponse(404, b"{}"))
        .add(yahoo_url("BNDIDX"), yahoo_ok([("2026-10-16", 99.0), ("2026-10-19", 99.5)]))
        .add(tiingo_url("BRDMKT"), tiingo_ok(("2026-10-16", 258.0), ("2026-10-19", 259.0)))
    )
    summary = run_prices(conn, KEY, NOW, transport=again, sleep=no_sleep)
    assert "startDate=2026-10-14&endDate=2026-10-20" in again.calls[2][0]
    assert summary["rows"] == 0


def test_job_stale_tiingo_falls_back_and_yahoo_never_replaces_tiingo(
    conn: sqlite3.Connection,
) -> None:
    put_prices(conn, "BRDMKT", [Bar("2026-10-09", 250_000_000)])
    fake = (
        FakeTransport()
        .add(tiingo_url("BNDIDX"), tiingo_ok(("2026-10-19", 99.0)))
        # Tiingo's newest close is 11 days old: stale.
        .add(tiingo_url("BRDMKT"), tiingo_ok(("2026-10-09", 250.0)))
        .add(yahoo_url("BRDMKT"), yahoo_ok([("2026-10-09", 251.0), ("2026-10-19", 255.0)]))
    )
    summary = run_prices(conn, KEY, NOW, transport=fake, sleep=no_sleep)
    assert fake.hosts() == ["tiingo", "tiingo", "yahoo"]
    assert summary["sources"]["BRDMKT"] == "yahoo"
    assert prices(conn, "BRDMKT") == [
        ("2026-10-09", 250_000_000, "tiingo"),  # Tiingo's row stays
        ("2026-10-19", 255_000_000, "yahoo"),
    ]


def test_job_429_stops_tiingo_for_the_run(conn: sqlite3.Connection) -> None:
    fake = (
        FakeTransport()
        .add(tiingo_url("BNDIDX"), HttpResponse(429, b""))
        .add(yahoo_url("BNDIDX"), HttpResponse(429, b""))
        .add(tiingo_url("BRDMKT"), tiingo_ok(("2026-10-19", 259.0)))
    )
    put_prices(conn, "BNDIDX", [Bar("2026-06-12", 98_000_000)])
    with pytest.raises(JobFailed) as exc:  # nothing was updated
        run_prices(conn, KEY, NOW, transport=fake, sleep=no_sleep)
    summary = exc.value.summary
    # One Tiingo call, one Yahoo call, then both are stopped: BRDMKT is not asked.
    assert fake.hosts() == ["tiingo", "yahoo"]
    assert summary["stopped"] == ["tiingo", "yahoo"]
    assert summary["errors"] == {"BNDIDX": "rate_limited", "BRDMKT": "rate_limited"}
    # The last close is kept.
    assert prices(conn, "BNDIDX") == [("2026-06-12", 98_000_000, "tiingo")]


def test_job_5xx_backs_off_then_succeeds(conn: sqlite3.Connection) -> None:
    fake = (
        FakeTransport()
        .add(
            tiingo_url("BNDIDX"),
            HttpResponse(502, b""),
            HttpResponse(503, b""),
            tiingo_ok(("2026-10-19", 99.0)),
        )
        .add(tiingo_url("BRDMKT"), tiingo_ok(("2026-10-19", 259.0)))
    )
    sleeps = Sleeps()
    summary = run_prices(conn, KEY, NOW, transport=fake, sleep=sleeps)
    assert fake.hosts() == ["tiingo"] * 4
    assert sleeps == [2.0, 1.5, 4.0, 1.5, 1.5]
    assert summary["errors"] == {} and summary["calls"]["tiingo"] == 4


def test_job_call_cap_defers_the_rest(conn: sqlite3.Connection, monkeypatch) -> None:
    monkeypatch.setattr(price_job_module, "MAX_TIINGO_CALLS", 1)
    fake = FakeTransport().add(tiingo_url("BNDIDX"), tiingo_ok(("2026-10-19", 99.0)))
    summary = run_prices(conn, KEY, NOW, transport=fake, sleep=no_sleep)
    assert fake.hosts() == ["tiingo"]
    assert summary["deferred"] == ["BRDMKT"] and summary["errors"] == {}


def test_job_fails_when_every_symbol_fails(conn: sqlite3.Connection) -> None:
    fake = (
        FakeTransport()
        .add("https://api.tiingo.com/", NetworkError("network"))
        .add("https://query1.finance.yahoo.com/", NetworkError("timeout"))
    )
    with pytest.raises(JobFailed) as exc:
        run_prices(conn, KEY, NOW, transport=fake, sleep=no_sleep)
    assert exc.value.summary["errors"] == {"BNDIDX": "network", "BRDMKT": "network"}


def test_no_key_in_any_url_log_or_job_row(
    samples: Settings,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("TIINGO_API_KEY", KEY)
    config.get_settings.cache_clear()
    s = config.get_settings()
    fake = (
        FakeTransport()
        .add(tiingo_url("BNDIDX"), HttpResponse(500, b"{}"))
        .add(yahoo_url("BNDIDX"), yahoo_ok([("2026-10-19", 99.0)]))
        .add(tiingo_url("BRDMKT"), tiingo_ok(("2026-10-19", 259.0)))
    )
    monkeypatch.setattr(price_job_module, "run_prices", _with(fake))
    job = price_job(s, clock=lambda: NOW)
    Scheduler([job], s.db_path, clock=lambda: NOW).run_job(job, NOW)
    config.get_settings.cache_clear()
    assert fake.calls and all(KEY not in url for url, _ in fake.calls)
    assert all("token" not in url.lower() for url, _ in fake.calls)
    logs = captured(caplog, capsys.readouterr().out)
    assert KEY not in logs and "259" not in logs
    rows = conn.execute("SELECT status, summary_json FROM job_runs WHERE job = 'prices'").fetchall()
    assert [r["status"] for r in rows] == ["ok"]
    assert KEY not in rows[0]["summary_json"] and "259" not in rows[0]["summary_json"]


def _with(fake: FakeTransport):
    real = run_prices

    def run(conn, key, now, **_: object) -> dict:
        return real(conn, key, now, transport=fake, sleep=no_sleep)

    return run


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------


def test_last_slot_runs_tuesday_to_saturday() -> None:
    chicago = config.HOUSEHOLD_TZ
    # Monday 10:00 Chicago: the last slot is Saturday 06:30.
    monday = datetime(2026, 10, 19, 10, 0, tzinfo=chicago)
    assert last_slot(monday) == datetime(2026, 10, 17, 6, 30, tzinfo=chicago).astimezone(UTC)
    tuesday_early = datetime(2026, 10, 20, 6, 0, tzinfo=chicago)
    assert last_slot(tuesday_early) == last_slot(monday)
    tuesday = datetime(2026, 10, 20, 6, 31, tzinfo=chicago)
    assert last_slot(tuesday) == datetime(2026, 10, 20, 6, 30, tzinfo=chicago).astimezone(UTC)


def test_schedule_startup_catch_up_then_slots(samples: Settings, conn) -> None:
    sched = PriceSchedule(samples.db_path, has_key=True)
    assert sched(NOW) is True  # never ran: startup catch-up
    with write_transaction(conn):
        conn.execute(
            """INSERT INTO job_runs (job, started_at, finished_at, status, summary_json)
               VALUES ('prices', ?, ?, 'ok', '{}')""",
            (NOW.isoformat(), NOW.isoformat()),
        )
        upsert_bars(conn, "BRDMKT", [Bar("2026-10-19", 1_000_000)], "tiingo", NOW.isoformat())
        upsert_bars(conn, "BNDIDX", [Bar("2026-10-19", 1_000_000)], "tiingo", NOW.isoformat())
    fresh = PriceSchedule(samples.db_path, has_key=True)
    assert fresh(NOW + timedelta(hours=1)) is False  # recent success, no missing symbol
    # Wednesday 06:30 Chicago passes between two checks.
    assert fresh(datetime(2026, 10, 21, 11, 29, tzinfo=UTC)) is False
    assert fresh(datetime(2026, 10, 21, 11, 31, tzinfo=UTC)) is True
    assert fresh(datetime(2026, 10, 21, 12, 0, tzinfo=UTC)) is False
    # Saturday runs. Sunday and Monday do not.
    assert fresh(datetime(2026, 10, 24, 12, 0, tzinfo=UTC)) is True
    assert fresh(datetime(2026, 10, 25, 12, 0, tzinfo=UTC)) is False
    assert fresh(datetime(2026, 10, 26, 12, 0, tzinfo=UTC)) is False
    assert fresh(datetime(2026, 10, 27, 12, 0, tzinfo=UTC)) is True


# --------------------------------------------------------------------------
# Estimate (PLAN 3.9)
# --------------------------------------------------------------------------


def _statement(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Sep 30 balance, BRDMKT quantity and price."""
    bal = conn.execute(
        "SELECT balance_cents FROM balances WHERE account_key = ? AND as_of = ?", (BROKER, SEP30)
    ).fetchone()[0]
    h = conn.execute(
        """SELECT quantity_micro, price_micro FROM holdings
           WHERE account_key = ? AND as_of = ? AND holding_key = 'broad-market'""",
        (BROKER, SEP30),
    ).fetchone()
    return bal, h[0], h[1]


def test_estimate_equals_statement_value_then_moves(conn: sqlite3.Connection) -> None:
    bal, qty, price = _statement(conn)
    put_prices(
        conn,
        "BRDMKT",
        [Bar("2026-09-30", price), Bar("2026-10-01", price), Bar("2026-10-16", price + 3_000_000)],
    )
    h = History(conn, date(2026, 9, 30), date(2026, 10, 20))
    days = h.daily(BROKER)
    assert days[0].balance_cents == bal and days[0].estimate is None  # the statement day
    assert days[1].balance_cents == bal and days[1].estimate is not None  # same close
    change = round(Decimal(qty) * 3_000_000 / price_estimate.CENTS_DIVISOR)
    assert days[-1].balance_cents == bal + change
    est = days[-1].estimate
    assert est.kind == "price" and est.based_on == SEP30 and est.through == "2026-10-16"
    assert est.note == "Estimated from prices as of Oct 16. Shares from the Sep 30 statement."
    # Real numbers only: no estimate (Checks uses this).
    real = History(conn, date(2026, 10, 20), date(2026, 10, 20), estimates=False)
    assert real.at_end(BROKER).balance_cents == bal and real.at_end(BROKER).estimate is None
    # The cached history picks up new prices.
    assert history(conn, date(2026, 10, 20), date(2026, 10, 20)).at_end(BROKER).balance_cents == (
        bal + change
    )


def test_estimate_applies_splits(conn: sqlite3.Connection) -> None:
    bal, qty, price = _statement(conn)
    put_prices(
        conn,
        "BRDMKT",
        [
            Bar("2026-09-30", price),
            Bar("2026-10-09", price),
            Bar("2026-10-12", price // 2, split_factor_micro=2_000_000),  # 2 for 1, flat
            Bar("2026-10-13", price // 2 + 1_000_000),
        ],
    )
    h = History(conn, date(2026, 10, 9), date(2026, 10, 13))
    by_day = dict(zip(h.days, h.daily(BROKER), strict=True))
    # A split alone does not move the value (rounding of the halved price aside).
    assert abs(by_day["2026-10-12"].balance_cents - bal) <= 1
    expected = round(
        Decimal(qty) * 2 * (price // 2 + 1_000_000) / price_estimate.CENTS_DIVISOR
        - Decimal(qty) * price / price_estimate.CENTS_DIVISOR
    )
    assert by_day["2026-10-13"].balance_cents == bal + expected


def test_real_balance_wins_and_constant_adds_nothing(conn: sqlite3.Connection) -> None:
    bal, _, price = _statement(conn)
    put_prices(conn, "BRDMKT", [Bar("2026-09-30", price), Bar("2026-10-16", price * 2)])
    with write_transaction(conn):
        conn.execute(
            """INSERT INTO balances (account_key, as_of, source, balance_cents, run_id)
               VALUES (?, '2026-10-15', 'manual', 12_345, 'test')""",
            (BROKER,),
        )
    clear_cache()
    h = History(conn, date(2026, 10, 14), date(2026, 10, 16))
    days = h.daily(BROKER)
    assert days[0].estimate is not None
    assert days[1].balance_cents == 12_345 and days[1].estimate is None
    assert days[2].balance_cents == 12_345 and days[2].estimate is None
    # Statement-mode override: no api holding left, so no estimate at all.
    with write_transaction(conn):
        conn.execute("DELETE FROM balances WHERE run_id = 'test'")
        conn.execute(
            """INSERT INTO symbol_map (account_key, holding_key, price_mode, updated_at)
               VALUES (?, 'broad-market', 'statement', 'now')""",
            (BROKER,),
        )
    clear_cache()
    assert History(conn, date(2026, 10, 16), date(2026, 10, 16)).at_end(BROKER).balance_cents == bal
    assert (
        price_estimate.estimate(conn, History(conn, date(2026, 10, 16), date(2026, 10, 16))) == {}
    )


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@pytest.fixture
def full(samples: Settings, admin: TestClient) -> TestClient:
    return admin


def test_holdings_allocation_and_history(conn: sqlite3.Connection, full: TestClient) -> None:
    bal, qty, price = _statement(conn)
    put_prices(conn, "BRDMKT", [Bar("2026-09-30", price), Bar("2026-10-19", price + 1_000_000)])
    r = full.get("/api/investments/holdings")
    assert r.status_code == 200
    body = r.json()
    broker = next(g for g in body["groups"] if g["key"] == BROKER)
    rows = {h["holding_key"]: h for h in broker["holdings"]}
    stock = rows["broad-market"]
    assert stock["mode"] == "api" and stock["estimated"] and stock["price_date"] == "2026-10-19"
    assert stock["value_cents"] == stock["statement_value_cents"] + round(Decimal(qty) / 10**4)
    assert rows["cash"]["mode"] == "constant" and rows["cash"]["price_date"] is None
    # The account's estimate matches the holdings' total.
    accounts = full.get("/api/accounts").json()
    acct = next(a for g in accounts["groups"] for a in g["accounts"] if a["account_key"] == BROKER)
    assert acct["estimate"]["kind"] == "price"
    assert acct["balance_cents"] == bal + round(Decimal(qty) / 10**4)
    retirement = next(g for g in body["groups"] if g["key"] == "alex-401k")
    trust = retirement["holdings"][0]
    assert trust["mode"] == "statement" and not trust["estimated"]
    assert not trust["concentrated"]  # 100% of the account, but a target-date fund
    assert not rows["cash"]["concentrated"]
    # Gain on cost uses the value shown (here a price estimate). Cash has none.
    assert stock["cost_basis_cents"] == 3_467_434
    assert stock["gain_cents"] == stock["value_cents"] - 3_467_434
    assert rows["cash"]["gain_cents"] is None and rows["cash"]["gain_pct"] is None
    assert (broker["basis_holdings"], broker["no_basis_holdings"]) == (1, 0)
    assert broker["gain_cents"] == stock["gain_cents"]
    assert broker["account_type"] == "brokerage"
    # The 401k trust has no cost basis: no gain line, and it counts as missing.
    assert trust["gain_cents"] is None
    assert retirement["gain_cents"] is None and retirement["no_basis_holdings"] == 1
    assert retirement["gain_share_pct"] is None
    # The kids 529: gains as a share of the account.
    college = next(g for g in body["groups"] if g["key"] == "kids-529")
    assert college["account_type"] == "529"
    assert (college["cost_basis_cents"], college["gain_cents"]) == (1_755_000, 434_048)
    assert (college["gain_pct"], college["gain_share_pct"]) == ("24.7", "19.8")
    # Notes and dormant come from the registry, as stored.
    for g in body["groups"]:
        note = conn.execute(
            "SELECT notes FROM accounts WHERE account_key = ?", (g["key"],)
        ).fetchone()[0]
        assert g["notes"] == note and g["dormant"] is False

    by_class = full.get("/api/investments/holdings?group_by=class").json()
    assert {g["key"] for g in by_class["groups"]} >= {"us-stock", "cash", "target-date"}
    assert by_class["total_cents"] == body["total_cents"]
    for g in by_class["groups"]:
        assert (g["notes"], g["dormant"], g["account_type"]) == (None, False, None)
    assert full.get("/api/investments/holdings?group_by=owner").status_code == 422

    alloc = full.get("/api/investments/allocation").json()
    assert sum(c["value_cents"] for c in alloc["classes"]) == alloc["total_cents"]

    hist = full.get("/api/investments/history?range=3M").json()
    assert hist["points"][-1]["estimated"] is True
    seg = next(s for s in hist["estimated_segments"] if s["account_key"] == BROKER)
    assert seg["based_on"] == SEP30 and seg["end"] == TODAY.isoformat()
    assert any(p["date"] == SEP30 and p["account_key"] == BROKER for p in hist["statement_points"])
    assert hist["latest_price_date"] == "2026-10-19"

    # 1D and 7D count back from the newest close (Oct 19), not from today: closes are
    # stored the morning after, so 1D from today would always be flat. 1D shows the move
    # to the Oct 19 close.
    by_day = {p["date"]: p["value_cents"] for p in hist["points"]}
    newest = date.fromisoformat(hist["latest_price_date"])
    for key, days in (("1D", 1), ("7D", 7)):
        short = full.get(f"/api/investments/history?range={key}").json()
        start = newest - timedelta(days=days)
        assert short["range"] == {"key": key, "start": start.isoformat(), "end": TODAY.isoformat()}
        assert len(short["points"]) == (TODAY - start).days + 1
        assert short["points"][-1] == hist["points"][-1]
        assert short["points"][0]["value_cents"] == by_day[start.isoformat()]
    one_day = full.get("/api/investments/history?range=1D").json()["points"]
    assert one_day[-1]["value_cents"] - one_day[0]["value_cents"] == round(Decimal(qty) / 10**4)
    assert full.get("/api/investments/history?range=2D").status_code == 422


def test_gain_on_cost_edge_cases_notes_and_dormant(conn: sqlite3.Connection) -> None:
    from app.services.investments import holdings_page

    sep = conn.execute(
        """SELECT statement_key, run_id FROM statements
           WHERE account_key = 'kids-529' AND period_end = ? AND is_current = 1""",
        (SEP30,),
    ).fetchone()
    with write_transaction(conn):
        # A second fund in the 529 with no cost basis.
        conn.execute(
            """INSERT INTO holdings (statement_key, account_key, as_of, holding_key,
                   holding_name, value_cents, asset_class, run_id)
               VALUES (?, 'kids-529', ?, 'bond-portfolio', 'Bond Portfolio', ?, 'bond', ?)""",
            (sep["statement_key"], SEP30, 10_000, sep["run_id"]),
        )
        # A basis of 0 gives a gain but no percent.
        conn.execute(
            """UPDATE holdings SET cost_basis_cents = 0
               WHERE account_key = 'kids-529' AND holding_key = 'age-based-portfolio'"""
        )
        # Cash with a basis still has no gain and is not missing one.
        conn.execute(
            """UPDATE holdings SET cost_basis_cents = 100
               WHERE account_key = ? AND holding_key = 'cash'""",
            (BROKER,),
        )
        conn.execute(
            """UPDATE accounts SET dormant = 1, notes = 'Gift money only.'
               WHERE account_key = 'kids-529'"""
        )
    clear_cache()
    groups = {g["key"]: g for g in holdings_page(conn)["groups"]}
    college = groups["kids-529"]
    rows = {h["holding_key"]: h for h in college["holdings"]}
    portfolio = rows["age-based-portfolio"]
    assert portfolio["gain_cents"] == portfolio["value_cents"] and portfolio["gain_pct"] is None
    assert rows["bond-portfolio"]["gain_cents"] is None
    assert (college["basis_holdings"], college["no_basis_holdings"]) == (1, 1)
    assert college["gain_cents"] == portfolio["value_cents"]
    assert college["gain_pct"] is None and college["gain_share_pct"] is None
    assert (college["notes"], college["dormant"]) == ("Gift money only.", True)
    broker = groups[BROKER]
    cash = next(h for h in broker["holdings"] if h["holding_key"] == "cash")
    assert cash["cost_basis_cents"] == 100 and cash["gain_cents"] is None
    assert (broker["basis_holdings"], broker["no_basis_holdings"]) == (1, 0)
    assert broker["dormant"] is False
    by_class = holdings_page(conn, "class")["groups"]
    assert all((g["notes"], g["dormant"]) == (None, False) for g in by_class)


def test_checks_never_count_estimates(conn: sqlite3.Connection, full: TestClient) -> None:
    # Without its Sep 30 balance, the brokerage carries Aug 31 into the Checks
    # date (Sep 30), so a price estimate would apply there.
    with write_transaction(conn):
        conn.execute("DELETE FROM balances WHERE account_key = ? AND as_of = ?", (BROKER, SEP30))
    clear_cache()
    before = full.get("/api/checks").json()
    aug = conn.execute(
        """SELECT price_micro FROM holdings WHERE account_key = ? AND as_of = '2026-08-31'
           AND holding_key = 'broad-market'""",
        (BROKER,),
    ).fetchone()[0]
    put_prices(conn, "BRDMKT", [Bar("2026-08-31", aug), Bar("2026-09-29", aug * 2)])
    day = date(2026, 9, 30)
    assert history(conn, day, day).at_end(BROKER).estimate is not None
    after = full.get("/api/checks").json()
    assert before["as_of"] == SEP30
    assert after["net_worth"] == before["net_worth"]


def test_prices_status_and_sanity(conn: sqlite3.Connection, full: TestClient) -> None:
    _, _, price = _statement(conn)
    # Sep 30 close 2% above the statement price: flagged.
    put_prices(conn, "BRDMKT", [Bar("2026-09-30", price * 102 // 100), Bar("2026-10-19", price)])
    body = full.get("/api/prices/status").json()
    assert body["tiingo_key_set"] is False
    assert body["schedule"] == "06:30 America/Chicago, Tuesday to Saturday"
    assert body["last_run"] is None
    syms = {s["symbol"]: s for s in body["symbols"]}
    assert syms["BRDMKT"]["last_date"] == "2026-10-19" and not syms["BRDMKT"]["stale"]
    assert syms["BNDIDX"]["last_date"] is None and syms["BNDIDX"]["stale"]
    flags = body["sanity"]["flagged"]
    assert [(f["symbol"], f["statement_date"], f["gap_pct"]) for f in flags] == [
        ("BRDMKT", SEP30, "2.00")
    ]
    assert body["sanity"]["checked"] == 1


def test_prices_status_shows_key_set_and_errors(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_helpers import ADMIN, ADMIN_PW, add_users, app_for, client_for

    add_users(samples)
    with write_transaction(c := connect(samples.db_path)):
        c.execute(
            """INSERT INTO job_runs (job, started_at, finished_at, status, summary_json)
               VALUES ('prices', ?, ?, 'ok', ?)""",
            (
                NOW.isoformat(),
                NOW.isoformat(),
                json.dumps(
                    {
                        "symbols": 2,
                        "updated": 1,
                        "calls": {"tiingo": 2, "yahoo": 1},
                        "errors": {"BNDIDX": "not_found"},
                        "stopped": [],
                    }
                ),
            ),
        )
    c.close()
    app = app_for(monkeypatch, dev=False, TIINGO_API_KEY=KEY)
    for client in client_for(app, dev=False):
        assert login(client, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        r = client.get("/api/prices/status")
        assert KEY not in r.text
        body = r.json()
        assert body["tiingo_key_set"] is True
        assert body["last_run"]["tiingo_calls"] == 2
        bnd = next(s for s in body["symbols"] if s["symbol"] == "BNDIDX")
        assert bnd["error"] == "not_found" and bnd["error_label"] == "Symbol not found."
        assert client.get("/api/symbols").json()["tiingo_key_set"] is True


def test_symbols_get_put(conn: sqlite3.Connection, full: TestClient) -> None:
    rows = full.get("/api/symbols").json()["rows"]
    trust = next(r for r in rows if r["holding_key"] == "target-date-trust")
    assert trust["rule_mode"] == "statement" and trust["override"] is None

    def put(body: dict) -> object:
        return full.put("/api/symbols", json=body, headers=H)

    base = {"account_key": "alex-401k", "holding_key": "target-date-trust"}
    r = put({**base, "price_mode": "api", "symbol": "tdtx", "note": "Same fund as the trust"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "api" and out["symbol"] == "TDTX" and out["override"]["note"]
    assert "TDTX" in api_symbols(conn)
    # Rules for typed text: a note with a long digit run is refused without echo.
    r = put({**base, "price_mode": "statement", "note": "acct " + "1234" * 4})
    assert r.status_code == 422 and "1234" * 4 not in r.text
    assert put({**base, "price_mode": "constant", "symbol": "ABC"}).status_code == 422
    assert put({**base, "price_mode": "api", "symbol": "bad symbol!"}).status_code == 422
    assert put({**base, "price_mode": "api"}).status_code == 422  # no printed symbol
    assert put({**base, "holding_key": "nope", "price_mode": None}).status_code == 404
    r = put({**base, "price_mode": None})
    assert r.status_code == 200 and r.json()["override"] is None and r.json()["mode"] == "statement"
    ev = conn.execute(
        "SELECT detail_json FROM events WHERE entity = 'symbol_map' ORDER BY id"
    ).fetchall()
    assert [json.loads(e[0]) for e in ev] == [{"price_mode": "api"}, {"price_mode": None}]


def test_symbols_put_is_admin_only(samples: Settings, prod: TestClient) -> None:
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    assert prod.get("/api/symbols").status_code == 200
    r = prod.put(
        "/api/symbols",
        json={"account_key": BROKER, "holding_key": "cash", "price_mode": "statement"},
        headers=H,
    )
    assert r.status_code == 403


def test_cli_prices(
    samples: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app import cli

    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    config.get_settings.cache_clear()
    assert cli.main(["prices"]) == 1
    assert "TIINGO_API_KEY is not set" in capsys.readouterr().err
    monkeypatch.setenv("TIINGO_API_KEY", KEY)
    config.get_settings.cache_clear()
    fake = FakeTransport().add("https://api.tiingo.com/", tiingo_ok(("2026-10-19", 10.0)))
    monkeypatch.setattr(price_job_module, "run_prices", _with(fake))
    assert cli.main(["prices"]) == 0
    out = capsys.readouterr().out
    assert "2 of 2 symbols updated" in out and KEY not in out and "10.0" not in out
    config.get_settings.cache_clear()
