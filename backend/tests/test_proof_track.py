"""The proof track (2026-10-08, the learner on S4a: "quite convoluted"): the proof plan, the proof prompts, the
mechanical checks of a proof lesson, the grader's standard-form rule for keys with letters, and a proof skill
re-planned end to end (offline, fake LLM)."""

from __future__ import annotations

import asyncio

from test_pipeline_build import llm_calls, tiny_course  # noqa: F401  (fixture)
from test_pipeline_v2 import built_course, skill_by_title

from app import db, fake_llm
from app.fake_proof import proof_lesson, proof_plan
from app.learning.grading import UNCOMPUTED_FEEDBACK, UNCOMPUTED_REAL_FEEDBACK, check_answer
from app.llm_schemas import LessonV2Draft, ScopeStatement, TeachingPlan
from app.pipeline.lesson_v2 import InvalidPlan, PlanGraph, validate_plan, validate_v2
from app.pipeline.proof_track import plan_problems, proof_problems
from app.prompts.openai import (
    PLANNER_INSTRUCTIONS,
    PROOF_PLANNER_INSTRUCTIONS,
    PROOF_QUESTION_REPAIR_NOTE,
    PROOF_REVIEWER_INSTRUCTIONS,
    PROOF_WRITER_INSTRUCTIONS,
    WRITER_INSTRUCTIONS,
)
from app.pipeline.questions import answer_known_problems
from app.pipeline.replan import replan


def lesson() -> LessonV2Draft:
    return proof_lesson("Prove complex arithmetic laws by comparing coordinates", "Prove …", "S4a")


def problems(draft: LessonV2Draft) -> tuple[list[str], list[str]]:
    return proof_problems(draft, proof_plan())


def grade(answer: str, response: str):
    return check_answer("expression", answer=answer, correct_choice=None, choice=None, tolerance=None, response=response)


# ---------------------------------------------------------------------------
# The reference lesson and the plan
# ---------------------------------------------------------------------------


def test_the_reference_proof_lesson_passes_every_check():
    draft = lesson()
    assert problems(draft) == ([], [])
    assert plan_problems(proof_plan(), 2) == []
    spec = fake_llm._spec("a", draft.title, draft.objective, "proof", [], [0]).model_copy(update={"proof": proof_plan()})
    asyncio.run(validate_v2(draft, spec, [], proof=True, strict=True))  # the app's own validation too


def test_a_proof_plan_with_grader_letters_or_a_one_sided_scope_is_refused():
    plan = proof_plan().model_copy(update={"coordinate_letters": ["a", "b", "e", "x"]})
    found = " ".join(plan_problems(plan, 2))
    assert "'e'" in found and "'x'" in found  # e is Euler's number; x is a slot name in two_routes
    one_sided = proof_plan().model_copy(
        update={"scope_statements": [ScopeStatement(statement=f"claim {n}", true=True) for n in range(3)]}
    )
    assert any("both true and false" in p for p in plan_problems(one_sided, 2))
    assert any("does not exist" in p for p in plan_problems(proof_plan(), 1))  # a statement planned for point 2 of 1
    foreign = proof_plan().model_copy(update={"guided_key": "-q*d+(a*c)*i"})
    assert any("not in coordinate_letters" in p for p in plan_problems(foreign, 2))


def test_a_proof_skill_must_come_with_a_proof_plan():
    graph = PlanGraph(key="S2", keys={"S1", "S2"}, prereqs={"S1": 0.25}, dependents={}, descendants=set(), pages={0, 1})
    spec = fake_llm._spec("a", "Prove the laws", "Prove that αβ = βα for all α, β.", "proof", [("S1", 0.25)], [0])
    plan = TeachingPlan(split_test="No", decision="keep", rationale="", skills=[spec], dependents=[])
    try:
        validate_plan(plan, graph)
    except InvalidPlan as e:
        assert "proof plan" in str(e)
    else:
        raise AssertionError("a proof skill without a proof plan was accepted")
    with_plan = plan.model_copy(update={"skills": [spec.model_copy(update={"proof": proof_plan(), "practice_steps": ["T", "W"]})]})
    validated, _ = validate_plan(with_plan, graph)
    assert validated.skills[0].proof.strategy_name == "Two routes, same destination"


# ---------------------------------------------------------------------------
# The checks find the first S4a's defects
# ---------------------------------------------------------------------------


def test_new_letters_and_long_expansions_are_found():
    draft = lesson()
    walkthrough = draft.knowledge_points[0].questions[0]
    steps = list(walkthrough.steps)
    steps[0] = steps[0].model_copy(update={"answer": "q+r-s-t+(q+r+s+t)*i"})  # a new letter set (the first S4a)
    steps[1] = steps[1].model_copy(update={"answer": "a*c-b*d-a*d-b*c+a*g+(a*c+b*d+a*d+b*c-a*g)*i"})  # 5 terms
    kps = [draft.knowledge_points[0].model_copy(update={"questions": [walkthrough.model_copy(update={"steps": steps}), *draft.knowledge_points[0].questions[1:]]}), draft.knowledge_points[1]]
    hard, soft = problems(draft.model_copy(update={"knowledge_points": kps}))
    assert any("k1q1 step 1" in p and "not the lesson's letters" in p for p in hard)
    assert any("k1q1 step 2" in p and "5 terms" in p for p in soft)


def test_the_learners_part_must_be_a_route_leg_not_printed_and_after_numbers():
    draft = lesson()
    blocks = list(draft.blocks)
    faded = blocks[5]
    regroup = faded.model_copy(update={"check": faded.check.model_copy(update={"answer": "-3+i"})})  # a number, not route B
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:5], regroup, *blocks[6:]]}))
    assert any("fewer than 2 letters" in p for p in hard)
    printed = blocks[4].model_copy(update={"body": blocks[4].body + "\n\nRoute B will give $(-ad-bc)+(ac-bd)i$."})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:4], printed, *blocks[5:]]}))
    assert any("printed before the learner computes it" in p for p in hard)
    letters_first = blocks[2].model_copy(update={"check": blocks[2].check.model_copy(update={"answer": "-a*d-b*c+(a*c-b*d)*i"})})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:2], letters_first, *blocks[3:]]}))
    assert any("numbers first" in p for p in hard)


def test_inflated_statements_method_options_and_missing_scope_are_found():
    draft = lesson()
    walkthrough = draft.knowledge_points[0].questions[0]
    inflated = walkthrough.model_copy(update={"prompt": walkthrough.prompt.replace("(\\alpha+\\beta)", "(\\alpha+8)(\\beta+11i)", 1)})
    steps = list(walkthrough.steps)
    steps[2] = steps[2].model_copy(update={"choices": [
        "Compute route A, then compare it with route B coordinate by coordinate",
        "Real sums can be regrouped and reordered", "Real products can be reordered", "Adding $0$ changes nothing"]})
    inflated = inflated.model_copy(update={"steps": steps})
    scope = draft.knowledge_points[1]
    no_scope = scope.model_copy(update={"questions": [scope.questions[2], *scope.questions[:2]]})  # the decide question served
    kps = [draft.knowledge_points[0].model_copy(update={"questions": [inflated, *draft.knowledge_points[0].questions[1:]]}), no_scope]
    hard, _ = problems(draft.model_copy(update={"knowledge_points": kps}))
    assert any("shifted by a constant" in p for p in hard)
    assert any("describes a method" in p for p in hard)
    assert any("tests what a proof establishes" in p for p in hard)


def test_the_capstone_uses_the_statement_and_drops_the_scaffold():
    draft = lesson()
    cap = draft.capstone
    steps = list(cap.steps)
    steps[1] = steps[1].model_copy(update={"prompt": "Reset the slots and enter $(\\alpha\\beta)\\mu$."})
    steps[3] = steps[3].model_copy(update={"answer": "a+b*i"})
    hard, _ = problems(draft.model_copy(update={"capstone": cap.model_copy(update={"steps": steps})}))
    assert any("reset the slots" in p for p in hard)
    assert any("USES the proved statement" in p for p in hard)


def test_the_warning_appears_once():
    draft = lesson()
    blocks = list(draft.blocks)
    nagging = [b.model_copy(update={"body": b.body + " (without assuming the law)"}) if n in (3, 6) else b for n, b in enumerate(blocks)]
    _, soft = problems(draft.model_copy(update={"blocks": nagging}))
    assert any("circularity warning" in p for p in soft)


# ---------------------------------------------------------------------------
# The grader's standard-form rule (all lessons) and the repair messages
# ---------------------------------------------------------------------------


def test_a_retyped_product_is_not_a_computed_answer():
    key = "-a*d-b*c+(a*c-b*d)*i"  # α(βi) for α = a+bi, β = c+di
    for retyped in ("(a+b*i)*(-d+c*i)", "(a+bi)(-d+ci)", "i(a+bi)(c+di)"):
        result = grade(key, retyped)
        assert not result.correct and result.invalid and (result.feedback == UNCOMPUTED_FEEDBACK or "Not equivalent" in result.feedback)
    assert grade(key, "(a+b*i)*(-d+c*i)").feedback == UNCOMPUTED_FEEDBACK
    for computed in ("a(-d)-bc+(ac+b(-d))i", "-(ad+bc)+(ac-bd)i", "-ad-bc+i(ac-bd)"):
        assert grade(key, computed).correct, computed  # the coordinates may stay unexpanded
    real = grade("a*c-b*d", "(a+bi)(c+di)-(ad+bc)i")
    assert not real.correct and real.invalid and real.feedback == UNCOMPUTED_REAL_FEEDBACK
    assert grade("(3*a-b, a+3*b, c, d)", "(3a-b, a+3b, c, d)").correct  # a slot tuple
    assert grade("3+i", "(2-i)(1+i)").invalid  # constant keys: the existing reduced-form rule


def test_proof_repairs_keep_the_lessons_letters():
    draft = lesson()
    q = draft.knowledge_points[0].questions[0]
    steps = list(q.steps)
    steps[1] = steps[1].model_copy(update={"answer": steps[0].answer, "answer_display": steps[0].answer_display})  # forced
    forced = q.model_copy(update={"steps": steps})
    general = " ".join(answer_known_problems(forced, []))
    proof = " ".join(answer_known_problems(forced, [], proof=True))
    assert "choice step justify" in general and "slots of the second route's last product" in proof
    assert "other letters" not in proof


# ---------------------------------------------------------------------------
# Prompts and the pipeline
# ---------------------------------------------------------------------------


def test_proof_prompts_replace_the_general_proof_rules():
    assert "THE PROOF TRACK" in PROOF_WRITER_INSTRUCTIONS and "THE PROOF TRACK (this lesson" not in WRITER_INSTRUCTIONS
    assert "The teaching proves 1–2 representative statements" not in PROOF_WRITER_INSTRUCTIONS
    assert "Which definition do we apply first?" not in PROOF_WRITER_INSTRUCTIONS
    assert "1–2 representative statements" in PLANNER_INSTRUCTIONS and "1–2 representative" not in PROOF_PLANNER_INSTRUCTIONS
    assert "THE PROOF PLAN" in PROOF_PLANNER_INSTRUCTIONS and "PROOF PASS" in PROOF_REVIEWER_INSTRUCTIONS
    assert "fresh numbers" not in PROOF_QUESTION_REPAIR_NOTE and "the lesson's letters" in PROOF_QUESTION_REPAIR_NOTE
    for name in ("Two routes, same destination", "Find it, then check it", "Suppose two, show they are one",
                 "One law per line", "Two halves, one at a time", "Check the conditions, or break one"):
        assert name in PROOF_PLANNER_INSTRUCTIONS and name in PROOF_WRITER_INSTRUCTIONS


def test_a_proof_skill_is_replanned_on_the_proof_track(tmp_path, monkeypatch):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Justify commutativity")["key"]
    seen: dict[str, list[str]] = {}
    original = fake_llm.respond

    def capture(**kwargs):
        seen.setdefault(kwargs["schema"].__name__, []).append(kwargs["instructions"] + "\n" + kwargs["text"])
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", capture)
    report = asyncio.run(replan(cid, key, no_split=True))
    result = report.results[0]
    assert result.ok, result.error
    assert all("THE PROOF PLAN" in t for t in seen["TeachingPlan"])
    assert all("THE PROOF TRACK (this lesson" in t and "PROOF PLAN (binding" in t for t in seen["LessonV2Draft"])
    assert all("PROOF PASS" in t for t in seen["LessonReview"])
    assert not [n for n in result.notes if "proof track" in n], result.notes
    stored = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (result.skill_id,))["lesson_json"])
    assert stored["plan"]["proof"]["pattern"] == "two_routes"
    assert stored["blocks"][0]["heading"] == "Two routes, same destination"


# ---------------------------------------------------------------------------
# What the judges found in the first live proof-track S4a (2026-10-08)
# ---------------------------------------------------------------------------


def test_the_faded_row_leaves_route_bs_inputs_to_the_learner():
    draft = lesson()
    blocks = list(draft.blocks)
    filled = blocks[5].model_copy(update={"body": blocks[5].body.replace("| $a$ | $b$ | ? | ? | ? | ? |", "| $a$ | $b$ | $-d$ | $c$ | ? | ? |")})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:5], filled, *blocks[6:]]}))
    assert any("faded slot row prints the inputs" in p for p in hard)


def test_transfer_is_served_first_and_one_question_per_point():
    draft = lesson()
    kp1, kp2 = draft.knowledge_points
    repair_first = kp1.model_copy(update={"questions": [kp1.questions[2], *kp1.questions[:2]]})  # the slot-error spare first
    longer = kp2.model_copy(update={"target_questions": 3})
    hard, soft = problems(draft.model_copy(update={"knowledge_points": [repair_first, longer]}))
    assert any("knowledge point 1 leads with k1q3" in p for p in hard)
    assert any("practice questions are served before the capstone" in p for p in soft)


def test_explanations_do_not_answer_the_next_step_and_the_capstone_keeps_the_letters():
    draft = lesson()
    walkthrough = draft.knowledge_points[0].questions[0]
    steps = list(walkthrough.steps)
    steps[1] = steps[1].model_copy(update={"explanation": "| Coordinate | Route A | Route B |\n|---|---|---|\n| Real | $x$ | $x$ |"})
    kp1 = draft.knowledge_points[0].model_copy(update={"questions": [walkthrough.model_copy(update={"steps": steps}), *draft.knowledge_points[0].questions[1:]]})
    cap = draft.capstone.model_copy(update={"prompt": draft.capstone.prompt.replace("\\mu", "\\lambda")})
    _, soft = problems(draft.model_copy(update={"knowledge_points": [kp1, draft.knowledge_points[1]], "capstone": cap}))
    assert any("k1q1 step 2: its explanation prints the route comparison" in p for p in soft)
    assert any("\\lambda names a computed number" in p for p in soft)
    assert not any("\\alpha names" in p or "\\beta names" in p for p in soft)  # αβ = 2−5i assigns nothing


def test_a_conditional_step_and_printed_arithmetic_are_found():
    """The second live S4a: a capstone step 'if this justification holds enter the slots, otherwise enter {}', and a
    numbers check whose table printed 1·(−1)−1·2 in the result cells."""
    draft = lesson()
    steps = list(draft.capstone.steps)
    steps[2] = steps[2].model_copy(update={"prompt": "If the comparison holds, enter the four slots; otherwise enter {} instead."})
    blocks = list(draft.blocks)
    printed = blocks[2].model_copy(update={"body": blocks[2].body + "\n\n| product | $x$ | $y$ | $u$ | $v$ | real | imaginary |\n|---|---|---|---|---|---|---|\n| $\\alpha(\\beta\\lambda)$ | $1$ | $1$ | $-1$ | $2$ | $1(-1)-1\\cdot2$ | $1\\cdot2+1(-1)$ |"})
    hard, _ = problems(draft.model_copy(update={"capstone": draft.capstone.model_copy(update={"steps": steps}),
                                                 "blocks": [*blocks[:2], printed, *blocks[3:]]}))
    assert any("cap step 3: its answer depends on a condition" in p for p in hard)
    assert any("block 3: the slot table of the first graded check prints the arithmetic" in p for p in hard)


def test_proof_problems_are_weighed_not_fatal_in_a_revision():
    from app.pipeline.lesson_v2 import proof_debt

    spec = fake_llm._spec("a", "Prove the laws", "Prove that αβ = βα for all α, β.", "proof", [], [0]).model_copy(update={"proof": proof_plan()})
    draft = lesson()
    assert proof_debt(draft, spec) == 0
    cap = draft.capstone.model_copy(update={"prompt": draft.capstone.prompt + "\n\n| product | $x$ |\n|---|---|\n| $\\alpha\\mu$ | $a$ |"})
    assert proof_debt(draft.model_copy(update={"capstone": cap}), spec) == 1
    assert proof_debt(draft, spec.model_copy(update={"proof": None})) == 0


def test_a_letter_the_plan_reserves_for_the_capstones_number_is_not_an_input():
    """Opus's S4a plan (2026-10-08) listed 'μ: capstone only, a fixed number computed by an earlier skill'."""
    plan = proof_plan().model_copy(update={"letters": [*proof_plan().letters, "μ: capstone only, a fixed number ($\\mu=(2-i)(1+i)=3+i$)"]})
    _, soft = proof_problems(lesson(), plan)
    assert not any("names a computed number" in p for p in soft)
    _, soft = proof_problems(lesson().model_copy(update={"capstone": lesson().capstone.model_copy(update={
        "prompt": lesson().capstone.prompt.replace("\\mu", "\\lambda")})}), plan)
    assert any("\\lambda names a computed number" in p for p in soft)
