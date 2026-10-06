"""Quizzes (SPEC §6.7): due every 150 XP + 5 new skills, forced, timed on the server, no help, results at the end,
remedial reviews, FSRS credit, optional retake, question selection, the time-factor setting."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fsrs import Rating
from test_learning_fixtures import (  # noqa: F401
    T0,
    add_question,
    build_course,
    card,
    days,
    isolated_engine,
    learn,
    question,
    right,
    set_due,
    task_state,
    wrong,
)

from app import db
from app.api import study
from app.learning import engine, quiz, scheduler, selector, state, stats, xp
from app.schemas import AnswerSubmit

ROOTS = ["S1", "S2", "R1", "R2", "R3", "R4"]


def make_learned(fx, keys: list[str], at: datetime) -> None:
    """Skills learned at `at` (first FSRS review Good), without going through their lessons."""
    for key in keys:
        sid = fx.sid(key)
        c = scheduler.review(None, sid, Rating.Good, at)
        db.execute(
            "UPDATE progress SET status = 'learned', card_json = ?, lesson_passed_at = ?, lesson_attempts = 1 WHERE skill_id = ?",
            (scheduler.dump_card(c), scheduler.iso(at), sid),
        )
    state.recompute_unlocks(fx.course_id)


def seen_before(fx, key: str, qids: list[str]) -> None:
    """The learner answered these questions in an earlier (finished) review."""
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, phase, outcome, created_at, completed_at)"
        " VALUES (?, ?, ?, 'review', 'completed', 'done', 'passed', ?, ?)",
        (task_id, fx.course_id, fx.sid(key), scheduler.iso(T0 - days(1)), scheduler.iso(T0 - days(1))),
    )
    for qid in qids:
        db.execute(
            "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, independent, created_at)"
            " VALUES (?, ?, ?, ?, 1, 'correct', 1, ?)",
            (db.new_id(), task_id, qid, fx.sid(key), scheduler.iso(T0 - days(1))),
        )


def earn(fx, amount: int, at: datetime) -> None:
    """Course XP from lessons/reviews (a task id that is not a quiz)."""
    xp.award(fx.course_id, f"lesson-{db.new_id()}", amount, at)


def due_course(keys: list[str] = ROOTS, *, extra_roots: int = 4, at: datetime = T0, amount: int = 150):
    fx = build_course(extra_roots=extra_roots)
    make_learned(fx, keys, at)
    earn(fx, amount, at)
    return fx


def start_quiz(fx, now: datetime) -> str:
    task = selector.continue_task(fx.course_id, now)
    assert task.type == "quiz"
    return task.id


def run_quiz(task_id: str, now: datetime, marks: str, *, step: timedelta = timedelta(seconds=20)):
    """Answer the quiz: '1' right, '0' wrong, 's' skip. Returns the last AnswerResult."""
    task = engine.begin_practice(task_id, now)
    result = None
    for i, mark in enumerate(marks):
        qid = task.question.id
        at = now + step * (i + 1)
        if mark == "s":
            result = engine.submit_answer(task_id, AnswerSubmit(question_id=qid, skip=True), at)
        else:
            result = engine.submit_answer(task_id, (right if mark == "1" else wrong)(qid), at)
        task = result.task
    return result


def quiz_rows_of(fx) -> list[dict]:
    return db.query("SELECT * FROM tasks WHERE course_id = ? AND type = 'quiz' ORDER BY rowid", (fx.course_id,))


def do_review(fx, key: str, now: datetime) -> None:
    task_id = engine.start_review(fx.sid(key), "test", now)
    task = engine.get_task(task_id)
    while task.phase != "done":
        task = engine.submit_answer(task_id, right(task.question.id), now).task


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(study.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Due computation
# ---------------------------------------------------------------------------


def test_due_needs_150_xp_and_5_new_skills() -> None:
    fx = build_course(extra_roots=4)
    make_learned(fx, ["S1", "S2", "R1", "R2"], T0)
    earn(fx, 200, T0)
    status = quiz.quiz_status(fx.course_id, T0)
    assert not status.due and status.skills_since == 4 and status.xp_until is None  # 4 skills: not yet

    make_learned(fx, ["R3"], T0 + timedelta(hours=1))
    assert quiz.quiz_status(fx.course_id, T0).due  # 5 skills, 200 XP

    fx = build_course(extra_roots=4)
    make_learned(fx, ["S1", "S2", "R1", "R2", "R3"], T0)
    earn(fx, 149, T0)
    status = quiz.quiz_status(fx.course_id, T0)
    assert (status.due, status.xp_since, status.xp_until) == (False, 149, 1)
    s = stats.compute_stats(fx.course_id, T0)
    assert (s.quiz_due, s.xp_until_quiz, s.last_quiz_score) == (False, 1, None)
    earn(fx, 1, T0)
    s = stats.compute_stats(fx.course_id, T0)
    assert (s.quiz_due, s.xp_until_quiz) == (True, 0)
    session = selector.next_response(fx.course_id, T0).session
    assert (session.quiz_due, session.xp_until_quiz) == (True, 0)


def test_quiz_xp_is_excluded_and_the_counter_restarts_after_a_quiz() -> None:
    fx = due_course(extra_roots=10)
    task_id = start_quiz(fx, T0)
    result = run_quiz(task_id, T0 + timedelta(minutes=1), "111111")
    assert result.xp_awarded == 6  # 6 MC questions -> 6 minutes × 100 %
    after = T0 + timedelta(hours=1)
    status = quiz.quiz_status(fx.course_id, after)
    assert not status.due and status.xp_since == 0 and status.xp_until is None  # quiz XP doesn't count
    assert stats.compute_stats(fx.course_id, after).last_quiz_score == 1.0

    # 150 XP but only 4 new skills since the quiz: not due; the 5th makes it due
    earn(fx, 150, after)
    make_learned(fx, ["R5", "R6", "R7", "R8"], after)
    assert not quiz.quiz_status(fx.course_id, after).due
    make_learned(fx, ["R9"], after)
    assert quiz.quiz_status(fx.course_id, after).due


def test_not_due_when_too_few_learned_skills_have_quiz_questions() -> None:
    fx = due_course(["S1", "S2", "R1", "R2", "R3"])
    for qid in [q for kp in fx.questions["R3"] for q in kp]:
        db.execute("UPDATE questions SET kind = 'free_response' WHERE id = ?", (qid,))
    status = quiz.quiz_status(fx.course_id, T0)
    assert not status.due and status.eligible_skills == 4 and status.xp_until is None
    assert quiz.build_plan(fx.course_id, T0) is None


# ---------------------------------------------------------------------------
# Forced
# ---------------------------------------------------------------------------


def test_forced_menu_continue_and_manual_start_over_http(client: TestClient) -> None:
    fx = due_course()
    cid = fx.course_id
    nxt = client.get(f"/api/courses/{cid}/next").json()
    assert nxt["task"] is None and nxt["session"]["quiz_due"] is True
    assert nxt["options"] == [
        {
            "skill": None, "type": "quiz", "xp_value": 6, "importance": quiz.QUIZ_OPTION_IMPORTANCE,
            "reason": "Quiz — 150 XP since you started: 6 questions, about 6 minutes",
        }
    ]  # fmt: skip

    refused = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3"), "type": "lesson", "force": True})
    assert refused.status_code == 409 and refused.json()["detail"] == "Take the quiz first — about 6 minutes."
    refused = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S1"), "type": "review"})
    assert refused.status_code == 409

    task = client.post(f"/api/courses/{cid}/continue").json()
    assert (task["type"], task["phase"], task["skill"], task["question"], task["lesson"]) == ("quiz", "learn", None, None, None)
    info = task["quiz"]
    assert info["kind"] == "quiz" and info["question_count"] == 6 and info["time_limit_seconds"] == 360
    assert info["started_at"] is None and info["deadline"] is None and info["seconds_left"] is None
    assert [s["key"] for s in info["skills"]] == ROOTS and task["xp_value"] == 6
    assert task["reason"] == "Quiz — 150 XP since you started: 6 questions, about 6 minutes"

    assert client.post(f"/api/tasks/{task['id']}/abandon").status_code == 409
    nxt = client.get(f"/api/courses/{cid}/next").json()
    assert nxt["task"]["id"] == task["id"] and nxt["options"] == []
    assert client.post(f"/api/courses/{cid}/continue").json()["id"] == task["id"]
    quiz_again = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": "", "type": "quiz"})
    assert quiz_again.status_code == 200 and quiz_again.json()["id"] == task["id"]
    other = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3"), "replace_active": True, "force": True})
    assert other.status_code == 409 and other.json()["detail"] == "Take the quiz first — about 6 minutes."

    started = client.post(f"/api/tasks/{task['id']}/practice").json()
    assert started["phase"] == "practice" and started["quiz"]["started_at"] and started["question"] is not None
    assert 0 < started["quiz"]["seconds_left"] <= 360
    left = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3")})
    assert left.status_code == 409 and left.json()["detail"].startswith("Finish the quiz first — about ")
    assert client.post(f"/api/tasks/{task['id']}/abandon").status_code == 409


def test_the_active_task_is_finished_first() -> None:
    fx = build_course(extra_roots=4)
    make_learned(fx, ROOTS, T0)
    lesson = selector.start_task(fx.course_id, fx.sid("S3"), "lesson", T0)
    earn(fx, 150, T0)  # the quiz becomes due while the lesson is open
    nxt = selector.next_response(fx.course_id, T0)
    assert nxt.task.id == lesson.id and [(o.type, o.skill) for o in nxt.options] == [("quiz", None)]
    assert selector.continue_task(fx.course_id, T0).id == lesson.id
    assert selector.start_task(fx.course_id, fx.sid("S3"), "lesson", T0).id == lesson.id  # resuming is fine
    with pytest.raises(engine.Conflict, match="Take the quiz first"):
        selector.start_task(fx.course_id, fx.sid("S4"), "lesson", T0, force=True)
    task = engine.begin_practice(lesson.id, T0)
    while task.phase != "done":
        task = engine.submit_answer(lesson.id, right(task.question.id), T0).task
    nxt = selector.next_response(fx.course_id, T0)
    assert nxt.task is None and [o.type for o in nxt.options] == ["quiz"]
    assert selector.continue_task(fx.course_id, T0).type == "quiz"


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------


def test_intro_then_the_clock_runs_on_the_server() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    later = T0 + timedelta(minutes=30)  # the intro does not run the clock
    assert engine.get_task(task_id, later).quiz.seconds_left is None
    task = engine.begin_practice(task_id, later)
    assert task.quiz.started_at == scheduler.iso(later)
    assert task.quiz.deadline == scheduler.iso(later + timedelta(seconds=360))
    assert engine.get_task(task_id, later + timedelta(seconds=100)).quiz.seconds_left == 260
    again = engine.begin_practice(task_id, later + timedelta(seconds=50))  # a second /practice changes nothing
    assert again.quiz.started_at == task.quiz.started_at and again.question.id == task.question.id


def test_answers_after_the_deadline_are_refused_and_finish_the_quiz() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    task = engine.begin_practice(task_id, T0)
    deadline = T0 + timedelta(seconds=360)
    first = task.question.id
    task = engine.submit_answer(task_id, right(first), T0 + timedelta(seconds=30)).task
    # within the grace period an answer still counts
    task = engine.submit_answer(task_id, right(task.question.id), deadline + timedelta(seconds=1)).task
    third = task.question.id
    with pytest.raises(engine.Conflict, match="Time is up."):
        engine.submit_answer(task_id, right(third), deadline + timedelta(seconds=5))
    done = engine.get_task(task_id, deadline + timedelta(seconds=6))
    assert done.phase == "done" and done.outcome == "failed" and done.quiz.seconds_left is None
    results = done.quiz.results
    assert [r.answered for r in results] == [True, True, False, False, False, False]
    assert [r.score for r in results] == [1, 1, 0, 0, 0, 0]
    assert all(r.correct_answer and r.solution for r in results) and results[0].response == "B"
    assert done.progress.score == pytest.approx(2 / 6) and done.xp_value == 2  # round(6 min × 1/3)
    row = db.query_one("SELECT completed_at FROM tasks WHERE id = ?", (task_id,))
    assert row["completed_at"] == scheduler.iso(deadline)  # it ended when the time ran out
    shown = db.query("SELECT question_id, response FROM attempts WHERE task_id = ? ORDER BY rowid", (task_id,))
    assert [a["question_id"] for a in shown][2] == third and shown[2]["response"] == ""  # seen, unanswered
    assert [a["question_id"] for a in shown] == task_state(task_id)["set"]  # the results show all: all seen
    with pytest.raises(engine.Conflict, match="Time is up."):
        engine.submit_answer(task_id, right(third), deadline + timedelta(seconds=10))


def test_reading_the_task_or_the_menu_finishes_an_expired_quiz() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    engine.begin_practice(task_id, T0)
    late = T0 + timedelta(hours=3)
    nxt = selector.next_response(fx.course_id, late)
    assert nxt.task is None and not nxt.session.quiz_due
    done = engine.get_task(task_id, late)
    assert done.phase == "done" and done.quiz.results and all(not r.answered and r.score == 0 for r in done.quiz.results)
    assert done.xp_value == 0 and len(done.quiz.missed_skills) == 6

    fx = due_course()
    task_id = start_quiz(fx, T0)
    engine.begin_practice(task_id, T0)
    assert engine.get_task(task_id, T0 + timedelta(minutes=7)).phase == "done"


# ---------------------------------------------------------------------------
# Conduct
# ---------------------------------------------------------------------------


def test_nothing_about_correctness_leaks_before_the_end() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    task = engine.begin_practice(task_id, T0)
    q = task.question
    assert q.hints == [] and q.checklist == [] and q.source_exercise is None
    for fn in (lambda: engine.hint(q.id, T0), lambda: engine.reveal(q.id, T0), lambda: engine.easier_step(task_id, T0)):
        with pytest.raises(engine.Conflict):
            fn()
    for submit in (right(q.id), wrong(task.question.id)):
        task = engine.get_task(task_id, T0)
        submit.question_id = task.question.id
        result = engine.submit_answer(task_id, submit, T0 + timedelta(seconds=10))
        assert (result.status, result.correct, result.independent, result.score) == ("recorded", False, False, 0)
        assert (result.correct_answer, result.correct_choice, result.solution) == ("", None, "")
        assert result.feedback == "Answer saved." and result.step_result is None
        p = result.task.progress
        assert (p.correct, p.points, p.score) == (0, 0, 0) and result.task.quiz.results == []
        assert result.task.quiz.missed_skills == [] and result.task.outcome is None
    assert result.task.progress.answered == 2 and result.task.progress.total_questions == 6


def test_skip_scores_zero_and_a_flag_voids_the_question() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    task = engine.begin_practice(task_id, T0)
    skipped = engine.submit_answer(task_id, AnswerSubmit(question_id=task.question.id, skip=True), T0 + timedelta(seconds=5))
    assert skipped.status == "recorded" and skipped.feedback == "Skipped — no point for this one."
    flagged_id = skipped.task.question.id
    flagged_skill = question(flagged_id)["skill_id"]
    task = engine.flag(flagged_id, "the key is wrong", T0 + timedelta(seconds=10))
    assert task.question.id != flagged_id and task.quiz.question_count == 5 and task.progress.total_questions == 5
    assert flagged_skill not in [s.id for s in task.quiz.skills]
    result = None
    while task.phase != "done":
        result = engine.submit_answer(task_id, right(task.question.id), T0 + timedelta(seconds=30))
        task = result.task
    assert result.feedback == "Answer saved. Quiz finished: 4 of 5 right (80%)."
    assert task.outcome == "passed" and len(task.quiz.results) == 5 and task.progress.score == pytest.approx(0.8)
    assert flagged_id not in [r.question_id for r in task.quiz.results]
    first = task.quiz.results[0]
    assert (first.answered, first.response, first.score) == (False, None, 0)
    assert [s.key for s in task.quiz.missed_skills] == [first.skill.key]
    assert result.xp_awarded == 5 and not task.quiz.retake_available  # round(6 min × 0.8): the time limit stays
    assert db.query_one("SELECT flagged FROM questions WHERE id = ?", (flagged_id,))["flagged"] == 1


def test_a_flag_after_the_quiz_rescores_it() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    done = run_quiz(task_id, T0, "011111").task
    assert done.outcome == "passed" and done.xp_value == 5  # 5/6 = 83 %
    missed = done.quiz.results[0]
    task = engine.flag(missed.question_id, "", T0 + timedelta(minutes=5))
    assert task.outcome == "passed" and task.progress.score == 1.0 and task.xp_value == 6 and task.quiz.missed_skills == []
    assert scheduler.remedial_info(missed.skill.id) == {}
    assert db.query_one("SELECT SUM(xp) AS n FROM xp_log WHERE task_id = ?", (task_id,))["n"] == 6


def test_an_unsimplified_right_value_scores_zero_and_unreadable_input_is_asked_again() -> None:
    fx = due_course()
    for key in ROOTS:  # make every quiz question an expression question
        db.execute("UPDATE questions SET kind = 'expression', answer = '3+4*i', answer_display = '$3+4i$', choices_json = '[]',"
                   " correct_choice = NULL WHERE skill_id = ?", (fx.sid(key),))  # fmt: skip
    task_id = start_quiz(fx, T0)
    task = engine.begin_practice(task_id, T0)
    assert task.quiz.time_limit_seconds == 540  # 6 × 90 s
    qid = task.question.id
    with pytest.raises(engine.Invalid):
        engine.submit_answer(task_id, AnswerSubmit(question_id=qid, response="3+(("), T0)
    result = engine.submit_answer(task_id, AnswerSubmit(question_id=qid, response="(1+2i)*(1+2i) + 6"), T0)
    assert result.status == "recorded" and result.feedback == "Answer saved."
    task = result.task
    while task.phase != "done":
        task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert [r.score for r in task.quiz.results] == [0, 1, 1, 1, 1, 1]
    assert task.quiz.results[0].response == "(1+2i)*(1+2i) + 6"


# ---------------------------------------------------------------------------
# Afterwards: remedial reviews, FSRS credit, retake
# ---------------------------------------------------------------------------


def test_missed_skills_are_reviewed_first_and_right_answers_due_soon_count_as_reviews() -> None:
    fx = due_course()
    now = T0 + days(1)
    set_due(fx.sid("S2"), now + days(2))  # due within 3 days: a right answer is an FSRS review
    set_due(fx.sid("R1"), now + days(10))  # not due soon: only recorded
    set_due(fx.sid("R2"), now + days(10))
    before = {key: card(fx.sid(key)) for key in ROOTS}
    task_id = start_quiz(fx, now)
    end = now + timedelta(minutes=5)
    result = run_quiz(task_id, now, "011100")  # S1 wrong, S2 R1 R2 right, R3 R4 wrong
    done = result.task
    assert [r.skill.key for r in done.quiz.results] == ROOTS
    assert done.outcome == "failed" and done.progress.score == pytest.approx(0.5) and result.xp_awarded == 3
    assert [s.key for s in done.quiz.missed_skills] == ["S1", "R3", "R4"]
    assert result.suggestion_text.startswith("S1, R3 and R4 are due as reviews now")
    assert "retake" in result.suggestion_text and not done.quiz.retake_available
    finished = db.parse_iso(db.query_one("SELECT completed_at FROM tasks WHERE id = ?", (task_id,))["completed_at"])
    assert finished <= end
    for key in ("S1", "R3", "R4"):
        assert scheduler.is_due(card(fx.sid(key)), finished)
        assert scheduler.remedial_info(fx.sid(key))["quiz"] == task_id
    explicit = {r["skill_id"] for r in db.query("SELECT skill_id FROM review_log WHERE created_at > ? AND implicit = 0", (scheduler.iso(now),))}
    assert explicit == {fx.sid("S2")}
    assert card(fx.sid("S2")).last_review > before["S2"].last_review
    assert card(fx.sid("R1")).due == before["R1"].due  # no FSRS change
    assert db.query_one("SELECT reviews FROM progress WHERE skill_id = ?", (fx.sid("S2"),))["reviews"] == 1

    options = selector.rank(fx.course_id, finished + timedelta(minutes=1))
    day = quiz.quiz_day(finished)
    assert [(c.skill["key"], c.type) for c in options[:3]] == [("S1", "review"), ("R3", "review"), ("R4", "review")]
    assert options[0].reason == f"Missed in the quiz on {day}"
    nxt = selector.next_response(fx.course_id, finished + timedelta(minutes=1))
    assert nxt.options[0].reason == f"Missed in the quiz on {day}" and not nxt.session.quiz_due
    assert all(o.type != "quiz" for o in nxt.options)  # no retake before the remedial reviews


def test_a_skill_missed_in_the_quiz_gets_no_implicit_credit_from_a_dependent_lesson() -> None:
    fx = due_course()
    now = T0 + days(1)
    quiz_id = start_quiz(fx, now)
    run_quiz(quiz_id, now, "011111")  # S1 missed
    later = now + timedelta(hours=1)
    lesson_id = engine.start_lesson(fx.sid("S3"), "test", later)  # S3 practises S1 (w 0.8): normally a FIRe credit
    task = engine.begin_practice(lesson_id, later)
    while task.phase != "done":
        task = engine.submit_answer(lesson_id, right(task.question.id), later).task
    assert task.outcome == "passed"
    assert scheduler.is_due(card(fx.sid("S1")), later) and scheduler.remedial_info(fx.sid("S1"))["quiz"] == quiz_id
    assert selector.rank(fx.course_id, later)[0].skill["key"] == "S1"
    do_review(fx, "S1", later)
    assert scheduler.remedial_info(fx.sid("S1")) == {} and not engine.get_task(quiz_id, later).quiz.retake_available


def test_retake_after_the_remedial_reviews_is_optional_and_does_not_reset_the_counter() -> None:
    fx = due_course()
    now = T0 + days(1)
    quiz_id = start_quiz(fx, now)
    original = run_quiz(quiz_id, now, "011100").task
    original_set = task_state(quiz_id)["set"]
    later = now + timedelta(hours=1)
    do_review(fx, "S1", later)
    do_review(fx, "R3", later)
    assert not engine.get_task(quiz_id, later).quiz.retake_available
    with pytest.raises(engine.Conflict, match="No quiz is due"):
        selector.start_task(fx.course_id, "", "quiz", later)
    do_review(fx, "R4", later)
    assert engine.get_task(quiz_id, later).quiz.retake_available

    nxt = selector.next_response(fx.course_id, later)
    assert not nxt.session.quiz_due and nxt.options[-1].type == "quiz" and nxt.options[-1].skill is None
    assert nxt.options[-1].reason.startswith(f"Optional retake of the quiz on {quiz.quiz_day(now)} (50%): 6 questions")
    assert len(nxt.options) > 1 and selector.continue_task(fx.course_id, later).type != "quiz"  # never forced

    fx2_lesson = engine.active_task_row(fx.course_id)
    engine.abandon(fx2_lesson["id"], later)
    retake = selector.start_task(fx.course_id, "", "quiz", later)
    assert retake.quiz.kind == "retake" and retake.phase == "learn"
    assert [s.key for s in retake.quiz.skills] == [s.key for s in original.quiz.skills]
    assert not set(task_state(retake.id)["set"]) & set(original_set)  # fresh questions
    assert engine.abandon(retake.id, later).outcome == "abandoned"  # optional: may be left before the clock starts
    assert engine.get_task(quiz_id, later).quiz.retake_available  # ... and stays on offer
    assert quiz.retake_offer(fx.course_id, later) is not None


def test_a_running_retake_cannot_be_left_and_does_not_count_as_the_quiz() -> None:
    fx = due_course()
    now = T0 + days(1)
    quiz_id = start_quiz(fx, now)
    run_quiz(quiz_id, now, "111100")
    later = now + timedelta(hours=1)
    for key in ("R3", "R4"):
        do_review(fx, key, later)
    retake = selector.start_task(fx.course_id, "", "quiz", later)
    engine.begin_practice(retake.id, later)
    with pytest.raises(engine.Conflict):
        engine.abandon(retake.id, later)
    with pytest.raises(engine.Conflict, match="Finish or leave the quiz retake first"):
        selector.start_task(fx.course_id, fx.sid("S3"), "lesson", later)
    done = run_quiz(retake.id, later, "111111").task  # begin_practice again only checks the clock
    assert done.quiz.kind == "retake" and done.outcome == "passed" and not done.quiz.retake_available
    assert done.xp_value == done.quiz.time_limit_seconds // 60  # 100 %: one XP per minute of the time limit
    status = quiz.quiz_status(fx.course_id, later + timedelta(minutes=10))
    assert status.since == db.query_one("SELECT completed_at FROM tasks WHERE id = ?", (quiz_id,))["completed_at"]
    review_xp = db.query_one(
        "SELECT SUM(x.xp) AS n FROM xp_log x JOIN tasks t ON t.id = x.task_id WHERE t.type = 'review'"
    )["n"]
    assert status.last_score == 1.0 and status.xp_since == review_xp  # the retake's XP is excluded too
    assert not engine.get_task(quiz_id, later).quiz.retake_available  # one retake per quiz
    assert quiz.retake_offer(fx.course_id, later) is None


# ---------------------------------------------------------------------------
# Question selection
# ---------------------------------------------------------------------------


def test_one_single_answer_question_per_skill_unseen_first_tier_two_preferred() -> None:
    fx = due_course()
    s1 = fx.questions["S1"]
    add_question(fx, "S1", 0, kind="multistep", tier=2, steps=[{"prompt": "x", "kind": "numeric", "answer": "1"}])
    add_question(fx, "S1", 0, kind="free_response", tier=2)
    db.execute("UPDATE questions SET kind = 'free_response' WHERE id = ?", (s1[0][2],))  # the tier-2 MC of KP 0
    seen_before(fx, "S1", [s1[1][2]])  # KP 1's tier-2 MC: answered before
    plan = quiz.build_plan(fx.course_id, T0)
    assert [s["key"] for s in plan.skills] == ROOTS and len({q["skill_id"] for q in plan.questions}) == 6
    assert all(q["kind"] in quiz.QUIZ_KINDS for q in plan.questions)
    by_skill = {q["skill_id"]: q for q in plan.questions}
    assert by_skill[fx.sid("S1")]["id"] == s1[0][3]  # unseen, tier 3 (tier 2 left: free response / seen)
    assert by_skill[fx.sid("S2")]["id"] == fx.questions["S2"][0][2]  # unseen tier 2 first


def test_tier_nudge_follows_the_last_quiz_score() -> None:
    assert [quiz.tier_rank(t, None) for t in (1, 2, 3)] == [1, 0, 0]
    assert [quiz.tier_rank(t, 0.95) for t in (1, 2, 3)] == [2, 1, 0]
    assert [quiz.tier_rank(t, 0.5) for t in (1, 2, 3)] == [0, 0, 1]
    fx = due_course(extra_roots=10)
    task_id = start_quiz(fx, T0)
    run_quiz(task_id, T0, "111111")  # 100 % -> harder next time
    later = T0 + days(1)
    make_learned(fx, ["R5", "R6", "R7", "R8", "R9"], later)
    earn(fx, 150, later)
    plan = quiz.build_plan(fx.course_id, later)
    assert {q["tier"] for q in plan.questions} == {3}

    fx = due_course(extra_roots=10)
    task_id = start_quiz(fx, T0)
    run_quiz(task_id, T0, "100000")  # 17 % -> easier next time
    make_learned(fx, ["R5", "R6", "R7", "R8", "R9"], later)
    plan = quiz.build_plan(fx.course_id, later)
    assert {q["tier"] for q in plan.questions} == {1}


def test_new_skills_first_then_the_lowest_recall_and_six_to_eight_questions() -> None:
    fx = build_course(extra_roots=10)  # 14 skills
    make_learned(fx, ["S1", "S2", "R1", "R2", "R3", "R4"], T0)
    earn(fx, 150, T0)
    task_id = start_quiz(fx, T0)
    run_quiz(task_id, T0, "111111")
    later = T0 + days(5)
    make_learned(fx, ["R2"], T0 - days(40))  # an old skill with low recall (re-learned long ago)
    make_learned(fx, ["R5", "R6", "R7", "R8", "R9"], later)
    earn(fx, 150, later)
    plan = quiz.build_plan(fx.course_id, later)
    assert len(plan.questions) == 6 and {s["key"] for s in plan.skills} == {"R2", "R5", "R6", "R7", "R8", "R9"}
    assert [s["key"] for s in plan.skills] == ["R2", "R5", "R6", "R7", "R8", "R9"]  # asked in learning order

    make_learned(fx, ["R10", "S3", "S4"], later)
    plan = quiz.build_plan(fx.course_id, later)  # 8 new skills: 8 questions, all new
    assert len(plan.questions) == 8 and "R2" not in {s["key"] for s in plan.skills}

    small = build_course(extra_roots=4)
    make_learned(small, ["S1", "S2", "R1", "R2", "R3"], T0)  # only 5 learned skills: 5 questions
    assert len(quiz.build_plan(small.course_id, T0).questions) == 5


def test_top_ups_are_requested_for_skills_with_few_unseen_questions(isolated_engine) -> None:
    fx = due_course()
    seen_before(fx, "R4", fx.questions["R4"][0] + fx.questions["R4"][1][:3])
    start_quiz(fx, T0)
    assert (fx.sid("R4"), None) in isolated_engine and (fx.sid("S1"), None) not in isolated_engine


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_time_factor_setting_over_http(client: TestClient) -> None:
    fx = due_course()
    assert client.put("/api/settings", json={"quiz_time_factor": 1.5}).json() == {"daily_goal": 30, "quiz_time_factor": 1.5}
    assert client.put("/api/settings", json={"quiz_time_factor": 0.4}).status_code == 422
    assert client.put("/api/settings", json={"quiz_time_factor": 3.5}).status_code == 422
    assert quiz.time_limit_seconds(["numeric", "multiple_choice"], 1.0) == 180  # 150 s -> 3 minutes
    assert quiz.time_limit_seconds(["multiple_choice"] * 6, 0.5) == 180
    task = client.post(f"/api/courses/{fx.course_id}/continue").json()
    assert task["quiz"]["time_limit_seconds"] == 540  # 6 × 60 s × 1.5
    client.put("/api/settings", json={"quiz_time_factor": 2})
    assert client.get(f"/api/tasks/{task['id']}").json()["quiz"]["time_limit_seconds"] == 720  # the intro follows it
    started = client.post(f"/api/tasks/{task['id']}/practice").json()
    assert started["quiz"]["time_limit_seconds"] == 720
    client.put("/api/settings", json={"quiz_time_factor": 1})
    assert client.get(f"/api/tasks/{task['id']}").json()["quiz"]["time_limit_seconds"] == 720  # frozen once started
    assert client.get("/api/settings").json() == {"daily_goal": 30, "quiz_time_factor": 1.0}


def test_full_quiz_over_http(client: TestClient) -> None:
    fx = due_course()
    task = client.post(f"/api/courses/{fx.course_id}/continue").json()
    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    qid = task["question"]["id"]
    assert client.post(f"/api/questions/{qid}/hint").status_code == 409
    assert client.post(f"/api/questions/{qid}/reveal").status_code == 409
    assert client.post(f"/api/tasks/{task['id']}/easier").status_code == 409
    stale = client.post(f"/api/tasks/{task['id']}/answer", json={"question_id": fx.questions["S3"][0][0], "response": "1"})
    assert stale.status_code == 409
    result = None
    while task["phase"] != "done":
        body = {"question_id": task["question"]["id"], "choice": question(task["question"]["id"])["correct_choice"]}
        result = client.post(f"/api/tasks/{task['id']}/answer", json=body).json()
        assert result["status"] == "recorded"
        task = result["task"]
    assert result["xp_awarded"] == 6 and task["outcome"] == "passed" and task["skill"] is None
    assert len(task["quiz"]["results"]) == 6 and all(r["score"] == 1 for r in task["quiz"]["results"])
    assert client.post(f"/api/questions/{qid}/reveal").status_code == 200  # solutions are open after the end
    nxt = client.get(f"/api/courses/{fx.course_id}/next").json()
    assert not nxt["session"]["quiz_due"] and all(o["type"] != "quiz" for o in nxt["options"])


# ---------------------------------------------------------------------------
# Review findings (regressions)
# ---------------------------------------------------------------------------


def progress_row(fx, key: str) -> dict:
    return db.query_one("SELECT card_json, status, reviews FROM progress WHERE skill_id = ?", (fx.sid(key),))


def explicit_reviews(fx, key: str, after: datetime) -> int:
    return db.query_one(
        "SELECT COUNT(*) AS n FROM review_log WHERE skill_id = ? AND implicit = 0 AND created_at > ?",
        (fx.sid(key), scheduler.iso(after)),
    )["n"]


def test_questions_never_reached_count_as_seen_so_the_remedial_review_serves_others() -> None:
    fx = due_course()
    for key in ROOTS:  # KP 0: only the tier-2 MC (the quiz's pick) not answered before
        kp0 = fx.questions[key][0]
        seen_before(fx, key, [kp0[0], kp0[1], kp0[3]])
    task_id = start_quiz(fx, T0)
    assert task_state(task_id)["set"] == [fx.questions[key][0][2] for key in ROOTS]
    task = engine.begin_practice(task_id, T0)
    task = engine.submit_answer(task_id, right(task.question.id), T0 + timedelta(seconds=30)).task
    late = T0 + timedelta(seconds=400)  # the time ran out with the 2nd question on screen
    with pytest.raises(engine.Conflict, match="Time is up."):
        engine.submit_answer(task_id, right(task.question.id), late)
    done = engine.get_task(task_id, late)
    assert [r.answered for r in done.quiz.results] == [True] + [False] * 5
    assert all(r.correct_answer and r.solution for r in done.quiz.results)  # every answer was shown
    for key, qid in zip(ROOTS, task_state(task_id)["set"]):
        assert qid in engine._answered_ids(fx.sid(key))
    for key, qid in zip(ROOTS[1:], task_state(task_id)["set"][1:]):  # the missed skills' remedial reviews
        assert scheduler.remedial_info(fx.sid(key))["quiz"] == task_id
        review_id = engine.start_review(fx.sid(key), "test", late)
        assert qid not in task_state(review_id)["set"]
        engine.abandon(review_id, late)


def test_course_stats_finish_a_quiz_whose_time_ran_out() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    engine.begin_practice(task_id, T0)
    running = stats.compute_stats(fx.course_id, T0 + timedelta(minutes=1))
    assert running.quiz_due and running.due_reviews == 0
    late = T0 + timedelta(hours=1)
    s = stats.compute_stats(fx.course_id, late)  # read before /next (library card, course header)
    assert not s.quiz_due and s.due_reviews == 6 and s.last_quiz_score == 0.0
    row = db.query_one("SELECT status, outcome FROM tasks WHERE id = ?", (task_id,))
    assert (row["status"], row["outcome"]) == ("completed", "failed")


def test_the_quiz_reason_follows_the_time_setting_until_the_clock_starts() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    reason = "Quiz — 150 XP since you started: 6 questions, about {} minutes"
    assert engine.get_task(task_id, T0).reason == reason.format(6)
    quiz.set_time_factor(2.0)
    assert engine.get_task(task_id, T0).reason == reason.format(12)
    assert selector.next_response(fx.course_id, T0).task.reason == reason.format(12)  # the course page's card
    started = engine.begin_practice(task_id, T0)
    assert started.reason == reason.format(12) and started.quiz.time_limit_seconds == 720
    quiz.set_time_factor(1.0)  # frozen once the clock runs
    assert engine.get_task(task_id, T0 + timedelta(seconds=10)).reason == reason.format(12)
    assert db.query_one("SELECT reason FROM tasks WHERE id = ?", (task_id,))["reason"] == reason.format(12)
    assert quiz._reason_with_size("Quiz: 6 questions, about 6 minutes", 1, 1) == "Quiz: 1 question, about 1 minute"


def _missed_prerequisites_course():
    """R1-R6 (learned) are prerequisites (w 1) of the unlearned S1, which unlocks R7-R20 (and S3, S4): 16 others."""
    fx = build_course(extra_roots=20)
    edges = [(f"R{n}", "S1") for n in range(1, 7)] + [("S1", f"R{n}") for n in range(7, 21)]
    for src, dst in edges:
        db.execute(
            "INSERT INTO edges(course_id, src, dst, weight, reduced) VALUES (?,?,?,?,1)",
            (fx.course_id, fx.sid(src), fx.sid(dst), 1.0),
        )
    make_learned(fx, [f"R{n}" for n in range(1, 7)], T0)
    earn(fx, 150, T0)
    return fx


def test_reviews_of_skills_missed_in_the_quiz_top_the_menu_above_any_lesson(monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _missed_prerequisites_course()
    now = T0 + days(1)
    run_quiz(start_quiz(fx, now), now, "000000")  # R1-R6 all missed
    later = now + timedelta(minutes=30)
    lesson = next(c for c in selector._lesson_candidates(selector._load(fx.course_id), later) if c.skill["key"] == "S1")
    assert "Also refreshes" not in lesson.reason and not lesson.covers  # FIRe will not credit them
    assert lesson.importance == pytest.approx(1 + 0.3 * 16)  # no repetition-compression bonus for them
    menu = selector.rank(fx.course_id, later)
    assert all(c.type == "review" and c.quiz_remedial for c in menu)
    monkeypatch.setattr(selector, "QUIZ_REMEDIAL_BONUS", 0.0)  # the tier, not the bonus, puts them on top
    menu = selector.rank(fx.course_id, later)
    assert menu[0].quiz_remedial and menu[0].importance < lesson.importance
    task = selector.continue_task(fx.course_id, later)
    assert task.type == "review" and task.reason.startswith("Missed in the quiz on")


def test_a_flag_after_the_quiz_restores_the_card_of_a_missed_skill() -> None:
    fx = due_course()
    now = T0 + days(1)
    set_due(fx.sid("S2"), now + days(10))
    before = progress_row(fx, "S2")
    quiz_id = start_quiz(fx, now)
    done = run_quiz(quiz_id, now, "101111").task  # S2 missed
    assert scheduler.is_due(card(fx.sid("S2")), now + timedelta(minutes=5))
    later = now + timedelta(minutes=15)
    flagged = done.quiz.results[1]
    assert flagged.skill.key == "S2"
    task = engine.flag(flagged.question_id, "", later)
    assert task.quiz.missed_skills == [] and task.outcome == "passed"
    assert progress_row(fx, "S2") == before and not scheduler.is_due(card(fx.sid("S2")), later)
    assert scheduler.remedial_info(fx.sid("S2")) == {}
    assert all(c.skill["key"] != "S2" for c in selector.rank(fx.course_id, later))
    assert fx.sid("S2") not in task_state(quiz_id)["result"]["effects"]


def test_a_flag_after_the_quiz_takes_back_the_fsrs_credit() -> None:
    fx = due_course()
    now = T0 + days(1)  # S1 is due within 3 days: a right answer is an FSRS review
    before = progress_row(fx, "S1")
    quiz_id = start_quiz(fx, now)
    done = run_quiz(quiz_id, now, "111111").task
    assert fx.sid("S1") in task_state(quiz_id)["result"]["credited"] and explicit_reviews(fx, "S1", now) == 1
    assert progress_row(fx, "S1")["reviews"] == before["reviews"] + 1
    engine.flag(done.quiz.results[0].question_id, "", now + timedelta(minutes=15))
    assert progress_row(fx, "S1") == before and explicit_reviews(fx, "S1", now) == 0
    result = task_state(quiz_id)["result"]
    assert fx.sid("S1") not in result["credited"] and fx.sid("S1") not in result["effects"]
    assert fx.sid("S2") in result["credited"]  # the others keep theirs


def test_a_flag_after_the_remedial_review_leaves_the_card_alone() -> None:
    fx = due_course()
    now = T0 + days(1)
    set_due(fx.sid("S2"), now + days(10))
    quiz_id = start_quiz(fx, now)
    done = run_quiz(quiz_id, now, "101111").task  # S2 missed
    later = now + timedelta(hours=1)
    do_review(fx, "S2", later)
    reviewed = progress_row(fx, "S2")
    engine.flag(done.quiz.results[1].question_id, "", later + timedelta(minutes=1))
    assert progress_row(fx, "S2") == reviewed and fx.sid("S2") not in task_state(quiz_id)["result"]["missed"]


def _two_quizzes(second: str):
    """S1 missed in quiz 1 (its review not done); quiz 2 (5 new skills + S1, the lowest recall) answered `second`."""
    fx = due_course(extra_roots=10)
    now = T0 + days(1)
    first_id = start_quiz(fx, now)
    run_quiz(first_id, now, "011111")
    assert scheduler.remedial_info(fx.sid("S1"))["quiz"] == first_id
    later = now + timedelta(hours=2)
    make_learned(fx, ["R5", "R6", "R7", "R8", "R9"], later)
    earn(fx, 150, later)
    second_id = start_quiz(fx, later)
    assert [s.key for s in engine.get_task(second_id, later).quiz.skills] == ["S1", "R5", "R6", "R7", "R8", "R9"]
    done = run_quiz(second_id, later, second).task
    return fx, first_id, second_id, done, later


def test_a_right_answer_in_a_later_quiz_settles_the_earlier_quiz_mark() -> None:
    fx, first_id, second_id, done, later = _two_quizzes("111111")
    s1 = fx.sid("S1")
    assert s1 in task_state(second_id)["result"]["credited"] and "quiz" not in scheduler.remedial_info(s1)
    due_again = scheduler.utc(card(s1).due) + timedelta(minutes=1)
    review = next(c for c in selector._review_candidates(selector._load(fx.course_id), due_again) if c.skill["id"] == s1)
    assert review.reason.startswith("Review due") and not review.quiz_remedial
    assert review.importance < selector.QUIZ_REMEDIAL_BONUS

    # the credit voided by a flag: the earlier quiz's review is owed again
    engine.flag(done.quiz.results[0].question_id, "", later + timedelta(minutes=10))
    assert scheduler.remedial_info(s1)["quiz"] == first_id and scheduler.is_due(card(s1), later + timedelta(minutes=10))


def test_a_skill_missed_in_both_quizzes_carries_the_later_mark() -> None:
    fx, first_id, second_id, done, later = _two_quizzes("011111")
    s1 = fx.sid("S1")
    assert scheduler.remedial_info(s1)["quiz"] == second_id
    engine.flag(done.quiz.results[0].question_id, "", later + timedelta(minutes=10))
    assert scheduler.remedial_info(s1)["quiz"] == first_id  # still owed from the first quiz


def test_skills_left_out_of_a_full_quiz_lead_the_next_one() -> None:
    fx = build_course(extra_roots=16)
    first = [f"R{n}" for n in range(1, 11)]
    for i, key in enumerate(first):
        make_learned(fx, [key], T0 + timedelta(minutes=i))
    now = T0 + timedelta(hours=1)
    earn(fx, 150, now)
    plan = quiz.build_plan(fx.course_id, now)
    assert [s["key"] for s in plan.skills] == first[:8]  # the 2 latest are left out
    run_quiz(start_quiz(fx, now), now, "11111111")
    later = now + days(1)
    second = [f"R{n}" for n in range(11, 17)] + ["S1", "S2"]
    for i, key in enumerate(second):
        make_learned(fx, [key], later + timedelta(minutes=i))
    earn(fx, 150, later + timedelta(hours=1))
    plan = quiz.build_plan(fx.course_id, later + timedelta(hours=1))
    assert len(plan.skills) == 8 and {s["key"] for s in plan.skills} == {"R9", "R10", *second[:6]}


def test_a_quiz_closed_as_abandoned_by_a_script_does_not_reset_the_counter() -> None:
    fx = due_course()
    task_id = start_quiz(fx, T0)
    engine.begin_practice(task_id, T0)
    db.execute(  # what a raw-SQL maintenance path does (e.g. a lesson swap abandoning the quiz's first skill's tasks)
        "UPDATE tasks SET status = 'completed', phase = 'done', outcome = 'abandoned', completed_at = ? WHERE id = ?",
        (scheduler.iso(T0 + timedelta(minutes=1)), task_id),
    )
    status = quiz.quiz_status(fx.course_id, T0 + timedelta(minutes=2))
    assert status.due and status.since is None and status.xp_since == 150
    again = selector.continue_task(fx.course_id, T0 + timedelta(minutes=2))
    assert again.type == "quiz" and again.id != task_id and len(again.quiz.skills) == 6


# ---------------------------------------------------------------------------
# Integration fixes: no lesson/book view during a quiz; maintenance scripts never close a running quiz
# ---------------------------------------------------------------------------


def test_lessons_and_book_pages_are_closed_while_a_quiz_is_open() -> None:
    from app.api import courses

    fx = due_course()
    task_id = start_quiz(fx, T0)
    assert quiz.blocks_reference(fx.course_id, T0)  # a forced quiz blocks from its intro on
    app = FastAPI()
    app.include_router(courses.router)
    response = TestClient(app).get(f"/api/skills/{fx.sid(ROOTS[0])}/lesson")
    assert response.status_code == 409 and response.json()["detail"] == quiz.REFERENCE_CLOSED
    started = engine.begin_practice(task_id, T0)
    deadline = db.parse_iso(started.quiz.deadline)
    assert quiz.blocks_reference(fx.course_id, deadline - timedelta(seconds=1))
    assert not quiz.blocks_reference(fx.course_id, deadline + timedelta(minutes=5))  # time is up: reference reopens


def test_regenerating_a_lesson_never_closes_a_running_quiz() -> None:
    from app.pipeline.lessons import abandon_active_tasks, retire_questions

    fx = due_course()
    task_id = start_quiz(fx, T0)
    task = engine.begin_practice(task_id, T0)
    quiz_row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    current = task.question.id
    skill_of_current = db.query_one("SELECT skill_id FROM questions WHERE id = ?", (current,))["skill_id"]
    with db.connect() as conn:
        assert abandon_active_tasks(conn, quiz_row["skill_id"]) == 0
        retire_questions(conn, skill_of_current)
    assert db.query_one("SELECT status FROM tasks WHERE id = ?", (task_id,))["status"] == "active"
    assert db.query_one("SELECT id FROM questions WHERE id = ?", (current,)) is not None  # kept (retired) for the quiz
