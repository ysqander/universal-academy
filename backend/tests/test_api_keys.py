"""API keys entered in the app (GET/PUT /api/settings/keys) and the per-OS default data dir."""

from __future__ import annotations

import json
import stat
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, llm, llm_anthropic
from app.config import Settings, load_app_keys, settings
from app.main import app

OPENAI = "sk-app-test-0000000000000000abcd"
ANTHROPIC = "sk-ant-app-test-00000000000wxyz"
ENV_OPENAI = "sk-env-test-0000000000000001111"
FIELDS = ("openai_api_key", "anthropic_api_key", "nvidia_api_key", "llm_provider", "app_keys", "fake_llm", "agent_dir")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """No .env and no key in the environment (conftest blanks them, which would opt out of saved keys); the shared
    settings are restored afterwards."""
    monkeypatch.setattr(config, "_DOTENV", {})
    for name in config.APP_KEY_FIELDS:
        monkeypatch.delenv(name, raising=False)
    before = {f: getattr(settings, f) for f in FIELDS}
    yield TestClient(app)
    for f, value in before.items():
        object.__setattr__(settings, f, value)


def keys_file() -> Path:
    return settings.data_dir / config.APP_KEYS_FILE


def test_a_saved_key_is_masked_wins_over_the_environment_and_can_be_removed(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    config.reload_app_keys()
    assert client.get("/api/settings/keys").json()["keys"]["OPENAI_API_KEY"] == {"set": True, "last4": "1111", "source": "env"}

    r = client.put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": f"  {OPENAI}\n"}})
    assert r.status_code == 200, r.text
    assert r.json()["keys"]["OPENAI_API_KEY"] == {"set": True, "last4": "abcd", "source": "app"}
    assert r.json()["keys"]["NVIDIA_API_KEY"] == {"set": False, "last4": None, "source": None}
    assert OPENAI not in r.text and ENV_OPENAI not in r.text
    assert settings.openai_api_key == OPENAI  # in use at once, no restart
    assert stat.S_IMODE(keys_file().stat().st_mode) == 0o600
    assert json.loads(keys_file().read_text()) == {"OPENAI_API_KEY": OPENAI}
    assert OPENAI not in client.get("/api/settings/keys").text

    r = client.put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": ""}})
    assert r.json()["keys"]["OPENAI_API_KEY"] == {"set": True, "last4": "1111", "source": "env"}
    assert settings.openai_api_key == ENV_OPENAI and not keys_file().exists()


def test_saving_the_missing_key_makes_the_app_ready_without_a_restart(client):
    object.__setattr__(settings, "fake_llm", False)
    object.__setattr__(settings, "llm_provider", "openai")
    health = client.get("/api/health").json()
    assert health["llm_ready"] is False and health["missing_keys"] == ["OPENAI_API_KEY"]

    llm_anthropic._client = object()  # a client built with the previous key
    r = client.put("/api/settings/keys", json={"keys": {"ANTHROPIC_API_KEY": ANTHROPIC}, "provider": "anthropic"})
    assert r.status_code == 200 and r.json()["provider"] == "anthropic" and r.json()["llm_ready"] is True
    health = client.get("/api/health").json()
    assert health["provider"] == "anthropic" and health["llm_ready"] is True and health["missing_keys"] == []
    assert settings.anthropic_api_key == ANTHROPIC and llm_anthropic._client is None and llm._client is None
    assert json.loads(keys_file().read_text()) == {"ANTHROPIC_API_KEY": ANTHROPIC, "UA_LLM_PROVIDER": "anthropic"}
    assert ANTHROPIC not in str(health)

    client.put("/api/settings/keys", json={"keys": {"ANTHROPIC_API_KEY": ""}})
    health = client.get("/api/health").json()
    assert health["llm_ready"] is False and health["missing_keys"] == ["ANTHROPIC_API_KEY"]
    assert json.loads(keys_file().read_text()) == {"UA_LLM_PROVIDER": "anthropic"}


def test_a_variable_blanked_in_the_environment_still_wins_but_a_blank_dotenv_line_does_not(client, monkeypatch):
    client.put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": OPENAI}})
    monkeypatch.setenv("OPENAI_API_KEY", "")  # a test server's "no key"
    config.reload_app_keys()
    assert settings.openai_api_key == ""
    assert client.get("/api/settings/keys").json()["keys"]["OPENAI_API_KEY"]["set"] is False
    monkeypatch.setattr(config, "_DOTENV", {"OPENAI_API_KEY": ""})  # .env copied from .env.example: blank line
    config.reload_app_keys()
    assert settings.openai_api_key == OPENAI


def test_bad_values_are_refused_without_echoing_them(client):
    for value in ("sk-abc\ndef-0000000000", "sk-abc def-00000000000", "sk-" + "x" * 600, "sk-ключ-000000000000"):
        r = client.put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": value}})
        assert r.status_code == 422 and value not in r.text and "OPENAI_API_KEY" in r.text
    assert client.put("/api/settings/keys", json={"keys": {"GITHUB_TOKEN": "x"}}).status_code == 422
    assert client.put("/api/settings/keys", json={"provider": "gemini"}).status_code == 422
    assert not keys_file().exists()


def test_keys_can_be_saved_only_from_this_computer(client):
    remote = TestClient(app, client=("192.168.1.23", 50000))
    assert remote.put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": OPENAI}}).status_code == 403
    assert not keys_file().exists()
    assert TestClient(app, client=("127.0.0.1", 50000)).put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": OPENAI}}).status_code == 200


def test_the_agent_route_uses_no_saved_key(client, tmp_path):
    object.__setattr__(settings, "agent_dir", str(tmp_path / "broker"))
    body = client.put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": OPENAI}}).json()
    assert body["agent_route"] is True and body["keys"]["OPENAI_API_KEY"]["source"] == "app"
    assert settings.openai_api_key == "" and settings.llm_ready


def test_an_unreadable_file_or_unknown_provider_is_ignored(client):
    keys_file().parent.mkdir(parents=True, exist_ok=True)
    keys_file().write_text('{"UA_LLM_PROVIDER": "gemini", "OPENAI_API_KEY": 42, "OTHER": "x"}')
    assert Settings(data_dir=settings.data_dir).llm_provider == "openai"
    assert load_app_keys(settings.data_dir) == {"UA_LLM_PROVIDER": "gemini"}
    keys_file().write_text("not json")
    assert load_app_keys(settings.data_dir) == {}


def test_a_test_run_never_reads_saved_keys_outside_the_temp_dir(client, monkeypatch):
    client.put("/api/settings/keys", json={"keys": {"OPENAI_API_KEY": OPENAI}})
    assert load_app_keys(settings.data_dir) == {"OPENAI_API_KEY": OPENAI}
    monkeypatch.setattr(tempfile, "gettempdir", lambda: "/nonexistent-temp-root")  # the data dir is no longer "temp"
    assert load_app_keys(settings.data_dir) == {}


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "{home}/Library/Application Support/UniversalAcademy"),
        ("linux", {}, "{home}/.local/share/UniversalAcademy"),
        ("linux", {"XDG_DATA_HOME": "/srv/xdg"}, "/srv/xdg/UniversalAcademy"),
        ("linux", {"XDG_DATA_HOME": "relative/xdg"}, "{home}/.local/share/UniversalAcademy"),  # invalid per the spec
        ("freebsd14", {}, "{home}/.local/share/UniversalAcademy"),
        ("win32", {"APPDATA": "/winroot/Roaming"}, "/winroot/Roaming/UniversalAcademy"),
        ("win32", {}, "{home}/AppData/Roaming/UniversalAcademy"),
    ],
)
def test_default_data_dir_per_platform(monkeypatch, tmp_path, platform, env, expected):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("XDG_DATA_HOME", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(config.sys, "platform", platform)
    assert config.default_data_dir() == Path(expected.format(home=tmp_path))
