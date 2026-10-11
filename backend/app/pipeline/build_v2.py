"""The in-app build's lessons with lesson generation v2 (2026-10-11; before, the Build course button wrote v1 lessons
and v2 ran only from scripts/replan.py — docs/DECISIONS.md §1).

Each skill of the graph is planned (the v2 planner may split it into 2–5 skills), then written, reviewed, revised,
validated, stored and verified by lesson_v2.generate_v2_lesson — the same steps as scripts/replan.py.

Planning follows the graph: a skill is planned as soon as every one of its prerequisites has been planned, so a
split's new keys and edges are in place when its dependents are planned (their planner sees the final course map).
Independent skills plan in parallel (no wave waits for its slowest plan); splits are applied one at a time. A lesson
starts writing as soon as its plan is applied, while other skills are still planning. The spec each lesson is written from is kept (kv `v2_spec:<skill id>`) until the
lesson is stored, so a resumed build rewrites a failed or interrupted lesson without planning it again (a split part
is never split again).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .. import db
from ..llm_schemas import TeachingSpec
from .context import BuildContext, load_course, skill_rows
from .lesson_v2 import SPEC_KEY, generate_v2_lesson
from .lessons import lesson_prefix
from .replan import LESSON_CONCURRENCY, apply_split, check_plan, plan_replan

log = logging.getLogger("ua.pipeline.build_v2")

PLAN_CONCURRENCY = 4


def stored_spec(skill_id: str) -> TeachingSpec | None:
    raw = db.kv_get(SPEC_KEY.format(skill_id))
    if not raw:
        return None
    try:
        return TeachingSpec.model_validate_json(raw)
    except ValueError:
        return None


async def run_v2_lessons(ctx: BuildContext, refresh: Callable[[], None]) -> None:
    """Plan and write a v2 lesson for every skill without a ready lesson. Never raises for one skill's failure: that
    lesson is marked failed (Resume retries it)."""
    course_id, tracker = ctx.course.id, ctx.tracker
    plan_slots = asyncio.Semaphore(PLAN_CONCURRENCY)
    write_slots = asyncio.Semaphore(LESSON_CONCURRENCY)
    split_lock = asyncio.Lock()
    writers: list[asyncio.Task] = []
    settled: set[str] = set()  # skills whose plan is applied (their keys and edges are final)
    started: set[str] = set()  # skills planned or writing in this run

    def fail(skill_id: str, key: str, message: str) -> None:
        tracker.log(f"Lesson {key} (v2): {message}", level=logging.ERROR)
        db.execute("UPDATE skills SET lesson_status = 'failed' WHERE id = ? AND lesson_status != 'ready'", (skill_id,))
        refresh()

    def start_writer(skill_id: str, key: str, spec: TeachingSpec) -> None:
        db.kv_set(SPEC_KEY.format(skill_id), spec.model_dump_json())
        settled.add(skill_id)

        async def write() -> None:
            async with write_slots:
                try:
                    await generate_v2_lesson(ctx, skill_id, spec, lesson_prefix(load_course(course_id)))
                except Exception as e:  # noqa: BLE001 — one lesson's failure never stops the build
                    log.exception("v2 lesson %s crashed", key)
                    stored = db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (skill_id,))
                    if stored is not None and stored["lesson_status"] == "ready":  # stored; a later step crashed
                        tracker.log(f"Lesson {key} (v2): stored, then {e} (verification is retried)", level=logging.WARNING)
                    else:
                        fail(skill_id, key, f"unexpected error {e}")
                # the spec goes once the lesson is stored, also when its verification crashed afterwards (the
                # build's verification pass retries that); a failed lesson keeps it for the resume
                row = db.query_one("SELECT lesson_status FROM skills WHERE id = ?", (skill_id,))
                if row is None or row["lesson_status"] == "ready":
                    db.execute("DELETE FROM kv WHERE key = ?", (SPEC_KEY.format(skill_id),))
                refresh()

        writers.append(asyncio.create_task(write()))

    async def plan_one(row: dict) -> None:
        skill_id, key = row["id"], row["key"]
        spec = stored_spec(skill_id)
        if spec is not None:  # planned in an earlier run: write it again from the same spec
            start_writer(skill_id, key, spec)
            return
        async with plan_slots:
            try:
                planned = await plan_replan(course_id, key)
            except Exception as e:  # noqa: BLE001
                log.exception("v2 plan %s failed", key)
                settled.add(skill_id)
                fail(skill_id, key, f"planning failed: {e}")
                return
        for note in planned.notes:
            tracker.log(f"Plan {key}: {note}", level=logging.WARNING)
        plan = planned.plan
        if plan.decision == "split":
            async with split_lock:
                try:
                    checked = check_plan(course_id, key, plan)  # against the graph as it is now (earlier splits)
                    keys, ids, notes, _ = apply_split(course_id, key, checked.plan)
                except Exception as e:  # noqa: BLE001 — SplitRefused, an invalid plan, a vanished skill
                    tracker.log(f"Split {key} not applied ({e}); written as one lesson", level=logging.WARNING)
                else:
                    tracker.log(f"Split {key} into {', '.join(keys.values())}")
                    for note in notes:
                        tracker.log(f"Split {key}: {note}", level=logging.WARNING)
                    specs = {s.local_id: s for s in checked.plan.skills}
                    settled.add(skill_id)
                    for local, new_id in ids.items():
                        started.add(new_id)
                        start_writer(new_id, keys[local], specs[local])
                    refresh()
                    return
            async with plan_slots:  # the split could not be applied: plan the skill as kept
                try:
                    planned = await plan_replan(course_id, key, no_split=True)
                except Exception as e:  # noqa: BLE001
                    settled.add(skill_id)
                    fail(skill_id, key, f"planning failed: {e}")
                    return
            plan = planned.plan
        start_writer(skill_id, key, plan.skills[0])

    planners: dict[asyncio.Task, str] = {}  # running plans → their skill
    try:
        announced = False
        while True:
            rows = skill_rows(course_id)
            settled |= {r["id"] for r in rows if r["lesson_status"] == "ready"}
            pending = [r for r in rows if r["lesson_status"] != "ready" and r["id"] not in started]
            if not pending and not planners:
                break
            if pending and not announced:
                n = len(pending)
                tracker.log(f"Planning and writing {n} lesson{'s' * (n != 1)} (lesson generation v2)…")
                announced = True
            prereqs: dict[str, set[str]] = {}
            for e in db.query("SELECT src, dst FROM edges WHERE course_id = ?", (course_id,)):
                prereqs.setdefault(e["dst"], set()).add(e["src"])
            # a skill whose prerequisites are planned, or one planned in an earlier run (its keys are final)
            ready = [r for r in pending if prereqs.get(r["id"], set()) <= settled or stored_spec(r["id"]) is not None]
            if not ready and not planners:  # a prerequisite never settles (a cycle): plan the rest as the graph is
                ready = pending
            for r in ready:
                started.add(r["id"])
                planners[asyncio.create_task(plan_one(r))] = r["id"]
            done, _ = await asyncio.wait(planners, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                del planners[task]
                task.result()  # plan_one reports a skill's failure itself; anything else ends the build
        if writers:
            await asyncio.gather(*writers)
    finally:
        # Interrupted (shutdown, Ctrl-C) or crashed: no plan or lesson outlives the build. A writer left running would
        # keep saving this run's job row over a resumed run's, and the resume would write the same lesson again.
        unfinished = [t for t in (*planners, *writers) if not t.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)
