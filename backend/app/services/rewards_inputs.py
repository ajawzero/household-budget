"""Writes for the rewards inputs (docs/plans/rewards-inputs.md sections 3 and 6):
balance entries, app programs and benefit edits. The overlay
(`rewards_overlay`) lays them on the chat's rewards when a page is read. The
chat's data is never changed.

- Every write runs inside the caller's `write_transaction`, and refuses to run
  outside one. Before the chat's first 1.3 run every write is a 409.
- Balances (any signed-in user): one per program per day, on or before
  household today. Points and miles are whole numbers, cash back has at most 2
  decimal places. A program the page does not show is a 404. Timeshares take
  no entries (they use allocations). A second entry for the same day replaces
  the first. Each entry stores the unit of the program's kind when it was
  typed: whole (points and miles) or cash (cash back).
- Stranded entries are kept but not shown: every entry on a key the page does
  not show or on a timeshare, and the entries in the other unit on a shown
  program (the chat changed its unit). They move or are deleted from "Needs a
  look".
- App programs (admin): key `app-<slug>`. Only app programs change here. A
  kind change to the other unit relabels the entries (a kind picked by
  mistake); cash to whole needs whole numbers. Delete takes the program's
  balances, unless the chat sends that key too ("Use the chat's"): then the
  balances stay with the chat's program, and the ones in another unit are
  stranded. "Delete balances" names a unit and deletes the key's stranded
  entries in that unit only. When none are left in it (an import changed the
  page since it loaded), it is a 409 and nothing is deleted.
- Move: an app program's balances, or a key's stranded entries, go to another
  shown program in their unit. Points and miles go only to points or miles,
  cash back only to cash back. An app program moved away is deleted ("Use the
  chat's" on a duplicate). An app program with no balances moves to any
  program. Benefits that name a key that is gone after the move (an app free
  night, a program edit) name the new program. Delete is a 422 while a
  benefit names the program, unless the chat sends that key too.
- Benefits (admin, plan 3.2): one reward_benefit_edits row per key. A chat
  benefit's row holds the changed fields and the chat's value of each when it
  was saved (base). Setting the chat's own value drops the field. An app
  benefit's row holds its whole body, key `app-<slug>`. key, account_key,
  kind, cadence and cycles never change. Every change is checked as the
  overlay would show it: the item must pass RewardBenefit, name a shown
  program and known categories. A chat row with nothing left is deleted.
- An amount change starts at amount_from, by default the start of the cycle
  that holds today. Earlier cycles keep the old amount: the chat's, or an app
  item's amount_before. One start is kept per benefit, so a second change keeps
  the first one's start or moves it earlier. A later start is a 422: the
  cycles between would go back to the amount from before any change. An
  amount set back to the one before any change takes no start.
- An edit the overlay skips (rule 9: the chat changed the kind, or the merged
  item no longer passes) stays as it is. Hide, show again and match_off still
  work. Any field change is a 422 until the edit is deleted, unless the change
  makes it fit again.
- A field the chat caught up on (the chat now sends the owner's value) is dropped
  at the next write on that row, as the page already shows it as the chat's.
- Move (orphans and duplicates): edits, words, choices and marks go to
  another current benefit on the same card and kind in one write. A clash on
  a unique key is a 409. Nothing is dropped.
- Match words (admin, plan 4.10): 0 to 10 per credit, shown or hidden. They
  replace the saved ones. Each is 3 to 40 printable ASCII characters, never
  one that fits inside generic words (CREDIT, EDIT, T CREDIT), once per
  benefit (case ignored). Suggested words come from the name, seen words from
  the card's credit rows next to the chat's used days. A preview (nothing
  saved) runs the matching with and without the word.
- Cycle marks (any signed-in user): "Mark used" or "Mark not used" (0) on a
  cycle the page shows on a credit, hidden ones too. A used day is not in the
  future and goes only with a used amount. Clear deletes the mark.
- Credit choices (any signed-in user, plan 4.4): the row must be a live card
  credit (a candidate for matching). A link names a shown credit on the
  row's card that matches statements, and optionally pins one of its cycles.
  The guard comes from the live row, with the run that last wrote it. A
  choice that re-found its row after a statement correction is replaced by a
  write on the row (or cleared by a delete on it), so it never shows as lost
  next to the new one.
- Typed text (names, notes, every text field, match words) and the key made
  from a name pass detect_sensitive with the rewards section rules and the
  private lists. A match is a 422 that never holds the text.
- Each write adds one `events` row per entity, kind `rewards_edit`, with the
  action, field names and counts only. Never values.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError

from app.importer.models import (
    KEY_MAX,
    RewardBenefit,
    RewardProgram,
    Rewards,
    micro_to_str,
    to_cents,
    to_micro,
)
from app.importer.sanitize import PrivateRules, scan_text
from app.services import rewards
from app.services import rewards_match as rm
from app.services import rewards_overlay as ov
from app.services.overrides import EditError, check_text, require_category
from app.services.rewards_overlay import UNIT, read_transaction
from app.state import readers

EVENT_KIND = "rewards_edit"
SECTION = "rewards"
MICRO = 1_000_000
APP_PREFIX = "app-"
SUFFIX_MAX = 99  # app-name-2 to app-name-99 on a clash
BALANCE_RE = re.compile(r"^[0-9]+(?:\.[0-9]{1,2})?$")
WHOLE_KINDS = ("points", "miles")
PROGRAM_FIELDS = (
    "name",
    "kind",
    "group",
    "owner_key",
    "cpp_low",
    "cpp_high",
    "expires_on",
    "note",
)

NO_REWARDS = "Rewards start with the chat's first export."
NO_PROGRAM = "No program with that key."
NO_BALANCE = "No balance with that id."
TIMESHARE = "Timeshares use their yearly points, not balances."
FUTURE = "The date cannot be in the future."
BAD_BALANCE = "Enter the balance as a number."
WHOLE = "Points and miles are whole numbers."
CENTS = "Cash back has at most 2 decimal places."
ONE_A_DAY = "One balance per program per day."
APP_ONLY = "Only programs added in the app can be changed here."
UNKNOWN_PERSON = "Unknown person."
VALUE_RANGE = "Enter both ends of the value range, low first."
FACE_VALUE = "Cash back counts at face value. Leave the value range out."
NOT_FITTING = "Some balances do not fit that kind. Points and miles are whole numbers."
OTHER_UNIT = (
    "Those balances are in another unit. Points and miles move only to points or miles, "
    "and cash back only to cash back."
)
SAME_PROGRAM = "Pick another program."
SAME_DAY = "Both programs have a balance for the same day. Delete one first."
BALANCES_CHANGED = "Those balances changed since the page loaded. Check the page again."
NOT_DUPLICATE = "These programs do not have the same name."
TOO_MANY = "Too many programs have that name. Pick another name."
NOTHING = "Nothing to change."
NOT_CLEARABLE = "The name, kind and group cannot be empty."
NO_BENEFIT = "No benefit with that key."
NOT_A_CARD = "Pick a credit or store card."
NEEDS_CYCLES = "Multi-year and one-time credits need their cycles."
NO_CYCLES = "Only multi-year and one-time credits list their cycles."
CYCLE_STARTS = "Each cycle needs its own start date."
FIXED = "The key, card, kind, cadence and cycles cannot change. End this benefit and add a new one."
BAD_FIT = "That does not fit this kind of benefit."
UNKNOWN_PROGRAM = "Unknown program."
SET_AND_RESET = "A field cannot be changed and reset at once."
APP_EDIT = "This benefit was added in the app. Change it or delete it instead."
AMOUNT_FROM = "Change the amount to say when the new amount starts."
AMOUNT_LATER = (
    "The amount already changes from an earlier day. Start this change on that day or earlier."
)
NO_FIT_FIRST = "Your edit no longer fits this benefit. Delete the edit first."
MOVE_NO_FIT = "Your edits do not fit that benefit. Pick another, or delete them."
PROGRAM_IN_USE = "A benefit uses this program. Delete that benefit or its edit first."
NOT_EDITED = "That field has no edit."
CHAT_DELETE = "Only benefits added in the app can be deleted. End or hide it instead."
NOT_SHOWN = "The chat no longer sends this benefit. Move or delete it."
CHAT_BENEFIT = "Only benefits added in the app, or ones the chat no longer sends, can move."
SAME_BENEFIT = "Pick another benefit."
OTHER_BENEFIT = "Pick a benefit of the same kind on the same card."
UNKNOWN_BENEFIT = "No export says what this benefit was. Delete it instead."
EDIT_CLASH = "That benefit has its own edits. Delete one first."
TERM_CLASH = "Both benefits have the same match word. Delete one first."
MARK_CLASH = "Both benefits have a mark on the same cycle. Delete one first."
NOT_ALIKE = "These benefits are not alike."
MATCH_OFF_CREDITS = "Only credits match statement rows."
TERMS_MAX = 10
SEEN_DAYS = 3  # a credit row this many days from the chat's used day is "seen"
TERM_TWICE = "Each match word can be saved once."
TOO_MANY_TERMS = "A benefit takes at most 10 match words."
NOT_A_CREDIT = "Only credits have cycles."
NO_CYCLE = "No cycle of this credit starts on that day."
UNUSED_DAY = "Leave the date out when the credit was not used."
NO_MARK = "No mark on that cycle."
NO_ROW = "No statement row with that key."
NOT_CARD_CREDIT = "This row is not a credit on a card."
PICK_CREDIT = "Pick a credit on this row's card."
NEVER_ON_STATEMENTS = "That credit never shows on statements. Turn matching on first."
PICK_CYCLE = "Pick a cycle of that credit."
NO_CHOICE = "No choice on that row."
BENEFIT_TEXT = {
    "name": "name",
    "note": "notes",
    "how_to_use": "how_to_use",
    "earn_before": "earn_before",
    "earn_after": "earn_after",
    "status_tier": "status_tier",
}


def _in_write(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("rewards inputs are written inside write_transaction")


def _event(
    conn: sqlite3.Connection, user_id: int | None, now: str, entity: str, key: str, detail: dict
) -> None:
    conn.execute(
        """INSERT INTO events (at, user_id, kind, entity, entity_key, detail_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (now, user_id, EVENT_KIND, entity, key, json.dumps(detail, sort_keys=True)),
    )


# --------------------------------------------------------------------------
# What the page shows
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Programs:
    """The chat's programs with the app programs on top (the app wins a key)."""

    chat: dict[str, RewardProgram]
    app: dict[str, sqlite3.Row]
    runs: list[readers.StateRun]

    def kind(self, key: str) -> str | None:
        if key in self.app:
            return self.app[key]["kind"]
        p = self.chat.get(key)
        return p.kind if p else None

    def unit(self, key: str) -> str | None:
        """The unit the key's balances count in now. None for a timeshare or a
        key the page does not show."""
        return UNIT.get(self.kind(key) or "")

    def keys(self) -> set[str]:
        return set(self.chat) | set(self.app)


def _chat(conn: sqlite3.Connection) -> tuple[list[readers.StateRun], Rewards]:
    """The runs and the chat's rewards, or a 409 before the first 1.3 run."""
    runs = readers.monthly_runs(conn)
    chat = readers.rewards(runs)
    if chat is None:
        raise EditError(409, NO_REWARDS)
    return runs, chat


def _programs(
    conn: sqlite3.Connection, found: tuple[list[readers.StateRun], Rewards] | None = None
) -> Programs:
    """The programs as the page shows them, or a 409 before the first 1.3 run."""
    runs, chat = found or _chat(conn)
    app = {
        r["program_key"]: r
        for r in conn.execute("SELECT * FROM reward_app_programs ORDER BY program_key")
    }
    return Programs({p.key: p for p in chat.programs}, app, runs)


def _balance_keys(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT DISTINCT program_key FROM reward_balances")}


def _entries(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, as_of, balance_micro, unit FROM reward_balances WHERE program_key = ? "
        "ORDER BY as_of",
        (key,),
    ).fetchall()


def _stranded(conn: sqlite3.Connection, progs: Programs, key: str) -> list[sqlite3.Row]:
    """Entries kept but not shown: every entry on a key the page does not show
    or on a timeshare, and on a shown program the ones in the other unit."""
    unit = progs.unit(key)
    return [r for r in _entries(conn, key) if r["unit"] != unit]


# --------------------------------------------------------------------------
# Balances
# --------------------------------------------------------------------------


def _fits(kind: str, micro: int) -> bool:
    if kind in WHOLE_KINDS:
        return micro % MICRO == 0
    return micro % (MICRO // 100) == 0


def balance_micro(kind: str, text: str) -> int:
    """A typed balance in micro-units, checked against the program's kind."""
    if BALANCE_RE.fullmatch(text) is None:
        raise EditError(422, BAD_BALANCE)
    micro = to_micro(text)
    if kind in WHOLE_KINDS and "." in text:
        raise EditError(422, WHOLE)
    if not _fits(kind, micro):
        raise EditError(422, CENTS)
    return micro


def add_balances(
    conn: sqlite3.Connection,
    entries: Iterable[tuple[str, str, date | None]],
    *,
    today: date,
    user_id: int | None,
    now: str,
) -> list[str]:
    """Add or replace one balance per (program, day), in the unit of the
    program's kind now. Entries are (program_key, balance, as_of or None for
    today). Returns the keys in order."""
    _in_write(conn)
    progs = _programs(conn)
    rows: list[tuple[str, str, int, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, text, as_of in entries:
        kind = progs.kind(key)
        if kind is None:
            raise EditError(404, NO_PROGRAM)
        if kind == "timeshare":
            raise EditError(422, TIMESHARE)
        day = as_of or today
        if day > today:
            raise EditError(422, FUTURE)
        if (key, day.isoformat()) in seen:
            raise EditError(422, ONE_A_DAY)
        seen.add((key, day.isoformat()))
        rows.append((key, day.isoformat(), balance_micro(kind, text), UNIT[kind]))
    for key, day, micro, unit in rows:
        old = conn.execute(
            "SELECT 1 FROM reward_balances WHERE program_key = ? AND as_of = ?", (key, day)
        ).fetchone()
        conn.execute(
            """INSERT INTO reward_balances (program_key, as_of, balance_micro, unit, updated_by,
                   updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (program_key, as_of) DO UPDATE SET
                 balance_micro = excluded.balance_micro, unit = excluded.unit,
                 updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
            (key, day, micro, unit, user_id, now),
        )
        _event(conn, user_id, now, "balance", key, {"action": "replace" if old else "add"})
    return list(dict.fromkeys(k for k, _, _, _ in rows))


def delete_balance(
    conn: sqlite3.Connection, balance_id: int, *, user_id: int | None, now: str
) -> str:
    """Delete one entry. Returns its program key."""
    _in_write(conn)
    _programs(conn)
    row = conn.execute(
        "SELECT program_key FROM reward_balances WHERE id = ?", (balance_id,)
    ).fetchone()
    if row is None:
        raise EditError(404, NO_BALANCE)
    conn.execute("DELETE FROM reward_balances WHERE id = ?", (balance_id,))
    _event(conn, user_id, now, "balance", row["program_key"], {"action": "delete"})
    return row["program_key"]


def balance_history(conn: sqlite3.Connection, key: str) -> dict | None:
    """The app's entries for a key, newest first, the chat's current balance and
    the program as the page shows it. None when nothing is known of the key.
    An entry in another unit than the program's now is never shown. Nor is the
    chat's balance when an app program wins the key and the chat counts it in
    another unit."""
    with read_transaction(conn):
        chat = readers.rewards(readers.monthly_runs(conn))
        rows = conn.execute(
            """SELECT id, as_of, balance_micro, unit FROM reward_balances WHERE program_key = ?
               ORDER BY as_of DESC""",
            (key,),
        ).fetchall()
        program = rewards.program_views(conn, {key}).get(key)
    sent = next((p for p in chat.programs if p.key == key), None) if chat else None
    if program is None and not rows and sent is None:
        return None
    app_wins = program is not None and program["source"] == "app"
    unit = UNIT.get(program["kind"]) if program is not None else None
    chat_counts = (
        sent is not None
        and sent.balance is not None
        and program is not None
        and UNIT.get(sent.kind) == unit
    )
    return {
        "program_key": key,
        "program": program,
        "entries": [
            {
                "id": r["id"],
                "as_of": r["as_of"],
                "balance_micro": r["balance_micro"],
                "unit": r["unit"],
                "shown": app_wins and r["as_of"] == program["as_of"] and r["unit"] == unit,
            }
            for r in rows
        ],
        "chat": {
            "balance_micro": to_micro(sent.balance),
            "as_of": sent.as_of,
            "source": sent.source,
        }
        if chat_counts
        else None,
    }


# --------------------------------------------------------------------------
# App programs
# --------------------------------------------------------------------------


def slug(name: str) -> str:
    """Lowercase ASCII words joined by hyphens, digits dropped ("$300 travel
    credit" -> "travel-credit"). The key rule bans runs of 3 digits."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = (re.sub(r"[0-9]", "", w) for w in re.split(r"[^a-z0-9]+", ascii_name.lower()))
    return "-".join(w for w in words if w)


def app_key(name: str, taken: set[str], fallback: str) -> str:
    """`app-<slug>`, with -2, -3 on a clash, cut to fit the 40-character key."""
    base = slug(name) or fallback
    for n in range(1, SUFFIX_MAX + 1):
        suffix = "" if n == 1 else f"-{n}"
        key = APP_PREFIX + base[: KEY_MAX - len(APP_PREFIX) - len(suffix)].rstrip("-") + suffix
        if key not in taken:
            return key
    raise EditError(422, TOO_MANY)


def _iso(value: date | str | None) -> str | None:
    return value.isoformat() if isinstance(value, date) else value


def _row_values(
    conn: sqlite3.Connection, v: Mapping[str, Any], private: PrivateRules | None
) -> dict[str, Any]:
    """Checked values for a reward_app_programs row."""
    check_text("name", [v["name"]], private, SECTION)
    if v["note"] is not None:
        check_text("notes", [v["note"]], private, SECTION)
    owner = v["owner_key"]
    if owner is not None:
        found = conn.execute("SELECT 1 FROM people WHERE person_key = ?", (owner,)).fetchone()
        if found is None:
            raise EditError(422, UNKNOWN_PERSON)
    low, high = v["cpp_low"], v["cpp_high"]
    if (low is None) != (high is None):
        raise EditError(422, VALUE_RANGE)
    if low is not None and v["kind"] == "cash_back":
        raise EditError(422, FACE_VALUE)
    if low is not None and to_micro(low) > to_micro(high):
        raise EditError(422, VALUE_RANGE)
    return {
        "name": v["name"],
        "kind": v["kind"],
        "program_group": v["group"],
        "owner_key": owner,
        "cpp_low_micro": None if low is None else to_micro(low),
        "cpp_high_micro": None if high is None else to_micro(high),
        "expires_on": _iso(v["expires_on"]),
        "note": v["note"],
    }


def _balances_fit(conn: sqlite3.Connection, key: str, unit: str, kind: str) -> bool:
    """The key's entries in `unit` fit `kind` (cash to whole needs whole numbers)."""
    return all(
        _fits(kind, r[0])
        for r in conn.execute(
            "SELECT balance_micro FROM reward_balances WHERE program_key = ? AND unit = ?",
            (key, unit),
        )
    )


def add_program(
    conn: sqlite3.Connection,
    values: Mapping[str, Any],
    *,
    private: PrivateRules | None,
    user_id: int | None,
    now: str,
) -> str:
    """Add an app program. Returns its new key."""
    _in_write(conn)
    progs = _programs(conn)
    v = {f: values.get(f) for f in PROGRAM_FIELDS} | {"group": values.get("group") or "secondary"}
    row = _row_values(conn, v, private)
    key = app_key(row["name"], progs.keys() | _balance_keys(conn), "program")
    # The slug folds accents and drops digits, so it can spell what the name hid.
    check_text("name", [key], private, SECTION)
    cols = ["program_key", *row, "updated_by", "updated_at"]
    conn.execute(
        f"INSERT INTO reward_app_programs ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})",
        (key, *row.values(), user_id, now),
    )
    fields = sorted(f for f in PROGRAM_FIELDS if v[f] is not None)
    _event(conn, user_id, now, "program", key, {"action": "add", "fields": fields})
    return key


def _not_app(progs: Programs, key: str) -> EditError:
    """A chat program is a 422 (only app programs change here), else a 404."""
    return EditError(422, APP_ONLY) if key in progs.chat else EditError(404, NO_PROGRAM)


def _app_row(progs: Programs, key: str) -> sqlite3.Row:
    row = progs.app.get(key)
    if row is None:
        raise _not_app(progs, key)
    return row


def update_program(
    conn: sqlite3.Connection,
    key: str,
    changes: Mapping[str, Any],
    *,
    private: PrivateRules | None,
    user_id: int | None,
    now: str,
) -> None:
    """Change an app program. A field left out stays. The key never changes. A
    kind change to the other unit relabels the program's entries in the old
    unit: the admin fixes a kind picked by mistake, so they were typed in the
    new one."""
    _in_write(conn)
    progs = _programs(conn)  # the 409 before the first 1.3 run comes first
    if not changes or set(changes) - set(PROGRAM_FIELDS):
        raise EditError(422, NOTHING)
    if any(f in changes and changes[f] is None for f in ("name", "kind", "group")):
        raise EditError(422, NOT_CLEARABLE)
    row = _app_row(progs, key)
    v: dict[str, Any] = {
        "name": row["name"],
        "kind": row["kind"],
        "group": row["program_group"],
        "owner_key": row["owner_key"],
        "cpp_low": None if row["cpp_low_micro"] is None else micro_to_str(row["cpp_low_micro"]),
        "cpp_high": None if row["cpp_high_micro"] is None else micro_to_str(row["cpp_high_micro"]),
        "expires_on": row["expires_on"],
        "note": row["note"],
    } | dict(changes)
    values = _row_values(conn, v, private)
    old, new = UNIT[row["kind"]], UNIT[values["kind"]]
    if old != new and not _balances_fit(conn, key, old, values["kind"]):
        raise EditError(422, NOT_FITTING)
    conn.execute(
        f"""UPDATE reward_app_programs SET {", ".join(f"{c} = ?" for c in values)},
               updated_by = ?, updated_at = ?
            WHERE program_key = ?""",
        (*values.values(), user_id, now, key),
    )
    detail: dict[str, Any] = {"action": "change", "fields": sorted(changes)}
    if old != new:
        n = conn.execute(
            """UPDATE reward_balances SET unit = ?, updated_by = ?, updated_at = ?
               WHERE program_key = ? AND unit = ?""",
            (new, user_id, now, key, old),
        ).rowcount
        if n:
            detail["balances"] = n
    _event(conn, user_id, now, "program", key, detail)


def delete_program(
    conn: sqlite3.Connection,
    key: str,
    *,
    unit: str | None = None,
    user_id: int | None,
    now: str,
) -> None:
    """Delete an app program and its balances. When the chat sends that key too,
    the balances stay with the chat's program, and the ones in another unit
    are stranded. With a unit ("Delete balances"), delete only the key's
    stranded entries in that unit: the ones the admin saw and confirmed. A 409
    when none are left in it, so an import that changed the page in between
    never deletes entries that now count."""
    _in_write(conn)
    progs = _programs(conn)
    if unit is not None:
        stranded = [r for r in _stranded(conn, progs, key) if r["unit"] == unit]
        if not stranded:
            raise EditError(409, BALANCES_CHANGED)
        conn.executemany("DELETE FROM reward_balances WHERE id = ?", [(r["id"],) for r in stranded])
        detail = {"action": "delete_balances", "balances": len(stranded)}
        _event(conn, user_id, now, "program", key, detail)
        return
    if key in progs.app:
        sent = progs.chat.get(key)
        if sent is None and _program_refs(conn, key):
            raise EditError(422, PROGRAM_IN_USE)
        conn.execute("DELETE FROM reward_app_programs WHERE program_key = ?", (key,))
        n = 0
        if sent is None:
            n = conn.execute("DELETE FROM reward_balances WHERE program_key = ?", (key,)).rowcount
        _event(conn, user_id, now, "program", key, {"action": "delete", "balances": n})
        return
    raise _not_app(progs, key)


def _program_refs(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    """Edit rows (app benefits and program edits) that name the program key."""
    return conn.execute(
        """SELECT * FROM reward_benefit_edits
           WHERE json_extract(fields_json, '$.program_key') = ? ORDER BY benefit_key""",
        (key,),
    ).fetchall()


def _repoint(
    conn: sqlite3.Connection,
    chat: Rewards,
    key: str,
    to_key: str,
    *,
    user_id: int | None,
    now: str,
) -> int:
    """Benefits that name the program key name to_key instead. On a chat
    benefit that already names to_key, the program edit goes. Returns the
    count."""
    sent = {b.key: b.program_key for b in chat.benefits}
    rows = _program_refs(conn, key)
    for row in rows:
        v = _row_state(row)
        if row["origin"] == "chat" and sent.get(row["benefit_key"]) == to_key:
            v["fields"].pop("program_key")
            v["base"].pop("program_key", None)
        else:
            v["fields"]["program_key"] = to_key
        _save_row(
            conn, row["benefit_key"], row["origin"], row["account_key"], v, user_id=user_id, now=now
        )
    return len(rows)


def move_balances(
    conn: sqlite3.Connection, key: str, to_key: str, *, user_id: int | None, now: str
) -> None:
    """Move balances to another shown program in their unit. The source is an
    app program (every entry; then the program is deleted) or a key's stranded
    entries: on a dropped key or a timeshare the ones in to_key's unit (a key
    can hold both units), on a shown program the ones in the other unit. An
    app program with no balances moves to any program, a timeshare too: that
    is "Use the chat's" on a duplicate. Benefits that name a key that is gone
    after the move name to_key. A shown chat program stays, and so do they."""
    _in_write(conn)
    found = _chat(conn)
    progs = _programs(conn, found)
    app = key in progs.app
    rows = _entries(conn, key) if app else _stranded(conn, progs, key)
    if not app and not rows:
        raise _not_app(progs, key)
    if to_key == key:
        raise EditError(422, SAME_PROGRAM)
    kind = progs.kind(to_key)
    if kind is None:
        raise EditError(404, NO_PROGRAM)
    if rows:
        if kind == "timeshare":
            raise EditError(422, TIMESHARE)
        fit = [r for r in rows if r["unit"] == UNIT[kind]]
        if not fit or (app and len(fit) < len(rows)):
            raise EditError(422, OTHER_UNIT)
        rows = fit
    taken = {r["as_of"] for r in _entries(conn, to_key)}
    if any(r["as_of"] in taken for r in rows):
        raise EditError(409, SAME_DAY)
    conn.executemany(
        "UPDATE reward_balances SET program_key = ?, updated_by = ?, updated_at = ? WHERE id = ?",
        [(to_key, user_id, now, r["id"]) for r in rows],
    )
    if app:
        conn.execute("DELETE FROM reward_app_programs WHERE program_key = ?", (key,))
    detail: dict[str, Any] = {"action": "move", "balances": len(rows), "program_deleted": app}
    if app or progs.kind(key) is None:
        refs = _repoint(conn, found[1], key, to_key, user_id=user_id, now=now)
        if refs:
            detail["benefits"] = refs
    _event(conn, user_id, now, "program", key, detail)


def keep_both(
    conn: sqlite3.Connection, key: str, other_key: str, *, user_id: int | None, now: str
) -> None:
    """ "Keep both" on a duplicate: the app program and the chat program with the
    same name both stay, and the notice goes away."""
    _in_write(conn)
    progs = _programs(conn)
    row = _app_row(progs, key)
    other = progs.chat.get(other_key)
    if other is None or other_key == key or other.name.casefold() != row["name"].casefold():
        raise EditError(422, NOT_DUPLICATE)
    conn.execute(
        """UPDATE reward_app_programs SET dup_dismissed_key = ?, updated_by = ?, updated_at = ?
           WHERE program_key = ?""",
        (other_key, user_id, now, key),
    )
    _event(conn, user_id, now, "program", key, {"action": "keep_both"})


# --------------------------------------------------------------------------
# Benefits (plan 3.2)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Benefits:
    """The chat's benefits and the edit rows, read in the write."""

    chat: dict[str, dict[str, Any]]  # the chat's items as JSON dicts, by key
    rows: dict[str, sqlite3.Row]  # reward_benefit_edits, by key
    programs: Programs
    today: date

    def app(self, key: str) -> sqlite3.Row | None:
        row = self.rows.get(key)
        return row if row is not None and row["origin"] == "app" else None

    def shown(self) -> set[str]:
        """Every current benefit, hidden ones too. An app item that no longer
        passes is not shown."""
        apps = {k for k in self.rows if self.app(k) is not None}
        return set(self.chat) | {k for k in apps if self._app_ok(k)}

    def _app_item(self, key: str) -> dict[str, Any]:
        row = self.rows[key]
        return ov.app_benefit(
            key,
            row["account_key"],
            json.loads(row["fields_json"]),
            row["amount_from"],
            row["amount_before"],
            self.today.year,
        )

    def _app_ok(self, key: str) -> bool:
        return ov.benefit_ok(self._app_item(key), self.programs.keys())

    def _applied(self, key: str) -> ov.Applied:
        """A chat item with its edit row on top, as the overlay does it."""
        row, sent = self.rows[key], self.chat[key]
        fields, base = json.loads(row["fields_json"]), json.loads(row["base_json"])
        return ov.fitted(sent, fields, base, row["amount_from"], self.programs.keys())

    def item(self, key: str) -> dict[str, Any] | None:
        """The benefit as the overlay shows it now (an app row's body, even
        one that no longer passes), or None when not shown."""
        row = self.rows.get(key)
        if row is not None and row["origin"] == "app":
            return self._app_item(key)
        sent = self.chat.get(key)
        if sent is None or row is None:
            return sent
        return self._applied(key).item

    def fits(self, key: str) -> bool:
        """False when the overlay skips the key's chat edit (rule 9)."""
        row = self.rows.get(key)
        if row is None or row["origin"] == "app" or key not in self.chat:
            return True
        return self._applied(key).fits

    def card_kind(self, key: str) -> tuple[str, str] | None:
        """A benefit's (account_key, kind): shown, else from the newest run that
        sent it."""
        item = self.item(key)
        if item is not None:
            return item["account_key"], item["kind"]
        return ov.benefit_origins(self.programs.runs).get(key)


def _benefits(conn: sqlite3.Connection, today: date) -> Benefits:
    """The benefits as the page shows them, or a 409 before the first 1.3 run."""
    found = _chat(conn)
    rows = {
        r["benefit_key"]: r
        for r in conn.execute("SELECT * FROM reward_benefit_edits ORDER BY benefit_key")
    }
    chat = {b.key: b.model_dump(mode="json", exclude_none=True) for b in found[1].benefits}
    return Benefits(chat, rows, _programs(conn, found), today)


def _plain(value: Any) -> Any:
    """A value as JSON: dates as ISO text, lists and cycles inside too, nulls
    in cycles dropped."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items() if v is not None}
    return value


def _scan_benefit(values: Mapping[str, Any], private: PrivateRules | None) -> None:
    for f, label in BENEFIT_TEXT.items():
        if values.get(f) is not None:
            check_text(label, [values[f]], private, SECTION)


def _valid(conn: sqlite3.Connection, st: Benefits, item: Mapping[str, Any]) -> None:
    """The item as the overlay would show it passes RewardBenefit, and names a
    shown program and known categories."""
    try:
        b = RewardBenefit.model_validate(dict(item))
    except ValidationError:
        raise EditError(422, BAD_FIT) from None
    if b.program_key is not None and b.program_key not in st.programs.keys():
        raise EditError(422, UNKNOWN_PROGRAM)
    for k in b.category_keys or []:
        require_category(conn, k)
    starts = [c.start for c in b.cycles or []]
    if len(set(starts)) != len(starts):
        raise EditError(422, CYCLE_STARTS)


def _tidy(values: dict[str, Any]) -> dict[str, Any]:
    """Category keys and cycles in order, as the chat sends them."""
    if values.get("category_keys") is not None:
        values["category_keys"] = sorted(set(values["category_keys"]))
    if values.get("cycles") is not None:
        values["cycles"] = sorted(values["cycles"], key=lambda c: c["start"])
    return values


def _cycle_start(item: Mapping[str, Any] | None, today: date) -> str:
    """The start of the cycle that holds today, else today."""
    day = today.isoformat()
    for c in (item or {}).get("cycles") or []:
        if c["start"] <= day <= c["end"]:
            return c["start"]
    return day


def _benefit_keys(conn: sqlite3.Connection) -> set[str]:
    """Every key an input holds."""
    return {
        r[0]
        for r in conn.execute(
            """SELECT benefit_key FROM reward_benefit_edits
               UNION SELECT benefit_key FROM reward_match_terms
               UNION SELECT benefit_key FROM reward_credit_decisions WHERE benefit_key IS NOT NULL
               UNION SELECT benefit_key FROM reward_cycle_marks"""
        )
    }


def _save_row(
    conn: sqlite3.Connection,
    key: str,
    origin: str,
    account_key: str,
    v: Mapping[str, Any],
    *,
    user_id: int | None,
    now: str,
) -> None:
    """Insert or replace a key's edit row. A chat row with nothing left goes."""
    if origin == "chat" and not v["fields"] and not v["hidden"] and not v["match_off"]:
        conn.execute("DELETE FROM reward_benefit_edits WHERE benefit_key = ?", (key,))
        return
    conn.execute(
        """INSERT INTO reward_benefit_edits (benefit_key, origin, account_key, fields_json,
               base_json, amount_from, amount_before, hidden, match_off, updated_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (benefit_key) DO UPDATE SET
             account_key = excluded.account_key, fields_json = excluded.fields_json,
             base_json = excluded.base_json, amount_from = excluded.amount_from,
             amount_before = excluded.amount_before, hidden = excluded.hidden,
             match_off = excluded.match_off, updated_by = excluded.updated_by,
             updated_at = excluded.updated_at""",
        (
            key,
            origin,
            account_key,
            json.dumps(v["fields"], sort_keys=True),
            json.dumps(v["base"], sort_keys=True),
            v["amount_from"],
            v["amount_before"],
            int(v["hidden"]),
            int(v["match_off"]),
            user_id,
            now,
        ),
    )


def _row_state(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "fields": {},
            "base": {},
            "amount_from": None,
            "amount_before": None,
            "hidden": False,
            "match_off": False,
        }
    return {
        "fields": json.loads(row["fields_json"]),
        "base": json.loads(row["base_json"]),
        "amount_from": row["amount_from"],
        "amount_before": row["amount_before"],
        "hidden": bool(row["hidden"]),
        "match_off": bool(row["match_off"]),
    }


def _amount_start(current: str | None, sent: str | None, default: str) -> str:
    """When a changed amount starts. A first change starts on the day sent, by
    default `default`. A later one keeps the first one's start, or moves it
    earlier. A later day is refused: only one start is kept, so the cycles
    between would go back to the amount from before any change."""
    if current is None:
        return sent or default
    if sent is None:
        return current
    if sent > current:
        raise EditError(422, AMOUNT_LATER)
    return sent


def _prune(v: dict[str, Any], sent: Mapping[str, Any]) -> None:
    """Drop the fields the chat caught up on (rule 3). The page already shows
    them as the chat's, so a later chat change shows quietly."""
    for f in [f for f, mine in v["fields"].items() if sent.get(f) == mine]:
        v["fields"].pop(f)
        v["base"].pop(f, None)
    if "amount" not in v["fields"]:
        v["amount_from"] = None


def _app_changes(
    v: dict[str, Any], new: Mapping[str, Any], start: str | None, default: str
) -> None:
    """An app benefit's new fields, and when a new amount starts. Cycles that
    start before it keep amount_before, the amount before any change."""
    was = v["fields"].get("amount")
    for f, value in new.items():
        if value is None:
            v["fields"].pop(f, None)
        else:
            v["fields"][f] = value
    if "amount" in new:
        first = was if v["amount_before"] is None else v["amount_before"]
        if v["fields"].get("amount") == first:  # the amount before any change: no start
            v["amount_before"] = v["amount_from"] = None
        elif v["amount_before"] is None:  # the first change
            v["amount_before"], v["amount_from"] = was, start or default
        else:
            v["amount_from"] = _amount_start(v["amount_from"], start, default)
    elif start is not None:
        if v["amount_before"] is None:
            raise EditError(422, AMOUNT_FROM)
        v["amount_from"] = start


def _chat_changes(
    st: Benefits,
    key: str,
    v: dict[str, Any],
    new: Mapping[str, Any],
    resets: list[str],
    start: str | None,
    default: str,
) -> dict[str, Any] | None:
    """A chat benefit's edit with the changes on top. Returns the item as the
    overlay would show it, or None when only hidden or match_off change on an
    edit the overlay skips (rule 9): that edit stays as it is."""
    sent = st.chat[key]
    stale = not st.fits(key)
    if stale and not new and not resets and start is None:
        return None
    edited = "amount" in v["fields"] and v["fields"]["amount"] != sent.get("amount")
    prior = v["amount_from"] if edited else None
    for f in resets:
        v["fields"].pop(f, None)
        v["base"].pop(f, None)
    for f, value in new.items():
        if value == sent.get(f):  # the chat's own value: not an edit
            v["fields"].pop(f, None)
            v["base"].pop(f, None)
        else:
            v["fields"][f], v["base"][f] = value, sent.get(f)
    _prune(v, sent)
    if not ov.edit_fits(sent["kind"], v["fields"]):
        raise EditError(422, NO_FIT_FIRST if stale else BAD_FIT)
    if "amount" in new and "amount" in v["fields"]:
        v["amount_from"] = _amount_start(prior, start, default)
    elif start is not None and "amount" not in new:  # a start with no amount sent
        if "amount" not in v["fields"]:
            raise EditError(422, AMOUNT_FROM)
        v["amount_from"] = start
    if "amount" not in v["fields"]:  # also the chat's amount set back: its start goes
        v["amount_from"] = None
    item = ov.apply_edit(sent, v["fields"], v["base"], v["amount_from"]).item
    if stale and not ov.benefit_ok(item, st.programs.keys()):
        raise EditError(422, NO_FIT_FIRST)
    return item


def add_benefit(
    conn: sqlite3.Connection,
    values: Mapping[str, Any],
    *,
    private: PrivateRules | None,
    today: date,
    user_id: int | None,
    now: str,
) -> str:
    """Add a benefit in the app (rule 7). Returns its new key."""
    _in_write(conn)
    st = _benefits(conn, today)
    body = _tidy(_plain({f: v for f, v in values.items() if v is not None}))
    account = conn.execute(
        "SELECT type FROM accounts WHERE account_key = ?", (body["account_key"],)
    ).fetchone()
    if account is None or account["type"] not in rewards.CARD_TYPES:
        raise EditError(422, NOT_A_CARD)
    stored = body.get("cadence") in ov.STORED_CYCLES
    if stored and not body.get("cycles"):
        raise EditError(422, NEEDS_CYCLES)
    if not stored and "cycles" in body:
        raise EditError(422, NO_CYCLES)
    _scan_benefit(body, private)
    taken = set(st.chat) | _benefit_keys(conn) | set(ov.benefit_origins(st.programs.runs))
    key = app_key(body["name"], taken, "benefit")
    # The slug folds accents and drops digits, so it can spell what the name hid.
    check_text("name", [key], private, SECTION)
    body["key"] = key
    _valid(conn, st, ov.app_benefit(key, body["account_key"], body, None, None, today.year))
    v = _row_state(None) | {"fields": body}
    _save_row(conn, key, "app", body["account_key"], v, user_id=user_id, now=now)
    fields = sorted(f for f in body if f not in ("key", "account_key"))
    _event(conn, user_id, now, "benefit", key, {"action": "add", "fields": fields})
    return key


def update_benefit(
    conn: sqlite3.Connection,
    key: str,
    *,
    changes: Mapping[str, Any] | None = None,
    reset: Iterable[str] = (),
    hidden: bool | None = None,
    match_off: bool | None = None,
    amount_from: date | None = None,
    private: PrivateRules | None,
    today: date,
    user_id: int | None,
    now: str,
) -> None:
    """Change, reset, hide or show a benefit (rules 2 to 6). `changes`: field ->
    new value, None clears it. `reset`: fields that go back to the chat's."""
    _in_write(conn)
    st = _benefits(conn, today)  # the 409 before the first 1.3 run comes first
    new = _tidy({f: _plain(v) for f, v in (changes or {}).items()})
    resets = sorted(set(reset))
    if not new and not resets and hidden is None and match_off is None and amount_from is None:
        raise EditError(422, NOTHING)
    if set(new) & set(resets):
        raise EditError(422, SET_AND_RESET)
    if (set(new) | set(resets)) - set(ov.EDITABLE_FIELDS):
        raise EditError(422, FIXED)
    row, sent = st.rows.get(key), st.chat.get(key)
    app = st.app(key)
    if app is None and sent is None:
        raise EditError(404, NO_BENEFIT)
    _scan_benefit(new, private)
    old = st.item(key)
    v = _row_state(row)
    start = amount_from.isoformat() if amount_from is not None else None
    if app is not None:
        if resets:
            raise EditError(422, APP_EDIT)
        _app_changes(v, new, start, _cycle_start(old, today))
        item = ov.app_benefit(
            key, app["account_key"], v["fields"], v["amount_from"], v["amount_before"], today.year
        )
    else:
        # None: an edit the overlay skips; the chat's item is what shows
        item = _chat_changes(st, key, v, new, resets, start, _cycle_start(old, today)) or old
    if match_off is not None:
        if match_off and item["kind"] != "credit":
            raise EditError(422, MATCH_OFF_CREDITS)
        v["match_off"] = match_off
    if hidden is not None:
        v["hidden"] = hidden
    _valid(conn, st, item)
    origin = "app" if app is not None else "chat"
    _save_row(conn, key, origin, item["account_key"], v, user_id=user_id, now=now)
    detail: dict[str, Any] = {"action": "change"}
    named = sorted(new) + (["amount_from"] if start is not None else [])
    if named:
        detail["fields"] = named
    if resets:
        detail["reset"] = resets
    for flag, sent_flag in (("hidden", hidden), ("match_off", match_off)):
        if sent_flag is not None:
            detail[flag] = sent_flag
    _event(conn, user_id, now, "benefit", key, detail)


def _chat_row(st: Benefits, key: str) -> tuple[sqlite3.Row | None, dict[str, Any]]:
    """A shown chat benefit's edit row (None when it has none) and its item."""
    if st.app(key) is not None:
        raise EditError(422, APP_EDIT)
    sent = st.chat.get(key)
    if sent is None:
        raise EditError(422, NOT_SHOWN) if key in st.rows else EditError(404, NO_BENEFIT)
    return st.rows.get(key), sent


def resolve_benefit(
    conn: sqlite3.Connection,
    key: str,
    field: str,
    choice: str,
    *,
    today: date,
    user_id: int | None,
    now: str,
) -> None:
    """The chat changed an edited field: keep mine (base becomes the chat's
    value) or use the chat's (the edit on that field goes)."""
    _in_write(conn)
    st = _benefits(conn, today)
    row, sent = _chat_row(st, key)
    v = _row_state(row)
    if field not in v["fields"]:
        raise EditError(422, NOT_EDITED)
    if choice == "mine":
        v["base"][field] = sent.get(field)
    else:
        v["fields"].pop(field)
        v["base"].pop(field, None)
    _prune(v, sent)
    _valid(conn, st, ov.apply_edit(sent, v["fields"], v["base"], v["amount_from"]).item)
    _save_row(conn, key, "chat", sent["account_key"], v, user_id=user_id, now=now)
    action = "keep_mine" if choice == "mine" else "use_chat"
    _event(conn, user_id, now, "benefit", key, {"action": action, "fields": [field]})


def reset_benefit(
    conn: sqlite3.Connection, key: str, *, today: date, user_id: int | None, now: str
) -> None:
    """Use the chat's for every field. Hide, match words and the rest stay."""
    _in_write(conn)
    st = _benefits(conn, today)
    row, sent = _chat_row(st, key)
    v = _row_state(row)
    if not v["fields"]:
        raise EditError(422, NOTHING)
    fields = sorted(v["fields"])
    v |= {"fields": {}, "base": {}, "amount_from": None, "amount_before": None}
    _save_row(conn, key, "chat", sent["account_key"], v, user_id=user_id, now=now)
    _event(conn, user_id, now, "benefit", key, {"action": "reset", "fields": fields})


_HELD = (
    ("terms", "reward_match_terms"),
    ("choices", "reward_credit_decisions"),
    ("marks", "reward_cycle_marks"),
)


def _held(conn: sqlite3.Connection, key: str) -> bool:
    """Any input on the key: an edit row, words, choices or marks."""
    return key in _benefit_keys(conn)


def delete_benefit(
    conn: sqlite3.Connection, key: str, *, today: date, user_id: int | None, now: str
) -> None:
    """Delete an app benefit with its words, choices and marks. When the chat
    sends that key too ("Use the chat's"), they stay with the chat's item. On a
    key the chat no longer sends, delete every input on it."""
    _in_write(conn)
    st = _benefits(conn, today)
    app = st.app(key) is not None
    if not app and key in st.chat:
        raise EditError(422, CHAT_DELETE)
    if not app and not _held(conn, key):
        raise EditError(404, NO_BENEFIT)
    n = conn.execute("DELETE FROM reward_benefit_edits WHERE benefit_key = ?", (key,)).rowcount
    detail: dict[str, Any] = {"action": "delete"}
    if not app:
        detail = {"action": "delete_orphan", "edits": n}
    keep = app and key in st.chat  # the chat's item takes them over by key
    for name, table in _HELD:
        detail[name] = 0
        if not keep:
            sql = f"DELETE FROM {table} WHERE benefit_key = ?"
            detail[name] = conn.execute(sql, (key,)).rowcount
    _event(conn, user_id, now, "benefit", key, detail)


def move_benefit(
    conn: sqlite3.Connection,
    key: str,
    to_key: str,
    *,
    today: date,
    user_id: int | None,
    now: str,
) -> None:
    """Move a key's inputs to another current benefit on the same card and
    kind, in one write: an orphan's edits, words, choices and marks, or an app
    benefit's words, choices and marks (the app benefit is deleted: "Use the
    chat's" on a duplicate). A clash on a unique key is a 409."""
    _in_write(conn)
    st = _benefits(conn, today)
    app = st.app(key) is not None
    if not app and key in st.chat:
        raise EditError(422, CHAT_BENEFIT)
    if not app and not _held(conn, key):
        raise EditError(404, NO_BENEFIT)
    if to_key == key:
        raise EditError(422, SAME_BENEFIT)
    if to_key not in st.shown():
        raise EditError(404, NO_BENEFIT)
    source = st.card_kind(key)
    if source is None:
        raise EditError(422, UNKNOWN_BENEFIT)
    if source != st.card_kind(to_key):
        raise EditError(422, OTHER_BENEFIT)
    moves_row = not app and key in st.rows
    if moves_row and to_key in st.rows:
        raise EditError(409, EDIT_CLASH)
    clash = conn.execute(
        """SELECT 1 FROM reward_match_terms a JOIN reward_match_terms b
             ON upper(a.term) = upper(b.term)
           WHERE a.benefit_key = ? AND b.benefit_key = ?""",
        (key, to_key),
    ).fetchone()
    if clash is not None:
        raise EditError(409, TERM_CLASH)
    clash = conn.execute(
        """SELECT 1 FROM reward_cycle_marks a JOIN reward_cycle_marks b
             ON a.cycle_start = b.cycle_start
           WHERE a.benefit_key = ? AND b.benefit_key = ?""",
        (key, to_key),
    ).fetchone()
    if clash is not None:
        raise EditError(409, MARK_CLASH)
    if moves_row:  # the edits must fit the target as the overlay would show it
        row = st.rows[key]
        fields, base = json.loads(row["fields_json"]), json.loads(row["base_json"])
        done = ov.fitted(st.chat[to_key], fields, base, row["amount_from"], st.programs.keys())
        if not done.fits:
            raise EditError(422, MOVE_NO_FIT)
        _valid(conn, st, done.item)
    detail: dict[str, Any] = {"action": "move", "benefit_deleted": app}
    if app:
        conn.execute("DELETE FROM reward_benefit_edits WHERE benefit_key = ?", (key,))
        detail["edits"] = 0
    else:
        detail["edits"] = conn.execute(
            """UPDATE reward_benefit_edits SET benefit_key = ?, updated_by = ?, updated_at = ?
               WHERE benefit_key = ?""",
            (to_key, user_id, now, key),
        ).rowcount
    for name, table in _HELD:
        detail[name] = conn.execute(
            f"UPDATE {table} SET benefit_key = ?, updated_by = ?, updated_at = ? "
            "WHERE benefit_key = ?",
            (to_key, user_id, now, key),
        ).rowcount
    _event(conn, user_id, now, "benefit", key, detail)


def keep_both_benefits(
    conn: sqlite3.Connection,
    key: str,
    other_key: str,
    *,
    today: date,
    user_id: int | None,
    now: str,
) -> None:
    """ "Keep both" on a duplicate: the app benefit and the chat benefit like it
    both stay, and the notice goes away."""
    _in_write(conn)
    st = _benefits(conn, today)
    row = st.app(key)
    if row is None:
        raise EditError(422, APP_EDIT) if key in st.chat else EditError(404, NO_BENEFIT)
    if other_key not in st.chat or st.app(other_key) is not None:  # a chat item, not taken over
        raise EditError(422, NOT_ALIKE)
    if not ov.alike(st.item(key) or {}, st.item(other_key) or {}):
        raise EditError(422, NOT_ALIKE)
    conn.execute(
        """UPDATE reward_benefit_edits SET dup_dismissed_key = ?, updated_by = ?, updated_at = ?
           WHERE benefit_key = ?""",
        (other_key, user_id, now, key),
    )
    _event(conn, user_id, now, "benefit", key, {"action": "keep_both"})


# --------------------------------------------------------------------------
# For benefit files (rewards_files): the same reads, checks and writes
# --------------------------------------------------------------------------


def benefits_state(conn: sqlite3.Connection, today: date) -> Benefits:
    """The benefits as the page shows them, or a 409 before the first 1.3 run."""
    return _benefits(conn, today)


def input_keys(conn: sqlite3.Connection) -> set[str]:
    """Every benefit key an input holds: edits, words, choices and marks."""
    return _benefit_keys(conn)


def row_state(row: sqlite3.Row | None) -> dict[str, Any]:
    """An edit row as fields, base, amount_from, amount_before, hidden and
    match_off. None gives an empty one."""
    return _row_state(row)


def check_benefit(conn: sqlite3.Connection, st: Benefits, item: Mapping[str, Any]) -> None:
    """The item as the overlay would show it passes RewardBenefit, and names a
    shown program and known categories."""
    _valid(conn, st, item)


def changed_benefit(
    conn: sqlite3.Connection,
    st: Benefits,
    key: str,
    changes: Mapping[str, Any],
    *,
    start: str | None,
    today: date,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A shown benefit's edit row and its item as the overlay would show it,
    after `changes` (field -> value, None clears), checked as update_benefit
    checks them. Nothing is written. `start`: when a changed amount starts."""
    v = _row_state(st.rows.get(key))
    old = st.item(key)
    if old is None:
        raise EditError(404, NO_BENEFIT)
    app = st.app(key)
    new = _tidy(dict(changes))
    if app is not None:
        _app_changes(v, new, start, _cycle_start(old, today))
        item = ov.app_benefit(
            key, app["account_key"], v["fields"], v["amount_from"], v["amount_before"], today.year
        )
    else:
        item = _chat_changes(st, key, v, new, [], start, _cycle_start(old, today)) or old
    _valid(conn, st, item)
    return v, item


def save_benefit_row(
    conn: sqlite3.Connection,
    key: str,
    origin: str,
    account_key: str,
    v: Mapping[str, Any],
    *,
    user_id: int | None,
    now: str,
) -> None:
    """Insert or replace a key's edit row. A chat row with nothing left goes."""
    _in_write(conn)
    _save_row(conn, key, origin, account_key, v, user_id=user_id, now=now)


def record_event(
    conn: sqlite3.Connection, user_id: int | None, now: str, entity: str, key: str, detail: dict
) -> None:
    """One `events` row, kind rewards_edit. The detail holds names and counts only."""
    _in_write(conn)
    _event(conn, user_id, now, entity, key, detail)


# --------------------------------------------------------------------------
# Credit matching: match words, cycle marks and credit choices (plan 4 and 6)
# --------------------------------------------------------------------------


def _view(conn: sqlite3.Connection) -> ov.Effective:
    """The effective rewards, or a 409 before the first 1.3 run."""
    runs, _ = _chat(conn)
    eff = ov.effective_rewards(conn, runs)
    if eff is None:  # the chat sent rewards, so the overlay gives a view
        raise EditError(409, NO_REWARDS)
    return eff


def _credit(eff: ov.Effective, key: str, wrong_kind: str) -> RewardBenefit:
    """A credit the page shows, hidden ones too."""
    found = next((b for b in (*eff.rewards.benefits, *eff.meta.hidden) if b.key == key), None)
    if found is None:
        raise EditError(404, NO_BENEFIT)
    if found.kind != "credit":
        raise EditError(422, wrong_kind)
    return found


def _starts(b: RewardBenefit) -> set[str]:
    """The cycles the page shows: none that start after the benefit ends."""
    return {c.start for c in b.cycles or [] if b.ends_on is None or c.start <= b.ends_on}


def _terms(inputs: ov.Inputs) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for t in inputs.terms:
        out.setdefault(t["benefit_key"], []).append(t["term"])
    return {k: tuple(v) for k, v in out.items()}


def _matching(
    conn: sqlite3.Connection, eff: ov.Effective, inputs: ov.Inputs, today: date
) -> tuple[list[rm.Benefit], rm.Rows]:
    """What the overlay matches on: every effective credit, hidden ones too,
    and the candidate rows, read in the caller's transaction."""
    items = [b for b in (*eff.rewards.benefits, *eff.meta.hidden) if b.kind == "credit"]
    benefits = rm.benefits_from(items, eff.meta.match_off)
    return benefits, rm.load_rows(conn, benefits, inputs.decisions, today)


def _check_term(term: str, private: PrivateRules | None) -> str:
    text = rm.check_term(term)
    check_text("term", [text], private, SECTION)
    return text


def set_terms(
    conn: sqlite3.Connection,
    key: str,
    terms: Sequence[str],
    *,
    private: PrivateRules | None,
    user_id: int | None,
    now: str,
) -> None:
    """Replace a credit's match words (0 to 10)."""
    _in_write(conn)
    _credit(_view(conn), key, MATCH_OFF_CREDITS)
    if len(terms) > TERMS_MAX:
        raise EditError(422, TOO_MANY_TERMS)
    clean: list[str] = []
    for term in terms:
        text = _check_term(term, private)
        if rm.ascii_upper(text) in {rm.ascii_upper(t) for t in clean}:
            raise EditError(422, TERM_TWICE)
        clean.append(text)
    detail = {"action": "replace", **replace_terms(conn, key, clean, user_id=user_id, now=now)}
    _event(conn, user_id, now, "terms", key, detail)


def replace_terms(
    conn: sqlite3.Connection, key: str, clean: Sequence[str], *, user_id: int | None, now: str
) -> dict[str, int]:
    """Replace a key's saved match words with checked ones. Returns the counts:
    terms, added and removed (case ignored)."""
    _in_write(conn)
    old = {
        rm.ascii_upper(r[0])
        for r in conn.execute("SELECT term FROM reward_match_terms WHERE benefit_key = ?", (key,))
    }
    new = {rm.ascii_upper(t) for t in clean}
    conn.execute("DELETE FROM reward_match_terms WHERE benefit_key = ?", (key,))
    for text in clean:
        conn.execute(
            """INSERT INTO reward_match_terms (benefit_key, term, updated_by, updated_at)
               VALUES (?, ?, ?, ?)""",
            (key, text, user_id, now),
        )
    return {"terms": len(clean), "added": len(new - old), "removed": len(old - new)}


def _suggested(name: str, taken: set[str], private: PrivateRules | None) -> list[str]:
    """Words of the name, less generic, cadence and filler words and any word
    check_term refuses, in name order."""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z]+", name):
        word = rm.ascii_upper(word)
        if len(word) < rm.TERM_MIN or word in rm.GENERIC or word in rm.FILLER or word in taken:
            continue
        try:
            rm.check_term(word)
        except EditError:
            continue  # a piece of a generic word, like EDIT
        if not scan_text(word, private, SECTION):
            taken.add(word)
            out.append(word)
    return out


def _seen(
    conn: sqlite3.Connection,
    sent: RewardBenefit | None,
    taken: set[str],
    private: PrivateRules | None,
) -> dict | None:
    """The most common description, digits removed, of the card's credit rows
    with the same cents as a chat cycle's used, within 3 days of its used day.
    A description counts once per cycle. Only one that makes a valid word."""
    if sent is None:
        return None
    uses = [
        (to_cents(c.used), c.used_on)
        for c in sent.cycles or []
        if c.used is not None and c.used_on is not None and to_cents(c.used) > 0
    ]
    if not uses:
        return None
    days = [date.fromisoformat(d) for _, d in uses]
    span = (min(days) - timedelta(days=SEEN_DAYS), max(days) + timedelta(days=SEEN_DAYS))
    rows = conn.execute(
        rm.CANDIDATES_SQL, (sent.account_key, span[0].isoformat(), span[1].isoformat())
    ).fetchall()
    found: dict[str, int] = {}
    for cents, day in uses:
        at = date.fromisoformat(day)
        texts = {
            " ".join(re.sub(r"[0-9]", "", r["description"]).split())
            for r in rows
            if r["amount_cents"] == cents
            and abs((date.fromisoformat(r["txn_date"]) - at).days) <= SEEN_DAYS
        }
        for text in texts - {""}:
            found[text] = found.get(text, 0) + 1
    for text, n in sorted(found.items(), key=lambda kv: (-kv[1], kv[0])):
        try:
            term = rm.check_term(text)
        except EditError:
            continue
        if rm.ascii_upper(term) not in taken and not scan_text(term, private, SECTION):
            return {"term": term, "count": n}
    return None


def terms_view(conn: sqlite3.Connection, key: str, *, private: PrivateRules | None) -> dict:
    """A credit's saved, suggested and seen match words."""
    with read_transaction(conn):
        eff = ov.effective_rewards(conn, readers.monthly_runs(conn))
        if eff is None:
            raise EditError(404, NO_BENEFIT)
        b = _credit(eff, key, MATCH_OFF_CREDITS)
        saved = [
            r[0]
            for r in conn.execute(
                """SELECT term FROM reward_match_terms WHERE benefit_key = ?
                   ORDER BY upper(term)""",
                (key,),
            )
        ]
        taken = {rm.ascii_upper(t) for t in saved}
        sent = next((x for x in eff.chat.benefits if x.key == key), None)
        seen = _seen(conn, sent, taken, private)
    return {
        "benefit_key": key,
        "terms": saved,
        "suggested": _suggested(b.name, set(taken), private),
        "seen": seen,
        "match_off": key in eff.meta.match_off,
    }


def term_preview(
    conn: sqlite3.Connection, key: str, term: str, *, private: PrivateRules | None, today: date
) -> dict:
    """What a match word would do, with nothing saved: the card's credit rows
    this year that hold it, where each counts now and with the word (a tie
    with another benefit's word waits for review), and the cycles whose use or
    source would change (plan 3.3). `elsewhere` counts rows that count for
    another benefit now, whether or not the word would move them."""
    with read_transaction(conn):
        eff = _view(conn)
        text = _check_term(term, private)
        b = _credit(eff, key, MATCH_OFF_CREDITS)
        inputs = ov.load_inputs(conn)
        benefits, rows = _matching(conn, eff, inputs, today)
    terms = _terms(inputs)
    before = rm.match(benefits, terms, inputs.decisions, rows, today)
    more = terms | {key: (*terms.get(key, ()), text)}
    after = rm.match(benefits, more, inputs.decisions, rows, today)
    now = {r.txn_key: at[0] for at, rs in before.cycles.items() for r in rs}
    then = {r.txn_key: at[0] for at, rs in after.cycles.items() for r in rs}
    tied = {x.txn_key for x in after.review if key in x.options}
    names = {x.key: x.name for x in (*eff.rewards.benefits, *eff.meta.hidden)}
    word, first = rm.ascii_upper(text), f"{today.year}-01-01"
    hits = [
        r
        for r in rows.credits
        if r.account_key == b.account_key
        and first <= r.txn_date <= today.isoformat()
        and word in rm.ascii_upper(r.description)
    ]
    marks = {(m["benefit_key"], m["cycle_start"]): m["used_cents"] for m in inputs.marks}

    def use(found: rm.Match, at: tuple[str, str], card: str) -> tuple[int, str | None]:
        """As the page reads it (plan 3.3)."""
        if at in marks:
            return marks[at], "set"
        here = found.cycles.get(at, ())
        source = ov.cycle_source(at, here, found, eff.meta, rows.coverage.get(card))
        if source == "rows":
            return sum(r.cents for r in here), "rows"
        return (eff.meta.chat_used[at], "chat") if source == "chat" else (0, None)

    hidden = {h.key for h in eff.meta.hidden} - {key}
    cycles = []
    for bn in benefits:
        if bn.key in hidden:
            continue
        for c in bn.cycles:
            at = (bn.key, c.start)
            was, source = use(before, at, bn.account_key)
            will, then_source = use(after, at, bn.account_key)
            if (was, source) != (will, then_source):
                cycles.append(
                    {
                        "benefit_key": bn.key,
                        "benefit_name": bn.name,
                        "start": c.start,
                        "used_cents_before": was,
                        "used_cents_after": will,
                        "source_before": source,
                        "source_after": then_source,
                    }
                )
    return {
        "benefit_key": key,
        "matches": len(hits),
        "elsewhere": sum(1 for r in hits if now.get(r.txn_key) not in (None, key)),
        "rows": [
            {
                "txn_key": r.txn_key,
                "date": r.txn_date,
                "merchant": r.merchant,
                "description": r.description,
                "cents": r.cents,
                "now_key": now.get(r.txn_key),
                "now_name": names.get(now.get(r.txn_key) or ""),
                "after_key": then.get(r.txn_key),
                "after_name": names.get(then.get(r.txn_key) or ""),
                "tie": r.txn_key in tied,
            }
            for r in hits
        ],
        "cycles": cycles,
    }


def set_mark(
    conn: sqlite3.Connection,
    key: str,
    start: date,
    used_cents: int,
    used_on: date | None,
    *,
    today: date,
    user_id: int | None,
    now: str,
) -> None:
    """ "Mark used" or "Mark not used" (0) on a cycle the page shows."""
    _in_write(conn)
    b = _credit(_view(conn), key, NOT_A_CREDIT)
    day = start.isoformat()
    if day not in _starts(b):
        raise EditError(404, NO_CYCLE)
    if used_on is not None and used_on > today:
        raise EditError(422, FUTURE)
    if used_cents == 0 and used_on is not None:
        raise EditError(422, UNUSED_DAY)
    old = conn.execute(
        "SELECT 1 FROM reward_cycle_marks WHERE benefit_key = ? AND cycle_start = ?", (key, day)
    ).fetchone()
    conn.execute(
        """INSERT INTO reward_cycle_marks (benefit_key, cycle_start, used_cents, used_on,
               updated_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (benefit_key, cycle_start) DO UPDATE SET
             used_cents = excluded.used_cents, used_on = excluded.used_on,
             updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        (key, day, used_cents, _iso(used_on), user_id, now),
    )
    detail = {"action": "replace" if old else "set", "used": used_cents > 0}
    _event(conn, user_id, now, "mark", key, detail)


def delete_mark(
    conn: sqlite3.Connection, key: str, start: date, *, user_id: int | None, now: str
) -> None:
    """Clear a cycle's mark. The cycle may be one the chat no longer sends."""
    _in_write(conn)
    _credit(_view(conn), key, NOT_A_CREDIT)
    n = conn.execute(
        "DELETE FROM reward_cycle_marks WHERE benefit_key = ? AND cycle_start = ?",
        (key, start.isoformat()),
    ).rowcount
    if not n:
        raise EditError(404, NO_MARK)
    _event(conn, user_id, now, "mark", key, {"action": "clear"})


# A live card credit, as matching reads candidates (rewards_match.CANDIDATES_SQL).
CARD_CREDIT_SQL = f"""
    SELECT 1 FROM v_transactions v JOIN accounts a ON a.account_key = v.account_key
    WHERE v.txn_key = ? AND v.txn_type = 'refund' AND v.amount_cents > 0
      AND COALESCE(v.is_transfer, 0) = 0 AND v.hidden = 0 AND v.pre_history = 0
      AND a.type IN ({", ".join("?" for _ in rewards.CARD_TYPES)})"""


GUARD_FIELDS = ("account_key", "txn_date", "amount_cents", "desc_norm", "merchant", "run_id")
GUARD_HASHES = ("row_sig", "near")  # hashes only: checked by format, never scanned or masked
# The live credits near a chosen row on its card, the row too: the guard's near map.
NEAR_SQL = """
    SELECT txn_key, description, merchant FROM transactions
    WHERE account_key = ? AND txn_date BETWEEN ? AND ? AND amount_cents > 0
      AND removed_at IS NULL
    ORDER BY txn_key"""


def choice_guard(conn: sqlite3.Connection, txn_key: str) -> dict[str, Any] | None:
    """What proves and re-finds a chosen row after a statement correction (plan
    4.4), from the live row, or None when no live row has the key. The imported
    identity (desc_norm and merchant, like the overrides guard) and run_id, the
    run that last wrote the row: the re-find takes only rows a later replace
    wrote. row_sig: a hash of the full description and imported merchant, so a
    key a statement replace handed to another row is not taken for this one.
    near: txn_key -> row_sig of the live credits on the card within 7 days,
    this one too. They were there when the choice was saved, so none of them is
    its corrected row, under its key or under a key it moved to."""
    row = conn.execute(
        f"""SELECT {", ".join(GUARD_FIELDS)}, description FROM transactions
            WHERE txn_key = ? AND removed_at IS NULL""",
        (txn_key,),
    ).fetchone()
    if row is None:
        return None
    guard: dict[str, Any] = {f: row[f] for f in GUARD_FIELDS}
    guard["row_sig"] = rm.row_sig(row["description"], row["merchant"])
    at = date.fromisoformat(row["txn_date"])
    span = (at - timedelta(days=rm.REFIND_DAYS), at + timedelta(days=rm.REFIND_DAYS))
    guard["near"] = {
        r["txn_key"]: rm.row_sig(r["description"], r["merchant"])
        for r in conn.execute(NEAR_SQL, (row["account_key"], *(d.isoformat() for d in span)))
    }
    return guard


def _refound(conn: sqlite3.Connection, eff: ov.Effective, txn_key: str, today: date) -> str | None:
    """The choice that found this row after a statement correction, if any."""
    inputs = ov.load_inputs(conn)
    if not inputs.decisions:
        return None
    benefits, rows = _matching(conn, eff, inputs, today)
    found = rm.match(benefits, _terms(inputs), inputs.decisions, rows, today)
    return found.refound.get(txn_key)


def set_choice(
    conn: sqlite3.Connection,
    txn_key: str,
    decision: str,
    *,
    benefit_key: str | None,
    cycle_start: date | None,
    reason: str | None,
    today: date,
    user_id: int | None,
    now: str,
) -> None:
    """Count a card credit for a benefit (link), or say it is not one. A choice
    on the row, its own or one that re-found it, is replaced."""
    _in_write(conn)
    eff = _view(conn)
    guard = choice_guard(conn, txn_key)  # what proves and re-finds the row later
    if guard is None:
        raise EditError(404, NO_ROW)
    if conn.execute(CARD_CREDIT_SQL, (txn_key, *rewards.CARD_TYPES)).fetchone() is None:
        raise EditError(422, NOT_CARD_CREDIT)
    start = _iso(cycle_start)
    if decision == "link":
        b = next((x for x in eff.rewards.benefits if x.key == benefit_key), None)
        if b is None or b.kind != "credit" or b.account_key != guard["account_key"]:
            raise EditError(422, PICK_CREDIT)
        if b.key in eff.meta.match_off:
            raise EditError(422, NEVER_ON_STATEMENTS)
        if start is not None and start not in _starts(b):
            raise EditError(422, PICK_CYCLE)
    own = conn.execute(
        "SELECT 1 FROM reward_credit_decisions WHERE txn_key = ?", (txn_key,)
    ).fetchone()
    replaced = own is not None
    if not replaced and (old := _refound(conn, eff, txn_key, today)) is not None:
        conn.execute("DELETE FROM reward_credit_decisions WHERE txn_key = ?", (old,))
        replaced = True
    conn.execute(
        """INSERT INTO reward_credit_decisions (txn_key, decision, reason, benefit_key,
               cycle_start, guard_json, updated_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (txn_key) DO UPDATE SET
             decision = excluded.decision, reason = excluded.reason,
             benefit_key = excluded.benefit_key, cycle_start = excluded.cycle_start,
             guard_json = excluded.guard_json, updated_by = excluded.updated_by,
             updated_at = excluded.updated_at""",
        (
            txn_key,
            decision,
            reason if decision == "not_benefit" else None,
            benefit_key if decision == "link" else None,
            start if decision == "link" else None,
            json.dumps(guard, sort_keys=True, separators=(",", ":")),
            user_id,
            now,
        ),
    )
    detail: dict[str, Any] = {"action": decision, "replaced": replaced}
    if start is not None:
        detail["pinned"] = True
    _event(conn, user_id, now, "credit", txn_key, detail)


def delete_choice(
    conn: sqlite3.Connection, txn_key: str, *, today: date, user_id: int | None, now: str
) -> None:
    """Clear the choice on a row: its own, or one that re-found it. A lost
    choice is cleared by its own key."""
    _in_write(conn)
    eff = _view(conn)
    n = conn.execute("DELETE FROM reward_credit_decisions WHERE txn_key = ?", (txn_key,)).rowcount
    if not n:
        old = _refound(conn, eff, txn_key, today)
        if old is None:
            raise EditError(404, NO_CHOICE)
        conn.execute("DELETE FROM reward_credit_decisions WHERE txn_key = ?", (old,))
    _event(conn, user_id, now, "credit", txn_key, {"action": "clear"})


def choice_view(conn: sqlite3.Connection, txn_key: str) -> dict:
    """A row's choice and the cycle it counts in now."""
    with read_transaction(conn):
        eff = ov.effective_rewards(conn, readers.monthly_runs(conn))
        row = conn.execute(
            """SELECT decision, benefit_key, cycle_start, reason FROM reward_credit_decisions
               WHERE txn_key = ?""",
            (txn_key,),
        ).fetchone()
    if eff is None or row is None:
        raise EditError(404, NO_CHOICE)
    counted = next(
        (at for at, keys in sorted(eff.meta.cycle_rows.items()) if txn_key in keys), None
    )
    return {
        "txn_key": txn_key,
        **dict(row),
        "counted_in": {"benefit_key": counted[0], "cycle_start": counted[1]} if counted else None,
    }
