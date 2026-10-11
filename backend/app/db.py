"""SQLite persistence: schema + tiny helpers. One short-lived connection per operation (safe across asyncio tasks/threads).

Conventions
- ids are short random hex strings (new_id()).
- *_json columns hold JSON text; use dumps()/loads().
- timestamps are ISO-8601 UTC strings (now_iso()).
- page indexes are 0-based PDF page indexes everywhere; printed labels live in books.page_labels_json.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    outline_json TEXT NOT NULL DEFAULT '[]',      -- list[OutlineNode]
    outline_source TEXT NOT NULL DEFAULT 'none',  -- pdf | detected | none
    page_labels_json TEXT NOT NULL DEFAULT '[]',  -- list[str], index = pdf page
    has_text_layer INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id),
    title TEXT NOT NULL,
    start_page INTEGER NOT NULL,
    end_page INTEGER NOT NULL,
    sections_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'building',      -- building | ready | failed
    big_picture TEXT NOT NULL DEFAULT '',
    excluded_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    course_id TEXT PRIMARY KEY REFERENCES courses(id),
    state TEXT NOT NULL,                          -- queued | running | done | failed | interrupted
    stages_json TEXT NOT NULL,                    -- list[JobStage]
    log_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    course_id TEXT NOT NULL REFERENCES courses(id),
    page_index INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    markdown TEXT NOT NULL DEFAULT '',
    items_json TEXT NOT NULL DEFAULT '[]',        -- list[BoxedItem]
    uncertain_json TEXT NOT NULL DEFAULT '[]',
    is_exercise_page INTEGER NOT NULL DEFAULT 0,
    engine TEXT NOT NULL DEFAULT '',              -- e.g. "openai:gpt-5.4-mini+nemotron"
    raw_json TEXT NOT NULL DEFAULT '{}',          -- raw engine outputs for debugging
    PRIMARY KEY (course_id, page_index)
);

CREATE TABLE IF NOT EXISTS figures (
    id TEXT PRIMARY KEY,                          -- "p21-f1" (unique per course; stored with course_id)
    course_id TEXT NOT NULL REFERENCES courses(id),
    page_index INTEGER NOT NULL,
    bbox_json TEXT NOT NULL,                      -- [x0,y0,x1,y1] in PDF points
    caption TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS foundations (
    course_id TEXT NOT NULL REFERENCES courses(id),
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    check_question TEXT NOT NULL,
    check_answer TEXT NOT NULL,
    known INTEGER,                                -- NULL = unanswered, 0/1
    PRIMARY KEY (course_id, key)
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id),
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    difficulty INTEGER NOT NULL DEFAULT 2,
    est_minutes INTEGER NOT NULL DEFAULT 10,
    order_index INTEGER NOT NULL DEFAULT 0,       -- topological learning order
    source_pages_json TEXT NOT NULL DEFAULT '[]',
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    figure_ids_json TEXT NOT NULL DEFAULT '[]',
    foundations_json TEXT NOT NULL DEFAULT '[]',
    misconceptions_json TEXT NOT NULL DEFAULT '[]',
    lesson_status TEXT NOT NULL DEFAULT 'pending', -- pending | ready | failed
    lesson_json TEXT,                              -- LessonDraft with knowledge_points[*].questions removed (they live in `questions`)
    viz_json TEXT,                                 -- VisualizationPlan (+ pre-sampled plot data)
    UNIQUE (course_id, key)
);

CREATE TABLE IF NOT EXISTS edges (
    course_id TEXT NOT NULL REFERENCES courses(id),
    src TEXT NOT NULL REFERENCES skills(id),       -- prerequisite
    dst TEXT NOT NULL REFERENCES skills(id),       -- dependent skill
    weight REAL NOT NULL DEFAULT 0.5,              -- encompassing weight 0-1
    reduced INTEGER NOT NULL DEFAULT 1,            -- 1 if in transitive reduction (draw it)
    PRIMARY KEY (src, dst)
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    key TEXT NOT NULL,
    kp_index INTEGER NOT NULL DEFAULT 0,           -- knowledge point (0-based) within the skill's lesson
    tier INTEGER NOT NULL,
    kind TEXT NOT NULL,
    prompt TEXT NOT NULL,
    choices_json TEXT NOT NULL DEFAULT '[]',
    correct_choice INTEGER,
    answer TEXT NOT NULL DEFAULT '',
    answer_display TEXT NOT NULL DEFAULT '',
    tolerance REAL,
    hints_json TEXT NOT NULL DEFAULT '[]',
    solution TEXT NOT NULL DEFAULT '',
    checklist_json TEXT NOT NULL DEFAULT '[]',
    targets TEXT NOT NULL DEFAULT '',
    source_exercise TEXT,
    flagged INTEGER NOT NULL DEFAULT 0,            -- excluded from scoring/serving when 1
    flag_reason TEXT NOT NULL DEFAULT '',
    verify_json TEXT NOT NULL DEFAULT '{}',        -- {"status": "agree"|"disagree"|"skipped", ...}
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    skill_id TEXT PRIMARY KEY REFERENCES skills(id),
    course_id TEXT NOT NULL REFERENCES courses(id),
    status TEXT NOT NULL DEFAULT 'locked',         -- locked | available | in_progress | learned | mastered
    card_json TEXT,                                -- fsrs.Card.to_json() once learned
    lesson_attempts INTEGER NOT NULL DEFAULT 0,
    lesson_passed_at TEXT,
    kp_failures_json TEXT NOT NULL DEFAULT '{}',   -- {"<kp_index>": failures} → 2nd failure at same KP triggers remediation
    cooldown_until TEXT,                           -- after a failed lesson, don't re-serve until then
    reviews INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    implicit_credits INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id),
    skill_id TEXT NOT NULL REFERENCES skills(id),
    type TEXT NOT NULL,                            -- lesson | review
    status TEXT NOT NULL DEFAULT 'active',         -- active | completed
    phase TEXT NOT NULL DEFAULT 'learn',           -- learn | practice | done
    reason TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL DEFAULT '{}',         -- served question ids, counters, current question id
    outcome TEXT,                                  -- passed | failed | abandoned
    xp_awarded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    skill_id TEXT NOT NULL REFERENCES skills(id),
    correct INTEGER NOT NULL,
    status TEXT NOT NULL,                          -- correct | incorrect | partial (a true/false set) | self_graded | voided
    independent INTEGER NOT NULL DEFAULT 0,
    response TEXT NOT NULL DEFAULT '',
    hints_used INTEGER NOT NULL DEFAULT 0,
    revealed INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_log (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    course_id TEXT NOT NULL,
    rating INTEGER NOT NULL,                       -- fsrs Rating value (1..4)
    implicit INTEGER NOT NULL DEFAULT 0,           -- 1 = FIRe-style implicit credit
    weight REAL NOT NULL DEFAULT 1.0,
    source_skill_id TEXT,                          -- skill whose practice generated implicit credit
    due_before TEXT,
    due_after TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xp_log (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    task_id TEXT,
    xp INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS block_feedback (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    block_index INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'unclear',
    note TEXT NOT NULL DEFAULT '',
    lesson_hash TEXT NOT NULL DEFAULT '',          -- which version of the lesson the learner saw
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skills_course ON skills(course_id);
CREATE INDEX IF NOT EXISTS idx_questions_skill ON questions(skill_id);
CREATE INDEX IF NOT EXISTS idx_tasks_course ON tasks(course_id, status);
CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_xp_course ON xp_log(course_id, created_at);
"""

# Columns added after the first release: (table, column, DDL). Applied idempotently by init_db().
MIGRATIONS: list[tuple[str, str, str]] = [
    ("questions", "steps_json", "ALTER TABLE questions ADD COLUMN steps_json TEXT NOT NULL DEFAULT '[]'"),
    ("questions", "role", "ALTER TABLE questions ADD COLUMN role TEXT NOT NULL DEFAULT 'practice'"),  # practice | capstone
]

_init_lock = threading.Lock()
_initialized_for: str | None = None


def init_db() -> None:
    global _initialized_for
    with _init_lock:
        settings.data_dir.mkdir(parents=True, exist_ok=True)  # the books dir is created by the first import
        settings.courses_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.db_path)
        try:
            conn.executescript(SCHEMA)
            for table, column, ddl in MIGRATIONS:
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()
        _initialized_for = str(settings.db_path)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Short-lived connection; commits on success, rolls back on error."""
    if _initialized_for != str(settings.db_path):
        init_db()
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | dict = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def executemany(sql: str, seq: list[tuple] | list[dict]) -> None:
    with connect() as conn:
        conn.executemany(sql, seq)


def new_id() -> str:
    return secrets.token_hex(6)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


def kv_get(key: str, default: str | None = None) -> str | None:
    row = query_one("SELECT value FROM kv WHERE key = ?", (key,))
    return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
