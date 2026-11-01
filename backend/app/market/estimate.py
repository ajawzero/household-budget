"""Price estimate on day D (PLAN 3.9, 6.2). Computed on request, never stored.

For an account with api-priced holdings, take the newest holdings snapshot
from a statement dated before D (period end d0). Then

    estimate(D) = value(d0) + sum over api holdings of
                  shares(D) * close(D) - quantity * close(d0)

where shares(D) = quantity * the splits that took effect after d0 through D,
and closes are raw closes that carry over weekends and holidays. In split
adjusted terms this is PLAN 3.9's quantity * split factor * (close(D) -
close(d0)). value(d0) is the stored balance on d0 (else the holdings' sum),
so the estimate equals the statement value on the statement date.

- Constant and statement holdings add no change.
- A holding with no close near d0 or D adds no change. With no api holding
  priced at all, there is no estimate (no est. badge).
- A stored balance after d0 (a later statement, a manual or valuation row)
  ends the estimate: real balances win.
- Covers every day of the range, back to the first snapshot.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from app.market.common import MICRO
from app.market.modes import Mode, mode_map
from app.market.store import PriceBook
from app.services.balances import Estimate, History

KIND = "price"
CENTS_DIVISOR = MICRO * MICRO // 100  # micro shares * micro dollars -> cents


def day_label(day: str) -> str:
    """Like "Sep 30"."""
    d = date.fromisoformat(day)
    return f"{d.strftime('%b')} {d.day}"


def note(price_date: str, statement_date: str) -> str:
    return (
        f"Estimated from prices as of {day_label(price_date)}. "
        f"Shares from the {day_label(statement_date)} statement."
    )


@dataclass(frozen=True)
class Holding:
    account_key: str
    as_of: str
    holding_key: str
    name: str
    symbol: str | None
    cusip: str | None
    asset_class: str | None
    quantity_micro: int | None
    price_micro: int | None
    value_cents: int
    mode: Mode
    cost_basis_cents: int | None = None


@dataclass(frozen=True)
class Snapshot:
    account_key: str
    as_of: str
    holdings: tuple[Holding, ...]

    @property
    def total_cents(self) -> int:
        return sum(h.value_cents for h in self.holdings)

    def api(self) -> list[Holding]:
        return [h for h in self.holdings if h.mode.mode == "api" and h.quantity_micro]


def snapshots(conn: sqlite3.Connection) -> dict[str, list[Snapshot]]:
    """account -> holdings snapshots of its current statements, oldest first.

    A statement with has_holdings and no rows is a snapshot with no holdings
    (everything was sold).
    """
    modes = mode_map(conn)
    rows: dict[tuple[str, str], list[Holding]] = {}
    for s in conn.execute(
        """SELECT account_key, period_end FROM statements
           WHERE is_current = 1 AND has_holdings = 1 ORDER BY account_key, period_end"""
    ):
        rows[(s["account_key"], s["period_end"])] = []
    for r in conn.execute(
        """SELECT h.* FROM holdings h JOIN statements s
             ON s.statement_key = h.statement_key AND s.is_current = 1
           ORDER BY h.account_key, h.as_of, h.holding_key"""
    ):
        k = (r["account_key"], r["as_of"])
        if k not in rows:
            continue
        mode = modes.get((r["account_key"], r["holding_key"]), Mode("statement", None, "rule"))
        rows[k].append(
            Holding(
                account_key=r["account_key"],
                as_of=r["as_of"],
                holding_key=r["holding_key"],
                name=r["holding_name"],
                symbol=r["symbol"],
                cusip=r["cusip"],
                asset_class=r["asset_class"],
                quantity_micro=r["quantity_micro"],
                price_micro=r["price_micro"],
                value_cents=r["value_cents"],
                mode=mode,
                cost_basis_cents=r["cost_basis_cents"],
            )
        )
    out: dict[str, list[Snapshot]] = {}
    for (acct, as_of), hs in sorted(rows.items()):
        out.setdefault(acct, []).append(Snapshot(acct, as_of, tuple(hs)))
    return out


@dataclass(frozen=True)
class Change:
    cents: Fraction  # value change since the statement
    shares_micro: int  # quantity after splits
    close_micro: int  # close used on D
    price_date: str


def holding_change(book: PriceBook, h: Holding, day: str) -> Change | None:
    """An api holding's change from its statement date to `day`, or None without closes.

    The close on the statement date carries at most CARRY_DAYS. The close on
    `day` is the newest one however old, so a failed price run keeps the last
    close ("price as of <date>").
    """
    if h.mode.symbol is None or not h.quantity_micro:
        return None
    c0 = book.close_on(h.mode.symbol, h.as_of)
    c1 = book.latest(h.mode.symbol, day)
    if c0 is None or c1 is None:
        return None
    if c1.date <= c0.date:
        return Change(Fraction(0), h.quantity_micro, c0.close_micro, c0.date)
    factor = book.split_factor(h.mode.symbol, c0.date, c1.date)
    shares = h.quantity_micro * factor
    cents = (shares * c1.close_micro - h.quantity_micro * c0.close_micro) / CENTS_DIVISOR
    return Change(cents, round(shares), c1.close_micro, c1.date)


def round_cents(value: Fraction) -> int:
    """Half away from zero."""
    sign = -1 if value < 0 else 1
    return sign * int(abs(value) + Fraction(1, 2))


def load_book(conn: sqlite3.Connection, snaps: dict[str, list[Snapshot]], end: str) -> PriceBook:
    symbols = {h.mode.symbol for ss in snaps.values() for s in ss for h in s.api()}
    starts = [s.as_of for ss in snaps.values() for s in ss if s.api()]
    if not symbols or not starts:
        return PriceBook({})
    return PriceBook.load(conn, {s for s in symbols if s}, min(starts), end)


def account_estimate(
    snap: Snapshot, base_cents: int, book: PriceBook, day: str
) -> tuple[int, Estimate] | None:
    """The estimate on `day` from one snapshot, or None when nothing is priced."""
    total = Fraction(0)
    dates = []
    for h in snap.api():
        ch = holding_change(book, h, day)
        if ch is None:
            continue
        total += ch.cents
        dates.append(ch.price_date)
    if not dates:
        return None
    through = min(dates)
    return base_cents + round_cents(total), Estimate(
        KIND, snap.as_of, through, note(through, snap.as_of)
    )


# --------------------------------------------------------------------------
# Estimator interface (app/services/balances.py)
# --------------------------------------------------------------------------


def signature(conn: sqlite3.Connection) -> tuple:
    p = conn.execute("SELECT count(*), max(fetched_at) FROM prices").fetchone()
    m = conn.execute(
        """SELECT group_concat(account_key || ':' || holding_key || ':' || price_mode
                               || ':' || coalesce(symbol, ''), ',')
           FROM (SELECT * FROM symbol_map ORDER BY account_key, holding_key)"""
    ).fetchone()
    return (tuple(p), m[0])


def estimate(conn: sqlite3.Connection, h: History) -> dict[str, dict[str, tuple[int, Estimate]]]:
    all_snaps = snapshots(conn)
    snaps = {
        k: v
        for k, v in all_snaps.items()
        if k in h.accounts
        and h.accounts[k]["tracked_by"] == "statements"
        and any(s.api() for s in v)
    }
    if not snaps:
        return {}
    book = load_book(conn, snaps, h.days[-1])
    out: dict[str, dict[str, tuple[int, Estimate]]] = {}
    for key, ss in snaps.items():
        closed_on = h.accounts[key]["closed_on"]
        dates = [s.as_of for s in ss]
        stored = h.stored_dates(key)
        days: dict[str, tuple[int, Estimate]] = {}
        for day in h.days:
            if closed_on is not None and closed_on < day:
                continue
            j = bisect_left(dates, day) - 1  # newest snapshot strictly before day
            if j < 0:
                continue
            snap = ss[j]
            if not snap.api():
                continue
            k = bisect_right(stored, snap.as_of)
            if k < len(stored) and stored[k] <= day:
                continue  # a newer real balance wins
            base = h.stored_balance(key, snap.as_of)
            if base is None:
                base = snap.total_cents
            found = account_estimate(snap, base, book, day)
            if found is not None:
                days[day] = found
        if days:
            out[key] = days
    return out
