"""The eight read-only tools for Ask (PLAN 7.4).

- Inputs are Pydantic models (extra fields forbidden). Their JSON schemas are
  the tool schemas. Bad input returns a tool error with a short message.
- Every query runs on the AI connection (safe.py): parameterized SQL over the
  *_safe views only. Computed numbers come from `ai_facts_safe` (facts.py),
  rows from `transactions_safe` and `cashflow_rows_safe`.
- `limit` is clamped to 1..100. Totals come from separate COUNT and SUM
  queries, so the model never adds up rows. Row results have the shape
  {returned, total_matching, truncated, next_offset, sum_amount, rows}.
- A `category` input is one category by exact name, else one group by exact
  name, else a partial match (`_categories`). Results list the categories
  they counted (`matched_categories`).
- Every result is halved (rows first) until its JSON fits RESULT_CHARS.
- Amounts are US dollars as decimal strings ("1234.56"). Signs: on accounts
  and transactions, money out or money owed is negative; in summaries,
  spending is positive.
- Tools are sorted by name, and their definitions never change between
  requests, so they stay in the prompt cache.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app.ai.postprocess import Scrub
from app.ai.safe import AiConnection, run

RESULT_CHARS = 12_000
MAX_ROWS = 100
MAX_DOLLARS = 1_000_000_000  # search sizes; far past any household amount
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])", re.ASCII)
DAY = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
SPENDING_FLOWS = ("spending", "card_no_stmt")


class ToolError(Exception):
    """Bad input or nothing to show. The message goes back to the model."""


def usd(cents: int | None) -> str | None:
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    c = abs(int(cents))
    return f"{sign}{c // 100}.{c % 100:02d}"


def _month(v: str | None) -> str | None:
    if v is not None and not MONTH.fullmatch(v):
        raise ValueError("Use a month like 2026-08.")
    return v


def _day(v: str | None) -> str | None:
    if v is not None:
        try:
            if not DAY.fullmatch(v):
                raise ValueError
            date.fromisoformat(v)  # a real day, not 2026-02-30
        except ValueError:
            raise ValueError("Use a date like 2026-08-31.") from None
    return v


# The validators check the format. A `pattern` here would apply to the whole
# `str | None` and make an explicit null raise TypeError instead of being left out.
Month = Annotated[
    str | None, AfterValidator(_month), Field(default=None, description="A month, like 2026-08.")
]
Day = Annotated[
    str | None, AfterValidator(_day), Field(default=None, description="A date, like 2026-08-31.")
]
Limit = Annotated[int, Field(default=25, description="Rows to return, 1 to 100.")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Facts helpers
# --------------------------------------------------------------------------


def _facts(conn: AiConnection, kind: str) -> list[tuple[str, dict]]:
    rows = run(conn, "SELECT key, json FROM ai_facts_safe WHERE kind = ? ORDER BY key", [kind])
    return [(r["key"], json.loads(r["json"])) for r in rows]


def _fact(conn: AiConnection, kind: str, key: str) -> dict | None:
    rows = run(conn, "SELECT json FROM ai_facts_safe WHERE kind = ? AND key = ?", [kind, key])
    return json.loads(rows[0]["json"]) if rows else None


def _meta(conn: AiConnection) -> dict:
    return _fact(conn, "meta", "latest") or {}


def _pct(milli: int | None) -> str | None:
    """Milli-percent as a percent string: 4500 becomes "4.5"."""
    if milli is None:
        return None
    text = f"{Decimal(milli) / 1000:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _money(obj: Any) -> Any:
    """Turn every *_cents field into a dollar string field without the suffix, and
    every *_milli_pct field into a percent string (`apr_pct`)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.endswith("_cents") and (isinstance(v, int) or v is None):
                out[k[: -len("_cents")]] = usd(v)
            elif k.endswith("_milli_pct") and (isinstance(v, int) or v is None):
                out[k[: -len("_milli_pct")] + "_pct"] = _pct(v)
            else:
                out[k] = _money(v)
        return out
    if isinstance(obj, list):
        return [_money(v) for v in obj]
    return obj


def _in_range(month: str, start: str | None, end: str | None) -> bool:
    return (start is None or month >= start) and (end is None or month <= end)


# --------------------------------------------------------------------------
# The tools
# --------------------------------------------------------------------------

AccountGroup = Literal[
    "cash", "credit_cards", "investments", "retirement", "real_estate", "vehicles", "loans", "other"
]


class AccountBalancesIn(_In):
    group: AccountGroup | None = Field(
        default=None, description="Only accounts in this group. Leave out for all."
    )
    include_closed: bool = Field(default=False, description="Also list closed accounts.")


def _account(x: dict, today: str | None) -> dict:
    row = {
        "name": x["display_name"],
        "institution": x["institution"],
        "owner": x["owner_label"],
        "group": x["group_label"],
        "type": x["type"],
        "balance": usd(x["balance_cents"]),
        "as_of": x["as_of"],
        "basis": x["source"],
        "estimated": x["estimated"],
        "stale": x["stale"],
        "missing": x["missing"],
        "in_net_worth": x["include_in_net_worth"],
        "equity": usd(x["equity_cents"]),
    }
    if x["estimated"]:
        # The balance is the app's estimate for today. The real balance it starts
        # from (a statement or a value, on its own date) goes under based_on.
        row.update(
            as_of=today,
            basis=f"estimate ({x['estimate_kind']})",
            estimate_through=x["estimate_through"],
            based_on={
                "as_of": x["as_of"],
                "balance": usd(x["real_balance_cents"]),
                "basis": x["source"],
            },
        )
    return row


def account_balances(conn: AiConnection, a: AccountBalancesIn) -> dict:
    accounts = [v for _, v in _facts(conn, "account")]
    if a.group:
        accounts = [x for x in accounts if x["group"] == a.group]
    if not a.include_closed:
        accounts = [x for x in accounts if x["active"]]
    summary = _fact(conn, "networth_summary", "latest") or {}
    out = {
        "as_of": summary.get("as_of"),
        "net_worth": usd(summary.get("net_worth_cents")),
        "assets": usd(summary.get("assets_cents")),
        "liabilities": usd(summary.get("liabilities_cents")),
        "by_type": _money(summary.get("by_type", [])),
        "accounts": [_account(x, summary.get("as_of")) for x in accounts],
        "notes": {
            "partial": summary.get("partial"),
            "missing": summary.get("missing"),
            "stale": summary.get("stale"),
            "estimated": summary.get("estimated"),
        },
    }
    if a.group in (None, "credit_cards", "loans"):
        debt = _fact(conn, "debt", "all") or {}
        out["debts"] = _money(debt.get("ledger", []))
    return out


class BudgetStatusIn(_In):
    month: Month = None


BUDGET_ABOUT = (
    "A group's plan is the target in use. source edited means it was changed in the app; "
    "chat_plan is the chat's number."
)


def budget_status(conn: AiConnection, a: BudgetStatusIn) -> dict:
    meta = _meta(conn)
    month = a.month or meta.get("latest_complete_month") or meta.get("current_month")
    fact = _fact(conn, "budget", month) if month else None
    if fact is None:
        months = [k for k, _ in _facts(conn, "budget")]
        raise ToolError(f"No budget for {month}. Months with budgets: {', '.join(months)}.")
    return {"about": BUDGET_ABOUT, **_money(fact)}


class CashflowIn(_In):
    start_month: Month = None
    end_month: Month = None


PLAN_ABOUT = (
    "A month's planned spending includes saved and invested "
    "(plan.spending_incl_saved_invested), so plan left_over is income minus that. "
    "Actual spending leaves saved and invested out. Compare plan with actual "
    "through plan_vs_actual, which puts both on the same basis."
)


def _plan_month(m: dict) -> dict:
    """A month in dollars, with the plan's spending named for what it holds (PLAN 3.9)."""
    out = _money(m)
    if out.get("plan"):
        out["plan"]["spending_incl_saved_invested"] = out["plan"].pop("spending")
    return out


def cashflow(conn: AiConnection, a: CashflowIn) -> dict:
    months = [
        v for k, v in _facts(conn, "cashflow_month") if _in_range(k, a.start_month, a.end_month)
    ]
    complete = [m for m in months if m["complete"]]
    plans = [
        v for k, v in _facts(conn, "plan_vs_actual") if _in_range(k, a.start_month, a.end_month)
    ]
    meta = _meta(conn)
    return {
        "about": PLAN_ABOUT,
        "latest_complete_month": meta.get("latest_complete_month"),
        "current_month": meta.get("current_month"),
        "months": [_plan_month(m) for m in months],
        "complete_months_total": {
            "months": len(complete),
            "income": usd(sum(m["income_cents"] for m in complete)),
            "spending": usd(sum(m["spending_cents"] for m in complete)),
            "saved_invested": usd(sum(m["saved_invested_cents"] for m in complete)),
            "left_over": usd(sum(m["left_over_cents"] for m in complete)),
        },
        "plan_vs_actual": _money(plans),
    }


class HoldingsIn(_In):
    account: str | None = Field(
        default=None, max_length=60, description="Part of an account name. Leave out for all."
    )


def holdings(conn: AiConnection, a: HoldingsIn) -> dict:
    rows = [v for _, v in _facts(conn, "holding")]
    if a.account:
        needle = a.account.casefold()
        rows = [r for r in rows if needle in r["account_name"].casefold()]
    rows.sort(key=lambda r: -(r["value_cents"] or 0))
    for r in rows:
        r["quantity"] = _micro(r.pop("quantity_micro"))
        r["price"] = _micro(r.pop("price_micro"))
    return {
        "total_value": usd(sum(r["value_cents"] or 0 for r in rows)),
        "holdings": _money(rows),
    }


def _micro(v: int | None) -> str | None:
    if v is None:
        return None
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v // 1_000_000}.{v % 1_000_000:06d}".rstrip("0").rstrip(".")


Series = Literal[
    "networth",
    "cash",
    "credit_cards",
    "investments",
    "retirement",
    "real_estate",
    "vehicles",
    "loans",
]


class NetWorthHistoryIn(_In):
    series: Series = Field(default="networth", description="Net worth, or one account group.")
    start_month: Month = None
    end_month: Month = None


def net_worth_history(conn: AiConnection, a: NetWorthHistoryIn) -> dict:
    fact = _fact(conn, "networth", a.series)
    if fact is None:
        raise ToolError("No history yet.")
    points = [
        {
            "date": p["date"],
            "value": usd(p["value_cents"]),
            "partial": p["partial"],
            "estimated": p["estimated"],
        }
        for p in fact["points"]
        if _in_range(p["date"][:7], a.start_month, a.end_month)
    ]
    return {
        "series": fact["label"],
        "points": points,
        "about": "One point per month (its last day), plus the newest day. Partial "
        "means some accounts had no balance yet.",
    }


class PlansAndRecurringIn(_In):
    show: Literal["all", "plans", "recurring", "debt", "utilities", "contributions"] = Field(
        default="all",
        description="Plans (installments and promos), recurring bills, or debts; all is "
        "those three. utilities: utility bills, usage and due dates. contributions: "
        "401k, HSA and IRA money this year against the limits.",
    )
    utility: str | None = Field(
        default=None,
        max_length=60,
        description="With show utilities: part of a provider name, for that utility's "
        "last 12 bills.",
    )


UTILITY_BILLS_SHOWN = 3  # per utility when no utility is named
UTILITIES_ABOUT = (
    "Each utility holds its own bills, newest first: the newest 3, or up to the last "
    "12 when a utility is named, and its bills past due. Usage is in the line's unit. per_day is "
    "usage (or the total) over the bill's days. last_year is the bill about one year "
    "earlier, and change_pct compares with it. due_by is the due date, or the bill "
    "date plus 40 days when the bill has none. avg_bill_12m is the average bill over "
    "the 12 months to the newest bill. estimated_read marks a bill with an estimated "
    "meter reading. status: paid, amount_differs (paid another amount), past_due, "
    "not_paid, no_history (no payment history yet)."
)
CONTRIBUTIONS_ABOUT = (
    "Money put in this year against the IRS limit, per person and plan. ytd counts "
    "pay stubs through ytd_as_of, plus prior-employer and personal money. projected "
    "adds the checks left at the newest check's amount. status: under, at_limit, "
    "over, no_limit, no_data, no_schedule. A change of election starts on change_on; "
    "needed_per_check from then reaches the limit. other is IRA and 529 money this "
    "year, which has no limit in the app."
)


def _units(obj: Any) -> Any:
    """Turn every *_micro field into a decimal string field without the suffix:
    usage of 1.5 units, stored in micro-units, becomes "1.5"."""
    if isinstance(obj, dict):
        return {
            (k[: -len("_micro")] if k.endswith("_micro") else k): _micro(v)
            if k.endswith("_micro") and (isinstance(v, int) or v is None)
            else _units(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_units(v) for v in obj]
    return obj


def _utilities(conn: AiConnection, name: str | None) -> list[dict]:
    """Every utility, or the one whose provider or key holds `name`, each holding
    its own bills (newest first) and past-due bills. Nothing links a bill to its
    utility by name or key: two hidden utilities look alike, and a key never
    holds data (ask.py scrubs stored results again value by value)."""
    found = [v for _, v in _facts(conn, "utility")]
    if name is not None:
        needle = name.strip().casefold()
        found = [
            u
            for u in found
            if needle and (needle in u["provider"].casefold() or needle in u["utility_key"])
        ]
        if not found:
            raise ToolError("No utility has that name. Leave utility out to see them all.")
    else:
        for u in found:
            u["bills"] = u["bills"][:UTILITY_BILLS_SHOWN]
    return [_money(_units({**u, "past_due_count": len(u["past_due"])})) for u in found]


def _fit_bills(result: dict, scrub: Scrub | None) -> None:
    """Cut the oldest bills of the utility with the most, until the result fits.
    _shrink never looks inside a list, so it would cut whole utilities instead.
    Runs after the scrub, so it sees the text as sent, and what it adds goes
    through the same scrub. When the scrub changed the shape (a private term
    like "Bills" renames a key), it does nothing and _shrink cuts."""
    found = result.get("utilities")
    utilities = found.get("utilities") if isinstance(found, dict) else None
    if not isinstance(utilities, list) or not all(
        isinstance(u, dict) and isinstance(u.get("bills"), list) for u in utilities
    ):
        return
    totals: dict[int, int] = {}
    while len(dump(result)) > RESULT_CHARS:
        u = max(utilities, key=lambda x: (len(x["bills"]), len(dump(x["bills"]))))
        if len(u["bills"]) < 2:
            return  # _shrink takes it from here
        total = totals.setdefault(id(u), len(u["bills"]))
        keep = len(u["bills"]) // 2
        u["bills"] = u["bills"][:keep]
        added = {"bills_total": total, "bills_note": f"Showing the newest {keep} of {total} bills."}
        u.update(scrub.json(added) if scrub else added)


def plans_and_recurring(conn: AiConnection, a: PlansAndRecurringIn) -> dict:
    if a.utility is not None and a.show != "utilities":
        raise ToolError("utility works with show utilities.")
    out: dict[str, Any] = {}
    if a.show in ("all", "plans"):
        out["plans"] = _money(_fact(conn, "plans", "all") or {})
    if a.show in ("all", "recurring"):
        out["recurring"] = _money(_fact(conn, "recurring", "all") or {})
    if a.show in ("all", "debt"):
        out["debt"] = _money(_fact(conn, "debt", "all") or {})
    if a.show == "utilities":
        out["utilities"] = {"about": UTILITIES_ABOUT, "utilities": _utilities(conn, a.utility)}
    if a.show == "contributions":
        out["contributions"] = {
            "about": CONTRIBUTIONS_ABOUT,
            **_money(_fact(conn, "contributions", "all") or {}),
        }
    return out


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# Cash flow's own row for card payments with no statement (cashflow_rows_safe).
CARD_NO_STATEMENT = ("card-no-statement", "Card spending (no statement)", "", "spending")

Cat = tuple[str, str, str, str]  # key, name, group name, kind


def _categories(
    conn: AiConnection, text: str, extra: tuple[Cat, ...] = (), flow: str | None = None
) -> tuple[list[str], list[str]]:
    """(keys, names) of the categories a `category` input means, ignoring case:
    the category with that exact name, else the group with that exact name, else
    the categories whose name, then whose group's name, holds the text. So
    "Fitness" is that one category, not every category in "Services and fitness".
    With a `flow`, the categories that can count in it are tried first (only
    income categories count as income), so "Interest" in spending is card
    interest, not the income category."""
    cats: list[Cat] = [
        (r["category_key"], r["name"], r["group_name"], r["kind"])
        for r in run(
            conn,
            "SELECT category_key, name, group_name, kind FROM categories_safe "
            "ORDER BY group_name, name",
        )
    ]
    cats += extra
    needle = text.strip().casefold()
    tests: tuple[Callable[[Cat], bool], ...] = (
        lambda c: c[1].casefold() == needle,
        lambda c: c[2].casefold() == needle,
        lambda c: needle in c[1].casefold(),
        lambda c: needle in c[2].casefold(),
    )
    pools = [cats]
    if flow is not None:
        pools.insert(0, [c for c in cats if (c[3] == "income") == (flow == "income")])
    for pool in pools:
        for test in tests if needle else ():
            found = [c for c in pool if test(c)]
            if found:
                return [c[0] for c in found], [c[1] for c in found]
    raise ToolError(
        "No category or category group has that name. The household context lists them."
    )


def _in_keys(keys: list[str]) -> str:
    return f"category_key IN ({', '.join('?' for _ in keys)})"


class SearchTransactionsIn(_In):
    text: str | None = Field(
        default=None, max_length=60, description="Part of a merchant, category or account name."
    )
    start_date: Day = None
    end_date: Day = None
    account: str | None = Field(default=None, max_length=60, description="Part of an account name.")
    category: str | None = Field(
        default=None, max_length=60, description="A category or category group name."
    )
    direction: Literal["any", "out", "in"] = Field(
        default="any", description="out: money out or new charges. in: money in or credits."
    )
    # Bounded, so infinity or a huge number is bad input, not a crash.
    min_amount: float | None = Field(
        default=None,
        ge=0,
        le=MAX_DOLLARS,
        allow_inf_nan=False,
        description="Smallest size in dollars.",
    )
    max_amount: float | None = Field(
        default=None,
        ge=0,
        le=MAX_DOLLARS,
        allow_inf_nan=False,
        description="Largest size in dollars.",
    )
    include_transfers: bool = Field(
        default=True, description="Include moves between household accounts."
    )
    sort: Literal["newest", "oldest", "largest", "smallest"] = "newest"
    limit: Limit = 25
    offset: int = Field(default=0, ge=0, le=100_000)


SEARCH_ORDER = {
    "newest": "txn_date DESC, txn_key",
    "oldest": "txn_date ASC, txn_key",
    "largest": "abs(amount_cents) DESC, txn_key",
    "smallest": "abs(amount_cents) ASC, txn_key",
}


def search_transactions(conn: AiConnection, a: SearchTransactionsIn) -> dict:
    where = ["pre_history = 0"]
    params: list[Any] = []
    if a.text:
        where.append(
            "(merchant LIKE ? ESCAPE '\\' OR category_name LIKE ? ESCAPE '\\' "
            "OR counterparty_name LIKE ? ESCAPE '\\')"
        )
        params += [_like(a.text)] * 3
    if a.start_date:
        where.append("txn_date >= ?")
        params.append(a.start_date)
    if a.end_date:
        where.append("txn_date <= ?")
        params.append(a.end_date)
    if a.account:
        where.append("account_name LIKE ? ESCAPE '\\'")
        params.append(_like(a.account))
    matched = None
    if a.category:
        keys, matched = _categories(conn, a.category)
        where.append(_in_keys(keys))
        params += keys
    if a.direction == "out":
        where.append("amount_cents < 0")
    elif a.direction == "in":
        where.append("amount_cents > 0")
    if a.min_amount is not None:
        where.append("abs(amount_cents) >= ?")
        params.append(round(a.min_amount * 100))
    if a.max_amount is not None:
        where.append("abs(amount_cents) <= ?")
        params.append(round(a.max_amount * 100))
    if not a.include_transfers:
        where.append("is_transfer = 0")
    clause = " AND ".join(where)
    limit = max(1, min(MAX_ROWS, a.limit))
    totals = run(
        conn,
        f"""SELECT count(txn_key) AS n, coalesce(sum(amount_cents), 0) AS s
            FROM transactions_safe WHERE {clause}""",
        params,
    )[0]
    rows = run(
        conn,
        f"""SELECT txn_date, merchant, counterparty_name, amount_cents, category_name,
                   account_name, cardholder, txn_type, is_transfer
            FROM transactions_safe WHERE {clause}
            ORDER BY {SEARCH_ORDER[a.sort]} LIMIT ? OFFSET ?""",
        [*params, limit, a.offset],
    )
    out_rows = [
        {
            "date": r["txn_date"],
            "merchant": r["merchant"] or r["counterparty_name"],
            "amount": usd(r["amount_cents"]),
            "category": r["category_name"],
            "account": r["account_name"],
            "cardholder": r["cardholder"],
            "type": r["txn_type"],
            "transfer": bool(r["is_transfer"]),
        }
        for r in rows
    ]
    result = _page(out_rows, int(totals["n"]), a.offset, usd(int(totals["s"])))
    if matched is not None:
        result["matched_categories"] = matched
    return result


GroupBy = Literal["category", "group", "merchant", "month", "account"]
FLOW_SQL = {
    "spending": ("flow IN ('spending', 'card_no_stmt')", "out_cents"),
    "income": ("flow = 'income'", "amount_cents"),
    "saved_invested": ("flow = 'saved'", "out_cents"),
}
GROUP_SQL = {
    "category": "coalesce(category_name, 'Uncategorized')",
    "group": (
        "coalesce((SELECT g.group_name FROM categories_safe AS g "
        "WHERE g.category_key = cashflow_rows_safe.category_key), "
        "CASE WHEN group_key = 'card-no-statement' THEN 'Card spending (no statement)' "
        "ELSE 'Not spending' END)"
    ),
    "merchant": "coalesce(merchant_label, 'No merchant')",
    "month": "month",
    "account": "account_name",
}


class SpendingSummaryIn(_In):
    start_month: Month = Field(
        default=None,
        description="First month, like 2026-05. Alone, it runs through the latest complete "
        "month (or the current month, when it starts later).",
    )
    end_month: Month = Field(
        default=None, description="Last month, like 2026-08. Alone, it is the only month."
    )
    group_by: GroupBy = "category"
    flow: Literal["spending", "income", "saved_invested"] = Field(
        default="spending", description="What to add up. Refunds net against spending."
    )
    category: str | None = Field(
        default=None, max_length=60, description="Only this category or category group."
    )
    limit: Limit = 25
    offset: int = Field(default=0, ge=0, le=100_000, description="Skip this many groups.")


def spending_summary(conn: AiConnection, a: SpendingSummaryIn) -> dict:
    meta = _meta(conn)
    current = meta.get("current_month")
    latest = meta.get("latest_complete_month") or current
    if a.start_month and not a.end_month:
        # Like the Cash Flow breakdown: from that month through the latest
        # complete one. A later start runs through the current month.
        start = a.start_month
        end = latest if latest and start <= latest else max(start, current or start)
    else:
        start = a.start_month or a.end_month or latest
        end = a.end_month or start
    if start > end:
        raise ToolError("start_month is after end_month.")
    flow_where, value = FLOW_SQL[a.flow]
    where = [flow_where, "month BETWEEN ? AND ?"]
    params: list[Any] = [start, end]
    matched = None
    if a.category:
        keys, matched = _categories(conn, a.category, (CARD_NO_STATEMENT,), a.flow)
        where.append(_in_keys(keys))
        params += keys
    clause = " AND ".join(where)
    label = GROUP_SQL[a.group_by]
    limit = max(1, min(MAX_ROWS, a.limit))
    total = run(
        conn,
        f"""SELECT coalesce(sum({value}), 0) AS s, count(txn_key) AS n
            FROM cashflow_rows_safe WHERE {clause}""",
        params,
    )[0]
    # Groups ignore case, so "Cafe" and "CAFE" add up together.
    groups = run(
        conn,
        f"""SELECT count(label) AS n FROM (SELECT {label} AS label
            FROM cashflow_rows_safe WHERE {clause} GROUP BY lower(label))""",
        params,
    )[0]
    order = "label" if a.group_by == "month" else "amount DESC, label"
    rows = run(
        conn,
        f"""SELECT {label} AS label, sum({value}) AS amount, count(txn_key) AS n
            FROM cashflow_rows_safe WHERE {clause}
            GROUP BY lower(label) ORDER BY {order} LIMIT ? OFFSET ?""",
        [*params, limit, a.offset],
    )
    # A list, like every result: a key never holds data (ask.py scrubs values).
    statuses = [
        {"month": k, "status": v["status"]}
        for k, v in _facts(conn, "cashflow_month")
        if _in_range(k, start, end)
    ]
    result = _page(
        [{"label": r["label"], "amount": usd(r["amount"]), "count": r["n"]} for r in rows],
        int(groups["n"]),
        a.offset,
        usd(int(total["s"])),
    )
    result.update(
        {
            "from": start,
            "to": end,
            "flow": a.flow,
            "group_by": a.group_by,
            "rows_counted": int(total["n"]),
            "month_status": statuses,
        }
    )
    if matched is not None:
        result["matched_categories"] = matched
    return result


# --------------------------------------------------------------------------
# Result shaping
# --------------------------------------------------------------------------


def _page(rows: list[dict], total: int, offset: int, sum_amount: str | None) -> dict:
    return {
        "offset": offset,
        "returned": len(rows),
        "total_matching": total,
        "truncated": offset + len(rows) < total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        "sum_amount": sum_amount,
        "rows": rows,
    }


_TIME_KEYS = ("month", "date", "txn_date", "bill_date", "as_of", "effective_month")


def _when(item: Any) -> str | None:
    if isinstance(item, dict):
        for k in _TIME_KEYS:
            if isinstance(item.get(k), str):
                return item[k]
    return None


def _lists(node: dict) -> list[tuple[dict, str]]:
    """(parent, key) of every list with 2+ items, in dicts at any depth."""
    found = []
    for k, v in node.items():
        if isinstance(v, list) and len(v) > 1:
            found.append((node, k))
        elif isinstance(v, dict):
            found += _lists(v)
    return found


def dump(result: Any) -> str:
    """A tool result's JSON text, the one way it is written: compact, keys sorted,
    and letters outside ASCII as they are, not as \\u escapes (so a scrub of the
    text sees a name as it is written). Loading it and dumping it again gives the
    same bytes, so a stored result re-scrubbed with an unchanged scrub is sent
    unchanged (ask.py)."""
    return json.dumps(result, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _shrink(result: dict, scrub: Scrub | None = None) -> str:
    """JSON that fits RESULT_CHARS.

    A page (`rows`) keeps its first rows, and `next_offset` points just past
    them. Otherwise the longest list anywhere is halved, keeping the newest
    half of a list in date order, with a note saying what was kept. Totals
    never change. Added text and keys use the same scrub as the result,
    before checking its size."""
    totals: dict[tuple[int, str], int] = {}
    text = dump(result)
    while len(text) > RESULT_CHARS:
        rows = result.get("rows")
        if "returned" in result and isinstance(rows, list) and len(rows) > 1:
            result["rows"] = rows[: len(rows) // 2]
            result["returned"] = len(result["rows"])
            result["truncated"] = True
            result["next_offset"] = int(result.get("offset") or 0) + len(result["rows"])
        else:
            lists = _lists(result)
            if not lists:
                error = {"error": "The result is too large. Narrow the question."}
                return dump(scrub.json(error) if scrub else error)
            parent, key = max(lists, key=lambda pk: len(dump(pk[0][pk[1]])))
            items = parent[key]
            keep = len(items) // 2
            first, last = _when(items[0]), _when(items[-1])
            if first is not None and last is not None and first < last:
                parent[key], which = items[-keep:], "newest"
            else:
                parent[key], which = items[:keep], "first"
            # Keep the original count even if the scrub renames its metadata key.
            total = totals.setdefault((id(parent), key), parent.get(f"{key}_total", len(items)))
            added = {}
            for field, value in (
                (f"{key}_total", total),
                (f"{key}_note", f"Showing the {which} {keep} of {total}. Ask for less."),
            ):
                clean_field = scrub.text(field) if scrub else field
                # Two hidden field names can coincide. Keep both the count and
                # note under distinct, stable keys through cuts and follow-ups.
                if clean_field in added:
                    clean_field += "_2"
                added[clean_field] = scrub.json(value) if scrub else value
            parent.update(added)
        text = dump(result)
    return text


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    model: type[_In]
    fn: Callable[[AiConnection, Any], dict]
    status: str  # the chip shown while it runs
    fit: Callable[[dict, Scrub | None], None] | None = None  # a cut that knows the shape


TOOLS: tuple[Tool, ...] = tuple(
    sorted(
        (
            Tool(
                "account_balances",
                "Current balances of the household's accounts (statement values, plus the "
                "app's estimates from prices and values), net worth, and debts with rates.",
                AccountBalancesIn,
                account_balances,
                "Checking balances",
            ),
            Tool(
                "budget_status",
                "One month's budget: the plan's target and the actual spending for each group "
                "and category, and the chat's cycle budgets.",
                BudgetStatusIn,
                budget_status,
                "Checking the budget",
            ),
            Tool(
                "cashflow",
                "Income, spending, saved and invested, and left over by month, with the plan "
                "where one exists. Months that are not complete are marked.",
                CashflowIn,
                cashflow,
                "Reading cash flow",
            ),
            Tool(
                "holdings",
                "Investment holdings: name, symbol, value, quantity and price, by account.",
                HoldingsIn,
                holdings,
                "Reading holdings",
            ),
            Tool(
                "net_worth_history",
                "Net worth, or one account group's total, at each month end.",
                NetWorthHistoryIn,
                net_worth_history,
                "Looking at net worth over time",
            ),
            Tool(
                "plans_and_recurring",
                "Installment plans and 0% promos (monthly cost, what is left, end month), "
                "recurring bills and subscriptions (keep: the household chose to keep it; tag: "
                "streaming, hobbies or software), debts with rates and payoff order, utility "
                "bills (usage, due dates, past due, year over year, the 12-month average), and "
                "401k, HSA and IRA contributions this year against the limits.",
                PlansAndRecurringIn,
                plans_and_recurring,
                "Reading plans and bills",
                fit=_fit_bills,
            ),
            Tool(
                "search_transactions",
                "Find transactions by merchant, category, account, date or size. Returns "
                "rows plus the count and sum of every match.",
                SearchTransactionsIn,
                search_transactions,
                "Searching transactions",
            ),
            Tool(
                "spending_summary",
                "Add up spending (or income, or saved and invested) by category, group, "
                "merchant, month or account, using the same rules as the Cash Flow page. "
                "Rows with no merchant name add up under 'No merchant'.",
                SpendingSummaryIn,
                spending_summary,
                "Adding up spending",
            ),
        ),
        key=lambda t: t.name,
    )
)
BY_NAME = {t.name: t for t in TOOLS}


def _schema(model: type[_In]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def definitions() -> list[dict]:
    """The `tools` list for the API, sorted by name. The same on every request."""
    return [
        {"name": t.name, "description": t.description, "input_schema": _schema(t.model)}
        for t in TOOLS
    ]


@dataclass(frozen=True)
class ToolRun:
    content: str  # JSON text for the tool_result
    is_error: bool


def execute(
    conn: AiConnection, name: str, raw_input: object, scrub: Scrub | None = None
) -> ToolRun:
    """Run one tool call. Never raises for bad input: the model gets an error result.
    `scrub` is the last pass on the result before it goes to the model."""
    tool = BY_NAME.get(name)
    if tool is None:
        # The model sometimes changes a name's case. Accept an unambiguous match.
        matches = [t for t in TOOLS if t.name.lower() == str(name).lower()]
        if len(matches) != 1:
            return ToolRun(f"Unknown tool. The tools are: {', '.join(BY_NAME)}.", True)
        tool = matches[0]
    try:
        args = tool.model.model_validate(raw_input if isinstance(raw_input, dict) else {})
    except ValidationError as exc:
        where = ", ".join(".".join(str(p) for p in e["loc"]) or "input" for e in exc.errors())
        return ToolRun(f"Bad input for {tool.name}: check {where}.", True)
    try:
        result = tool.fn(conn, args)
        result = scrub.json(result) if scrub else result
        if tool.fit is not None:
            tool.fit(result, scrub)
        return ToolRun(_shrink(result, scrub), False)
    except ToolError as exc:
        return ToolRun(str(exc), True)
