"""Phase 6 review fixes: the last-four scrub and the output filter across line breaks.
Fake numbers only, built at runtime so the repo scanner never sees a full one."""

from __future__ import annotations

import re

from app.ai.postprocess import MASK, PRIVATE_LABEL, Scrub, StreamFilter, filter_text, hides_last4

LAST4 = "8812"
# Every way a statement or a chat writes an account's last four.
LAST4_FORMS = [
    f"Summit card x{LAST4} (Joint)",
    f"POS DEBIT CARD X{LAST4} HARBOR",
    f"ONLINE PMT TO CHK ...{LAST4}",
    f"Checking …{LAST4}",
    f"ACH DEBIT -{LAST4} HARBOR",
    f"Card *{LAST4} was used",
    f"Card #{LAST4} was used",
    f"Pay the card ending in {LAST4} first",
    f"Pay the card ending {LAST4} first",
    f"Card x{LAST4}, then rent",
    f"Card x{LAST4}.",
]
# Years, dates and amounts that look like a known last four stay as they are.
KEPT = [
    "The 2025-26 plan",
    f"Taxes for {'-'.join(['2025', '2026'])} are done",
    "Paid on 2026-09-30",
    "Paid $4321 today",
    "Paid -$4321 today",
    "Paid -4321.00 today",
    "Paid 4,321.00 today",
    "Sep 4321.5 units",
    f"Ref 1{'4321'} kept",
]
KEPT_LAST4S = {"2025", "2026", "4321"}


def test_scrub_masks_every_last_four_form() -> None:
    scrub = Scrub(None, {LAST4})
    for text in LAST4_FORMS:
        out = scrub.text(text)
        assert LAST4 not in out and MASK in out, text
    assert scrub.json({"note": f"Summit card x{LAST4}"}) == {"note": f"Summit card x{MASK}"}


def test_scrub_keeps_years_dates_and_amounts() -> None:
    scrub = Scrub(None, KEPT_LAST4S)
    for text in KEPT:
        assert scrub.text(text) == text


def test_chat_text_hides_every_last_four_form() -> None:
    for text in LAST4_FORMS:
        assert hides_last4(text, {LAST4}), text
    for text in KEPT:
        assert not hides_last4(text, KEPT_LAST4S), text


# --------------------------------------------------------------------------
# The output filter across line breaks
# --------------------------------------------------------------------------

GROUPS = ["4111"] + ["1111"] * 3


def test_filter_catches_a_private_name_split_by_a_newline(private_rules) -> None:
    out = filter_text("You pay Example Private\nService each month.", private_rules)
    assert out == f"You pay {PRIVATE_LABEL} each month."
    out = filter_text("At example\n\nemployer co today.", private_rules)
    assert "EMPLOYER" not in out.upper()


def test_filter_catches_a_number_split_by_a_newline() -> None:
    card = "\n".join(GROUPS)
    assert filter_text(f"Card {card} was used.") == f"Card {MASK} was used."
    phone = "(608)\n" + "555-" + "1234"
    assert filter_text(f"Call {phone} today.") == f"Call {MASK} today."


def test_filter_keeps_short_numbers_one_per_line() -> None:
    for text in [
        "2025\n2026",
        "Years:\n2025\n2026\n",
        "You spent $1,234.56\non 2026-08-31.",
        "Top months\n- Jan 2026: $1,200.00\n- Feb 2026: $980.00",
    ]:
        assert filter_text(text) == text


def shown(sf: StreamFilter, pieces: list[str]) -> list[str]:
    """What the browser shows after each event (text appends, replace swaps)."""
    states, now = [], ""
    for piece in [*pieces, None]:
        ev = sf.finish() if piece is None else sf.feed(piece)
        if ev:
            now = now + ev[1] if ev[0] == "text" else ev[1]
            states.append(now)
    return states


def test_stream_filter_replaces_a_private_name_split_by_a_newline(private_rules) -> None:
    sf = StreamFilter(private_rules)
    states = shown(sf, ["You pay Example Private\n", "Service each month.", " Done."])
    assert states[-1] == sf.text == f"You pay {PRIVATE_LABEL} each month. Done."
    assert not any(private_rules.check(s) for s in states)


def test_stream_filter_replaces_a_number_split_by_newlines() -> None:
    sf = StreamFilter()
    states = shown(
        sf, ["Card ", *(f"{g}\n" for g in GROUPS[:-1]), f"{GROUPS[-1]} was used.", " Done."]
    )
    assert states[-1] == sf.text == f"Card {MASK} was used. Done."
    assert all("".join(GROUPS) not in re.sub(r"\D", "", s) for s in states)
