"""Replan one skill with lesson generation v2 (SPEC §4.2b, docs/lesson-v2.md) — used by scripts/replan.py.

    await plan_replan(course_id, key, *, no_split=False) -> PlanResult       planner call + validation (no writes)
    check_plan(course_id, key, plan, *, no_split=False) -> PlanResult        re-validate a saved plan (no LLM call)
    await apply_replan(course_id, key, plan_result, *, usage=None) -> ReplanReport
    await replan(course_id, key, *, no_split=False, plan=None) -> ReplanReport   both steps
    report_lines(report) -> list[str]                                        the printed report

Split (decision 'split'): allowed only while the original skill has no answered (non-voided) attempts, no FSRS card
and no review history. In ONE transaction the original's tasks (and voided attempts), questions, progress, feedback
and edges and the skill row are deleted; the new skills (keys S2a, S2b, …) are created with edges from each spec's
prerequisites and from the dependents mapping (new skill → dependent, with the original edge's weight); order_index
and the reduced flags of the whole course are recomputed. Progress rows are then initialized (unlocks recomputed) and
a v2 lesson is written, reviewed, revised, stored and verified for each new skill (concurrently, bounded).
Keep: the skill's lesson is replaced by a v2 lesson (answered questions retired, active tasks abandoned in the swap
transaction, as in regenerate).

Like regenerate, the job row shows 'running' meanwhile, and an interruption leaves the course servable: the split is
atomic, a new skill without a lesson yet is 'pending' (Resume in the app writes a v1 lesson for it), and a kept
skill keeps its old lesson until the new one is stored.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .. import db, llm
from ..learning import state
from ..llm_schemas import TeachingPlan
from . import jobs
from .context import BuildContext, load_course
from .graph import reorder_course
from .lesson_v2 import (
    V2Result,
    describe_plan,
    generate_v2_lesson,
    new_keys,
    plan_graph,
    plan_skill,
    review_lines,
    review_summary,
    validate_plan,
)
from .lessons import lesson_prefix
from .tracker import JobTracker

log = logging.getLogger("ua.pipeline.replan")

LESSON_CONCURRENCY = 3


class SplitRefused(RuntimeError):
    """The original skill has learner history that a split would destroy."""


@dataclass
class PlanResult:
    key: str
    plan: TeachingPlan
    notes: list[str] = field(default_factory=list)
    split_allowed: bool = True
    blockers: list[str] = field(default_factory=list)


@dataclass
class ReplanReport:
    course_id: str
    key: str
    plan: TeachingPlan
    plan_notes: list[str] = field(default_factory=list)
    decision: str = "keep"
    keys: dict[str, str] = field(default_factory=dict)  # local id → skill key
    skill_ids: dict[str, str] = field(default_factory=dict)  # local id → skill id
    split_notes: list[str] = field(default_factory=list)
    deleted_tasks: int = 0
    results: list[V2Result] = field(default_factory=list)
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _skill_row(course_id: str, key: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = ?", (course_id, key))
    if row is None:
        raise LookupError(f"Skill {key} not found in course {course_id}")
    return row


def split_blockers(skill_id: str, conn=None) -> list[str]:
    """Learner history a split would destroy: answered (non-voided) attempts, an FSRS card, review history."""

    def one(sql: str) -> Any:
        if conn is not None:
            return conn.execute(sql, (skill_id,)).fetchone()[0]
        return next(iter(db.query_one(sql, (skill_id,)).values()))

    reasons = []
    attempts = one("SELECT COUNT(*) FROM attempts WHERE skill_id = ? AND status != 'voided'")
    if attempts:
        reasons.append(f"{attempts} answered attempt{'s' * (attempts != 1)}")
    if one("SELECT COUNT(*) FROM progress WHERE skill_id = ? AND card_json IS NOT NULL AND card_json != ''"):
        reasons.append("an FSRS card (the skill was learned)")
    reviews = one("SELECT COUNT(*) FROM review_log WHERE skill_id = ?")
    if reviews:
        reasons.append(f"{reviews} review-log entr{'ies' if reviews != 1 else 'y'}")
    return reasons


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


async def plan_replan(course_id: str, key: str, *, no_split: bool = False) -> PlanResult:
    course = load_course(course_id)
    skill = _skill_row(course_id, key)
    blockers = split_blockers(skill["id"])
    allow = not no_split and not blockers
    notes = []
    if blockers and not no_split:
        notes.append(f"{key} cannot be split ({'; '.join(blockers)}) — planning it as a kept skill (--no-split)")
    plan, plan_notes = await plan_skill(course, skill, allow_split=allow, blockers=blockers or None)
    return PlanResult(key=key, plan=plan, notes=notes + plan_notes, split_allowed=allow, blockers=blockers)


def check_plan(course_id: str, key: str, plan: TeachingPlan, *, no_split: bool = False) -> PlanResult:
    """Re-validate a saved plan against the current graph. A split plan for a skill that may not be split raises
    SplitRefused (the caller re-plans with no_split=True)."""
    course = load_course(course_id)
    skill = _skill_row(course_id, key)
    blockers = split_blockers(skill["id"])
    allow = not no_split and not blockers
    if plan.decision == "split" and not allow:
        raise SplitRefused(
            f"the saved plan splits {key}, but " + ("; ".join(blockers) if blockers else "--no-split was given")
        )
    validated, notes = validate_plan(plan, plan_graph(course, skill), allow_split=allow)
    return PlanResult(key=key, plan=validated, notes=notes, split_allowed=allow, blockers=blockers)


# ---------------------------------------------------------------------------
# Split (one transaction)
# ---------------------------------------------------------------------------


def apply_split(course_id: str, key: str, plan: TeachingPlan) -> tuple[dict[str, str], dict[str, str], list[str], int]:
    """Replace the skill by the plan's skills. Returns (local id → key, local id → skill id, notes, deleted tasks)."""
    notes: list[str] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # the history check and the swap are one step, also for other processes
        original = conn.execute("SELECT * FROM skills WHERE course_id = ? AND key = ?", (course_id, key)).fetchone()
        if original is None:
            raise LookupError(f"Skill {key} not found in course {course_id}")
        original = dict(original)
        sid = original["id"]
        blockers = split_blockers(sid, conn)
        if blockers:
            raise SplitRefused(f"{key} has learner history ({'; '.join(blockers)}); it cannot be split")
        rows = conn.execute("SELECT id, key FROM skills WHERE course_id = ?", (course_id,)).fetchall()
        id_of = {r["key"]: r["id"] for r in rows}
        incoming = {
            r["src"]: r["weight"] for r in conn.execute("SELECT src, weight FROM edges WHERE dst = ?", (sid,)).fetchall()
        }
        outgoing = {
            r["dst"]: r["weight"] for r in conn.execute("SELECT dst, weight FROM edges WHERE src = ?", (sid,)).fetchall()
        }
        keys = new_keys(key, plan, set(id_of) - {key})

        # the original and everything hanging off it (only voided attempts can exist: answered ones block the split)
        conn.execute("DELETE FROM attempts WHERE skill_id = ? OR task_id IN (SELECT id FROM tasks WHERE skill_id = ?)", (sid, sid))
        deleted_tasks = conn.execute("DELETE FROM tasks WHERE skill_id = ?", (sid,)).rowcount
        conn.execute("DELETE FROM questions WHERE skill_id = ?", (sid,))
        conn.execute("DELETE FROM progress WHERE skill_id = ?", (sid,))
        conn.execute("DELETE FROM block_feedback WHERE skill_id = ?", (sid,))
        conn.execute("DELETE FROM edges WHERE src = ? OR dst = ?", (sid, sid))
        conn.execute("DELETE FROM skills WHERE id = ?", (sid,))

        figure_rows = conn.execute("SELECT id, page_index FROM figures WHERE course_id = ?", (course_id,)).fetchall()
        page_of = {r["id"]: r["page_index"] for r in figure_rows}
        attached = [f for f in db.loads(original["figure_ids_json"], []) if f in page_of]
        ids: dict[str, str] = {}
        now = db.now_iso()
        for spec in plan.skills:
            new_id = db.new_id()
            ids[spec.local_id] = new_id
            pages = set(spec.source_pages)
            conn.execute(
                "INSERT INTO skills(id, course_id, key, title, objective, kind, summary, difficulty, est_minutes, "
                "order_index, source_pages_json, source_refs_json, figure_ids_json, foundations_json, "
                "misconceptions_json, lesson_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 'pending')",
                (
                    new_id,
                    course_id,
                    keys[spec.local_id],
                    spec.title,
                    spec.objective,
                    spec.kind,
                    spec.objective,  # replaced by the lesson's goal line once it is written
                    spec.difficulty,
                    spec.est_minutes,
                    db.dumps(spec.source_pages),
                    db.dumps(spec.source_refs),
                    db.dumps([f for f in attached if page_of[f] in pages]),
                    original["foundations_json"],
                    db.dumps(spec.misconceptions),
                ),
            )
            conn.execute(
                "INSERT INTO progress(skill_id, course_id, status, updated_at) VALUES (?, ?, 'locked', ?)",
                (new_id, course_id, now),
            )

        edges: dict[tuple[str, str], float] = {}
        for spec in plan.skills:
            for link in spec.prerequisites:
                src = ids.get(link.key) or id_of.get(link.key)
                if src is None or src == sid:
                    notes.append(f"{keys[spec.local_id]}: prerequisite {link.key} no longer exists — skipped")
                    continue
                edges[(src, ids[spec.local_id])] = link.weight
        for link in plan.dependents:
            dst = id_of.get(link.dependent_key)
            if dst is None or dst not in outgoing:
                continue
            for local in link.needs:
                edges[(ids[local], dst)] = outgoing[dst]
        # safety net (validate_plan already guarantees it): every original link survives through some new skill
        for src, weight in incoming.items():
            if not any(e[0] == src and e[1] in ids.values() for e in edges):
                edges[(src, ids[plan.skills[0].local_id])] = weight
                notes.append(f"kept the original prerequisite link {src} → {keys[plan.skills[0].local_id]}")
        for dst, weight in outgoing.items():
            if not any(e[1] == dst and e[0] in ids.values() for e in edges):
                for spec in plan.skills:
                    edges[(ids[spec.local_id], dst)] = weight
                notes.append(f"dependent {dst} was not mapped: it now needs every new skill")
        conn.executemany(
            "INSERT INTO edges(course_id, src, dst, weight, reduced) VALUES (?, ?, ?, ?, 1)",
            [(course_id, src, dst, weight) for (src, dst), weight in edges.items()],
        )
        notes += reorder_course(conn, course_id)
    state.init_progress(course_id)  # unlocks of the new skills (their prerequisites' progress is untouched)
    return keys, ids, notes, deleted_tasks


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


async def apply_replan(course_id: str, key: str, planned: PlanResult, *, usage: llm.Usage | None = None) -> ReplanReport:
    course = load_course(course_id)
    if jobs.is_building(course_id):
        raise RuntimeError("The course is building; wait for the build to finish (or resume it) first.")
    skill = _skill_row(course_id, key)
    plan = planned.plan
    report = ReplanReport(course_id=course_id, key=key, plan=plan, plan_notes=list(planned.notes), decision=plan.decision)
    usage = usage or llm.current_usage.get() or llm.Usage()
    token = llm.current_usage.set(usage)
    cost_before = usage.cost_usd
    jobs._active.add(course_id)  # no build may start in this process meanwhile
    tracker = JobTracker.load(course_id)
    final_state = tracker.state if tracker.state not in ("running", "queued") else "done"
    tracker.state = "running"  # the UI shows progress; another process sees the course as building
    ctx = BuildContext(course=course, tracker=tracker, usage=usage)
    tracker.log(f"Replanning {key} with lesson generation v2: {plan.decision} ({len(plan.skills)} skill{'s' * (len(plan.skills) != 1)})")
    try:
        if plan.decision == "split":
            report.keys, report.skill_ids, report.split_notes, report.deleted_tasks = apply_split(course_id, key, plan)
            tracker.log(f"Split {key} into {', '.join(report.keys.values())}")
            for note in report.split_notes:
                tracker.log(f"Split {key}: {note}", level=logging.WARNING)
        else:
            report.keys, report.skill_ids = {plan.skills[0].local_id: key}, {plan.skills[0].local_id: skill["id"]}
        prefix = lesson_prefix(course)  # after the split: the course map shows the new skills
        specs = {s.local_id: s for s in plan.skills}
        semaphore = asyncio.Semaphore(LESSON_CONCURRENCY)

        async def one(local: str) -> V2Result:
            async with semaphore:
                try:
                    return await generate_v2_lesson(ctx, report.skill_ids[local], specs[local], prefix)
                except Exception as e:  # noqa: BLE001 — one skill's failure never stops the others
                    log.exception("v2 lesson %s crashed", report.keys[local])
                    tracker.log(f"Lesson {report.keys[local]} (v2): unexpected error {e}", level=logging.ERROR)
                    return V2Result(key=report.keys[local], skill_id=report.skill_ids[local], error=str(e))

        locals_ = [s.local_id for s in plan.skills]
        first = await one(locals_[0])  # the first lesson call writes the prompt cache for the others
        rest = await asyncio.gather(*(one(local) for local in locals_[1:]))
        report.results = [first, *rest]
        jobs.refresh_pipeline_counters(ctx)
        report.cost_usd = usage.cost_usd
        tracker.log(f"Replan of {key} finished · ${usage.cost_usd - cost_before:.2f} · {jobs._cost_summary(usage)}")
    except (asyncio.CancelledError, KeyboardInterrupt):
        final_state = "interrupted"  # → Resume in the app writes (v1) lessons for new skills still pending
        tracker.log(
            f"Replan of {key} interrupted — the course stays servable; resume to write missing lessons · "
            f"{jobs._cost_summary(usage)}",
            level=logging.WARNING,
        )
        raise
    finally:
        tracker.finish(final_state, tracker.error)
        llm.current_usage.reset(token)
        jobs._active.discard(course_id)
    return report


async def replan(
    course_id: str, key: str, *, no_split: bool = False, plan: TeachingPlan | None = None
) -> ReplanReport:
    """Plan (or re-validate a given plan) and apply it."""
    usage = llm.current_usage.get() or llm.Usage()
    token = llm.current_usage.set(usage)
    try:
        if plan is None:
            planned = await plan_replan(course_id, key, no_split=no_split)
        else:
            try:
                planned = check_plan(course_id, key, plan, no_split=no_split)
            except SplitRefused as e:
                planned = await plan_replan(course_id, key, no_split=True)
                planned.notes.insert(0, f"{e} — re-planned as a kept skill")
        return await apply_replan(course_id, key, planned, usage=usage)
    finally:
        llm.current_usage.reset(token)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def report_lines(report: ReplanReport) -> list[str]:
    lines = ["PLAN", *describe_plan(report.plan, report.key)]
    if report.plan_notes:
        lines += ["", "Plan checks:"] + [f"- {n}" for n in report.plan_notes]
    if report.decision == "split":
        lines += ["", f"SPLIT: {report.key} → {', '.join(report.keys.values())} · {report.deleted_tasks} task(s) of {report.key} removed"]
        lines += [f"- {n}" for n in report.split_notes]
        edges = db.query(
            "SELECT a.key AS src, b.key AS dst, e.weight, e.reduced FROM edges e JOIN skills a ON a.id = e.src "
            "JOIN skills b ON b.id = e.dst WHERE e.course_id = ? AND (e.src IN ({0}) OR e.dst IN ({0}))".format(
                ",".join("?" * len(report.skill_ids))
            ),
            (report.course_id, *report.skill_ids.values(), *report.skill_ids.values()),
        )
        lines += ["Edges: " + ", ".join(f"{e['src']}→{e['dst']} ({e['weight']:g}{'' if e['reduced'] else ', implied'})" for e in edges)]
        order = db.query("SELECT key FROM skills WHERE course_id = ? ORDER BY order_index", (report.course_id,))
        lines.append("Order: " + " ".join(r["key"] for r in order))
    for result in report.results:
        lines += ["", "=" * 100]
        row = db.query_one("SELECT * FROM skills WHERE id = ?", (result.skill_id,))
        title = row["title"] if row else "?"
        status = (row or {}).get("lesson_status", "?")
        lines.append(f"{result.key} · {title} — lesson {status}" + (f" (error: {result.error})" if result.error else ""))
        if not row or not result.ok:
            continue
        lesson = db.loads(row["lesson_json"], {})
        progress = db.query_one("SELECT status FROM progress WHERE skill_id = ?", (result.skill_id,))
        lines.append(f"Objective: {row['objective']} · status {progress['status'] if progress else '—'}")
        lines.append(f"Your goal: {lesson.get('goal_line', '')}")
        lines.append("Blocks:")
        for i, block in enumerate(lesson.get("blocks") or [], start=1):
            extra = []
            if block.get("visual"):
                extra.append(f"visual {block['visual']['format']}")
            if block.get("check"):
                extra.append(f"check {block['check']['kind']}")
            lines.append(f"  {i}. [{block['kind']}] {block.get('heading') or '—'}" + (f"  ({', '.join(extra)})" if extra else ""))
        checks = [b["check"] for b in lesson.get("blocks") or [] if b.get("check")]
        if checks:
            lines.append("Checks:")
            for c in checks:
                answer = c.get("answer") or (f"option {c['correct_choice']}" if c.get("correct_choice") is not None else c.get("answer_display", ""))
                lines.append(f"  - [{c['kind']}] {c['prompt']} → {answer}")
        questions = db.query("SELECT kp_index, kind, flagged FROM questions WHERE skill_id = ? ORDER BY kp_index, rowid", (result.skill_id,))
        lines.append("Knowledge points:")
        for k, kp in enumerate(lesson.get("knowledge_points") or []):
            kinds = ", ".join(q["kind"] for q in questions if q["kp_index"] == k)
            lines.append(f"  {k + 1}. {kp.get('title', '')} — {kinds}")
        if lesson.get("deeper"):
            lines.append(f"Go deeper: {lesson['deeper'][:300]}{'…' if len(lesson['deeper']) > 300 else ''}")
        lines.append(f"Initial review: {review_summary(result.initial_review)}")
        lines += [f"  {line}" for line in review_lines(result.initial_review)]
        if result.revised:
            lines.append(f"Final review (after one revision): {review_summary(result.final_review)}")
            lines += [f"  {line}" for line in review_lines(result.final_review)]
        else:
            lines.append("Not revised (the first review reported nothing that needs a revision).")
        if result.notes:
            lines += ["Notes:"] + [f"  - {n}" for n in result.notes]
        statuses = Counter(
            db.loads(r["verify_json"], {}).get("status", "unverified")
            for r in db.query("SELECT verify_json FROM questions WHERE skill_id = ? AND kind != 'free_response'", (result.skill_id,))
        )
        flagged = sum(1 for q in questions if q["flagged"])
        lines.append(f"Verification: {dict(statuses)} · {flagged} flagged")
        lines += [f"  - {n}" for n in result.verify_notes]
    lines += ["", f"Cost: ${report.cost_usd:.2f}"]
    return lines
