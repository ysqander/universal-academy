"""The SPA fallback must never serve files outside frontend/dist."""

from fastapi.testclient import TestClient

from app.main import app


def test_spa_route_blocks_path_traversal():
    client = TestClient(app)
    for path in ("/..%2F..%2FREADME.md", "/..%2F..%2F.env", "/%2Fetc%2Fhosts", "/..%2Fbackend%2Fpyproject.toml"):
        r = client.get(path)
        assert "OPENAI_API_KEY" not in r.text
        assert "[project]" not in r.text
        assert "localhost" not in r.text or r.headers.get("content-type", "").startswith("text/html")


def test_unknown_api_path_is_404():
    assert TestClient(app).get("/api/does-not-exist").status_code == 404


def test_lifespan_marks_stale_builds_on_startup_and_running_builds_on_shutdown(tmp_path, monkeypatch):
    import asyncio

    from test_pipeline_build import insert_book, make_tiny_pdf, tiny_course

    from app import db
    from app.pipeline import jobs

    stale = tiny_course(tmp_path)
    db.execute("UPDATE jobs SET state = 'running' WHERE course_id = ?", (stale,))

    async def hanging_render(ctx):
        ctx.tracker.update("render", status="running")
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs, "run_render_stage", hanging_render)
    book_id = insert_book(make_tiny_pdf(tmp_path / "other.pdf"))
    with TestClient(app) as client:
        assert jobs.get_job(stale).state == "interrupted"  # startup: left running by a previous process
        r = client.post("/api/courses", json={"book_id": book_id, "title": "", "start_page": 0, "end_page": 2})
        cid = r.json()["id"]
        assert client.get(f"/api/courses/{cid}/job").json()["state"] == "running"
    job = jobs.get_job(cid)  # shutdown: the running build was cancelled and recorded as interrupted
    assert job.state == "interrupted" and "running" not in {s.status for s in job.stages}
    assert db.query_one("SELECT status FROM courses WHERE id = ?", (cid,))["status"] == "failed"
