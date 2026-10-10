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
    assert any("block 6: the scored check's answer" in p and "printed before the learner answers it" in p for p in hard)
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
    kps = [draft.knowledge_points[0].model_copy(update={"questions": [inflated, *draft.knowledge_points[0].questions[1:]]}),
           draft.knowledge_points[1]]
    # no scored step and no variant step about what the proof rests on (statement sets that are all true test nothing)
    blocks = list(draft.blocks)
    blocks[7] = blocks[7].model_copy(update={"check": blocks[7].check.model_copy(update={"answer": "TTT"})})
    cap_steps = list(draft.capstone.steps)
    cap_steps[2] = cap_steps[2].model_copy(update={"answer": "TTT"})
    cap = draft.capstone.model_copy(update={"steps": cap_steps})
    hard, _ = problems(draft.model_copy(update={"knowledge_points": kps, "blocks": blocks, "capstone": cap}))
    assert any("shifted by a constant" in p for p in hard)
    assert any("describes a method" in p for p in hard)
    assert any("tests what the proof rests on" in p for p in hard)


def test_the_variant_is_short_and_drops_the_scaffold():
    draft = lesson()
    cap = draft.capstone
    steps = list(cap.steps)
    steps[1] = steps[1].model_copy(update={"prompt": "Reset the slots and enter $(\\alpha\\beta)\\mu$."})
    hard, _ = problems(draft.model_copy(update={"capstone": cap.model_copy(update={"steps": [*steps, steps[0]]})}))
    assert any("reset the slots" in p for p in hard)
    assert any("the variant has 2–3" in p for p in hard)
    one_typed = [steps[0], steps[2]]
    hard, _ = problems(draft.model_copy(update={"capstone": cap.model_copy(update={"steps": one_typed})}))
    assert any("cap: 1 typed steps; at least 2" in p for p in hard)


def test_scored_steps_need_hints_and_may_not_be_readable_before_they_are_answered():
    """2026-10-09, the learner after S4a: the steps worked out in the proof are the score (rules.GUIDED_PROOF_RULE)."""
    draft = lesson()
    blocks = list(draft.blocks)
    assert not any("scored" in p for p in sum(problems(draft), []))
    no_hint = blocks[2].model_copy(update={"check": blocks[2].check.model_copy(update={"hint": ""})})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:2], no_hint, *blocks[3:]]}))
    assert any("block 3: the scored check has no hint" in p for p in hard)
    gives = blocks[3].model_copy(update={"check": blocks[3].check.model_copy(update={"hint": "It is $1+3i$."})})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:3], gives, *blocks[4:]]}))
    assert any("block 4: the hint gives the answer away" in p for p in hard)
    # an earlier check's explanation is on screen: it may not hold a later step's answer
    explained = blocks[2].model_copy(update={"check": blocks[2].check.model_copy(update={
        "explanation": blocks[2].check.explanation + " Route A starts with $\\alpha\\beta=1+3i$."})})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:2], explained, *blocks[3:]]}))
    assert any("block 4: the scored check's answer $1+3i$ is printed before" in p for p in hard)
    coin = blocks[7].model_copy(update={"check": blocks[7].check.model_copy(update={
        "kind": "true_false", "choices": ["True", "False"], "correct_choice": 0, "answer": ""})})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:7], coin, *blocks[8:]]}))
    assert any("block 8: a scored true_false check is a coin flip" in p for p in hard)
    unscored = [b.model_copy(update={"check": None}) if n in (3, 8) else b for n, b in enumerate(blocks)]
    hard, soft = problems(draft.model_copy(update={"blocks": unscored}))
    assert any("the teaching has 3 scored steps" in p for p in hard)
    assert any("block 9: the general case has no scored '?' entry" in p for p in soft)


def test_the_variant_is_its_own_statement():
    plan = proof_plan()
    assert plan_problems(plan, 2) == []
    replay = plan.model_copy(update={"capstone_statement": plan.practice_statements[0].statement})
    assert any("repeats the teaching's statement" in p for p in plan_problems(replay, 2))
    assert any("variant" in p for p in plan_problems(plan.model_copy(update={"capstone_statement": ""}), 2))


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


def test_the_learners_scoring_request_reaches_every_proof_prompt_of_both_providers():
    """rules.GUIDED_PROOF_RULE (2026-10-09) is written once and shared; the general texts keep formative checks."""
    from app.prompts import anthropic, openai
    from app.prompts.rules import GUIDED_PROOF_RULE

    for texts in (openai, anthropic):
        for name in ("PROOF_PLANNER_INSTRUCTIONS", "PROOF_WRITER_INSTRUCTIONS"):
            assert GUIDED_PROOF_RULE in getattr(texts, name), name
        formative = "formative — graded by the app, never scored"
        assert formative not in texts.PROOF_WRITER_INSTRUCTIONS and formative in texts.WRITER_INSTRUCTIONS
        assert "scored step of the teaching or variant step" in texts.PROOF_REVIEWER_INSTRUCTIONS
        assert "THE VARIANT" in texts.PROOF_CAPSTONE_REPAIR_NOTE and "earlier skill computes" not in texts.PROOF_CAPSTONE_REPAIR_NOTE
        assert GUIDED_PROOF_RULE not in texts.WRITER_INSTRUCTIONS


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
    # the checks are scored steps with their hints, every one verified; the variant fills the capstone slot
    from app.learning import guided

    assert stored["scoring"] == "guided" and guided.scored_indices(stored) == [2, 3, 5, 7, 8]
    assert all(stored["blocks"][i]["check"]["hint"] for i in guided.scored_indices(stored))
    assert [r["status"] for r in stored["guided_verify"]] == ["agree"] * 5, stored["guided_verify"]
    cap = db.query_one("SELECT steps_json FROM questions WHERE skill_id = ? AND role = 'capstone'", (result.skill_id,))
    assert len(db.loads(cap["steps_json"])) == 3


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
    assert any("practice questions lead the knowledge points" in p for p in soft)


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


def test_forced_and_tuple_answers_of_scored_steps_are_found():
    """The branch review (2026-10-09): the 1 of a checked inverse, and a slot tuple printed in an earlier block."""
    draft = lesson()
    blocks = list(draft.blocks)
    forced = blocks[3].model_copy(update={
        "body": blocks[3].body + "\n\nRecall $\\frac{2}{5}-\\frac{1}{5}i$ and $2+i$.",
        "check": blocks[3].check.model_copy(update={"answer": "1", "answer_display": "$1$"})})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:3], forced, *blocks[4:]]}))
    assert any("block 4: the scored check's answer $1$ is forced" in p for p in hard), hard
    tup = blocks[8].model_copy(update={"check": blocks[8].check.model_copy(update={
        "answer": "(a, b, c*g-d*h, c*h+d*g)", "answer_display": "$(a,b,cg-dh,ch+dg)$"})})
    printed = blocks[6].model_copy(update={"body": blocks[6].body + "\n\nThe outer slots will be $(a, b, cg-dh, ch+dg)$."})
    hard, _ = problems(draft.model_copy(update={"blocks": [*blocks[:6], printed, blocks[7], tup]}))
    assert any("block 9: the scored check's tuple" in p for p in hard), hard


def test_the_proof_prompts_drop_the_old_capstone_and_keep_the_general_block_limits():
    from app.prompts import anthropic, openai

    for texts in (openai, anthropic):
        writer = texts.PROOF_WRITER_INSTRUCTIONS
        assert "It CHAINS this lesson's idea" not in writer and "10 blocks (THE PROOF TRACK" not in writer
        assert "earlier skill does real work" not in texts.PROOF_CAPSTONE_REPAIR_NOTE
        # 9 blocks (10 with a planned miss) since the reflect check went (2026-10-10)
        assert "At most 9 blocks" in writer and texts.PROOF_REVISION_NOTE == texts.REVISION_NOTE
        assert "more than 10 blocks" in texts.PROOF_REVIEWER_INSTRUCTIONS
        assert "no earlier skill doing real work" not in texts.PROOF_REVIEWER_INSTRUCTIONS
        assert "It CHAINS this lesson's idea" in texts.WRITER_INSTRUCTIONS  # the general texts keep theirs


def test_no_prompt_asks_for_the_point_that_one_example_proves_nothing():
    """The learner, 2026-10-10 (rules.EXAMPLE_FEEDBACK, rule 17): "one specific example does not make a law" is known
    and redundant — no reflect on it, no claim to reject, no bridge; a concrete case exemplifies the general rule."""
    from app.prompts import anthropic, openai
    from app.prompts.rules import EXAMPLE_FEEDBACK, EXAMPLE_POINT_RULE, GUIDED_PROOF_RULE, REFERENCE_PROOF

    roles = ("PLANNER_INSTRUCTIONS", "WRITER_INSTRUCTIONS", "REVIEWER_INSTRUCTIONS", "PROOF_PLANNER_INSTRUCTIONS",
             "PROOF_WRITER_INSTRUCTIONS", "PROOF_REVIEWER_INSTRUCTIONS")
    for texts in (openai, anthropic):
        assert all(EXAMPLE_FEEDBACK in getattr(texts, name) for name in roles)
        assert EXAMPLE_POINT_RULE in texts.TOPUP_INSTRUCTIONS and EXAMPLE_POINT_RULE in texts.LESSON_INSTRUCTIONS
        track = texts.PROOF_PLANNER_INSTRUCTIONS + texts.PROOF_WRITER_INSTRUCTIONS + texts.PROOF_REVIEWER_INSTRUCTIONS
        track = track.replace(EXAMPLE_FEEDBACK, "")  # (the rule quotes the old wordings)
        for gone in ("why agreement on numbers is not a proof", "one example is not a proof", "What a proof establishes",
                     "an\n   example or a special case taken as a proof", "a single example, a wrong law"):
            assert gone not in track + texts.LESSON_INSTRUCTIONS, gone
        assert "9 blocks, about 330 words of prose, 5 scored steps (S), no reflect check" in track
        assert "Outside a proof skill, include one explanation question" in texts.PROOF_WRITER_INSTRUCTIONS
        assert "a proof lesson has no explanation ('reflect') check" in texts.PROOF_REVIEWER_INSTRUCTIONS
    assert "reflect" not in REFERENCE_PROOF and "One 'reflect' check" not in GUIDED_PROOF_RULE


def test_a_draft_that_makes_the_example_point_goes_to_the_revision():
    """The wordings the stored lessons used (S4a, S10, S13) are found anywhere the learner sees them; the S8a/S8b
    "why" reflects, a stage claim of find_then_check and a one-coordinate claim are other points."""
    from app.pipeline.lesson_v2 import example_point_problems, quality_problems

    draft = lesson()
    assert example_point_problems(draft) == []
    made = [
        "Both routes reached $-3+i$. Why does this not yet prove $(\\alpha\\beta)\\lambda=\\alpha(\\beta\\lambda)$?",
        "Why is knowing $h(\\frac12)=3$ not enough to specify $h$ on $[0,1]$?",
        "We have proved, by the check with $x=(1,-3)$ and $y=(2,1)$ alone, that $2(x+y)=2x+2y$ for every $x,y$.",
        "One computation covers one case only. A proof must cover every allowed input.",
        "A numerical check covers one case only, and a fixed length proves only that length.",
        "Computing $(2+i)(1-3i)$ gives $1-5i$; this alone proves commutativity for every complex pair.",
    ]
    other = [
        "Why is a real residual not enough to conclude that the corresponding unknown coordinate is real?",
        "Why does the coordinate equation $\\lambda\\cdot0=0$ leave $\\lambda$ unrestricted?",
        "We have proved, by solving the two real equations alone, that the candidate does satisfy $(a+i)\\beta=1$.",
        "Comparing coordinate 1 alone proves the law.",
        "One counterexample refutes an \"always\" claim.",
        "Why is $6i^2$ not yet in standard form?",
    ]
    for text in made + other:
        blocks = list(draft.blocks)
        blocks[4] = blocks[4].model_copy(update={"body": blocks[4].body + "\n\n" + text})
        found = example_point_problems(draft.model_copy(update={"blocks": blocks}))
        assert bool(found) == (text in made), (text, found)
        if found:
            assert found[0].startswith("block 5:")
    kps = list(draft.knowledge_points)
    q = kps[1].questions[0]
    kps[1] = kps[1].model_copy(update={"questions": [q.model_copy(update={"choices": [*q.choices[:4], made[2]]}),
                                                     *kps[1].questions[1:]]})
    problems = quality_problems(draft.model_copy(update={"knowledge_points": kps}), proof=True)
    assert any(p.startswith("k2q1:") and "rule 17" in p for p in problems), problems


def test_a_proof_lesson_revises_once_and_its_final_review_runs_lighter(tmp_path, monkeypatch):
    """2026-10-09 (time per lesson): one revision round for a proof lesson, its final review at the base effort,
    and the teaching blocks the final review still blocks go to a block repair."""
    from app.llm_schemas import BlockRepair, BridgeIssue, LessonReview
    from app.pipeline import lesson_v2

    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Justify commutativity")["key"]
    from app import llm

    calls: list[tuple[str, int, str]] = []
    original = fake_llm.respond
    structured = llm.structured

    async def spy(**kwargs):  # the effort each call asks for (the fake LLM itself ignores it)
        calls.append((kwargs["schema"].__name__, kwargs.get("effort_notches", 0), kwargs.get("label", "")))
        return await structured(**kwargs)

    monkeypatch.setattr(llm, "structured", spy)

    def blocking_review(**kwargs):
        name = kwargs["schema"].__name__
        out = original(**kwargs)
        if name == "LessonReview":  # every review blocks block 3: one revision, then a block repair
            issue = BridgeIssue(location="Block 3 — Finish route B", inference_required="x", why_insufficient="y",
                                repair="z", severity="blocking")
            out = out.model_copy(update={"issues": [issue]})
        return out

    monkeypatch.setattr(fake_llm, "respond", blocking_review)
    report = asyncio.run(replan(cid, key, no_split=True))
    assert report.results[0].ok, report.results[0].error
    reviews = [(n, e) for n, e, label in calls if n == "LessonReview"]
    assert len(reviews) == 2 and reviews[0][1] == 1 and reviews[1][1] == 0  # first at the proof notch, final without
    assert sum(1 for n, *_ in calls if n == "LessonV2Draft") == 2  # the draft and ONE revision
    assert sum(1 for n, *_ in calls if n == "BlockRepair") == 1
    assert lesson_v2.blocked_blocks(LessonReview.model_validate({
        "issues": [{"location": "Blocks 9–10 — the general case", "inference_required": "", "why_insufficient": "",
                                     "repair": "", "severity": "blocking"},
                                    {"location": "k1q2", "inference_required": "", "why_insufficient": "", "repair": "",
                                     "severity": "blocking"}],
        "density_issues": [], "untaught_method_exercises": [], "math_errors": [], "out_of_scope_violations": [],
        "optional_content_needed": [], "split_recommended": False, "split_reason": "",
    })).keys() == {9, 10}
    assert BlockRepair(blocks=[]).blocks == []


def test_a_variant_explanation_may_not_name_the_law_a_later_step_asks_for():
    """The guided S10 (2026-10-09): step 2's explanation gave "(a+b)c = c(a+b) = ac+bc by commutativity and
    distributivity" before step 3 asked about it — the variant's one real move was shown, not asked."""
    draft = lesson()
    steps = list(draft.capstone.steps)
    steps[1] = steps[1].model_copy(update={"explanation": steps[1].explanation + " The coordinates agree by commutativity."})
    hard, _ = problems(draft.model_copy(update={"capstone": draft.capstone.model_copy(update={"steps": steps})}))
    assert any("cap step 2: its explanation names the law before step 3" in p for p in hard)
