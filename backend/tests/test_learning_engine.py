"""Task engine: unlocking, lesson passing/failing, remediation, easier step, flags, reviews, XP (SPEC §6.2-§6.4)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_learning_fixtures import (  # noqa: F401
    T0,
    build_course,
    card,
    days,
    isolated_engine,
    learn,
    question,
    right,
    set_due,
    status,
    wrong,
)

from app import db
from app.learning import engine, scheduler, selector, state
from app.learning.engine import request_top_up as real_request_top_up  # captured before the fixture stubs it
from app.schemas import AnswerSubmit


def start_practice(fx, key: str, now=T0):
    task_id = engine.start_lesson(fx.sid(key), "test", now)
    return task_id, engine.begin_practice(task_id, now)


def xp_total(course_id: str) -> int:
    return db.query_one("SELECT COALESCE(SUM(xp), 0) AS n FROM xp_log WHERE course_id = ?", (course_id,))["n"]


# ---------------------------------------------------------------------------
# Unlocking
# ---------------------------------------------------------------------------


def test_init_progress_and_unlocking() -> None:
    fx = build_course()
    assert [status(fx.sid(k)) for k in ("S1", "S2", "S3", "S4")] == ["available", "available", "locked", "locked"]
    state.init_progress(fx.course_id)  # idempotent
    learn(fx, "S1", T0)
    assert status(fx.sid("S3")) == "locked"  # S2 still missing
    learn(fx, "S2", T0)
    assert status(fx.sid("S3")) == "available"
    info = state.skill_states(fx.course_id, T0)
    assert info[fx.sid("S1")].status == "learned"
    assert info[fx.sid("S1")].due is False and info[fx.sid("S1")].retrievability == pytest.approx(1.0)
    assert info[fx.sid("S3")].retrievability is None
    later = state.skill_states(fx.course_id, T0 + days(30))
    assert later[fx.sid("S1")].due is True


def test_failed_lesson_generation_does_not_gate_dependents() -> None:
    fx = build_course()
    db.execute("UPDATE skills SET lesson_status = 'failed' WHERE id = ?", (fx.sid("S2"),))
    learn(fx, "S1", T0)
    assert status(fx.sid("S3")) == "available"


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------


def test_lesson_phases_and_pass() -> None:
    fx = build_course(est_minutes=12)
    task_id = engine.start_lesson(fx.sid("S1"), "Next new skill", T0)
    task = engine.get_task(task_id)
    assert task.phase == "learn" and task.question is None and task.lesson is not None
    assert len(task.lesson.knowledge_points) == 2 and task.xp_value == 12
    assert status(fx.sid("S1")) == "in_progress"
    with pytest.raises(engine.Conflict):
        engine.submit_answer(task_id, right(fx.questions["S1"][0][0]), T0)
    task = engine.begin_practice(task_id, T0)
    assert task.phase == "practice" and task.question.id == fx.questions["S1"][0][0]
    assert engine.begin_practice(task_id, T0).question.id == task.question.id  # idempotent
    r1 = engine.submit_answer(task_id, right(task.question.id), T0)
    assert r1.correct and r1.independent and r1.task.progress.streak == 1 and r1.task.progress.kp_index == 0
    assert r1.correct_answer and r1.solution
    r2 = engine.submit_answer(task_id, right(r1.task.question.id), T0)
    assert r2.task.progress.kp_index == 1 and r2.task.progress.streak == 0  # KP 1 passed with 2 in a row
    assert r2.task.question.kp_index == 1
    r3 = engine.submit_answer(task_id, right(r2.task.question.id), T0)
    r4 = engine.submit_answer(task_id, right(r3.task.question.id), T0)
    assert r4.task.phase == "done" and r4.task.outcome == "passed"
    assert r4.xp_awarded == 12 + 2  # est_minutes + no-mistakes bonus
    assert xp_total(fx.course_id) == 14
    assert status(fx.sid("S1")) == "learned"
    assert card(fx.sid("S1")) is not None
    assert r4.task.progress.answered == 4 and r4.task.progress.correct == 4


def test_lesson_xp_is_clamped_and_bonus_needs_no_mistakes() -> None:
    fx = build_course(est_minutes=45)
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    while task.phase != "done":
        task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.outcome == "passed" and task.xp_value == 20  # clamped, no bonus after a wrong answer


def test_hints_and_reveals_do_not_count_toward_the_streak() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    hinted = engine.submit_answer(task_id, right(task.question.id, hints_used=1), T0)
    assert hinted.correct and not hinted.independent and hinted.task.progress.streak == 0
    assert hinted.suggestion is None  # assisted is not a miss
    qid = hinted.task.question.id
    revealed = engine.reveal(qid, T0)
    assert revealed.solution and revealed.correct_answer
    after_reveal = engine.submit_answer(task_id, right(qid), T0)
    assert after_reveal.correct and not after_reveal.independent and after_reveal.task.progress.streak == 0
    ok = engine.submit_answer(task_id, right(after_reveal.task.question.id), T0)
    assert ok.independent and ok.task.progress.streak == 1
    attempts = db.query("SELECT independent, revealed, hints_used FROM attempts WHERE task_id = ? ORDER BY rowid", (task_id,))
    assert [(a["independent"], a["revealed"], a["hints_used"]) for a in attempts] == [(0, 0, 1), (0, 1, 0), (1, 0, 0)]


def test_invalid_input_is_not_counted() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    with pytest.raises(engine.Invalid):
        engine.submit_answer(task_id, AnswerSubmit(question_id=task.question.id, response="2(("), T0)
    with pytest.raises(engine.Conflict):
        engine.submit_answer(task_id, right(fx.questions["S1"][1][0]), T0)  # not the current question
    assert not db.query("SELECT 1 FROM attempts WHERE task_id = ?", (task_id,))
    assert engine.get_task(task_id).question.id == task.question.id


def _fail_kp0(fx, key="S1", now=T0):
    task_id, task = start_practice(fx, key, now)
    result = None
    while task.phase != "done":
        result = engine.submit_answer(task_id, wrong(task.question.id), now)
        task = result.task
    return task_id, result


def test_kp_failure_halts_lesson_with_cooldown() -> None:
    fx = build_course()
    task_id, result = _fail_kp0(fx)
    assert result.task.outcome == "failed" and result.xp_awarded == 0
    assert result.suggestion == "retry_later" and "2 hours" in result.suggestion_text
    # 4-question pool: after 3 misses two-in-a-row is impossible, so the lesson stops early
    assert result.task.progress.answered == 3
    progress = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert db.loads(progress["kp_failures_json"]) == {"0": 1}
    assert db.parse_iso(progress["cooldown_until"]) == T0 + timedelta(hours=2)
    assert progress["status"] == "in_progress"
    assert xp_total(fx.course_id) == 0
    # the selector re-offers it only after the cooldown
    offered = lambda now: {(c.skill["key"], c.type) for c in selector.rank(fx.course_id, now)}  # noqa: E731
    assert ("S1", "lesson") not in offered(T0 + timedelta(hours=1))
    assert ("S1", "lesson") in offered(T0 + timedelta(hours=2, minutes=1))


def test_retry_prefers_unseen_questions() -> None:
    fx = build_course()
    _fail_kp0(fx)
    _, task = start_practice(fx, "S1", T0 + timedelta(hours=3))
    assert task.question.id == fx.questions["S1"][0][3]  # the only KP-1 question not answered before


def test_second_failure_at_same_kp_triggers_remediation() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    learn(fx, "S2", T0)
    now = T0 + days(1)
    assert not scheduler.is_due(card(fx.sid("S1")), now)
    _, first = _fail_kp0(fx, "S3", now)
    assert first.suggestion == "retry_later"
    later = now + timedelta(hours=3)
    _, second = _fail_kp0(fx, "S3", later)
    assert second.suggestion == "review_prerequisite"
    assert "S1" in second.suggestion_text and "S2" in second.suggestion_text
    assert scheduler.is_due(card(fx.sid("S1")), later) and scheduler.is_due(card(fx.sid("S2")), later)
    menu = selector.rank(fx.course_id, later)
    assert [(c.skill["key"], c.type) for c in menu[:2]] == [("S1", "review"), ("S2", "review")]
    assert menu[0].reason.startswith("Refresh before retrying S3")


def test_easier_step_suggestion_and_swap() -> None:
    fx = build_course()
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (fx.questions["S1"][0][3],))
    task_id, task = start_practice(fx, "S1")
    first = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert first.suggestion is None
    second = engine.submit_answer(task_id, wrong(first.task.question.id), T0)
    assert second.task.phase == "practice"  # still passable: two questions left
    assert second.suggestion == "easier_step"
    current = second.task.question
    assert current.tier == 2
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.id == fx.questions["S1"][0][3] and swapped.question.tier == 1
    assert swapped.progress.kp_questions_used == 3  # the swapped-in question replaces the skipped one
    with pytest.raises(engine.Conflict):
        engine.easier_step(task_id, T0)  # nothing unseen left in KP 1 (no previous KP)
    # the skipped question is never served again in this task
    nxt = engine.submit_answer(task_id, right(swapped.question.id), T0).task
    assert nxt.phase == "done" and nxt.outcome == "failed"  # 1 in a row, pool exhausted


def test_no_easier_question_suggests_rereading() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    first = engine.submit_answer(task_id, wrong(task.question.id), T0)
    second = engine.submit_answer(task_id, wrong(first.task.question.id), T0)
    assert second.task.question.tier == 2  # the only unseen one left is tier 3: harder, not easier
    assert second.suggestion == "reread_example"
    with pytest.raises(engine.Conflict):
        engine.easier_step(task_id, T0)


def test_easier_step_can_use_previous_kp() -> None:
    fx = build_course()
    kp0, kp1 = fx.questions["S1"]
    for qid, tier in [(kp1[0], 2), (kp1[1], 3), (kp1[2], 3), (kp1[3], 3), (kp0[3], 1)]:
        db.execute("UPDATE questions SET tier = ? WHERE id = ?", (tier, qid))
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.kp_index == 1 and task.question.tier == 2
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.id == kp0[3]  # lowest unseen tier across KP 1 and KP 2
    reviews = engine.start_review  # reviews have no easier step
    learn(fx, "S2", T0)
    engine.abandon(task_id, T0)
    review_id = reviews(fx.sid("S2"), "test", T0 + days(5))
    with pytest.raises(engine.Conflict):
        engine.easier_step(review_id, T0 + days(5))


def test_flag_current_question_serves_replacement() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    flagged_id = task.question.id
    after = engine.flag(flagged_id, "answer key looks wrong", T0)
    assert after.question.id != flagged_id
    assert question(flagged_id)["flagged"] == 1
    assert after.progress.kp_questions_available == 3 and after.progress.kp_questions_used == 1


def test_flag_voids_a_wrong_answer_and_restores_the_streak() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    bad = task.question.id
    task = engine.submit_answer(task_id, wrong(bad), T0).task
    assert task.progress.streak == 0
    after = engine.flag(bad, "", T0)
    # the voided miss no longer breaks the streak; with 1 in a row the current question is still KP 1
    assert after.progress.streak == 1 and after.progress.answered == 1
    assert db.query_one("SELECT status FROM attempts WHERE question_id = ?", (bad,))["status"] == "voided"
    final = engine.submit_answer(task_id, right(after.question.id), T0)
    assert final.task.progress.kp_index == 1  # KP passed: ✔ (voided ✖) ✔


def test_flag_reopens_a_lesson_failed_because_of_that_question() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    last = task.question.id
    failed = engine.submit_answer(task_id, wrong(last), T0)
    assert failed.task.outcome == "failed"
    reopened = engine.flag(last, "the expected answer is wrong", T0)
    assert reopened.phase == "practice" and reopened.outcome is None
    assert reopened.question is not None and reopened.progress.streak == 1
    progress = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert db.loads(progress["kp_failures_json"]) == {} and progress["cooldown_until"] is None


def test_flag_requires_a_served_question() -> None:
    fx = build_course()
    with pytest.raises(engine.Conflict):
        engine.flag(fx.questions["S1"][0][0], "", T0)
    with pytest.raises(engine.NotFound):
        engine.flag("nope", "", T0)


def test_free_response_self_grading() -> None:
    fx = build_course()
    qid = fx.questions["S1"][0][0]
    db.execute("UPDATE questions SET kind = 'free_response', answer = '', checklist_json = '[\"states the definition\"]' WHERE id = ?", (qid,))
    task_id, task = start_practice(fx, "S1")
    assert task.question.kind == "free_response"
    with pytest.raises(engine.Invalid):
        engine.submit_answer(task_id, AnswerSubmit(question_id=qid, response="proof"), T0)
    engine.reveal(qid, T0)  # seeing the model solution is part of self-assessment
    partial = engine.submit_answer(task_id, AnswerSubmit(question_id=qid, response="proof", self_grade="partial"), T0)
    assert partial.status == "self_graded" and not partial.correct and partial.task.progress.streak == 0
    nxt = partial.task.question.id
    good = engine.submit_answer(task_id, right(nxt), T0)
    assert good.independent


def test_abandon() -> None:
    fx = build_course()
    task_id, _ = start_practice(fx, "S1")
    task = engine.abandon(task_id, T0)
    assert task.phase == "done" and task.outcome == "abandoned" and task.question is None
    assert engine.abandon(task_id, T0).outcome == "abandoned"  # idempotent
    with pytest.raises(engine.Conflict):
        engine.begin_practice(task_id, T0)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


def _review(fx, key: str, now, pattern: str):
    task_id = engine.start_review(fx.sid(key), "Review due", now)
    task = engine.get_task(task_id)
    assert task.phase == "practice" and task.progress.total_questions == 3
    result = None
    for mark in pattern:
        result = engine.submit_answer(task_id, (right if mark == "1" else wrong)(task.question.id), now)
        task = result.task
    return task_id, result


def test_review_set_mixes_kps_and_prefers_unseen() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    task_id = engine.start_review(fx.sid("S1"), "Review due", T0 + days(3))
    st = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])
    kps = [question(q)["kp_index"] for q in st["set"]]
    assert sorted(set(kps)) == [0, 1] and len(st["set"]) == 3
    seen = {a["question_id"] for a in db.query("SELECT question_id FROM attempts WHERE skill_id = ?", (fx.sid("S1"),))}
    assert not seen & set(st["set"])


@pytest.mark.parametrize(
    "pattern,outcome,xp_awarded,rating",
    [("111", "passed", 5, 3), ("011", "passed", 4, 2), ("110", "failed", 1, 1), ("010", "failed", 1, 1)],
)
def test_review_scoring_rating_and_xp(pattern: str, outcome: str, xp_awarded: int, rating: int) -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    before = card(fx.sid("S1"))
    task_id, result = _review(fx, "S1", now, pattern)
    assert result.task.outcome == outcome and result.xp_awarded == xp_awarded
    log_row = db.query_one("SELECT * FROM review_log WHERE skill_id = ? AND implicit = 0 ORDER BY created_at DESC", (fx.sid("S1"),))
    assert log_row["rating"] == rating
    after = card(fx.sid("S1"))
    assert scheduler.utc(after.last_review) == now and after.due != before.due
    progress = db.query_one("SELECT reviews, lapses FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert progress["reviews"] == 1 and progress["lapses"] == int(rating == 1)
    if outcome == "failed":
        assert result.suggestion == "reread_example"


def test_review_hint_gives_half_credit_and_reveal_none() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    task = engine.get_task(task_id)
    first = engine.submit_answer(task_id, right(task.question.id), now)
    hinted = engine.submit_answer(task_id, right(first.task.question.id, hints_used=2), now)
    assert hinted.correct and not hinted.independent and "half credit" in hinted.feedback
    engine.reveal(hinted.task.question.id, now)
    last = engine.submit_answer(task_id, right(hinted.task.question.id), now)
    assert "no credit" in last.feedback
    # credits 1, 0.5, 0 -> (1 + 1 + 0) / 6 = 0.33 -> Again
    assert last.task.outcome == "failed" and last.xp_awarded == 1


def test_failed_review_penalizes_dependents_and_success_credits_ancestors() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3"):
        learn(fx, key, T0)
    set_due(fx.sid("S3"), T0 + days(30))
    now = T0 + days(3)
    _review(fx, "S1", now, "000")
    assert scheduler.utc(card(fx.sid("S3")).due) == now + days(1)  # penalty flows up (w = 0.8)
    later = now + days(2)
    set_due(fx.sid("S1"), later + days(1))
    _review(fx, "S3", later, "111")
    s1 = card(fx.sid("S1"))
    assert scheduler.utc(s1.last_review) == later  # credit flows down (w = 0.8, due within 3 days)


def test_flag_in_active_review_replaces_question() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    task = engine.submit_answer(task_id, wrong(engine.get_task(task_id).question.id), now).task
    first_q = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])["set"][0]
    task = engine.flag(first_q, "ambiguous", now)
    assert task.progress.answered == 0 and task.progress.total_questions == 3
    for _ in range(3):
        task = engine.submit_answer(task_id, right(task.question.id), now).task
    assert task.outcome == "passed" and task.xp_value == 5  # the voided miss never counted


def test_flag_after_finished_review_rescores_it() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id, result = _review(fx, "S1", now, "110")
    assert result.task.outcome == "failed"
    last_q = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])["set"][2]
    task = engine.flag(last_q, "", now + timedelta(minutes=1))
    assert task.outcome == "passed" and task.xp_value == 5
    assert xp_total(fx.course_id) == 12 + 5  # lesson (10 + no-mistakes bonus) + re-scored review
    rows = db.query("SELECT rating FROM review_log WHERE skill_id = ? AND implicit = 0 ORDER BY created_at", (fx.sid("S1"),))
    assert [r["rating"] for r in rows] == [3, 3]  # lesson pass + the re-scored review (the Again row is gone)
    assert db.query_one("SELECT lapses FROM progress WHERE skill_id = ?", (fx.sid("S1"),))["lapses"] == 0


def test_review_requires_learned_skill() -> None:
    fx = build_course()
    with pytest.raises(engine.Conflict):
        engine.start_review(fx.sid("S1"), "x", T0)


# ---------------------------------------------------------------------------
# Question top-ups
# ---------------------------------------------------------------------------


def test_top_up_requested_when_unseen_questions_run_low(isolated_engine) -> None:
    fx = build_course()
    _fail_kp0(fx)
    assert (fx.sid("S1"), 0) in isolated_engine  # only 1 unseen question left in KP 1
    learn(fx, "S2", T0)
    isolated_engine.clear()
    engine.start_review(fx.sid("S2"), "Review due", T0 + days(3))
    assert isolated_engine == [(fx.sid("S2"), None)]  # 4 unseen - 3 in the set < 3


def test_top_up_wrapper_calls_pipeline_once_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline import topup

    calls: list[tuple[str, int | None]] = []
    monkeypatch.setattr(topup, "request_top_up", lambda skill_id, kp_index=None: calls.append((skill_id, kp_index)))
    monkeypatch.setattr(engine, "_topup_requested", {})
    real_request_top_up("s1", 0)
    real_request_top_up("s1", 0)
    real_request_top_up("s1", 1)
    assert calls == [("s1", 0), ("s1", 1)]
