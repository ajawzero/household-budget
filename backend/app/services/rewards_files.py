"""Benefit files (docs/plans/rewards-inputs.md section 5): update one card's
benefits from a JSON file (an issuer refresh), with a preview, an apply and a
template. Format 1.3 is unchanged: each item is a 1.3 RewardBenefit, plus an
optional app-only `match_terms`.

- The file: kind "budget-app-benefits", version 1, account_key (a credit or
  store card), starts_on (default household today), mode merge (only the
  benefits that change) or replace (every benefit on the card), and up to 50
  benefits. Merge mode takes none (a card with no benefits has an empty
  template, and it previews "No changes"). Replace mode needs at least 1, so
  an empty file never ends a whole card. Unknown fields are refused.
- Everything is checked before anything is written. A problem carries a place
  and a rule ("benefits[2].amount", "money-format") and fixed wording, never a
  value. An unknown field's name is input too, so it is never echoed, and an
  account_key that is not a card is never sent back.
- Each item, with match_terms taken off, passes RewardBenefit, then the
  in-app add's rules (FileItemIn, from BenefitIn): money like 12.00 under 10
  million, dates from 2000, a name of 100 characters that shows, at most 20
  cycles and 20 categories, and text with no control or bidi characters,
  spaces folded as one_line does. The folded text is what is scanned and
  stored. Dates stop at 2099-12-31, starts_on too, so the day math of
  matching never runs off the calendar. An item's account_key, when sent, is
  the file's. No key twice. Known categories, a shown program. used and
  used_on are refused (the app works out use). Cycles on a chat key are
  refused (cycles stay the chat's). New multi_year and one_time items need
  cycles. Calendar cadences carry none. Every text, key, made key and match
  word is scanned with the rewards rules and the private lists, field by
  field.
- Keys: a chat key on this card is a change: an edit row with only the fields
  that differ from what the page shows, the chat's value of each as base, and
  a changed amount starting on starts_on. An amount set back to the one
  before an earlier change, while that change starts before starts_on, is a
  problem: one start cannot hold three amounts. A chat key on another card
  fails the file. An app key on this card (sent as app-<key>, or as <key>)
  takes the new fields. A new key is stored as app-<key>, cut to fit 40
  characters, or app-<key>-2 and on when app-<key> is taken (another card's
  app benefit, or a key an input holds).
- A file never changes a benefit's kind or cadence, on a chat key or an app
  key: the item is a problem at its kind or cadence. That is done in the app:
  End this benefit, then Add a benefit.
- An added benefit starts on starts_on: its body keeps the start
  (rewards_overlay.APP_START), so cycles that end before it are left out, the
  one that holds it starts on it, and Fees count nothing of it in a year
  before it, its counted_value too. A multi_year or one_time credit keeps the
  start only when starts_on is in a later year, and then needs a cycle that
  ends on or after it. From a day this year it keeps the cycles the file
  gives, as an add in the app does. New cycles on an app benefit that keeps a
  start need one that ends on or after that start too.
- An item with match_terms replaces that benefit's words (an empty list clears
  them). Without it, the words stay. Only credits take words: another kind
  can send only an empty list. The template lists words on credits only.
- Replace mode: every other benefit the page shows on the card that runs past
  the day before starts_on ends then. Hidden benefits are left alone: they
  are out of every total already, and the template leaves them out.
- Preview writes nothing. It lists to add, to change (field names, and the
  start of a changed amount), to end, the unchanged count and the problems,
  with base_sig: a sha256 of the card's benefits as the page shows them, its
  edit rows and their words. Apply sends the same file plus base_sig. In one
  write transaction it checks the file again and recomputes base_sig. A
  mismatch is a 409. Then it writes all or nothing, with one events row that
  holds counts only.
- The template is the card's shown benefits in file form: no used, used_on or
  chat cycles, current words included, hidden ones left out, starts_on today.
  Sent back unchanged it previews "No changes".
- The file is never written to disk or logged.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import PydanticCustomError

from app.api.rewards_models import EARLIEST, BenefitIn
from app.importer.models import (
    KEY_MAX,
    KEY_PATTERN,
    RULES,
    Date,
    Key,
    RewardBenefit,
    Sha256,
    iter_text,
)
from app.importer.sanitize import PrivateRules, scan_text
from app.services import rewards
from app.services import rewards_inputs as ri
from app.services import rewards_match as rm
from app.services import rewards_overlay as ov
from app.services.overrides import UNKNOWN_CATEGORY, EditError

FILE_KIND = "budget-app-benefits"
FILE_MAX_BYTES = 256 * 1024
ITEMS_MAX = 50
UNKNOWN_FIELD = "(unknown field)"
WHOLE_FILE = "(file)"
TOO_BIG = "This file is too big."
STALE = "The benefits changed since you checked the file. Check it again."
HAS_PROBLEMS = "The file has problems. Check it again."
NOT_A_CARD = "No credit or store card with that key."
# File dates stop here. A date near the calendar's end would break the day
# math of matching (a cycle end plus 7 days).
LATEST = date(2099, 12, 31)
# Rule ids of the file, next to the importer's RULES. Plain text, never a value.
FILE_RULES = {
    "file-items": "A file holds up to 50 benefits, and at least 1 to replace a card's list.",
    "file-card": "account_key must be a credit or store card.",
    "file-other-card": "An item's account_key must be the file's.",
    "file-key-twice": "Each benefit can be in the file once.",
    "file-key-other-card": "This key is a benefit on another card.",
    "file-key-taken": "This key is in use. Pick another key.",
    "file-category": "This category is not known.",
    "file-program": "This program is not on the page.",
    "upload-used": "Leave used and used_on out. The app works out use.",
    "file-chat-cycles": "Cycles of a benefit the chat sends stay the chat's. Leave them out.",
    "file-needs-cycles": "Multi-year and one-time credits need cycles, and one must end on or "
    "after the day the credit starts.",
    "file-no-cycles": "Monthly, quarterly, semi-annual and annual credits take no cycles.",
    "cycle-starts": "Each cycle needs its own start date.",
    "file-cycles": "A benefit lists 1 to 20 cycles.",
    "file-categories": "A benefit names at most 20 categories.",
    "money-format": "Money is dollars and cents, like 12.00, under 10 million.",
    "date-range": "Use a real date from 2000-01-01 to 2099-12-31.",
    "name-format": "A name is up to 100 characters that show, with no control characters.",
    "text-format": "Text is up to 500 characters, with no control or direction characters.",
    "terms-format": "match_terms is a list of up to 10 match words.",
    "terms-credit": "Only credits match statement rows.",
    "term-format": rm.TERM_FORMAT,
    "term-generic": rm.TERM_GENERIC,
    "term-twice": ri.TERM_TWICE,
    "edit-no-fit": ri.NO_FIT_FIRST,
    "amount-start": ri.AMOUNT_LATER,
    "amount-back": "The amount goes back to the one before your change, and that change "
    "starts earlier. Start this on that day or earlier.",
    "file-kind-change": "A file cannot change a benefit's cadence or kind. End it and add the "
    "new one in the app.",
}
# An EditError from the shared checks -> (rule, the field it is about).
EDIT_RULES = {
    ri.BAD_FIT: ("reward-benefit-fields", None),
    ri.NO_FIT_FIRST: ("edit-no-fit", None),
    ri.AMOUNT_LATER: ("amount-start", "amount"),
    ri.UNKNOWN_PROGRAM: ("file-program", "program_key"),
    UNKNOWN_CATEGORY: ("file-category", "category_keys"),
    ri.CYCLE_STARTS: ("cycle-starts", "cycles"),
}
CYCLE_FIELDS = ("start", "end", "amount")
# A field the in-app rules (FileItemIn) refuse -> its rule.
IN_RULES = {
    "name": "name-format",
    "cycles": "file-cycles",
    "category_keys": "file-categories",
    **dict.fromkeys(("amount", "counted_value", "cap", "reported_spent"), "money-format"),
    **dict.fromkeys(("enroll_by", "ends_on", "as_of", "start", "end"), "date-range"),
    **dict.fromkeys(
        ("how_to_use", "note", "earn_before", "earn_after", "status_tier"), "text-format"
    ),
}


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------


def _in_range(v: str) -> str:
    if not EARLIEST.isoformat() <= v <= LATEST.isoformat():
        raise PydanticCustomError("date-range", FILE_RULES["date-range"])
    return v


FileDate = Annotated[Date, AfterValidator(_in_range)]


class BenefitFile(BaseModel):
    """A card's benefits file, as sent to preview. Items are checked one by
    one, so each problem can name its place."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["budget-app-benefits"]
    version: Literal[1]
    account_key: Key = Field(description="A credit or store card.")
    starts_on: FileDate | None = Field(
        default=None,
        description="When the changes start, 2000-01-01 to 2099-12-31. Default: today. Changed "
        "amounts and added benefits start on it. An added multi-year or one-time credit keeps "
        "the cycles it gives, unless starts_on is in a later year. Replace mode ends the rest "
        "the day before.",
    )
    mode: Literal["merge", "replace"] = Field(
        default="merge",
        description="merge: only the benefits that change. replace: every benefit on the "
        "card; the rest end the day before starts_on.",
    )
    benefits: list[dict[str, Any]] = Field(
        max_length=ITEMS_MAX,
        description="Format 1.3 benefits, plus an optional app-only match_terms list. "
        "Replace mode needs at least 1.",
    )


class BenefitFileApply(BenefitFile):
    """The same file, plus the base_sig its preview gave."""

    base_sig: Sha256


class FileItemIn(BenefitIn):
    """An item held to the in-app add's rules, after RewardBenefit: money under
    10 million, dates from 2000, a name that shows, one-line text with no
    control or bidi characters, and the cycle and category caps. match_terms
    are checked apart."""

    key: Key


@dataclass(frozen=True)
class Problem:
    """A place in the file and a rule, with fixed wording. Never a value."""

    where: str
    rule: str
    message: str


class FileProblems(Exception):
    """The file has problems. Carries places and rules only."""

    def __init__(self, problems: Sequence[Problem]) -> None:
        super().__init__(HAS_PROBLEMS)
        self.problems = list(problems)


def _problem(where: str, rule: str, message: str | None = None) -> Problem:
    text = message or FILE_RULES.get(rule) or RULES.get(rule) or RULES["schema"]
    return Problem(where, rule, text)


def _place(prefix: str, loc: Sequence[str | int]) -> str:
    """("cycles", 0, "amount") under "benefits[2]" -> "benefits[2].cycles[0].amount"."""
    out = prefix
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part != UNKNOWN_FIELD and ("[" in part or " " in part):
            continue  # a pydantic branch name
        else:
            out += f".{part}" if out else part
    return out or WHOLE_FILE


def _errors(exc: ValidationError, prefix: str = "") -> list[Problem]:
    """A ValidationError as places and rules. Input values and unknown field
    names are dropped."""
    out = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = list(err["loc"])
        kind = err["type"]
        if kind == "extra_forbidden" and loc:
            loc[-1] = UNKNOWN_FIELD
        if not prefix and not loc:  # not JSON, or not an object
            out.append(_problem(WHOLE_FILE, "json"))
        elif not prefix and loc == ["benefits"] and kind in ("too_short", "too_long"):
            out.append(_problem("benefits", "file-items"))
        else:
            known = kind in RULES or kind in FILE_RULES
            out.append(_problem(_place(prefix, loc), kind if known else "schema"))
    return list(dict.fromkeys(out))


def _in_errors(exc: ValidationError, where: str) -> list[Problem]:
    """FileItemIn's errors as places and rules, the rule named by the field."""
    out = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = list(err["loc"])
        rule = IN_RULES.get(str(loc[-1]) if loc else "", "schema")
        out.append(_problem(_place(where, loc), rule))
    return list(dict.fromkeys(out))


def _late(item: FileItemIn) -> list[str]:
    """The places of the item's dates after LATEST."""
    days = [(f, getattr(item, f)) for f in ("enroll_by", "ends_on", "as_of")]
    for j, c in enumerate(item.cycles or []):
        days += [(f"cycles[{j}].start", c.start), (f"cycles[{j}].end", c.end)]
    return [at for at, day in days if day is not None and day > LATEST]


def _edit_problem(where: str, exc: EditError) -> Problem:
    if exc.status != 422:
        raise exc
    rule, at = EDIT_RULES.get(exc.message, ("edit", None))
    return Problem(f"{where}.{at}" if at else where, rule, exc.message)


def _parse[F: BenefitFile](raw: bytes, model: type[F]) -> tuple[F | None, list[Problem]]:
    try:
        return model.model_validate_json(raw), []
    except ValidationError as exc:
        return None, _errors(exc)


# --------------------------------------------------------------------------
# Reading the card
# --------------------------------------------------------------------------


def _is_card(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT type FROM accounts WHERE account_key = ?", (key,)).fetchone()
    return row is not None and row["type"] in rewards.CARD_TYPES


def _card_name(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT display_name FROM accounts WHERE account_key = ?", (key,)).fetchone()
    return None if row is None else row["display_name"]


def _words(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Every key's saved match words, A to Z."""
    out: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT benefit_key, term FROM reward_match_terms ORDER BY benefit_key, upper(term), term"
    ):
        out.setdefault(r[0], []).append(r[1])
    return out


def _same_words(a: Sequence[str], b: Sequence[str]) -> bool:
    def order(t: str) -> tuple[str, str]:
        return rm.ascii_upper(t), t

    return sorted(a, key=order) == sorted(b, key=order)


def _hidden(st: ri.Benefits, key: str) -> bool:
    row = st.rows.get(key)
    return row is not None and bool(row["hidden"])


def _clean(item: Mapping[str, Any]) -> dict[str, Any]:
    """An item in file form: no nulls, categories sorted once each, cycles by
    start with start, end and amount only."""
    out = {f: v for f, v in item.items() if v is not None}
    if out.get("category_keys") is not None:
        out["category_keys"] = sorted(set(out["category_keys"]))
    if "cycles" in out:
        cycles = [{f: c[f] for f in CYCLE_FIELDS if c.get(f) is not None} for c in out["cycles"]]
        out["cycles"] = sorted(cycles, key=lambda c: c["start"])
    return out


def _form(st: ri.Benefits, key: str) -> dict[str, Any]:
    """A shown benefit as the template writes it. An app benefit is its stored
    body (its cycles without the pinned amounts). A chat benefit is what the
    page shows, without the chat's cycles."""
    row = st.app(key)
    if row is not None:
        item = json.loads(row["fields_json"]) | {"key": key, "account_key": row["account_key"]}
        item.pop(ov.APP_START, None)  # the app's own, not a file field
        if item.get("cadence") not in ov.STORED_CYCLES:
            item.pop("cycles", None)
    else:
        item = dict(st.item(key) or {})
        item.pop("cycles", None)
    return _clean(item)


def _on_card(st: ri.Benefits, card: str) -> list[str]:
    """The shown benefits on the card, hidden ones too, by key."""
    return [k for k in sorted(st.shown()) if (st.item(k) or {}).get("account_key") == card]


def base_sig(conn: sqlite3.Connection, st: ri.Benefits, card: str) -> str:
    """sha256 of the card's benefits as the page shows them, its edit rows and
    their match words."""
    items = {k: st.item(k) for k in _on_card(st, card)}
    rows = {
        k: {c: r[c] for c in r.keys() if c not in ("updated_by", "updated_at")}
        for k, r in sorted(st.rows.items())
        if r["account_key"] == card
    }
    words = _words(conn)
    blob = {
        "benefits": items,
        "rows": rows,
        "terms": {k: words.get(k, []) for k in sorted(set(items) | set(rows))},
    }
    text = json.dumps(blob, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


# --------------------------------------------------------------------------
# Checking the items
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    index: int
    benefit: RewardBenefit
    form: dict[str, Any]  # the item in file form
    terms: list[str] | None  # checked match words; None when left out

    @property
    def where(self) -> str:
        return f"benefits[{self.index}]"


def _check_terms(
    value: Any, where: str, private: PrivateRules | None
) -> tuple[list[str] | None, list[Problem]]:
    at = f"{where}.match_terms"
    if (
        not isinstance(value, list)
        or len(value) > ri.TERMS_MAX
        or not all(isinstance(t, str) for t in value)
    ):
        return None, [_problem(at, "terms-format")]
    clean: list[str] = []
    out: list[Problem] = []
    seen: set[str] = set()
    for j, term in enumerate(value):
        here = f"{at}[{j}]"
        try:
            text = rm.check_term(term)
        except EditError as exc:
            out.append(
                _problem(here, "term-generic" if exc.message == rm.TERM_GENERIC else "term-format")
            )
            continue
        found = scan_text(text, private, ri.SECTION)
        if found:
            out += [_problem(here, rule) for rule in found]
            continue
        if rm.ascii_upper(text) in seen:
            out.append(_problem(here, "term-twice"))
            continue
        seen.add(rm.ascii_upper(text))
        clean.append(text)
    return clean, out


def _check_item(
    raw: dict[str, Any],
    index: int,
    card: str,
    known: tuple[set[str], set[str]],
    private: PrivateRules | None,
) -> tuple[Item | None, list[Problem]]:
    """One item on its own: RewardBenefit, the in-app rules, use, scans,
    categories, program and match words. `known`: the shown programs and the
    active categories."""
    where = f"benefits[{index}]"
    data = dict(raw)
    terms_value = data.pop("match_terms", None)  # null counts as left out
    out: list[Problem] = []
    if "account_key" not in data:
        data["account_key"] = card
    elif data["account_key"] != card:
        out.append(_problem(f"{where}.account_key", "file-other-card"))
    if isinstance(data.get("cycles"), list):
        for j, c in enumerate(data["cycles"]):
            for name in ("used", "used_on"):
                if isinstance(c, dict) and name in c:
                    out.append(_problem(f"{where}.cycles[{j}].{name}", "upload-used"))
    try:
        RewardBenefit.model_validate(data)
    except ValidationError as exc:
        return None, out + _errors(exc, where)
    if isinstance(data.get("cycles"), list):  # use is refused above
        data["cycles"] = [
            {f: v for f, v in c.items() if f not in ("used", "used_on")} for c in data["cycles"]
        ]
    try:
        sent = FileItemIn.model_validate(data)
    except ValidationError as exc:
        return None, out + _in_errors(exc, where)
    late = [_problem(f"{where}.{at}", "date-range") for at in _late(sent)]
    if late:
        return None, out + late
    try:  # the folded text, as it would be stored
        b = RewardBenefit.model_validate(sent.model_dump(mode="json", exclude_none=True))
    except ValidationError as exc:
        return None, out + _errors(exc, where)
    for loc, text in iter_text(b):
        out += [_problem(f"{where}.{loc}", rule) for rule in scan_text(text, private, ri.SECTION)]
    programs, categories = known
    if b.program_key is not None and b.program_key not in programs:
        out.append(_problem(f"{where}.program_key", "file-program"))
    for j, k in enumerate(b.category_keys or []):
        if k not in categories:
            out.append(_problem(f"{where}.category_keys[{j}]", "file-category"))
    starts = [c.start for c in b.cycles or []]
    if len(set(starts)) != len(starts):
        out.append(_problem(f"{where}.cycles", "cycle-starts"))
    terms = None
    if terms_value is not None:
        terms, found = _check_terms(terms_value, where, private)
        out += found
        if b.kind != "credit" and terms_value != []:  # an empty list clears stray words
            out.append(_problem(f"{where}.match_terms", "terms-credit"))
    form = _clean(b.model_dump(mode="json", exclude_none=True))
    return Item(index, b, form, terms), out


def _cycle_rules(form: Mapping[str, Any], where: str) -> list[Problem]:
    """A new body: multi_year and one_time need cycles, calendar cadences carry none."""
    stored = form.get("cadence") in ov.STORED_CYCLES
    if stored and not form.get("cycles"):
        return [_problem(f"{where}.cycles", "file-needs-cycles")]
    if not stored and "cycles" in form:
        return [_problem(f"{where}.cycles", "file-no-cycles")]
    return []


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


@dataclass
class Step:
    """One benefit to add, change or end."""

    key: str
    origin: str  # chat or app
    account_key: str
    name: str
    kind: str
    v: dict[str, Any] | None = None  # the edit row to save; None: only words change
    fields: tuple[str, ...] = ()
    amount_from: str | None = None
    terms: list[str] | None = None  # the words to save; None: they stay
    ends_on: str | None = None


@dataclass
class Plan:
    starts_on: str
    adds: list[Step] = field(default_factory=list)
    changes: list[Step] = field(default_factory=list)
    ends: list[Step] = field(default_factory=list)
    unchanged: int = 0
    problems: list[Problem] = field(default_factory=list)


def _cut(key: str) -> str:
    return key[:KEY_MAX].rstrip("-")


class _Planner:
    """Works out the plan for a checked file. Nothing is written."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        st: ri.Benefits,
        f: BenefitFile,
        private: PrivateRules | None,
        today: date,
    ) -> None:
        self.conn, self.st, self.f, self.private, self.today = conn, st, f, private, today
        self.card = f.account_key
        self.plan = Plan(f.starts_on or today.isoformat())
        self.day_before = (date.fromisoformat(self.plan.starts_on) - timedelta(days=1)).isoformat()
        self.words = _words(conn)
        self.taken = set(st.chat) | ri.input_keys(conn) | set(ov.benefit_origins(st.programs.runs))
        self.claimed: set[str] = set()  # keys the file's items land on, new keys too

    # -- keys ---------------------------------------------------------------

    def _made_key_ok(self, key: str, where: str) -> bool:
        """A key the app makes: a valid key, and scanned like typed text."""
        if re.fullmatch(KEY_PATTERN, key) is None:
            self.plan.problems.append(_problem(f"{where}.key", "key-format"))
            return False
        found = scan_text(key, self.private, ri.SECTION)
        self.plan.problems += [_problem(f"{where}.key", rule) for rule in found]
        return not found

    def _app_here(self, key: str) -> bool:
        row = self.st.app(key)
        return row is not None and row["account_key"] == self.card

    def _new_key(self, base: str) -> str:
        """app-<base>, then app-<base>-2 and on, cut to fit: an app benefit on
        this card, or a key nothing holds."""
        for n in range(1, ri.SUFFIX_MAX + 1):
            suffix = "" if n == 1 else f"-{n}"
            key = ri.APP_PREFIX + base[: KEY_MAX - len(ri.APP_PREFIX) - len(suffix)]
            key = key.rstrip("-") + suffix
            if key in self.claimed or key in self.st.chat:
                continue
            if self._app_here(key) or key not in self.taken:
                return key
        raise EditError(422, ri.TOO_MANY)

    # -- steps --------------------------------------------------------------

    def _new_words(self, item: Item, key: str) -> list[str] | None:
        if item.terms is None or _same_words(item.terms, self.words.get(key, [])):
            return None
        return item.terms

    def _amount_back(self, key: str, v: Mapping[str, Any], where: str) -> bool:
        """An amount set back to the one before any change clears that change's
        start, so every cycle since it would go back too. When that start is
        before starts_on, one start cannot hold the three amounts: a problem."""
        row = self.st.rows.get(key)
        was = None if row is None else row["amount_from"]
        if was is None or v["amount_from"] is not None or was >= self.plan.starts_on:
            return False
        self.plan.problems.append(_problem(f"{where}.amount", "amount-back"))
        return True

    def _no_cycles_left(self, after: Mapping[str, Any], where: str) -> bool:
        """A multi_year or one_time benefit whose cycles all end before its own
        start (APP_START) would show none and count nothing: a problem."""
        if after.get("cadence") in ov.STORED_CYCLES and not after.get("cycles"):
            self.plan.problems.append(_problem(f"{where}.cycles", "file-needs-cycles"))
            return True
        return False

    def _add(self, item: Item, key: str) -> None:
        """A new app benefit. It keeps starts_on as its start, so it counts
        nothing before it (see rewards_overlay.app_benefit). A multi_year or
        one_time one keeps it only from a later year: this year it keeps the
        cycles the file gives, as an add in the app does, so rows before
        starts_on still match and Fees count each cycle in its own year."""
        body = item.form | {"key": key, "account_key": self.card}
        problems = _cycle_rules(body, item.where)
        if problems:
            self.plan.problems += problems
            return
        later = self.plan.starts_on > f"{self.today.year}-12-31"
        if later or body.get("cadence") not in ov.STORED_CYCLES:
            body[ov.APP_START] = self.plan.starts_on
        after = ov.app_benefit(key, self.card, body, None, None, self.today.year)
        if self._no_cycles_left(after, item.where):
            return
        ri.check_benefit(self.conn, self.st, after)
        v = ri.row_state(None) | {"fields": body}
        step = Step(key, "app", self.card, body["name"], body["kind"], v, terms=item.terms or None)
        self.plan.adds.append(step)

    def _replace_app(self, item: Item, key: str) -> None:
        """An existing app benefit on this card takes the item's fields. Its
        kind and cadence are the item's (see item()). New cycles on a body
        with its own start need one that ends on or after it."""
        form = item.form | {"key": key}
        problems = _cycle_rules(form, item.where)
        if problems:
            self.plan.problems += problems
            return
        cur = _form(self.st, key)
        diff = tuple(f for f in sorted(set(form) | set(cur)) if form.get(f) != cur.get(f))
        words = self._new_words(item, key)
        if not diff and words is None:
            self.plan.unchanged += 1
            return
        v = None
        if diff:
            start = self.plan.starts_on if "amount" in diff else None
            changes = {f: form.get(f) for f in diff}
            v, after = ri.changed_benefit(
                self.conn, self.st, key, changes, start=start, today=self.today
            )
            if self._no_cycles_left(after, item.where):
                return
            if "amount" in diff and self._amount_back(key, v, item.where):
                return
        amount_from = v["amount_from"] if v is not None and "amount" in diff else None
        self.plan.changes.append(
            Step(key, "app", self.card, form["name"], form["kind"], v, diff, amount_from, words)
        )

    def _change_chat(self, item: Item, key: str) -> None:
        """A chat benefit on this card: only the fields that differ change."""
        if "cycles" in item.form:
            self.plan.problems.append(_problem(f"{item.where}.cycles", "file-chat-cycles"))
            return
        form = item.form
        cur = _form(self.st, key)
        diff = tuple(f for f in sorted(set(form) | set(cur)) if form.get(f) != cur.get(f))
        words = self._new_words(item, key)
        if not diff and words is None:
            self.plan.unchanged += 1
            return
        v = None
        if diff:
            start = self.plan.starts_on if "amount" in diff else None
            changes = {f: form.get(f) for f in diff}
            v, _ = ri.changed_benefit(
                self.conn, self.st, key, changes, start=start, today=self.today
            )
            if "amount" in diff and self._amount_back(key, v, item.where):
                return
        amount_from = v["amount_from"] if v is not None and "amount" in diff else None
        self.plan.changes.append(
            Step(key, "chat", self.card, form["name"], form["kind"], v, diff, amount_from, words)
        )

    def _end(self, key: str, where: str) -> None:
        """End a shown benefit the day before starts_on, unless it ends by then."""
        item = self.st.item(key) or {}
        if item.get("ends_on") is not None and item["ends_on"] <= self.day_before:
            return
        try:
            v, _ = ri.changed_benefit(
                self.conn, self.st, key, {"ends_on": self.day_before}, start=None, today=self.today
            )
        except EditError as exc:
            self.plan.problems.append(_edit_problem(where, exc))
            return
        origin = "app" if self.st.app(key) is not None else "chat"
        step = Step(key, origin, self.card, item["name"], item["kind"], v, ("ends_on",))
        step.ends_on = self.day_before
        self.plan.ends.append(step)

    def _kind_changed(self, item: Item, key: str) -> bool:
        """A file never changes a benefit's kind or cadence: a problem at the
        first that differs from the one the page shows. the owner does that in the
        app (End this benefit, then Add a benefit)."""
        cur = self.st.item(key) or {}
        for name in ("kind", "cadence"):
            if item.form.get(name) != cur.get(name):
                self.plan.problems.append(_problem(f"{item.where}.{name}", "file-kind-change"))
                return True
        return False

    def item(self, item: Item) -> None:
        key, where = item.benefit.key, item.where
        stored = key
        if key not in self.st.chat and not key.startswith(ri.APP_PREFIX):
            # An app benefit on this card sent as <key>, or a new key: app-<key>,
            # else the next free one. Another card's app-<key> is not this one.
            plain = _cut(ri.APP_PREFIX + key)
            if plain in self.claimed:
                self.plan.problems.append(_problem(f"{where}.key", "file-key-twice"))
                return
            try:
                stored = plain if self._app_here(plain) else self._new_key(key)
            except EditError as exc:
                self.plan.problems.append(_edit_problem(where, exc))
                return
        if stored in self.claimed:
            self.plan.problems.append(_problem(f"{where}.key", "file-key-twice"))
            return
        app = self.st.app(stored)
        sent = self.st.chat.get(stored) if app is None else None
        owner = app["account_key"] if app is not None else (sent or {}).get("account_key")
        if owner is not None and owner != self.card:
            self.plan.problems.append(_problem(f"{where}.key", "file-key-other-card"))
            return
        if app is None and sent is None:
            if stored in self.taken:
                self.plan.problems.append(_problem(f"{where}.key", "file-key-taken"))
                return
            if not self._made_key_ok(stored, where):
                return
        self.claimed.add(stored)
        if (app is not None or sent is not None) and self._kind_changed(item, stored):
            return
        try:
            if app is not None:
                self._replace_app(item, stored)
            elif sent is None:
                self._add(item, stored)
            else:
                self._change_chat(item, stored)
        except EditError as exc:
            self.plan.problems.append(_edit_problem(where, exc))

    def ends(self) -> None:
        """Replace mode: every other shown benefit on the card ends the day
        before starts_on. Hidden ones are left alone."""
        for key in _on_card(self.st, self.card):
            if key in self.claimed or _hidden(self.st, key):
                continue
            self._end(key, "mode")


def _review(
    conn: sqlite3.Connection,
    st: ri.Benefits,
    f: BenefitFile,
    private: PrivateRules | None,
    today: date,
) -> Plan:
    """Every check, then the plan. Nothing is written."""
    problems: list[Problem] = []
    if f.mode == "replace" and not f.benefits:  # never end a whole card by an empty file
        problems.append(_problem("benefits", "file-items"))
    card_ok = _is_card(conn, f.account_key)
    if not card_ok:
        problems.append(_problem("account_key", "file-card"))
    programs = st.programs.keys()
    categories = {
        r[0] for r in conn.execute("SELECT category_key FROM categories WHERE active = 1")
    }
    items: list[Item] = []
    for i, raw in enumerate(f.benefits):
        item, found = _check_item(raw, i, f.account_key, (programs, categories), private)
        problems += found
        if item is not None and not found:
            items.append(item)
    if not card_ok:
        return Plan(f.starts_on or today.isoformat(), problems=list(dict.fromkeys(problems)))
    # The items that passed still go through the keys, so one check lists every
    # problem. The rest of the card ends only for a clean file.
    planner = _Planner(conn, st, f, private, today)
    for item in items:
        planner.item(item)
    if f.mode == "replace" and not problems:
        planner.ends()
    plan = planner.plan
    plan.problems = list(dict.fromkeys(problems + plan.problems))
    return plan


# --------------------------------------------------------------------------
# Preview, apply and the template
# --------------------------------------------------------------------------


def _out(conn: sqlite3.Connection, f: BenefitFile | None, plan: Plan) -> dict:
    """The preview. Lists stay empty while the file has problems, so nothing
    from a refused item goes back."""
    card = f.account_key if f is not None and _is_card(conn, f.account_key) else None
    clean = not plan.problems
    return {
        "account_key": card,
        "account_name": _card_name(conn, card) if card is not None else None,
        "mode": f.mode if f is not None else None,
        "starts_on": plan.starts_on or None,
        "to_add": [
            {"key": s.key, "name": s.name, "kind": s.kind, "terms": bool(s.terms)}
            for s in (plan.adds if clean else [])
        ],
        "to_change": [
            {
                "key": s.key,
                "name": s.name,
                "fields": list(s.fields),
                "amount_from": s.amount_from,
                "terms": s.terms is not None,
            }
            for s in (plan.changes if clean else [])
        ],
        "to_end": [
            {"key": s.key, "name": s.name, "ends_on": s.ends_on}
            for s in (plan.ends if clean else [])
        ],
        "unchanged": plan.unchanged if clean else 0,
        "problems": [
            {"where": p.where, "rule": p.rule, "message": p.message} for p in plan.problems
        ],
        "base_sig": None,
        "no_changes": clean and not (plan.adds or plan.changes or plan.ends),
    }


def preview(
    conn: sqlite3.Connection, raw: bytes, *, private: PrivateRules | None, today: date
) -> dict:
    """What the file would do. Nothing is written. A 409 before the first 1.3 run."""
    with ov.read_transaction(conn):
        st = ri.benefits_state(conn, today)
        f, problems = _parse(raw, BenefitFile)
        if f is None:
            return _out(conn, None, Plan("", problems=problems))
        plan = _review(conn, st, f, private, today)
        out = _out(conn, f, plan)
        if not plan.problems:
            out["base_sig"] = base_sig(conn, st, f.account_key)
    return out


def apply(
    conn: sqlite3.Connection,
    raw: bytes,
    *,
    private: PrivateRules | None,
    today: date,
    user_id: int | None,
    now: str,
) -> dict[str, int]:
    """Check the file again and write it, all or nothing, inside the caller's
    write_transaction. FileProblems when it has problems, a 409 when the
    benefits changed since its preview. Returns the counts."""
    if not conn.in_transaction:
        raise RuntimeError("rewards inputs are written inside write_transaction")
    st = ri.benefits_state(conn, today)  # the 409 before the first 1.3 run comes first
    f, problems = _parse(raw, BenefitFileApply)
    if f is None:
        raise FileProblems(problems)
    if not _is_card(conn, f.account_key):
        raise FileProblems([_problem("account_key", "file-card")])
    if base_sig(conn, st, f.account_key) != f.base_sig:
        raise EditError(409, STALE)
    plan = _review(conn, st, f, private, today)
    if plan.problems:
        raise FileProblems(plan.problems)
    words = 0
    for step in (*plan.ends, *plan.changes, *plan.adds):
        if step.v is not None:
            ri.save_benefit_row(
                conn,
                step.key,
                step.origin,
                step.account_key,
                step.v,
                user_id=user_id,
                now=now,
            )
        if step.terms is not None:
            ri.replace_terms(conn, step.key, step.terms, user_id=user_id, now=now)
            words += 1
    counts = {
        "added": len(plan.adds),
        "changed": len(plan.changes),
        "ended": len(plan.ends),
        "unchanged": plan.unchanged,
    }
    saved = counts["added"] + counts["changed"] + counts["ended"]
    if saved:
        detail = {"action": "apply", **counts, "terms": words}
        ri.record_event(conn, user_id, now, "file", f.account_key, detail)
    return {"saved": saved, **counts}


def template(conn: sqlite3.Connection, account_key: str, *, today: date) -> dict:
    """The card's shown benefits in file form, hidden ones left out. A 404 for
    a key that is not a card, a 409 before the first 1.3 run."""
    with ov.read_transaction(conn):
        st = ri.benefits_state(conn, today)
        if not _is_card(conn, account_key):
            raise EditError(404, NOT_A_CARD)
        words = _words(conn)
        items = []
        for key in _on_card(st, account_key):
            if _hidden(st, key):
                continue
            form = _form(st, key)
            if words.get(key) and form["kind"] == "credit":  # only credits take words
                form["match_terms"] = words[key]
            items.append(form)
    return {
        "kind": FILE_KIND,
        "version": 1,
        "account_key": account_key,
        "starts_on": today.isoformat(),
        "mode": "merge",
        "benefits": items,
    }
