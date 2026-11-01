"""Response and request models for the AI routes (PLAN 7, 8.2)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.ai.settings import AiSettings


class Out(BaseModel):
    model_config = {"extra": "forbid"}


class AiStatus(Out):
    enabled: bool = Field(description="The admin setting.")
    key_present: bool = Field(description="ANTHROPIC_API_KEY is set on the server.")
    available: bool = Field(description="AI features may run now.")
    reason: Literal["off", "no_key", "spend_limit"] | None
    blocked_until: str | None = Field(description="UTC time a spend-limit pause ends.")
    blocked_reason: str | None
    thread_retention_days: int = Field(description="Ask conversations are deleted after this.")


class PriceTier(Out):
    over_tokens: int
    input: str
    write_5m: str
    write_1h: str
    cache_read: str
    output: str


class ModelPrice(Out):
    model: str
    input: str = Field(description="USD per million tokens, as a decimal string.")
    write_5m: str
    write_1h: str
    cache_read: str
    output: str
    long_prompt: PriceTier | None


class AiSettingsOut(Out):
    settings: AiSettings
    status: AiStatus
    effort: dict[str, str] = Field(description="Effort per feature (fixed).")
    models: list[str]
    prices: list[ModelPrice]
    prices_checked_on: str
    prices_source: str


class UsageCall(Out):
    at: str
    feature: str
    model: str
    effort: str | None
    input_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int
    cost_micro_usd: int
    stop_reason: str | None
    latency_ms: int | None
    error: str | None
    web_searches: int = 0


class AiUsage(Out):
    month: str
    month_cost_micro_usd: int
    month_calls: int
    month_by_feature_micro_usd: dict[str, int]
    monthly_cap_cents: int
    month_used_pct: str | None
    warning_pct: int | None = Field(description="50 or 80 once the month passes that share.")
    today_cost_micro_usd: int
    today_calls: int
    today_questions: int
    daily_cap_cents: int
    daily_questions: int
    cache_hit_pct: str | None
    calls: list[UsageCall] = Field(description="The last 20 calls, newest first. Metadata only.")


class AskIn(Out):
    question: str = Field(max_length=4000)
    thread_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ThreadSummary(Out):
    thread_id: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int
    closed: bool
    model: str


class ThreadsOut(Out):
    threads: list[ThreadSummary]


class ThreadTurn(Out):
    question: str
    answer: str
    tools: list[str] = Field(description="Status labels of the tools used, in order.")
    notice: str | None


class ThreadOut(Out):
    thread_id: str
    title: str
    created_at: str
    updated_at: str
    closed: bool
    model: str
    turns: list[ThreadTurn]
    cost_micro_usd: int | None = Field(description="What the thread cost. Admin only.")


class AiReview(Out):
    run_id: str
    month: str | None
    model: str
    created_at: str
    summary: str
    changes: list[str]
    anomalies: list[str]
    actions: list[str]


class AiReviewOut(Out):
    review: AiReview | None


class RunReviewOut(Out):
    status: str
    reason: str | None = None
    message: str | None = Field(default=None, description="Why it was skipped, to show as is.")


class CategorySuggestion(Out):
    id: int
    txn_key: str
    txn_date: str
    amount_cents: int
    merchant: str | None
    description: str = Field(description="Statement text, shown when there is no merchant.")
    account_name: str
    category_key: str
    category_name: str
    category_emoji: str | None
    confidence: int
    reason: str | None
    model: str
    created_at: str


class SuggestionsOut(Out):
    suggestions: list[CategorySuggestion]


class SuggestionRunOut(Out):
    merchants: int = Field(description="Merchants sent to the model.")
    suggested: int = Field(description="New suggestions stored (one per transaction).")


class SuggestionBulkIn(Out):
    # Bounded like the path id: a huge id would overflow SQLite's integer (500, not 422).
    ids: list[Annotated[int, Field(ge=1, le=1 << 31)]] = Field(min_length=1, max_length=500)
    action: Literal["approve", "reject"]


class SuggestionDecisionOut(Out):
    changed: int
