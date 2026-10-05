"""Skill progress state. STUB — the learning-engine build replaces the bodies (SPEC §6.2, §6.5).

Public interface (stable, used by the pipeline + courses API):
    init_progress(course_id) -> None            # idempotent: create missing progress rows, recompute unlocks
    recompute_unlocks(course_id) -> None
    skill_states(course_id) -> dict[skill_id, SkillStateInfo]
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import db


@dataclass
class SkillStateInfo:
    status: str  # locked | available | in_progress | learned | mastered
    due: bool = False
    due_at: str | None = None
    retrievability: float | None = None
    stability_days: float | None = None
    reviews: int = 0


def init_progress(course_id: str) -> None:
    now = db.now_iso()
    for s in db.query("SELECT id FROM skills WHERE course_id = ?", (course_id,)):
        db.execute(
            "INSERT OR IGNORE INTO progress(skill_id, course_id, status, updated_at) VALUES (?, ?, 'locked', ?)",
            (s["id"], course_id, now),
        )
    recompute_unlocks(course_id)


def recompute_unlocks(course_id: str) -> None:
    learned = {
        r["skill_id"]
        for r in db.query(
            "SELECT skill_id FROM progress WHERE course_id = ? AND status IN ('learned','mastered')", (course_id,)
        )
    }
    prereqs: dict[str, set[str]] = {}
    for e in db.query("SELECT src, dst FROM edges WHERE course_id = ?", (course_id,)):
        prereqs.setdefault(e["dst"], set()).add(e["src"])
    for r in db.query("SELECT skill_id, status FROM progress WHERE course_id = ?", (course_id,)):
        if r["status"] == "locked" and prereqs.get(r["skill_id"], set()) <= learned:
            db.execute("UPDATE progress SET status = 'available', updated_at = ? WHERE skill_id = ?", (db.now_iso(), r["skill_id"]))


def skill_states(course_id: str) -> dict[str, SkillStateInfo]:
    return {
        r["skill_id"]: SkillStateInfo(status=r["status"], reviews=r["reviews"])
        for r in db.query("SELECT skill_id, status, reviews FROM progress WHERE course_id = ?", (course_id,))
    }
