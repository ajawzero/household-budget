"""Phase 6 fix pass 3: the output filter per line, the year range next to digit
groups, and a last four that is also a year. Fake numbers only, built at runtime
so the repo scanner never sees a full one."""

from __future__ import annotations

import pytest

from app.ai.postprocess import MASK, Scrub, StreamFilter, filter_text, hides_last4
from app.importer.sanitize import luhn_ok


def shown(sf: StreamFilter, pieces: list[str]) -> list[str]:
    """What the browser shows after each event (text appends, replace swaps)."""
    states, now = [], ""
    for piece in [*pieces, None]:
        ev = sf.finish() if piece is None else sf.feed(piece)
        if ev:
            now = now + ev[1] if ev[0] == "text" else ev[1]
            states.append(now)
    return states


# --------------------------------------------------------------------------
# N8: digit and address rules run per line
# --------------------------------------------------------------------------

YEARS = "\n".join(str(y) for y in range(2019, 2027))
KEPT_LINES = [
    "Net worth: $412,500\nA good place to start is the van loan.",
    "Spending this month: $1,234\nThe best way to save is to cook more.",
    "- Dining out visits: 12\nOne way to cut back is to cook at home.",
    "Counts by month:\n- 120\n- 450\n- 1200",
    f"Years:\n{YEARS}",
    f"Years:\n{YEARS}\n",
]


@pytest.mark.parametrize("text", KEPT_LINES)
def test_filter_does_not_join_lines(text: str) -> None:
    assert filter_text(text) == text
    sf = StreamFilter()
    states = shown(sf, text.splitlines(keepends=True))
    assert sf.text == text
    assert not any(MASK in s for s in states)


def test_filter_still_masks_an_address_on_one_line() -> None:
    addr = " ".join(["12", "Oak", "Way"])
    assert filter_text(f"Send it to {addr}\nThanks.") == f"Send it to {MASK}\nThanks."


def test_filter_masks_long_numbers_per_line() -> None:
    raw = f"Spending by year\n2023: {41_200}\n2024: {43_800}"
    assert filter_text(raw) == f"Spending by year\n2023: {MASK}\n2024: {MASK}"


def test_filter_masks_a_card_with_year_like_groups_split_by_newlines() -> None:
    head = ["4111", "2019", "2020"]
    last = next(f"{n:04d}" for n in range(10_000) if luhn_ok("".join(head) + f"{n:04d}"))
    card = "\n".join([*head, last])
    assert filter_text(f"Card {card} was used.") == f"Card {MASK} was used."


# --------------------------------------------------------------------------
# N10: a year range joined to other digit groups is not kept
# --------------------------------------------------------------------------


def span(a: str, b: str) -> str:
    """Four digits, a dash, four digits."""
    return f"{a}-{b}"


YR = span("2019", "2026")
A, B = "4111", "1111"


@pytest.mark.parametrize(
    ("raw", "out"),
    [
        (f"Card {A} {YR} {B}", f"Card {A} {MASK} {B}"),
        (f"Card {A} {YR} {B} {B}", f"Card {A} {MASK} {MASK}"),
        (f"Card {YR} {B} {A} used", f"Card {MASK} {MASK} used"),
        (f"Card {A} - {YR} used", f"Card {A} - {MASK} used"),
        # Kept: words or punctuation around it.
        (f"From {YR}, 14 trips.", f"From {YR}, 14 trips."),
        (f"From {YR}. 3 trips.", f"From {YR}. 3 trips."),
        (f"Spent $4,000 ({YR}).", f"Spent $4,000 ({YR})."),
        (f"In {YR}: 12 visits.", f"In {YR}: 12 visits."),
    ],
)
def test_year_range_next_to_a_digit_group_is_masked(raw: str, out: str) -> None:
    assert filter_text(raw) == out
    sf = StreamFilter()
    sf.feed(raw)
    sf.finish()
    assert sf.emitted == out == sf.text


# --------------------------------------------------------------------------
# N9 and N24: a last four that is also a year
# --------------------------------------------------------------------------

YEAR_LAST4S = {"2025", "2026", "1234"}
# Plain years, dates and amounts stay, even when a last four equals them.
KEPT = [
    "What did we spend in 2025?",
    "Spending on 10/20/2025?",
    "Net worth at the end of 2025.",
    "What changed in FY2025?",
    "The 2025\N{EN DASH}2026 plan",
    "Aug 2026",
    "Paid on Oct 20, 2026",
    "In March 2026, you spent $412",
    "Your 2026s budget",
    "Opened 2026, Oct",
    "Spending 2026 was up",
    "Balance was $-1234 on Oct 1.",
]
# A year-like last four after a card cue is masked.
CUED = [
    "Summit card x{}",
    "POS DEBIT CARD X{} HARBOR",
    "ONLINE PMT TO CHK ...{}",
    "Checking …{}",
    "ACH DEBIT -{} HARBOR",
    "Card *{} was used",
    "Card #{} was used",
    "Pay the card ending in {} first",
    "Pay the card ending {} first",
    "PAY THE CARD ENDING IN {} FIRST",
    "Card x{}, then rent",
    "Card x{}.",
]


@pytest.mark.parametrize("text", KEPT)
def test_scrub_keeps_a_year_that_is_a_last_four(text: str) -> None:
    assert Scrub(None, YEAR_LAST4S).text(text) == text
    assert not hides_last4(text, YEAR_LAST4S)


@pytest.mark.parametrize("form", CUED)
def test_scrub_masks_a_year_like_last_four_after_a_cue(form: str) -> None:
    text = form.format("2026")
    out = Scrub(None, YEAR_LAST4S).text(text)
    assert "2026" not in out and MASK in out
    assert hides_last4(text, YEAR_LAST4S)


def test_scrub_masks_any_other_last_four_bare() -> None:
    assert Scrub(None, YEAR_LAST4S).text("Paid 1234 dollars") == f"Paid {MASK} dollars"
    assert hides_last4("Paid 1234 dollars", YEAR_LAST4S)
