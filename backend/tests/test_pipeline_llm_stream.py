"""llm.structured — the request path used by every pipeline call (mocked OpenAI client): streaming, retries, error
normalization (every failure → LLMError) and usage accounting of failed attempts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from app import llm
from app.config import settings


class Out(BaseModel):
    answer: str


class FakeStream:
    """What responses.create(stream=True) returns: an async-iterable, async context manager of events."""

    def __init__(self, events: list[SimpleNamespace], *, raise_after: Exception | None = None):
        self.events = events
        self.raise_after = raise_after
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self.events:
            yield event
        if self.raise_after is not None:
            raise self.raise_after


class FakeClient:
    """Each call to responses.create pops the next scripted outcome: a FakeStream, a response or an exception."""

    def __init__(self, outcomes: list):
        self.outcomes = outcomes
        self.requests: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def usage(inp: int = 1000, out: int = 500) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=inp, output_tokens=out, input_tokens_details=SimpleNamespace(cached_tokens=0))


def response(text: str, *, status: str = "completed", reason: str | None = None, tokens: int = 500) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        output_text=text,
        output=[],
        usage=usage(out=tokens),
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
    )


def completed(answer: str) -> FakeStream:
    return FakeStream(
        [
            SimpleNamespace(type="response.output_text.delta"),
            SimpleNamespace(type="response.completed", response=response(json.dumps({"answer": answer}))),
        ]
    )


def http_error(cls: type[openai.APIStatusError], status: int, message: str = "nope") -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return cls(message, response=httpx.Response(status, request=request), body={"message": message})


@pytest.fixture
def live_mode(monkeypatch):
    object.__setattr__(settings, "fake_llm", False)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    yield
    object.__setattr__(settings, "fake_llm", True)


def run(client: FakeClient, monkeypatch, *, stream: bool = True, tracker: llm.Usage | None = None) -> Out:
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    async def call() -> Out:
        token = llm.current_usage.set(tracker)
        try:
            return await llm.structured(
                role="graph", instructions="i", text="t", schema=Out, max_output_tokens=1000, stream=stream
            )
        finally:
            llm.current_usage.reset(token)

    return asyncio.run(call())


def test_streamed_call_returns_the_parsed_final_response(live_mode, monkeypatch):
    stream = completed("5")
    client = FakeClient([stream])
    assert run(client, monkeypatch).answer == "5"
    request = client.requests[0]
    assert request["stream"] is True and request["store"] is False
    assert request["text"]["format"]["type"] == "json_schema" and request["text"]["format"]["name"] == "Out"
    assert stream.closed


def test_non_streamed_call_uses_the_same_request(live_mode, monkeypatch):
    client = FakeClient([response('{"answer": "7"}')])
    assert run(client, monkeypatch, stream=False).answer == "7"
    assert "stream" not in client.requests[0] and client.requests[0]["text"]["format"]["strict"] is True


def test_incomplete_stream_is_retried_with_a_larger_budget(live_mode, monkeypatch):
    incomplete = response('{"answ', status="incomplete", reason="max_output_tokens")
    client = FakeClient([FakeStream([SimpleNamespace(type="response.incomplete", response=incomplete)]), completed("ok")])
    assert run(client, monkeypatch).answer == "ok"
    assert [r["max_output_tokens"] for r in client.requests] == [1000, 1750]


def test_failed_stream_is_retried_then_reported(live_mode, monkeypatch):
    def failed():
        return FakeStream([SimpleNamespace(type="response.failed", response=SimpleNamespace(error="server_error", usage=usage()))])

    client = FakeClient([failed(), completed("ok")])
    assert run(client, monkeypatch).answer == "ok"

    client = FakeClient([failed() for _ in range(3)])
    with pytest.raises(llm.LLMError, match="server_error"):
        run(client, monkeypatch)


def test_error_event_is_retried_and_reported_as_llm_error(live_mode, monkeypatch):
    def error_event():
        return FakeStream([SimpleNamespace(type="error", code="server_error", message="The server had an error")])

    client = FakeClient([error_event(), completed("ok")])
    assert run(client, monkeypatch).answer == "ok"
    assert len(client.requests) == 2

    client = FakeClient([error_event() for _ in range(3)])
    with pytest.raises(llm.LLMError, match="server_error"):
        run(client, monkeypatch)


def test_stream_without_a_final_response_is_retried(live_mode, monkeypatch):
    client = FakeClient([FakeStream([SimpleNamespace(type="response.output_text.delta")]), completed("ok")])
    assert run(client, monkeypatch).answer == "ok"
    client = FakeClient([FakeStream([]) for _ in range(3)])
    with pytest.raises(llm.LLMError, match="without a response.completed"):
        run(client, monkeypatch)


def test_sdk_api_error_inside_the_stream_is_retried(live_mode, monkeypatch):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    broken = FakeStream([], raise_after=openai.APIError("An error occurred during streaming", request, body=None))
    client = FakeClient([broken, completed("ok")])
    assert run(client, monkeypatch).answer == "ok"


def test_unexpected_exceptions_become_llm_errors_after_retries(live_mode, monkeypatch):
    client = FakeClient([RuntimeError("protocol confusion") for _ in range(3)])
    with pytest.raises(llm.LLMError, match="protocol confusion"):
        run(client, monkeypatch)
    assert len(client.requests) == 3


@pytest.mark.parametrize(
    ("cls", "status"), [(openai.PermissionDeniedError, 403), (openai.NotFoundError, 404), (openai.UnprocessableEntityError, 422)]
)
def test_client_errors_are_not_retried(live_mode, monkeypatch, cls, status):
    client = FakeClient([http_error(cls, status, "model gpt-x does not exist")])
    with pytest.raises(llm.LLMError, match=f"HTTP {status}"):
        run(client, monkeypatch, stream=False)
    assert len(client.requests) == 1


def test_server_status_errors_are_retried(live_mode, monkeypatch):
    client = FakeClient([http_error(openai.APIStatusError, 503), response('{"answer": "x"}')])
    assert run(client, monkeypatch, stream=False).answer == "x"


def test_usage_of_failed_and_invalid_attempts_is_counted(live_mode, monkeypatch):
    invalid = FakeStream([SimpleNamespace(type="response.completed", response=response('{"wrong": 1}', tokens=300))])
    failed = FakeStream(
        [SimpleNamespace(type="response.failed", response=SimpleNamespace(error="server_error", usage=usage(out=200)))]
    )
    tracker = llm.Usage()
    client = FakeClient([invalid, failed, completed("ok")])
    assert run(client, monkeypatch, tracker=tracker).answer == "ok"
    assert tracker.calls == 3
    assert tracker.output_tokens == 300 + 200 + 500 and tracker.cost_usd > 0


def test_refusal_is_retried_then_reported(live_mode, monkeypatch):
    refusal = SimpleNamespace(
        status="completed",
        output_text="",
        output=[SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="I can't help with that")])],
        usage=usage(),
        incomplete_details=None,
    )
    client = FakeClient([refusal, refusal, refusal])
    with pytest.raises(llm.LLMError, match="refused"):
        run(client, monkeypatch, stream=False)
