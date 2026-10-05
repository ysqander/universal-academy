"""Question top-up: more practice questions for a knowledge point when the unseen pool runs low (SPEC §6.4).

    await top_up_questions(skill_id, kp_index=None, n=3) -> int     # generate, validate, insert held, verify, release
    request_top_up(skill_id, kp_index=None) -> None                 # fire-and-forget with in-flight dedup
    release_stale_holds(skill_ids=None) -> int                      # holds left by an interrupted top-up → servable

New machine-checkable questions are inserted flagged 'verification: pending', so they are never served before the
verifier has checked them; verification then writes the final flag (a failed verification call releases the
question, like the build does). One top-up per (skill, knowledge point) runs at a time; keys and duplicate prompts
are re-checked inside the insert transaction.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from .. import db, llm
from ..llm_schemas import QuestionDraft
from .context import load_course, page_marker
from .lessons import is_proof_skill, next_question_key
from .prompts import TOPUP_INSTRUCTIONS
from .questions import PENDING_REASON, clean_questions, insert_questions, length_cue, normalized_prompt
from .verify import keep_kps_servable, needs_verification, verify_question

log = logging.getLogger("ua.pipeline.topup")

MAX_NEW = 6


class TopUpDraft(BaseModel):
    questions: list[QuestionDraft] = Field(
        description="Exactly the requested number of new questions for the knowledge point, keys 'kNqM' as given."
    )


class TopUpUnavailable(LookupError):
    """The skill does not exist or its lesson is not ready."""


_kp_locks: dict[tuple[str, int], tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
_kp_locks_guard = threading.Lock()
_running: Counter[str] = Counter()  # skill id → top-ups executing in this process (their held rows are not stale)
_running_guard = threading.Lock()
STALE_HOLD_S = 600.0  # a held question this old has outlived any top-up (another process may run one meanwhile)


def _kp_lock(skill_id: str, kp: int) -> asyncio.Lock:
    """One top-up per (skill, knowledge point) per event loop; other loops are serialized by the DB transaction."""
    loop = asyncio.get_running_loop()
    with _kp_locks_guard:
        entry = _kp_locks.get((skill_id, kp))
        if entry is None or entry[0] is not loop or entry[0].is_closed():
            entry = (loop, asyncio.Lock())
            _kp_locks[(skill_id, kp)] = entry
        return entry[1]


async def top_up_questions(skill_id: str, kp_index: int | None = None, n: int = 3) -> int:
    """Generate `n` new questions for one knowledge point (default: the one with the fewest unseen questions),
    verify them and make them servable. Returns the number of questions added."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if skill is None or skill["lesson_status"] != "ready":
        raise TopUpUnavailable(f"Skill {skill_id} has no ready lesson")
    lesson = db.loads(skill["lesson_json"], {})
    kps = lesson.get("knowledge_points", [])
    if not kps:
        raise TopUpUnavailable(f"Skill {skill_id} has no knowledge points")
    kp = kp_index if kp_index is not None and 0 <= kp_index < len(kps) else neediest_kp(skill_id, len(kps))
    n = max(1, min(MAX_NEW, n))
    usage = llm.current_usage.get()
    token = llm.current_usage.set(llm.Usage()) if usage is None else None
    with _running_guard:
        _running[skill_id] += 1
    try:
        async with _kp_lock(skill_id, kp):
            added = await _top_up(skill, lesson, kp, n)
    finally:
        with _running_guard:
            _running[skill_id] -= 1
            if _running[skill_id] <= 0:
                del _running[skill_id]
        if token is not None:
            spent = llm.current_usage.get()
            llm.current_usage.reset(token)
            if spent and spent.calls:
                log.info("top-up %s KP %d: %d LLM calls · $%.4f", skill["key"], kp + 1, spent.calls, spent.cost_usd)
    return added


async def _top_up(skill: dict[str, Any], lesson: dict[str, Any], kp: int, n: int) -> int:
    skill_id = skill["id"]
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
    for q in kept:
        if length_cue(q):
            log.warning("top-up %s %s: correct option much longer than the distractors", skill["key"], q.key)
    if not kept:
        return 0
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # keys and prompts are re-read under the write lock (other threads/loops)
        rows = conn.execute("SELECT key, kp_index, prompt FROM questions WHERE skill_id = ?", (skill_id,)).fetchall()
        used = {r["key"] for r in rows}
        seen = {normalized_prompt(r["prompt"]) for r in rows if r["kp_index"] == kp}
        fresh: list[QuestionDraft] = []
        for q in kept:
            prompt_key = normalized_prompt(q.prompt)
            if prompt_key in seen or len(fresh) >= n:
                continue
            seen.add(prompt_key)
            key = q.key.strip()
            if not re.fullmatch(rf"k{kp + 1}q\d+", key) or key in used:
                key = next_question_key(kp, used)
            used.add(key)
            fresh.append(q.model_copy(update={"key": key}))
        ids = insert_questions(conn, skill_id, kp, fresh, pending=True) if fresh else []
    if not ids:
        return 0
    # the new questions, plus any of this KP still held from an interrupted earlier top-up
    rows = db.query("SELECT * FROM questions WHERE skill_id = ? AND kp_index = ?", (skill_id, kp))
    checked = [r for r in rows if needs_verification(r)]
    verdicts = await asyncio.gather(*(verify_question(r) for r in checked))
    for row, verdict in zip(checked, verdicts):
        if verdict is None:  # verification call failed twice: servable unverified, as in the build
            db.execute(
                "UPDATE questions SET flagged = 0, flag_reason = '' WHERE id = ? AND flag_reason = ?",
                (row["id"], PENDING_REASON),
            )
    for note in keep_kps_servable(skill_id):
        log.info("top-up %s: %s", skill["key"], note)
    log.info("top-up %s KP %d: added %d questions", skill["key"], kp + 1, len(ids))
    return len(ids)


def release_stale_holds(skill_ids: list[str] | None = None, *, older_than_s: float = STALE_HOLD_S) -> int:
    """Questions left held as 'verification: pending' without a verdict — by a top-up that was interrupted (server
    stopped) or by an older version of the regeneration tool — become servable unverified, as when a verification call
    fails; the next resume verifies them. Holds of top-ups running in this process, and recent ones (a top-up in
    another process), are kept. `skill_ids=None`: every course. Returns the number of questions released."""
    cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat(timespec="seconds")
    with _running_guard:
        busy = set(_running)
    sql = "SELECT id, skill_id FROM questions WHERE flagged = 1 AND flag_reason = ? AND verify_json = '{}' AND created_at <= ?"
    params: list[Any] = [PENDING_REASON, cutoff]
    if skill_ids is not None:
        if not skill_ids:
            return 0
        sql += f" AND skill_id IN ({','.join('?' * len(skill_ids))})"
        params += skill_ids
    ids = [r["id"] for r in db.query(sql, tuple(params)) if r["skill_id"] not in busy]
    for qid in ids:
        db.execute(
            "UPDATE questions SET flagged = 0, flag_reason = '' WHERE id = ? AND flag_reason = ? AND verify_json = '{}'",
            (qid, PENDING_REASON),
        )
    if ids:
        log.info("released %d question(s) left held for verification", len(ids))
    return len(ids)


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
    lines += [f"Takeaway: {example.get('takeaway', '')}"]
    if is_proof_skill(skill):
        lines += [
            "",
            (
                "This is a proof skill: make at least one of the new questions a free_response proof of a statement "
                "of this knowledge point, with a model proof as the solution and a checklist of 2–5 concrete criteria."
            ),
        ]
    lines += ["", "Existing questions (do not repeat or reword):"]
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


def _resolve_kp(skill_id: str, kp_index: int | None) -> int | None:
    if kp_index is not None:
        return kp_index
    try:
        row = db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (skill_id,))
        kps = db.loads(row["lesson_json"], {}).get("knowledge_points", []) if row else []
        return neediest_kp(skill_id, len(kps)) if kps else None
    except Exception:  # noqa: BLE001 — resolution is only an optimisation of the dedup key
        return None


def request_top_up(skill_id: str, kp_index: int | None = None) -> None:
    """Schedule a background top-up for the skill unless one is already running for the same knowledge point."""
    key = (skill_id, _resolve_kp(skill_id, kp_index))
    with _lock:
        if key in _in_flight:
            return
        _in_flight.add(key)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        _spawn(key)
    elif _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_spawn, key)
    else:
        threading.Thread(target=lambda: asyncio.run(_run(key)), name=f"top-up-{skill_id}", daemon=True).start()


def _spawn(key: tuple[str, int | None]) -> None:
    """Start the top-up as a task of the running loop (tracked, so shutdown can cancel it)."""
    task = asyncio.get_running_loop().create_task(_run(key), name=f"top-up-{key[0]}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def shutdown(timeout: float = 5.0) -> None:
    """Server shutdown: cancel background top-ups of this loop. Questions still held as 'verification: pending' stay
    out of serving until a resume or a later top-up of their knowledge point verifies them, or release_stale_holds
    (next server start) releases them."""
    tasks = [t for t in _tasks if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


async def _run(key: tuple[str, int | None]) -> None:
    try:
        await top_up_questions(key[0], key[1])
    except Exception:  # noqa: BLE001 — background work: log, never raise into the event loop
        log.exception("background top-up for skill %s failed", key[0])
    finally:
        with _lock:
            _in_flight.discard(key)
