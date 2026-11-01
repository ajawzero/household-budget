"""FHFA House Price Index: fetch, store and read (PLAN 6.3).

- One GET of FHFA's public master file with a browser User-Agent. Nothing
  about the household is sent: no address, no ZIP code, no key. The file is
  read line by line and only the series below are kept.
- Values are upserted into `index_values` and never deleted, so a failed
  fetch keeps the last values ("index through 2026 Q2").
- Schedule: weekly, Wednesday 06:45 America/Chicago (FHFA releases on
  Tuesdays near month end), plus at startup when the last success is over 8
  days old. After a failed run it tries again once a day.

The curve: each quarter is anchored at its middle (Feb 15, May 15, Aug 15,
Nov 15) and each month at its 15th. Between anchors the index moves
log-linearly by day: I(d) = I_a * (I_b / I_a) ** ((d - a) / (b - a)). After
the last anchor it holds flat (the home value never moves
on a guess; it moves again when FHFA publishes the next quarter). The past
year's trend is still worked out and shown, for information only. Before the
first anchor it is flat. Seasonally adjusted series are preferred, so the estimate
does not swing with the seasons.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.config import HOUSEHOLD_TZ
from app.db.connect import connect, write_transaction
from app.demo.context import DemoRefused, in_demo
from app.jobs.scheduler import JobFailed, iso, last_success
from app.market import http
from app.market.http import BROWSER_UA

log = logging.getLogger("app.valuations")

JOB_NAME = "hpi"
MASTER_URL = "https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv"
TIMEOUT_SECONDS = 60.0
MAX_BYTES = 64 * 1024 * 1024  # the master file is about 17 MB
RUN_AT = time(6, 45)
RUN_WEEKDAY = 2  # Wednesday (Monday is 0)
SCHEDULE_LABEL = "Wednesdays 06:45 America/Chicago"
STARTUP_MAX_AGE = timedelta(days=8)
RETRY_AFTER = timedelta(hours=24)
MAX_TREND = 0.10  # cap on the past-year trend shown for information, either way
# Hold the index flat after the newest quarter. Set True to
# project the past-year trend instead.
PROJECT_AFTER_LAST = False
ATTRIBUTION = "Index: FHFA House Price Index. Not endorsed by FHFA."


@dataclass(frozen=True)
class Series:
    series_id: str
    short: str  # for notes: "FHFA Austin index"
    flavor: str
    frequency: str  # quarterly or monthly
    level: str
    place_id: str
    column: str  # index_sa or index_nsa


METRO = "12420"  # FHFA place id (CBSA) for Austin, TX
SERIES: tuple[Series, ...] = (
    Series(
        "fhfa:exp:austin:sa",
        "FHFA Austin index",
        "expanded-data",
        "quarterly",
        "MSA",
        METRO,
        "index_sa",
    ),
    Series(
        "fhfa:at:austin:nsa",
        "FHFA Austin index",
        "all-transactions",
        "quarterly",
        "MSA",
        METRO,
        "index_nsa",
    ),
    Series(
        "fhfa:po:tx:sa",
        "FHFA Texas index",
        "purchase-only",
        "quarterly",
        "State",
        "TX",
        "index_sa",
    ),
    Series(
        "fhfa:po:wsc:sa:m",
        "FHFA West South Central index",
        "purchase-only",
        "monthly",
        "USA or Census Division",
        "DV_WSC",
        "index_sa",
    ),
)
# Home estimates use the first of these that has values. The monthly
# division series is stored for reference only.
HOME_ORDER = (SERIES[0].series_id, SERIES[1].series_id, SERIES[2].series_id)
_BY_KEY = {(s.flavor, s.frequency, s.level, s.place_id): s for s in SERIES}
_BY_ID = {s.series_id: s for s in SERIES}


class FetchError(Exception):
    """The download failed. `code` is a short id, never a URL or a body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# A line source: GET the URL and yield its text lines. Tests pass a fake.
LineSource = Callable[[str, Mapping[str, str], float], Iterator[str]]


def urllib_lines(url: str, headers: Mapping[str, str], timeout: float) -> Iterator[str]:
    """Stream a text file line by line with urllib. Raises FetchError."""
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        resp = http.open_url(req, timeout)  # no redirects (app/market/http.py)
    except urllib.error.HTTPError as exc:
        raise FetchError(_status_code(exc.code)) from None
    except TimeoutError:
        raise FetchError("timeout") from None
    except urllib.error.URLError as exc:
        raise FetchError(http.url_error_code(exc)) from None
    except OSError:
        raise FetchError("network") from None
    with resp:
        read = 0
        try:
            for raw in resp:
                read += len(raw)
                if read > MAX_BYTES:
                    raise FetchError("bad_response")
                yield raw.decode("utf-8", errors="replace")
        except TimeoutError:
            raise FetchError("timeout") from None
        except OSError:
            raise FetchError("network") from None


def _status_code(status: int) -> str:
    if status == 429:
        return "rate_limited"
    if status == 404:
        return "not_found"
    if status >= 500:
        return "server"
    return "bad_response"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def period_start(year: int, period: int, freq: str) -> str:
    month = 3 * period - 2 if freq == "q" else period
    return date(year, month, 1).isoformat()


def anchor_date(start: str, freq: str) -> date:
    """The middle of the period: Feb 15 for Q1, the 15th for a month."""
    d = date.fromisoformat(start)
    return date(d.year, d.month + 1 if freq == "q" else d.month, 15)


def period_label(start: str, freq: str) -> str:
    d = date.fromisoformat(start)
    if freq == "q":
        return f"{d.year} Q{(d.month - 1) // 3 + 1}"
    return f"{d.strftime('%b')} {d.year}"


def _micro(text: str) -> int | None:
    text = text.strip()
    if not text or text == "-":
        return None  # suppressed (fewer than 10 sales) or missing
    try:
        v = Decimal(text)
    except InvalidOperation:
        return None
    return int((v * 1_000_000).to_integral_value()) if v > 0 else None


def parse_master(lines: Iterable[str]) -> dict[str, dict[str, tuple[str, int]]]:
    """series_id -> {period_start: (freq, value_micro)} for the kept series.

    Rows match on flavor, frequency, level and place id by column name, not
    by position. Blank values are skipped. A stray tab in `note` is ignored.
    """
    reader = csv.reader(line.rstrip("\r\n") for line in lines)
    header = next(reader, None)
    if header is None:
        raise FetchError("bad_response")
    cols = {name.strip(): i for i, name in enumerate(header)}
    need = ("hpi_type", "hpi_flavor", "frequency", "level", "place_id", "yr", "period")
    if any(c not in cols for c in (*need, "index_sa", "index_nsa")):
        raise FetchError("bad_response")
    out: dict[str, dict[str, tuple[str, int]]] = {}
    width = max(cols.values()) + 1
    for row in reader:
        if len(row) < width - 1:  # the note column may be cut off
            continue
        row = row + [""] * (width - len(row))
        if row[cols["hpi_type"]].strip() != "traditional":
            continue
        s = _BY_KEY.get(
            (
                row[cols["hpi_flavor"]].strip(),
                row[cols["frequency"]].strip(),
                row[cols["level"]].strip(),
                row[cols["place_id"]].strip(),
            )
        )
        if s is None:
            continue
        value = _micro(row[cols[s.column]])
        try:
            year, period = int(row[cols["yr"]]), int(row[cols["period"]])
        except ValueError:
            continue
        freq = "q" if s.frequency == "quarterly" else "m"
        if value is None or not (1 <= period <= (4 if freq == "q" else 12)):
            continue
        out.setdefault(s.series_id, {})[period_start(year, period, freq)] = (freq, value)
    return out


# --------------------------------------------------------------------------
# Storing and the job
# --------------------------------------------------------------------------


def store(
    conn: sqlite3.Connection, parsed: dict[str, dict[str, tuple[str, int]]], stamp: str
) -> int:
    """Upsert values. Returns rows inserted or changed. Call inside a write transaction."""
    changed = 0
    for series_id, values in parsed.items():
        for start, (freq, micro) in values.items():
            cur = conn.execute(
                """INSERT INTO index_values (series_id, period_start, freq, value_micro, fetched_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (series_id, period_start) DO UPDATE SET
                     freq = excluded.freq, value_micro = excluded.value_micro,
                     fetched_at = excluded.fetched_at
                   WHERE value_micro IS NOT excluded.value_micro OR freq IS NOT excluded.freq""",
                (series_id, start, freq, micro, stamp),
            )
            changed += cur.rowcount
        if values:
            conn.execute(
                "UPDATE index_series SET last_fetch_at = ?, last_period = ? WHERE series_id = ?",
                (stamp, max(values), series_id),
            )
    return changed


def run_hpi(conn: sqlite3.Connection, now: datetime, *, source: LineSource | None = None) -> dict:
    """Download, parse and store. Returns the job summary. Raises JobFailed.

    The summary holds series ids, period dates, counts and error codes only.
    Raises DemoRefused in demo, whatever the source.
    """
    if in_demo():
        raise DemoRefused("index fetch refused in demo")
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/csv"}
    fetch = source or urllib_lines  # looked up per call, so tests can block the network
    try:
        parsed = parse_master(fetch(MASTER_URL, headers, TIMEOUT_SECONDS))
    except FetchError as exc:
        log.warning("hpi fetch failed", extra={"code": exc.code})
        raise JobFailed({"error": exc.code}) from None
    if not parsed:
        raise JobFailed({"error": "no_data"})
    with write_transaction(conn):
        rows = store(conn, parsed, iso(now))
    summary = {
        "rows": rows,
        "series": {sid: max(vals) for sid, vals in sorted(parsed.items())},
    }
    log.info("hpi ran", extra={"series": len(parsed), "rows": rows})
    return summary


def last_slot(now: datetime) -> datetime:
    """The most recent Wednesday 06:45 America/Chicago at or before `now`, in UTC."""
    local = now.astimezone(HOUSEHOLD_TZ)
    day = local.date()
    for _ in range(8):
        slot = datetime.combine(day, RUN_AT, tzinfo=HOUSEHOLD_TZ)
        if day.weekday() == RUN_WEEKDAY and slot <= local:
            return slot.astimezone(UTC)
        day -= timedelta(days=1)
    raise AssertionError("unreachable")


def _last_failed(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT status FROM job_runs WHERE job = ? ORDER BY id DESC LIMIT 1", (JOB_NAME,)
    ).fetchone()
    return row is not None and row["status"] != "ok"


class HpiSchedule:
    """When the index job is due.

    First check: the last success is over 8 days old, or there is none.
    After that: each time the Wednesday 06:45 slot passes between two checks,
    or once a day after a failed run.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.checked_at: datetime | None = None
        self.started_at: datetime | None = None

    def __call__(self, now: datetime) -> bool:
        previous, self.checked_at = self.checked_at, now
        due = False
        conn = connect(self.db_path)
        try:
            if previous is None:
                last = last_success(conn, JOB_NAME)
                due = last is None or now - last > STARTUP_MAX_AGE
            elif previous < last_slot(now) <= now:
                due = True
            elif self.started_at is not None and now - self.started_at >= RETRY_AFTER:
                due = _last_failed(conn)
        finally:
            conn.close()
        if due:
            self.started_at = now
        return due


def hpi_job_parts(db_path: Path) -> tuple[Callable[[datetime], dict], HpiSchedule]:
    def run(now: datetime) -> dict:
        conn = connect(db_path)
        try:
            return run_hpi(conn, now)
        finally:
            conn.close()

    return run, HpiSchedule(db_path)


# --------------------------------------------------------------------------
# Reading: the curve
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Curve:
    series_id: str
    short: str
    anchors: tuple[tuple[date, float], ...]  # (anchor date, index level), oldest first
    last_start: str  # period_start of the newest value
    freq: str
    trend: float | None  # yearly log growth used after the last anchor, None if unknown

    @property
    def through_label(self) -> str:
        return period_label(self.last_start, self.freq)

    @property
    def last_anchor(self) -> date:
        return self.anchors[-1][0]

    def projected(self, d: date) -> bool:
        return PROJECT_AFTER_LAST and d > self.last_anchor and self.trend is not None

    def at(self, d: date) -> float:
        a = self.anchors
        if d <= a[0][0]:
            return a[0][1]
        if d >= a[-1][0]:
            if self.trend is None or not PROJECT_AFTER_LAST:
                return a[-1][1]
            return a[-1][1] * math.exp(self.trend * (d - a[-1][0]).days / 365.25)
        lo, hi = 0, len(a) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if a[mid][0] <= d:
                lo = mid
            else:
                hi = mid
        (da, ia), (db, ib) = a[lo], a[hi]
        return ia * (ib / ia) ** ((d - da).days / (db - da).days)


def make_curve(series_id: str, rows: list[tuple[str, str, int]]) -> Curve | None:
    """rows: (period_start, freq, value_micro), any order. None with under two values."""
    rows = sorted(rows)
    if len(rows) < 2:
        return None
    anchors = tuple((anchor_date(s, f), v / 1_000_000) for s, f, v in rows)
    last_d, last_v = anchors[-1]
    year_ago = last_d - timedelta(days=365)
    trend = None
    if anchors[0][0] <= year_ago:
        flat = Curve(series_id, "", anchors, rows[-1][0], rows[-1][1], None)
        g = math.log(last_v / flat.at(year_ago))
        trend = max(-MAX_TREND, min(MAX_TREND, g))
    series = _BY_ID.get(series_id)
    return Curve(
        series_id,
        series.short if series else series_id,
        anchors,
        rows[-1][0],
        rows[-1][1],
        trend,
    )


def load_curve(conn: sqlite3.Connection, order: Iterable[str] = HOME_ORDER) -> Curve | None:
    """The first series in `order` with at least two values, as a curve."""
    for series_id in order:
        rows = [
            (r["period_start"], r["freq"], r["value_micro"])
            for r in conn.execute(
                "SELECT period_start, freq, value_micro FROM index_values WHERE series_id = ?",
                (series_id,),
            )
        ]
        curve = make_curve(series_id, rows)
        if curve is not None:
            return curve
    return None


def status(conn: sqlite3.Connection) -> dict:
    """The index state for Settings and the valuations page. No values."""
    curve = load_curve(conn)
    run = conn.execute(
        "SELECT * FROM job_runs WHERE job = ? ORDER BY id DESC LIMIT 1", (JOB_NAME,)
    ).fetchone()
    error = None
    if run is not None and run["status"] != "ok":
        error = (json.loads(run["summary_json"] or "{}") or {}).get("error") or "failed"
    success = last_success(conn, JOB_NAME)
    row = (
        conn.execute(
            "SELECT * FROM index_series WHERE series_id = ?", (curve.series_id,)
        ).fetchone()
        if curve
        else None
    )
    return {
        "series_id": curve.series_id if curve else None,
        "label": row["label"] if row else None,
        "through": curve.last_start if curve else None,
        "through_label": curve.through_label if curve else None,
        "trend_milli_pct": round((math.exp(curve.trend) - 1) * 100_000)
        if curve and curve.trend is not None
        else None,
        "last_fetch_at": row["last_fetch_at"] if row else None,
        "last_success_at": success.isoformat(timespec="seconds") if success else None,
        "last_run_status": None if run is None else ("ok" if run["status"] == "ok" else "failed"),
        "error": error,
        "schedule": SCHEDULE_LABEL,
        "attribution": ATTRIBUTION,
    }
