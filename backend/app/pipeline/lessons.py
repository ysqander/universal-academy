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
    chapter_text,
    course_figures,
    course_map,
    figure_line,
    focus_pages,
    page_marker,
)
from .prompts import LESSON_INSTRUCTIONS
from .questions import clean_questions, insert_questions, is_machine_checkable

log = logging.getLogger("ua.pipeline.lessons")

MAX_KPS = 4
MIN_QUESTIONS_PER_KP = 2
ATTEMPTS = 2


class InvalidLesson(ValueError):
    pass


def lesson_prefix(course: CourseInfo) -> str:
    """Chapter text + course map: identical for every skill of a course (prompt-cache friendly)."""
    return chapter_text(course) + "\n" + course_map(course.id)


async def ensure_lesson(ctx: BuildContext, skill_id: str, prefix: str) -> bool:
    """Generate, validate and store the skill's lesson unless it is ready. Returns True when the lesson is ready."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if skill is None:
        return False
    if skill["lesson_status"] == "ready":
        return True
    text = lesson_request(ctx.course, skill)
    error: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            draft = await llm.structured(
                role="lesson",
                instructions=LESSON_INSTRUCTIONS,
                shared_prefix=prefix,
                text=text,
                schema=LessonDraft,
                max_output_tokens=32000,
                label=f"lesson {skill['key']}",
                stream=True,
            )
            lesson, pools, notes = validate_lesson(draft)
        except Exception as e:  # noqa: BLE001 — any failure is retried once, then the lesson is marked failed
            error = e
            log.warning("lesson %s attempt %d failed: %s", skill["key"], attempt + 1, e)
            continue
        persist_lesson(skill_id, lesson, pools)
        for note in notes:
            ctx.tracker.log(f"Lesson {skill['key']}: {note}", level=logging.WARNING)
        n_questions = sum(len(p) for p in pools)
        ctx.tracker.log(f"Lesson {skill['key']} ready — {len(pools)} knowledge points, {n_questions} questions")
        return True
    db.execute("UPDATE skills SET lesson_status = 'failed' WHERE id = ?", (skill_id,))
    ctx.tracker.log(f"Lesson {skill['key']} failed after {ATTEMPTS} attempts: {error}", level=logging.ERROR)
    return False


def validate_lesson(draft: LessonDraft) -> tuple[dict[str, Any], list[list[QuestionDraft]], list[str]]:
    """Clean the questions of every knowledge point. Returns (lesson_json dict without questions, question pools per
    KP, notes). Raises InvalidLesson when a knowledge point would be unpassable (< 2 valid questions)."""
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
        pools.append(_unique_keys(kept, i, used_keys))
    lesson = draft.model_dump()
    lesson["knowledge_points"] = [{k: v for k, v in kp.model_dump().items() if k != "questions"} for kp in kps]
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


def persist_lesson(skill_id: str, lesson: dict[str, Any], pools: list[list[QuestionDraft]]) -> None:
    """Questions first, then lesson_status='ready' — in one transaction."""
    with db.connect() as conn:
        conn.execute("DELETE FROM questions WHERE skill_id = ?", (skill_id,))
        for kp_index, pool in enumerate(pools):
            insert_questions(conn, skill_id, kp_index, pool)
        conn.execute(
            "UPDATE skills SET lesson_json = ?, lesson_status = 'ready' WHERE id = ?", (db.dumps(lesson), skill_id)
        )


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

    attached = set(db.loads(skill["figure_ids_json"], []))
    figures = [f for f in course_figures(course) if f.db_id in attached or f.page in pages]
    if figures:
        lines += ["", "Book figures on these pages:"] + [figure_line(f) for f in figures]
    if exercise_pages:
        lines += [
            "",
            "Book exercises are on pages "
            + ", ".join(page_marker(p, course.label(p), True) for p in exercise_pages)
            + " — adapt the ones that fit this objective and cite them.",
        ]
    return "\n".join(lines)
