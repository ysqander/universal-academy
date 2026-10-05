"""Lesson validation and the lesson view: proof skills, answer-format balance, source refs, figures, legacy repair."""

from __future__ import annotations

import pytest
from test_pipeline_build import build, tiny_course

from app import db, fake_llm
from app.llm_schemas import LessonDraft, QuestionDraft
from app.pipeline.context import FigureInfo
from app.pipeline.lessons import InvalidLesson, is_proof_skill, resolve_figures, validate_lesson
from app.pipeline.questions import first_free_response_second, format_warnings, length_cue
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
    assert is_proof_skill({"kind": "concept", "objective": "Show that addition is commutative."})
    assert not is_proof_skill({"kind": "computation", "objective": "Compute $(a+bi)(c+di)$."})


def test_proof_lesson_needs_a_free_response_in_every_knowledge_point():
    draft = fake_draft()  # a computation lesson: KP 1 has no free-response question
    with pytest.raises(InvalidLesson, match="knowledge point 1 of a proof skill"):
        validate_lesson(draft, proof=True)
    _, _, notes = validate_lesson(draft, proof=True, strict=False)
    assert any("no free-response proof" in n for n in notes)
    _, pools, _ = validate_lesson(fake_draft("Write the lesson package for skill S2: Justify\nKind: proof"), proof=True)
    assert all("free_response" in [q.kind for q in pool[:2]] for pool in pools)


def test_first_free_response_moves_to_second_position():
    draft = fake_draft()
    pool = draft.knowledge_points[1].questions  # [expression, numeric, mc, numeric, free_response]
    ordered = first_free_response_second(pool)
    assert [q.kind for q in ordered] == ["expression", "free_response", "numeric", "multiple_choice", "numeric"]
    assert first_free_response_second(ordered) == ordered


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
