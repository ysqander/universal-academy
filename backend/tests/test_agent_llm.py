"""app/agent_llm.py: model calls answered by Claude Code agents through request files (UA_AGENT_DIR) — the same
request and schema as an API call, the answer validated the same way, and no API key in use."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from app import agent_llm
from app.llm_schemas import ScopeStatement


def _answer_when_asked(root: Path, make_answer, *, via_validate: bool = True) -> threading.Thread:
    """A stand-in agent: claim the first request, write an answer, publish it with `validate`."""

    def run() -> None:
        while True:
            got = agent_llm.claim(root, wait=10)
            if got["requests"]:
                break
        rid = got["requests"][0]["id"]
        request = json.loads((root / "requests" / f"{rid}.json").read_text())
        out = root / "responses" / (f"{rid}.json.tmp" if via_validate else f"{rid}.json")
        out.write_text(make_answer(request))
        if via_validate:
            ok, message = agent_llm.validate(root, rid)
            assert ok, message

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_a_call_is_written_as_a_request_and_returns_the_validated_answer(tmp_path: Path) -> None:
    thread = _answer_when_asked(tmp_path, lambda req: json.dumps({"statement": req["text"].upper(), "true": True}))
    result = asyncio.run(agent_llm.structured(
        root=tmp_path, role="graph", effort="xhigh", instructions="Say it loudly.", text="we have proved it",
        schema=ScopeStatement, images=[b"\x89PNG"], shared_prefix="context", label="plan S14", timeout=30,
    ))
    thread.join(5)
    assert result == ScopeStatement(statement="WE HAVE PROVED IT", true=True)
    request = json.loads(next((tmp_path / "requests").glob("*.json")).read_text())
    assert request["instructions"] == "Say it loudly." and request["shared_prefix"] == "context"
    assert request["schema_ref"] == "app.llm_schemas:ScopeStatement" and "statement" in request["schema"]["properties"]
    assert Path(request["images"][0]).read_bytes() == b"\x89PNG"
    assert agent_llm.claim(tmp_path, wait=0) == {"done": False, "requests": []}  # answered: nothing left to claim
    (tmp_path / "DONE").write_text("")
    assert agent_llm.claim(tmp_path, wait=0) == {"done": True, "requests": []}


def test_validate_refuses_an_answer_that_does_not_fit_and_the_request_is_offered_again(tmp_path: Path) -> None:
    rid = "x1"
    (tmp_path / "requests").mkdir(parents=True)
    (tmp_path / "requests" / f"{rid}.json").write_text(json.dumps({
        "id": rid, "role": "verify", "label": "verify k1q1", "effort": "low", "instructions": "", "shared_prefix": "",
        "text": "", "images": [], "schema_ref": "app.llm_schemas:ScopeStatement", "schema": {},
    }))
    assert [r["id"] for r in agent_llm.claim(tmp_path, wait=0)["requests"]] == [rid]
    assert agent_llm.claim(tmp_path, wait=0)["requests"] == []  # claimed
    (tmp_path / "responses" / f"{rid}.json.tmp").write_text('{"statement": 3}')
    ok, message = agent_llm.validate(tmp_path, rid)
    assert not ok and "true" in message and not (tmp_path / "responses" / f"{rid}.json").exists()
    (tmp_path / "responses" / f"{rid}.json.tmp").write_text('{"statement": "s", "true": false}')
    assert agent_llm.validate(tmp_path, rid)[0] and (tmp_path / "responses" / f"{rid}.json").exists()


def test_an_answer_published_without_validation_is_rejected_and_asked_again(tmp_path: Path) -> None:
    answers = iter(['{"statement": 1}', '{"statement": "fine", "true": true}'])

    def agent() -> None:
        for _ in range(2):
            while True:
                got = agent_llm.claim(tmp_path, wait=10)
                if got["requests"]:
                    break
            rid = got["requests"][0]["id"]
            (tmp_path / "responses" / f"{rid}.json").write_text(next(answers))
            time.sleep(0.5)

    threading.Thread(target=agent, daemon=True).start()
    result = asyncio.run(agent_llm.structured(
        root=tmp_path, role="verify", effort="low", instructions="", text="", schema=ScopeStatement, images=None,
        shared_prefix=None, label="verify", timeout=30,
    ))
    assert result.statement == "fine"
    assert "statement" in next((tmp_path / "errors").glob("*.txt")).read_text()  # the rejection was recorded


def test_agent_mode_uses_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    monkeypatch.setenv("UA_AGENT_DIR", "/tmp/agents")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    assert settings.agent_dir == "/tmp/agents" and settings.llm_ready
    assert settings.openai_api_key == "" and settings.anthropic_api_key == "" and settings.nvidia_api_key == ""
