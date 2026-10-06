"""Task engine: unlocking, score-based lessons (KP targets, extra questions, capstone, 80 % pass, early stop),
remediation, easier step, flags, legacy written proofs, reviews, XP (SPEC §6.2-§6.4)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from test_learning_fixtures import (  # noqa: F401
    CAPSTONE_STEPS,
    T0,
    add_capstone,
    add_question,
    answer_fully,
    build_course,
    card,
    days,
    isolated_engine,
    learn,
    question,
    right,
    set_due,
    set_targets,
    status,
    step_submit,
    task_state,
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


def lesson_rating(skill_id: str) -> int:
    row = db.query_one("SELECT rating FROM review_log WHERE skill_id = ? AND implicit = 0 ORDER BY created_at, rowid", (skill_id,))
    return row["rating"]


def failures(skill_id: str) -> dict:
    return db.loads(db.query_one("SELECT kp_failures_json FROM progress WHERE skill_id = ?", (skill_id,))["kp_failures_json"])


def finish(task_id: str, task, now=T0):
    """Answer everything left correctly; returns the last AnswerResult."""
    result = None
    while task.phase != "done":
        result = answer_fully(task_id, task.question.id, now)
        task = result.task
    return result


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
# Lessons: plan, points, score, pass
# ---------------------------------------------------------------------------


def test_lesson_phases_and_perfect_pass() -> None:
    fx = build_course(est_minutes=12)
    task_id = engine.start_lesson(fx.sid("S1"), "Next new skill", T0)
    task = engine.get_task(task_id)
    assert task.phase == "learn" and task.question is None and task.lesson is not None
    assert len(task.lesson.knowledge_points) == 2 and task.xp_value == 12
    p = task.progress
    assert (p.planned, p.kp_target, p.score, p.points, p.max_points, p.pass_mark, p.in_capstone) == (4, 2, 0, 0, 0, 0.8, False)
    assert status(fx.sid("S1")) == "in_progress"
    with pytest.raises(engine.Conflict):
        engine.submit_answer(task_id, right(fx.questions["S1"][0][0]), T0)
    task = engine.begin_practice(task_id, T0)
    assert task.phase == "practice" and task.question.id == fx.questions["S1"][0][0]
    assert engine.begin_practice(task_id, T0).question.id == task.question.id  # idempotent
    r1 = engine.submit_answer(task_id, right(task.question.id), T0)
    assert r1.correct and r1.independent and r1.score == 1.0 and r1.question_done and r1.step_result is None
    assert (r1.task.progress.points, r1.task.progress.max_points, r1.task.progress.score) == (1.0, 1.0, 1.0)
    assert r1.task.progress.kp_index == 0 and r1.task.progress.kp_questions_used == 2
    assert r1.correct_answer and r1.solution
    r2 = engine.submit_answer(task_id, right(r1.task.question.id), T0)
    assert r2.task.progress.kp_index == 1 and r2.task.question.kp_index == 1  # KP 1: its 2 planned questions done
    r3 = engine.submit_answer(task_id, right(r2.task.question.id), T0)
    r4 = engine.submit_answer(task_id, right(r3.task.question.id), T0)
    assert r4.task.phase == "done" and r4.task.outcome == "passed"
    assert r4.xp_awarded == 12 + 2  # round(12 × 1.0) + perfect bonus
    assert "Lesson passed with 100%" in r4.feedback
    assert xp_total(fx.course_id) == 14
    assert status(fx.sid("S1")) == "learned" and card(fx.sid("S1")) is not None
    assert lesson_rating(fx.sid("S1")) == 3  # Good
    assert (r4.task.progress.answered, r4.task.progress.correct, r4.task.progress.planned) == (4, 4, 4)


def test_hint_is_half_a_point_and_reveal_is_zero() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    q0 = task.question.id
    engine.hint(q0, T0)
    hinted = engine.submit_answer(task_id, right(q0), T0)
    assert hinted.correct and not hinted.independent and hinted.score == 0.5 and "half a point" in hinted.feedback
    assert hinted.suggestion is None  # assisted is not a miss to recover from with an easier question
    p = hinted.task.progress
    assert (p.points, p.max_points, p.score, p.kp_target, p.planned) == (0.5, 1.0, 0.5, 3, 5)  # +1 after help
    assert "One more question on this step" in hinted.feedback
    q1 = hinted.task.question.id
    shown = engine.reveal(q1, T0)
    assert shown.solution and shown.correct_answer
    after_reveal = engine.submit_answer(task_id, right(q1), T0)
    assert after_reveal.correct and not after_reveal.independent and after_reveal.score == 0.0
    assert "no points" in after_reveal.feedback
    # 0.5 + 0 of 2, and at best 4 more → 4.5 / 6 = 0.75: 80 % is out of reach, the lesson stops right here
    assert after_reveal.task.outcome == "failed" and after_reveal.suggestion == "retry_later"
    attempts = db.query("SELECT independent, revealed, hints_used FROM attempts WHERE task_id = ? ORDER BY rowid", (task_id,))
    assert [(a["independent"], a["revealed"], a["hints_used"]) for a in attempts] == [(0, 0, 1), (0, 1, 0)]


def test_extra_question_after_a_miss_and_a_pass_exactly_at_80_percent() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    miss = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert not miss.correct and miss.score == 0.0 and miss.task.outcome is None
    assert (miss.task.progress.kp_target, miss.task.progress.planned) == (3, 5)
    assert "One more question on this step comes next" in miss.feedback
    task = miss.task
    for expected_kp in (0, 0, 1, 1):
        assert task.progress.kp_index == expected_kp
        task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.outcome == "passed" and task.progress.score == pytest.approx(0.8)  # 4 / 5
    assert task.xp_value == 8  # round(10 × 0.8), no perfect bonus
    assert lesson_rating(fx.sid("S1")) == 2  # Hard below 0.9
    assert task_state(task_id)["result"]["score"] == pytest.approx(0.8)


def test_score_of_90_percent_starts_with_good() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id, hints_used=1), T0).task  # 0.5, one extra question
    done = finish(task_id, task)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx(0.9)  # 4.5 / 5
    assert lesson_rating(fx.sid("S1")) == 3 and done.xp_awarded == 9


def test_two_misses_halt_a_legacy_lesson_with_cooldown() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    first = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert first.task.phase == "practice"  # 4 / 5 is still possible
    result = engine.submit_answer(task_id, wrong(first.task.question.id), T0)
    assert result.task.outcome == "failed" and result.xp_awarded == 0  # 4 / 6 at best
    assert result.suggestion == "retry_later" and "2 hours" in result.suggestion_text and "80%" in result.suggestion_text
    assert result.task.progress.answered == 2
    progress = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert db.loads(progress["kp_failures_json"]) == {"0": 1}
    assert db.parse_iso(progress["cooldown_until"]) == T0 + timedelta(hours=2)
    assert progress["status"] == "in_progress" and xp_total(fx.course_id) == 0
    offered = lambda now: {(c.skill["key"], c.type) for c in selector.rank(fx.course_id, now)}  # noqa: E731
    assert ("S1", "lesson") not in offered(T0 + timedelta(hours=1))
    assert ("S1", "lesson") in offered(T0 + timedelta(hours=2, minutes=1))


def test_extra_questions_are_capped_at_two_per_kp() -> None:
    fx = build_course()
    set_targets(fx, "S1", [3, 3])
    add_question(fx, "S1", 0)  # a 5th KP-1 question
    add_capstone(fx, "S1")
    task_id, task = start_practice(fx, "S1")
    targets = []
    for _ in range(4):
        engine.hint(task.question.id, T0)
        task = engine.submit_answer(task_id, right(task.question.id), T0).task
        targets.append(task.progress.kp_target)
    assert targets == [4, 5, 5, 5]  # 3 + at most 2
    assert task.phase == "practice" and task.progress.kp_questions_available == 5
    # 2 of 4 so far, at best 2 + 1 + 3 + 2 (capstone) of 10 = 0.8: still passable
    done = finish(task_id, task)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx(0.8)
    assert done.task.progress.answered == 9 and lesson_rating(fx.sid("S1")) == 2


def test_no_extra_question_once_the_kp_pool_is_used_up() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    db.execute("DELETE FROM questions WHERE id IN (?, ?)", (kp0[2], kp0[3]))
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id, hints_used=1), T0).task
    assert task.progress.kp_target == 2 and task.progress.planned == 4  # nothing unseen left for an extra
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.kp_index == 1
    done = finish(task_id, task)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx(3.5 / 4)


def test_kp_targets_come_from_the_lesson() -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 3])
    task_id, task = start_practice(fx, "S1")
    assert (task.progress.kp_target, task.progress.planned) == (1, 4)
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.kp_index == 1 and task.progress.kp_target == 3
    done = finish(task_id, task)
    assert done.task.outcome == "passed" and done.task.progress.answered == 4
    assert engine._targets_from_lesson(
        {"knowledge_points": [{"target_questions": 0}, {"target_questions": 7}, {}, {"target_questions": "x"}, {"target_questions": 2}]}
    ) == {"0": 1, "1": 3, "2": 2, "3": 2, "4": 2}


@pytest.mark.parametrize("with_capstone", [False, True])
def test_early_stop_counts_every_remaining_question_and_the_capstone_twice(with_capstone: bool) -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 1])
    if with_capstone:
        add_capstone(fx, "S1")
    task_id, task = start_practice(fx, "S1")
    first = engine.submit_answer(task_id, wrong(task.question.id), T0)
    if not with_capstone:
        assert first.task.outcome == "failed"  # (0 + 1 + 1) / 3: out of reach after one miss
        assert "pauses here" in first.suggestion_text
        # the plan it stopped in, as the meter showed it before the answer: "1 of 2 answered", never "1 of 1"
        assert (first.task.progress.answered, first.task.progress.planned) == (1, 2)
        assert (engine.get_task(task_id).progress.answered, engine.get_task(task_id).progress.planned) == (1, 2)
        return
    assert first.task.phase == "practice" and first.task.progress.kp_target == 2  # (0 + 1 + 1 + 2) / 5 = 0.8
    assert (first.task.progress.answered, first.task.progress.planned) == (1, 4)
    second = engine.submit_answer(task_id, wrong(first.task.question.id), T0)
    assert second.task.outcome == "failed" and second.task.progress.answered == 2  # 4 / 6
    assert second.task.progress.planned == 4 and "pauses here" in second.suggestion_text  # "2 of 4", never "2 of 2"


def test_halted_note_and_step_revealed_are_filled_once_the_schema_declares_them(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import schemas

    class Progress(schemas.TaskProgress):
        halted: bool = False

    class Answer(schemas.AnswerResult):
        note: str = ""

    class Step(schemas.StepResult):
        revealed: bool = False

    monkeypatch.setattr(engine, "TaskProgress", Progress)
    monkeypatch.setattr(engine, "AnswerResult", Answer)
    monkeypatch.setattr(engine, "StepResult", Step)
    fx = build_course()
    task_id, cap, to_cap = _to_capstone(fx)
    assert to_cap.note == "Last: one problem that puts the lesson together, step by step — it counts double."
    assert to_cap.task.progress.halted is False
    engine.reveal(cap, T0)
    s0 = engine.submit_answer(task_id, AnswerSubmit(question_id=cap, step=0), T0)  # (2 + 2 × 2/3) / 4 at best
    assert s0.status == "step" and s0.step_result.revealed is True and s0.note == ""
    reloaded = engine.get_task(task_id).question.step_results
    assert [r.revealed for r in reloaded] == [True]  # a reload still knows the step's solution was shown
    stopped = engine.submit_answer(task_id, step_submit(cap, 1, correct=False), T0)
    assert stopped.task.progress.halted is True and stopped.step_result.revealed is False
    other = build_course()
    set_targets(other, "S1", [1, 1])
    t2, task = start_practice(other, "S1")
    task = engine.submit_answer(t2, right(task.question.id), T0).task
    last = engine.submit_answer(t2, wrong(task.question.id), T0)
    assert last.task.outcome == "failed" and last.task.progress.halted is False  # everything planned was answered


def test_a_miss_on_the_last_planned_question_ends_below_the_mark_not_early() -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 1])
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.planned == 2
    last = engine.submit_answer(task_id, wrong(task.question.id), T0)
    # its extra question could only reach (1 + 0 + 1) / 3: the lesson ends with everything the learner was shown answered
    assert last.task.outcome == "failed" and (last.task.progress.answered, last.task.progress.planned) == (2, 2)
    assert "Your score, 50%, is below the 80% pass mark" in last.suggestion_text
    assert "pauses" not in last.suggestion_text and "out of reach" not in last.suggestion_text
    assert failures(fx.sid("S1")) == {"1": 1}


@pytest.mark.parametrize(
    "score,pct",
    [(0.0, 0), (0.125, 13), (0.285, 29), (0.665, 67), (0.9167, 92), (0.796, 79), (43 / 54, 79), (0.8, 80),
     (0.805, 81), (0.995, 99), (0.9999, 99), (1.0, 100), (1.2, 100), (-0.1, 0)],
)  # fmt: skip
def test_score_percent_rounds_half_up_but_never_onto_the_pass_mark_or_100(score: float, pct: int) -> None:
    assert engine.score_percent(score) == pct


def test_retry_prefers_unseen_questions() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    engine.submit_answer(task_id, wrong(task.question.id), T0)
    _, task = start_practice(fx, "S1", T0 + timedelta(hours=3))
    assert task.question.id == fx.questions["S1"][0][2]  # the first KP-1 question not answered before


def _fail_now(fx, key: str, now) -> object:
    task_id, task = start_practice(fx, key, now)
    result = None
    while task.phase != "done":
        result = engine.submit_answer(task_id, wrong(task.question.id), now)
        task = result.task
    return result


def test_second_failure_of_the_same_lesson_triggers_remediation() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    learn(fx, "S2", T0)
    now = T0 + days(1)
    assert not scheduler.is_due(card(fx.sid("S1")), now)
    first = _fail_now(fx, "S3", now)
    assert first.suggestion == "retry_later"
    later = now + timedelta(hours=3)
    task_id, task = start_practice(fx, "S3", later)
    task = engine.submit_answer(task_id, right(task.question.id), later).task
    task = engine.submit_answer(task_id, right(task.question.id), later).task
    assert task.progress.kp_index == 1
    task = engine.submit_answer(task_id, wrong(task.question.id), later).task
    second = engine.submit_answer(task_id, wrong(task.question.id), later)
    assert second.task.outcome == "failed" and failures(fx.sid("S3")) == {"0": 1, "1": 1}  # a different KP this time
    assert second.suggestion == "review_prerequisite"
    assert "S1" in second.suggestion_text and "S2" in second.suggestion_text and "twice" in second.suggestion_text
    assert scheduler.is_due(card(fx.sid("S1")), later) and scheduler.is_due(card(fx.sid("S2")), later)
    menu = selector.rank(fx.course_id, later)
    assert [(c.skill["key"], c.type) for c in menu[:2]] == [("S1", "review"), ("S2", "review")]
    assert menu[0].reason.startswith("Refresh before retrying S3")


def test_a_failure_recorded_by_the_old_per_kp_rule_counts_toward_remediation() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    learn(fx, "S2", T0)
    db.execute("UPDATE progress SET kp_failures_json = ? WHERE skill_id = ?", (db.dumps({"1": 1}), fx.sid("S3")))
    result = _fail_now(fx, "S3", T0 + days(1))
    assert result.suggestion == "review_prerequisite" and failures(fx.sid("S3")) == {"0": 1, "1": 1}


# ---------------------------------------------------------------------------
# Capstone: multistep, step hints, reveal, reload
# ---------------------------------------------------------------------------


def _to_capstone(fx, key="S1", now=T0):
    set_targets(fx, key, [1, 1])
    cap = add_capstone(fx, key)
    task_id, task = start_practice(fx, key, now)
    assert task.progress.planned == 3
    task = engine.submit_answer(task_id, right(task.question.id), now).task
    result = engine.submit_answer(task_id, right(task.question.id), now)
    assert result.task.question.id == cap
    return task_id, cap, result


def test_capstone_steps_hints_and_reload() -> None:
    fx = build_course()
    task_id, cap, result = _to_capstone(fx)
    assert "Last: one problem" in result.feedback and "counts double" in result.feedback
    task = result.task
    q = task.question
    assert task.progress.in_capstone and (task.progress.kp_target, task.progress.kp_questions_used) == (1, 1)
    assert q.role == "capstone" and q.kind == "multistep" and q.step_index == 0 and q.step_results == []
    assert [s.prompt for s in q.steps][0] == "Step 1: compute $2+3$." and q.steps[1].choices == ["A", "B", "C"]
    dumped = q.model_dump_json()
    assert '"answer"' not in dumped and "Law C lets us regroup" not in dumped  # no answers or explanations leak
    with pytest.raises(engine.Conflict, match="final problem"):
        engine.easier_step(task_id, T0)
    with pytest.raises(engine.Conflict, match="step 1"):
        engine.submit_answer(task_id, AnswerSubmit(question_id=cap, response="5"), T0)  # no step given
    with pytest.raises(engine.Conflict):
        engine.submit_answer(task_id, step_submit(cap, 1), T0)  # not the current step
    with pytest.raises(engine.Invalid):
        engine.submit_answer(task_id, AnswerSubmit(question_id=cap, step=0, response="2(("), T0)
    with pytest.raises(engine.Invalid):
        engine.submit_answer(task_id, AnswerSubmit(question_id=cap, step=0), T0)  # nothing submitted
    s0 = engine.submit_answer(task_id, step_submit(cap, 0), T0)
    assert s0.status == "step" and not s0.question_done and s0.correct and s0.independent and s0.score == 1.0
    assert s0.step_result.index == 0 and s0.step_result.score == 1.0 and s0.step_result.explanation == "$2+3=5$."
    assert s0.correct_answer == "$5$" and s0.xp_awarded == 0
    assert s0.task.question.step_index == 1 and len(s0.task.question.step_results) == 1
    assert s0.task.progress.answered == 2  # the capstone counts once all its steps are answered
    assert not db.query("SELECT 1 FROM attempts WHERE question_id = ?", (cap,))
    reloaded = engine.get_task(task_id)
    assert reloaded.question.step_index == 1 and reloaded.question.step_results[0].correct
    with pytest.raises(engine.Conflict):
        engine.hint(cap, T0, step=0)  # an earlier step
    h = engine.hint(cap, T0, step=1)
    assert (h.hint, h.hints_used) == ("Not A.", 1)
    assert engine.hint(cap, T0).hint == "Not A."  # default: the current step; still one hint
    assert engine.get_task(task_id).question.hints_used == 1
    s1 = engine.submit_answer(task_id, step_submit(cap, 1), T0)
    assert s1.status == "step" and s1.step_result.score == 0.5 and "half a point" in s1.step_result.feedback
    assert s1.correct_choice == 2 and s1.score == pytest.approx(0.75)  # running mean of the steps so far
    assert engine.get_task(task_id).question.hints_used == 0  # the next step starts fresh
    last = engine.submit_answer(task_id, step_submit(cap, 2), T0)
    assert last.question_done and last.status == "incorrect" and not last.correct  # not every step was unaided
    assert last.score == pytest.approx(2.5 / 3) and last.step_result.index == 2
    assert "2 of 3 steps right" not in last.feedback and "3 of 3 steps right" in last.feedback
    assert last.solution == "Whole worked solution."
    # 1 + 1 + 2 × 0.833 of 4 = 0.917: passed, Good
    assert last.task.outcome == "passed" and last.task.progress.score == pytest.approx((2 + 2 * 2.5 / 3) / 4)
    assert lesson_rating(fx.sid("S1")) == 3 and last.xp_awarded == 9
    row = db.query_one("SELECT * FROM attempts WHERE question_id = ?", (cap,))
    assert (row["correct"], row["independent"], row["status"], row["hints_used"], row["revealed"]) == (0, 0, "incorrect", 1, 0)
    assert [s["score"] for s in json.loads(row["response"])["steps"]] == [1.0, 0.5, 1.0]


def test_capstone_halts_between_steps_once_80_percent_is_out_of_reach() -> None:
    fx = build_course()
    task_id, cap, _ = _to_capstone(fx)
    s0 = engine.submit_answer(task_id, step_submit(cap, 0, correct=False), T0)
    # (2 + 2 × (0 + 1 + 1) / 3) / 4 = 0.83: still reachable
    assert s0.status == "step" and not s0.correct and s0.step_result.score == 0 and s0.task.phase == "practice"
    shown = engine.reveal(cap, T0)
    assert (shown.correct_answer, shown.solution) == ("C", "Law C lets us regroup.")  # only the current step
    assert engine.get_task(task_id).question.revealed is True
    s1 = engine.submit_answer(task_id, AnswerSubmit(question_id=cap, step=1), T0)  # nothing typed after the reveal
    assert s1.step_result.score == 0 and "Answer viewed" in s1.step_result.feedback
    # (2 + 2 × (0 + 0 + 1) / 3) / 4 = 0.67 even with step 3 right: the lesson stops now, not after step 3
    assert s1.question_done and s1.status == "incorrect" and s1.task.outcome == "failed" and s1.task.question is None
    assert "1 left unanswered" in s1.feedback and s1.suggestion == "retry_later"
    assert "out of reach" in s1.suggestion_text and "pauses here" in s1.suggestion_text
    p = s1.task.progress
    assert (p.answered, p.planned) == (2, 3)  # the capstone was not finished: "2 of 3", never "3 of 3"
    assert p.score == pytest.approx(2 / (2 + 2 * 2 / 3))  # its 2 answered steps weigh 2 × 2/3
    assert failures(fx.sid("S1")) == {"cap": 1}
    row = db.query_one("SELECT revealed, hints_used, status FROM attempts WHERE question_id = ?", (cap,))
    assert (row["revealed"], row["hints_used"], row["status"]) == (1, 0, "incorrect")
    entry = task_state(task_id)["log"][-1]
    assert entry["unfinished"] and entry["w"] == pytest.approx(4 / 3) and len(entry["steps"]) == 2


def test_capstone_answered_in_full_below_the_mark_is_not_an_early_stop() -> None:
    fx = build_course()
    task_id, cap, _ = _to_capstone(fx)
    engine.submit_answer(task_id, step_submit(cap, 0), T0)
    s1 = engine.submit_answer(task_id, step_submit(cap, 1, correct=False), T0)
    assert s1.status == "step" and s1.task.phase == "practice"  # (2 + 2 × 2/3) / 4 = 0.83 at best
    last = engine.submit_answer(task_id, step_submit(cap, 2, correct=False), T0)
    assert last.score == pytest.approx(1 / 3) and last.status == "incorrect"
    # (1 + 1 + 2/3) / 4 = 0.67 with everything answered: below the pass mark, not "out of reach … pauses here"
    assert last.task.outcome == "failed" and last.suggestion == "retry_later"
    assert "Your score, 67%, is below the 80% pass mark" in last.suggestion_text
    assert "pauses" not in last.suggestion_text and "2 hours" in last.suggestion_text
    assert (last.task.progress.answered, last.task.progress.planned) == (3, 3)
    assert failures(fx.sid("S1")) == {"cap": 1}


def test_flagged_capstone_is_replaced_then_skipped() -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 1])
    first_cap, second_cap = add_capstone(fx, "S1"), add_capstone(fx, "S1")
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.question.id == first_cap
    engine.submit_answer(task_id, step_submit(first_cap, 0), T0)
    task = engine.flag(first_cap, "step 2 is ambiguous", T0)
    assert task.question.id == second_cap and task.question.step_index == 0 and task.question.step_results == []
    task = engine.flag(second_cap, "", T0)
    assert task.phase == "done" and task.outcome == "passed"  # no capstone left: 2 / 2


def _to_capstone_at_two_of_three(fx):
    """S1 with targets 1/1 and one capstone: a miss, its extra question and KP 2 right → 2 / 3, at best 4 / 5 = 0.8."""
    set_targets(fx, "S1", [1, 1])
    cap = add_capstone(fx, "S1")
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    result = engine.submit_answer(task_id, right(task.question.id), T0)
    assert result.task.question.id == cap and result.task.progress.score == pytest.approx(2 / 3)
    return task_id, cap


def cooldown_until(skill_id: str):
    return db.query_one("SELECT cooldown_until FROM progress WHERE skill_id = ?", (skill_id,))["cooldown_until"]


def test_flagging_the_capstone_mid_problem_ends_the_lesson_without_a_failure() -> None:
    fx = build_course()
    task_id, cap = _to_capstone_at_two_of_three(fx)
    engine.submit_answer(task_id, step_submit(cap, 0), T0)
    task = engine.flag(cap, "step 2 has no right option", T0)
    # no replacement: scored on the practice alone, 2 / 3 — below 80 %, but not the learner's failure
    assert task.phase == "done" and task.outcome == "failed" and task.progress.score == pytest.approx(2 / 3)
    assert (task.progress.answered, task.progress.planned) == (3, 3)
    assert failures(fx.sid("S1")) == {} and cooldown_until(fx.sid("S1")) is None
    assert task_state(task_id)["fail"]["counted"] is False
    assert ("S1", "lesson") in {(c.skill["key"], c.type) for c in selector.rank(fx.course_id, T0)}
    assert selector.start_task(fx.course_id, fx.sid("S1"), "lesson", T0).skill.key == "S1"  # no cooldown to force


@pytest.mark.parametrize("stop", ["mid_problem", "after_problem"])
def test_flagging_the_capstone_a_lesson_failed_in_takes_the_failure_back(stop: str) -> None:
    fx = build_course()
    task_id, cap = _to_capstone_at_two_of_three(fx)
    engine.submit_answer(task_id, step_submit(cap, 0), T0)
    if stop == "mid_problem":  # (2 + 2 × (1 + 0 + 1) / 3) / 5 = 0.67 at best: stops after step 2
        failed = engine.submit_answer(task_id, step_submit(cap, 1, correct=False), T0)
    else:  # everything answered: (2 + 2 × 2/3) / 5
        engine.submit_answer(task_id, step_submit(cap, 1), T0)
        failed = engine.submit_answer(task_id, step_submit(cap, 2, correct=False), T0)
    assert failed.task.outcome == "failed" and failures(fx.sid("S1")) == {"cap": 1}
    assert cooldown_until(fx.sid("S1")) is not None
    after = engine.flag(cap, "", T0)
    assert after.outcome == "failed"  # 2 / 3 without it: still below 80 %, but no longer counted
    assert failures(fx.sid("S1")) == {} and cooldown_until(fx.sid("S1")) is None
    assert task_state(task_id)["fail"]["counted"] is False
    assert selector.start_task(fx.course_id, fx.sid("S1"), "lesson", T0).skill.key == "S1"  # no cooldown to force


def test_flagging_the_capstone_a_lesson_stopped_in_can_pass_it() -> None:
    fx = build_course()
    task_id, cap, _ = _to_capstone(fx)  # 2 / 2 so far
    engine.submit_answer(task_id, step_submit(cap, 0, correct=False), T0)
    stopped = engine.submit_answer(task_id, step_submit(cap, 1, correct=False), T0)
    assert stopped.task.outcome == "failed" and failures(fx.sid("S1")) == {"cap": 1}
    after = engine.flag(cap, "", T0)
    assert after.outcome == "passed" and after.progress.score == pytest.approx(1.0)  # scored without it: 2 / 2
    assert failures(fx.sid("S1")) == {} and status(fx.sid("S1")) == "learned"


def test_a_flag_that_makes_80_percent_reachable_resumes_the_capstone_where_it_stopped() -> None:
    fx = build_course()
    task_id, cap = _to_capstone_at_two_of_three(fx)
    first_miss = task_state(task_id)["log"][0]["q"]
    stopped = engine.submit_answer(task_id, step_submit(cap, 0, correct=False), T0)  # (2 + 2 × 2/3) / 5 at best
    assert stopped.task.outcome == "failed" and stopped.task.progress.planned == 4
    resumed = engine.flag(first_miss, "", T0)  # now 2 / 2, and the capstone can still bring (2 + 4/3) / 4
    assert resumed.phase == "practice" and resumed.question.id == cap
    assert resumed.question.step_index == 1 and [r.correct for r in resumed.question.step_results] == [False]
    assert failures(fx.sid("S1")) == {} and cooldown_until(fx.sid("S1")) is None
    engine.submit_answer(task_id, step_submit(cap, 1), T0)
    done = engine.submit_answer(task_id, step_submit(cap, 2), T0)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx((2 + 2 * 2 / 3) / 4)
    rows = db.query("SELECT status FROM attempts WHERE question_id = ? ORDER BY rowid", (cap,))
    assert [r["status"] for r in rows] == ["voided", "incorrect"]


def test_a_capstone_step_in_the_other_complex_notation_is_asked_again() -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 1])
    steps = [dict(CAPSTONE_STEPS[0]), {**CAPSTONE_STEPS[2], "answer": "(1/5, 2/5)", "answer_display": "$(1/5, 2/5)$"}]
    cap = add_capstone(fx, "S1", steps=steps)
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    engine.submit_answer(task_id, step_submit(cap, 0), T0)
    with pytest.raises(engine.Invalid, match="ordered pair"):
        engine.submit_answer(task_id, AnswerSubmit(question_id=cap, step=1, response="1/5+2/5i"), T0)
    assert len(task_state(task_id)["step_results"]) == 1  # nothing recorded for step 2
    done = engine.submit_answer(task_id, AnswerSubmit(question_id=cap, step=1, response="(1/5, 2/5)"), T0)
    assert done.question_done and done.score == 1.0 and done.task.outcome == "passed"


def test_multistep_practice_question_scores_the_mean_of_its_steps() -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 1])
    for qid in fx.questions["S1"][0]:
        db.execute("DELETE FROM questions WHERE id = ?", (qid,))
    steps = [{**s, "prompt": f"Practice {s['prompt']}"} for s in CAPSTONE_STEPS]
    ms = add_question(fx, "S1", 0, kind="multistep", answer="", tier=2, steps=steps)
    task_id, task = start_practice(fx, "S1")
    assert task.question.id == ms and task.question.role == "practice" and not task.progress.in_capstone
    engine.submit_answer(task_id, step_submit(ms, 0), T0)
    with pytest.raises(engine.Conflict, match="Finish this problem first"):
        engine.easier_step(task_id, T0)
    engine.submit_answer(task_id, step_submit(ms, 1), T0)
    done = engine.submit_answer(task_id, step_submit(ms, 2, correct=False), T0)
    assert done.question_done and done.score == pytest.approx(2 / 3)
    assert "2 of 3 steps right" in done.feedback
    assert task_state(task_id)["log"][0]["res"] == "partial"
    final = finish(task_id, done.task)
    assert final.task.outcome == "passed" and final.task.progress.score == pytest.approx((2 / 3 + 1) / 2)


def test_step_must_be_current_after_another_tab_answered_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.learning import grading

    fx = build_course()
    task_id, cap, _ = _to_capstone(fx)
    real = grading.check_answer
    calls = []

    def answer_meanwhile(*args, **kwargs):
        if not calls:
            calls.append(1)
            engine.submit_answer(task_id, step_submit(cap, 0), T0)  # the other tab wins the race
        return real(*args, **kwargs)

    monkeypatch.setattr(grading, "check_answer", answer_meanwhile)
    with pytest.raises(engine.Conflict):
        engine.submit_answer(task_id, step_submit(cap, 0), T0)
    assert len(task_state(task_id)["step_results"]) == 1


# ---------------------------------------------------------------------------
# Easier step
# ---------------------------------------------------------------------------


def test_easier_step_after_a_miss_is_scored_normally() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    db.execute("UPDATE questions SET tier = 2 WHERE id = ?", (kp0[1],))
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (kp0[3],))
    task_id, task = start_practice(fx, "S1")
    with pytest.raises(engine.Conflict, match="Try this question first"):
        engine.easier_step(task_id, T0)
    miss = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert miss.suggestion == "easier_step" and miss.task.progress.easier_available
    assert miss.task.question.id == kp0[1] and miss.task.progress.kp_wrong == 1
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.id == kp0[3] and swapped.progress.kp_questions_used == 2  # replaces the skipped one
    assert swapped.progress.easier_available is False  # nothing easier than tier 1
    r = engine.submit_answer(task_id, right(kp0[3]), T0)
    assert r.independent and r.score == 1.0 and r.task.question.id == kp0[2]  # the extra question of KP 1
    done = finish(task_id, r.task)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx(0.8)
    assert not db.query("SELECT 1 FROM attempts WHERE question_id = ?", (kp0[1],))  # the skipped one never returns


def _easier_setup():
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    db.execute("UPDATE questions SET tier = 2 WHERE id = ?", (kp0[1],))
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (kp0[3],))
    task_id, task = start_practice(fx, "S1")
    miss = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert miss.task.question.id == kp0[1]
    return fx, task_id, kp0


@pytest.mark.parametrize("help_used,points", [("reveal", 0.0), ("hint", 0.5), ("none", 1.0)])
def test_easier_step_keeps_the_help_already_used_on_the_swapped_question(help_used: str, points: float) -> None:
    fx, task_id, kp0 = _easier_setup()
    if help_used == "reveal":
        engine.reveal(kp0[1], T0)
    elif help_used == "hint":
        engine.hint(kp0[1], T0)
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.id == kp0[3]
    assert swapped.question.revealed is False and swapped.question.hints_used == 0  # nothing of q4 is shown
    r = engine.submit_answer(task_id, right(kp0[3]), T0)
    assert r.correct and r.score == points and r.independent is (help_used == "none")
    if help_used == "reveal":
        assert "replaced had been shown, so no points" in r.feedback
        # 0 + 0 so far, at best + 1 + 2 of 5: out of reach, exactly as answering q2 after its reveal would have been
        assert r.task.outcome == "failed"
    elif help_used == "hint":
        assert "replaced, so at most half a point" in r.feedback
    row = db.query_one("SELECT independent, revealed, hints_used FROM attempts WHERE question_id = ?", (kp0[3],))
    assert row["independent"] == int(help_used == "none")
    assert (row["revealed"], row["hints_used"]) == ((1, 0) if help_used == "reveal" else (0, 1) if help_used == "hint" else (0, 0))
    entry = task_state(task_id)["log"][-1]
    assert entry["q"] == kp0[3] and entry["pts"] == points


def test_carried_reveal_is_no_answer_to_the_easier_question() -> None:
    fx, task_id, kp0 = _easier_setup()
    engine.reveal(kp0[1], T0)
    engine.easier_step(task_id, T0)
    with pytest.raises(engine.Invalid):  # q4's own solution was never shown: an empty answer is still asked again
        engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[3]), T0)
    after_hint = engine.hint(kp0[3], T0)  # its own hint on top changes nothing: 0 points
    assert after_hint.hints_used == 1
    assert engine.submit_answer(task_id, right(kp0[3]), T0).score == 0.0


def test_easier_step_stays_in_the_current_kp() -> None:
    fx = build_course()
    kp0, kp1 = fx.questions["S1"]
    for qid, tier in [(kp1[0], 2), (kp1[1], 2), (kp1[2], 3), (kp1[3], 3), (kp0[3], 1)]:
        db.execute("UPDATE questions SET tier = ? WHERE id = ?", (tier, qid))
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.kp_index == 1
    miss = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert miss.suggestion is None and miss.task.progress.easier_available is False
    with pytest.raises(engine.Conflict, match="No easier question"):
        engine.easier_step(task_id, T0)  # KP 1's unseen tier-1 question is not used for KP 2
    learn(fx, "S2", T0)
    engine.abandon(task_id, T0)
    review_id = engine.start_review(fx.sid("S2"), "test", T0 + days(5))
    with pytest.raises(engine.Conflict):
        engine.easier_step(review_id, T0 + days(5))  # reviews have no easier step


def test_two_misses_in_a_row_without_an_easier_question_suggest_rereading() -> None:
    fx = build_course()
    set_targets(fx, "S1", [3, 3])
    add_question(fx, "S1", 0, tier=3)  # room for both extra questions
    add_capstone(fx, "S1")
    task_id, task = start_practice(fx, "S1")
    first = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert first.suggestion is None  # the next question is tier 1: nothing easier
    second = engine.submit_answer(task_id, wrong(first.task.question.id), T0)
    assert second.task.phase == "practice"  # (0 + 3 + 3 + 2) / 10 = 0.8 is still possible
    assert second.task.question.tier == 2 and second.suggestion == "reread_example"
    with pytest.raises(engine.Conflict):
        engine.easier_step(task_id, T0)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_flag_current_question_serves_replacement() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    flagged_id = task.question.id
    after = engine.flag(flagged_id, "answer key looks wrong", T0)
    assert after.question.id != flagged_id
    assert question(flagged_id)["flagged"] == 1
    assert after.progress.kp_questions_used == 1 and after.progress.kp_questions_available == 3


def test_flag_voids_a_wrong_answer_and_its_extra_question() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    bad = task.question.id
    task = engine.submit_answer(task_id, wrong(bad), T0).task
    assert task.progress.kp_target == 3 and task.progress.score == 0.5
    after = engine.flag(bad, "", T0)
    assert after.progress.answered == 1 and after.progress.kp_target == 2 and after.progress.score == 1.0
    assert db.query_one("SELECT status FROM attempts WHERE question_id = ?", (bad,))["status"] == "voided"
    final = engine.submit_answer(task_id, right(after.question.id), T0)
    assert final.task.progress.kp_index == 1


def test_flag_reopens_a_lesson_failed_because_of_that_question() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    last = task.question.id
    failed = engine.submit_answer(task_id, wrong(last), T0)
    assert failed.task.outcome == "failed"
    reopened = engine.flag(last, "the expected answer is wrong", T0)
    assert reopened.phase == "practice" and reopened.outcome is None and reopened.question is not None
    assert reopened.progress.answered == 1
    progress = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert db.loads(progress["kp_failures_json"]) == {} and progress["cooldown_until"] is None
    done = finish(task_id, reopened)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx(0.8)


def test_flag_does_not_reopen_a_lesson_still_out_of_reach() -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 1])
    task_id, task = start_practice(fx, "S1")
    hinted = task.question.id
    task = engine.submit_answer(task_id, right(hinted, hints_used=1), T0).task
    failed = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert failed.task.outcome == "failed"  # (0.5 + 0 + 1 + 1) / 4
    after = engine.flag(hinted, "", T0)  # without it: (0 + 1 + 1) / 3 — still below 80 %
    assert after.outcome == "failed" and failures(fx.sid("S1")) == {"0": 1}


def test_flag_requires_a_served_question() -> None:
    fx = build_course()
    with pytest.raises(engine.Conflict):
        engine.flag(fx.questions["S1"][0][0], "", T0)
    with pytest.raises(engine.NotFound):
        engine.flag("nope", "", T0)


def test_flag_after_a_passed_lesson_tops_up_xp() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    bad = task.question.id
    task = engine.submit_answer(task_id, wrong(bad), T0).task
    done = finish(task_id, task)
    assert done.task.outcome == "passed" and done.xp_awarded == 8
    after = engine.flag(bad, "", T0)
    assert after.xp_value == 12 and xp_total(fx.course_id) == 12  # 4 / 4 now: 10 + perfect bonus


# ---------------------------------------------------------------------------
# Written proofs (legacy lessons with free-response questions)
# ---------------------------------------------------------------------------


def _make_free_response(*qids: str) -> None:
    for qid in qids:
        db.execute(
            "UPDATE questions SET kind = 'free_response', answer = '', checklist_json = ? WHERE id = ?",
            (db.dumps(["states the definition", "concludes"]), qid),
        )


PROOF = "Let z = a + bi; then z times its conjugate is a^2 + b^2."


def test_free_response_self_grading_points() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0], kp0[2])
    task_id, task = start_practice(fx, "S1")
    qid = task.question.id
    assert task.question.kind == "free_response" and qid == kp0[0]
    with pytest.raises(engine.Invalid):
        engine.submit_answer(task_id, AnswerSubmit(question_id=qid, response="proof"), T0)
    engine.reveal(qid, T0, response=PROOF)  # comparing a committed answer is part of self-assessment
    partial = engine.submit_answer(task_id, AnswerSubmit(question_id=qid, response=PROOF, self_grade="partial"), T0)
    assert partial.status == "self_graded" and not partial.correct and partial.score == 0.5
    assert partial.task.progress.kp_target == 3
    good = engine.submit_answer(task_id, right(partial.task.question.id), T0)
    assert good.independent and good.task.question.id == kp0[2]  # the KP's last planned question: the written one
    engine.reveal(kp0[2], T0, response=PROOF)
    proof = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[2], response=PROOF, self_grade="correct"), T0)
    assert proof.independent and proof.score == 1.0 and proof.task.progress.kp_index == 1


def test_free_response_revealed_before_answering_scores_zero() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0], kp0[1])
    task_id, _ = start_practice(fx, "S1")
    engine.reveal(kp0[0], T0)  # no committed answer: a plain reveal
    copied = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[0], response=PROOF, self_grade="correct"), T0)
    assert copied.correct and not copied.independent and copied.score == 0 and "no points" in copied.feedback
    second = copied.task.question.id
    engine.reveal(second, T0, response="tbd")  # too short to be a committed answer
    short = engine.submit_answer(task_id, AnswerSubmit(question_id=second, response="tbd", self_grade="correct"), T0)
    assert not short.independent and short.score == 0
    rows = db.query("SELECT independent, revealed FROM attempts WHERE task_id = ? ORDER BY rowid", (task_id,))
    assert [(r["independent"], r["revealed"]) for r in rows] == [(0, 1), (0, 1)]


def test_free_response_edited_after_comparing_is_assisted() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0])
    task_id, _ = start_practice(fx, "S1")
    engine.reveal(kp0[0], T0, response="My first attempt at the proof, quite short.")
    engine.reveal(kp0[0], T0)  # 'Show solution' after committing changes nothing
    edited = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[0], response=PROOF, self_grade="correct"), T0)
    assert not edited.independent and edited.score == 0


def test_free_response_comparison_after_commit_is_independent() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0])
    task_id, _ = start_practice(fx, "S1")
    engine.reveal(kp0[0], T0, response=PROOF)
    engine.reveal(kp0[0], T0)  # a later plain reveal (e.g. 'Show solution') doesn't undo the commit
    ok = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[0], response="  " + PROOF + " ", self_grade="correct"), T0)
    assert ok.independent and ok.score == 1.0


def test_free_response_revealed_review_gets_no_credit() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    st = task_state(task_id)
    _make_free_response(st["set"][0])
    engine.reveal(st["set"][0], now)
    result = engine.submit_answer(task_id, AnswerSubmit(question_id=st["set"][0], response=PROOF, self_grade="correct"), now)
    assert not result.independent and "no points" in result.feedback
    assert task_state(task_id)["log"][0]["credit"] == 0.0


def test_proof_kp_serves_the_written_question_second_and_waives_it_after_a_miss() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[3])  # last in pool order
    task_id, task = start_practice(fx, "S1")
    first = engine.submit_answer(task_id, right(task.question.id), T0)
    assert first.task.question.id == kp0[3]  # served no later than the KP's 2nd question
    miss = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[3], response=PROOF, self_grade="incorrect"), T0)
    assert miss.task.phase == "practice" and miss.task.outcome is None
    assert "then try the next one" in miss.feedback and "first review" in miss.feedback
    done = finish(task_id, miss.task)
    assert done.task.outcome == "passed" and done.suggestion is None
    assert done.suggestion_text == engine.PROOF_WAIVED_TEXT
    assert lesson_rating(fx.sid("S1")) == 2
    st = task_state(task_id)
    assert st["proof_waived"] == [0] and st["result"]["proof_waived"] is True
    assert failures(fx.sid("S1")) == {}


@pytest.mark.parametrize("how", ["hint", "reveal", "short", "partial"])
def test_written_answer_with_help_does_not_end_the_lesson_and_waives_the_proof(how: str) -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[1])  # the KP's only written question, served 2nd
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.question.id == kp0[1]
    grade = "partial" if how == "partial" else "correct"
    if how == "hint":
        engine.hint(kp0[1], T0)
    elif how == "reveal":
        engine.reveal(kp0[1], T0)
    else:
        engine.reveal(kp0[1], T0, response="zz̄ = |z|²" if how == "short" else PROOF)
    response = "zz̄ = |z|²" if how == "short" else PROOF
    result = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[1], response=response, self_grade=grade), T0)
    # a "short" commit is no commitment: it is a plain reveal (0 points)
    assert not result.independent and result.score == (0.0 if how in ("reveal", "short") else 0.5)
    assert result.task.phase == "practice" and result.task.outcome is None and result.suggestion is None
    assert "pauses" not in result.feedback
    done = finish(task_id, result.task)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx(0.8 if how in ("reveal", "short") else 0.9)
    assert lesson_rating(fx.sid("S1")) == 2  # Hard: the written proof was waived, even at 90 %
    progress = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert db.loads(progress["kp_failures_json"], {}) == {} and progress["cooldown_until"] is None


def test_assisted_written_answer_serves_another_written_question_if_one_is_left() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[1], kp0[3])
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.question.id == kp0[1]
    engine.hint(kp0[1], T0)
    helped = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[1], response=PROOF, self_grade="correct"), T0)
    assert "another written question comes up soon" in helped.feedback
    assert helped.task.question.id == kp0[3]  # the KP's extra question is the written one
    engine.reveal(kp0[3], T0, response=PROOF)
    proof = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[3], response=PROOF, self_grade="correct"), T0)
    assert proof.independent and proof.task.progress.kp_index == 1
    done = finish(task_id, proof.task)
    assert done.task.outcome == "passed" and done.suggestion_text is None
    assert lesson_rating(fx.sid("S1")) == 3  # Good: 4.5 / 5 and the proof was written unaided


def test_too_short_written_answer_is_half_a_point_even_without_a_reveal() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0])
    task_id, _ = start_practice(fx, "S1")
    short = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[0], response="z = z", self_grade="correct"), T0)
    assert short.correct and not short.independent and short.score == 0.5
    assert "too short" in short.feedback and "hint" not in short.feedback
    assert short.task.phase == "practice"


def test_proof_kp_needs_the_written_answer_beyond_its_plan() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    _make_free_response(kp0[3])  # e.g. a top-up added a written question to the pool mid-lesson
    result = engine.submit_answer(task_id, right(task.question.id), T0)
    assert result.task.progress.kp_index == 0 and result.task.progress.kp_target == 3
    assert "written question" in result.feedback and result.task.question.id == kp0[3]


def test_easier_step_never_swaps_out_the_needed_written_question() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[2])  # tier 2: the KP's only written question
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.question.id == kp0[2] and task.progress.kp_wrong == 1  # served as the KP's 2nd question
    assert task.progress.easier_available is False
    with pytest.raises(engine.Conflict, match="written answer is needed"):
        engine.easier_step(task_id, T0)


# ---------------------------------------------------------------------------
# Hints are recorded server-side (D2)
# ---------------------------------------------------------------------------


def test_hints_are_recorded_server_side_and_survive_a_reload() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    qid = task.question.id
    first = engine.hint(qid, T0)
    assert (first.hint, first.hints_used) == ("h1", 1)
    second = engine.hint(qid, T0)
    assert (second.hint, second.hints_used) == ("h2", 2)
    assert engine.get_task(task_id).question.hints_used == 2
    # a reload resets the client's counter to 0: the server still knows
    result = engine.submit_answer(task_id, right(qid, hints_used=0), T0)
    assert result.correct and not result.independent and result.score == 0.5
    assert db.query_one("SELECT hints_used FROM attempts WHERE task_id = ?", (task_id,))["hints_used"] == 2
    nxt = result.task.question
    assert nxt.hints_used == 0  # the next question starts with no hints; the client's count still counts too
    assert engine.submit_answer(task_id, right(nxt.id, hints_used=1), T0).independent is False


def test_hints_run_out_and_need_a_current_question() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    qid = task.question.id
    for _ in range(3):
        engine.hint(qid, T0)
    again = engine.hint(qid, T0)
    assert (again.hint, again.hints_used) == ("h3", 3)  # capped at the last hint
    with pytest.raises(engine.Conflict):
        engine.hint(fx.questions["S1"][1][0], T0)  # not the current question of an open task
    db.execute("UPDATE questions SET hints_json = '[]' WHERE id = ?", (qid,))
    with pytest.raises(engine.Conflict, match="no hints"):
        engine.hint(qid, T0)
    with pytest.raises(engine.NotFound):
        engine.hint("nope", T0)


def test_review_hint_recorded_server_side_gives_half_credit() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    qid = engine.get_task(task_id).question.id
    engine.hint(qid, now)
    result = engine.submit_answer(task_id, right(qid), now)
    assert result.correct and not result.independent and "half a point" in result.feedback and result.score == 0.5


def test_task_restores_hints_reveal_and_committed_answer_after_a_reload() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[1])
    task_id, task = start_practice(fx, "S1")
    assert (task.question.hints_used, task.question.revealed, task.question.committed_response) == (0, False, None)
    engine.hint(task.question.id, T0)
    engine.hint(task.question.id, T0)
    reloaded = engine.get_task(task_id)
    assert reloaded.question.hints_used == 2 and reloaded.question.revealed is False
    assert reloaded.question.role == "practice" and reloaded.question.steps == []
    engine.reveal(task.question.id, T0)
    assert engine.get_task(task_id).question.revealed is True
    task = engine.submit_answer(task_id, right(task.question.id), T0).task  # 0 points: the lesson goes on (0.8)
    assert task.question.id == kp0[1] and task.question.hints_used == 0 and task.question.revealed is False
    typed = "Let z = a + bi.\n\nThen z times its conjugate is a^2 + b^2."
    engine.reveal(kp0[1], T0, response=typed)
    reloaded = engine.get_task(task_id)
    assert reloaded.question.committed_response == typed.strip()  # as typed, line breaks kept
    assert reloaded.question.revealed is False  # a comparison after a committed answer is not a reveal


def test_progress_reports_kp_wrong_and_whether_an_easier_step_is_available() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    db.execute("UPDATE questions SET tier = 2 WHERE id = ?", (kp0[1],))
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (kp0[3],))
    task_id, task = start_practice(fx, "S1")
    assert (task.progress.kp_wrong, task.progress.easier_available) == (0, False)  # no miss yet
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.question.tier == 2 and task.progress.kp_wrong == 1 and task.progress.easier_available is True
    assert engine.get_task(task_id).progress.easier_available is True  # survives a reload
    swapped = engine.easier_step(task_id, T0)
    assert swapped.progress.easier_available is False
    task = engine.submit_answer(task_id, right(swapped.question.id), T0).task
    assert task.phase == "practice" and task.progress.kp_wrong == 1


# ---------------------------------------------------------------------------
# Grading runs outside the engine lock
# ---------------------------------------------------------------------------


def test_grading_does_not_hold_the_engine_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from app.learning import grading

    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    real = grading.check_answer
    acquired: list[bool] = []

    def slow_check(*args, **kwargs):
        def other() -> None:
            ok = engine.lock.acquire(timeout=2)
            acquired.append(ok)
            if ok:
                engine.lock.release()

        t = threading.Thread(target=other)
        t.start()
        t.join()
        return real(*args, **kwargs)

    monkeypatch.setattr(grading, "check_answer", slow_check)
    result = engine.submit_answer(task_id, right(task.question.id), T0)
    assert acquired == [True] and result.correct


def test_state_change_during_grading_is_a_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.learning import grading

    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    qid = task.question.id
    real = grading.check_answer

    def flag_meanwhile(*args, **kwargs):
        engine.flag(qid, "changed in another tab", T0)
        return real(*args, **kwargs)

    monkeypatch.setattr(grading, "check_answer", flag_meanwhile)
    with pytest.raises(engine.Conflict):
        engine.submit_answer(task_id, right(qid), T0)
    assert not db.query("SELECT 1 FROM attempts WHERE task_id = ? AND status != 'voided'", (task_id,))


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
    assert task.phase == "practice" and task.progress.total_questions == 3 and task.progress.planned == 3
    result = None
    for mark in pattern:
        result = engine.submit_answer(task_id, (right if mark == "1" else wrong)(task.question.id), now)
        task = result.task
    return task_id, result


def test_review_set_mixes_kps_and_prefers_unseen() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    task_id = engine.start_review(fx.sid("S1"), "Review due", T0 + days(3))
    st = task_state(task_id)
    kps = [question(q)["kp_index"] for q in st["set"]]
    assert sorted(set(kps)) == [0, 1] and len(st["set"]) == 3
    seen = {a["question_id"] for a in db.query("SELECT question_id FROM attempts WHERE skill_id = ?", (fx.sid("S1"),))}
    assert not seen & set(st["set"])


@pytest.mark.parametrize(
    "pattern,outcome,xp_awarded,rating",
    [
        ("111", "passed", 5, 3),  # 1.0
        ("011", "passed", 4, 3),  # 5/6: a strong finish counts more
        ("101", "failed", 2, 2),  # 4/6: Hard
        ("110", "failed", 1, 1),  # 3/6: Again
        ("010", "failed", 1, 1),
    ],
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
    assert result.task.progress.score == pytest.approx(task_state(task_id)["result"]["score"], abs=1e-4)
    if outcome == "failed":
        assert result.suggestion == "reread_example"
        assert ("a little shaky" in result.suggestion_text) == (rating == 2)


def test_review_hint_gives_half_credit_and_reveal_none() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    task = engine.get_task(task_id)
    first = engine.submit_answer(task_id, right(task.question.id), now)
    hinted = engine.submit_answer(task_id, right(first.task.question.id, hints_used=2), now)
    assert hinted.correct and not hinted.independent and "half a point" in hinted.feedback
    assert (hinted.task.progress.points, hinted.task.progress.max_points) == (2.0, 3.0)  # 1×1 + 2×0.5 of 1 + 2
    engine.reveal(hinted.task.question.id, now)
    last = engine.submit_answer(task_id, right(hinted.task.question.id), now)
    assert "no points" in last.feedback
    # credits 1, 0.5, 0 -> (1 + 1 + 0) / 6 = 0.33 -> Again
    assert last.task.outcome == "failed" and last.xp_awarded == 1


def test_review_includes_a_capstone_on_every_second_review_weighted_double() -> None:
    fx = build_course()
    cap = add_capstone(fx, "S1")
    learn(fx, "S1", T0)  # the lesson's capstone answered
    now = T0 + days(3)
    first_id = engine.start_review(fx.sid("S1"), "Review due", now)
    assert cap not in task_state(first_id)["set"]  # the first review after the lesson: short questions only
    task = engine.get_task(first_id)
    while task.phase != "done":
        task = answer_fully(first_id, task.question.id, now).task
    later = now + days(10)
    task_id = engine.start_review(fx.sid("S1"), "Review due", later)
    st = task_state(task_id)
    assert len(st["set"]) == 3 and st["set"][-1] == cap  # it replaces one question and comes last
    task = engine.get_task(task_id)
    for _ in range(2):
        assert not task.progress.in_capstone
        task = engine.submit_answer(task_id, right(task.question.id), later).task
    assert task.progress.in_capstone and task.question.role == "capstone"
    engine.submit_answer(task_id, step_submit(cap, 0), later)
    engine.hint(cap, later, step=1)
    engine.submit_answer(task_id, step_submit(cap, 1), later)
    last = engine.submit_answer(task_id, step_submit(cap, 2, correct=False), later)
    assert last.score == pytest.approx(0.5)
    # order weights 1, 2, 3 × 2: (1 + 2 + 6 × 0.5) / 9 = 0.67 → Hard, 2 XP
    assert last.task.outcome == "failed" and last.xp_awarded == 2
    assert task_state(task_id)["log"][-1]["w"] == 2
    assert lesson_rating(fx.sid("S1")) == 3
    rows = db.query("SELECT rating FROM review_log WHERE skill_id = ? AND implicit = 0 ORDER BY created_at, rowid", (fx.sid("S1"),))
    assert [r["rating"] for r in rows][-1] == 2


def test_a_never_answered_capstone_joins_the_first_review() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    cap = add_capstone(fx, "S1")  # e.g. added after the lesson was passed
    task_id = engine.start_review(fx.sid("S1"), "Review due", T0 + days(3))
    assert task_state(task_id)["set"][-1] == cap


def test_flagged_review_capstone_is_replaced_by_a_question() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    cap = add_capstone(fx, "S1")
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    task = engine.get_task(task_id)
    for _ in range(2):
        task = engine.submit_answer(task_id, right(task.question.id), now).task
    assert task.question.id == cap
    task = engine.flag(cap, "", now)
    assert task.question.id != cap and task.question.role == "practice"
    done = engine.submit_answer(task_id, right(task.question.id), now)
    assert done.task.outcome == "passed" and done.xp_awarded == 5


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
    first_q = task_state(task_id)["set"][0]
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
    last_q = task_state(task_id)["set"][2]
    task = engine.flag(last_q, "", now + timedelta(minutes=1))
    assert task.outcome == "passed" and task.xp_value == 5
    assert xp_total(fx.course_id) == 12 + 5  # lesson (10 + perfect bonus) + re-scored review
    rows = db.query("SELECT rating FROM review_log WHERE skill_id = ? AND implicit = 0 ORDER BY created_at", (fx.sid("S1"),))
    assert [r["rating"] for r in rows] == [3, 3]  # lesson pass + the re-scored review (the Again row is gone)
    assert db.query_one("SELECT lapses FROM progress WHERE skill_id = ?", (fx.sid("S1"),))["lapses"] == 0


def test_rescored_review_undoes_stale_fire_effects() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3"):
        learn(fx, key, T0)
    s3_due = T0 + days(30)
    set_due(fx.sid("S3"), s3_due)
    now = T0 + days(3)
    task_id, result = _review(fx, "S1", now, "100")
    assert result.task.outcome == "failed"
    assert scheduler.utc(card(fx.sid("S3")).due) == now + days(1)  # penalty flowed up
    implicit = lambda: db.query(  # noqa: E731
        "SELECT rating FROM review_log WHERE skill_id = ? AND implicit = 1", (fx.sid("S3"),)
    )
    assert [r["rating"] for r in implicit()] == [1]
    for qid in task_state(task_id)["set"][1:]:
        task = engine.flag(qid, "the answer key is wrong", now + timedelta(minutes=1))
    assert task.outcome == "passed"
    assert scheduler.utc(card(fx.sid("S3")).due) == s3_due  # the stale penalty is gone
    assert implicit() == []


def test_review_requires_learned_skill() -> None:
    fx = build_course()
    with pytest.raises(engine.Conflict):
        engine.start_review(fx.sid("S1"), "x", T0)


def test_review_of_a_proof_skill_includes_one_written_question() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    kp0, kp1 = fx.questions["S1"]
    _make_free_response(kp0[1], kp1[1], kp1[2])
    now = T0 + days(3)
    plain = engine.start_review(fx.sid("S1"), "Review due", now)  # not a proof skill: no rule
    engine.abandon(plain, now)
    db.execute("UPDATE skills SET kind = 'proof' WHERE id = ?", (fx.sid("S1"),))
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    st = task_state(task_id)
    kinds = [question(q)["kind"] for q in st["set"]]
    assert kinds.count("free_response") == 1 and len(st["set"]) == 3
    assert {question(q)["kp_index"] for q in st["set"]} == {0, 1}


# ---------------------------------------------------------------------------
# Question top-ups
# ---------------------------------------------------------------------------


def test_top_up_requested_when_unseen_questions_run_low(isolated_engine) -> None:
    fx = build_course()
    _fail_now(fx, "S1", T0)  # 2 answered: 2 unseen left in KP 1 < target + 1
    assert (fx.sid("S1"), 0) in isolated_engine
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


# ---------------------------------------------------------------------------
# Tasks written by earlier versions keep working (in-flight state of a real course)
# ---------------------------------------------------------------------------


def _insert_task(fx, key: str, type_: str, phase: str, st: dict, now=T0) -> str:
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, phase, reason, state_json, created_at)"
        " VALUES (?, ?, ?, ?, 'active', ?, 'old', ?, ?)",
        (task_id, fx.course_id, fx.sid(key), type_, phase, st if isinstance(st, str) else db.dumps(st), scheduler.iso(now)),
    )
    return task_id


def _old_attempt(fx, key: str, task_id: str, qid: str, *, correct: int, independent: int, hints: int = 0, revealed: int = 0) -> None:
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, independent, response, hints_used,"
        " revealed, seconds, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (db.new_id(), task_id, qid, fx.sid(key), correct, "correct" if correct else "incorrect", independent, "x", hints,
         revealed, 3.0, scheduler.iso(T0)),
    )  # fmt: skip


def _old_lesson_state(**overrides) -> dict:
    st = {
        "kp_total": 2, "kp": 0, "playable": [0, 1], "served": [], "slots": {}, "easier": [], "log": [], "wrong_run": 0,
        "self_grades": {}, "fail": None, "result": {}, "current": None, "revealed": False, "hints": 0,
        "compared": None, "compared_raw": None,
    }  # fmt: skip
    st.update(overrides)
    return st


def test_old_active_lesson_state_is_scored_from_its_attempts() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    st = _old_lesson_state(
        served=[kp0[0], kp0[1], kp0[2]], slots={"0": [kp0[0], kp0[1], kp0[2]]}, current=kp0[2],
        log=[
            {"q": kp0[0], "kp": 0, "res": "assisted", "void": False, "scaffold": False},
            {"q": kp0[1], "kp": 0, "res": "correct", "void": False, "scaffold": False},
        ],
    )
    task_id = _insert_task(fx, "S1", "lesson", "practice", st)
    _old_attempt(fx, "S1", task_id, kp0[0], correct=1, independent=0, hints=1)
    _old_attempt(fx, "S1", task_id, kp0[1], correct=1, independent=1)
    task = engine.get_task(task_id)
    p = task.progress
    assert task.question.id == kp0[2] and task.question.step_index == 0
    assert (p.points, p.max_points, p.kp_target, p.planned, p.answered) == (1.5, 2.0, 3, 5, 2)
    done = finish(task_id, task)
    assert done.task.outcome == "passed" and done.task.progress.score == pytest.approx(0.9)
    assert lesson_rating(fx.sid("S1")) == 3


def test_old_state_with_a_revealed_answer_scores_zero_and_can_halt() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    st = _old_lesson_state(
        served=[kp0[0], kp0[1]], slots={"0": [kp0[0], kp0[1]]}, current=kp0[1],
        log=[{"q": kp0[0], "kp": 0, "res": "assisted", "void": False, "scaffold": False}],
    )
    task_id = _insert_task(fx, "S1", "lesson", "practice", st)
    _old_attempt(fx, "S1", task_id, kp0[0], correct=1, independent=0, revealed=1)
    assert engine.get_task(task_id).progress.points == 0.0
    result = engine.submit_answer(task_id, wrong(kp0[1]), T0)
    assert result.task.outcome == "failed"  # 0 + 0, at best 4 / 6


def test_old_learn_phase_lesson_state_takes_the_lesson_targets() -> None:
    fx = build_course()
    set_targets(fx, "S3", [1, 1])
    st = {"kp_total": 2, "kp": 0, "playable": [0, 1], "served": [], "slots": {}, "easier": [], "log": [],
          "wrong_run": 0, "self_grades": {}, "fail": None, "result": {}, "current": None, "revealed": False, "hints": 0}  # fmt: skip
    task_id = _insert_task(fx, "S3", "lesson", "learn", st)
    assert engine.get_task(task_id).progress.planned == 2
    task = engine.begin_practice(task_id, T0)
    assert task.question.kp_index == 0
    done = finish(task_id, task)
    assert done.task.outcome == "passed" and done.task.progress.answered == 2


@pytest.mark.parametrize("raw", ["{}", "not json", "[]", '{"log": [{"q": null}], "slots": [], "served": null}'])
def test_broken_or_empty_old_state_never_crashes(raw: str) -> None:
    fx = build_course()
    task_id = _insert_task(fx, "S1", "lesson", "learn", raw)
    task = engine.get_task(task_id)
    assert task.question is None and task.progress.answered == 0
    task = engine.begin_practice(task_id, T0)
    assert task.question is not None
    assert engine.abandon(task_id, T0).outcome == "abandoned"
    review_id = _insert_task(fx, "S2", "review", "practice", raw)
    assert engine.get_task(review_id).progress.answered == 0


def test_old_review_state_in_progress_finishes_with_the_new_scoring() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    kp0, kp1 = fx.questions["S1"]
    st = {
        "set": [kp0[2], kp1[2], kp1[3]], "pos": 2, "served": [kp0[2], kp1[2], kp1[3]], "self_grades": {}, "result": {},
        "log": [{"q": kp0[2], "res": "wrong", "credit": 0.0, "void": False}, {"q": kp1[2], "res": "correct", "credit": 1.0, "void": False}],
        "current": kp1[3], "revealed": False, "hints": 0, "compared": None, "compared_raw": None,
    }  # fmt: skip
    task_id = _insert_task(fx, "S1", "review", "practice", st, now)
    assert engine.get_task(task_id).progress.points == 2.0  # 1×0 + 2×1
    done = engine.submit_answer(task_id, right(kp1[3]), now)
    assert done.task.outcome == "passed" and done.xp_awarded == 4  # (0 + 2 + 3) / 6 = 0.83 → Good
