"""Row → API-model builders shared by the courses API (pipeline) and the study API (learning)."""

from __future__ import annotations

from typing import Any

from . import db
from .pipeline.textutil import bare_latex, clean_source_refs, lesson_text, mentions_caption, placeholder_ids
from .schemas import (
    Figure,
    KnowledgePoint,
    Lesson,
    NotationItem,
    PlotCurve,
    QuestionPublic,
    SkillRef,
    Visualization,
    VisualizationFrame,
    WorkedExample,
)


def page_labels_for(book_id: str) -> list[str]:
    row = db.query_one("SELECT page_labels_json FROM books WHERE id = ?", (book_id,))
    return db.loads(row["page_labels_json"], []) if row else []


def course_page_labels(course_id: str) -> list[str]:
    row = db.query_one(
        "SELECT b.page_labels_json FROM courses c JOIN books b ON b.id = c.book_id WHERE c.id = ?", (course_id,)
    )
    return db.loads(row["page_labels_json"], []) if row else []


def label_of(labels: list[str], page: int) -> str:
    return labels[page] if 0 <= page < len(labels) and labels[page] else str(page + 1)


def local_figure_id(course_id: str, figure_id: str) -> str:
    """The figure id the API exposes: the parser's local id ("p21-f1"), matching [[figure:p21-f1]] placeholders.
    The figures table stores "<course_id>-p21-f1" (see pipeline.context.figure_db_id)."""
    return figure_id.removeprefix(f"{course_id}-")


def figure_view(row: dict[str, Any], labels: list[str]) -> Figure:
    local = local_figure_id(row["course_id"], row["id"])
    return Figure(
        id=local,
        page=row["page_index"],
        page_label=label_of(labels, row["page_index"]),
        caption=row["caption"],
        description=row["description"],
        url=f"/api/courses/{row['course_id']}/figures/{local}.png",
    )


def question_public(row: dict[str, Any]) -> QuestionPublic:
    return QuestionPublic(
        id=row["id"],
        skill_id=row["skill_id"],
        key=row["key"],
        kp_index=row["kp_index"],
        tier=row["tier"],
        kind=row["kind"],
        prompt=row["prompt"],
        choices=db.loads(row["choices_json"], []),
        hints=db.loads(row["hints_json"], []),
        checklist=db.loads(row["checklist_json"], []),
        source_exercise=row["source_exercise"],
        flagged=bool(row["flagged"]),
    )


def skill_status(skill_id: str) -> str:
    row = db.query_one("SELECT status FROM progress WHERE skill_id = ?", (skill_id,))
    return row["status"] if row else "locked"


def skill_ref(row: dict[str, Any], status: str | None = None) -> SkillRef:
    return SkillRef(id=row["id"], key=row["key"], title=row["title"], status=status or skill_status(row["id"]))


def visualization_view(viz: dict[str, Any] | None, course_id: str, labels: list[str]) -> Visualization | None:
    """viz_json = VisualizationPlan fields + optional pre-sampled 'plot_curves' (list[PlotCurve dict])."""
    if not viz:
        return None
    figure = None
    fid = viz.get("book_figure_id")
    if fid:
        frow = db.query_one(
            "SELECT * FROM figures WHERE course_id = ? AND id IN (?, ?)", (course_id, fid, f"{course_id}-{fid}")
        )
        if frow:
            figure = figure_view(frow, labels)
    fmt = viz.get("format", "none")
    if fmt == "book_figure" and figure is None:
        fmt = "none"
    elif fmt == "none" and figure is not None:
        fmt = "book_figure"  # a downgraded plan falls back to the figure the planner picked
    plot = viz.get("plot") or {}
    return Visualization(
        format=fmt,
        misunderstanding=viz.get("misunderstanding", ""),
        purpose=viz.get("purpose", ""),
        rationale=viz.get("rationale", ""),
        learner_prompt=viz.get("learner_prompt", ""),
        caption=viz.get("caption", ""),
        figure=figure,
        table=viz.get("table"),
        # Stray $/$$ delimiters break KaTeX display rendering; courses built before the pipeline stripped them keep them.
        steps=[{**step, "latex": bare_latex(step.get("latex", ""))} for step in viz.get("steps") or []],
        diagram=viz.get("diagram"),
        plot_x_range=plot.get("x_range"),
        plot_y_range=plot.get("y_range"),
        plot_parameter=plot.get("parameter"),
        plot_curves=[PlotCurve(**c) for c in viz.get("plot_curves", [])],
        frames=[
            VisualizationFrame(**{**f, "latex": bare_latex(f["latex"]) if f.get("latex") else f.get("latex")})
            for f in viz.get("frames") or []
        ],
    )


def lesson_view(skill_id: str) -> Lesson | None:
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if not skill or skill["lesson_status"] != "ready" or not skill["lesson_json"]:
        return None
    course_id = skill["course_id"]
    labels = course_page_labels(course_id)
    data = db.loads(skill["lesson_json"], {})
    questions = db.query("SELECT kp_index, flagged FROM questions WHERE skill_id = ?", (skill_id,))
    kps = []
    for i, kp in enumerate(data.get("knowledge_points", [])):
        kps.append(
            KnowledgePoint(
                index=i,
                title=kp.get("title", f"Step {i + 1}"),
                explanation=kp.get("explanation", ""),
                worked_example=WorkedExample(**kp["worked_example"]),
                question_count=sum(1 for q in questions if q["kp_index"] == i and not q["flagged"]),
            )
        )
    source_pages = db.loads(skill["source_pages_json"], [])
    figures = [figure_view(r, labels) for r in lesson_figure_rows(skill, data)]
    prereqs = db.query(
        "SELECT s.* FROM edges e JOIN skills s ON s.id = e.src WHERE e.dst = ? ORDER BY s.order_index", (skill_id,)
    )
    unlocks = db.query(
        "SELECT s.* FROM edges e JOIN skills s ON s.id = e.dst WHERE e.src = ? ORDER BY s.order_index", (skill_id,)
    )
    return Lesson(
        skill_id=skill_id,
        skill_key=skill["key"],
        title=skill["title"],
        objective=skill["objective"],
        why_it_matters=data.get("why_it_matters", ""),
        idea=data.get("idea", ""),
        notation=[NotationItem(**n) for n in data.get("notation", [])],
        knowledge_points=kps,
        missing_steps=data.get("missing_steps", ""),
        common_mistakes=data.get("common_mistakes", []),
        connection=data.get("connection", ""),
        visualization=visualization_view(db.loads(skill["viz_json"], None), course_id, labels),
        source_refs=clean_source_refs(data.get("source_refs") or db.loads(skill["source_refs_json"], [])),
        source_pages=source_pages,
        page_labels=[label_of(labels, p) for p in source_pages],
        figures=figures,
        prerequisites=[skill_ref(r) for r in prereqs],
        unlocks=[skill_ref(r) for r in unlocks],
        question_count=sum(1 for q in questions if not q["flagged"]),
    )


def lesson_figure_rows(skill: dict[str, Any], lesson: dict[str, Any]) -> list[dict[str, Any]]:
    """Figure rows a lesson shows: the figures attached to the skill, every figure its text places with
    [[figure:ID]], and figures on its source pages whose caption the text quotes (lessons may only refer to figures
    the learner can see)."""
    course_id = skill["course_id"]
    rows = db.query("SELECT * FROM figures WHERE course_id = ? ORDER BY page_index, id", (course_id,))
    if not rows:
        return []
    attached = {f"{course_id}-{local_figure_id(course_id, f)}" for f in db.loads(skill["figure_ids_json"], [])}
    text = lesson_text(lesson)
    placed = {f"{course_id}-{local_figure_id(course_id, f)}" for f in placeholder_ids(text)}
    pages = set(db.loads(skill["source_pages_json"], []))
    return [
        r
        for r in rows
        if r["id"] in attached
        or r["id"] in placed
        or (r["page_index"] in pages and r["caption"] and mentions_caption(text, r["caption"]))
    ]
