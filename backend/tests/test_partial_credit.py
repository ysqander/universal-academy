"""Per-statement credit on true/false sets (the learner, 2026-10-11: "each true or false scored separately"): a set of
three or more statements earns the share it got right — halved with a hint, 0 after the solution — in lessons,
reviews, quizzes and multistep steps; a two-statement set stays all or nothing; a partly right set is a miss (one
extra question) but not a wrong answer (no easier-step offer)."""

from __future__ import annotations

import pytest
from test_guided_steps import RIGHT, answer, guided_course, start
from test_learning_fixtures import T0, build_course, isolated_engine, make_true_false_set, question, task_state  # noqa: F401

from app.learning import engine, quiz
from app.schemas import AnswerSubmit


def _first_kp_question(fx, key="S1"):
    task_id = engine.start_lesson(fx.sid(key), "test", T0)
    task = engine.begin_practice(task_id, T0)
    return task_id, task.question


def test_three_of_four_right_earns_three_quarters_and_a_hint_halves_it() -> None:
    fx = build_course()
    kp0 = fx.questions["S1"][0]
    for qid in kp0:
        make_true_false_set(qid, "FTTF")
    task_id, q = _first_kp_question(fx)
    r = engine.submit_answer(task_id, AnswerSubmit(question_id=q.id, response="FTTT"), T0)
    assert r.status == "partial" and not r.correct and r.score == pytest.approx(0.75)
    assert r.feedback.startswith("3 of 4 statements right: statement 4 is actually false.")
    assert r.suggestion != "easier_step" and r.task.progress.kp_wrong == 0  # partly right is not "wrong"
    assert r.task.progress.points == pytest.approx(0.75) and r.task.progress.kp_target >= 2  # a miss: one more question
    hinted = engine.submit_answer(task_id, AnswerSubmit(question_id=r.task.question.id, response="FTTT", hints_used=1), T0)
    assert hinted.score == pytest.approx(0.375)  # ¾ of the half a hinted answer can earn


def test_a_two_statement_set_is_all_or_nothing() -> None:
    fx = build_course()
    qid = fx.questions["S1"][0][0]
    make_true_false_set(qid, "TF")
    task_id, q = _first_kp_question(fx)
    assert q.id == qid
    r = engine.submit_answer(task_id, AnswerSubmit(question_id=qid, response="TT"), T0)
    assert r.status == "incorrect" and r.score == 0


def test_a_quiz_scores_the_right_statements_of_a_set() -> None:
    fx = build_course()
    qid = fx.questions["S1"][0][0]
    make_true_false_set(qid, "FTTF")
    row = question(qid)
    assert quiz._grade(row, AnswerSubmit(question_id=qid, response="FTTF")) == 1.0
    assert quiz._grade(row, AnswerSubmit(question_id=qid, response="FTFF")) == pytest.approx(0.75)
    assert quiz._grade(row, AnswerSubmit(question_id=qid, response="TFFT")) == 0.0


def test_a_proof_step_set_partly_right_twice_keeps_the_better_try_and_is_partly_right() -> None:
    fx, _ = guided_course()
    task_id = start(fx)
    for i in (2, 3, 4, 5, 6):
        answer(fx, task_id, i, RIGHT[i])
    first = answer(fx, task_id, 7, {"response": "TTT"})  # key FTT: 2 of 3 right
    assert first.check.status == "retry" and "statement" not in first.check.feedback.lower()  # nothing given away
    second = answer(fx, task_id, 7, {"response": "TTT"})
    # the same 2 of 3 again: ½ × ⅔ on the second try, but the first try's ⅔ is kept
    assert second.final and second.check.correct is False and second.check.points == pytest.approx(2 / 3)
    assert "kept from your first try" in second.check.feedback
    entry = task_state(task_id)["log"][-1]
    assert entry["q"] == "block:7" and entry["pts"] == pytest.approx(2 / 3) and entry["res"] == "partial"


def test_a_hinted_partly_right_answer_says_why_its_share_was_halved() -> None:
    fx = build_course()
    for qid in fx.questions["S1"][0]:
        make_true_false_set(qid, "FTTF")
    task_id, q = _first_kp_question(fx)
    r = engine.submit_answer(task_id, AnswerSubmit(question_id=q.id, response="FTTT", hints_used=1), T0)
    assert r.score == pytest.approx(0.375) and "With a hint, so half of that share: 0.38 point." in r.feedback


def test_every_true_false_set_counts_as_a_choice_question_for_the_caps() -> None:
    from app.pipeline.questions import is_choice_based

    assert is_choice_based("true_false_set", 2) and is_choice_based("true_false_set", 5)
    assert not is_choice_based("numeric") and not is_choice_based("expression")
