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
    assert nxt.phase == "done" and nxt.outcome == "failed"  # scaffolding doesn't count, pool exhausted


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
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.question.tier == 3
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.id == kp0[3]  # lowest unseen tier across KP 1 and KP 2
    after = engine.submit_answer(task_id, right(kp0[3]), T0).task
    assert after.progress.kp_index == 1 and after.progress.streak == 0  # a KP-1 question never counts for KP 2
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


def _make_free_response(*qids: str) -> None:
    for qid in qids:
        db.execute(
            "UPDATE questions SET kind = 'free_response', answer = '', checklist_json = ? WHERE id = ?",
            (db.dumps(["states the definition", "concludes"]), qid),
        )


PROOF = "Let z = a + bi; then z times its conjugate is a^2 + b^2."


def test_free_response_self_grading() -> None:
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
    assert partial.status == "self_graded" and not partial.correct and partial.task.progress.streak == 0
    nxt = partial.task.question
    assert nxt.id == kp0[1]
    good = engine.submit_answer(task_id, right(nxt.id), T0)
    assert good.independent
    # the KP's other written question comes next (the streak alone can't pass a KP with a written question)
    assert good.task.question.id == kp0[2]
    engine.reveal(kp0[2], T0, response=PROOF)
    proof = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[2], response=PROOF, self_grade="correct"), T0)
    assert proof.independent and proof.task.progress.kp_index == 1


def test_free_response_revealed_before_answering_is_assisted() -> None:
    """D1: 'Show solution' before writing an answer, then self-grading 'correct', is not independent."""
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0], kp0[1])
    task_id, task = start_practice(fx, "S1")
    engine.reveal(kp0[0], T0)  # no committed answer: a plain reveal
    copied = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[0], response=PROOF, self_grade="correct"), T0)
    assert copied.correct and not copied.independent and copied.task.progress.streak == 0
    assert "help" in copied.feedback
    # the client flag alone also counts, and a too-short "commit" is not a comparison
    second = copied.task.question.id
    engine.reveal(second, T0, response="tbd")
    short = engine.submit_answer(task_id, AnswerSubmit(question_id=second, response="tbd", self_grade="correct"), T0)
    assert not short.independent
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
    assert not edited.independent


def test_free_response_comparison_after_commit_is_independent() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0])
    task_id, _ = start_practice(fx, "S1")
    engine.reveal(kp0[0], T0, response=PROOF)
    engine.reveal(kp0[0], T0)  # a later plain reveal (e.g. 'Show solution') doesn't undo the commit
    ok = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[0], response="  " + PROOF + " ", self_grade="correct"), T0)
    assert ok.independent and ok.task.progress.streak == 1


def test_free_response_revealed_review_gets_no_credit() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    st = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])
    _make_free_response(st["set"][0])
    engine.reveal(st["set"][0], now)
    result = engine.submit_answer(task_id, AnswerSubmit(question_id=st["set"][0], response=PROOF, self_grade="correct"), now)
    assert not result.independent and "no credit" in result.feedback
    st = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])
    assert st["log"][0]["credit"] == 0.0


def _lesson_rating(skill_id: str) -> int:
    row = db.query_one("SELECT rating FROM review_log WHERE skill_id = ? AND implicit = 0 ORDER BY created_at, rowid", (skill_id,))
    return row["rating"]


def _finish_lesson(task_id: str, task):
    result = None
    while task.phase != "done":
        result = engine.submit_answer(task_id, right(task.question.id), T0)
        task = result.task
    return result


def test_proof_kp_serves_free_response_by_second_question_and_waives_it_after_a_miss() -> None:
    """E1: a KP's only written question self-graded 'incorrect' no longer fails the lesson: the requirement is
    waived, the KP passes on two in a row, and the lesson starts with an earlier first review (Hard)."""
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[3])  # last in pool order
    task_id, task = start_practice(fx, "S1")
    first = engine.submit_answer(task_id, right(task.question.id), T0)
    assert first.task.question.id == kp0[3]  # served no later than the KP's 2nd question
    assert first.task.progress.streak == 1
    miss = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[3], response=PROOF, self_grade="incorrect"), T0)
    assert miss.task.phase == "practice" and miss.task.outcome is None  # not failed: 3 of 5 questions are left
    assert miss.task.progress.streak == 0 and "then try the next one" in miss.feedback
    assert "can still pass on two in a row" in miss.feedback and "first review" in miss.feedback
    task = engine.submit_answer(task_id, right(miss.task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.kp_index == 1  # passed on the plain two-in-a-row rule
    done = _finish_lesson(task_id, task)
    assert done.task.outcome == "passed" and done.suggestion is None
    assert done.suggestion_text == engine.PROOF_WAIVED_TEXT
    assert _lesson_rating(fx.sid("S1")) == 2  # Hard: an earlier first review
    st = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])
    assert st["proof_waived"] == [0] and st["result"]["proof_waived"] is True
    progress = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert db.loads(progress["kp_failures_json"], {}) == {}


@pytest.mark.parametrize("how", ["hint", "reveal", "short", "partial"])
def test_written_answer_with_help_never_fails_the_lesson(how: str) -> None:
    """E1 (QA): a hint, 'Show solution', a too-short committed answer or 'Partly' on the KP's only written question
    used to end the lesson at question 2 of 5 while the feedback said 'it isn't a miss'."""
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
    assert not result.independent
    assert result.task.phase == "practice" and result.task.outcome is None and result.suggestion is None
    if how != "partial":
        assert "isn't a miss" in result.feedback  # consistent: the lesson really goes on
    assert "pauses" not in result.feedback
    task = engine.submit_answer(task_id, right(result.task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.kp_index == 1
    done = _finish_lesson(task_id, task)
    assert done.task.outcome == "passed" and _lesson_rating(fx.sid("S1")) == 2
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
    task = engine.submit_answer(task_id, right(helped.task.question.id), T0).task  # kp0[2]
    assert task.question.id == kp0[3] and task.progress.streak == 1  # the requirement still counts
    engine.reveal(kp0[3], T0, response=PROOF)
    proof = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[3], response=PROOF, self_grade="correct"), T0)
    assert proof.independent and proof.task.progress.kp_index == 1
    done = _finish_lesson(task_id, proof.task)
    assert done.task.outcome == "passed" and done.suggestion_text is None
    assert _lesson_rating(fx.sid("S1")) == 3  # Good: the proof was written unaided


def test_too_short_written_answer_is_not_independent_even_without_a_reveal() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[0])
    task_id, _ = start_practice(fx, "S1")
    short = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[0], response="z = z", self_grade="correct"), T0)
    assert short.correct and not short.independent and "too short" in short.feedback and "With help" not in short.feedback
    assert short.task.phase == "practice"


def test_assisted_answer_that_ends_the_lesson_does_not_claim_it_isnt_a_miss() -> None:
    fx = build_course()
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.phase == "practice"  # two left: still passable
    last = engine.submit_answer(task_id, right(task.question.id, hints_used=1), T0)
    assert last.task.outcome == "failed" and last.suggestion == "retry_later"
    assert "With help" in last.feedback and "isn't a miss" not in last.feedback


def test_proof_kp_two_in_a_row_without_the_written_answer_is_not_a_pass() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[1], kp0[3])
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.question.id == kp0[1]
    engine.reveal(kp0[1], T0, response=PROOF)
    task = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[1], response=PROOF, self_grade="incorrect"), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task  # kp0[2]
    assert task.progress.streak == 1
    assert task.question.id == kp0[3]  # the next correct answer must be the written one
    engine.reveal(kp0[3], T0, response=PROOF)
    done = engine.submit_answer(task_id, AnswerSubmit(question_id=kp0[3], response=PROOF, self_grade="correct"), T0)
    assert done.independent and done.task.progress.kp_index == 1


def test_proof_note_when_streak_is_reached_without_the_written_answer() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    _make_free_response(kp0[3])  # e.g. a top-up added a written question to the pool mid-lesson
    result = engine.submit_answer(task_id, right(task.question.id), T0)
    assert result.task.progress.kp_index == 0 and result.task.progress.streak == 1  # one more (the written one)
    assert "written question" in result.feedback
    assert result.task.question.id == kp0[3]


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
    # a reload resets the client's counter to 0: the server still knows
    result = engine.submit_answer(task_id, right(qid, hints_used=0), T0)
    assert result.correct and not result.independent and result.task.progress.streak == 0
    assert db.query_one("SELECT hints_used FROM attempts WHERE task_id = ?", (task_id,))["hints_used"] == 2
    # the next question starts with no hints; the client-reported count still counts too (max of both)
    nxt = result.task.question.id
    assert engine.submit_answer(task_id, right(nxt, hints_used=1), T0).independent is False


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
    assert result.correct and not result.independent and "half credit" in result.feedback


# ---------------------------------------------------------------------------
# Easier step (D5) and the "question N of up to M" count (D7)
# ---------------------------------------------------------------------------


def test_easier_step_needs_a_wrong_answer_in_the_current_kp() -> None:
    fx = build_course()
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (fx.questions["S1"][0][3],))
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    with pytest.raises(engine.Conflict, match="Try this question first"):
        engine.easier_step(task_id, T0)
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.question.tier == 2
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.tier == 1


def test_scaffold_and_previous_kp_answers_never_pass_the_current_kp() -> None:
    """QA scenario: KP1 tiers [1,1,2,3], KP2 [2,2,3,3]; wrong, wrong, easier -> a KP1 question, right, one KP2
    right must not pass the lesson."""
    fx = build_course()
    kp0, kp1 = fx.questions["S1"]
    for qid, tier in [(kp1[0], 2), (kp1[1], 2), (kp1[2], 3), (kp1[3], 3)]:
        db.execute("UPDATE questions SET tier = ? WHERE id = ?", (tier, qid))
    db.execute(  # a 5th KP-2 question keeps the KP passable after the swap
        "INSERT INTO questions(id, skill_id, key, kp_index, tier, kind, prompt, choices_json, correct_choice, answer,"
        " answer_display, hints_json, solution, created_at) VALUES (?,?,'k2q5',1,3,'numeric','extra','[]',NULL,'5','5','[]','s',?)",
        (db.new_id(), fx.sid("S1"), scheduler.iso(T0)),
    )
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.progress.kp_index == 1
    with pytest.raises(engine.Conflict):
        engine.easier_step(task_id, T0)  # no wrong answer in KP 2 yet
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    swapped = engine.easier_step(task_id, T0)
    assert question(swapped.question.id)["kp_index"] == 0
    task = engine.submit_answer(task_id, right(swapped.question.id), T0).task
    assert task.progress.kp_index == 1 and task.progress.streak == 0 and task.phase == "practice"
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.phase == "practice" and task.progress.streak == 1  # not passed


def test_same_kp_scaffold_does_not_advance_the_streak() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (kp0[3],))
    db.execute("UPDATE questions SET tier = 2 WHERE id = ?", (kp0[1],))
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    task = engine.submit_answer(task_id, right(task.question.id), T0).task  # streak 1
    assert task.progress.streak == 1 and task.question.tier == 2
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.id == kp0[3]
    after = engine.submit_answer(task_id, right(kp0[3]), T0).task
    assert after.progress.streak == 1 and after.progress.kp_index == 0  # neither advanced nor broken


def test_easier_step_never_swaps_out_the_needed_written_question() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[2])  # tier 2: the KP's only written question
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.question.id == kp0[2]  # served as the KP's 2nd question
    with pytest.raises(engine.Conflict, match="written answer is needed"):
        engine.easier_step(task_id, T0)


def test_question_count_is_capped_at_the_kp_limit() -> None:
    fx = build_course()
    sid = fx.sid("S1")
    for n in range(3):
        db.execute(
            "INSERT INTO questions(id, skill_id, key, kp_index, tier, kind, prompt, choices_json, correct_choice, answer,"
            " answer_display, hints_json, solution, created_at) VALUES (?,?,?,0,1,'numeric','extra','[]',NULL,'5','5','[]','s',?)",
            (db.new_id(), sid, f"extra{n}", scheduler.iso(T0)),
        )
    _, task = start_practice(fx, "S1")
    assert task.progress.kp_questions_available == engine.MAX_KP_QUESTIONS  # pool of 7, but at most 5 are served


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


def test_rescored_review_undoes_stale_fire_effects() -> None:
    """A review failed only because of a broken question: once re-scored to a pass, the penalty it put on a
    dependent is undone (and the credit of the new result applied)."""
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
    st = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])
    for qid in st["set"][1:]:
        task = engine.flag(qid, "the answer key is wrong", now + timedelta(minutes=1))
    assert task.outcome == "passed"
    assert scheduler.utc(card(fx.sid("S3")).due) == s3_due  # the stale penalty is gone
    assert implicit() == []


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


# ---------------------------------------------------------------------------
# E3: the server's per-attempt record is handed back to the client
# ---------------------------------------------------------------------------


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
    engine.reveal(task.question.id, T0)
    assert engine.get_task(task_id).question.revealed is True
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.question.id == kp0[1] and task.question.hints_used == 0 and task.question.revealed is False
    typed = "Let z = a + bi.\n\nThen z times its conjugate is a^2 + b^2."
    engine.reveal(kp0[1], T0, response=typed)
    reloaded = engine.get_task(task_id)
    assert reloaded.question.committed_response == typed.strip()  # as typed, line breaks kept
    assert reloaded.question.revealed is False  # a comparison after a committed answer is not a reveal


def test_progress_reports_kp_wrong_and_whether_an_easier_step_is_available() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (kp0[3],))
    task_id, task = start_practice(fx, "S1")
    assert (task.progress.kp_wrong, task.progress.easier_available) == (0, False)  # no miss yet
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.question.tier == 1 and task.progress.kp_wrong == 1
    assert task.progress.easier_available is False  # nothing strictly easier than a tier-1 question
    task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.question.tier == 2 and task.progress.kp_wrong == 1
    assert task.progress.easier_available is True  # an unseen tier-1 question is left
    assert engine.get_task(task_id).progress.easier_available is True  # survives a reload
    swapped = engine.easier_step(task_id, T0)
    assert swapped.progress.easier_available is False  # nothing easier than the swapped-in question
    task = engine.submit_answer(task_id, right(swapped.question.id), T0).task
    assert task.phase == "done" and (task.progress.kp_wrong, task.progress.easier_available) == (0, False)


def test_easier_step_is_not_available_for_the_needed_written_question() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    _make_free_response(kp0[2])
    task_id, task = start_practice(fx, "S1")
    task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
    assert task.question.id == kp0[2] and task.progress.kp_wrong == 1
    assert task.progress.easier_available is False
    with pytest.raises(engine.Conflict, match="won't count as a miss"):
        engine.easier_step(task_id, T0)


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
    st = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])
    kinds = [question(q)["kind"] for q in st["set"]]
    assert kinds.count("free_response") == 1 and len(st["set"]) == 3
    assert {question(q)["kp_index"] for q in st["set"]} == {0, 1}
