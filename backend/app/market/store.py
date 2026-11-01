"""The `prices` table: upsert bars, and read closes back (PLAN 3.5, 6.2).

- Upsert on (symbol, date). A row is rewritten only when its numbers change,
  so a re-run changes nothing (fetched_at included).
- A Yahoo row never replaces a Tiingo row for the same day.
- Closes carry over weekends and holidays: the close on D is the newest close
  on or before D, up to CARRY_DAYS back.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from app.market.common import MICRO, Bar

CARRY_DAYS = 7  # longest market closure plus a weekend


def upsert_bars(
    conn: sqlite3.Connection, symbol: str, bars: Iterable[Bar], source: str, now: str
) -> int:
    """Write bars for one symbol. Returns how many rows were added or changed.

    Call inside a write transaction.
    """
    changed = 0
    for b in bars:
        cur = conn.execute(
            """INSERT INTO prices (symbol, date, close_micro, adj_close_micro,
                   split_factor_micro, div_cash_micro, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (symbol, date) DO UPDATE SET
                 close_micro = excluded.close_micro,
                 adj_close_micro = excluded.adj_close_micro,
                 split_factor_micro = excluded.split_factor_micro,
                 div_cash_micro = excluded.div_cash_micro,
                 source = excluded.source,
                 fetched_at = excluded.fetched_at
               WHERE (prices.source != 'tiingo' OR excluded.source = 'tiingo')
                 AND (prices.close_micro != excluded.close_micro
                      OR prices.adj_close_micro IS NOT excluded.adj_close_micro
                      OR prices.split_factor_micro != excluded.split_factor_micro
                      OR prices.div_cash_micro != excluded.div_cash_micro
                      OR prices.source != excluded.source)""",
            (
                symbol,
                b.date,
                b.close_micro,
                b.adj_close_micro,
                b.split_factor_micro,
                b.div_cash_micro,
                source,
                now,
            ),
        )
        changed += cur.rowcount
    return changed


def last_dates(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """symbol -> (newest date, its source)."""
    return {
        r["symbol"]: (r["date"], r["source"])
        for r in conn.execute(
            """SELECT p.symbol, p.date, p.source FROM prices p
               JOIN (SELECT symbol, max(date) AS d FROM prices GROUP BY symbol) m
                 ON m.symbol = p.symbol AND m.d = p.date"""
        )
    }


@dataclass(frozen=True)
class Close:
    date: str
    close_micro: int


class PriceBook:
    """Closes and splits for some symbols over a date range, in memory."""

    def __init__(self, rows: dict[str, list[tuple[str, int, int]]]) -> None:
        # symbol -> [(date, close_micro, split_factor_micro)] in date order
        self._rows = rows
        self._dates = {s: [r[0] for r in v] for s, v in rows.items()}
        self._splits = {s: [(r[0], r[2]) for r in v if r[2] != MICRO] for s, v in rows.items()}

    @classmethod
    def load(
        cls, conn: sqlite3.Connection, symbols: Iterable[str], start: str, end: str
    ) -> PriceBook:
        syms = sorted(set(symbols))
        rows: dict[str, list[tuple[str, int, int]]] = {s: [] for s in syms}
        if not syms:
            return cls(rows)
        first = (date.fromisoformat(start) - timedelta(days=CARRY_DAYS)).isoformat()
        marks = ", ".join("?" for _ in syms)
        for r in conn.execute(
            f"""SELECT symbol, date, close_micro, split_factor_micro FROM prices
                WHERE symbol IN ({marks}) AND date BETWEEN ? AND ?
                ORDER BY symbol, date""",
            (*syms, first, end),
        ):
            rows[r["symbol"]].append((r["date"], r["close_micro"], r["split_factor_micro"]))
        return cls(rows)

    def close_on(self, symbol: str, day: str) -> Close | None:
        """The newest close on or before `day`, at most CARRY_DAYS old."""
        dates = self._dates.get(symbol)
        if not dates:
            return None
        i = bisect_right(dates, day) - 1
        if i < 0:
            return None
        d, close, _ = self._rows[symbol][i]
        if (date.fromisoformat(day) - date.fromisoformat(d)).days > CARRY_DAYS:
            return None
        return Close(d, close)

    def latest(self, symbol: str, day: str) -> Close | None:
        """The newest close on or before `day`, however old."""
        dates = self._dates.get(symbol)
        if not dates:
            return None
        i = bisect_right(dates, day) - 1
        if i < 0:
            return None
        d, close, _ = self._rows[symbol][i]
        return Close(d, close)

    def split_factor(self, symbol: str, after: str, through: str) -> Fraction:
        """Product of the splits that took effect after `after`, up to `through`."""
        out = Fraction(1)
        for d, split in self._splits.get(symbol, ()):
            if after < d <= through:
                out *= Fraction(split, MICRO)
        return out
