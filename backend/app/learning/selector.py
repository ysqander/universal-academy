"""Task selection: the ranked menu and "Continue learning" (SPEC §6.6).

- Reviews: learned skills due now. importance = 1 + (1 - R) * 2 + 0.2 * #dependents (+1 for remedial reviews).
- Lessons: available / in-progress skills with a ready lesson, not cooling down.
  importance = 1 + 0.3 * #transitive dependents + Σ w(S→P) over due ancestors P (repetition compression).
  A due review covered by a menu lesson with w >= 0.5 is dropped (the lesson's success will credit it).
- Lesson floor: after 3 completed reviews in a row the top option is a lesson (if any).
- Interleave: no two consecutive options for the same skill; ties by order_index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import db, views
from ..schemas import NextTaskResponse, SessionInfo, Task, TaskOption
from . import engine, scheduler, stats, xp
from .state import unmet_prerequisites

log = logging.getLogger("ua.learning.selector")

MENU_SIZE = 5
LESSON_FLOOR_REVIEWS = 3
REMEDIAL_BONUS = 1.0


@dataclass
class Candidate:
    skill: dict[str, Any]
    type: str  # lesson | review
    importance: float
    reason: str
    xp_value: int
    covers: set[str] = field(default_factory=set)  # due reviews this lesson knocks out
    remedial: bool = False

    def option(self) -> TaskOption:
        return TaskOption(
            skill=views.skill_ref(self.skill), type=self.type, reason=self.reason, xp_value=self.xp_value, importance=round(self.importance, 3)
        )


@dataclass
class _CourseView:
    skills: dict[str, dict[str, Any]]
    progress: dict[str, dict[str, Any]]
    cards: dict[str, Any]
    graph: scheduler.SkillGraph
    question_counts: dict[str, int]


def _load(course_id: str) -> _CourseView:
    skills = {s["id"]: s for s in db.query("SELECT * FROM skills WHERE course_id = ? ORDER BY order_index", (course_id,))}
    progress = {p["skill_id"]: p for p in db.query("SELECT * FROM progress WHERE course_id = ?", (course_id,))}
    cards = {
        sid: scheduler.load_card(p["card_json"])
        for sid, p in progress.items()
        if p["status"] in scheduler.LEARNED and p["card_json"]
    }
    counts = {
        r["skill_id"]: r["n"]
        for r in db.query(
            "SELECT q.skill_id, COUNT(*) AS n FROM questions q JOIN skills s ON s.id = q.skill_id"
            " WHERE s.course_id = ? AND q.flagged = 0 GROUP BY q.skill_id",
            (course_id,),
        )
    }
    return _CourseView(skills, progress, cards, scheduler.load_graph(course_id), counts)


def _keys(view: _CourseView, skill_ids: list[str]) -> str:
    keys = [view.skills[s]["key"] for s in sorted(skill_ids, key=lambda s: view.skills[s]["order_index"])]
    return keys[0] if len(keys) == 1 else ", ".join(keys[:-1]) + " and " + keys[-1]


def _review_candidates(view: _CourseView, now: datetime) -> list[Candidate]:
    out = []
    for sid, card in view.cards.items():
        if sid not in view.skills or not scheduler.is_due(card, now) or not view.question_counts.get(sid):
            continue
        recall = scheduler.retrievability(card, now) or 0.0
        importance = 1 + (1 - recall) * 2 + 0.2 * len(view.graph.dependents.get(sid, []))
        target = scheduler.remedial_target(sid)
        if target and target in view.skills:
            reason = f"Refresh before retrying {view.skills[target]['key']} — estimated recall {round(recall * 100)}%"
            importance += REMEDIAL_BONUS
        else:
            reason = f"Review due — estimated recall {round(recall * 100)}%"
        out.append(Candidate(view.skills[sid], "review", importance, reason, xp.REVIEW_XP_PASS, remedial=bool(target)))
    return out


def _locked(view: _CourseView, skill_id: str) -> bool:
    """Some in-chapter prerequisite is not learned yet (a prerequisite whose lesson failed to generate doesn't gate)."""
    return any(
        src in view.skills
        and view.progress.get(src, {}).get("status") not in scheduler.LEARNED
        and view.skills[src]["lesson_status"] != "failed"
        for src, _ in view.graph.prereqs.get(skill_id, [])
    )


def _lesson_candidates(view: _CourseView, now: datetime) -> list[Candidate]:
    due = {sid for sid, card in view.cards.items() if scheduler.is_due(card, now)}
    out = []
    for sid, skill in view.skills.items():
        p = view.progress.get(sid)
        if not p or p["status"] not in ("available", "in_progress") or skill["lesson_status"] != "ready":
            continue
        if _locked(view, sid):
            continue  # never offer a locked skill, even one started with "Learn anyway" (E2)
        if not view.question_counts.get(sid):
            continue
        cooldown = db.parse_iso(p["cooldown_until"])
        if cooldown and cooldown > scheduler.utc(now):
            continue
        n_desc = len(view.graph.descendants(sid))
        covered = {a: w for a, w in view.graph.down.get(sid, {}).items() if a in due}
        importance = 1 + 0.3 * n_desc + sum(covered.values())
        if db.loads(p["kp_failures_json"], {}):
            reason = "Second try — fresh questions after a pause"
        elif p["status"] == "in_progress":
            reason = "Started before — begins again from the top"
        else:
            reason = "Next new skill"
        if n_desc:
            reason += f" — unlocks {n_desc} other{'s' if n_desc > 1 else ''}"
        if covered:
            reason += f". Also refreshes {_keys(view, list(covered))}, which {'is' if len(covered) == 1 else 'are'} due"
        covers = {a for a, w in covered.items() if w >= scheduler.FIRE_FULL_CREDIT_WEIGHT}
        out.append(Candidate(skill, "lesson", importance, reason, xp.lesson_xp_value(skill["est_minutes"]), covers))
    return out


def _last_completed_types(course_id: str, n: int) -> list[str]:
    rows = db.query(
        "SELECT type FROM tasks WHERE course_id = ? AND status = 'completed' AND outcome != 'abandoned'"
        " ORDER BY completed_at DESC, rowid DESC LIMIT ?",
        (course_id, n),
    )
    return [r["type"] for r in rows]


def _interleave(ranked: list[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    pending = list(ranked)
    while pending:
        pick = next((c for c in pending if not out or c.skill["id"] != out[-1].skill["id"]), pending[0])
        pending.remove(pick)
        out.append(pick)
    return out


def rank(course_id: str, now: datetime, *, exclude_skill: str | None = None) -> list[Candidate]:
    view = _load(course_id)
    lessons = [c for c in _lesson_candidates(view, now) if c.skill["id"] != exclude_skill]
    reviews = [c for c in _review_candidates(view, now) if c.skill["id"] != exclude_skill]

    def order(c: Candidate) -> tuple[float, int, int]:
        return (-c.importance, c.skill["order_index"], 0 if c.type == "review" else 1)

    lessons.sort(key=order)
    recent = _last_completed_types(course_id, LESSON_FLOOR_REVIEWS)
    lesson_first = bool(lessons) and len(recent) == LESSON_FLOOR_REVIEWS and all(t == "review" for t in recent)

    def build_menu(knocked: set[str]) -> list[Candidate]:
        ranked = sorted(lessons + [c for c in reviews if c.remedial or c.skill["id"] not in knocked], key=order)
        if lesson_first:
            ranked.remove(lessons[0])
            ranked.insert(0, lessons[0])
        return _interleave(ranked)[:MENU_SIZE]

    # A due review is dropped only when a lesson that is actually on the menu covers it. Dropping reviews can only
    # pull more lessons onto the menu, so the knocked-out set grows monotonically to a fixed point.
    knocked: set[str] = set()
    while True:
        menu = build_menu(knocked)
        covered = knocked.union(*(c.covers for c in menu if c.type == "lesson"))
        if covered == knocked:
            return menu
        knocked = covered


# ---------------------------------------------------------------------------
# Messages + session
# ---------------------------------------------------------------------------


def _when(dt: datetime, now: datetime) -> str:
    local, today = scheduler.utc(dt).astimezone(), stats.local_date(now)
    clock = local.strftime("%H:%M")
    days = (local.date() - today).days
    if days <= 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if days < 7:
        return f"on {local.strftime('%A')} at {clock}"
    return f"on {local.strftime('%a %d %b')} at {clock}"


def _idle_message(course_id: str, now: datetime) -> str:
    view = _load(course_id)
    if not view.skills:
        return "This course has no skills yet."
    unlearned = [s for sid, s in view.skills.items() if view.progress.get(sid, {}).get("status") not in scheduler.LEARNED]
    if any(s["lesson_status"] == "pending" for s in unlearned):
        return "Lessons are still being generated…"
    cooldowns = [
        db.parse_iso(view.progress[s["id"]]["cooldown_until"])
        for s in unlearned
        if s["id"] in view.progress and view.progress[s["id"]]["cooldown_until"]
    ]
    upcoming = [c for c in cooldowns if c and c > scheduler.utc(now)]
    future_dues = [scheduler.utc(c.due) for c in view.cards.values() if c is not None and scheduler.utc(c.due) > scheduler.utc(now)]
    parts = []
    if upcoming:
        parts.append(f"a paused lesson returns {_when(min(upcoming), now)}")
    if future_dues:
        parts.append(f"next review due {_when(min(future_dues), now)}")
    if parts:
        return "All caught up — " + "; ".join(parts) + "."
    if any(s["lesson_status"] == "failed" for s in unlearned):
        return "Some lessons failed to generate — resume the course build to retry them."
    if unlearned:
        return "Nothing can be studied right now — the remaining lessons have no practice questions."
    return "All caught up — every skill in this chapter is learned."


def session_info(course_id: str, now: datetime) -> SessionInfo:
    goal = stats.get_daily_goal()
    today = stats.xp_today(now)
    return SessionInfo(xp_today=today, daily_goal=goal, goal_reached=today >= goal, due_reviews=stats.due_review_count(course_id, now))


def _require_course(course_id: str) -> None:
    if not db.query_one("SELECT 1 FROM courses WHERE id = ?", (course_id,)):
        raise engine.NotFound("Course not found.")


def next_response(course_id: str, now: datetime) -> NextTaskResponse:
    """The active task (if any) plus the ranked menu. No side effects."""
    _require_course(course_id)
    active = engine.active_task_row(course_id)
    options = rank(course_id, now, exclude_skill=active["skill_id"] if active else None)
    session = session_info(course_id, now)
    if options or active:
        message = "Daily goal reached — a good place to stop (or keep going)." if session.goal_reached else ""
    else:
        message = _idle_message(course_id, now)
    return NextTaskResponse(
        task=engine.build_task(active) if active else None,
        options=[c.option() for c in options],
        session=session,
        message=message,
    )


# ---------------------------------------------------------------------------
# Starting tasks
# ---------------------------------------------------------------------------


def _start(candidate_skill: dict[str, Any], type_: str, reason: str, now: datetime) -> Task:
    starter = engine.start_lesson if type_ == "lesson" else engine.start_review
    return engine.get_task(starter(candidate_skill["id"], reason, now))


def continue_task(course_id: str, now: datetime) -> Task:
    """Resume the active task, or start the top option of the menu."""
    with engine.lock:
        _require_course(course_id)
        active = engine.active_task_row(course_id)
        if active:
            return engine.build_task(active)
        options = rank(course_id, now)
        if not options:
            raise engine.Conflict(_idle_message(course_id, now))
        top = options[0]
        return _start(top.skill, top.type, top.reason, now)


def start_task(
    course_id: str, skill_id: str, type_: str | None, now: datetime, *, replace_active: bool = False, force: bool = False
) -> Task:
    """Manual override: learn or review a chosen skill.

    Everything that could stop the new task is checked BEFORE the active task is touched. Another active task is a
    409 ("Finish or leave … first.") unless `replace_active`, in which case it is abandoned. `force` starts a lesson
    on a locked skill (the learner's override of prerequisite gating); a skill whose lesson was already started that
    way (lesson_attempts > 0) is resumed or restarted without `force` (E2). A lesson paused after a failure (cooldown)
    is a 409 ("This lesson comes back at HH:MM (local time) with fresh questions.") until the cooldown ends, unless
    `force` (the learner's "Start anyway")."""
    with engine.lock:
        _require_course(course_id)
        skill = db.query_one("SELECT * FROM skills WHERE id = ? AND course_id = ?", (skill_id, course_id))
        if not skill:
            raise engine.NotFound("Skill not found in this course.")
        progress = db.query_one("SELECT status, lesson_attempts, cooldown_until FROM progress WHERE skill_id = ?", (skill_id,))
        learned = bool(progress) and progress["status"] in scheduler.LEARNED
        type_ = type_ or ("review" if learned else "lesson")
        active = engine.active_task_row(course_id)
        if active and active["skill_id"] == skill_id and active["type"] == type_:
            return engine.build_task(active)  # resuming the open task never needs `force`
        started_before = bool(progress) and (progress["lesson_attempts"] or 0) > 0
        if type_ == "lesson" and not learned and not force and not started_before:
            blockers = unmet_prerequisites(course_id, skill_id)
            if blockers:
                names = ", ".join(f"{b['key']} ({b['title']})" for b in blockers)
                raise engine.Conflict(f"{skill['key']} is locked — learn {names} first.")
        if type_ == "lesson" and not learned and not force:
            cooldown = db.parse_iso(progress["cooldown_until"]) if progress else None
            if cooldown and cooldown > scheduler.utc(now):
                raise engine.Conflict(engine.cooldown_message(cooldown))
        if type_ == "review" and not learned:
            raise engine.Conflict("Reviews start after the lesson has been passed.")
        initial = engine.prepare_start(skill_id, type_)  # raises Conflict while the active task is still intact
        if active:
            if not replace_active:
                current = db.query_one("SELECT title FROM skills WHERE id = ?", (active["skill_id"],))
                raise engine.Conflict(f"Finish or leave '{current['title'] if current else 'your open task'}' first.")
            engine.abandon(active["id"], now)
        menu = {(c.skill["id"], c.type): c for c in rank(course_id, now)}
        chosen = menu.get((skill_id, type_))
        reason = chosen.reason if chosen else ("Chosen by you" if type_ == "lesson" else "Extra review — chosen by you")
        starter = engine.start_lesson if type_ == "lesson" else engine.start_review
        return engine.get_task(starter(skill_id, reason, now, initial))
