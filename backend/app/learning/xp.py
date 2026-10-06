"""XP rules (SPEC §6.3, §6.4): 1 XP ≈ 1 focused minute.

Lesson: round(est_minutes × score), clamped 3-20, +2 when the score is 100 %.
Review: 4 at score >= 0.8 (+1 when perfect), 2 at score >= 0.6, 1 otherwise.
"""

from __future__ import annotations

import math
from datetime import datetime

from .. import db
from .scheduler import iso

LESSON_XP_MIN = 3
LESSON_XP_MAX = 20
LESSON_PERFECT_BONUS = 2
REVIEW_XP_PASS = 4
REVIEW_XP_PERFECT_BONUS = 1
REVIEW_XP_HARD = 2
REVIEW_XP_FAIL = 1
REVIEW_PASS_SCORE = 0.8  # Good
REVIEW_HARD_SCORE = 0.6  # Hard
_EPS = 1e-9


def _clamp(value: int) -> int:
    return max(LESSON_XP_MIN, min(LESSON_XP_MAX, value))


def lesson_xp_value(est_minutes: int | None) -> int:
    """What a lesson is worth before it is done (shown on the menu and the task)."""
    return _clamp(int(est_minutes or 0))


def lesson_xp(est_minutes: int | None, score: float) -> int:
    """XP for a passed lesson with the given score (0-1)."""
    base = _clamp(int(math.floor(float(est_minutes or 0) * max(0.0, min(1.0, score)) + 0.5)))
    return base + (LESSON_PERFECT_BONUS if score >= 1.0 - _EPS else 0)


def review_xp(score: float) -> int:
    if score >= REVIEW_PASS_SCORE - _EPS:
        return REVIEW_XP_PASS + (REVIEW_XP_PERFECT_BONUS if score >= 1.0 - _EPS else 0)
    if score >= REVIEW_HARD_SCORE - _EPS:
        return REVIEW_XP_HARD
    return REVIEW_XP_FAIL


def award(course_id: str, task_id: str, xp: int, now: datetime) -> None:
    if xp:
        db.execute(
            "INSERT INTO xp_log(id, course_id, task_id, xp, created_at) VALUES (?, ?, ?, ?, ?)",
            (db.new_id(), course_id, task_id, int(xp), iso(now)),
        )


def revoke(task_id: str) -> None:
    db.execute("DELETE FROM xp_log WHERE task_id = ?", (task_id,))
