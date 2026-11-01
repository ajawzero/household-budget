"""GET /api/checks: the app's numbers next to the chat's, on a statement basis (PLAN 8.2).

- The run: the one asked for, else the newest imported monthly run (run order)
  that has a `net_worth` section. Its `net_worth.as_of` is the day compared.
- App numbers on that day use stored balances: statement, valuation, manual,
  and the monthly run's own estimates for loans with no statement. Price
  estimates are computed, never stored, so they never count here.
- Parts are by nw_group with secured loans netted into their asset's group,
  the same as By owner on the Accounts page.
- A line matches when the two differ by 100 cents or less.
- Cash flow: the chat's `cashflow_actual` next to the app's own numbers from
  v_cashflow_rows, plus the Phase 4 lists in app/services/checks_p4.py.

Notes are plain sentences that explain gaps. They hold account names and
dates, never amounts.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from app.importer.models import CASH_LIKE_TYPES, CashflowActual, NetWorth, to_cents
from app.importer.order import run_order_key
from app.services import budget_targets, checks_p4, rewards, trips, utilities
from app.services import cashflow as cf
from app.services.balances import History, history
from app.services.coverage import coverage_state, missing_cells
from app.services.networth import SECURED_LOAN_TYPES, by_owner, humanize, nw_labels, owner_group
from app.state import readers

MATCH_CENTS = 100


def line(app: int | None, chat: int | None) -> dict:
    """One compared number. status: match, differs, app_only or chat_only."""
    if app is None or chat is None:
        status = "chat_only" if app is None else "app_only"
        return {"app_cents": app, "chat_cents": chat, "diff_cents": None, "status": status}
    diff = app - chat
    return {
        "app_cents": app,
        "chat_cents": chat,
        "diff_cents": diff,
        "status": "match" if abs(diff) <= MATCH_CENTS else "differs",
    }


def _net_worth_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.run_id, r.mode, s.json FROM run_sections s
           JOIN import_runs r ON r.run_id = s.run_id
           WHERE r.status = 'imported' AND s.section = 'net_worth'"""
    ).fetchall()


def pick_run(conn: sqlite3.Connection, run_id: str | None) -> sqlite3.Row | None:
    rows = _net_worth_runs(conn)
    if run_id is not None:
        return next((r for r in rows if r["run_id"] == run_id), None)
    monthly = [r for r in rows if r["mode"] == "monthly"]
    return max(monthly, key=lambda r: run_order_key(r["run_id"])) if monthly else None


def _section(conn: sqlite3.Connection, run_id: str, section: str) -> object | None:
    row = conn.execute(
        "SELECT json FROM run_sections WHERE run_id = ? AND section = ?", (run_id, section)
    ).fetchone()
    return json.loads(row["json"]) if row else None


def _worked_back(conn: sqlite3.Connection, h: History, as_of: str) -> list[str]:
    """Accounts whose balance on as_of comes from a statement that closed later."""
    types = sorted(CASH_LIKE_TYPES)
    keys = set(h.net_worth_keys())
    out = []
    for r in conn.execute(
        f"""SELECT s.account_key, min(s.period_end) AS closed FROM statements s
            WHERE s.is_current = 1 AND s.period_start <= ? AND s.period_end > ?
              AND s.account_key IN (SELECT account_key FROM accounts
                                    WHERE type IN ({", ".join("?" for _ in types)}))
            GROUP BY s.account_key ORDER BY s.account_key""",
        (as_of, as_of, *types),
    ):
        b = h.at_end(r["account_key"]) if r["account_key"] in keys else None
        if b is not None and b.source == "statement" and b.as_of == as_of:
            name = h.accounts[r["account_key"]]["display_name"]
            out.append(
                f"{name}: the balance on {checks_p4.date_label(as_of)} is worked back "
                f"from the statement that closed {checks_p4.date_label(r['closed'])}."
            )
    return out


def checks(conn: sqlite3.Connection, run_id: str | None = None) -> dict | None:
    """The Checks payload, or None when there is no such run."""
    run = pick_run(conn, run_id)
    if run is None:
        return None
    chat = NetWorth.model_validate(json.loads(run["json"]))
    as_of = chat.as_of
    day = date.fromisoformat(as_of)
    h = history(conn, day, day, estimates=False)  # statement basis
    nw = h.net_worth()

    labels = nw_labels(h)
    app_parts = {p["key"]: p for p in by_owner(h)}
    chat_parts = {p.key: to_cents(p.amount) for p in chat.parts}
    parts = [
        {"key": key, "label": p["label"], **line(p["cents"], chat_parts.get(key))}
        for key, p in app_parts.items()
    ]
    parts += [
        {"key": key, "label": labels.get(key, humanize(key)), **line(None, cents)}
        for key, cents in sorted(chat_parts.items())
        if key not in app_parts
    ]

    notes = [
        "App numbers use the stored balances on "
        f"{checks_p4.date_label(as_of)}, including the monthly run's estimates for loans "
        "with no statement. No price estimates."
    ]
    for key in nw.missing:
        notes.append(
            f"{h.accounts[key]['display_name']} has no balance on {checks_p4.date_label(as_of)}. "
            "The app total leaves it out."
        )
    for key in nw.stale:
        notes.append(
            f"{h.accounts[key]['display_name']}'s newest balance is older than "
            "its next expected statement."
        )
    notes += _worked_back(conn, h, as_of)
    netted: dict[str, str] = {}
    for key in h.net_worth_keys():
        a = h.accounts[key]
        group = a["nw_group"] or "other"
        if a["type"] in SECURED_LOAN_TYPES and owner_group(h, key) != group:
            netted[group] = owner_group(h, key)
    for key, into in sorted(netted.items()):
        if key in chat_parts:
            notes.append(
                f"The chat lists {labels.get(key, humanize(key))} on its own. "
                f"The app counts it inside {labels.get(into, humanize(into))}."
            )

    state = coverage_state(conn)
    chat_months = [
        CashflowActual.model_validate(c)
        for c in _section(conn, run["run_id"], "cashflow_actual") or []
    ]
    cashflow = checks_p4.cashflow_checks(conn, chat_months, state)
    runs, edits = budget_targets.runs_and_edits(conn)
    plan_months = readers.plan_months(runs)
    plan = cf.plan_for(runs, plan_months[-1], edits) if plan_months else None
    util = utilities.checks(conn)

    return {
        "run_id": run["run_id"],
        "as_of": as_of,
        "net_worth": line(nw.value_cents, to_cents(chat.value)),
        "assets": line(nw.assets_cents, to_cents(chat.assets)),
        "liabilities": line(nw.liabilities_cents, to_cents(chat.liabilities)),
        "parts": parts,
        "cashflow": cashflow,
        "plan_mapping": cf.plan_mapping(cf.labels(conn), plan) if plan else [],
        "plan_mapping_month": plan.month if plan else None,
        "unpaired_transfers": checks_p4.unpaired_transfers(conn, state),
        "card_payments_no_statement": checks_p4.card_payments_no_statement(conn, state),
        "missing_statements": missing_cells(conn),
        "payment_basis_watch": checks_p4.payment_basis_watch(conn, state),
        "utility_past_due": util["past_due"],
        "utility_payments_no_bill": util["payments_no_bill"],
        "utility_warnings": util["warnings"],
        "utility_bill_count": util["bill_count"],
        "rewards": rewards.checks(conn),
        "trips": trips.checks(conn),
        "latest_complete_month": state.latest_complete_month(),
        "notes": notes,
    }
