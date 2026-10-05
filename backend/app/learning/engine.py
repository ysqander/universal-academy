"""Study task engine: lesson and review tasks (SPEC §6.3, §6.4).

A task's mutable state lives in tasks.state_json:

lesson  {"kp_total", "kp", "playable": [kp...], "current", "revealed", "served": [qid...],
         "slots": {"<kp>": [qid...]},          # questions counted toward that KP's 5-question limit
         "log": [{"q", "kp", "res", "void"}],  # res: correct (independent) | assisted | wrong
         "wrong_run", "self_grades": {qid: grade}, "fail": {...} | None, "result": {...}}
review  {"set": [qid...], "pos", "current", "revealed", "served", "log": [{"q", "res", "credit", "void"}],
         "self_grades", "result": {...}}

Every mutating function takes an explicit `now` and serializes through `lock` (single-process app; SQLite
writes are short and each step is a handful of statements). Reads (get_task, build_task) need neither.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fsrs import Rating

from .. import db, views
from ..schemas import AnswerResult, AnswerSubmit, RevealResponse, Task, TaskProgress
from . import grading, scheduler, xp
from . import state as skill_state

log = logging.getLogger("ua.learning.engine")

NEEDED_IN_ROW = 2
MAX_KP_QUESTIONS = 5
REVIEW_SIZE = 3
LESSON_COOLDOWN = timedelta(hours=2)
REMEDIATION_AFTER_FAILURES = 2
EASIER_AFTER_WRONG = 2
TOPUP_DEDUPE_SECONDS = 600.0

lock = threading.RLock()


class StudyError(Exception):
    status_code = 400


class NotFound(StudyError):
    status_code = 404


class Conflict(StudyError):
    status_code = 409


class Invalid(StudyError):
    status_code = 422


@dataclass
class _Finish:
    xp: int = 0
    suggestion: str | None = None
    suggestion_text: str | None = None


@dataclass
class _Graded:
    correct: bool
    status: str
    independent: bool
    feedback: str
    res: str
    response: str
    revealed: bool


# ---------------------------------------------------------------------------
# Row access
# ---------------------------------------------------------------------------


def _task_row(task_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise NotFound("Task not found.")
    return row


def _skill_row(skill_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if not row:
        raise NotFound("Skill not found.")
    return row


def _question_row(question_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if not row:
        raise NotFound("Question not found.")
    return row


def _progress_row(skill: dict[str, Any]) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (skill["id"],))
    if row is None:
        skill_state.init_progress(skill["course_id"])
        row = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (skill["id"],))
    assert row is not None
    return row


def _questions(skill_id: str) -> list[dict[str, Any]]:
    """Pool order = insertion order within each knowledge point."""
    return db.query("SELECT * FROM questions WHERE skill_id = ? ORDER BY kp_index, rowid", (skill_id,))


def _answered_ids(skill_id: str) -> dict[str, str]:
    """question id -> last time the learner answered it (voided attempts excluded)."""
    rows = db.query(
        "SELECT question_id, MAX(created_at) AS last FROM attempts WHERE skill_id = ? AND status != 'voided' GROUP BY question_id",
        (skill_id,),
    )
    return {r["question_id"]: r["last"] for r in rows}


def active_task_row(course_id: str) -> dict[str, Any] | None:
    return db.query_one(
        "SELECT * FROM tasks WHERE course_id = ? AND status = 'active' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (course_id,),
    )


def _save_state(task_id: str, state: dict[str, Any], **columns: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in ("state_json", *columns))
    db.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (db.dumps(state), *columns.values(), task_id))


def _complete(task: dict[str, Any], st: dict[str, Any], outcome: str, xp_awarded: int, now: datetime) -> None:
    st["current"] = None
    _save_state(
        task["id"], st, status="completed", phase="done", outcome=outcome, xp_awarded=xp_awarded, completed_at=scheduler.iso(now)
    )
    task.update(status="completed", phase="done", outcome=outcome, xp_awarded=xp_awarded)


def _answer_display(q: dict[str, Any]) -> str:
    if q["answer_display"]:
        return q["answer_display"]
    choices = db.loads(q["choices_json"], [])
    if q["correct_choice"] is not None and 0 <= q["correct_choice"] < len(choices):
        return choices[q["correct_choice"]]
    return q["answer"]


# ---------------------------------------------------------------------------
# Question top-ups (pipeline)
# ---------------------------------------------------------------------------

_topup_requested: dict[tuple[str, int | None], float] = {}


def request_top_up(skill_id: str, kp_index: int | None = None) -> None:
    """Ask the pipeline for fresh questions (it runs them in the background); at most once per 10 min per KP."""
    key = (skill_id, kp_index)
    now = time.monotonic()
    if now - _topup_requested.get(key, -TOPUP_DEDUPE_SECONDS) < TOPUP_DEDUPE_SECONDS:
        return
    _topup_requested[key] = now
    try:
        from ..pipeline.topup import request_top_up as pipeline_top_up
    except ImportError:
        log.info("question top-up unavailable (no pipeline.topup); skill %s kp %s", skill_id, kp_index)
        return
    try:
        pipeline_top_up(skill_id, kp_index)
    except Exception:
        log.exception("question top-up request failed for skill %s kp %s", skill_id, kp_index)
        return
    log.info("requested question top-up for skill %s kp %s", skill_id, kp_index)


def _unseen_count(skill_id: str, *, kp: int | None = None, exclude: Collection[str] = ()) -> int:
    answered = _answered_ids(skill_id)
    return sum(
        1
        for q in _questions(skill_id)
        if not q["flagged"] and q["id"] not in answered and q["id"] not in exclude and (kp is None or q["kp_index"] == kp)
    )


# ---------------------------------------------------------------------------
# Task views
# ---------------------------------------------------------------------------


def _kp_entries(st: dict[str, Any], kp: int) -> list[dict[str, Any]]:
    return [e for e in st["log"] if e["kp"] == kp and not e.get("void")]


def _trailing(entries: list[dict[str, Any]], res: str) -> int:
    count = 0
    for e in reversed(entries):
        if e["res"] != res:
            break
        count += 1
    return count


def _live(st: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in st["log"] if not e.get("void")]


def _progress_view(task: dict[str, Any], st: dict[str, Any], skill_id: str) -> TaskProgress:
    live = _live(st)
    answered = len(live)
    correct = sum(1 for e in live if e["res"] in ("correct", "assisted"))
    if task["type"] == "review":
        return TaskProgress(
            kp_index=0, kp_total=1, streak=_trailing(live, "correct"), needed_in_row=NEEDED_IN_ROW,
            kp_questions_used=answered, kp_questions_available=len(st["set"]),
            answered=answered, correct=correct, total_questions=len(st["set"]),
        )  # fmt: skip
    kp_total = st["kp_total"]
    kp = min(st["kp"], kp_total - 1)
    passed = task["outcome"] == "passed"
    available = sum(1 for q in _questions(skill_id) if q["kp_index"] == kp and not q["flagged"])
    return TaskProgress(
        kp_index=kp, kp_total=kp_total,
        streak=NEEDED_IN_ROW if passed else _trailing(_kp_entries(st, kp), "correct"),
        needed_in_row=NEEDED_IN_ROW, kp_questions_used=len(st["slots"].get(str(kp), [])),
        kp_questions_available=available, answered=answered, correct=correct, total_questions=0,
    )  # fmt: skip


def build_task(task: dict[str, Any]) -> Task:
    st = db.loads(task["state_json"], {})
    skill = _skill_row(task["skill_id"])
    question = None
    if task["status"] == "active" and st.get("current"):
        qrow = db.query_one("SELECT * FROM questions WHERE id = ?", (st["current"],))
        question = views.question_public(qrow) if qrow else None
    if task["status"] == "completed":
        xp_value = task["xp_awarded"]
    elif task["type"] == "lesson":
        xp_value = xp.lesson_xp_value(skill["est_minutes"])
    else:
        xp_value = xp.REVIEW_XP_PASS
    return Task(
        id=task["id"],
        course_id=task["course_id"],
        type=task["type"],
        phase=task["phase"],
        skill=views.skill_ref(skill),
        reason=task["reason"],
        xp_value=xp_value,
        lesson=views.lesson_view(skill["id"]) if task["type"] == "lesson" else None,
        question=question,
        progress=_progress_view(task, st, skill["id"]),
        outcome=task["outcome"],
    )


def get_task(task_id: str) -> Task:
    return build_task(_task_row(task_id))


# ---------------------------------------------------------------------------
# Starting tasks
# ---------------------------------------------------------------------------


def _insert_task(course_id: str, skill_id: str, type_: str, phase: str, reason: str, st: dict[str, Any], now: datetime) -> str:
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, phase, reason, state_json, created_at)"
        " VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
        (task_id, course_id, skill_id, type_, phase, reason, db.dumps(st), scheduler.iso(now)),
    )
    return task_id


def start_lesson(skill_id: str, reason: str, now: datetime) -> str:
    with lock:
        return _start_lesson(skill_id, reason, now)


def _start_lesson(skill_id: str, reason: str, now: datetime) -> str:
    skill = _skill_row(skill_id)
    if skill["lesson_status"] != "ready" or not skill["lesson_json"]:
        raise Conflict("The lesson for this skill is still being generated.")
    kp_total = len(db.loads(skill["lesson_json"], {}).get("knowledge_points", []))
    pools = {q["kp_index"] for q in _questions(skill_id) if not q["flagged"]}
    playable = [k for k in range(kp_total) if k in pools]
    if not playable:
        raise Conflict("This lesson has no practice questions yet.")
    st = {
        "kp_total": kp_total, "kp": playable[0], "playable": playable, "current": None, "revealed": False,
        "served": [], "slots": {}, "log": [], "wrong_run": 0, "self_grades": {}, "fail": None, "result": {},
    }  # fmt: skip
    task_id = _insert_task(skill["course_id"], skill_id, "lesson", "learn", reason, st, now)
    progress = _progress_row(skill)
    if progress["status"] not in scheduler.LEARNED:
        db.execute(
            "UPDATE progress SET status = 'in_progress', lesson_attempts = lesson_attempts + 1, updated_at = ? WHERE skill_id = ?",
            (scheduler.iso(now), skill_id),
        )
    log.info("started lesson task %s for skill %s (%s)", task_id, skill["key"], reason)
    return task_id


def _review_set(skill_id: str) -> list[str]:
    """One question per knowledge point where possible: never answered first, then least recently answered."""
    answered = _answered_ids(skill_id)
    pool = [q for q in _questions(skill_id) if not q["flagged"]]
    pool.sort(key=lambda q: (q["id"] in answered, answered.get(q["id"], "")))  # stable: pool order breaks ties
    chosen: list[dict[str, Any]] = []
    for kp in sorted({q["kp_index"] for q in pool}):
        best = next((q for q in pool if q["kp_index"] == kp), None)
        if best and len(chosen) < REVIEW_SIZE:
            chosen.append(best)
    for q in pool:
        if len(chosen) >= REVIEW_SIZE:
            break
        if q not in chosen:
            chosen.append(q)
    chosen.sort(key=lambda q: (q["kp_index"], q["tier"]))
    return [q["id"] for q in chosen]


def start_review(skill_id: str, reason: str, now: datetime) -> str:
    with lock:
        return _start_review(skill_id, reason, now)


def _start_review(skill_id: str, reason: str, now: datetime) -> str:
    skill = _skill_row(skill_id)
    if _progress_row(skill)["status"] not in scheduler.LEARNED:
        raise Conflict("Reviews start after the lesson has been passed.")
    qids = _review_set(skill_id)
    if not qids:
        raise Conflict("There are no review questions for this skill right now.")
    st = {"set": qids, "pos": 0, "current": qids[0], "revealed": False, "served": list(qids), "log": [], "self_grades": {}, "result": {}}
    task_id = _insert_task(skill["course_id"], skill_id, "review", "practice", reason, st, now)
    if _unseen_count(skill_id, exclude=set(qids)) < REVIEW_SIZE:
        request_top_up(skill_id)
    log.info("started review task %s for skill %s (%s)", task_id, skill["key"], reason)
    return task_id


def abandon(task_id: str, now: datetime) -> Task:
    with lock:
        task = _task_row(task_id)
        if task["status"] == "active":
            _complete(task, db.loads(task["state_json"], {}), "abandoned", 0, now)
            log.info("abandoned task %s", task_id)
        return build_task(task)


# ---------------------------------------------------------------------------
# Lesson flow
# ---------------------------------------------------------------------------


def _pick_lesson_question(skill_id: str, kp: int, served: set[str]) -> dict[str, Any] | None:
    answered = _answered_ids(skill_id)
    pool = [q for q in _questions(skill_id) if q["kp_index"] == kp and not q["flagged"] and q["id"] not in served]
    pool.sort(key=lambda q: q["id"] in answered)  # never-seen questions first on a retry, pool order otherwise
    return pool[0] if pool else None


def _serve_lesson_question(st: dict[str, Any], skill_id: str) -> bool:
    q = _pick_lesson_question(skill_id, st["kp"], set(st["served"]))
    if q is None:
        return False
    st["current"] = q["id"]
    st["revealed"] = False
    st["served"].append(q["id"])
    st["slots"].setdefault(str(st["kp"]), []).append(q["id"])
    return True


def _can_still_pass(st: dict[str, Any], skill_id: str) -> bool:
    """Could the current KP still reach 2 in a row within its limits (before serving the next question)?"""
    kp = st["kp"]
    streak = _trailing(_kp_entries(st, kp), "correct")
    used = len(st["slots"].get(str(kp), []))
    served = set(st["served"])
    unserved = sum(1 for q in _questions(skill_id) if q["kp_index"] == kp and not q["flagged"] and q["id"] not in served)
    return streak + min(MAX_KP_QUESTIONS - used, unserved) >= NEEDED_IN_ROW


def begin_practice(task_id: str, now: datetime) -> Task:
    with lock:
        task = _task_row(task_id)
        if task["status"] != "active":
            raise Conflict("This task is already finished.")
        if task["phase"] == "learn":
            st = db.loads(task["state_json"], {})
            if not _serve_first_from(st, task["skill_id"]):
                raise Conflict("No practice questions are available for this lesson right now.")
            _save_state(task_id, st, phase="practice")
            task = _task_row(task_id)
        return build_task(task)


def _serve_first_from(st: dict[str, Any], skill_id: str) -> bool:
    """Serve from the current KP, skipping KPs whose pool became empty (all flagged)."""
    for kp in [k for k in st["playable"] if k >= st["kp"]]:
        st["kp"] = kp
        if _serve_lesson_question(st, skill_id):
            return True
    return False


def _lesson_progress(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime, *, count_failure: bool = True
) -> _Finish | None:
    """After an answer (or a voided one): pass the KP, fail the lesson, or serve the next question."""
    kp = st["kp"]
    if _trailing(_kp_entries(st, kp), "correct") >= NEEDED_IN_ROW:
        later = [k for k in st["playable"] if k > kp]
        st["wrong_run"] = 0
        if later:
            st["kp"] = later[0]
            if st.get("current") and st["current"] in st["slots"].get(str(kp), []):
                st["slots"][str(kp)].remove(st["current"])  # served for the KP that has just been passed
            st["current"] = None
            if _serve_first_from(st, skill["id"]):
                return None
            request_top_up(skill["id"])
        return _finish_lesson_pass(task, st, skill, now)
    if st.get("current"):
        return None
    if not _can_still_pass(st, skill["id"]):
        return _finish_lesson_fail(task, st, skill, now, counted=count_failure)
    _serve_lesson_question(st, skill["id"])
    return None


def _finish_lesson_pass(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> _Finish:
    wrong = sum(1 for e in _live(st) if e["res"] == "wrong")
    amount = xp.lesson_xp(skill["est_minutes"], wrong)
    _, log_id = scheduler.apply_rating(skill["course_id"], skill["id"], Rating.Good, now)
    db.execute(
        "UPDATE progress SET lesson_passed_at = ?, cooldown_until = NULL, updated_at = ? WHERE skill_id = ?",
        (scheduler.iso(now), scheduler.iso(now), skill["id"]),
    )
    st["kp"] = st["kp_total"]
    st["result"] = {"review_log_id": log_id, "wrong": wrong}
    _complete(task, st, "passed", amount, now)
    xp.award(skill["course_id"], task["id"], amount, now)
    scheduler.apply_success_credit(skill["course_id"], skill["id"], now)
    scheduler.clear_remedial(target=skill["id"])
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("lesson passed: skill %s, %d XP", skill["key"], amount)
    return _Finish(xp=amount)


def _finish_lesson_fail(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime, *, counted: bool = True
) -> _Finish:
    kp = st["kp"]
    progress = _progress_row(skill)
    learned = progress["status"] in scheduler.LEARNED
    failures: dict[str, int] = db.loads(progress["kp_failures_json"], {})
    counted = counted and not learned
    if counted:
        failures[str(kp)] = failures.get(str(kp), 0) + 1
    if not learned:
        db.execute(
            "UPDATE progress SET kp_failures_json = ?, cooldown_until = ?, updated_at = ? WHERE skill_id = ?",
            (db.dumps(failures), scheduler.iso(now + LESSON_COOLDOWN), scheduler.iso(now), skill["id"]),
        )
    st["fail"] = {"kp": kp, "counted": counted, "prev_cooldown": progress["cooldown_until"], "remediated": []}
    _complete(task, st, "failed", 0, now)
    if _unseen_count(skill["id"], kp=kp) < NEEDED_IN_ROW + 1:
        request_top_up(skill["id"], kp)
    finish = _Finish(
        suggestion="retry_later",
        suggestion_text="This lesson is paused for now and comes back in about 2 hours with fresh questions. "
        "Work on something else in the meantime.",
    )
    if counted and failures[str(kp)] >= REMEDIATION_AFTER_FAILURES:
        chosen = scheduler.remediate(skill["course_id"], skill["id"], now)
        st["fail"]["remediated"] = chosen
        _save_state(task["id"], st)
        if chosen:
            names = " and ".join(f"{r['key']} ({r['title']})" for r in (_skill_row(c) for c in chosen))
            due = "it is now due as a review" if len(chosen) == 1 else "they are now due as reviews"
            finish = _Finish(
                suggestion="review_prerequisite",
                suggestion_text=f"You've hit the same step twice. Refresh {names} first — {due}. "
                "This lesson returns later with fresh questions.",
            )
        else:
            finish.suggestion_text = (
                "You've hit the same step twice. Re-read the source passage for this skill before the lesson returns."
            )
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("lesson failed: skill %s at KP %d (failures there: %s)", skill["key"], kp, failures.get(str(kp), 0))
    return finish


def _easier_candidate(skill_id: str, st: dict[str, Any]) -> dict[str, Any] | None:
    """Lowest-tier unseen question from the current or previous KP, never harder than the current question."""
    kp = st["kp"]
    served = set(st["served"])
    answered = _answered_ids(skill_id)
    current = db.query_one("SELECT tier FROM questions WHERE id = ?", (st["current"],)) if st.get("current") else None
    max_tier = current["tier"] if current else None
    pool = [
        (pos, q)
        for pos, q in enumerate(_questions(skill_id))
        if q["kp_index"] in (kp, kp - 1)
        and not q["flagged"]
        and q["id"] not in served
        and (max_tier is None or q["tier"] <= max_tier)
    ]
    if not pool:
        return None
    return min(pool, key=lambda pq: (pq[1]["tier"], pq[1]["kp_index"] != kp, pq[1]["id"] in answered, pq[0]))[1]


def easier_step(task_id: str, now: datetime) -> Task:
    with lock:
        task = _task_row(task_id)
        st = db.loads(task["state_json"], {})
        if task["type"] != "lesson":
            raise Conflict("An easier step is only available during a lesson.")
        if task["status"] != "active" or task["phase"] != "practice" or not st.get("current"):
            raise Conflict("There is no current lesson question to swap.")
        candidate = _easier_candidate(task["skill_id"], st)
        if candidate is None:
            raise Conflict("No easier question is left in this lesson — try a hint or show the solution.")
        slot = st["slots"].setdefault(str(st["kp"]), [])
        if st["current"] in slot:
            slot.remove(st["current"])
        slot.append(candidate["id"])
        st["served"].append(candidate["id"])
        st["current"] = candidate["id"]
        st["revealed"] = False
        st["wrong_run"] = 0
        _save_state(task_id, st)
        log.info("task %s: easier step -> question %s (tier %s)", task_id, candidate["key"], candidate["tier"])
        return build_task(_task_row(task_id))


# ---------------------------------------------------------------------------
# Review flow
# ---------------------------------------------------------------------------


def _replacement_review_question(skill_id: str, st: dict[str, Any], prefer_kp: int | None) -> str | None:
    answered = _answered_ids(skill_id)
    served = set(st["served"])
    pool = [q for q in _questions(skill_id) if not q["flagged"] and q["id"] not in served]
    if not pool:
        return None
    pool.sort(key=lambda q: (q["kp_index"] != prefer_kp, q["id"] in answered, answered.get(q["id"], "")))
    return pool[0]["id"]


def _apply_review_result(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> tuple[float, Rating]:
    credits = [e["credit"] for e in _live(st)]
    score = scheduler.review_score(credits)
    rating = scheduler.rating_for_score(score)
    progress = _progress_row(skill)
    before = {k: progress[k] for k in ("card_json", "status", "reviews", "lapses")}
    card, log_id = scheduler.apply_rating(skill["course_id"], skill["id"], rating, now)
    db.execute(
        "UPDATE progress SET reviews = reviews + 1, lapses = lapses + ?, updated_at = ? WHERE skill_id = ?",
        (int(rating == Rating.Again), scheduler.iso(now), skill["id"]),
    )
    st["result"] = {
        "before": before, "after_card": scheduler.dump_card(card), "review_log_id": log_id,
        "score": score, "rating": int(rating), "reviewed_at": scheduler.iso(now),
    }  # fmt: skip
    return score, rating


def _finish_review(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> _Finish:
    score, rating = _apply_review_result(task, st, skill, now)
    amount = xp.review_xp(score)
    passed = score >= xp.REVIEW_PASS_SCORE - 1e-9
    _complete(task, st, "passed" if passed else "failed", amount, now)
    xp.award(skill["course_id"], task["id"], amount, now)
    if rating == Rating.Again:
        scheduler.apply_failure_penalty(skill["course_id"], skill["id"], now)
    else:
        scheduler.apply_success_credit(skill["course_id"], skill["id"], now)
    scheduler.clear_remedial(skill["id"])
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("review %s: skill %s score %.2f -> %s, %d XP", task["id"], skill["key"], score, rating.name, amount)
    if passed:
        return _Finish(xp=amount)
    return _Finish(
        xp=amount,
        suggestion="reread_example",
        suggestion_text=f"Recall was shaky ({round(score * 100)}%). This skill comes back for review soon — "
        "reopen its lesson's worked examples before then.",
    )


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


_SELF_GRADE_FEEDBACK = {
    "correct": "Self-assessed as correct.",
    "partial": "Self-assessed as partly correct — it counts as a miss for passing, and it's logged.",
    "incorrect": "Self-assessed as incorrect. Study the model solution, then try the next one.",
}


def _grade(q: dict[str, Any], sub: AnswerSubmit, revealed_before: bool) -> _Graded:
    revealed = sub.revealed_solution or revealed_before
    hints = max(0, sub.hints_used)
    if q["kind"] == "free_response":
        if sub.self_grade is None:
            raise Invalid("Self-assess your answer against the checklist: correct, partial or incorrect.")
        correct = sub.self_grade == "correct"
        independent = correct and hints == 0
        res = "correct" if independent else ("assisted" if correct else "wrong")
        return _Graded(correct, "self_graded", independent, _SELF_GRADE_FEEDBACK[sub.self_grade], res, sub.response or "", False)
    is_choice = q["kind"] in ("multiple_choice", "true_false")
    empty = sub.choice is None if is_choice else not (sub.response or "").strip()
    if empty and revealed:
        correct, feedback = False, "Solution viewed — no answer submitted."
    else:
        result = grading.check_answer(
            q["kind"],
            answer=q["answer"],
            correct_choice=q["correct_choice"],
            tolerance=q["tolerance"],
            response=sub.response,
            choice=sub.choice,
        )
        if result.invalid:
            raise Invalid(result.feedback)
        correct, feedback = result.correct, result.feedback
    independent = correct and hints == 0 and not revealed
    res = "correct" if independent else ("assisted" if correct else "wrong")
    response = str(sub.choice) if is_choice and sub.choice is not None else (sub.response or "")
    return _Graded(correct, "correct" if correct else "incorrect", independent, feedback, res, response, revealed)


def _assisted_note(task_type: str, revealed: bool) -> str:
    if task_type == "lesson":
        return " With help, so it doesn't count toward the two in a row — but it isn't a miss."
    return " The solution was shown first, so no credit." if revealed else " With hints, so half credit."


def _record_attempt(task: dict[str, Any], q: dict[str, Any], g: _Graded, sub: AnswerSubmit, now: datetime) -> None:
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, independent, response, hints_used, revealed, seconds, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            db.new_id(), task["id"], q["id"], q["skill_id"], int(g.correct), g.status, int(g.independent),
            g.response[:4000], max(0, sub.hints_used), int(g.revealed), max(0.0, sub.seconds), scheduler.iso(now),
        ),
    )  # fmt: skip


def submit_answer(task_id: str, sub: AnswerSubmit, now: datetime) -> AnswerResult:
    with lock:
        task = _task_row(task_id)
        if task["status"] != "active":
            raise Conflict("This task is already finished.")
        if task["phase"] != "practice":
            raise Conflict("Start the practice first.")
        st = db.loads(task["state_json"], {})
        if sub.question_id != st.get("current"):
            raise Conflict("That question is not the current one — reload the task.")
        q = _question_row(sub.question_id)
        skill = _skill_row(task["skill_id"])
        graded = _grade(q, sub, st.get("revealed", False))
        if graded.res == "assisted":
            graded.feedback += _assisted_note(task["type"], graded.revealed)
        _record_attempt(task, q, graded, sub, now)
        if q["kind"] == "free_response" and sub.self_grade:
            st["self_grades"][q["id"]] = sub.self_grade
        st["current"] = None
        finish = (_after_lesson_answer if task["type"] == "lesson" else _after_review_answer)(task, st, skill, q, graded, now)
        if task["status"] == "active":
            _save_state(task_id, st)
        return AnswerResult(
            correct=graded.correct,
            status=graded.status,
            independent=graded.independent,
            feedback=graded.feedback,
            correct_answer=_answer_display(q),
            solution=q["solution"],
            task=build_task(_task_row(task_id)),
            xp_awarded=finish.xp,
            suggestion=finish.suggestion,
            suggestion_text=finish.suggestion_text,
        )


def _after_lesson_answer(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], q: dict[str, Any], g: _Graded, now: datetime
) -> _Finish:
    st["log"].append({"q": q["id"], "kp": st["kp"], "res": g.res, "void": False})
    st["wrong_run"] = st.get("wrong_run", 0) + 1 if g.res == "wrong" else 0
    finish = _lesson_progress(task, st, skill, now)
    if finish is not None:
        return finish
    if st["wrong_run"] >= EASIER_AFTER_WRONG:
        if _easier_candidate(skill["id"], st) is not None:
            return _Finish(
                suggestion="easier_step",
                suggestion_text="Two misses in a row. Take an easier step: re-read the worked example, then try a simpler question.",
            )
        return _Finish(
            suggestion="reread_example",
            suggestion_text="Two misses in a row — re-read the worked example above before the next question.",
        )
    return _Finish()


def _after_review_answer(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], q: dict[str, Any], g: _Graded, now: datetime
) -> _Finish:
    credit = 1.0 if g.res == "correct" else (0.5 if g.correct and not g.revealed else 0.0)
    st["log"].append({"q": q["id"], "kp": q["kp_index"], "res": g.res, "credit": credit, "void": False})
    st["pos"] += 1
    if st["pos"] >= len(st["set"]):
        return _finish_review(task, st, skill, now)
    st["current"] = st["set"][st["pos"]]
    st["revealed"] = False
    return _Finish()


# ---------------------------------------------------------------------------
# Reveal + flag
# ---------------------------------------------------------------------------


def reveal(question_id: str, now: datetime) -> RevealResponse:
    with lock:
        q = _question_row(question_id)
        for task in db.query(
            "SELECT * FROM tasks WHERE skill_id = ? AND status = 'active' AND state_json LIKE ?",
            (q["skill_id"], f"%{question_id}%"),
        ):
            st = db.loads(task["state_json"], {})
            if st.get("current") == question_id and q["kind"] != "free_response":
                st["revealed"] = True
                _save_state(task["id"], st)
                log.info("task %s: solution revealed for question %s", task["id"], q["key"])
        return RevealResponse(solution=q["solution"], correct_answer=_answer_display(q))


def _task_serving(question_id: str, skill_id: str) -> dict[str, Any] | None:
    rows = db.query(
        "SELECT * FROM tasks WHERE skill_id = ? AND state_json LIKE ? ORDER BY status = 'active' DESC, created_at DESC, rowid DESC",
        (skill_id, f"%{question_id}%"),
    )
    return next((r for r in rows if question_id in db.loads(r["state_json"], {}).get("served", [])), None)


def flag(question_id: str, reason: str, now: datetime) -> Task:
    """Exclude a question the learner believes is wrong; its attempts in the task never count against them."""
    with lock:
        q = _question_row(question_id)
        task = _task_serving(question_id, q["skill_id"])
        if task is None:
            raise Conflict("This question hasn't been served in a task yet.")
        db.execute("UPDATE questions SET flagged = 1, flag_reason = ? WHERE id = ?", ((reason or "").strip()[:500], question_id))
        db.execute("UPDATE attempts SET status = 'voided' WHERE task_id = ? AND question_id = ?", (task["id"], question_id))
        log.info("question %s flagged by learner (task %s): %s", q["key"], task["id"], reason)
        st = db.loads(task["state_json"], {})
        for entry in st["log"]:
            if entry["q"] == question_id:
                entry["void"] = True
        skill = _skill_row(task["skill_id"])
        if task["type"] == "lesson":
            _void_in_lesson(task, st, skill, question_id, now)
        else:
            _void_in_review(task, st, skill, question_id, now)
        request_top_up(skill["id"], q["kp_index"])
        return build_task(_task_row(task["id"]))


def _reopen_failed_lesson(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> bool:
    """A lesson that failed partly because of a now-flagged question resumes where it stopped."""
    fail = st.get("fail")
    if task["outcome"] != "failed" or not fail or active_task_row(task["course_id"]) is not None:
        return False
    if fail["counted"]:
        progress = _progress_row(skill)
        failures: dict[str, int] = db.loads(progress["kp_failures_json"], {})
        key = str(fail["kp"])
        failures[key] = max(0, failures.get(key, 0) - 1)
        db.execute(
            "UPDATE progress SET kp_failures_json = ?, cooldown_until = ?, updated_at = ? WHERE skill_id = ?",
            (db.dumps({k: v for k, v in failures.items() if v}), fail["prev_cooldown"], scheduler.iso(now), skill["id"]),
        )
    st["kp"] = fail["kp"]
    st["fail"] = None
    db.execute(
        "UPDATE tasks SET status = 'active', phase = 'practice', outcome = NULL, completed_at = NULL WHERE id = ?", (task["id"],)
    )
    task.update(status="active", phase="practice", outcome=None)
    log.info("task %s reopened after a flag", task["id"])
    return True


def _void_in_lesson(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], qid: str, now: datetime) -> None:
    answered_here = any(e["q"] == qid for e in st["log"])
    if task["status"] == "completed":
        if task["outcome"] == "passed":
            _regrade_passed_lesson(task, st, skill, now)
            _save_state(task["id"], st)
            return
        if not (answered_here and _reopen_failed_lesson(task, st, skill, now)):
            _save_state(task["id"], st)
            return
    for slot in st["slots"].values():
        if qid in slot:
            slot.remove(qid)
    if st.get("current") == qid:
        st["current"] = None
    st["wrong_run"] = _trailing(_kp_entries(st, st["kp"]), "wrong")
    # A pool emptied by flags is not the learner's failure: it pauses the lesson without counting toward remediation.
    _lesson_progress(task, st, skill, now, count_failure=False)
    if task["status"] == "active":
        _save_state(task["id"], st)


def _regrade_passed_lesson(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> None:
    """Voiding a wrong answer can only help: restore the no-mistakes bonus if it was the only miss."""
    wrong = sum(1 for e in _live(st) if e["res"] == "wrong")
    amount = xp.lesson_xp(skill["est_minutes"], wrong)
    if amount > task["xp_awarded"]:
        xp.award(skill["course_id"], task["id"], amount - task["xp_awarded"], now)
        db.execute("UPDATE tasks SET xp_awarded = ? WHERE id = ?", (amount, task["id"]))
        st["result"]["wrong"] = wrong


def _void_in_review(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], qid: str, now: datetime) -> None:
    if task["status"] == "completed":
        _rescore_review(task, st, skill)
        return
    if qid in st["set"]:
        idx = st["set"].index(qid)
        kp = _question_row(qid)["kp_index"]
        replacement = _replacement_review_question(skill["id"], st, kp)
        if idx < st["pos"]:
            st["set"].pop(idx)
            st["pos"] -= 1
            if replacement:
                st["set"].append(replacement)
                st["served"].append(replacement)
        elif replacement:
            st["set"][idx] = replacement
            st["served"].append(replacement)
        else:
            st["set"].pop(idx)
    if st["pos"] >= len(st["set"]):
        if _live(st):
            _finish_review(task, st, skill, now)
        else:
            _complete(task, st, "abandoned", 0, now)
        return
    st["current"] = st["set"][st["pos"]]
    st["revealed"] = False
    _save_state(task["id"], st)


def _rescore_review(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any]) -> None:
    """Re-score a finished review without the voided answer, if nothing has touched the skill's card since."""
    result = st.get("result") or {}
    progress = _progress_row(skill)
    if task["outcome"] not in ("passed", "failed") or not result or progress["card_json"] != result.get("after_card"):
        _save_state(task["id"], st)
        return
    before = result["before"]
    db.execute(
        "UPDATE progress SET card_json = ?, status = ?, reviews = ?, lapses = ? WHERE skill_id = ?",
        (before["card_json"], before["status"], before["reviews"], before["lapses"], skill["id"]),
    )
    db.execute("DELETE FROM review_log WHERE id = ?", (result["review_log_id"],))
    xp.revoke(task["id"])
    reviewed_at = db.parse_iso(result["reviewed_at"])
    assert reviewed_at is not None
    if not _live(st):
        _complete(task, st, "abandoned", 0, reviewed_at)
        log.info("review %s voided entirely after a flag", task["id"])
        return
    score, _ = _apply_review_result(task, st, skill, reviewed_at)
    amount = xp.review_xp(score)
    _complete(task, st, "passed" if score >= xp.REVIEW_PASS_SCORE - 1e-9 else "failed", amount, reviewed_at)
    xp.award(skill["course_id"], task["id"], amount, reviewed_at)
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("review %s re-scored after a flag: %.2f", task["id"], score)
