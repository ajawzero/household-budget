"""Statement credits matched to card credit benefits (docs/plans/rewards-inputs.md
section 4). `load_rows` does the SQL, inside the overlay's read transaction.
`match` and `best_guess` are pure and deterministic: the same inputs give the
same result. The overlay fills cycle use from the result (plan 3.3).

- Benefits: every effective credit, chat or app, hidden ones included. Cycles
  are cut at the benefit's ends_on as the page cuts them, and carry their own
  amount after an amount change. A match_off benefit ("never shows on
  statements") takes no rows.
- Candidates: live rows (v_transactions) on those cards, txn_type refund,
  amount above 0, not a transfer, not hidden, not pre_history. Negative rows
  never count, so the purchase a credit offsets never matches its word.
- Window per card: the earliest cycle start to the latest cycle end plus 7
  days, capped at today. Rows dated this household year are read too, for the
  review list, and rows within 7 days of each choice's guard, for the re-find.
- Choices first. A statement replace that drops the first of two rows with
  the same date, amount and desc_norm hands its key to the second, so a key
  alone proves nothing. The guard's row_sig (a hash of the full imported
  description and merchant) does. A choice finds its row:
  1. Its key holds a live candidate with the guard's row_sig: that row.
  2. Its key holds a live row with the guard's row_sig that is no longer a
     candidate (hidden, a transfer now): the choice does nothing.
  3. Else (the row was removed, or its key holds another row now) the
     re-find: the one best `rank_candidate` match among candidates on the
     guard's card, in the key's statement, dated within 7 days and written by
     that statement's current run (the replace that dropped or rewrote the
     row), when that run is later than the guard's. Rows live when the choice
     was saved are never it: a key in the guard's near list, or the row_sig
     of a near row other than the chosen one (a row that only moved keys).
     Rows with a choice of their own are ranked too: when one of them is best
     or ties for best, the choice stays lost.
  Else "lost", never a guess: no match, a tie, two choices on one row, a key
  with no current statement run, or a guard with no row_sig or near. A row
  with the guard's row_sig is the chosen row as far as a choice can tell: a
  same-day twin with the same text proves nothing more. not_benefit drops the
  row. link counts it for its benefit, in its cycle when cycle_start still
  exists (pinned), else in its home cycle. A link to a credit that takes no
  rows (matching off, or not a credit on this card now) counts nowhere, with
  the flag "off".
- Decided cycles (plan 3.3): the chat's used may count a row that a choice
  moved, so these cycles never fall back to it while statements cover them.
  A found choice decides its row's natural place when it puts the row
  anywhere else: the home cycle of each benefit whose words win or tie on it,
  and a link's own home cycle. A spill that moves rows out of a cycle decides
  it. So does a choice that leaves empty a cycle the words alone fill (a
  spill it stopped, say): matching runs once more with no choices to see.
- Words: `term in description`, ASCII case ignored, on the imported
  description. Never desc_norm (cut to 32 characters) and never the editable
  merchant. Only benefits on the row's card. A benefit scores its longest
  matching word. The strictly longest wins. A tie leaves the row ambiguous: to
  review, never picked. A benefit takes nothing dated before its first cycle
  starts, or more than 7 days after its ends_on. Words apply to rows in the
  card's window or this year.
- Home cycle: the cycle that holds the row's date; else the latest cycle that
  ended within 7 days before it (a late post); else the flag "no_cycle".
- Flags and the review are this household year only. A row dated before it
  counts when a cycle holds it, and is otherwise left alone: no flag, no
  review.
- Spill, per benefit in start order: while cycle i is over its amount, its
  rows dated on or before end(i-1) plus 7 days move to cycle i-1, in
  (txn_date, txn_key) order, each one only when i-1 stays at or under its
  amount. Pinned rows never move. An unpinned row dated from a cycle's start
  to the previous cycle's end plus 7 days offers "Count in <previous cycle>
  instead".
- Unmatched: candidates on cards with a credit benefit, dated this household
  year up to today, with no word match and no choice. "returns": a purchase on
  the same card with the same imported merchant and the exact negative amount
  within 90 days before. "trips": the row has a trip_key. Else, with the
  ambiguous rows, "review", with a best guess: a benefit whose cycle at that
  date has room equal to the amount, then room of at least the amount, then
  the most name words in common. A tie goes to the first key. Room counts a
  mark's or the matched rows' use, not the chat's: the chat's used usually
  counts the very row that waits for review.
- Match words (check_term): 3 to 40 printable ASCII characters, never one that
  fits inside a line made only of generic words (CREDIT, STATEMENT, ...):
  matching is by substring, so "EDIT" or "T CREDIT" would claim every
  STATEMENT CREDIT. The writes also scan them.
- Nothing here logs. Errors carry fixed text, never a value.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import string
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.importer.models import RewardBenefit, to_cents
from app.importer.order import is_run_id, run_order_key
from app.services.overrides import EditError, rank_candidate

LATE_DAYS = 7  # a late post, a spill, a "Count in <previous cycle>" offer, an ended benefit
REFIND_DAYS = 7  # a choice re-finds its row within this many days of its guard
RETURN_DAYS = 90  # a purchase this many days before a credit makes it look like a return
TERM_MIN, TERM_MAX = 3, 40
CHUNK = 500
# A match word made only of these is refused: they are on every kind of credit.
GENERIC = frozenset(
    {
        "CREDIT",
        "CREDITS",
        "STATEMENT",
        "REFUND",
        "RETURN",
        "ADJUSTMENT",
        "OFFER",
        "BONUS",
        "REWARD",
        "REWARDS",
        "POINTS",
        "CASH",
        "BACK",
        "CARD",
    }
)
# Name words that say nothing about the merchant: cadence and filler.
FILLER = frozenset(
    {
        "MONTHLY",
        "QUARTERLY",
        "SEMI",
        "ANNUAL",
        "YEARLY",
        "MULTI",
        "YEAR",
        "MONTH",
        "QUARTER",
        "ONE",
        "TIME",
        "EVERY",
        "PER",
        "THE",
        "AND",
        "FOR",
        "OF",
        "TO",
        "IN",
        "ON",
        "AT",
        "AN",
    }
)
TERM_FORMAT = "A match word is 3 to 40 plain letters, digits, spaces or marks."
TERM_GENERIC = "Use a word from the credit line, like the merchant."
_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)
_PRINTABLE = frozenset(chr(c) for c in range(0x20, 0x7F))

CANDIDATES_SQL = """
    SELECT txn_key, account_key, txn_date, amount_cents, description, merchant,
           imported_merchant, desc_norm, trip_key, run_id, statement_key
    FROM v_transactions
    WHERE account_key = ? AND txn_date BETWEEN ? AND ?
      AND txn_type = 'refund' AND amount_cents > 0 AND COALESCE(is_transfer, 0) = 0
      AND hidden = 0 AND pre_history = 0
    ORDER BY txn_date, txn_key"""
# The coverage query in rewards.card_spend, over the card's cycles.
COVERAGE_SQL = """
    SELECT min(period_start) AS lo, max(period_end) AS hi FROM statements
    WHERE account_key = ? AND is_current = 1 AND period_end >= ? AND period_start <= ?"""
# What each choice's key holds now: its row, removed or live, and the run of its
# statement's current version.
HELD_SQL = """
    SELECT t.txn_key, t.statement_key, t.description, t.merchant, t.removed_at,
           s.run_id AS current_run
    FROM transactions t
    LEFT JOIN statements s ON s.statement_key = t.statement_key AND s.is_current = 1
    WHERE t.txn_key IN ({marks})"""


# --------------------------------------------------------------------------
# What matching works on
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Cycle:
    start: str
    end: str  # cut at the benefit's ends_on
    cents: int  # the cycle's amount


@dataclass(frozen=True)
class Benefit:
    """An effective credit, as matching sees it."""

    key: str
    account_key: str
    name: str
    ends_on: str | None
    cycles: tuple[Cycle, ...]  # in start order, cut at ends_on
    matches: bool = True  # False: match_off ("never shows on statements")


@dataclass(frozen=True)
class CreditRow:
    """A candidate statement credit."""

    txn_key: str
    account_key: str
    txn_date: str
    cents: int  # above 0
    description: str  # as imported: words match this
    merchant: str | None  # as shown (an edit wins)
    imported_merchant: str | None
    desc_norm: str
    trip_key: str | None
    run_id: str | None = None  # the run that last wrote the row
    statement_key: str | None = None


@dataclass(frozen=True)
class Held:
    """What a choice's txn_key holds now (load_rows)."""

    statement_key: str
    run_id: str | None  # the run of the statement's current version, if any
    row_sig: str | None = None  # the live row's; None: the row was removed


@dataclass(frozen=True)
class Purchase:
    account_key: str
    txn_date: str
    cents: int  # the amount as a positive number
    merchant: str | None  # imported


@dataclass(frozen=True)
class Rows:
    """What load_rows read."""

    credits: tuple[CreditRow, ...] = ()  # by (txn_date, txn_key)
    purchases: tuple[Purchase, ...] = ()  # with the amount of a credit this year
    # card -> (first period_start, last period_end) of its current statements
    coverage: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    held: Mapping[str, Held] = field(default_factory=dict)  # each choice's txn_key, when known


@dataclass(frozen=True)
class RowFlag:
    """A credit row that needs a look. lost: a choice whose row is gone (txn_key
    is the choice's). no_cycle: words or a link name a benefit, and no cycle
    holds the row's date. off: a link names a credit that takes no rows
    (matching off, or not a credit on the row's card now)."""

    kind: str
    txn_key: str
    benefit_key: str | None = None


@dataclass(frozen=True)
class ReviewItem:
    """An unmatched or ambiguous row ("Card credits to review")."""

    txn_key: str
    group: str  # review, returns or trips
    options: tuple[str, ...] = ()  # ambiguous: the benefits whose words tie
    guess: tuple[str, str] | None = None  # (benefit key, cycle start)


@dataclass(frozen=True)
class Match:
    # (benefit key, cycle start) -> the rows counted, by (txn_date, txn_key)
    cycles: Mapping[tuple[str, str], tuple[CreditRow, ...]]
    pinned: frozenset[str]  # rows a choice put in a cycle
    earlier: Mapping[str, str]  # txn_key -> the previous cycle's start
    flags: tuple[RowFlag, ...]
    review: tuple[ReviewItem, ...]  # no guesses yet
    # row key -> the choice's txn_key, for a choice that re-found its row
    refound: Mapping[str, str] = field(default_factory=dict)
    # (benefit key, cycle start) a choice or a spill moved a row out of: its use
    # never falls back to the chat's while statements cover it (plan 3.3)
    decided: frozenset[tuple[str, str]] = frozenset()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def ascii_upper(text: str) -> str:
    """Upper case for ASCII letters only, like SQLite's upper()."""
    return text.translate(_UPPER)


def _plus(day: str, days: int) -> str:
    """The day `days` later (or earlier), held at the calendar's ends."""
    try:
        return (date.fromisoformat(day) + timedelta(days=days)).isoformat()
    except OverflowError:
        return (date.max if days > 0 else date.min).isoformat()


def _order(row: CreditRow) -> tuple[str, str]:
    return row.txn_date, row.txn_key


def name_words(text: str) -> frozenset[str]:
    """The words of a name or a description that can tell a credit apart:
    letters only, generic and filler words left out."""
    return (
        frozenset(
            w for w in re.findall(r"[A-Z]+", ascii_upper(text)) if len(w) > 1 and w not in GENERIC
        )
        - FILLER
    )


def _fits_generic(tokens: Sequence[str]) -> bool:
    """The word could sit inside a line made only of generic words: one token
    inside a generic word ("EDIT"), or the end of one, whole ones, then the
    start of one ("T CREDIT", "STATEMENT CR")."""
    if len(tokens) == 1:
        return any(tokens[0] in g for g in GENERIC)
    head, *middle, tail = tokens
    return (
        any(g.endswith(head) for g in GENERIC)
        and all(t in GENERIC for t in middle)
        and any(g.startswith(tail) for g in GENERIC)
    )


def check_term(term: str) -> str:
    """A match word, trimmed with inner spaces collapsed. Raises EditError(422)
    with fixed text, never the word."""
    if any(ch not in _PRINTABLE for ch in term):
        raise EditError(422, TERM_FORMAT)
    text = " ".join(term.split())
    if not TERM_MIN <= len(text) <= TERM_MAX:
        raise EditError(422, TERM_FORMAT)
    tokens = re.findall(r"[A-Z0-9]+", ascii_upper(text))
    if not tokens or _fits_generic(tokens):
        raise EditError(422, TERM_GENERIC)
    return text


def benefits_from(items: Iterable[RewardBenefit], match_off: Collection[str]) -> list[Benefit]:
    """Every credit, by key, with its cycles cut at ends_on as the page cuts
    them: cycles that start after it are left out, the last one ends on it."""
    out = []
    for b in items:
        if b.kind != "credit":
            continue
        cycles = tuple(
            Cycle(
                c.start,
                min(c.end, b.ends_on) if b.ends_on is not None else c.end,
                to_cents(c.amount or b.amount or "0.00"),
            )
            for c in sorted(b.cycles or [], key=lambda c: c.start)
            if b.ends_on is None or c.start <= b.ends_on
        )
        out.append(Benefit(b.key, b.account_key, b.name, b.ends_on, cycles, b.key not in match_off))
    return sorted(out, key=lambda b: b.key)


def home_cycle(b: Benefit, day: str) -> Cycle | None:
    """The cycle that holds the day, else the latest one that ended within 7
    days before it (a late post)."""
    for c in b.cycles:
        if c.start <= day <= c.end:
            return c
    late = [c for c in b.cycles if c.end < day <= _plus(c.end, LATE_DAYS)]
    return max(late, key=lambda c: c.end) if late else None


def is_covered(span: tuple[str, str] | None, cycle: Cycle) -> bool:
    """The card's current statements (first start, last end) run from the cycle
    start or earlier through its end plus 7 days."""
    return span is not None and span[0] <= cycle.start and _plus(cycle.end, LATE_DAYS) <= span[1]


def row_sig(description: str, merchant: str | None) -> str:
    """A row's imported identity in a choice's guard: sha256 hex of its full
    description and its imported merchant. A hash, so no text is stored."""
    text = f"{description}\x1f{merchant or ''}"
    return hashlib.sha256(text.encode()).hexdigest()


def _guard(decision: Mapping[str, Any]) -> dict[str, Any]:
    guard = json.loads(decision["guard_json"])
    return guard if isinstance(guard, dict) else {}


def _sig(row: CreditRow) -> str:
    return row_sig(row.description, row.imported_merchant)


def _guard_near(guard: Mapping[str, Any]) -> Mapping[str, str] | None:
    """The rows live near the chosen one when it was saved (txn_key ->
    row_sig), or None when the guard cannot prove anything: no row_sig, or no
    well formed near map."""
    near = guard.get("near")
    if not isinstance(guard.get("row_sig"), str) or not isinstance(near, dict):
        return None
    return near if all(isinstance(s, str) for s in near.values()) else None


def _same_row(guard: Mapping[str, Any], sig: str | None) -> bool:
    """The live row under a choice's key (its row_sig) is the row the choice
    was saved on."""
    return sig is not None and _guard_near(guard) is not None and guard["row_sig"] == sig


def _guard_day(guard: Mapping[str, Any]) -> str | None:
    day = guard.get("txn_date")
    try:
        return date.fromisoformat(day).isoformat() if isinstance(day, str) else None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Reading (inside the overlay's read transaction)
# --------------------------------------------------------------------------


def _widen(spans: dict[str, list[str]], card: str, lo: str, hi: str) -> None:
    span = spans.setdefault(card, [lo, hi])
    span[0], span[1] = min(span[0], lo), max(span[1], hi)


def load_rows(
    conn: sqlite3.Connection,
    benefits: Sequence[Benefit],
    decisions: Sequence[Mapping[str, Any]],
    today: date,
) -> Rows:
    """The candidates, the purchases a return would offset, each card's
    statement coverage and what each choice's key holds now. The caller holds
    the read transaction."""
    day = today.isoformat()
    year_start = f"{today.year}-01-01"
    reads: dict[str, list[str]] = {}  # card -> [first day, last day] of candidates
    cover: dict[str, list[str]] = {}  # card -> the span of its cycles, end plus 7 days
    for b in benefits:
        _widen(reads, b.account_key, year_start, day)
        for c in b.cycles:
            late = _plus(c.end, LATE_DAYS)
            _widen(cover, b.account_key, c.start, late)
            if b.matches:
                _widen(reads, b.account_key, c.start, late)
    for d in decisions:
        guard = _guard(d)
        card, at = guard.get("account_key"), _guard_day(guard)
        if isinstance(card, str) and at is not None:
            _widen(reads, card, _plus(at, -REFIND_DAYS), _plus(at, REFIND_DAYS))
    credits: list[CreditRow] = []
    for card, (lo, hi) in sorted(reads.items()):
        hi = min(hi, day)
        if lo <= hi:
            credits += [
                CreditRow(
                    r["txn_key"],
                    r["account_key"],
                    r["txn_date"],
                    r["amount_cents"],
                    r["description"],
                    r["merchant"],
                    r["imported_merchant"],
                    r["desc_norm"],
                    r["trip_key"],
                    r["run_id"],
                    r["statement_key"],
                )
                for r in conn.execute(CANDIDATES_SQL, (card, lo, hi))
            ]
    credits.sort(key=_order)
    purchases: list[Purchase] = []
    for card in sorted({b.account_key for b in benefits}):
        amounts = sorted(
            {-r.cents for r in credits if r.account_key == card and year_start <= r.txn_date}
        )
        if not amounts:
            continue
        marks = ", ".join("?" for _ in amounts)
        purchases += [
            Purchase(card, r["txn_date"], -r["amount_cents"], r["imported_merchant"])
            for r in conn.execute(
                f"""SELECT txn_date, amount_cents, imported_merchant FROM v_transactions
                    WHERE account_key = ? AND txn_type = 'purchase' AND txn_date BETWEEN ? AND ?
                      AND amount_cents IN ({marks})
                    ORDER BY txn_date, txn_key""",
                (card, _plus(year_start, -RETURN_DAYS), day, *amounts),
            )
        ]
    coverage: dict[str, tuple[str, str]] = {}
    for card, (lo, hi) in sorted(cover.items()):
        row = conn.execute(COVERAGE_SQL, (card, lo, hi)).fetchone()
        if row is not None and row["lo"] is not None:
            coverage[card] = (row["lo"], row["hi"])
    keys = sorted({d["txn_key"] for d in decisions})
    held: dict[str, Held] = {}
    for i in range(0, len(keys), CHUNK):
        part = keys[i : i + CHUNK]
        for r in conn.execute(HELD_SQL.format(marks=", ".join("?" for _ in part)), part):
            live = r["removed_at"] is None
            sig = row_sig(r["description"], r["merchant"]) if live else None  # imported merchant
            held[r["txn_key"]] = Held(r["statement_key"], r["current_run"], sig)
    return Rows(tuple(credits), tuple(purchases), coverage, held)


# --------------------------------------------------------------------------
# Matching (pure)
# --------------------------------------------------------------------------


def _newer(run: str | None, guard_run: str) -> bool:
    """The run comes after the guard's row's run. The importer keeps the run on
    unchanged rows and sets the new one on changed or added rows, so the rows a
    later replace wrote carry its run."""
    return run is not None and is_run_id(run) and run_order_key(run) > run_order_key(guard_run)


def _refind(
    key: str,
    guard: Mapping[str, Any],
    held: Held | None,
    credits: Sequence[CreditRow],
    chosen: Collection[str],
) -> CreditRow | None:
    """The one best candidate for a choice whose row is gone or whose key holds
    another row now, or None. Only rows the replace wrote: in the key's
    statement and written by its current run. Rows live when the choice was
    saved are never it, even when that replace changed them: a key in the
    guard's near list, or the row_sig of a near row other than the chosen one
    (a row that only moved keys). Rows with a choice of their own are ranked
    too, so a decided twin never leaves the other one to a guess. A guard with
    no run takes the current run as it is. A guard with no row_sig or near map
    finds nothing."""
    card, at, since = guard.get("account_key"), _guard_day(guard), guard.get("run_id")
    near = _guard_near(guard)
    if at is None or near is None or held is None or held.run_id is None:
        return None
    if since is not None and not (isinstance(since, str) and is_run_id(since)):
        return None
    if since is not None and not _newer(held.run_id, since):
        return None  # no replace since the choice was saved
    moved = {sig for k, sig in near.items() if k != key}
    lo, hi = _plus(at, -REFIND_DAYS), _plus(at, REFIND_DAYS)
    ranked = sorted(
        (
            (rank, r.txn_key, r)
            for r in credits
            if r.account_key == card
            and r.statement_key == held.statement_key
            and r.run_id == held.run_id
            and lo <= r.txn_date <= hi
            and r.txn_key not in near
            and _sig(r) not in moved
            and (
                rank := rank_candidate(
                    guard,
                    {
                        "desc_norm": r.desc_norm,
                        "merchant": r.imported_merchant,
                        "amount_cents": r.cents,
                        "txn_date": r.txn_date,
                    },
                )
            )
            is not None
        ),
        key=lambda x: x[0],
        reverse=True,
    )
    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return None  # nothing like it, or a tie: lost, never a guess
    best = ranked[0][2]
    return None if best.txn_key in chosen else best  # decided already: lost


def winners(
    row: CreditRow, benefits: Iterable[Benefit], terms: Mapping[str, Sequence[str]]
) -> tuple[str, ...]:
    """The benefits on the row's card whose longest matching word is the
    longest: one wins, more are a tie. A benefit takes nothing dated before its
    first cycle starts, or more than 7 days after it ended."""
    text = ascii_upper(row.description)
    limit = _plus(row.txn_date, -LATE_DAYS)
    scores: dict[str, int] = {}
    for b in benefits:
        if b.account_key != row.account_key or not b.matches:
            continue
        if b.ends_on is not None and b.ends_on < limit:
            continue
        if b.cycles and row.txn_date < b.cycles[0].start:
            continue  # not started yet
        best = max((len(t) for t in terms.get(b.key, ()) if ascii_upper(t) in text), default=0)
        if best:
            scores[b.key] = best
    top = max(scores.values(), default=0)
    return tuple(sorted(k for k, v in scores.items() if v == top))


def _looks_like_return(row: CreditRow, purchases: Iterable[Purchase]) -> bool:
    if not row.imported_merchant:
        return False
    name, lo = row.imported_merchant.casefold(), _plus(row.txn_date, -RETURN_DAYS)
    return any(
        p.account_key == row.account_key
        and p.cents == row.cents
        and lo <= p.txn_date <= row.txn_date
        and (p.merchant or "").casefold() == name
        for p in purchases
    )


def _spill(
    b: Benefit, placed: dict[tuple[str, str], list[CreditRow]], pinned: Collection[str]
) -> set[tuple[str, str]]:
    """Move rows back one cycle while a cycle is over its amount (plan 4.7).
    Returns the cycles rows moved out of."""
    moved: set[tuple[str, str]] = set()
    for prev, cur in zip(b.cycles, b.cycles[1:], strict=False):
        here = placed.get((b.key, cur.start))
        if not here:
            continue
        there = placed.setdefault((b.key, prev.start), [])
        limit = _plus(prev.end, LATE_DAYS)
        for row in sorted(here, key=_order):
            if sum(r.cents for r in here) <= cur.cents:
                break
            if row.txn_key in pinned or row.txn_date > limit:
                continue
            if sum(r.cents for r in there) + row.cents <= prev.cents:
                here.remove(row)
                there.append(row)
                moved.add((b.key, cur.start))
    return moved


def match(
    benefits: Sequence[Benefit],
    terms: Mapping[str, Sequence[str]],
    decisions: Sequence[Mapping[str, Any]],
    rows: Rows,
    today: date,
) -> Match:
    """Choices, words, home cycles, spill and the unmatched rows (plan 4.4 to
    4.9). `terms`: benefit key -> its match words."""
    day = today.isoformat()
    year_start = f"{today.year}-01-01"
    credits = sorted(rows.credits, key=_order)
    by_key = {r.txn_key: r for r in credits}
    counting = {b.key: b for b in benefits if b.matches}
    cards = {b.account_key for b in benefits}
    first: dict[str, str] = {}  # card -> its earliest counting cycle start
    for b in counting.values():
        for c in b.cycles[:1]:
            first[b.account_key] = min(first.get(b.account_key, c.start), c.start)
    chosen = {d["txn_key"] for d in decisions}
    flags: list[RowFlag] = []

    def wordable(row: CreditRow) -> bool:
        """Words may claim the row: on a card with a credit, dated up to today,
        and not before this year or the card's first counting cycle."""
        return (
            row.account_key in cards
            and row.txn_date <= day
            and row.txn_date >= min(year_start, first.get(row.account_key, year_start))
        )

    # Choices find their rows. A key that holds another row now re-finds like a
    # removed one. Two choices on one re-found row are both lost.
    found: dict[str, Mapping[str, Any]] = {}  # row key -> its choice
    refound: dict[str, list[Mapping[str, Any]]] = {}
    for d in sorted(decisions, key=lambda d: d["txn_key"]):
        key, guard, held = d["txn_key"], _guard(d), rows.held.get(d["txn_key"])
        if key in by_key and _same_row(guard, _sig(by_key[key])):
            found[key] = d
        elif key not in by_key and held is not None and _same_row(guard, held.row_sig):
            continue  # the row is live but no longer a candidate: nothing to count
        elif (row := _refind(key, guard, held, credits, chosen)) is not None:
            refound.setdefault(row.txn_key, []).append(d)
        else:
            flags.append(RowFlag("lost", key, d["benefit_key"]))
    for key, ds in sorted(refound.items()):
        if len(ds) == 1:
            found[key] = ds[0]
        else:
            flags += [RowFlag("lost", d["txn_key"], d["benefit_key"]) for d in ds]

    # Pinned links first, then unpinned links and words to their home cycles.
    # A choice that puts its row anywhere but its natural place (the home
    # cycle of each benefit whose words win or tie, a link's own home cycle)
    # decides that place.
    placed: dict[tuple[str, str], list[CreditRow]] = {}
    pinned: set[str] = set()
    homes: list[tuple[CreditRow, Benefit]] = []
    decided: set[tuple[str, str]] = set()
    for key, d in sorted(found.items()):
        row = by_key[key]
        b = counting.get(d["benefit_key"]) if d["decision"] == "link" else None
        if b is not None and b.account_key != row.account_key:
            b = None
        won = winners(row, counting.values(), terms) if wordable(row) else ()
        natural = {
            (nb.key, home.start)
            for nb in (*(counting[k] for k in won), *([b] if b is not None else []))
            if (home := home_cycle(nb, row.txn_date)) is not None
        }
        at: tuple[str, str] | None = None  # where the choice puts the row
        if b is None:
            # not_benefit, or a link to a credit that takes no rows here: counted nowhere
            if d["decision"] == "link" and year_start <= row.txn_date:
                flags.append(RowFlag("off", key, d["benefit_key"]))
        elif (cycle := next((c for c in b.cycles if c.start == d["cycle_start"]), None)) is None:
            homes.append((row, b))
            if (home := home_cycle(b, row.txn_date)) is not None:
                at = (b.key, home.start)
        else:
            at = (b.key, cycle.start)
            placed.setdefault(at, []).append(row)
            pinned.add(key)
        decided |= natural - {at}
    ambiguous: dict[str, tuple[str, ...]] = {}
    worded: set[str] = set()
    for row in credits:
        if row.txn_key in found or not wordable(row):
            continue  # a choice holds it, or it was read only for a re-find
        won = winners(row, counting.values(), terms)
        if len(won) == 1:
            homes.append((row, counting[won[0]]))
            worded.add(row.txn_key)
        elif won and year_start <= row.txn_date:  # a tie before this year: left alone
            ambiguous[row.txn_key] = won
    for row, b in homes:
        cycle = home_cycle(b, row.txn_date)
        if cycle is not None:
            placed.setdefault((b.key, cycle.start), []).append(row)
        elif year_start <= row.txn_date:  # before this year: counted nowhere, never flagged
            flags.append(RowFlag("no_cycle", row.txn_key, b.key))

    earlier: dict[str, str] = {}
    for b in counting.values():
        decided |= _spill(b, placed, pinned)
        for prev, cur in zip(b.cycles, b.cycles[1:], strict=False):
            limit = _plus(prev.end, LATE_DAYS)
            for row in placed.get((b.key, cur.start), []):
                if row.txn_key not in pinned and cur.start <= row.txn_date <= limit:
                    earlier[row.txn_key] = prev.start
    if decisions:
        # A cycle the words alone fill that the choices leave empty: a choice
        # moved its row, or stopped the spill that brought a late post there.
        words_only = match(benefits, terms, (), rows, today)
        decided |= {at for at in words_only.cycles if not placed.get(at)}

    review: list[ReviewItem] = []
    for row in credits:
        key = row.txn_key
        if key in ambiguous:
            review.append(ReviewItem(key, "review", ambiguous[key]))
            continue
        if key in found or key in worded or row.account_key not in cards:
            continue
        if not year_start <= row.txn_date <= day:
            continue
        if _looks_like_return(row, rows.purchases):
            review.append(ReviewItem(key, "returns"))
        else:
            review.append(ReviewItem(key, "trips" if row.trip_key else "review"))
    return Match(
        cycles={k: tuple(sorted(v, key=_order)) for k, v in sorted(placed.items()) if v},
        pinned=frozenset(pinned),
        earlier=earlier,
        flags=tuple(sorted(flags, key=lambda f: (f.kind, f.txn_key))),
        review=tuple(review),
        refound={k: d["txn_key"] for k, d in sorted(found.items()) if d["txn_key"] != k},
        decided=frozenset(decided),
    )


def best_guess(
    row: CreditRow,
    benefits: Iterable[Benefit],
    used: Mapping[tuple[str, str], int],
    options: Collection[str] = (),
) -> tuple[str, str] | None:
    """(benefit key, cycle start) for "Count it": a benefit on the row's card
    whose cycle at the row's date has room equal to the amount, then room of at
    least the amount, then the most name words in common. A tie goes to the
    first key. `used`: cents used per (benefit key, cycle start). `options`:
    for an ambiguous row, the benefits its words tie on."""
    words = name_words(row.description)
    best: tuple[tuple[bool, bool, int], tuple[str, str]] | None = None
    for b in sorted(benefits, key=lambda b: b.key):
        if b.account_key != row.account_key or not b.matches:
            continue
        if options and b.key not in options:
            continue
        cycle = home_cycle(b, row.txn_date)
        if cycle is None:
            continue
        room = cycle.cents - used.get((b.key, cycle.start), 0)
        score = (room == row.cents, room >= row.cents, len(words & name_words(b.name)))
        if best is None or score > best[0]:
            best = (score, (b.key, cycle.start))
    return best[1] if best else None
