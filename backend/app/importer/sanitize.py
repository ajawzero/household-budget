"""Redaction checks: `detect_sensitive` and the private lists.

Patterns come from `schema/redaction.json` (loaded once). The same file drives
`schema/check_run.py`, so the chat's checker and the app agree. A rule with
`sections` runs only when the caller names one of those snapshot sections.
`booking-code` covers rewards and trips (from 1.3) and flags, installment_plans
and goals (from 1.4, redaction.json version 4; the app checks these sections in
runs of every version, since the rule goes by section, never by schema_version).
Every other caller is unchanged.

Nothing here returns, logs or raises with a matched value. Results are rule ids.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from app.importer.models import PERSONAL_SUBSCRIPTION, Issue, Snapshot, iter_text

REDACTION_ENV = "BUDGET_REDACTION_FILE"
PRIVATE_RULES_ENV = "BUDGET_PRIVATE_RULES"
_DEFAULT_REDACTION = Path(__file__).resolve().parents[3] / "schema" / "redaction.json"


@dataclass(frozen=True)
class _Rule:
    id: str
    kind: str  # regex, digit_groups, luhn
    rx: re.Pattern[str] | None  # None for luhn
    masked: bool
    min_digits: int = 0
    max_digits: int = 0
    gap: re.Pattern[str] | None = None  # luhn only
    tight_gap: re.Pattern[str] | None = None
    loose_min_group: int = 0
    sections: frozenset[str] = frozenset()  # empty: every text; else only these sections

    def applies(self, section: str | None) -> bool:
        return not self.sections or section in self.sections


@dataclass(frozen=True)
class Redaction:
    rules: tuple[_Rule, ...]
    masks: tuple[re.Pattern[str], ...]
    stock_plan_id: str
    stock_plan: re.Pattern[str]
    private_rule_ids: dict[str, str]
    min_squashed: int


def _compile(spec: dict) -> re.Pattern[str]:
    return re.compile(spec["pattern"], re.IGNORECASE if spec.get("ignore_case") else 0)


def _opt_rx(spec: dict, name: str) -> re.Pattern[str] | None:
    return re.compile(spec[name]) if name in spec else None


def redaction_path() -> Path:
    return Path(os.environ.get(REDACTION_ENV) or _DEFAULT_REDACTION)


@cache
def load_redaction(path: str | None = None) -> Redaction:
    """Load and compile redaction.json once (per path)."""
    data = json.loads(Path(path or redaction_path()).read_text(encoding="utf-8"))
    rules = tuple(
        _Rule(
            id=r["id"],
            kind=r["kind"],
            rx=_compile(r) if "pattern" in r else None,
            masked=bool(r.get("masked")),
            min_digits=int(r.get("min_digits", 0)),
            max_digits=int(r.get("max_digits", 0)),
            gap=_opt_rx(r, "gap"),
            tight_gap=_opt_rx(r, "tight_gap"),
            loose_min_group=int(r.get("loose_min_group", 0)),
            sections=frozenset(r.get("sections", ())),
        )
        for r in data["rules"]
    )
    return Redaction(
        rules=rules,
        masks=tuple(re.compile(m["pattern"]) for m in data["masks"]),
        stock_plan_id=data["stock_plan"]["id"],
        stock_plan=_compile(data["stock_plan"]),
        private_rule_ids=dict(data["private"]["rule_ids"]),
        min_squashed=int(data["private"]["min_squashed"]),
    )


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isascii() and ch.isdigit())


def normalize(text: str) -> str:
    """NFKC, ASCII digits, '-' for every dash, ' ' for every space, format characters removed.

    So full-width digits, no-break spaces, en dashes or zero-width spaces cannot
    hide a number from the rules (redaction.json "normalize").
    """
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        if ch.isascii():
            out.append(ch)
            continue
        digit = unicodedata.decimal(ch, None)
        cat = unicodedata.category(ch)
        if digit is not None:
            out.append(str(digit))
        elif cat == "Pd" or ch == "\u2212":
            out.append("-")
        elif ch.isspace() or cat == "Zs":
            out.append(" ")
        elif cat != "Cf":
            out.append(ch)
    return "".join(out)


_DIGIT_GROUP = re.compile(r"[0-9]+")


def _luhn_hit(rule: _Rule, text: str) -> bool:
    """Any window of digit groups, joined by allowed gaps, that holds a Luhn number."""
    assert rule.gap is not None and rule.tight_gap is not None
    groups = [(m.start(), m.end(), m.group()) for m in _DIGIT_GROUP.finditer(text)]
    for i, (_, _, first) in enumerate(groups):
        digits = first
        if rule.min_digits <= len(digits) <= rule.max_digits and luhn_ok(digits):
            return True
        for j in range(i + 1, len(groups)):
            prev, cur = groups[j - 1], groups[j]
            gap = text[prev[1] : cur[0]]
            if not rule.gap.fullmatch(gap):
                break
            if not rule.tight_gap.fullmatch(gap) and min(len(prev[2]), len(cur[2])) < (
                rule.loose_min_group
            ):
                break
            digits += cur[2]
            if len(digits) > rule.max_digits:
                break
            if len(digits) >= rule.min_digits and luhn_ok(digits):
                return True
    return False


def detect_sensitive(text: str, section: str | None = None) -> list[str]:
    """Return the ids of every redaction rule the text breaks, in rule order.
    `section` is the snapshot section the text came from, for sectioned rules."""
    red = load_redaction()
    text = normalize(text)
    masked = text
    for m in red.masks:
        masked = m.sub("_", masked)
    found = []
    for rule in red.rules:
        if not rule.applies(section):
            continue
        target = masked if rule.masked else text
        if rule.kind == "regex" and rule.rx is not None:
            hit = rule.rx.search(target) is not None
        elif rule.kind == "digit_groups" and rule.rx is not None:
            hit = any(len(_digits(m.group())) >= rule.min_digits for m in rule.rx.finditer(target))
        elif rule.kind == "luhn":
            hit = _luhn_hit(rule, target)
        else:  # unknown kinds fail closed
            hit = True
        if hit:
            found.append(rule.id)
    return found


def _luhn_spans(rule: _Rule, text: str) -> list[tuple[int, int]]:
    """Spans of the digit-group windows `_luhn_hit` would flag."""
    assert rule.gap is not None and rule.tight_gap is not None
    groups = [(m.start(), m.end(), m.group()) for m in _DIGIT_GROUP.finditer(text)]
    spans = []
    for i, (start, end, first) in enumerate(groups):
        digits = first
        if rule.min_digits <= len(digits) <= rule.max_digits and luhn_ok(digits):
            spans.append((start, end))
        for j in range(i + 1, len(groups)):
            prev, cur = groups[j - 1], groups[j]
            gap = text[prev[1] : cur[0]]
            if not rule.gap.fullmatch(gap):
                break
            if not rule.tight_gap.fullmatch(gap) and min(len(prev[2]), len(cur[2])) < (
                rule.loose_min_group
            ):
                break
            digits += cur[2]
            if len(digits) > rule.max_digits:
                break
            if len(digits) >= rule.min_digits and luhn_ok(digits):
                spans.append((start, cur[1]))
    return spans


def sensitive_spans(text: str, skip: Collection[str] = ()) -> list[tuple[int, int]]:
    """Where in `text` the detect_sensitive rules match, as merged (start, end) spans.

    `text` must already be `normalize`d, so the spans index it directly. Masks
    (dates, year ranges, money) are blanked to the same length first, so they
    never match and the indexes stay put. Rules whose id is in `skip` are not
    run. Used by the AI output filter, which replaces each span. Never logs or
    returns the matched text.
    """
    red = load_redaction()
    masked = text
    for m in red.masks:
        masked = m.sub(lambda hit: "_" * len(hit.group()), masked)
    spans: list[tuple[int, int]] = []
    for rule in red.rules:
        if rule.id in skip or rule.sections:  # sectioned rules are for import scans only
            continue
        target = masked if rule.masked else text
        if rule.kind == "regex" and rule.rx is not None:
            spans += [hit.span() for hit in rule.rx.finditer(target)]
        elif rule.kind == "digit_groups" and rule.rx is not None:
            spans += [
                hit.span()
                for hit in rule.rx.finditer(target)
                if len(_digits(hit.group())) >= rule.min_digits
            ]
        elif rule.kind == "luhn":
            spans += _luhn_spans(rule, target)
        else:  # unknown kinds fail closed: the whole text
            spans.append((0, len(text)))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def is_stock_plan_name(name: str) -> bool:
    """True when a holding name mentions a stock plan, ESPP, RSU or unvested item."""
    return load_redaction().stock_plan.search(name) is not None


# --------------------------------------------------------------------------
# Private lists
# --------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# What the privacy rescan writes over a match (PrivateRules.mask). One source with the
# models, which keep a tag off the item with this name (recurring-tag-private).
SUBSCRIPTION_TEXT = PERSONAL_SUBSCRIPTION
EMPLOYER_TEXT = "Employer"


def _spaced(s: str) -> str:
    return _NON_ALNUM.sub(" ", normalize(s).casefold()).strip()


def _squashed(s: str) -> str:
    return _NON_ALNUM.sub("", normalize(s).casefold())


# Cyrillic and Greek letters that look like Latin ones, one for one, so a
# look-alike letter cannot hide a private name from the AI output filter.
_CONFUSABLES = str.maketrans(
    "аАвВеЕкКмМнНоОрРсСтТуУхХіІјЈѕЅԁӏɡαΑβΒεΕζΖηΗιΙκΚμΜνΝοΟρΡτΤυΥχΧ",
    "aAbBeEkKmMhHoOpPcCtTyYxXiIjJsSdlgaAbBeEzZnHiIkKuMvNoOpPtTuYxX",
)


@dataclass(frozen=True)
class PrivateRules:
    """Personal subscription names and employer terms. Values never leave this object."""

    subscriptions: tuple[str, ...] = field(default=(), repr=False)
    employer_terms: tuple[str, ...] = field(default=(), repr=False)
    digest: str = ""  # sha256 of the file bytes, for the privacy rescan trigger

    def __repr__(self) -> str:
        return (
            f"PrivateRules(subscriptions={len(self.subscriptions)}, "
            f"employer_terms={len(self.employer_terms)})"
        )

    def _hits(self, terms: Iterable[str], text: str, min_squashed: int) -> bool:
        spaced, squashed = _spaced(text), _squashed(text)
        for term in terms:
            t_sp, t_sq = _spaced(term), _squashed(term)
            if t_sp and t_sp in spaced:
                return True
            if len(t_sq) >= min_squashed and t_sq in squashed:
                return True
        return False

    def check(self, text: str) -> list[str]:
        """Rule ids for private-list matches in text (never the matched term)."""
        red = load_redaction()
        ids = red.private_rule_ids
        found = []
        if self._hits(self.subscriptions, text, red.min_squashed):
            found.append(ids["personal_subscriptions"])
        if self._hits(self.employer_terms, text, red.min_squashed):
            found.append(ids["employer_terms"])
        return found

    def spans(self, text: str, terms: Iterable[str] | None = None) -> list[tuple[int, int]]:
        """Where private-list terms appear in `text` (already `normalize`d), the same
        way `check` matches them: the spaced form as a substring, or the letters and
        digits alone when long enough. Look-alike Cyrillic and Greek letters count
        as the Latin ones. Used by the AI output filter. `terms`: only these (default
        both lists)."""
        min_sq = load_redaction().min_squashed
        text = text.translate(_CONFUSABLES)  # same length, so the spans still fit
        found: list[tuple[int, int]] = []
        for term in (*self.subscriptions, *self.employer_terms) if terms is None else terms:
            words, squashed = _spaced(term).split(), _squashed(term)
            patterns = []
            if words:
                patterns.append("[^0-9a-z]+".join(re.escape(w) for w in words))
            if len(squashed) >= min_sq:
                patterns.append("[^0-9a-z]*".join(re.escape(ch) for ch in squashed))
            for pattern in patterns:
                found += [m.span() for m in re.finditer(pattern, text, re.IGNORECASE)]
        merged: list[tuple[int, int]] = []
        for start, end in sorted(found):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def mask(self, text: str) -> str | None:
        """The text with private-list matches masked (privacy rescan), or None when
        nothing matches. A personal subscription name makes the whole value
        SUBSCRIPTION_TEXT (README section 9). Employer terms are cut out; when
        nothing is left, or a match is still there, the value is EMPLOYER_TEXT."""
        found = self.check(text)
        if not found:
            return None
        if load_redaction().private_rule_ids["personal_subscriptions"] in found:
            return SUBSCRIPTION_TEXT
        out = normalize(text)
        for start, end in reversed(self.spans(out, self.employer_terms)):
            out = f"{out[:start]} {out[end:]}"
        out = " ".join(out.split())
        return out if out and not self.check(out) else EMPLOYER_TEXT

    def __bool__(self) -> bool:
        return bool(self.subscriptions or self.employer_terms)


EMPTY_PRIVATE_RULES = PrivateRules()


class PrivateRulesError(ValueError):
    """The private rules file is unreadable or has the wrong shape. Never holds values."""


def _terms(obj: object) -> tuple[str, ...]:
    if obj is None:
        return ()
    if not isinstance(obj, list) or not all(isinstance(t, str) and t.strip() for t in obj):
        raise PrivateRulesError("Each private list must be a list of non-empty strings.")
    return tuple(obj)


def load_private_rules(path: str | os.PathLike[str]) -> PrivateRules:
    """Load `private_rules.json`: {"personal_subscriptions": [...], "employer_terms": [...]}."""
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError, ValueError:
        raise PrivateRulesError("Private rules file cannot be read as UTF-8 JSON.") from None
    allowed = {"personal_subscriptions", "employer_terms"}
    if not isinstance(data, dict) or not set(data) <= allowed:
        raise PrivateRulesError(
            "Private rules file must be an object with personal_subscriptions "
            "and employer_terms lists only."
        )
    return PrivateRules(
        subscriptions=_terms(data.get("personal_subscriptions")),
        employer_terms=_terms(data.get("employer_terms")),
        digest=hashlib.sha256(raw).hexdigest(),
    )


def load_private_rules_from_env(env: str = PRIVATE_RULES_ENV) -> PrivateRules:
    """Load the file named by BUDGET_PRIVATE_RULES. Unset means no private rules."""
    path = os.environ.get(env)
    return load_private_rules(path) if path else EMPTY_PRIVATE_RULES


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def scan_text(
    text: str, private: PrivateRules | None = None, section: str | None = None
) -> list[str]:
    """detect_sensitive plus the private lists."""
    found = detect_sensitive(text, section)
    if private:
        found += private.check(text)
    return found


def _section(loc: str) -> str:
    """'trips[0].note' -> 'trips', 'rewards.programs[1].name' -> 'rewards'."""
    return re.split(r"[.\[]", loc, maxsplit=1)[0]


def location_section(where: str) -> str | None:
    """The snapshot section of an issue location, for sectioned rules on unknown field
    names: 'snapshot.json:trips[0].x' -> 'trips'. None for every other file."""
    if not where.startswith("snapshot.json:"):
        return None
    return _section(where[len("snapshot.json:") :])


def scan_model(model: BaseModel, where: str, private: PrivateRules | None = None) -> list[Issue]:
    """Scan every text field and key of a validated model. Returns rule id and location only.
    For a snapshot, each text is scanned with its section's rules (booking-code)."""
    issues = []
    is_snapshot = isinstance(model, Snapshot)
    for loc, text in iter_text(model):
        for rule in scan_text(text, private, _section(loc) if is_snapshot else None):
            issues.append(Issue(rule, f"{where}{loc}"))
    return issues
