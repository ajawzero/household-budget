"""Request and response models for transactions, edits and rules (PLAN 8.2, Phase 4).

Money is integer cents, signed (negative = money out or a new charge).
Dates are ISO `YYYY-MM-DD`, months `YYYY-MM`.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.api.models import Out

KEY_RE = r"^[a-z][a-z0-9_-]{0,63}$"
TXN_KEY_RE = r"^[0-9a-f]{64}$"
MONTH_RE = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
MAX_FILTER_ITEMS = 50
MERCHANT_MAX = 64
NOTES_MAX = 500
TAG_MAX = 32
TAGS_MAX = 10
BULK_MAX = 500

Key = Annotated[str, StringConstraints(pattern=KEY_RE)]
TxnKey = Annotated[str, StringConstraints(pattern=TXN_KEY_RE)]
Month = Annotated[str, StringConstraints(pattern=MONTH_RE)]
Sort = Literal["date_desc", "date_asc", "amount_desc", "amount_asc"]
MatchKind = Literal["merchant_equals", "description_contains"]
CategorySource = Literal["override", "rule", "imported"]

_WS = re.compile(r"\s+")


class In(BaseModel):
    model_config = {"extra": "forbid"}


# Bidi controls can make shown text look different from what it holds.
# Other format characters (the zero-width joiner in many emoji, for example)
# are allowed: detect_sensitive strips them before it scans.
_BIDI = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _no_controls(text: str, allow_newlines: bool = False) -> str:
    for ch in text:
        if (unicodedata.category(ch) == "Cc" and not (allow_newlines and ch in "\n\t")) or (
            ch in _BIDI
        ):
            raise ValueError("control characters are not allowed")
    return text


def one_line(text: str, max_len: int) -> str | None:
    """Collapse whitespace and trim. Empty means None (clear)."""
    s = _WS.sub(" ", _no_controls(text)).strip()
    if len(s) > max_len:
        raise ValueError("too long")
    return s or None


# --------------------------------------------------------------------------
# Filters (shared by search, summary)
# --------------------------------------------------------------------------


class TxnFilters(In):
    """Filters for search and summary. Lists match any of their items; filters combine with AND.

    `owners` matches the cardholder when the row has one, else the account owner.
    `amount_min_cents` and `amount_max_cents` compare the absolute amount.
    `direction` "out" is amount < 0, "in" is amount > 0.
    `uncategorized` true keeps rows whose shown category is uncategorized.
    `edited` true keeps rows with any override.
    `hidden`: "exclude" (default) leaves hidden rows out, "only" shows only them.
    `trip_key` keeps rows tagged with that trip, whatever their date.
    """

    date_from: date | None = None
    date_to: date | None = None
    months: list[Month] = Field(default_factory=list, max_length=MAX_FILTER_ITEMS)
    accounts: list[Key] = Field(default_factory=list, max_length=MAX_FILTER_ITEMS)
    categories: list[Key] = Field(default_factory=list, max_length=MAX_FILTER_ITEMS)
    category_groups: list[Key] = Field(default_factory=list, max_length=MAX_FILTER_ITEMS)
    owners: list[Key] = Field(default_factory=list, max_length=MAX_FILTER_ITEMS)
    amount_min_cents: int | None = Field(default=None, ge=0)
    amount_max_cents: int | None = Field(default=None, ge=0)
    direction: Literal["in", "out"] | None = None
    is_transfer: bool | None = None
    is_reimbursable: bool | None = None
    needs_review: bool | None = None
    uncategorized: bool | None = None
    edited: bool | None = None
    hidden: Literal["exclude", "include", "only"] = "exclude"
    recurring_key: Key | None = None
    plan_key: Key | None = None
    trip_key: Key | None = None


class TxnSearchIn(TxnFilters):
    """POST /api/transactions/search. `q` is free text, kept out of URLs and logs.

    It matches the description, merchant (shown and imported), notes, tags and
    category name, ignoring case. The summary comes with the first page only
    (no cursor) unless `include_summary` is false.
    """

    q: str | None = Field(default=None, max_length=100)
    sort: Sort = "date_desc"
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=100, ge=1, le=500)
    include_summary: bool = True


# --------------------------------------------------------------------------
# Rows and pages
# --------------------------------------------------------------------------


class TxnRowOut(Out):
    txn_key: str
    txn_date: str
    post_date: str | None
    account_key: str
    account_name: str
    institution: str | None
    description: str = Field(description="The statement text, sanitized.")
    merchant: str | None = Field(description="Shown merchant: the override, else imported.")
    imported_merchant: str | None
    category_key: str = Field(description="Shown category: override, else rule, else imported.")
    category_name: str | None
    category_emoji: str | None
    category_group: str | None
    category_kind: str | None
    category_source: CategorySource
    rule_id: int | None = Field(description="The rule that set the category, if any.")
    imported_category_key: str
    cardholder: str | None
    cardholder_label: str | None
    owner: str | None = Field(description="Cardholder, else the account owner.")
    owner_label: str | None
    amount_cents: int
    txn_type: str
    is_transfer: bool
    is_reimbursable: bool
    exclude_from_budget: bool
    hidden: bool
    needs_review: bool
    pre_history: bool
    plan_key: str | None
    recurring_key: str | None
    trip_key: str | None
    trip_name: str | None = Field(
        description="The trip's name, or null when the app has no trip with that key."
    )
    import_notes: str | None = Field(description="The note from the export, if any.")
    notes: str | None = Field(description="The household's own note.")
    tags: list[str]
    has_override: bool
    overridden: list[str] = Field(
        description="Edit fields set by an override: category_key, merchant, is_transfer, "
        "is_reimbursable, exclude_from_budget, hidden, notes, tags."
    )


class DayTotalOut(Out):
    date: str
    total_cents: int = Field(description="Signed sum of every filtered row on this date.")
    count: int


class LargestTxnOut(Out):
    txn_key: str
    txn_date: str
    merchant: str | None
    description: str
    amount_cents: int


class TxnSummaryOut(Out):
    count: int
    total_cents: int = Field(description="Signed sum of every filtered row.")
    income_cents: int = Field(description="Income categories, transfers left out. Positive.")
    spending_cents: int = Field(
        description="Spending categories, transfers left out, refunds netted. Positive = spent."
    )
    average_cents: int | None = Field(description="Mean signed amount, rounded.")
    largest: LargestTxnOut | None = Field(description="Largest absolute amount, not a transfer.")
    first_date: str | None
    last_date: str | None


class TxnSearchOut(Out):
    rows: list[TxnRowOut]
    next_cursor: str | None
    days: list[DayTotalOut] = Field(
        description="One per date on this page, totals over the whole filtered set."
    )
    summary: TxnSummaryOut | None


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------


class TxnPatchIn(In):
    """Fields to set. A field left out is unchanged. null clears the override field
    (the imported value, or the rule's category, shows again). An empty merchant or
    note, or an empty tag list, also clears."""

    category_key: Key | None = None
    merchant: str | None = Field(default=None, max_length=200)
    is_transfer: bool | None = None
    is_reimbursable: bool | None = None
    exclude_from_budget: bool | None = None
    hidden: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=TAGS_MAX)

    @field_validator("merchant")
    @classmethod
    def _merchant(cls, v: str | None) -> str | None:
        return None if v is None else one_line(v, MERCHANT_MAX)

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = _no_controls(v, allow_newlines=True).strip()
        if len(s) > NOTES_MAX:
            raise ValueError("too long")
        return s or None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out: list[str] = []
        seen: set[str] = set()
        for t in v:
            s = one_line(t, TAG_MAX)
            if s and s.casefold() not in seen:
                seen.add(s.casefold())
                out.append(s)
        return out or None

    def changes(self) -> dict:
        return {f: getattr(self, f) for f in self.model_fields_set}


class TxnBulkIn(In):
    txn_keys: list[TxnKey] = Field(min_length=1, max_length=BULK_MAX)
    changes: TxnPatchIn


class TxnBulkOut(Out):
    count: int = Field(description="Rows in the request.")
    updated: int = Field(description="Rows whose shown values changed.")


class TxnEventOut(Out):
    id: int | None = Field(description="None for the synthesized 'imported' entry.")
    at: str | None
    kind: str = Field(
        description="imported, category_changed, merchant_changed, transfer_changed, "
        "reimbursable_changed, exclude_changed, hidden_changed, note_added, note_changed, "
        "note_removed, tags_changed, rekeyed, orphaned."
    )
    via: Literal["edit", "bulk", "rule", "import", "suggestion"] = Field(
        description="'suggestion': a category suggestion someone approved."
    )
    from_value: str | None
    to_value: str | None
    user: str | None = Field(description="Display name of who made the change.")
    rule_id: int | None
    prev_txn_key: str | None = Field(description="Set on 'rekeyed': the key it came from.")
    run_id: str | None = Field(description="Set on 'imported': the run that last wrote the row.")


class TxnEventsOut(Out):
    txn_key: str
    events: list[TxnEventOut] = Field(description="Oldest first.")


class OverrideOut(Out):
    category_key: str | None
    merchant: str | None
    is_transfer: bool | None
    is_reimbursable: bool | None
    exclude_from_budget: bool | None
    hidden: bool | None
    notes: str | None
    tags: list[str]


class OrphanedEditOut(Out):
    txn_key: str
    txn_date: str | None = Field(description="From the guard (the row at edit time).")
    amount_cents: int | None
    desc_norm: str | None
    imported_merchant: str | None
    account_key: str | None
    account_name: str | None
    statement_key: str | None
    removed_at: str | None
    orphaned_at: str | None
    override: OverrideOut
    updated_at: str
    updated_by: str | None


class OrphanedEditsOut(Out):
    rows: list[OrphanedEditOut]


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def _pattern_text(v: str | None) -> str | None:
    return None if v is None else _no_controls(v)


class RuleIn(In):
    match_kind: MatchKind
    pattern: str = Field(min_length=1, max_length=200)
    category_key: Key
    created_from_txn_key: TxnKey | None = None

    pattern_ok = field_validator("pattern")(_pattern_text)


class RulePatchIn(In):
    match_kind: MatchKind | None = None
    pattern: str | None = Field(default=None, min_length=1, max_length=200)
    category_key: Key | None = None
    active: bool | None = None

    pattern_ok = field_validator("pattern")(_pattern_text)


class RulePreviewIn(In):
    match_kind: MatchKind
    pattern: str = Field(min_length=1, max_length=200)
    category_key: Key | None = None

    pattern_ok = field_validator("pattern")(_pattern_text)


class RulePreviewOut(Out):
    matches: int = Field(description="Live rows the rule would match.")
    edited: int = Field(description="Of those, rows with a category override (kept).")
    would_change: int = Field(description="Rows whose shown category would change.")


class RuleOut(Out):
    id: int
    match_kind: MatchKind
    pattern: str
    category_key: str
    category_name: str | None
    category_emoji: str | None
    active: bool
    applied_count: int = Field(description="Live rows whose category this rule sets now.")
    created_from_txn_key: str | None
    created_by: str | None
    created_at: str
    updated_at: str


class RulesOut(Out):
    rules: list[RuleOut]
