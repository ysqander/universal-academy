"""Lesson v2 in the study loop (SPEC §4.2b): inline checks and "This step isn't clear" over HTTP, and true_false_set
questions in lessons and reviews."""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_learning_fixtures import (  # noqa: F401
    T0,
    build_course,
    days,
    isolated_engine,
    learn,
    make_true_false_set,
    question,
    right,
    wrong,
)

from app import db, views
from app.api import study
from app.learning import engine
from app.llm_schemas import InlineCheck, TeachingBlock
from app.schemas import AnswerSubmit


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(study.router)
    return TestClient(app)


def _check(kind: str, prompt: str, *, answer: str = "", choices=(), correct=None, display: str = "", why: str) -> dict:
    return InlineCheck(
        kind=kind, prompt=prompt, choices=list(choices), correct_choice=correct, answer=answer, answer_display=display, explanation=why
    ).model_dump()  # fmt: skip


def _block(kind: str, body: str, check: dict | None = None, heading: str | None = None) -> dict:
    block = TeachingBlock(kind=kind, job=f"{kind} job", new_idea="", heading=heading, body=body, visual=None, check=check)
    return block.model_dump()


STATEMENTS = ["$(1,9)=(9,1)$", "$\\{1,9\\}=\\{9,1\\}$", "$(1,9)=(1,9)$"]

# index -> block (the reference lesson of docs/lesson-v2.md, plus one check of every kind)
BLOCKS = [
    _block("setup", "Let's work out $(2+3i)(1+2i)$.", heading="What changes when we multiply complex numbers?"),
    _block("demonstrate", "The four products are $2$, $4i$, $3i$ and $6i^2$.", heading="First, account for all four products"),
    _block(
        "check", "Next, use $i^2=-1$.",
        _check("expression", "What does $6i^2$ become?", answer="-6", display="$-6$", why="$6i^2=6(-1)=-6$."),
    ),
    _block(
        "faded_example", "$(1+2i)(3+i)=3+i+6i+2i^2$. Replace $2i^2$, then combine the terms.",
        _check("expression", "Finish the product.", answer="1+7*i", display="$1+7i$", why="$3-2+(1+6)i=1+7i$."),
    ),
    _block("check", "Count the cells.", _check("numeric", "How many products does the grid have?", answer="4", why="Two times two.")),
    _block(
        "check", "Where does $i^2$ appear?",
        _check(
            "multiple_choice", "Which entry contains $i^2$?", choices=["top-left", "top-right", "bottom-right"], correct=2,
            why="Only the bottom-right entry multiplies two terms containing $i$.",
        ),
    ),
    _block(
        "check", "A quick fact.",
        _check("true_false", "$i^2=1$", choices=["True", "False"], correct=1, display="False", why="$i^2=-1$."),
    ),
    _block(
        "check", "Pairs versus sets.",
        _check(
            "true_false_set", "Mark each equality true or false.", choices=STATEMENTS, answer="FTT",
            why="Order matters in a pair, not in a set.",
        ),
    ),
    _block(
        "check", "Explain it.",
        _check(
            "reflect", "Why did the bottom-right entry become a real number?",
            display="It contains $i\\cdot i=i^2=-1$.", why="Two factors of $i$ make $i^2$, which is $-1$.",
        ),
    ),
    _block("summary", "Expand, replace $i^2$, collect terms."),
]  # fmt: skip

SECRETS = ["-6", "1+7*i", "1+7i", "bottom-right", "FTT", "i\\cdot i", "Order matters", "Two times two"]


def v2_lesson(key: str = "S1") -> dict:
    """A stored v2 lesson: LessonV2Draft fields + format, with the fixture's two practice knowledge points."""
    kp = {"explanation": "Reminder.", "worked_example": {"problem": "Compute.", "steps": [{"math": "$1$", "why": "w"}], "takeaway": "t"}}
    return {
        "format": "v2",
        "title": f"Skill {key}",
        "objective": "Multiply two complex numbers",
        "goal_line": "Calculate a product such as $(2+3i)(1+2i)$.",
        "blocks": BLOCKS,
        "notation": [],
        "knowledge_points": [{"title": f"{key} step 1", **kp}, {"title": f"{key} step 2", **kp}],
        "common_mistakes": [],
        "connection": "Next: powers of $i$.",
        "deeper": "",
        "source_refs": ["1.3"],
        "assumed": ["S1: add complex numbers"],
        "out_of_scope": ["roots"],
    }


def set_v2(fx, key: str = "S1") -> str:
    sid = fx.sid(key)
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(v2_lesson(key)), sid))
    return sid


def post_check(client: TestClient, sid: str, index: int, **body):
    return client.post(f"/api/skills/{sid}/blocks/{index}/check", json=body)


def assert_no_leak(detail: str) -> None:
    assert not any(secret in detail for secret in SECRETS), detail


def nothing_recorded() -> None:
    for table in ("attempts", "tasks", "xp_log", "review_log", "block_feedback"):
        assert not db.query(f"SELECT 1 FROM {table}"), table
    assert {r["status"] for r in db.query("SELECT status FROM progress")} <= {"available", "locked"}


# ---------------------------------------------------------------------------
# Inline checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "index,body,correct,feedback",
    [
        (2, {"response": "-6"}, True, "Correct."),
        (2, {"response": "−6"}, True, "Correct."),
        (2, {"response": "6"}, False, "Check the sign."),
        (3, {"response": "1+7i"}, True, "Correct."),
        (3, {"response": "7i+1"}, True, "Correct — equivalent to the expected answer."),
        (3, {"response": "1-7i"}, False, "Your answer is the complex conjugate of the expected one — check the sign of the imaginary part."),
        (4, {"response": "4"}, True, "Correct."),
        (4, {"response": "3"}, False, "Not quite."),
        (5, {"choice": 2}, True, "Correct."),
        (5, {"choice": 0}, False, "Not this option."),
        (6, {"choice": 1}, True, "Correct."),
        (6, {"choice": 0}, False, "Not this option."),
        (7, {"response": "FTT"}, True, "Correct."),
        (7, {"response": "false, true, true"}, True, "Correct."),
        (7, {"response": "TTT"}, False, "Statement 1 is actually false."),
        (7, {"response": "T F T"}, False, "Statement 1 is actually false; statement 2 is actually true."),
    ],
)  # fmt: skip
def test_inline_check_grades_every_kind(client: TestClient, index: int, body: dict, correct: bool, feedback: str) -> None:
    fx = build_course()
    sid = set_v2(fx)
    res = post_check(client, sid, index, **body)
    assert res.status_code == 200, res.text
    out = res.json()
    check = BLOCKS[index]["check"]
    assert out["correct"] is correct and out["feedback"] == feedback
    assert out["explanation"] == check["explanation"]  # always shown after a readable response, right or wrong
    expected_display = {5: "bottom-right", 7: "1 False · 2 True · 3 True", 4: "4"}.get(index, check["answer_display"])
    assert out["answer_display"] == expected_display
    nothing_recorded()  # formative: no attempt, task, XP or progress change


def test_reflect_check_is_not_graded(client: TestClient) -> None:
    fx = build_course()
    sid = set_v2(fx)
    for body in ({}, {"response": "because i times i is -1"}, {"choice": 3}):
        out = post_check(client, sid, 8, **body).json()
        assert out == {
            "correct": None,
            "feedback": "Compare with the explanation.",
            "answer_display": "It contains $i\\cdot i=i^2=-1$.",
            "explanation": "Two factors of $i$ make $i^2$, which is $-1$.",
        }
    nothing_recorded()


@pytest.mark.parametrize(
    "index,body,message",
    [
        (2, {}, "Enter an answer first."),
        (2, {"response": "   "}, "Enter an answer first."),
        (2, {"response": "2(("}, "Couldn't read that expression."),
        (2, {"response": "6*(-1)"}, "Same value — now simplify it fully"),
        (2, {"response": "6i^2"}, "Same value — now simplify it fully"),
        (3, {"response": "3+i+6i+2i^2"}, "Same value — now simplify it to the form a + bi."),
        (4, {"response": "four"}, "A number is expected here."),
        (4, {"response": "2+2"}, "Same value — now simplify it fully"),
        (5, {}, "Choose one of the options."),
        (5, {"choice": 3}, "Choose one of the options."),
        (5, {"choice": -1}, "Choose one of the options."),
        (5, {"response": "bottom-right"}, "Choose one of the options."),
        (6, {"choice": 2}, "Choose one of the options."),
        (7, {}, "Mark every statement true or false (3 statements)."),
        (7, {"response": "FT"}, "Mark every statement true or false (3 statements). You marked 2."),
        (7, {"response": "1 0 1"}, "Mark every statement true or false (3 statements). Use T or F"),
        (7, {"choice": 0}, "Mark every statement true or false (3 statements)."),
    ],
)  # fmt: skip
def test_unreadable_inline_answers_are_422_without_leaking_the_answer(client: TestClient, index: int, body: dict, message: str) -> None:
    fx = build_course()
    sid = set_v2(fx)
    res = post_check(client, sid, index, **body)
    if message == "A number is expected here.":  # a readable non-number is a wrong answer, as for questions
        assert res.status_code == 200 and res.json()["correct"] is False and res.json()["feedback"] == message
        return
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert isinstance(detail, str) and detail.startswith(message)
    assert_no_leak(detail)
    nothing_recorded()


def test_check_and_feedback_404s(client: TestClient) -> None:
    fx = build_course()
    sid = set_v2(fx)
    v1 = fx.sid("S2")  # still a v1 lesson: no teaching blocks
    cases = [
        ("nope", 2, "Skill not found."),
        (sid, len(BLOCKS), "This lesson has no such step."),
        (sid, -1, "This lesson has no such step."),
        (sid, 0, "This step has no check."),  # a setup block
        (v1, 0, "This lesson has no such step."),
    ]
    for skill_id, index, detail in cases:
        res = post_check(client, skill_id, index, response="-6")
        assert res.status_code == 404 and res.json()["detail"] == detail
        assert_no_leak(res.json()["detail"])
    assert post_check(client, sid, "x", response="1").status_code == 422  # not an index
    for skill_id, index, _ in cases[:2] + cases[4:]:
        assert client.post(f"/api/skills/{skill_id}/blocks/{index}/feedback", json={}).status_code == 404
    assert client.get("/api/skills/nope/feedback").status_code == 404
    db.execute("UPDATE skills SET lesson_json = NULL WHERE id = ?", (v1,))
    assert post_check(client, v1, 0, response="1").status_code == 404
    nothing_recorded()


def test_a_check_with_an_unknown_kind_or_a_broken_block_is_404(client: TestClient) -> None:
    fx = build_course()
    lesson = v2_lesson()
    lesson["blocks"] = [{"kind": "check", "body": "x", "check": {"kind": "essay", "prompt": "?"}}, "not a block"]
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), fx.sid("S1")))
    assert post_check(client, fx.sid("S1"), 0, response="1").json()["detail"] == "This step has no check."
    assert post_check(client, fx.sid("S1"), 1, response="1").json()["detail"] == "This lesson has no such step."


def test_inline_check_answer_display_fallbacks(client: TestClient) -> None:
    fx = build_course()
    lesson = v2_lesson()
    lesson["blocks"] = [
        _block("check", "x", _check("true_false", "$i^2=-1$", correct=0, why="Yes.")),  # choices omitted by the writer
        _block("check", "x", _check("expression", "Square $i$.", answer="-1", why="$i^2=-1$.")),
    ]
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), fx.sid("S1")))
    out = post_check(client, fx.sid("S1"), 0, choice=0).json()
    assert out["correct"] is True and out["answer_display"] == "True"
    assert post_check(client, fx.sid("S1"), 0, choice=2).status_code == 422
    out = post_check(client, fx.sid("S1"), 1, response="-1").json()
    assert out["correct"] is True and out["answer_display"] == "-1"


# ---------------------------------------------------------------------------
# "This step isn't clear"
# ---------------------------------------------------------------------------


def test_block_feedback_is_stored_with_the_lesson_version(client: TestClient) -> None:
    fx = build_course()
    sid = set_v2(fx)
    stored = db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"]
    res = client.post(f"/api/skills/{sid}/blocks/1/feedback", json={})
    assert res.status_code == 204 and res.content == b""
    res = client.post(f"/api/skills/{sid}/blocks/3/feedback", json={"kind": "unclear", "note": "  Why does 2i² become -2?\x00  "})
    assert res.status_code == 204
    assert client.post(f"/api/skills/{sid}/blocks/3/feedback", json={"kind": "boring"}).status_code == 422
    assert client.post(f"/api/skills/{sid}/blocks/{len(BLOCKS)}/feedback").status_code == 404
    client.post(f"/api/skills/{sid}/blocks/0/feedback", json={"note": "x" * 5000})

    rows = db.query("SELECT * FROM block_feedback ORDER BY created_at, rowid")
    assert [(r["skill_id"], r["block_index"], r["kind"]) for r in rows] == [(sid, 1, "unclear"), (sid, 3, "unclear"), (sid, 0, "unclear")]
    assert rows[1]["note"] == "Why does 2i² become -2?" and len(rows[2]["note"]) == 2000
    assert {r["lesson_hash"] for r in rows} == {hashlib.sha1(stored.encode()).hexdigest()}

    listed = client.get(f"/api/skills/{sid}/feedback").json()
    assert [(f["block_index"], f["kind"], f["note"]) for f in listed] == [
        (1, "unclear", ""), (3, "unclear", "Why does 2i² become -2?"), (0, "unclear", "x" * 2000)
    ]  # fmt: skip
    assert all(f["created_at"] and f["lesson_hash"] == rows[0]["lesson_hash"] for f in listed)
    assert client.get(f"/api/skills/{fx.sid('S2')}/feedback").json() == []

    # a regenerated lesson is a new version: later feedback carries the new hash
    lesson = v2_lesson()
    lesson["goal_line"] = "A clearer goal."
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), sid))
    client.post(f"/api/skills/{sid}/blocks/1/feedback", json={})
    hashes = [f["lesson_hash"] for f in client.get(f"/api/skills/{sid}/feedback").json()]
    assert len(set(hashes)) == 2 and hashes[-1] != hashes[0]
    assert not db.query("SELECT 1 FROM attempts") and not db.query("SELECT 1 FROM tasks")


def _stored_version(sid: str) -> str:
    return hashlib.sha1(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"].encode()).hexdigest()


def test_requests_from_an_outdated_lesson_are_409_and_change_nothing(client: TestClient) -> None:
    """QA: blocks are named by position, so a page showing a regenerated lesson's old blocks must not be graded
    against (or leave feedback on) whatever block now has that index."""
    fx = build_course()
    sid = set_v2(fx)
    old = _stored_version(sid)
    assert views.lesson_view(sid).version == old  # what the page sends back
    for version in (old, old[:12], old[:8].upper(), None, ""):  # the full hash, a prefix of it, or no version at all
        out = post_check(client, sid, 2, response="-6", version=version)
        assert out.status_code == 200 and out.json()["correct"] is True, (version, out.text)
    assert client.post(f"/api/skills/{sid}/blocks/2/feedback", json={"version": old[:12]}).status_code == 204
    assert [r["lesson_hash"] for r in db.query("SELECT lesson_hash FROM block_feedback")] == [old]  # the full hash
    db.execute("DELETE FROM block_feedback")

    # the lesson is regenerated: block 2 is now a reflect check, and there are fewer blocks
    lesson = v2_lesson()
    lesson["blocks"] = [BLOCKS[0], BLOCKS[1], BLOCKS[8], BLOCKS[3]]
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), sid))
    new = _stored_version(sid)
    assert new != old and views.lesson_view(sid).version == new
    stale = [
        post_check(client, sid, 2, response="-6", version=old),  # would be "graded" against the reflect check
        post_check(client, sid, 2, version=old[:12]),
        post_check(client, sid, 7, response="FTT", version=old),  # no longer exists: still 409, not 404
        post_check(client, sid, 2, response="-6", version=new[:4]),  # too short to name a version
        client.post(f"/api/skills/{sid}/blocks/2/feedback", json={"note": "where did -6 come from?", "version": old}),
        client.post(f"/api/skills/{sid}/blocks/9/feedback", json={"version": old[:12]}),
    ]
    for res in stale:
        assert res.status_code == 409 and res.json()["detail"] == "This lesson was updated — reload to see the new version."
    assert not db.query("SELECT 1 FROM block_feedback")
    nothing_recorded()

    out = post_check(client, sid, 2, version=new[:12])
    assert out.status_code == 200 and out.json()["correct"] is None  # the reflect check the reloaded page shows
    assert post_check(client, sid, 3, response="1+7i", version=new).json()["correct"] is True
    assert client.post(f"/api/skills/{sid}/blocks/2/feedback", json={"note": "?", "version": new[:12]}).status_code == 204
    assert client.post(f"/api/skills/{sid}/blocks/1/feedback", json={}).status_code == 204  # older client: accepted
    assert [r["lesson_hash"] for r in db.query("SELECT lesson_hash FROM block_feedback ORDER BY created_at, rowid")] == [new, new]
    assert client.post("/api/skills/nope/blocks/0/feedback", json={"version": old}).status_code == 404  # 404 before 409


def test_feedback_works_on_blocks_without_a_check(client: TestClient) -> None:
    fx = build_course()
    sid = set_v2(fx)
    for index in range(len(BLOCKS)):
        assert client.post(f"/api/skills/{sid}/blocks/{index}/feedback", json={"note": f"block {index}"}).status_code == 204
    assert client.post(f"/api/skills/{sid}/blocks/0/feedback").status_code == 204  # no body: plain "unclear"
    listed = client.get(f"/api/skills/{sid}/feedback").json()
    assert [f["block_index"] for f in listed] == [*range(len(BLOCKS)), 0]
    assert listed[-1]["kind"] == "unclear" and listed[-1]["note"] == ""


def test_a_malformed_answer_key_is_never_a_server_error(client: TestClient) -> None:
    fx = build_course()
    lesson = v2_lesson()
    lesson["blocks"] = [
        _block("check", "x", _check("multiple_choice", "Pick.", choices=["a", "b"], why="w")),  # no correct_choice
        _block("check", "x", _check("true_false_set", "Mark.", choices=STATEMENTS, answer="3", why="w")),
    ]
    lesson["blocks"][0]["check"]["correct_choice"] = "b"
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), fx.sid("S1")))
    for index, body in ((0, {"choice": 1}), (1, {"response": "TFT"})):
        out = post_check(client, fx.sid("S1"), index, **body)
        assert out.status_code == 200 and out.json()["correct"] is False and "no answer key" in out.json()["feedback"]


# ---------------------------------------------------------------------------
# v2 lessons and true_false_set questions in the study loop
# ---------------------------------------------------------------------------


def test_v2_lesson_runs_like_v1_and_inline_checks_never_touch_the_task(client: TestClient) -> None:
    fx = build_course()
    sid = set_v2(fx)
    task = client.post(f"/api/courses/{fx.course_id}/continue").json()
    assert task["type"] == "lesson" and task["skill"]["key"] == "S1" and task["progress"]["kp_total"] == 2
    before = db.query_one("SELECT state_json, phase FROM tasks WHERE id = ?", (task["id"],))
    assert post_check(client, sid, 7, response="TTT").json()["correct"] is False
    assert post_check(client, sid, 2, response="-6").json()["correct"] is True
    assert client.post(f"/api/skills/{sid}/blocks/2/feedback", json={}).status_code == 204
    assert db.query_one("SELECT state_json, phase FROM tasks WHERE id = ?", (task["id"],)) == before
    assert not db.query("SELECT 1 FROM attempts")

    task = client.post(f"/api/tasks/{task['id']}/practice").json()
    while task["phase"] != "done":
        q = question(task["question"]["id"])
        body = {"question_id": q["id"]}
        if q["kind"] in ("multiple_choice", "true_false"):
            body["choice"] = q["correct_choice"]
        else:
            body["response"] = q["answer"]
        task = client.post(f"/api/tasks/{task['id']}/answer", json=body).json()["task"]
    assert task["outcome"] == "passed" and task["skill"]["status"] == "learned"


def _all_true_false_sets(fx, key: str) -> list[list[str]]:
    keys = ["FTT", "TFT", "TTF", "FFT"]
    for kp_questions in fx.questions[key]:
        for qid, answer in zip(kp_questions, keys):
            make_true_false_set(qid, answer)
    return fx.questions[key]


def test_lesson_with_true_false_set_questions() -> None:
    fx = build_course()
    set_v2(fx)
    kp0 = fx.questions["S1"][0]
    for qid, answer in zip(kp0, ["FTT", "TFT", "TTF", "FFT"]):
        make_true_false_set(qid, answer)
    db.execute("UPDATE questions SET answer_display = 'False, true, true' WHERE id = ?", (kp0[0],))
    task_id = engine.start_lesson(fx.sid("S1"), "test", T0)
    task = engine.begin_practice(task_id, T0)
    first = task.question
    assert first is not None and first.kind == "true_false_set" and first.choices == ["Statement 1 about $i$."] + [
        f"Statement {n} about $i$." for n in (2, 3)
    ]

    # unreadable / incomplete marks: asked again, nothing recorded
    for response in ("1 0 1", "FT", "", None):
        with pytest.raises(engine.Invalid) as err:
            engine.submit_answer(task_id, AnswerSubmit(question_id=first.id, response=response), T0)
        assert str(err.value).startswith("Mark every statement true or false (3 statements).")
        assert "FTT" not in str(err.value)
    assert not db.query("SELECT 1 FROM attempts WHERE task_id = ?", (task_id,))

    missed = engine.submit_answer(task_id, AnswerSubmit(question_id=first.id, response="T T T"), T0)
    assert not missed.correct and missed.status == "incorrect" and not missed.independent
    assert missed.feedback.startswith("Statement 1 is actually false.") and "One more question" in missed.feedback
    assert missed.correct_answer == "False, true, true" and missed.correct_choice is None and missed.solution

    assert missed.score == 0 and missed.task.progress.kp_target == 3  # one extra question after the miss

    second = missed.task.question
    a = engine.submit_answer(task_id, right(second.id), T0)
    assert a.correct and a.independent and a.correct_answer == "1 True · 2 False · 3 True" and a.score == 1
    b = engine.submit_answer(task_id, right(a.task.question.id), T0)
    assert b.independent and b.task.progress.kp_index == 1  # the KP's 3 planned questions are done
    task = b.task
    while task.phase != "done":
        task = engine.submit_answer(task_id, right(task.question.id), T0).task
    assert task.outcome == "passed" and task.progress.score == pytest.approx(0.8)
    attempts = db.query("SELECT response, correct, independent FROM attempts WHERE task_id = ? ORDER BY created_at, rowid", (task_id,))
    assert [(r["response"], r["correct"], r["independent"]) for r in attempts[:3]] == [
        ("T T T", 0, 0), ("TFT", 1, 1), ("TTF", 1, 1)
    ]


def test_wrong_true_false_sets_count_as_misses_and_lead_to_the_easier_step() -> None:
    fx = build_course()
    _all_true_false_sets(fx, "S1")
    kp0 = fx.questions["S1"][0]
    db.execute("UPDATE questions SET tier = 3 WHERE id = ?", (kp0[1],))  # served second ...
    db.execute("UPDATE questions SET tier = 1 WHERE id = ?", (kp0[3],))  # ... with an easier one left
    task_id = engine.start_lesson(fx.sid("S1"), "test", T0)
    task = engine.begin_practice(task_id, T0)
    r1 = engine.submit_answer(task_id, wrong(task.question.id), T0)
    assert not r1.correct and r1.score == 0 and r1.task.progress.kp_wrong == 1 and r1.task.question.id == kp0[1]
    assert r1.suggestion == "easier_step" and r1.task.progress.easier_available
    swapped = engine.easier_step(task_id, T0)
    assert swapped.question.id == kp0[3] and swapped.question.kind == "true_false_set"
    assert engine.submit_answer(task_id, right(kp0[3]), T0).correct


def test_reveal_and_skip_on_a_true_false_set() -> None:
    fx = build_course()
    _all_true_false_sets(fx, "S1")
    task_id = engine.start_lesson(fx.sid("S1"), "test", T0)
    qid = engine.begin_practice(task_id, T0).question.id
    shown = engine.reveal(qid, T0)
    assert shown.correct_answer == "1 False · 2 True · 3 True"
    result = engine.submit_answer(task_id, AnswerSubmit(question_id=qid), T0)  # solution viewed, nothing submitted
    assert not result.correct and result.feedback.startswith("Solution viewed")


def test_review_with_true_false_set_questions(client: TestClient) -> None:
    fx = build_course()
    set_v2(fx)
    _all_true_false_sets(fx, "S1")
    learn(fx, "S1", T0)
    now = T0 + days(3)
    task_id = engine.start_review(fx.sid("S1"), "Review due", now)
    task = engine.get_task(task_id)
    st = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])
    assert len(st["set"]) == 3 and {question(q)["kind"] for q in st["set"]} == {"true_false_set"}
    assert sorted({question(q)["kp_index"] for q in st["set"]}) == [0, 1]

    bad = client.post(f"/api/tasks/{task_id}/answer", json={"question_id": task.question.id, "response": "yes"})
    assert bad.status_code == 422 and bad.json()["detail"].startswith("Mark every statement true or false (3 statements).")
    body = {"question_id": task.question.id, "response": question(task.question.id)["answer"]}
    first = client.post(f"/api/tasks/{task_id}/answer", json=body)
    assert first.json()["correct"] and first.json()["independent"]
    second_q = question(first.json()["task"]["question"]["id"])
    flipped = "".join("F" if c == "T" else "T" for c in second_q["answer"])
    second = client.post(f"/api/tasks/{task_id}/answer", json={"question_id": second_q["id"], "response": flipped}).json()
    assert second["correct"] is False and second["feedback"].count("is actually") == 3
    assert second["correct_answer"] == " · ".join(
        f"{n} {'True' if c == 'T' else 'False'}" for n, c in enumerate(second_q["answer"], start=1)
    )
    third = engine.submit_answer(task_id, right(second["task"]["question"]["id"], hints_used=1), now)
    assert third.task.phase == "done"
    log = db.loads(db.query_one("SELECT state_json FROM tasks WHERE id = ?", (task_id,))["state_json"])["log"]
    assert [e["credit"] for e in log] == [1.0, 0.0, 0.5]
    assert third.task.outcome == "failed"  # (2 + 0 + 1) / 6 = 0.5 < pass score
