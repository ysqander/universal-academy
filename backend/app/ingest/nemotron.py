"""NVIDIA Nemotron Parse client (hosted build.nvidia.com API): page image → layout blocks.

parse_layout(png) -> list[Block]; each Block has a class (Text, Picture, Caption, Formula, ...), a bbox
normalized to the input image ([x0, y0, x1, y1], 0-1, origin top-left) and its markdown text.

Two hosted protocols are supported (docs/research/nemotron-parse.md):
- nemotron-parse-2.0: control-token prompt, raw "<x_..><y_..>text<x_..><y_..><class_X>" string output whose
  coordinates refer to a white-padded 1664x2048 canvas (we fit the image to that canvas ourselves so the
  un-padding is exact); the API may answer 202 + NVCF-REQID, which is polled.
- nemotron-parse (v1): `tools=[markdown_bbox]`, JSON blocks in tool_calls with image-normalized bboxes.
The primary model (DEFAULT_MODEL unless UA_NEMOTRON_MODEL names another one) is tried first; if it fails the
other protocol is used as a fallback.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from PIL import Image

from ..config import settings

log = logging.getLogger("ua.nemotron")

MODEL_V2 = "nvidia/nemotron-parse-2.0"
MODEL_V1 = "nvidia/nemotron-parse"
# v1 won a live comparison on Axler chapter 1: it found the figures 2.0 missed (p.9 additive inverse), returns
# LaTeX in text blocks (2.0 mostly Unicode math) and was reliable where 2.0 returned 502s and inverted bboxes.
DEFAULT_MODEL = MODEL_V1
_CONFIG_DEFAULT_MODEL = "nvidia/nemotron-parse"  # app.config default for UA_NEMOTRON_MODEL

PROMPT_V2 = "</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"
CANVAS_W, CANVAS_H = 1664, 2048
MAX_TOKENS_V1 = 8192
MAX_TOKENS_V2 = 4000  # hosted 2.0 has max_model_len 4096 (prompt + output)
STATUS_URL = "https://integrate.api.nvidia.com/v1/status/{req_id}"

REQUEST_TIMEOUT = 180.0
MAX_ATTEMPTS = 5
MAX_IN_FLIGHT = 3
MIN_SPACING_S = 1.6  # free tier allows ~40 requests/minute

_BLOCK_RE = re.compile(
    r"<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)>(.*?)<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)><class_([^>]+)>",
    re.DOTALL,
)
_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


_disabled_logged = False


class NemotronError(RuntimeError):
    pass


@dataclass
class Block:
    type: str  # Text | Title | Section-header | List-item | Picture | Caption | Formula | Table | Page-header | ...
    bbox: list[float]  # [x0, y0, x1, y1] normalized to the input image
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def enabled() -> bool:
    return settings.has_nvidia and settings.parse_engine != "openai" and not settings.fake_llm


def primary_model() -> str:
    """settings.nemotron_model wins only when the user changed it from the config default."""
    configured = settings.nemotron_model
    return configured if configured and configured != _CONFIG_DEFAULT_MODEL else DEFAULT_MODEL


def canvas_zoom(width_pt: float, height_pt: float) -> float:
    """Render zoom (pixels per PDF point) at which a page exactly fits the 2.0 input canvas."""
    return min(CANVAS_W / width_pt, CANVAS_H / height_pt)


async def parse_layout(png: bytes) -> list[Block]:
    """Layout blocks of one page image, in the model's reading order.

    Returns [] when Nemotron is disabled (no NVIDIA key, UA_PARSE_ENGINE=openai or fake mode).
    Raises NemotronError when every model failed.
    """
    global _disabled_logged
    if not enabled():
        if not _disabled_logged:
            log.info(
                "Nemotron Parse disabled (NVIDIA key set: %s, UA_PARSE_ENGINE=%s, fake LLM: %s)",
                settings.has_nvidia, settings.parse_engine, settings.fake_llm,
            )
            _disabled_logged = True
        return []
    first = primary_model()
    models = [first] + [m for m in (MODEL_V2, MODEL_V1) if m != first]
    errors: list[str] = []
    for model in models:
        try:
            blocks = await _parse_with(model, png)
            log.info("nemotron %s: %d blocks", model, len(blocks))
            return blocks
        except NemotronError as e:
            log.warning("nemotron %s failed: %s", model, e)
            errors.append(f"{model}: {e}")
    raise NemotronError("; ".join(errors))


async def _parse_with(model: str, png: bytes) -> list[Block]:
    if _is_v1(model):
        payload = {
            "model": model,
            "tools": [{"type": "function", "function": {"name": "markdown_bbox"}}],
            "messages": [{"role": "user", "content": f'<img src="data:image/png;base64,{base64.b64encode(png).decode()}" />'}],
            "max_tokens": MAX_TOKENS_V1,
        }
        body = await _post(payload)
        return parse_v1_response(body)

    fitted, w, h = fit_to_canvas(png)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT_V2},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(fitted).decode()}},
                ],
            }
        ],
        "max_tokens": MAX_TOKENS_V2,
        "temperature": 0,
    }
    body = await _post(payload)
    choice = (body.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        log.warning("nemotron %s output truncated at max_tokens; trailing blocks may be missing", model)
    content = (choice.get("message") or {}).get("content") or ""
    return parse_v2_content(content, w, h)


def _is_v1(model: str) -> bool:
    return model == MODEL_V1


# ---------------------------------------------------------------------------
# Response parsing (pure functions, unit-tested offline)
# ---------------------------------------------------------------------------


def fit_to_canvas(png: bytes) -> tuple[bytes, int, int]:
    """Scale the image (aspect kept) so it exactly fits 1664x2048; return (png, width, height)."""
    with Image.open(io.BytesIO(png)) as im:
        rgb = im.convert("RGB")
    scale = min(CANVAS_W / rgb.width, CANVAS_H / rgb.height)
    w, h = min(CANVAS_W, round(rgb.width * scale)), min(CANVAS_H, round(rgb.height * scale))
    if (w, h) != rgb.size:
        rgb = rgb.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, "PNG", optimize=True)
    return buf.getvalue(), w, h


def _canvas_to_image(coords: tuple[float, float, float, float], w: int, h: int) -> list[float]:
    pad_l, pad_t = (CANVAS_W - w) // 2, (CANVAS_H - h) // 2
    x0, y0, x1, y1 = coords
    out = [
        (x0 * CANVAS_W - pad_l) / w,
        (y0 * CANVAS_H - pad_t) / h,
        (x1 * CANVAS_W - pad_l) / w,
        (y1 * CANVAS_H - pad_t) / h,
    ]
    return [round(min(1.0, max(0.0, v)), 5) for v in out]


def parse_v2_content(content: str, w: int, h: int) -> list[Block]:
    """Parse the 2.0 raw token string; (w, h) = size of the image as sent (fitted to the canvas)."""
    blocks: list[Block] = []
    for m in _BLOCK_RE.finditer(content):
        x0, y0, text, x1, y1, cls = m.groups()
        cls = "Formula" if cls == "Inline-formula" else cls
        text = text.replace("<tbc>", "").replace("\\<|unk|\\>", "").strip()
        blocks.append(Block(type=cls, bbox=_canvas_to_image((float(x0), float(y0), float(x1), float(y1)), w, h), text=text))
    return blocks


def parse_v1_response(body: dict[str, Any]) -> list[Block]:
    """Parse a v1 tools-mode response: tool_calls[0].function.arguments is a JSON list (maybe nested once)."""
    try:
        message = body["choices"][0]["message"]
        arguments = message["tool_calls"][0]["function"]["arguments"]
    except (KeyError, IndexError, TypeError) as e:
        raise NemotronError(f"unexpected v1 response shape: {str(body)[:300]}") from e
    data = json.loads(arguments) if isinstance(arguments, str) else arguments
    if data and isinstance(data[0], list):
        data = [item for group in data for item in group]
    blocks: list[Block] = []
    for item in data:
        box = item.get("bbox") or {}
        try:
            coords = [float(box[k]) for k in ("xmin", "ymin", "xmax", "ymax")]
        except (KeyError, TypeError, ValueError):
            continue
        blocks.append(
            Block(
                type=str(item.get("type") or "Text"),
                bbox=[round(min(1.0, max(0.0, v)), 5) for v in coords],
                text=str(item.get("text") or "").strip(),
            )
        )
    return blocks


# ---------------------------------------------------------------------------
# HTTP with rate limiting, 202 polling and retries
# ---------------------------------------------------------------------------


class _RateLimiter:
    """At most MAX_IN_FLIGHT concurrent requests and MIN_SPACING_S between request starts (per event loop)."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sem: asyncio.Semaphore | None = None
        self._lock: asyncio.Lock | None = None
        self._last_start = 0.0

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._sem = asyncio.Semaphore(MAX_IN_FLIGHT)
            self._lock = asyncio.Lock()

    async def __aenter__(self) -> None:
        self._bind()
        assert self._sem is not None and self._lock is not None
        await self._sem.acquire()
        async with self._lock:
            wait = self._last_start + MIN_SPACING_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

    async def __aexit__(self, *exc: object) -> None:
        assert self._sem is not None
        self._sem.release()


_limiter = _RateLimiter()


async def _post(payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {settings.nvidia_api_key}", "Accept": "application/json"}
    last = ""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with _limiter:
                    resp = await client.post(settings.nemotron_url, headers=headers, json=payload)
                resp = await _poll_if_pending(client, resp, headers)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                await asyncio.sleep(min(30.0, 2.0**attempt + 1))
                continue
            if resp.status_code == 200:
                return resp.json()
            last = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code not in _RETRYABLE:
                raise NemotronError(last)
            retry_after = _retry_after(resp)
            await asyncio.sleep(retry_after if retry_after is not None else min(60.0, 2.0 ** (attempt + 1)))
    raise NemotronError(f"gave up after {MAX_ATTEMPTS} attempts ({last})")


async def _poll_if_pending(client: httpx.AsyncClient, resp: httpx.Response, headers: dict[str, str]) -> httpx.Response:
    deadline = time.monotonic() + REQUEST_TIMEOUT
    while resp.status_code == 202:
        req_id = resp.headers.get("NVCF-REQID")
        if not req_id:
            raise NemotronError("202 Accepted without an NVCF-REQID header")
        if time.monotonic() > deadline:
            raise NemotronError(f"request {req_id} still pending after {REQUEST_TIMEOUT:.0f}s")
        await asyncio.sleep(1.0)
        resp = await client.get(STATUS_URL.format(req_id=req_id), headers=headers)
    return resp


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    try:
        return min(60.0, float(value)) if value else None
    except ValueError:
        return None
