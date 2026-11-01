"""Investments screen: holdings, allocation and value history (PLAN 6.2, 8.2).

Holdings come from each account's newest holdings snapshot (a current
statement with has_holdings) on or before today. Each holding is valued:
- api: the statement value plus its price change since the statement
  (app/market/estimate.py), with shares after splits and the newest close.
  `estimated` is true. With no close near the statement date or today it
  keeps the statement value and price (`price_missing`).
- constant: price 1.00, statement value.
- statement: statement price and value, as of the statement date.
A stored balance newer than the snapshot (a manual balance, for example)
ends the estimate, the same as on the Accounts page.

Concentration: a holding over 20% of its account, leaving out cash
(constant mode and cash classes).

Classes: an admin's pick (market/classes.py) wins over the statement's class.

Money is integer cents, quantities and prices integer micro-units.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from dataclasses import replace
from datetime import date, timedelta

from app import config
from app.market.classes import class_picks
from app.market.estimate import Holding, Snapshot, holding_change, load_book, round_cents, snapshots
from app.market.job import STALE_DAYS
from app.market.modes import CONSTANT_CLASSES
from app.market.store import last_dates
from app.services.networth import (
    DEFAULT_RANGE,
    history_for,
    humanize,
    pct,
    range_out,
    resolve_range,
    series_points,
)

CONCENTRATION = (20, 100)  # over 20% of an account
CONSTANT_PRICE_MICRO = 1_000_000
HISTORY_GROUPS = ("investments", "retirement")
NO_CLASS = "unclassified"


def today() -> date:
    """Household today. Tests replace `app.config.household_today`."""
    return config.household_today()


def _accounts(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["account_key"]: r for r in conn.execute("SELECT * FROM accounts")}


def _latest_snapshots(
    conn: sqlite3.Connection, on: str, accounts: dict[str, sqlite3.Row]
) -> dict[str, Snapshot]:
    out = {}
    for key, ss in snapshots(conn).items():
        a = accounts.get(key)
        if a is None or a["tracked_by"] != "statements":
            continue
        if a["closed_on"] is not None and a["closed_on"] < on:
            continue
        before = [s for s in ss if s.as_of <= on]
        if before:
            out[key] = before[-1]
    return out


def _newer_balance(conn: sqlite3.Connection, key: str, after: str, on: str) -> bool:
    dates = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT as_of FROM balances WHERE account_key = ? ORDER BY as_of", (key,)
        )
    ]
    i = bisect_right(dates, after)
    return i < len(dates) and dates[i] <= on


# Funds of funds already spread the money: no concentration flag (review, Oct 21).
DIVERSIFIED_CLASSES = frozenset({"target-date", "balanced", "allocation"})


def _is_cash(h: Holding) -> bool:
    return h.mode.mode == "constant" or (h.asset_class or "") in CONSTANT_CLASSES


def holding_rows(conn: sqlite3.Connection, on: date | None = None) -> list[dict]:
    """Every current holding, valued on `on` (default: household today)."""
    day = (on or today()).isoformat()
    stale_before = ((on or today()) - timedelta(days=STALE_DAYS)).isoformat()
    accounts = _accounts(conn)
    snaps = _latest_snapshots(conn, day, accounts)
    book = load_book(conn, {k: [s] for k, s in snaps.items()}, day)
    picks = class_picks(conn)
    rows = []
    for key, snap in snaps.items():
        frozen = _newer_balance(conn, key, snap.as_of, day)
        acct_rows = []
        for h in snap.holdings:
            # A class pick regroups the holding. Its mode is already set (from the
            # statement's class), so the value stays the same.
            picked = picks.get((key, h.holding_key))
            if picked is not None:
                h = replace(h, asset_class=picked)
            row = {
                "account_key": key,
                "account_name": accounts[key]["display_name"],
                "holding_key": h.holding_key,
                "name": h.name,
                "symbol": h.mode.symbol if h.mode.mode == "api" else h.symbol,
                "asset_class": h.asset_class,
                "mode": h.mode.mode,
                "mode_source": h.mode.source,
                "statement_date": snap.as_of,
                "statement_quantity_micro": h.quantity_micro,
                "statement_price_micro": h.price_micro,
                "statement_value_cents": h.value_cents,
                "quantity_micro": h.quantity_micro,
                "price_micro": h.price_micro,
                "price_date": snap.as_of,
                "value_cents": h.value_cents,
                "estimated": False,
                "price_missing": False,
                "price_stale": False,
                "cost_basis_cents": h.cost_basis_cents,
            }
            if h.mode.mode == "constant":
                row["price_micro"] = CONSTANT_PRICE_MICRO if h.quantity_micro else None
                row["price_date"] = None
            elif h.mode.mode == "api" and h.quantity_micro and not frozen:
                ch = holding_change(book, h, day)
                if ch is None:
                    row["price_missing"] = True
                    row["price_stale"] = True
                else:
                    row.update(
                        quantity_micro=ch.shares_micro,
                        price_micro=ch.close_micro,
                        price_date=ch.price_date,
                        value_cents=h.value_cents + round_cents(ch.cents),
                        estimated=True,
                        price_stale=ch.price_date < stale_before,
                    )
            acct_rows.append((h, row))
        total = sum(r["value_cents"] for _, r in acct_rows)
        for h, row in acct_rows:
            share = pct(row["value_cents"], total) if total > 0 else None
            row["share_of_account_pct"] = share
            row["concentrated"] = bool(
                total > 0
                and not _is_cash(h)
                and (h.asset_class or "") not in DIVERSIFIED_CLASSES
                and row["value_cents"] * CONCENTRATION[1] > total * CONCENTRATION[0]
            )
            # Gain on cost uses the value shown, which may be a price estimate.
            basis = h.cost_basis_cents
            gain = None if _is_cash(h) or basis is None else row["value_cents"] - basis
            row["gain_cents"] = gain
            row["gain_pct"] = pct(gain, basis) if gain is not None and basis > 0 else None
            rows.append(row)
    return rows


def _by_value(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-r["value_cents"], r["account_key"], r["holding_key"]))


def _row_is_cash(r: dict) -> bool:
    return r["mode"] == "constant" or (r["asset_class"] or "") in CONSTANT_CLASSES


def _gain_fields(g: dict) -> dict:
    """A group's gain on cost over the holdings that have one (gap audit A7)."""
    with_gain = [r for r in g["holdings"] if r["gain_cents"] is not None]
    missing = sum(1 for r in g["holdings"] if not _row_is_cash(r) and r["cost_basis_cents"] is None)
    basis = sum(r["cost_basis_cents"] for r in with_gain) if with_gain else None
    gain = sum(r["gain_cents"] for r in with_gain) if with_gain else None
    total = g["total_cents"]
    return {
        "cost_basis_cents": basis,
        "gain_cents": gain,
        "gain_pct": pct(gain, basis) if gain is not None and basis > 0 else None,
        "gain_share_pct": (
            pct(gain, total) if gain is not None and missing == 0 and total > 0 else None
        ),
        "basis_holdings": len(with_gain),
        "no_basis_holdings": missing,
    }


def holdings_page(conn: sqlite3.Connection, group_by: str = "account") -> dict:
    on = today()
    rows = holding_rows(conn, on)
    accts = _accounts(conn)
    by_account = group_by == "account"
    groups: dict[str, dict] = {}
    for r in rows:
        if group_by == "class":
            key = r["asset_class"] or NO_CLASS
            label = humanize(key)
        else:
            key, label = r["account_key"], r["account_name"]
        g = groups.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "total_cents": 0,
                "statement_date": None,
                "estimated": False,
                "holdings": [],
                "account_type": accts[key]["type"] if by_account else None,
                "notes": accts[key]["notes"] if by_account else None,
                "dormant": bool(accts[key]["dormant"]) if by_account else False,
            },
        )
        g["total_cents"] += r["value_cents"]
        g["estimated"] = g["estimated"] or r["estimated"]
        if by_account:
            g["statement_date"] = r["statement_date"]
        g["holdings"].append(r)
    out = []
    for g in groups.values():
        g["holdings"] = _by_value(g["holdings"])
        g |= _gain_fields(g)
        out.append(g)
    out.sort(key=lambda g: (-g["total_cents"], g["key"]))
    return {
        "as_of": on.isoformat(),
        "group_by": group_by,
        "total_cents": sum(r["value_cents"] for r in rows),
        "estimated": any(r["estimated"] for r in rows),
        "concentrated": sum(1 for r in rows if r["concentrated"]),
        "groups": out,
    }


def _slices(values: dict[str, int], total: int) -> list[dict]:
    out = [
        {"key": k, "label": humanize(k), "value_cents": v, "pct": pct(v, total) if total else None}
        for k, v in values.items()
    ]
    out.sort(key=lambda s: (-s["value_cents"], s["key"]))
    return out


def allocation(conn: sqlite3.Connection) -> dict:
    on = today()
    rows = holding_rows(conn, on)
    total = sum(r["value_cents"] for r in rows)
    classes: dict[str, int] = {}
    per_account: dict[str, dict] = {}
    for r in rows:
        cls = r["asset_class"] or NO_CLASS
        classes[cls] = classes.get(cls, 0) + r["value_cents"]
        a = per_account.setdefault(
            r["account_key"],
            {"account_key": r["account_key"], "display_name": r["account_name"], "classes": {}},
        )
        a["classes"][cls] = a["classes"].get(cls, 0) + r["value_cents"]
    accounts = []
    for a in per_account.values():
        value = sum(a["classes"].values())
        accounts.append(
            {
                "account_key": a["account_key"],
                "display_name": a["display_name"],
                "value_cents": value,
                "pct": pct(value, total) if total else None,
                "classes": _slices(a["classes"], value),
            }
        )
    accounts.sort(key=lambda a: (-a["value_cents"], a["account_key"]))
    return {
        "as_of": on.isoformat(),
        "total_cents": total,
        "estimated": any(r["estimated"] for r in rows),
        "classes": _slices(classes, total),
        "accounts": accounts,
    }


def investments_history(conn: sqlite3.Connection, range_key: str = DEFAULT_RANGE) -> dict:
    """Daily total of investment and retirement accounts, with statement points
    and the estimated segments per account. 1D and 7D count back from the newest
    close, so 1D is the last trading day's move (on a weekend too)."""
    latest = max((d for d, _ in last_dates(conn).values()), default=None)
    r = resolve_range(conn, range_key, anchor=date.fromisoformat(latest) if latest else None)
    h = history_for(conn, r)
    keys = [k for k in h.net_worth_keys() if h.accounts[k]["account_group"] in HISTORY_GROUPS]
    points = series_points(h, keys)
    daily = {k: h.daily(k) for k in keys}
    for i, p in enumerate(points):
        p["estimated"] = any(daily[k][i].estimate is not None for k in keys)
    segments = []
    for k in keys:
        run: list[int] = []
        for i, b in enumerate(daily[k]):
            if b.estimate is not None:
                run.append(i)
            if run and (b.estimate is None or i == len(h.days) - 1):
                last = daily[k][run[-1]].estimate
                assert last is not None
                segments.append(
                    {
                        "account_key": k,
                        "start": h.days[run[0]],
                        "end": h.days[run[-1]],
                        "based_on": last.based_on,
                        "note": last.note,
                    }
                )
                run = []
    statement_points = []
    if keys:
        marks = ", ".join("?" for _ in keys)
        best: dict[tuple[str, str], tuple[int, int, str]] = {}
        rank = {"statement": 0, "manual": 1, "valuation": 2}
        for row in conn.execute(
            f"""SELECT account_key, as_of, source, balance_cents FROM balances
                WHERE account_key IN ({marks}) AND as_of BETWEEN ? AND ?
                  AND source IN ('statement', 'manual', 'valuation')""",
            (*keys, h.days[0], h.days[-1]),
        ):
            k = (row["account_key"], row["as_of"])
            if k not in best or rank[row["source"]] < best[k][0]:
                best[k] = (rank[row["source"]], row["balance_cents"], row["source"])
        statement_points = [
            {"date": day, "account_key": acct, "value_cents": cents, "source": source}
            for (acct, day), (_, cents, source) in sorted(best.items(), key=lambda x: x[0][::-1])
        ]
    names = {k: h.accounts[k]["display_name"] for k in keys}
    return {
        "range": range_out(r),
        "accounts": [{"account_key": k, "display_name": names[k]} for k in keys],
        "points": points,
        "statement_points": statement_points,
        "estimated_segments": segments,
        "latest_price_date": latest,
    }
