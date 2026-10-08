"""Anthropic (Claude) provider behind llm.structured (2026-10-08): the Messages API with native JSON-schema output
(output_config.format), reasoning steered by output_config.effort (adaptive thinking — Opus 5.5 cannot turn it off),
the instructions and the shared prefix cached as system blocks, streaming, retries, and usage/cost accounting.

The schema is the one the OpenAI path sends (every field required, additionalProperties false), adapted to
Anthropic's limits: at most 16 union-typed fields per request, so a nullable string, number or list is sent as a plain
field with a "none" sentinel ("", -1, []) and turned back into null before validation (anthropic_schema /
restore_nulls). The client only ever uses the app's own key and base URL — never ANTHROPIC_BASE_URL or a profile from
the environment (Claude Code exports ANTHROPIC_BASE_URL to the shells it starts).
"""

from __future__ import annotations

import asyncio
import base64
import copy
import functools
import json
import logging
import os
import time
from typing import Any
from unittest import mock

import anthropic
from anthropic import AsyncAnthropic
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger("ua.llm")

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MAX_BUDGET = 128_000  # synchronous output limit of the 5.5 models (thinking included)
MAX_UNIONS = 16  # Anthropic: parameters with union types (anyOf) per request

# USD per 1M tokens: (input, 5-minute cache write, 1-hour cache write, cache read, output). Haiku 5.5 charges 5x on
# every rate once a prompt exceeds 100K tokens (LONG_PROMPT).
PRICES: dict[str, tuple[float, float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 5.0, 8.0, 0.20, 20.0),
    "claude-sonnet-5-5": (2.0, 2.50, 4.0, 0.10, 10.0),
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),
}
LONG_PROMPT: dict[str, tuple[int, float]] = {"claude-haiku-5-5": (100_000, 5.0)}
# error types worth another attempt, also when they arrive inside a stream that began with HTTP 200
RETRYABLE_TYPES = {"overloaded_error", "api_error", "timeout_error", "rate_limit_error"}
_SENTINEL = {"string": "", "integer": -1, "number": -1, "array": []}

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        from .llm import LLMError

        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set — add it to the .env file in the project root.")
        # Everything explicit, and the SDK built where it sees no ANTHROPIC_* variable at all: with a key it skips the
        # credential chain, but it would still merge ANTHROPIC_CUSTOM_HEADERS into every request (headers that can
        # carry another credential), and Claude Code exports ANTHROPIC_* settings to the shells it starts.
        headers = {"anthropic-workspace-id": settings.anthropic_workspace_id} if settings.anthropic_workspace_id else None
        with mock.patch.dict(os.environ):
            for name in [k for k in os.environ if k.startswith("ANTHROPIC_")]:
                del os.environ[name]
            _client = AsyncAnthropic(
                api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url, max_retries=5, timeout=900.0,
                default_headers=headers,
            )
    return _client


def raise_effort(effort: str, notches: int) -> str:
    """The effort `notches` levels higher on Anthropic's scale (low … max). OpenAI's 'minimal' is read as 'low'."""
    effort = "low" if effort == "minimal" else effort
    if notches <= 0 or effort not in EFFORT_LEVELS:
        return effort
    return EFFORT_LEVELS[min(len(EFFORT_LEVELS) - 1, EFFORT_LEVELS.index(effort) + notches)]


def cost_usd(model: str, uncached: int, cache_write: int, cache_read: int, out: int, *, ttl: str = "1h") -> float:
    p_in, p_w5, p_w1h, p_read, p_out = PRICES.get(model, PRICES["claude-opus-5-5"])
    threshold, factor = LONG_PROMPT.get(model, (0, 1.0))
    scale = factor if threshold and uncached + cache_write + cache_read > threshold else 1.0
    p_write = p_w1h if ttl == "1h" else p_w5
    return scale * (uncached * p_in + cache_write * p_write + cache_read * p_read + out * p_out) / 1_000_000


# ---------------------------------------------------------------------------
# Schema: the OpenAI strict schema, adapted to Anthropic's structured-output limits
# ---------------------------------------------------------------------------


def _resolve(node: dict, root: dict) -> dict:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return root["$defs"][ref.split("/")[-1]]
    return node


def _nullable_branch(node: dict) -> dict | None:
    """The non-null branch of a value-or-null union, else None."""
    options = node.get("anyOf")
    if not isinstance(options, list) or len(options) != 2:
        return None
    rest = [o for o in options if o.get("type") != "null"]
    return rest[0] if len(rest) == 1 and len(options) == 2 and any(o.get("type") == "null" for o in options) else None


def _sentinel_type(branch: dict, root: dict) -> str | None:
    kind = _resolve(branch, root).get("type")
    return kind if kind in _SENTINEL else None


def _walk(node: Any, root: dict, fn) -> Any:
    """Apply fn to every schema node (dict), children first; property NAMES are never touched."""
    if not isinstance(node, dict):
        return node
    out = dict(node)
    for key in ("properties", "$defs"):
        if isinstance(out.get(key), dict):
            out[key] = {name: _walk(child, root, fn) for name, child in out[key].items()}
    for key in ("items", "additionalProperties"):
        if isinstance(out.get(key), dict):
            out[key] = _walk(out[key], root, fn)
    for key in ("anyOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [_walk(child, root, fn) for child in out[key]]
    return fn(out)


def _used_defs(schema: dict) -> set[str]:
    used: set[str] = set()
    todo = [schema]
    while todo:
        node = todo.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.split("/")[-1]
                if name not in used:
                    used.add(name)
                    todo.append(schema["$defs"][name])
            todo += [v for k, v in node.items() if k != "$defs"]
        elif isinstance(node, list):
            todo += node
    return used


def count_unions(schema: dict) -> int:
    count = 0
    todo: list[Any] = [schema]
    while todo:
        node = todo.pop()
        if isinstance(node, dict):
            if "anyOf" in node or isinstance(node.get("type"), list):
                count += 1
            todo += list(node.values())
        elif isinstance(node, list):
            todo += node
    return count


@functools.lru_cache(maxsize=None)
def strict_schema(schema: type[BaseModel]) -> dict:
    return to_strict_json_schema(schema)


@functools.lru_cache(maxsize=None)
def _anthropic_schema_json(schema: type[BaseModel]) -> str:
    root = copy.deepcopy(strict_schema(schema))

    def adapt(node: dict) -> dict:
        node.pop("title", None)
        branch = _nullable_branch(node)
        if branch is not None and _sentinel_type(branch, root) is not None:
            kind = _sentinel_type(branch, root)
            plain = dict(_resolve(branch, root))
            plain.pop("title", None)
            note = {"string": '"" when there is none', "integer": "-1 when there is none",
                    "number": "-1 when there is none", "array": "[] when there is none"}[kind]
            description = node.get("description") or plain.get("description") or ""
            plain["description"] = f"{description} ({note})".strip()
            return plain
        return node

    adapted = _walk(root, root, adapt)
    if "$defs" in adapted:
        used = _used_defs(adapted)
        adapted["$defs"] = {name: d for name, d in adapted["$defs"].items() if name in used}
        if not adapted["$defs"]:
            adapted.pop("$defs")
    unions = count_unions(adapted)
    if unions > MAX_UNIONS:
        raise ValueError(f"{schema.__name__}: {unions} union-typed fields after adaptation; Anthropic accepts {MAX_UNIONS}")
    return json.dumps(adapted)


def anthropic_schema(schema: type[BaseModel]) -> dict:
    """The JSON schema sent in output_config.format for this model (cached per model class)."""
    return json.loads(_anthropic_schema_json(schema))


def restore_nulls(data: Any, schema: type[BaseModel]) -> Any:
    """Undo the sentinels of anthropic_schema (walking the original strict schema next to the data) and match string
    enums case-insensitively (Anthropic may change the casing of an enum value)."""
    root = strict_schema(schema)

    def visit(value: Any, node: dict) -> Any:
        node = _resolve(node, root)
        branch = _nullable_branch(node)
        if branch is not None:
            kind = _sentinel_type(branch, root)
            if kind is not None and value == _SENTINEL[kind]:
                return None
            return None if value is None else visit(value, branch)
        enum = node.get("enum")
        if isinstance(enum, list) and isinstance(value, str) and value not in enum:
            match = [e for e in enum if isinstance(e, str) and e.lower() == value.lower()]
            return match[0] if len(match) == 1 else value
        if isinstance(value, dict) and isinstance(node.get("properties"), dict):
            return {k: visit(v, node["properties"][k]) if k in node["properties"] else v for k, v in value.items()}
        if isinstance(value, list) and isinstance(node.get("items"), dict):
            return [visit(v, node["items"]) for v in value]
        return value

    return visit(data, root)


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


def _image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()}}


def build_request(
    *, model: str, effort: str, instructions: str, text: str, schema: type[BaseModel], images: list[bytes] | None,
    shared_prefix: str | None, budget: int,
) -> dict:
    system: list[dict] = [{"type": "text", "text": instructions}]
    if shared_prefix:
        system.append({"type": "text", "text": shared_prefix})
    # one breakpoint after the last system block: instructions (+ chapter) are reused across a lesson's calls and
    # across parallel lessons; a 1-hour entry survives the minutes between a draft, its review and its revision
    cache: dict[str, str] = {"type": "ephemeral"}
    if settings.anthropic_cache_ttl == "1h":
        cache["ttl"] = "1h"
    system[-1]["cache_control"] = cache
    content: list[dict] = [_image_block(png) for png in images or []]  # images before the text (Anthropic's advice)
    content.append({"type": "text", "text": text})
    return {
        "model": model,
        "max_tokens": budget,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "thinking": {"type": "adaptive"},
        "output_config": {"format": {"type": "json_schema", "schema": anthropic_schema(schema)}, "effort": effort},
    }


async def structured(
    *, role: str, model: str, effort: str, instructions: str, text: str, schema: type[BaseModel],
    images: list[bytes] | None, shared_prefix: str | None, max_output_tokens: int, label: str,
) -> BaseModel:
    """llm.structured for Anthropic. Always streamed (the SDK requires it for large max_tokens, and long silent
    requests get dropped). Retries (3 attempts) like the OpenAI path: overload/server errors, broken streams,
    truncation (larger budget), refusals and output that does not fit the schema; usage of every billed attempt is
    recorded."""
    from .llm import LLMError, _get_semaphore

    budget = min(max_output_tokens, MAX_BUDGET)
    last_error: Exception | None = None
    for attempt in range(3):
        started = time.monotonic()
        try:
            request = build_request(
                model=model, effort=effort, instructions=instructions, text=text, schema=schema, images=images,
                shared_prefix=shared_prefix, budget=budget,
            )
        except ValueError as e:  # a schema Anthropic cannot take: a programming error, not retryable
            raise LLMError(str(e)) from e
        try:
            async with _get_semaphore():
                async with _get_client().messages.stream(**request) as stream:
                    message = await stream.get_final_message()
        except LLMError:
            raise
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic authentication failed — check ANTHROPIC_API_KEY in .env.") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"Anthropic refused access ({model}): {e.message}") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"Anthropic does not know the model {model!r}: {e.message}") from e
        except anthropic.BadRequestError as e:  # bad schema / parameter / image: retrying cannot help
            if "workspace" in str(e.message).lower() and not settings.anthropic_workspace_id:
                raise LLMError(
                    "The Anthropic key is not tied to a workspace: add ANTHROPIC_WORKSPACE_ID=<workspace id> to .env "
                    f"(Claude Console → Settings → Workspaces), or use a key created inside a workspace. ({e.message})"
                ) from e
            raise LLMError(f"Anthropic rejected the request ({model}): {e.message}") from e
        except anthropic.RateLimitError as e:  # the SDK already honoured retry-after; a spend cap does not clear
            last_error = e
            await asyncio.sleep(10 * (attempt + 1))
            continue
        except (anthropic.APIConnectionError, anthropic.InternalServerError) as e:  # incl. 529 overloaded
            last_error = e
            await asyncio.sleep(5 * (attempt + 1))
            continue
        except anthropic.APIStatusError as e:
            # an error event inside a stream arrives as status 200 (the stream's own response) with its type in the body
            kind = _error_type(e)
            if e.status_code < 400 or e.status_code in (408, 409) or e.status_code >= 500 or kind in RETRYABLE_TYPES:
                last_error = e
                await asyncio.sleep((10 if kind == "rate_limit_error" else 5) * (attempt + 1))
                continue
            raise LLMError(f"Anthropic rejected the request ({model}): HTTP {e.status_code} {e.message}") from e
        except Exception as e:  # noqa: BLE001 — stream protocol errors, connections dropped mid-stream …: retried
            log.warning("%s call %s: unexpected %s: %s", role, label, type(e).__name__, e)
            last_error = e
            await asyncio.sleep(5 * (attempt + 1))
            continue

        _record_usage(role, model, message, label, time.monotonic() - started)
        stop = getattr(message, "stop_reason", None)
        if stop == "max_tokens":
            last_error = LLMError("incomplete response (max_tokens)")
            budget = min(int(budget * 1.75), MAX_BUDGET)
            continue
        if stop == "refusal":
            details = getattr(message, "stop_details", None)
            last_error = LLMError(f"model refused: {details or 'refusal'}")
            continue
        output = "".join(
            getattr(block, "text", "") or "" for block in getattr(message, "content", None) or [] if getattr(block, "type", "") == "text"
        )
        if not output.strip():
            last_error = LLMError(f"model returned no output (stop_reason {stop})")
            continue
        try:
            return schema.model_validate(restore_nulls(json.loads(output), schema))
        except (ValueError, ValidationError) as e:  # not JSON, or JSON that does not fit the schema
            last_error = e
            continue

    raise LLMError(f"{role} call failed after retries ({label}): {last_error}")


def _error_type(e: Exception) -> str | None:
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return error["type"]
        if isinstance(body.get("type"), str) and body["type"] != "error":
            return body["type"]
    return getattr(e, "type", None)


def _record_usage(role: str, model: str, message, label: str, seconds: float) -> None:
    from .llm import current_usage

    usage = getattr(message, "usage", None)
    if usage is None:
        return
    uncached = getattr(usage, "input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cost = cost_usd(model, uncached, write, read, out, ttl=settings.anthropic_cache_ttl)
    tracker = current_usage.get()
    if tracker:
        tracker.record(role, uncached + write + read, read, out, cost)
    log.info(
        "llm %s %s %s in=%d cached=%d written=%d out=%d %.1fs $%.4f",
        role, model, label, uncached + write + read, read, write, out, seconds, cost if tracker else 0.0,
    )
