"""Course statistics + daily goal (SPEC §6.6).

Public interface (stable): compute_stats(course_id, now=None) -> CourseStats

Quiz fields (SPEC §6.7) come from learning/quiz.py: quiz_due, xp_until_quiz (None until enough skills are learned),
last_quiz_score (the last finished quiz or retake). compute_stats first finishes an active quiz whose time ran out
(its missed skills become due reviews and it is no longer due) — the library and the course header read stats
before /next.

"Today" and streaks use the machine's local timezone. XP today in CourseStats is per course; the daily goal
and the streak belong to the learner, so they count XP across all courses (session_xp_today, streak_days).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .. import db
from ..schemas import CourseStats
from . import quiz, scheduler

DEFAULT_DAILY_GOAL = 30
DAILY_GOAL_KEY = "daily_goal"


def local_date(dt: datetime) -> date:
    return scheduler.utc(dt).astimezone().date()


def get_daily_goal() -> int:
    raw = db.kv_get(DAILY_GOAL_KEY)
    try:
        value = int(raw) if raw is not None else DEFAULT_DAILY_GOAL
    except ValueError:
        return DEFAULT_DAILY_GOAL
    return value if value > 0 else DEFAULT_DAILY_GOAL


def set_daily_goal(value: int) -> int:
    db.kv_set(DAILY_GOAL_KEY, str(int(value)))
    return int(value)


def _xp_by_day(course_id: str | None) -> dict[date, int]:
    sql = "SELECT xp, created_at FROM xp_log" + (" WHERE course_id = ?" if course_id else "")
    totals: dict[date, int] = {}
    for r in db.query(sql, (course_id,) if course_id else ()):
        day = local_date(db.parse_iso(r["created_at"]))
        totals[day] = totals.get(day, 0) + r["xp"]
    return totals


def xp_today(now: datetime, course_id: str | None = None) -> int:
    return _xp_by_day(course_id).get(local_date(now), 0)


def streak_days(now: datetime) -> int:
    """Consecutive local days with XP > 0, ending today (or yesterday, so the streak survives until tonight)."""
    active = {d for d, xp in _xp_by_day(None).items() if xp > 0}
    day = local_date(now)
    if day not in active:
        day -= timedelta(days=1)
    count = 0
    while day in active:
        count += 1
        day -= timedelta(days=1)
    return count


def due_review_count(course_id: str, now: datetime) -> int:
    rows = db.query(
        "SELECT card_json FROM progress WHERE course_id = ? AND status IN ('learned','mastered') AND card_json IS NOT NULL",
        (course_id,),
    )
    return sum(1 for r in rows if scheduler.is_due(scheduler.load_card(r["card_json"]), now))


def compute_stats(course_id: str, now: datetime | None = None) -> CourseStats:
    now = now or datetime.now(timezone.utc)
    quiz.expire_active(course_id, now)  # a quiz whose time ran out is finished first: it changes due reviews + quiz_due
    rows =db.query("SELECT status, card_json FROM progress WHERE course_id = ?", (course_id,))
    counts = db.query_one(
        "SELECT COUNT(*) AS total, SUM(lesson_status = 'ready') AS ready FROM skills WHERE course_id = ?", (course_id,)
    ) or {"total": 0, "ready": 0}
    xp_total = db.query_one("SELECT COALESCE(SUM(xp), 0) AS n FROM xp_log WHERE course_id = ?", (course_id,))["n"]
    cards = [
        scheduler.load_card(r["card_json"]) for r in rows if r["status"] in ("learned", "mastered") and r["card_json"]
    ]
    dues = [scheduler.utc(c.due) for c in cards if c is not None]

    def count(*statuses: str) -> int:
        return sum(1 for r in rows if r["status"] in statuses)

    quiz_state = quiz.quiz_status(course_id, now)

    return CourseStats(
        total_skills=counts["total"] or 0,
        learned=count("learned", "mastered"),
        mastered=count("mastered"),
        available=count("available"),
        in_progress=count("in_progress"),
        locked=count("locked"),
        due_reviews=sum(1 for d in dues if d <= scheduler.utc(now)),
        lessons_ready=counts["ready"] or 0,
        xp_today=xp_today(now, course_id),
        xp_total=xp_total,
        daily_goal=get_daily_goal(),
        streak_days=streak_days(now),
        next_review_at=scheduler.iso(min(dues)) if dues else None,
        quiz_due=quiz_state.due,
        xp_until_quiz=quiz_state.xp_until,
        last_quiz_score=quiz_state.last_score,
    )
