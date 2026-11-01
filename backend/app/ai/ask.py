"""Ask: questions answered with the read-only tools (PLAN 5.6, 7.1, 7.3, 7.4).

A turn:
1. `prepare` (before any streaming): AI must be available, the question must
   pass the checks (length, `detect_sensitive`; private-list names become
   "personal subscription", an account's last four becomes "…"), and the caps
   must allow a question. Then the question is stored, in the person's open
   thread or a new one.
2. `stream_turn`: up to MAX_CALLS model calls. Each call streams; tool status
   chips and filtered text go out as server-sent events. Tool calls run on
   the AI connection. Every response and tool result is stored as is.
3. The turn ends with a `done` event carrying the whole answer filtered at
   once (it replaces the streamed text) and, for an admin, the cost.
If the browser leaves mid-turn, the model call is cancelled (its usage is
still logged) and the thread is closed, off the event loop. `when_settled`
says when that is over.

Threads are append-only: model, effort and the household context are pinned
when a thread starts, and nothing stored is ever edited or removed until the
thread is deleted. What is sent again goes through the last scrub (PLAN 5.7),
so a name added to the private list later is not sent again. A thread closes
(the next question starts a new one) after NEW_THREAD_TURNS turns, near
NEW_THREAD_TOKENS prompt tokens, when its pin is gone (a restart), or when a
turn does not end cleanly (an error, a refusal, a cut-off answer, too many
steps), because then its last message cannot take another question.

Prompt layout (cached): tools (fixed, sorted) -> system [rules, household
context with a cache breakpoint] -> messages, with top-level automatic
caching for the growing loop. The user turn is "Today is <date>. Asked by
<name>. <question>". tool_choice stays auto.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from app import config
from app.ai import client as ai_client
from app.ai import ledger, tools
from app.ai import settings as ai_settings
from app.ai.context import household_context
from app.ai.facts import ensure_facts
from app.ai.postprocess import PRIVATE_LABEL, Scrub, StreamFilter, filter_text, load_last4s
from app.ai.pricing import Tokens, cost_micro_usd, estimate_micro_usd, tokens_of
from app.ai.prompts import ASK_RULES
from app.ai.safe import AiConnection, open_ai_connection
from app.auth.users import User
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.importer.runner import private_rules_for
from app.importer.sanitize import PrivateRules, detect_sensitive, normalize

log = logging.getLogger("app.ai")

MAX_CALLS = 6
MAX_TOKENS = 8_000
QUESTION_MAX = 2_000
NEW_THREAD_TURNS = 10
NEW_THREAD_TOKENS = 80_000
# A first guess at a question's cost when there is no earlier call to go by.
FIRST_PROMPT_TOKENS = 12_000
FIRST_OUTPUT_TOKENS = 2_000

PREFIX = re.compile(r"^Today is \d{4}-\d{2}-\d{2}\. Asked by [^\n]*?\. ")

NEW_THREAD_TEXT = "New conversation. Claude can't see the questions above."
NOTICE = {
    "refusal": "Claude can't help with that question.",
    "max_tokens": "The answer was cut off. Try a narrower question.",
    "too_many_steps": "That question needed more steps than one answer allows. "
    "Try a narrower question.",
    "stopped": "The answer stopped early. Your next question starts a new conversation.",
}


class QuestionError(Exception):
    """The question was not accepted. `message` is shown as is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _iso() -> str:
    return ai_settings.iso(_now())


# --------------------------------------------------------------------------
# Question checks (PLAN 5.6)
# --------------------------------------------------------------------------


def check_question(text: str, private: PrivateRules | None) -> str:
    """The question as it will be sent. Raises QuestionError when it is refused."""
    text = normalize(text).strip()
    if not text:
        raise QuestionError("Type a question first.")
    if len(text) > QUESTION_MAX:
        raise QuestionError(f"Keep the question under {QUESTION_MAX:,} characters.")
    if detect_sensitive(text):
        raise QuestionError(
            "The question looks like it has an account or card number, a phone number, an "
            "email or an address. Leave that part out and ask again."
        )
    if private:
        spans = private.spans(text)
        for start, end in reversed(spans):
            text = text[:start] + PRIVATE_LABEL + text[end:]
    return text


# --------------------------------------------------------------------------
# Threads (stored as sent and returned, never edited)
# --------------------------------------------------------------------------


def _append(conn: sqlite3.Connection, thread_id: str, role: str, kind: str, content: Any) -> int:
    row = conn.execute(
        "SELECT coalesce(max(seq), 0) + 1 FROM ai_messages WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    seq = int(row[0])
    conn.execute(
        """INSERT INTO ai_messages (thread_id, seq, role, kind, content_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (thread_id, seq, role, kind, json.dumps(content, ensure_ascii=False), _iso()),
    )
    return seq


def append_message(db_path: Any, thread_id: str, role: str, kind: str, content: Any) -> None:
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            _append(conn, thread_id, role, kind, content)
    finally:
        conn.close()


def model_messages(conn: sqlite3.Connection, thread_id: str) -> list[dict]:
    """The thread's messages exactly as stored. Notices are not sent."""
    return [
        {"role": r["role"], "content": json.loads(r["content_json"])}
        for r in conn.execute(
            """SELECT role, content_json FROM ai_messages
               WHERE thread_id = ? AND kind <> 'notice' ORDER BY seq""",
            (thread_id,),
        )
    ]


_THINKING = ("thinking", "redacted_thinking")


def _result(content: Any, scrub: Scrub) -> Any:
    """A tool result's content as it is sent: scrubbed value by value, as
    `tools.execute` did, and written with `tools.dump`, so an unchanged scrub gives
    the same bytes. (A scrub of the JSON text would mask a number equal to a last
    four and miss a name written as \\u escapes.) An error message is plain text."""
    if not isinstance(content, str):
        return scrub.json(content)
    try:
        value = json.loads(content)
    except ValueError:
        return scrub.text(content)
    return tools.dump(scrub.json(value))


def _scrubbed(block: dict, scrub: Scrub) -> dict:
    """One content block as it is sent: text, tool input and tool results go through
    the scrub. Thinking blocks are signed and go back unchanged."""
    kind = block.get("type")
    if kind == "text":
        return {**block, "text": scrub.text(block.get("text", ""))}
    if kind == "tool_use":
        return {**block, "input": scrub.json(block.get("input", {}))}
    if kind == "tool_result":
        return {**block, "content": _result(block.get("content", ""), scrub)}
    return block


def to_send(messages: list[dict], scrub: Scrub, drop_thinking: int = 0) -> list[dict]:
    """Stored messages as they are sent again: through the last scrub (PLAN 5.7),
    so a name put on the private list since is not sent again. The stored rows
    stay as they are. The first `drop_thinking` messages go without their
    thinking blocks (see `_Pin`). A reply left empty (it was only thinking) is
    left out, since the API refuses an empty message, and the user messages
    around it go as one."""
    out: list[dict] = []
    merge = False
    for i, m in enumerate(messages):
        content = [
            _scrubbed(b, scrub)
            for b in m["content"]
            if not (i < drop_thinking and b.get("type") in _THINKING)
        ]
        if not content:
            merge = True
        elif merge and out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + content}
            merge = False
        else:
            out.append({"role": m["role"], "content": content})
            merge = False
    return out


@dataclass(frozen=True)
class _Pin:
    """What a thread's turns are sent with. The API takes a replayed thinking block
    only while everything sent before it (system prompt included) is unchanged,
    and the cached prefix needs the same bytes, so the household context is the
    one the thread started with (the tools give current numbers). When the scrub
    changes anything earlier turns send (the private list or the last fours
    changed), every earlier turn goes without its thinking blocks from then on:
    the API takes only a leading run of turns without them."""

    context: str  # the household context when the thread started, before the scrub
    scrub: Scrub  # the scrub the newest turn was sent with
    drop_thinking: int = 0  # leading messages sent without their thinking blocks


# In memory, newest last. A thread without a pin (after a restart) starts over.
MAX_PINS = 200
_PINS: OrderedDict[str, _Pin] = OrderedDict()
_PINS_LOCK = threading.Lock()


def _pinned(thread_id: str) -> _Pin | None:
    with _PINS_LOCK:
        pin = _PINS.get(thread_id)
        if pin is not None:
            _PINS.move_to_end(thread_id)
        return pin


def _set_pin(thread_id: str, pin: _Pin | None) -> None:
    with _PINS_LOCK:
        if pin is None:
            _PINS.pop(thread_id, None)
            return
        _PINS[thread_id] = pin
        _PINS.move_to_end(thread_id)
        while len(_PINS) > MAX_PINS:
            _PINS.popitem(last=False)


def _repinned(pin: _Pin, scrub: Scrub, history: list[dict]) -> _Pin:
    """The pin for a turn sent with `scrub`, after the stored `history`."""
    before = pin.scrub
    if before.private == scrub.private and before.last4s == scrub.last4s:
        return replace(pin, scrub=scrub)
    changed = scrub.text(pin.context) != before.text(pin.context) or to_send(
        history, scrub
    ) != to_send(history, before)
    return _Pin(pin.context, scrub, len(history) if changed else pin.drop_thinking)


@dataclass
class Prepared:
    thread_id: str
    new_thread: bool
    model: str
    effort: str
    messages: list[dict]  # as stored; `to_send` makes what is sent
    notice: str | None = None
    user_id: int | None = None
    is_admin: bool = False
    estimate_micro: int = 0
    private: PrivateRules | None = field(default=None, repr=False)
    # Set when the browser left mid-turn: cancels the model call, then closes the thread.
    settling: asyncio.Task | None = field(default=None, repr=False)


def first_name(user: User) -> str:
    """Only the first name goes to the model (PLAN 14.3 Q6.3)."""
    words = user.display_name.split()
    return words[0] if words else "someone"


def _open_thread(conn: sqlite3.Connection, thread_id: str | None, user: User) -> sqlite3.Row | None:
    if not thread_id:
        return None
    row = conn.execute(
        "SELECT * FROM ai_threads WHERE thread_id = ? AND user_id = ?", (thread_id, user.id)
    ).fetchone()
    if row is None or row["closed"]:
        return None
    if row["turn_count"] >= NEW_THREAD_TURNS or row["prompt_tokens"] >= NEW_THREAD_TOKENS:
        return None
    if _pinned(thread_id) is None:  # its context is gone (a restart)
        return None
    return row


def prepare(settings: Settings, user: User, question: str, thread_id: str | None) -> Prepared:
    """Checks, caps and storing the question. Raises QuestionError or AiError."""
    private = private_rules_for(settings)
    text = check_question(question, private)
    # A typed last four is masked like everywhere else (the model never sees them).
    text = Scrub(private, load_last4s(settings.db_path)).text(text)
    conn = connect(settings.db_path)
    try:
        st = ai_settings.status(conn, settings)
        if not st["available"]:
            raise ai_client.AiError(ai_client.unavailable_text(conn, st["reason"]), "unavailable")
        s = ai_settings.load(conn)
        thread = _open_thread(conn, thread_id, user)
        model = thread["model"] if thread else s.ask_model
        effort = thread["effort"] if thread else ai_settings.EFFORT["ask"]
        estimate = max(
            ledger.last_cost(conn, "ask"),
            estimate_micro_usd(model, FIRST_PROMPT_TOKENS, FIRST_OUTPUT_TOKENS),
        )
        try:
            ledger.check_caps(conn, s, "ask", estimate, _now(), new_question=True)
        except ledger.CapReached as cap:
            raise ai_client.AiError(cap.message, "cap") from None
        notice = None
        if thread_id and thread is None:
            notice = NEW_THREAD_TEXT
        user_turn = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Today is {config.household_today().isoformat()}. "
                    f"Asked by {first_name(user)}. {text}",
                }
            ],
        }
        with write_transaction(conn):
            if thread is None:
                new_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO ai_threads (thread_id, user_id, created_at, updated_at, model,
                           effort, title, turn_count, prompt_tokens, closed)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0)""",
                    (new_id, user.id, _iso(), _iso(), model, effort, text[:120]),
                )
                tid = new_id
            else:
                tid = thread["thread_id"]
            _append(conn, tid, "user", "question", user_turn["content"])
            messages = model_messages(conn, tid)
        return Prepared(
            thread_id=tid,
            new_thread=thread is None,
            model=model,
            effort=effort,
            messages=messages,
            notice=notice,
            user_id=user.id,
            is_admin=user.is_admin,
            estimate_micro=estimate,
            private=private,
        )
    finally:
        conn.close()


def _finish(
    db_path: Any, thread_id: str, prompt_tokens: int, closed: bool, notice: str | None
) -> None:
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            if notice:
                _append(conn, thread_id, "assistant", "notice", [{"type": "text", "text": notice}])
            conn.execute(
                """UPDATE ai_threads SET turn_count = turn_count + 1, updated_at = ?,
                       prompt_tokens = max(prompt_tokens, ?),
                       closed = max(closed, ?)
                   WHERE thread_id = ?""",
                (_iso(), prompt_tokens, int(closed), thread_id),
            )
    finally:
        conn.close()
    if closed:
        _set_pin(thread_id, None)


def _record_stopped(db_path: Any, info: ai_client.CallInfo, seen: dict) -> None:
    """The usage row of a call cancelled mid-stream: the prompt tokens the API
    reported when the stream started, and the output so far estimated from the
    text, tool input and thinking the stream counted, as for a stream that
    breaks (hidden thinking cannot be counted)."""
    t = tokens_of(seen["usage"]) if seen["usage"] is not None else Tokens()
    chars = getattr(seen["stream"], "chars", 0)  # client._Counted
    t = replace(t, output=max(t.output, chars // ai_client.CHARS_PER_TOKEN))
    row = ledger.UsageRow(
        info.feature,
        info.model,
        info.effort,
        info.user_id,
        info.thread_id,
        tokens=t,
        cost_micro_usd=cost_micro_usd(info.model, t),
        request_id=seen.get("request_id"),
        latency_ms=int((time.monotonic() - seen["started"]) * 1000),
        error="CancelledError",
    )
    conn = connect(db_path)
    try:
        with write_transaction(conn):
            ledger.record(conn, row, _now())
    finally:
        conn.close()


async def _settle(
    task: asyncio.Task | None, db_path: Any, thread_id: str, prompt_tokens: int
) -> None:
    """After the browser left mid-turn: cancel the model call and wait until it has
    ended (it logs its usage), then close the thread. The database work runs off
    the event loop, so a held write lock never stalls other requests."""
    if task is not None:
        task.cancel()
        await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # it had ended already; read it so it is not reported
    try:
        await asyncio.to_thread(_finish, db_path, thread_id, prompt_tokens, True, None)
    except Exception as exc:  # never the message
        log.warning("ask cleanup failed", extra={"exc_class": type(exc).__name__})


# Tasks that run on after the request that started them, kept until they end.
_BACKGROUND: set[asyncio.Task] = set()


def background(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Run `coro` as a task that is not garbage collected before it ends."""
    task = asyncio.get_running_loop().create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


def when_settled(p: Prepared, callback: Callable[[], None]) -> None:
    """Run `callback` once nothing of the turn is left running: now, or when the
    cleanup after a cut-off turn has ended."""
    if p.settling is None or p.settling.done():
        callback()
    else:
        p.settling.add_done_callback(lambda _t: callback())


# --------------------------------------------------------------------------
# The turn
# --------------------------------------------------------------------------


def _system(context_text: str) -> list[dict]:
    return [
        {"type": "text", "text": ASK_RULES},
        {"type": "text", "text": context_text, "cache_control": {"type": "ephemeral"}},
    ]


def request_params(p: Prepared, system: list[dict], messages: list[dict]) -> dict:
    return {
        "model": p.model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": tools.definitions(),
        "messages": messages,
        "output_config": {"effort": p.effort},
        "cache_control": {"type": "ephemeral"},
    }


def _content(message: Any) -> list[dict]:
    """The response's content blocks exactly as returned, for storing and sending back."""
    return [block.to_dict() for block in message.content]


async def stream_turn(settings: Settings, p: Prepared) -> AsyncIterator[dict]:
    """Server-sent events for one turn: thread, status, text, replace, done or error."""
    db_path = settings.db_path
    yield {"type": "thread", "thread_id": p.thread_id, "new": p.new_thread, "notice": p.notice}
    sf = StreamFilter(p.private)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    sent = {"any": False}
    prompt_tokens = 0
    cost = 0
    closed = True
    notice: str | None = None
    finished = False
    ai_conn: AiConnection | None = None
    task: asyncio.Task | None = None
    getter: asyncio.Future | None = None
    try:
        sig = await asyncio.to_thread(ensure_facts, db_path, p.private)
        scrub = Scrub(p.private, await asyncio.to_thread(load_last4s, db_path))
        ai_conn = await asyncio.to_thread(open_ai_connection, db_path)
        history = p.messages[:-1]
        pin = _pinned(p.thread_id)
        if pin is None:  # a new thread (or one whose pin went since `prepare`)
            context_text = await asyncio.to_thread(household_context, ai_conn, sig, db_path)
            pin = _Pin(context_text, scrub, len(history))
        pin = _repinned(pin, scrub, history)
        _set_pin(p.thread_id, pin)
        system = _system(scrub.text(pin.context))
        messages = to_send(p.messages, scrub, pin.drop_thinking)
        estimate = p.estimate_micro

        for step in range(MAX_CALLS):
            params = request_params(p, system, messages)
            sf.separate()
            info = ai_client.CallInfo(
                "ask", p.model, p.effort, p.user_id, p.thread_id, estimate_micro=estimate
            )

            async def request(
                client: Any, params: dict = params, info: ai_client.CallInfo = info
            ) -> Any:
                seen: dict[str, Any] = {"usage": None, "stream": None, "started": time.monotonic()}
                try:
                    async with client.messages.stream(**params) as stream:
                        seen["stream"] = stream
                        seen["request_id"] = getattr(stream, "request_id", None)
                        async for event in stream:
                            if event.type == "message_start":
                                seen["usage"] = event.message.usage
                            elif event.type == "content_block_start":
                                block = event.content_block
                                if block.type == "tool_use":
                                    tool = tools.BY_NAME.get(block.name)
                                    label = tool.status if tool else "Working"
                                    await queue.put(
                                        {"type": "status", "tool": block.name, "label": label}
                                    )
                            elif event.type == "text":
                                out = sf.feed(event.text)
                                if out:
                                    sent["any"] = True
                                    await queue.put({"type": out[0], "text": out[1]})
                        return await stream.get_final_message()
                except asyncio.CancelledError:  # the browser left: log what is known
                    await asyncio.to_thread(_record_stopped, db_path, info, seen)
                    raise

            task = asyncio.create_task(
                ai_client.call(settings, info, request, can_retry=lambda: not sent["any"])
            )
            while not task.done() or not queue.empty():
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield getter.result()
                else:
                    getter.cancel()
                    # Not `await getter`: its CancelledError would swallow a cancel
                    # of this task (the browser left) that lands meanwhile.
                    await asyncio.wait({getter})
            final = task.result()  # raises AiError

            content = _content(final)
            await asyncio.to_thread(
                append_message, db_path, p.thread_id, "assistant", "assistant", content
            )
            messages.append(
                {"role": "assistant", "content": [_scrubbed(b, scrub) for b in content]}
            )
            t = tokens_of(final.usage)
            prompt_tokens = max(prompt_tokens, t.prompt)
            cost += cost_micro_usd(p.model, t)
            stop = final.stop_reason

            if stop == "tool_use":
                results = []
                for block in final.content:
                    if block.type != "tool_use":
                        continue
                    ran = await asyncio.to_thread(
                        tools.execute, ai_conn, block.name, block.input, scrub
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": ran.content,
                            "is_error": ran.is_error,
                        }
                    )
                await asyncio.to_thread(
                    append_message, db_path, p.thread_id, "user", "tool_results", results
                )
                # Sent the way every later turn sends them, so the bytes match.
                messages.append({"role": "user", "content": [_scrubbed(r, scrub) for r in results]})
                # The next call's prompt: this one, its reply and the tool results,
                # all priced as uncached for the cap check.
                grown = t.prompt + t.output + sum(len(r["content"]) for r in results) // 3
                estimate = max(
                    p.estimate_micro, estimate_micro_usd(p.model, grown, FIRST_OUTPUT_TOKENS)
                )
                if step == MAX_CALLS - 1:
                    notice = NOTICE["too_many_steps"]
                    break
                continue
            if stop == "pause_turn":
                continue
            if stop in ("end_turn", "stop_sequence"):
                closed = False
            elif stop in NOTICE:
                notice = NOTICE[stop]
            else:
                notice = NOTICE["stopped"]
            break

        tail = sf.finish()
        if tail:
            yield {"type": tail[0], "text": tail[1]}
        finished = True
        await asyncio.to_thread(_finish, db_path, p.thread_id, prompt_tokens, closed, notice)
        yield {
            "type": "done",
            "thread_id": p.thread_id,
            "text": sf.text,
            "notice": notice,
            "thread_closed": closed,
            "cost_micro_usd": cost if p.is_admin else None,
        }
    except ai_client.AiError as exc:
        finished = True
        await asyncio.to_thread(_finish, db_path, p.thread_id, prompt_tokens, True, exc.message)
        yield {"type": "error", "message": exc.message, "kind": exc.kind}
    except Exception as exc:  # never the message: it can hold data
        log.error("ask failed", extra={"exc_class": type(exc).__name__})
        finished = True
        await asyncio.to_thread(
            _finish, db_path, p.thread_id, prompt_tokens, True, NOTICE["stopped"]
        )
        yield {"type": "error", "message": ai_client.FAILED_TEXT, "kind": "failed"}
    finally:
        if getter is not None:
            getter.cancel()
        if not finished:  # the browser went away mid-turn. No await here: it is cancelled.
            p.settling = background(_settle(task, db_path, p.thread_id, prompt_tokens))
        if ai_conn is not None:
            ai_conn.close()


# --------------------------------------------------------------------------
# Reading threads back (filtered for display)
# --------------------------------------------------------------------------


def _texts(content: list[dict]) -> list[str]:
    return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]


def threads(conn: sqlite3.Connection, user: User, private: PrivateRules | None) -> list[dict]:
    rows = conn.execute(
        """SELECT thread_id, title, created_at, updated_at, turn_count, closed, model
           FROM ai_threads WHERE user_id = ? ORDER BY updated_at DESC LIMIT 30""",
        (user.id,),
    ).fetchall()
    return [
        {
            "thread_id": r["thread_id"],
            "title": filter_text(r["title"], private),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "turn_count": r["turn_count"],
            "closed": bool(r["closed"]),
            "model": r["model"],
        }
        for r in rows
    ]


def thread_detail(
    conn: sqlite3.Connection, user: User, thread_id: str, private: PrivateRules | None
) -> dict | None:
    t = conn.execute(
        "SELECT * FROM ai_threads WHERE thread_id = ? AND user_id = ?", (thread_id, user.id)
    ).fetchone()
    if t is None:
        return None
    turns: list[dict] = []
    for r in conn.execute(
        "SELECT kind, content_json FROM ai_messages WHERE thread_id = ? ORDER BY seq",
        (thread_id,),
    ):
        content = json.loads(r["content_json"])
        if r["kind"] == "question":
            question = PREFIX.sub("", " ".join(_texts(content)))
            turns.append(
                {
                    "question": filter_text(question, private),
                    "answer": "",
                    "tools": [],
                    "notice": None,
                }
            )
        elif not turns:
            continue
        elif r["kind"] == "assistant":
            turn = turns[-1]
            text = "\n\n".join(t for t in _texts(content) if t)
            if text:
                turn["answer"] = f"{turn['answer']}\n\n{text}" if turn["answer"] else text
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool = tools.BY_NAME.get(b.get("name", ""))
                    turn["tools"].append(tool.status if tool else "Working")
        elif r["kind"] == "notice":
            turns[-1]["notice"] = " ".join(_texts(content))
    for turn in turns:
        turn["answer"] = filter_text(turn["answer"], private)
    cost = None
    if user.is_admin:
        (cost,) = conn.execute(
            "SELECT COALESCE(SUM(cost_micro_usd), 0) FROM ai_usage WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    return {
        "thread_id": t["thread_id"],
        "title": filter_text(t["title"], private),
        "created_at": t["created_at"],
        "updated_at": t["updated_at"],
        "closed": bool(t["closed"]),
        "model": t["model"],
        "turns": turns,
        "cost_micro_usd": cost,
    }
