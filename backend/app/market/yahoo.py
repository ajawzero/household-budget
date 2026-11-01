"""Yahoo's chart endpoint, the fallback (PLAN 6.2). Unofficial, no key.

GET https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
    ?period1=<epoch>&period2=<epoch>&interval=1d&events=div,split

Only for symbols where Tiingo errors or is stale. Needs a browser-like
User-Agent (429 without one). No cookie or crumb.

Answer: chart.result[0] with meta (exchangeTimezoneName, instrumentType),
timestamp[] (epoch seconds at the market open), indicators.quote[0].close[],
indicators.adjclose[0].adjclose[] and events.splits / events.dividends.
Errors are in chart.error.

Rules:
- Dates come from the timestamps in the exchange time zone.
- A null close is skipped (a fund's NAV for today is null until it posts).
- Today's bar for a stock or ETF is skipped before 4 PM exchange time, since
  it is an intraday price, not a close.
- Yahoo's `close` is adjusted for splits. The app stores raw closes, like
  Tiingo, so each close is multiplied back by the splits that took effect
  after its date. Every such split is inside the answer, because the range
  always runs through today.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from fractions import Fraction
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.market.common import MICRO, Bar, ProviderError, provider_symbol, status_error, to_micro
from app.market.http import (
    BROWSER_UA,
    DEFAULT_TIMEOUT,
    NetworkError,
    Transport,
    urllib_transport,
)

SOURCE = "yahoo"
BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
DEFAULT_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time(0, 0), tzinfo=UTC).timestamp())


def chart_url(symbol: str, start: date, end: date) -> str:
    ticker = quote(provider_symbol(symbol), safe="-")
    query = urlencode(
        {
            "period1": _epoch(start),
            "period2": _epoch(end + timedelta(days=1)),
            "interval": "1d",
            "events": "div,split",
        }
    )
    return f"{BASE_URL}/{ticker}?{query}"


HEADERS: Mapping[str, str] = {"User-Agent": BROWSER_UA, "Accept": "application/json"}


def _tz(meta: dict) -> ZoneInfo:
    name = meta.get("exchangeTimezoneName")
    if isinstance(name, str):
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            pass
    return DEFAULT_TZ


def _splits(events: dict, tz: ZoneInfo) -> list[tuple[str, Fraction]]:
    out = []
    for e in (events.get("splits") or {}).values():
        try:
            ratio = Fraction(int(e["numerator"]), int(e["denominator"]))
            day = datetime.fromtimestamp(int(e["date"]), tz).date().isoformat()
        except KeyError, TypeError, ValueError, ZeroDivisionError:
            continue
        if ratio > 0:
            out.append((day, ratio))
    return out


def _dividends(events: dict, tz: ZoneInfo) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in (events.get("dividends") or {}).values():
        try:
            day = datetime.fromtimestamp(int(e["date"]), tz).date().isoformat()
        except KeyError, TypeError, ValueError:
            continue
        amount = to_micro(e.get("amount"))
        if amount:
            out[day] = amount
    return out


def parse(body: bytes, now: datetime) -> list[Bar]:
    """Bars from a chart answer. `now` decides whether today's bar is a close yet."""
    try:
        chart = json.loads(body)["chart"]
    except ValueError, KeyError, TypeError:
        raise ProviderError("bad_response", 200) from None
    if chart.get("error"):
        code = str((chart["error"] or {}).get("code", "")).lower()
        raise ProviderError("not_found" if "not found" in code else "bad_response", 200)
    results = chart.get("result") or []
    if not results:
        raise ProviderError("not_found", 200)
    res = results[0]
    meta = res.get("meta") or {}
    tz = _tz(meta)
    stamps = res.get("timestamp") or []
    indicators = res.get("indicators") or {}
    closes = ((indicators.get("quote") or [{}])[0] or {}).get("close") or []
    adj = ((indicators.get("adjclose") or [{}])[0] or {}).get("adjclose") or []
    events = res.get("events") or {}
    splits = _splits(events, tz)
    divs = _dividends(events, tz)
    local_now = now.astimezone(tz)
    is_fund = meta.get("instrumentType") == "MUTUALFUND"

    bars: dict[str, Bar] = {}
    for i, stamp in enumerate(stamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        try:
            day = datetime.fromtimestamp(int(stamp), tz).date()
        except TypeError, ValueError, OverflowError:
            continue
        if day == local_now.date() and not is_fund and local_now.time() < MARKET_CLOSE:
            continue  # intraday
        iso = day.isoformat()
        later = Fraction(1)
        for split_day, ratio in splits:
            if split_day > iso:
                later *= ratio
        adjusted = to_micro(close)
        if adjusted is None or adjusted <= 0:
            continue
        raw = round(adjusted * later)
        on_day = Fraction(1)
        for split_day, ratio in splits:
            if split_day == iso:
                on_day *= ratio
        bars[iso] = Bar(
            date=iso,
            close_micro=raw,
            adj_close_micro=to_micro(adj[i]) if i < len(adj) else None,
            split_factor_micro=round(on_day * MICRO),
            div_cash_micro=divs.get(iso, 0),
        )
    return [bars[d] for d in sorted(bars)]


def fetch(
    symbol: str,
    start: date,
    end: date,
    now: datetime,
    transport: Transport = urllib_transport,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Bar]:
    """Daily bars from start to end (both included). Raises ProviderError."""
    try:
        resp = transport(chart_url(symbol, start, end), HEADERS, timeout)
    except NetworkError as exc:
        raise ProviderError(exc.code) from None
    if resp.status != 200:
        raise status_error(resp.status)
    return [b for b in parse(resp.body, now) if start.isoformat() <= b.date <= end.isoformat()]
