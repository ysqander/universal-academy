"""Shared builders for the learning-engine tests: a small course inserted straight into the DB.

Graph (prerequisite -> dependent, encompassing weight):
    S1 -0.8-> S3 -0.6-> S4        effective: w(S4→S3)=0.6, w(S4→S1)=0.48, w(S3→S1)=0.8, w(S3→S2)=0.4
    S2 -0.4-> S3
Every skill has a 2-KP lesson and 4 questions per KP (tiers 1, 1, 2, 3; numeric / expression / MC).
`extra_roots` adds independent skills R1, R2, ... with no edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from fsrs import Scheduler

from app import db
from app.learning import engine, scheduler, state
from app.schemas import AnswerSubmit

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


@dataclass
class CourseFixture:
    course_id: str
    skills: dict[str, str] = field(default_factory=dict)  # key -> skill id
    questions: dict[str, list[list[str]]] = field(default_factory=dict)  # key -> [kp][i] -> question id

    def sid(self, key: str) -> str:
        return self.skills[key]


@pytest.fixture(autouse=True)
def isolated_engine(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int | None]]:
    """No FSRS fuzzing, and question top-ups are recorded instead of starting background generation."""
    monkeypatch.setattr(
        scheduler, "scheduler", Scheduler(desired_retention=0.9, learning_steps=(), relearning_steps=(), enable_fuzzing=False)
    )
    requested: list[tuple[str, int | None]] = []
    monkeypatch.setattr(engine, "request_top_up", lambda skill_id, kp_index=None: requested.append((skill_id, kp_index)))
    return requested


def _lesson_json(key: str) -> dict:
    def kp(n: int) -> dict:
        return {
            "title": f"{key} step {n + 1}",
            "explanation": f"Explanation of step {n + 1}.",
            "worked_example": {"problem": "Compute $1+1$.", "steps": [{"math": "$1+1=2$", "why": "addition"}], "takeaway": "Add."},
        }

    return {
        "why_it_matters": "Because.",
        "idea": "The idea.",
        "notation": [],
        "knowledge_points": [kp(0), kp(1)],
        "missing_steps": "",
        "common_mistakes": [],
        "connection": "Next.",
        "source_refs": ["1.1"],
    }


def _question_specs(skill_index: int, kp: int) -> list[dict]:
    base = 100 * skill_index + 10 * kp
    return [
        {"tier": 1, "kind": "numeric", "answer": str(base + 1), "display": f"${base + 1}$"},
        {"tier": 1, "kind": "expression", "answer": f"{base + 2}+{kp + 1}*i", "display": f"${base + 2}+{kp + 1}i$"},
        {"tier": 2, "kind": "multiple_choice", "choices": ["A", "B", "C"], "correct": 1, "display": "B"},
        {"tier": 3, "kind": "expression", "answer": f"({base}, -2, 3)", "display": f"$({base}, -2, 3)$"},
    ]


def build_course(*, est_minutes: int = 10, lesson_status: str = "ready", extra_roots: int = 0) -> CourseFixture:
    now = scheduler.iso(T0)
    book_id, course_id = db.new_id(), db.new_id()
    db.execute(
        "INSERT INTO books(id, title, author, filename, pdf_path, page_count, created_at) VALUES (?,?,?,?,?,?,?)",
        (book_id, "Test Book", "Author", "book.pdf", "/dev/null", 10, now),
    )
    db.execute(
        "INSERT INTO courses(id, book_id, title, start_page, end_page, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (course_id, book_id, "Chapter 1", 0, 9, "ready", now),
    )
    fx = CourseFixture(course_id)
    keys = ["S1", "S2", "S3", "S4"] + [f"R{n}" for n in range(1, extra_roots + 1)]
    for index, key in enumerate(keys, start=1):
        sid = db.new_id()
        fx.skills[key] = sid
        db.execute(
            "INSERT INTO skills(id, course_id, key, title, objective, kind, est_minutes, order_index, lesson_status, lesson_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, course_id, key, f"Skill {key}", f"Do {key}", "computation", est_minutes, index, lesson_status, db.dumps(_lesson_json(key))),
        )
        fx.questions[key] = []
        for kp in range(2):
            ids = []
            for i, spec in enumerate(_question_specs(index, kp)):
                qid = db.new_id()
                ids.append(qid)
                db.execute(
                    "INSERT INTO questions(id, skill_id, key, kp_index, tier, kind, prompt, choices_json, correct_choice, answer,"
                    " answer_display, hints_json, solution, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        qid, sid, f"k{kp + 1}q{i + 1}", kp, spec["tier"], spec["kind"], f"Question {key} {kp}.{i}",
                        db.dumps(spec.get("choices", [])), spec.get("correct"), spec.get("answer", ""), spec["display"],
                        db.dumps(["h1", "h2", "h3"]), f"Solution {key} {kp}.{i}", now,
                    ),
                )  # fmt: skip
            fx.questions[key].append(ids)
    for src, dst, w in [("S1", "S3", 0.8), ("S2", "S3", 0.4), ("S3", "S4", 0.6)]:
        db.execute(
            "INSERT INTO edges(course_id, src, dst, weight, reduced) VALUES (?,?,?,?,1)", (course_id, fx.sid(src), fx.sid(dst), w)
        )
    state.init_progress(course_id)
    return fx


def question(qid: str) -> dict:
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (qid,))
    assert row is not None
    return row


def right(qid: str, **extra) -> AnswerSubmit:
    q = question(qid)
    if q["kind"] in ("multiple_choice", "true_false"):
        return AnswerSubmit(question_id=qid, choice=q["correct_choice"], **extra)
    return AnswerSubmit(question_id=qid, response=q["answer"], **extra)


def wrong(qid: str, **extra) -> AnswerSubmit:
    q = question(qid)
    if q["kind"] in ("multiple_choice", "true_false"):
        return AnswerSubmit(question_id=qid, choice=(q["correct_choice"] + 1) % 3, **extra)
    return AnswerSubmit(question_id=qid, response="98765", **extra)


def learn(fx: CourseFixture, key: str, now: datetime) -> None:
    """Pass a skill's lesson perfectly (through the engine)."""
    task_id = engine.start_lesson(fx.sid(key), "test", now)
    task = engine.begin_practice(task_id, now)
    while task.phase != "done":
        assert task.question is not None
        task = engine.submit_answer(task_id, right(task.question.id), now).task
    assert task.outcome == "passed"


def card(skill_id: str):
    row = db.query_one("SELECT card_json FROM progress WHERE skill_id = ?", (skill_id,))
    return scheduler.load_card(row["card_json"]) if row else None


def set_due(skill_id: str, due: datetime) -> None:
    c = card(skill_id)
    assert c is not None
    c.due = due
    db.execute("UPDATE progress SET card_json = ? WHERE skill_id = ?", (scheduler.dump_card(c), skill_id))


def status(skill_id: str) -> str:
    return db.query_one("SELECT status FROM progress WHERE skill_id = ?", (skill_id,))["status"]


def days(n: float) -> timedelta:
    return timedelta(days=n)
