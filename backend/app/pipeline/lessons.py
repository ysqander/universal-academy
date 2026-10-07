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
    MAX_CHOICE_SHARE_HARD,
    InvalidQuestion,
    capstone_issues,
    choice_share,
    clean_capstone,
    clean_questions,
    format_warnings,
    insert_questions,
    is_machine_checkable,
    pair_form_warnings,
)
from .textutil import FIGURE_PLACEHOLDER_RE, clean_source_refs, lesson_text, mentions_caption

log = logging.getLogger("ua.pipeline.lessons")

MAX_KPS = 4
MIN_QUESTIONS_PER_KP = 2
TARGET_RANGE = (1, 3)  # knowledge_points[].target_questions: questions served to demonstrate the KP (SPEC §6.3)
LEGACY_TARGET = 2  # lessons stored before targets existed
ATTEMPTS = 2
RETIRED_REASON = "retired: lesson regenerated"
# "Show that …" is not on the list: a "show that" about one given object (show that $w$ is a cube root of $1$) is a
# computation; general statements are planned with kind 'proof'.
_PROOF_VERBS = re.compile(r"^\s*(prove|justify)\b", re.IGNORECASE)


def is_proof_skill(skill: dict[str, Any]) -> bool:
    """Proof / justify skills: practised with multistep walkthroughs of the proof (choose the justified next line, the
    law used, the hypothesis needed — each step machine-checked), never with free-text proofs."""
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
            # rather than leaving the skill without a lesson. The capstone likewise: a last draft keeps a repaired
            # capstone (giveaway steps dropped) or ends after the practice.
            lesson, pools, notes = validate_lesson(draft, proof=proof, strict=not last)
            capstone, cap_notes = validate_capstone(draft.capstone, strict=not last)
            notes += cap_notes
        except Exception as e:  # noqa: BLE001 — any failure is retried once, then the lesson is marked failed
            error = e
            log.warning("lesson %s attempt %d failed: %s", skill["key"], attempt + 1, e)
            continue
        lesson, figure_ids, figure_notes = resolve_figures(lesson, skill, course_figures(ctx.course))
        notes += figure_notes
        abandoned = persist_lesson(skill_id, lesson, pools, figure_ids=figure_ids, capstone=capstone)
        if abandoned:
            ctx.abandoned_tasks += abandoned
            notes.append(f"abandoned {abandoned} active task{'s' * (abandoned != 1)} started on the previous version")
        for note in notes:
            ctx.tracker.log(f"Lesson {skill['key']}: {note}", level=logging.WARNING)
        n_questions = sum(len(p) for p in pools)
        ctx.tracker.log(
            f"Lesson {skill['key']} ready — {len(pools)} knowledge points, {n_questions} questions, "
            + (f"a {len(capstone.steps)}-step capstone" if capstone else "no capstone")
        )
        return True
    if skill["lesson_status"] == "ready":
        ctx.tracker.log(f"Lesson {skill['key']} kept — regeneration failed: {error}", level=logging.ERROR)
        return False
    db.execute("UPDATE skills SET lesson_status = 'failed' WHERE id = ?", (skill_id,))
    ctx.tracker.log(f"Lesson {skill['key']} failed after {ATTEMPTS} attempts: {error}", level=logging.ERROR)
    return False


def clamp_target(value: Any) -> int:
    low, high = TARGET_RANGE
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        return LEGACY_TARGET


def free_response_problem(kp_index: int, keys: list[str]) -> str:
    """The validation problem for free_response questions in new content (the same text from every check)."""
    return (
        f"knowledge point {kp_index + 1} has free_response question(s) {', '.join(keys)}: new lessons have no written "
        "answers (no 'show your working') — make a computation a typed numeric/expression answer and a proof or "
        "explanation a multistep walkthrough whose steps are machine-checked"
    )


def validate_lesson(
    draft: LessonDraft, *, proof: bool = False, strict: bool = True
) -> tuple[dict[str, Any], list[list[QuestionDraft]], list[str]]:
    """Clean the questions of every knowledge point. Returns (lesson_json dict without questions, question pools per
    KP in serving order, notes). Each KP keeps its target_questions (clamped to 1-3) and needs at least target + 1
    valid questions (target + 2 are requested): the target plus one for after a miss. New content has no
    free_response questions and no common mistakes. strict=True raises InvalidLesson on a free_response question or
    a pool smaller than target + 1; strict=False drops free_response questions and lowers the target to fit the
    pool. A KP with fewer than 2 valid questions is always invalid."""
    kps = draft.knowledge_points[:MAX_KPS]
    if not kps:
        raise InvalidLesson("lesson has no knowledge points")
    notes: list[str] = []
    if len(draft.knowledge_points) > MAX_KPS:
        notes.append(f"kept the first {MAX_KPS} of {len(draft.knowledge_points)} knowledge points")
    pools: list[list[QuestionDraft]] = []
    targets: list[int] = []
    used_keys: set[str] = set()
    for i, kp in enumerate(kps):
        written = [q.key or "?" for q in kp.questions if q.kind == "free_response"]
        if written:
            message = free_response_problem(i, written)
            if strict:
                raise InvalidLesson(message)
            notes.append(f"{message} — dropped")
        kept, dropped = clean_questions([q for q in kp.questions if q.kind != "free_response"], f"KP {i + 1}")
        notes.extend(f"dropped {d}" for d in dropped)
        target = clamp_target(kp.target_questions)
        if target != kp.target_questions:
            notes.append(f"KP {i + 1}: target_questions {kp.target_questions} clamped to {target}")
        if len(kept) < MIN_QUESTIONS_PER_KP:
            raise InvalidLesson(f"knowledge point {i + 1} has only {len(kept)} valid questions")
        if len(kept) < target + 1:
            message = (
                f"knowledge point {i + 1} has {len(kept)} valid questions for target_questions {target} (at least "
                f"{target + 1} are needed — the target plus one for after a miss; write {target + 2})"
            )
            if strict:
                raise InvalidLesson(message)
            target = len(kept) - 1
            notes.append(f"{message} — target lowered to {target}")
        if sum(1 for q in kept if is_machine_checkable(q.kind)) < 2:
            notes.append(f"KP {i + 1} has fewer than 2 machine-checkable questions")
        targets.append(target)
        pools.append(_unique_keys(kept, i, used_keys))
    if proof and not any(q.kind == "multistep" for pool in pools for q in pool):
        notes.append("proof skill without a multistep walkthrough among its practice questions")
    share = choice_share(pools)
    if strict and share > MAX_CHOICE_SHARE_HARD:
        raise InvalidLesson(
            f"{share:.0%} of the questions are multiple choice or true/false (at most 40%); replace most of them with "
            "typed numeric/expression questions (concrete instances) or multistep walkthroughs"
        )
    notes += format_warnings(pools) + pair_form_warnings([q for pool in pools for q in pool])
    lesson = draft.model_dump()
    lesson.pop("capstone", None)  # stored as a question (role 'capstone'), see validate_capstone / persist_lesson
    lesson["knowledge_points"] = [
        {**{k: v for k, v in kp.model_dump().items() if k != "questions"}, "target_questions": target}
        for kp, target in zip(kps, targets)
    ]
    lesson["common_mistakes"] = []  # no "common mistakes" section in new lessons (the learner's review)
    refs = clean_source_refs(draft.source_refs)
    if len(refs) < len(draft.source_refs):
        notes.append(f"kept {len(refs)} of {len(draft.source_refs)} source refs (item numbers only)")
    lesson["source_refs"] = refs
    return lesson, pools, notes


def capstone_problems(q: QuestionDraft | None) -> list[str]:
    """Every problem of a draft capstone ([] = usable as is): a structural one (not multistep, an invalid step) or
    the content rules of capstone_issues (a step whose answer is known before it is computed, a complex result asked
    as a coordinate pair). One revision can fix them all."""
    if q is None:
        return ["capstone: missing — the lesson needs a final multistep problem"]
    try:
        cap = clean_capstone(q)
    except InvalidQuestion as e:
        return [f"capstone: {e}"]
    return [f"capstone: {message}" for _, message in capstone_issues(cap)]


def validate_capstone(q: QuestionDraft | None, *, strict: bool = True) -> tuple[QuestionDraft | None, list[str]]:
    """Validate the lesson's final problem (v1 and v2 lessons, SPEC §4.2b). strict=True raises InvalidLesson on any
    problem (capstone_problems), so the draft is regenerated or revised. strict=False repairs: steps whose answer is
    known before it is computed are dropped while at least 2 steps remain (their check belongs in the explanation);
    a structurally invalid capstone — or one left with fewer than 2 steps — is left out (the lesson ends after the
    practice); a coordinate-pair answer is kept with a note. Returns (the capstone or None, notes)."""
    if q is None:
        if strict:
            raise InvalidLesson("capstone: missing — the lesson needs a final multistep problem")
        return None, ["no capstone — the lesson ends after the practice"]
    try:
        cap = clean_capstone(q)
    except InvalidQuestion as e:
        if strict:
            raise InvalidLesson(f"capstone: {e}") from e
        return None, [f"no valid capstone (capstone: {e}) — the lesson ends after the practice"]
    issues = capstone_issues(cap)
    if not issues:
        return cap, []
    if strict:
        raise InvalidLesson("capstone: " + "; ".join(message for _, message in issues))
    known = sorted({i for i, message in issues if "coordinate pair" not in message})
    notes = [f"capstone: {message}" + (" — step dropped" if i in known else " — kept") for i, message in issues]
    if not known:
        return cap, notes
    steps = [step for i, step in enumerate(cap.steps) if i not in known]
    if len(steps) < 2:
        return None, notes + ["no valid capstone (fewer than 2 steps left) — the lesson ends after the practice"]
    last_dropped = len(cap.steps) - 1 in known
    update: dict[str, Any] = {"steps": steps}
    if last_dropped:
        update["answer_display"] = steps[-1].answer_display
    return cap.model_copy(update=update), notes


def kp_targets(lesson: dict[str, Any]) -> list[int]:
    """target_questions of each stored knowledge point (legacy lessons: 2)."""
    return [clamp_target(kp.get("target_questions", LEGACY_TARGET)) for kp in lesson.get("knowledge_points") or []]


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
    skill_id: str,
    lesson: dict[str, Any],
    pools: list[list[QuestionDraft]],
    *,
    figure_ids: list[str] | None = None,
    capstone: QuestionDraft | None = None,
) -> int:
    """Questions first, then lesson_status='ready' — in one transaction. Questions of an earlier version of the lesson
    are deleted, except those the learner already answered: they are retired (flagged, renamed key) so the attempt
    history stays valid. Active tasks on the skill (started on the old version — possibly while the new one was being
    written, from another process) are abandoned in the same transaction, so no task can point at a deleted question.
    The capstone is stored like a v2 lesson's: role 'capstone', kp_index = the number of knowledge points.
    Returns the number of tasks abandoned."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # the task check and the question swap are one step, also for other processes
        abandoned = abandon_active_tasks(conn, skill_id)
        retire_questions(conn, skill_id)
        for kp_index, pool in enumerate(pools):
            insert_questions(conn, skill_id, kp_index, pool)
        if capstone is not None:
            insert_questions(conn, skill_id, len(pools), [capstone], role="capstone")
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
        "WHERE skill_id = ? AND status = 'active' AND type != 'quiz'",  # a running quiz spans many skills: never closed
        (db.now_iso(), skill_id),
    ).rowcount


# questions an active task has served or is showing (safety net: never delete what a task may still answer)
_VALID_STATE = "CASE WHEN json_valid(t.state_json) THEN t.state_json ELSE '{}' END"
_IN_ACTIVE_TASKS = (
    # a quiz's questions belong to many skills (tasks.skill_id is only its first one), so every active quiz counts
    f"SELECT j.value FROM tasks t, json_each({_VALID_STATE}, '$.served') j WHERE t.status = 'active' "
    "AND (t.skill_id = ? OR t.type = 'quiz') "
    "AND j.value IS NOT NULL "  # NOT IN (…, NULL) would match nothing
    f"UNION SELECT json_extract({_VALID_STATE}, '$.current') FROM tasks t WHERE t.status = 'active' "
    "AND (t.skill_id = ? OR t.type = 'quiz') "
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
                "This is a PROOF skill: practise it with multistep walkthroughs of the proofs (see PROOF SKILLS in the "
                "instructions) — never free_response."
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
