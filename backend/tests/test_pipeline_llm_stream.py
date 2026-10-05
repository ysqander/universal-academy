"""llm.structured(stream=True) — the streamed request path used by the pipeline's long calls (mocked client)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app import llm
from app.config import settings


class Out(BaseModel):
    answer: str


class FakeStream:
    def __init__(self, events: list[SimpleNamespace], final: SimpleNamespace | None):
        self.events = events
        self.final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self.events:
            yield event

    async def get_final_response(self):
        if self.final is None:
            raise RuntimeError("Didn't receive a `response.completed` event.")
        return self.final


class FakeClient:
    def __init__(self, streams: list[FakeStream]):
        self.streams = streams
        self.requests: list[dict] = []
        self.responses = SimpleNamespace(stream=self._stream, parse=self._parse)

    def _stream(self, **request):
        self.requests.append(request)
        return self.streams.pop(0)

    async def _parse(self, **request):  # pragma: no cover - must not be used when stream=True
        raise AssertionError("non-streaming path used")


def completed(answer: str) -> SimpleNamespace:
    return SimpleNamespace(status="completed", output_parsed=Out(answer=answer), usage=None, incomplete_details=None)


@pytest.fixture
def live_mode(monkeypatch):
    object.__setattr__(settings, "fake_llm", False)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    yield
    object.__setattr__(settings, "fake_llm", True)


def run(client: FakeClient, monkeypatch) -> Out:
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    return asyncio.run(
        llm.structured(role="graph", instructions="i", text="t", schema=Out, max_output_tokens=1000, stream=True)
    )


def test_streamed_call_returns_the_parsed_final_response(live_mode, monkeypatch):
    client = FakeClient([FakeStream([SimpleNamespace(type="response.output_text.delta")], completed("5"))])
    assert run(client, monkeypatch).answer == "5"
    assert client.requests[0]["text_format"] is Out and client.requests[0]["store"] is False


def test_incomplete_stream_is_retried_with_a_larger_budget(live_mode, monkeypatch):
    incomplete = SimpleNamespace(
        status="incomplete", usage=None, incomplete_details=SimpleNamespace(reason="max_output_tokens")
    )
    client = FakeClient(
        [
            FakeStream([SimpleNamespace(type="response.incomplete", response=incomplete)], None),
            FakeStream([], completed("ok")),
        ]
    )
    assert run(client, monkeypatch).answer == "ok"
    assert [r["max_output_tokens"] for r in client.requests] == [1000, 1750]


def test_failed_stream_is_retried_then_reported(live_mode, monkeypatch):
    failed = SimpleNamespace(type="response.failed", response=SimpleNamespace(error="server_error"))
    client = FakeClient([FakeStream([failed], None), FakeStream([], completed("ok"))])
    assert run(client, monkeypatch).answer == "ok"

    client = FakeClient([FakeStream([failed], None) for _ in range(3)])
    with pytest.raises(llm.LLMError, match="server_error"):
        run(client, monkeypatch)
