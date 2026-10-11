"""Guided proof lessons (SPEC §6.3b): the teaching sequence's checks are scored steps answered inside the task —
gated on the server, 1 / ½ on a second try / 0 — then one variant problem (weight 2) ends the lesson; a left attempt
continues, a finished one makes the next a retry on fresh questions. Also: hint texts are sent only once used."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_learning_blocks import BLOCKS, v2_lesson
from test_learning_fixtures import (  # noqa: F401
    T0,
    add_capstone,
    answer_fully,
    build_course,
    isolated_engine,
    learn,
    status,
    task_state,
)

from app import db, views
from app.api import courses, study
from app.learning import engine
from app.schemas import BlockCheckRequest

# BLOCKS: 2 expression (-6), 3 faded expression (1+7*i), 4 numeric (4), 5 multiple choice (2), 6 true/false (1),
# 7 true/false set (FTT), 8 reflect (never scored)
SCORED = [2, 3, 4, 5, 6, 7]
RIGHT = {2: {"response": "-6"}, 3: {"response": "1+7*i"}, 4: {"response": "4"}, 5: {"choice": 2}, 6: {"choice": 1},
         7: {"response": "FTT"}}  # fmt: skip
WRONG = {2: {"response": "6"}, 3: {"response": "3+7*i"}, 4: {"response": "5"}, 5: {"choice": 0}, 6: {"choice": 0},
         7: {"response": "TFF"}}  # fmt: skip  (every mark wrong: a set with some right earns their share)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(study.router)
    app.include_router(courses.router)
    return TestClient(app)


def guided_course(*, variant: bool = True):
    """S1 is a guided proof lesson (BLOCKS, scoring 'guided') with a 3-step variant; its KPs keep their questions."""
    fx = build_course()
    lesson = {**v2_lesson("S1"), "scoring": "guided", "deeper": "A deeper look."}
    db.execute("UPDATE skills SET lesson_json = ?, kind = 'proof' WHERE id = ?", (db.dumps(lesson), fx.sid("S1")))
    cap = add_capstone(fx, "S1", kp=2) if variant else None
    return fx, cap


def version(fx) -> str:
    return views.lesson_version(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (fx.sid("S1"),))["lesson_json"])


def start(fx) -> str:
    return engine.start_lesson(fx.sid("S1"), "test", T0)


def answer(fx, task_id: str, index: int, body: dict):
    return engine.check_guided(task_id, index, BlockCheckRequest(version=version(fx), **body), T0)


def answer_all(fx, task_id: str, right: bool = True) -> None:
    for i in SCORED:
        r = answer(fx, task_id, i, (RIGHT if right else WRONG)[i])
        if not r.final:
            answer(fx, task_id, i, (RIGHT if right else WRONG)[i])


def test_the_lesson_is_gated_on_the_server_until_each_scored_step_is_answered() -> None:
    fx, _ = guided_course()
    task_id = start(fx)
    task = engine.get_task(task_id)
    lesson = task.lesson
    assert task.phase == "learn" and task.question is None and lesson.guided == "scored"
    assert [b.index for b in lesson.blocks] == [0, 1, 2] and lesson.blocks_total == len(BLOCKS)
    assert lesson.deeper == "" and all(kp.explanation == "" and kp.worked_example.steps == [] for kp in lesson.knowledge_points)
    check = lesson.blocks[2].check
    assert check.scored and check.second_try and check.state.status == "open" and check.state.answer_display == ""
    dumped = task.model_dump_json()
    assert "1+7" not in dumped and "Order matters" not in dumped and "A deeper look" not in dumped
    p = task.progress
    assert (p.guided_total, p.guided_done, p.in_guided, p.variant, p.planned) == (6, 0, True, True, 7)
    assert p.best_possible == pytest.approx(1.0)
    with pytest.raises(engine.Conflict, match="steps of the proof"):
        engine.begin_practice(task_id, T0)
    with pytest.raises(engine.Conflict, match="earlier step"):
        answer(fx, task_id, 3, RIGHT[3])
    r = answer(fx, task_id, 2, RIGHT[2])
    assert r.final and r.check.points == 1.0 and r.check.answer_display == "$-6$" and r.check.explanation
    assert [b.index for b in r.task.lesson.blocks] == [0, 1, 2, 3]  # the next step arrives with the answer
    assert r.task.progress.points == 1.0 and r.task.progress.answered == 1 and r.task.progress.guided_done == 1
    assert answer(fx, task_id, 2, WRONG[2]).check.points == 1.0  # a done step is never graded again


def test_second_try_is_half_a_point_and_never_shows_the_answer_first() -> None:
    fx, _ = guided_course()
    task_id = start(fx)
    answer(fx, task_id, 2, RIGHT[2])
    first = answer(fx, task_id, 3, WRONG[3])
    assert not first.final and first.check.status == "retry" and "one more try" in first.check.feedback
    assert first.check.answer_display == "" and first.check.explanation == "" and first.check.first_response == "3+7*i"
    assert [b.index for b in first.task.lesson.blocks] == [0, 1, 2, 3]  # still gated
    second = answer(fx, task_id, 3, RIGHT[3])
    assert second.final and second.check.points == 0.5 and second.check.correct and "½ point" in second.check.feedback
    # a statement set: "not yet" never says which statements are wrong
    for i in (4, 5, 6):
        answer(fx, task_id, i, RIGHT[i])
    tf = answer(fx, task_id, 7, WRONG[7])
    assert tf.check.status == "retry" and "statement" not in tf.check.feedback.lower()
    assert answer(fx, task_id, 7, WRONG[7]).check.points == 0.0  # wrong twice: 0, and now the answer
    st = task_state(task_id)
    assert [(e["q"], e["pts"]) for e in st["log"]] == [
        ("block:2", 1.0), ("block:3", 0.5), ("block:4", 1.0), ("block:5", 1.0), ("block:6", 1.0), ("block:7", 0.0)
    ]


def test_a_choice_step_has_one_try_and_show_answer_is_zero() -> None:
    fx, _ = guided_course()
    task_id = start(fx)
    for i in (2, 3, 4):
        answer(fx, task_id, i, RIGHT[i])
    mc = answer(fx, task_id, 5, WRONG[5])
    assert mc.final and mc.check.points == 0.0 and mc.check.answer_display == "bottom-right"
    shown = engine.reveal_guided(task_id, 6, version(fx), T0)
    assert shown.final and shown.check.revealed and shown.check.points == 0.0 and shown.check.response is None
    assert engine.reveal_guided(task_id, 6, version(fx), T0).check.points == 0.0  # idempotent


def test_unreadable_answers_stale_versions_and_wrong_tasks_use_no_try(client: TestClient) -> None:
    fx, _ = guided_course()
    task_id = start(fx)
    url = f"/api/tasks/{task_id}/blocks/2/check"
    assert client.post(url, json={"response": "2((", "version": version(fx)}).status_code == 422
    assert client.post(url, json={"response": "", "version": version(fx)}).status_code == 422
    assert client.post(url, json={"response": "-6"}).status_code == 409  # no version
    assert client.post(url, json={"response": "-6", "version": "0" * 40}).status_code == 409
    assert client.post(f"/api/tasks/{task_id}/blocks/1/check", json={"response": "1", "version": version(fx)}).status_code == 404
    assert task_state(task_id)["guided"]["checks"]["2"]["tries"] == 0
    ok = client.post(url, json={"response": "-6", "version": version(fx)})
    assert ok.status_code == 200 and ok.json()["check"]["points"] == 1.0 and ok.json()["task"]["progress"]["guided_done"] == 1


def test_the_open_check_endpoint_and_the_preview_never_hand_out_a_scored_step(client: TestClient) -> None:
    fx, _ = guided_course()
    sid = fx.sid("S1")
    refused = client.post(f"/api/skills/{sid}/blocks/2/check", json={"response": "-6", "version": version(fx)})
    assert refused.status_code == 409 and "-6" not in refused.text
    reflect = client.post(f"/api/skills/{sid}/blocks/8/check", json={"version": version(fx)})
    assert reflect.status_code == 409 and "Two factors" not in reflect.text  # past the open step: not shown yet
    shown = client.post(f"/api/skills/{sid}/blocks/0/check", json={"version": version(fx)})
    assert shown.status_code == 404  # block 0 has no check: the gate lets the request through to the usual answer
    preview = client.get(f"/api/skills/{sid}/lesson").json()
    assert [b["index"] for b in preview["blocks"]] == [0, 1, 2] and preview["guided"] == "scored"
    assert preview["knowledge_points"][0]["explanation"] == "" and "Order matters" not in str(preview)
    task_id = start(fx)
    answer(fx, task_id, 2, RIGHT[2])
    assert [b["index"] for b in client.get(f"/api/skills/{sid}/lesson").json()["blocks"]] == [0, 1, 2, 3]


def test_all_steps_right_then_the_variant_passes_with_good() -> None:
    fx, cap = guided_course()
    task_id = start(fx)
    answer_all(fx, task_id)
    task = engine.get_task(task_id)
    assert not task.progress.in_guided and len(task.lesson.blocks) == len(BLOCKS) and task.lesson.deeper
    practice = engine.begin_practice(task_id, T0)
    assert practice.phase == "practice" and practice.question.id == cap and practice.progress.in_capstone
    assert practice.lesson.knowledge_points[0].explanation == ""  # the variant comes without KP material
    last = answer_fully(task_id, cap, T0)
    assert last.task.outcome == "passed" and last.task.progress.score == pytest.approx(1.0)
    assert last.task.progress.planned == 7 and last.task.progress.answered == 7
    assert status(fx.sid("S1")) == "learned"
    assert not db.query("SELECT 1 FROM attempts WHERE question_id != ?", (cap,))  # no KP question was served


def test_a_weak_guided_score_ends_the_attempt_and_the_next_one_is_a_retry_on_fresh_questions() -> None:
    fx, cap = guided_course()
    task_id = start(fx)
    answer_all(fx, task_id, right=False)  # 0 of 6: even a perfect variant gives 2 of 8
    assert engine.get_task(task_id).progress.best_possible == pytest.approx(0.25)
    done = engine.begin_practice(task_id, T0)
    assert done.phase == "done" and done.outcome == "failed" and done.progress.halted and done.progress.planned == 7
    progress = db.query_one("SELECT kp_failures_json, cooldown_until FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert db.loads(progress["kp_failures_json"]) == {"guided": 1} and progress["cooldown_until"]
    retry_id = start(fx)
    retry = engine.get_task(retry_id)
    assert retry.lesson.guided == "retry" and len(retry.lesson.blocks) == len(BLOCKS) and retry.lesson.deeper
    check = retry.lesson.blocks[2].check
    assert check.state.status == "done" and not check.state.scored and check.state.answer_display == "$-6$"
    assert retry.progress.guided_total == 0 and retry.lesson.knowledge_points[0].explanation
    practice = engine.begin_practice(retry_id, T0)
    assert practice.question.role == "practice" and practice.question.kp_index == 0  # KP questions this time
    st = task_state(retry_id)
    assert st["fresh_capstone_only"] and st["stage"] == "kp" and st["log"] == []


def test_leaving_an_attempt_keeps_its_answers() -> None:
    fx, _ = guided_course()
    task_id = start(fx)
    answer(fx, task_id, 2, RIGHT[2])
    answer(fx, task_id, 3, WRONG[3])  # first try used
    engine.abandon(task_id, T0)
    again = start(fx)
    st = task_state(again)
    assert st["stage"] == "guided" and st["guided"]["checks"]["3"]["status"] == "retry"
    assert [e["q"] for e in st["log"]] == ["block:2"]
    assert answer(fx, again, 3, RIGHT[3]).check.points == 0.5  # still the second try


def test_a_reported_step_is_kept_for_review_and_keeps_its_points() -> None:
    """A report that voided the step would be a bypass: show every answer, report them all, pass on the variant."""
    fx, _ = guided_course()
    task_id = start(fx)
    with pytest.raises(engine.Conflict):
        engine.flag_guided(task_id, 2, "looks wrong", T0)  # not answered yet
    answer(fx, task_id, 2, WRONG[2])
    answer(fx, task_id, 2, WRONG[2])
    task = engine.flag_guided(task_id, 2, "the key is wrong", T0)
    assert task.progress.points == 0 and task.progress.max_points == 1 and task.progress.guided_total == 6
    assert task.lesson.blocks[2].check.state.reported and not task.lesson.blocks[2].check.state.voided
    row = db.query_one("SELECT kind, note, block_index FROM block_feedback")
    assert (row["kind"], row["note"], row["block_index"]) == ("wrong_key", "the key is wrong", 2)


def test_a_learned_skill_or_a_lesson_without_gradeable_checks_is_not_guided(client: TestClient) -> None:
    fx, _ = guided_course()
    sid = fx.sid("S1")
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
    for block in lesson["blocks"]:
        if block.get("check") and block["check"]["kind"] != "reflect":
            block["check"]["withheld"] = "verification disagreed"
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), sid))
    task_id = start(fx)
    st = task_state(task_id)
    assert "guided" not in st and st["stage"] == "kp" and st["playable"] == [0, 1]
    engine.abandon(task_id, T0)
    fx2, _ = guided_course()
    learn_task = engine.start_lesson(fx2.sid("S1"), "test", T0)
    answer_all(fx2, learn_task)
    engine.begin_practice(learn_task, T0)
    answer_fully(learn_task, engine.get_task(learn_task).question.id, T0)
    assert status(fx2.sid("S1")) == "learned"
    full = client.get(f"/api/skills/{fx2.sid('S1')}/lesson").json()
    assert len(full["blocks"]) == len(BLOCKS) and full["guided"] is None and not full["blocks"][2]["check"]["scored"]
    v = views.lesson_version(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (fx2.sid("S1"),))["lesson_json"])
    assert client.post(f"/api/skills/{fx2.sid('S1')}/blocks/2/check", json={"response": "-6", "version": v}).status_code == 200


def test_hint_texts_are_sent_only_once_used() -> None:
    fx = build_course()
    task_id = engine.start_lesson(fx.sid("S1"), "test", T0)
    q = engine.begin_practice(task_id, T0).question
    assert q.hints == [] and q.hint_count == 3 and q.hints_used == 0
    assert engine.hint(q.id, T0).hint == "h1"
    again = engine.get_task(task_id).question
    assert again.hints == ["h1"] and again.hint_count == 3 and again.hints_used == 1


def test_leaving_during_the_variant_comes_back_to_the_same_problem_and_step() -> None:
    fx, cap = guided_course()
    task_id = start(fx)
    answer_all(fx, task_id)
    engine.begin_practice(task_id, T0)
    from test_learning_fixtures import step_submit

    engine.submit_answer(task_id, step_submit(cap, 0, correct=False), T0)  # step 1 missed
    engine.abandon(task_id, T0)
    with pytest.raises(engine.Conflict, match="Continue the lesson"):
        engine.reveal(cap, T0)  # nothing handed out while the problem waits in the left attempt
    again = start(fx)
    task = engine.begin_practice(again, T0)
    assert task.question.id == cap and task.question.step_index == 1 and not task.question.step_results[0].correct
    last = answer_fully(again, cap, T0)
    assert last.task.outcome == "passed" and last.task.progress.score == pytest.approx((6 + 2 * 2 / 3) / 8)


def test_a_lesson_without_a_servable_variant_promises_none() -> None:
    fx, _ = guided_course(variant=False)
    task_id = start(fx)
    answer_all(fx, task_id)
    progress = engine.get_task(task_id).progress
    assert not progress.variant and progress.guided_total == 6 and progress.planned == 6
    assert engine.begin_practice(task_id, T0).outcome == "passed"


def test_a_left_attempt_keeps_the_knowledge_point_material_back_and_the_menu_says_it_continues() -> None:
    from app.learning import selector

    fx, _ = guided_course()
    task_id = start(fx)
    answer(fx, task_id, 2, RIGHT[2])
    left = engine.abandon(task_id, T0)
    assert left.lesson.knowledge_points[0].explanation == "" and [b.index for b in left.lesson.blocks] == [0, 1, 2, 3]
    reasons = [o.reason for o in selector.next_response(fx.course_id, T0).options if o.skill and o.skill.key == "S1"]
    assert reasons and reasons[0].startswith("Started before — continues where you left off")


def test_a_record_of_another_lesson_version_is_never_shown_over_new_blocks() -> None:
    fx, _ = guided_course()
    task_id = start(fx)
    answer(fx, task_id, 2, RIGHT[2])
    answer(fx, task_id, 3, RIGHT[3])
    engine.abandon(task_id, T0)
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (fx.sid("S1"),))["lesson_json"])
    lesson["blocks"][3]["check"]["answer"], lesson["blocks"][3]["check"]["answer_display"] = "7+i", "$7+i$"
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), fx.sid("S1")))  # regenerated
    old = engine.get_task(task_id)
    assert [b.index for b in old.lesson.blocks] == [0, 1, 2] and "7+i" not in old.model_dump_json()


def test_a_two_statement_set_has_one_try_and_a_second_guided_failure_says_what_to_refresh() -> None:
    fx, _ = guided_course()
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (fx.sid("S1"),))["lesson_json"])
    lesson["blocks"][7]["check"]["choices"] = lesson["blocks"][7]["check"]["choices"][:2]
    lesson["blocks"][7]["check"]["answer"] = "FT"
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), fx.sid("S1")))
    learn(fx, "S2", T0)
    for attempt in range(2):
        task_id = start(fx)
        if attempt == 0:
            for i in SCORED[:-1]:
                r = answer(fx, task_id, i, WRONG[i])
                if not r.final:
                    answer(fx, task_id, i, WRONG[i])
            two = answer(fx, task_id, 7, {"response": "TT"})
            assert two.final and two.check.points == 0.0  # no second try on two statements
            done = engine.begin_practice(task_id, T0)
        else:  # the retry: knowledge-point questions, all wrong
            from test_learning_fixtures import wrong

            task = engine.begin_practice(task_id, T0)
            while task.phase != "done":
                task = engine.submit_answer(task_id, wrong(task.question.id), T0).task
            done = task
        assert done.outcome == "failed"
    assert "twice" in done.progress.suggestion_text


def test_preview_after_a_left_and_after_a_failed_attempt(client: TestClient) -> None:
    fx, _ = guided_course()
    sid = fx.sid("S1")
    task_id = start(fx)
    answer(fx, task_id, 2, RIGHT[2])
    engine.abandon(task_id, T0)
    left = client.get(f"/api/skills/{sid}/lesson").json()
    assert [b["index"] for b in left["blocks"]] == [0, 1, 2, 3] and left["knowledge_points"][0]["explanation"] == ""
    again = start(fx)
    answer_all(fx, again, right=False)
    engine.begin_practice(again, T0)  # out of reach: the attempt ends
    failed = client.get(f"/api/skills/{sid}/lesson").json()
    assert len(failed["blocks"]) == len(BLOCKS) and failed["knowledge_points"][0]["explanation"]


def test_a_step_the_verifier_rejects_is_withheld_and_running_attempts_carry_over() -> None:
    import asyncio

    from app.fake_llm import _marker
    from app.pipeline import verify

    fx, _ = guided_course()
    sid = fx.sid("S1")
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
    for i in SCORED:  # the fake solver answers what the marker says: the key, except at block 4
        c = lesson["blocks"][i]["check"]
        c["prompt"] += _marker(c.get("correct_choice"), "5" if i == 4 else c.get("answer", ""))
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), sid))
    task_id = start(fx)
    answer(fx, task_id, 2, RIGHT[2])
    notes = asyncio.run(verify.verify_guided_checks(sid))
    assert any("block 5: withheld from scoring" in n for n in notes), notes
    stored = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
    assert stored["blocks"][4]["check"]["withheld"] and [r["status"] for r in stored["guided_verify"]].count("disagree") == 1
    task = engine.get_task(task_id)  # carried over: not stale, block 2 still answered, block 4 out of the score
    assert task.progress.guided_total == 5 and task.lesson.blocks[2].check.state.status == "done"
    assert answer(fx, task_id, 3, RIGHT[3]).check.points == 1.0
    assert "4" not in [str(i) for i in task_state(task_id)["guided"]["order"]]
    request = verify.guided_check_request(stored["blocks"], 3, verify._check_row({"id": sid, "key": "S1"}, 3, stored["blocks"][3]["check"]))
    assert "1+7*i" not in request.split("CURRENT STEP")[0]  # never the step's own answer


def test_guided_work_blocks_a_split_and_reaches_the_planner() -> None:
    from app.pipeline.lesson_v2 import learner_misses
    from app.pipeline.replan import split_blockers

    fx, _ = guided_course()
    task_id = start(fx)
    answer(fx, task_id, 2, WRONG[2])
    answer(fx, task_id, 2, RIGHT[2])  # second try
    engine.reveal_guided(task_id, 3, version(fx), T0)
    engine.flag_guided(task_id, 3, "", T0)
    assert any("answered proof step" in r for r in split_blockers(fx.sid("S1")))
    misses = learner_misses(fx.sid("S1"))
    assert any("block 3" in m and "second try" in m for m in misses) and any("block 4" in m and "shown" in m for m in misses)
