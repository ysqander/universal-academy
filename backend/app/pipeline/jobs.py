"""Build jobs (SPEC §4): create a course, run or resume its build in the background, report and clean up.

Public interface:
    create_course(req) -> course_id          ValueError (→ 400) for a bad book/page range or a missing/damaged PDF
    start_build(course_id)                    background task (needs a running event loop); no-op while one runs
    await run_build(course_id) -> JobStatus   the whole pipeline; idempotent and resumable
    resume_build(course_id) -> JobStatus      LookupError for an unknown course; ValueError (→ 400) while render or
                                              parse is left and the PDF is missing or damaged; also re-runs only the failed lessons
                                              (+ their visuals/verification) of a ready course
    get_job(course_id) -> JobStatus | None
    is_building(course_id) -> bool
    mark_interrupted_jobs() -> int            on startup: running/queued → interrupted (+ stale verification holds
                                              released, see topup.release_stale_holds)
    await shutdown()                          on server shutdown: cancel builds/top-ups, mark them interrupted
    delete_course(course_id)                  rows + files; RuntimeError while building

An interrupted build (server stopped, task cancelled) never stays 'running': its job becomes 'interrupted', stages
that were running become 'failed', and a course that was still 'building' becomes 'failed' (resumable; the course
returns to 'building' when the resumed run starts).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time

from .. import db, llm
from ..config import settings
from ..ingest.pdf import locate_book_pdf
from ..learning import state
from ..schemas import CourseCreate, JobStatus
from ..views import label_of
from . import topup
from .context import BuildContext, BuildError, course_dir, load_course, skill_rows
from .graph import run_graph_stage
from .lessons import ensure_lesson, lesson_prefix
from .pages import run_parse_stage, run_render_stage
from .tracker import JobTracker, status_from_row
from .verify import verify_skill
from .visuals import ensure_visual

log = logging.getLogger("ua.pipeline")

MAX_FAILED_LESSON_SHARE = 0.5
PIPELINE_STAGES = ("lessons", "visuals", "verify")

_tasks: dict[str, asyncio.Task] = {}  # course_id → its build task (strong refs: the loop only keeps weak ones)
_active: set[str] = set()  # courses whose run_build is executing in this process


# ---------------------------------------------------------------------------
# Course + job lifecycle
# ---------------------------------------------------------------------------


def create_course(req: CourseCreate) -> str:
    book = db.query_one("SELECT id, title, page_count, page_labels_json, pdf_path FROM books WHERE id = ?", (req.book_id,))
    if book is None:
        raise ValueError(f"Book {req.book_id} does not exist.")
    if not locate_book_pdf(book["id"], book["pdf_path"], book["page_count"])[1]:
        raise ValueError(f"The PDF of “{book['title']}” is missing or damaged in the books folder. Upload the same file again to restore it.")
    if not 0 <= req.start_page <= req.end_page < book["page_count"]:
        raise ValueError(
            f"Invalid page range {req.start_page}–{req.end_page}: the book has pages 0–{book['page_count'] - 1} "
            "and the start must not be after the end."
        )
    n_pages = req.end_page - req.start_page + 1
    if n_pages > settings.max_course_pages:
        raise ValueError(
            f"{n_pages} pages selected; a course can have at most {settings.max_course_pages} pages "
            "(UA_MAX_COURSE_PAGES). Select a single chapter or a few sections."
        )
    labels = db.loads(book["page_labels_json"], [])
    title = req.title.strip() or (
        ", ".join(req.sections[:2])
        if req.sections
        else f"Pages {label_of(labels, req.start_page)}–{label_of(labels, req.end_page)}"
    )
    course_id = db.new_id()
    db.execute(
        "INSERT INTO courses(id, book_id, title, start_page, end_page, sections_json, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'building', ?)",
        (course_id, req.book_id, title, req.start_page, req.end_page, db.dumps(req.sections), db.now_iso()),
    )
    tracker = JobTracker.create(course_id)
    tracker.log(f"Course created: {title} ({n_pages} pages)")
    return course_id


def start_build(course_id: str) -> None:
    """Run the build as a background task of the current event loop (no-op if this course is already building)."""
    existing = _tasks.get(course_id)
    if course_id in _active or (existing is not None and not existing.done()):
        return
    loop = asyncio.get_running_loop()
    topup.remember_loop(loop)
    task = loop.create_task(run_build(course_id), name=f"build-{course_id}")
    _tasks[course_id] = task
    task.add_done_callback(lambda t: _forget_task(course_id, t))


def _forget_task(course_id: str, task: asyncio.Task) -> None:
    if _tasks.get(course_id) is task:
        del _tasks[course_id]
    if not task.cancelled() and task.exception() is not None:
        log.error("build task for %s crashed", course_id, exc_info=task.exception())


def is_building(course_id: str) -> bool:
    if course_id in _active or (course_id in _tasks and not _tasks[course_id].done()):
        return True
    row = db.query_one("SELECT state FROM jobs WHERE course_id = ?", (course_id,))
    return row is not None and row["state"] in ("running", "queued")


def get_job(course_id: str) -> JobStatus | None:
    row = db.query_one("SELECT * FROM jobs WHERE course_id = ?", (course_id,))
    return status_from_row(row) if row else None


def resume_build(course_id: str) -> JobStatus:
    """ValueError when the stages still to run need the book's PDF and it is missing."""
    course = load_course(course_id)
    if not (course_id in _active or (course_id in _tasks and not _tasks[course_id].done())):
        tracker = JobTracker.load(course_id)
        needs_pdf = any(stage.status != "done" for key, stage in tracker.stages.items() if key in ("render", "parse"))
        book = db.query_one("SELECT pdf_path, page_count FROM books WHERE id = ?", (course.book_id,))
        if needs_pdf and not locate_book_pdf(course.book_id, book["pdf_path"], book["page_count"])[1]:
            raise ValueError(f"The PDF of “{course.book_title}” is missing or damaged in the books folder. Upload the same file again to restore it.")
        tracker.state = "queued"
        tracker.log("Resume requested — finished work is kept, the rest is redone.")
        start_build(course_id)
    job = get_job(course_id)
    assert job is not None
    return job


INTERRUPTED_LINE = "Interrupted — the server stopped during the build. Resume to continue."
NOTHING_TO_RESUME_LINE = "Nothing left to resume — every stage has finished."


def pipeline_complete(course_id: str) -> bool:
    """Nothing is left for a resume to do: the course is ready, every stage of its job finished, every lesson is
    written, every visual planned and every machine-checkable question verified."""
    course = db.query_one("SELECT status FROM courses WHERE id = ?", (course_id,))
    row = db.query_one("SELECT stages_json FROM jobs WHERE course_id = ?", (course_id,))
    if course is None or row is None or course["status"] != "ready":
        return False
    stages = db.loads(row["stages_json"], [])
    if not stages or any(s.get("status") != "done" for s in stages):
        return False
    c = _pipeline_counts(course_id)
    return c["failed"] == 0 and c["ready"] == c["skills"] and c["visuals"] == c["ready"] and c["verified"] == c["questions"]


def settled_state(course_id: str, state: str) -> str:
    """The state to leave a job in after maintenance (replan, regenerate): an 'interrupted' it inherited becomes
    'done' when the course has nothing left to resume, so the course page stops offering a resume."""
    return "done" if state == "interrupted" and pipeline_complete(course_id) else state


def _mark_interrupted(tracker: JobTracker, line: str | None) -> None:
    """Stop showing work in progress: running stages → failed, job → interrupted, a building course → failed."""
    for stage in tracker.stages.values():
        if stage.status == "running":
            stage.status = "failed"
    if line:
        tracker.log(line, level=logging.WARNING)
    tracker.finish("interrupted")
    db.execute("UPDATE courses SET status = 'failed' WHERE id = ? AND status = 'building'", (tracker.course_id,))


def mark_interrupted_jobs(
    *, force: bool = False, only: set[str] | None = None, line: str = INTERRUPTED_LINE
) -> int:
    """On startup (and shutdown): builds left running/queued become 'interrupted' (resumable). Jobs already marked
    interrupted but still showing a running stage or a 'building' course are repaired too. Builds executing in this
    process are skipped unless force=True; `only` limits the pass to these courses. Returns the number of jobs newly
    marked."""
    try:
        topup.remember_loop(asyncio.get_running_loop())
    except RuntimeError:
        pass
    rows = db.query(
        "SELECT j.course_id, j.state, j.stages_json, c.status AS course_status FROM jobs j "
        "JOIN courses c ON c.id = j.course_id WHERE j.state IN ('running', 'queued', 'interrupted')"
    )
    count = 0
    for row in rows:
        if (row["course_id"] in _active and not force) or (only is not None and row["course_id"] not in only):
            continue
        if row["state"] == "interrupted":
            stale = any(s.get("status") == "running" for s in db.loads(row["stages_json"], []))
            if stale or row["course_status"] == "building":
                _mark_interrupted(JobTracker.load(row["course_id"]), None)
            elif pipeline_complete(row["course_id"]):  # e.g. left by maintenance that finished the work later
                tracker = JobTracker.load(row["course_id"])
                tracker.log(NOTHING_TO_RESUME_LINE)
                tracker.finish("done")
            continue
        _mark_interrupted(JobTracker.load(row["course_id"]), line)
        count += 1
    if count:
        log.info("marked %d unfinished build(s) as interrupted", count)
    if only is None and not force:  # startup: questions an interrupted top-up (or regeneration) left held
        try:
            topup.release_stale_holds()
        except Exception:  # noqa: BLE001 — never block the start over this
            log.exception("releasing stale verification holds failed")
    return count


async def shutdown(timeout: float = 5.0) -> None:
    """Server shutdown (lifespan exit): cancel running builds and top-ups, give them a moment to record the
    interruption, then mark whatever is still running as interrupted."""
    ours = set(_active) | set(_tasks)
    tasks = [t for t in _tasks.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
    await topup.shutdown(timeout)
    if ours:  # only builds of this process: another process (e.g. scripts/regenerate.py) may be running its own
        mark_interrupted_jobs(
            force=True, only=ours, line="Interrupted — the server shut down during the build. Resume to continue."
        )


def delete_course(course_id: str) -> None:
    """Delete a course with all its rows and files. RuntimeError while it is building."""
    load_course(course_id)
    if is_building(course_id):
        raise RuntimeError("The course is still building; wait for it to finish before deleting it.")
    skills = "(SELECT id FROM skills WHERE course_id = ?)"
    with db.connect() as conn:
        conn.execute(
            f"DELETE FROM attempts WHERE skill_id IN {skills} OR task_id IN (SELECT id FROM tasks WHERE course_id = ?)",
            (course_id, course_id),
        )
        conn.execute("DELETE FROM tasks WHERE course_id = ?", (course_id,))
        conn.execute(f"DELETE FROM review_log WHERE course_id = ? OR skill_id IN {skills}", (course_id, course_id))
        conn.execute("DELETE FROM xp_log WHERE course_id = ?", (course_id,))
        conn.execute(f"DELETE FROM progress WHERE course_id = ? OR skill_id IN {skills}", (course_id, course_id))
        conn.execute(f"DELETE FROM questions WHERE skill_id IN {skills}", (course_id,))
        for table in ("edges", "skills", "foundations", "figures", "pages", "jobs"):
            conn.execute(f"DELETE FROM {table} WHERE course_id = ?", (course_id,))
        conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
    shutil.rmtree(course_dir(course_id), ignore_errors=True)
    log.info("deleted course %s", course_id)


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


async def run_build(course_id: str) -> JobStatus:
    """Run every stage, skipping finished work. Never raises for build failures: they end up in the job row."""
    if course_id in _active:
        log.warning("build of %s already running; not starting a second one", course_id)
        job = get_job(course_id)
        assert job is not None
        return job
    course = load_course(course_id)
    _active.add(course_id)
    usage = llm.Usage()
    token = llm.current_usage.set(usage)
    started = time.monotonic()
    tracker = JobTracker.load(course_id)
    try:
        ctx = BuildContext(course=course, tracker=tracker, usage=usage)
        resumed = any(s.status == "done" for s in tracker.stages.values())
        tracker.begin_run()
        db.execute("UPDATE courses SET status = 'building' WHERE id = ? AND status != 'ready'", (course_id,))
        tracker.log("Build resumed" if resumed else f"Build started — {len(course.pages)} pages")
        await run_render_stage(ctx)
        await run_parse_stage(ctx)
        await run_graph_stage(ctx)
        await run_skill_pipeline(ctx)
        finalize(ctx)
        tracker.log(f"Build finished in {_duration(time.monotonic() - started)} · {_cost_summary(usage)}")
        tracker.finish("done")
    except asyncio.CancelledError:
        _mark_interrupted(tracker, "Interrupted — resume to continue.")
        raise
    except Exception as e:  # noqa: BLE001 — every failure is reported through the job row
        if not isinstance(e, BuildError):
            log.exception("build of %s crashed", course_id)
        _fail(tracker, course_id, str(e) if isinstance(e, BuildError) else f"Unexpected error: {e}", usage)
    finally:
        llm.current_usage.reset(token)
        _active.discard(course_id)
    return tracker.to_status()


def _fail(tracker: JobTracker, course_id: str, message: str, usage: llm.Usage) -> None:
    for stage in tracker.stages.values():
        if stage.status == "running":
            stage.status = "failed"
    tracker.log(f"Build failed: {message} · {_cost_summary(usage)}", level=logging.ERROR)
    tracker.finish("failed", message)
    db.execute("UPDATE courses SET status = 'failed' WHERE id = ? AND status != 'ready'", (course_id,))


async def run_skill_pipeline(ctx: BuildContext) -> None:
    """Lessons → visuals → verification per skill, without a barrier between stages: a skill's visual and
    verification start as soon as its lesson is stored. The first lesson runs alone to write the prompt cache."""
    course, tracker = ctx.course, ctx.tracker
    skills = skill_rows(course.id)
    for key in PIPELINE_STAGES:
        tracker.update(key, status="running", save=False)
    refresh_pipeline_counters(ctx)
    before = _pipeline_counts(course.id)
    has_work = (
        before["ready"] < before["skills"]
        or before["visuals"] < before["ready"]
        or before["visual_errors"] > 0
        or before["verified"] < before["questions"]
    )
    prefix = lesson_prefix(course)
    pending = [s for s in skills if s["lesson_status"] != "ready"]
    attempted: set[str] = set()
    if pending:
        tracker.log(f"Writing {len(pending)} lesson{'s' * (len(pending) != 1)}…")
        attempted.add(pending[0]["id"])
        await ensure_lesson(ctx, pending[0]["id"], prefix)
        refresh_pipeline_counters(ctx)
    results = await asyncio.gather(
        *(_process_skill(ctx, s["id"], prefix, write_lesson=s["id"] not in attempted) for s in skills),
        return_exceptions=True,
    )
    for skill, result in zip(skills, results):
        if isinstance(result, BaseException):
            log.error("pipeline for skill %s crashed", skill["key"], exc_info=result)
            tracker.log(f"Skill {skill['key']}: unexpected error {result}", level=logging.ERROR)
    _close_pipeline_stages(ctx, report=has_work)


async def _process_skill(ctx: BuildContext, skill_id: str, prefix: str, *, write_lesson: bool) -> None:
    if write_lesson:
        ready = await ensure_lesson(ctx, skill_id, prefix)
        refresh_pipeline_counters(ctx)
    else:
        ready = db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (skill_id,))["lesson_status"] == "ready"
    if not ready:
        return

    async def visual() -> None:
        await ensure_visual(ctx, skill_id)
        refresh_pipeline_counters(ctx)

    async def verification() -> None:
        notes = await verify_skill(skill_id)
        key = db.query_one("SELECT key FROM skills WHERE id = ?", (skill_id,))["key"]
        for note in notes:
            ctx.tracker.log(f"Verify {key}: {note}")
        refresh_pipeline_counters(ctx)

    await asyncio.gather(visual(), verification())


def _pipeline_counts(course_id: str) -> dict[str, int]:
    skills = db.query_one(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(lesson_status = 'ready'), 0) AS ready, "
        "COALESCE(SUM(lesson_status = 'failed'), 0) AS failed, "
        "COALESCE(SUM(lesson_status = 'ready' AND viz_json IS NOT NULL), 0) AS visuals, "
        "COALESCE(SUM(viz_json LIKE '%\"error\":%'), 0) AS visual_errors "
        "FROM skills WHERE course_id = ?",
        (course_id,),
    )
    questions = db.query_one(
        "SELECT COUNT(*) AS total, COALESCE(SUM(q.verify_json != '{}'), 0) AS verified, "
        "COALESCE(SUM(q.flagged), 0) AS flagged, "
        "COALESCE(SUM(json_extract(q.verify_json, '$.status') IN ('agree', 'agree_on_recheck')), 0) AS agree, "
        "COALESCE(SUM(json_extract(q.verify_json, '$.status') = 'agree_on_recheck'), 0) AS rechecked, "
        "COALESCE(SUM(json_extract(q.verify_json, '$.status') = 'disagree_kept'), 0) AS kept "
        "FROM questions q JOIN skills s ON s.id = q.skill_id "
        "WHERE s.course_id = ? AND q.kind != 'free_response' "
        # questions flagged by the learner (or retired) are out of verification's hands
        "AND NOT (q.flagged = 1 AND q.flag_reason NOT LIKE 'verification:%')",
        (course_id,),
    )
    return {
        "skills": skills["total"],
        "ready": skills["ready"],
        "failed": skills["failed"],
        "visuals": skills["visuals"],
        "visual_errors": skills["visual_errors"],
        "questions": questions["total"],
        "verified": questions["verified"],
        "flagged": questions["flagged"],
        "agree": questions["agree"],
        "rechecked": questions["rechecked"],
        "kept": questions["kept"],
    }


def refresh_pipeline_counters(ctx: BuildContext) -> None:
    """Recompute lessons/visuals/verify counters from the DB (accurate across resumes and concurrent workers)."""
    c = _pipeline_counts(ctx.course.id)
    tracker = ctx.tracker
    lessons_msg = f"{c['ready']} ready" + (f" · {c['failed']} failed" if c["failed"] else "")
    lessons_finished = c["ready"] + c["failed"] == c["skills"]
    visuals_finished = lessons_finished and c["visuals"] == c["ready"]
    tracker.update(
        "lessons",
        done=c["ready"] + c["failed"],
        total=c["skills"],
        message=lessons_msg,
        status="done" if lessons_finished else None,
        save=False,
    )
    tracker.update(
        "visuals",
        done=c["visuals"],
        total=c["skills"] - c["failed"],
        message=f"{c['visuals']} planned",
        status="done" if visuals_finished else None,
        save=False,
    )
    tracker.update(
        "verify",
        done=c["verified"],
        total=c["questions"],
        message=f"{c['verified']} checked · {c['flagged']} flagged",
        save=False,
    )
    tracker.save()


def _close_pipeline_stages(ctx: BuildContext, *, report: bool) -> None:
    refresh_pipeline_counters(ctx)
    c = _pipeline_counts(ctx.course.id)
    tracker = ctx.tracker
    lesson_failure = c["skills"] and c["failed"] / c["skills"] > MAX_FAILED_LESSON_SHARE
    tracker.update("lessons", status="failed" if lesson_failure else "done", save=False)
    visuals_msg = f"{c['visuals']} planned" + (f" · {c['visual_errors']} failed" if c["visual_errors"] else "")
    tracker.update("visuals", status="done", message=visuals_msg, save=False)
    unchecked = c["questions"] - c["verified"]
    verify_msg = f"{c['agree']}/{c['questions']} agree · {c['flagged']} flagged"
    if c["rechecked"]:
        verify_msg += f" · {c['rechecked']} agreed on a careful re-check"
    if c["kept"]:
        verify_msg += f" · {c['kept']} kept despite disagreement"
    if unchecked:
        verify_msg += f" · {unchecked} not checked"
    tracker.update("verify", status="done", message=verify_msg)
    if report:
        lessons_msg = f"{c['ready']} ready" + (f" · {c['failed']} failed" if c["failed"] else "")
        tracker.log(f"Lessons: {lessons_msg} · ${ctx.cost('lesson'):.2f}")
        tracker.log(f"Visuals: {visuals_msg} · ${ctx.cost('viz'):.2f}")
        tracker.log(f"Verification: {verify_msg} · ${ctx.cost('verify'):.2f}")
    if lesson_failure:
        raise BuildError(f"{c['failed']} of {c['skills']} lessons failed.")


def finalize(ctx: BuildContext) -> None:
    tracker = ctx.tracker
    tracker.update("finalize", status="running", done=0, total=1)
    state.init_progress(ctx.course.id)
    db.execute("UPDATE courses SET status = 'ready' WHERE id = ?", (ctx.course.id,))
    tracker.update("finalize", status="done", done=1, total=1, message="Course ready")


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _cost_summary(usage: llm.Usage) -> str:
    parts = ", ".join(f"{role} ${cost:.2f}" for role, cost in usage.by_role.items())
    return f"${usage.cost_usd:.2f} total" + (f" ({parts})" if parts else "")
