"""Phase 6 fix pass 4: the output filter across lines, the year range next to
other numbers, and the card cues for a last four that is also a year.

A fuzz harness with a fixed seed has two suites: (a) leaks that must be masked,
(b) normal answers that must stay byte for byte. Fake numbers only, built at
runtime so the repo scanner never sees a full one."""

from __future__ import annotations

import random
import re
from collections.abc import Callable

import pytest

from app.ai.postprocess import (
    MASK,
    PRIVATE_LABEL,
    Scrub,
    StreamFilter,
    filter_text,
    hides_last4,
)
from app.importer.sanitize import PrivateRules, luhn_ok

SEED = 1022
CASES = 400
_DIGIT = re.compile(r"[0-9]")


def digits(text: str) -> str:
    return "".join(_DIGIT.findall(text))


def stream(text: str, rng: random.Random, private: PrivateRules | None = None):
    """Feed `text` in random chunk sizes. Returns every state the browser shows."""
    sf = StreamFilter(private)
    events, i = [], 0
    while i < len(text):
        k = rng.randint(1, 9)
        events.append(sf.feed(text[i : i + k]))
        i += k
    events.append(sf.finish())
    states, now = [], ""
    for ev in events:
        if ev:
            now = now + ev[1] if ev[0] == "text" else ev[1]
            states.append(now)
    return states, sf


def check_all(make: Callable[[random.Random], tuple[str, str]], test: Callable) -> None:
    """Run `test(text, want, rng)` on CASES (text, want) pairs from `make`."""
    rng = random.Random(SEED)
    bad = []
    for _ in range(CASES):
        text, want = make(rng)
        problem = test(text, want, rng)
        if problem:
            bad.append((text, problem))
    assert not bad, f"{len(bad)}/{CASES} failed, e.g. {bad[:3]!r}"


# ==========================================================================
# Suite (a): leaks that must be masked
# ==========================================================================

# A break: one newline, spaces and one "." or "-" next to it, CRLF or CR.
BREAKS = [
    "\n", " \n", "\n ", " \n ", "\t\n", "\r\n", "\r", " \r\n ",
    "-\n", "\n-", ".\n", "\n.", " -\n", "- \n", ". \n", "-\r\n", ".\r\n", "\r\n-",
]  # fmt: skip
SAME_LINE = [" ", "-", "."]
NUMBER_CONTEXTS = [
    "Card {} was used.",
    "Your card is {}",
    "{}",
    "Number: {}.",
    "- Card:\n{}\nThanks.",
    "Pay with {}, then rent.",
    "Use {} today! Then stop.",
    "Call {} today.",
    "Phone: {}",
    "Their number is {}\nok",
]


def in_context(number: str, rng: random.Random, contexts: list[str]) -> tuple[str, str]:
    context = rng.choice(contexts)
    return context.replace("{}", number), context.replace("{}", MASK)


def luhn_card(rng: random.Random) -> list[str]:
    """A Luhn-valid card in groups of 4 or more digits, not every group a year."""
    prefix, sizes = rng.choice(
        [("4", [4, 4, 4, 4]), ("51", [4, 4, 4, 4]), ("6011", [4, 4, 4, 4]),
         ("37", [4, 6, 5]), ("4", [4, 4, 5]), ("2221", [4, 4, 4, 4])]
    )  # fmt: skip
    body = prefix + "".join(str(rng.randrange(10)) for _ in range(sum(sizes) - len(prefix) - 1))
    number = next(body + c for c in "0123456789" if luhn_ok(body + c))
    groups, i = [], 0
    for size in sizes:
        groups.append(number[i : i + size])
        i += size
    return groups


def broken_card(rng: random.Random) -> tuple[str, str]:
    groups = luhn_card(rng)
    gaps = [rng.choice(SAME_LINE) for _ in groups[1:]]
    for i in rng.sample(range(len(gaps)), rng.randint(1, len(gaps))):
        gaps[i] = rng.choice(BREAKS)
    number = groups[0] + "".join(g + n for g, n in zip(gaps, groups[1:], strict=True))
    return in_context(number, rng, NUMBER_CONTEXTS)


def broken_phone(rng: random.Random) -> tuple[str, str]:
    a, b, c = str(rng.randint(201, 989)), str(rng.randint(200, 999)), f"{rng.randrange(10_000):04d}"
    sep = rng.choice(["-", "."])
    head, gap1 = rng.choice([(f"({a})", " "), (f"({a})", ""), (a, sep)])
    gap2 = rng.choice([sep, " "]) if head.startswith("(") else sep
    brk = rng.choice(["\n", " \n", "\n ", " \n ", "\t\n", "\r\n", "\r"])
    mode = rng.choice(["replace", "after", "before"])

    def broken(gap: str) -> str:
        if mode == "replace" or not gap.strip():
            return gap + brk if mode == "after" else brk
        return gap + brk if mode == "after" else brk + gap

    if rng.random() < 0.5:
        number = head + broken(gap1) + b + gap2 + c
    else:
        number = head + gap1 + b + broken(gap2) + c
    return in_context(number, rng, [*NUMBER_CONTEXTS, "+1 {} works"])


def masked_number(text: str, want: str, rng: random.Random) -> str | None:
    """The number is masked, by filter_text and by the stream at every step."""
    out = filter_text(text)
    if out != want:
        return f"filter_text gave {out!r}"
    number = digits(text.removeprefix("+1 "))
    states, sf = stream(text, rng)
    if states[-1] != out or sf.text != out:
        return f"stream ended with {states[-1]!r}"
    for state in states:
        if number in digits(state):
            return f"stream showed {state!r}"
    return None


def test_fuzz_cards_broken_over_lines_are_masked() -> None:
    check_all(broken_card, masked_number)


def test_fuzz_phones_broken_over_lines_are_masked() -> None:
    check_all(broken_phone, masked_number)


# A last four after every card cue form. "{}" is the four digits.
CUE_FORMS = [
    "Summit card x{}",
    "POS DEBIT CARD X{} HARBOR",
    "Card #{} was used",
    "card # {}",
    "Checking …{}",
    "ONLINE PMT TO CHK ...{}",
    "Visa •{}",
    "ACH DEBIT -{} HARBOR",
    "-{} HARBOR",
    "Card *{} was used",
    "card {}",
    "PURCHASE AUTHORIZED ON 10/15 CARD {}",
    "card{}",
    "acct {}",
    "Acct #: {}",
    "Acct: {}",
    "account {}",
    "Account No. {}",
    "Card No {}",
    "card no.: {}",
    "CHK {}",
    "chk#{}",
    "card ending {}",
    "card ending in {}",
    "card ending in: {}",
    "card ending #{}",
    "card ending in # {}",
    "Account ending in {}",
    "acct ending: {}",
    "checking ending in {}",
    "savings ending {}",
    "Visa ending in {}",
    "Mastercard ending in {}",
    "Amex ending {}",
    "card number ending in {}",
    "card ending\tin {}",
    "card ending\nin {}",
    "card  ending  in  {}",
    "card ending in…{}",
]
YEAR_FOURS = ["2026", "2019", "1999", "1907"]
OTHER_FOUR = "4471"


# Fix pass 5: a card word alone counts only when no word follows the four, so
# "card 2019 spending" stays a plain question. These forms get no word after.
BARE_WORD_FORMS = {
    "card {}",
    "PURCHASE AUTHORIZED ON 10/15 CARD {}",
    "acct {}",
    "Acct: {}",
    "account {}",
    "CHK {}",
}


def vary(form: str, rng: random.Random) -> str:
    """The same cue in another case, with something after it."""
    endings = ["", ".", ",", " first", "?", " was paid."]
    if form in BARE_WORD_FORMS:
        endings = ["", ".", ",", "?"]
    case = rng.choice([str.lower, str.upper, str.title, str, None])
    if case is None:
        form = "".join(ch.upper() if rng.random() < 0.5 else ch.lower() for ch in form)
    else:
        form = case(form)
    return form + rng.choice(endings)


@pytest.mark.parametrize("four", [*YEAR_FOURS, OTHER_FOUR])
def test_fuzz_last_four_after_every_cue_is_masked(four: str) -> None:
    rng = random.Random(SEED)
    last4s = {four, OTHER_FOUR, "1099"}
    scrub = Scrub(None, last4s)
    bad = []
    for form in CUE_FORMS:
        for _ in range(8):
            cue = vary(form, rng)
            text, want = cue.replace("{}", four), cue.replace("{}", MASK)
            if scrub.text(text) != want or scrub.json({"q": [text]}) != {"q": [want]}:
                bad.append((text, scrub.text(text)))
            elif not hides_last4(text, last4s):
                bad.append((text, "hides_last4 is False"))
    assert not bad, f"{len(bad)} failed, e.g. {bad[:5]!r}"


PRIVATE = PrivateRules(
    subscriptions=("Example Private Service", "Nightowlx", "Glowbox Plus"),
    employer_terms=("Initrode Systems",),
)
LOOK_ALIKES = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "i": "і", "y": "у"}


def private_variant(rng: random.Random) -> tuple[str, str]:
    name = rng.choice([*PRIVATE.subscriptions, *PRIVATE.employer_terms])
    kind = rng.choice(
        ["case", "split", "split_case", "zero_width", "soft_hyphen", "full_width", "look_alike"]
    )
    if kind in ("case", "split_case"):
        name = "".join(ch.upper() if rng.random() < 0.5 else ch.lower() for ch in name)
    if kind in ("split", "split_case"):
        k = rng.randint(1, len(name) - 1)
        name = name[:k] + rng.choice(["\n", "\r\n", "\n\n", " \n", "\n- ", "\r"]) + name[k:]
    elif kind in ("zero_width", "soft_hyphen"):
        k = rng.randint(1, len(name) - 1)
        name = name[:k] + ("​" if kind == "zero_width" else "­") + name[k:]
    elif kind == "full_width":
        name = "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in name)
    elif kind == "look_alike":
        name = "".join(LOOK_ALIKES.get(ch, ch) if rng.random() < 0.5 else ch for ch in name)
    contexts = ["You paid {} $12.99.", "{}", "- {}\n- Rent", "Top: {}. Next: rent.", "From {} too."]
    return in_context(name, rng, contexts)


def masked_private(text: str, want: str, rng: random.Random) -> str | None:
    out = filter_text(text, PRIVATE)
    if PRIVATE.check(out) or PRIVATE_LABEL not in out:
        return f"filter_text gave {out!r}"
    states, sf = stream(text, rng, PRIVATE)
    if states[-1] != out or sf.text != out:
        return f"stream ended with {states[-1]!r}"
    for state in states:
        if PRIVATE.check(state):
            return f"stream showed {state!r}"
    return None


def test_fuzz_private_names_are_masked() -> None:
    check_all(private_variant, masked_private)


# ==========================================================================
# Suite (b): normal answers that stay byte for byte
# ==========================================================================

LABELS = ["Rent", "Groceries", "Dining out", "Gas", "Utilities", "Travel", "Gym"]
MONTHS = ["Jan", "Feb", "March", "Oct", "October"]
YEARS = [str(y) for y in range(2015, 2028)]


def amount(rng: random.Random) -> str:
    """Dollars with commas (never a bare run of 5 or more digits)."""
    value = rng.choice([rng.randint(1, 999), rng.randint(1_000, 99_999), rng.randint(1, 999_999)])
    text = f"{value:,}" + (f".{rng.randrange(100):02d}" if rng.random() < 0.4 else "")
    return rng.choice(["$", "$", "-$", ""]) + text


def benign_block(rng: random.Random) -> str:
    start = rng.randint(2015, 2022)
    years = range(start, start + rng.randint(2, 6))
    y1, y2 = sorted(rng.sample(range(2015, 2027), 2))
    n, y, label = rng.randint(1, 99), rng.choice(YEARS), rng.choice(LABELS)
    bullet = rng.choice(["", "", "- ", "* "])
    kind = rng.randrange(14)
    if kind == 0:
        lines = ["Net worth by year:"] + [f"{bullet}{yr}: {amount(rng)}" for yr in years]
    elif kind == 1:
        lines = ["Spending by year"] + [f"{bullet}{yr} {amount(rng)}" for yr in years]
    elif kind == 2:
        lines = ["Visits by year:"] + [f"{bullet}{yr}: {rng.randint(1, 999)}" for yr in years]
    elif kind == 3:
        # Bare 4-digit counts never come two in a row here: four of them, one
        # per line, read as a card broken over lines when they pass the Luhn check.
        lines = ["Visits per month:"] + [
            rng.choice([str(rng.randint(1, 999)), f"{rng.randint(1_000, 9_999):,}"])
            if i % 2
            else str(rng.randint(1, 1999))
            for i in range(rng.randint(2, 8))
        ]
    elif kind == 4:
        lines = ["Totals:"] + [f"{amount(rng)}" for _ in range(rng.randint(2, 8))]
    elif kind == 5:
        lines = [
            f"{n}. {a}-{b}: {amount(rng)} on {label}"
            for n, (a, b) in enumerate(
                [sorted(rng.sample(range(2015, 2027), 2)) for _ in range(rng.randint(2, 14))], 1
            )
        ]
    elif kind == 6:
        lines = [
            rng.choice(
                [
                    f"Top {n} {y1}-{y2} merchants",
                    f"You spent {amount(rng)}. {y1}-{y2} was your best stretch.",
                    f"Trips {y1}-{y2}. {rng.randint(1, 999)} trips in all.",
                    f"- {y1}-{y2} - {n} trips",
                    f"{y1}-{y2}: {rng.randint(1, 999)} visits",
                    f"From {y1}-{y2}, {n} trips.",
                    f"{n}. {y1}-{y2} total",
                ]
            )
        ]
    elif kind == 7:
        lines = [
            rng.choice(
                [
                    f"How much will we have saved by mid-{y}?",
                    f"Plan for Jan-{y}",
                    f"Budget for **{y}**",
                    f"**Best year:** {y} at {amount(rng)}",
                    f"You made **{n}** trips, **{rng.randint(1, 99)}.{rng.randrange(10)}%** more.",
                    f"Year ending {y} totals",
                    f"The year ending in {y} was good.",
                    f"Spending pre-{y} vs now",
                    f"Your end-of-{y} goal",
                    f"*{y}* plan",
                    f"What did we spend in {y}?",
                    f"In {y}, you spent {amount(rng)}.",
                    f"What changed in FY{y}?",
                ]
            )
        ]
    elif kind == 8:
        lines = [
            f"Paid on {rng.choice(MONTHS)} {rng.randint(1, 28)}, {y}",
            f"Due {rng.randint(1, 12)}/{rng.randint(1, 28)}/{y}",
            f"Posted {y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        ]
    elif kind == 9:
        lines = [
            f"{label} is {rng.randint(1, 99)}.{rng.randrange(10)}% of spending",
            f"Savings rate: {rng.randint(1, 100)}%",
            f"Up {rng.randint(1, 99)}% from {y}",
        ]
    elif kind == 10:
        lines = ["Years:"] + [str(yr) for yr in years]
    elif kind == 11:
        lines = ["| Year | Spent | Share |", "| --- | --- | --- |"] + [
            f"| {yr} | {amount(rng)} | {rng.randint(1, 99)}.{rng.randrange(10)}% |" for yr in years
        ]
    elif kind == 12:
        lines = [f"{rng.choice(MONTHS)} {yr}: {amount(rng)}" for yr in years]
    else:
        lines = [
            f"You spent {amount(rng)} on {label} in {rng.choice(MONTHS)} {y}.",
            f"That is {amount(rng)} more than {y1}.",
        ]
    if kind in (8, 9):
        rng.shuffle(lines)
    return "\n".join(lines)


def benign_answer(rng: random.Random) -> tuple[str, str]:
    blocks = [benign_block(rng) for _ in range(rng.randint(1, 4))]
    text = "".join(b + rng.choice(["\n", "\n\n"]) for b in blocks[:-1]) + blocks[-1]
    return text, text


SCRUB_LAST4S = {*YEARS, OTHER_FOUR}


def unchanged(text: str, want: str, rng: random.Random) -> str | None:
    out = filter_text(text)
    if out != text:
        return f"filter_text gave {out!r}"
    states, sf = stream(text, rng)
    if states[-1] != text or sf.text != text:
        return f"stream ended with {states[-1]!r}"
    if any(not text.startswith(state) for state in states):
        return "stream sent a replace"
    scrub = Scrub(None, SCRUB_LAST4S)
    if scrub.text(text) != text or hides_last4(text, SCRUB_LAST4S):
        return f"scrub gave {scrub.text(text)!r}"
    return None


def test_fuzz_normal_answers_stay_byte_for_byte() -> None:
    check_all(benign_answer, unchanged)


# ==========================================================================
# The review's evidence, as plain cases
# ==========================================================================

REF = "657" + "13"  # five digits
# Built at runtime, so the repo scanner does not read them as account forms.
YR = "-".join(["2019", "2026"])  # a year range
B = "*" * 2  # markdown bold
KEPT_ANSWERS = [
    "Net worth by year:\n2019: $120,400\n2020: $135,250\n2021: $151,900\n2022: $160,300",
    "Header:\n2019: $574,354\n2020: $686,963\n2021: $998,485\n2022: $919,166",
    "2019: 202\n2020: 488\n2021: 718",
    "Visits per month:\n120\n450\n1200",
    f"10. {YR}: $4,000 on travel",
    f"12. {YR} total",
    f"You spent $4,000. {YR} was your best stretch.",
    f"Trips {YR}. 14 trips in all.",
    f"Top 10 {YR} merchants",
    f"- {YR} - 14 trips",
    f"Budget for {B}2026{B}",
    f"{B}Best year:{B} 2024 at $188,900",
    f"You made {B}14{B} trips, {B}12.5%{B} more.",
]


@pytest.mark.parametrize("text", KEPT_ANSWERS)
def test_normal_answer_is_kept(text: str) -> None:
    assert filter_text(text) == text
    states, sf = stream(text, random.Random(SEED))
    assert states[-1] == text == sf.text


def test_a_long_number_in_parens_does_not_join_the_next_line() -> None:
    text = f"Ref ({REF})\n1260, 2018, 2023"
    assert filter_text(text) == f"Ref ({MASK})\n1260, 2018, 2023"


def card_groups() -> list[str]:
    head = "4153" + "7760" + "2077"
    last = next(f"{n:04d}" for n in range(10_000) if luhn_ok(head + f"{n:04d}"))
    return ["4153", "7760", "2077", last]


@pytest.mark.parametrize("brk", ["\r\n", "\r", "-\n", "\n-", ".\n", " -\n", "-\r\n"])
def test_card_broken_by_crlf_or_a_dash_or_dot_is_masked(brk: str) -> None:
    text = f"Card {brk.join(card_groups())} was used."
    assert filter_text(text) == f"Card {MASK} was used."


@pytest.mark.parametrize(
    "number",
    [
        "555-\n201-0147",
        "(555)\r\n201-0147",
        "555-201\r\n0147",
        "555.201.\n0147",
        "(555) 201-\n0147",
    ],
)
def test_phone_broken_over_lines_is_masked(number: str) -> None:
    assert filter_text(f"Call {number} today.") == f"Call {MASK} today."


def test_crlf_becomes_lf() -> None:
    assert filter_text("Rent is up.\r\nGas is down.\rDone.") == "Rent is up.\nGas is down.\nDone."


LAST4S = {"2026", "4471", "1099"}
SCRUB_KEPT = [
    "How much will we have saved by mid-2026?",
    "Spending pre-2026 vs now",
    "Plan for Jan-2026",
    f"Budget for {B}2026{B}",
    "*2026* plan",
    "end-of-2026 goal",
    "Year ending 2026 totals",
    "the year ending in 2026",
    "Scorecard 2026 results",
]
SCRUB_MASKED = [
    "PURCHASE AUTHORIZED ON 10/15 CARD {}",
    "Visa •{}",
    "card ending in: {}",
    "acct {}",
    "Acct #: {}",
    "Account {}",
    "Card No. {}",
    "CHK {}",
    "Visa ending in {}",
]


@pytest.mark.parametrize("text", SCRUB_KEPT)
def test_scrub_keeps_a_year_in_ordinary_wording(text: str) -> None:
    assert Scrub(None, LAST4S).text(text) == text
    assert not hides_last4(text, LAST4S)


@pytest.mark.parametrize("form", SCRUB_MASKED)
def test_scrub_masks_a_year_like_last_four_after_a_bank_form(form: str) -> None:
    assert Scrub(None, LAST4S).text(form.format("2026")) == form.format(MASK)
    assert hides_last4(form.format("2026"), LAST4S)
