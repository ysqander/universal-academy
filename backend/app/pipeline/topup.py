"""Question top-up: more practice questions for a knowledge point when the unseen pool runs low (SPEC §6.4).

    await top_up_questions(skill_id, kp_index=None, n=3) -> int     # generate, validate, insert held, verify, release
    request_top_up(skill_id, kp_index=None) -> None                 # fire-and-forget with in-flight dedup
    await top_up_capstone(skill_id) -> int                          # a new capstone variant once every one was answered
    request_capstone_top_up(skill_id) -> None                       # fire-and-forget with in-flight dedup
    release_stale_holds(skill_ids=None) -> int                      # holds left by an interrupted top-up → servable

New machine-checkable questions are inserted flagged 'verification: pending', so they are never served before the
verifier has checked them; verification then writes the final flag (a failed verification call releases the
question, like the build does). One top-up per (skill, knowledge point) runs at a time — the capstone counts as the
point after the last one (its kp_index); keys and duplicate prompts are re-checked inside the insert transaction.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import Counter
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from .. import db, llm
from ..llm_schemas import QuestionDraft, TeachingSpec
from .context import load_course, page_marker
from .lesson_v2 import render_lesson, rewrite_capstone, stored_questions, stored_spec
from .lessons import is_proof_skill, lesson_prefix, next_question_key
from .. import prompts
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
    with _tracked(skill_id, f"top-up {skill['key']} KP {kp + 1}"):
        async with _kp_lock(skill_id, kp):
            return await _top_up(skill, lesson, kp, n)


@contextmanager
def _tracked(skill_id: str, label: str) -> Iterator[None]:
    """A top-up running in this process (its held rows are not stale), with its LLM spend logged."""
    usage = llm.current_usage.get()
    token = llm.current_usage.set(llm.Usage()) if usage is None else None
    with _running_guard:
        _running[skill_id] += 1
    try:
        yield
    finally:
        with _running_guard:
            _running[skill_id] -= 1
            if _running[skill_id] <= 0:
                del _running[skill_id]
        if token is not None:
            spent = llm.current_usage.get()
            llm.current_usage.reset(token)
            if spent and spent.calls:
                log.info("%s: %d LLM calls · $%.4f", label, spent.calls, spent.cost_usd)


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
        instructions=prompts.for_role("lesson").TOPUP_INSTRUCTIONS,
        text=topup_request(skill, lesson, kp, existing, planned_keys),
        schema=TopUpDraft,
        max_output_tokens=24000,
        label=f"top-up {skill['key']} KP{kp + 1}",
        stream=True,
    )
    written = [q.key for q in draft.questions if q.kind == "free_response"]
    if written:  # new content has no written answers (no "show your working"): SPEC §4.2b
        log.warning("top-up %s KP %d: dropped free_response question(s) %s", skill["key"], kp + 1, ", ".join(written))
    kept, dropped = clean_questions(
        [q for q in draft.questions if q.kind != "free_response"], f"top-up {skill['key']} KP {kp + 1}"
    )
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


# A retake must not repeat a capstone the learner answered (docs/DECISIONS.md §5): the capstone writer gets this as the
# "problem" with the current capstone, followed by the answered ones.
VARIANT_PROBLEM = (
    "the learner has answered the capstone(s) below, so a retake of this lesson needs a NEW VARIANT: the same "
    "decisions and step structure (the same step kinds in the same order), new numbers or a new case — never a copy "
    "or a relabelling of an answered capstone"
)


def _fresh_capstone_exists(skill_id: str) -> bool | None:
    """Is a capstone never answered by the learner servable or held for verification? None: the skill has none."""
    caps = db.query(
        "SELECT id, flagged, flag_reason FROM questions WHERE skill_id = ? AND role = 'capstone' "
        "AND key NOT LIKE 'old-%'",
        (skill_id,),
    )
    if not caps:
        return None
    rows = db.query("SELECT DISTINCT question_id FROM attempts WHERE skill_id = ? AND status != 'voided'", (skill_id,))
    answered = {r["question_id"] for r in rows}
    return any(r["id"] not in answered and (not r["flagged"] or r["flag_reason"] == PENDING_REASON) for r in caps)


async def top_up_capstone(skill_id: str) -> int:
    """Write one new capstone variant when the learner has answered every capstone of the skill (v2 lessons: the
    capstone writer, validated like a capstone repair), insert it held, verify it and release it. Returns 1 when a
    variant became servable, else 0."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if skill is None or skill["lesson_status"] != "ready":
        raise TopUpUnavailable(f"Skill {skill_id} has no ready lesson")
    lesson = db.loads(skill["lesson_json"], {}) or {}
    spec = stored_spec(lesson)
    if spec is None or _fresh_capstone_exists(skill_id) is not False:
        return 0  # a v1 lesson, no capstone at all, or one still unanswered
    kp = len(lesson.get("knowledge_points") or [])  # the capstone's kp_index
    with _tracked(skill_id, f"capstone top-up {skill['key']}"):
        async with _kp_lock(skill_id, kp):
            if _fresh_capstone_exists(skill_id) is not False:  # another top-up wrote one meanwhile
                return 0
            return await _top_up_capstone(skill, lesson, spec, kp)


async def _top_up_capstone(skill: dict[str, Any], lesson: dict[str, Any], spec: TeachingSpec, kp: int) -> int:
    skill_id = skill["id"]
    course = load_course(skill["course_id"])
    pools, current = stored_questions(skill_id)
    answered = db.query(
        "SELECT * FROM questions WHERE skill_id = ? AND role = 'capstone' AND key NOT LIKE 'old-%' AND EXISTS "
        "(SELECT 1 FROM attempts a WHERE a.question_id = questions.id AND a.status != 'voided') ORDER BY rowid",
        (skill_id,),
    )
    problems = [VARIANT_PROBLEM + ":"] + [
        f"answered {r['key']}: {r['prompt']} Steps: "
        + " | ".join(str(s.get("prompt", "")) for s in db.loads(r["steps_json"], []) or [])
        for r in answered
    ]
    draft = await rewrite_capstone(
        course, skill, spec, render_lesson(lesson, pools, current), lesson_prefix(course), problems
    )
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        sql = "SELECT key, prompt FROM questions WHERE skill_id = ? AND role = 'capstone'"
        rows = conn.execute(sql, (skill_id,)).fetchall()
        if normalized_prompt(draft.prompt) in {normalized_prompt(r["prompt"]) for r in rows}:
            log.warning("capstone top-up %s: the variant repeats an existing capstone — dropped", skill["key"])
            return 0
        used, n = {r["key"] for r in rows}, 2
        while f"cap{n}" in used:
            n += 1
        ids = insert_questions(
            conn, skill_id, kp, [draft.model_copy(update={"key": f"cap{n}"})], pending=True, role="capstone"
        )
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (ids[0],))
    if await verify_question(row) is None:  # verification call failed twice: servable unverified, as in the build
        db.execute(
            "UPDATE questions SET flagged = 0, flag_reason = '' WHERE id = ? AND flag_reason = ?",
            (ids[0], PENDING_REASON),
        )
    row = db.query_one("SELECT flagged, flag_reason FROM questions WHERE id = ?", (ids[0],))
    if row["flagged"]:
        log.warning("capstone top-up %s: cap%d withheld — %s", skill["key"], n, row["flag_reason"])
        return 0
    log.info("capstone top-up %s: added cap%d", skill["key"], n)
    return 1


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
                "This is a proof skill: make at least one of the new questions a multistep walkthrough of a proof of a "
                "statement of this knowledge point (2–4 machine-checked steps: which definition applies, the next "
                "justified line, which law justifies it)."
            ),
        ]
    lines += ["", "Existing questions (do not repeat or reword them, and do not make the same demand again):"]
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
_capstones_in_flight: set[str] = set()
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
    _schedule(lambda: _run(key), f"top-up-{skill_id}")


def request_capstone_top_up(skill_id: str) -> None:
    """Schedule a background capstone top-up for the skill unless one is already running for it."""
    with _lock:
        if skill_id in _capstones_in_flight:
            return
        _capstones_in_flight.add(skill_id)
    _schedule(lambda: _run_capstone(skill_id), f"capstone-top-up-{skill_id}")


def _schedule(work: Callable[[], Coroutine[Any, Any, None]], name: str) -> None:
    """Run `work` on the running loop, on the remembered server loop, or else in a thread of its own."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        _spawn(work, name)
    elif _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_spawn, work, name)
    else:
        threading.Thread(target=lambda: asyncio.run(work()), name=name, daemon=True).start()


def _spawn(work: Callable[[], Coroutine[Any, Any, None]], name: str) -> None:
    """Start the top-up as a task of the running loop (tracked, so shutdown can cancel it)."""
    task = asyncio.get_running_loop().create_task(work(), name=name)
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


async def _run_capstone(skill_id: str) -> None:
    try:
        await top_up_capstone(skill_id)
    except Exception:  # noqa: BLE001 — background work: log, never raise into the event loop
        log.exception("background capstone top-up for skill %s failed", skill_id)
    finally:
        with _lock:
            _capstones_in_flight.discard(skill_id)
