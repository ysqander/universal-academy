"""Nemotron Parse client: response parsing, canvas fitting, HTTP retries/polling/fallback (mock transport)."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import httpx
import pytest
from PIL import Image

from app.ingest import nemotron as nm


def png_of(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "PNG")
    return buf.getvalue()


def v1_body(blocks: list[dict[str, Any]], nested: bool = True) -> dict[str, Any]:
    args = json.dumps([blocks] if nested else blocks)
    return {"choices": [{"message": {"tool_calls": [{"function": {"name": "markdown_bbox", "arguments": args}}]}}]}


V1_BLOCKS = [
    {"bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.5, "ymax": 0.3}, "text": "Hello", "type": "Text"},
    {"bbox": {"xmin": 0.6, "ymin": 0.1, "xmax": 0.9, "ymax": 0.4}, "text": "", "type": "Picture"},
]


def test_parse_v1_response_nested_and_flat() -> None:
    for nested in (True, False):
        blocks = nm.parse_v1_response(v1_body(V1_BLOCKS, nested))
        assert [(b.type, b.bbox, b.text) for b in blocks] == [
            ("Text", [0.1, 0.2, 0.5, 0.3], "Hello"),
            ("Picture", [0.6, 0.1, 0.9, 0.4], ""),
        ]


def test_parse_v1_response_bad_shape() -> None:
    with pytest.raises(nm.NemotronError):
        nm.parse_v1_response({"choices": [{"message": {"content": "no tools"}}]})


def test_parse_v2_content_unpads_canvas() -> None:
    # A 1207x2048 image is centered on the 1664x2048 canvas with 228 px of white on the left.
    w, h = 1207, 2048
    x0 = (228 + 0.25 * w) / 1664
    x1 = (228 + 0.75 * w) / 1664
    content = (
        f"<x_{x0:.4f}><y_0.1>Some **text**<x_{x1:.4f}><y_0.2><class_Text>\n\n"
        "<x_0.5><y_0.3><x_0.6><y_0.4><class_Picture>\n\n"
        "<x_0.2><y_0.5>\\(x^2\\)<tbc><x_0.4><y_0.55><class_Inline-formula>"
    )
    blocks = nm.parse_v2_content(content, w, h)
    assert [b.type for b in blocks] == ["Text", "Picture", "Formula"]
    assert blocks[0].bbox == pytest.approx([0.25, 0.1, 0.75, 0.2], abs=1e-3)
    assert blocks[0].text == "Some **text**"
    assert blocks[2].text == "\\(x^2\\)"


def test_fit_to_canvas_keeps_aspect() -> None:
    fitted, w, h = nm.fit_to_canvas(png_of(1042, 1767))
    assert h == 2048 and abs(w - round(1042 * 2048 / 1767)) <= 1
    with Image.open(io.BytesIO(fitted)) as im:
        assert im.size == (w, h)
    assert nm.canvas_zoom(375, 636) == pytest.approx(2048 / 636)


def test_disabled_without_key_or_in_fake_mode() -> None:
    assert not nm.enabled()  # conftest: fake LLM + empty NVIDIA key
    assert asyncio.run(nm.parse_layout(png_of(10, 10))) == []


def test_primary_model_default() -> None:
    assert nm.primary_model() == nm.DEFAULT_MODEL


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch):
    """Route nemotron's httpx client through a handler; no waiting between requests."""
    calls: list[httpx.Request] = []
    handler_box: dict[str, Any] = {}
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler_box["fn"](request)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, transport=httpx.MockTransport(respond), **kwargs)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(nm.httpx, "AsyncClient", factory)
    monkeypatch.setattr(nm.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(nm, "MIN_SPACING_S", 0.0)
    monkeypatch.setattr(nm, "enabled", lambda: True)

    def install(fn):
        handler_box["fn"] = fn
        return calls

    return install


def test_v1_retries_on_429_then_succeeds(mock_http) -> None:
    responses = iter([httpx.Response(429, json={"error": "slow down"}), httpx.Response(200, json=v1_body(V1_BLOCKS))])
    calls = mock_http(lambda r: next(responses))
    blocks = asyncio.run(nm._parse_with(nm.MODEL_V1, png_of(20, 30)))
    assert len(blocks) == 2 and len(calls) == 2
    body = json.loads(calls[0].content)
    assert body["model"] == nm.MODEL_V1 and body["tools"][0]["function"]["name"] == "markdown_bbox"
    assert body["messages"][0]["content"].startswith('<img src="data:image/png;base64,')


def test_v2_polls_202_until_done(mock_http) -> None:
    content = "<x_0.3><y_0.1>Hi<x_0.6><y_0.2><class_Text>"
    done = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, headers={"NVCF-REQID": "req-1"})
        assert request.url.path.endswith("/v1/status/req-1")
        return httpx.Response(200, json=done)

    calls = mock_http(handler)
    blocks = asyncio.run(nm._parse_with(nm.MODEL_V2, png_of(1664, 2048)))
    assert [(b.type, b.text) for b in blocks] == [("Text", "Hi")]
    assert [c.method for c in calls] == ["POST", "GET"]
    payload = json.loads(calls[0].content)
    assert payload["messages"][0]["content"][0]["text"] == nm.PROMPT_V2
    assert payload["max_tokens"] <= 4096


def test_parse_layout_falls_back_to_second_model(mock_http) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == nm.DEFAULT_MODEL:
            return httpx.Response(400, json={"error": "bad request"})
        if model == nm.MODEL_V2:
            return httpx.Response(200, json={"choices": [{"message": {"content": "<x_0.1><y_0.1>A<x_0.2><y_0.2><class_Title>"}}]})
        return httpx.Response(200, json=v1_body(V1_BLOCKS))

    calls = mock_http(handler)
    blocks = asyncio.run(nm.parse_layout(png_of(100, 150)))
    assert blocks and len(calls) == 2  # 400 is not retried; the other protocol answers


def test_parse_layout_raises_when_all_models_fail(mock_http) -> None:
    mock_http(lambda r: httpx.Response(422, json={"detail": "invalid"}))
    with pytest.raises(nm.NemotronError):
        asyncio.run(nm.parse_layout(png_of(100, 150)))
