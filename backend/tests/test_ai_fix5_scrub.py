"""Phase 6 fix pass 5: phones and cards broken over lines next to other digits,
the card cues for a last four that is also a year, two years at a sentence end,
and the stream inside an open bold span.

Cases come from the final review's harness. Fake numbers only, built at runtime
so the repo scanner never sees a full one."""

from __future__ import annotations

import random
import re

import pytest

from app.ai.postprocess import MASK, Scrub, StreamFilter, filter_text, hides_last4
from app.importer.sanitize import luhn_ok

SEED = 1022
_DIGIT = re.compile(r"[0-9]")
B = "*" * 2  # markdown bold
STARS = "*" * 4
XS = "X" * 4


def digits(text: str) -> str:
    return "".join(_DIGIT.findall(text))


def states(text: str, rng: random.Random) -> tuple[list[str], StreamFilter]:
    """Feed `text` in random chunk sizes. Returns every state the browser shows."""
    sf = StreamFilter()
    events, i = [], 0
    while i < len(text):
        k = rng.randint(1, 12)
        events.append(sf.feed(text[i : i + k]))
        i += k
    events.append(sf.finish())
    shown, now = [], ""
    for ev in events:
        if ev:
            now = now + ev[1] if ev[0] == "text" else ev[1]
            shown.append(now)
    return shown, sf


def assert_masked(text: str, secret: str, want: str | None = None) -> None:
    """filter_text hides `secret`, and so does every stream state (3 chunkings)."""
    out = filter_text(text)
    assert secret not in digits(out), out
    if want is not None:
        assert out == want
    rng = random.Random(SEED)
    for _ in range(3):
        shown, sf = states(text, rng)
        assert shown[-1] == out == sf.text
        for state in shown:
            assert secret not in digits(state), state


def assert_kept(text: str) -> None:
    """filter_text keeps `text`, and the stream never shows anything else."""
    assert filter_text(text) == text
    rng = random.Random(SEED)
    for _ in range(3):
        shown, sf = states(text, rng)
        assert shown[-1] == text == sf.text
        assert all(text.startswith(state) for state in shown), shown


# ==========================================================================
# 1. A phone number broken over a line: spaced, mixed, or after 1- or +1-
# ==========================================================================

A, P, C = "415", "555", "01" + "42"
PHONE = A + P + C
TOLL, TOLL_C = "800", "01" + "99"


@pytest.mark.parametrize(
    ("number", "shown"),
    [
        (f"{A} {P}\n{C}", MASK),
        (f"{A}\n{P} {C}", MASK),
        (f"{A} \n{P} {C}", MASK),
        (f"+1 {A} {P}\n{C}", f"+1 {MASK}"),
        (f"{A} {P}-\n{C}", MASK),
        (f"{A}  {P}\n{C}", MASK),
        (f"{A} -\n{P} - {C}", MASK),
        (f"+1-{A}-{P}-\n{C}", f"+1-{MASK}"),
        (f"1.{A}.{P}.\n{C}", f"1.{MASK}"),
    ],
)
def test_phone_broken_over_a_line_is_masked(number: str, shown: str) -> None:
    assert_masked(f"Call {number} today.", PHONE, f"Call {shown} today.")


def test_toll_free_number_broken_after_a_one_is_masked() -> None:
    number = f"1-{TOLL}-{P}-\n{TOLL_C}"
    assert_masked(f"Call {number} today.", TOLL + P + TOLL_C, f"Call 1-{MASK} today.")


# The review's phones.py: 14 layouts, each gap broken 4 ways.
PHONE_LAYOUTS = [
    ("(", A, ") ", P, "-", C), ("(", A, ")", P, "-", C), ("", A, "-", P, "-", C),
    ("", A, ".", P, ".", C), ("", A, " ", P, " ", C), ("", A, " ", P, "-", C),
    ("+1 ", A, " ", P, " ", C), ("+1 (", A, ") ", P, "-", C), ("1-", A, "-", P, "-", C),
    ("+1-", A, "-", P, "-", C), ("+1 ", A, "-", P, "-", C), ("1 (", A, ") ", P, "-", C),
    ("1.", A, ".", P, ".", C), ("1 ", A, " ", P, " ", C),
]  # fmt: skip


def broken(layout: tuple[str, ...], gap: int, brk: str) -> str:
    """The number with a break in place of gap 1 or 2 (after its "." or "-")."""
    pre, a, s1, b, s2, c = layout
    if gap == 1:
        s1 = s1.rstrip() + brk
    else:
        s2 = s2.rstrip() + brk
    return pre + a + s1 + b + s2 + c


PHONE_CASES = [
    broken(layout, gap, brk)
    for layout in PHONE_LAYOUTS
    for gap in (1, 2)
    for brk in ("\n", " \n", "\n ", "\r\n")
]


@pytest.mark.parametrize("number", PHONE_CASES)
def test_every_phone_layout_broken_over_a_line_is_masked(number: str) -> None:
    assert_masked(f"Call {number} today.", PHONE)


def test_fuzz_phones_broken_over_a_line_are_masked() -> None:
    """The review's leaks.py phone layouts, seeded."""
    rng = random.Random(SEED)
    bad = []
    for _ in range(300):
        a = rng.choice(["202", "312", "415", "617", "718", "800", "888"])
        c = "01" + f"{rng.randrange(100):02d}"
        pre, s1, s2 = rng.choice(
            [("(", ") ", "-"), ("", " ", " "), ("+1 ", " ", " "), ("+1 (", ") ", "-"),
             ("1-", "-", "-"), ("+1-", "-", "-"), ("", " ", "-"), ("1.", ".", "."),
             ("+1 ", "-", "-"), ("", "/", "-"), ("1 ", " ", " ")]
        )  # fmt: skip
        if pre.endswith("("):
            a, pre = f"({a}", pre[:-1]
        brk = rng.choice(["\n", " \n", "\n ", "\r\n", "  \n"])
        number = broken((pre, a, s1, P, s2, c), rng.choice([1, 2]), brk)
        text = rng.choice(
            ["Call {} to dispute it.", "Phone: {}", "**{}**", "Reach them at\n{}\ntoday."]
        )
        text = text.replace("{}", number)
        secret = digits(a) + P + c
        out = filter_text(text)
        shown, _ = states(text, rng)
        if secret in digits(out) or any(secret in digits(s) for s in shown):
            bad.append((text, out))
    assert not bad, f"{len(bad)} leaked, e.g. {bad[:3]!r}"


# ==========================================================================
# 2. A card one group per line, next to another digit group
# ==========================================================================


def card_groups() -> list[str]:
    head = "4532" + "0151" + "1283"
    last = next(f"{n:04d}" for n in range(10_000) if luhn_ok(head + f"{n:04d}"))
    return ["4532", "0151", "1283", last]


GROUPS = card_groups()
CARD = "".join(GROUPS)
LINES = "\n".join(GROUPS)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (f"Card {LINES} 12 charges.", f"Card {MASK} 12 charges."),
        (f"Card {LINES}\n2026 charges.", f"Card {MASK}\n2026 charges."),
        (f"Card 12 {LINES}.", f"Card 12 {MASK}."),
        (f"Card 12\n{LINES}\n34 charges.", f"Card 12\n{MASK}\n34 charges."),
        (f"Card {LINES}\n2026: 12 charges.", f"Card {MASK}\n2026: 12 charges."),
        (f"Card {LINES}. 12 charges.", f"Card {MASK}. 12 charges."),
    ],
)
def test_card_one_group_per_line_next_to_other_digits_is_masked(text: str, want: str) -> None:
    assert_masked(text, CARD, want)


def test_a_list_of_numbers_one_per_line_is_kept() -> None:
    # Four of them pass the Luhn check, but a fifth stands alone on its line.
    # (While streaming, the four are masked until the fifth arrives.)
    extra = next(
        f"{n:04d}" for n in range(1000, 10_000) if not luhn_ok("".join(GROUPS[1:]) + f"{n:04d}")
    )
    text = f"Visits per month:\n{LINES}\n{extra}"
    assert filter_text(text) == text
    shown, sf = states(text, random.Random(SEED))
    assert shown[-1] == text == sf.text


def test_a_window_does_not_cut_a_year_range() -> None:
    ranges = ["-".join(["2016", "2017"]), "-".join(["2018", "2020"])]
    assert luhn_ok("1199" + "2016" + "2017" + "2018")
    assert_kept(f"1199\n{ranges[0]}\n{ranges[1]} (55%)")


# ==========================================================================
# 3. Card cues for a last four that is also a year
# ==========================================================================

LAST4S = {"2019", "4471"}
CUE_FORMS = [
    # "ending" or "ending in" after any word but a time word
    "Travel Card ending in {}",
    "Discover ending in {}",
    "Citi ending {}",
    "my debit ending in {}",
    "Visa Signature ending in {}",
    "Chase Freedom ending in {}",
    "the one ending in {}",
    "Sapphire ending with {}",
    f"card ending in {B}{{}}{B}",
    # a run of stars or x, or masked groups and a dash
    f"Paid with Visa {STARS}{{}}",
    f"Card {B}{{}} charged",
    f"Card {STARS}{{}} charged",
    f"Paid with card {XS}-{XS}-{XS}-{{}} today",
    f"Paid with card {STARS}-{STARS}-{{}} today",
    "Card x-{} today",
    f"Card {STARS} {{}} was used",
    f"{XS.lower()} {XS.lower()} {{}} was used",
    # card number words
    "card number {} spending",
    "account number {}",
    "Card No. {} was paid",
    "last four {}",
    "last 4: {}",
    f"Card {B}{{}}{B}",
]


@pytest.mark.parametrize("form", CUE_FORMS)
def test_scrub_masks_a_year_like_last_four_in_a_card_form(form: str) -> None:
    text, want = form.replace("{}", "2019"), form.replace("{}", MASK)
    assert Scrub(None, LAST4S).text(text) == want
    assert hides_last4(text, LAST4S)


PLAIN_FORMS = [
    # plain questions: a card word and a year with a word after it
    "account {} summary?",
    "What did my credit card {} bill total?",
    "how much on my card {} vs 2020",
    "Card: {} was the worst year",
    "card {} spending",
    f"My card {B}{{}}{B} spending was high",
    # "ending" after a time word
    "the year ending in {}",
    "the lease ending in {}",
    "The quarter ending in {}",
    "the 12 months ending {}",
    "the period ending in {}",
    "the term ending in {}",
    "Year ending {} totals",
    # emphasis and plain years
    f"Budget for {B}{{}}{B}",
    "*{}* plan",
    f"{B}Note:{B} {{}} was high.",
    "Spending mid-{} vs now",
    f"{B}{{}} total:{B} $5,000",
    f"Card {B}{{}} total:{B} $5,000",
    f"{B}2018{B}-{B}{{}}{B} spending fell.",
]


@pytest.mark.parametrize("form", PLAIN_FORMS)
def test_scrub_keeps_a_year_in_plain_wording(form: str) -> None:
    text = form.replace("{}", "2019")
    assert Scrub(None, LAST4S).text(text) == text
    assert not hides_last4(text, LAST4S)


# ==========================================================================
# 4. Two years at a sentence end, and a mask inside a bold pair
# ==========================================================================

Y0, Y1, Y2, Y3 = 2019, "2024", "2025", "2026"
KEPT_YEARS = [
    f"Spending peaked in {Y1}. {Y2} was lower.",
    f"It started in {Y0}. {Y0 + 1} was the low point.",
    f"Up 12% in {Y2}. {B}{Y3}{B} so far: $4,100.",
    f"as of 4/10/{Y3}. {Y1} total was $3,100.",
    f"Net worth rose 28.8% over Q4 {Y2}. {Y1} total: $9,175.01.",
    f"{B}Up in {Y2}.{B} {Y3} was flat.",
    f"{B}{Y2}{B} total: $9,249.39. {B}{Y1}{B} total: $6,388.",
    f"***{Y2}*** was your best year.",
]


@pytest.mark.parametrize("text", KEPT_YEARS)
def test_two_years_at_a_sentence_end_are_kept(text: str) -> None:
    assert_kept(text)


def test_fuzz_answers_with_years_at_sentence_ends_are_kept() -> None:
    rng = random.Random(SEED)
    bad = []
    for _ in range(300):

        def year() -> str:
            y = str(rng.randint(2019, 2026))
            return f"{B}{y}{B}" if rng.random() < 0.35 else y

        money = f"${rng.uniform(1, 20_000):,.2f}"
        text = rng.choice(
            [
                f"{year()} total: {money}. {year()} total: {money}.",
                f"Spending peaked in {year()}. {year()} was lower.",
                f"Up {rng.randint(1, 99)}% in {year()}. {year()} so far: {money}.",
                f"Paid on {rng.randint(1, 12)}/{rng.randint(1, 28)}/{year()}. {year()} next.",
                f"Over Q{rng.randint(1, 4)} {year()}. {year()} total: {money}.",
            ]
        )
        if filter_text(text) != text:
            bad.append((text, filter_text(text)))
    assert not bad, f"{len(bad)} garbled, e.g. {bad[:3]!r}"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # Still masked: more digits joined to the years.
        (f"Ref 1234 {Y1}. {Y2} here", f"Ref {MASK} here"),
        # A mask that would cut a bold pair takes in the whole pair.
        (f"Code 1234. {B}5678{B} here", f"Code {MASK} here"),
        (f"Code {B}1234{B} 5678 here", f"Code {MASK} here"),
    ],
)
def test_mask_never_leaves_a_lone_bold_marker(text: str, want: str) -> None:
    assert filter_text(text) == want


# ==========================================================================
# 5. The stream waits inside an open bold span
# ==========================================================================


def test_stream_does_not_flash_a_masked_bold_year() -> None:
    sf = StreamFilter()
    events = [sf.feed(p) for p in [f"{B}2025: up", f" 12%.{B} Then", " 2026."]] + [sf.finish()]
    sent = [ev for ev in events if ev]
    assert sent == [("text", f"{B}2025: up 12%.{B} Then 2026.")]


@pytest.mark.parametrize(
    "text",
    [
        f"{B}2025: up 12%.{B} Then 2026.",
        f"{B}2025: up 12%.{B} Then {B}2026: up 4%.{B}",
        f"{B}Q3 2025:{B} up 12%. {B}2026: flat.{B} Done.",
        f"{B}*2025: up.*{B} Then 2026.",
    ],
)
def test_stream_shows_only_prefixes_of_a_bold_answer(text: str) -> None:
    assert_kept(text)
