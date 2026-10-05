"""Full build pipeline in fake-LLM mode: stages, persistence, verification, failure handling, resume, top-up."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path

import networkx as nx
import pymupdf
import pytest

from app import db, fake_llm, llm
from app.ingest.pdf import read_book_meta
from app.llm_schemas import IndependentAnswer, LessonDraft
from app.pipeline import jobs, topup, verify
from app.pipeline.questions import normalized_prompt
from app.schemas import CourseCreate, JobStatus, Lesson
from app.views import lesson_view

# ---------------------------------------------------------------------------
# Helpers (also imported by test_pipeline_api.py)
# ---------------------------------------------------------------------------

TINY_PAGES = [
    "1A Complex Numbers\n1.1 definition: complex numbers\nA complex number is an ordered pair (a, b), written a + bi.\n"
    "(a + bi)(c + di) = (ac - bd) + (ad + bc)i",
    "1.3 properties of complex arithmetic\ncommutativity, associativity, identities, inverses.\n"
    "The figure shows two vectors and their sum.",
    "Exercises 1A\n1 Show that a + b = b + a for all complex numbers a, b.\n2 Find two distinct square roots of i.",
]


def make_tiny_pdf(path: Path) -> Path:
    """3 pages of text; page 1 carries a vector drawing (a triangle of arrows)."""
    doc = pymupdf.open()
    for i, text in enumerate(TINY_PAGES):
        page = doc.new_page()
        page.insert_text((72, 90), text, fontsize=11)
        if i == 1:
            shape = page.new_shape()
            shape.draw_line((150, 400), (330, 260))
            shape.draw_line((150, 400), (360, 430))
            shape.draw_line((330, 260), (360, 430))
            shape.finish(color=(0, 0, 0), width=1.5)
            shape.commit()
    doc.save(path)
    doc.close()
    return path


def insert_book(pdf_path: Path) -> str:
    meta = read_book_meta(pdf_path)
    book_id = db.new_id()
    db.execute(
        "INSERT INTO books(id, title, author, filename, pdf_path, page_count, outline_json, outline_source, "
        "page_labels_json, has_text_layer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            book_id,
            meta.title,
            meta.author,
            pdf_path.name,
            str(pdf_path),
            meta.page_count,
            db.dumps([n.model_dump() for n in meta.outline]),
            meta.outline_source,
            db.dumps(meta.page_labels),
            int(meta.has_text_layer),
            db.now_iso(),
        ),
    )
    return book_id


def tiny_course(tmp_path: Path) -> str:
    book_id = insert_book(make_tiny_pdf(tmp_path / "tiny.pdf"))
    return jobs.create_course(CourseCreate(book_id=book_id, title="Tiny", start_page=0, end_page=2, sections=["1A"]))


@pytest.fixture
def llm_calls(monkeypatch) -> Counter:
    """Count fake LLM calls per schema."""
    calls: Counter = Counter()
    original = fake_llm.respond

    def counting(**kwargs):
        calls[kwargs["schema"].__name__] += 1
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", counting)
    return calls


def fail_for(monkeypatch, schema_name: str, when=lambda text: True) -> None:
    original = fake_llm.respond

    def failing(**kwargs):
        if kwargs["schema"].__name__ == schema_name and when(kwargs["text"]):
            raise RuntimeError(f"simulated {schema_name} failure")
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", failing)


def build(course_id: str) -> JobStatus:
    return asyncio.run(jobs.run_build(course_id))


def assert_complete(course_id: str, job: JobStatus) -> None:
    assert job.state == "done", job.error
    assert [s.status for s in job.stages] == ["done"] * 7
    assert all(s.done == s.total for s in job.stages)
    assert db.query_one("SELECT status FROM courses WHERE id = ?", (course_id,))["status"] == "ready"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_full_build_on_tiny_pdf(tmp_path, llm_calls):
    cid = tiny_course(tmp_path)
    job = build(cid)
    assert_complete(cid, job)

    pages = db.query("SELECT * FROM pages WHERE course_id = ?", (cid,))
    assert len(pages) == 3 and any(p["is_exercise_page"] for p in pages)
    assert all((jobs.course_dir(cid) / "pages" / f"{p['page_index']}.png").is_file() for p in pages)
    figures = db.query("SELECT * FROM figures WHERE course_id = ?", (cid,))
    assert figures and all(f["id"].startswith(f"{cid}-") and Path(f["image_path"]).is_file() for f in figures)

    skills = db.query("SELECT * FROM skills WHERE course_id = ? ORDER BY order_index", (cid,))
    assert [s["key"] for s in skills] == [f"S{i + 1}" for i in range(len(skills))]
    assert all(s["lesson_status"] == "ready" and s["viz_json"] for s in skills)
    edges = db.query("SELECT * FROM edges WHERE course_id = ?", (cid,))
    graph = nx.DiGraph([(e["src"], e["dst"]) for e in edges])
    assert nx.is_directed_acyclic_graph(graph)
    order = {s["id"]: s["order_index"] for s in skills}
    assert all(order[e["src"]] < order[e["dst"]] for e in edges)
    assert db.query("SELECT * FROM foundations WHERE course_id = ?", (cid,))
    assert db.query_one("SELECT big_picture FROM courses WHERE id = ?", (cid,))["big_picture"]

    questions = db.query(
        "SELECT q.* FROM questions q JOIN skills s ON s.id = q.skill_id WHERE s.course_id = ?", (cid,)
    )
    statuses = Counter(db.loads(q["verify_json"], {}).get("status") for q in questions)
    assert statuses["agree"] == sum(1 for q in questions if q["kind"] != "free_response")
    assert statuses["skipped"] == sum(1 for q in questions if q["kind"] == "free_response")
    assert not any(q["flagged"] for q in questions)
    assert llm_calls["IndependentAnswer"] == statuses["agree"]
    assert llm_calls["KnowledgeGraphDraft"] == 1 and llm_calls["LessonDraft"] == len(skills)

    progress = db.query("SELECT * FROM progress WHERE course_id = ?", (cid,))
    assert len(progress) == len(skills) and any(p["status"] == "available" for p in progress)

    formats = Counter()
    for s in skills:
        lesson = lesson_view(s["id"])
        assert isinstance(lesson, Lesson) and len(lesson.knowledge_points) == 2
        assert all(kp.question_count >= 2 for kp in lesson.knowledge_points)
        formats[lesson.visualization.format] += 1
        if lesson.visualization.format == "function_plot":
            assert lesson.visualization.plot_curves and lesson.visualization.plot_parameter is not None
    assert len(formats) >= 4
    assert any("Lesson S1 ready" in line for line in job.log)


def test_resume_is_a_noop(tmp_path, llm_calls):
    cid = tiny_course(tmp_path)
    assert_complete(cid, build(cid))
    llm_calls.clear()
    job = build(cid)
    assert_complete(cid, job)
    assert sum(llm_calls.values()) == 0
    assert "Build resumed" in job.log[-2]


def test_concurrent_runs_are_refused(tmp_path, llm_calls):
    cid = tiny_course(tmp_path)

    async def twice():
        return await asyncio.gather(jobs.run_build(cid), jobs.run_build(cid))

    first, second = asyncio.run(twice())
    assert first.state == "done"
    assert llm_calls["KnowledgeGraphDraft"] == 1


def test_start_build_runs_in_the_background(tmp_path):
    cid = tiny_course(tmp_path)

    async def main():
        jobs.start_build(cid)
        jobs.start_build(cid)  # no second task while one is pending
        assert jobs.is_building(cid)
        while jobs.is_building(cid):
            await asyncio.sleep(0.05)

    asyncio.run(main())
    assert_complete(cid, jobs.get_job(cid))


def test_axler_section_1a_fake_build(axler_pdf, llm_calls):
    book_id = insert_book(axler_pdf)
    cid = jobs.create_course(
        CourseCreate(book_id=book_id, title="1A Rⁿ and Cⁿ", start_page=15, end_page=24, sections=["1A R^n and C^n"])
    )
    job = build(cid)
    assert_complete(cid, job)
    pages = db.query("SELECT page_index, label, markdown FROM pages WHERE course_id = ? ORDER BY page_index", (cid,))
    assert [p["page_index"] for p in pages] == list(range(15, 25))
    assert pages[0]["label"] == "2" and "complex" in pages[0]["markdown"].lower()
    skills = db.query("SELECT * FROM skills WHERE course_id = ?", (cid,))
    assert all(15 <= p <= 24 for s in skills for p in db.loads(s["source_pages_json"], []))
    assert all(s["lesson_status"] == "ready" for s in skills)
    assert db.query_one("SELECT COUNT(*) AS n FROM figures WHERE course_id = ?", (cid,))["n"] >= 1


# ---------------------------------------------------------------------------
# Failures and recovery
# ---------------------------------------------------------------------------


def test_single_lesson_failure_is_retried_then_recovered_on_resume(tmp_path, monkeypatch, llm_calls):
    cid = tiny_course(tmp_path)
    fail_for(monkeypatch, "LessonDraft", lambda text: "skill S2:" in text)
    job = build(cid)
    assert job.state == "done"
    s2 = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = 'S2'", (cid,))
    assert s2["lesson_status"] == "failed" and s2["viz_json"] is None
    lessons = next(s for s in job.stages if s.key == "lessons")
    assert lessons.message == "5 ready · 1 failed" and lessons.done == lessons.total
    assert any("Lesson S2 failed after 2 attempts" in line for line in job.log)

    monkeypatch.undo()
    calls: Counter = Counter()
    original = fake_llm.respond
    monkeypatch.setattr(fake_llm, "respond", lambda **kw: calls.update([kw["schema"].__name__]) or original(**kw))
    job = build(cid)
    assert_complete(cid, job)
    assert calls["LessonDraft"] == 1 and calls["VisualizationPlan"] == 1 and calls["KnowledgeGraphDraft"] == 0


def test_majority_of_failed_lessons_fails_the_job(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    fail_for(monkeypatch, "LessonDraft")
    job = build(cid)
    assert job.state == "failed" and "lessons failed" in job.error
    assert next(s for s in job.stages if s.key == "lessons").status == "failed"
    assert db.query_one("SELECT status FROM courses WHERE id = ?", (cid,))["status"] == "failed"
    # the graph is kept and visible with statuses
    assert db.query_one("SELECT COUNT(*) AS n FROM progress WHERE course_id = ?", (cid,))["n"] == 6


def test_graph_failure_fails_the_job_and_resume_recovers(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    fail_for(monkeypatch, "KnowledgeGraphDraft")
    job = build(cid)
    assert job.state == "failed" and "Knowledge graph failed" in job.error
    assert next(s for s in job.stages if s.key == "graph").status == "failed"
    assert next(s for s in job.stages if s.key == "parse").status == "done"
    monkeypatch.undo()
    assert_complete(cid, build(cid))


def test_visual_failure_never_fails_the_course(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    fail_for(monkeypatch, "VisualizationPlan", lambda text: "Skill S3:" in text)
    job = build(cid)
    assert_complete(cid, job)
    viz = db.loads(db.query_one("SELECT viz_json FROM skills WHERE course_id = ? AND key = 'S3'", (cid,))["viz_json"])
    assert viz["format"] == "none" and "error" in viz
    assert "1 failed" in next(s for s in job.stages if s.key == "visuals").message
    s3 = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S3'", (cid,))["id"]
    assert lesson_view(s3).visualization.format == "none"
    monkeypatch.undo()
    assert_complete(cid, build(cid))
    viz = db.loads(db.query_one("SELECT viz_json FROM skills WHERE id = ?", (s3,))["viz_json"])
    assert "error" not in viz


def test_disagreements_are_flagged_but_every_kp_stays_servable(tmp_path, monkeypatch):
    original = fake_llm.respond

    def contrarian(**kwargs):
        if kwargs["schema"] is IndependentAnswer:
            return IndependentAnswer(choice=9, answer="12345", confidence=0.9, problems="")
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", contrarian)
    cid = tiny_course(tmp_path)
    job = build(cid)
    assert job.state == "done"
    for skill in db.query("SELECT id FROM skills WHERE course_id = ?", (cid,)):
        rows = db.query("SELECT * FROM questions WHERE skill_id = ?", (skill["id"],))
        for kp in {r["kp_index"] for r in rows}:
            pool = [r for r in rows if r["kp_index"] == kp]
            assert sum(1 for r in pool if not r["flagged"]) == 2
        flagged = [r for r in rows if r["flagged"]]
        assert flagged and all(r["flag_reason"].startswith("verification: independent solver answered") for r in flagged)
        kept = [db.loads(r["verify_json"]) for r in rows if not r["flagged"] and r["kind"] != "free_response"]
        assert all(k["status"] == "disagree_kept" for k in kept)
    message = next(s for s in job.stages if s.key == "verify").message
    assert "0/" in message and "kept despite disagreement" in message


def test_interrupted_jobs_are_marked_and_resumable(tmp_path):
    cid = tiny_course(tmp_path)
    db.execute("UPDATE jobs SET state = 'running' WHERE course_id = ?", (cid,))
    tracker = jobs.JobTracker.load(cid)
    tracker.update("render", status="running")
    assert jobs.mark_interrupted_jobs() == 1
    job = jobs.get_job(cid)
    assert job.state == "interrupted" and "Interrupted" in job.log[-1]
    assert "running" not in {s.status for s in job.stages}  # never left spinning
    assert db.query_one("SELECT status FROM courses WHERE id = ?", (cid,))["status"] == "failed"  # UI stops polling

    async def resume():
        status = jobs.resume_build(cid)
        assert status.state == "queued"
        while jobs.is_building(cid):
            await asyncio.sleep(0.05)

    asyncio.run(resume())
    assert_complete(cid, jobs.get_job(cid))


# ---------------------------------------------------------------------------
# Course creation, deletion, top-up
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [(-1, 2, "Invalid page range"), (2, 1, "Invalid page range"), (0, 3, "Invalid page range")],
)
def test_create_course_validates_the_range(tmp_path, start, end, message):
    book_id = insert_book(make_tiny_pdf(tmp_path / "tiny.pdf"))
    with pytest.raises(ValueError, match=message):
        jobs.create_course(CourseCreate(book_id=book_id, title="x", start_page=start, end_page=end))
    with pytest.raises(ValueError, match="does not exist"):
        jobs.create_course(CourseCreate(book_id="nope", title="x", start_page=0, end_page=0))


def test_create_course_limits_pages(axler_pdf):
    book_id = insert_book(axler_pdf)
    with pytest.raises(ValueError, match="at most"):
        jobs.create_course(CourseCreate(book_id=book_id, title="", start_page=0, end_page=200))
    cid = jobs.create_course(CourseCreate(book_id=book_id, title="", start_page=15, end_page=24))
    assert db.query_one("SELECT title FROM courses WHERE id = ?", (cid,))["title"] == "Pages 2–11"


def test_delete_course_removes_rows_and_files(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    course_dir = jobs.course_dir(cid)
    assert course_dir.exists()
    db.execute("UPDATE jobs SET state = 'running' WHERE course_id = ?", (cid,))
    with pytest.raises(RuntimeError):
        jobs.delete_course(cid)
    db.execute("UPDATE jobs SET state = 'done' WHERE course_id = ?", (cid,))
    jobs.delete_course(cid)
    assert not course_dir.exists()
    for table in ("skills", "edges", "foundations", "figures", "pages", "jobs", "progress"):
        assert db.query(f"SELECT * FROM {table} WHERE course_id = ?", (cid,)) == []
    assert db.query("SELECT * FROM questions") == []


def test_top_up_adds_verified_questions(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    skill = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S1'", (cid,))["id"]
    before = db.query("SELECT key FROM questions WHERE skill_id = ?", (skill,))
    added = asyncio.run(topup.top_up_questions(skill, kp_index=0, n=3))
    assert added == 3
    new = db.query(
        "SELECT * FROM questions WHERE skill_id = ? AND key NOT IN (%s)" % ",".join("?" * len(before)),
        (skill, *[b["key"] for b in before]),
    )
    assert [q["key"] for q in new] == ["k1q5", "k1q6", "k1q7"]
    assert all(q["kp_index"] == 0 and db.loads(q["verify_json"])["status"] == "agree" for q in new)
    assert lesson_view(skill).knowledge_points[0].question_count == 7


def test_request_top_up_deduplicates_in_flight_requests(tmp_path, llm_calls):
    cid = tiny_course(tmp_path)
    build(cid)
    llm_calls.clear()
    skill = db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = 'S2'", (cid,))["id"]

    async def main():
        topup.request_top_up(skill, 1)
        topup.request_top_up(skill, 1)
        while topup._in_flight:
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert llm_calls["TopUpDraft"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM questions WHERE skill_id = ? AND kp_index = 1", (skill,))["n"] == 8


# ---------------------------------------------------------------------------
# Regression tests (QA pass)
# ---------------------------------------------------------------------------


def _skill(cid: str, key: str) -> str:
    return db.query_one("SELECT id FROM skills WHERE course_id = ? AND key = ?", (cid, key))["id"]


def test_cancelled_build_leaves_no_running_stage_and_old_rows_are_repaired(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    started = asyncio.Event()

    async def hanging_graph(ctx):
        ctx.tracker.update("graph", status="running")
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs, "run_graph_stage", hanging_graph)

    async def main():
        jobs.start_build(cid)
        await started.wait()
        task = jobs._tasks[cid]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    job = jobs.get_job(cid)
    assert job.state == "interrupted"
    assert {s.key: s.status for s in job.stages}["graph"] == "failed"
    assert "running" not in {s.status for s in job.stages}
    assert db.query_one("SELECT status FROM courses WHERE id = ?", (cid,))["status"] == "failed"

    # a row written by an older version (interrupted, but a stage still 'running', course 'building') is repaired
    stages = [s.model_dump() | ({"status": "running"} if s.key == "lessons" else {}) for s in job.stages]
    db.execute("UPDATE jobs SET stages_json = ? WHERE course_id = ?", (db.dumps(stages), cid))
    db.execute("UPDATE courses SET status = 'building' WHERE id = ?", (cid,))
    assert jobs.mark_interrupted_jobs() == 0
    assert "running" not in {s.status for s in jobs.get_job(cid).stages}
    assert db.query_one("SELECT status FROM courses WHERE id = ?", (cid,))["status"] == "failed"

    monkeypatch.undo()
    assert_complete(cid, build(cid))  # resumable: back to building, then ready


def test_shutdown_cancels_builds_and_marks_them_interrupted(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    started = asyncio.Event()

    async def hanging_render(ctx):
        ctx.tracker.update("render", status="running")
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs, "run_render_stage", hanging_render)

    async def main():
        jobs.start_build(cid)
        await started.wait()
        await jobs.shutdown(timeout=2)
        assert not jobs.is_building(cid)

    asyncio.run(main())
    job = jobs.get_job(cid)
    assert job.state == "interrupted" and "running" not in {s.status for s in job.stages}


def test_verification_never_unflags_a_learner_flag(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = _skill(cid, "S1")
    q = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND kind = 'multiple_choice'", (sid,))
    # reported by the learner before verification ran (e.g. while the build was still verifying)
    db.execute(
        "UPDATE questions SET verify_json = '{}', flagged = 1, flag_reason = 'Learner: answer key is wrong' WHERE id = ?",
        (q["id"],),
    )
    assert_complete(cid, build(cid))  # resume
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (q["id"],))
    assert row["flagged"] == 1 and row["flag_reason"] == "Learner: answer key is wrong"

    # flagged by the learner (empty reason) while the verification call was in flight
    other = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND kind = 'numeric'", (sid,))
    db.execute("UPDATE questions SET verify_json = '{}' WHERE id = ?", (other["id"],))
    snapshot = db.query_one("SELECT * FROM questions WHERE id = ?", (other["id"],))
    db.execute("UPDATE questions SET flagged = 1, flag_reason = '' WHERE id = ?", (other["id"],))
    verdict = asyncio.run(verify.verify_question(snapshot))
    assert verdict is not None and not verdict.flagged
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (other["id"],))
    assert row["flagged"] == 1 and row["flag_reason"] == "" and db.loads(row["verify_json"])["status"] == "agree"

    # keep_kps_servable never restores it either, even when the KP runs short
    db.execute(
        "UPDATE questions SET flagged = 1, flag_reason = 'Learner: typo' WHERE skill_id = ? AND kp_index = 0", (sid,)
    )
    verify.keep_kps_servable(sid)
    assert all(r["flagged"] for r in db.query("SELECT flagged FROM questions WHERE skill_id = ? AND kp_index = 0", (sid,)))


def test_verifier_accepts_an_unsimplified_but_equal_answer(tmp_path):
    row = {
        "kind": "expression",
        "answer": "-7+22*i",
        "correct_choice": None,
        "tolerance": None,
        "choices_json": "[]",
    }
    independent = IndependentAnswer(choice=None, answer="(2+3*i)*(4+5*i)", confidence=0.9, problems="")
    assert not verify.judge(row, independent).flagged


def test_top_up_questions_are_held_until_verified(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = _skill(cid, "S1")
    before = {r["id"] for r in db.query("SELECT id FROM questions WHERE skill_id = ?", (sid,))}
    marks = ",".join("?" * len(before))
    seen_during_verification: list[dict] = []
    usage_during_call: list[llm.Usage | None] = []
    original = fake_llm.respond

    def gated(**kwargs):
        usage_during_call.append(llm.current_usage.get())
        if kwargs["schema"] is IndependentAnswer and not seen_during_verification:  # the first verifier call
            seen_during_verification.extend(
                db.query(f"SELECT * FROM questions WHERE skill_id = ? AND id NOT IN ({marks})", (sid, *before))
            )
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", gated)
    assert asyncio.run(topup.top_up_questions(sid, kp_index=0, n=3)) == 3
    assert seen_during_verification and all(
        r["flagged"] == 1 and r["flag_reason"] == "verification: pending" for r in seen_during_verification
    )
    assert all(isinstance(u, llm.Usage) for u in usage_during_call)  # top-up spend is attributed
    new = db.query(f"SELECT * FROM questions WHERE skill_id = ? AND id NOT IN ({marks})", (sid, *before))
    assert len(new) == 3 and not any(r["flagged"] for r in new)


def test_top_up_releases_questions_when_verification_fails(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = _skill(cid, "S1")
    fail_for(monkeypatch, "IndependentAnswer")
    assert asyncio.run(topup.top_up_questions(sid, kp_index=1, n=2)) == 2
    new = db.query("SELECT * FROM questions WHERE skill_id = ? AND kp_index = 1 AND verify_json = '{}'", (sid,))
    assert len(new) == 2 and not any(r["flagged"] for r in new)  # servable unverified, like the build


def test_concurrent_top_ups_do_not_duplicate_keys_or_questions(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = _skill(cid, "S1")

    async def twice():
        return await asyncio.gather(topup.top_up_questions(sid, 0), topup.top_up_questions(sid, 0))

    assert asyncio.run(twice()) == [3, 3]
    rows = db.query("SELECT key, prompt FROM questions WHERE skill_id = ?", (sid,))
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys))
    kp0 = db.query("SELECT prompt FROM questions WHERE skill_id = ? AND kp_index = 0", (sid,))
    prompts = [normalized_prompt(r["prompt"]) for r in kp0]
    assert len(prompts) == len(set(prompts)) == 4 + 6


def test_proof_skills_serve_a_free_response_by_the_second_question(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    for key in ("S2", "S6"):  # the fake graph's proof skills
        rows = db.query("SELECT kp_index, kind FROM questions WHERE skill_id = ? ORDER BY rowid", (_skill(cid, key),))
        for kp in {r["kp_index"] for r in rows}:
            kinds = [r["kind"] for r in rows if r["kp_index"] == kp]
            assert "free_response" in kinds[:2], (key, kp, kinds)


def test_proof_lesson_without_a_free_response_is_retried_with_feedback(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    original = fake_llm.respond
    texts: list[str] = []

    def no_proof_first(**kwargs):
        draft = original(**kwargs)
        if kwargs["schema"] is LessonDraft and "skill S2:" in kwargs["text"]:
            texts.append(kwargs["text"])
            if len(texts) == 1:
                kps = [kp.model_copy(update={"questions": [q for q in kp.questions if q.kind != "free_response"]}) for kp in draft.knowledge_points]
                return draft.model_copy(update={"knowledge_points": kps})
        return draft

    monkeypatch.setattr(fake_llm, "respond", no_proof_first)
    assert_complete(cid, build(cid))
    assert len(texts) == 2 and "rejected" in texts[1] and "free-response" in texts[1]


def test_proof_lesson_is_kept_with_a_warning_when_the_retry_also_lacks_a_proof(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    original = fake_llm.respond

    def never_proof(**kwargs):
        draft = original(**kwargs)
        if kwargs["schema"] is LessonDraft and "skill S2:" in kwargs["text"]:
            kps = [kp.model_copy(update={"questions": [q for q in kp.questions if q.kind != "free_response"]}) for kp in draft.knowledge_points]
            return draft.model_copy(update={"knowledge_points": kps})
        return draft

    monkeypatch.setattr(fake_llm, "respond", never_proof)
    job = build(cid)
    assert_complete(cid, job)
    assert any("Lesson S2" in line and "no free-response proof" in line for line in job.log)


def test_lessons_keep_item_refs_only_and_show_the_figures_they_reference(tmp_path):
    cid = tiny_course(tmp_path)
    build(cid)
    for skill in db.query("SELECT * FROM skills WHERE course_id = ?", (cid,)):
        lesson = lesson_view(skill["id"])
        assert lesson.source_refs == ["1.1", "1.3", "1.13"]
        placed = set(re.findall(r"\[\[figure:([^\]]+)\]\]", lesson.idea))
        assert placed <= {f.id for f in lesson.figures}  # every placed figure is shown, under the same (local) id
        assert all(not f.id.startswith(cid) for f in lesson.figures)
        if lesson.visualization and lesson.visualization.figure:
            assert not lesson.visualization.figure.id.startswith(cid)


def test_regenerating_a_lesson_retires_answered_questions(tmp_path):
    from app.pipeline.lessons import persist_lesson, validate_lesson

    cid = tiny_course(tmp_path)
    build(cid)
    sid = _skill(cid, "S1")
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, created_at) VALUES (?, ?, ?, 'lesson', 'completed', ?)",
        (task_id, cid, sid, db.now_iso()),
    )
    answered = db.query_one("SELECT id, key FROM questions WHERE skill_id = ? ORDER BY rowid", (sid,))
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, created_at) VALUES (?, ?, ?, ?, 1, 'correct', ?)",
        (db.new_id(), task_id, answered["id"], sid, db.now_iso()),
    )
    draft = fake_llm.respond(schema=LessonDraft, role="lesson", instructions="", text="skill S1: x")
    lesson, pools, _ = validate_lesson(draft)
    persist_lesson(sid, lesson, pools)  # would violate the attempts foreign key if the row were deleted
    old = db.query_one("SELECT * FROM questions WHERE id = ?", (answered["id"],))
    assert old["flagged"] == 1 and old["flag_reason"].startswith("retired") and old["key"] == f"old-{answered['key']}"
    live = db.query("SELECT key FROM questions WHERE skill_id = ? AND flagged = 0", (sid,))
    assert len(live) == sum(len(p) for p in pools)


def test_regenerate_in_place_keeps_learner_history(tmp_path, llm_calls):
    from app.pipeline.regenerate import regenerate

    cid = tiny_course(tmp_path)
    build(cid)
    s1, s3 = _skill(cid, "S1"), _skill(cid, "S3")
    answered = db.query_one("SELECT * FROM questions WHERE skill_id = ? ORDER BY rowid", (s1,))
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, state_json, created_at) "
        "VALUES (?, ?, ?, 'lesson', 'active', '{}', ?)",
        (task_id, cid, s1, db.now_iso()),
    )
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, created_at) VALUES (?, ?, ?, ?, 1, 'correct', ?)",
        (db.new_id(), task_id, answered["id"], s1, db.now_iso()),
    )
    reported = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND kind = 'numeric'", (s3,))
    db.execute("UPDATE questions SET flagged = 1, flag_reason = 'Learner: unclear' WHERE id = ?", (reported["id"],))
    llm_calls.clear()

    report = asyncio.run(regenerate(cid, ["S1"]))
    assert report.lessons_regenerated == ["S1"] and report.abandoned_tasks == 1
    assert llm_calls["LessonDraft"] == 1 and llm_calls["VisualizationPlan"] == 1
    assert db.query_one("SELECT status FROM tasks WHERE id = ?", (task_id,))["status"] != "active"
    old = db.query_one("SELECT * FROM questions WHERE id = ?", (answered["id"],))
    assert old["flagged"] == 1 and old["key"].startswith("old-")
    assert jobs.get_job(cid).state == "done"

    # verification only: learner flags survive, everything else is re-checked
    llm_calls.clear()
    report = asyncio.run(regenerate(cid, ["S3"], lessons=False, visuals=False))
    machine = db.query_one(
        "SELECT COUNT(*) AS n FROM questions WHERE skill_id = ? AND kind != 'free_response' AND flagged = 0", (s3,)
    )["n"]
    assert llm_calls["IndependentAnswer"] == machine and llm_calls["LessonDraft"] == 0
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (reported["id"],))
    assert row["flagged"] == 1 and row["flag_reason"] == "Learner: unclear"

    # a lesson whose regeneration fails is kept as it was
    before = db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (s3,))["lesson_json"]
    original = fake_llm.respond

    def failing(**kwargs):
        if kwargs["schema"] is LessonDraft:
            raise RuntimeError("simulated")
        return original(**kwargs)

    fake_llm.respond = failing
    try:
        report = asyncio.run(regenerate(cid, ["S3"]))
    finally:
        fake_llm.respond = original
    assert report.lessons_kept == ["S3"]
    row = db.query_one("SELECT lesson_status, lesson_json FROM skills WHERE id = ?", (s3,))
    assert row["lesson_status"] == "ready" and row["lesson_json"] == before
