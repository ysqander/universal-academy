"""Courses API smoke tests against a FastAPI app with the courses router (and the books router when present)."""

from __future__ import annotations

import importlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_pipeline_build import insert_book, make_tiny_pdf

from app import db
from app.api import courses
from app.schemas import Course, CourseGraph, CourseSummary, FoundationNode, JobStatus, Lesson, SourcePage


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(courses.router)
    try:
        app.include_router(importlib.import_module("app.api.books").router)
    except ModuleNotFoundError:
        pass
    return app


@pytest.fixture
def client():
    with TestClient(make_app()) as c:
        yield c


def wait_for_job(client: TestClient, cid: str, timeout: float = 60) -> JobStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = JobStatus.model_validate(client.get(f"/api/courses/{cid}/job").json())
        if job.state not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("build did not finish")


def test_course_lifecycle(client, tmp_path):
    book_id = insert_book(make_tiny_pdf(tmp_path / "tiny.pdf"))
    r = client.post(
        "/api/courses", json={"book_id": book_id, "title": "Tiny", "start_page": 0, "end_page": 2, "sections": ["1A"]}
    )
    assert r.status_code == 200, r.text
    course = Course.model_validate(r.json())
    cid = course.id
    assert course.job is not None and course.label_start == "1" and course.label_end == "3"

    job = wait_for_job(client, cid)
    assert job.state == "done" and len(job.log) <= 50

    summaries = [CourseSummary.model_validate(c) for c in client.get("/api/courses").json()]
    assert [c.id for c in summaries] == [cid] and summaries[0].status == "ready"
    course = Course.model_validate(client.get(f"/api/courses/{cid}").json())
    assert course.big_picture and course.excluded and course.stats.total_skills == 6
    assert course.stats.lessons_ready == 6

    graph = CourseGraph.model_validate(client.get(f"/api/courses/{cid}/graph").json())
    assert len(graph.skills) == 6 and graph.edges and len(graph.foundations) == 2
    assert {s.status for s in graph.skills} <= {"available", "locked"}
    assert all(s.page_labels for s in graph.skills)

    for skill in graph.skills:
        r = client.get(f"/api/skills/{skill.id}/lesson")
        assert r.status_code == 200, r.text
        lesson = Lesson.model_validate(r.json())
        assert lesson.question_count >= 8 and lesson.visualization is not None

    figure_skill = next(s for s in graph.skills if s.figure_ids)
    fid = figure_skill.figure_ids[0]
    assert not fid.startswith(cid)  # D8: local figure ids ("p1-f1") everywhere in the API
    r = client.get(f"/api/courses/{cid}/figures/{fid}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert client.get(f"/api/courses/{cid}/figures/{cid}-{fid}.png").status_code == 200  # the DB form still works
    assert client.get(f"/api/courses/{cid}/figures/nope.png").status_code == 404
    lesson = Lesson.model_validate(client.get(f"/api/skills/{figure_skill.id}/lesson").json())
    assert fid in {f.id for f in lesson.figures}
    assert all(client.get(f.url).status_code == 200 for f in lesson.figures)

    page = SourcePage.model_validate(client.get(f"/api/courses/{cid}/source/1").json())
    assert page.label == "2" and page.markdown and page.image_url == f"/api/books/{book_id}/pages/1.png?dpi=150"
    assert client.get(f"/api/courses/{cid}/source/7").status_code == 404

    r = client.post(f"/api/foundations/{cid}/A1", json={"known": True})
    assert FoundationNode.model_validate(r.json()).known is True
    assert client.post(f"/api/foundations/{cid}/A9", json={"known": True}).status_code == 404

    r = client.post(f"/api/skills/{graph.skills[0].id}/more-questions")
    assert r.status_code == 200 and r.json() == {"added": 3}

    resumed = JobStatus.model_validate(client.post(f"/api/courses/{cid}/resume").json())
    assert resumed.course_id == cid
    assert wait_for_job(client, cid).state == "done"

    db.execute("UPDATE jobs SET state = 'running' WHERE course_id = ?", (cid,))
    assert client.delete(f"/api/courses/{cid}").status_code == 409
    db.execute("UPDATE jobs SET state = 'done' WHERE course_id = ?", (cid,))
    assert client.delete(f"/api/courses/{cid}").status_code == 204
    assert client.get(f"/api/courses/{cid}").status_code == 404
    assert client.get("/api/courses").json() == []


def test_errors(client, tmp_path):
    book_id = insert_book(make_tiny_pdf(tmp_path / "tiny.pdf"))
    r = client.post("/api/courses", json={"book_id": book_id, "title": "x", "start_page": 2, "end_page": 9})
    assert r.status_code == 400 and "Invalid page range" in r.json()["detail"]
    r = client.post("/api/courses", json={"book_id": "missing", "title": "x", "start_page": 0, "end_page": 0})
    assert r.status_code == 400
    assert client.get("/api/courses/nope").status_code == 404
    assert client.get("/api/courses/nope/job").status_code == 404
    assert client.post("/api/courses/nope/resume").status_code == 404
    assert client.delete("/api/courses/nope").status_code == 404
    assert client.get("/api/courses/nope/graph").status_code == 404
    assert client.get("/api/skills/nope/lesson").status_code == 404
    assert client.post("/api/skills/nope/more-questions").status_code == 404


def test_lesson_is_404_until_ready(client, tmp_path):
    book_id = insert_book(make_tiny_pdf(tmp_path / "tiny.pdf"))
    cid = client.post("/api/courses", json={"book_id": book_id, "title": "", "start_page": 0, "end_page": 2}).json()["id"]
    wait_for_job(client, cid)
    skill = db.query_one("SELECT id FROM skills WHERE course_id = ? ORDER BY order_index", (cid,))["id"]
    db.execute("UPDATE skills SET lesson_status = 'pending' WHERE id = ?", (skill,))
    r = client.get(f"/api/skills/{skill}/lesson")
    assert r.status_code == 404 and "still being written" in r.json()["detail"]
    assert client.post(f"/api/skills/{skill}/more-questions").status_code == 409


def test_resume_retries_only_the_failed_lessons_of_a_ready_course(client, tmp_path, monkeypatch):
    from test_pipeline_build import fail_for

    from app import fake_llm

    book_id = insert_book(make_tiny_pdf(tmp_path / "tiny.pdf"))
    fail_for(monkeypatch, "LessonDraft", lambda text: "skill S3:" in text)
    cid = client.post("/api/courses", json={"book_id": book_id, "title": "", "start_page": 0, "end_page": 2}).json()["id"]
    assert wait_for_job(client, cid).state == "done"
    graph = CourseGraph.model_validate(client.get(f"/api/courses/{cid}/graph").json())
    assert [s.key for s in graph.skills if s.lesson_status == "failed"] == ["S3"]
    assert client.get(f"/api/courses/{cid}").json()["status"] == "ready"

    monkeypatch.undo()
    calls: list[str] = []
    original = fake_llm.respond
    monkeypatch.setattr(fake_llm, "respond", lambda **kw: calls.append(kw["schema"].__name__) or original(**kw))
    assert client.post(f"/api/courses/{cid}/resume").status_code == 200
    assert wait_for_job(client, cid).state == "done"
    graph = CourseGraph.model_validate(client.get(f"/api/courses/{cid}/graph").json())
    assert all(s.lesson_status == "ready" for s in graph.skills)
    assert calls.count("LessonDraft") == 1 and calls.count("VisualizationPlan") == 1 and "KnowledgeGraphDraft" not in calls
    assert client.get(f"/api/courses/{cid}").json()["status"] == "ready"


def test_more_questions_reports_llm_failures_as_502(client, tmp_path, monkeypatch):
    from app import llm

    book_id = insert_book(make_tiny_pdf(tmp_path / "tiny.pdf"))
    cid = client.post("/api/courses", json={"book_id": book_id, "title": "", "start_page": 0, "end_page": 2}).json()["id"]
    wait_for_job(client, cid)
    skill = db.query_one("SELECT id FROM skills WHERE course_id = ? ORDER BY order_index", (cid,))["id"]

    async def broken(*_args, **_kwargs):
        raise llm.LLMError("lesson call failed after retries")

    monkeypatch.setattr(courses, "top_up_questions", broken)
    r = client.post(f"/api/skills/{skill}/more-questions")
    assert r.status_code == 502 and "Could not generate questions" in r.json()["detail"]


def test_a_moved_data_dir_keeps_courses_working(client, tmp_path):
    """#32: rows store paths relative to the data dir; the book PDF and figures are found by id after a move."""
    import shutil

    from app.config import settings
    from app.pipeline.context import course_dir

    with open(make_tiny_pdf(tmp_path / "tiny.pdf"), "rb") as fh:
        book_id = client.post("/api/books", files={"file": ("tiny.pdf", fh, "application/pdf")}).json()["id"]
    cid = client.post("/api/courses", json={"book_id": book_id, "title": "", "start_page": 0, "end_page": 2}).json()["id"]
    assert wait_for_job(client, cid).state == "done"
    stored_pdf = db.query_one("SELECT pdf_path FROM books WHERE id = ?", (book_id,))["pdf_path"]
    figure = db.query_one("SELECT id, image_path FROM figures WHERE course_id = ?", (cid,))
    assert stored_pdf == f"books/{book_id}/book.pdf" and not figure["image_path"].startswith("/")
    fid = figure["id"].removeprefix(f"{cid}-")

    old, new = settings.data_dir, tmp_path / "moved"
    shutil.copytree(old, new)
    shutil.rmtree(old)
    object.__setattr__(settings, "data_dir", new)
    db._initialized_for = None
    assert client.get(f"/api/courses/{cid}/figures/{fid}.png").status_code == 200
    assert client.get(f"/api/books/{book_id}/pages/0.png").status_code == 200

    # rows written by older versions hold absolute paths into the old location: still found by id
    db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", (str(old / "books" / book_id / "book.pdf"), book_id))
    db.execute("UPDATE figures SET image_path = ? WHERE id = ?", (str(old / "courses" / cid / "figures" / f"{fid}.png"), figure["id"]))
    assert client.get(f"/api/courses/{cid}/figures/{fid}.png").status_code == 200

    # a new course on the same book builds; a course whose pages must be read again resumes
    other = client.post("/api/courses", json={"book_id": book_id, "title": "", "start_page": 0, "end_page": 1}).json()["id"]
    assert wait_for_job(client, other).state == "done"
    db.execute("DELETE FROM pages WHERE course_id = ?", (cid,))
    shutil.rmtree(course_dir(cid) / "pages")
    assert client.post(f"/api/courses/{cid}/resume").status_code == 200
    job = wait_for_job(client, cid)
    assert job.state == "done", job.error
    assert db.query_one("SELECT COUNT(*) AS n FROM pages WHERE course_id = ?", (cid,))["n"] == 3
    assert client.get(f"/api/courses/{cid}/figures/{fid}.png").status_code == 200
