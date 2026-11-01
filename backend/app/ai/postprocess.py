"""The output filter (PLAN 7.7), applied before AI text is stored or streamed.

1. Em and en dashes become a comma (spaced dashes) or a plain hyphen (a dash
   inside a range like 2025-26).
2. Text is normalized (`sanitize.normalize`), so full-width digits or odd
   spaces cannot hide a number.
3. Private-list matches (personal subscription names, employer terms) become
   "personal subscription".
4. `detect_sensitive` matches become "…". A year range (both years 1990 to
   2099, the second one later) stays, though the split-account rule matches it,
   unless a group of 3 or more digits is joined to it by spaces, a dash or a
   dot (not a dot that ends a sentence). Two years joined only by a sentence
   end (one sentence ends with a year, the next starts with one) stay too,
   unless more digits are joined to them.
5. If a line still breaks a rule after that, the whole line becomes "…".

Steps 4 and 5 read markdown bold stars as spaces, so a bold year or count is
not a masked number. A mask that would cut a bold pair takes in the whole pair,
so no lone "**" is left.

Line ends are "\n" first ("\r\n" and "\r" become "\n"). Step 3 runs on the
whole text first, so a name split by a line break is caught too. Step 4 runs
per line, so a number at the end of a line never joins the next line. One
exception: digits that plainly form one card or phone number across a break
(one newline, with spaces and one "." or "-" next to it at most) are masked
across it. A card: consecutive groups of 4 or more digits, joined by one
space, dot or dash or a break, that hold 13 to 19 digits, pass the Luhn check
and are not all years. As for the Luhn rule, a date, a year range or an
amount with cents is never part of one. Another digit group next to the card
does not hide it, unless that group is alone on its own line: a list of
numbers, one per line, holds a card only when the whole list is one. A phone
number: (NNN) NNN-NNNN or NNN NNN NNNN, each gap a space, ".", "-" or "/" as
the phone rule takes it, with one break in place of a gap or next to it. A
"1" or "+1" may come first. A blank line, a letter, a comma or a list dash
ends the number.

Streaming: `StreamFilter` holds text back until a sentence or line ends, and
then filters everything received up to there. What the browser shows is always
that filtered text, never a piece filtered on its own: when more text changes
what was already shown (a number or name that went on past a sentence end), it
sends the whole filtered text again as a replacement. A sentence end inside a
bold span that is still open, or on a line with no letters yet, waits for the
line end. `finish()` emits the rest.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

from app.db.connect import connect
from app.importer.sanitize import (
    PrivateRules,
    detect_sensitive,
    load_redaction,
    luhn_ok,
    normalize,
    sensitive_spans,
)

PRIVATE_LABEL = "personal subscription"
MASK = "…"

_SPACED_DASH = re.compile(r"\s+[—–]\s+|\s*—\s*")
_RANGE_DASH = re.compile(r"–")
# A sentence or line ends here: emit up to it.
_BOUNDARY = re.compile(r"(?:[.!?:](?=\s)|\n)")
_LINE_END = re.compile(r"\r\n?")
# Numbers masked across a line break (see the module notes). A break is one
# newline, with spaces and one "." or "-" next to it at most (a "-" after it
# only right before a digit, so a list dash ends the number).
_BREAK = r"[ \t]*(?:[.-][ \t]*\n[ \t]*|\n[ \t]*(?:[.-](?=\d))?)"
# Digit groups joined by one space, dot or dash, or by a break: the whole run.
_RUN = re.compile(rf"(?<!\d)\d+(?:(?:[ .-]|{_BREAK})\d+)+")
# A phone number with one break: (NNN) NNN-NNNN or NNN NNN NNNN, each gap as
# the phone rule takes it (a space, ".", "-" or "/", spaces around it), one gap
# a break (one "." "-" or "/" next to it at most). As in the phone rule, no
# digit right before it, so a 1-800 number counts.
_GAP = r"[ \t]*[ ./-][ \t]*"
_PHONE_GAP = r"[ \t]*(?:[./-][ \t]*\n[ \t]*|\n[ \t]*(?:[./-](?=\d))?)"
_PHONE_BREAK = re.compile(
    r"(?<!\d)"
    rf"(?:\(\d{{3}}\){_PHONE_GAP}\d{{3}}[ \t]*[ ./-]?[ \t]*"
    rf"|\(\d{{3}}\)[ \t]*\d{{3}}{_PHONE_GAP}"
    rf"|\d{{3}}{_PHONE_GAP}\d{{3}}{_GAP}|\d{{3}}{_GAP}\d{{3}}{_PHONE_GAP})"
    r"\d{4}(?!\d)(?![./-]\d)"
)
_DIGITS = re.compile(r"\d+")
_WORD = re.compile(r"\w")
_YEAR = re.compile(r"(?:19|20)\d\d")
# Four digits, a dash, four digits: the split-account rule's shape. Kept when it
# is a year range not joined to more digits, nor to a group of 3 or more digits
# by spaces, a dash or a dot that does not end a sentence, as digits-split joins
# groups (output only: the question check and the importer still refuse it).
_YEAR_RANGE = re.compile(r"(?<!\d[.-])(199\d|20\d\d)-(199\d|20\d\d)(?![.-]\d)")
_JOINED_BEFORE = re.compile(r"\d{3}(?:\s*-\s*|\s*\.|\s+)$")
_JOINED_AFTER = re.compile(r"(?:\s*-\s*|\s*\.|\s+)\d{3}")
# Two years joined only by a sentence end (one sentence ends with a year, the
# next starts with one), with no group of 2 or more digits joined to them as
# digits-split joins groups: that rule would read them as one number. The rules
# see a comma there instead.
_DIGITS_GAP = r"(?:[ \t]*[.-][ \t]*|[ \t]+)"
_YEAR_STOP = re.compile(
    r"(?<!\d)((?:19|20)\d\d[ \t]*)\."
    rf"(?=[ \t]+(?:19|20)\d\d(?!\d)(?!{_DIGITS_GAP}\d\d))"
)
_JOINED_LEFT = re.compile(rf"\d\d{_DIGITS_GAP}\Z")
# Markdown bold (or bold italic), around a year or a label before one. The rules
# see its stars as spaces, so they never read as a masked number.
_BOLD = re.compile(r"(?<!\*)(\*\*\*?)(?=[^\s*])[^*\n]*?(?<=[^\s*])\1(?!\*)")
_OPEN_BOLD = re.compile(r"(?<!\*)\*\*\*?(?=[^\s*])")
_ALPHA = re.compile(r"[^\W\d_]")


def _dashes(text: str) -> str:
    text = _SPACED_DASH.sub(", ", text)
    return _RANGE_DASH.sub("-", text)


def _replace(text: str, spans: list[tuple[int, int]], label: str) -> str:
    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        out.append(label)
        last = end
    out.append(text[last:])
    return "".join(out)


def _year_range(text: str, start: int, end: int) -> bool:
    m = _YEAR_RANGE.match(text, start)
    return (
        m is not None
        and m.end() == end
        and int(m.group(1)) < int(m.group(2))
        and not _JOINED_BEFORE.search(text, 0, start)
        and not _JOINED_AFTER.match(text, end)
    )


def _unstar(bold: str, stars: int) -> str:
    return " " * stars + bold[stars:-stars] + " " * stars


def _view(line: str) -> str:
    """The line as the rules read it: bold stars as spaces, and a comma for a
    sentence-end dot between two years (the same length, so spans still fit)."""
    line = _BOLD.sub(lambda m: _unstar(m.group(), len(m.group(1))), line)

    def stop(m: re.Match[str]) -> str:
        return m.group() if _JOINED_LEFT.search(line, 0, m.start()) else f"{m.group(1)},"

    return _YEAR_STOP.sub(stop, line)


def _merge(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _widen(line: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """A span that cuts a bold pair takes in the whole pair, so no lone "**" is left."""
    bold = [(*m.span(), len(m.group(1))) for m in _BOLD.finditer(line)]
    if not bold:
        return spans
    out = []
    for start, end in spans:
        for b_start, b_end, stars in bold:
            inside = b_start + stars <= start and end <= b_end - stars
            if start < b_end and b_start < end and not inside:
                start, end = min(start, b_start), max(end, b_end)
        out.append((start, end))
    return _merge(out)


def _spans(text: str) -> list[tuple[int, int]]:
    """`sensitive_spans`, less a span that is only a year range."""
    return [(s, e) for s, e in sensitive_spans(text) if not _year_range(text, s, e)]


def _alone(text: str, start: int, end: int) -> bool:
    """The digit group at text[start:end] is alone on its line, as in a list."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return not _WORD.search(text, line_start, start) and not _WORD.search(text, end, line_end)


def _cards(text: str, run: re.Match[str], low: int, high: int) -> list[tuple[int, int]]:
    """Cards in a run of digit groups that crosses a break: each window of
    consecutive groups of 4 or more digits that holds `low` to `high` digits,
    passes the Luhn check, is not all years and crosses a break. A group next
    to the window does not stop it, unless that group is alone on another line."""
    groups = [m.span() for m in _DIGITS.finditer(text, run.start(), run.end())]

    def near_ok(near: int, edge: int) -> bool:
        a, b = sorted((groups[near], groups[edge]))
        return "\n" not in text[a[1] : b[0]] or not _alone(text, *groups[near])

    spans = []
    for i in range(len(groups)):
        digits = ""
        for j in range(i, len(groups)):
            start, end = groups[j]
            if end - start < 4 or len(digits) + end - start > high:
                break
            digits += text[start:end]
            first = groups[i][0]
            if (
                len(digits) >= low
                and "\n" in text[first:end]
                and not all(_YEAR.fullmatch(text[a:b]) for a, b in groups[i : j + 1])
                and luhn_ok(digits)
                and (i == 0 or near_ok(i - 1, i))
                and (j == len(groups) - 1 or near_ok(j + 1, j))
            ):
                spans.append((first, end))
    return spans


def _broken_numbers(text: str) -> list[tuple[int, int]]:
    """Cards or phone numbers broken over lines (see the module notes)."""
    if "\n" not in text:
        return []
    red = load_redaction()
    luhn = next(rule for rule in red.rules if rule.id == "luhn")
    spans = [m.span() for m in _PHONE_BREAK.finditer(text)]
    # As the Luhn rule does, a card never takes in a date, a year range or money.
    masked = text
    for mask in red.masks:
        masked = mask.sub(lambda hit: "_" * len(hit.group()), masked)
    for m in _RUN.finditer(masked):
        if "\n" in m.group():
            spans += _cards(text, m, luhn.min_digits, luhn.max_digits)
    return _merge(spans)


def _breaks(line: str) -> bool:
    """`detect_sensitive` finds something other than a year range."""
    if not detect_sensitive(line):
        return False
    for start, end in sensitive_spans(line):
        if _year_range(line, start, end):
            line = line[:start] + "_" * (end - start) + line[end:]
    return bool(detect_sensitive(line))


def _line(line: str, private: PrivateRules | None) -> str:
    if private:
        line = _replace(line, private.spans(line), PRIVATE_LABEL)
    line = _replace(line, _widen(line, _spans(_view(line))), MASK)
    if _breaks(_view(line)) or (private and private.check(line)):
        return MASK
    return line


def filter_text(text: str, private: PrivateRules | None = None) -> str:
    """Filter a whole text (see the module notes)."""
    text = normalize(_dashes(_LINE_END.sub("\n", text)))
    if private:
        text = _replace(text, private.spans(text), PRIVATE_LABEL)
    text = _replace(text, _broken_numbers(text), MASK)
    return "\n".join(_line(line, private) for line in text.split("\n"))


Emit = tuple[str, str]  # ("text", piece to append) or ("replace", the whole text so far)


# Four digits not joined to other digits, so x1234, …1234, -1234 or *1234 count.
# Not part of an amount ($1234, $-1234, 1234.56), a date, a year range or a
# longer number.
_FOUR = re.compile(r"(?<![\d$])(?<!\d[.,-])(?<!\$-)(\d{4})(?!\d|[.,-]\d)")
# A last four that is also a year (1900 to 2099) counts only after a card cue.
# Stars right before it that open emphasis (1 to 3 stars, closed later on the
# line, as in a bold year or a bold label that starts with one) are read past.
# - Right before it: x, #, •, …, ..., one or more "*", a "-" with no letter,
#   digit or "*" before it (-2026, not mid-2026), a masked group and a dash
#   ("x-", or a run of x, * or • and "-", as in a card written with X groups,
#   with no letter or digit before it), a run and a space (4 or more "*", 2 or
#   more "•" or "x"), or a card word with no space (card2026).
# - A card or account word with "#", "no." or "number" (card no. 2026).
# - "ending", "ending in" or "ending with" (an optional ":" or "#" after it),
#   unless a time word comes just before it (year ending 2026).
# - A card word alone (card, acct, account, chk, last four) with an optional
#   ":", only when no word follows the four (card 2026, not card 2026 spending).
_CUE = re.compile(
    r"(?:[x#…•]|\.\.\.|\*+|(?<![A-Za-z0-9*])-|(?<![A-Za-z0-9*•])[x*•]+-"
    r"|(?:\*{4,}|•{2,}|(?<![A-Za-z0-9])x{2,}) |\b(?:card|acct|account|chk)"
    r"|\b(?:card|acct|account|chk|checking|savings)\.?\s*(?:#|no\b\.?|number\b)[\s:#]*)\Z",
    re.IGNORECASE,
)
_ENDING = re.compile(r"\bending(?:\s+(?:in|with))?[\s:#]*\Z", re.IGNORECASE)
_TIME_WORD = re.compile(
    r"\b(?:year|yr|fy|month|quarter|qtr|week|day|decade|period|term|lease|season|semester)s?"
    r"\W*\Z",
    re.IGNORECASE,
)
_CARD_WORD = re.compile(
    r"(?:\b(?:card|acct|account|chk)|\blast\s+(?:four|4)(?:\s+digits)?)\.?[\s*:]*\Z",
    re.IGNORECASE,
)
_WORD_AFTER = re.compile(r"\**[ \t]*(?:[-/][ \t]*)?\w")
# The closing stars of emphasis opened by 1, 2 or 3 stars.
_CLOSE = {n: re.compile(rf"(?<=[^\s*])\*{{{n}}}(?!\*)") for n in (1, 2, 3)}
_LETTER = re.compile(r"[A-Za-z]")


def _is_last4(m: re.Match[str], last4s: Collection[str]) -> bool:
    """A `_FOUR` match is a known last four, and not a plain year."""
    four = m.group(1)
    if four not in last4s:
        return False
    if not _YEAR.fullmatch(four):
        return True
    text, low, at = m.string, max(0, m.start() - 40), m.start()
    stars = len(text[low:at]) - len(text[low:at].rstrip("*"))
    line_end = text.find("\n", m.end())
    if line_end < 0:
        line_end = len(text)
    if stars in _CLOSE and _CLOSE[stars].search(text, m.end(), line_end):
        at -= stars  # emphasis that the same line closes: read past its stars
    if _CUE.search(text, low, at):
        return True
    ending = _ENDING.search(text, low, at)
    if ending and not _TIME_WORD.search(text, low, ending.start()):
        return True
    return bool(_CARD_WORD.search(text, low, at)) and not _WORD_AFTER.match(text, m.end())


def load_last4s(db_path: str | Path) -> frozenset[str]:
    """Every account's last four digits, read by the app (never sent) for `Scrub`."""
    conn = connect(db_path, readonly=True)
    try:
        rows = conn.execute("SELECT last4 FROM accounts WHERE last4 IS NOT NULL").fetchall()
    finally:
        conn.close()
    return frozenset(r[0] for r in rows if r[0])


def hides_last4(text: str, last4s: Iterable[str]) -> bool:
    """True when free text (it has letters) holds a known last four as its own word."""
    found = set(last4s)
    if not found or not _LETTER.search(text):
        return False
    return any(_is_last4(m, found) for m in _FOUR.finditer(text))


class Scrub:
    """The last pass on everything sent to Anthropic (PLAN 5.7).

    - Private-list names become "personal subscription". A name added to the
      list after an import is still in the data until a privacy rescan.
    - In free text, a group of four digits that is an account's last four
      becomes "…". One that is also a year (1900 to 2099) does only after a
      card cue (x2026, …2026, a run of stars, "card #2026", "Amex ending in
      2026", "card 2026" with no word after it), so a plain year stays.
      Amounts, dates and year ranges are left alone.
    """

    def __init__(self, private: PrivateRules | None, last4s: Iterable[str] = ()) -> None:
        self.private = private
        self.last4s = frozenset(last4s)

    def text(self, value: str) -> str:
        if self.private:
            spans = self.private.spans(value)
            if spans:
                value = _replace(value, spans, PRIVATE_LABEL)
        if self.last4s and _LETTER.search(value):
            value = _FOUR.sub(lambda m: MASK if _is_last4(m, self.last4s) else m.group(0), value)
        return value

    def json(self, value: Any) -> Any:
        """The same structure with every string scrubbed, keys too. Tool results
        keep data out of keys; this is the backstop for a key that holds a name."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                (self.text(k) if isinstance(k, str) else k): self.json(v) for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.json(v) for v in value]
        return value


class StreamFilter:
    """Filter text that arrives in pieces. Emits at sentence or line ends only.

    After each emit, the browser holds exactly `filter_text(raw[:cut])`. If that
    still starts with what was sent, only the new part goes out ("text").
    Otherwise the whole filtered text goes out ("replace"). `text`, sent at the
    end of the turn, is the whole answer filtered at once.
    """

    def __init__(self, private: PrivateRules | None = None) -> None:
        self.private = private
        self.raw = ""  # everything received
        self.cut = 0  # raw[:cut] has been emitted
        self.emitted = ""  # what the browser shows now

    def _emit(self, upto: int) -> Emit | None:
        if upto <= self.cut:
            return None
        whole = filter_text(self.raw[:upto], self.private)
        self.cut = upto
        if whole.startswith(self.emitted):
            out = whole[len(self.emitted) :]
            self.emitted = whole
            return ("text", out) if out else None
        self.emitted = whole
        return ("replace", whole)

    def _can_cut(self, m: re.Match[str]) -> bool:
        """A line end, or a sentence end on a line with a letter and no open bold."""
        if m.group() == "\n":
            return True
        line = self.raw[self.raw.rfind("\n", 0, m.start()) + 1 : m.end()]
        return bool(_ALPHA.search(line)) and not _OPEN_BOLD.search(_BOLD.sub("", line))

    def feed(self, delta: str) -> Emit | None:
        """Add text. Returns what to send now, if anything."""
        self.raw += delta
        upto = 0
        for m in _BOUNDARY.finditer(self.raw, self.cut):
            if self._can_cut(m):
                upto = m.end()
        return self._emit(upto)

    def separate(self) -> None:
        """Start a new paragraph (text from the next model call follows)."""
        if self.raw and not self.raw.endswith("\n"):
            self.raw += "\n\n"

    def finish(self) -> Emit | None:
        return self._emit(len(self.raw))

    @property
    def text(self) -> str:
        """The whole answer, filtered at once."""
        return filter_text(self.raw, self.private)
