"""Runtime configuration, loaded from the project-root .env (one level above backend/)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
_DOTENV = dotenv_values(PROJECT_ROOT / ".env") if (PROJECT_ROOT / ".env").exists() else {}


def default_data_dir() -> Path:
    """Live data (SQLite + files) stays OUT of the project folder, which is cloud-synced: syncing a database while it
    is written can leave torn "conflict" copies. macOS: ~/Library/Application Support/UniversalAcademy; Windows:
    %APPDATA%\\UniversalAcademy; Linux and others: $XDG_DATA_HOME/UniversalAcademy (~/.local/share/UniversalAcademy)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "UniversalAcademy"
    if sys.platform == "win32":
        return Path(os.getenv("APPDATA", "").strip() or Path.home() / "AppData" / "Roaming") / "UniversalAcademy"
    xdg = os.getenv("XDG_DATA_HOME", "").strip()
    # The XDG spec: a relative XDG_DATA_HOME is invalid and ignored.
    return (Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "share") / "UniversalAcademy"


def default_books_dir() -> Path:
    """Book PDFs live in the project's books/ folder (one folder per book), so a new course from another chapter never
    needs the PDF again. The folder is not in git: deploy or restore it with the data dir. PDFs are written once, so
    syncing the folder is safe."""
    return PROJECT_ROOT / "books"


def project_path(value: str) -> Path:
    """A configured path; a relative one is taken from the project root, not the working directory (run.sh and the
    scripts run from backend/)."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


# Credentials a pytest run blanks, and Anthropic SDK settings it removes: a bare Anthropic client reads them from the
# environment (Claude Code exports ANTHROPIC_BASE_URL to the shells it starts), and the app never relies on them.
_SECRET_ENV = ("OPENAI_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_WORKSPACE_ID")
_AMBIENT_ANTHROPIC_ENV = (
    "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_CONFIG_DIR", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_SERVICE_ACCOUNT_ID", "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_IDENTITY_TOKEN_FILE",
)


def _isolate_test_runs() -> None:
    """A pytest run that did not load tests/conftest.py (e.g. a test module imported from another directory) must
    never touch the learner's data or a paid API: force the fake LLM, blank keys (OpenAI, NVIDIA, Anthropic) and a
    temporary data dir. (On 2026-10-07 such a run wrote a test course into the live database and made paid calls.)"""
    if "pytest" not in sys.modules or os.getenv("UA_ALLOW_LIVE_TESTS") == "1":
        return
    os.environ["UA_FAKE_LLM"] = "1"
    for key in _SECRET_ENV:
        os.environ[key] = ""
    # the same provider setup whatever .env chooses for the app (tests that need another set it themselves)
    os.environ["UA_LLM_PROVIDER"] = "openai"
    for role in ("VISION", "GRAPH", "LESSON", "VIZ", "VERIFY"):
        os.environ[f"UA_PROVIDER_{role}"] = ""
    for key in _AMBIENT_ANTHROPIC_ENV:
        os.environ.pop(key, None)
    import tempfile

    data_dir = os.getenv("UA_DATA_DIR", "").strip()
    if not data_dir or Path(data_dir).expanduser().resolve() == default_data_dir().resolve():
        os.environ["UA_DATA_DIR"] = tempfile.mkdtemp(prefix="ua-test-")
    # Books default to the project folder: a test may only use a books dir under the system temp dir (whatever .env says)
    books_dir = os.getenv("UA_BOOKS_DIR", "").strip()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not books_dir or not project_path(books_dir).resolve().is_relative_to(temp_root):
        os.environ["UA_BOOKS_DIR"] = tempfile.mkdtemp(prefix="ua-test-books-")


_isolate_test_runs()


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _project_first(name: str) -> str:
    """An Anthropic setting from the project's .env before the shell's: Claude Code's shells carry ANTHROPIC_* values of
    their own, which must never bill or route the app's calls. A variable set to an empty string still wins (a test
    server's opt-out, the pytest isolation above)."""
    shell = os.environ.get(name)
    if shell is not None and not shell.strip():
        return ""
    return (_DOTENV.get(name) or "").strip() or (shell or "").strip()


PROVIDERS = ("openai", "anthropic")
ROLES = ("vision", "graph", "lesson", "viz", "verify")
KEY_NAMES = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

def configured_books_dir() -> Path:
    """UA_BOOKS_DIR when set. Otherwise the project's books/ folder for the app's own data dir — but <data>/books for
    any other UA_DATA_DIR: a sandbox or test server on a copy of the database (same book ids) must never write to or
    delete from the real books folder. It still reads PDFs from there (pdf.book_pdf_candidates). A UA_BOOKS_DIR from
    .env belongs to the data dir .env names: a process given another UA_DATA_DIR uses <data>/books."""
    explicit = _env("UA_BOOKS_DIR", "")
    data_dir = _env("UA_DATA_DIR", "")
    own = {default_data_dir().resolve()} | (
        {Path(str(_DOTENV["UA_DATA_DIR"])).expanduser().resolve()} if _DOTENV.get("UA_DATA_DIR") else set()
    )
    own_data_dir = not data_dir or Path(data_dir).expanduser().resolve() in own
    from_dotenv = bool(explicit) and explicit == str(_DOTENV.get("UA_BOOKS_DIR") or "").strip()
    if explicit and (own_data_dir or not from_dotenv):
        return project_path(explicit)
    if not own_data_dir:
        return Path(data_dir).expanduser() / "books"
    return default_books_dir()


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

    # Provider (2026-10-08): UA_LLM_PROVIDER picks OpenAI or Anthropic for every role; UA_PROVIDER_<ROLE> overrides one
    # role (e.g. Opus writes, GPT reviews). Each provider has its own models, efforts and prompt set (app/prompts/).
    llm_provider: str = field(default_factory=lambda: _env("UA_LLM_PROVIDER", "openai").lower())
    provider_vision: str = field(default_factory=lambda: _env("UA_PROVIDER_VISION", "").lower())
    provider_graph: str = field(default_factory=lambda: _env("UA_PROVIDER_GRAPH", "").lower())
    provider_lesson: str = field(default_factory=lambda: _env("UA_PROVIDER_LESSON", "").lower())
    provider_viz: str = field(default_factory=lambda: _env("UA_PROVIDER_VIZ", "").lower())
    provider_verify: str = field(default_factory=lambda: _env("UA_PROVIDER_VERIFY", "").lower())
    # Anthropic: Opus 5.5 where OpenAI uses Sol, Haiku 5.5 where it uses Luna. Efforts: low/medium/high/xhigh/max.
    anthropic_api_key: str = field(default_factory=lambda: _project_first("ANTHROPIC_API_KEY"))
    # An organisation-level key (not created inside a workspace) must name the workspace on every request.
    anthropic_workspace_id: str = field(default_factory=lambda: _project_first("ANTHROPIC_WORKSPACE_ID"))
    # Never the ambient ANTHROPIC_BASE_URL (Claude Code sets it for its own shells): only this app setting.
    anthropic_base_url: str = field(default_factory=lambda: _env("UA_ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
    anthropic_cache_ttl: str = field(default_factory=lambda: _env("UA_ANTHROPIC_CACHE_TTL", "1h"))
    anthropic_model_vision: str = field(default_factory=lambda: _env("UA_ANTHROPIC_MODEL_VISION", "claude-haiku-5-5"))
    anthropic_model_graph: str = field(default_factory=lambda: _env("UA_ANTHROPIC_MODEL_GRAPH", "claude-opus-5-5"))
    anthropic_model_lesson: str = field(default_factory=lambda: _env("UA_ANTHROPIC_MODEL_LESSON", "claude-opus-5-5"))
    anthropic_model_viz: str = field(default_factory=lambda: _env("UA_ANTHROPIC_MODEL_VIZ", "claude-opus-5-5"))
    anthropic_model_verify: str = field(default_factory=lambda: _env("UA_ANTHROPIC_MODEL_VERIFY", "claude-haiku-5-5"))
    anthropic_effort_vision: str = field(default_factory=lambda: _env("UA_ANTHROPIC_EFFORT_VISION", "low"))
    anthropic_effort_graph: str = field(default_factory=lambda: _env("UA_ANTHROPIC_EFFORT_GRAPH", "high"))
    anthropic_effort_lesson: str = field(default_factory=lambda: _env("UA_ANTHROPIC_EFFORT_LESSON", "medium"))
    anthropic_effort_viz: str = field(default_factory=lambda: _env("UA_ANTHROPIC_EFFORT_VIZ", "low"))
    anthropic_effort_verify: str = field(default_factory=lambda: _env("UA_ANTHROPIC_EFFORT_VERIFY", "high"))

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
    # Book PDFs: <books_dir>/<book_id>/book.pdf. The database, course files and page renders stay in the data dir.
    books_dir: Path = field(default_factory=lambda: configured_books_dir())

    # Set UA_FAKE_LLM=1 to run the whole pipeline offline against canned responses (tests / UI dev).
    fake_llm: bool = field(default_factory=lambda: _env("UA_FAKE_LLM", "0") == "1")
    # Set UA_AGENT_DIR to a folder to have every model call answered by Claude Code agents (app/agent_llm.py) instead
    # of an API; UA_AGENT_TIMEOUT (seconds) is how long one call may wait for its answer.
    agent_dir: str = field(default_factory=lambda: _env("UA_AGENT_DIR", ""))
    agent_timeout: float = field(default_factory=lambda: float(_env("UA_AGENT_TIMEOUT", "7200")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "academy.sqlite3"

    @property
    def legacy_books_dir(self) -> Path:
        """Where books were stored before 2026-10-10 (<data>/books/<id>/book.pdf): read as a fallback only."""
        return self.data_dir / "books"

    @property
    def page_cache_dir(self) -> Path:
        """Rendered page images (<id>/<page>@<dpi>.png): derived, safe to delete."""
        return self.data_dir / "page-cache"

    @property
    def upload_tmp_dir(self) -> Path:
        """Uploads are received here (outside the synced books folder) and copied into place once complete."""
        return self.data_dir / "tmp"

    @property
    def courses_dir(self) -> Path:
        return self.data_dir / "courses"

    def __post_init__(self) -> None:
        if self.agent_dir:  # agents answer every model call: no OpenAI/Anthropic key may be used, whatever .env holds
            for key in ("openai_api_key", "anthropic_api_key"):  # the page-layout model (NVIDIA) is still called
                object.__setattr__(self, key, "")
        for role in ROLES:
            provider = self.provider(role)
            if provider not in PROVIDERS:
                raise ValueError(
                    f"LLM provider {provider!r} for role {role} is unknown — UA_LLM_PROVIDER / UA_PROVIDER_{role.upper()} "
                    f"must be one of {', '.join(PROVIDERS)}"
                )

    def provider(self, role: str) -> str:
        """The provider that runs a role: its UA_PROVIDER_<ROLE> override, else UA_LLM_PROVIDER."""
        return getattr(self, f"provider_{role}") or self.llm_provider

    def role_config(self, role: str) -> tuple[str, str, str]:
        """(provider, model, effort) of a role."""
        provider = self.provider(role)
        prefix = "anthropic_" if provider == "anthropic" else ""
        return provider, getattr(self, f"{prefix}model_{role}"), getattr(self, f"{prefix}effort_{role}")

    def api_key(self, provider: str) -> str:
        return self.anthropic_api_key if provider == "anthropic" else self.openai_api_key

    @property
    def providers_in_use(self) -> list[str]:
        return sorted({self.provider(role) for role in ROLES})

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key) or self.fake_llm

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key) or self.fake_llm

    @property
    def missing_keys(self) -> list[str]:
        """The .env keys a course build needs and lacks: the key of every provider the roles use (none on the Claude
        Code route, UA_AGENT_DIR) and NVIDIA_API_KEY — the page-layout model is part of every build, not an option
        (the owner, 2026-10-11). None with the fake LLM."""
        if self.fake_llm:
            return []
        missing = [] if self.agent_dir else [KEY_NAMES[p] for p in self.providers_in_use if not self.api_key(p)]
        if not self.nvidia_api_key and self.parse_engine != "openai":
            missing.append("NVIDIA_API_KEY")
        return missing

    @property
    def llm_ready(self) -> bool:
        """Every key a build needs is set (or the fake LLM answers)."""
        return not self.missing_keys

    @property
    def has_nvidia(self) -> bool:
        return bool(self.nvidia_api_key)


settings = Settings()

