"""A test run that bypasses tests/conftest.py still never reaches the learner's data or a paid API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

PROBE = (
    "import json, pytest\n"
    "from app.config import settings, default_data_dir, default_books_dir\n"
    "import os\n"
    "print(json.dumps({'fake': settings.fake_llm, 'openai': settings.openai_api_key, 'nvidia': settings.nvidia_api_key,"
    " 'anthropic': settings.anthropic_api_key, 'anthropic_env': os.environ.get('ANTHROPIC_API_KEY'),"
    " 'base_url_env': os.environ.get('ANTHROPIC_BASE_URL'), 'base_url': settings.anthropic_base_url,"
    " 'providers': sorted({settings.provider(r) for r in ('vision', 'graph', 'lesson', 'viz', 'verify')}),"
    " 'data_dir': str(settings.data_dir), 'default': str(default_data_dir()),"
    " 'books_dir': str(settings.books_dir), 'default_books': str(default_books_dir())}))\n"
)


def probe(env_extra: dict[str, str]) -> dict:
    planted = ("OPENAI_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")
    env = {k: v for k, v in os.environ.items() if not k.startswith("UA_") and k not in planted}
    env |= {"OPENAI_API_KEY": "sk-not-a-real-key", "NVIDIA_API_KEY": "nv-not-a-real-key",
            "ANTHROPIC_API_KEY": "sk-ant-not-a-real-key", "ANTHROPIC_BASE_URL": "https://example.invalid",
            "UA_LLM_PROVIDER": "anthropic", "UA_PROVIDER_LESSON": "anthropic"} | env_extra
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BACKEND, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_pytest_process_without_conftest_is_offline_and_isolated():
    seen = probe({})
    assert seen["fake"] is True and seen["openai"] == "" and seen["nvidia"] == ""
    assert seen["anthropic"] == "" and seen["anthropic_env"] == "" and seen["base_url_env"] is None
    assert seen["base_url"] == "https://api.anthropic.com"
    assert seen["providers"] == ["openai"]  # pinned, whatever .env chooses for the app
    assert seen["data_dir"] != seen["default"] and "ua-test-" in seen["data_dir"]
    assert seen["books_dir"] != seen["default_books"] and "ua-test-books-" in seen["books_dir"]


def test_the_default_data_dir_is_refused_even_when_named_explicitly():
    seen = probe({"UA_DATA_DIR": probe({})["default"]})
    assert seen["data_dir"] != seen["default"] and seen["fake"] is True


def test_a_sandbox_data_dir_never_writes_to_the_project_books_folder(tmp_path: Path):
    """Outside pytest: a scratch UA_DATA_DIR (a copy of the live DB shares its book ids) keeps its books in <data>/books;
    only an explicit UA_BOOKS_DIR or the app's own data dir use the project folder."""
    code = ("import json\nfrom app.config import settings, default_books_dir\n"
            "print(json.dumps({'books': str(settings.books_dir), 'project': str(default_books_dir())}))")
    env = {k: v for k, v in os.environ.items() if not k.startswith("UA_")} | {"UA_FAKE_LLM": "1"}

    def books_dir(extra: dict[str, str]) -> dict:
        out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env | extra, capture_output=True, text=True, check=True)
        return json.loads(out.stdout.strip().splitlines()[-1])

    sandbox = books_dir({"UA_DATA_DIR": str(tmp_path / "sandbox")})
    assert sandbox["books"] == str(tmp_path / "sandbox" / "books")
    explicit = books_dir({"UA_DATA_DIR": str(tmp_path / "server"), "UA_BOOKS_DIR": "books"})
    assert explicit["books"] == explicit["project"]  # relative to the project root, not the working directory


def test_a_books_dir_from_dotenv_belongs_to_the_dotenv_data_dir(monkeypatch, tmp_path: Path):
    from app import config

    monkeypatch.setattr(config, "_DOTENV", {"UA_BOOKS_DIR": "books"})
    monkeypatch.setenv("UA_BOOKS_DIR", "books")  # as load_dotenv puts it
    monkeypatch.setenv("UA_DATA_DIR", str(tmp_path / "sandbox"))
    assert config.configured_books_dir() == tmp_path / "sandbox" / "books"  # a sandbox: never the project's books/
    monkeypatch.delenv("UA_DATA_DIR")
    assert config.configured_books_dir() == config.PROJECT_ROOT / "books"  # the app itself
    monkeypatch.setenv("UA_BOOKS_DIR", "/srv/books")  # set in the shell for this process: honoured
    monkeypatch.setenv("UA_DATA_DIR", str(tmp_path / "server"))
    assert config.configured_books_dir() == Path("/srv/books")


def test_the_project_books_folder_is_refused_even_when_named_explicitly():
    default_books = probe({})["default_books"]
    for named in (default_books, "books", str(Path.home() / "some-books")):  # absolute, relative to the project, elsewhere
        seen = probe({"UA_BOOKS_DIR": named})
        assert "ua-test-books-" in seen["books_dir"], named
