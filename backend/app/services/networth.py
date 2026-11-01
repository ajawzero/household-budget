"""Accounts page, net worth series and account detail (PLAN 3.9, 8.2).

Money is integer cents, signed from the net worth view (assets positive, owed
negative) unless a name says otherwise. All balance math is in balances.py;
this module groups, sums and shapes it.

Change rule: an account's change is its balance at the end of the range minus
its balance on the first day in range that has one. An account missing at the
start therefore counts from its first point. Before `opened_on` an account
counts as 0 in net worth, so the net worth series change includes a new
account's whole balance. The account row and its group measure from the
account's first balance instead (`History.row_change`), so a van bought in
August shows how its value moved, not +$53,900. Series changes are sums of
account changes, and `start_cents = end_cents - change_cents`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app import config
from app.importer.models import CASH_LIKE_TYPES
from app.market.classes import class_picks
from app.services import flows
from app.services.balances import SOURCE_RANK, History, LatestBalance, history
from app.services.transactions import query_rows, row_out

RANGE_KEYS = ("1M", "3M", "6M", "YTD", "1Y", "3Y", "ALL")
# The Investments value chart also takes 1D and 7D: priced holdings move every weekday.
INVESTMENT_RANGE_KEYS = ("1D", "7D", *RANGE_KEYS)
RANGE_DAYS = {"1D": 1, "7D": 7}
RANGE_MONTHS = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36}
DEFAULT_RANGE = "YTD"

GROUPS = (
    ("cash", "Cash"),
    ("credit_cards", "Credit cards"),
    ("investments", "Investments"),
    ("retirement", "Retirement"),
    ("real_estate", "Real estate"),
    ("vehicles", "Vehicles"),
    ("loans", "Loans"),
    ("other", "Other"),
)
GROUP_LABELS = dict(GROUPS)
LIABILITY_GROUPS = frozenset({"credit_cards", "loans"})
SERIES_KEYS = ("networth", *(g for g, _ in GROUPS if g != "other"))
EQUITY_TYPES = frozenset({"property", "vehicle"})
SECURED_LOAN_TYPES = frozenset({"loan", "mortgage", "heloc"})
SPARKLINE_MAX = 24


def today() -> date:
    """Household today. Tests replace `app.config.household_today`."""
    return config.household_today()


def humanize(key: str) -> str:
    return key.replace("-", " ").replace("_", " ").capitalize()


def pct(part: int, whole: int) -> str | None:
    """part / |whole| as a percent string with one decimal ("4.2"), None when whole is 0."""
    if whole == 0:
        return None
    value = Decimal(part) * 100 / abs(Decimal(whole))
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# Ranges
# --------------------------------------------------------------------------


def _months_back(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 - months
    year, month = divmod(total, 12)
    month += 1
    for d in (day.day, 30, 29, 28):  # clamp to the month's last day
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def earliest_point(conn: sqlite3.Connection) -> date | None:
    """The first day any statement-tracked account has a balance.

    That is a stored balance, the start of a cash-like statement period, or
    the start of any statement period with an opening (see balances.py).
    """
    types = sorted(CASH_LIKE_TYPES)
    row = conn.execute(
        f"""SELECT min(d) AS first FROM (
             SELECT min(b.as_of) AS d FROM balances b
               JOIN accounts a ON a.account_key = b.account_key
              WHERE a.tracked_by = 'statements'
             UNION ALL
             SELECT min(s.period_start) FROM statements s
               JOIN accounts a ON a.account_key = s.account_key
              WHERE s.is_current = 1 AND a.tracked_by = 'statements'
                AND (a.type IN ({", ".join("?" for _ in types)})
                     OR s.opening_cents IS NOT NULL))""",
        types,
    ).fetchone()
    return date.fromisoformat(row["first"]) if row["first"] else None


def resolve_range(
    conn: sqlite3.Connection, key: str, end: date | None = None, anchor: date | None = None
) -> dict:
    """{"key", "start", "end"} as dates. Ranges end on household today.

    No range starts before the earliest balance point, so a chart never opens
    with days that have no data at all.

    1D and 7D count back from `anchor` when it is given and not after the end: the
    newest market close (Investments). Closes are stored the morning after, so 1D
    counted from today would compare one close with itself.
    """
    end = end or today()
    earliest = earliest_point(conn)
    if key == "ALL":
        return {"key": key, "start": min(earliest or end, end), "end": end}
    if key == "YTD":
        start = date(end.year, 1, 1)
    elif key in RANGE_DAYS:
        base = min(anchor, end) if anchor is not None else end
        start = base - timedelta(days=RANGE_DAYS[key])
    else:
        start = _months_back(end, RANGE_MONTHS[key])
    if earliest is not None:
        start = max(start, min(earliest, end))
    return {"key": key, "start": start, "end": end}


def range_out(r: dict) -> dict:
    return {"key": r["key"], "start": r["start"].isoformat(), "end": r["end"].isoformat()}


def history_for(conn: sqlite3.Connection, r: dict) -> History:
    return history(conn, r["start"], r["end"])


# --------------------------------------------------------------------------
# Account rows
# --------------------------------------------------------------------------


def _people(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["person_key"]: r["display_name"] for r in conn.execute("SELECT * FROM people")}


def sparklines(conn: sqlite3.Connection, start: str, end: str) -> dict[str, list[dict]]:
    """account -> statement and valuation points in range, one per date, the newest 24."""
    best: dict[tuple[str, str], tuple[int, int]] = {}
    for r in conn.execute(
        """SELECT account_key, as_of, source, balance_cents FROM balances
           WHERE as_of BETWEEN ? AND ? AND source IN ('statement', 'valuation')""",
        (start, end),
    ):
        k = (r["account_key"], r["as_of"])
        rank = SOURCE_RANK[r["source"]]
        if k not in best or rank < best[k][0]:
            best[k] = (rank, r["balance_cents"])
    out: dict[str, list[dict]] = {}
    for (acct, day), (_, cents) in sorted(best.items()):
        out.setdefault(acct, []).append({"date": day, "balance_cents": cents})
    return {k: v[-SPARKLINE_MAX:] for k, v in out.items()}


def _equity(h: History, key: str, value: int | None) -> dict | None:
    """Value minus the loans secured by this home or vehicle. None without such loans."""
    if h.accounts[key]["type"] not in EQUITY_TYPES or value is None:
        return None
    # Only loans counted in net worth, so the row agrees with the By owner part.
    counted = set(h.net_worth_keys())
    loans = [
        k
        for k, a in h.accounts.items()
        if a["secured_by_account_key"] == key and a["type"] in SECURED_LOAN_TYPES and k in counted
    ]
    owed = [h.at_end(k).value_cents for k in loans]
    owed = [c for c in owed if c is not None]
    if not owed:
        return None
    loan = sum(owed)  # signed: owed is negative
    return {
        "value_cents": value,
        "loan_cents": loan,
        "equity_cents": value + loan,
        "underwater": value + loan < 0,
    }


def estimate_out(b: LatestBalance) -> dict | None:
    if b.estimate is None:
        return None
    out = asdict(b.estimate)
    out.pop("adds_part")
    return out


def estimated_accounts(h: History, keys: list[str]) -> list[dict]:
    """Accounts whose end balance is an estimate, for the est. badge tooltips."""
    out = []
    for key in keys:
        e = h.at_end(key).estimate
        if e is not None:
            out.append(
                {
                    "account_key": key,
                    "display_name": h.accounts[key]["display_name"],
                    "kind": e.kind,
                    "based_on": e.based_on,
                    "note": e.note,
                }
            )
    return out


def _source(b: LatestBalance) -> str | None:
    return None if b.source == "closed" else b.source


def account_row(
    h: History, key: str, people: dict[str, str], sparks: dict[str, list[dict]]
) -> dict:
    a = h.accounts[key]
    b = h.at_end(key)
    tracked = a["tracked_by"] == "payments"
    return {
        "account_key": key,
        "display_name": a["display_name"],
        "institution": a["institution"],
        "owner": a["owner_person_key"],
        "owner_label": people.get(a["owner_person_key"], humanize(a["owner_person_key"])),
        "type": a["type"],
        "subtype": a["subtype"],
        "group": a["account_group"],
        "nw_group": a["nw_group"],
        "is_asset": bool(a["is_asset"]),
        "include_in_net_worth": bool(a["include_in_net_worth"]),
        "tracked_by": a["tracked_by"],
        "active": bool(a["active"]),
        "dormant": bool(a["dormant"]),
        "balance_cents": b.balance_cents,
        "as_of": b.as_of,
        "source": _source(b),
        "stale": b.stale,
        "missing": b.missing,
        "change_cents": None if tracked else h.row_change(key),
        "sparkline": [] if tracked else sparks.get(key, []),
        "equity": _equity(h, key, b.balance_cents),
        "estimate": estimate_out(b),
    }


# --------------------------------------------------------------------------
# GET /api/accounts
# --------------------------------------------------------------------------


def nw_labels(h: History) -> dict[str, str]:
    labels: dict[str, str] = {}
    for a in h.accounts.values():
        if a["nw_group"] and a["nw_group_label"] and a["nw_group"] not in labels:
            labels[a["nw_group"]] = a["nw_group_label"]
    return labels


def _sorted_parts(parts: list[dict]) -> list[dict]:
    """Assets largest first, then liabilities largest owed first."""
    assets = sorted((p for p in parts if not p["is_liability"]), key=lambda p: -p["cents"])
    owed = sorted((p for p in parts if p["is_liability"]), key=lambda p: p["cents"])
    return assets + owed


def owner_group(h: History, key: str) -> str:
    """The nw_group an account counts in for By owner and the dashboard parts.

    A loan, mortgage or HELOC whose `secured_by_account_key` points to an
    asset in net worth counts in that asset's group, so the part is the net
    equity (value minus loan), the same equity the Accounts row shows. The
    group's label does not matter. Accounts page groups stay by type.
    """
    a = h.accounts[key]
    secured = h.accounts.get(a["secured_by_account_key"] or "")
    if (
        a["type"] in SECURED_LOAN_TYPES
        and secured is not None
        and secured["is_asset"]
        and secured["include_in_net_worth"]
    ):
        return secured["nw_group"] or "other"
    return a["nw_group"] or "other"


def by_owner(h: History) -> list[dict]:
    """Net worth by nw_group at the end of the range (secured loans net into their asset)."""
    labels = nw_labels(h)
    cents: dict[str, int] = {}
    assets: dict[str, bool] = {}
    for key in h.net_worth_keys():
        value = h.at_end(key).value_cents
        if value is None:
            continue
        g = owner_group(h, key)
        cents[g] = cents.get(g, 0) + value
        assets[g] = assets.get(g, False) or bool(h.accounts[key]["is_asset"])
    parts = [
        {"key": g, "label": labels.get(g, humanize(g)), "cents": c, "is_liability": not assets[g]}
        for g, c in sorted(cents.items())
    ]
    return _sorted_parts(parts)


def accounts_page(conn: sqlite3.Connection, range_key: str = DEFAULT_RANGE) -> dict:
    r = resolve_range(conn, range_key)
    h = history_for(conn, r)
    people = _people(conn)
    sparks = sparklines(conn, h.days[0], h.days[-1])
    included = set(h.net_worth_keys())
    groups = []
    for gkey, label in GROUPS:
        keys = [k for k, a in h.accounts.items() if a["account_group"] == gkey]
        if not keys:
            continue
        rows = [account_row(h, k, people, sparks) for k in keys]
        counted = [row for row in rows if row["account_key"] in included]
        total = sum(h.at_end(row["account_key"]).value_cents or 0 for row in counted)
        change = sum(row["change_cents"] or 0 for row in counted)
        groups.append(
            {
                "group": gkey,
                "label": label,
                "is_liability": gkey in LIABILITY_GROUPS,
                "total_cents": total,
                "change_cents": change,
                # Start = total minus change: each account from its first balance in range.
                "change_pct": pct(change, total - change),
                "estimated": any(row["estimate"] is not None for row in counted),
                "accounts": rows,
            }
        )
    nw = h.net_worth()
    by_type = _sorted_parts(
        [
            {
                "key": g["group"],
                "label": g["label"],
                "cents": g["total_cents"],
                "is_liability": g["is_liability"],
            }
            for g in groups
            if any(row["account_key"] in included for row in g["accounts"])
        ]
    )
    tracked = [k for k, a in h.accounts.items() if a["tracked_by"] == "payments"]
    excluded = [
        k
        for k, a in h.accounts.items()
        if a["tracked_by"] == "statements" and not a["include_in_net_worth"]
    ]
    return {
        "as_of": r["end"].isoformat(),
        "range": range_out(r),
        "groups": groups,
        "summary": {
            "assets_cents": nw.assets_cents,
            "liabilities_cents": nw.liabilities_cents,
            "net_worth_cents": nw.value_cents,
            "by_type": by_type,
            "by_owner": by_owner(h),
        },
        "notes": {
            "partial": nw.partial,
            "missing": list(nw.missing),
            "stale": list(nw.stale),
            "tracked": sorted(tracked),
            "excluded": sorted(excluded),
            "estimated": estimated_accounts(h, sorted(included)),
        },
    }


# --------------------------------------------------------------------------
# GET /api/networth/series
# --------------------------------------------------------------------------


def series_keys(h: History, series: str) -> list[str]:
    keys = h.net_worth_keys()
    if series == "networth":
        return keys
    return [k for k in keys if h.accounts[k]["account_group"] == series]


def series_points(h: History, keys: list[str]) -> list[dict]:
    daily = [h.daily(k) for k in keys]
    points = []
    for i, day in enumerate(h.days):
        total = 0
        partial = estimated = False
        for d in daily:
            b = d[i]
            cents = b.value_cents
            if cents is None:
                partial = True
                continue
            total += cents
            partial = partial or b.stale
            estimated = estimated or b.estimate is not None
        points.append(
            {"date": day, "value_cents": total, "partial": partial, "estimated": estimated}
        )
    return points


def change_of(h: History, keys: list[str]) -> int:
    return sum(c for c in (h.change(k) for k in keys) if c is not None)


def networth_series(
    conn: sqlite3.Connection, range_key: str = DEFAULT_RANGE, series: str = "networth"
) -> dict:
    r = resolve_range(conn, range_key)
    h = history_for(conn, r)
    keys = series_keys(h, series)
    points = series_points(h, keys)
    end = points[-1]["value_cents"]
    change = change_of(h, keys)
    start = end - change
    return {
        "series": series,
        "range": range_out(r),
        "points": points,
        "start_cents": start,
        "end_cents": end,
        "change_cents": change,
        "change_pct": pct(change, start),
        "estimated_accounts": estimated_accounts(h, keys),
    }


# --------------------------------------------------------------------------
# GET /api/accounts/{account_key}
# --------------------------------------------------------------------------


def micro_str(value: int | None) -> str | None:
    """Micro-units as a plain decimal string: 1500000 -> "1.5"."""
    if value is None:
        return None
    return format(Decimal(value).scaleb(-6).normalize(), "f")


def account_detail(conn: sqlite3.Connection, key: str, range_key: str = DEFAULT_RANGE) -> dict:
    """The account's row, daily balances, statements, holdings and terms. KeyError if unknown."""
    r = resolve_range(conn, range_key)
    h = history_for(conn, r)
    if key not in h.accounts:
        raise KeyError(key)
    a = h.accounts[key]
    sparks = sparklines(conn, h.days[0], h.days[-1])
    stored = set(h.stored_dates(key))
    statements = [
        {
            "period_start": s["period_start"],
            "period_end": s["period_end"],
            "opening_cents": s["opening_cents"],
            "closing_cents": s["closing_cents"],
            "balance_cents": s["balance_cents"],
            "net_flows_cents": s["net_flows_cents"],
            "market_change_cents": s["market_change_cents"],
            "txn_count": s["txn_count"],
            "reconciled": bool(s["reconciled"]),
        }
        for s in conn.execute(
            """SELECT * FROM statements WHERE account_key = ? AND is_current = 1
               ORDER BY period_end DESC""",
            (key,),
        )
    ]
    picks = class_picks(conn)
    holdings = [
        {
            "key": x["holding_key"],
            "name": x["holding_name"],
            "symbol": x["symbol"],
            "quantity": micro_str(x["quantity_micro"]),
            "price": micro_str(x["price_micro"]),
            "value_cents": x["value_cents"],
            "asset_class": picks.get((key, x["holding_key"]), x["asset_class"]),
        }
        for x in conn.execute(
            """SELECT h.* FROM holdings h
               JOIN statements s ON s.statement_key = h.statement_key AND s.is_current = 1
               WHERE h.account_key = ? AND h.as_of = (
                   SELECT max(period_end) FROM statements
                   WHERE account_key = ? AND is_current = 1 AND has_holdings = 1)
               ORDER BY h.value_cents DESC, h.holding_key""",
            (key, key),
        )
    ]
    return {
        "account": account_row(h, key, _people(conn), sparks),
        "points": [
            {
                "date": day,
                "balance_cents": b.balance_cents,
                "estimated": b.estimate is not None,
                "real": day in stored,
            }
            for day, b in zip(h.days, h.daily(key), strict=True)
            if b.balance_cents is not None
        ],
        "statements": statements,
        "investment": flows.investment_summary(conn, key, today()),
        "recent_transactions": [
            row_out(t) for t in query_rows(conn, ["t.hidden = 0", "t.account_key = ?"], [key], 20)
        ],
        "holdings": holdings,
        "terms": {
            "apr_milli_pct": a["apr_milli_pct"],
            "rate_type": a["rate_type"],
            "payoff_date": a["payoff_date"],
            "priority": a["priority"],
            "policy_note": a["policy_note"],
            "credit_limit_cents": a["credit_limit_cents"],
            "original_cents": a["original_cents"],
            "min_payment_cents": a["min_payment_cents"],
            "autopay": a["autopay"],
        },
        "about": {
            "custodian": a["custodian"],
            "advisor": a["advisor"],
            "servicer": a["servicer"],
            "notes": a["notes"],
            "opened_on": a["opened_on"],
            "closed_on": a["closed_on"],
        },
    }
