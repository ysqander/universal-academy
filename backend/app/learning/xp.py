"""XP rules (SPEC §6.3, §6.4): 1 XP ≈ 1 focused minute."""

from __future__ import annotations

from datetime import datetime

from .. import db
from .scheduler import iso

LESSON_XP_MIN = 3
LESSON_XP_MAX = 20
LESSON_PERFECT_BONUS = 2
REVIEW_XP_PASS = 4
REVIEW_XP_PERFECT_BONUS = 1
REVIEW_XP_FAIL = 1
REVIEW_PASS_SCORE = 0.6


def lesson_xp_value(est_minutes: int | None) -> int:
    return max(LESSON_XP_MIN, min(LESSON_XP_MAX, int(est_minutes or 0)))


def lesson_xp(est_minutes: int | None, wrong_answers: int) -> int:
    return lesson_xp_value(est_minutes) + (LESSON_PERFECT_BONUS if wrong_answers == 0 else 0)


def review_xp(score: float) -> int:
    if score >= REVIEW_PASS_SCORE - 1e-9:
        return REVIEW_XP_PASS + (REVIEW_XP_PERFECT_BONUS if score >= 1.0 - 1e-9 else 0)
    return REVIEW_XP_FAIL


def award(course_id: str, task_id: str, xp: int, now: datetime) -> None:
    if xp:
        db.execute(
            "INSERT INTO xp_log(id, course_id, task_id, xp, created_at) VALUES (?, ?, ?, ?, ?)",
            (db.new_id(), course_id, task_id, int(xp), iso(now)),
        )


def revoke(task_id: str) -> None:
    db.execute("DELETE FROM xp_log WHERE task_id = ?", (task_id,))
