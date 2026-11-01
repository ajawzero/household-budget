"""Tiingo end-of-day prices, the primary source (PLAN 6.2).

GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=&endDate=
with the header `Authorization: Token <key>`. The key never goes in the URL
(Tiingo also accepts `?token=`, which would leak into proxy and server logs).

The answer is a JSON list of days: date, close (raw), adjClose, splitFactor
(1.0 unless a split took effect that day) and divCash. Mutual fund NAVs post
after midnight ET, stock closes at 5:30 PM ET, so the 06:30 CT run sees both.

Free tier: 50 requests an hour, 1,000 a day, 500 symbols a month. Internal
use only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from urllib.parse import quote, urlencode

from app.market.common import (
    MICRO,
    Bar,
    ProviderError,
    provider_symbol,
    status_error,
    to_micro,
)
from app.market.http import DEFAULT_TIMEOUT, NetworkError, Transport, urllib_transport

SOURCE = "tiingo"
BASE_URL = "https://api.tiingo.com/tiingo/daily"


def prices_url(symbol: str, start: date, end: date) -> str:
    ticker = quote(provider_symbol(symbol).lower(), safe="-")
    query = urlencode({"startDate": start.isoformat(), "endDate": end.isoformat()})
    return f"{BASE_URL}/{ticker}/prices?{query}"


def headers(key: str) -> Mapping[str, str]:
    return {
        "Authorization": f"Token {key}",
        "Accept": "application/json",
        "User-Agent": "budget-app",
    }


def parse(body: bytes) -> list[Bar]:
    try:
        data = json.loads(body)
    except ValueError:
        raise ProviderError("bad_response", 200) from None
    if isinstance(data, dict):  # {"detail": "Error: Ticker 'X' not found"}
        detail = str(data.get("detail", "")).lower()
        raise ProviderError("not_found" if "not found" in detail else "bad_response", 200)
    if not isinstance(data, list):
        raise ProviderError("bad_response", 200)
    bars: dict[str, Bar] = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("date"), str):
            continue
        close = to_micro(item.get("close"))
        if close is None or close <= 0:
            continue
        split = to_micro(item.get("splitFactor"))
        day = item["date"][:10]
        bars[day] = Bar(
            date=day,
            close_micro=close,
            adj_close_micro=to_micro(item.get("adjClose")),
            split_factor_micro=split if split and split > 0 else MICRO,
            div_cash_micro=to_micro(item.get("divCash")) or 0,
        )
    return [bars[d] for d in sorted(bars)]


def fetch(
    symbol: str,
    start: date,
    end: date,
    key: str,
    transport: Transport = urllib_transport,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Bar]:
    """Daily bars from start to end (both included). Raises ProviderError."""
    try:
        resp = transport(prices_url(symbol, start, end), headers(key), timeout)
    except NetworkError as exc:
        raise ProviderError(exc.code) from None
    if resp.status != 200:
        raise status_error(resp.status)
    return parse(resp.body)
