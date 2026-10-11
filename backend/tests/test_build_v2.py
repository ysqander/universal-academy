"""The in-app build with lesson generation v2 (app/pipeline/build_v2.py): planned in waves along the graph, splits
applied, every lesson written, reviewed and verified as scripts/replan.py does, and a resume that rewrites a failed
lesson from its kept spec without planning it again."""

from __future__ import annotations

import asyncio

import pytest
from test_pipeline_build import assert_complete, build, tiny_course

from app import db
from app.config import settings
from app.pipeline import build_v2, jobs, replan


@pytest.fixture
def v2_build():
    before = settings.lesson_generation
    object.__setattr__(settings, "lesson_generation", "v2")  # Settings is frozen
    yield
    object.__setattr__(settings, "lesson_generation", before)


def keys(cid: str) -> dict[str, str]:
    return {r["id"]: r["key"] for r in db.query("SELECT id, key FROM skills WHERE course_id = ?", (cid,))}


def test_the_build_writes_v2_lessons_and_applies_the_planners_splits(tmp_path, v2_build):
    cid = tiny_course(tmp_path)
    graph_keys: list[str] = []
    original_plan = replan.plan_replan

    async def recording(course_id, key, **kw):
        graph_keys.append(key)
        return await original_plan(course_id, key, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_v2, "plan_replan", recording)
        job = build(cid)
    assert_complete(cid, job)
    skills = db.query("SELECT * FROM skills WHERE course_id = ?", (cid,))
    assert skills and all(s["lesson_status"] == "ready" for s in skills)
    assert all(db.loads(s["lesson_json"], {}).get("format") == "v2" for s in skills)
    ids = keys(cid)
    split = [k for k in graph_keys if k not in ids.values()]  # planned, then replaced by its parts
    assert split, "the fake planner splits a skill whose title joins two abilities"
    for k in split:
        assert {f"{k}a", f"{k}b", f"{k}c"} <= set(ids.values())
    for e in db.query("SELECT src, dst FROM edges WHERE course_id = ?", (cid,)):
        assert e["src"] in ids and e["dst"] in ids  # every edge points at a skill that exists after the splits
    assert not db.query("SELECT key FROM kv WHERE key LIKE 'v2_spec:%'")  # specs are dropped once a lesson is stored
    assert "lesson generation v2" in " ".join(job.log)


def test_a_resumed_build_rewrites_a_failed_lesson_from_its_kept_spec(tmp_path, v2_build):
    cid = tiny_course(tmp_path)
    original_write = build_v2.generate_v2_lesson
    failed: list[str] = []

    async def failing_once(ctx, skill_id, spec, prefix):
        if not failed:
            failed.append(skill_id)
            raise RuntimeError("writer unavailable")
        return await original_write(ctx, skill_id, spec, prefix)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_v2, "generate_v2_lesson", failing_once)
        first = build(cid)
    assert first.state == "done" and failed
    row = db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (failed[0],))
    assert row["lesson_status"] == "failed"
    assert build_v2.stored_spec(failed[0]) is not None

    planned_again: list[str] = []
    original_plan = replan.plan_replan

    async def recording(course_id, key, **kw):
        planned_again.append(key)
        return await original_plan(course_id, key, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_v2, "plan_replan", recording)
        second = build(cid)
    assert_complete(cid, second)
    assert db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (failed[0],))["lesson_status"] == "ready"
    assert planned_again == []  # written again from the kept spec, not planned (nor split) again
    assert build_v2.stored_spec(failed[0]) is None


def test_deleting_a_course_drops_its_kept_specs(tmp_path, v2_build):
    from app.pipeline import jobs

    cid = tiny_course(tmp_path)
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ?", (cid,))
    if sid is None:  # the graph is written by the build
        build(cid)
        sid = db.query_one("SELECT id FROM skills WHERE course_id = ?", (cid,))
    db.kv_set(build_v2.SPEC_KEY.format(sid["id"]), "{}")
    jobs.delete_course(cid)
    assert db.kv_get(build_v2.SPEC_KEY.format(sid["id"])) is None


def test_a_lesson_stored_before_a_crash_drops_its_spec(tmp_path, v2_build):
    cid = tiny_course(tmp_path)
    original_write = build_v2.generate_v2_lesson
    crashed: list[str] = []

    async def crash_after_storing(ctx, skill_id, spec, prefix):  # e.g. its verification crashed after the lesson was stored
        result = await original_write(ctx, skill_id, spec, prefix)
        if not crashed:
            crashed.append(skill_id)
            raise RuntimeError("verifier unavailable")
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_v2, "generate_v2_lesson", crash_after_storing)
        job = build(cid)
    assert_complete(cid, job)
    assert db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (crashed[0],))["lesson_status"] == "ready"
    assert build_v2.stored_spec(crashed[0]) is None


def test_an_interrupted_build_leaves_no_plan_or_lesson_running(tmp_path, v2_build):
    cid = tiny_course(tmp_path)
    original_plan = replan.plan_replan
    plans: list[str] = []
    writing: list[str] = []

    async def slow_after_the_first(course_id, key, **kw):
        plans.append(key)
        if len(plans) > 1:  # later plans are still running when the build is interrupted
            await asyncio.sleep(3600)
        return await original_plan(course_id, key, **kw)

    async def hanging(ctx, skill_id, spec, prefix):
        writing.append(skill_id)
        await asyncio.sleep(3600)

    async def interrupt() -> set[asyncio.Task]:
        task = asyncio.create_task(jobs.run_build(cid))
        while not (writing and len(plans) > 1):
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return asyncio.all_tasks() - {asyncio.current_task()}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_v2, "plan_replan", slow_after_the_first)
        mp.setattr(build_v2, "generate_v2_lesson", hanging)
        left = asyncio.run(interrupt())
    assert left == set()  # a writer left running would save this run's job row over a resumed run's
    assert jobs.get_job(cid).state == "interrupted"
    assert build_v2.stored_spec(writing[0]) is not None  # the resume writes that lesson from its kept spec


def test_a_replan_split_part_that_failed_is_rewritten_by_resume_without_a_new_split(tmp_path, v2_build):
    """scripts/replan.py keeps each part's spec until its lesson is stored, so the app's Resume (a v2 build) rewrites
    a failed part from it instead of planning (and maybe splitting) the part again."""
    import asyncio

    object.__setattr__(settings, "lesson_generation", "v1")  # the course's first build: v1 lessons
    cid = tiny_course(tmp_path)
    assert build(cid).state == "done"
    object.__setattr__(settings, "lesson_generation", "v2")
    key = db.query_one("SELECT key FROM skills WHERE course_id = ? AND title LIKE 'Add and multiply%'", (cid,))["key"]
    from app.pipeline import replan as replan_module

    original_write = replan_module.generate_v2_lesson
    failed: list[str] = []

    async def failing_part_b(ctx, skill_id, spec, prefix):
        if spec.local_id == "b" and not failed:
            failed.append(skill_id)
            raise RuntimeError("writer unavailable")
        return await original_write(ctx, skill_id, spec, prefix)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(replan_module, "generate_v2_lesson", failing_part_b)
        report = asyncio.run(replan_module.replan(cid, key))
    assert report.decision == "split" and failed
    part = failed[0]
    assert build_v2.stored_spec(part) is not None  # kept: its lesson was not stored
    assert all(build_v2.stored_spec(sid) is None for sid in report.skill_ids.values() if sid != part)

    planned: list[str] = []
    original_plan = replan.plan_replan

    async def recording(course_id, k, **kw):
        planned.append(k)
        return await original_plan(course_id, k, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_v2, "plan_replan", recording)
        resumed = build(cid)
    assert resumed.state == "done"
    assert db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (part,))["lesson_status"] == "ready"
    assert planned == [] and f"{key}b" in keys(cid).values() and not any(k.startswith(f"{key}b") and k != f"{key}b" for k in keys(cid).values())


def test_a_skill_is_planned_once_its_own_prerequisites_are_planned(tmp_path, v2_build):
    """No wave barrier: every skill is planned after all of its prerequisites (so it sees their splits)."""
    cid = tiny_course(tmp_path)
    order: list[str] = []
    original_plan = replan.plan_replan

    async def recording(course_id, key, **kw):
        order.append(key)
        return await original_plan(course_id, key, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_v2, "plan_replan", recording)
        assert build(cid).state == "done"
    ids = keys(cid)
    position = {k: i for i, k in enumerate(order)}
    for e in db.query("SELECT src, dst FROM edges WHERE course_id = ?", (cid,)):
        src, dst = ids[e["src"]], ids[e["dst"]]
        # a split part was never planned itself: compare through the key it came from (S2a → S2)
        src_planned = src if src in position else src.rstrip("abcde")
        dst_planned = dst if dst in position else dst.rstrip("abcde")
        if src_planned in position and dst_planned in position and src_planned != dst_planned:
            assert position[src_planned] < position[dst_planned], (src, dst, order)
