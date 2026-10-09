"""Teaching blocks of v2 lessons (SPEC §4.2b): inline checks and "This step isn't clear" feedback.

A v2 lesson is stored as skills.lesson_json with "format": "v2" and an ordered "blocks" list (TeachingBlock fields);
a block's "check" holds an InlineCheck (kind, prompt, choices, correct_choice, answer, answer_display, explanation).

Inline checks are formative. They are graded on the server with the learner-facing grader (grading.check_answer, so
an unsimplified but correct value gets the same "now simplify it" request as a practice question), and nothing is
recorded: no attempt, no XP, no effect on the lesson's practice, streaks or the FSRS card. The answer and its
explanation are returned only after a readable response; every error message is free of the answer key.

The exception is a guided proof lesson (learning/guided.py): its scored checks count toward the lesson and are
answered inside the task (engine.check_guided). Here they are refused (409) until the skill is learned — otherwise
this endpoint would hand out their answers before the lesson asks for them.

Blocks are named by position, so both endpoints take the Lesson.version the learner is looking at (views.lesson_version
of the stored lesson_json, the value views.lesson_view sends). When the lesson was regenerated since the page loaded,
the request is refused with 409 STALE_LESSON instead of grading (or recording feedback on) a different block. A request
without a version is accepted (older clients).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .. import db, views
from ..schemas import BlockCheckRequest, BlockCheckResult, BlockFeedbackRequest
from . import engine, grading, guided, scheduler

log = logging.getLogger("ua.learning.blocks")

GRADED_KINDS = ("numeric", "expression", "multiple_choice", "true_false", "true_false_set")
CHECK_KINDS = (*GRADED_KINDS, "reflect")
REFLECT_FEEDBACK = "Compare with the explanation."
MAX_NOTE_CHARS = 2000
STALE_LESSON = guided.STALE_LESSON
MIN_VERSION_CHARS = views.MIN_VERSION_CHARS


def _lesson(skill_id: str) -> tuple[dict[str, Any], list[Any]]:
    """(skill row, teaching blocks); a v1 lesson (or a skill without a lesson) has no blocks."""
    skill = db.query_one("SELECT id, key, lesson_json FROM skills WHERE id = ?", (skill_id,))
    if not skill:
        raise engine.NotFound("Skill not found.")
    data = db.loads(skill["lesson_json"], None) if skill["lesson_json"] else None
    blocks = data.get("blocks") if isinstance(data, dict) else None
    return skill, blocks if isinstance(blocks, list) else []


def lesson_hash(lesson_json: str | None) -> str:
    """Which version of a lesson the learner saw: views.lesson_version (sha1 of the stored lesson_json), i.e.
    Lesson.version and block_feedback.lesson_hash — one helper, so the two can never drift apart."""
    return views.lesson_version(lesson_json)


same_version = views.same_version


def _block(skill_id: str, index: int, version: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(skill row, block at index). version: the Lesson.version the request was made from (None/'' = not sent);
    a different one is a Conflict even when the index no longer exists (the page shows an older lesson)."""
    skill, blocks = _lesson(skill_id)
    if (version or "").strip() and not same_version(version or "", skill["lesson_json"]):
        log.info("block request for skill %s on an outdated lesson version", skill["key"])
        raise engine.Conflict(STALE_LESSON)
    if not 0 <= index < len(blocks) or not isinstance(blocks[index], dict):
        raise engine.NotFound("This lesson has no such step.")
    return skill, blocks[index]


def _check_of(block: dict[str, Any]) -> dict[str, Any]:
    check = block.get("check")
    if not isinstance(check, dict) or check.get("kind") not in CHECK_KINDS:
        raise engine.NotFound("This step has no check.")
    return check


answer_display = guided.answer_display


def _grade(check: dict[str, Any], body: BlockCheckRequest) -> grading.GradeResult:
    if check["kind"] == "true_false_set":
        key = grading.parse_true_false_set(str(check.get("answer") or ""))
        if key and check.get("choices") and len(key) != len(check["choices"]):
            log.warning("inline check: %d statements but a %d-mark answer key", len(check["choices"]), len(key))
    return guided.grade_check(check, body)


def check_block(skill_id: str, index: int, body: BlockCheckRequest) -> BlockCheckResult:
    """Grade a response to a block's inline check. Called WITHOUT engine.lock (grading has its own time budget)."""
    skill, block = _block(skill_id, index, body.version)
    check = _check_of(block)
    if index in guided.scored_indices(db.loads(skill["lesson_json"], {})) and views.skill_status(skill_id) not in scheduler.LEARNED:
        raise engine.Conflict(guided.SCORED_IN_LESSON)
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


def record_feedback(skill_id: str, index: int, body: BlockFeedbackRequest, now: datetime) -> None:
    """Store "This step isn't clear" for one teaching block, tagged with the lesson version the learner saw: the
    stored lesson's full hash, which is the client's version by construction (a stale version is a Conflict and
    nothing is stored); without a version, the lesson stored when the request arrives."""
    skill, _ = _block(skill_id, index, body.version)
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
