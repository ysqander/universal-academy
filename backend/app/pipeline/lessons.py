"""Lessons stage: one LessonDraft per skill. The chapter text is sent as a byte-identical shared prefix so every
call after the first reads it from the prompt cache."""

from __future__ import annotations

import logging
import re
from typing import Any

from .. import db, llm
from ..llm_schemas import LessonDraft, QuestionDraft
from .context import (
    BuildContext,
    CourseInfo,
    FigureInfo,
    chapter_text,
    course_figures,
    course_map,
    figure_line,
    focus_pages,
    page_marker,
)
from .prompts import LESSON_INSTRUCTIONS
from .questions import (
    clean_questions,
    first_free_response_second,
    MAX_CHOICE_SHARE_HARD,
    choice_share,
    format_warnings,
    insert_questions,
    is_machine_checkable,
)
from .textutil import FIGURE_PLACEHOLDER_RE, clean_source_refs, lesson_text, mentions_caption

log = logging.getLogger("ua.pipeline.lessons")

MAX_KPS = 4
MIN_QUESTIONS_PER_KP = 2
ATTEMPTS = 2
RETIRED_REASON = "retired: lesson regenerated"
_PROOF_VERBS = re.compile(r"^\s*(prove|justify|show)\b", re.IGNORECASE)


def is_proof_skill(skill: dict[str, Any]) -> bool:
    """Proof / justify skills (E1): every knowledge point is practised with a free-response proof (q2), and where
    natural a second one (q4) for when the first was assisted."""
    return skill.get("kind") == "proof" or bool(_PROOF_VERBS.match(skill.get("objective") or ""))


class InvalidLesson(ValueError):
    pass


def lesson_prefix(course: CourseInfo) -> str:
    """Chapter text + course map: identical for every skill of a course (prompt-cache friendly)."""
    return chapter_text(course) + "\n" + course_map(course.id)


async def ensure_lesson(ctx: BuildContext, skill_id: str, prefix: str, *, force: bool = False) -> bool:
    """Generate, validate and store the skill's lesson unless it is ready. Returns True when the lesson is ready.
    force=True regenerates a ready lesson; if that fails, the existing lesson is kept (returns False)."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if skill is None:
        return False
    if skill["lesson_status"] == "ready" and not force:
        return True
    text = lesson_request(ctx.course, skill)
    proof = is_proof_skill(skill)
    error: Exception | None = None
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        request = text
        if isinstance(error, InvalidLesson):
            request += f"\n\nA previous draft of this lesson was rejected: {error}. Fix that in this draft."
        try:
            draft = await llm.structured(
                role="lesson",
                instructions=LESSON_INSTRUCTIONS,
                shared_prefix=prefix,
                text=request,
                schema=LessonDraft,
                max_output_tokens=32000,
                label=f"lesson {skill['key']}",
                stream=True,
            )
            # The proof rule is enforced with a retry; a last draft that still misses it is kept (with a warning)
            # rather than leaving the skill without a lesson.
            lesson, pools, notes = validate_lesson(draft, proof=proof, strict=not last)
        except Exception as e:  # noqa: BLE001 — any failure is retried once, then the lesson is marked failed
            error = e
            log.warning("lesson %s attempt %d failed: %s", skill["key"], attempt + 1, e)
            continue
        lesson, figure_ids, figure_notes = resolve_figures(lesson, skill, course_figures(ctx.course))
        notes += figure_notes
        abandoned = persist_lesson(skill_id, lesson, pools, figure_ids=figure_ids)
        if abandoned:
            ctx.abandoned_tasks += abandoned
            notes.append(f"abandoned {abandoned} active task{'s' * (abandoned != 1)} started on the previous version")
        for note in notes:
            ctx.tracker.log(f"Lesson {skill['key']}: {note}", level=logging.WARNING)
        n_questions = sum(len(p) for p in pools)
        ctx.tracker.log(f"Lesson {skill['key']} ready — {len(pools)} knowledge points, {n_questions} questions")
        return True
    if skill["lesson_status"] == "ready":
        ctx.tracker.log(f"Lesson {skill['key']} kept — regeneration failed: {error}", level=logging.ERROR)
        return False
    db.execute("UPDATE skills SET lesson_status = 'failed' WHERE id = ?", (skill_id,))
    ctx.tracker.log(f"Lesson {skill['key']} failed after {ATTEMPTS} attempts: {error}", level=logging.ERROR)
    return False


def validate_lesson(
    draft: LessonDraft, *, proof: bool = False, strict: bool = True
) -> tuple[dict[str, Any], list[list[QuestionDraft]], list[str]]:
    """Clean the questions of every knowledge point. Returns (lesson_json dict without questions, question pools per
    KP in serving order, notes). Raises InvalidLesson when a knowledge point would be unpassable (< 2 valid
    questions) and — for a proof skill with strict=True — when a knowledge point has no free-response proof."""
    kps = draft.knowledge_points[:MAX_KPS]
    if not kps:
        raise InvalidLesson("lesson has no knowledge points")
    notes: list[str] = []
    if len(draft.knowledge_points) > MAX_KPS:
        notes.append(f"kept the first {MAX_KPS} of {len(draft.knowledge_points)} knowledge points")
    pools: list[list[QuestionDraft]] = []
    used_keys: set[str] = set()
    for i, kp in enumerate(kps):
        kept, dropped = clean_questions(kp.questions, f"KP {i + 1}")
        notes.extend(f"dropped {d}" for d in dropped)
        if len(kept) < MIN_QUESTIONS_PER_KP:
            raise InvalidLesson(f"knowledge point {i + 1} has only {len(kept)} valid questions")
        if sum(1 for q in kept if is_machine_checkable(q.kind)) < 2:
            notes.append(f"KP {i + 1} has fewer than 2 machine-checkable questions")
        written = sum(1 for q in kept if q.kind == "free_response")
        if proof and not written:
            message = f"knowledge point {i + 1} of a proof skill has no free-response proof question"
            if strict:
                raise InvalidLesson(message)
            notes.append(message)
        elif proof and written == 1:  # an assisted first proof then waives the written requirement for this KP
            notes.append(f"KP {i + 1} has a single free-response proof (no second attempt if the first is assisted)")
        pools.append(first_free_response_second(_unique_keys(kept, i, used_keys)))
    share = choice_share(pools)
    if strict and share > MAX_CHOICE_SHARE_HARD:
        raise InvalidLesson(
            f"{share:.0%} of the questions are multiple choice or true/false (at most 40%); replace most of them with "
            "typed numeric/expression questions (concrete instances) or free_response explanations"
        )
    notes += format_warnings(pools)
    lesson = draft.model_dump()
    lesson["knowledge_points"] = [{k: v for k, v in kp.model_dump().items() if k != "questions"} for kp in kps]
    refs = clean_source_refs(draft.source_refs)
    if len(refs) < len(draft.source_refs):
        notes.append(f"kept {len(refs)} of {len(draft.source_refs)} source refs (item numbers only)")
    lesson["source_refs"] = refs
    return lesson, pools, notes


def _unique_keys(questions: list[QuestionDraft], kp_index: int, used: set[str]) -> list[QuestionDraft]:
    result = []
    for q in questions:
        key = q.key.strip()
        if not re.fullmatch(r"k\d+q\d+", key) or key in used:
            key = next_question_key(kp_index, used)
        used.add(key)
        result.append(q.model_copy(update={"key": key}))
    return result


def next_question_key(kp_index: int, used: set[str]) -> str:
    n = 1
    while f"k{kp_index + 1}q{n}" in used:
        n += 1
    return f"k{kp_index + 1}q{n}"


def persist_lesson(
    skill_id: str, lesson: dict[str, Any], pools: list[list[QuestionDraft]], *, figure_ids: list[str] | None = None
) -> int:
    """Questions first, then lesson_status='ready' — in one transaction. Questions of an earlier version of the lesson
    are deleted, except those the learner already answered: they are retired (flagged, renamed key) so the attempt
    history stays valid. Active tasks on the skill (started on the old version — possibly while the new one was being
    written, from another process) are abandoned in the same transaction, so no task can point at a deleted question.
    Returns the number of tasks abandoned."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # the task check and the question swap are one step, also for other processes
        abandoned = abandon_active_tasks(conn, skill_id)
        retire_questions(conn, skill_id)
        for kp_index, pool in enumerate(pools):
            insert_questions(conn, skill_id, kp_index, pool)
        if figure_ids is None:
            conn.execute(
                "UPDATE skills SET lesson_json = ?, lesson_status = 'ready' WHERE id = ?", (db.dumps(lesson), skill_id)
            )
        else:
            conn.execute(
                "UPDATE skills SET lesson_json = ?, lesson_status = 'ready', figure_ids_json = ? WHERE id = ?",
                (db.dumps(lesson), db.dumps(figure_ids), skill_id),
            )
    return abandoned


def abandon_active_tasks(conn, skill_id: str) -> int:
    """Close the skill's active tasks as abandoned (no XP), like the engine's abandon. Returns how many."""
    return conn.execute(
        "UPDATE tasks SET status = 'completed', phase = 'done', outcome = 'abandoned', xp_awarded = 0, completed_at = ?, "
        "state_json = CASE WHEN json_valid(state_json) THEN json_set(state_json, '$.current', NULL) ELSE state_json END "
        "WHERE skill_id = ? AND status = 'active'",
        (db.now_iso(), skill_id),
    ).rowcount


# questions an active task has served or is showing (safety net: never delete what a task may still answer)
_VALID_STATE = "CASE WHEN json_valid(t.state_json) THEN t.state_json ELSE '{}' END"
_IN_ACTIVE_TASKS = (
    f"SELECT j.value FROM tasks t, json_each({_VALID_STATE}, '$.served') j WHERE t.status = 'active' AND t.skill_id = ? "
    "AND j.value IS NOT NULL "  # NOT IN (…, NULL) would match nothing
    f"UNION SELECT json_extract({_VALID_STATE}, '$.current') FROM tasks t WHERE t.status = 'active' AND t.skill_id = ? "
    f"AND json_extract({_VALID_STATE}, '$.current') IS NOT NULL"
)


def retire_questions(conn, skill_id: str) -> int:
    """Delete the skill's questions; ones with attempts — or held by an active task — are kept as flagged history.
    Returns how many were retired."""
    conn.execute(
        "DELETE FROM questions WHERE skill_id = ? AND id NOT IN (SELECT question_id FROM attempts WHERE skill_id = ?) "
        f"AND id NOT IN ({_IN_ACTIVE_TASKS})",
        (skill_id, skill_id, skill_id, skill_id),
    )
    rows = conn.execute("SELECT id, key FROM questions WHERE skill_id = ?", (skill_id,)).fetchall()
    for row in rows:
        key = row["key"] if row["key"].startswith("old-") else f"old-{row['key']}"
        conn.execute(
            "UPDATE questions SET flagged = 1, flag_reason = ?, key = ? WHERE id = ?", (RETIRED_REASON, key, row["id"])
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Figures: a lesson may only refer to figures the learner can see
# ---------------------------------------------------------------------------


def offered_figures(course: CourseInfo, skill: dict[str, Any]) -> list[FigureInfo]:
    """The figures the lesson request lists: attached to the skill, or printed on its source pages."""
    return figures_for_skill(course_figures(course), skill)


def figures_for_skill(figures: list[FigureInfo], skill: dict[str, Any]) -> list[FigureInfo]:
    attached = set(db.loads(skill["figure_ids_json"], []))
    pages = set(db.loads(skill["source_pages_json"], []))
    return [f for f in figures if f.db_id in attached or f.page in pages]


def resolve_figures(
    lesson: dict[str, Any], skill: dict[str, Any], figures: list[FigureInfo]
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Normalize [[figure:ID]] placeholders to local ids (dropping unknown ones) and attach every figure the lesson
    places, or quotes by caption among the offered ones, to the skill so the lesson view shows it.
    `figures` = all figures of the course. Returns (lesson, figure db ids, notes)."""
    by_id = {f.local_id: f for f in figures} | {f.db_id: f for f in figures}
    offered = figures_for_skill(figures, skill)
    notes: list[str] = []
    placed: list[FigureInfo] = []

    def rewrite(match: re.Match[str]) -> str:
        fig = by_id.get(match.group(1))
        if fig is None:
            notes.append(f"removed a placeholder for unknown figure {match.group(1)!r}")
            return ""
        placed.append(fig)
        return f"[[figure:{fig.local_id}]]"

    def fix(text: str) -> str:
        return FIGURE_PLACEHOLDER_RE.sub(rewrite, text) if text else text

    for field in ("why_it_matters", "idea", "missing_steps", "connection"):
        lesson[field] = fix(lesson.get(field) or "")
    for field in ("goal_line", "deeper"):  # v2
        if field in lesson:
            lesson[field] = fix(lesson.get(field) or "")
    for block in lesson.get("blocks") or []:  # v2 teaching sequence
        block["body"] = fix(block.get("body") or "")
        if block.get("check"):
            block["check"]["explanation"] = fix(block["check"].get("explanation") or "")
    for kp in lesson.get("knowledge_points") or []:
        kp["explanation"] = fix(kp.get("explanation") or "")
        example = kp.get("worked_example") or {}
        if example:
            example["problem"] = fix(example.get("problem") or "")
            for step in example.get("steps") or []:
                step["math"], step["why"] = fix(step.get("math") or ""), fix(step.get("why") or "")
    text = lesson_text(lesson)
    quoted = [f for f in offered if f.caption and mentions_caption(text, f.caption)]
    ids = list(dict.fromkeys(db.loads(skill["figure_ids_json"], []) + [f.db_id for f in placed + quoted]))
    return lesson, ids, notes


def lesson_request(course: CourseInfo, skill: dict[str, Any]) -> str:
    """The call-specific part of a lesson request: the skill, its neighbours in the graph and where to look."""
    pages = db.loads(skill["source_pages_json"], [])
    exercise_pages = [
        r["page_index"]
        for r in db.query("SELECT page_index FROM pages WHERE course_id = ? AND is_exercise_page = 1", (course.id,))
    ]
    lines = [
        f"Write the lesson package for skill {skill['key']}: {skill['title']}",
        "",
        f"Objective: {skill['objective']}",
        f"Kind: {skill['kind']} · difficulty {skill['difficulty']}/5 · about {skill['est_minutes']} minutes",
        f"Summary: {skill['summary']}",
    ]
    refs = db.loads(skill["source_refs_json"], [])
    if refs:
        lines.append("Book items: " + ", ".join(refs))
    if pages:
        markers = ", ".join(page_marker(p, course.label(p)) for p in pages)
        around = [p for p in focus_pages(pages, course) if p not in pages]
        lines.append(
            f"Taught on: {markers} — read these closely"
            + (f" (and their neighbours: pages {', '.join(map(str, around))})." if around else ".")
        )
    misconceptions = db.loads(skill["misconceptions_json"], [])
    if misconceptions:
        lines += ["Known misconceptions:"] + [f"- {m}" for m in misconceptions]
    foundation_keys = db.loads(skill["foundations_json"], [])
    if foundation_keys:
        rows = db.query("SELECT key, title, description FROM foundations WHERE course_id = ?", (course.id,))
        lines += ["Assumed outside knowledge:"] + [
            f"- {r['key']} · {r['title']}: {r['description']}" for r in rows if r["key"] in foundation_keys
        ]

    prereqs = db.query(
        "SELECT s.key, s.title, s.objective, e.weight FROM edges e JOIN skills s ON s.id = e.src "
        "WHERE e.dst = ? ORDER BY s.order_index",
        (skill["id"],),
    )
    lines += ["", "Prerequisites (learned before this lesson — use them freely, do not re-teach them):"]
    lines += (
        [f"- {p['key']} · {p['title']} — {p['objective']} (encompassing weight {p['weight']:g})" for p in prereqs]
        if prereqs
        else ["(none — this is a starting skill of the course)"]
    )
    dependents = db.query(
        "SELECT s.key, s.title, s.objective FROM edges e JOIN skills s ON s.id = e.dst WHERE e.src = ? "
        "ORDER BY s.order_index",
        (skill["id"],),
    )
    lines += ["", "Later skills that build on this one (do not teach them; the connection may point to them):"]
    lines += [f"- {d['key']} · {d['title']} — {d['objective']}" for d in dependents] if dependents else ["(none)"]

    figures = offered_figures(course, skill)
    if figures:
        lines += [
            "",
            (
                "Book figures for this skill — the only figures the lesson may refer to (place one with "
                "[[figure:ID]] on its own line; every figure you place or name by its caption is shown to the learner):"
            ),
        ] + [figure_line(f) for f in figures]
    else:
        lines += ["", "Book figures for this skill: none — do not refer to any figure."]
    if is_proof_skill(skill):
        lines += [
            "",
            (
                "This is a PROOF skill: every knowledge point needs a free_response proof question as its q2 and, "
                "wherever natural, a second free_response proof of a variant as its q4 (see PROOF SKILLS in the "
                "instructions)."
            ),
        ]
    if exercise_pages:
        lines += [
            "",
            "Book exercises are on pages "
            + ", ".join(page_marker(p, course.label(p), True) for p in exercise_pages)
            + " — adapt the ones that fit this objective and cite them.",
        ]
    return "\n".join(lines)
