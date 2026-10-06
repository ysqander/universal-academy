"""Shared builders for the learning-engine tests: a small course inserted straight into the DB.

Graph (prerequisite -> dependent, encompassing weight):
    S1 -0.8-> S3 -0.6-> S4        effective: w(S4→S3)=0.6, w(S4→S1)=0.48, w(S3→S1)=0.8, w(S3→S2)=0.4
    S2 -0.4-> S3
Every skill has a 2-KP lesson (legacy: no target_questions, so 2 per KP; no capstone) and 4 questions per KP
(tiers 1, 1, 2, 3; numeric / expression / MC). `extra_roots` adds independent skills R1, R2, ... with no edges.
`set_targets` / `add_question` / `add_capstone` turn a skill into a new-style lesson (KP targets, multistep capstone).
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


def wrong_response(q: dict) -> str:
    """A readable but wrong response: a true/false set with its first mark flipped, otherwise a wrong number."""
    if q["kind"] == "true_false_set":
        return ("F" if q["answer"][0] == "T" else "T") + q["answer"][1:]
    return "98765"


def wrong(qid: str, **extra) -> AnswerSubmit:
    q = question(qid)
    if q["kind"] in ("multiple_choice", "true_false"):
        return AnswerSubmit(question_id=qid, choice=(q["correct_choice"] + 1) % 3, **extra)
    return AnswerSubmit(question_id=qid, response=wrong_response(q), **extra)


def make_true_false_set(qid: str, answer: str = "FTT", answer_display: str = "") -> None:
    """Turn a fixture question into a 'mark each statement true or false' question."""
    statements = [f"Statement {n + 1} about $i$." for n in range(len(answer))]
    db.execute(
        "UPDATE questions SET kind = 'true_false_set', choices_json = ?, correct_choice = NULL, answer = ?, answer_display = ?"
        " WHERE id = ?",
        (db.dumps(statements), answer, answer_display, qid),
    )


def learn(fx: CourseFixture, key: str, now: datetime) -> None:
    """Pass a skill's lesson perfectly (through the engine; multistep questions step by step)."""
    task_id = engine.start_lesson(fx.sid(key), "test", now)
    task = engine.begin_practice(task_id, now)
    while task.phase != "done":
        assert task.question is not None
        task = answer_fully(task_id, task.question.id, now).task
    assert task.outcome == "passed"


# ---------------------------------------------------------------------------
# Score-based lessons: KP targets, extra questions, multistep capstones
# ---------------------------------------------------------------------------

CAPSTONE_STEPS = [
    {
        "prompt": "Step 1: compute $2+3$.", "kind": "numeric", "choices": [], "correct_choice": None, "answer": "5",
        "answer_display": "$5$", "tolerance": None, "hint": "Add the two numbers.", "explanation": "$2+3=5$.",
    },
    {
        "prompt": "Step 2: which law justifies the next move?", "kind": "multiple_choice", "choices": ["A", "B", "C"],
        "correct_choice": 2, "answer": "", "answer_display": "C", "tolerance": None, "hint": "Not A.",
        "explanation": "Law C lets us regroup.",
    },
    {
        "prompt": "Step 3: write the result as $a+bi$.", "kind": "expression", "choices": [], "correct_choice": None,
        "answer": "3+4*i", "answer_display": "$3+4i$", "tolerance": None, "hint": "The real part is 3.",
        "explanation": "So the result is $3+4i$.",
    },
]  # fmt: skip


def add_question(
    fx: CourseFixture, key: str, kp: int, *, kind: str = "numeric", answer: str = "7", tier: int = 1,
    role: str = "practice", steps: list[dict] | None = None, choices: list[str] | None = None,
    correct: int | None = None, hints: list[str] | None = None, solution: str = "Model solution.",
) -> str:  # fmt: skip
    qid = db.new_id()
    n = db.query_one("SELECT COUNT(*) AS n FROM questions WHERE skill_id = ?", (fx.sid(key),))["n"]
    db.execute(
        "INSERT INTO questions(id, skill_id, key, kp_index, tier, kind, prompt, choices_json, correct_choice, answer,"
        " answer_display, hints_json, solution, steps_json, role, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            qid, fx.sid(key), f"x{n + 1}", kp, tier, kind, f"Extra question {n + 1}", db.dumps(choices or []), correct,
            answer, "", db.dumps(hints if hints is not None else ["h1"]), solution, db.dumps(steps or []), role,
            scheduler.iso(T0),
        ),
    )  # fmt: skip
    return qid


def add_capstone(fx: CourseFixture, key: str, steps: list[dict] | None = None, kp: int = 0) -> str:
    """A multistep capstone (kp_index deliberately overlaps a KP: the role, not the KP, makes it the capstone)."""
    return add_question(
        fx, key, kp, kind="multistep", answer="", tier=3, role="capstone", steps=steps or CAPSTONE_STEPS,
        hints=[], solution="Whole worked solution.",
    )  # fmt: skip


def set_targets(fx: CourseFixture, key: str, targets: list) -> None:
    """Set knowledge_points[i].target_questions of a skill's lesson (None removes it: legacy)."""
    row = db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (fx.sid(key),))
    lesson = db.loads(row["lesson_json"])
    for kp, target in zip(lesson["knowledge_points"], targets):
        if target is None:
            kp.pop("target_questions", None)
        else:
            kp["target_questions"] = target
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), fx.sid(key)))


def step_submit(qid: str, index: int, correct: bool = True, **extra) -> AnswerSubmit:
    steps = db.loads(question(qid)["steps_json"])
    s = steps[index]
    if s["kind"] in ("multiple_choice", "true_false"):
        choice = s["correct_choice"] if correct else (s["correct_choice"] + 1) % max(2, len(s["choices"]))
        return AnswerSubmit(question_id=qid, step=index, choice=choice, **extra)
    return AnswerSubmit(question_id=qid, step=index, response=s["answer"] if correct else "98765", **extra)


def answer_fully(task_id: str, qid: str, now: datetime, correct: bool = True):
    """Answer the current question (every step of a multistep one); returns the last AnswerResult."""
    q = question(qid)
    if q["kind"] != "multistep":
        return engine.submit_answer(task_id, (right if correct else wrong)(qid), now)
    task = engine.get_task(task_id)
    result = None
    for index in range(task.question.step_index, len(task.question.steps)):
        result = engine.submit_answer(task_id, step_submit(qid, index, correct), now)
    return result


def task_state(task_id: str) -> dict:
    return db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])


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
