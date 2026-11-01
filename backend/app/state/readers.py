"""Read chat-owned state from run_sections (PLAN 3.7).

Rules:
- State comes, per section, from the newest imported monthly run (in run
  order) that carries the section. Backfill and correction runs carry none.
- Month-keyed sections (`cashflow_plan`, `budgets`): month M uses the newest
  run whose `meta.this_month` is on or before M. Past months keep the plan
  they had at the time.

Sections are parsed with the import models, so callers get typed objects.
Money fields stay decimal strings here; services turn them into cents.

Format 1.1 sections (`recurring`, `installment_plans`, `flags`, `pending`):
use `recurring`, `installment_plans`, `flags` and `pending` below. Each
returns the list from the newest monthly run that carries the section, or []
when no imported run has it yet (1.0 runs never do). `section_source` tells
which run that was. Items left out of the newest run count as ended or
resolved: these readers never merge older runs.

Format 1.3 `rewards`: one object with lists (programs, benefits, milestones,
routing, redemptions) and highlights. `rewards` returns it from the newest
monthly run that carries it, whole, or None before the first 1.3 run. Trips
are history with their own tables (migration 0009), not state.

Format 1.4 `goals`: a list sorted by key. `goals` returns it from the newest
monthly run that carries it, whole. A stored `[]` clears it. [] before the
first run that has it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from app.importer.models import (
    Budget,
    CashflowEvent,
    CashflowPhase,
    CashflowPlan,
    Flag,
    Goal,
    Income,
    IncomeStream,
    InstallmentPlan,
    Meta,
    PayoffStep,
    PendingItem,
    Recurring,
    Review,
    Rewards,
    Todo,
)
from app.importer.order import run_order_key

_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "meta": TypeAdapter(Meta),
    "cashflow_plan": TypeAdapter(list[CashflowPlan]),
    "cashflow_events": TypeAdapter(list[CashflowEvent]),
    "cashflow_phases": TypeAdapter(list[CashflowPhase]),
    "payoff_sequence": TypeAdapter(list[PayoffStep]),
    # income is a history section; its income_streams list is state (PLAN 3.7)
    "income": TypeAdapter(Income),
    "budgets": TypeAdapter(list[Budget]),
    "todos": TypeAdapter(list[Todo]),
    "review": TypeAdapter(Review),
    # format 1.1
    "recurring": TypeAdapter(list[Recurring]),
    "installment_plans": TypeAdapter(list[InstallmentPlan]),
    "flags": TypeAdapter(list[Flag]),
    "pending": TypeAdapter(list[PendingItem]),
    # format 1.3
    "rewards": TypeAdapter(Rewards),
    # format 1.4
    "goals": TypeAdapter(list[Goal] | None),  # a stored null reads as none
}


@dataclass(frozen=True)
class StateRun:
    run_id: str
    this_month: str | None  # from the run's own meta section
    sections: dict[str, str]  # section name -> stored JSON
    finished_at: str | None = None  # when the import finished (UTC ISO, sorts in time order)


def monthly_runs(conn: sqlite3.Connection) -> list[StateRun]:
    """Imported monthly runs with their state sections, newest first."""
    names = list(_ADAPTERS)
    rows = conn.execute(
        f"""SELECT s.run_id, s.section, s.json, r.finished_at FROM run_sections s
            JOIN import_runs r ON r.run_id = s.run_id
            WHERE r.status = 'imported' AND r.mode = 'monthly'
              AND s.section IN ({", ".join("?" for _ in names)})""",
        names,
    ).fetchall()
    by_run: dict[str, dict[str, str]] = {}
    finished: dict[str, str | None] = {}
    for r in rows:
        by_run.setdefault(r["run_id"], {})[r["section"]] = r["json"]
        finished[r["run_id"]] = r["finished_at"]
    out = []
    for run_id in sorted(by_run, key=run_order_key, reverse=True):
        sections = by_run[run_id]
        meta = json.loads(sections["meta"]) if "meta" in sections else {}
        out.append(StateRun(run_id, meta.get("this_month"), sections, finished[run_id]))
    return out


def _parse(section: str, text: str) -> Any:
    return _ADAPTERS[section].validate_python(json.loads(text))


def newest(runs: list[StateRun], section: str) -> Any | None:
    """The section from the newest run that carries it, parsed, or None."""
    for run in runs:
        if section in run.sections:
            return _parse(section, run.sections[section])
    return None


def previous(runs: list[StateRun], section: str) -> Any | None:
    """The section from the second newest run that carries it, parsed, or None."""
    found = [run for run in runs if section in run.sections][1:2]
    return _parse(section, found[0].sections[section]) if found else None


def for_month(runs: list[StateRun], section: str, month: str) -> list[Any]:
    """Items of a month-keyed section for `month`, from the newest run whose
    this_month is on or before it and that carries the section."""
    for run in runs:
        if run.this_month is None or run.this_month > month or section not in run.sections:
            continue
        return [item for item in _parse(section, run.sections[section]) if item.month == month]
    return []


def plan_months(runs: list[StateRun]) -> list[str]:
    """Every month any run plans for, ascending."""
    months: set[str] = set()
    for run in runs:
        if "cashflow_plan" in run.sections:
            months.update(p.month for p in _parse("cashflow_plan", run.sections["cashflow_plan"]))
    return sorted(months)


def meta(runs: list[StateRun]) -> Meta | None:
    return newest(runs, "meta")


def section_source(runs: list[StateRun], section: str) -> str | None:
    """Run id of the newest monthly run that carries `section`, or None."""
    return next((run.run_id for run in runs if section in run.sections), None)


# ---- format 1.1 sections (newest monthly run per section) ----


def recurring(runs: list[StateRun]) -> list[Recurring]:
    """Recurring bills and subscriptions, sorted by key."""
    return newest(runs, "recurring") or []


def previous_recurring(runs: list[StateRun]) -> list[Recurring] | None:
    """The recurring list from the monthly run before the newest one that has it."""
    return previous(runs, "recurring")


def installment_plans(runs: list[StateRun]) -> list[InstallmentPlan]:
    """Card installment plans, sorted by key. `remaining` is information only:
    never add it to net worth (card balances already include plan balances)."""
    return newest(runs, "installment_plans") or []


def flags(runs: list[StateRun]) -> list[Flag]:
    """The chat's flags, sorted by key. Read-only in v1."""
    return newest(runs, "flags") or []


def pending(runs: list[StateRun]) -> list[PendingItem]:
    """The chat's pending list, sorted by key. Read-only in v1."""
    return newest(runs, "pending") or []


# ---- format 1.3 rewards (newest monthly run, whole) ----


def rewards(runs: list[StateRun]) -> Rewards | None:
    """The chat's rewards view: every list sorted by key. None before the first
    1.3 monthly run. Items left out of the newest run count as ended."""
    return newest(runs, "rewards")


# ---- format 1.4 goals (newest monthly run, whole) ----


def goals(runs: list[StateRun]) -> list[Goal]:
    """The chat's goals, sorted by key, from the newest monthly run that carries the
    section, whole. A run that sends [] (or null) clears them. [] before the first such
    run."""
    return newest(runs, "goals") or []


def cashflow_phases(runs: list[StateRun]) -> list[CashflowPhase]:
    return newest(runs, "cashflow_phases") or []


def payoff_sequence(runs: list[StateRun]) -> list[PayoffStep]:
    return newest(runs, "payoff_sequence") or []


def income_streams(runs: list[StateRun]) -> list[IncomeStream]:
    """income.income_streams from the newest monthly run that carries the list."""
    for run in runs:
        if "income" in run.sections:
            income = _parse("income", run.sections["income"])
            if income.income_streams is not None:
                return sorted(income.income_streams, key=lambda s: s.key)
    return []


def newest_section_json(conn: sqlite3.Connection, section: str) -> Any | None:
    """Raw JSON of a section from the newest imported monthly run that has it."""
    rows = conn.execute(
        """SELECT s.run_id, s.json FROM run_sections s
           JOIN import_runs r ON r.run_id = s.run_id
           WHERE r.status = 'imported' AND r.mode = 'monthly' AND s.section = ?""",
        (section,),
    ).fetchall()
    if not rows:
        return None
    return json.loads(max(rows, key=lambda r: run_order_key(r["run_id"]))["json"])
