"""Budget target edits (docs/plans/budget-targets.md): the owner's group targets, laid
on the chat's plan when a page is read. The chat's run data is never changed.

- One `budget_target_edits` row (migration 0014) per (kind, key, month). kind is
  'group' in v1: a key of `cashflow_plan[month].by_group`. Editable keys are the
  spending category groups and Saved and invested. Plan It installments is what
  the plans billed, so it is never edited. A spending group the chat left out can
  get a target: the edit adds it to the plan.
- `cashflow.plan_view` is the one reader of the chat's plan. It calls
  `effective_plan` with the month's rows, and `cashflow.plan_for` returns the
  plan every screen shows. Callers read the runs and the rows in one read
  transaction (`runs_and_edits`), so an import that commits mid-read cannot mix
  them.
- The merge works on a plain dict and rebuilds `CashflowPlan` with
  model_validate, never model_copy: the models are strict and frozen. Amounts go
  back through cents_to_str. Groups stay in key order, app-added ones too.
  planned_spending moves by the sum of (edited - chat) over the rows, so a gap
  the chat's groups already had stays the same. planned_income never changes.
- If the rebuild fails, the log names the error class only, and the caller gets
  the chat's plan with a notice. Never a 500. With no rows for the month, the
  chat's plan comes back as it is.
- A month with no chat plan shows no plan, even with rows. They stay and come
  back when the chat plans the month again.

Who wins, on each read (the rewards base rule). Each row holds the owner's number
(target) and the chat's number for that month and key when it was saved (base, NULL
when the chat had none). With chat_now the chat's number today:
- chat_now == target: the chat caught up. It shows as the chat's, no tag. The
  row stays, so a later change by the chat is still caught.
- chat_now == base, and no run imported after the save sent target: the owner's
  number shows, tagged edited.
- anything else (the chat changed or dropped the group, or caught up and then
  went back to base): the owner's number stays, tagged edited, with a conflict. Keep
  mine saves the same target again, which re-bases the row on chat_now and
  dates it now. Use the chat's deletes the row. A run counts as after the save
  by its finished_at against the row's updated_at (both UTC ISO).

A row whose key is no longer editable (its group stopped counting in spending)
is left out on read, so a key nobody can change in the app never moves a plan.
It stays in the table and comes back if the group counts again.

Saves (`save`) run inside the caller's `write_transaction` and refuse to run
outside one. Every check runs before the first write, so all changes save or
none do. Each save adds one `events` row with counts only, never amounts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from app import config
from app.importer.models import CashflowPlan, cents_to_str, to_cents
from app.services.coverage import current_month
from app.services.overrides import EditError
from app.services.rewards_overlay import read_transaction
from app.state import readers

log = logging.getLogger("app.budgets")

KIND_GROUP = "group"
SAVED_INVESTED = "saved-invested"
PLAN_INSTALLMENTS = "plan-installments"
MAX_CENTS = 10**8  # $1,000,000.00
MAX_CHANGES = 40
EVENT_KIND = "budget_target_edit"
ENTITY = "budget_target_edits"

FAILED_TEXT = "Your target edits could not be shown. These are the chat's. Nothing was lost."
PAST_TEXT = "Past months keep the targets they had."
ADMIN_TEXT = "Only an admin can change targets."
NO_PLAN_TEXT = "The chat has not sent a plan for this month yet."
STALE_TEXT = "The chat's plan changed. Look again."
INSTALLMENTS_TEXT = "Plan It installments are set by your plans."
UNKNOWN_TEXT = "That group cannot get a target."
DUPLICATE_TEXT = "Each group can change once per save."
AMOUNT_TEXT = "Use an amount from $0 to $1,000,000."
COUNT_TEXT = f"Change 1 to {MAX_CHANGES} targets at a time."
NEGATIVE_TEXT = "The chat planned less than $0 for a group you changed, so it cannot get a target."
NEGATIVE_LATER_TEXT = (
    "The chat planned less than $0 in a later month for a group you changed. "
    "Save without the later months."
)
# The largest INTEGER SQLite stores. A chat number above it cannot be a base.
SQLITE_MAX = (1 << 63) - 1
TOO_LARGE_TEXT = "The chat's number for a group you changed is too large to store."
TOO_LARGE_LATER_TEXT = (
    "The chat's number in a later month for a group you changed is too large to store. "
    "Save without the later months."
)

Source = Literal["chat", "edited"]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    kind: str
    target_key: str
    month: str
    target_cents: int
    base_cents: int | None
    updated_by: int | None
    updated_at: str


@dataclass(frozen=True)
class Edits:
    rows: tuple[Edit, ...] = ()

    def for_month(self, month: str) -> tuple[Edit, ...]:
        return tuple(r for r in self.rows if r.month == month)

    def get(self, key: str, month: str) -> Edit | None:
        return next(
            (
                r
                for r in self.rows
                if r.kind == KIND_GROUP and r.target_key == key and r.month == month
            ),
            None,
        )


NO_EDITS = Edits()


def load(conn: sqlite3.Connection) -> Edits:
    """The rows for keys that are editable today, in one read transaction (or
    the caller's). A tiny table. A row for another key stays in the table but
    is left out, so it never moves a plan that nobody can change back."""
    with read_transaction(conn):
        allowed = editable_keys(conn)
        rows = conn.execute(
            """SELECT kind, target_key, month, target_cents, base_cents, updated_by, updated_at
               FROM budget_target_edits ORDER BY kind, target_key, month"""
        ).fetchall()
    return Edits(tuple(Edit(*r) for r in rows if r[1] in allowed))


def runs_and_edits(conn: sqlite3.Connection) -> tuple[list[readers.StateRun], Edits]:
    """The monthly runs and the edits, from one snapshot."""
    with read_transaction(conn):
        return readers.monthly_runs(conn), load(conn)


def editable_keys(conn: sqlite3.Connection) -> frozenset[str]:
    """The spending category groups and Saved and invested."""
    rows = conn.execute("SELECT group_key FROM category_groups WHERE counts_in_spending = 1")
    return frozenset({r[0] for r in rows} | {SAVED_INVESTED})


def amounts(plan: CashflowPlan | None) -> dict[str, int]:
    """Group key -> cents, in plan order. Empty with no plan."""
    return {g.key: to_cents(g.amount) for g in plan.by_group} if plan else {}


# --------------------------------------------------------------------------
# The overlay
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSource:
    """Where one edited group's number came from (see the module rules)."""

    key: str
    source: Source
    chat_cents: int | None  # the chat's number today; None when it sends none
    target_cents: int
    base_cents: int | None
    by: int | None
    at: str
    conflict: bool


@dataclass(frozen=True)
class PlanView:
    plan: CashflowPlan | None  # what every screen shows
    chat: CashflowPlan | None  # the chat's plan, as sent
    groups: dict[str, GroupSource]  # one per row laid on top, by key
    notice: str | None = None


def judge(
    chat_cents: int | None, target_cents: int, base_cents: int | None, caught_up: bool = False
) -> tuple[Source, bool]:
    """(source, conflict) for one row: the base rule. caught_up: a run imported
    after the save sent target, so a chat back on base has moved on again."""
    if chat_cents == target_cents:
        return "chat", False
    if chat_cents == base_cents and not caught_up:
        return "edited", False
    return "edited", True


def caught_up(
    rows: Sequence[Edit], since: Sequence[tuple[str | None, CashflowPlan]]
) -> frozenset[str]:
    """Keys of the rows the chat caught up on after the save: a run that finished
    after the row's updated_at sent the owner's number. since holds (finished_at,
    the month's plan) for runs imported after an edit (cashflow.plan_view)."""
    return frozenset(
        r.target_key
        for r in rows
        if any(
            at is not None and at > r.updated_at and amounts(p).get(r.target_key) == r.target_cents
            for at, p in since
        )
    )


def effective_plan(
    chat: CashflowPlan | None, rows: Sequence[Edit], caught: frozenset[str] = frozenset()
) -> PlanView:
    """The chat's plan for one month with that month's rows on top. caught: the
    keys the chat caught up on since the save (see caught_up)."""
    if chat is None:
        return PlanView(None, None, {})
    rows = [r for r in rows if r.kind == KIND_GROUP and r.month == chat.month]
    if not rows:
        return PlanView(chat, chat, {})
    sent = amounts(chat)
    merged = dict(sent)
    groups: dict[str, GroupSource] = {}
    try:
        delta = 0
        for r in rows:
            was = sent.get(r.target_key)
            merged[r.target_key] = r.target_cents
            delta += r.target_cents - (was or 0)
            source, conflict = judge(was, r.target_cents, r.base_cents, r.target_key in caught)
            groups[r.target_key] = GroupSource(
                key=r.target_key,
                source=source,
                chat_cents=was,
                target_cents=r.target_cents,
                base_cents=r.base_cents,
                by=r.updated_by,
                at=r.updated_at,
                conflict=conflict,
            )
        data = chat.model_dump(mode="json", exclude_none=True)
        data["by_group"] = [
            {"key": k, "amount": cents_to_str(c)} for k, c in sorted(merged.items())
        ]
        data["planned_spending"] = cents_to_str(to_cents(chat.planned_spending) + delta)
        plan = CashflowPlan.model_validate(data)
    # ValueError and OverflowError: bad numbers in the data. The page shows the
    # chat's plan, never a 500.
    except (ValidationError, KeyError, TypeError, ValueError, OverflowError) as exc:
        log.warning("budget targets overlay failed", extra={"error": type(exc).__name__})
        return PlanView(chat, chat, {}, FAILED_TEXT)
    return PlanView(plan, chat, groups)


def household_date(at: str) -> str:
    """An ISO timestamp as the household date (America/Chicago)."""
    return datetime.fromisoformat(at).astimezone(config.HOUSEHOLD_TZ).date().isoformat()


def provenance(conn: sqlite3.Connection, view: PlanView, key: str) -> tuple[Source, dict | None]:
    """(source, edit) for one group on the page. edit is set only for an edited
    group: who changed it, on which household date, and whether the chat sent
    another number since."""
    src = view.groups.get(key)
    if src is None or src.source == "chat":
        return "chat", None
    name = None
    if src.by is not None:
        row = conn.execute("SELECT display_name FROM users WHERE id = ?", (src.by,)).fetchone()
        name = row[0] if row else None
    return "edited", {"by": name, "at": household_date(src.at), "conflict": src.conflict}


def later_plan_months(runs: list[readers.StateRun], month: str, edits: Edits) -> list[str]:
    """The months after `month` the chat planned, ascending: what also_later saves."""
    from app.services import cashflow as cf  # cashflow imports this module

    return [
        m
        for m in readers.plan_months(runs)
        if m > month and cf.plan_view(runs, m, edits).chat is not None
    ]


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    group_key: str
    target_cents: int | None  # None: Use the chat's (the row goes)
    seen_chat_cents: int | None  # the chat's number the page showed


def _in_write(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("budget target edits are written inside write_transaction")


def _apply(
    conn: sqlite3.Connection,
    month: str,
    key: str,
    target: int | None,
    chat_now: int | None,
    row: Edit | None,
    user_id: int | None,
    now: str,
) -> str | None:
    """One row: set, kept (the same target re-based), cleared, or nothing."""
    if target is None or target == chat_now:
        if row is None:
            return None
        conn.execute(
            "DELETE FROM budget_target_edits WHERE kind = ? AND target_key = ? AND month = ?",
            (KIND_GROUP, key, month),
        )
        return "cleared"
    conn.execute(
        """INSERT INTO budget_target_edits
               (kind, target_key, month, target_cents, base_cents, updated_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (kind, target_key, month) DO UPDATE SET
             target_cents = excluded.target_cents, base_cents = excluded.base_cents,
             updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        (KIND_GROUP, key, month, target, chat_now, user_id, now),
    )
    return "kept" if row is not None and row.target_cents == target else "set"


def save(
    conn: sqlite3.Connection,
    *,
    month: str,
    changes: Sequence[Change],
    also_later: bool,
    user_id: int | None,
    now: str,
) -> dict[str, int]:
    """Save group target changes for `month`, and with also_later for each later
    month the chat planned (each with its own base). Raises EditError. Call inside
    a write transaction. Returns the counts written to the events row."""
    _in_write(conn)
    from app.services import cashflow as cf  # cashflow imports this module

    if month < current_month():
        raise EditError(422, PAST_TEXT)
    if not 1 <= len(changes) <= MAX_CHANGES:
        raise EditError(422, COUNT_TEXT)
    keys = [c.group_key for c in changes]
    if len(set(keys)) != len(keys):
        raise EditError(422, DUPLICATE_TEXT)
    allowed = editable_keys(conn)
    for c in changes:
        if c.group_key == PLAN_INSTALLMENTS:
            raise EditError(422, INSTALLMENTS_TEXT)
        if c.group_key not in allowed:
            raise EditError(422, UNKNOWN_TEXT)
        if c.target_cents is not None and not 0 <= c.target_cents <= MAX_CENTS:
            raise EditError(422, AMOUNT_TEXT)

    runs = readers.monthly_runs(conn)
    edits = load(conn)
    chat = cf.plan_view(runs, month, edits).chat
    if chat is None:
        raise EditError(409, NO_PLAN_TEXT)
    sent = {month: amounts(chat)}
    for c in changes:
        if c.seen_chat_cents != sent[month].get(c.group_key):
            raise EditError(409, STALE_TEXT)
    if also_later:
        for m in later_plan_months(runs, month, edits):
            sent[m] = amounts(cf.plan_view(runs, m, edits).chat)
    # A target is never below $0, and neither is the base it stores. A chat
    # number below $0 (the import format allows one), or one too large for an
    # SQLite INTEGER, cannot take an edit. Use the chat's still works, since it
    # only deletes.
    for m, chat_amounts in sent.items():
        for c in changes:
            base = chat_amounts.get(c.group_key) or 0
            if c.target_cents is None:
                continue
            if base < 0:
                raise EditError(422, NEGATIVE_TEXT if m == month else NEGATIVE_LATER_TEXT)
            if base > SQLITE_MAX:
                raise EditError(422, TOO_LARGE_TEXT if m == month else TOO_LARGE_LATER_TEXT)

    counts = {"set": 0, "kept": 0, "cleared": 0, "also_later": len(sent) - 1}
    for m, chat_amounts in sent.items():
        for c in changes:
            done = _apply(
                conn,
                m,
                c.group_key,
                c.target_cents,
                chat_amounts.get(c.group_key),
                edits.get(c.group_key, m),
                user_id,
                now,
            )
            if done:
                counts[done] += 1
    conn.execute(
        """INSERT INTO events (at, user_id, kind, entity, entity_key, detail_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (now, user_id, EVENT_KIND, ENTITY, month, json.dumps(counts, sort_keys=True)),
    )
    return counts
