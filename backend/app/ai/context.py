"""The household context for Ask (PLAN 3.6, 7.3).

A short, deterministic summary the model gets in the system prompt, under a
cache breakpoint: people (first names), accounts by group, net worth, the
latest complete month's cash flow, the current month's plan, plans and
recurring totals, utility bills, contributions this year, and the category list.

- Read only through the *_safe views on the AI connection.
- Deterministic: fixed order, sorted keys, whole dollars. No user names, ids or
  "today", so the cached prefix stays the same until the data changes.
- Held in memory, keyed by the database file and the facts signature (it
  changes with each import, price or value update, edit, rule change and day).
  The file is in the key because the demo database can have the real one's
  signature (docs/plans/demo-mode.md). An Ask thread keeps the
  context it started with for its whole life (ask.py), so its follow-ups are
  sent under the same system prompt.
- Ask sends it through the last scrub (PLAN 5.7), like everything else.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.ai.safe import AiConnection, run

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], str] = {}


def dollars(cents: int | None) -> str:
    if cents is None:
        return "none"
    sign = "-" if cents < 0 else ""
    whole = (abs(int(cents)) + 50) // 100
    return f"{sign}${whole:,}"


def _fact(conn: AiConnection, kind: str, key: str) -> dict | None:
    rows = run(conn, "SELECT json FROM ai_facts_safe WHERE kind = ? AND key = ?", [kind, key])
    return json.loads(rows[0]["json"]) if rows else None


def _facts(conn: AiConnection, kind: str) -> list[dict]:
    rows = run(conn, "SELECT json FROM ai_facts_safe WHERE kind = ? ORDER BY key", [kind])
    return [json.loads(r["json"]) for r in rows]


def build(conn: AiConnection) -> str:
    lines = [
        "Household context. It comes from the app's data and is a summary only: "
        "use the tools for exact and current numbers.",
        "",
    ]
    people = run(
        conn,
        """SELECT display_name, role FROM people_safe WHERE role <> 'joint'
           ORDER BY role, display_name""",
    )
    if people:
        lines.append(
            "People: " + ", ".join(f"{p['display_name']} ({p['role']})" for p in people) + "."
        )

    meta = _fact(conn, "meta", "latest") or {}
    if meta.get("first_month"):
        lines.append(
            f"Months with data: {meta['first_month']} to {meta['last_month']}. Latest complete "
            f"month: {meta.get('latest_complete_month') or 'none yet'}."
        )

    summary = _fact(conn, "networth_summary", "latest")
    if summary:
        lines.append(
            f"Net worth: {dollars(summary['net_worth_cents'])} (assets "
            f"{dollars(summary['assets_cents'])}, owed {dollars(summary['liabilities_cents'])})."
        )

    accounts = [a for a in _facts(conn, "account") if a["active"]]
    if accounts:
        lines += ["", "Accounts by group (balance; owed is negative):"]
        groups: dict[str, list[dict]] = {}
        for a in accounts:
            groups.setdefault(a["group_label"], []).append(a)
        for label in sorted(groups):
            parts = []
            for a in sorted(groups[label], key=lambda x: x["display_name"]):
                bal = (
                    "tracked by payments"
                    if a["source"] == "tracked"
                    else dollars(a["balance_cents"])
                )
                tag = " (estimate)" if a["estimated"] else ""
                parts.append(f"{a['display_name']} ({a['owner_label']}) {bal}{tag}")
            lines.append(f"- {label}: " + "; ".join(parts) + ".")

    month = meta.get("latest_complete_month")
    cf = _fact(conn, "cashflow_month", month) if month else None
    if cf:
        lines += [
            "",
            f"Cash flow {month}: income {dollars(cf['income_cents'])}, spending "
            f"{dollars(cf['spending_cents'])}, saved and invested "
            f"{dollars(cf['saved_invested_cents'])}, left over {dollars(cf['left_over_cents'])}.",
        ]
    current = meta.get("current_month")
    cur = _fact(conn, "cashflow_month", current) if current else None
    if cur and cur.get("plan"):
        p = cur["plan"]
        # Planned spending includes saved and invested (PLAN 3.9). Actual spending does not.
        lines.append(
            f"Plan for {current}: income {dollars(p['income_cents'])}, spending "
            f"{dollars(p['spending_cents'])} including "
            f"{dollars(p['saved_invested_cents'])} saved and invested."
        )

    plans = _fact(conn, "plans", "all")
    if plans:
        lines.append(
            f"Installment plans and promos: {plans['active_count']} active, "
            f"{dollars(plans['monthly_total_cents'])} a month."
        )
    rec = _fact(conn, "recurring", "all")
    if rec:
        lines.append(
            f"Recurring bills and subscriptions: {rec['count']} items, about "
            f"{dollars(rec['monthly_equivalent_cents'])} a month."
        )

    utils = _facts(conn, "utility")
    if utils:
        parts = []
        for u in utils:
            services = ", ".join(x["service"] for x in u["services"])
            late = f", {len(u['past_due'])} past due" if u["past_due"] else ""
            parts.append(
                f"{u['provider']} ({services}) last bill {dollars(u['last_total_cents'])}, "
                f"12-month average {dollars(u['avg_bill_12m_cents'])}{late}"
            )
        lines.append("Utility bills: " + "; ".join(parts) + ".")
    contrib = _fact(conn, "contributions", "all")
    if contrib and contrib["people"]:
        parts = [
            f"{p['person']} {x['plan']} {dollars(x['ytd_cents'])}"
            + (f" of {dollars(x['limit_cents'])}" if x["limit_cents"] is not None else "")
            for p in contrib["people"]
            for x in p["plans"]
        ]
        lines.append(f"Contributions {contrib['year']}: " + "; ".join(parts) + ".")

    cats = run(
        conn,
        """SELECT group_name, name FROM categories_safe WHERE active = 1
           ORDER BY group_name, name""",
    )
    if cats:
        lines += ["", "Categories by group:"]
        by_group: dict[str, list[str]] = {}
        for c in cats:
            by_group.setdefault(c["group_name"], []).append(c["name"])
        for g in sorted(by_group):
            lines.append(f"- {g}: {', '.join(by_group[g])}.")
    return "\n".join(lines)


def household_context(conn: AiConnection, signature: str, db_path: str | Path) -> str:
    """The context for this database file and facts signature, built once.

    `db_path` is the file `conn` reads. The caller passes it: the AI connection
    cannot run PRAGMA database_list."""
    key = (str(Path(db_path).resolve()), signature)
    with _LOCK:
        found = _CACHE.get(key)
    if found is not None:
        return found
    text = build(conn)
    with _LOCK:
        _CACHE.clear()  # only the newest is useful
        _CACHE[key] = text
    return text
