"""Question top-up: more practice questions for a knowledge point when the unseen pool runs low (SPEC §6.4).

    await top_up_questions(skill_id, kp_index=None, n=3) -> int     # generate, validate, verify, insert
    request_top_up(skill_id, kp_index=None) -> None                 # fire-and-forget with in-flight dedup
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from pydantic import BaseModel, Field

from .. import db, llm
from ..llm_schemas import QuestionDraft
from .context import load_course, page_marker
from .lessons import next_question_key
from .prompts import TOPUP_INSTRUCTIONS
from .questions import clean_questions, insert_questions, normalized_prompt
from .verify import keep_kps_servable, needs_verification, verify_question

log = logging.getLogger("ua.pipeline.topup")

MAX_NEW = 6


class TopUpDraft(BaseModel):
    questions: list[QuestionDraft] = Field(
        description="Exactly the requested number of new questions for the knowledge point, keys 'kNqM' as given."
    )


class TopUpUnavailable(LookupError):
    """The skill does not exist or its lesson is not ready."""


async def top_up_questions(skill_id: str, kp_index: int | None = None, n: int = 3) -> int:
    """Generate `n` new questions for one knowledge point (default: the one with the fewest unseen questions),
    verify them and store them. Returns the number of questions added."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if skill is None or skill["lesson_status"] != "ready":
        raise TopUpUnavailable(f"Skill {skill_id} has no ready lesson")
    lesson = db.loads(skill["lesson_json"], {})
    kps = lesson.get("knowledge_points", [])
    if not kps:
        raise TopUpUnavailable(f"Skill {skill_id} has no knowledge points")
    kp = kp_index if kp_index is not None and 0 <= kp_index < len(kps) else neediest_kp(skill_id, len(kps))
    n = max(1, min(MAX_NEW, n))
    existing = db.query("SELECT * FROM questions WHERE skill_id = ? AND kp_index = ? ORDER BY key", (skill_id, kp))
    used_keys = {r["key"] for r in db.query("SELECT key FROM questions WHERE skill_id = ?", (skill_id,))}
    planned_keys = []
    for _ in range(n):
        planned_keys.append(next_question_key(kp, used_keys))
        used_keys.add(planned_keys[-1])

    draft = await llm.structured(
        role="lesson",
        instructions=TOPUP_INSTRUCTIONS,
        text=topup_request(skill, lesson, kp, existing, planned_keys),
        schema=TopUpDraft,
        max_output_tokens=24000,
        label=f"top-up {skill['key']} KP{kp + 1}",
        stream=True,
    )
    kept, dropped = clean_questions(draft.questions, f"top-up {skill['key']} KP {kp + 1}")
    for reason in dropped:
        log.warning("dropped %s", reason)
    seen = {normalized_prompt(r["prompt"]) for r in existing}
    fresh: list[QuestionDraft] = []
    for q, key in zip([q for q in kept if normalized_prompt(q.prompt) not in seen], planned_keys):
        seen.add(normalized_prompt(q.prompt))
        fresh.append(q.model_copy(update={"key": key}))
    if not fresh:
        return 0
    with db.connect() as conn:
        ids = insert_questions(conn, skill_id, kp, fresh)
    marks = ",".join("?" * len(ids))
    rows = db.query(f"SELECT * FROM questions WHERE id IN ({marks})", tuple(ids))
    await asyncio.gather(*(verify_question(r) for r in rows if needs_verification(r)))
    for note in keep_kps_servable(skill_id):
        log.info("top-up %s: %s", skill["key"], note)
    log.info("top-up %s KP %d: added %d questions", skill["key"], kp + 1, len(fresh))
    return len(fresh)


def neediest_kp(skill_id: str, kp_count: int) -> int:
    """The knowledge point with the fewest unflagged questions that were never attempted."""
    rows = db.query(
        "SELECT q.kp_index, COUNT(*) AS n FROM questions q WHERE q.skill_id = ? AND q.flagged = 0 "
        "AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.question_id = q.id) GROUP BY q.kp_index",
        (skill_id,),
    )
    unseen = {r["kp_index"]: r["n"] for r in rows}
    return min(range(kp_count), key=lambda k: (unseen.get(k, 0), k))


def topup_request(
    skill: dict[str, Any], lesson: dict[str, Any], kp: int, existing: list[dict[str, Any]], keys: list[str]
) -> str:
    course = load_course(skill["course_id"])
    point = lesson["knowledge_points"][kp]
    example = point.get("worked_example", {})
    pages = db.loads(skill["source_pages_json"], [])
    marks = ",".join("?" * len(pages))
    passages = (
        db.query(
            f"SELECT page_index, label, markdown FROM pages WHERE course_id = ? AND page_index IN ({marks}) "
            "ORDER BY page_index",
            (course.id, *pages),
        )
        if pages
        else []
    )
    lines = [
        f"Write {len(keys)} new questions with keys {', '.join(keys)}.",
        "",
        f"Skill {skill['key']}: {skill['title']}",
        f"Objective: {skill['objective']}",
        "",
        f"Knowledge point {kp + 1}: {point.get('title', '')}",
        point.get("explanation", ""),
        "",
        f"Worked example: {example.get('problem', '')}",
    ]
    lines += [f"- {s.get('math', '')} ({s.get('why', '')})" for s in example.get("steps", [])]
    lines += [f"Takeaway: {example.get('takeaway', '')}", "", "Existing questions (do not repeat or reword):"]
    lines += [f"- [{r['kind']}, tier {r['tier']}] {r['prompt']}" for r in existing] or ["(none)"]
    if passages:
        lines += ["", "Source passage:"]
        for p in passages:
            lines += [page_marker(p["page_index"], p["label"] or course.label(p["page_index"])), p["markdown"]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fire-and-forget scheduling
# ---------------------------------------------------------------------------

_in_flight: set[tuple[str, int | None]] = set()
_lock = threading.Lock()
_tasks: set[asyncio.Task] = set()
_loop: asyncio.AbstractEventLoop | None = None


def remember_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called from async entry points so sync callers (FastAPI threadpool endpoints) can schedule top-ups."""
    global _loop
    _loop = loop


def request_top_up(skill_id: str, kp_index: int | None = None) -> None:
    """Schedule a background top-up for the skill unless one is already running for the same knowledge point."""
    key = (skill_id, kp_index)
    with _lock:
        if key in _in_flight:
            return
        _in_flight.add(key)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        task = running.create_task(_run(key))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    elif _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_run(key), _loop)
    else:
        threading.Thread(target=lambda: asyncio.run(_run(key)), name=f"top-up-{skill_id}", daemon=True).start()


async def _run(key: tuple[str, int | None]) -> None:
    try:
        await top_up_questions(key[0], key[1])
    except Exception:  # noqa: BLE001 — background work: log, never raise into the event loop
        log.exception("background top-up for skill %s failed", key[0])
    finally:
        with _lock:
            _in_flight.discard(key)
