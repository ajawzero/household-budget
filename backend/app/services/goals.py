"""Goals page (format 1.4 `goals`, docs/plans/format-14.md sections 3.8 and 7).

Each goal from the newest monthly run that has the section (readers.goals), with now =
the sum of its accounts' balances today: balances.history with estimates, as on Accounts.
A plain list. The college projection is D2, later.

Rules:
- An account with no balance yet adds nothing and is marked missing. With no balance at
  all, now is None, never a silent 0. A card tracked by payments has no balance and is
  never missing. A closed account counts 0, one not open yet counts 0.
- as_of is the oldest balance date among the accounts that have one. stale when any of
  those is stale.
- to_go = target - now, never below 0. reached when now >= target.
- start_year shows here only. The Dashboard and the AI facts never read goals (PLAN 3.8,
  5.7). Nothing else imports this module.
"""

from __future__ import annotations

import sqlite3

from app.importer.models import Goal, to_cents
from app.services.balances import History, history
from app.services.networth import estimate_out, humanize, today
from app.state import readers


def _account(h: History, key: str) -> dict:
    a = h.accounts.get(key)
    if a is None:  # gone from the registry (a purge): by key, no balance
        return {
            "account_key": key,
            "display_name": humanize(key),
            "balance_cents": None,
            "as_of": None,
            "stale": False,
            "missing": True,
            "estimate": None,
        }
    b = h.at_end(key)
    return {
        "account_key": key,
        "display_name": a["display_name"],
        "balance_cents": b.value_cents,  # 0 before opened_on; None missing or tracked
        "as_of": b.as_of,
        "stale": b.stale,
        "missing": b.missing,
        "estimate": estimate_out(b),
    }


def goal_out(g: Goal, h: History, people: dict[str, str]) -> dict:
    accounts = [_account(h, key) for key in g.accounts]
    known = [a for a in accounts if a["balance_cents"] is not None]
    now = sum(a["balance_cents"] for a in known) if known else None
    target = to_cents(g.target)
    dates = [a["as_of"] for a in known if a["as_of"]]
    return {
        "key": g.key,
        "name": g.name,
        "kind": g.kind,
        "person_name": people.get(g.person, humanize(g.person)) if g.person else None,
        "start_year": g.start_year,
        "target_date": g.target_date,
        "note": g.note,
        "target_cents": target,
        "now_cents": now,
        "to_go_cents": None if now is None else max(target - now, 0),
        "reached": now is not None and now >= target,
        "as_of": min(dates) if dates else None,
        "stale": any(a["stale"] for a in known),
        "accounts": accounts,
    }


def goals_page(conn: sqlite3.Connection) -> dict:
    on = today()  # household date; tests patch config.household_today
    runs = readers.monthly_runs(conn)
    h = history(conn, on, on)  # cached per import signature
    people = {
        r["person_key"]: r["display_name"]
        for r in conn.execute("SELECT person_key, display_name FROM people")
    }
    return {
        "goals": [goal_out(g, h, people) for g in readers.goals(runs)],
        "source_run_id": readers.section_source(runs, "goals"),
    }
