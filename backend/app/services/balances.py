"""Balance per account on each day, and net worth (PLAN 3.9).

Rules used here:
- Cash-like accounts (checking, savings, cards, loans) whose current statement
  covers the date D and is transaction level: that statement's `balance` when
  set, else its closing, minus its live rows posted after D (post date, else
  transaction date). When two statements cover D, the later period end wins.
  Transaction level means it has rows and they bridge the period: opening plus
  the rows equals closing (opening unknown: rows are enough). A statement
  without such rows (a quarterly savings summary) only gives its closing point.
- Otherwise the latest balance on or before D, from statement, valuation,
  manual or estimate rows (carry forward). Stored estimates come from the
  monthly run, for loans with no statement (the two vehicle loans).
- Computed estimates (Phase 5) are laid over the real series: the price
  estimate for investment accounts (app/market/estimate.py), and the home,
  vehicle and vehicle loan estimates (app/valuations/estimate.py). They are never
  stored, never replace a real balance on the same day (one exception: a
  camper conversion is added on top of the KBB value it is not part of, see
  `Estimate.adds_part`), and `History(...,
  estimates=False)` leaves them out (the Checks page). A day with an estimate
  carries `estimate` (how it was made and from what date) for the est. badge.
- A current statement's `opening` is the balance at the start of its period, so
  it is a balance point on `period_start` (source 'statement'). It ranks below
  every stored source, so a real balance on the same day always wins. Skipped
  when the statement has a separate `balance` (plan balances), because the
  opening leaves those out. Point-in-time reports have no opening.
- On a tie (same as_of), source order wins: statement, manual, valuation, estimate.
- Balances are signed from the net worth view: assets positive, owed negative.
- After `closed_on` the balance is 0.
- Before the first balance, on or after `opened_on` (or when that is unknown),
  the account is missing: `balance_cents = None` ("No balance"), which marks
  net worth partial. Never a silent 0. Before `opened_on` it does not exist
  yet: it counts as 0 and is not missing.
- A balance is stale once its account is past its next expected statement
  plus 15 days (monthly or quarterly cadence), or past `stale_after_days`.
- Cards tracked by payments have no balance. They show source 'tracked'
  ("Tracked by payments"), are never in net worth and are never missing.

`History` computes every account's daily step series for one date range once.
`history()` caches it by (database, import signature, estimate inputs, range),
so a page that asks for several series reads the tables once.

Estimators: modules listed in `_estimators()`. Each has `signature(conn)` (a
tuple that changes when its inputs change, for the cache) and
`estimate(conn, h)`, which returns {account_key: {day: (cents, Estimate)}}
for days with no real balance of their own. They read `h.real_daily()`,
`h.stored_dates()` and `h.stored_balance()`, never `h.daily()`.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from threading import Lock

from app.config import household_today
from app.importer.models import CASH_LIKE_TYPES

GRACE_DAYS = 15
CADENCE_DAYS = {"monthly": 31, "quarterly": 92}
CADENCE_MONTHS = {"monthly": 1, "quarterly": 3}
SOURCE_RANK = {"statement": 0, "manual": 1, "valuation": 2, "estimate": 3}
OPENING = "opening"  # internal source of an opening point; shown as 'statement'
_POINT_RANK = {**SOURCE_RANK, OPENING: len(SOURCE_RANK)}


@dataclass(frozen=True)
class Estimate:
    """How a computed estimate was made. Never stored."""

    kind: str  # price, home, vehicle or loan
    based_on: str  # date of the real balance or statement it starts from
    through: str | None  # newest price or index date used
    note: str  # tooltip text, plain words, no amounts
    # True when the estimate adds a part the real balance leaves out (a camper
    # conversion that KBB does not count), so it also applies on that real
    # balance's own day. Every other estimate yields to a real balance.
    adds_part: bool = False


@dataclass(frozen=True)
class LatestBalance:
    account_key: str
    balance_cents: int | None  # None: no balance on that day (missing, not open, tracked)
    as_of: str | None
    source: str | None  # statement, manual, valuation, 'closed' or 'tracked'
    stale: bool = False
    opened: bool = True  # False before opened_on: counts as 0, not missing
    estimate: Estimate | None = None  # set when balance_cents is a computed estimate

    @property
    def missing(self) -> bool:
        return self.balance_cents is None and self.source != "tracked" and self.opened

    @property
    def value_cents(self) -> int | None:
        """The amount to add into totals. 0 before the account opened, None when missing."""
        if not self.opened:
            return 0
        return self.balance_cents


@dataclass(frozen=True)
class NetWorth:
    as_of: str
    value_cents: int
    assets_cents: int
    liabilities_cents: int  # positive: money owed
    groups: dict[str, int] = field(default_factory=dict)  # nw_group -> signed cents
    missing: tuple[str, ...] = ()  # accounts with no balance
    stale: tuple[str, ...] = ()

    @property
    def partial(self) -> bool:
        return bool(self.missing or self.stale)


def days_between(start: date, end: date) -> list[str]:
    """ISO dates from start to end, both included."""
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _stale_after(acct: dict, as_of: str) -> str | None:
    """The last day a balance from `as_of` is still fresh, or None if it never goes stale."""
    days = CADENCE_DAYS.get(acct["statement_cadence"])
    if days is not None:
        days += GRACE_DAYS
    elif acct["stale_after_days"] is not None:
        days = acct["stale_after_days"]
    else:
        return None
    return (date.fromisoformat(as_of) + timedelta(days=days)).isoformat()


def _add_months(day: date, months: int) -> date:
    """`months` later on the same day, clamped to the month's end. A month's last day
    steps to the target month's last day (Sep 30 to Oct 31), like coverage.py."""
    total = day.year * 12 + day.month - 1 + months
    year, month = divmod(total, 12)
    month_end = (day + timedelta(days=1)).day == 1
    for d in (31, 30, 29, 28) if month_end else (day.day, 30, 29, 28):
        try:
            return date(year, month + 1, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def next_due(acct: dict, as_of: str) -> str | None:
    """When the next balance was due after one from `as_of`: one statement period
    later (a month, or three months for quarterly), else `stale_after_days`. None
    when the account never goes stale. The stale rule adds GRACE_DAYS on top."""
    months = CADENCE_MONTHS.get(acct["statement_cadence"])
    if months is not None:
        return _add_months(date.fromisoformat(as_of), months).isoformat()
    if acct["stale_after_days"] is not None:
        return (date.fromisoformat(as_of) + timedelta(days=acct["stale_after_days"])).isoformat()
    return None


def _bridges(statement: sqlite3.Row, posted: list[tuple[str, int]]) -> bool:
    """True when the statement's rows explain the whole period (PLAN 3.9).

    Rows must exist, and opening plus the rows must equal closing when the
    opening is known. Otherwise walking the closing back over the period
    would put the period's change on the wrong days.
    """
    if statement["txn_count"] == 0 or not posted:
        return False
    if statement["opening_cents"] is None:
        return True
    return statement["opening_cents"] + sum(a for _, a in posted) == statement["closing_cents"]


class History:
    """Every account's balance on every day from start to end (both included)."""

    def __init__(
        self, conn: sqlite3.Connection, start: date, end: date, *, estimates: bool = True
    ) -> None:
        self.start, self.end = start, end
        self.days = days_between(start, end)
        self.accounts: dict[str, dict] = {
            r["account_key"]: dict(r)
            for r in conn.execute(
                """SELECT * FROM accounts
                   ORDER BY account_group, coalesce(display_order, 1000000), display_name"""
            )
        }
        self._points = self._load_points(conn)
        self._covered = self._load_covered(conn)
        self._real: dict[str, list[LatestBalance]] = {}
        self._daily: dict[str, list[LatestBalance]] = {}
        self._estimates: dict[str, dict[str, tuple[int, Estimate]]] = {}
        if estimates:
            for module in _estimators():
                for key, days in module.estimate(conn, self).items():
                    self._estimates.setdefault(key, {}).update(days)

    # ---- loading ----

    def _load_points(self, conn: sqlite3.Connection) -> dict[str, list[tuple[str, str, int]]]:
        """account -> [(as_of, source, cents)] up to the end, best source last within a day.

        Stored balances plus one opening point per current statement (see the
        module rules). Openings rank last, so they never beat a real balance.
        """
        out: dict[str, list[tuple[str, str, int]]] = {}
        end = self.days[-1]
        for r in conn.execute(
            """SELECT account_key, as_of, source, balance_cents FROM balances
               WHERE as_of <= ?
               UNION ALL
               SELECT account_key, period_start, ?, opening_cents FROM statements
               WHERE is_current = 1 AND opening_cents IS NOT NULL AND period_start <= ?
                 AND (balance_cents IS NULL OR balance_cents = closing_cents)""",
            (end, OPENING, end),
        ):
            out.setdefault(r["account_key"], []).append(
                (r["as_of"], r["source"], r["balance_cents"])
            )
        for pts in out.values():
            pts.sort(key=lambda p: (p[0], -_POINT_RANK[p[1]]))
        return out

    def _load_covered(self, conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
        """account -> {day: balance} for days in range that a current statement covers.

        Cash-like, statement-tracked accounts only, and only transaction-level
        statements (see `_bridges`). Others fall back to their balance points.
        """
        first, last = self.days[0], self.days[-1]
        types = sorted(CASH_LIKE_TYPES)
        marks = ", ".join("?" for _ in types)
        statements = conn.execute(
            f"""SELECT s.statement_key, s.account_key, s.period_start, s.period_end,
                       s.opening_cents, s.closing_cents, s.txn_count,
                       COALESCE(s.balance_cents, s.closing_cents) AS total
                FROM statements s JOIN accounts a ON a.account_key = s.account_key
                WHERE s.is_current = 1 AND s.period_start <= ? AND s.period_end >= ?
                  AND a.tracked_by = 'statements' AND a.type IN ({marks})
                ORDER BY s.account_key, s.period_end""",
            (last, first, *types),
        ).fetchall()
        rows: dict[str, list[tuple[str, int]]] = {}
        if statements:
            keys = [s["statement_key"] for s in statements]
            for r in conn.execute(
                f"""SELECT statement_key, COALESCE(post_date, txn_date) AS posted, amount_cents
                    FROM transactions WHERE removed_at IS NULL
                      AND statement_key IN ({", ".join("?" for _ in keys)})""",
                keys,
            ):
                rows.setdefault(r["statement_key"], []).append((r["posted"], r["amount_cents"]))
        out: dict[str, dict[str, int]] = {}
        for s in statements:  # later period ends overwrite earlier ones
            posted = sorted(rows.get(s["statement_key"], []))
            if not _bridges(s, posted):
                continue
            after = sum(amount for _, amount in posted)  # rows posted after the day
            i = 0
            cover = out.setdefault(s["account_key"], {})
            day = max(date.fromisoformat(s["period_start"]), self.start)
            stop = min(date.fromisoformat(s["period_end"]), self.end)
            while day <= stop:
                iso = day.isoformat()
                while i < len(posted) and posted[i][0] <= iso:
                    after -= posted[i][1]
                    i += 1
                cover[iso] = s["total"] - after
                day += timedelta(days=1)
        return out

    # ---- per account ----

    def real_daily(self, key: str) -> list[LatestBalance]:
        """The account's real balance on each day of the range (no computed estimates)."""
        if key not in self._real:
            self._real[key] = self._compute(key)
        return self._real[key]

    def daily(self, key: str) -> list[LatestBalance]:
        """The account's balance on each day, with computed estimates laid over."""
        if key not in self._daily:
            real = self.real_daily(key)
            est = self._estimates.get(key)
            if est:
                stored = set(self.stored_dates(key))
                self._daily[key] = [
                    _overlay(b, e)
                    if (e := est.get(day)) is not None and (day not in stored or e[1].adds_part)
                    else b
                    for day, b in zip(self.days, real, strict=True)
                ]
            else:
                self._daily[key] = real
        return self._daily[key]

    def stored_dates(self, key: str) -> list[str]:
        """Dates of the account's stored balances (not opening points), in order."""
        return sorted({p[0] for p in self._points.get(key, []) if p[1] != OPENING})

    def stored_balance(self, key: str, day: str) -> int | None:
        """The best stored balance on exactly `day`, if any (source order wins)."""
        best = None
        for as_of, source, cents in self._points.get(key, []):
            if as_of == day and source != OPENING:
                if best is None or SOURCE_RANK[source] < best[0]:
                    best = (SOURCE_RANK[source], cents)
        return best[1] if best else None

    def at_end(self, key: str) -> LatestBalance:
        return self.daily(key)[-1]

    def _compute(self, key: str) -> list[LatestBalance]:
        acct = self.accounts[key]
        if acct["tracked_by"] == "payments":
            tracked = LatestBalance(key, None, None, "tracked")
            return [tracked] * len(self.days)
        closed_on, opened_on = acct["closed_on"], acct["opened_on"]
        points = self._points.get(key, [])
        cover = self._covered.get(key, {})
        out: list[LatestBalance] = []
        i = 0
        current: LatestBalance | None = None
        fresh_until: str | None = None
        for day in self.days:
            while i < len(points) and points[i][0] <= day:
                as_of, source, cents = points[i]
                source = "statement" if source == OPENING else source
                current = LatestBalance(key, cents, as_of, source)
                fresh_until = _stale_after(acct, as_of)
                i += 1
            if closed_on is not None and closed_on < day:
                out.append(LatestBalance(key, 0, closed_on, "closed"))
            elif day in cover:
                out.append(LatestBalance(key, cover[day], day, "statement"))
            elif current is not None:
                stale = fresh_until is not None and fresh_until < day
                out.append(
                    LatestBalance(key, current.balance_cents, current.as_of, current.source, stale)
                )
            else:
                opened = opened_on is None or opened_on <= day
                out.append(LatestBalance(key, None, None, None, opened=opened))
        return out

    def change(self, key: str) -> int | None:
        """Balance at the end minus at the start of the range, or at the account's
        first day in range that has a balance. None when the end has no balance."""
        series = self.daily(key)
        end = series[-1].value_cents
        if end is None:
            return None
        first = next(b.value_cents for b in series if b.value_cents is not None)
        return end - first

    def row_change(self, key: str) -> int | None:
        """Like `change`, but an account opened in the range counts from its first
        balance, not from the 0 before `opened_on` (the Accounts row and group)."""
        series = self.daily(key)
        end = series[-1].value_cents
        if end is None:
            return None
        first = next(
            (b.balance_cents for b in series if b.balance_cents is not None and b.opened),
            end,
        )
        return end - first

    # ---- net worth ----

    def net_worth_keys(self) -> list[str]:
        """Accounts counted in net worth (cards tracked by payments never are)."""
        return [
            k
            for k, a in self.accounts.items()
            if a["include_in_net_worth"] and a["tracked_by"] == "statements"
        ]

    def net_worth(self, index: int = -1) -> NetWorth:
        """Net worth on one day of the range (default: the end).

        Assets = sum over is_asset accounts. Liabilities = minus the sum over the
        others, so a card with a credit balance nets down. Groups are plain sums.
        """
        assets = liabilities = 0
        groups: dict[str, int] = {}
        missing, stale = [], []
        for key in sorted(self.net_worth_keys()):
            acct = self.accounts[key]
            b = self.daily(key)[index]
            cents = b.value_cents
            if cents is None:
                missing.append(key)
                continue
            if b.stale:
                stale.append(key)
            if acct["is_asset"]:
                assets += cents
            else:
                liabilities -= cents
            group = acct["nw_group"] or "other"
            groups[group] = groups.get(group, 0) + cents
        return NetWorth(
            as_of=self.days[index],
            value_cents=assets - liabilities,
            assets_cents=assets,
            liabilities_cents=liabilities,
            groups=dict(sorted(groups.items())),
            missing=tuple(missing),
            stale=tuple(stale),
        )


def _overlay(b: LatestBalance, est: tuple[int, Estimate] | None) -> LatestBalance:
    """Lay one computed estimate over a day with no stored balance of its own.

    Closed, tracked and missing days keep their real value.
    """
    if est is None or b.source in ("closed", "tracked") or b.balance_cents is None:
        return b
    # A home or vehicle estimate keeps the value current between the quarterly
    # checks, so the day is not stale. Price and loan estimates
    # still wait on a statement, so their staleness stays.
    stale = b.stale and est[1].kind not in ("home", "vehicle")
    return replace(b, balance_cents=est[0], estimate=est[1], stale=stale)


def _estimators() -> tuple:
    """Modules that compute estimates. Imported here to avoid import cycles."""
    from app.market import estimate as price_estimate
    from app.valuations import estimate as asset_estimate

    return (price_estimate, asset_estimate)


# --------------------------------------------------------------------------
# Cache. Imported data changes through imports, which always touch
# import_runs, and through the privacy rescan, which stamps its settings row
# (`importer.rescan.HASH_SETTING`), even from the CLI in another process.
# Estimate inputs (prices, overrides) have their own signature.
# --------------------------------------------------------------------------

_CACHE: OrderedDict[tuple, History] = OrderedDict()
_CACHE_SIZE = 16
_CACHE_LOCK = Lock()


def import_signature(conn: sqlite3.Connection) -> tuple:
    row = conn.execute(
        """SELECT count(*), max(started_at), max(finished_at), group_concat(status),
             (SELECT updated_at FROM settings WHERE key = 'privacy_rules') FROM import_runs"""
    ).fetchone()
    return tuple(row)


def estimate_signature(conn: sqlite3.Connection) -> tuple:
    return tuple(module.signature(conn) for module in _estimators())


def history(conn: sqlite3.Connection, start: date, end: date, *, estimates: bool = True) -> History:
    """A cached History for the range, keyed by the database file, the import
    signature and (with estimates) the estimate inputs."""
    db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
    sig = estimate_signature(conn) if estimates else None
    key = (db_file, import_signature(conn), sig, estimates, start, end)
    with _CACHE_LOCK:
        found = _CACHE.get(key)
        if found is not None:
            _CACHE.move_to_end(key)
            return found
    built = History(conn, start, end, estimates=estimates)
    with _CACHE_LOCK:
        _CACHE[key] = built
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return built


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# --------------------------------------------------------------------------
# One-day helpers (Phase 1 callers and tests)
# --------------------------------------------------------------------------


def latest_balances(conn: sqlite3.Connection, on: date | None = None) -> dict[str, LatestBalance]:
    """account_key -> balance on `on` (default: household today). See the module rules."""
    on = on or household_today()
    h = History(conn, on, on)
    return {key: h.at_end(key) for key in h.accounts}


def net_worth(conn: sqlite3.Connection, on: date | None = None) -> NetWorth:
    on = on or household_today()
    return History(conn, on, on).net_worth()


def accounts_with_balances(conn: sqlite3.Connection, on: date | None = None) -> list[dict]:
    """Every account row as a dict, plus its latest balance fields."""
    on = on or household_today()
    h = History(conn, on, on)
    out = []
    for key, acct in h.accounts.items():
        row = {k: v for k, v in acct.items() if k not in ("first_run_id", "run_id")}
        b = h.at_end(key)
        row.update(
            balance_cents=b.balance_cents,
            balance_as_of=b.as_of,
            balance_source=b.source,
            balance_stale=b.stale,
        )
        out.append(row)
    return out
