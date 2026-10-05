"""Row → API-model builders shared by the courses API (pipeline) and the study API (learning)."""

from __future__ import annotations

from typing import Any

from . import db
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


def figure_view(row: dict[str, Any], labels: list[str]) -> Figure:
    return Figure(
        id=row["id"],
        page=row["page_index"],
        page_label=label_of(labels, row["page_index"]),
        caption=row["caption"],
        description=row["description"],
        url=f"/api/courses/{row['course_id']}/figures/{row['id']}.png",
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
        frow = db.query_one("SELECT * FROM figures WHERE id = ? AND course_id = ?", (fid, course_id))
        if frow:
            figure = figure_view(frow, labels)
    fmt = viz.get("format", "none")
    if fmt == "book_figure" and figure is None:
        fmt = "none"
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
        steps=viz.get("steps") or [],
        diagram=viz.get("diagram"),
        plot_x_range=plot.get("x_range"),
        plot_y_range=plot.get("y_range"),
        plot_parameter=plot.get("parameter"),
        plot_curves=[PlotCurve(**c) for c in viz.get("plot_curves", [])],
        frames=[VisualizationFrame(**f) for f in viz.get("frames") or []],
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
    figure_ids = db.loads(skill["figure_ids_json"], [])
    figures = []
    if figure_ids:
        marks = ",".join("?" * len(figure_ids))
        for frow in db.query(f"SELECT * FROM figures WHERE course_id = ? AND id IN ({marks})", (course_id, *figure_ids)):
            figures.append(figure_view(frow, labels))
    prereqs = db.query(
        "SELECT s.* FROM edges e JOIN skills s ON s.id = e.src WHERE e.dst = ? ORDER BY s.order_index", (skill_id,)
    )
    unlocks = db.query(
        "SELECT s.* FROM edges e JOIN skills s ON s.id = e.dst WHERE e.src = ? ORDER BY s.order_index", (skill_id,)
    )
    source_pages = db.loads(skill["source_pages_json"], [])
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
        source_refs=data.get("source_refs", db.loads(skill["source_refs_json"], [])),
        source_pages=source_pages,
        page_labels=[label_of(labels, p) for p in source_pages],
        figures=figures,
        prerequisites=[skill_ref(r) for r in prereqs],
        unlocks=[skill_ref(r) for r in unlocks],
        question_count=sum(1 for q in questions if not q["flagged"]),
    )
