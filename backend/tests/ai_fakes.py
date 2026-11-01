"""A fake Anthropic client for the AI tests. No key, no network.

`FakeClient` answers `messages.stream`, `messages.create` and `messages.parse`
from a script of responses, and records every request it gets. Responses are
real SDK `Message` objects (built with `message(...)`), so storing them with
`to_dict()` and sending them back behaves as it would with the API.

Like the SDK, request ids ("req_fake_<n>", n counts requests) are on a
create/parse response and on an open stream, never on a stream's final
message. A `Broken` step is a stream that starts and then fails the way the
SDK fails: `stream_error()` for an error event, `dropped()` or `stalled()` for
the connection.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx2
from anthropic import APIStatusError, BadRequestError, InternalServerError, RateLimitError
from anthropic.types import Message


def usage(
    input_tokens: int = 1000,
    output_tokens: int = 200,
    write: int = 0,
    read: int = 0,
    geo: str | None = None,
    searches: int | None = None,
) -> dict:
    out: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": write,
        "cache_read_input_tokens": read,
    }
    if geo is not None:
        out["inference_geo"] = geo
    if searches is not None:
        out["server_tool_use"] = {"web_search_requests": searches, "web_fetch_requests": 0}
    return out


def message(
    content: list[dict],
    stop_reason: str = "end_turn",
    model: str = "claude-sonnet-5-5",
    **use: Any,
) -> Message:
    return Message.model_validate(
        {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage(**use),
        }
    )


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def thinking(t: str = "") -> dict:
    return {"type": "thinking", "thinking": t, "signature": "fake-signature"}


def server_tool_use(query: str, id: str = "srvtoolu_1") -> dict:
    """A web search Claude ran (server side)."""
    return {"type": "server_tool_use", "id": id, "name": "web_search", "input": {"query": query}}


def search_results(tool_use_id: str, results: list[tuple[str, str]]) -> dict:
    """A web search's results: (url, title) pairs."""
    return {
        "type": "web_search_tool_result",
        "tool_use_id": tool_use_id,
        "content": [
            {
                "type": "web_search_result",
                "url": url,
                "title": title,
                "encrypted_content": "fake-encrypted",
                "page_age": "May 1, 2026",
            }
            for url, title in results
        ],
    }


def search_error(tool_use_id: str, code: str = "max_uses_exceeded") -> dict:
    return {
        "type": "web_search_tool_result",
        "tool_use_id": tool_use_id,
        "content": {"type": "web_search_tool_result_error", "error_code": code},
    }


def cited(t: str, *sources: tuple[str, str]) -> dict:
    """Text that cites web search results: (url, title) pairs."""
    return {
        "type": "text",
        "text": t,
        "citations": [
            {
                "type": "web_search_result_location",
                "url": url,
                "title": title,
                "encrypted_index": "fake-index",
                "cited_text": t[:150],
            }
            for url, title in sources
        ],
    }


def tool_use(name: str, args: dict, id: str = "toolu_1") -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": args}


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _response(status: int, headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(status, headers=headers or {}, request=_request())


def bad_request(message: str) -> BadRequestError:
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return BadRequestError(message, response=_response(400), body=body)


def rate_limited(retry_after: str | None = None, code: str | None = None) -> RateLimitError:
    err: dict[str, Any] = {"type": "rate_limit_error", "message": "limited"}
    if code:
        err["details"] = {"error_code": code}
    headers = {"retry-after": retry_after} if retry_after else {}
    return RateLimitError(
        "limited", response=_response(429, headers), body={"type": "error", "error": err}
    )


def server_error() -> InternalServerError:
    body = {"type": "error", "error": {"type": "api_error", "message": "oops"}}
    return InternalServerError("oops", response=_response(500), body=body)


def billing_error() -> APIStatusError:
    """HTTP 402. The SDK has no class for it."""
    body = {"type": "error", "error": {"type": "billing_error", "message": "billing"}}
    return APIStatusError(f"{body}", response=_response(402), body=body)


def stream_error(kind: str = "overloaded_error") -> APIStatusError:
    """What the SDK raises for an `error` event inside a stream: a plain
    APIStatusError with the stream's status, 200, not OverloadedError."""
    body = {"type": "error", "error": {"type": kind, "message": "Overloaded"}}
    return APIStatusError(f"{body}", response=_response(200), body=body)


def dropped() -> httpx2.RemoteProtocolError:
    """The connection closed mid-stream. The SDK passes this on unwrapped."""
    return httpx2.RemoteProtocolError("peer closed connection", request=_request())


def stalled() -> httpx2.ReadTimeout:
    """Nothing arrived for the read timeout mid-stream. Also unwrapped."""
    return httpx2.ReadTimeout("timed out", request=_request())


@dataclass
class Broken:
    """A stream that starts and then fails with `error`. `partial` is what the
    SDK has gathered by then (message_start's usage and the content so far),
    or None when it fails before message_start."""

    error: Exception
    partial: Message | None = None


class _Stream:
    def __init__(self, step: Message | Broken, request_id: str) -> None:
        self.request_id = request_id
        broken = step if isinstance(step, Broken) else None
        self.msg = broken.partial if broken else step
        self.error = broken.error if broken else None

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self):
        return self._events()

    async def _events(self):
        for block in self.msg.content if self.msg is not None else []:
            yield SimpleNamespace(type="content_block_start", content_block=block)
            if block.type == "text":
                # Text arrives in small pieces, like the real stream.
                for i in range(0, len(block.text), 7):
                    yield SimpleNamespace(type="text", text=block.text[i : i + 7])
            elif block.type == "tool_use":
                yield SimpleNamespace(type="input_json", partial_json=json.dumps(block.input))
            elif block.type == "thinking" and block.thinking:
                yield SimpleNamespace(type="thinking", thinking=block.thinking)
        if self.error is not None:
            raise self.error

    @property
    def current_message_snapshot(self) -> Message:
        assert self.msg is not None  # the SDK asserts too, before message_start
        return self.msg

    async def get_final_message(self) -> Message:
        if self.error is not None:
            raise self.error
        return self.msg


Step = Message | Exception | Broken | Callable[[dict], Message]


class _Messages:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def _next(self, kind: str, params: dict) -> Any:
        self.client.requests.append((kind, copy.deepcopy(params)))
        if not self.client.script:
            raise AssertionError("The fake client ran out of scripted responses.")
        step = self.client.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, Broken):
            if kind != "stream":
                raise step.error
            return step
        if callable(step) and not isinstance(step, Message):
            return step(params)
        return step

    def _request_id(self) -> str:
        return f"req_fake_{len(self.client.requests)}"

    def _with_id(self, msg: Any) -> Any:
        """A create/parse response carries its request id, as in the SDK."""
        if not isinstance(msg, Message):
            return msg
        out = msg.model_copy()
        out._request_id = self._request_id()
        return out

    def stream(self, **params: Any) -> Any:
        client = self.client

        class _Ctx:
            async def __aenter__(self_inner) -> _Stream:
                step = client.messages._next("stream", params)
                return _Stream(step, client.messages._request_id())

            async def __aexit__(self_inner, *exc: object) -> None:
                return None

        return _Ctx()

    async def create(self, **params: Any) -> Message:
        return self._with_id(self._next("create", params))

    async def parse(self, **params: Any) -> Any:
        return self._with_id(self._next("parse", params))


class FakeClient:
    def __init__(self, script: list[Step] | None = None) -> None:
        self.script: list[Step] = list(script or [])
        self.requests: list[tuple[str, dict]] = []
        self.messages = _Messages(self)


def parsed(model_obj: Any, stop_reason: str = "end_turn", model: str = "claude-opus-5-5", **use):
    """A structured-output reply: a Message whose text is the object's JSON."""
    return message([text(model_obj.model_dump_json())], stop_reason, model, **use)
