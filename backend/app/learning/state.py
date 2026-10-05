"""Skill progress state (SPEC §6.2).

Public interface (stable, used by the pipeline + courses API):
    init_progress(course_id) -> None            # idempotent: create missing progress rows, recompute unlocks
    recompute_unlocks(course_id) -> None
    skill_states(course_id, now=None) -> dict[skill_id, SkillStateInfo]

Statuses: locked (an in-chapter prerequisite is not learned) -> available -> in_progress (lesson attempted)
-> learned (lesson passed; FSRS card exists) -> mastered (card stability >= 21 days). Learned skills never re-lock.
A prerequisite whose lesson failed to generate cannot be learned, so it does not gate its dependents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import db
from . import scheduler

log = logging.getLogger("ua.learning.state")


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


def unmet_prerequisites(course_id: str, skill_id: str) -> list[dict]:
    """Prerequisite skill rows that still block `skill_id` (not learned, lesson not failed)."""
    return db.query(
        "SELECT s.* FROM edges e JOIN skills s ON s.id = e.src LEFT JOIN progress p ON p.skill_id = s.id"
        " WHERE e.course_id = ? AND e.dst = ? AND COALESCE(p.status, 'locked') NOT IN ('learned','mastered')"
        " AND s.lesson_status != 'failed' ORDER BY s.order_index",
        (course_id, skill_id),
    )


def recompute_unlocks(course_id: str) -> None:
    learned = {
        r["skill_id"]
        for r in db.query(
            "SELECT skill_id FROM progress WHERE course_id = ? AND status IN ('learned','mastered')", (course_id,)
        )
    }
    not_gating = {
        r["id"] for r in db.query("SELECT id FROM skills WHERE course_id = ? AND lesson_status = 'failed'", (course_id,))
    }
    prereqs: dict[str, set[str]] = {}
    for e in db.query("SELECT src, dst FROM edges WHERE course_id = ?", (course_id,)):
        prereqs.setdefault(e["dst"], set()).add(e["src"])
    now = db.now_iso()
    for r in db.query("SELECT skill_id, status, lesson_attempts, card_json FROM progress WHERE course_id = ?", (course_id,)):
        if r["status"] in ("learned", "mastered"):
            card = scheduler.load_card(r["card_json"])
            wanted = scheduler.status_for(card) if card else r["status"]
        else:
            open_ = prereqs.get(r["skill_id"], set()) - learned - not_gating
            if open_:
                wanted = "locked"
            else:
                wanted = "in_progress" if r["lesson_attempts"] > 0 else "available"
        if wanted != r["status"]:
            db.execute("UPDATE progress SET status = ?, updated_at = ? WHERE skill_id = ?", (wanted, now, r["skill_id"]))
            log.debug("skill %s: %s -> %s", r["skill_id"], r["status"], wanted)


def skill_states(course_id: str, now: datetime | None = None) -> dict[str, SkillStateInfo]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, SkillStateInfo] = {}
    for r in db.query("SELECT skill_id, status, reviews, card_json FROM progress WHERE course_id = ?", (course_id,)):
        card = scheduler.load_card(r["card_json"]) if r["status"] in ("learned", "mastered") else None
        out[r["skill_id"]] = SkillStateInfo(
            status=r["status"],
            due=scheduler.is_due(card, now),
            due_at=scheduler.iso(card.due) if card else None,
            retrievability=scheduler.retrievability(card, now),
            stability_days=float(card.stability) if card and card.stability is not None else None,
            reviews=r["reviews"],
        )
    return out
