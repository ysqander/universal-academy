"""Courses API (SPEC §5): create/build/resume/delete courses, the knowledge graph, lessons, figures, source pages."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from .. import db, llm
from ..learning.state import skill_states
from ..learning.stats import compute_stats
from ..pipeline import jobs
from ..pipeline.context import figure_db_id
from ..pipeline.topup import TopUpUnavailable, top_up_questions
from ..schemas import (
    Course,
    CourseCreate,
    CourseGraph,
    CourseSummary,
    FoundationKnownRequest,
    FoundationNode,
    JobStatus,
    Lesson,
    SkillEdge,
    SkillNode,
    SourcePage,
)
from ..views import label_of, lesson_view, local_figure_id, page_labels_for

log = logging.getLogger("ua.api.courses")

router = APIRouter()

SOURCE_DPI = 150


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _course_row(course_id: str) -> dict[str, Any]:
    row = db.query_one(
        "SELECT c.*, b.title AS book_title, b.page_labels_json FROM courses c JOIN books b ON b.id = c.book_id "
        "WHERE c.id = ?",
        (course_id,),
    )
    if row is None:
        raise HTTPException(404, f"Course {course_id} not found")
    return row


def _summary(row: dict[str, Any]) -> CourseSummary:
    labels = db.loads(row["page_labels_json"], [])
    return CourseSummary(
        id=row["id"],
        book_id=row["book_id"],
        book_title=row["book_title"],
        title=row["title"],
        status=row["status"],
        start_page=row["start_page"],
        end_page=row["end_page"],
        label_start=label_of(labels, row["start_page"]),
        label_end=label_of(labels, row["end_page"]),
        created_at=row["created_at"],
        stats=compute_stats(row["id"]),
    )


def course_detail(course_id: str) -> Course:
    row = _course_row(course_id)
    return Course(
        **_summary(row).model_dump(),
        sections=db.loads(row["sections_json"], []),
        big_picture=row["big_picture"],
        excluded=db.loads(row["excluded_json"], []),
        job=jobs.get_job(course_id),
    )


def _foundation(row: dict[str, Any]) -> FoundationNode:
    return FoundationNode(
        key=row["key"],
        title=row["title"],
        description=row["description"],
        check_question=row["check_question"],
        check_answer=row["check_answer"],
        known=None if row["known"] is None else bool(row["known"]),
    )


# ---------------------------------------------------------------------------
# Courses + jobs
# ---------------------------------------------------------------------------


@router.post("/api/courses", response_model=Course)
async def create_course(req: CourseCreate) -> Course:
    try:
        course_id = jobs.create_course(req)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    jobs.start_build(course_id)
    return course_detail(course_id)


@router.get("/api/courses", response_model=list[CourseSummary])
def list_courses() -> list[CourseSummary]:
    rows = db.query(
        "SELECT c.*, b.title AS book_title, b.page_labels_json FROM courses c JOIN books b ON b.id = c.book_id "
        "ORDER BY c.created_at DESC, c.rowid DESC"
    )
    return [_summary(r) for r in rows]


@router.get("/api/courses/{cid}", response_model=Course)
def get_course(cid: str) -> Course:
    return course_detail(cid)


@router.get("/api/courses/{cid}/job", response_model=JobStatus)
def get_job(cid: str) -> JobStatus:
    _course_row(cid)
    job = jobs.get_job(cid)
    if job is None:
        raise HTTPException(404, f"Course {cid} has no build job")
    return job


@router.post("/api/courses/{cid}/resume", response_model=JobStatus)
async def resume(cid: str) -> JobStatus:
    _course_row(cid)
    return jobs.resume_build(cid)


@router.delete("/api/courses/{cid}", status_code=204)
def delete_course(cid: str) -> Response:
    _course_row(cid)
    try:
        jobs.delete_course(cid)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Graph, foundations
# ---------------------------------------------------------------------------


@router.get("/api/courses/{cid}/graph", response_model=CourseGraph)
def get_graph(cid: str) -> CourseGraph:
    row = _course_row(cid)
    labels = db.loads(row["page_labels_json"], [])
    states = skill_states(cid)
    skills = []
    for s in db.query("SELECT * FROM skills WHERE course_id = ? ORDER BY order_index", (cid,)):
        info = states.get(s["id"])
        pages = db.loads(s["source_pages_json"], [])
        skills.append(
            SkillNode(
                id=s["id"],
                key=s["key"],
                title=s["title"],
                objective=s["objective"],
                kind=s["kind"],
                summary=s["summary"],
                difficulty=s["difficulty"],
                est_minutes=s["est_minutes"],
                order_index=s["order_index"],
                source_pages=pages,
                page_labels=[label_of(labels, p) for p in pages],
                source_refs=db.loads(s["source_refs_json"], []),
                figure_ids=[local_figure_id(cid, f) for f in db.loads(s["figure_ids_json"], [])],
                foundations=db.loads(s["foundations_json"], []),
                misconceptions=db.loads(s["misconceptions_json"], []),
                lesson_status=s["lesson_status"],
                status=info.status if info else "locked",
                due=info.due if info else False,
                due_at=info.due_at if info else None,
                retrievability=info.retrievability if info else None,
                stability_days=info.stability_days if info else None,
                reviews=info.reviews if info else 0,
            )
        )
    edges = [
        SkillEdge(source=e["src"], target=e["dst"], weight=e["weight"], reduced=bool(e["reduced"]))
        for e in db.query("SELECT * FROM edges WHERE course_id = ?", (cid,))
    ]
    foundations = [
        _foundation(f) for f in db.query("SELECT * FROM foundations WHERE course_id = ? ORDER BY key", (cid,))
    ]
    return CourseGraph(course_id=cid, skills=skills, edges=edges, foundations=foundations)


@router.post("/api/foundations/{cid}/{key}", response_model=FoundationNode)
def set_foundation_known(cid: str, key: str, req: FoundationKnownRequest) -> FoundationNode:
    changed = db.execute(
        "UPDATE foundations SET known = ? WHERE course_id = ? AND key = ?", (int(req.known), cid, key)
    )
    if not changed:
        raise HTTPException(404, f"Foundation {key} not found in course {cid}")
    row = db.query_one("SELECT * FROM foundations WHERE course_id = ? AND key = ?", (cid, key))
    assert row is not None
    return _foundation(row)


# ---------------------------------------------------------------------------
# Figures, source pages, lessons
# ---------------------------------------------------------------------------


@router.get("/api/courses/{cid}/figures/{fid}.png", response_class=FileResponse)
def get_figure(cid: str, fid: str) -> FileResponse:
    row = db.query_one(
        "SELECT image_path FROM figures WHERE course_id = ? AND id IN (?, ?)", (cid, fid, figure_db_id(cid, fid))
    )
    if row is None or not Path(row["image_path"]).is_file():
        raise HTTPException(404, f"Figure {fid} not found")
    return FileResponse(row["image_path"], media_type="image/png")


@router.get("/api/courses/{cid}/source/{page}", response_model=SourcePage)
def get_source_page(cid: str, page: int) -> SourcePage:
    course = _course_row(cid)
    if not course["start_page"] <= page <= course["end_page"]:
        raise HTTPException(404, f"Page {page} is not part of this course")
    labels = page_labels_for(course["book_id"])
    row = db.query_one("SELECT * FROM pages WHERE course_id = ? AND page_index = ?", (cid, page))
    return SourcePage(
        page=page,
        label=(row["label"] if row and row["label"] else label_of(labels, page)),
        markdown=row["markdown"] if row else "",
        image_url=f"/api/books/{course['book_id']}/pages/{page}.png?dpi={SOURCE_DPI}",
        uncertain=db.loads(row["uncertain_json"], []) if row else [],
    )


@router.get("/api/skills/{sid}/lesson", response_model=Lesson)
def get_lesson(sid: str) -> Lesson:
    skill = db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (sid,))
    if skill is None:
        raise HTTPException(404, f"Skill {sid} not found")
    lesson = lesson_view(sid)
    if lesson is None:
        status = skill["lesson_status"]
        detail = "The lesson could not be generated." if status == "failed" else "The lesson is still being written."
        raise HTTPException(404, detail)
    return lesson


@router.post("/api/skills/{sid}/more-questions")
async def more_questions(sid: str) -> dict[str, int]:
    if db.query_one("SELECT 1 FROM skills WHERE id = ?", (sid,)) is None:
        raise HTTPException(404, f"Skill {sid} not found")
    try:
        added = await top_up_questions(sid)
    except TopUpUnavailable as e:
        raise HTTPException(409, str(e)) from e
    except llm.LLMError as e:
        raise HTTPException(502, f"Could not generate questions: {e}") from e
    return {"added": added}
