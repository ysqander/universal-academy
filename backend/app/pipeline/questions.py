"""Post-validation of generated questions and their storage in the `questions` table."""

from __future__ import annotations

import logging
import re
import sqlite3

from .. import db
from ..learning.grading import check_answer
from ..llm_schemas import QuestionDraft

log = logging.getLogger("ua.pipeline.questions")

MACHINE_CHECKABLE = {"multiple_choice", "true_false", "numeric", "expression"}
TRUE_FALSE = ["True", "False"]
MAX_CHOICES = 6
MAX_CHECKLIST = 5


class InvalidQuestion(ValueError):
    pass


def is_machine_checkable(kind: str) -> bool:
    return kind in MACHINE_CHECKABLE


def clean_question(q: QuestionDraft) -> QuestionDraft:
    """Return a normalized copy or raise InvalidQuestion with the reason."""
    if not q.prompt.strip():
        raise InvalidQuestion("empty prompt")
    update: dict = {"tier": min(3, max(1, int(q.tier))), "hints": [h for h in q.hints if h.strip()][:3]}
    if q.kind == "multiple_choice":
        choices = [c for c in q.choices if c.strip()]
        if not 2 <= len(choices) <= MAX_CHOICES or len(choices) != len(q.choices):
            raise InvalidQuestion(f"multiple choice needs 2-{MAX_CHOICES} non-empty choices")
        if len({_norm(c) for c in choices}) != len(choices):
            raise InvalidQuestion("duplicate choices")
        _require_choice_index(q.correct_choice, len(choices))
        update |= {"answer": "", "tolerance": None, "checklist": []}
    elif q.kind == "true_false":
        if [c.strip().lower() for c in q.choices] not in ([], ["true", "false"]):
            raise InvalidQuestion(f"true/false choices must be {TRUE_FALSE}, got {q.choices}")
        _require_choice_index(q.correct_choice, 2)
        update |= {"choices": TRUE_FALSE, "answer": "", "tolerance": None, "checklist": []}
    elif q.kind in ("numeric", "expression"):
        answer = _strip_math(q.answer)
        if not answer:
            raise InvalidQuestion(f"{q.kind} question without an answer")
        tolerance = abs(q.tolerance) if q.tolerance is not None and q.kind == "numeric" else None
        if not check_answer(
            q.kind, answer=answer, correct_choice=None, tolerance=tolerance, response=answer, choice=None
        ).correct:
            raise InvalidQuestion(f"the grader cannot read the answer {answer!r}")
        update |= {"answer": answer, "tolerance": tolerance, "choices": [], "correct_choice": None, "checklist": []}
    elif q.kind == "free_response":
        checklist = [c for c in q.checklist if c.strip()][:MAX_CHECKLIST]
        if len(checklist) < 2:
            raise InvalidQuestion("free response needs a checklist of at least 2 items")
        if not q.solution.strip():
            raise InvalidQuestion("free response needs a model solution")
        update |= {"checklist": checklist, "answer": "", "tolerance": None, "choices": [], "correct_choice": None}
    else:
        raise InvalidQuestion(f"unknown kind {q.kind}")
    return q.model_copy(update=update)


def clean_questions(questions: list[QuestionDraft], context: str) -> tuple[list[QuestionDraft], list[str]]:
    """Clean a pool; invalid questions are dropped with a reason."""
    kept, dropped = [], []
    for q in questions:
        try:
            kept.append(clean_question(q))
        except InvalidQuestion as e:
            dropped.append(f"{context} {q.key or '?'}: {e}")
    return kept, dropped


def _require_choice_index(index: int | None, n: int) -> None:
    if index is None or not 0 <= index < n:
        raise InvalidQuestion(f"correct_choice {index} out of range 0..{n - 1}")


def _strip_math(text: str) -> str:
    return text.strip().strip("$").strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def normalized_prompt(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def insert_questions(
    conn: sqlite3.Connection, skill_id: str, kp_index: int, questions: list[QuestionDraft]
) -> list[str]:
    """Insert questions of one knowledge point; returns their ids. Free-response questions are marked as not
    needing verification."""
    ids = []
    now = db.now_iso()
    for q in questions:
        qid = db.new_id()
        verify = {} if is_machine_checkable(q.kind) else {"status": "skipped", "reason": "self-graded"}
        conn.execute(
            "INSERT INTO questions(id, skill_id, key, kp_index, tier, kind, prompt, choices_json, correct_choice, "
            "answer, answer_display, tolerance, hints_json, solution, checklist_json, targets, source_exercise, "
            "verify_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                qid,
                skill_id,
                q.key,
                kp_index,
                q.tier,
                q.kind,
                q.prompt.strip(),
                db.dumps(q.choices),
                q.correct_choice,
                q.answer,
                q.answer_display.strip(),
                q.tolerance,
                db.dumps(q.hints),
                q.solution.strip(),
                db.dumps(q.checklist),
                q.targets.strip(),
                (q.source_exercise or "").strip() or None,
                db.dumps(verify),
                now,
            ),
        )
        ids.append(qid)
    return ids
