"""Single entry point for OpenAI calls: structured outputs, images, concurrency, retries, usage/cost accounting.

Usage:
    result = await structured(role="graph", instructions=..., text=..., schema=KnowledgeGraphDraft)

Roles map to (model, reasoning effort) via settings. With UA_FAKE_LLM=1 the call is answered by
app.fake_llm.respond(schema, role, instructions, text) instead (offline tests / UI development).
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import Literal, TypeVar

import openai
from openai import AsyncOpenAI
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger("ua.llm")

Role = Literal["vision", "graph", "lesson", "viz", "verify"]
T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens: (input, cached input, output). Used only for the cost estimate shown in job logs.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-astra": (10.0, 1.0, 50.0),
    "gpt-6.1-sol": (2.0, 0.10, 10.0),
    "gpt-6-sol": (2.0, 0.20, 10.0),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-sol": (4.0, 0.40, 20.0),
    "gpt-5.6-terra": (2.0, 0.20, 12.0),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.5": (5.0, 0.50, 30.0),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
}


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    by_role: dict[str, float] = field(default_factory=dict)

    def add(self, role: str, model: str, inp: int, cached: int, out: int) -> float:
        p_in, p_cached, p_out = PRICES.get(model, (2.0, 0.2, 10.0))
        cost = ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1_000_000
        self.calls += 1
        self.input_tokens += inp
        self.cached_tokens += cached
        self.output_tokens += out
        self.cost_usd += cost
        self.by_role[role] = self.by_role.get(role, 0.0) + cost
        return cost


# The pipeline sets this per course so usage can be attributed and logged.
current_usage: contextvars.ContextVar[Usage | None] = contextvars.ContextVar("current_usage", default=None)

_client: AsyncOpenAI | None = None
_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


class LLMError(RuntimeError):
    pass


class _StreamFailed(RuntimeError):
    """A streamed response failed, reported an error event or ended early (retried like a server error)."""


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set — add it to the .env file in the project root.")
        _client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=5, timeout=900.0)
    return _client


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(max(1, settings.llm_concurrency))
        _semaphore_loop = loop
    return _semaphore


def role_model(role: Role) -> tuple[str, str]:
    return {
        "vision": (settings.model_vision, settings.effort_vision),
        "graph": (settings.model_graph, settings.effort_graph),
        "lesson": (settings.model_lesson, settings.effort_lesson),
        "viz": (settings.model_viz, settings.effort_viz),
        "verify": (settings.model_verify, settings.effort_verify),
    }[role]


def png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def structured(
    *,
    role: Role,
    instructions: str,
    text: str,
    schema: type[T],
    images: list[bytes] | None = None,
    shared_prefix: str | None = None,
    max_output_tokens: int = 32000,
    label: str = "",
    stream: bool = False,
) -> T:
    """Run one structured-output call and return the parsed Pydantic object.

    - `instructions`: system-level task description (developer message).
    - `shared_prefix`: large context reused across many calls (e.g. chapter markdown). It is placed in the
      developer message behind an explicit cache breakpoint so parallel calls read it from the prompt cache.
    - `text`: the call-specific user message; `images`: PNG bytes attached to the user message (detail=original).
    - `stream`: stream the response events instead of waiting for one silent HTTP response. Use it for calls that
      can take minutes (graph, lessons): some networks drop connections that stay idle that long, and the
      non-streaming request then hangs until the client timeout.
    Retries (3 attempts): the SDK retries transport/429/5xx itself; on top of that we retry server errors, failed or
    broken streams, truncation (with a larger output budget) and output that does not fit the schema. Every failure
    surfaces as LLMError. The usage of every billed attempt — also failed and invalid ones — is recorded.
    """
    if settings.fake_llm:
        from . import fake_llm

        return fake_llm.respond(schema=schema, role=role, instructions=instructions, text=text, shared_prefix=shared_prefix)

    model, effort = role_model(role)
    developer_content: list[dict] = [{"type": "input_text", "text": instructions}]
    if shared_prefix:
        developer_content.append(
            {"type": "input_text", "text": shared_prefix, "prompt_cache_breakpoint": {"mode": "explicit"}}
        )
    user_content: list[dict] = [{"type": "input_text", "text": text}]
    for png in images or []:
        user_content.append({"type": "input_image", "image_url": png_data_url(png), "detail": "original"})
    messages = [
        {"role": "developer", "content": developer_content},
        {"role": "user", "content": user_content},
    ]
    # The same JSON-schema format responses.parse() would send; we parse the output ourselves so that usage is
    # recorded before validation (an invalid answer is billed too).
    text_format = {"format": type_to_text_format_param(schema)}

    budget = max_output_tokens
    last_error: Exception | None = None
    for attempt in range(3):
        started = time.monotonic()
        request = dict(
            model=model,
            reasoning={"effort": effort},
            input=messages,
            text=text_format,
            max_output_tokens=budget,
            prompt_cache_options={"mode": "explicit"},
            store=False,
        )
        try:
            async with _get_semaphore():
                if stream:
                    resp = await _stream_response(request, role=role, model=model, label=label, started=started)
                else:
                    resp = await _get_client().responses.create(**request)
        except LLMError:
            raise  # configuration problems (no API key) — not retryable
        except openai.BadRequestError as e:  # not retryable: bad schema / param / image
            raise LLMError(f"OpenAI rejected the request ({model}): {e.message}") from e
        except openai.AuthenticationError as e:
            raise LLMError("OpenAI authentication failed — check OPENAI_API_KEY in .env.") from e
        except openai.RateLimitError as e:
            if getattr(e, "code", None) == "insufficient_quota":
                raise LLMError("OpenAI account is out of credits (insufficient_quota).") from e
            last_error = e
            await asyncio.sleep(10 * (attempt + 1))
            continue
        except (openai.APIConnectionError, openai.InternalServerError, _StreamFailed) as e:
            last_error = e
            await asyncio.sleep(5 * (attempt + 1))
            continue
        except openai.APIStatusError as e:
            if e.status_code in (408, 409) or e.status_code >= 500:
                last_error = e
                await asyncio.sleep(5 * (attempt + 1))
                continue
            # 403 / 404 (unknown model name) / 422 …: retrying cannot help
            raise LLMError(f"OpenAI rejected the request ({model}): HTTP {e.status_code} {e.message}") from e
        except openai.APIError as e:  # error payloads inside a stream (no HTTP status)
            last_error = e
            await asyncio.sleep(5 * (attempt + 1))
            continue
        except Exception as e:  # noqa: BLE001 — anything else (SDK protocol errors …) is retried, then LLMError
            log.warning("%s call %s: unexpected %s: %s", role, label, type(e).__name__, e)
            last_error = e
            await asyncio.sleep(5 * (attempt + 1))
            continue

        _record_usage(role, model, resp, label, time.monotonic() - started)
        if getattr(resp, "status", None) == "incomplete":
            reason = getattr(getattr(resp, "incomplete_details", None), "reason", None) or "unknown"
            last_error = LLMError(f"incomplete response ({reason})")
            if reason == "max_output_tokens":
                budget = min(int(budget * 1.75), 128000)
            continue
        output = _output_text(resp)
        if not output:
            refusal = _refusal(resp)
            last_error = LLMError(f"model refused: {refusal}" if refusal else "model returned no output")
            continue
        try:
            return schema.model_validate_json(output)
        except ValidationError as e:  # model produced JSON that does not fit the schema
            last_error = e
            continue

    raise LLMError(f"{role} call failed after retries ({label}): {last_error}")


async def _stream_response(request: dict, *, role: str, model: str, label: str, started: float):
    """A streamed responses.create(). Returns the final response of a 'response.completed' or 'response.incomplete'
    event (the caller handles truncation). Failed responses, 'error' events and streams that end without a final
    response raise _StreamFailed (retried like a server error); a failed response's usage is still recorded."""
    events = await _get_client().responses.create(**request, stream=True)
    async with events:
        async for event in events:
            kind = getattr(event, "type", "")
            if kind in ("response.completed", "response.incomplete"):
                return event.response
            if kind == "response.failed":
                response = getattr(event, "response", None)
                _record_usage(role, model, response, f"{label} (failed)", time.monotonic() - started)
                raise _StreamFailed(str(getattr(response, "error", None) or "response failed"))
            if kind == "error":
                code = getattr(event, "code", None) or "error"
                raise _StreamFailed(f"{code}: {getattr(event, 'message', '') or 'stream error'}")
    raise _StreamFailed("stream ended without a response.completed event")


def _output_text(resp) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text:
        return text
    chunks = []
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", "") == "output_text":
                chunks.append(getattr(content, "text", "") or "")
    return "".join(chunks)


def _refusal(resp) -> str:
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", "") == "refusal":
                return getattr(content, "refusal", "") or "refusal"
    return ""


def _record_usage(role: str, model: str, resp, label: str, seconds: float) -> None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    details = getattr(usage, "input_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    tracker = current_usage.get()
    cost = tracker.add(role, model, inp, cached, out) if tracker else 0.0
    log.info("llm %s %s %s in=%d cached=%d out=%d %.1fs $%.4f", role, model, label, inp, cached, out, seconds, cost)
