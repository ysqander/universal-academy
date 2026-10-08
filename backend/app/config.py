"""Runtime configuration, loaded from the project-root .env (one level above backend/)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def default_data_dir() -> Path:
    """Live data (SQLite + files) stays OUT of the project folder, which is cloud-synced: syncing a database while it
    is written can leave torn "conflict" copies. macOS: ~/Library/Application Support/UniversalAcademy."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "UniversalAcademy"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "universal-academy"


def _isolate_test_runs() -> None:
    """A pytest run that did not load tests/conftest.py (e.g. a test module imported from another directory) must
    never touch the learner's data or a paid API: force the fake LLM, blank keys and a temporary data dir. (On
    2026-10-07 such a run wrote a test course into the live database and made paid calls.)"""
    if "pytest" not in sys.modules or os.getenv("UA_ALLOW_LIVE_TESTS") == "1":
        return
    os.environ["UA_FAKE_LLM"] = "1"
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["NVIDIA_API_KEY"] = ""
    data_dir = os.getenv("UA_DATA_DIR", "").strip()
    if not data_dir or Path(data_dir).expanduser().resolve() == default_data_dir().resolve():
        import tempfile

        os.environ["UA_DATA_DIR"] = tempfile.mkdtemp(prefix="ua-test-")


_isolate_test_runs()


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip())
    nvidia_api_key: str = field(default_factory=lambda: os.getenv("NVIDIA_API_KEY", "").strip())

    # Model roles — every role can be overridden in .env (see docs/SPEC.md §8 for the rationale).
    model_vision: str = field(default_factory=lambda: _env("UA_MODEL_VISION", "gpt-6-luna"))
    model_graph: str = field(default_factory=lambda: _env("UA_MODEL_GRAPH", "gpt-6.1-sol"))
    model_lesson: str = field(default_factory=lambda: _env("UA_MODEL_LESSON", "gpt-6.1-sol"))
    model_viz: str = field(default_factory=lambda: _env("UA_MODEL_VIZ", "gpt-6.1-sol"))
    model_verify: str = field(default_factory=lambda: _env("UA_MODEL_VERIFY", "gpt-6-luna"))
    effort_vision: str = field(default_factory=lambda: _env("UA_EFFORT_VISION", "low"))
    effort_graph: str = field(default_factory=lambda: _env("UA_EFFORT_GRAPH", "high"))
    effort_lesson: str = field(default_factory=lambda: _env("UA_EFFORT_LESSON", "medium"))
    effort_viz: str = field(default_factory=lambda: _env("UA_EFFORT_VIZ", "low"))
    effort_verify: str = field(default_factory=lambda: _env("UA_EFFORT_VERIFY", "high"))
    # Proof skills get more reasoning on the same model (the learner, 2026-10-08: "use sol at one higher reasoning
    # notch"): planner high → xhigh, writer medium → high, reviewer high → xhigh.
    proof_effort_notches: int = field(default_factory=lambda: int(_env("UA_PROOF_EFFORT_NOTCHES", "1")))

    # Page parsing engine: "auto" (nemotron when NVIDIA key present, plus OpenAI vision), "openai", "nemotron".
    parse_engine: str = field(default_factory=lambda: _env("UA_PARSE_ENGINE", "auto"))
    nemotron_model: str = field(default_factory=lambda: _env("UA_NEMOTRON_MODEL", "nvidia/nemotron-parse"))
    nemotron_url: str = field(
        default_factory=lambda: _env("UA_NEMOTRON_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
    )

    llm_concurrency: int = field(default_factory=lambda: int(_env("UA_LLM_CONCURRENCY", "6")))
    render_dpi: int = field(default_factory=lambda: int(_env("UA_RENDER_DPI", "200")))
    max_course_pages: int = field(default_factory=lambda: int(_env("UA_MAX_COURSE_PAGES", "80")))

    data_dir: Path = field(default_factory=lambda: Path(_env("UA_DATA_DIR", str(default_data_dir()))))

    # Set UA_FAKE_LLM=1 to run the whole pipeline offline against canned responses (tests / UI dev).
    fake_llm: bool = field(default_factory=lambda: _env("UA_FAKE_LLM", "0") == "1")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "academy.sqlite3"

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    @property
    def courses_dir(self) -> Path:
        return self.data_dir / "courses"

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key) or self.fake_llm

    @property
    def has_nvidia(self) -> bool:
        return bool(self.nvidia_api_key)


settings = Settings()
