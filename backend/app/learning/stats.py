"""Course statistics. STUB — the learning-engine build replaces the body (SPEC §6.6).

Public interface (stable): compute_stats(course_id) -> CourseStats
"""

from __future__ import annotations

from .. import db
from ..schemas import CourseStats


def compute_stats(course_id: str) -> CourseStats:
    rows = db.query("SELECT status FROM progress WHERE course_id = ?", (course_id,))
    total = db.query_one("SELECT COUNT(*) AS n FROM skills WHERE course_id = ?", (course_id,))["n"]
    ready = db.query_one(
        "SELECT COUNT(*) AS n FROM skills WHERE course_id = ? AND lesson_status = 'ready'", (course_id,)
    )["n"]
    count = lambda *st: sum(1 for r in rows if r["status"] in st)  # noqa: E731
    return CourseStats(
        total_skills=total,
        learned=count("learned", "mastered"),
        mastered=count("mastered"),
        available=count("available"),
        in_progress=count("in_progress"),
        locked=count("locked"),
        lessons_ready=ready,
    )
