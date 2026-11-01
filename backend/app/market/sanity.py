"""Statement price sanity check (PLAN 6.2).

For every api holding in a current statement that printed a price, compare
the stored provider close on the statement date (carried over weekends and
holidays) with the statement's price. A gap over 1% is flagged on Settings,
Prices. That catches a wrong ticker.

Computed on request, so it is current after every import and every price
run. Holdings with no stored close yet count as unchecked.
"""

from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from app.market.estimate import snapshots
from app.market.store import PriceBook

TOLERANCE = Fraction(1, 100)


def pct_str(value: Fraction) -> str:
    """A ratio as a percent string with two places: Fraction(123, 10_000) -> "1.23"."""
    d = Decimal(value.numerator) * 100 / Decimal(value.denominator)
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def sanity(conn: sqlite3.Connection) -> dict:
    names = {r["account_key"]: r["display_name"] for r in conn.execute("SELECT * FROM accounts")}
    snaps = snapshots(conn)
    checks = [
        h
        for ss in snaps.values()
        for s in ss
        for h in s.api()
        if h.price_micro and h.mode.symbol is not None
    ]
    if not checks:
        return {"checked": 0, "unchecked": 0, "flagged": []}
    book = PriceBook.load(
        conn,
        {h.mode.symbol for h in checks if h.mode.symbol},
        min(h.as_of for h in checks),
        max(h.as_of for h in checks),
    )
    checked = unchecked = 0
    flagged = []
    for h in checks:
        assert h.mode.symbol is not None and h.price_micro
        close = book.close_on(h.mode.symbol, h.as_of)
        if close is None:
            unchecked += 1
            continue
        checked += 1
        gap = Fraction(close.close_micro - h.price_micro, h.price_micro)
        if abs(gap) > TOLERANCE:
            flagged.append(
                {
                    "account_key": h.account_key,
                    "account_name": names.get(h.account_key, h.account_key),
                    "holding_key": h.holding_key,
                    "name": h.name,
                    "symbol": h.mode.symbol,
                    "statement_date": h.as_of,
                    "price_date": close.date,
                    "gap_pct": pct_str(gap),
                }
            )
    flagged.sort(key=lambda f: (f["statement_date"], f["account_key"], f["holding_key"]))
    flagged.reverse()
    return {"checked": checked, "unchecked": unchecked, "flagged": flagged}
