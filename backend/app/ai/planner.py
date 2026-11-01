"""The trip planner (docs/plans/trip-planner.md).

A plan, and each follow-up, is two Claude calls:

1. Research. It has Claude's web search tool and gets only the trip request
   (place, dates, how many travelers, home airport, interests, pace, lean)
   plus the earlier research notes and the follow-up text. No names, no
   rewards, no cards: the typed note and changes lose the household's names,
   program, benefit, card and account names and points balances first
   (`Redact`), so nothing in it can carry household data into a search.
   The notes come back with their sources (search results and citations).
2. The plan. It has no tools at all. It gets the request, the rewards brief,
   the research notes and the sources by id, and returns JSON (structured
   output). It never writes a URL: it names source ids, and the app turns them
   into links from the research call's own search results.

The app does the sums (value per point, balances, free night dates, nights
with no lodging, the way home, cost totals), so they are always right.

A plan runs in the background, one at a time for the household. Its turns
(request, research, plan, notice) are only added, never changed. Text the
person types gets the question checks. Every request is scrubbed (private
names, last fours) and every text field of a plan goes through the answer
filter before it is stored. Logs hold counts and exception classes only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import uuid
from collections import Counter
from collections.abc import Callable, Collection, Coroutine, Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote_plus, urlsplit

import pydantic
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import config
from app.ai import client as ai_client
from app.ai import ledger
from app.ai import settings as ai_settings
from app.ai.postprocess import MASK, PRIVATE_LABEL, Scrub, filter_text, load_last4s
from app.ai.pricing import estimate_micro_usd
from app.ai.prompts import PLAN_RESEARCH_RULES, PLAN_WRITE_RULES
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.demo.context import in_demo
from app.importer.runner import private_rules_for
from app.importer.sanitize import PrivateRules, detect_sensitive, normalize
from app.services.rewards import rewards_page

log = logging.getLogger("app.ai")

# The newest web search tool: it filters results before Claude reads them.
# In parts: scan_repo blocks long digit runs.
SEARCH_TOOL = "_".join(("web_search", "2026" + "0318"))
MAX_SEARCHES = 10  # a new plan's research
FOLLOW_UP_SEARCHES = 5
# pause_turn resumes per research turn. Each resume is its own call. It sends
# the same tools (a changed tool list breaks the paused turn's thinking), so
# a turn resumes only while it has run fewer searches than its limit: at most
# twice the limit less one in all.
MAX_CONTINUATIONS = 3
FOLLOW_UP_LIMIT = 10
RESEARCH_MAX_TOKENS = 16_000
WRITE_MAX_TOKENS = 32_000
# Estimates before a call (the caps check them). Search results count as input.
RESEARCH_PROMPT_EST = 30_000
RESEARCH_OUTPUT_EST = 6_000
WRITE_PROMPT_EST = 20_000
WRITE_OUTPUT_EST = 12_000
NOTES_MAX = 40_000  # characters of research notes kept per turn
SOURCES_PER_TURN = 30
URL_MAX = 500
TITLE_MAX = 200
MAX_NIGHTS = 30

BUSY_TEXT = "A trip plan is being made. Try again when it is done."
RESTART_TEXT = "Stopped when the app restarted. Try again."
REFUSED_TEXT = "Claude would not plan this trip. Try other words."
EMPTY_TEXT = "The research found nothing to plan with. Try other words or dates."
LONG_TEXT = "The plan came out too long. Try fewer nights or a smaller change."
LIMIT_TEXT = f"A plan takes {FOLLOW_UP_LIMIT} changes. Start a new plan for more."

Interest = Literal[
    "food",
    "history",
    "museums",
    "art",
    "nature",
    "hiking",
    "beaches",
    "nightlife",
    "shopping",
    "family",
    "sports",
    "music",
    "relaxing",
]
LEAN_WORDS = {"value": "best value", "cash": "cash", "points": "points"}
ItemKind = Literal[
    "flight",
    "lodging",
    "rental_car",
    "gas",
    "food",
    "activities",
    "tickets",
    "shopping",
    "insurance",
    "parking",
    "transit",
    "cash",
    "supplies",
    "fees",
    "other",
]


class PlanError(Exception):
    """A request the planner refuses. `message` is shown as is."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class PlanFailed(Exception):
    """A turn that ended without a plan. `message` is shown as is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    where: str = Field(min_length=1, max_length=120, description="The place, as typed.")
    start_date: date | None = None
    end_date: date | None = None
    month: str | None = Field(
        default=None,
        pattern=r"^20\d\d-(0[1-9]|1[0-2])$",
        description="YYYY-MM, with nights, when the dates are open.",
    )
    nights: int | None = Field(default=None, ge=1, le=MAX_NIGHTS)
    people: list[str] = Field(default_factory=list, max_length=12, description="Person keys.")
    guests: int = Field(default=0, ge=0, le=20, description="People outside the household.")
    home_airport: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    interests: list[Interest] = Field(default_factory=list, max_length=13)
    note: str = Field(default="", max_length=300)
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    lean: Literal["cash", "points", "value"] = "value"

    @model_validator(mode="after")
    def _shape(self) -> PlanRequest:
        if self.start_date is not None or self.end_date is not None:
            if self.start_date is None or self.end_date is None or self.month is not None:
                raise ValueError("Give a start and an end date, or a month and nights.")
            n = (self.end_date - self.start_date).days
            if not 1 <= n <= MAX_NIGHTS or self.nights not in (None, n):
                raise ValueError("A trip is 1 to 30 nights.")
            self.nights = n
        elif self.month is None or self.nights is None:
            raise ValueError("Give dates, or a month and nights.")
        if len(set(self.people)) != len(self.people) or len(set(self.interests)) != len(
            self.interests
        ):
            raise ValueError("A person or interest is there twice.")
        if not self.people and not self.guests:
            raise ValueError("Pick who is going.")
        return self


def _clean(text: str, private: PrivateRules | None, what: str) -> str:
    """Typed text as it will be sent: the Ask question checks."""
    text = re.sub(r"\s+", " ", normalize(text)).strip()
    if detect_sensitive(text):
        raise PlanError(
            f"The {what} looks like it has an account or card number, a phone number, an "
            "email or an address. Leave that part out."
        )
    if private:
        for start, end in reversed(private.spans(text)):
            text = text[:start] + PRIVATE_LABEL + text[end:]
    return text


def _first_night(req: dict) -> date:
    if req.get("start_date"):
        return date.fromisoformat(req["start_date"])
    return date.fromisoformat(f"{req['month']}-01")


def checked_request(
    conn: sqlite3.Connection, raw: PlanRequest, private: PrivateRules | None
) -> dict:
    """The request as stored and sent, or PlanError."""
    today = config.household_today()
    where = _clean(raw.where, private, "place")
    if not where:
        raise PlanError("Type where you want to go.")
    note = _clean(raw.note, private, "note")
    if raw.start_date is not None:
        if raw.start_date < today:
            raise PlanError("Pick dates from today on.")
        if raw.start_date > today + timedelta(days=730):
            raise PlanError("Pick dates within two years.")
    elif raw.month is not None:
        if raw.month < today.strftime("%Y-%m"):
            raise PlanError("Pick a month from this one on.")
        if raw.month > (today + timedelta(days=730)).strftime("%Y-%m"):
            raise PlanError("Pick a month within two years.")
    known = {r[0] for r in conn.execute("SELECT person_key FROM people")}
    if any(p not in known for p in raw.people):
        raise PlanError("Pick people from the household.")
    out = raw.model_dump(mode="json")
    out["where"] = where
    out["note"] = note
    out["people"] = sorted(raw.people)
    return out


def follow_up_text(text: str, private: PrivateRules | None) -> str:
    text = _clean(text, private, "change")
    if not text:
        raise PlanError("Type what to change.")
    if len(text) > 1000:
        raise PlanError("Keep the change under 1,000 characters.")
    return text


# Words in program and account names that say nothing about the household.
_GENERIC = frozenset(
    """the and of for my our your points point rewards reward miles mile hotel hotels
    card cards club cash back travel credit store checking savings account bank plus
    preferred premier elite gold silver platinum blue green red black annual yearly
    fee airline vacation member members honors joint loan mortgage van car home house
    family shared camper boat kids college trust ira roth hsa 401k retirement brokerage
    investment investments business personal everyday primary emergency fund""".split()
)
_WORDS = re.compile(r"[^\W_]+")


def _either(terms: Iterable[str]) -> re.Pattern | None:
    """Any of the terms as whole words, any case, any spacing or punctuation."""
    parts = sorted(
        {r"\W+".join(map(re.escape, _WORDS.findall(t))) for t in terms if _WORDS.search(t)},
        key=len,
        reverse=True,
    )
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.I) if parts else None


class Redact:
    """What the research call must not see in typed text, each part becoming
    "…": the household's names (people, aliases, users), its program,
    benefit, card and account names, the distinctive words of program and
    account names (Summit, Cobalt), and points balances (84,250 with any
    grouping or none, 84.25k, and any balance before "points" or "miles").
    The plan call gets the text whole.

    The place loses names, whole rewards and account names and balances. In
    the note and the changes, the place's own parts stay as they are ("Hike
    near Pine Mountain" for Pine Mountain), so a place that shares a word with
    a program or a bank keeps working."""

    def __init__(
        self,
        names: Iterable[str],
        phrases: Iterable[str],
        words: Iterable[str],
        balances: Iterable[int],
    ) -> None:
        names = [n for n in names if _WORDS.search(n)]
        phrases = [p for p in phrases if _WORDS.search(p)]
        self.names = _either([*names, *(w for n in names for w in _WORDS.findall(n) if len(w) > 1)])
        self.phrases = _either(p for p in phrases if len(_WORDS.findall(p)) > 1)
        self.words = _either(
            [
                *(w for w in words if len(w) > 1 and not w.isdigit()),
                *(p for p in phrases if len(_WORDS.findall(p)) == 1),
            ]
        )
        self.numbers = _balances([n for n in balances if n > 0])

    def _out(self, text: str, *patterns: re.Pattern | None) -> str:
        for pattern in patterns:
            if pattern is not None:
                text = pattern.sub(MASK, text)
        return text

    def place(self, text: str) -> str:
        return self._out(text, self.names, self.phrases, *self.numbers)

    def text(self, value: str, place: str) -> str:
        parts = [
            p.strip()
            for p in _PLACE_SPLIT.split(self.place(place))
            if len(p.strip()) > 2 and _WORDS.search(p)
        ]
        kept: list[str] = []
        guard = _either(parts)
        if guard is not None:

            def keep(m: re.Match) -> str:
                kept.append(m.group(0))
                return chr(0xE000 + len(kept) - 1)  # a private-use character, not a word

            value = guard.sub(keep, value)
        value = self._out(value, self.names, self.phrases, self.words, *self.numbers)
        for i, part in enumerate(kept):
            value = value.replace(chr(0xE000 + i), part)
        return value


# Where a place splits into the parts the note may name ("Pine Mountain").
_PLACE_SPLIT = re.compile(r"[,;/()…]|\b(?:with|and|near|in|to|for|from|then|via|plus|or)\b", re.I)


def _balances(balances: list[int]) -> list[re.Pattern]:
    """Patterns for points balances: the number written out with any grouping
    (1,000 and up), in thousands with a k, and any balance before a points cue."""
    full, thousands, cued = set(), set(), set()
    for n in balances:
        cued.add(str(n))
        if n < 1000:
            continue
        grouped = f"{n:,}"
        for sep in (",", ".", " ", "\u00a0", "\u202f", "'"):
            full.add(grouped.replace(",", sep))
        full.add(str(n))
        k = Decimal(n) / 1000
        for d in (0, 1, 2):
            for rounding in (ROUND_HALF_UP, ROUND_FLOOR):
                thousands.add(str(k.quantize(Decimal(1).scaleb(-d), rounding=rounding)))
    cued |= full
    out = []

    def either(forms: set[str]) -> str:
        return "|".join(map(re.escape, sorted(forms, key=len, reverse=True)))

    if full:
        out.append(re.compile(rf"(?<![\d,.])(?:{either(full)})(?![\d,]|\.\d)"))
    if thousands:
        out.append(re.compile(rf"(?<![\d,.])(?:{either(thousands)})\s?[kK](?![^\W\d_])"))
    if cued:
        out.append(
            re.compile(
                rf"(?<![\d,.])(?:{either(cued)})(?=\s*(?:points?|pts|miles?|nights?)\b)", re.I
            )
        )
    return out


NO_REDACT = Redact((), (), (), ())


def household(conn: sqlite3.Connection) -> Redact:
    """The Redact for this household: names from people, users and accounts;
    every program, benefit, perk, cap and card name and every balance from the
    Rewards page (more than the plan call's brief holds)."""
    names: list[str] = []
    for r in conn.execute("SELECT display_name, aliases_json, role FROM people"):
        if r["role"] == "joint":  # "Joint" is not a name
            continue
        names.append(r["display_name"])
        try:
            aliases = json.loads(r["aliases_json"] or "[]")
        except ValueError:
            aliases = []
        names += [a for a in aliases if isinstance(a, str)] if isinstance(aliases, list) else []
    names += [r[0] for r in conn.execute("SELECT display_name FROM users")]
    accounts = [r[0] for r in conn.execute("SELECT display_name FROM accounts")]
    page = rewards_page(conn)
    programs = [p["name"] for p in page["programs"]]
    benefits = [
        b.get("name") or ""
        for group in ("benefits", "perks", "caps", "milestones")
        for b in page[group]
    ]
    cards = [
        b.get("account_name") or ""
        for group in ("benefits", "perks", "caps", "routing")
        for b in page[group]
    ]
    words = [
        w
        for n in (*programs, *accounts, *cards)
        for w in _WORDS.findall(n)
        if len(w) > 2 and w.casefold() not in _GENERIC
    ]
    balances = []
    for p in page["programs"]:
        if p["kind"] != "cash_back" and p["balance_micro"] is not None:
            balances.append(int(p["balance_micro"]) // 1_000_000)
        for a in p.get("allocations") or []:
            if a.get("left_micro") is not None:
                balances.append(int(a["left_micro"]) // 1_000_000)
    return Redact(names, [*programs, *benefits, *cards, *accounts], words, balances)


def research_input(
    req: dict, notes: list[str], follow_ups: list[str], redact: Redact = NO_REDACT
) -> str:
    """The research call's whole input: the request, with household details
    taken out of what was typed. No names, no rewards."""
    place = req["where"]
    lines = [f"Place: {redact.place(place)}"]
    if req.get("start_date"):
        lines.append(f"Dates: {req['start_date']} to {req['end_date']}, {req['nights']} nights")
    else:
        lines.append(f"Month: {req['month']}, {req['nights']} nights, dates open")
    lines.append(f"Travelers: {len(req['people']) + req['guests']}")
    if req.get("home_airport"):
        lines.append(f"Home airport: {req['home_airport']}")
    else:
        lines.append("Home airport: not given. Skip flights.")
    if req["interests"]:
        lines.append("Interests: " + ", ".join(req["interests"]))
    if req["note"]:
        lines.append(f"Their note: {redact.text(req['note'], place)}")
    lines.append(f"Pace: {req['pace']}. Lean: {LEAN_WORDS[req['lean']]}.")
    text = "Trip request\n" + "\n".join(lines)
    if follow_ups:
        if notes:
            text += "\n\nEarlier research notes\n" + "\n\n".join(notes)[-NOTES_MAX:]
        text += "\n\nChanges asked for, oldest first\n" + "\n".join(
            f"- {redact.text(f, place)}" for f in follow_ups
        )
        text += "\n\nSearch only for what the newest change needs. Then write notes for it."
    return text


# --------------------------------------------------------------------------
# The plan's shape (structured output)
# --------------------------------------------------------------------------

Ids = list[str]
SOURCE_IDS = Field(default_factory=list, description='Source ids like "S3" that back this.')


class PointsUse(BaseModel):
    item: str = Field(description="What it pays for, like Round-trip flights for 2.")
    kind: Literal["flight", "lodging", "rental_car", "activities", "other"]
    program: str = Field(description="One of the household's programs, or empty.")
    points: int = Field(description="Points or miles for the whole party. 0 for a benefit.")
    taxes_usd: int = Field(description="Cash taxes and fees paid with the points.")
    cash_price_usd: int = Field(description="The cash price of the same thing.")
    benefit: str = Field(description="A free night or travel credit used, or empty.")
    source_ids: Ids = SOURCE_IDS
    note: str = ""


class FlightOption(BaseModel):
    label: str
    route: str
    when: str
    cash_low_usd: int = Field(description="Round trip for the whole party.")
    cash_high_usd: int
    program: str = Field(description="A program for an award, or empty.")
    points: int = 0
    taxes_usd: int = 0
    source_ids: Ids = SOURCE_IDS
    note: str = ""


class Hotel(BaseModel):
    name: str
    area: str
    nightly_low_usd: int
    nightly_high_usd: int
    program: str = Field(description="The household program it belongs to, or empty.")
    points_per_night: int = 0
    free_night_ok: bool = False
    why: str = ""
    source_ids: Ids = SOURCE_IDS


class Ground(BaseModel):
    mode: Literal["rental_car", "transit", "train", "taxi", "walk", "drive", "other"]
    note: str
    low_usd: int
    high_usd: int
    source_ids: Ids = SOURCE_IDS


class DayItem(BaseModel):
    part: Literal["morning", "afternoon", "evening"]
    kind: Literal["sight", "meal", "activity", "travel", "rest"]
    title: str
    note: str = ""
    low_usd: int = 0
    high_usd: int = 0
    source_ids: Ids = SOURCE_IDS


class Day(BaseModel):
    day: int
    date: str = Field(description="YYYY-MM-DD, or empty when the dates are open.")
    base: str = Field(description="Where they sleep that night. Empty on the last day.")
    lodging: str = Field(description="The place to stay that night. Empty on the last day.")
    travel: str = Field(description="The day's legs, like a flight or a train, or empty.")
    items: list[DayItem]


class CostLine(BaseModel):
    kind: ItemKind
    low_usd: int
    high_usd: int
    note: str = ""


class PlanOut(BaseModel):
    title: str = Field(description="A short name, like Lisbon in May.")
    summary: str = Field(description="Two or three sentences.")
    points_plan: list[PointsUse]
    flights: list[FlightOption]
    lodging: list[Hotel]
    ground: list[Ground]
    days: list[Day]
    costs: list[CostLine]
    checks: list[str] = Field(description="Things to confirm before booking.")
    link_ids: Ids = Field(description="The sources most worth opening to check and book.")


LIMITS = {
    "points_plan": 12,
    "flights": 3,
    "lodging": 3,
    "ground": 6,
    "days": MAX_NIGHTS + 1,
    "items": 8,
    "costs": 15,
    "checks": 10,
    "link_ids": 12,
    "source_ids": 6,
}
USD_MAX = 1_000_000
POINTS_MAX = 10_000_000
# Strings that are ids or fixed words, never filtered.
_NOT_TEXT = {"kind", "part", "mode", "date", "source_ids", "link_ids"}
# Strings that name a program or a benefit of the brief (enums in the format).
_NAMES = ("program", "benefit")


def _label(entries: list[dict], hints: tuple[str, ...]) -> None:
    """Give same-named entries their own names, from the first hint they have
    (an owner, a program, a card), else a number. The plan names them."""
    counts = Counter(e["name"] for e in entries)
    used: set[str] = set()
    for e in entries:
        name = e["name"]
        hint = next((e[h] for h in hints if e.get(h)), None)
        if counts[name] > 1 and hint:
            name = f"{name} ({hint})"
        label, n = name, 1
        while label in used:
            n += 1
            label = f"{name} {n}"
        used.add(label)
        e["name"] = label


def plan_brief(brief: dict) -> dict:
    """The brief the plan call gets (already scrubbed): program and benefit names
    made unique, so each name is one entry and the sums match it."""
    out = json.loads(json.dumps(brief))
    _label(out["programs"], ("owner",))
    _label([*out["free_nights"], *out["travel_credits"]], ("program", "card"))
    return out


def enum_names(brief: dict) -> dict[str, set[str]]:
    return {
        "program": {p["name"] for p in brief["programs"]},
        "benefit": {b["name"] for b in (*brief["free_nights"], *brief["travel_credits"])},
    }


# The plan's shape, as a JSON schema in the plan call's system prompt. Not as
# structured output: the API refuses to compile a grammar for a shape this
# large. tidy() blanks program and benefit names that are not the brief's.
PLAN_SHAPE = json.dumps(ai_client.json_format(PlanOut)["schema"], separators=(",", ":"))
PLAN_SYSTEM = f"{PLAN_WRITE_RULES}\n\nThe JSON schema of the plan:\n{PLAN_SHAPE}"


def plan_out(message: Any) -> PlanOut | None:
    """The reply as a plan, or None. Without structured output the reply can
    wrap the object in a code fence or a sentence, with braces of its own
    ("fly from {ORD}"), so this takes the first whole JSON object that is a plan."""
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    decoder = json.JSONDecoder()
    at = text.find("{")
    while at >= 0:
        try:
            obj, _end = decoder.raw_decode(text, at)
            return PlanOut.model_validate(obj)
        except ValueError:  # not JSON here, or JSON that is not a plan
            at = text.find("{", at + 1)
    return None


def unknown_benefits(plan: PlanOut, names: dict[str, set[str]]) -> set[int]:
    """The points uses that name a benefit the brief does not have. tidy()
    blanks the name, so derive() is told which uses they were."""
    return {
        i
        for i, use in enumerate(plan.points_plan)
        if use.benefit and use.benefit not in names["benefit"]
    }


def _int(value: Any, top: int) -> int:
    return max(0, min(int(value), top))


# A link in free text: a scheme, www., or a host with a path or query. Links
# come only from the research call's search results, by source id.
_URLISH = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*:/+|\bwww\.)\S*"
    r"|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/?#:]\S*)?(?!\w)"
)


def tidy(
    plan: PlanOut,
    known_ids: set[str],
    private: PrivateRules | None,
    names: dict[str, set[str]],
) -> dict:
    """The plan as stored: lists cut to size, numbers in range, source ids that
    exist, program and benefit names that are in the brief (`enum_names`), and
    every text field with any link masked and through the answer filter."""

    def walk(node: Any, key: str = "") -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in ("source_ids", "link_ids"):
                    ids = [i for i in dict.fromkeys(v) if i in known_ids]
                    out[k] = ids[: LIMITS[k]]
                elif isinstance(v, list):
                    out[k] = [walk(x, k) for x in v[: LIMITS.get(k, 50)]]
                else:
                    out[k] = walk(v, k)
            for low, high in (
                ("cash_low_usd", "cash_high_usd"),
                ("nightly_low_usd", "nightly_high_usd"),
                ("low_usd", "high_usd"),
            ):
                if low in out and high in out and out[low] > out[high]:
                    out[low], out[high] = out[high], out[low]
            return out
        if isinstance(node, bool):
            return node
        if isinstance(node, int):
            return _int(node, POINTS_MAX if key in ("points", "points_per_night") else USD_MAX)
        if isinstance(node, str):
            if key in _NOT_TEXT:
                return node[:20]
            if key in _NAMES:
                return node if node in names[key] else ""
            # Links masked after the filter: its NFKC pass would spell "…" out.
            return _URLISH.sub(MASK, filter_text(node[:2000], private)).strip()
        return node

    out = walk(plan.model_dump(mode="json"))
    for i, day in enumerate(out["days"], start=1):
        day["day"] = i
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day["date"]):
            day["date"] = ""
    return out


# --------------------------------------------------------------------------
# The rewards brief (the plan call only)
# --------------------------------------------------------------------------

_TRAVEL = re.compile(
    r"travel|airline|air\b|flight|hotel|lodging|stay|ride|uber|lyft|rental|rail|train|"
    r"transit|parking|bag|lounge|global entry|tsa|cruise|vacation",
    re.IGNORECASE,
)
_MICRO = Decimal(1_000_000)


def _whole(micro: int | None, kind: str) -> int | str | None:
    if micro is None:
        return None
    value = Decimal(micro) / _MICRO
    if kind == "cash_back":
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return int(value)


def rewards_brief(conn: sqlite3.Connection) -> dict:
    """What the plan call sees of the household's rewards (the owner's approved list):
    programs with balance, value range, status, expiry, best uses and avoid;
    free nights; travel credits left this cycle; status perks; card routing.
    No account numbers, transactions, account balances, income, budgets,
    milestones, fees or imported trips."""
    page = rewards_page(conn)
    programs = []
    for p in page["programs"]:
        row: dict[str, Any] = {
            "name": p["name"],
            "kind": p["kind"],
            "balance": _whole(p["balance_micro"], p["kind"]),
            "balance_as_of": p["as_of"],
            "cents_per_point_low": p["cpp_low"],
            "cents_per_point_high": p["cpp_high"],
            "status": p["status_tier"],
            "owner": p["owner_name"],
            "expires_on": p["expires_on"],
            "best_uses": p["best_uses"],
            "avoid": p["avoid"],
        }
        if p["kind"] == "timeshare":
            row["allocations"] = [
                {
                    "year": a["year"],
                    "left": _whole(a["left_micro"], "points"),
                    "expires_on": a["expires_on"],
                    "book_by": a["book_by"],
                }
                for a in p["allocations"] or []
            ]
        programs.append(row)
    free_nights, credits = [], []
    for b in page["benefits"]:
        if b["group"] == "ended" or b["hidden"]:
            continue
        if b["kind"] == "free_night":
            free_nights.append(
                {
                    "name": b["name"],
                    "program": b["program_name"],
                    "ends_on": b["ends_on"],
                    "how_to_use": b["how_to_use"],
                }
            )
        elif b["kind"] == "credit" and _TRAVEL.search(f"{b['name']} {b['how_to_use'] or ''}"):
            cycle = b["cycle"] or {}
            left = cycle.get("left_cents")
            if left == 0:
                continue
            credits.append(
                {
                    "name": b["name"],
                    "card": b["account_name"],
                    "left_usd": None if left is None else f"{left / 100:.2f}",
                    "cycle_ends": cycle.get("end"),
                    "how_to_use": b["how_to_use"],
                }
            )
    return {
        "as_of": page["today"],
        "programs": programs,
        "free_nights": free_nights,
        "travel_credits": credits,
        "status": [
            {"name": s["name"], "program": s["program_name"], "status": s["status_tier"]}
            for s in page["perks"]
        ],
        "card_routing": [
            {"spend": r["spend"], "card": r["account_name"], "earn": r["earn"]}
            for r in page["routing"]
        ],
    }


# --------------------------------------------------------------------------
# What the app works out
# --------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _cpp(cash: int, taxes: int, points: int) -> Decimal | None:
    """Cents per point: (cash price less the taxes paid with points) / points."""
    if points <= 0 or cash <= taxes:
        return None
    value = Decimal(cash - taxes) * 100 / Decimal(points)
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _has_travel(day: dict) -> bool:
    return bool(day["travel"].strip()) or any(i["kind"] == "travel" for i in day["items"])


def _have(program: dict, first: date) -> int | str | None:
    """The points to spend: the balance, or a timeshare's points left for the
    trip's year. A cash-back balance is dollars, so it is never compared."""
    if program["kind"] == "timeshare":
        left = [
            a["left"]
            for a in program.get("allocations") or []
            if a["year"] == first.year and isinstance(a["left"], int)
        ]
        return sum(left) if left else None
    return program["balance"]


def _night_list(nights: list[int]) -> str:
    """3, 4, 5, 7 -> "3 to 5 and 7"."""
    runs: list[list[int]] = []
    for n in nights:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    parts = [str(a) if a == b else f"{a} to {b}" for a, b in runs]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


CHECKS_PER_KIND = 5


def derive(plan: dict, req: dict, brief: dict, unknown: Collection[int] = ()) -> dict:
    """Value per point, balances, the night bar, the checks and the cost totals.
    `unknown`: the points uses whose benefit was not the brief's (unknown_benefits)."""
    programs = {p["name"]: p for p in brief["programs"]}
    free_nights = {b["name"]: b for b in brief["free_nights"]}
    credits = {b["name"]: b for b in brief["travel_credits"]}
    first = _first_night(req)
    checks: list[dict] = []

    points, needed = [], {}
    for i, use in enumerate(plan["points_plan"]):
        p = programs.get(use["program"])
        cpp = _cpp(use["cash_price_usd"], use["taxes_usd"], use["points"])
        low = _decimal(p["cents_per_point_low"]) if p else None
        high = _decimal(p["cents_per_point_high"]) if p else None
        band = None
        if cpp is not None and low is not None and high is not None:
            band = "below" if cpp < low else "above" if cpp > high else "within"
        points.append(
            {
                "value_cents_per_point": None if cpp is None else str(cpp),
                "range": band,
                "yours": p is not None,
            }
        )
        if use["points"] > 0:
            if p is None:
                checks.append(
                    {
                        "kind": "points_not_yours",
                        "text": f"{use['item']} uses points that are not in your rewards.",
                    }
                )
            else:
                needed[p["name"]] = needed.get(p["name"], 0) + use["points"]
        if i in unknown:
            checks.append(
                {
                    "kind": "points_not_yours",
                    "text": f"{use['item']} uses a benefit that is not in your rewards.",
                }
            )
        night = free_nights.get(use["benefit"])
        if night and night["ends_on"] and date.fromisoformat(night["ends_on"]) < first:
            checks.append(
                {
                    "kind": "free_night_expires",
                    "text": f"The {night['name']} ends {night['ends_on']}, before the trip.",
                }
            )
        credit = credits.get(use["benefit"])
        if credit and credit["cycle_ends"]:
            checks.append(
                {
                    "kind": "credit_use_by",
                    "text": f"Use the {credit['name']} by {credit['cycle_ends']}.",
                }
            )

    balances = []
    for name, need in needed.items():
        have = _have(programs[name], first)
        enough = have >= need if isinstance(have, int) else None
        balances.append({"program": name, "needed": need, "have": have, "enough": enough})
        if enough is False:
            checks.append(
                {
                    "kind": "not_enough_points",
                    "text": f"{name}: the plan uses {need:,} points and the balance is {have:,}.",
                }
            )

    days = plan["days"]
    nights, empty = [], []
    for n in range(1, req["nights"] + 1):
        day = days[n - 1] if n <= len(days) else None
        stay = day["lodging"].strip() if day else ""
        when = (first + timedelta(days=n - 1)).isoformat() if req.get("start_date") else None
        nights.append({"night": n, "date": when, "lodging": stay or None})
        if not stay:
            empty.append(n)
    if len(empty) == 1:
        when = nights[empty[0] - 1]["date"]
        on = f" ({when})" if when else ""
        checks.append({"kind": "no_lodging", "text": f"Night {empty[0]}{on} has no place to stay."})
    elif empty:
        checks.append(
            {"kind": "no_lodging", "text": f"Nights {_night_list(empty)} have no place to stay."}
        )
    if not days or not _has_travel(days[-1]):
        checks.append({"kind": "no_way_home", "text": "The plan has no way home on the last day."})
    for prev, day in zip(days, days[1:], strict=False):
        a, b = prev["base"].strip(), day["base"].strip()
        if a and b and a.casefold() != b.casefold() and not _has_travel(day):
            checks.append(
                {
                    "kind": "no_travel",
                    "text": f"Day {day['day']} is in {b} with no travel from {a}.",
                }
            )

    # At most CHECKS_PER_KIND of a kind, so every kind found is shown.
    per_kind: Counter[str] = Counter()
    kept = []
    for c in checks:
        per_kind[c["kind"]] += 1
        if per_kind[c["kind"]] <= CHECKS_PER_KIND:
            kept.append(c)

    by_kind: dict[str, list[int]] = {}
    for line in plan["costs"]:
        sums = by_kind.setdefault(line["kind"], [0, 0])
        sums[0] += line["low_usd"] * 100
        sums[1] += line["high_usd"] * 100
    return {
        "points": points,
        "balances": balances,
        "nights": nights,
        "checks": kept,
        "costs": {
            "by_kind": [
                {"kind": k, "low_cents": v[0], "high_cents": v[1]} for k, v in by_kind.items()
            ],
            "low_cents": sum(v[0] for v in by_kind.values()),
            "high_cents": sum(v[1] for v in by_kind.values()),
        },
    }


# --------------------------------------------------------------------------
# Sources and links
# --------------------------------------------------------------------------


def _decoded(url: str) -> str:
    """The URL with its %-escapes and + read, as a page or a browser reads them."""
    for _ in range(3):
        out = unquote_plus(url)
        if out == url:
            break
        url = out
    return url


def private_in_url(url: str, private: PrivateRules) -> bool:
    """A private-list name in a link: as is, %-escaped, or in look-alike letters."""
    return bool(private.check(url)) or bool(private.spans(normalize(_decoded(url))))


_HOST = re.compile(r"[a-z0-9.-]+")


def safe_url(url: Any, private: PrivateRules | None) -> str | None:
    """A link the app may show: http or https, a plain ASCII host (so the site
    shown is the site it opens), no user name or password, at most URL_MAX
    characters, and no private-list name in it."""
    if not isinstance(url, str) or not url or len(url) > URL_MAX:
        return None
    if not url.isprintable() or any(c.isspace() for c in url) or "\\" in url:
        return None
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError:
        return None
    host = parts.hostname or ""
    if parts.scheme not in ("https", "http") or "." not in host.strip("."):
        return None
    if not _HOST.fullmatch(host):
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    if private and private_in_url(url, private):
        return None
    return url


def _site(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def notes_and_sources(
    blocks: list[Any], private: PrivateRules | None, first: int
) -> tuple[str, list[dict]]:
    """The research notes, each cited passage marked with its source ids, and the
    sources: cited ones first, then the other search results. Ids run on from
    `first` so they stay unique across a plan's turns."""
    sources: dict[str, dict] = {}

    def add(url: Any, title: Any, page_age: Any = None) -> str | None:
        url = safe_url(url, private)
        if url is None:
            return None
        if url in sources:
            if page_age and not sources[url]["page_age"]:
                sources[url]["page_age"] = str(page_age)[:40]
            return sources[url]["id"]
        if len(sources) >= SOURCES_PER_TURN:
            return None
        site = _site(url)
        name = filter_text(normalize(str(title or ""))[:TITLE_MAX], private).strip()
        sources[url] = {
            "id": f"S{first + len(sources)}",
            "url": url,
            "site": site,
            "title": name or site,
            "page_age": str(page_age)[:40] if page_age else None,
        }
        return sources[url]["id"]

    parts = []
    for b in blocks:
        if getattr(b, "type", "") != "text":
            continue
        ids: list[str] = []
        for c in getattr(b, "citations", None) or []:
            if getattr(c, "type", "") == "web_search_result_location":
                sid = add(getattr(c, "url", None), getattr(c, "title", None))
                if sid and sid not in ids:
                    ids.append(sid)
        body = b.text.rstrip()
        parts.append(body + (f" [{', '.join(ids)}]" if ids else "") + b.text[len(body) :])
    for b in blocks:
        if getattr(b, "type", "") == "web_search_tool_result" and isinstance(b.content, list):
            for r in b.content:
                add(
                    getattr(r, "url", None), getattr(r, "title", None), getattr(r, "page_age", None)
                )
    notes = filter_text("".join(parts).strip()[:NOTES_MAX], private)
    return notes, list(sources.values())


# --------------------------------------------------------------------------
# Storage (each helper opens its own connection; call them in a thread)
# --------------------------------------------------------------------------


def _iso(dt: datetime | None = None) -> str:
    return ai_settings.iso(dt or datetime.now(UTC))


def _add_turn(conn: sqlite3.Connection, plan_id: str, kind: str, content: Any) -> int:
    seq = int(
        conn.execute(
            "SELECT coalesce(max(seq), 0) + 1 FROM trip_plan_turns WHERE plan_id = ?", (plan_id,)
        ).fetchone()[0]
    )
    conn.execute(
        """INSERT INTO trip_plan_turns (plan_id, seq, kind, content_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (plan_id, seq, kind, json.dumps(content, ensure_ascii=False), _iso()),
    )
    return seq


def _turns(conn: sqlite3.Connection, plan_id: str) -> list[dict]:
    return [
        {"seq": r["seq"], "kind": r["kind"], "at": r["created_at"], **json.loads(r["content_json"])}
        for r in conn.execute(
            "SELECT * FROM trip_plan_turns WHERE plan_id = ? ORDER BY seq", (plan_id,)
        )
    ]


def _state(db_path: Path, plan_id: str) -> dict:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM trip_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise PlanFailed(ai_client.FAILED_TEXT)
        return {
            "model": row["model"],
            "request": json.loads(row["request_json"]),
            "turns": _turns(conn, plan_id),
            "travelers": _names(conn, json.loads(row["request_json"])["people"]),
        }
    finally:
        conn.close()


def _names(conn: sqlite3.Connection, keys: list[str]) -> list[dict]:
    names = {
        r["person_key"]: r["display_name"]
        for r in conn.execute("SELECT person_key, display_name FROM people")
    }
    return [{"key": k, "name": names.get(k, k)} for k in keys]


def travelers(conn: sqlite3.Connection) -> list[dict]:
    """The people a plan can name: adults and children, not a trust or joint."""
    return [
        {"key": r["person_key"], "name": r["display_name"]}
        for r in conn.execute(
            """SELECT person_key, display_name FROM people WHERE role IN ('adult', 'child')
               ORDER BY display_name, person_key"""
        )
    ]


def _brief(db_path: Path) -> dict:
    conn = connect(db_path)
    try:
        return rewards_brief(conn)
    finally:
        conn.close()


def _store(db_path: Path, plan_id: str, kind: str, content: Any, *, done: bool) -> None:
    """Add a turn. `done` ends the run: ready once any plan exists, else failed."""
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            _add_turn(conn, plan_id, kind, content)
            if done:
                conn.execute(
                    """UPDATE trip_plans SET updated_at = ?, status = CASE WHEN EXISTS (
                           SELECT 1 FROM trip_plan_turns WHERE plan_id = ? AND kind = 'plan')
                           THEN 'ready' ELSE 'failed' END
                       WHERE plan_id = ?""",
                    (_iso(), plan_id, plan_id),
                )
    finally:
        conn.close()


def estimates(conn: sqlite3.Connection, model: str, follow_up: bool = False) -> tuple[int, int]:
    """(research, plan) of a turn, before it starts (the caps check them)."""
    searches = FOLLOW_UP_SEARCHES if follow_up else MAX_SEARCHES
    research = max(
        ledger.last_cost(conn, "plan_research"),
        estimate_micro_usd(model, RESEARCH_PROMPT_EST, RESEARCH_OUTPUT_EST, searches),
    )
    write = max(
        ledger.last_cost(conn, "plan_write"),
        estimate_micro_usd(model, WRITE_PROMPT_EST, WRITE_OUTPUT_EST),
    )
    return research, write


def estimate_micro(conn: sqlite3.Connection, model: str, follow_up: bool = False) -> int:
    """A whole turn, research and plan."""
    return sum(estimates(conn, model, follow_up))


def _info(
    feature: str,
    model: str,
    user_id: int | None,
    plan_id: str | None,
    estimate: int,
    *,
    check_question: bool = False,
    question: bool = False,
) -> ai_client.CallInfo:
    """`check_question`: the daily question limit applies (before a turn is
    stored). `question`: the turn's first call, whose row counts as its question."""
    return ai_client.CallInfo(
        feature,
        model,
        ai_settings.EFFORT[feature],
        user_id,
        estimate_micro=estimate,
        new_question=check_question,
        plan_id=plan_id,
        question=question,
    )


def _precheck(db_path: Path, settings: Settings, model: str, user_id: int, follow: bool) -> None:
    """AI is available and the whole turn fits under the caps and the daily
    question limit. Raises AiError, before anything is stored."""
    conn = connect(db_path)
    try:
        estimate = estimate_micro(conn, model, follow)
    finally:
        conn.close()
    ai_client.preflight(
        db_path,
        settings,
        _info("plan_research", model, user_id, None, estimate, check_question=True),
    )


def new_plan(db_path: Path, settings: Settings, user_id: int, raw: PlanRequest) -> str:
    """Check and store a new plan's request. Returns the plan id. The caller runs it."""
    private = private_rules_for(settings)
    conn = connect(db_path)
    try:
        req = checked_request(conn, raw, private)
        model = ai_settings.load(conn).planner_model
    finally:
        conn.close()
    _precheck(db_path, settings, model, user_id, False)
    plan_id = uuid.uuid4().hex
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            now = _iso()
            conn.execute(
                """INSERT INTO trip_plans (plan_id, created_by, created_at, updated_at, title,
                       status, request_json, trip_key, model)
                   VALUES (?, ?, ?, ?, ?, 'running', ?, NULL, ?)""",
                (plan_id, user_id, now, now, req["where"], json.dumps(req), model),
            )
            _add_turn(conn, plan_id, "request", {"request": req})
    finally:
        conn.close()
    return plan_id


def follow_up(db_path: Path, settings: Settings, user_id: int, plan_id: str, text: str) -> None:
    """Check and store a follow-up. The caller runs it."""
    private = private_rules_for(settings)
    text = follow_up_text(text, private)
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT model, status FROM trip_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise PlanError("Trip plan not found.", 404)
        if row["status"] == "running":
            raise PlanError(BUSY_TEXT, 409)
        asked = conn.execute(
            "SELECT count(*) FROM trip_plan_turns WHERE plan_id = ? AND kind = 'request'",
            (plan_id,),
        ).fetchone()[0]
        if asked > FOLLOW_UP_LIMIT:
            raise PlanError(LIMIT_TEXT, 409)
        model = row["model"]
    finally:
        conn.close()
    _precheck(db_path, settings, model, user_id, True)
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            cur = conn.execute(
                "UPDATE trip_plans SET status = 'running', updated_at = ? WHERE plan_id = ?",
                (_iso(), plan_id),
            )
            if cur.rowcount == 0:  # deleted meanwhile (the rescan runs apart)
                raise PlanError("Trip plan not found.", 404)
            _add_turn(conn, plan_id, "request", {"follow_up": text})
    finally:
        conn.close()


def reset_running(db_path: Path) -> int:
    """At startup: a plan left running by a restart ends with a notice."""
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            ids = [
                r[0]
                for r in conn.execute("SELECT plan_id FROM trip_plans WHERE status = 'running'")
            ]
            for plan_id in ids:
                _add_turn(conn, plan_id, "notice", {"text": RESTART_TEXT})
                conn.execute(
                    """UPDATE trip_plans SET status = CASE WHEN EXISTS (
                           SELECT 1 FROM trip_plan_turns WHERE plan_id = ? AND kind = 'plan')
                           THEN 'ready' ELSE 'failed' END
                       WHERE plan_id = ?""",
                    (plan_id, plan_id),
                )
        return len(ids)
    finally:
        conn.close()


def startup_reset(db_path: Path) -> int:
    """reset_running for the app's start. Never raises: the app still starts."""
    try:
        return reset_running(db_path)
    except Exception as exc:  # the type only: a message may hold a value
        log.error("trip plan reset failed", extra={"exc_class": type(exc).__name__})
        return 0


def delete_plan(conn: sqlite3.Connection, plan_id: str) -> bool:
    """The caller holds the write transaction. Not while a plan or follow-up is
    being started: it may be this one."""
    if _starting or running_plan() == plan_id:
        raise PlanError(BUSY_TEXT, 409)
    return conn.execute("DELETE FROM trip_plans WHERE plan_id = ?", (plan_id,)).rowcount > 0


def link_trip(conn: sqlite3.Connection, plan_id: str, trip_key: str | None) -> bool:
    """Link a plan to an imported trip, or unlink it. The caller holds the write
    transaction. False when there is no such plan."""
    if trip_key is not None:
        found = conn.execute("SELECT 1 FROM trips WHERE trip_key = ?", (trip_key,)).fetchone()
        if found is None:
            raise PlanError("No trip with that key.", 404)
    cur = conn.execute(
        "UPDATE trip_plans SET trip_key = ?, updated_at = ? WHERE plan_id = ?",
        (trip_key, _iso(), plan_id),
    )
    return cur.rowcount > 0


# --------------------------------------------------------------------------
# The turn
# --------------------------------------------------------------------------


def _streamed(params: dict) -> Callable[[Any], Coroutine[Any, Any, Any]]:
    async def request(client: Any) -> Any:
        async with client.messages.stream(**params) as stream:
            async for _event in stream:
                pass
            return await stream.get_final_message()

    return request


def _searches(message: Any) -> int:
    use = getattr(getattr(message, "usage", None), "server_tool_use", None)
    return int(getattr(use, "web_search_requests", 0) or 0)


def _kept(turns: list[dict]) -> tuple[list[str], list[dict]]:
    """What a turn builds on: the follow-ups that made a version and this turn's
    own, and the research of the first request, of those follow-ups and of this
    turn. A change that ended in a notice is not sent again, nor its research."""
    groups: list[list[dict]] = []
    for t in turns:
        if t["kind"] == "request":
            groups.append([t])
        elif groups:
            groups[-1].append(t)
    changes, research = [], []
    for i, group in enumerate(groups):
        made = any(t["kind"] == "plan" for t in group)
        if i == 0 or made or i == len(groups) - 1:
            research += [t for t in group if t["kind"] == "research"]
            if "follow_up" in group[0]:
                changes.append(group[0]["follow_up"])
    return changes, research


async def _research(
    settings: Settings,
    plan_id: str,
    state: dict,
    user_id: int | None,
    scrub: Scrub,
    redact: Redact,
    costs: tuple[int, int],
) -> dict:
    """`costs`: the turn's (research, plan) estimates. Every call's cap check
    keeps room for the plan call, so research cannot spend what it needs."""
    turns = state["turns"]
    follow_ups, kept = _kept(turns)
    notes = [t["notes"] for t in kept]
    first_id = 1 + sum(len(t["sources"]) for t in turns if t["kind"] == "research")
    limit = FOLLOW_UP_SEARCHES if follow_ups else MAX_SEARCHES
    text = scrub.text(research_input(state["request"], notes, follow_ups, redact))
    messages: list[dict] = [{"role": "user", "content": text}]
    model = state["model"]
    alone = estimate_micro_usd(model, RESEARCH_PROMPT_EST, RESEARCH_OUTPUT_EST, limit)
    # The same tools on every call: see MAX_CONTINUATIONS.
    tools = [{"type": SEARCH_TOOL, "name": "web_search", "max_uses": limit}]
    used, blocks, stop = 0, [], None
    for step in range(1 + MAX_CONTINUATIONS):
        if step and used >= limit:
            break
        params = {
            "model": model,
            "max_tokens": RESEARCH_MAX_TOKENS,
            "system": PLAN_RESEARCH_RULES,
            "messages": messages,
            "tools": tools,
            "output_config": {"effort": ai_settings.EFFORT["plan_research"]},
        }
        estimate = (costs[0] if step == 0 else alone) + costs[1]
        info = _info("plan_research", model, user_id, plan_id, estimate, question=step == 0)
        try:
            message = await ai_client.call(settings, info, _streamed(params), record_cancel=True)
        except pydantic.ValidationError, ValueError:
            raise PlanFailed(ai_client.FAILED_TEXT) from None
        except ai_client.AiError as exc:
            if step and exc.kind == "cap":  # no room to go on: plan with what was found
                break
            raise
        used += _searches(message)
        content = list(message.content)
        blocks.extend(content)
        stop = message.stop_reason
        if stop != "pause_turn":
            break
        # Resume: the paused turn goes back unchanged, as one assistant message.
        sent = [b.to_dict() for b in content]
        if messages[-1]["role"] == "assistant":
            messages[-1] = {"role": "assistant", "content": messages[-1]["content"] + sent}
        else:
            messages.append({"role": "assistant", "content": sent})
    if stop == "refusal":
        raise PlanFailed(REFUSED_TEXT)
    found, sources = notes_and_sources(blocks, scrub.private, first_id)
    if not found:
        raise PlanFailed(EMPTY_TEXT)
    return {"notes": found, "sources": sources, "searches": used, "stop": stop}


def _write_input(state: dict, research: list[dict], brief: dict, today: date) -> dict:
    req = state["request"]
    turns = state["turns"]
    plans = [t for t in turns if t["kind"] == "plan"]
    return {
        "today": today.isoformat(),
        "request": {
            "place": req["where"],
            "start_date": req.get("start_date"),
            "end_date": req.get("end_date"),
            "month": req.get("month"),
            "nights": req["nights"],
            "travelers": [p["name"] for p in state["travelers"]],
            "guests": req["guests"],
            "home_airport": req.get("home_airport"),
            "interests": req["interests"],
            "note": req["note"],
            "pace": req["pace"],
            "lean": LEAN_WORDS[req["lean"]],
        },
        "rewards": brief,
        "research_notes": "\n\n".join(r["notes"] for r in research)[-2 * NOTES_MAX :],
        "sources": [
            {"id": s["id"], "title": s["title"], "site": s["site"]}
            for r in research
            for s in r["sources"]
        ],
        "previous_plan": plans[-1]["plan"] if plans else None,
        "changes_asked": _kept(turns)[0],
    }


async def _write(
    settings: Settings,
    plan_id: str,
    state: dict,
    research: list[dict],
    brief: dict,
    user_id: int | None,
    scrub: Scrub,
) -> PlanOut:
    """`brief`: scrubbed, from plan_brief."""
    content = scrub.json(_write_input(state, research, brief, config.household_today()))
    payload = json.dumps(content, ensure_ascii=False)
    model = state["model"]
    # No tools: nothing in this call can reach the web.
    params = {
        "model": model,
        "max_tokens": WRITE_MAX_TOKENS,
        "system": PLAN_SYSTEM,
        "messages": [{"role": "user", "content": payload}],
        "output_config": {"effort": ai_settings.EFFORT["plan_write"]},
    }
    estimate = estimate_micro_usd(model, (len(payload) + len(PLAN_SYSTEM)) // 3, WRITE_OUTPUT_EST)
    info = _info("plan_write", model, user_id, plan_id, estimate)
    try:
        message = await ai_client.call(settings, info, _streamed(params), record_cancel=True)
    except pydantic.ValidationError, ValueError:
        raise PlanFailed(ai_client.FAILED_TEXT) from None
    if message.stop_reason == "refusal":
        raise PlanFailed(REFUSED_TEXT)
    if message.stop_reason != "end_turn":
        raise PlanFailed(LONG_TEXT)
    out = plan_out(message)
    if out is None:
        raise PlanFailed(ai_client.FAILED_TEXT)
    return out


async def run_turn(settings: Settings, plan_id: str, user_id: int | None) -> bool:
    """Research, then write one version of the plan. Ends with a plan (True) or
    a notice (False)."""
    db_path = settings.db_path
    try:
        state = await asyncio.to_thread(_state, db_path, plan_id)
        private = private_rules_for(settings)
        scrub = Scrub(private, await asyncio.to_thread(load_last4s, db_path))
        raw_brief = await asyncio.to_thread(_brief, db_path)
        conn = await asyncio.to_thread(connect, db_path)
        try:
            follow = any("follow_up" in t for t in state["turns"] if t["kind"] == "request")
            costs = await asyncio.to_thread(estimates, conn, state["model"], follow)
            redact = await asyncio.to_thread(household, conn)
        finally:
            conn.close()
        # Everything the plan call gets is scrubbed.
        brief = plan_brief(scrub.json(raw_brief))
        found = await _research(settings, plan_id, state, user_id, scrub, redact, costs)
        await asyncio.to_thread(_store, db_path, plan_id, "research", found, done=False)
        research = _kept(state["turns"])[1] + [found]
        out = await _write(settings, plan_id, state, research, brief, user_id, scrub)
        known = {s["id"] for r in research for s in r["sources"]}
        names = enum_names(brief)
        plan = tidy(out, known, private, names)
        derived = derive(plan, state["request"], brief, unknown_benefits(out, names))
        version = {"plan": plan, "derived": derived}
        await asyncio.to_thread(_store, db_path, plan_id, "plan", version, done=True)
        log.info(
            "trip plan written",
            extra={"searches": found["searches"], "sources": len(found["sources"])},
        )
        return True
    except asyncio.CancelledError:
        raise
    except (ai_client.AiError, PlanFailed) as exc:
        notice = exc.message
    except Exception as exc:  # never the message: it can hold data
        log.error("trip plan failed", extra={"exc_class": type(exc).__name__})
        notice = ai_client.FAILED_TEXT
    try:
        await asyncio.to_thread(_store, db_path, plan_id, "notice", {"text": notice}, done=True)
    except Exception as exc:
        log.error("trip plan notice failed", extra={"exc_class": type(exc).__name__})
    return False


# --------------------------------------------------------------------------
# One plan at a time (uvicorn runs one worker, so this is the whole app)
# --------------------------------------------------------------------------

_task: asyncio.Task | None = None
_task_plan: str | None = None
_starting = False
# Tasks kept until they end, so they are not garbage collected.
_BACKGROUND: set[asyncio.Task] = set()


def running_plan() -> str | None:
    """The plan being made now, if any. None in demo: the running plan is a real one."""
    if in_demo():
        return None
    t = _task
    if t is None or t.done() or t.get_loop().is_closed():
        return None
    return _task_plan


async def start(
    settings: Settings,
    user_id: int,
    prepare: Callable[[], str | None],
    plan_id: str | None = None,
) -> str:
    """Run `prepare` (it checks and stores the turn, in a thread; a new plan's
    returns its id) and then the turn in the background. Raises PlanError(409)
    while another plan runs."""
    global _task, _task_plan, _starting
    if _starting or running_plan() is not None:
        raise PlanError(BUSY_TEXT, 409)
    _starting = True
    try:
        made = await asyncio.to_thread(prepare)
        plan_id = made or plan_id
        assert plan_id is not None
        task = asyncio.get_running_loop().create_task(run_turn(settings, plan_id, user_id))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        _task, _task_plan = task, plan_id
        return plan_id
    finally:
        _starting = False


# --------------------------------------------------------------------------
# Reading plans
# --------------------------------------------------------------------------


def _filtered(value: Any, private: PrivateRules | None) -> Any:
    if isinstance(value, str):
        return filter_text(value, private)
    if isinstance(value, list):
        return [_filtered(v, private) for v in value]
    return value


def _request_view(conn: sqlite3.Connection, req: dict, private: PrivateRules | None) -> dict:
    return {
        "where": filter_text(req["where"], private),
        "start_date": req.get("start_date"),
        "end_date": req.get("end_date"),
        "month": req.get("month"),
        "nights": req["nights"],
        "people": _names(conn, req["people"]),
        "guests": req["guests"],
        "home_airport": req.get("home_airport"),
        "interests": req["interests"],
        "note": filter_text(req["note"], private),
        "pace": req["pace"],
        "lean": req["lean"],
    }


def _links(plan: dict, sources: dict[str, dict], private: PrivateRules | None) -> list[dict]:
    """Every source the version names: link_ids first, then the rest. Checked
    again against today's private lists (the rescan runs only at startup)."""
    ids = list(plan["link_ids"])

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "source_ids":
                    ids.extend(v)
                else:
                    collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    collect(plan)
    return [
        {**{k: sources[i][k] for k in ("id", "url", "site")}, "title": filter_text(t, private)}
        for i in dict.fromkeys(ids)
        if i in sources and safe_url(sources[i]["url"], private)
        for t in [sources[i]["title"]]
    ]


def _cost(conn: sqlite3.Connection, plan_id: str) -> tuple[int, int]:
    row = conn.execute(
        """SELECT coalesce(sum(cost_micro_usd), 0), coalesce(sum(web_searches), 0)
           FROM ai_usage WHERE plan_id = ?""",
        (plan_id,),
    ).fetchone()
    cents = (int(row[0]) + ledger.CENT // 2) // ledger.CENT
    return cents, int(row[1])


def _trip_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT trip_key, name FROM trips")}


def _creator(conn: sqlite3.Connection, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = conn.execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def list_plans(conn: sqlite3.Connection, private: PrivateRules | None) -> list[dict]:
    trips = _trip_names(conn)
    out = []
    for r in conn.execute("SELECT * FROM trip_plans ORDER BY updated_at DESC, plan_id"):
        req = json.loads(r["request_json"])
        plans = [
            json.loads(t[0])
            for t in conn.execute(
                """SELECT content_json FROM trip_plan_turns WHERE plan_id = ? AND kind = 'plan'
                   ORDER BY seq""",
                (r["plan_id"],),
            )
        ]
        costs = plans[-1]["derived"]["costs"] if plans else None
        out.append(
            {
                "plan_id": r["plan_id"],
                "title": filter_text(r["title"], private),
                "status": "running" if running_plan() == r["plan_id"] else r["status"],
                "start_date": req.get("start_date"),
                "end_date": req.get("end_date"),
                "month": req.get("month"),
                "nights": req["nights"],
                "updated_at": r["updated_at"],
                "created_by_name": _creator(conn, r["created_by"]),
                "trip_key": r["trip_key"],
                "trip_name": trips.get(r["trip_key"]) if r["trip_key"] else None,
                "versions": len(plans),
                # The newest version's range: a linked trip's card shows it ("plan est.").
                "low_cents": costs["low_cents"] if costs else None,
                "high_cents": costs["high_cents"] if costs else None,
            }
        )
    return out


def plan_view(conn: sqlite3.Connection, plan_id: str, private: PrivateRules | None) -> dict | None:
    r = conn.execute("SELECT * FROM trip_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if r is None:
        return None
    turns = _turns(conn, plan_id)
    sources = {s["id"]: s for t in turns if t["kind"] == "research" for s in t["sources"]}
    versions, asked = [], None
    for t in turns:
        if t["kind"] == "request":
            asked = t.get("follow_up")
        elif t["kind"] == "plan":
            versions.append(
                {
                    "version": len(versions) + 1,
                    "created_at": t["at"],
                    "follow_up": None if asked is None else filter_text(asked, private),
                    "plan": t["plan"],
                    "derived": t["derived"],
                    "links": _links(t["plan"], sources, private),
                }
            )
    last = turns[-1] if turns else None
    notice = last["text"] if last and last["kind"] == "notice" else None
    follow_ups = sum(1 for t in turns if t["kind"] == "request" and "follow_up" in t)
    cents, searches = _cost(conn, plan_id)
    trips = _trip_names(conn)
    status = "running" if running_plan() == plan_id else r["status"]
    step = None
    if status == "running":  # the turn stores its research when the search is done
        step = "writing" if last and last["kind"] == "research" else "searching"
    return {
        "plan_id": plan_id,
        "title": filter_text(r["title"], private),
        "status": status,
        "step": step,
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "created_by_name": _creator(conn, r["created_by"]),
        "model": r["model"],
        "request": _request_view(conn, json.loads(r["request_json"]), private),
        "trip_key": r["trip_key"],
        "trip_name": trips.get(r["trip_key"]) if r["trip_key"] else None,
        "versions": versions,
        "notice": _filtered(notice, private),
        "follow_ups_left": max(0, FOLLOW_UP_LIMIT - follow_ups),
        "cost_cents": cents,
        "web_searches": searches,
    }
