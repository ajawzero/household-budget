"""The daily price job (PLAN 6.2).

Schedule: 06:30 America/Chicago, Tuesday to Saturday (the prior day's closes,
and fund NAVs that post after midnight ET). At startup it runs when the last
success is over 24 hours old. It also runs, at most every 6 hours, when an
api symbol has no stored price at all (a new import added it).

With no TIINGO_API_KEY the job never runs and Settings says so.

Per run, per api symbol (deduped across accounts):
- Window: from the newest stored date minus 5 days through today, so gaps
  heal. A symbol with no prices starts 7 days before its first statement.
- Tiingo first. At most 45 Tiingo calls per run; the rest wait for the next
  run. A 429 stops Tiingo for this run. A 5xx is retried twice with backoff.
- Yahoo when Tiingo errors (not on the call limit) or is stale (no close in
  the last 5 days of the window). A Yahoo 429 stops Yahoo for this run.
- If both fail, the last stored close is kept ("price as of <date>").
- 1.5 seconds between calls.

job_runs summary: counts, calls per provider, and per-symbol source and
error codes. Tickers and codes only, never a price, a URL or the key.
"""

from __future__ import annotations

import logging
import sqlite3
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from app.config import HOUSEHOLD_TZ, Settings
from app.db.connect import connect, write_transaction
from app.demo.context import DemoRefused, in_demo
from app.jobs.scheduler import JobFailed, iso, last_success
from app.market import tiingo, yahoo
from app.market.common import Bar, ProviderError
from app.market.http import Transport, urllib_transport
from app.market.modes import api_symbols
from app.market.store import last_dates, upsert_bars

log = logging.getLogger("app.market")

JOB_NAME = "prices"
RUN_AT = time(6, 30)
RUN_WEEKDAYS = frozenset({1, 2, 3, 4, 5})  # Tuesday to Saturday (Monday is 0)
SCHEDULE_LABEL = "06:30 America/Chicago, Tuesday to Saturday"
STARTUP_MAX_AGE = timedelta(hours=24)
NEW_SYMBOL_RETRY = timedelta(hours=6)
MAX_TIINGO_CALLS = 45
LOOKBACK_DAYS = 5
FIRST_LEAD_DAYS = 7
STALE_DAYS = 5
PAUSE_SECONDS = 1.5
BACKOFF_SECONDS = (2.0, 4.0)  # one wait before each 5xx retry

Sleep = Callable[[float], None]


@dataclass
class RunState:
    calls: dict[str, int] = field(default_factory=lambda: {"tiingo": 0, "yahoo": 0})
    stopped: set[str] = field(default_factory=set)  # providers stopped by a 429
    first_call: bool = True


def household_date(now: datetime) -> date:
    return now.astimezone(HOUSEHOLD_TZ).date()


def _pause(state: RunState, sleep: Sleep) -> None:
    if not state.first_call:
        sleep(PAUSE_SECONDS)
    state.first_call = False


def _tiingo(
    symbol: str,
    start: date,
    end: date,
    key: str,
    state: RunState,
    transport: Transport,
    sleep: Sleep,
) -> list[Bar]:
    """Tiingo with 5xx retries. Raises ProviderError. Counts every call."""
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        if state.calls["tiingo"] >= MAX_TIINGO_CALLS:
            raise ProviderError("deferred")
        _pause(state, sleep)
        state.calls["tiingo"] += 1
        try:
            return tiingo.fetch(symbol, start, end, key, transport)
        except ProviderError as exc:
            if exc.code == "rate_limited":
                state.stopped.add("tiingo")
                raise
            if exc.code != "server" or attempt == len(BACKOFF_SECONDS):
                raise
            sleep(BACKOFF_SECONDS[attempt])
    raise AssertionError("unreachable")


def _yahoo(
    symbol: str,
    start: date,
    end: date,
    now: datetime,
    state: RunState,
    transport: Transport,
    sleep: Sleep,
) -> list[Bar]:
    _pause(state, sleep)
    state.calls["yahoo"] += 1
    try:
        return yahoo.fetch(symbol, start, end, now, transport)
    except ProviderError as exc:
        if exc.code == "rate_limited":
            state.stopped.add("yahoo")
        raise


def _stale(bars: list[Bar], end: date) -> bool:
    return not bars or bars[-1].date < (end - timedelta(days=STALE_DAYS)).isoformat()


def window(symbol: str, first_needed: str, last: dict[str, tuple[str, str]], today: date) -> date:
    """The start date to fetch from for one symbol."""
    if symbol in last:
        return date.fromisoformat(last[symbol][0]) - timedelta(days=LOOKBACK_DAYS)
    return date.fromisoformat(first_needed) - timedelta(days=FIRST_LEAD_DAYS)


def run_prices(
    conn: sqlite3.Connection,
    key: str | None,
    now: datetime,
    *,
    transport: Transport = urllib_transport,
    sleep: Sleep = _time.sleep,
) -> dict:
    """Fetch and store prices for every api symbol. Returns the job summary.

    Raises JobFailed when every symbol failed (and there was at least one).
    Raises DemoRefused in demo, whatever the transport.
    """
    if in_demo():
        raise DemoRefused("price fetch refused in demo")
    if not key:
        return {"skipped": "no_key"}
    today = household_date(now)
    needed = api_symbols(conn)
    last = last_dates(conn)
    state = RunState()
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}
    deferred: list[str] = []
    rows = 0
    stamp = iso(now)
    for symbol, first in needed.items():
        start = min(window(symbol, first, last, today), today)
        tiingo_bars: list[Bar] = []
        error: str | None = None
        if "tiingo" in state.stopped:
            error = "rate_limited"
        else:
            try:
                tiingo_bars = _tiingo(symbol, start, today, key, state, transport, sleep)
            except ProviderError as exc:
                error = exc.code
        if error == "deferred":
            deferred.append(symbol)
            continue
        yahoo_bars: list[Bar] = []
        if (error is not None or _stale(tiingo_bars, today)) and "yahoo" not in state.stopped:
            try:
                yahoo_bars = _yahoo(symbol, start, today, now, state, transport, sleep)
            except ProviderError as exc:
                error = error or exc.code
            else:
                error = None if yahoo_bars or tiingo_bars else (error or "no_data")
        elif error is None and not tiingo_bars:
            error = "no_data"
        with write_transaction(conn):
            rows += upsert_bars(conn, symbol, tiingo_bars, tiingo.SOURCE, stamp)
            rows += upsert_bars(conn, symbol, yahoo_bars, yahoo.SOURCE, stamp)
        if tiingo_bars or yahoo_bars:
            newest = max(
                [(b.date, tiingo.SOURCE) for b in tiingo_bars]
                + [(b.date, yahoo.SOURCE) for b in yahoo_bars]
            )
            sources[symbol] = newest[1]
        if error is not None:
            errors[symbol] = error
    summary = {
        "symbols": len(needed),
        "updated": len(sources),
        "rows": rows,
        "calls": state.calls,
        "sources": sources,
        "errors": errors,
        "deferred": deferred,
        "stopped": sorted(state.stopped),
    }
    log.info(
        "prices ran",
        extra={
            "symbols": len(needed),
            "updated": len(sources),
            "errors": len(errors),
            "tiingo_calls": state.calls["tiingo"],
            "yahoo_calls": state.calls["yahoo"],
        },
    )
    if needed and not sources and not deferred:
        raise JobFailed(summary)
    return summary


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------


def last_slot(now: datetime) -> datetime:
    """The most recent 06:30 America/Chicago on a run day, at or before `now`, in UTC."""
    local = now.astimezone(HOUSEHOLD_TZ)
    day = local.date()
    for _ in range(8):
        slot = datetime.combine(day, RUN_AT, tzinfo=HOUSEHOLD_TZ)
        if day.weekday() in RUN_WEEKDAYS and slot <= local:
            return slot.astimezone(UTC)
        day -= timedelta(days=1)
    raise AssertionError("unreachable")


def missing_symbols(conn: sqlite3.Connection) -> list[str]:
    """Api symbols with no stored price at all."""
    have = set(last_dates(conn))
    return [s for s in api_symbols(conn) if s not in have]


class PriceSchedule:
    """When the price job is due.

    First check: the last success is over 24 hours old, or there is none.
    After that: each time a 06:30 slot on a run day passes between two
    checks. Any check: an api symbol has no price yet and the job has not
    started for that reason in the last 6 hours. Never without a key.
    """

    def __init__(self, db_path: Path, has_key: bool) -> None:
        self.db_path = db_path
        self.has_key = has_key
        self.checked_at: datetime | None = None
        self.new_symbol_run_at: datetime | None = None

    def __call__(self, now: datetime) -> bool:
        if not self.has_key:
            return False
        previous, self.checked_at = self.checked_at, now
        conn = connect(self.db_path)
        try:
            if previous is None:
                last = last_success(conn, JOB_NAME)
                if last is None or now - last > STARTUP_MAX_AGE:
                    return True
            elif previous < last_slot(now) <= now:
                return True
            if (
                self.new_symbol_run_at is None or now - self.new_symbol_run_at >= NEW_SYMBOL_RETRY
            ) and missing_symbols(conn):
                self.new_symbol_run_at = now
                return True
        finally:
            conn.close()
        return False


def price_job_parts(settings: Settings) -> tuple[Callable[[datetime], dict], PriceSchedule]:
    """The run function and its schedule, for jobs/scheduler.py."""
    key = settings.tiingo_api_key.get_secret_value() if settings.tiingo_api_key else None

    def run(now: datetime) -> dict:
        conn = connect(settings.db_path)
        try:
            return run_prices(conn, key, now)
        finally:
            conn.close()

    return run, PriceSchedule(settings.db_path, key is not None)
