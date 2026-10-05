"""Course stats, XP today, daily goal and streaks in the machine's local timezone (SPEC §6.6)."""

from __future__ import annotations

from datetime import timedelta

from test_learning_fixtures import T0, build_course, card, days, isolated_engine, learn  # noqa: F401

from app import db
from app.learning import scheduler, stats, xp


def test_stats_counts_and_next_review() -> None:
    fx = build_course()
    empty = stats.compute_stats(fx.course_id, T0)
    assert (empty.total_skills, empty.available, empty.locked, empty.learned, empty.lessons_ready) == (4, 2, 2, 0, 4)
    assert empty.next_review_at is None and empty.xp_total == 0 and empty.daily_goal == 30
    learn(fx, "S1", T0)
    learn(fx, "S2", T0 + timedelta(hours=1))
    s = stats.compute_stats(fx.course_id, T0 + timedelta(hours=2))
    assert (s.learned, s.available, s.locked, s.due_reviews) == (2, 1, 1, 0)
    assert s.xp_today == 24 and s.xp_total == 24
    assert s.next_review_at == scheduler.iso(card(fx.sid("S1")).due)
    later = stats.compute_stats(fx.course_id, T0 + days(30))
    assert later.due_reviews == 2 and later.xp_today == 0 and later.xp_total == 24


def test_daily_goal_setting() -> None:
    assert stats.get_daily_goal() == 30
    stats.set_daily_goal(45)
    assert stats.get_daily_goal() == 45
    db.kv_set(stats.DAILY_GOAL_KEY, "garbage")
    assert stats.get_daily_goal() == 30


def test_streak_counts_consecutive_local_days() -> None:
    fx = build_course()
    for d in (0, 1, 2):
        xp.award(fx.course_id, f"t{d}", 5, T0 + days(d))
    assert stats.streak_days(T0 + days(2)) == 3
    assert stats.streak_days(T0 + days(3)) == 3  # nothing yet today: the streak survives until tonight
    assert stats.streak_days(T0 + days(4)) == 0
    xp.award(fx.course_id, "t5", 5, T0 + days(5))
    assert stats.streak_days(T0 + days(5)) == 1
    assert stats.compute_stats(fx.course_id, T0 + days(5)).streak_days == 1


def test_xp_today_per_course_and_across_courses() -> None:
    a, b = build_course(), build_course()
    xp.award(a.course_id, "t1", 7, T0)
    xp.award(b.course_id, "t2", 3, T0 + timedelta(minutes=1))
    now = T0 + timedelta(minutes=2)
    assert stats.xp_today(now, a.course_id) == 7
    assert stats.xp_today(now) == 10
    assert stats.compute_stats(a.course_id, now).xp_today == 7
    assert stats.xp_today(T0 + days(1)) == 0


def test_xp_rules() -> None:
    assert xp.lesson_xp(1, 0) == 3 + 2 and xp.lesson_xp(50, 1) == 20 and xp.lesson_xp(12, 3) == 12
    assert xp.review_xp(1.0) == 5 and xp.review_xp(0.73) == 4 and xp.review_xp(0.47) == 1
