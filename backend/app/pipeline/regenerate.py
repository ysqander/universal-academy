"""Regenerate parts of an existing course in place (after prompt or pipeline fixes), keeping the learner's progress.

    await regenerate(course_id, keys=None, lessons=True, visuals=True, verify=True) -> RegenerateReport

- lessons: every selected lesson is rewritten; the new version replaces the old one only when it validates (a failed
  regeneration keeps the old lesson). Questions the learner already answered are retired (flagged, key 'old-…'), never
  deleted, so attempt history and FSRS state stay valid; active tasks on those skills are abandoned first because
  they point at the old questions. A regenerated lesson also gets a new visual and verification.
- visuals: the visual plan is redone.
- verify: machine-checkable questions are re-verified (learner flags and retired questions are left alone); they are
  held out of serving while the verifier runs.
Progress (status, FSRS cards, XP) is never touched. Runs in the caller's event loop; refuses while a build runs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .. import db, llm
from . import jobs
from .context import BuildContext, load_course, skill_rows
from .lessons import ensure_lesson, lesson_prefix
from .questions import PENDING_REASON
from .tracker import JobTracker
from .verify import verify_skill
from .visuals import ensure_visual

log = logging.getLogger("ua.pipeline.regenerate")


@dataclass
class RegenerateReport:
    skills: list[str] = field(default_factory=list)
    lessons_regenerated: list[str] = field(default_factory=list)
    lessons_kept: list[str] = field(default_factory=list)  # regeneration failed; the old lesson stays
    abandoned_tasks: int = 0
    cost_usd: float = 0.0


async def regenerate(
    course_id: str,
    keys: list[str] | None = None,
    *,
    lessons: bool = True,
    visuals: bool = True,
    verify: bool = True,
) -> RegenerateReport:
    course = load_course(course_id)
    if jobs.is_building(course_id):
        raise RuntimeError("The course is building; wait for the build to finish (or resume it) first.")
    selected = [s for s in skill_rows(course_id) if not keys or s["key"] in keys]
    unknown = sorted(set(keys or []) - {s["key"] for s in selected})
    if unknown:
        raise LookupError(f"Unknown skill keys: {', '.join(unknown)}")
    report = RegenerateReport(skills=[s["key"] for s in selected])
    jobs._active.add(course_id)  # no build may start in this process meanwhile
    usage = llm.Usage()
    token = llm.current_usage.set(usage)
    tracker = JobTracker.load(course_id)
    previous_state = tracker.state if tracker.state not in ("running", "queued") else "done"
    tracker.state = "running"  # the UI shows progress; another process sees the course as building
    ctx = BuildContext(course=course, tracker=tracker, usage=usage)
    parts = [name for name, on in (("lessons", lessons), ("visuals", visuals), ("verification", verify)) if on]
    tracker.log(f"Regenerating {', '.join(parts)} for {len(selected)} skill{'s' * (len(selected) != 1)}")
    try:
        ids = [s["id"] for s in selected]
        if lessons:
            report.abandoned_tasks = _abandon_active_tasks(ids)
        if visuals:
            _execute_in("UPDATE skills SET viz_json = NULL WHERE id IN ({marks})", ids)
        prefix = lesson_prefix(course) if lessons else ""

        async def one(skill: dict) -> None:
            regenerated = False
            if lessons:
                regenerated = await ensure_lesson(ctx, skill["id"], prefix, force=True)
                if regenerated:
                    report.lessons_regenerated.append(skill["key"])
                    db.execute("UPDATE skills SET viz_json = NULL WHERE id = ?", (skill["id"],))
                elif skill["lesson_status"] == "ready":
                    report.lessons_kept.append(skill["key"])
            if regenerated or visuals:
                await ensure_visual(ctx, skill["id"])
            if regenerated or verify:
                if not regenerated:  # a regenerated lesson's questions are new and unverified anyway
                    _hold_for_verification([skill["id"]])
                for note in await verify_skill(skill["id"]):
                    tracker.log(f"Verify {skill['key']}: {note}")
                _release_unverified(skill["id"])

        if selected:
            await one(selected[0])  # the first lesson call writes the prompt cache for the others
            await asyncio.gather(*(one(s) for s in selected[1:]))
        jobs.refresh_pipeline_counters(ctx)
        report.cost_usd = usage.cost_usd
        tracker.log(f"Regeneration finished · {jobs._cost_summary(usage)}")
    finally:
        tracker.finish(previous_state, tracker.error)
        llm.current_usage.reset(token)
        jobs._active.discard(course_id)
    return report


def _execute_in(sql: str, ids: list[str]) -> None:
    if ids:
        db.execute(sql.format(marks=",".join("?" * len(ids))), tuple(ids))


def _abandon_active_tasks(skill_ids: list[str]) -> int:
    """Active tasks on these skills point at questions that are about to be replaced."""
    if not skill_ids:
        return 0
    from ..learning import engine

    marks = ",".join("?" * len(skill_ids))
    rows = db.query(f"SELECT id FROM tasks WHERE status = 'active' AND skill_id IN ({marks})", tuple(skill_ids))
    now = datetime.now(UTC)
    for row in rows:
        try:
            engine.abandon(row["id"], now)
        except Exception:  # noqa: BLE001 — fall back to closing the task directly
            log.exception("abandoning task %s failed; closing it directly", row["id"])
            db.execute(
                "UPDATE tasks SET status = 'completed', phase = 'done', outcome = 'abandoned', completed_at = ? "
                "WHERE id = ?",
                (db.now_iso(), row["id"]),
            )
    return len(rows)


def _hold_for_verification(skill_ids: list[str]) -> None:
    """Re-verify machine-checkable questions: out of serving until the verifier answers. Learner flags and retired
    questions are left alone."""
    _execute_in(
        "UPDATE questions SET verify_json = '{{}}', flagged = 1, flag_reason = '" + PENDING_REASON + "' "
        "WHERE skill_id IN ({marks}) AND kind != 'free_response' "
        "AND NOT (flagged = 1 AND flag_reason NOT LIKE 'verification:%')",
        skill_ids,
    )


def _release_unverified(skill_id: str) -> None:
    """Questions whose verification call failed become servable unverified, as in the build."""
    db.execute(
        "UPDATE questions SET flagged = 0, flag_reason = '' WHERE skill_id = ? AND flag_reason = ? AND verify_json = '{}'",
        (skill_id, PENDING_REASON),
    )
