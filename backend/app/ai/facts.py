"""Computed numbers for the AI, whitelisted field by field (PLAN 5.7, 7.3, 7.4).

The app's own services compute balances (with price and value estimates), net
worth, cash flow, budgets, plans, recurring items, holdings, utility bills and
contributions. Rebuilding that
logic in SQL over the *_safe views would give the AI numbers that disagree
with the screens. So this module runs the services on a normal connection,
copies only the fields named in each `pick` below into `ai_facts`, and the AI
reads them through `ai_facts_safe`.

- Every text value is scanned (`detect_sensitive` plus the private lists). A
  value that hits becomes "(hidden)". Free text the chat writes (notes, policy
  notes, estimate explanations, plan components) is left out entirely.
  Two fixed fields the chat sets on bills, keep and tag (format 1.4), go in
  as they are, except on a bill whose name is hidden or is the personal
  subscription: there both are left out.
- Facts are rebuilt when the signature changes: imports, estimate inputs
  (prices, values, mileage), edits and rules, the household's day,
  the category tables, the contribution limits and settings, the holding
  class picks, the budget target edits, and FACTS_VERSION (a change to this
  module).
- The budget fact carries the effective target (plan_cents, with the edits
  on top), the chat's number (chat_plan_cents) and where it came from
  (source). Never who made an edit.
- Building runs outside the write lock. Only the swap of the table holds it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import config
from app.ai.postprocess import hides_last4
from app.ai.settings import iso
from app.db.connect import connect, write_transaction
from app.importer.apply import IMPORTED_TABLES
from app.importer.models import is_personal_subscription
from app.importer.sanitize import PrivateRules, scan_text
from app.services import (
    budgets,
    cashflow,
    contributions,
    investments,
    networth,
    plans,
    recurring,
    utilities,
)
from app.services.balances import estimate_signature, import_signature
from app.services.coverage import HISTORY_START, add_months, current_month

log = logging.getLogger("app.ai")

FACTS_VERSION = 7
HIDDEN = "(hidden)"
_BUILD_LOCK = threading.Lock()


class FactsError(Exception):
    """Facts could not be built. Carries no values."""


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------


def data_signature(conn: sqlite3.Connection) -> tuple:
    """Row counts of the imported tables. A reapply or a purge changes them even
    when the list of runs looks the same."""
    return tuple(
        tuple(conn.execute(f"SELECT count(*), coalesce(max(rowid), 0) FROM {t}").fetchone())
        for t in IMPORTED_TABLES
    )


def edits_signature(conn: sqlite3.Connection) -> tuple:
    rows = (
        conn.execute("SELECT coalesce(max(id), 0) FROM transaction_events").fetchone(),
        conn.execute("SELECT count(*), max(updated_at) FROM transaction_overrides").fetchone(),
        conn.execute("SELECT count(*), max(updated_at) FROM merchant_rules").fetchone(),
    )
    return tuple(tuple(r) for r in rows)


def signature(conn: sqlite3.Connection, private: PrivateRules | None = None) -> str:
    categories = [
        [tuple(r) for r in conn.execute("SELECT * FROM categories ORDER BY category_key")],
        [tuple(r) for r in conn.execute("SELECT * FROM category_groups ORDER BY group_key")],
    ]
    category_hash = hashlib.sha256(json.dumps(categories).encode()).hexdigest()
    # Admins change these in Settings, outside any import.
    limits = [
        [tuple(r) for r in conn.execute("SELECT * FROM contribution_limits ORDER BY year, kind")],
        [tuple(r) for r in conn.execute("SELECT * FROM contribution_people ORDER BY person_key")],
        [
            tuple(r)
            for r in conn.execute(
                """SELECT account_key, holding_key, asset_class FROM holding_classes
                   ORDER BY account_key, holding_key"""
            )
        ],
        # Budget target edits (migration 0014). Who and when stay out: they change
        # no number.
        [
            tuple(r)
            for r in conn.execute(
                """SELECT kind, target_key, month, target_cents, base_cents
                   FROM budget_target_edits ORDER BY kind, target_key, month"""
            )
        ],
    ]
    limits_hash = hashlib.sha256(json.dumps(limits).encode()).hexdigest()
    parts = [
        FACTS_VERSION,
        category_hash,
        limits_hash,
        private.digest if private else None,
        list(import_signature(conn)),
        repr(estimate_signature(conn)),
        [list(r) for r in edits_signature(conn)],
        [list(r) for r in data_signature(conn)],
        config.household_today().isoformat(),
    ]
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------
# Whitelisting
# --------------------------------------------------------------------------


class _Cleaner:
    def __init__(self, private: PrivateRules | None, last4s: frozenset[str] = frozenset()) -> None:
        self.private = private
        self.last4s = last4s

    def text(self, value: Any) -> Any:
        """Text that fails a scan, or that holds an account's last four, is hidden whole."""
        if not isinstance(value, str):
            return value
        if scan_text(value, self.private) or hides_last4(value, self.last4s):
            return HIDDEN
        return value

    def pick(self, src: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
        """Copy only `fields` (each must exist), scanning text values."""
        try:
            return {f: self.text(src[f]) for f in fields}
        except KeyError as exc:
            raise FactsError(f"A service no longer returns field {exc.args[0]}.") from None

    def names(self, values: Iterable[Any]) -> list[Any]:
        return [self.text(v) for v in values]


ACCOUNT_FIELDS = (
    "account_key",
    "display_name",
    "institution",
    "owner_label",
    "type",
    "group",
    "is_asset",
    "include_in_net_worth",
    "tracked_by",
    "active",
    "balance_cents",
    "as_of",
    "source",
    "stale",
    "missing",
)
BUDGET_GROUP_FIELDS = (
    "key",
    "label",
    "kind",
    "plan_cents",
    "actual_cents",
    "remaining_cents",
    "status",
    "chat_plan_cents",
    "source",
)
BUDGET_CATEGORY_FIELDS = ("name", "actual_cents", "target_cents", "remaining_cents", "status")
CHAT_BUDGET_FIELDS = (
    "label",
    "period_basis",
    "cycle_note",
    "target_cents",
    "reported_actual_cents",
    "status",
)
CASHFLOW_FIELDS = (
    "month",
    "status",
    "complete",
    "income_cents",
    "spending_cents",
    "saved_invested_cents",
    "left_over_cents",
    "savings_rate",
    "payment_basis_cents",
    "card_no_statement_cents",
    "uncategorized_cents",
    "reimbursable_cents",
)
PLAN_FIELDS = (
    "name",
    "kind_label",
    "status",
    "account_name",
    "monthly_cost_cents",
    "remaining_cents",
    "payments_left",
    "end_month",
    "promo_expiry",
    "pay_before",
    "included_in_spending_plan",
)
RECURRING_FIELDS = (
    "name",
    "category_name",
    "account_name",
    "cadence",
    "status",
    "variable",
    "amount_cents",
    "monthly_equivalent_cents",
    "next_due",
    "last_paid_date",
    "last_paid_cents",
    "owner_label",
    "end_date",
    "keep",
    "tag",
)
DEBT_FIELDS = (
    "name",
    "type",
    "owed_cents",
    "apr_milli_pct",
    "rate_type",
    "required_cents",
    "paying_cents",
    "credit_limit_cents",
    "utilization_pct",
    "payoff_date",
    "autopay",
    "plans_remaining_cents",
)
UTILITY_FIELDS = (
    "utility_key",
    "provider",
    "services",
    "bill_count",
    "first_period_end",
    "last_period_end",
    "last_bill_date",
    "last_total_cents",
    "last_status",
    "next_bill_date",
    "next_bill_late",
    "bills_in_avg",
)
UTILITY_BILLS = 12  # the newest bills per utility; each one names its year-ago bill
CONTRIBUTION_FIELDS = (
    "ytd_cents",
    "ytd_as_of",
    "limit_cents",
    "limit_confirmed",
    "room_cents",
    "per_check_cents",
    "per_check_pct",
    "checks_left",
    "projected_cents",
    "projected_room_cents",
    "status",
    "change_on",
    "needed_per_check_cents",
    "needed_pct",
    "shared",
)
HOLDING_FIELDS = (
    "account_name",
    "name",
    "symbol",
    "asset_class",
    "statement_date",
    "quantity_micro",
    "price_micro",
    "price_date",
    "value_cents",
    "estimated",
    "price_stale",
    "share_of_account_pct",
)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def _month_end_points(points: list[dict]) -> list[dict]:
    """The last daily point of each month, plus the newest point."""
    out: dict[str, dict] = {}
    for p in points:
        out[p["date"][:7]] = p
    return [
        {
            "date": p["date"],
            "value_cents": p["value_cents"],
            "partial": p["partial"],
            "estimated": p["estimated"],
        }
        for p in out.values()
    ]


def _recurring_item(c: _Cleaner, x: Mapping[str, Any]) -> dict:
    """A bill whose name is hidden or is the personal subscription loses keep and tag
    too: they would hint at what it is. This covers a private bill the rescan has not
    masked yet (its name hits the private rules here)."""
    item = c.pick(x, RECURRING_FIELDS)
    if item["name"] == HIDDEN or is_personal_subscription(x["name"]):
        item["keep"] = item["tag"] = None
    return item


def _part(c: _Cleaner, x: Mapping[str, Any]) -> dict:
    """A net worth part: label, amount and whether it is owed."""
    return {
        "label": c.text(x["label"]),
        "amount_cents": x["cents"],
        "is_liability": x["is_liability"],
    }


def _utility_bill(c: _Cleaner, b: Mapping[str, Any], y: Mapping[str, Any]) -> dict:
    """One bill from GET /api/utilities/{key}, with its year over year row."""
    paid = b["payment"]
    services = []
    for s in y["services"]:
        line = next(ln for ln in b["lines"] if ln["service"] == s["service"])
        row = {
            "service": s["service"],
            "unit": s["unit"],
            "usage_micro": line["usage_micro"],
            "usage_last_year_micro": s["usage_micro"]["last_year"],
            "usage_change_pct": s["usage_micro"]["change_pct"],
            "per_day_micro": s["per_day_micro"]["value"],
            "per_day_last_year_micro": s["per_day_micro"]["last_year"],
            "per_day_change_pct": s["per_day_micro"]["change_pct"],
            "charges_cents": line["charges_cents"],
        }
        pdd = s["per_degree_day_micro"]  # heating (gas) with degree days only
        if pdd is not None and pdd["value"] is not None:
            row["per_degree_day_micro"] = pdd["value"]
            row["per_degree_day_change_pct"] = pdd["change_pct"]
        services.append(row)
    return {
        "bill_date": b["bill_date"],
        "period_end": b["period_end"],
        "days": b["days"],
        "total_cents": b["total_cents"],
        "due_date": b["due_date"],
        "due_by": b["due_by"],
        "status": b["status"],
        "paid_date": paid["txn_date"] if paid else None,
        "paid_amount_cents": paid["amount_cents"] if paid else None,
        "heating_degree_days": b["heating_degree_days"],
        "credits_cents": b["credits_cents"],
        "total_per_day_cents": y["total_per_day_cents"]["value"],
        "last_year_period_end": y["last_year_period_end"],
        "total_last_year_cents": y["total_cents"]["last_year"],
        "total_change_pct": y["total_cents"]["change_pct"],
        "total_per_day_change_pct": y["total_per_day_cents"]["change_pct"],
        "services": services,
        # Only when set, to keep a year of bills inside one tool result.
        **({"estimated_read": True} if b["estimated_read"] else {}),
    }


def build(conn: sqlite3.Connection, private: PrivateRules | None) -> list[tuple[str, str, dict]]:
    """Every fact as (kind, key, fields). Reads with the app's services."""
    last4s = frozenset(
        r[0] for r in conn.execute("SELECT last4 FROM accounts WHERE last4 IS NOT NULL") if r[0]
    )
    c = _Cleaner(private, last4s)
    facts: list[tuple[str, str, dict]] = []
    today = config.household_today()

    # Accounts and the net worth summary (the Accounts page's numbers).
    page = networth.accounts_page(conn, "1M")
    # The page's own History (cached), for the real balance under an estimate.
    h = networth.history_for(conn, networth.resolve_range(conn, "1M"))
    for g in page["groups"]:
        for a in g["accounts"]:
            row = c.pick(a, ACCOUNT_FIELDS)
            est = a["estimate"]
            row["group_label"] = g["label"]
            row["estimated"] = est is not None
            row["estimate_kind"] = est["kind"] if est else None
            # An estimate starts from the real balance on `as_of` (`source`): that
            # balance, and the newest price or index date the estimate used.
            row["estimate_through"] = est["through"] if est else None
            row["real_balance_cents"] = (
                h.real_daily(a["account_key"])[-1].balance_cents if est else None
            )
            row["equity_cents"] = a["equity"]["equity_cents"] if a["equity"] else None
            facts.append(("account", a["account_key"], row))
    summary = page["summary"]
    notes = page["notes"]
    facts.append(
        (
            "networth_summary",
            "latest",
            {
                "as_of": page["as_of"],
                "net_worth_cents": summary["net_worth_cents"],
                "assets_cents": summary["assets_cents"],
                "liabilities_cents": summary["liabilities_cents"],
                "by_type": [_part(c, x) for x in summary["by_type"]],
                "by_owner": [_part(c, x) for x in summary["by_owner"]],
                "partial": notes["partial"],
                "missing": c.names(notes["missing"]),
                "stale": c.names(notes["stale"]),
                "estimated": c.names(e["display_name"] for e in notes["estimated"]),
            },
        )
    )

    # Net worth over time: month ends and the newest day, per series.
    for series in networth.SERIES_KEYS:
        s = networth.networth_series(conn, "ALL", series)
        facts.append(
            (
                "networth",
                series,
                {
                    "series": series,
                    "label": "Net worth" if series == "networth" else networth.GROUP_LABELS[series],
                    "points": _month_end_points(s["points"]),
                },
            )
        )

    # Cash flow by month, and plan vs actual where a plan exists: the whole
    # history (up to the service's limit), the same months the tools can sum.
    first = max(HISTORY_START, add_months(current_month(), -(cashflow.MAX_MONTHS - 1)))
    cf = cashflow.cashflow(conn, first, None)
    for m in cf["months"]:
        row = c.pick(m, CASHFLOW_FIELDS)
        plan = m["plan"]
        row["plan"] = (
            c.pick(
                plan,
                (
                    "income_cents",
                    "spending_cents",
                    "saved_invested_cents",
                    "left_over_cents",
                    "saved_pct",
                ),
            )
            if plan
            else None
        )
        facts.append(("cashflow_month", m["month"], row))
    for pva in cf["plan_vs_actual"]:
        facts.append(
            (
                "plan_vs_actual",
                pva["month"],
                {
                    "month": pva["month"],
                    "complete": pva["complete"],
                    "planned_income_cents": pva["planned_income_cents"],
                    "actual_income_cents": pva["actual_income_cents"],
                    "planned_spending_cents": pva["planned_spending_cents"],
                    "actual_spending_cents": pva["actual_spending_cents"],
                    "lines": [
                        c.pick(ln, ("label", "measure", "plan_cents", "actual_cents", "status"))
                        for ln in pva["lines"]
                    ],
                },
            )
        )
    months = [m["month"] for m in cf["months"]]
    facts.append(
        (
            "meta",
            "latest",
            {
                "today": today.isoformat(),
                "current_month": current_month(),
                "latest_complete_month": cf["latest_complete_month"],
                "first_month": months[0] if months else None,
                "last_month": months[-1] if months else None,
            },
        )
    )

    # Budgets for each month the app has.
    for month in months:
        b = budgets.budgets_page(conn, month)
        facts.append(
            (
                "budget",
                month,
                {
                    "month": b["month"],
                    "complete": b["complete"],
                    "note": c.text(b["note"]),
                    "planned_income_cents": b["planned_income_cents"],
                    "actual_income_cents": b["actual_income_cents"],
                    "totals": c.pick(
                        b["totals"], ("plan_cents", "actual_cents", "remaining_cents")
                    ),
                    "groups": [
                        {
                            **c.pick(g, BUDGET_GROUP_FIELDS),
                            "categories": [
                                c.pick(x, BUDGET_CATEGORY_FIELDS) for x in g["categories"]
                            ],
                        }
                        for g in b["groups"]
                    ],
                    "chat_budgets": [c.pick(x, CHAT_BUDGET_FIELDS) for x in b["chat_budgets"]],
                    "statements_needed": c.names(b["statements_needed"]),
                },
            )
        )

    # Plans (Plan It, Pay Over Time, promos) and debts.
    p = plans.plans_page(conn)
    facts.append(
        (
            "plans",
            "all",
            {
                "active_count": p["active_count"],
                "monthly_total_cents": p["monthly_total_cents"],
                "total_deferred_cents": p["total_deferred_cents"],
                "plans": [c.pick(x, PLAN_FIELDS) for card in p["cards"] for x in card["plans"]],
                "step_downs": [
                    {
                        **c.pick(s, ("month", "monthly_cents", "drop_cents")),
                        "ended": c.names(s["ended"]),
                    }
                    for s in p["step_downs"]
                ],
            },
        )
    )
    d = plans.debt_page(conn)
    facts.append(
        (
            "debt",
            "all",
            {
                "total_debt_cents": d["total_debt_cents"],
                "ledger": [c.pick(x, DEBT_FIELDS) for x in d["ledger"]],
                "payoff_sequence": [
                    c.pick(x, ("month", "when_label", "action")) for x in d["payoff_sequence"]
                ],
            },
        )
    )

    # Recurring bills and subscriptions.
    r = recurring.all_view(conn, current_month())
    facts.append(
        (
            "recurring",
            "all",
            {
                "items": [_recurring_item(c, x) for x in r["items"]],
                "monthly_equivalent_cents": r["totals"]["monthly_equivalent_cents"],
                "count": r["totals"]["count"],
            },
        )
    )

    # Utility bills (the Utilities tab's numbers): the summary with the 12-month
    # average, every bill past due, and the newest bills, newest first. The
    # bill's note is free text from the chat, so it is left out.
    for key in [x["utility_key"] for x in utilities.summaries(conn)]:
        d = utilities.detail(conn, key)
        if d is None:
            continue
        s = d["summary"]
        yoy = {y["bill_key"]: y for y in d["yoy"]}
        newest = list(reversed(d["bills"]))[:UTILITY_BILLS]
        paid = next((b["payment"] for b in newest if b["payment"]), None)
        facts.append(
            (
                "utility",
                key,
                {
                    **c.pick(s, UTILITY_FIELDS),
                    "avg_bill_12m_cents": s["avg_bill_cents"],
                    "total_12m_cents": s["total_12m_cents"],
                    # The account that paid the newest paid bill.
                    "paid_from": c.text(paid["account_name"]) if paid else None,
                    "past_due": [
                        {
                            "bill_date": b["bill_date"],
                            "due_by": b["due_by"],
                            "total_cents": b["total_cents"],
                        }
                        for b in d["bills"]
                        if b["status"] == "past_due"
                    ],
                    "bills": [_utility_bill(c, b, yoy[b["bill_key"]]) for b in newest],
                },
            )
        )

    # 401k and HSA money this year against the limits (the Contributions
    # page), and IRA and 529 money this year, which has no limit in the app.
    cp = contributions.contributions_page(conn, today.year, today)
    facts.append(
        (
            "contributions",
            "all",
            {
                "year": cp["year"],
                "as_of": cp["as_of"],
                "limits_found": cp["limits_found"],
                "people": [
                    {
                        "person": c.text(p["display_name"]),
                        "plans": [
                            {
                                "plan": x["label"],
                                **c.pick(x, CONTRIBUTION_FIELDS),
                                "ytd_parts": dict(x["ytd_parts"]),
                                "k415c": x["k415c"],
                                "notes": c.names(x["notes"]),
                            }
                            for x in p["plans"]
                        ],
                    }
                    for p in cp["people"]
                ],
                "hsa_family": cp["hsa_family"],
                "other": [
                    {
                        "person": c.text(o["person"]),
                        "account": c.text(o["account_name"]),
                        "kind": o["label"],
                        "ytd_cents": o["ytd_cents"],
                        "count": o["count"],
                        "last_date": o["last_date"],
                    }
                    for o in contributions.other_contributions(conn, today.year)
                ],
            },
        )
    )

    # Holdings (the Investments page's values, estimates included).
    for h in investments.holding_rows(conn):
        facts.append(
            ("holding", f"{h['account_key']}:{h['holding_key']}", c.pick(h, HOLDING_FIELDS))
        )
    return facts


def _write(conn: sqlite3.Connection, facts: list[tuple[str, str, dict]], sig: str) -> None:
    with write_transaction(conn):
        conn.execute("DELETE FROM ai_facts")
        conn.executemany(
            "INSERT INTO ai_facts (kind, key, json) VALUES (?, ?, ?)",
            [(k, key, json.dumps(v, sort_keys=True)) for k, key, v in facts],
        )
        conn.execute(
            """INSERT INTO ai_facts_state (id, signature, built_at) VALUES (1, ?, ?)
               ON CONFLICT (id) DO UPDATE SET signature = excluded.signature,
                 built_at = excluded.built_at""",
            (sig, iso(datetime.now(UTC))),
        )


def ensure_facts(db_path: str | Path, private: PrivateRules | None) -> str:
    """Rebuild ai_facts when the data changed. Returns the signature in force."""
    with _BUILD_LOCK:
        conn = connect(db_path)
        try:
            sig = signature(conn, private)
            row = conn.execute("SELECT signature FROM ai_facts_state WHERE id = 1").fetchone()
            if row is not None and row["signature"] == sig:
                return sig
            facts = build(conn, private)
            _write(conn, facts, sig)
            log.info("ai facts built", extra={"facts": len(facts)})
            return sig
        finally:
            conn.close()
