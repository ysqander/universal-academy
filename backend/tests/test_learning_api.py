"""End-to-end study loop over HTTP (FastAPI app with only the study router)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_learning_fixtures import (  # noqa: F401
    add_capstone,
    build_course,
    isolated_engine,
    question,
    set_targets,
    wrong_response,
)

from app import db
from app.api import study


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(study.router)
    return TestClient(app)


def answer_body(qid: str, correct: bool = True, **extra) -> dict:
    q = question(qid)
    body: dict = {"question_id": qid, **extra}
    if q["kind"] in ("multiple_choice", "true_false"):
        body["choice"] = q["correct_choice"] if correct else (q["correct_choice"] + 1) % 3
    else:
        body["response"] = q["answer"] if correct else wrong_response(q)
    return body


def test_full_study_loop(client: TestClient) -> None:
    fx = build_course()
    cid = fx.course_id

    nxt = client.get(f"/api/courses/{cid}/next").json()
    assert nxt["task"] is None and [o["skill"]["key"] for o in nxt["options"]] == ["S1", "S2"]
    assert nxt["session"] == {"xp_today": 0, "daily_goal": 30, "goal_reached": False, "due_reviews": 0}

    task = client.post(f"/api/courses/{cid}/continue").json()
    assert task["type"] == "lesson" and task["phase"] == "learn" and task["lesson"]["skill_key"] == "S1"
    assert client.get(f"/api/tasks/{task['id']}").json()["id"] == task["id"]

    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    assert task["phase"] == "practice" and task["question"]["kp_index"] == 0
    assert "answer" not in task["question"] and "solution" not in task["question"]

    bad = client.post(f"/api/tasks/{task['id']}/answer", json={"question_id": task["question"]["id"], "response": "2(("})
    assert bad.status_code == 422 and "Couldn't read" in bad.json()["detail"]

    revealed = client.post(f"/api/questions/{task['question']['id']}/reveal").json()
    assert revealed["solution"] and revealed["correct_answer"]
    result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(task["question"]["id"])).json()
    assert result["correct"] and not result["independent"]  # the solution was revealed first

    assert result["score"] == 0 and result["question_done"] is True and result["step_result"] is None
    task = result["task"]
    progress = task["progress"]
    assert (progress["kp_target"], progress["planned"], progress["pass_mark"], progress["score"]) == (3, 5, 0.8, 0)
    xp_awarded = 0
    while task["phase"] != "done":
        result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(task["question"]["id"])).json()
        assert result["status"] == "correct" and result["feedback"] and result["score"] == 1
        task = result["task"]
        xp_awarded += result["xp_awarded"]
    # 0 + 1 + 1 + 1 + 1 of 5 = 80 %: passed exactly at the mark, round(10 × 0.8) XP
    assert task["outcome"] == "passed" and xp_awarded == 8 and task["skill"]["status"] == "learned"
    assert task["progress"]["score"] == pytest.approx(0.8)

    again = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(fx.questions["S1"][0][0]))
    assert again.status_code == 409

    nxt = client.get(f"/api/courses/{cid}/next").json()
    assert nxt["session"]["xp_today"] == 8 and nxt["task"] is None
    assert [o["skill"]["key"] for o in nxt["options"]] == ["S2"]


def test_manual_start_flag_easier_and_abandon(client: TestClient) -> None:
    fx = build_course()
    cid = fx.course_id
    locked = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3")})
    assert locked.status_code == 409 and "locked" in locked.json()["detail"]

    task = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S2"), "type": "lesson"}).json()
    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    flagged_q = task["question"]["id"]
    task = client.post(f"/api/questions/{flagged_q}/flag", json={"reason": "the answer key is wrong"}).json()
    assert task["question"]["id"] != flagged_q
    assert db.query_one("SELECT flagged, flag_reason FROM questions WHERE id = ?", (flagged_q,)) == {
        "flagged": 1,
        "flag_reason": "learner: the answer key is wrong",
    }

    done = client.post(f"/api/tasks/{task['id']}/abandon").json()
    assert done["phase"] == "done" and done["outcome"] == "abandoned"

    kp0 = fx.questions["S1"][0]
    db.execute("UPDATE questions SET tier = 2 WHERE id = ?", (kp0[1],))
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (kp0[3],))
    task = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S1")}).json()
    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(task["question"]["id"], correct=False)).json()
    task = result["task"]
    assert result["suggestion"] == "easier_step" and result["suggestion_text"]
    easier = client.post(f"/api/tasks/{task['id']}/easier")
    assert easier.status_code == 200 and easier.json()["question"]["id"] == kp0[3]
    again = client.post(f"/api/tasks/{task['id']}/easier")
    assert again.status_code == 409 and "No easier question" in again.json()["detail"]


def test_errors_and_settings(client: TestClient) -> None:
    assert client.get("/api/courses/nope/next").status_code == 404
    assert client.post("/api/courses/nope/continue").status_code == 404
    assert client.get("/api/tasks/nope").status_code == 404
    assert client.post("/api/tasks/nope/answer", json={"question_id": "x", "response": "1"}).status_code == 404
    assert client.post("/api/questions/nope/reveal").status_code == 404
    assert client.post("/api/questions/nope/flag", json={}).status_code == 404

    pending = build_course(lesson_status="pending")
    nothing = client.post(f"/api/courses/{pending.course_id}/continue")
    assert nothing.status_code == 409 and nothing.json()["detail"] == "Lessons are still being generated…"

    assert client.get("/api/settings").json() == {"daily_goal": 30}
    assert client.put("/api/settings", json={"daily_goal": 45}).json() == {"daily_goal": 45}
    assert client.get("/api/settings").json() == {"daily_goal": 45}
    assert client.put("/api/settings", json={"daily_goal": 0}).status_code == 422
    assert client.put("/api/settings", json={}).status_code == 422


def test_hint_reveal_and_task_switching_over_http(client: TestClient) -> None:
    fx = build_course()
    cid = fx.course_id
    task = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S1")}).json()
    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    qid = task["question"]["id"]

    # D2: hints come from the server, which records them for the current attempt
    hint = client.post(f"/api/questions/{qid}/hint")
    assert hint.status_code == 200 and hint.json() == {"hint": "h1", "hints_used": 1}
    result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(qid, hints_used=0)).json()
    assert result["correct"] and not result["independent"]
    assert client.post(f"/api/questions/{qid}/hint").status_code == 409  # no longer the current question
    assert client.post("/api/questions/nope/hint").status_code == 404

    assert result["score"] == 0.5
    task = result["task"]
    result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(task["question"]["id"])).json()

    # choice questions report the right option
    task = result["task"]
    mc = task["question"]
    assert mc["kind"] == "multiple_choice"
    result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(mc["id"])).json()
    assert result["independent"] and result["correct_choice"] == question(mc["id"])["correct_choice"]
    task = result["task"]

    # D6: another task is open -> 409 unless replace_active; locked skills need force
    conflict = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S2")})
    assert conflict.status_code == 409 and conflict.json()["detail"] == "Finish or leave 'Skill S1' first."
    locked = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3"), "replace_active": True})
    assert locked.status_code == 409 and "locked" in locked.json()["detail"]
    assert client.get(f"/api/tasks/{task['id']}").json()["outcome"] is None  # the open task survived both
    forced = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3"), "replace_active": True, "force": True})
    assert forced.status_code == 200 and forced.json()["skill"]["key"] == "S3"
    assert client.get(f"/api/tasks/{task['id']}").json()["outcome"] == "abandoned"

    # D1: a reveal with a body; a non-free-response question is always marked revealed
    task = client.post(f"/api/tasks/{forced.json()['id']}/practice").json()
    q = task["question"]["id"]
    revealed = client.post(f"/api/questions/{q}/reveal", json={"response": "a long enough committed answer"})
    assert revealed.status_code == 200 and revealed.json()["solution"]
    result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(q)).json()
    assert result["correct"] and not result["independent"] and result["score"] == 0


def test_unsimplified_answer_is_asked_again_over_http(client: TestClient) -> None:
    fx = build_course()
    cid = fx.course_id
    task = client.post(f"/api/courses/{cid}/continue").json()
    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    qid = task["question"]["id"]  # numeric, answer 101
    again = client.post(f"/api/tasks/{task['id']}/answer", json={"question_id": qid, "response": "100+1"})
    assert again.status_code == 422 and again.json()["detail"].startswith("Same value")
    assert not db.query("SELECT 1 FROM attempts WHERE task_id = ?", (task["id"],))
    ok = client.post(f"/api/tasks/{task['id']}/answer", json={"question_id": qid, "response": "101"}).json()
    assert ok["correct"] and ok["independent"]


def test_learn_anyway_then_resume_without_force_and_reload_restores_state(client: TestClient) -> None:
    """E2 + E3 over HTTP: the drawer's 'Continue lesson' (no force) on a force-started locked skill is not a 409,
    and a reload hands back the hints shown, the reveal and the easier-step availability."""
    fx = build_course()
    cid = fx.course_id
    forced = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3"), "force": True}).json()
    resumed = client.post(f"/api/courses/{cid}/tasks", json={"skill_id": fx.sid("S3")})
    assert resumed.status_code == 200 and resumed.json()["id"] == forced["id"]
    assert all(o["skill"]["key"] != "S3" for o in client.get(f"/api/courses/{cid}/next").json()["options"])

    task = client.post(f"/api/tasks/{forced['id']}/practice").json()
    assert task["progress"]["kp_wrong"] == 0 and task["progress"]["easier_available"] is False
    result = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(task["question"]["id"], correct=False)).json()
    qid = result["task"]["question"]["id"]
    assert client.post(f"/api/questions/{qid}/hint").json()["hints_used"] == 1
    client.post(f"/api/questions/{qid}/reveal", json={})
    reloaded = client.get(f"/api/tasks/{task['id']}").json()
    assert reloaded["question"]["id"] == qid
    assert reloaded["question"]["hints_used"] == 1 and reloaded["question"]["revealed"] is True
    assert reloaded["question"]["committed_response"] is None
    assert reloaded["progress"]["kp_wrong"] == 1
    assert reloaded["progress"]["easier_available"] is False  # the current question is tier 1: nothing easier


def test_multistep_capstone_over_http(client: TestClient) -> None:
    fx = build_course()
    set_targets(fx, "S1", [1, 1])
    cap = add_capstone(fx, "S1")
    task = client.post(f"/api/courses/{fx.course_id}/tasks", json={"skill_id": fx.sid("S1")}).json()
    assert task["progress"]["planned"] == 3
    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    for _ in range(2):
        task = client.post(f"/api/tasks/{task['id']}/answer", json=answer_body(task["question"]["id"])).json()["task"]
    q = task["question"]
    assert q["id"] == cap and q["role"] == "capstone" and q["kind"] == "multistep" and task["progress"]["in_capstone"]
    assert [s["index"] for s in q["steps"]] == [0, 1, 2] and all("answer" not in s for s in q["steps"])
    url = f"/api/tasks/{task['id']}/answer"
    no_step = client.post(url, json={"question_id": cap, "response": "5"})
    assert no_step.status_code == 409
    assert client.post(url, json={"question_id": cap, "step": 1, "choice": 2}).status_code == 409
    step0 = client.post(url, json={"question_id": cap, "step": 0, "response": "5"}).json()
    assert step0["status"] == "step" and step0["question_done"] is False and step0["step_result"]["score"] == 1
    assert step0["task"]["question"]["step_index"] == 1 and step0["xp_awarded"] == 0
    assert client.post(f"/api/questions/{cap}/hint?step=0").status_code == 409
    hint = client.post(f"/api/questions/{cap}/hint?step=1")
    assert hint.status_code == 200 and hint.json() == {"hint": "Not A.", "hints_used": 1}
    reloaded = client.get(f"/api/tasks/{task['id']}").json()["question"]
    assert reloaded["step_index"] == 1 and reloaded["hints_used"] == 1 and len(reloaded["step_results"]) == 1
    step1 = client.post(url, json={"question_id": cap, "step": 1, "choice": 2}).json()
    assert step1["step_result"]["score"] == 0.5 and step1["correct_choice"] == 2
    shown = client.post(f"/api/questions/{cap}/reveal", json={}).json()
    assert shown["correct_answer"] == "$3+4i$"
    last = client.post(url, json={"question_id": cap, "step": 2, "response": "3+4i"}).json()
    assert last["question_done"] is True and last["status"] == "incorrect" and last["score"] == pytest.approx(0.5)
    # (1 + 1 + 2 × 0.5) / 4 = 0.75 < 80 %
    assert last["task"]["outcome"] == "failed" and last["suggestion"] == "retry_later"
