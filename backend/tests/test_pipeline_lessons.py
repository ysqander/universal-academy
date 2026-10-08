"""Lesson validation and the lesson view: proof skills (walkthroughs), no written answers, knowledge-point targets,
answer-format balance, source refs, figures, legacy repair."""

from __future__ import annotations

import pytest
from test_pipeline_build import build, tiny_course

from app import db, fake_llm
from app.llm_schemas import LessonDraft, QuestionDraft
from app.pipeline.context import FigureInfo
from app.pipeline.lessons import InvalidLesson, is_proof_skill, resolve_figures, validate_lesson
from app.pipeline.questions import format_warnings, length_cue
from app.pipeline.textutil import clean_source_refs
from app.views import lesson_view, visualization_view


def fake_draft(text: str = "Write the lesson package for skill S1: Multiply\nKind: computation") -> LessonDraft:
    return fake_llm.respond(schema=LessonDraft, role="lesson", instructions="", text=text)


def mc(key: str, choices: list[str], correct: int) -> QuestionDraft:
    return QuestionDraft(
        key=key,
        tier=1,
        kind="multiple_choice",
        prompt=f"Which argument proves it? ({key})",
        choices=choices,
        correct_choice=correct,
        answer="",
        answer_display="",
        tolerance=None,
        hints=["a", "b", "c"],
        solution="s",
        checklist=[],
        targets="t",
        source_exercise=None,
        steps=[],
    )


# ---------------------------------------------------------------------------
# source_refs
# ---------------------------------------------------------------------------


def test_source_refs_keep_short_item_references_only():
    refs = [
        "1.3",
        "Example 1.4",
        "Exercise 1A.3",
        "1.1 Definition — complex numbers, C",
        "Printed p.8: Elements of R² can be thought of as points or as vectors.",
        "Positive-integer powers and exponent identities, printed p.4",
        "1.3",
        "Definition 1.1 (complex numbers)",
        "2 Find two distinct square roots",
        "p. 8",
    ]
    assert clean_source_refs(refs) == ["1.3", "Example 1.4", "Exercise 1A.3", "1.1", "Definition 1.1"]


# ---------------------------------------------------------------------------
# Proof skills (D4) and serving order
# ---------------------------------------------------------------------------


def test_proof_skill_detection():
    assert is_proof_skill({"kind": "proof", "objective": "Compute"})
    assert is_proof_skill({"kind": "concept", "objective": "Justify why $0v=0$."})
    assert is_proof_skill({"kind": "proof", "objective": "Show that addition is commutative."})
    assert not is_proof_skill({"kind": "computation", "objective": "Show that $w$ is a cube root of $1$."})
    assert not is_proof_skill({"kind": "computation", "objective": "Compute $(a+bi)(c+di)$."})


def free_response(key: str) -> QuestionDraft:
    return QuestionDraft(
        key=key, tier=2, kind="free_response",
        prompt="Write a worked evaluation of $(3-2i)(-1+4i)$. Your working must show all four products.",
        choices=[], correct_choice=None, answer="", answer_display="See the model answer.", tolerance=None,
        hints=["a", "b", "c"], solution="model", checklist=["four products", "uses $i^2=-1$"], targets="t",
        source_exercise=None, steps=[],
    )


def test_new_lessons_have_no_free_response_questions():
    """The learner's review of S2: no "show your working" questions — a draft with one is revised (strict) or loses it."""
    draft = fake_draft()
    draft.knowledge_points[0].questions.insert(1, free_response("k1q9"))
    with pytest.raises(InvalidLesson, match="knowledge point 1 has free_response question.* k1q9"):
        validate_lesson(draft)
    lesson, pools, notes = validate_lesson(draft, strict=False)
    assert all(q.kind != "free_response" for pool in pools for q in pool)
    assert any("free_response" in n and n.endswith("dropped") for n in notes)
    assert lesson["common_mistakes"] == []  # no common-mistakes section in new lessons


def test_proof_lessons_are_practised_with_multistep_walkthroughs(tmp_path):
    from app.pipeline.context import load_course
    from app.pipeline.lessons import lesson_request
    from app.prompts.openai import LESSON_INSTRUCTIONS

    assert "free_response" not in LESSON_INSTRUCTIONS.split("PROOF SKILLS")[1].split("QUESTIONS")[0]
    assert "multistep walkthrough" in LESSON_INSTRUCTIONS
    _, pools, notes = validate_lesson(fake_draft("Write the lesson package for skill S2: Justify\nKind: proof"), proof=True)
    assert [q.kind for q in pools[0]][1] == "multistep" and not any("without a multistep" in n for n in notes)
    walkthrough = pools[0][1]
    assert 2 <= len(walkthrough.steps) <= 4 and walkthrough.hints == [] and walkthrough.answer == ""
    no_walkthrough = fake_draft()
    no_walkthrough.knowledge_points = [
        kp.model_copy(update={"questions": [q for q in kp.questions if q.kind != "multistep"], "target_questions": 2})
        for kp in no_walkthrough.knowledge_points
    ]
    _, _, notes = validate_lesson(no_walkthrough, proof=True)
    assert any("proof skill without a multistep walkthrough" in n for n in notes)

    cid = tiny_course(tmp_path)
    build(cid)
    s2 = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = 'S2'", (cid,))
    assert "multistep walkthroughs" in lesson_request(load_course(cid), s2)
    rows = db.query("SELECT kind, steps_json, role FROM questions WHERE skill_id = ? ORDER BY rowid", (s2["id"],))
    assert not any(r["kind"] == "free_response" for r in rows)
    multistep = [r for r in rows if r["kind"] == "multistep"]
    assert multistep and all(len(db.loads(r["steps_json"])) == 3 for r in multistep)
    assert [r["role"] for r in multistep].count("capstone") == 1 and rows[-1]["role"] == "capstone"  # stored last
    assert all(db.loads(r["steps_json"]) == [] for r in rows if r["kind"] != "multistep")


def test_knowledge_point_targets_are_clamped_and_need_one_spare_question():
    lesson, pools, _ = validate_lesson(fake_draft())
    assert [kp["target_questions"] for kp in lesson["knowledge_points"]] == [2, 3]
    assert [len(p) for p in pools] == [4, 5]
    draft = fake_draft()
    draft.knowledge_points[0].target_questions = 7  # clamped to 3; 4 questions = 3 + 1 spare: fine
    draft.knowledge_points[1].target_questions = 0  # clamped to 1
    lesson, _, notes = validate_lesson(draft)
    assert [kp["target_questions"] for kp in lesson["knowledge_points"]] == [3, 1]
    assert any("target_questions 7 clamped to 3" in n for n in notes)
    short = fake_draft()
    short.knowledge_points[1].questions = short.knowledge_points[1].questions[:3]  # target 3 needs at least 4
    with pytest.raises(InvalidLesson, match="knowledge point 2 has 3 valid questions for target_questions 3"):
        validate_lesson(short)
    lesson, pools, notes = validate_lesson(short, strict=False)
    assert lesson["knowledge_points"][1]["target_questions"] == 2 and len(pools[1]) == 3
    assert any("target lowered to 2" in n for n in notes)
    with pytest.raises(InvalidLesson, match="only 1 valid questions"):
        short.knowledge_points[1].questions = short.knowledge_points[1].questions[:1]
        validate_lesson(short, strict=False)


# ---------------------------------------------------------------------------
# Answer-format balance (warnings)
# ---------------------------------------------------------------------------


def test_length_cue_and_multiple_choice_share_are_reported():
    long = "For arbitrary $k\\in\\{1,\\dots,n\\}$, the $k$th coordinate of $p+0$ is $p_k+0=p_k$; so every coordinate agrees."
    cued = mc("k1q1", ["The first coordinate agrees.", long, "Check one example."], 1)
    balanced = mc("k1q2", ["$(1,2)$", "$(2,1)$", "$(1,-2)$"], 0)
    assert length_cue(cued) and not length_cue(balanced)
    notes = format_warnings([[cued, balanced, mc("k1q3", ["short", "a much longer option here", "mid"], 1)]])
    assert any("3 of 3 questions are multiple choice or true/false" in n for n in notes)
    assert any("much longer than the distractors in k1q1" in n for n in notes)
    assert any("the correct option is the longest in" in n for n in notes)
    _, _, notes = validate_lesson(fake_draft())
    assert not any("multiple choice" in n for n in notes)  # the fake lesson is balanced


def test_mostly_choice_based_lesson_is_rejected_when_strict():
    draft = fake_draft()
    for kp in draft.knowledge_points:
        for q in kp.questions:
            q.kind, q.choices, q.correct_choice, q.answer, q.tolerance, q.checklist = (
                "true_false", ["True", "False"], 0, "", None, [])
    with pytest.raises(InvalidLesson, match="multiple choice or true/false"):
        validate_lesson(draft)
    _, pools, notes = validate_lesson(draft, strict=False)  # last attempt: kept, with a warning
    assert pools and any("multiple choice or true/false" in n for n in notes)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def figure(local: str, page: int, caption: str = "") -> FigureInfo:
    return FigureInfo(db_id=f"c1-{local}", local_id=local, page=page, label=str(page + 1), caption=caption, description="")


def test_resolve_figures_normalizes_placeholders_and_attaches_referenced_figures():
    figures = [
        figure("p21-f1", 21, "A vector and its additive inverse"),
        figure("p22-f1", 22, "Scalar multiplication"),
        figure("p30-f1", 30, "Far away"),
    ]
    skill = {"figure_ids_json": "[]", "source_pages_json": "[21, 22]"}
    lesson = {
        "why_it_matters": "",
        "idea": "See\n\n[[figure:c1-p30-f1]]\n\nand [[figure:nope]]. The figure **“Scalar multiplication.”** shows it.",
        "missing_steps": "",
        "connection": "",
        "knowledge_points": [{"explanation": "x", "worked_example": {"problem": "", "steps": [], "takeaway": ""}}],
    }
    lesson, ids, notes = resolve_figures(lesson, skill, figures)
    assert "[[figure:p30-f1]]" in lesson["idea"] and "nope" not in lesson["idea"]
    assert ids == ["c1-p30-f1", "c1-p22-f1"]  # placed + quoted by caption (never the unreferenced p21 figure)
    assert any("unknown figure 'nope'" in n for n in notes)


def test_lesson_view_shows_quoted_figures_and_repairs_legacy_visuals(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    skill = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = 'S3'", (cid,))
    fig = db.query_one("SELECT * FROM figures WHERE course_id = ?", (cid,))
    db.execute("UPDATE figures SET caption = 'A vector and its additive inverse' WHERE id = ?", (fig["id"],))
    lesson = db.loads(skill["lesson_json"])
    lesson["idea"] = "The figure “A vector and its additive inverse” pictures this cancellation."
    lesson["source_refs"] = ["1.13", "Printed p.8: Elements of R² can be thought of as points"]
    viz = {
        "format": "equation_steps",
        "book_figure_id": None,
        "steps": [{"latex": "$$\\frac{3+2i}{1-2i}=1$$", "note": ""}, {"latex": "\\[x=1\\]", "note": ""}],
        "frames": [],
    }
    db.execute(
        "UPDATE skills SET figure_ids_json = '[]', lesson_json = ?, viz_json = ? WHERE id = ?",
        (db.dumps(lesson), db.dumps(viz), skill["id"]),
    )
    view = lesson_view(skill["id"])
    assert [f.id for f in view.figures] == [fig["id"].removeprefix(f"{cid}-")]
    assert view.figures[0].url.endswith(f"/figures/{view.figures[0].id}.png")
    assert view.source_refs == ["1.13"]
    assert [s.latex for s in view.visualization.steps] == ["\\frac{3+2i}{1-2i}=1", "x=1"]

    # a plan downgraded to 'none' still shows the figure the planner picked; frames lose stray delimiters too
    legacy = {"format": "none", "book_figure_id": fig["id"], "frames": [{"title": "a", "diagram": None, "latex": "$x$", "note": ""}]}
    shown = visualization_view(legacy, cid, [])
    assert shown.format == "book_figure" and shown.figure.id == fig["id"].removeprefix(f"{cid}-")
    assert shown.frames[0].latex == "x"
    assert visualization_view({"format": "book_figure", "book_figure_id": "p1-f1"}, cid, []).figure is not None
