"""Iteration 3 (the learner's review of the S2 lessons), fake LLM, offline: multistep questions (validation, storage,
public view without answers, step-by-step verification with a re-check), the lesson capstone (required in v2, stored
with role 'capstone' after the last knowledge point, never un-flagged), knowledge-point targets (stored, shown,
kept servable), no free_response / common mistakes in new content, and the prompts' new rules."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_pipeline_build import build, llm_calls, solver_calls, tiny_course  # noqa: F401  (fixture)
from test_pipeline_v2 import built_course, skill_by_title, split_plan

from app import db, fake_llm
from app.llm_schemas import IndependentAnswer, LessonReview, LessonV2Draft, MultistepStep, QuestionDraft
from app.pipeline import jobs, topup, verify
from app.pipeline.lesson_v2 import content_problems, render_lesson, stored_questions, validate_v2
from app.pipeline.lessons import InvalidLesson
from app.pipeline.prompts import (
    DISTINCT_DEMANDS_RULE,
    LESSON_INSTRUCTIONS,
    MULTISTEP_FORMAT,
    NO_WRITTEN_ANSWERS_RULE,
    TARGET_RULE,
    TOPUP_INSTRUCTIONS,
    VERIFY_STEP_INSTRUCTIONS,
    VERIFY_STEP_RECHECK,
)
from app.pipeline.questions import InvalidQuestion, asks_to_show_working, clean_question, format_warnings
from app.pipeline.replan import replan, report_lines
from app.views import lesson_view, question_public


def draft() -> LessonV2Draft:
    return fake_llm.respond(schema=LessonV2Draft, role="lesson", instructions="", text="Write the lesson for skill S1: x")


def step(**kw) -> MultistepStep:
    base = dict(prompt="Compute $(1+i)^2$. Enter $a+bi$.", kind="expression", choices=[], correct_choice=None,
                answer="2*i", answer_display="$2i$", tolerance=None, hint="Use $i^2=-1$.", explanation="$1+2i+i^2=2i$.")
    return MultistepStep(**(base | kw))


def multistep(steps: list[MultistepStep], **kw) -> QuestionDraft:
    base = dict(key="k1q3", tier=2, kind="multistep", prompt="Find $(1+i)^4$ by squaring twice.", choices=["x"],
                correct_choice=1, answer="junk", answer_display="", tolerance=0.1, hints=["a", "b", "c"], solution="",
                checklist=["c"], targets="chain", source_exercise=None, steps=steps)
    return QuestionDraft(**(base | kw))


def capstone_row(sid: str) -> dict:
    row = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND role = 'capstone'", (sid,))
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Multistep validation
# ---------------------------------------------------------------------------


def test_multistep_questions_are_validated_step_by_step():
    q = clean_question(multistep([step(), step(prompt="Square $2i$. Enter a number.", kind="numeric", answer="-4",
                                               answer_display="$-4$", explanation="$(2i)^2=4i^2=-4$.")]))
    assert (q.answer, q.choices, q.correct_choice, q.tolerance, q.checklist, q.hints) == ("", [], None, None, [], [])
    assert q.answer_display == "$-4$"  # the final step's result when the draft gave none
    assert "**Step 1.**" in q.solution and len(q.steps) == 2
    mc = step(kind="multiple_choice", choices=["$2i$", "$-2i$", "$2$"], correct_choice=0, answer="ignored",
              prompt="Which is $(1+i)^2$?")
    tf = step(kind="true_false", choices=[], correct_choice=1, answer="", prompt="Is $(1+i)^2$ real?")
    q = clean_question(multistep([mc, tf]))
    assert q.steps[0].answer == "" and q.steps[1].choices == ["True", "False"]
    for steps, reason in [
        ([step()], "2-4 guided steps"),
        ([step()] * 5, "2-4 guided steps"),
        ([step(), step(hint=" ")], "step 2: empty hint"),
        ([step(explanation=""), step()], "step 1: empty explanation"),
        ([step(), step(answer_display="")], "step 2: empty answer_display"),
        ([step(), step(answer="(((")], "step 2: the grader cannot read"),
        ([step(), step(kind="multiple_choice", choices=["a", "b"], correct_choice=5)], "step 2: correct_choice 5"),
        ([step(), step(kind="true_false_set", choices=["a", "b", "c"], answer="TF")], "step 2: .*one letter"),
    ]:
        with pytest.raises(InvalidQuestion, match=reason):
            clean_question(multistep(steps))
    # steps of a non-multistep question are dropped
    plain = clean_question(multistep([step(), step()], kind="expression", answer="-4", choices=[], correct_choice=None))
    assert plain.steps == [] and plain.answer == "-4"


def test_show_your_working_prompts_are_reported():
    assert asks_to_show_working("Write a worked evaluation of $(3-2i)(-1+4i)$. Your working must show all four products.")
    assert asks_to_show_working("Show your working and enter the result.")
    assert not asks_to_show_working("Compute $(3-2i)(-1+4i)$. Enter your answer in the form $a+bi$.")
    q = clean_question(multistep([step(), step()], kind="expression", answer="-4", choices=[], correct_choice=None,
                                 prompt="Show all your working, then enter $(1+i)^4$."))
    assert any("show working" in n and q.key in n for n in format_warnings([[q]]))


# ---------------------------------------------------------------------------
# v2 validation: capstone required, no free_response, no common mistakes
# ---------------------------------------------------------------------------


def test_v2_lesson_needs_a_valid_multistep_capstone():
    spec = split_plan().skills[0]
    good = draft()
    assert content_problems(good) == []
    no_cap = good.model_copy(deep=True)
    no_cap.capstone = no_cap.knowledge_points[0].questions[2]  # a plain numeric question
    assert content_problems(no_cap) == ["capstone: must be a multistep question (2–4 guided, machine-checked steps), not numeric"]
    with pytest.raises(InvalidLesson, match="capstone: must be a multistep question"):
        asyncio.run(validate_v2(no_cap, spec, []))
    lesson, pools, capstone, notes = asyncio.run(validate_v2(no_cap, spec, [], strict=False))
    assert capstone is None and any("no valid capstone" in n for n in notes) and pools
    one_step = good.model_copy(deep=True)
    one_step.capstone.steps = one_step.capstone.steps[:1]
    assert "2-4 guided steps" in content_problems(one_step)[0]


def test_free_response_and_capstone_problems_are_all_sent_to_the_revision(tmp_path, monkeypatch, llm_calls):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    original = fake_llm.respond
    revision_texts: list[str] = []

    def bad_first_draft(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"] is LessonV2Draft:
            if "REVISION:" in kwargs["text"]:
                revision_texts.append(kwargs["text"])
            else:
                written = result.knowledge_points[0].questions[0].model_copy(
                    update={"key": "k1q9", "kind": "free_response", "prompt": "Write a worked evaluation of $(3-2i)(-1+4i)$.",
                            "choices": [], "correct_choice": None, "checklist": ["four products", "$i^2=-1$"]})
                result.knowledge_points[1].questions.append(written)
                result.capstone = result.capstone.model_copy(update={"kind": "expression", "steps": []})
        return result

    monkeypatch.setattr(fake_llm, "respond", bad_first_draft)
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revised
    text = revision_texts[0]
    assert "knowledge point 2 has free_response question(s) k1q9" in text and "capstone: must be a multistep" in text
    rows = db.query("SELECT * FROM questions WHERE skill_id = ?", (result.skill_id,))
    assert not any(r["kind"] == "free_response" for r in rows)
    assert sum(r["role"] == "capstone" for r in rows) == 1


# ---------------------------------------------------------------------------
# Storage and views
# ---------------------------------------------------------------------------


def test_capstone_is_stored_after_the_last_knowledge_point_and_shown_without_answers(tmp_path, llm_calls):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    report = asyncio.run(replan(cid, key))
    sid = report.results[0].skill_id
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
    assert [kp["target_questions"] for kp in lesson["knowledge_points"]] == [2, 1] and lesson["common_mistakes"] == []
    cap = capstone_row(sid)
    assert (cap["key"], cap["kind"], cap["kp_index"], cap["tier"]) == ("cap", "multistep", 2, 3)
    steps = db.loads(cap["steps_json"])
    assert [s["kind"] for s in steps] == ["expression", "multiple_choice", "true_false_set"]
    assert steps[0]["answer"] == "5+5*i" and steps[2]["answer"] == "TFT" and all(s["explanation"] for s in steps)
    practice = db.query("SELECT * FROM questions WHERE skill_id = ? AND role = 'practice'", (sid,))
    assert {r["kp_index"] for r in practice} == {0, 1}

    public = question_public(cap)
    assert public.role == "capstone" and [s.index for s in public.steps] == [0, 1, 2]
    assert public.steps[1].choices == ["$1-i$", "$1+i$", "$5+5i$", "$i$"] and public.steps[0].hint
    shown = public.model_dump()  # without the fake verifier's invisible markers
    shown["steps"] = [{**st, "prompt": fake_llm._MARKER_RE.sub("", st["prompt"])} for st in shown["steps"]]
    dumped = json.dumps(shown)
    for secret in ("5+5*i", "TFT", "$(5+5i)(1-i)=5-5i+5i-5i^2=10$", "correct_choice", "explanation"):
        assert secret not in dumped, secret
    ordinary = question_public(practice[0])
    assert ordinary.role == "practice" and ordinary.steps == []

    view = lesson_view(sid)
    assert [kp.target_questions for kp in view.knowledge_points] == [2, 1] and view.common_mistakes == []
    assert [kp.question_count for kp in view.knowledge_points] == [4, 3]  # the capstone belongs to no KP

    text = "\n".join(report_lines(report))
    assert "target 2 of 4" in text and "Capstone (3 steps)" in text and "step 3 [true_false_set]" in text
    pools, capstone = stored_questions(sid)
    rendered = render_lesson(lesson, pools, capstone)
    assert "CAPSTONE" in rendered and "target_questions 1" in rendered and "Common mistakes" not in rendered


def test_legacy_rows_and_lessons_get_defaults(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S1'", (cid,))["id"]
    row = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND kind = 'numeric'", (sid,))
    legacy = {k: v for k, v in row.items() if k not in ("role", "steps_json")}  # a row from before the migration
    assert question_public(legacy).role == "practice" and question_public(legacy).steps == []
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
    for kp in lesson["knowledge_points"]:
        kp.pop("target_questions")
    lesson["common_mistakes"] = ["Old v1 section"]
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), sid))
    view = lesson_view(sid)
    assert [kp.target_questions for kp in view.knowledge_points] == [2, 2]
    assert view.common_mistakes == []  # a legacy v1 lesson keeps its data, but the section is never served (SPEC §6.3)
    assert db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])["common_mistakes"]


# ---------------------------------------------------------------------------
# Verification of multistep questions
# ---------------------------------------------------------------------------


def _capstone_skill(tmp_path) -> tuple[str, str]:
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    return cid, asyncio.run(replan(cid, key)).results[0].skill_id


def test_steps_are_verified_with_the_earlier_results_but_never_their_own_answer(tmp_path):
    _, sid = _capstone_skill(tmp_path)
    cap = capstone_row(sid)
    record = db.loads(cap["verify_json"])
    assert record["status"] == "agree" and [s["status"] for s in record["steps"]] == ["agree"] * 3 and not cap["flagged"]
    steps = db.loads(cap["steps_json"])
    text = verify.step_request(cap, steps, 2)
    assert cap["prompt"] in text and "Correct result of step 1: $5+5i$" in text and "Correct result of step 2: $1-i$" in text
    assert "CURRENT STEP 3 of 3 — solve only this step" in text and "1. The real part of $zw$ is $5$" in text
    assert "TFT" not in fake_llm._MARKER_RE.sub("", text)  # the current step's key is never shown (only the fake marker)
    first = fake_llm._MARKER_RE.sub("", verify.step_request(cap, steps, 0))
    assert "EARLIER STEPS" not in first and "5+5*i" not in first and "$5+5i$" not in first


def _scripted_solvers(monkeypatch, answers: dict[tuple[str, int], IndependentAnswer], calls: list):
    """(role, step number) → the solver's answer; other steps answer from the fake marker."""
    original = fake_llm.respond

    def respond(**kwargs):
        if kwargs["schema"] is IndependentAnswer and "CURRENT STEP" in kwargs["text"]:
            number = int(kwargs["text"].split("CURRENT STEP ")[1].split(" ")[0])
            calls.append((kwargs["role"], number, kwargs["instructions"]))
            scripted = answers.get((kwargs["role"], number))
            if scripted is not None:
                return scripted
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", respond)


def test_a_disagreeing_step_is_rechecked_by_the_stronger_model(tmp_path, monkeypatch):
    cid, sid = _capstone_skill(tmp_path)
    cap = capstone_row(sid)
    calls: list = []
    wrong = IndependentAnswer(choice=None, answer="5-5*i", confidence=0.9, problems="")
    _scripted_solvers(monkeypatch, {("verify", 1): wrong}, calls)
    verdict = asyncio.run(verify.verify_question(cap))
    assert not verdict.flagged and "re-check agreed" in verdict.note and "cap step 1" in verdict.note
    assert sorted((r, n) for r, n, _ in calls) == [("graph", 1), ("verify", 1), ("verify", 2), ("verify", 3)]
    assert {i for r, n, i in calls if r == "graph"} == {VERIFY_STEP_RECHECK}
    assert {i for r, n, i in calls if r == "verify"} == {VERIFY_STEP_INSTRUCTIONS}
    record = db.loads(capstone_row(sid)["verify_json"])
    assert record["status"] == "agree_on_recheck" and record["steps"][0]["status"] == "agree_on_recheck"
    assert record["steps"][0]["independent"]["answer"] == "5-5*i" and record["steps"][0]["recheck"]["answer"] == "5+5*i"
    counts = jobs._pipeline_counts(cid)  # counted in the build's verification stats
    assert counts["rechecked"] >= 1

    # still disagreeing after the re-check → the whole question is flagged, naming the step
    calls.clear()
    tf_wrong = IndependentAnswer(choice=None, answer="TTT", confidence=0.9, problems="")
    _scripted_solvers(monkeypatch, {("verify", 3): tf_wrong, ("graph", 3): tf_wrong}, calls)
    verdict = asyncio.run(verify.verify_question(capstone_row(sid)))
    assert verdict.flagged and verdict.reason.startswith("verification: step 3: independent solver answered 'TTT'")
    assert "re-check answered 'TTT'" in verdict.reason
    stored = capstone_row(sid)
    assert stored["flagged"] == 1 and db.loads(stored["verify_json"])["status"] == "disagree"
    # a flagged capstone stays flagged: the lesson then simply ends after the practice
    verify.keep_kps_servable(sid)
    assert capstone_row(sid)["flagged"] == 1


def test_a_failed_step_solve_leaves_the_question_unverified(tmp_path, monkeypatch):
    _, sid = _capstone_skill(tmp_path)
    cap = capstone_row(sid)
    db.execute("UPDATE questions SET verify_json = '{}' WHERE id = ?", (cap["id"],))
    original = fake_llm.respond

    def failing_step_two(**kwargs):
        if kwargs["schema"] is IndependentAnswer and "CURRENT STEP 2" in kwargs["text"]:
            raise RuntimeError("down")
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", failing_step_two)
    assert asyncio.run(verify.verify_question(capstone_row(sid))) is None
    assert capstone_row(sid)["verify_json"] == "{}"


def test_kps_stay_servable_up_to_their_target(tmp_path, monkeypatch):
    original = fake_llm.respond

    def contrarian(**kwargs):
        if kwargs["schema"] is IndependentAnswer:
            return IndependentAnswer(choice=9, answer="12345", confidence=0.9, problems="")
        return original(**kwargs)

    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    monkeypatch.setattr(fake_llm, "respond", contrarian)
    sid = asyncio.run(replan(cid, key)).results[0].skill_id
    rows = db.query("SELECT * FROM questions WHERE skill_id = ?", (sid,))
    servable = {kp: sum(1 for r in rows if r["kp_index"] == kp and not r["flagged"]) for kp in (0, 1)}
    assert servable == {0: 2, 1: 1}  # the targets, not a fixed 2
    assert capstone_row(sid)["flagged"] == 1  # never un-flagged to fill a quota


# ---------------------------------------------------------------------------
# Top-ups and prompts
# ---------------------------------------------------------------------------


def test_top_ups_never_add_written_questions(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S2'", (cid,))["id"]  # a proof skill
    original = fake_llm.respond
    texts: list[str] = []

    def with_written(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"].__name__ == "TopUpDraft":
            texts.append(kwargs["text"])
            written = result.questions[-1].model_copy(update={"kind": "free_response", "steps": [], "key": "k1q20",
                                                               "checklist": ["a", "b"], "prompt": "Prove it in writing."})
            result.questions.append(written)
        return result

    monkeypatch.setattr(fake_llm, "respond", with_written)
    added = asyncio.run(topup.top_up_questions(sid, kp_index=0, n=3))
    assert added == 3 and "multistep walkthrough" in texts[0] and "same demand" in texts[0]
    rows = db.query("SELECT kind, steps_json, verify_json FROM questions WHERE skill_id = ?", (sid,))
    assert not any(r["kind"] == "free_response" for r in rows)
    walkthroughs = [r for r in rows if r["kind"] == "multistep"]
    assert len(walkthroughs) >= 2 and all(db.loads(r["verify_json"])["status"] == "agree" for r in walkthroughs)


def test_prompts_carry_the_iteration_3_rules():
    from app.pipeline.prompts_v2 import (
        CAPSTONE_RULE,
        PLANNER_INSTRUCTIONS,
        PROOF_RULE,
        QUESTION_RULES,
        REVIEWER_INSTRUCTIONS,
        SERVED_SEQUENCE_RULE,
        V2_FEEDBACK,
        WRITER_INSTRUCTIONS,
    )
    from app.pipeline.prompts import VERIFY_INSTRUCTIONS

    for prompt in (LESSON_INSTRUCTIONS, TOPUP_INSTRUCTIONS, WRITER_INSTRUCTIONS):
        assert NO_WRITTEN_ANSWERS_RULE in prompt and DISTINCT_DEMANDS_RULE in prompt and MULTISTEP_FORMAT in prompt
    assert TARGET_RULE in LESSON_INSTRUCTIONS and TARGET_RULE in QUESTION_RULES
    assert CAPSTONE_RULE in WRITER_INSTRUCTIONS and PROOF_RULE in WRITER_INSTRUCTIONS
    for prompt in (PLANNER_INSTRUCTIONS, WRITER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS):
        assert V2_FEEDBACK in prompt
    writer, reviewer, planner = (" ".join(t.split()) for t in (WRITER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS, PLANNER_INSTRUCTIONS))
    # iteration-3 review: the capstone example no longer ends with a "multiply back" step whose answer is known
    assert "multiplying back" not in CAPSTONE_RULE.split("NOT:")[0] and "known in advance" in CAPSTONE_RULE
    assert "There is no \"common mistakes\" section" in writer and "common_mistakes:" not in writer
    assert "free_response:" not in writer and "free_response: prove" not in LESSON_INSTRUCTIONS
    assert "common_mistakes: [] (always empty" in LESSON_INSTRUCTIONS
    assert "drill without a new demand" in reviewer and "the capstone: missing, not multistep" in reviewer
    assert "a free_response question, or any question that asks the learner to show" in reviewer
    assert "written as the lesson's CAPSTONE" in planner and "plan fewer: one or two is fine" in planner
    # live run 1 (S3b served a quotient, the same quotient as an equation, then the capstone): the served sequence
    assert SERVED_SEQUENCE_RULE in QUESTION_RULES and "appears exactly once before the capstone, as one independent typed answer" in SERVED_SEQUENCE_RULE
    assert "the served sequence (the first target_questions questions of each knowledge point" in reviewer
    assert "the ONE straightforward independent, typed application of the lesson's main procedure that belongs in the served sequence" in reviewer
    # a deliberately false statement is not a defect (it flagged a correct true_false_set in the live run)
    assert "A statement or option that is false on purpose is not a defect" in " ".join(VERIFY_INSTRUCTIONS.split())
    assert VERIFY_INSTRUCTIONS in VERIFY_STEP_INSTRUCTIONS


def test_reviewer_sees_targets_steps_and_the_capstone():
    from app.pipeline.lesson_v2 import review_request

    text = review_request(split_plan().skills[0], draft(), key="S1", revised=False)
    assert "target_questions 2" in text and "target_questions 1" in text
    assert "CAPSTONE (the final multistep problem" in text and "cap [multistep, tier 3]" in text
    assert "step 2 [multiple_choice]" in text and "hint:" in text and "Common mistakes" not in text
    review = fake_llm.respond(schema=LessonReview, role="graph", instructions="", text="(clean)")
    assert review.issues == []


def test_build_verification_counts_multistep_questions(tmp_path, llm_calls):
    cid = tiny_course(tmp_path)
    job = build(cid)
    rows = db.query("SELECT q.* FROM questions q JOIN skills s ON s.id = q.skill_id WHERE s.course_id = ?", (cid,))
    multistep = [r for r in rows if r["kind"] == "multistep"]
    assert multistep and all(db.loads(r["verify_json"])["status"] == "agree" for r in multistep)
    assert llm_calls["IndependentAnswer"] == solver_calls(rows)
    message = next(s for s in job.stages if s.key == "verify").message
    assert message.startswith(f"{len(rows)}/{len(rows)} agree")
