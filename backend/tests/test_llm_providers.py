"""Switching between OpenAI and Anthropic (2026-10-08): provider per role, one prompt set per provider with the
learner's rules shared, the Anthropic call (request shape, schema adaptation, retries, cost) against a stub client, and
the guards that keep tests and fake runs away from both paid APIs."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import httpx2
import pytest
from pydantic import BaseModel

from app import llm, llm_anthropic, prompts
from app import llm_schemas as S
from app.config import PROVIDERS, ROLES, Settings, settings
from app.fake_proof import proof_lesson


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def make_settings(monkeypatch, **env: str) -> Settings:
    for key in [k for k in os.environ if k.startswith("UA_PROVIDER_") or k == "UA_LLM_PROVIDER"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_one_setting_switches_every_role_and_a_role_can_be_overridden(monkeypatch):
    openai = make_settings(monkeypatch)
    assert [openai.role_config(r)[0] for r in ROLES] == ["openai"] * 5
    assert openai.role_config("lesson")[1] == openai.model_lesson
    claude = make_settings(monkeypatch, UA_LLM_PROVIDER="anthropic", UA_PROVIDER_VERIFY="openai")
    assert claude.role_config("graph")[:2] == ("anthropic", "claude-opus-5-5")
    assert claude.role_config("lesson")[:2] == ("anthropic", "claude-opus-5-5")
    assert claude.role_config("vision")[:2] == ("anthropic", "claude-haiku-5-5")
    assert claude.role_config("verify")[0] == "openai"  # GPT verifies what Claude wrote
    assert claude.providers_in_use == ["anthropic", "openai"]
    with pytest.raises(ValueError, match="gemini"):
        make_settings(monkeypatch, UA_LLM_PROVIDER="gemini")


def test_tests_never_hold_a_key_or_the_ambient_anthropic_base_url():
    assert os.environ.get("ANTHROPIC_API_KEY", "") == "" and "ANTHROPIC_BASE_URL" not in os.environ
    assert settings.anthropic_api_key == "" and settings.openai_api_key == ""
    assert settings.anthropic_base_url == "https://api.anthropic.com"


def test_the_anthropic_client_uses_only_the_apps_key_and_base_url(monkeypatch):
    monkeypatch.setattr(llm_anthropic, "_client", None)
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm_anthropic._get_client()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")  # what Claude Code exports
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "not-the-apps-token")
    object.__setattr__(settings, "anthropic_api_key", "sk-ant-test")
    try:
        client = llm_anthropic._get_client()
        assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"
        assert client.api_key == "sk-ant-test" and client.auth_token is None
        assert "anthropic-workspace-id" not in client.default_headers
        monkeypatch.setattr(llm_anthropic, "_client", None)
        object.__setattr__(settings, "anthropic_workspace_id", "wrkspc_test")
        assert llm_anthropic._get_client().default_headers["anthropic-workspace-id"] == "wrkspc_test"
    finally:
        object.__setattr__(settings, "anthropic_api_key", "")
        object.__setattr__(settings, "anthropic_workspace_id", "")
        monkeypatch.setattr(llm_anthropic, "_client", None)


def test_fake_runs_never_reach_a_client_whatever_the_provider(monkeypatch):
    object.__setattr__(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(llm_anthropic, "_get_client", lambda: pytest.fail("a fake run reached the Anthropic client"))
    monkeypatch.setattr(llm, "_get_client", lambda: pytest.fail("a fake run reached the OpenAI client"))
    try:
        answer = asyncio.run(llm.structured(role="verify", instructions="x", text="y", schema=S.IndependentAnswer))
        assert isinstance(answer, S.IndependentAnswer)
    finally:
        object.__setattr__(settings, "llm_provider", "openai")


def test_efforts_follow_each_providers_scale():
    assert [llm.raise_effort(e, 1, "anthropic") for e in ("low", "medium", "high", "xhigh", "max")] == [
        "medium", "high", "xhigh", "max", "max"]
    assert llm.raise_effort("minimal", 0, "anthropic") == "low"
    assert llm.raise_effort("high", 1, "openai") == "xhigh" and llm.raise_effort("xhigh", 1, "openai") == "xhigh"


# ---------------------------------------------------------------------------
# Prompt sets: the learner's rules shared, each provider's wording its own
# ---------------------------------------------------------------------------


def public_texts(module) -> dict[str, str]:
    return {k: v for k, v in vars(module).items() if k.isupper() and isinstance(v, str)}


def test_both_prompt_sets_offer_every_text_the_app_asks_for():
    openai, anthropic = (public_texts(prompts.for_provider(p)) for p in PROVIDERS)
    assert set(openai) == set(anthropic)
    for name in ("GRAPH_INSTRUCTIONS", "LESSON_INSTRUCTIONS", "TOPUP_INSTRUCTIONS", "VIZ_INSTRUCTIONS", "VERIFY_INSTRUCTIONS",
                 "VERIFY_STATEMENTS_RECHECK", "VERIFY_STEP_INSTRUCTIONS", "VERIFY_STEP_RECHECK", "VERIFY_STEP_STATEMENTS_RECHECK",
                 "PLANNER_INSTRUCTIONS", "WRITER_INSTRUCTIONS", "REVIEWER_INSTRUCTIONS", "PROOF_PLANNER_INSTRUCTIONS",
                 "PROOF_WRITER_INSTRUCTIONS", "PROOF_REVIEWER_INSTRUCTIONS", "QUESTION_REPAIR_NOTE", "CAPSTONE_REPAIR_NOTE",
                 "PROOF_QUESTION_REPAIR_NOTE", "PROOF_CAPSTONE_REPAIR_NOTE", "REVISION_NOTE", "TRANSCRIBE_INSTRUCTIONS",
                 "DETECT_INSTRUCTIONS"):
        assert openai[name] and anthropic[name], name


@pytest.mark.parametrize("provider", PROVIDERS)
def test_each_set_keeps_the_headings_the_request_builders_point_to(provider):
    texts = prompts.for_provider(provider)
    assert "THE PROOF TRACK" in texts.PROOF_WRITER_INSTRUCTIONS and "THE PROOF PLAN" in texts.PROOF_PLANNER_INSTRUCTIONS
    assert "PROOF PASS" in texts.PROOF_REVIEWER_INSTRUCTIONS and "PROOF SKILLS" in texts.WRITER_INSTRUCTIONS
    assert texts.REVISION_NOTE.startswith("REVISION:")  # the fake LLM and the revision request read this header
    assert texts.CAPSTONE_RULE in texts.WRITER_INSTRUCTIONS and texts.OUTCOME_RULE in texts.PLANNER_INSTRUCTIONS


def test_the_learners_rules_are_one_text_shared_by_both_sets():
    from app.prompts import rules

    openai, anthropic = prompts.for_provider("openai"), prompts.for_provider("anthropic")
    for name, text in public_texts(rules).items():
        assert getattr(openai, name) is text and getattr(anthropic, name) is text, name
    assert openai.PROOF_PATTERN_NAMES is anthropic.PROOF_PATTERN_NAMES is rules.PROOF_PATTERN_NAMES


def test_calls_take_the_prompt_set_of_the_provider_that_runs_their_role():
    object.__setattr__(settings, "provider_lesson", "anthropic")
    try:
        assert prompts.for_role("lesson") is prompts.for_provider("anthropic")
        assert prompts.for_role("graph") is prompts.for_provider("openai")
    finally:
        object.__setattr__(settings, "provider_lesson", "")


# ---------------------------------------------------------------------------
# The Anthropic call
# ---------------------------------------------------------------------------

ALL_SCHEMAS = [S.PageTranscription, S.KnowledgeGraphDraft, S.LessonDraft, S.VisualizationPlan, S.IndependentAnswer,
               S.TeachingPlan, S.LessonV2Draft, S.LessonReview, S.QuestionRepair, S.CapstoneRepair]


@pytest.mark.parametrize("schema", ALL_SCHEMAS, ids=lambda s: s.__name__)
def test_every_schema_fits_anthropics_structured_output_limits(schema):
    adapted = llm_anthropic.anthropic_schema(schema)
    assert llm_anthropic.count_unions(adapted) <= llm_anthropic.MAX_UNIONS
    for node in _nodes(adapted):
        assert not isinstance(node.get("title"), str)  # pydantic's titles are dropped (a property may be called title)
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False and set(node.get("required", [])) == set(node.get("properties", {}))


def _nodes(schema):
    todo = [schema]
    while todo:
        node = todo.pop()
        if isinstance(node, dict):
            yield node
            todo += list(node.values())
        elif isinstance(node, list):
            todo += node


def _with_sentinels(data, schema: type[BaseModel]):
    """What Claude returns for the adapted schema: null scalars and lists as "", -1, []."""
    root = llm_anthropic.strict_schema(schema)

    def visit(value, node):
        node = llm_anthropic._resolve(node, root)
        branch = llm_anthropic._nullable_branch(node)
        if branch is not None:
            kind = llm_anthropic._sentinel_type(branch, root)
            if value is None:
                return llm_anthropic._SENTINEL[kind] if kind else None
            return visit(value, branch)
        if isinstance(value, dict) and "properties" in node:
            return {k: visit(v, node["properties"][k]) for k, v in value.items()}
        if isinstance(value, list) and isinstance(node.get("items"), dict):
            return [visit(v, node["items"]) for v in value]
        return value

    return visit(data, root)


def test_sentinels_come_back_as_nulls_and_enum_casing_is_repaired():
    draft = proof_lesson("Prove the laws", "Prove …", "S4a")
    data = draft.model_dump()
    sent = _with_sentinels(data, S.LessonV2Draft)
    assert sent != data  # something was nullable (headings, correct_choice, tolerance, …)
    assert S.LessonV2Draft.model_validate(llm_anthropic.restore_nulls(sent, S.LessonV2Draft)) == draft
    shouted = json.loads(json.dumps(sent).replace('"kind": "setup"', '"kind": "SETUP"', 1))
    assert S.LessonV2Draft.model_validate(llm_anthropic.restore_nulls(shouted, S.LessonV2Draft)).blocks[0].kind == "setup"


def test_the_request_caches_the_system_blocks_and_sends_no_sampling_parameters():
    request = llm_anthropic.build_request(
        model="claude-opus-5-5", effort="high", instructions="RULES", text="TASK", schema=S.IndependentAnswer,
        images=[b"\x89PNG"], shared_prefix="CHAPTER", budget=16000,
    )
    assert [b["text"] for b in request["system"]] == ["RULES", "CHAPTER"]
    assert "cache_control" not in request["system"][0]
    assert request["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    content = request["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "text"] and content[-1]["text"] == "TASK"
    assert request["messages"][-1]["role"] == "user"  # no assistant prefill
    assert request["output_config"]["effort"] == "high" and request["output_config"]["format"]["type"] == "json_schema"
    assert request["thinking"] == {"type": "adaptive"}
    assert not {"temperature", "top_p", "top_k"} & set(request)


class _Stream:
    def __init__(self, message):
        self.message = message

    async def __aenter__(self):
        if isinstance(self.message, Exception):
            raise self.message
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self.message


class _StubClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        self.requests.append(request)
        return _Stream(self.outcomes.pop(0))


def _message(payload, *, stop="end_turn", usage=(100, 1000, 0, 50)):
    uncached, write, read, out = usage
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop, stop_details=None,
        usage=SimpleNamespace(input_tokens=uncached, cache_creation_input_tokens=write, cache_read_input_tokens=read,
                              output_tokens=out),
    )


def _status_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("nope", response=httpx2.Response(status, request=request), body=None)


def _call(monkeypatch, outcomes, *, tracker=None, schema=S.IndependentAnswer):
    client = _StubClient(outcomes)
    monkeypatch.setattr(llm_anthropic, "_get_client", lambda: client)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def go():
        token = llm.current_usage.set(tracker)
        try:
            return await llm_anthropic.structured(
                role="verify", model="claude-haiku-5-5", effort="high", instructions="RULES", text="TASK", schema=schema,
                images=None, shared_prefix=None, max_output_tokens=16000, label="t",
            )
        finally:
            llm.current_usage.reset(token)

    return client, asyncio.run(go())


ANSWER = {"choice": -1, "answer": "3+i", "confidence": 0.9, "problems": ""}


def test_a_call_returns_the_parsed_answer_and_bills_cache_writes(monkeypatch):
    tracker = llm.Usage()
    client, answer = _call(monkeypatch, [_message(ANSWER)], tracker=tracker)
    assert answer == S.IndependentAnswer(choice=None, answer="3+i", confidence=0.9, problems="")
    assert tracker.calls == 1 and tracker.input_tokens == 1100 and tracker.output_tokens == 50
    assert tracker.cost_usd == pytest.approx((100 * 0.10 + 1000 * 0.20 + 50 * 0.50) / 1e6)  # 1-hour write price


def test_truncation_refusal_overload_and_bad_json_are_retried(monkeypatch):
    client, answer = _call(monkeypatch, [_message("", stop="max_tokens"), _status_error(llm_anthropic.anthropic.InternalServerError, 529),
                                         _message(ANSWER)])
    assert answer.answer == "3+i" and client.requests[1]["max_tokens"] == 28000  # 16000 × 1.75 after the truncation
    client, answer = _call(monkeypatch, [_message("", stop="refusal"), _message("{not json"), _message(ANSWER)])
    assert answer.answer == "3+i" and len(client.requests) == 3


def test_configuration_errors_are_not_retried(monkeypatch):
    for cls, status, match in ((llm_anthropic.anthropic.AuthenticationError, 401, "ANTHROPIC_API_KEY"),
                               (llm_anthropic.anthropic.NotFoundError, 404, "does not know the model"),
                               (llm_anthropic.anthropic.BadRequestError, 400, "rejected the request")):
        with pytest.raises(llm.LLMError, match=match):
            _call(monkeypatch, [_status_error(cls, status), _message(ANSWER)])


def test_three_failures_end_in_an_llm_error(monkeypatch):
    with pytest.raises(llm.LLMError, match="failed after retries"):
        _call(monkeypatch, [_message("", stop="refusal")] * 3)


def test_haiku_charges_five_times_above_a_hundred_thousand_prompt_tokens():
    short = llm_anthropic.cost_usd("claude-haiku-5-5", 50_000, 0, 0, 1000)
    long = llm_anthropic.cost_usd("claude-haiku-5-5", 150_000, 0, 0, 1000)
    assert long == pytest.approx(5 * (150_000 * 0.10 + 1000 * 0.50) / 1e6) and short == pytest.approx((50_000 * 0.10 + 1000 * 0.50) / 1e6)
    assert llm_anthropic.cost_usd("claude-opus-5-5", 1000, 0, 0, 1000) == pytest.approx((1000 * 4 + 1000 * 20) / 1e6)


def test_the_dispatcher_sends_anthropic_roles_to_the_anthropic_module(monkeypatch):
    seen = {}

    async def fake_structured(**kwargs):
        seen.update(kwargs)
        return S.IndependentAnswer(choice=None, answer="1", confidence=1.0, problems="")

    monkeypatch.setattr(llm_anthropic, "structured", fake_structured)
    object.__setattr__(settings, "fake_llm", False)
    object.__setattr__(settings, "provider_lesson", "anthropic")
    try:
        asyncio.run(llm.structured(role="lesson", instructions="x", text="y", schema=S.IndependentAnswer, effort_notches=1))
    finally:
        object.__setattr__(settings, "fake_llm", True)
        object.__setattr__(settings, "provider_lesson", "")
    assert seen["model"] == "claude-opus-5-5" and seen["effort"] == "high" and seen["label"].endswith("@high")
