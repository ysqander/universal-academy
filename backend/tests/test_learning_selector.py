"""Task menu: reviews vs lessons, repetition compression, lesson floor, interleaving, continue/start (SPEC §6.6)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_learning_fixtures import T0, build_course, card, days, isolated_engine, learn, right, set_due, status, wrong  # noqa: F401

from app import db
from app.learning import engine, scheduler, selector, stats


def menu(course_id: str, now) -> list[tuple[str, str]]:
    return [(c.skill["key"], c.type) for c in selector.rank(course_id, now)]


def do_review(fx, key: str, now, pattern: str = "111") -> None:
    task_id = engine.start_review(fx.sid(key), "test", now)
    task = engine.get_task(task_id)
    for mark in pattern:
        task = engine.submit_answer(task_id, (right if mark == "1" else wrong)(task.question.id), now).task
    assert task.phase == "done"


def test_fresh_course_offers_root_lessons_in_order() -> None:
    fx = build_course()
    options = selector.rank(fx.course_id, T0)
    assert [(c.skill["key"], c.type) for c in options] == [("S1", "lesson"), ("S2", "lesson")]
    # S1 leads to S3 and S4; S2 too -> equal importance, ties by order_index
    assert options[0].importance == pytest.approx(1 + 0.3 * 2)
    assert options[0].reason == "Next new skill — unlocks 2 others"
    assert options[0].xp_value == 10


def test_compression_lesson_knocks_out_covered_due_review() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    learn(fx, "S2", T0)
    now = T0 + days(30)
    options = selector.rank(fx.course_id, now)
    keys = [(c.skill["key"], c.type) for c in options]
    # S3's lesson practises S1 (w 0.8, due) -> the S1 review is dropped; S2 (w 0.4) still needs its review
    assert keys[0] == ("S3", "lesson")
    assert ("S1", "review") not in keys and ("S2", "review") in keys
    assert "Also refreshes S1 and S2, which are due" in options[0].reason
    assert options[0].importance == pytest.approx(1 + 0.3 * 1 + 0.8 + 0.4)
    # passing the lesson credits S1 implicitly, so its review is no longer due
    task_id = engine.start_lesson(fx.sid("S3"), options[0].reason, now)
    task = engine.begin_practice(task_id, now)
    while task.phase != "done":
        task = engine.submit_answer(task_id, right(task.question.id), now).task
    assert not scheduler.is_due(card(fx.sid("S1")), now)
    assert ("S1", "review") not in menu(fx.course_id, now)


def test_review_is_kept_when_the_covering_lesson_is_not_on_the_menu() -> None:
    fx = build_course(extra_roots=4)
    for key in ("S1", "S2", "R1", "R2", "R3", "R4"):
        learn(fx, key, T0)
    now = T0 + days(3000)  # long overdue: recall has decayed far below 50%
    set_due(fx.sid("S2"), now + days(10))  # S3's lesson then only covers S1: importance 1 + 0.3 + 0.8 = 2.1
    recall = scheduler.retrievability(card(fx.sid("R1")), now)
    assert 1 + (1 - recall) * 2 > 2.1  # every overdue review outranks the S3 lesson
    options = selector.rank(fx.course_id, now)
    keys = [(c.skill["key"], c.type) for c in options]
    assert len(keys) == 5 and ("S3", "lesson") not in keys
    assert ("S1", "review") in keys  # nothing on the menu knocks it out


def test_due_reviews_rank_by_forgetting() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3", "S4"):
        learn(fx, key, T0)
    set_due(fx.sid("S2"), T0 + days(1))
    set_due(fx.sid("S4"), T0 + days(1))
    now = T0 + days(20)
    options = selector.rank(fx.course_id, now)
    assert {(c.skill["key"], c.type) for c in options} >= {("S2", "review"), ("S4", "review")}
    for c in options:
        recall = scheduler.retrievability(card(c.skill["id"]), now)
        deps = 1 if c.skill["key"] in ("S1", "S2", "S3") else 0
        assert c.importance == pytest.approx(1 + (1 - recall) * 2 + 0.2 * deps)
        assert c.reason == f"Review due — estimated recall {round(recall * 100)}%"
    assert [c.importance for c in options] == sorted((c.importance for c in options), reverse=True)


def test_lesson_floor_after_three_reviews() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3"):
        learn(fx, key, T0)
    now = T0 + days(30)
    for key in ("S1", "S2", "S3"):
        do_review(fx, key, now)
    set_due(fx.sid("S2"), now)  # due again, but S2 is not encompassed by S4 (w 0.24 < 0.3)
    later = now + timedelta(hours=1)
    review_importance = next(c.importance for c in selector.rank(fx.course_id, later) if c.type == "review")
    lesson = next(c for c in selector.rank(fx.course_id, later) if c.type == "lesson")
    assert review_importance > lesson.importance  # the review would win on importance alone ...
    assert menu(fx.course_id, later)[0] == ("S4", "lesson")  # ... but three reviews in a row force a lesson


def test_interleave_never_repeats_a_skill_back_to_back() -> None:
    s1, s2 = {"id": "a", "order_index": 1}, {"id": "b", "order_index": 2}
    ranked = [
        selector.Candidate(s1, "review", 3, "", 4),
        selector.Candidate(s1, "lesson", 2.9, "", 10),
        selector.Candidate(s2, "review", 2, "", 4),
    ]
    out = selector._interleave(ranked)
    assert [(c.skill["id"], c.type) for c in out] == [("a", "review"), ("b", "review"), ("a", "lesson")]


def test_cooldown_hides_failed_lesson() -> None:
    fx = build_course()
    task_id = engine.start_lesson(fx.sid("S1"), "x", T0)
    task = engine.begin_practice(task_id, T0)
    while task.phase != "done":
        task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert menu(fx.course_id, T0 + timedelta(minutes=5)) == [("S2", "lesson")]
    later = menu(fx.course_id, T0 + timedelta(hours=3))
    assert ("S1", "lesson") in later
    retry = next(c for c in selector.rank(fx.course_id, T0 + timedelta(hours=3)) if c.skill["key"] == "S1")
    assert retry.reason.startswith("Second try")


def test_manual_start_during_the_cooldown_is_refused_unless_forced() -> None:
    fx = build_course()
    task_id = engine.start_lesson(fx.sid("S1"), "x", T0)
    task = engine.begin_practice(task_id, T0)
    while task.phase != "done":
        task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    until = T0 + timedelta(hours=2)
    message = f"This lesson comes back at {engine.local_clock(until)} (local time) with fresh questions."
    assert message.startswith("This lesson comes back at ") and engine.local_clock(until)[2] == ":"
    for type_ in (None, "lesson"):
        with pytest.raises(engine.Conflict) as exc:
            selector.start_task(fx.course_id, fx.sid("S1"), type_, T0 + timedelta(minutes=5))
        assert str(exc.value) == message
    attempts = lambda: db.query_one("SELECT lesson_attempts FROM progress WHERE skill_id = ?", (fx.sid("S1"),))["lesson_attempts"]  # noqa: E731
    assert attempts() == 1 and not engine.active_task_row(fx.course_id)  # nothing started
    other = selector.start_task(fx.course_id, fx.sid("S2"), None, T0 + timedelta(minutes=6))
    with pytest.raises(engine.Conflict, match="comes back at"):  # checked before the open task is touched
        selector.start_task(fx.course_id, fx.sid("S1"), None, T0 + timedelta(minutes=7), replace_active=True)
    assert engine.get_task(other.id).outcome is None
    forced = selector.start_task(fx.course_id, fx.sid("S1"), None, T0 + timedelta(minutes=8), replace_active=True, force=True)
    assert forced.type == "lesson" and forced.skill.key == "S1" and forced.id != task_id and attempts() == 2
    engine.abandon(forced.id, T0 + timedelta(minutes=9))
    after = selector.start_task(fx.course_id, fx.sid("S1"), None, until + timedelta(minutes=1))
    assert after.type == "lesson" and after.skill.key == "S1"


def test_cooldown_message_uses_local_time() -> None:
    until = T0 + timedelta(hours=2)
    assert engine.cooldown_message(until) == (
        f"This lesson comes back at {until.astimezone().strftime('%H:%M')} (local time) with fresh questions."
    )


def test_next_response_has_no_side_effects_and_reports_active_task() -> None:
    fx = build_course()
    response = selector.next_response(fx.course_id, T0)
    assert response.task is None and len(response.options) == 2 and response.message == ""
    assert response.session.daily_goal == 30 and response.session.xp_today == 0 and not response.session.goal_reached
    assert not db.query("SELECT 1 FROM tasks")
    task = selector.continue_task(fx.course_id, T0)
    assert task.skill.key == "S1" and task.type == "lesson" and task.reason.startswith("Next new skill")
    response = selector.next_response(fx.course_id, T0)
    assert response.task is not None and response.task.id == task.id
    assert [o.skill.key for o in response.options] == ["S2"]  # the active task's skill is not repeated
    assert selector.continue_task(fx.course_id, T0).id == task.id  # resume, don't start another


def test_messages_when_nothing_to_do() -> None:
    pending = build_course(lesson_status="pending")
    response = selector.next_response(pending.course_id, T0)
    assert response.options == [] and response.message == "Lessons are still being generated…"
    with pytest.raises(engine.Conflict, match="still being generated"):
        selector.continue_task(pending.course_id, T0)

    fx = build_course()
    for key in ("S1", "S2", "S3", "S4"):
        learn(fx, key, T0)
    message = selector.next_response(fx.course_id, T0 + timedelta(minutes=1)).message
    assert message.startswith("All caught up — next review due ")


def test_message_when_lesson_generation_failed() -> None:
    fx = build_course()
    db.execute("UPDATE skills SET lesson_status = 'failed' WHERE course_id = ?", (fx.course_id,))
    assert selector.next_response(fx.course_id, T0).message.startswith("Some lessons failed to generate")


def test_goal_reached_message() -> None:
    fx = build_course()
    stats.set_daily_goal(5)
    learn(fx, "S1", T0)
    response = selector.next_response(fx.course_id, T0 + timedelta(minutes=1))
    assert response.session.goal_reached and response.session.xp_today == 12
    assert response.message.startswith("Daily goal reached")


def test_start_task_manual_override() -> None:
    fx = build_course()
    with pytest.raises(engine.Conflict, match=r"S3 is locked — learn S1 \(Skill S1\), S2 \(Skill S2\) first"):
        selector.start_task(fx.course_id, fx.sid("S3"), None, T0)
    with pytest.raises(engine.Conflict, match="after the lesson"):
        selector.start_task(fx.course_id, fx.sid("S1"), "review", T0)
    with pytest.raises(engine.NotFound):
        selector.start_task(fx.course_id, "missing", None, T0)
    with pytest.raises(engine.NotFound):
        selector.start_task("missing", fx.sid("S1"), None, T0)
    first = selector.start_task(fx.course_id, fx.sid("S2"), None, T0)
    assert first.type == "lesson" and first.reason.startswith("Next new skill")
    assert selector.start_task(fx.course_id, fx.sid("S2"), "lesson", T0).id == first.id  # idempotent
    with pytest.raises(engine.Conflict, match=r"^Finish or leave 'Skill S2' first\.$"):
        selector.start_task(fx.course_id, fx.sid("S1"), None, T0)
    assert engine.get_task(first.id).outcome is None  # still open
    second = selector.start_task(fx.course_id, fx.sid("S1"), None, T0, replace_active=True)
    assert engine.get_task(first.id).outcome == "abandoned"
    assert second.skill.key == "S1"
    learn(fx, "S2", T0)
    review = selector.start_task(fx.course_id, fx.sid("S2"), None, T0 + days(1), replace_active=True)
    assert review.type == "review" and review.reason == "Extra review — chosen by you"


def test_manual_start_validates_before_abandoning_the_open_task() -> None:
    fx = build_course()
    learn(fx, "S2", T0)
    task_id = engine.start_lesson(fx.sid("S1"), "test", T0)
    engine.begin_practice(task_id, T0)
    engine.submit_answer(task_id, right(fx.questions["S1"][0][0]), T0)
    db.execute("UPDATE questions SET flagged = 1 WHERE skill_id = ?", (fx.sid("S2"),))  # nothing to review
    with pytest.raises(engine.Conflict, match="no review questions"):
        selector.start_task(fx.course_id, fx.sid("S2"), "review", T0, replace_active=True)
    db.execute("UPDATE skills SET lesson_status = 'pending' WHERE id = ?", (fx.sid("S4"),))
    with pytest.raises(engine.Conflict, match="still being generated"):
        selector.start_task(fx.course_id, fx.sid("S4"), "lesson", T0, replace_active=True, force=True)
    task = engine.get_task(task_id)
    assert task.outcome is None and task.phase == "practice" and task.progress.streak == 1  # untouched


def test_force_starts_a_locked_lesson() -> None:
    fx = build_course()
    with pytest.raises(engine.Conflict, match="locked"):
        selector.start_task(fx.course_id, fx.sid("S3"), "lesson", T0)
    task = selector.start_task(fx.course_id, fx.sid("S3"), "lesson", T0, force=True)
    assert task.type == "lesson" and task.skill.key == "S3" and task.reason == "Chosen by you"
    task = engine.begin_practice(task.id, T0)
    while task.phase != "done":
        task = engine.submit_answer(task.id, right(task.question.id), T0).task
    assert task.outcome == "passed" and status(fx.sid("S3")) == "learned"


def test_stale_locked_status_does_not_block_manual_start() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    learn(fx, "S2", T0)
    db.execute("UPDATE progress SET status = 'locked' WHERE skill_id = ?", (fx.sid("S3"),))
    task = selector.start_task(fx.course_id, fx.sid("S3"), None, T0)
    assert task.type == "lesson" and task.skill.key == "S3"


def test_learn_anyway_skill_stays_locked_off_the_menu_and_resumes_without_force() -> None:
    """E2: a lesson started with force on a locked skill can be resumed or restarted without force; the menu never
    offers a locked skill."""
    fx = build_course()
    task = selector.start_task(fx.course_id, fx.sid("S3"), "lesson", T0, force=True)
    assert status(fx.sid("S3")) == "locked"  # still locked: S1 and S2 are not learned
    # the drawer's / menu's buttons don't send force for it any more: resuming the open task works
    assert selector.start_task(fx.course_id, fx.sid("S3"), "lesson", T0).id == task.id
    assert selector.start_task(fx.course_id, fx.sid("S3"), None, T0).id == task.id
    assert selector.continue_task(fx.course_id, T0).id == task.id
    engine.abandon(task.id, T0)
    assert ("S3", "lesson") not in menu(fx.course_id, T0)
    assert selector.continue_task(fx.course_id, T0).skill.key == "S1"  # the top option, never the locked skill
    restarted = selector.start_task(fx.course_id, fx.sid("S3"), None, T0 + timedelta(minutes=1), replace_active=True)
    assert restarted.type == "lesson" and restarted.skill.key == "S3" and restarted.id != task.id
    assert status(fx.sid("S3")) == "locked"
    # a skill never started still needs force
    with pytest.raises(engine.Conflict, match="S4 is locked"):
        selector.start_task(fx.course_id, fx.sid("S4"), "lesson", T0, replace_active=True)


def test_menu_never_offers_a_locked_skill_even_with_a_stale_status() -> None:
    fx = build_course()
    db.execute("UPDATE progress SET status = 'in_progress', lesson_attempts = 1 WHERE skill_id = ?", (fx.sid("S3"),))
    assert ("S3", "lesson") not in menu(fx.course_id, T0)
    learn(fx, "S1", T0)
    learn(fx, "S2", T0)
    assert ("S3", "lesson") in menu(fx.course_id, T0)
