"""`cli ai-cache-check` (PLAN 5.4, 11): prove the Ask prefix is cached.

Sends one fixed question twice with the exact Ask request (tools, rules,
household context), one call after the other, and reports the cache counts.
The second call must read from the cache. It runs even while the AI setting
is off (it is the check before turning AI on), but it needs the key and
respects the spend-limit block and the caps. Each call is an ai_usage row
(feature cache_check). It costs a few cents.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai import tools
from app.ai.ask import MAX_TOKENS
from app.ai.context import household_context
from app.ai.facts import ensure_facts
from app.ai.postprocess import Scrub, filter_text, load_last4s
from app.ai.pricing import estimate_micro_usd
from app.ai.prompts import ASK_RULES
from app.ai.safe import ai_connection
from app.config import Settings
from app.db.connect import connect
from app.importer.runner import private_rules_for

QUESTION = "Cache check. In one short sentence, how many accounts are in the household context?"


async def cache_check(settings: Settings) -> dict:
    private = private_rules_for(settings)
    sig = await asyncio.to_thread(ensure_facts, settings.db_path, private)

    def _context() -> str:
        with ai_connection(settings.db_path) as ai:
            text = household_context(ai, sig, settings.db_path)
        return Scrub(private, load_last4s(settings.db_path)).text(text)  # as Ask sends it

    context_text = await asyncio.to_thread(_context)
    conn = connect(settings.db_path)
    try:
        model = ai_settings.load(conn).ask_model
    finally:
        conn.close()
    effort = ai_settings.EFFORT["ask"]
    params = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [
            {"type": "text", "text": ASK_RULES},
            {"type": "text", "text": context_text, "cache_control": {"type": "ephemeral"}},
        ],
        "tools": tools.definitions(),
        "messages": [{"role": "user", "content": [{"type": "text", "text": QUESTION}]}],
        "output_config": {"effort": effort},
        "cache_control": {"type": "ephemeral"},
    }

    async def request(client: Any) -> Any:
        return await client.messages.create(**params)

    estimate = estimate_micro_usd(model, len(context_text) // 3 + 4_000, MAX_TOKENS)
    info = ai_client.CallInfo(
        "cache_check", model, effort, require_enabled=False, estimate_micro=estimate
    )
    calls = []
    for _ in range(2):
        message = await ai_client.call(settings, info, request)
        usage = message.usage
        text = " ".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        calls.append(
            {
                "cache_creation_input_tokens": int(usage.cache_creation_input_tokens or 0),
                "cache_read_input_tokens": int(usage.cache_read_input_tokens or 0),
                "input_tokens": int(usage.input_tokens or 0),
                "output_tokens": int(usage.output_tokens or 0),
                "answer": filter_text(text, private)[:200],
            }
        )
    return {
        "model": model,
        "calls": calls,
        "ok": calls[1]["cache_read_input_tokens"] > 0,
    }
