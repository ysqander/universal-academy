"""Regenerate parts of an existing course in place (after prompt or pipeline fixes), keeping the learner's progress.

    await regenerate(course_id, keys=None, lessons=True, visuals=True, verify=True, capstones=False,
                     capstone_notes=None) -> RegenerateReport

- lessons: every selected lesson is rewritten; the new version replaces the old one only when it validates (a failed
  regeneration keeps the old lesson). Questions the learner already answered are retired (flagged, key 'old-…'), never
  deleted, so attempt history and FSRS state stay valid. Active tasks on a regenerated skill are abandoned inside the
  transaction that swaps its questions (also a task started while the new lesson was being written), so no task can
  point at a deleted question; when the regeneration fails the old lesson and its tasks stay as they were.
  A regenerated lesson also gets a new visual and verification.
- visuals: the visual plan is redone; the stored one is replaced only by a successful new plan.
- v2 lessons (lesson_json.format 'v2', see lesson_v2.py) are rewritten from their stored teaching spec with the v2
  writer → reviewer → revision; their visuals live in the blocks, so the visual planner skips them.
- capstones (v2 lessons): only the final multistep problem is rewritten — when it is missing, withheld by
  verification or breaks the capstone rules, or when capstone_notes name a problem — then stored and verified
  (lesson_v2.repair_stored_capstone); an answered old capstone is retired, never deleted.
- questions (v2 lessons): practice questions whose answer can be read (printed in the prompt, a step or a bank of
  lines, forced by a definition) or that question_notes name ("k2q1: …") are rewritten in place — same row, so a question
  served first stays served first — and verified (lesson_v2.repair_stored_questions).
- verify: machine-checkable questions are re-verified in place (learner flags and retired questions are left alone).
  Nothing is held out of serving meanwhile: each question keeps its old verdict until the new one is written.
Progress (status, FSRS cards, XP) is never touched. Runs in the caller's event loop; refuses while a build runs. An
interrupted run (Ctrl-C, cancellation) leaves every lesson servable — each skill has its old or its new content —
and marks the job 'interrupted', so the app offers Resume (which finishes missing visuals and verification).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .. import db, llm
from . import jobs
from .context import BuildContext, load_course, skill_rows
from .lesson_v2 import generate_v2_lesson, parse_question_note, repair_stored_capstone, repair_stored_questions, stored_spec
from .lessons import ensure_lesson, lesson_prefix
from .topup import release_stale_holds
from .tracker import JobTracker
from .verify import reverify_skill, verify_skill
from .visuals import ensure_visual

log = logging.getLogger("ua.pipeline.regenerate")


@dataclass
class RegenerateReport:
    skills: list[str] = field(default_factory=list)
    lessons_regenerated: list[str] = field(default_factory=list)
    lessons_kept: list[str] = field(default_factory=list)  # regeneration failed; the old lesson stays
    capstones: list[str] = field(default_factory=list)  # one outcome line per skill (capstones=True)
    questions: list[str] = field(default_factory=list)  # one line per rewritten question (questions=True)
    abandoned_tasks: int = 0
    released_holds: int = 0  # questions an earlier interrupted run / top-up had left out of serving
    cost_usd: float = 0.0


async def regenerate(
    course_id: str,
    keys: list[str] | None = None,
    *,
    lessons: bool = True,
    visuals: bool = True,
    verify: bool = True,
    capstones: bool = False,
    capstone_notes: list[str] | None = None,
    questions: bool = False,
    question_notes: list[str] | None = None,
) -> RegenerateReport:
    course = load_course(course_id)
    if jobs.is_building(course_id):
        raise RuntimeError("The course is building; wait for the build to finish (or resume it) first.")
    selected = [s for s in skill_rows(course_id) if not keys or s["key"] in keys]
    if questions and len(selected) != 1 and any(
        (parsed := parse_question_note(n)) is not None and parsed[0] is None for n in question_notes or []
    ):
        raise ValueError("a question note without a skill ('k2q1: …') needs exactly one selected skill — write 'S8b k2q1: …'")
    unknown = sorted(set(keys or []) - {s["key"] for s in selected})
    if unknown:
        raise LookupError(f"Unknown skill keys: {', '.join(unknown)}")
    report = RegenerateReport(skills=[s["key"] for s in selected])
    jobs._active.add(course_id)  # no build may start in this process meanwhile
    usage = llm.Usage()
    token = llm.current_usage.set(usage)
    tracker = JobTracker.load(course_id)
    final_state = tracker.state if tracker.state not in ("running", "queued") else "done"
    interrupted_now = False
    tracker.state = "running"  # the UI shows progress; another process sees the course as building
    ctx = BuildContext(course=course, tracker=tracker, usage=usage)
    parts = [
        name
        for name, on in (
            ("lessons", lessons), ("visuals", visuals), ("capstones", capstones), ("questions", questions), ("verification", verify)
        )
        if on
    ]
    tracker.log(f"Regenerating {', '.join(parts)} for {len(selected)} skill{'s' * (len(selected) != 1)}")
    try:
        ids = [s["id"] for s in selected]
        report.released_holds = release_stale_holds(ids)
        if report.released_holds:
            tracker.log(f"Released {report.released_holds} questions an earlier interrupted run had held back")
        prefix = lesson_prefix(course) if lessons or capstones or questions else ""

        async def one(skill: dict) -> None:
            regenerated = False
            spec = stored_spec(db.loads(skill["lesson_json"], None) or {})  # v2 lesson: rewritten from its stored plan
            if questions and not lessons:
                for line in await repair_stored_questions(ctx, skill["id"], prefix, question_notes):
                    report.questions.append(line)
                    tracker.log(f"Question {line}")
            if capstones and not lessons:
                outcome = await repair_stored_capstone(ctx, skill["id"], prefix, capstone_notes)
                report.capstones.append(outcome)
                tracker.log(f"Capstone {outcome}")
            if (questions or capstones) and not (lessons or visuals or verify):
                return
            if lessons and spec is not None:
                result = await generate_v2_lesson(ctx, skill["id"], spec, prefix)  # also verifies the new questions
                (report.lessons_regenerated if result.ok else report.lessons_kept).append(skill["key"])
                if result.ok or not verify:
                    return
                regenerated = False
            elif lessons:
                regenerated = await ensure_lesson(ctx, skill["id"], prefix, force=True)
                if regenerated:
                    report.lessons_regenerated.append(skill["key"])
                elif skill["lesson_status"] == "ready":
                    report.lessons_kept.append(skill["key"])
            if spec is None and (regenerated or visuals):  # a v2 lesson's visuals live in its blocks
                await ensure_visual(ctx, skill["id"], force=True)  # the old visual stays unless the new plan works
            notes: list[str] = []
            if regenerated:
                notes = await verify_skill(skill["id"])  # the new questions are unverified
            elif verify:
                notes = await reverify_skill(skill["id"])
            for note in notes:
                tracker.log(f"Verify {skill['key']}: {note}")

        async def guarded(skill: dict) -> None:
            try:
                await one(skill)
            except Exception as e:  # noqa: BLE001 — one skill's failure never stops the others (cancellation does)
                log.exception("regeneration of skill %s crashed", skill["key"])
                tracker.log(f"Skill {skill['key']}: unexpected error {e}", level=logging.ERROR)

        if selected:
            await guarded(selected[0])  # the first lesson call writes the prompt cache for the others
            await asyncio.gather(*(guarded(s) for s in selected[1:]))
        jobs.refresh_pipeline_counters(ctx)
        report.cost_usd = usage.cost_usd
        tracker.log(f"Regeneration finished · {jobs._cost_summary(usage)}")
    except (asyncio.CancelledError, KeyboardInterrupt):
        final_state, interrupted_now = "interrupted", True  # → Resume in the app finishes what is missing
        tracker.log(
            "Regeneration interrupted — every lesson keeps its old or its new version; resume to finish visuals and "
            f"verification · {jobs._cost_summary(usage)}",
            level=logging.WARNING,
        )
        raise
    finally:
        report.abandoned_tasks = ctx.abandoned_tasks
        if final_state == "interrupted" and not interrupted_now:  # inherited: done if nothing is left to resume
            final_state = jobs.settled_state(course_id, final_state)
        tracker.finish(final_state, tracker.error)
        llm.current_usage.reset(token)
        jobs._active.discard(course_id)
    return report
