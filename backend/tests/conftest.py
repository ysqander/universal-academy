"""Test setup: offline by default (fake LLM, no NVIDIA), isolated data dir and books dir per test (never the project's
books/ folder)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must happen before `app` is imported anywhere.
os.environ["UA_FAKE_LLM"] = "1"
os.environ["UA_DATA_DIR"] = tempfile.mkdtemp(prefix="ua-test-")
os.environ["UA_BOOKS_DIR"] = tempfile.mkdtemp(prefix="ua-test-books-")  # inherited by script subprocesses too
os.environ["NVIDIA_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["UA_LLM_PROVIDER"] = "openai"  # whatever .env chooses for the app
for _role in ("VISION", "GRAPH", "LESSON", "VIZ", "VERIFY"):
    os.environ[f"UA_PROVIDER_{_role}"] = ""
os.environ.pop("ANTHROPIC_BASE_URL", None)  # Claude Code exports it; the app never uses it

import pytest  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402

# Axler, Linear Algebra Done Right (4th ed., Open Access PDF): the example course's copy, or UA_TEST_AXLER_PDF
AXLER_PDF = Path(
    os.environ.get("UA_TEST_AXLER_PDF")
    or Path(__file__).resolve().parents[2] / "examples" / "linear-algebra-done-right" / "book.pdf"
).expanduser()


@pytest.fixture(autouse=True)
def fresh_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point settings.data_dir and settings.books_dir at fresh temp dirs and reset the DB for every test (script
    subprocesses get the same dirs through the environment)."""
    object.__setattr__(settings, "data_dir", tmp_path / "data")
    object.__setattr__(settings, "books_dir", tmp_path / "books")
    monkeypatch.setenv("UA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("UA_BOOKS_DIR", str(tmp_path / "books"))
    db._initialized_for = None
    db.init_db()
    yield tmp_path / "data"


@pytest.fixture
def axler_pdf() -> Path:
    if not AXLER_PDF.exists():
        pytest.skip("Axler PDF not available on this machine")
    return AXLER_PDF
