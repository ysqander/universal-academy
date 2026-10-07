"""Iteration-3 review fixes (fake LLM, offline): capstone content rules — no step whose answer is known before it is
computed (stated in the problem or a step prompt, an earlier result again, forced by a definition), complex results
in the form a+bi instead of coordinate pairs — enforced for v1 and v2 lessons (strict → regenerate / revise,
lenient → drop the giveaway step); v1 lessons now end with a capstone too (stored, verified, regenerated, untouched by
top-ups); prompts carry the decisions (answer form, capstone pattern, served sequence, motivation, reflect checks,
questions that need the lesson's idea)."""

from __future__ import annotations

import asyncio

import pytest
from test_pipeline_build import build, llm_calls, tiny_course  # noqa: F401  (fixture)
from test_pipeline_v2 import built_course, skill_by_title, split_plan

from app import db, fake_llm
from app.llm_schemas import LessonDraft, LessonV2Draft, MultistepStep, QuestionDraft
from app.pipeline import topup
from app.pipeline.lesson_v2 import capstone_defects, content_problems, validate_v2, write_request
from app.pipeline.lessons import InvalidLesson, capstone_problems, validate_capstone, validate_lesson
from app.pipeline.questions import (
    capstone_issues,
    clean_capstone,
    format_warnings,
    pair_form_warnings,
    step_reference_problems,
)
from app.pipeline.regenerate import regenerate
from app.pipeline.verify import verify_question
from app.pipeline.replan import replan
from app.views import lesson_view


def step(prompt: str, answer: str, display: str = "", kind: str = "expression", **kw) -> MultistepStep:
    base = dict(prompt=prompt, kind=kind, choices=[], correct_choice=None, answer=answer, answer_display=display or f"${answer}$",
                tolerance=None, hint="Think about the move.", explanation="Because.")
    return MultistepStep(**(base | kw))


def capstone(prompt: str, steps: list[MultistepStep]) -> QuestionDraft:
    return QuestionDraft(key="cap", tier=3, kind="multistep", prompt=prompt, choices=[], correct_choice=None, answer="",
                         answer_display="", tolerance=None, hints=[], solution="", checklist=[], targets="chain",
                         source_exercise=None, steps=steps)


# The two capstones the iteration-3 review rejected (S3a and S3b of the learner's course), and a rewrite of each.
S3A_OLD = capstone(
    "Construct the reciprocal of $1-2i$ by cancellation and normalization, then verify it by multiplying back. Enter "
    "each pair of coordinates as an ordered tuple $(\\text{real coordinate},\\text{imaginary coordinate})$.",
    [
        step("Compute $(1-2i)(1+2i)$. Enter the resulting real number.", "5", kind="numeric"),
        step("Use the product $5$ to normalize $1+2i$. Enter the reciprocal's coordinates as "
             "$(\\text{real coordinate},\\text{imaginary coordinate})$.", "(1/5, 2/5)"),
        step("Multiply $1-2i$ by the reciprocal $\\frac15+\\frac25i$. Enter the product's coordinates as "
             "$(\\text{real coordinate},\\text{imaginary coordinate})$.", "(1, 0)"),
    ],
)
S3A_ABI = capstone(
    "Construct the reciprocal of $1-2i$, then verify it by multiplying back.",
    [
        step("Compute $(1-2i)(1+2i)$. Enter the resulting real number.", "5", kind="numeric"),
        step("Use the product $5$ to normalize $1+2i$. Enter the reciprocal in the form $a+bi$.", "1/5+2*i/5"),
        step("Multiply $1-2i$ by the reciprocal $\\frac15+\\frac25i$. Enter the product in the form $a+bi$.", "1"),
    ],
)
S3B_OLD = capstone(
    "Solve $(1+2i)z=4-3i$ by using the coefficient's reciprocal, then check your solution by multiplying back.",
    [
        step("Find $1/(1+2i)$. Enter it in the form $a+bi$.", "1/5-2*i/5"),
        step("Use $1/(1+2i)=1/5-(2/5)i$ to compute $z=(4-3i)[1/(1+2i)]$. Enter $z$ in the form $a+bi$.", "-2/5-11*i/5"),
        step("Using $z=-2/5-(11/5)i$, compute $(1+2i)z$ to check the equation. Enter it in the form $a+bi$.", "4-3*i"),
    ],
)
GOOD = capstone(
    "Let $w=(1+3i)(2-i)$. Find $w$, then its reciprocal, then use that result.",
    [
        step("Compute $w$. Enter it in the form $a+bi$.", "5+5*i"),
        step("Enter $1/w$ in the form $a+bi$.", "1/10-i/10"),
        step("$5-5i$ differs from $w$ only in the sign of its imaginary coordinate. Without a new construction, enter "
             "$1/(5-5i)$ in the form $a+bi$.", "1/10+i/10"),
    ],
)


def test_capstone_steps_whose_answer_is_known_in_advance_are_found():
    assert capstone_issues(clean_capstone(GOOD)) == []
    old = capstone_issues(clean_capstone(S3A_OLD))  # the pair (1, 0) is judged as 1+0i: forced as well
    assert [(i, "coordinate pair" in m) for i, m in old] == [(1, True), (2, False), (2, True)]
    assert "a number times its reciprocal is 1" in old[1][1]
    assert [(i, "a number times its reciprocal is 1" in m) for i, m in capstone_issues(clean_capstone(S3A_ABI))] == [(2, True)]
    s3b = capstone_issues(clean_capstone(S3B_OLD))
    assert [i for i, _ in s3b] == [2] and "already written in the problem" in s3b[0][1]
    # "confirm you get 1" in the step's own prompt; the reciprocal of a reciprocal (an earlier result again)
    confirm = capstone("Let $z=3+4i$.", [step("Compute $z^2$. Enter it in the form $a+bi$.", "-7+24*i"),
                                          step("Multiply $1/z$ by $z$ and confirm that you get $1$.", "1", kind="numeric")])
    assert [i for i, _ in capstone_issues(clean_capstone(confirm))] == [1]
    twice = capstone("Let $w=(2+i)(2-3i)$.", [step("Enter $w$ in the form $a+bi$.", "7-4*i"),
                                               step("Enter $1/w$ in the form $a+bi$.", "7/65+4*i/65"),
                                               step("Enter the reciprocal of your step-2 answer.", "7-4*i")])
    assert [(i, "earlier step's result" in m) for i, m in capstone_issues(clean_capstone(twice))] == [(2, True)]
    # an integer that merely coincides with an earlier result, "use $i^2=-1$", products and vectors are not flagged
    fine = capstone("Use $i^2=-1$ throughout. Let $u=(2+i)(2-i)$ and $x=(1,2)$.", [
        step("Compute $u$. Enter a number.", "5", kind="numeric"),
        step("What is $(1+2i)(1-2i)$? Enter a number.", "5", kind="numeric"),
        step("Compute $3x$ in $\\mathbf{R}^2$. Enter it as $(x_1, x_2)$.", "(3, 6)"),
        step("What is $i^2$? Enter a number.", "-1", kind="numeric"),
    ])
    assert capstone_issues(clean_capstone(fine)) == []
    # a sum or difference written in the problem is a computation for the earlier skill's step, not a stated value
    # (the S2d and S7 re-plans of 2026-10-07 lost exactly this step)
    sums = capstone("Let $w=(12+5i)-(3+45i)$ and $\\alpha=(4-5i)+(-7+2i)$; also consider $3-(1+i)$.", [
        step("Compute $w$. Enter it in the form $a+bi$.", "9-40*i"),
        step("Compute $\\alpha$. Enter it in the form $a+bi$.", "-3-3*i"),
        step("Compute $3-(1+i)$. Enter it in the form $a+bi$.", "2-i"),
    ])
    assert capstone_issues(clean_capstone(sums)) == []
    stated = capstone("Let $w=9-40i$.", [step("Enter $w$ in the form $a+bi$.", "9-40*i"),
                                         step("Compute $2w$. Enter it in the form $a+bi$.", "18-80*i")])
    assert [i for i, _ in capstone_issues(clean_capstone(stated))] == [0]


def test_capstone_validation_is_strict_first_then_drops_the_giveaway_step():
    assert capstone_problems(GOOD) == [] and validate_capstone(GOOD) == (clean_capstone(GOOD), [])
    with pytest.raises(InvalidLesson, match="capstone: step 3 .*a number times its reciprocal is 1"):
        validate_capstone(S3A_ABI)
    cap, notes = validate_capstone(S3A_ABI, strict=False)
    assert [s.answer for s in cap.steps] == ["5", "1/5+2*i/5"] and cap.answer_display == "$1/5+2*i/5$"
    assert any("step dropped" in n for n in notes)
    # a coordinate-pair answer is a revision problem, but a last draft keeps it (with a note)
    cap, notes = validate_capstone(S3A_OLD, strict=False)
    assert [s.answer for s in cap.steps] == ["5", "(1/5, 2/5)"] and any("coordinate pair" in n and "kept" in n for n in notes)
    assert any("step 3" in n and "step dropped" in n for n in notes)
    two = capstone("Solve $(1+i)z=2$.", [step("Find $z$ in the form $a+bi$.", "1-i"),
                                          step("Check: compute $(1+i)z$.", "2", kind="numeric")])
    cap, notes = validate_capstone(two, strict=False)
    assert cap is None and any("fewer than 2 steps" in n for n in notes)
    plain = GOOD.model_copy(update={"kind": "expression", "steps": [], "answer": "1"})
    assert capstone_problems(plain) == ["capstone: must be a multistep question (2–4 guided, machine-checked steps), not expression"]


def test_capstone_steps_refer_only_to_earlier_steps_that_exist():
    # what a dropped step left behind in the S2d re-plan of 2026-10-07
    dangling = capstone("Let $w=(12+5i)-(3+45i)$. Find the roots of $z^2=w$, then use the root from Step 3.", [
        step("Using your value of $w$, find the root with positive real part. Enter it in the form $a+bi$.", "5-4*i"),
        step("Let $u$ be the root from Step 2. Compute $(1+u)^2$ in the form $a+bi$.", "20-48*i"),
    ])
    problems = step_reference_problems(clean_capstone(dangling))
    assert any("the problem refers to step 3" in p for p in problems)
    assert any("step 2 refers to step 2" in p for p in problems)
    assert any("refers to step" in p for p in capstone_problems(dangling))
    with pytest.raises(InvalidLesson, match="refers to step"):
        validate_capstone(dangling)
    assert step_reference_problems(clean_capstone(GOOD)) == []
    fine = capstone("Let $w=(2+i)(2-i)$.", [step("Compute $w$.", "5", kind="numeric"),
                                             step("Using your result from step 1, enter $1/w$.", "1/5", kind="numeric")])
    assert step_reference_problems(clean_capstone(fine)) == []


def _capstone_row(skill_id: str) -> dict:
    return db.query_one(
        "SELECT * FROM questions WHERE skill_id = ? AND role = 'capstone' AND key NOT LIKE 'old-%'", (skill_id,)
    )


def test_a_capstone_still_broken_after_the_revision_is_rewritten_not_cut(tmp_path, monkeypatch, llm_calls):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    original = fake_llm.respond

    def giveaway_always(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"] is LessonV2Draft:
            result.capstone = S3A_ABI  # the revision does not fix it either
        return result

    monkeypatch.setattr(fake_llm, "respond", giveaway_always)
    llm_calls.clear()
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revised and llm_calls["CapstoneRepair"] == 1
    assert any(n.startswith("capstone rewritten") and "reciprocal is 1" in n for n in result.notes)
    row = _capstone_row(result.skill_id)
    assert len(db.loads(row["steps_json"])) == 3 and not row["flagged"]  # all steps kept (none dropped)


def test_regenerate_rewrites_only_a_withheld_capstone(tmp_path, llm_calls):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    sid = asyncio.run(replan(cid, key)).results[0].skill_id
    old = _capstone_row(sid)
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, state_json, created_at) VALUES (?, ?, ?, 'lesson', 'completed', '{}', ?)",
        (task_id, cid, sid, db.now_iso()),
    )
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, response, created_at) VALUES (?,?,?,?,1,'correct','{}',?)",
        (db.new_id(), task_id, old["id"], sid, db.now_iso()),
    )
    db.execute(
        "UPDATE questions SET flagged = 1, flag_reason = 'verification: step 2: reported problem — option 3 is also correct' WHERE id = ?",
        (old["id"],),
    )
    assert capstone_defects(sid)[0].startswith("it is withheld from the learner — verification: step 2")
    practice = {r["id"] for r in db.query("SELECT id FROM questions WHERE skill_id = ? AND role != 'capstone'", (sid,))}
    llm_calls.clear()
    report = asyncio.run(regenerate(cid, [key], lessons=False, visuals=False, verify=False, capstones=True))
    assert llm_calls["CapstoneRepair"] == 1 and llm_calls["LessonV2Draft"] == 0
    assert "stored and verified" in report.capstones[0]
    new = _capstone_row(sid)
    assert new["id"] != old["id"] and not new["flagged"] and capstone_defects(sid) == []
    retired = db.query_one("SELECT * FROM questions WHERE id = ?", (old["id"],))
    assert retired["key"] == "old-cap1" and retired["flagged"]  # answered: kept as history, never deleted
    assert {r["id"] for r in db.query("SELECT id FROM questions WHERE skill_id = ? AND role != 'capstone'", (sid,))} == practice
    # a capstone without a defect is left alone unless a note names one
    llm_calls.clear()
    assert "capstone fine" in asyncio.run(regenerate(cid, [key], lessons=False, visuals=False, verify=False, capstones=True)).capstones[0]
    assert llm_calls["CapstoneRepair"] == 0
    noted = asyncio.run(regenerate(cid, [key], lessons=False, visuals=False, verify=False, capstones=True,
                                   capstone_notes=["the problem asks to compute alpha but no step does"]))
    assert "stored and verified" in noted.capstones[0] and llm_calls["CapstoneRepair"] == 1


def test_a_reported_problem_is_rechecked_before_it_withholds_a_capstone(tmp_path, monkeypatch):
    cid = built_course(tmp_path)
    sid = asyncio.run(replan(cid, skill_by_title(cid, "Distinguish lists")["key"])).results[0].skill_id
    row = _capstone_row(sid)
    original = fake_llm.respond
    confirm = {"graph": False}

    def weak_false_alarm(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"].__name__ == "IndependentAnswer" and (kwargs["role"] == "verify" or confirm["graph"]):
            result.problems = "Option 3 is also a valid answer."
        return result

    monkeypatch.setattr(fake_llm, "respond", weak_false_alarm)
    verdict = asyncio.run(verify_question(row))
    assert not verdict.flagged and verdict.record["status"] == "agree_on_recheck"
    assert all(s["status"] == "agree_on_recheck" for s in verdict.record["steps"]) and "found no defect" in verdict.note
    confirm["graph"] = True  # the careful re-check confirms the defect: withheld
    verdict = asyncio.run(verify_question(row))
    assert verdict.flagged and verdict.record["status"] == "problem" and "also a valid answer" in verdict.reason


def test_v2_giveaway_capstones_go_to_the_revision(tmp_path, monkeypatch):
    spec = split_plan().skills[0]
    draft = fake_llm.respond(schema=LessonV2Draft, role="lesson", instructions="", text="Write the lesson for skill S1: x")
    bad = draft.model_copy(update={"capstone": S3B_OLD})
    assert any("step 3" in p and "already written" in p for p in content_problems(bad))
    with pytest.raises(InvalidLesson, match="capstone: step 3"):
        asyncio.run(validate_v2(bad, spec, []))
    _, _, cap, notes = asyncio.run(validate_v2(bad, spec, [], strict=False))
    assert len(cap.steps) == 2 and any("step dropped" in n for n in notes)

    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    original = fake_llm.respond
    revisions: list[str] = []

    def giveaway_first(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"] is LessonV2Draft:
            if "REVISION:" in kwargs["text"]:
                revisions.append(kwargs["text"])
            else:
                result.capstone = S3A_ABI
        return result

    monkeypatch.setattr(fake_llm, "respond", giveaway_first)
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revised and "a number times its reciprocal is 1" in revisions[0]
    cap = db.query_one("SELECT steps_json FROM questions WHERE skill_id = ? AND role = 'capstone'", (result.skill_id,))
    assert db.loads(cap["steps_json"])[0]["answer"] == "5+5*i"  # the revised (fake) capstone was stored


def test_v1_lessons_end_with_a_verified_capstone(tmp_path, llm_calls):
    draft = fake_llm.respond(schema=LessonDraft, role="lesson", instructions="", text="skill S1: x")
    lesson, pools, _ = validate_lesson(draft)
    assert "capstone" not in lesson  # stored as a question, not in lesson_json
    cid = tiny_course(tmp_path)
    build(cid)
    for skill in db.query("SELECT id, key, lesson_json FROM skills WHERE course_id = ?", (cid,)):
        rows = db.query("SELECT * FROM questions WHERE skill_id = ? ORDER BY rowid", (skill["id"],))
        caps = [r for r in rows if r["role"] == "capstone"]
        n_kps = len(db.loads(skill["lesson_json"])["knowledge_points"])
        assert len(caps) == 1 and (caps[0]["key"], caps[0]["kind"], caps[0]["tier"], caps[0]["kp_index"]) == ("cap", "multistep", 3, n_kps)
        assert db.loads(caps[0]["verify_json"])["status"] == "agree" and not caps[0]["flagged"]
        assert "capstone" not in db.loads(skill["lesson_json"])
        view = lesson_view(skill["id"])
        assert sum(kp.question_count for kp in view.knowledge_points) == len(rows) - 1  # the capstone is in no KP

    # a top-up never touches the capstone; a regeneration replaces it (never two live capstones)
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S1'", (cid,))["id"]
    assert asyncio.run(topup.top_up_questions(sid, kp_index=1, n=2)) == 2
    from app.pipeline.regenerate import regenerate

    asyncio.run(regenerate(cid, ["S1"], visuals=False))
    live = db.query("SELECT key, kp_index FROM questions WHERE skill_id = ? AND role = 'capstone' AND key NOT LIKE 'old-%'", (sid,))
    assert live == [{"key": "cap", "kp_index": 2}]


def test_v1_lesson_with_an_invalid_capstone_is_regenerated_once(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    original = fake_llm.respond
    texts: list[str] = []

    def bad_capstone(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"] is LessonDraft and "skill S3:" in kwargs["text"]:
            texts.append(kwargs["text"])
            result.capstone = S3A_ABI if len(texts) == 1 else S3B_OLD
        return result

    monkeypatch.setattr(fake_llm, "respond", bad_capstone)
    build(cid)
    assert len(texts) == 2 and "A previous draft of this lesson was rejected: capstone: step 3" in texts[1]
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S3'", (cid,))["id"]
    cap = db.query_one("SELECT steps_json FROM questions WHERE skill_id = ? AND role = 'capstone'", (sid,))
    assert [s["answer"] for s in db.loads(cap["steps_json"])] == ["1/5-2*i/5", "-2/5-11*i/5"]  # last draft: repaired


def test_coordinate_pair_answers_in_practice_are_reported():
    q = QuestionDraft(key="k1q1", tier=1, kind="expression", prompt="Enter the real and imaginary coordinates of $1/(1+i)$ as a pair.",
                      choices=[], correct_choice=None, answer="(1/2, -1/2)", answer_display="", tolerance=None, hints=["a", "b", "c"],
                      solution="s", checklist=[], targets="", source_exercise=None, steps=[])
    vector = q.model_copy(update={"key": "k1q2", "prompt": "Compute $(1,2)+(3,4)$ in $\\mathbf{R}^2$.", "answer": "(4, 6)"})
    assert pair_form_warnings([q, vector]) == ["a complex number asked for as a coordinate pair (use the form a+bi) in k1q1"]
    assert "coordinate pair" not in " ".join(format_warnings([[vector]]))


def test_prompts_carry_the_iteration_3_review_decisions():
    from app.pipeline.prompts import (
        CAPSTONE_RULE,
        COMPLEX_FORM_RULE,
        DISTINCT_DEMANDS_RULE,
        LESSON_INSTRUCTIONS,
        MATH_FORMAT,
        TOPUP_INSTRUCTIONS,
    )
    from app.pipeline.prompts_v2 import (
        PLANNER_INSTRUCTIONS,
        QUESTION_RULES,
        REVIEWER_INSTRUCTIONS,
        SERVED_SEQUENCE_RULE,
        WRITER_INSTRUCTIONS,
    )

    flat = lambda t: " ".join(t.split())  # noqa: E731
    planner, writer, reviewer, v1 = map(flat, (PLANNER_INSTRUCTIONS, WRITER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS, LESSON_INSTRUCTIONS))
    # (a) complex numbers in the form a+bi everywhere
    for prompt in (LESSON_INSTRUCTIONS, TOPUP_INSTRUCTIONS, QUESTION_RULES):
        assert COMPLEX_FORM_RULE in prompt
    assert "never as coordinate pairs" in flat(CAPSTONE_RULE) and "never as coordinate pairs" in planner
    assert "a complex result asked as a coordinate pair" in reviewer and "\\text{...}" in MATH_FORMAT
    # (b) no step whose answer is known in advance; (c) chain an earlier skill, no replay in parts
    cap = flat(CAPSTONE_RULE)
    assert "No step's answer may be known before it is computed" in cap and "the earlier skill does real work" in cap.lower()
    assert "do not give away its key decision" in cap and "neither whole nor split into parts" in cap
    assert CAPSTONE_RULE in WRITER_INSTRUCTIONS and CAPSTONE_RULE in LESSON_INSTRUCTIONS  # v1 lessons have one too
    assert "capstone: the lesson's final multistep problem" in v1
    assert "whole chain is carried out once more" not in flat(SERVED_SEQUENCE_RULE)
    assert "never the sub-steps of a served question" in flat(SERVED_SEQUENCE_RULE)
    assert "check the quotient by multiplying back" not in planner and "No step whose answer is known in advance" in planner
    assert "a step whose answer is known before it is computed" in reviewer and "no earlier skill doing real work" in reviewer
    # motivate the goal of a transformation, not only its mechanism; never rebut a move before it is introduced
    assert "X must connect to the goal" in planner and "never open with a bridge that rebuts a move" in planner
    assert "why the intermediate result helps reach the goal" in writer and "Never refer to or rebut a move" in writer
    assert "a move justified only by its mechanism or immediate effect" in reviewer
    # the one reflect check: writer and reviewer agree
    assert "it asks WHY" in writer and "never turn it into a numeric check" in reviewer
    assert "other than the lesson's one explanation ('reflect') check" in reviewer
    # questions need the lesson's idea
    assert "needs THIS knowledge point's idea" in DISTINCT_DEMANDS_RULE and "a question whose answer needs no idea of this lesson" in reviewer


def test_writer_request_marks_the_planned_success_check_as_advisory(tmp_path):
    cid = built_course(tmp_path)
    from app.pipeline.context import load_course

    skill = skill_by_title(cid, "Distinguish lists")
    text = write_request(load_course(cid), skill, split_plan().skills[0], [])
    assert "Success check — the planner's idea for the capstone (where it conflicts with the CAPSTONE rules, the rules win" in text
