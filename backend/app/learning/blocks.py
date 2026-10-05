"""Teaching blocks of v2 lessons (SPEC §4.2b): inline checks and "This step isn't clear" feedback.

A v2 lesson is stored as skills.lesson_json with "format": "v2" and an ordered "blocks" list (TeachingBlock fields);
a block's "check" holds an InlineCheck (kind, prompt, choices, correct_choice, answer, answer_display, explanation).

Inline checks are formative. They are graded on the server with the learner-facing grader (grading.check_answer, so
an unsimplified but correct value gets the same "now simplify it" request as a practice question), and nothing is
recorded: no attempt, no XP, no effect on the lesson's practice, streaks or the FSRS card. The answer and its
explanation are returned only after a readable response; every error message is free of the answer key.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from .. import db
from ..schemas import BlockCheckRequest, BlockCheckResult, BlockFeedbackRequest
from . import engine, grading, scheduler

log = logging.getLogger("ua.learning.blocks")

GRADED_KINDS = ("numeric", "expression", "multiple_choice", "true_false", "true_false_set")
CHECK_KINDS = (*GRADED_KINDS, "reflect")
REFLECT_FEEDBACK = "Compare with the explanation."
MAX_NOTE_CHARS = 2000


def _lesson(skill_id: str) -> tuple[dict[str, Any], list[Any]]:
    """(skill row, teaching blocks); a v1 lesson (or a skill without a lesson) has no blocks."""
    skill = db.query_one("SELECT id, key, lesson_json FROM skills WHERE id = ?", (skill_id,))
    if not skill:
        raise engine.NotFound("Skill not found.")
    data = db.loads(skill["lesson_json"], None) if skill["lesson_json"] else None
    blocks = data.get("blocks") if isinstance(data, dict) else None
    return skill, blocks if isinstance(blocks, list) else []


def _block(skill_id: str, index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    skill, blocks = _lesson(skill_id)
    if not 0 <= index < len(blocks) or not isinstance(blocks[index], dict):
        raise engine.NotFound("This lesson has no such step.")
    return skill, blocks[index]


def _check_of(block: dict[str, Any]) -> dict[str, Any]:
    check = block.get("check")
    if not isinstance(check, dict) or check.get("kind") not in CHECK_KINDS:
        raise engine.NotFound("This step has no check.")
    return check


def _choices(check: dict[str, Any]) -> list[str]:
    choices = [str(c) for c in check.get("choices") or []]
    if check["kind"] == "true_false" and not choices:
        return ["True", "False"]
    return choices


def _correct_choice(check: dict[str, Any]) -> int | None:
    value = check.get("correct_choice")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def answer_display(check: dict[str, Any]) -> str:
    """The check's answer as shown after a response (its own answer_display when the writer gave one)."""
    if str(check.get("answer_display") or "").strip():
        return str(check["answer_display"])
    kind, answer = check["kind"], str(check.get("answer") or "")
    choices = _choices(check)
    correct = _correct_choice(check)
    if kind in ("multiple_choice", "true_false") and correct is not None and 0 <= correct < len(choices):
        return choices[correct]
    if kind == "true_false_set":
        return grading.true_false_set_display(answer)
    return answer


def _grade(check: dict[str, Any], body: BlockCheckRequest) -> grading.GradeResult:
    kind, answer = check["kind"], str(check.get("answer") or "")
    if kind in ("multiple_choice", "true_false"):
        n = len(_choices(check))
        if body.choice is None or (n and not 0 <= body.choice < n):
            return grading.GradeResult(False, "Choose one of the options.", invalid=True)
    elif not (body.response or "").strip():
        message = grading.true_false_set_request(answer) if kind == "true_false_set" else "Enter an answer first."
        return grading.GradeResult(False, message, invalid=True)
    if kind == "true_false_set":
        key = grading.parse_true_false_set(answer)
        if key and check.get("choices") and len(key) != len(check["choices"]):
            log.warning("inline check: %d statements but a %d-mark answer key", len(check["choices"]), len(key))
    return grading.check_answer(
        kind,
        answer=answer,
        correct_choice=_correct_choice(check),
        tolerance=None,
        response=body.response,
        choice=body.choice,
        require_simplified=True,
    )


def check_block(skill_id: str, index: int, body: BlockCheckRequest) -> BlockCheckResult:
    """Grade a response to a block's inline check. Called WITHOUT engine.lock (grading has its own time budget)."""
    skill, block = _block(skill_id, index)
    check = _check_of(block)
    explanation = str(check.get("explanation") or "")
    if check["kind"] == "reflect":
        return BlockCheckResult(correct=None, feedback=REFLECT_FEEDBACK, answer_display=answer_display(check), explanation=explanation)
    result = _grade(check, body)
    if result.invalid:
        raise engine.Invalid(result.feedback)
    log.info("inline check: skill %s block %d -> %s", skill["key"], index, "correct" if result.correct else "not yet")
    return BlockCheckResult(
        correct=result.correct, feedback=result.feedback, answer_display=answer_display(check), explanation=explanation
    )


def lesson_hash(lesson_json: str | None) -> str:
    """Which version of a lesson the learner saw (sha1 of the stored lesson_json)."""
    return hashlib.sha1((lesson_json or "").encode("utf-8")).hexdigest()


def record_feedback(skill_id: str, index: int, body: BlockFeedbackRequest, now: datetime) -> None:
    """Store "This step isn't clear" for one teaching block, tagged with the lesson version."""
    skill, _ = _block(skill_id, index)
    note = (body.note or "").replace("\x00", "").strip()[:MAX_NOTE_CHARS]
    db.execute(
        "INSERT INTO block_feedback(id, skill_id, block_index, kind, note, lesson_hash, created_at) VALUES (?,?,?,?,?,?,?)",
        (db.new_id(), skill_id, index, body.kind, note, lesson_hash(skill["lesson_json"]), scheduler.iso(now)),
    )
    log.info("block feedback: skill %s block %d marked %s%s", skill["key"], index, body.kind, " (with a note)" if note else "")


def list_feedback(skill_id: str) -> list[dict[str, Any]]:
    """Block feedback of a skill, oldest first (for later analysis of the lesson). lesson_hash tells which version of
    the lesson each entry was about (compare with lesson_hash(skills.lesson_json) for the current one)."""
    _lesson(skill_id)
    return db.query(
        "SELECT block_index, kind, note, created_at, lesson_hash FROM block_feedback WHERE skill_id = ? ORDER BY created_at, rowid",
        (skill_id,),
    )
