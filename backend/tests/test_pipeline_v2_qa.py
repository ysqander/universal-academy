"""Regression tests for the lesson-v2 QA round (fake LLM, offline): sibling ids in "Builds on" chips, Lesson.version,
faded examples with machine-checked checks, the content rules of the v2 prompts, the "in any order" tuple-to-set
rewrite, and the careful re-check of true_false_set verification disagreements."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from test_pipeline_build import build, llm_calls, tiny_course  # noqa: F401  (fixture)
from test_pipeline_v2 import built_course, graph, skill_by_title, split_plan

from app import db, fake_llm
from app.learning.grading import check_answer
from app.llm_schemas import IndependentAnswer, InlineCheck, LessonV2Draft, QuestionDraft
from app.pipeline import verify
from app.pipeline.lesson_v2 import clean_inline_check, validate_plan, validate_v2
from app.pipeline.lessons import InvalidLesson
from app.pipeline.prompts import VERIFY_STATEMENTS_RECHECK
from app.pipeline.questions import answers_in_any_order, clean_question
from app.pipeline.regenerate import regenerate
from app.pipeline.replan import replan
from app.pipeline.textutil import normalize_sibling_refs, sibling_keys
from app.views import lesson_version, lesson_view

# ---------------------------------------------------------------------------
# "Builds on" chips: the planner's sibling local ids become the final keys
# ---------------------------------------------------------------------------


def test_sibling_refs_are_rewritten_to_keys():
    sib = sibling_keys("S2d", ["S1", "S2a", "S2b", "S2c", "S2d", "S3", "S20a"])
    assert sib == {"a": "S2a", "b": "S2b", "c": "S2c", "d": "S2d"}
    assert sibling_keys("S2", ["S2", "S2a"]) == {} and sibling_keys("A1", ["A1"]) == {}
    assert normalize_sibling_refs(
        [
            "a (S2a): multiply two complex numbers in the form $a+bi$.",
            "b (S2b): square a complex number to obtain $(a^2-b^2)+2abi$.",
            "S2c (c): check a cube root",
            "skill d — find square roots",
            "A1: real-number arithmetic",
            "Expanding $(x+y)(z+w)$",
            "uses b (S2b) and A1",
            "a: multiply two complex numbers in the form $a+bi$.",  # the same chip as the first one: dropped
        ],
        sib,
    ) == [
        "S2a: multiply two complex numbers in the form $a+bi$.",
        "S2b: square a complex number to obtain $(a^2-b^2)+2abi$.",
        "S2c: check a cube root",
        "S2d: find square roots",
        "A1: real-number arithmetic",
        "Expanding $(x+y)(z+w)$",
        "uses S2b and A1",
    ]
    # without a map only the forms that carry a key change; ordinary maths text never does
    assert normalize_sibling_refs(["b (S2b): square", "b: square", "a+bi form", "(a) first"]) == [
        "S2b: square", "b: square", "a+bi form", "(a) first",
    ]


def test_plan_validation_rewrites_sibling_local_ids_in_assumed_prerequisites():
    validated, _ = validate_plan(split_plan(), graph())
    a, b, c = validated.skills
    assert b.assumed_prerequisites[0] == "S2a: multiply two complex numbers in the form $a+bi$."
    assert c.assumed_prerequisites[0] == "S2b: compute positive-integer powers of complex numbers."
    assert not any("(S2" in x for s in validated.skills for x in s.assumed_prerequisites)
    # renamed local ids (x, y, z → a, b, c) and a taken key (S2a exists: the split gets S2b, S2c, S2d)
    plan = split_plan()
    skills = [s.model_copy(update={"local_id": lid}) for s, lid in zip(plan.skills, "xyz")]
    skills[1] = skills[1].model_copy(update={"assumed_prerequisites": ["x (S2a): multiply", "Expand brackets"]})
    skills[2] = skills[2].model_copy(update={"assumed_prerequisites": ["y: powers", "(x) multiply"]})
    validated, _ = validate_plan(plan.model_copy(update={"skills": skills}), graph(keys={"S1", "S2", "S2a", "S3", "S4", "S5", "S9"}))
    assert validated.skills[1].assumed_prerequisites == ["S2b: multiply", "Expand brackets"]
    assert validated.skills[2].assumed_prerequisites == ["S2c: powers", "S2b: multiply"]
    kept, _ = validate_plan(split_plan().model_copy(update={"decision": "keep"}), graph())
    assert kept.skills[0].assumed_prerequisites[0] == "Expand $(x+y)(z+w)$ for real numbers"


def test_lesson_view_shows_keys_for_stored_sibling_ids_and_a_version(tmp_path, llm_calls):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Add and multiply")["key"]
    report = asyncio.run(replan(cid, key))
    a, b, c = (report.skill_ids[x] for x in "abc")
    # the split stored the validated plan: keys, never local ids
    stored = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (b,))["lesson_json"])
    assert stored["plan"]["assumed_prerequisites"][0] == f"{key}a: multiply two complex numbers in the form $a+bi$."
    assert lesson_view(b).assumed[0] == f"{key}a: multiply two complex numbers in the form $a+bi$."

    # a lesson stored before the fix (like the learner's S2b–S2d) is shown with keys without regeneration
    for sid, assumed in ((b, [f"a ({key}a): multiply two complex numbers in the form $a+bi$.", "A1: arithmetic"]),
                         (c, ["b: compute powers of complex numbers."])):
        lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
        lesson["plan"]["assumed_prerequisites"] = assumed
        db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), sid))
    assert lesson_view(b).assumed == [f"{key}a: multiply two complex numbers in the form $a+bi$.", "A1: arithmetic"]
    assert lesson_view(c).assumed == [f"{key}b: compute powers of complex numbers."]

    # Lesson.version: sha1 of the stored lesson_json — the value block feedback stores as lesson_hash
    from app.learning import blocks

    raw = db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (b,))["lesson_json"]
    view = lesson_view(b)
    assert view.version == lesson_version(raw) == hashlib.sha1(raw.encode("utf-8")).hexdigest() == blocks.lesson_hash(raw)
    assert lesson_view(a).version != view.version and lesson_version(None) == hashlib.sha1(b"").hexdigest()

    # regenerating such a lesson from its stored plan writes the keys (and the version changes)
    report = asyncio.run(regenerate(cid, [f"{key}b"], visuals=False, verify=False))
    assert report.lessons_regenerated == [f"{key}b"]
    stored = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (b,))["lesson_json"])
    assert stored["format"] == "v2" and stored["plan"]["assumed_prerequisites"][0].startswith(f"{key}a: multiply")
    assert lesson_view(b).version != view.version


def test_planner_sees_block_numbers_only_for_feedback_on_the_current_version(tmp_path):
    from app.pipeline.context import load_course
    from app.pipeline.lesson_v2 import plan_request

    cid = built_course(tmp_path)
    skill = skill_by_title(cid, "Distinguish lists")
    for index, note, version in ((2, "where did -15 come from?", "an-older-version"),
                                 (4, "why add the squares?", lesson_version(skill["lesson_json"]))):
        db.execute(
            "INSERT INTO block_feedback(id, skill_id, block_index, kind, note, lesson_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (db.new_id(), skill["id"], index, "unclear", note, version, db.now_iso()),
        )
    text = plan_request(load_course(cid), skill, allow_split=False)
    current = text.index("Learner feedback on the current lesson")
    earlier = text.index("Learner feedback on an earlier version of this lesson")
    assert "- block 5: why add the squares?" in text[current:earlier]
    assert "- where did -15 come from?" in text[earlier:] and "block 3" not in text


# ---------------------------------------------------------------------------
# Faded examples: the learner's missing step is graded (never a 'reflect' check)
# ---------------------------------------------------------------------------


def _draft() -> LessonV2Draft:
    return fake_llm.respond(schema=LessonV2Draft, role="lesson", instructions="", text="Write the lesson for skill S1: x")


def _reflect_faded(draft: LessonV2Draft) -> LessonV2Draft:
    broken = draft.model_copy(deep=True)
    faded = next(b for b in broken.blocks if b.kind == "faded_example")
    faded.check = InlineCheck(kind="reflect", prompt="Explain how the last step gives the answer.", choices=[],
                              correct_choice=None, answer="", answer_display="$1+7i$", explanation="$2i^2=-2$.")
    return broken


def test_faded_example_needs_a_machine_checked_check():
    spec = split_plan().skills[0]
    broken = _reflect_faded(_draft())
    index = next(i for i, b in enumerate(broken.blocks) if b.kind == "faded_example")
    with pytest.raises(InvalidLesson, match=f"block {index + 1}.*faded_example's check must be machine-checked.*not 'reflect'"):
        asyncio.run(validate_v2(broken, spec, []))
    lesson, _, _, notes = asyncio.run(validate_v2(broken, spec, [], strict=False))
    assert lesson["blocks"][index]["kind"] == "check" and lesson["blocks"][index]["check"]["kind"] == "reflect"
    assert any("faded_example with a 'reflect' check shown as a check block" in n for n in notes)
    assert "no faded example" in notes
    for kind, fields in (("numeric", dict(answer="1")), ("true_false_set", dict(choices=["a", "b"], answer="TF")),
                         ("multiple_choice", dict(choices=["a", "b"], correct_choice=0))):
        ok = broken.model_copy(deep=True)
        ok.blocks[index].check = InlineCheck(**({"kind": kind, "prompt": "Finish it.", "choices": [], "correct_choice": None,
                                                 "answer": "", "answer_display": "", "explanation": "e"} | fields))
        lesson, _, _, _ = asyncio.run(validate_v2(ok, spec, []))
        assert lesson["blocks"][index]["kind"] == "faded_example"


def test_a_reflect_faded_example_is_revised_with_the_validation_problem(tmp_path, monkeypatch):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    original = fake_llm.respond
    revision_texts = []

    def reflect_first_draft(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"] is LessonV2Draft:
            if "REVISION:" in kwargs["text"]:
                revision_texts.append(kwargs["text"])
            else:
                result = _reflect_faded(result)
        return result

    monkeypatch.setattr(fake_llm, "respond", reflect_first_draft)
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revised
    assert revision_texts and "faded_example's check must be machine-checked" in revision_texts[0]
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (result.skill_id,))["lesson_json"])
    faded = next(b for b in lesson["blocks"] if b["kind"] == "faded_example")
    assert faded["check"]["kind"] == "expression"


# ---------------------------------------------------------------------------
# The v2 prompts carry the content rules of this round
# ---------------------------------------------------------------------------


def test_v2_prompts_carry_the_content_rules():
    from app.pipeline.prompts_v2 import PLANNER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS, WRITER_INSTRUCTIONS

    planner, writer, reviewer = (" ".join(t.split()) for t in (PLANNER_INSTRUCTIONS, WRITER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS))
    # faded examples: graded, never reflect
    assert "is machine-checked" in writer and "never 'reflect'" in writer
    assert "A faded_example's check is never 'reflect'" in writer
    # motivate non-obvious moves; no skipped algebra lines
    assert "Motivate every non-obvious move BEFORE making it" in writer
    assert "never jump from an expanded form straight to a factored one" in writer
    assert "states its purpose in the same sentence" in planner
    assert "a non-routine move whose purpose is not stated" in reviewer
    # checking supplied answers: a computed candidate that matches in exactly one coordinate
    assert "matches the target in exactly one coordinate" in writer and "says explicitly why it fails" in writer
    assert "$2-i$ squares to" in writer and "exactly one coordinate" in planner
    assert "fail in every coordinate" in reviewer and "faded example whose check is a 'reflect'" in reviewer
    # siblings by key in assumed_prerequisites
    assert "never by its local id" in planner


# ---------------------------------------------------------------------------
# "in any order": only the order of the ANSWERS turns a tuple key into a set
# ---------------------------------------------------------------------------

UNORDERED_PROMPTS = [
    "Find both square roots of $2i$. Enter them in any order.",
    "Enter both roots separated by a comma, in any order.",
    "Give the two solutions in either order.",
    "Find all cube roots of $8$. The order of the roots does not matter.",
    "Enter both roots, separated by a comma (order does not matter).",
    "Solve $z^2=-4$. The roots may be entered in any order.",
]
ORDERED_PROMPTS = [
    "Multiply $z=1+2i$ and $w=3+i$ (you may multiply the factors in either order). Give the result as the pair "
    "$(\\operatorname{Re}, \\operatorname{Im})$.",
    "Addition in $\\mathbf{F}^2$ is commutative, so you may add the lists in either order. Compute $(3,5)+(2,-8)$. "
    "Enter your answer as a list $(a,b)$.",
    "Lists are ordered: the entries cannot be entered in any order. Enter $(1,3)+(0,1)$ as $(a,b)$.",
    "Order matters for lists, so do not give the entries in any order: write $(2,7)-(1,3)$ as $(a,b)$.",
    "Compute $3(1+i, -1+i)$ (the scalar products can be formed in any order). Enter the list.",
    "You may add the numbers in either order and enter the sum as $(a, b)$.",
    "Do not enter the roots in any order: list them as $(z_1, z_2)$ with the larger real part first.",
    "The order of the factors does not matter. Enter the product as $(\\operatorname{Re}, \\operatorname{Im})$.",
]


def _expression(prompt: str, answer: str = "(1, 7)") -> QuestionDraft:
    return QuestionDraft(key="k1q1", tier=1, kind="expression", prompt=prompt, choices=[], correct_choice=None,
                         answer=answer, answer_display="", tolerance=None, hints=["a", "b", "c"], solution="s",
                         checklist=[], targets="", source_exercise=None, steps=[])


@pytest.mark.parametrize("prompt", UNORDERED_PROMPTS)
def test_order_free_answers_become_a_set(prompt):
    assert answers_in_any_order(prompt)
    assert clean_question(_expression(prompt, "(1+i, -1-i)")).answer == "{1+i, -1-i}"
    check = InlineCheck(kind="expression", prompt=prompt, choices=[], correct_choice=None, answer="(1+i, -1-i)",
                        answer_display="", explanation="e")
    assert clean_inline_check(check)["answer"] == "{1+i, -1-i}"


@pytest.mark.parametrize("prompt", ORDERED_PROMPTS)
def test_positional_tuples_stay_ordered(prompt):
    assert not answers_in_any_order(prompt)
    q = clean_question(_expression(prompt))
    assert q.answer == "(1, 7)"
    swapped = check_answer("expression", answer=q.answer, correct_choice=None, tolerance=None, response="(7, 1)",
                           choice=None, require_simplified=False)
    assert not swapped.correct
    check = InlineCheck(kind="expression", prompt=prompt, choices=[], correct_choice=None, answer="(1, 7)",
                        answer_display="", explanation="e")
    assert clean_inline_check(check)["answer"] == "(1, 7)"


# ---------------------------------------------------------------------------
# Verification: a true_false_set disagreement is re-checked before the question is flagged
# ---------------------------------------------------------------------------


def _tf_set_row(cid: str) -> dict:
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S1'", (cid,))["id"]
    row = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND kind = 'true_false_set'", (sid,))
    assert row is not None and row["answer"] == "TFT"
    db.execute("UPDATE questions SET verify_json = '{}', flagged = 0, flag_reason = '' WHERE id = ?", (row["id"],))
    return db.query_one("SELECT * FROM questions WHERE id = ?", (row["id"],))


def _solvers(monkeypatch, *, first: str, second: str | Exception, calls: list):
    """The first (role 'verify') solve answers `first`; the careful re-check (role 'graph') answers `second`."""
    original = fake_llm.respond

    def respond(**kwargs):
        if kwargs["schema"] is IndependentAnswer:
            recheck = kwargs["instructions"] == VERIFY_STATEMENTS_RECHECK
            calls.append(kwargs["role"])
            if recheck and isinstance(second, Exception):
                raise second
            return IndependentAnswer(choice=None, answer=second if recheck else first, confidence=0.9, problems="")
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", respond)


def test_true_false_set_disagreement_is_kept_when_the_careful_recheck_agrees(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    row = _tf_set_row(cid)
    calls: list = []
    _solvers(monkeypatch, first="TTT", second="T F T", calls=calls)
    verdict = asyncio.run(verify.verify_question(row))
    assert calls == ["verify", "graph"]
    assert verdict is not None and not verdict.flagged and "re-check agreed with the key 'TFT'" in verdict.note
    stored = db.query_one("SELECT * FROM questions WHERE id = ?", (row["id"],))
    record = db.loads(stored["verify_json"])
    assert stored["flagged"] == 0 and stored["flag_reason"] == ""
    assert record["status"] == "agree_on_recheck"
    assert record["independent"]["answer"] == "TTT" and record["recheck"]["answer"] == "T F T"
    # the build's counters count it as agreeing; the note reaches the job log through verify_skill / reverify_skill
    from app.pipeline import jobs

    counts = jobs._pipeline_counts(cid)
    assert counts["rechecked"] == 1 and counts["agree"] == counts["verified"] - counts["flagged"] - counts["kept"]
    notes = asyncio.run(verify.reverify_skill(row["skill_id"]))
    assert any("'TTT'" in n and "re-check agreed" in n for n in notes)


def test_true_false_set_stays_flagged_when_the_recheck_disagrees_too(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    row = _tf_set_row(cid)
    calls: list = []
    _solvers(monkeypatch, first="TTT", second="FFT", calls=calls)
    verdict = asyncio.run(verify.verify_question(row))
    assert calls == ["verify", "graph"] and verdict.flagged and not verdict.note
    assert "independent solver answered 'TTT'" in verdict.reason and "re-check answered 'FFT'" in verdict.reason
    stored = db.query_one("SELECT * FROM questions WHERE id = ?", (row["id"],))
    assert stored["flagged"] == 1 and db.loads(stored["verify_json"])["status"] == "disagree"

    # a failed re-check call keeps the first verdict; an agreeing first solve needs no re-check
    for first, second, flagged, expected in (("TTT", RuntimeError("down"), True, ["verify", "graph"]), ("TFT", "FFF", False, ["verify"])):
        calls.clear()
        _solvers(monkeypatch, first=first, second=second, calls=calls)
        verdict = asyncio.run(verify.verify_question(_tf_set_row(cid)))
        assert verdict.flagged is flagged and calls == expected


def test_other_kinds_are_not_rechecked(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S1'", (cid,))["id"]
    row = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND kind = 'numeric'", (sid,))
    calls: list = []
    _solvers(monkeypatch, first="123456", second=row["answer"], calls=calls)
    verdict = asyncio.run(verify.verify_question(row))
    assert calls == ["verify"] and verdict.flagged and db.loads(
        db.query_one("SELECT verify_json FROM questions WHERE id = ?", (row["id"],))["verify_json"]
    )["status"] == "disagree"
