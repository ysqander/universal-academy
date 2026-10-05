"""Courses API smoke tests against a FastAPI app with the courses router (and the books router when present)."""

from __future__ import annotations

import importlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.api import courses
from app.schemas import Course, CourseGraph, CourseSummary, FoundationNode, JobStatus, Lesson, SourcePage
from test_pipeline_build import insert_book, make_tiny_pdf


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
    r = client.get(f"/api/courses/{cid}/figures/{fid}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    local = fid.removeprefix(f"{cid}-")
    assert client.get(f"/api/courses/{cid}/figures/{local}.png").status_code == 200
    assert client.get(f"/api/courses/{cid}/figures/nope.png").status_code == 404

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
