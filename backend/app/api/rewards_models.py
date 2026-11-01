"""Models for /api/rewards (docs/plans/phase-7.md section 4 and
docs/plans/rewards-inputs.md section 6), the Dashboard's credits line and the
Checks rewards block.

Money is integer cents, positive. Points, miles and timeshare points are
integer micro-units (1 point = 1,000,000), like utility usage. Cash back
balances are dollars in micro-units. Cents per point stay strings as the chat
sent them, like "1.2". Dates are ISO `YYYY-MM-DD`.

Writes (the In models) take balances, money and cents per point as strings,
like format 1.3: "5200" points, "37.42" cash back, "12.00" a credit, "1.2"
cents per point. Typed text is one line with no control characters, and is
scanned before it is stored.
"""

from __future__ import annotations

import unicodedata
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.importer.models import TEXT_MAX, Key

CycleState = Literal["done", "expired", "partial", "open"]
CycleSource = Literal["set", "rows", "chat"]
AppProgramKind = Literal["points", "miles", "cash_back"]
# The unit a balance entry was typed in: whole (points and miles) or cash (cash back).
BalanceUnit = Literal["whole", "cash"]
ProgramGroup = Literal["primary", "secondary"]
NAME_MAX = 64
NOTE_MAX = 500
BALANCES_MAX = 50
EARLIEST = date(2000, 1, 1)
LATEST = date(2099, 12, 31)  # files stop here too; keeps the day math in range
# A balance: whole points or miles, or cash back with cents. The kind's rule is
# checked by the service, so the message can name it.
Balance = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,10}(?:\.[0-9]{1,2})?$")]
# Cents per point, like "1.2". At most 99.9999.
Cpp = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,2}(?:\.[0-9]{1,4})?$")]
Day = Annotated[date, Field(ge=EARLIEST, le=LATEST)]
BenefitGroup = Literal["open", "auto", "used", "upcoming", "other", "ended"]
Cadence = Literal["monthly", "quarterly", "semi_annual", "annual", "multi_year", "one_time"]
BenefitKind = Literal["credit", "earning_cap", "status_perk", "free_night", "other"]
# The benefit fields an edit can change (rewards_overlay.EDITABLE_FIELDS). key,
# account_key, kind, cadence and cycles never change.
BenefitField = Literal[
    "name",
    "amount",
    "counted_value",
    "auto",
    "enrolled",
    "rolls_over",
    "enroll_by",
    "ends_on",
    "how_to_use",
    "note",
    "cap",
    "earn_before",
    "earn_after",
    "category_keys",
    "reported_spent",
    "as_of",
    "program_key",
    "status_tier",
]
BenefitNoticeKind = Literal[
    "benefit_chat_changed", "benefit_no_fit", "benefit_app_key", "benefit_duplicate"
]
Origin = Literal["chat", "app"]
BENEFIT_NAME_MAX = 100
CYCLES_MAX = 20
CATEGORY_KEYS_MAX = 20
# Money, like "12.00". Not negative.
MoneyText = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,7}\.[0-9]{2}$")]
TERMS_MAX = 10  # match words per benefit
TERM_MAX = 40  # characters in a match word (rewards_match.TERM_MAX)
Reason = Literal["return", "points", "other"]


class Out(BaseModel):
    model_config = {"extra": "forbid"}


class In(BaseModel):
    """Like txn_models.In: unknown fields are refused."""

    model_config = {"extra": "forbid"}


def one_line(text: str, max_len: int) -> str | None:
    """txn_models.one_line: no control characters, spaces collapsed, trimmed.
    Imported on call: txn_models imports app.api.models, which imports this
    module."""
    from app.api.txn_models import one_line as _one_line

    return _one_line(text, max_len)


class Deadline(Out):
    date: str
    label: str = Field(description='Like "Enroll in Yearly travel credit".')
    kind: Literal["enroll_by", "cycle_end", "book_by", "expires"]
    key: str = Field(description="The benefit or program key.")
    days_left: int


class RewardTiles(Out):
    value_low_cents: int = Field(description="Valued programs at their low cents per point.")
    value_high_cents: int
    valued_count: int = Field(description="Programs with a value (cpp or cash back).")
    credits_left_cents: int = Field(
        description="Not used yet on credits whose cycle holds today. Auto-loads count."
    )
    credits_left_count: int
    next_deadline: Deadline | None


class CardRef(Out):
    account_key: str
    name: str | None


class TimeshareYear(Out):
    year: int
    allocated_micro: int
    used_micro: int
    pulled_forward_micro: int | None = Field(
        description="Points of this year's allocation already borrowed into the year before."
    )
    left_micro: int = Field(description="allocated - used - pulled_forward.")
    expires_on: str | None
    book_by: str | None


class ProgramOut(Out):
    key: str
    name: str
    kind: Literal["points", "miles", "cash_back", "timeshare"]
    group: Literal["primary", "secondary"]
    balance_micro: int | None
    as_of: str | None
    source: Literal["statement", "manual", "app"] | None = Field(
        description="statement and manual are the chat's. app: an entry made in the app wins."
    )
    origin: Literal["chat", "app"] = Field(description="app: a program added in the app.")
    chat_balance_micro: int | None = Field(
        description="The chat's balance for the same day, when the app's entry wins and the "
        "two differ. Else null."
    )
    stale: bool = Field(description="as_of more than 90 days before household today.")
    owner_key: str | None
    owner_name: str | None
    earned_on: list[CardRef]
    status_tier: str | None
    status_via_name: str | None = Field(description="The card that grants the status.")
    cpp_low: str | None
    cpp_high: str | None
    value_low_cents: int | None = Field(description="Null when not valued.")
    value_high_cents: int | None
    expires_on: str | None
    last_redeemed_on: str | None = Field(
        description="1.4: the newest done_on among this program's done redemptions. Null with none."
    )
    best_uses: list[str]
    avoid: list[str]
    note: str | None
    allocations: list[TimeshareYear] = Field(description="Timeshares only, by year.")


class ValueRow(Out):
    key: str
    name: str
    kind: Literal["points", "miles", "cash_back", "timeshare"]
    balance_micro: int | None
    cpp_low: str | None
    cpp_high: str | None
    low_cents: int | None
    high_cents: int | None
    basis: Literal["cpp", "face", "not_valued"] = Field(
        description="face: cash back at face value. not_valued: no balance or no cpp."
    )


class ValueTable(Out):
    rows: list[ValueRow] = Field(description="Valued first, highest first, then not valued.")
    low_cents: int
    high_cents: int


class RedemptionOut(Out):
    key: str
    title: str
    status: Literal["ready", "consider", "done"]
    program_key: str
    program_name: str
    points_needed_micro: int | None = Field(description="On a done redemption, the points it used.")
    have_micro: int | None = Field(description="The program's balance now.")
    enough: bool | None = Field(
        description="have >= needed. Null on a done one, or with no needed or no balance."
    )
    trip_key: str | None
    trip_name: str | None = Field(
        description="The trip's name when the app has that trip. Else null, also with no trip_key."
    )
    note: str | None
    done_on: str | None = Field(description="1.4: the day a done redemption was used. Done only.")


class CycleRowOut(Out):
    txn_key: str
    date: str
    merchant: str | None = Field(description="As Transactions shows it.")
    description: str = Field(description="As the statement printed it. Match words read this.")
    cents: int = Field(description="The credit, positive.")
    earlier_start: str | None = Field(
        description="The previous cycle's start, while the row is dated within 7 days after "
        'that cycle ends ("Count in <previous cycle> instead"). Null for a row a choice put here.'
    )


class CycleOut(Out):
    start: str
    end: str
    amount_cents: int
    used_cents: int
    left_cents: int = Field(description="amount - used. 0 once expired.")
    used_on: str | None
    state: CycleState = Field(
        description="done: used reaches the amount. expired: past end, not done. "
        "partial: some used. open: none used."
    )
    days_left: int | None = Field(description="Days to end. Null once past.")
    source: CycleSource | None = Field(
        description="Where used comes from, one source, never added: set (marked in the app, "
        "0 means not used), rows (the matched statement credits), chat (the chat's). Null: "
        "not used."
    )
    chat_used_cents: int | None = Field(
        description='The chat\'s used, when it sent one. "The chat says $X" when rows win and '
        "it differs."
    )
    covered: bool = Field(
        description="The card's statements run from the start through the end plus 7 days. A "
        'cycle not covered and not used says "Waiting for rows", never "Not used".'
    )
    over_cents: int = Field(
        description="Matched rows above the amount (the cycle counts as done). 0 when not over."
    )
    rows: list[CycleRowOut] = Field(description="The statement credits matched to it, by date.")


class BenefitNoticeOut(Out):
    kind: BenefitNoticeKind = Field(
        description="The page's notices on this benefit. The page lists them with their text."
    )
    fields: list[str] = Field(description="benefit_chat_changed and benefit_no_fit: field names.")


class BenefitOut(Out):
    key: str
    name: str
    kind: BenefitKind = Field(
        description="The page's list holds credit, free_night and other. Writes give back any kind."
    )
    account_key: str
    account_name: str | None
    cadence: Cadence | None
    amount_cents: int | None = Field(description="Per cycle.")
    counted_value_cents: int | None
    auto: bool | None
    enrolled: bool | None
    enroll_by: str | None
    enroll_flag: bool = Field(description="Not enrolled, with an enroll-by date today or later.")
    rolls_over: bool | None
    ends_on: str | None
    program_name: str | None = Field(description="Free nights: the hotel program.")
    how_to_use: str | None
    note: str | None
    group: BenefitGroup = Field(
        description="open: open or partial this cycle, or a free night not expired. auto: "
        "loads by itself. used: done this cycle. upcoming: the next cycle has not started. "
        "ended: every cycle or the expiry is past. other: no dates."
    )
    deadline: str | None = Field(description="This cycle's end, or a free night's expiry.")
    days_left: int | None
    cycle: CycleOut | None = Field(description="This cycle, else the next one.")
    past_cycles: list[CycleOut]
    origin: Origin = Field(description="app: a benefit added in the app.")
    edited_fields: list[str] = Field(
        description="Chat benefits: the fields your edits change. The rest are the chat's."
    )
    notices: list[BenefitNoticeOut]
    hidden: bool = Field(description="Hidden benefits are left out of the page and every total.")
    match_off: bool = Field(description="This credit never shows on statements.")
    terms_count: int = Field(description="Saved match words.")
    amount_from: str | None = Field(
        description="When your amount change starts. Null with no change. Cycles that start "
        "before it keep the amount from before the change."
    )


class CategoryRef(Out):
    key: str
    name: str


class CardholderSpend(Out):
    person_key: str | None = Field(description="Null when the row names no cardholder.")
    name: str | None
    cents: int


class CapOut(Out):
    key: str
    name: str
    account_key: str
    account_name: str | None
    year: int
    cap_cents: int
    earn_before: str | None
    earn_after: str | None
    categories: list[CategoryRef]
    spent_cents: int = Field(description="Purchases minus refunds on the card in the categories.")
    left_cents: int
    by_cardholder: list[CardholderSpend] = Field(description="Largest first.")
    rows_from: str | None = Field(description="First day the card's statements cover this year.")
    rows_through: str | None
    reported_spent_cents: int | None = Field(description="The chat's number.")
    reported_as_of: str | None
    app_at_reported_cents: int | None = Field(description="The rows up to reported_as_of.")
    reported_differs: bool = Field(description="More than $25 apart.")
    note: str | None
    origin: Origin = Field(description="app: a benefit added in the app.")


class PerkOut(Out):
    key: str
    name: str
    account_name: str | None
    program_name: str | None
    status_tier: str | None
    note: str | None
    origin: Origin = Field(description="app: a benefit added in the app.")


class MonthSpend(Out):
    month: str
    cumulative_cents: int


class MilestoneBill(Out):
    key: str
    name: str
    monthly_cents: int = Field(description="Net monthly equivalent.")


class MilestoneBillToMove(MilestoneBill):
    account_name: str | None = Field(description="The account it is on now.")


class MilestoneOut(Out):
    key: str
    account_key: str
    account_name: str | None
    year: int
    threshold_cents: int
    reward: str
    status: Literal["open", "met", "missed"] = Field(description="The chat's status.")
    met_on: str | None
    plan: str | None
    note: str | None
    spent_cents: int = Field(
        description="Purchases minus refunds on the card in the year, every cardholder."
    )
    to_go_cents: int
    projection_cents: int | None = Field(description="Year-end at the pace so far.")
    need_per_month_cents: int | None
    pace: Literal["met", "missed", "on_pace", "behind"] | None
    rows_from: str | None
    rows_through: str | None
    months: list[MonthSpend] = Field(description="The rows' first month to their last.")
    reported_spent_cents: int | None
    reported_as_of: str | None
    app_at_reported_cents: int | None
    reported_differs: bool = Field(description="More than $25 apart.")
    bills: list[MilestoneBill] = Field(
        description="Counted recurring items on this card, largest first."
    )
    bills_monthly_cents: int = Field(description="The sum of bills.")
    bills_to_move: list[MilestoneBillToMove] = Field(
        description="Counted items set to move to this card from another account, largest first."
    )


class RouteOut(Out):
    key: str
    spend: str
    account_key: str
    account_name: str | None
    earn: str | None
    note: str | None
    display_order: int


class FeeCredit(Out):
    key: str
    name: str
    cents: int = Field(
        description="For the year: counted_value, else full_cents. 0 if it ended before the year."
    )
    full_cents: int = Field(
        description="Credits: this year's cycles up to ends_on, else amount x periods a year up "
        "to ends_on. Other kinds: the same as cents."
    )
    counted: bool = Field(description="cents is the chat's counted_value.")


class FeeRow(Out):
    key: str = Field(description='Recurring key, or "card:<account_key>" from the registry.')
    name: str
    kind: Literal["card_fee", "membership"]
    source: Literal["recurring", "registry"]
    account_key: str | None
    account_name: str | None
    owner_name: str | None
    gross_cents: int = Field(description="A year.")
    renews_on: str | None
    credits: list[FeeCredit] = Field(description="Only on the card's largest fee row.")
    credits_cents: int
    net_cents: int = Field(description="gross - credits. Negative when credits are larger.")
    net_positive: bool = Field(description="Credits more than the fee.")


class FeesOut(Out):
    year: int
    rows: list[FeeRow]
    gross_cents: int
    credits_cents: int
    net_cents: int


class NoticeOut(Out):
    kind: Literal[
        "overlay_failed",
        "program_app_key",
        "program_duplicate",
        "benefit_chat_changed",
        "benefit_no_fit",
        "benefit_app_key",
        "benefit_duplicate",
        "benefit_app_no_fit",
    ] = Field(
        description="overlay_failed: the edits could not be shown. program_app_key: the chat "
        "now sends a key the app made; the app's program is shown. program_duplicate: an app "
        "program has the same name as a chat program. benefit_chat_changed: the chat changed "
        "an edited field (Keep mine, Use the chat's). benefit_no_fit: the chat changed the "
        "kind and the edit is skipped (Delete edit). benefit_app_key: the chat now sends a key "
        "the app made; the app's benefit is shown (Use the chat's). benefit_duplicate: an app "
        "benefit is like a chat one (Use the chat's, Keep both). benefit_app_no_fit: an app "
        "benefit no longer passes (its program is gone), so it is not shown (Delete)."
    )
    text: str = Field(description="Fixed wording. Never a value.")
    key: str | None = Field(description="The program or benefit it is about.")
    other_key: str | None = Field(description="Duplicates: the chat's key.")
    fields: list[str] = Field(
        description='Benefits: the field names it is about. program_app_key: ["unit"] when the '
        "chat counts the key in another unit than the app program (or as a timeshare). The "
        "chat's balance is then never shown, and Use the chat's keeps the app's balances, "
        "not shown."
    )


class OrphanOut(Out):
    kind: Literal["program", "benefit"]
    key: str = Field(description="The key the inputs are on. They are kept, not shown.")
    holds: list[Literal["balances", "edits", "terms", "choices", "marks"]]
    reason: Literal["dropped", "timeshare", "unit"] = Field(
        description="dropped: the chat no longer sends the key. Programs only: timeshare (the "
        "program is now a timeshare) or unit (the program now counts in the other unit)."
    )
    unit: BalanceUnit | None = Field(
        description="Programs: the unit the balances were typed in. They move only to a "
        "program in that unit. A key with both units is listed once per unit. Null for "
        "benefits."
    )


class HiddenBenefitOut(Out):
    key: str
    name: str
    kind: BenefitKind
    account_key: str
    account_name: str | None
    origin: Origin


class ReviewCountsOut(Out):
    """The counts behind "Review N card credits", "Needs a look" and the Checks lines."""

    to_review: int = Field(description='Rows in "Card credits to review", ambiguous ones included.')
    ambiguous: int = Field(description="Rows whose match words tie on more than one benefit.")
    returns: int = Field(description='Rows in "Looks like returns".')
    trips: int = Field(description='Rows in "Points credits for a trip".')
    over: int = Field(description="Cycles whose matched rows go over the amount.")
    chat_says_used: int = Field(
        description="Covered cycles on a benefit with match words: the chat says used, and no "
        "row matches."
    )
    needs_a_look: int = Field(
        description="Credit rows in the review's Needs a look: choices that lost their row, "
        "matched rows no cycle holds, and choices on a credit that takes no rows. "
        "chat_says_used counts apart."
    )
    benefit_notes: int = Field(
        description="Benefit notices and orphans: the Needs a look card on the Benefits tab."
    )
    program_notes: int = Field(
        description="Program notices and orphans (the Needs a look card on the Points tab), and "
        "the note that the edits could not be shown."
    )


class RewardsOut(Out):
    source_run_id: str | None = Field(
        description="The monthly run the rewards came from. Null before the first 1.3 run."
    )
    today: str = Field(description="Household today, which cycle states use.")
    tiles: RewardTiles | None
    highlights: list[str]
    programs: list[ProgramOut] = Field(description="Primary first, then by name.")
    value_table: ValueTable
    redemptions: list[RedemptionOut] = Field(
        description="Ready, consider, done. Done ones newest first by done_on, undated ones "
        "last, then by title."
    )
    benefits: list[BenefitOut] = Field(description="Use-or-lose order.")
    caps: list[CapOut]
    perks: list[PerkOut]
    milestones: list[MilestoneOut] = Field(description="Open first.")
    routing: list[RouteOut] = Field(description="By display_order.")
    fees: FeesOut
    notices: list[NoticeOut] = Field(description="Notes on the app's inputs, in key order.")
    orphans: list[OrphanOut] = Field(
        description='Inputs kept but not shown ("Needs a look"): on keys the chat no longer '
        "sends, and balances on a timeshare or in another unit than the program's now."
    )
    hidden: list[HiddenBenefitOut] = Field(description='Hidden benefits ("Hidden (n)"), by key.')
    review_counts: ReviewCountsOut


class CreditsEnding(Out):
    count: int = Field(description="Credits not auto, not used, ending within 14 days.")
    left_cents: int
    first_end: str


class RewardStale(Out):
    key: str
    name: str
    what: Literal["balance", "reported_spent"]
    as_of: str


class RewardGap(Out):
    kind: Literal["milestone", "cap"]
    key: str
    name: str
    as_of: str = Field(description="The chat's as_of. The app counts rows up to this day.")
    app_cents: int
    chat_cents: int
    diff_cents: int = Field(description="app - chat.")
    status: Literal["match", "differs"] = Field(description="match when |diff| <= $25.")
    note: str | None = Field(description="Why the rows may not reach the chat's number.")


class RewardsCheck(Out):
    source_run_id: str | None
    stale: list[RewardStale] = Field(description="More than 90 days old, oldest first.")
    gaps: list[RewardGap]
    counts: ReviewCountsOut = Field(
        description="Card credits to review and what needs a look. The lines link to "
        "/rewards?tab=benefits&review=credits."
    )


# --------------------------------------------------------------------------
# Balances and app programs (docs/plans/rewards-inputs.md 3.1 and 6)
# --------------------------------------------------------------------------


class BalanceEntryIn(In):
    program_key: Key
    balance: Balance = Field(
        description='Points and miles whole, like "5200". Cash back like "37.42".'
    )
    as_of: Day | None = Field(default=None, description="Default: today. Not in the future.")


class BalancesIn(In):
    entries: list[BalanceEntryIn] = Field(
        min_length=1, max_length=BALANCES_MAX, description="One per program per day."
    )


class BalancesSavedOut(Out):
    saved: int
    programs: list[ProgramOut] = Field(description="The programs saved to, as the page shows.")


class BalanceEntryOut(Out):
    id: int
    as_of: str
    balance_micro: int
    unit: BalanceUnit = Field(
        description="The unit it was typed in. An entry in another unit than the program's "
        "now is kept, never shown."
    )
    shown: bool = Field(description="This entry is the balance the page shows.")


class ChatBalanceOut(Out):
    balance_micro: int
    as_of: str
    source: Literal["statement", "manual"]


class BalanceHistoryOut(Out):
    program_key: str
    program: ProgramOut | None = Field(description="Null when the page does not show the key.")
    entries: list[BalanceEntryOut] = Field(description="The app's entries, newest first.")
    chat: ChatBalanceOut | None = Field(
        description="The chat's current balance, if it sends one in the unit the page counts "
        "the program in. Null when an app program wins the key and the chat counts it in "
        "another unit."
    )


def _shows(s: str) -> bool:
    """Something is left to see once spaces and format characters (zero-width
    spaces and joiners, word joiners, byte order marks) are gone."""
    return any(not ch.isspace() and unicodedata.category(ch) != "Cf" for ch in s)


def _program_name(v: str) -> str:
    """one_line (no control characters, spaces collapsed), and never empty or
    made only of characters that do not show."""
    s = one_line(v, NAME_MAX)
    if s is None or not _shows(s):
        raise ValueError("empty")
    return s


class ProgramIn(In):
    name: str = Field(max_length=4 * NAME_MAX)
    kind: AppProgramKind
    group: ProgramGroup = "secondary"
    owner_key: Key | None = None
    cpp_low: Cpp | None = None
    cpp_high: Cpp | None = None
    expires_on: Day | None = None
    note: str | None = Field(default=None, max_length=4 * NOTE_MAX)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _program_name(v)

    @field_validator("note")
    @classmethod
    def _note(cls, v: str | None) -> str | None:
        return None if v is None else one_line(v, NOTE_MAX)


class ProgramPatchIn(In):
    """Only the fields sent change. Null clears owner_key, the value range,
    expires_on and note. name, kind and group cannot be null."""

    name: str | None = Field(default=None, max_length=4 * NAME_MAX)
    kind: AppProgramKind | None = None
    group: ProgramGroup | None = None
    owner_key: Key | None = None
    cpp_low: Cpp | None = None
    cpp_high: Cpp | None = None
    expires_on: Day | None = None
    note: str | None = Field(default=None, max_length=4 * NOTE_MAX)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _program_name(v)

    @field_validator("note")
    @classmethod
    def _note(cls, v: str | None) -> str | None:
        return None if v is None else one_line(v, NOTE_MAX)

    def changes(self) -> dict:
        return {f: getattr(self, f) for f in self.model_fields_set}


class MoveIn(In):
    to_key: Key


class KeepBothIn(In):
    other_key: Key = Field(description="The chat program with the same name.")


# --------------------------------------------------------------------------
# Benefit edits (docs/plans/rewards-inputs.md 3.2 and 6)
# --------------------------------------------------------------------------


def _benefit_name(v: str | None) -> str | None:
    """one_line, and never empty or made only of characters that do not show."""
    if v is None:
        return None
    s = one_line(v, BENEFIT_NAME_MAX)
    if s is None or not _shows(s):
        raise ValueError("empty")
    return s


class BenefitCycleIn(In):
    """A stored cycle (multi_year and one_time). Use stays the app's to work out."""

    start: Day
    end: Day
    amount: MoneyText | None = Field(default=None, description="Only when it differs.")


class BenefitSetIn(In):
    """The fields to change. Fields left out stay. Null clears one ("cleared by
    you"). key, account_key, kind, cadence and cycles cannot change: a cadence
    change is an end plus an add. The result must fit the benefit's kind."""

    name: str | None = Field(default=None, max_length=4 * BENEFIT_NAME_MAX)
    amount: MoneyText | None = Field(default=None, description="Credits: per cycle.")
    counted_value: MoneyText | None = None
    auto: bool | None = None
    enrolled: bool | None = None
    rolls_over: bool | None = None
    enroll_by: Day | None = None
    ends_on: Day | None = Field(
        default=None, description="End: cycles after it drop out, and Fees stop counting them."
    )
    how_to_use: str | None = Field(default=None, max_length=4 * TEXT_MAX)
    note: str | None = Field(default=None, max_length=4 * TEXT_MAX)
    cap: MoneyText | None = None
    earn_before: str | None = Field(default=None, max_length=4 * TEXT_MAX)
    earn_after: str | None = Field(default=None, max_length=4 * TEXT_MAX)
    category_keys: list[Key] | None = Field(default=None, max_length=CATEGORY_KEYS_MAX)
    reported_spent: MoneyText | None = None
    as_of: Day | None = None
    program_key: Key | None = None
    status_tier: str | None = Field(default=None, max_length=4 * TEXT_MAX)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _benefit_name(v)

    @field_validator("how_to_use", "note", "earn_before", "earn_after", "status_tier")
    @classmethod
    def _text(cls, v: str | None) -> str | None:
        return None if v is None else one_line(v, TEXT_MAX)

    def changes(self) -> dict:
        return {f: getattr(self, f) for f in self.model_fields_set}


class BenefitIn(BenefitSetIn):
    """A benefit added in the app. Calendar cadences get this year's cycles from
    the app, so they send none. multi_year and one_time send theirs."""

    name: str = Field(max_length=4 * BENEFIT_NAME_MAX)
    kind: BenefitKind
    account_key: Key = Field(description="A credit or store card.")
    cadence: Cadence | None = Field(default=None, description="Credits.")
    cycles: list[BenefitCycleIn] | None = Field(
        default=None, min_length=1, max_length=CYCLES_MAX, description="multi_year and one_time."
    )

    def values(self) -> dict:
        """The sent fields, left-out and null ones dropped."""
        return {f: v for f, v in self.model_dump().items() if v is not None}


class BenefitPatchIn(In):
    set: BenefitSetIn | None = None
    reset: list[BenefitField] | None = Field(
        default=None, max_length=len(BenefitField.__args__), description="Use the chat's."
    )
    hidden: bool | None = Field(default=None, description="Hide, or show again.")
    match_off: bool | None = Field(
        default=None, description="Credits: this credit never shows on statements."
    )
    amount_from: Day | None = Field(
        default=None,
        description="When a new amount starts. Default: the start of the current cycle. "
        "Cycles that start before it keep the old amount.",
    )


class ResolveIn(In):
    field: BenefitField
    choice: Literal["mine", "chat"] = Field(
        description="mine: keep your value. chat: use the chat's value (drops your edit)."
    )


class BenefitKeepBothIn(In):
    other_key: Key = Field(description="The chat benefit this one is like.")


# --------------------------------------------------------------------------
# Credit matching: the review, match words, cycle marks and credit choices
# (docs/plans/rewards-inputs.md 4 and 6)
# --------------------------------------------------------------------------


class GuessOut(Out):
    benefit_key: str
    benefit_name: str
    cycle_start: str


class ReviewRowOut(Out):
    txn_key: str
    date: str
    account_key: str
    account_name: str | None
    merchant: str | None = Field(description="As Transactions shows it.")
    description: str = Field(description="As the statement printed it. Match words read this.")
    cents: int = Field(description="The credit, positive.")
    trip_key: str | None
    options: list[str] = Field(
        description="Ambiguous rows: the shown benefits whose match words tie. Else empty. A "
        "tie with one shown benefit left is a plain row, with that benefit as the guess."
    )
    note: str | None = Field(
        description='Fixed wording: "Matches more than one benefit. Pick one." on an ambiguous '
        "row. Else null."
    )
    guess: GuessOut | None = Field(
        description='"Count it": a shown credit on the row\'s card, with matching on, whose cycle '
        "at that date has room equal to the amount, then room of at least the amount, then "
        "the most name words in common. Null when no such credit has a cycle at that date."
    )


class NeedsLookOut(Out):
    kind: Literal["lost_choice", "no_cycle", "choice_off", "chat_only"] = Field(
        description="lost_choice: a credit choice lost its row (Delete). no_cycle: match words "
        "or a choice name a benefit, and no cycle holds the row's date. choice_off: a choice "
        "counts the row for a credit that takes no statement rows now (Delete). chat_only: a "
        "covered cycle the chat says is used, and no row matches (Mark not used, Add a match "
        "word). Rows dated before this household year never show."
    )
    text: str = Field(description="Fixed wording. Never a value.")
    benefit_key: str | None = Field(description="Null on a lost Not a benefit choice.")
    benefit_name: str | None = Field(description="Null when the page does not show the benefit.")
    cycle_start: str | None = Field(description="chat_only: the cycle.")
    txn_key: str | None = Field(
        description="lost_choice: the choice's key, for DELETE /api/rewards/credits/{txn_key}. "
        "no_cycle and choice_off: the row's. Null on chat_only."
    )
    date: str | None = Field(
        description="lost_choice: the row's date when the choice was made. no_cycle and "
        "choice_off: the row's date. chat_only: the chat's used_on."
    )
    cents: int | None = Field(description="The row's amount, or the chat's used on chat_only.")


class RewardsReviewOut(Out):
    source_run_id: str | None = Field(description="Null before the first 1.3 run.")
    needs_a_look: list[NeedsLookOut] = Field(
        description="What needs a look and is not already a page notice or orphan."
    )
    credits: list[ReviewRowOut] = Field(
        description='"Card credits to review": no match word and no choice, or ambiguous. By date.'
    )
    returns: list[ReviewRowOut] = Field(
        description='"Looks like returns": a purchase on the card with the same merchant and '
        "amount within 90 days before. A closed group. Each row can still be counted."
    )
    trips: list[ReviewRowOut] = Field(
        description='"Points credits for a trip": rows with a trip_key. A closed group.'
    )
    counts: ReviewCountsOut


def _term_text(v: str) -> str:
    """one_line, and never empty. The service checks the match word rules."""
    s = one_line(v, 4 * TERM_MAX)
    if s is None:
        raise ValueError("empty")
    return s


class TermsIn(In):
    terms: list[Annotated[str, Field(max_length=4 * TERM_MAX)]] = Field(
        max_length=TERMS_MAX,
        description="Every match word for the benefit (0 to 10). They replace the saved ones. "
        "3 to 40 plain characters each, not made only of words like CREDIT or STATEMENT.",
    )

    @field_validator("terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        return [_term_text(t) for t in v]


class SeenTermOut(Out):
    term: str = Field(description="The most common description, digits removed.")
    count: int = Field(
        description='"Seen N times": the chat\'s used cycles with a card credit of the same '
        "cents within 3 days that carries this description."
    )


class TermsOut(Out):
    benefit_key: str
    terms: list[str] = Field(description="Saved match words, A to Z.")
    suggested: list[str] = Field(
        description="Words from the benefit name, less cadence and filler words. Never applied "
        "on their own."
    )
    seen: SeenTermOut | None
    match_off: bool = Field(description="This credit never shows on statements.")


class TermPreviewIn(In):
    benefit_key: Key
    term: str = Field(max_length=4 * TERM_MAX, description="In the body, never the URL.")

    @field_validator("term")
    @classmethod
    def _term(cls, v: str) -> str:
        return _term_text(v)


class PreviewRowOut(Out):
    txn_key: str
    date: str
    merchant: str | None
    description: str
    cents: int
    now_key: str | None = Field(description="The benefit the row counts for now. Null: none.")
    now_name: str | None
    after_key: str | None = Field(
        description="The benefit it would count for with the word. Null: none."
    )
    after_name: str | None
    tie: bool = Field(
        description="With the word, the row would tie with another benefit's word and wait "
        "for review."
    )


class CycleChangeOut(Out):
    benefit_key: str
    benefit_name: str
    start: str
    used_cents_before: int
    used_cents_after: int
    source_before: CycleSource | None
    source_after: CycleSource | None


class TermPreviewOut(Out):
    """Nothing is saved. The word is never echoed."""

    benefit_key: str
    matches: int = Field(
        description="Card credit rows on the benefit's card this year whose description holds "
        'the word ("Matches 8 credits this year").'
    )
    elsewhere: int = Field(
        description="Of those, rows that count for another benefit now, whether or not the "
        "word would move them. rows says where each one would go."
    )
    rows: list[PreviewRowOut] = Field(description="The rows the word holds, by date.")
    cycles: list[CycleChangeOut] = Field(
        description="Cycles whose use or source would change, by benefit and start."
    )


class MarkIn(In):
    used: MoneyText = Field(description='"Mark used": the amount. "Mark not used": "0.00".')
    used_on: Day | None = Field(
        default=None, description="Not in the future. Left out when not used."
    )


class CreditIn(In):
    decision: Literal["link", "not_benefit"] = Field(
        description='link: "Count it" or "Different benefit". not_benefit: "Not a benefit".'
    )
    benefit_key: Key | None = Field(
        default=None, description="link only: a credit on the row's card."
    )
    cycle_start: Day | None = Field(
        default=None,
        description='link only: pins a cycle of that credit, like "Count in <previous cycle> '
        "instead\". Left out: the cycle that holds the row's date.",
    )
    reason: Reason | None = Field(
        default=None, description="not_benefit only: A return, A points credit, Something else."
    )

    @model_validator(mode="after")
    def _fits(self) -> CreditIn:
        if self.decision == "link" and (self.benefit_key is None or self.reason is not None):
            raise ValueError("link")
        if self.decision == "not_benefit" and (
            self.reason is None or self.benefit_key is not None or self.cycle_start is not None
        ):
            raise ValueError("not_benefit")
        return self


class CountedInOut(Out):
    benefit_key: str
    cycle_start: str


class CreditChoiceOut(Out):
    txn_key: str
    decision: Literal["link", "not_benefit"]
    benefit_key: str | None
    cycle_start: str | None
    reason: Reason | None
    counted_in: CountedInOut | None = Field(
        description="Where the row counts now. Null: nowhere (Not a benefit, or no cycle holds "
        "its date)."
    )


# --------------------------------------------------------------------------
# Benefit files (docs/plans/rewards-inputs.md 5 and 6). The file itself is
# rewards_files.BenefitFile; the routes read it from the body as bytes.
# --------------------------------------------------------------------------


class FileProblemOut(Out):
    where: str = Field(
        description='The place in the file, like "benefits[2].amount". "(file)": the whole file. '
        "Never the value. An unknown field shows as (unknown field)."
    )
    rule: str = Field(description='The rule it breaks, like "money-format".')
    message: str = Field(description="Fixed wording. Never the value.")


class FileAddOut(Out):
    key: str = Field(description="The new key, app-<key>.")
    name: str
    kind: BenefitKind
    terms: bool = Field(description="Match words are saved with it.")


class FileChangeOut(Out):
    key: str
    name: str
    fields: list[str] = Field(description="The fields that change. Empty when only words change.")
    amount_from: str | None = Field(
        description='A changed amount starts on this day ("from <starts_on>"). Else null.'
    )
    terms: bool = Field(description="The match words are replaced.")


class FileEndOut(Out):
    key: str
    name: str
    ends_on: str = Field(description="The day before starts_on.")


class BenefitFilePreviewOut(Out):
    """Nothing is written. Lists stay empty while the file has problems."""

    account_key: str | None = Field(description="Null when the file names no card.")
    account_name: str | None
    mode: Literal["merge", "replace"] | None
    starts_on: str | None
    to_add: list[FileAddOut]
    to_change: list[FileChangeOut]
    to_end: list[FileEndOut] = Field(
        description="Replace mode: the card's other benefits that run past the day before "
        "starts_on. Hidden ones are left alone."
    )
    unchanged: int = Field(description="Items that change nothing.")
    problems: list[FileProblemOut]
    base_sig: str | None = Field(
        description="Send it back with the same file to apply. Null while the file has problems."
    )
    no_changes: bool = Field(description='"No changes": a clean file that changes nothing.')


class BenefitFileSavedOut(Out):
    saved: int = Field(description='"Saved. N changes.": added + changed + ended.')
    added: int
    changed: int
    ended: int
    unchanged: int


class FileProblemsOut(Out):
    detail: str = Field(description="Fixed wording.")
    problems: list[FileProblemOut]


class BenefitFileCycleOut(Out):
    start: str
    end: str
    amount: str | None = None


class BenefitFileItemOut(Out):
    """A format 1.3 benefit, plus the app's match words. Fields it does not have
    are left out."""

    key: str
    account_key: str
    name: str
    kind: BenefitKind
    cadence: Cadence | None = None
    amount: str | None = None
    cycles: list[BenefitFileCycleOut] | None = Field(
        default=None,
        description="App benefits that store their cycles (multi_year, one_time). The chat's "
        "cycles stay the chat's and are left out.",
    )
    counted_value: str | None = None
    auto: bool | None = None
    enrolled: bool | None = None
    rolls_over: bool | None = None
    enroll_by: str | None = None
    ends_on: str | None = None
    how_to_use: str | None = None
    note: str | None = None
    cap: str | None = None
    earn_before: str | None = None
    earn_after: str | None = None
    category_keys: list[str] | None = None
    reported_spent: str | None = None
    as_of: str | None = None
    program_key: str | None = None
    status_tier: str | None = None
    match_terms: list[str] | None = Field(
        default=None, description="App only: the saved match words. Sent, they replace them."
    )


class BenefitFileOut(Out):
    """The template: the card's shown benefits in file form, hidden ones left out.
    Uploaded unchanged, it previews "No changes"."""

    kind: Literal["budget-app-benefits"]
    version: Literal[1]
    account_key: str
    starts_on: str = Field(description="Today.")
    mode: Literal["merge", "replace"]
    benefits: list[BenefitFileItemOut]
