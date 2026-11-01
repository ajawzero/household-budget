"""The effective rewards view (docs/plans/rewards-inputs.md section 3): the
chat's rewards with the owner's app inputs laid on top, on read. The chat's data is
never changed.

- `readers.rewards(runs)` stays pure and chat-only. When it is None (before the
  chat's first 1.3 run), `effective_rewards` returns None and the empty state
  stays.
- The six `reward_*` tables (migration 0010) are read in one read transaction
  when the connection is not already in one. Inside a write it uses the
  caller's transaction. Callers read the runs inside `read_transaction` too, so
  the chat's rewards and the inputs come from one snapshot and an import that
  commits mid-read cannot mix them.
- The merge works on a plain dict and rebuilds `Rewards` with model_validate,
  never model_copy: the models are strict and frozen, and model_copy skips the
  checks. Sort order, duplicate keys and program references are checked only
  by the importer (snapshot_issues), so the merge sorts every list and checks
  those itself.
- Provenance stays outside the model, in `Effective.meta`. `source` inside the
  model stays the chat's `statement` or `manual`.
- If the rebuild fails, the log names the error class only, and the caller
  gets the chat's view with a notice. Nothing is lost. A malformed merged dict
  (an item with no key, a cycle with no start) fails in the merge or the sort
  with KeyError or TypeError before model_validate sees it, and counts the same.
  So do ValueError and OverflowError (a bad number or date): never a 500.

Order: merge programs and benefits, match credits (hidden benefits included),
fill cycle use, take out hidden benefits, validate. With no app rows the merge
changes nothing, so the result equals the chat's view.

Programs and balances (plan 3.1):
- App programs become RewardProgram items with their `app-` key. When the chat
  sends that key too, the app program wins, with a notice. The chat's balance
  is then a candidate only when the chat counts the key in the app program's
  unit. Else it is never shown, and the notice says so (fields ["unit"]).
- An app program and a chat program with the same name (casefolded) give a
  duplicate notice, unless "Keep both" saved that chat key.
- Each entry keeps the unit it was typed in: whole (points and miles) or cash
  (cash back). The stored unit is the truth, never a guess from older runs.
- Balance candidates: the chat's balance when sent, plus the app entries for
  that key in the program's unit now, dated on or before household today.
  The newest as_of wins. On the same day the app wins, and the chat's number
  is kept in meta when it differs. Timeshares take no entries (they use
  allocations).
- An app balance that wins goes in the model as `manual` (the model knows only
  statement and manual). Meta says it came from the app.
- Entries that are not candidates are kept, not shown, and listed as an
  orphan with a reason and their unit, one per unit: the chat dropped the key
  (dropped), it is now a timeshare (timeshare), or the program now counts in
  the other unit (unit). Move keeps the unit.
- "Keep both" stores one chat key. While that chat program keeps the name, it
  covers every chat program with the name (the chat can send one per owner).

Benefits (plan 3.2), per key: the chat's item from the newest run and the
reward_benefit_edits row.
- No row: shown as sent. A chat row: each field in fields_json wins. A JSON
  null clears the field. key, account_key, kind, cadence and cycles never
  change (a cadence change is an end plus an add).
- base_json holds the chat's value of each edited field when it was saved.
  The chat's value now equal to base: quiet. Equal to the owner's: the field stops
  counting as edited. Else the owner's stays, with a notice per field.
- An amount edit starts at amount_from. Cycles that start before it keep the
  old amount, pinned as the cycle's amount: the chat's for a chat item,
  amount_before for an app item. A cycle with its own amount keeps it.
- An edit that no longer fits the chat's item is skipped, and the chat's item
  is shown, with a notice: a field of another kind, a required field cleared,
  or a merged item that fails RewardBenefit or names a program the page does
  not show (the chat renamed it). Each item is checked on its own, so one
  stale edit never takes down the rest.
- A field the chat caught up on (the chat now sends the owner's value) stops
  counting as edited on read. The next write on that row drops it. Until then,
  if the chat moves on to a third value, the owner's value comes back with a notice
  (Keep mine, Use the chat's), never quietly.
- An app row holds the whole RewardBenefit body. Calendar cadences get this
  household year's cycles at read time. multi_year and one_time store theirs.
  A body a benefit file added keeps its start next to it (a multi_year or
  one_time one only from a later year): cycles that end before it are left
  out, the one that holds it starts on it, and meta keeps the start, so Fees
  count nothing of it in a year before it (every kind).
  When the chat sends the same key, the app item wins, with a notice. An app
  item that no longer passes (its program is gone) is left out, with a notice
  and Delete. When the chat sends that key too, the chat's item is shown.
- An app item and a chat item on the same card and kind, with the same name
  (casefolded) or the same cadence and amount, give a duplicate notice unless
  "Keep both" saved that chat key.
- Hidden benefits leave `benefits` after matching, so every reader skips
  them. They sit in meta.hidden.
- Inputs on a key the chat no longer sends (edits, words, choices, marks) are
  kept, not shown, and listed as an orphan. A row that only hides stays quiet.

Credits and cycle use (plan 3.3 and 4, `rewards_match`):
- Statement credits are matched to the credits, hidden ones included, with the
  rows read in the same read transaction as the inputs.
- One source per cycle, never added together: a cycle mark ("set", 0 means not
  used); else the matched rows when their sum is above 0 ("rows", used_on is
  the latest row date); else the chat's used and used_on ("chat"); else not
  used. The chat's used stays in meta, so the page can say "The chat says $X"
  when rows win and it differs. Rows above the amount count in full (over).
- A decided cycle (a choice or a spill moved its row elsewhere) never falls
  back to the chat's used, which may count the moved row. Else moving
  February's row to January would count it in January's rows and again in
  February's chat used. Covered, it reads from rows even when none are left:
  used 0, with no chat-only flag. Not covered yet, it waits for rows (no
  source, used 0), unless the chat's used_on is after the card's statements
  end: that use cannot be a moved row, so it stands.
- Covered: the card's current statements run from the cycle start or earlier
  through the cycle end plus 7 days. A cycle not covered waits for rows.
- A covered cycle on a benefit with match words, where the chat says used and
  no row matched: the chat's value stands, with a chat-only flag. Not on a
  match_off benefit.
- Marks on a cycle the chat no longer sends are kept and ignored. A benefit
  with no words, no choices and no marks reads exactly as the chat sent it.
- A tie in the review names only shown credits. A tie of hidden credits only
  stays quiet (their words claim the row). One shown credit left makes it a
  plain row to review, with that credit as the guess.
- A link to a credit that takes no rows (matching off, or not a credit on that
  card now) is flagged, so it can be seen and deleted. A link to a key the chat
  no longer sends is listed as an orphan instead.
"""

from __future__ import annotations

import calendar
import copy
import json
import logging
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from pydantic import ValidationError

from app import config
from app.importer.models import (
    BENEFIT_FIELDS,
    BENEFIT_REQUIRED,
    RewardBenefit,
    Rewards,
    cents_to_str,
    micro_to_str,
    to_cents,
    to_micro,
)
from app.services import rewards_match as rm
from app.services.rewards_match import RowFlag
from app.state import readers

log = logging.getLogger("app.rewards")

FAILED_TEXT = "Your edits could not be shown. Nothing was lost."
APP_KEY_TEXT = "The chat now sends this program too. Yours is shown."
DUPLICATE_TEXT = "Two programs have the same name."
BENEFIT_APP_KEY_TEXT = "The chat now sends this benefit too. Yours is shown."
NO_FIT_TEXT = "Your edit no longer fits this benefit."
APP_NO_FIT_TEXT = (
    "This benefit can no longer be shown. The program it names may be gone. "
    "Delete it and add it again."
)
BENEFIT_DUPLICATE_TEXT = "The chat now sends a benefit like this one. Fees may count it twice."
CHANGED_TEXT = "The chat changed {label} since your edit."
MICRO = 1_000_000
# The unit a balance is counted in. Points and miles are whole, cash back is
# dollars. Timeshares take no balances.
UNIT = {"points": "whole", "miles": "whole", "cash_back": "cash"}
LISTS = ("programs", "benefits", "milestones", "routing", "redemptions")
# Inner lists: sorted by these fields, no duplicates (like snapshot_issues).
INNER = {
    "programs": {"earned_on": None, "allocations": "year"},
    "benefits": {"cycles": "start", "category_keys": None},
}
# Benefits: cadences whose cycles the app makes for the household year, and
# the ones that store their own.
CALENDAR_STEPS = {"monthly": 1, "quarterly": 3, "semi_annual": 6, "annual": 12}
STORED_CYCLES = ("multi_year", "one_time")
# An app body's own first day, kept next to its RewardBenefit fields. A benefit
# file adds a benefit "from starts_on" with it (rewards_files): every kind, but
# a multi_year or one_time one only from a later year.
APP_START = "starts_on"
# Never edited. A cadence change is an end plus an add.
FIXED_FIELDS = ("key", "account_key", "kind", "cadence", "cycles")
EDITABLE_FIELDS = tuple(f for f in RewardBenefit.model_fields if f not in FIXED_FIELDS)
ALL_KIND_FIELDS = frozenset().union(*BENEFIT_FIELDS.values())
# In a notice: "The chat changed <label> since your edit." The drawer's labels
# (Yearly value, Loads by itself, Enrolled, Rolls over) are in the words.
FIELD_LABELS = {
    "name": "the name",
    "amount": "the amount",
    "counted_value": "the yearly value",
    "auto": "whether it loads by itself",
    "enrolled": "whether you are enrolled",
    "rolls_over": "whether it rolls over",
    "enroll_by": "the enroll-by date",
    "ends_on": "the end date",
    "how_to_use": "how to use it",
    "note": "the note",
    "cap": "the cap",
    "earn_before": "the rate before the cap",
    "earn_after": "the rate after the cap",
    "category_keys": "the categories",
    "reported_spent": "the reported spend",
    "as_of": "the reported date",
    "program_key": "the program",
    "status_tier": "the status tier",
}
# table -> ORDER BY. Every input the overlay reads.
INPUT_TABLES = {
    "reward_balances": "program_key, as_of",
    "reward_app_programs": "program_key",
    "reward_benefit_edits": "benefit_key",
    "reward_match_terms": "benefit_key, upper(term)",
    "reward_credit_decisions": "txn_key",
    "reward_cycle_marks": "benefit_key, cycle_start",
}


class OverlayError(ValueError):
    """The merged rewards break a rule model_validate does not check. Carries a
    rule id only, never a value."""


# --------------------------------------------------------------------------
# Inputs and the result
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    """The rows of the six reward tables, each in its ORDER BY."""

    balances: tuple[dict[str, Any], ...] = ()
    programs: tuple[dict[str, Any], ...] = ()
    edits: tuple[dict[str, Any], ...] = ()
    terms: tuple[dict[str, Any], ...] = ()
    decisions: tuple[dict[str, Any], ...] = ()
    marks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Notice:
    """A note for the page. `text` is fixed wording, never a value."""

    kind: str
    text: str
    key: str | None = None  # the program or benefit it is about
    fields: tuple[str, ...] = ()
    other: str | None = None  # the chat's key a duplicate is like


@dataclass(frozen=True)
class Orphan:
    """Inputs the page keeps but does not show ("Needs a look")."""

    kind: str  # program or benefit
    key: str
    holds: tuple[str, ...]  # balances, edits, terms, choices, marks
    # dropped: the chat no longer sends the key. Programs only: timeshare (the
    # program is now a timeshare) or unit (it now counts in the other unit).
    reason: str = "dropped"
    unit: str | None = None  # programs: the entries' unit, whole or cash


@dataclass
class OverlayMeta:
    """What the merge found, kept outside the model. With no app rows it holds
    only what matching finds: the chat's cycle sources, coverage and the rows
    to review."""

    # (program, balance or benefit, key) -> "app" or "edited". Left out: the chat's.
    provenance: dict[tuple[str, str], str] = field(default_factory=dict)
    # program key -> the chat's balance (micro) for the same day, when the app's
    # entry wins and the two differ ("The chat has N for the same day").
    chat_balance: dict[str, int] = field(default_factory=dict)
    edited_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # benefit key -> the day the owner's amount change starts, while it has one.
    amount_from: dict[str, str] = field(default_factory=dict)
    # benefit key -> an app body's own first day (APP_START, a file's starts_on).
    # Fees count nothing of it in a year before it.
    starts_on: dict[str, str] = field(default_factory=dict)
    hidden: list[RewardBenefit] = field(default_factory=list)  # taken out of the view
    hidden_keys: set[str] = field(default_factory=set)  # matched, then taken out
    match_off: set[str] = field(default_factory=set)  # benefit keys ("never on statements")
    terms_count: dict[str, int] = field(default_factory=dict)  # benefit key -> match words
    # (benefit key, cycle start) -> set, rows or chat (left out: not used); and
    # the txn keys matched to it, by date (kept when a mark wins).
    cycle_sources: dict[tuple[str, str], str] = field(default_factory=dict)
    cycle_rows: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    chat_used: dict[tuple[str, str], int] = field(default_factory=dict)  # cents, when sent
    chat_used_on: dict[tuple[str, str], str] = field(default_factory=dict)  # when sent
    covered: set[tuple[str, str]] = field(default_factory=set)
    over: dict[tuple[str, str], int] = field(default_factory=dict)  # rows above the amount
    # Covered cycles on a benefit with words: the chat says used, no row matches.
    chat_only: list[tuple[str, str]] = field(default_factory=list)
    # Every row in cycle_rows, review and the no_cycle flags, by txn_key.
    credit_rows: dict[str, rm.CreditRow] = field(default_factory=dict)
    earlier: dict[str, str] = field(default_factory=dict)  # txn_key -> previous cycle start
    review: list[rm.ReviewItem] = field(default_factory=list)  # with best guesses
    notices: list[Notice] = field(default_factory=list)
    orphans: list[Orphan] = field(default_factory=list)
    flags: list[RowFlag] = field(default_factory=list)  # lost choices, rows with no cycle


@dataclass(frozen=True)
class Effective:
    rewards: Rewards  # what every reader shows
    chat: Rewards  # the chat's view, as sent
    meta: OverlayMeta


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@contextmanager
def read_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One read transaction around the block, or the caller's own transaction
    when there is one (a write, or an outer read)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def _rows(conn: sqlite3.Connection, table: str) -> tuple[dict[str, Any], ...]:
    cur = conn.execute(f"SELECT * FROM {table} ORDER BY {INPUT_TABLES[table]}")
    cols = [d[0] for d in cur.description]
    return tuple(dict(zip(cols, r, strict=True)) for r in cur.fetchall())


def load_inputs(conn: sqlite3.Connection) -> Inputs:
    """Every reward input, in one read transaction (or the caller's)."""
    with read_transaction(conn):
        return Inputs(*(_rows(conn, t) for t in INPUT_TABLES))


# --------------------------------------------------------------------------
# Merge and checks
# --------------------------------------------------------------------------


def balance_text(micro: int) -> str:
    """A stored balance as the model's string: whole points stay whole."""
    if micro % MICRO == 0:
        return str(micro // MICRO)
    return micro_to_str(micro)


def app_program(row: dict[str, Any]) -> dict[str, Any]:
    """A reward_app_programs row as a RewardProgram dict, with no balance."""
    item: dict[str, Any] = {
        "key": row["program_key"],
        "name": row["name"],
        "kind": row["kind"],
        "group": row["program_group"],
    }
    for name, col in (("owner", "owner_key"), ("expires_on", "expires_on"), ("note", "note")):
        if row[col] is not None:
            item[name] = row[col]
    if row["cpp_low_micro"] is not None:
        item["cpp_low"] = micro_to_str(row["cpp_low_micro"])
        item["cpp_high"] = micro_to_str(row["cpp_high_micro"])
    return item


def _merge_programs(data: dict[str, Any], inputs: Inputs, meta: OverlayMeta, today: str) -> None:
    chat = {p["key"]: p for p in data["programs"]}
    shown = dict(chat)
    for row in inputs.programs:
        key = row["program_key"]
        item = app_program(row)
        sent = chat.get(key)
        if sent is not None:  # the chat sends an app- key: the app wins
            same_unit = UNIT.get(sent["kind"]) == UNIT[row["kind"]]
            fields = () if same_unit else ("unit",)
            meta.notices.append(Notice("program_app_key", APP_KEY_TEXT, key, fields))
            if same_unit and sent.get("balance") is not None:  # still a candidate
                item |= {f: sent[f] for f in ("balance", "as_of", "source")}
        shown[key] = item
        meta.provenance[("program", key)] = "app"
        name = row["name"].casefold()
        same = [o for o in sorted(chat) if o != key and chat[o]["name"].casefold() == name]
        if row["dup_dismissed_key"] not in same:  # "Keep both" covers every one with the name
            for other in same:
                meta.notices.append(Notice("program_duplicate", DUPLICATE_TEXT, key, other=other))
    entries: dict[str, list[dict[str, Any]]] = {}
    for b in inputs.balances:  # ordered by program_key, as_of
        entries.setdefault(b["program_key"], []).append(b)
    for key, rows in entries.items():
        item = shown.get(key)
        unit = None if item is None else UNIT.get(item["kind"])  # None: a timeshare
        for other in sorted({b["unit"] for b in rows} - {unit}):
            reason = "dropped" if item is None else "timeshare" if unit is None else "unit"
            meta.orphans.append(Orphan("program", key, ("balances",), reason, other))
        due = [b for b in rows if b["unit"] == unit and b["as_of"] <= today]
        if not due:
            continue
        best = due[-1]
        chat_as_of = item.get("as_of")
        if chat_as_of is not None and chat_as_of > best["as_of"]:
            continue  # a newer chat balance wins; the entries stay in the history
        if chat_as_of == best["as_of"] and to_micro(item["balance"]) != best["balance_micro"]:
            meta.chat_balance[key] = to_micro(item["balance"])
        item |= {
            "balance": balance_text(best["balance_micro"]),
            "as_of": best["as_of"],
            "source": "manual",
        }
        meta.provenance[("balance", key)] = "app"
    data["programs"] = list(shown.values())


def benefit_origins(runs: list[readers.StateRun]) -> dict[str, tuple[str, str]]:
    """Each benefit key's (account_key, kind) from the newest run that sent it.
    Runs are newest first."""
    found: dict[str, tuple[str, str]] = {}
    for run in runs:
        if "rewards" not in run.sections:
            continue
        for b in json.loads(run.sections["rewards"]).get("benefits", []):
            found.setdefault(b["key"], (b["account_key"], b["kind"]))
    return found


def calendar_cycles(cadence: str, year: int) -> list[dict[str, str]]:
    """The calendar cycles of a year: monthly, quarterly, semi_annual or annual."""
    step = CALENDAR_STEPS[cadence]
    out = []
    for first in range(1, 13, step):
        last = first + step - 1
        end = date(year, last, calendar.monthrange(year, last)[1])
        out.append({"start": f"{year}-{first:02d}-01", "end": end.isoformat()})
    return out


@dataclass(frozen=True)
class Applied:
    """A chat item with an edit row's fields on top."""

    item: dict[str, Any]
    edited: tuple[str, ...] = ()  # the fields that count as edited
    changed: tuple[str, ...] = ()  # the chat moved under these since the edit
    fits: bool = True  # False: the edit was skipped (rule 9)


def edit_fits(kind: str, fields: Mapping[str, Any]) -> bool:
    """An edit fits a kind when it changes no fixed field, no field of another
    kind, and clears no field the kind needs."""
    if any(f in FIXED_FIELDS for f in fields):
        return False
    if any(f in ALL_KIND_FIELDS - BENEFIT_FIELDS[kind] for f in fields):
        return False
    return not any(f in fields and fields[f] is None for f in BENEFIT_REQUIRED[kind])


def _pin(item: dict[str, Any], amount_from: str | None, old: str | None) -> None:
    """Cycles that start before amount_from keep the old amount."""
    if amount_from is None or old is None or old == item.get("amount"):
        return
    for c in item.get("cycles") or []:
        if c["start"] < amount_from and "amount" not in c:
            c["amount"] = old


def apply_edit(
    sent: Mapping[str, Any],
    fields: Mapping[str, Any],
    base: Mapping[str, Any],
    amount_from: str | None,
) -> Applied:
    """The chat's item with the edit's fields on top (rules 2 to 4 and 9)."""
    if not edit_fits(sent["kind"], fields):
        return Applied(dict(sent), fits=False)
    item = copy.deepcopy(dict(sent))
    edited: list[str] = []
    changed: list[str] = []
    for f in sorted(fields):
        mine, now = fields[f], sent.get(f)
        if now != base.get(f):
            if now == mine:
                continue  # the chat caught up: no longer an edit
            changed.append(f)
        edited.append(f)
        if mine is None:
            item.pop(f, None)
        else:
            item[f] = copy.deepcopy(mine)
    if "amount" in edited:
        _pin(item, amount_from, sent.get("amount"))
    return Applied(item, tuple(edited), tuple(changed))


def benefit_ok(item: Mapping[str, Any], programs: set[str]) -> bool:
    """A merged benefit passes RewardBenefit and what check() asks of one item:
    a shown program, and no cycle start or category key twice."""
    try:
        b = RewardBenefit.model_validate(_sort_inner("benefits", copy.deepcopy(dict(item))))
    except ValidationError, KeyError, TypeError:
        return False
    if b.program_key is not None and b.program_key not in programs:
        return False
    starts = [c.start for c in b.cycles or []]
    cats = list(b.category_keys or [])
    return len(set(starts)) == len(starts) and len(set(cats)) == len(cats)


def fitted(
    sent: Mapping[str, Any],
    fields: Mapping[str, Any],
    base: Mapping[str, Any],
    amount_from: str | None,
    programs: set[str],
) -> Applied:
    """apply_edit, and the merged item checked on its own (rule 9). An edit
    whose item fails is skipped: the chat's item is shown."""
    done = apply_edit(sent, fields, base, amount_from)
    if done.fits and not benefit_ok(done.item, programs):
        return Applied(dict(sent), fits=False)
    return done


def app_benefit(
    key: str,
    account_key: str,
    fields: Mapping[str, Any],
    amount_from: str | None,
    amount_before: str | None,
    year: int,
) -> dict[str, Any]:
    """An app row's body as a RewardBenefit dict (rule 7). A body with its own
    start (APP_START) leaves out the cycles that end before it, calendar or
    stored, and the cycle that holds it starts on it. A start in a later year
    gives that year's calendar cycles, so the credit shows as upcoming and
    counts nothing before. Fees read the start from meta (every kind)."""
    item = copy.deepcopy(dict(fields)) | {"key": key, "account_key": account_key}
    start = item.pop(APP_START, None)
    if item.get("cadence") in CALENDAR_STEPS:
        first = year if start is None else max(year, int(start[:4]))
        item["cycles"] = calendar_cycles(item["cadence"], first)
    if start is not None and item.get("cycles") is not None:
        item["cycles"] = [
            c | {"start": max(c["start"], start)} for c in item["cycles"] if c["end"] >= start
        ]
    _pin(item, amount_from, amount_before)
    return item


def alike(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Rule 11: the same card and kind, and the same name (casefolded) or the
    same cadence and amount."""
    if (a["account_key"], a["kind"]) != (b["account_key"], b["kind"]):
        return False
    if a["name"].casefold() == b["name"].casefold():
        return True
    terms = (a.get("cadence"), a.get("amount"))
    return None not in terms and terms == (b.get("cadence"), b.get("amount"))


def _merge_benefits(data: dict[str, Any], inputs: Inputs, meta: OverlayMeta, today: date) -> None:
    chat = {b["key"]: b for b in data["benefits"]}
    programs = {p["key"] for p in data["programs"]}  # merged first
    shown = dict(chat)
    hidden: set[str] = set()
    broken: set[str] = set()  # app items that no longer pass: left out, with a notice
    apps: list[dict[str, Any]] = []
    for row in inputs.edits:
        key = row["benefit_key"]
        fields = json.loads(row["fields_json"])
        if row["origin"] == "app":
            item = app_benefit(
                key,
                row["account_key"],
                fields,
                row["amount_from"],
                row["amount_before"],
                today.year,
            )
            if not benefit_ok(item, programs):  # the chat's item, if it sends the key, stays
                meta.notices.append(Notice("benefit_app_no_fit", APP_NO_FIT_TEXT, key))
                broken.add(key)
                continue
            shown[key] = item
            meta.provenance[("benefit", key)] = "app"
            if row["amount_before"] is not None and row["amount_from"] is not None:
                meta.amount_from[key] = row["amount_from"]
            if fields.get(APP_START) is not None:
                meta.starts_on[key] = fields[APP_START]
            if key in chat:  # the chat sends an app- key: the app wins
                meta.notices.append(Notice("benefit_app_key", BENEFIT_APP_KEY_TEXT, key))
            apps.append(row)
        elif key in chat:
            done = fitted(
                chat[key], fields, json.loads(row["base_json"]), row["amount_from"], programs
            )
            shown[key] = done.item
            if not done.fits:
                meta.notices.append(
                    Notice("benefit_no_fit", NO_FIT_TEXT, key, tuple(sorted(fields)))
                )
            for f in done.changed:
                text = CHANGED_TEXT.format(label=FIELD_LABELS.get(f, f))
                meta.notices.append(Notice("benefit_chat_changed", text, key, (f,)))
            if done.edited:
                meta.edited_fields[key] = done.edited
                meta.provenance[("benefit", key)] = "edited"
            if "amount" in done.edited and row["amount_from"] is not None:
                meta.amount_from[key] = row["amount_from"]
        else:
            continue  # the chat dropped the key: an orphan, below
        if row["match_off"]:
            meta.match_off.add(key)
        if row["hidden"]:
            hidden.add(key)
    # Duplicates: an app item against the chat's shown items. A chat key the app
    # took over (rule 10) and hidden items are left out.
    others = sorted(set(chat) - {r["benefit_key"] for r in apps} - hidden)
    for row in apps:
        key = row["benefit_key"]
        if key in hidden:
            continue
        for other in others:
            if other != row["dup_dismissed_key"] and alike(shown[key], shown[other]):
                meta.notices.append(
                    Notice("benefit_duplicate", BENEFIT_DUPLICATE_TEXT, key, other=other)
                )
    held: dict[str, list[str]] = {}
    found = [
        *((r["benefit_key"], "edits") for r in inputs.edits if r["origin"] == "chat" and _holds(r)),
        *((t["benefit_key"], "terms") for t in inputs.terms),
        *((d["benefit_key"], "choices") for d in inputs.decisions if d["benefit_key"]),
        *((m["benefit_key"], "marks") for m in inputs.marks),
    ]
    for key, what in found:  # a broken app item's notice covers its inputs
        if key not in shown and key not in broken and what not in held.setdefault(key, []):
            held[key].append(what)
    meta.orphans += [Orphan("benefit", k, tuple(v)) for k, v in sorted(held.items())]
    for t in inputs.terms:
        if t["benefit_key"] in shown:
            meta.terms_count[t["benefit_key"]] = meta.terms_count.get(t["benefit_key"], 0) + 1
    # Hidden benefits stay in for matching (their words still claim rows), and
    # leave `benefits` after it (_take_out_hidden).
    meta.hidden_keys = hidden
    data["benefits"] = list(shown.values())


def _holds(row: Mapping[str, Any]) -> bool:
    """An edit row holds an edit (not only a hide)."""
    return bool(json.loads(row["fields_json"])) or bool(row["match_off"])


def _merge(chat: Rewards, inputs: Inputs, meta: OverlayMeta) -> dict[str, Any]:
    """The chat's rewards as a plain dict with the inputs laid on top."""
    data = chat.model_dump(mode="json", exclude_none=True)
    today = config.household_today()
    _merge_programs(data, inputs, meta, today.isoformat())
    _merge_benefits(data, inputs, meta, today)
    return data


# --------------------------------------------------------------------------
# Credits and cycle use (plan 3.3 and 4)
# --------------------------------------------------------------------------


def _terms(inputs: Inputs) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for t in inputs.terms:  # ordered by benefit_key, upper(term)
        out.setdefault(t["benefit_key"], []).append(t["term"])
    return {k: tuple(v) for k, v in out.items()}


def cycle_source(
    at: tuple[str, str],
    rows: Sequence[rm.CreditRow],
    found: rm.Match,
    meta: OverlayMeta,
    span: tuple[str, str] | None,
) -> str | None:
    """Where a cycle with no mark takes its use from (plan 3.3): "rows",
    "chat", or None (not used, or waiting for rows). The page and the word
    preview both read it here. meta must hold the cycle's chat_used,
    chat_used_on and covered. A decided cycle never takes the chat's used
    while it may count a moved row: covered, it reads 0 from rows; not covered
    yet, it waits for rows, unless the chat's use is dated after the card's
    statements end (span), where no moved row can be."""
    if rows:
        return "rows"
    if at in found.decided:
        if at in meta.covered:
            return "rows"
        on = meta.chat_used_on.get(at)
        if on is None or span is None or on <= span[1]:
            return None
    return "chat" if at in meta.chat_used else None


def _fill_cycles(
    data: dict[str, Any],
    benefits: list[rm.Benefit],
    terms: Mapping[str, tuple[str, ...]],
    marks: tuple[dict[str, Any], ...],
    found: rm.Match,
    coverage: Mapping[str, tuple[str, str]],
    meta: OverlayMeta,
) -> None:
    """Each credit cycle's used and used_on from one source (plan 3.3), in the
    merged dict. A cycle with no mark and no rows keeps the chat's values,
    unless a choice or a spill moved its row out (decided): see
    cycle_source."""
    cut = {b.key: b for b in benefits}
    marked = {(m["benefit_key"], m["cycle_start"]): m for m in marks}
    for item in data["benefits"]:
        b = cut.get(item["key"])
        if b is None:
            continue
        cycles = {c.start: c for c in b.cycles}  # cut at ends_on
        span = coverage.get(b.account_key)
        worded = b.matches and bool(terms.get(b.key))
        for c in item.get("cycles") or []:
            at = (b.key, c["start"])
            own = cycles.get(c["start"])
            if own is not None and rm.is_covered(span, own):
                meta.covered.add(at)
            chat = to_cents(c["used"]) if c.get("used") is not None else None
            if chat is not None:
                meta.chat_used[at] = chat
                if c.get("used_on") is not None:
                    meta.chat_used_on[at] = c["used_on"]
            rows = found.cycles.get(at, ())
            if rows:
                meta.cycle_rows[at] = tuple(r.txn_key for r in rows)
            mark = marked.get(at)
            source = "set" if mark is not None else cycle_source(at, rows, found, meta, span)
            if mark is not None:
                used, used_on = mark["used_cents"], mark["used_on"]
            elif source == "rows":
                used, used_on = sum(r.cents for r in rows), rows[-1].txn_date if rows else None
                if own is not None and used > own.cents:
                    meta.over[at] = used - own.cents
            else:
                if source == "chat":
                    meta.cycle_sources[at] = "chat"
                    if chat is not None and chat > 0 and worded and at in meta.covered:
                        meta.chat_only.append(at)
                elif at in found.decided:  # waiting for rows, never the chat's used
                    c.pop("used", None)
                    c.pop("used_on", None)
                continue
            meta.cycle_sources[at] = source
            c["used"] = cents_to_str(used)
            if used_on is None:
                c.pop("used_on", None)
            else:
                c["used_on"] = used_on


def _match(
    conn: sqlite3.Connection, data: dict[str, Any], inputs: Inputs, meta: OverlayMeta, today: date
) -> None:
    """Match statement credits to the credits (hidden ones included), fill cycle
    use, and list the rows to review with a best guess each. The rows are read
    in the caller's read transaction."""
    items = [RewardBenefit.model_validate(b) for b in data["benefits"] if b["kind"] == "credit"]
    if not items:
        return
    benefits = rm.benefits_from(items, meta.match_off)
    terms = _terms(inputs)
    rows = rm.load_rows(conn, benefits, inputs.decisions, today)
    found = rm.match(benefits, terms, inputs.decisions, rows, today)
    _fill_cycles(data, benefits, terms, inputs.marks, found, rows.coverage, meta)
    # Room for the best guess: a mark's or the rows' use. The chat's used is left
    # out: it usually counts the very row that waits for review.
    used = {
        (item["key"], c["start"]): to_cents(c["used"])
        for item in data["benefits"]
        for c in item.get("cycles") or []
        if meta.cycle_sources.get((item["key"], c["start"])) in ("set", "rows")
    }
    by_key = {r.txn_key: r for r in rows.credits}
    shown = [b for b in benefits if b.key not in meta.hidden_keys]
    meta.review = []
    for x in found.review:
        # A tie names only shown credits. With none left, the hidden ones claim
        # the row (it never flags). With one left, it is a plain row to review.
        options = tuple(k for k in x.options if k not in meta.hidden_keys)
        if x.options and not options:
            continue
        guess = rm.best_guess(by_key[x.txn_key], shown, used, options)
        meta.review.append(replace(x, options=options if len(options) > 1 else (), guess=guess))
    # A link to a key the page does not have shows elsewhere: as an orphan, or
    # in a broken app item's notice.
    sent = {item["key"] for item in data["benefits"]}
    meta.flags = [f for f in found.flags if f.kind != "off" or f.benefit_key in sent]
    meta.earlier = dict(found.earlier)
    keys = {k for ks in meta.cycle_rows.values() for k in ks}
    keys |= {x.txn_key for x in meta.review} | {f.txn_key for f in meta.flags}
    meta.credit_rows = {k: by_key[k] for k in sorted(keys) if k in by_key}


def _take_out_hidden(data: dict[str, Any], meta: OverlayMeta) -> None:
    meta.hidden = [
        RewardBenefit.model_validate(b) for b in data["benefits"] if b["key"] in meta.hidden_keys
    ]
    data["benefits"] = [b for b in data["benefits"] if b["key"] not in meta.hidden_keys]


def _sort_inner(name: str, item: dict[str, Any]) -> dict[str, Any]:
    for inner, by in INNER.get(name, {}).items():
        if item.get(inner) is not None:
            item[inner] = sorted(item[inner], key=lambda v, b=by: v[b] if b else v)
    return item


def _sort(data: dict[str, Any]) -> dict[str, Any]:
    """Every list sorted by key, and the inner lists by their own fields."""
    for name in LISTS:
        data[name] = [
            _sort_inner(name, item) for item in sorted(data.get(name, []), key=lambda x: x["key"])
        ]
    return data


def _unique(values: list[Any]) -> None:
    if len(set(values)) != len(values):
        raise OverlayError("key-duplicate")


def check(r: Rewards, hidden: list[RewardBenefit] | tuple[()] = ()) -> None:
    """What snapshot_issues checks on the chat's rewards and model_validate does
    not: unique keys (inner lists too) and program references. Hidden benefits
    count too."""
    for name in LISTS:
        _unique([x.key for x in getattr(r, name)])
    _unique([b.key for b in [*r.benefits, *hidden]])
    for p in r.programs:
        _unique(list(p.earned_on or []))
        _unique([a.year for a in p.allocations or []])
    programs = {p.key for p in r.programs}
    for b in [*r.benefits, *hidden]:
        _unique([c.start for c in b.cycles or []])
        _unique(list(b.category_keys or []))
        if b.program_key is not None and b.program_key not in programs:
            raise OverlayError("reward-program-unknown")
    if any(x.program_key not in programs for x in r.redemptions):
        raise OverlayError("reward-program-unknown")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def effective_rewards(conn: sqlite3.Connection, runs: list[readers.StateRun]) -> Effective | None:
    """The chat's rewards with the app inputs on top, or None before the chat's
    first 1.3 run."""
    chat = readers.rewards(runs)
    if chat is None:
        return None
    meta = OverlayMeta()
    with read_transaction(conn):  # the inputs and the statement rows: one snapshot
        inputs = load_inputs(conn)
        try:
            data = _sort(_merge(chat, inputs, meta))
            _match(conn, data, inputs, meta, config.household_today())
            _take_out_hidden(data, meta)
            merged = Rewards.model_validate(data)
            check(merged, meta.hidden)
        # ValueError and OverflowError: bad numbers or dates in the data. The page
        # shows the chat's view, never a 500.
        except (
            ValidationError,
            OverlayError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
        ) as exc:
            log.warning("rewards overlay failed", extra={"error": type(exc).__name__})
            failed = OverlayMeta(notices=[Notice("overlay_failed", FAILED_TEXT)])
            return Effective(chat, chat, failed)
    meta.notices.sort(key=lambda n: n.key or "")  # key order; a key's own notes keep theirs
    return Effective(merged, chat, meta)
