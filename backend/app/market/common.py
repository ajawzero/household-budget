"""Shared pieces of the price clients (PLAN 6.2).

A `Bar` is one daily close in integer micro-units. `close_micro` is the raw
close on that date, not adjusted for later splits. `split_factor_micro` is
the split that took effect that day (1_000_000 means none, 2_000_000 is 2 for 1).

A `ProviderError` has a short code and the HTTP status. It never holds a URL,
a key or a response body, so it is safe to log and to store in job_runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MICRO = 1_000_000
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")

# Error codes, in plain words for Settings > Prices.
ERROR_LABELS = {
    "rate_limited": "Rate limited. Tries again next run.",
    "server": "Provider error. Tries again next run.",
    "not_found": "Symbol not found.",
    "auth": "Key refused.",
    "bad_response": "Unexpected answer from the provider.",
    "timeout": "Timed out.",
    "network": "Could not reach the provider.",
    "no_data": "No prices in the date range.",
    "deferred": "Waiting for the next run (call limit).",
}


@dataclass(frozen=True)
class Bar:
    date: str
    close_micro: int
    adj_close_micro: int | None = None
    split_factor_micro: int = MICRO
    div_cash_micro: int = 0


class ProviderError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def status_error(status: int) -> ProviderError:
    """Map a non-200 HTTP status to an error code."""
    if status == 429:
        return ProviderError("rate_limited", status)
    if status >= 500:
        return ProviderError("server", status)
    if status == 404:
        return ProviderError("not_found", status)
    if status in (401, 403):
        return ProviderError("auth", status)
    return ProviderError("bad_response", status)


def to_micro(value: object) -> int | None:
    """A JSON number as micro-units, rounded half up. None for null or junk."""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    if not d.is_finite():
        return None
    return int((d * MICRO).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def provider_symbol(symbol: str) -> str:
    """Statements print class shares as BRK.B. Tiingo and Yahoo both use BRK-B."""
    return symbol.replace(".", "-")
