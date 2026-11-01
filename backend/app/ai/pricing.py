"""Model prices and the cost of one call (PLAN 7.2).

Prices are USD per million tokens, checked Oct 22, 2026 against
https://platform.claude.com/docs/en/about-claude/pricing. Check them again when a model changes.

| Model             | Input | 5m write | 1h write | Cache read | Output |
|-------------------|-------|----------|----------|------------|--------|
| claude-opus-5-5   | 4.00  | 5.00     | 8.00     | 0.20       | 20.00  |
| claude-sonnet-5-5 | 2.00  | 2.50     | 4.00     | 0.10       | 10.00  |
| claude-haiku-5-5  | 0.10  | 0.125    | 0.20     | 0.01       | 0.50   |
| haiku, prompt > 100K tokens | 0.50 | 0.625 | 1.00 | 0.05     | 2.50   |

Opus 5.5 and Sonnet 5.5 cache reads are 0.05x input. Haiku 5.5 bills a whole
request at the higher row when its prompt (input + cache reads + cache writes)
is over 100,000 tokens.

Global routing has no multiplier. US-only inference costs 1.1x on every price
above. The app never asks for it (no `inference_geo` is sent), but a workspace
default can set it, so a call whose `usage.inference_geo` is "us" is priced at
1.1x.

USD per million tokens equals micro-USD per token, so a call's cost in
micro-USD is the sum of tokens x price, rounded half up once at the end.

Claude's web search (the trip planner's research call) costs $10 per 1,000
searches on top of tokens, checked Oct 27, 2026 against
https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool.
A search that fails is not billed, and the API counts only billed ones in
`usage.server_tool_use.web_search_requests`. Its result filtering runs code at
Anthropic with no charge beyond tokens. The fee is not raised for US-only calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

CHECKED_ON = "2026-10-22"
SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"


@dataclass(frozen=True)
class Price:
    input: Decimal
    write_5m: Decimal
    write_1h: Decimal
    cache_read: Decimal
    output: Decimal


def _p(*values: str) -> Price:
    return Price(*(Decimal(v) for v in values))


PRICES: dict[str, Price] = {
    "claude-opus-5-5": _p("4", "5", "8", "0.20", "20"),
    "claude-sonnet-5-5": _p("2", "2.50", "4", "0.10", "10"),
    "claude-haiku-5-5": _p("0.10", "0.125", "0.20", "0.01", "0.50"),
}
# Prompts over this many tokens use the second price on these models.
LONG_PROMPT_TOKENS = 100_000
LONG_PRICES: dict[str, Price] = {
    "claude-haiku-5-5": _p("0.50", "0.625", "1", "0.05", "2.50"),
}
# One web search in micro-USD ($10 per 1,000).
WEB_SEARCH_MICRO_USD = 10_000

# US-only inference (`usage.inference_geo` "us") costs this times every price.
US_ONLY = "us"
US_ONLY_MULTIPLIER = Decimal("1.1")

MODELS = tuple(PRICES)


class UsageLike(Protocol):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


@dataclass(frozen=True)
class Tokens:
    input: int = 0
    write_5m: int = 0
    write_1h: int = 0
    cache_read: int = 0
    output: int = 0
    # Where the call ran (`usage.inference_geo`). Sets the price, not a count.
    inference_geo: str | None = None
    # Billed web searches (`usage.server_tool_use.web_search_requests`).
    web_searches: int = 0

    @property
    def cache_write(self) -> int:
        return self.write_5m + self.write_1h

    @property
    def prompt(self) -> int:
        """Every input token of the request: uncached, written and read."""
        return self.input + self.cache_write + self.cache_read


def tokens_of(usage: object) -> Tokens:
    """Token counts from an SDK `usage` object. Cache writes split by TTL when the
    API says so (`usage.cache_creation`), else they count as 5-minute writes.
    `inference_geo` and the web search count come along because they set the price."""
    created = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    split = getattr(usage, "cache_creation", None)
    w5 = int(getattr(split, "ephemeral_5m_input_tokens", 0) or 0) if split else 0
    w1 = int(getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split else 0
    if w5 + w1 != created:
        w5, w1 = created, 0
    return Tokens(
        input=int(getattr(usage, "input_tokens", 0) or 0),
        write_5m=w5,
        write_1h=w1,
        cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        output=int(getattr(usage, "output_tokens", 0) or 0),
        inference_geo=getattr(usage, "inference_geo", None),
        web_searches=int(
            getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
        ),
    )


class UnknownModel(KeyError):
    """A model with no price. The app never calls one (settings only offer MODELS)."""


def price_for(model: str, prompt_tokens: int = 0) -> Price:
    if model not in PRICES:
        raise UnknownModel(model)
    if prompt_tokens > LONG_PROMPT_TOKENS and model in LONG_PRICES:
        return LONG_PRICES[model]
    return PRICES[model]


def cost_micro_usd(model: str, t: Tokens) -> int:
    """The cost of one call in micro-USD, rounded half up. US-only calls cost 1.1x
    on tokens. Web searches add their fee."""
    p = price_for(model, t.prompt)
    total = (
        t.input * p.input
        + t.write_5m * p.write_5m
        + t.write_1h * p.write_1h
        + t.cache_read * p.cache_read
        + t.output * p.output
    )
    if t.inference_geo == US_ONLY:
        total *= US_ONLY_MULTIPLIER
    total += t.web_searches * WEB_SEARCH_MICRO_USD
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def estimate_micro_usd(
    model: str, prompt_tokens: int, output_tokens: int, web_searches: int = 0
) -> int:
    """An estimate before a call, not an upper bound: every prompt token at the
    plain input rate. A cold prompt is billed as a 5-minute cache write (1.25x
    input), and output can run to max_tokens, so a call can cost more."""
    return cost_micro_usd(
        model, Tokens(input=prompt_tokens, output=output_tokens, web_searches=web_searches)
    )


def price_table() -> list[dict]:
    """For Settings: each model's prices as decimal strings (USD per million tokens)."""
    out = []
    for model, p in PRICES.items():
        row = {
            "model": model,
            "input": str(p.input),
            "write_5m": str(p.write_5m),
            "write_1h": str(p.write_1h),
            "cache_read": str(p.cache_read),
            "output": str(p.output),
            "long_prompt": None,
        }
        long = LONG_PRICES.get(model)
        if long is not None:
            row["long_prompt"] = {
                "over_tokens": LONG_PROMPT_TOKENS,
                "input": str(long.input),
                "write_5m": str(long.write_5m),
                "write_1h": str(long.write_1h),
                "cache_read": str(long.cache_read),
                "output": str(long.output),
            }
        out.append(row)
    return out
