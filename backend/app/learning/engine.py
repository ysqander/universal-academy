"""Study task engine: lesson and review tasks (SPEC §6.3, §6.4) — score-based (iteration 3).

Lessons. Knowledge point k has a target t_k (lesson_json knowledge_points[k].target_questions, 1-3; legacy lessons 2).
t_k questions of the KP are served in pool order; every wrong or assisted answer in the KP adds one extra question
(at most +2 per KP, while unserved questions remain). After the last KP comes the capstone (role 'capstone', a
multistep problem) when the skill has one. Points: 1 independent correct, 0.5 correct with a hint (no reveal), 0 wrong
or answered after revealing the solution; a multistep question scores the mean of its steps; the capstone weighs 2.
score = Σ w·p / Σ w over the answered (non-voided) questions. Everything planned answered with score >= 0.8 passes
(FSRS Good at >= 0.9, else Hard); as soon as 0.8 is out of reach even if every remaining planned question is answered
independently, the lesson halts (2h cooldown; the second failure of the same lesson remediates).

A task's mutable state lives in tasks.state_json:

lesson  {"kp_total", "kp", "playable": [kp...], "targets": {"<kp>": t}, "stage": "kp" | "capstone" | "done",
         "current", "served": [qid...],
         "revealed", "hints", "compared", "compared_raw",           # per current question: see _set_current
         "step", "step_results", "step_hint", "step_revealed", "step_seconds",  # per current multistep question
         "slots": {"<kp>": [qid...]},          # questions served for that KP (they count toward its plan)
         "easier": [qid...],                   # questions swapped in by the easier step (scored normally)
         "log": [{"q", "kp", "res", "pts", "w", "ok", "void", "scaffold", "cap"}],
         "wrong_run", "self_grades": {qid: grade}, "proof_waived": [kp...], "fail": {...} | None, "result": {...}}
review  {"set": [qid...], "pos", "current", ...per-question records as above..., "served",
         "log": [{"q", "kp", "res", "credit", "pts", "w", "ok", "void", "cap"}], "self_grades",
         "result": {..., "fire": [implicit events]}}

res: correct (independent) | assisted (correct with help) | partial (multistep, some steps missed) | wrong.
States written by earlier versions (no "pts"/"w"/"targets"/"stage", or no keys at all) are completed on load:
points come from the task's attempts rows (independent 1, correct without reveal 0.5, else 0; self-graded
free response from its grade), targets from the lesson (2 for legacy lessons).

Per current question the server records whether the solution was revealed before an answer was committed
("revealed"), how many hints were shown ("hints"), and — for free response — the answer the learner committed
before comparing it with the model solution ("compared", whitespace-normalized; "compared_raw" as typed). For a
multistep question it records the current step, the results of the answered steps and whether the current step's
hint / answer was shown. A reload cannot erase any of these, and build_task hands them back to the client
(QuestionPublic.hints_used / revealed / committed_response / step_index / step_results).

Multistep questions are answered one step at a time (AnswerSubmit.step = the current step, else 409). Each step is
graded with the checker of its kind; its point is 1 / 0.5 (its hint used) / 0 (wrong, or its answer revealed). A
non-final step returns status 'step' (question_done false); the final step records one attempt for the question
(response = JSON of the step results; correct = every step independent and correct) and the task moves on.

Written proofs (legacy lessons only — new lessons have no free_response questions): a KP whose pool has a
free-response question also needs one independent, self-graded "correct" written answer. It is served no later than
the KP's second question; if none can be obtained (no unserved written question left) the requirement is waived
and the lesson's first FSRS rating is Hard.

Every mutating function takes an explicit `now` and serializes through `lock` (single-process app; SQLite
writes are short and each step is a handful of statements). Grading an answer (SymPy, bounded by its own time
budget) happens OUTSIDE the lock; the state is re-validated afterwards. Reads (get_task, build_task) need neither.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections.abc import Collection
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from fsrs import Rating

from .. import db, views
from ..schemas import (
    AnswerResult,
    AnswerSubmit,
    HintResponse,
    MultistepStepPublic,
    QuestionPublic,
    RevealResponse,
    StepResult,
    Task,
    TaskProgress,
)
from . import grading, scheduler, xp
from . import state as skill_state

log = logging.getLogger("ua.learning.engine")

PASS_MARK = 0.8  # lessons and reviews (SPEC §6.3, §6.4)
GOOD_SCORE = 0.9  # a lesson passed at >= this starts with FSRS Good, otherwise Hard
HINT_POINTS = 0.5
CAPSTONE_WEIGHT = 2
LEGACY_TARGET = 2  # questions per KP when the lesson does not say (legacy lessons)
MIN_TARGET, MAX_TARGET = 1, 3
MAX_EXTRA = 2  # extra questions per KP after misses
NEEDED_IN_ROW = 2  # legacy display only (TaskProgress.streak / needed_in_row) — no longer the passing rule
REVIEW_SIZE = 3
LESSON_COOLDOWN = timedelta(hours=2)
REMEDIATION_AFTER_FAILURES = 2  # failures of the same lesson
REREAD_AFTER_WRONG = 2
TOPUP_DEDUPE_SECONDS = 600.0
COMPARE_MIN_CHARS = 15  # a committed free-response answer this long makes a reveal a comparison, not assistance
CAPSTONE_FAIL_KEY = "cap"  # progress.kp_failures_json key of a lesson that halted in its capstone
STEP_KINDS = ("multiple_choice", "true_false", "true_false_set", "numeric", "expression")
EPS = 1e-9

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
    note: str = ""  # appended to the answer feedback


@dataclass
class _Graded:
    correct: bool
    status: str
    independent: bool
    feedback: str
    res: str
    response: str
    revealed: bool
    hints: int
    pts: float
    seconds: float = 0.0


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


def _safe_loads(text: str | None, default: Any) -> Any:
    try:
        value = db.loads(text, default)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _save_state(task_id: str, state: dict[str, Any], **columns: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in ("state_json", *columns))
    db.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (db.dumps(state), *columns.values(), task_id))


def _complete(task: dict[str, Any], st: dict[str, Any], outcome: str, xp_awarded: int, now: datetime) -> None:
    st["current"] = None
    _save_state(
        task["id"], st, status="completed", phase="done", outcome=outcome, xp_awarded=xp_awarded, completed_at=scheduler.iso(now)
    )
    task.update(status="completed", phase="done", outcome=outcome, xp_awarded=xp_awarded)


_PER_QUESTION_DEFAULTS: dict[str, Any] = {
    "revealed": False,
    "hints": 0,
    "compared": None,
    "compared_raw": None,
    "step": 0,
    "step_results": [],
    "step_hint": False,
    "step_revealed": False,
    "step_seconds": 0.0,
}


def _set_current(st: dict[str, Any], qid: str | None) -> None:
    """Serve a question: its reveal / hint / comparison / step record starts fresh."""
    st["current"] = qid
    for key, value in _PER_QUESTION_DEFAULTS.items():
        st[key] = list(value) if isinstance(value, list) else value


# ---------------------------------------------------------------------------
# Questions: roles, multistep steps, displays
# ---------------------------------------------------------------------------


def _role(q: dict[str, Any]) -> str:
    return "capstone" if (q.get("role") or "practice") == "capstone" else "practice"


def _is_capstone(q: dict[str, Any]) -> bool:
    return _role(q) == "capstone"


def _is_capstone_id(question_id: str) -> bool:
    row = db.query_one("SELECT role FROM questions WHERE id = ?", (question_id,))
    return bool(row) and _is_capstone(row)


def _steps(q: dict[str, Any]) -> list[dict[str, Any]]:
    """A multistep question's steps; [] when it has none or any step is unusable (the question is then not served)."""
    if q.get("kind") != "multistep":
        return []
    raw = _safe_loads(q.get("steps_json"), [])
    steps: list[dict[str, Any]] = []
    for s in raw:
        if not isinstance(s, dict) or s.get("kind") not in STEP_KINDS or not str(s.get("prompt") or "").strip():
            return []
        steps.append(s)
    return steps


def _servable(q: dict[str, Any]) -> bool:
    return not q["flagged"] and (q["kind"] != "multistep" or bool(_steps(q)))


def _is_choice_kind(kind: str) -> bool:
    """Answered by picking one option (`choice`). A true/false set is answered with `response` ('TFT')."""
    return kind in ("multiple_choice", "true_false")


def _is_choice(q: dict[str, Any]) -> bool:
    return _is_choice_kind(q["kind"])


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _answer_display(q: dict[str, Any]) -> str:
    if q["answer_display"]:
        return q["answer_display"]
    if q["kind"] == "true_false_set":
        return grading.true_false_set_display(q["answer"])
    if q["kind"] == "multistep":
        steps = _steps(q)
        return _step_display(steps[-1]) if steps else q["answer"]
    choices = _safe_loads(q["choices_json"], [])
    if q["correct_choice"] is not None and 0 <= q["correct_choice"] < len(choices):
        return choices[q["correct_choice"]]
    return q["answer"]


def _step_display(step: dict[str, Any]) -> str:
    if step.get("answer_display"):
        return str(step["answer_display"])
    if step["kind"] == "true_false_set":
        return grading.true_false_set_display(str(step.get("answer") or ""))
    choices = step.get("choices") or []
    cc = _int_or_none(step.get("correct_choice"))
    if cc is not None and 0 <= cc < len(choices):
        return str(choices[cc])
    return str(step.get("answer") or "")


# ---------------------------------------------------------------------------
# Question pool of a skill (one load per operation)
# ---------------------------------------------------------------------------


@dataclass
class _Pool:
    rows: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]]
    answered: dict[str, str]  # qid -> last answered (any task)

    def practice(self, kp: int | None = None) -> list[dict[str, Any]]:
        return [q for q in self.rows if not _is_capstone(q) and _servable(q) and (kp is None or q["kp_index"] == kp)]

    def capstones(self) -> list[dict[str, Any]]:
        return [q for q in self.rows if _is_capstone(q) and _servable(q)]

    def is_capstone(self, qid: str | None) -> bool:
        q = self.by_id.get(qid or "")
        return q is not None and _is_capstone(q)


def _pool(skill_id: str) -> _Pool:
    rows = _questions(skill_id)
    return _Pool(rows, {q["id"]: q for q in rows}, _answered_ids(skill_id))


def _pick(pool: _Pool, candidates: list[dict[str, Any]], served: Collection[str], *, prefer_free_response: bool = False):
    """Never-seen questions first (on a retry), pool order otherwise."""
    cands = [q for q in candidates if q["id"] not in served]
    cands.sort(key=lambda q: q["id"] in pool.answered)
    if prefer_free_response:
        written = [q for q in cands if q["kind"] == "free_response"]
        if written:
            return written[0]
    return cands[0] if cands else None


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
    pool = _pool(skill_id)
    candidates = pool.practice(kp) if kp is not None else [q for q in pool.rows if _servable(q)]
    return sum(1 for q in candidates if q["id"] not in pool.answered and q["id"] not in exclude)


# ---------------------------------------------------------------------------
# Task state: loading (with migration of older states) and points
# ---------------------------------------------------------------------------


def _targets_from_lesson(lesson: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, kp in enumerate(lesson.get("knowledge_points") or []):
        raw = kp.get("target_questions") if isinstance(kp, dict) else None
        try:
            target = int(raw) if raw is not None and not isinstance(raw, bool) else LEGACY_TARGET
        except (TypeError, ValueError):
            target = LEGACY_TARGET
        out[str(i)] = max(MIN_TARGET, min(MAX_TARGET, target))
    return out


def _legacy_points(attempt: dict[str, Any], grade: str | None) -> float:
    """Points of an answer logged before points existed, from its attempts row."""
    if attempt["revealed"]:
        return 0.0
    if attempt["status"] == "self_graded":
        if grade == "correct":
            return 1.0 if attempt["independent"] else HINT_POINTS
        return HINT_POINTS if grade == "partial" else 0.0
    if attempt["independent"]:
        return 1.0
    return HINT_POINTS if attempt["correct"] else 0.0


def _complete_log(st: dict[str, Any], task: dict[str, Any]) -> None:
    entries = [e for e in st["log"] if isinstance(e, dict) and e.get("q")]
    st["log"] = entries
    if any("pts" not in e for e in entries):
        attempts = {
            a["question_id"]: a
            for a in db.query(
                "SELECT question_id, correct, independent, revealed, status FROM attempts WHERE task_id = ? AND status != 'voided'"
                " ORDER BY rowid",
                (task["id"],),
            )
        }
        grades = st.get("self_grades") or {}
        for e in entries:
            if "pts" in e:
                continue
            if "credit" in e:
                e["pts"] = float(e["credit"] or 0.0)
            elif e["q"] in attempts:
                e["pts"] = _legacy_points(attempts[e["q"]], grades.get(e["q"]))
            else:
                e["pts"] = {"correct": 1.0, "assisted": HINT_POINTS}.get(e.get("res"), 0.0)
    for e in entries:
        e.setdefault("res", "correct" if e["pts"] >= 1 - EPS else "wrong")
        e.setdefault("kp", 0)
        e.setdefault("w", 1)
        e.setdefault("void", False)
        e.setdefault("cap", False)
        e.setdefault("ok", e["res"] in ("correct", "assisted"))
        if task["type"] == "review":
            e.setdefault("credit", e["pts"])


def _load_state(task: dict[str, Any]) -> dict[str, Any]:
    """The task's state, completed with defaults so states written by earlier versions keep working."""
    st = _safe_loads(task["state_json"], {})
    st.setdefault("current", None)
    for key, value in _PER_QUESTION_DEFAULTS.items():
        if key not in st:
            st[key] = list(value) if isinstance(value, list) else value
    for key in ("served", "log"):
        if not isinstance(st.get(key), list):
            st[key] = []
    for key in ("self_grades", "result"):
        if not isinstance(st.get(key), dict):
            st[key] = {}
    _complete_log(st, task)
    if task["type"] == "review":
        if not isinstance(st.get("set"), list):
            st["set"] = []
        st.setdefault("pos", 0)
        return st
    if not all(k in st for k in ("kp_total", "playable", "targets")):
        skill = db.query_one("SELECT * FROM skills WHERE id = ?", (task["skill_id"],)) or {}
        lesson = _safe_loads(skill.get("lesson_json"), {})
        st.setdefault("kp_total", len(lesson.get("knowledge_points") or []))
        st.setdefault("targets", _targets_from_lesson(lesson))
        if "playable" not in st:
            pools = {q["kp_index"] for q in _pool(task["skill_id"]).practice()}
            st["playable"] = [k for k in range(int(st["kp_total"])) if k in pools]
    if not isinstance(st.get("slots"), dict):
        st["slots"] = {}
    for key in ("easier", "proof_waived"):
        if not isinstance(st.get(key), list):
            st[key] = []
    st.setdefault("kp", st["playable"][0] if st["playable"] else 0)
    st.setdefault("stage", "kp")
    st.setdefault("wrong_run", 0)
    st.setdefault("fail", None)
    return st


def _live(st: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in st["log"] if not e.get("void")]


def _live_by_q(st: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["q"]: e for e in _live(st)}


def _totals(st: dict[str, Any]) -> tuple[float, float]:
    """Lesson points: (Σ w·p, Σ w) over the answered, non-voided questions."""
    live = _live(st)
    return sum(e["w"] * e["pts"] for e in live), float(sum(e["w"] for e in live))


def _review_totals(st: dict[str, Any]) -> tuple[float, float]:
    """Review points with Math Academy's order weights: answer i (1-based) weighs i (× 2 for a capstone)."""
    live = _live(st)
    weights = [(i + 1) * e["w"] for i, e in enumerate(live)]
    return sum(w * e["credit"] for w, e in zip(weights, live)), float(sum(weights))


def _ratio(points: float, max_points: float) -> float:
    return points / max_points if max_points > 0 else 0.0


def _pct(score: float) -> int:
    return int(math.floor(score * 100 + 0.5))


# ---------------------------------------------------------------------------
# Lesson plan: KP targets, extras after a miss, capstone, the written-proof rule (legacy)
# ---------------------------------------------------------------------------


def _target(st: dict[str, Any], kp: int) -> int:
    try:
        return int(st["targets"].get(str(kp), LEGACY_TARGET))
    except (TypeError, ValueError, AttributeError):
        return LEGACY_TARGET


def _slot(st: dict[str, Any], kp: int) -> list[str]:
    return st["slots"].get(str(kp), [])


def _kp_done(st: dict[str, Any], kp: int) -> list[dict[str, Any]]:
    """Answered questions that count toward KP kp's plan (served for it, not voided)."""
    by_q = _live_by_q(st)
    return [by_q[qid] for qid in _slot(st, kp) if qid in by_q]


def _kp_plan(st: dict[str, Any], pool: _Pool, kp: int) -> int:
    """Questions planned for a KP: its target, +1 per wrong/assisted answer (at most +2), within what can be served."""
    misses = sum(1 for e in _kp_done(st, kp) if e["pts"] < 1 - EPS)
    served = set(st["served"])
    capacity = len(_slot(st, kp)) + sum(1 for q in pool.practice(kp) if q["id"] not in served)
    return min(_target(st, kp) + min(MAX_EXTRA, misses), capacity)


def _kp_written(pool: _Pool, kp: int) -> set[str]:
    return {q["id"] for q in pool.practice(kp) if q["kind"] == "free_response"}


def _proof_state(st: dict[str, Any], pool: _Pool, kp: int) -> str:
    """The written-proof requirement of a KP (legacy lessons):
    'none'    no (unflagged) free-response question in the KP's pool
    'done'    an independent, self-graded correct written answer in this task
    'waived'  every written question has been served (and answered) without one
    'pending' still needed (a written question is current or still unserved)"""
    fr = _kp_written(pool, kp)
    if not fr:
        return "none"
    if any(e["q"] in fr and e["res"] == "correct" for e in _live(st)):
        return "done"
    if st.get("current") not in fr and fr <= set(st["served"]):
        return "waived"
    return "pending"


def _kp_remaining(st: dict[str, Any], pool: _Pool, kp: int) -> int:
    """Questions still to answer in a KP (the current one included)."""
    remaining = max(0, _kp_plan(st, pool, kp) - len(_kp_done(st, kp)))
    if remaining == 0 and _proof_state(st, pool, kp) == "pending":
        remaining = 1  # the written answer the KP still needs
    return remaining


def _capstone_pending(st: dict[str, Any], pool: _Pool) -> bool:
    if st.get("stage") == "done" or any(e.get("cap") for e in _live(st)):
        return False
    if pool.is_capstone(st.get("current")):
        return True
    served = set(st["served"])
    return any(q["id"] not in served for q in pool.capstones())


def _remaining_weight(st: dict[str, Any], pool: _Pool) -> float:
    if st.get("stage") == "done":
        return 0.0
    total = 0.0
    if st.get("stage") != "capstone":
        total += sum(_kp_remaining(st, pool, k) for k in st["playable"] if k >= st["kp"])
    if _capstone_pending(st, pool):
        total += CAPSTONE_WEIGHT
    return total


def _best_possible(st: dict[str, Any], pool: _Pool) -> float:
    """The score if every remaining planned question is answered independently."""
    points, max_points = _totals(st)
    remaining = _remaining_weight(st, pool)
    return _ratio(points + remaining, max_points + remaining)


def _serve_lesson_question(st: dict[str, Any], pool: _Pool) -> bool:
    kp = st["kp"]
    slot = _slot(st, kp)
    prefer = False
    if _proof_state(st, pool, kp) == "pending":
        fr = _kp_written(pool, kp)
        # the KP's first written question comes no later than its 2nd question, and whenever the next answer
        # would otherwise complete the KP without it
        prefer = (len(slot) >= 1 and not any(qid in fr for qid in slot)) or len(_kp_done(st, kp)) + 1 >= _kp_plan(st, pool, kp)
    q = _pick(pool, pool.practice(kp), set(st["served"]), prefer_free_response=prefer)
    if q is None:
        return False
    _set_current(st, q["id"])
    st["served"].append(q["id"])
    st["slots"].setdefault(str(kp), []).append(q["id"])
    return True


def _serve_next(st: dict[str, Any], pool: _Pool) -> bool:
    """Serve the next planned question: the current KP's, a later KP's, then the capstone. False: nothing left."""
    if st.get("stage") == "done":
        return False
    if st.get("stage") != "capstone":
        for kp in [k for k in st["playable"] if k >= st["kp"]]:
            if kp != st["kp"]:
                st["kp"] = kp
                st["wrong_run"] = 0
            if _kp_remaining(st, pool, kp) > 0 and _serve_lesson_question(st, pool):
                return True
    if _capstone_pending(st, pool):
        cap = _pick(pool, pool.capstones(), set(st["served"]))
        if cap is not None:
            st["stage"] = "capstone"
            st["wrong_run"] = 0
            _set_current(st, cap["id"])
            st["served"].append(cap["id"])
            return True
    return False


# ---------------------------------------------------------------------------
# Task views
# ---------------------------------------------------------------------------


def _trailing_correct(entries: list[dict[str, Any]]) -> int:
    count = 0
    for e in reversed(entries):
        if e["res"] != "correct":
            break
        count += 1
    return count


def _lesson_progress_view(task: dict[str, Any], st: dict[str, Any], skill_id: str) -> TaskProgress:
    pool = _pool(skill_id)
    live = _live(st)
    kp_total = int(st.get("kp_total") or 0)
    kp = max(0, min(int(st.get("kp") or 0), kp_total - 1)) if kp_total else 0
    active = task["status"] == "active"
    ongoing = active and task["phase"] == "practice"
    in_capstone = active and st.get("stage") == "capstone"
    passed = task["outcome"] == "passed"
    points, max_points = _totals(st)
    done = _kp_done(st, kp)
    if in_capstone:
        kp_target = used = available = 1
    else:
        kp_target = len(done) + (_kp_remaining(st, pool, kp) if active else 0)
        used = len(_slot(st, kp))
        served = set(st["served"])
        capacity = used + sum(1 for q in pool.practice(kp) if q["id"] not in served)
        available = max(kp_target, min(_target(st, kp) + MAX_EXTRA, capacity))
    planned = 0
    for k in st["playable"]:
        n = len(_kp_done(st, k))
        if active and st.get("stage") != "capstone" and k >= st["kp"]:
            n += _kp_remaining(st, pool, k)
        planned += n
    has_capstone = any(e.get("cap") for e in live) or (active and _capstone_pending(st, pool))
    if has_capstone:
        planned += 1
    return TaskProgress(
        kp_index=kp, kp_total=kp_total,
        streak=NEEDED_IN_ROW if passed else min(NEEDED_IN_ROW, _trailing_correct(done)), needed_in_row=NEEDED_IN_ROW,
        kp_questions_used=used, kp_questions_available=available,
        answered=len(live), correct=sum(1 for e in live if e["ok"]), total_questions=0,
        kp_wrong=sum(1 for e in done if e["res"] == "wrong") if ongoing and not in_capstone else 0,
        easier_available=ongoing and _easier_blocker(st, pool) is None,
        kp_target=kp_target, points=points, max_points=max_points, score=_ratio(points, max_points),
        planned=planned, pass_mark=PASS_MARK, in_capstone=in_capstone, has_capstone=has_capstone,
    )  # fmt: skip


def _review_progress_view(task: dict[str, Any], st: dict[str, Any]) -> TaskProgress:
    live = _live(st)
    points, max_points = _review_totals(st)
    current = st.get("current")
    in_capstone = False
    if task["status"] == "active" and current:
        row = db.query_one("SELECT role FROM questions WHERE id = ?", (current,))
        in_capstone = bool(row) and _is_capstone(row)
    return TaskProgress(
        kp_index=0, kp_total=1, streak=_trailing_correct(live), needed_in_row=NEEDED_IN_ROW,
        kp_questions_used=len(live), kp_questions_available=len(st["set"]),
        answered=len(live), correct=sum(1 for e in live if e["ok"]), total_questions=len(st["set"]),
        points=points, max_points=max_points, score=_ratio(points, max_points),
        planned=len(st["set"]), pass_mark=PASS_MARK, in_capstone=in_capstone,
        has_capstone=any(_is_capstone_id(q) for q in st["set"]),
    )  # fmt: skip


def _step_result_public(r: dict[str, Any]) -> StepResult:
    return StepResult(
        index=int(r.get("index", 0)),
        correct=bool(r.get("correct")),
        score=float(r.get("score", 0.0)),
        feedback=str(r.get("feedback") or ""),
        answer_display=str(r.get("answer_display") or ""),
        explanation=str(r.get("explanation") or ""),
    )


def _question_view(qrow: dict[str, Any], st: dict[str, Any]) -> QuestionPublic:
    """The current question as the learner sees it, with the server's record of the attempt (E3)."""
    question = views.question_public(qrow)
    question.role = _role(qrow)  # type: ignore[assignment]
    if qrow["kind"] == "multistep":
        steps = _steps(qrow)
        question.steps = [
            MultistepStepPublic(
                index=i,
                prompt=str(s["prompt"]),
                kind=s["kind"],
                choices=[str(c) for c in s.get("choices") or []],
                hint=str(s.get("hint") or ""),
            )
            for i, s in enumerate(steps)
        ]
        question.step_index = max(0, min(int(st.get("step") or 0), len(steps) - 1))
        question.step_results = [_step_result_public(r) for r in st.get("step_results") or []]
        question.hints_used = 1 if st.get("step_hint") else 0  # the current step's hint
        question.revealed = bool(st.get("step_revealed"))  # the current step's answer
        return question
    question.hints_used = min(len(question.hints), int(st.get("hints") or 0))
    question.revealed = bool(st.get("revealed"))
    if question.kind == "free_response" and st.get("compared") is not None:
        question.committed_response = st.get("compared_raw") or st["compared"]
    return question


def build_task(task: dict[str, Any]) -> Task:
    st = _load_state(task)
    skill = _skill_row(task["skill_id"])
    question = None
    if task["status"] == "active" and st.get("current"):
        qrow = db.query_one("SELECT * FROM questions WHERE id = ?", (st["current"],))
        question = _question_view(qrow, st) if qrow else None
    if task["status"] == "completed":
        xp_value = task["xp_awarded"]
    elif task["type"] == "lesson":
        xp_value = xp.lesson_xp_value(skill["est_minutes"])
    else:
        xp_value = xp.REVIEW_XP_PASS
    progress = _lesson_progress_view(task, st, skill["id"]) if task["type"] == "lesson" else _review_progress_view(task, st)
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
        progress=progress,
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


def _lesson_initial_state(skill: dict[str, Any]) -> dict[str, Any]:
    """Validate that a lesson can start (no side effects) and build its initial state."""
    if skill["lesson_status"] == "failed":
        raise Conflict("This lesson could not be generated — resume the course build to retry it.")
    if skill["lesson_status"] != "ready" or not skill["lesson_json"]:
        raise Conflict("The lesson for this skill is still being generated.")
    lesson = _safe_loads(skill["lesson_json"], {})
    kp_total = len(lesson.get("knowledge_points") or [])
    pools = {q["kp_index"] for q in _pool(skill["id"]).practice()}
    playable = [k for k in range(kp_total) if k in pools]
    if not playable:
        raise Conflict("This lesson has no practice questions yet.")
    st = {
        "kp_total": kp_total, "kp": playable[0], "playable": playable, "targets": _targets_from_lesson(lesson),
        "stage": "kp", "served": [], "slots": {}, "easier": [], "log": [], "wrong_run": 0, "self_grades": {},
        "proof_waived": [], "fail": None, "result": {},
    }  # fmt: skip
    _set_current(st, None)
    return st


def _review_initial_state(skill: dict[str, Any]) -> dict[str, Any]:
    """Validate that a review can start (no side effects) and build its initial state."""
    if _progress_row(skill)["status"] not in scheduler.LEARNED:
        raise Conflict("Reviews start after the lesson has been passed.")
    qids = _review_set(skill["id"])
    if not qids:
        raise Conflict("There are no review questions for this skill right now.")
    st = {"set": qids, "pos": 0, "served": list(qids), "log": [], "self_grades": {}, "result": {}}
    _set_current(st, qids[0])
    return st


def prepare_start(skill_id: str, type_: str) -> dict[str, Any]:
    """Raise Conflict if a task of this type cannot start now; else return its initial state (no side effects)."""
    skill = _skill_row(skill_id)
    return _lesson_initial_state(skill) if type_ == "lesson" else _review_initial_state(skill)


def start_lesson(skill_id: str, reason: str, now: datetime, st: dict[str, Any] | None = None) -> str:
    with lock:
        return _start_lesson(skill_id, reason, now, st)


def _start_lesson(skill_id: str, reason: str, now: datetime, st: dict[str, Any] | None = None) -> str:
    skill = _skill_row(skill_id)
    st = st if st is not None else _lesson_initial_state(skill)
    task_id = _insert_task(skill["course_id"], skill_id, "lesson", "learn", reason, st, now)
    progress = _progress_row(skill)
    if progress["status"] not in scheduler.LEARNED:
        # A lesson started on a locked skill ("Learn anyway") stays locked until its prerequisites are learned (the
        # menu never offers it); lesson_attempts > 0 lets the learner resume or restart it without forcing again.
        locked = bool(skill_state.unmet_prerequisites(skill["course_id"], skill_id))
        db.execute(
            "UPDATE progress SET status = ?, lesson_attempts = lesson_attempts + 1, updated_at = ? WHERE skill_id = ?",
            ("locked" if locked else "in_progress", scheduler.iso(now), skill_id),
        )
    log.info("started lesson task %s for skill %s (%s)", task_id, skill["key"], reason)
    return task_id


def _is_proof_skill(skill: dict[str, Any]) -> bool:
    try:
        from ..pipeline.lessons import is_proof_skill
    except ImportError:  # pragma: no cover - the pipeline is part of the app
        return skill.get("kind") == "proof"
    return is_proof_skill(skill)


def _review_written_question(skill_id: str, pool: list[dict[str, Any]], answered: dict[str, str]) -> dict[str, Any] | None:
    """The written question a proof skill's review includes: one never answered independently and correctly first
    (a proof waived in the lesson comes back here), then never-answered ones, then the least recently answered."""
    written = [q for q in pool if q["kind"] == "free_response"]
    if not written:
        return None
    solid = {
        r["question_id"]
        for r in db.query(
            "SELECT DISTINCT question_id FROM attempts WHERE skill_id = ? AND independent = 1 AND correct = 1 AND status != 'voided'",
            (skill_id,),
        )
    }
    written.sort(key=lambda q: (q["id"] in solid, q["id"] not in answered, answered.get(q["id"], "")))
    return written[0]


def _review_capstone(skill_id: str, pool: _Pool) -> dict[str, Any] | None:
    """The capstone a review includes, if any: a never-answered one always; otherwise the least recently answered
    one on every second review (the 2nd, 4th, … — the first review after the lesson stays short questions)."""
    caps = pool.capstones()
    if not caps:
        return None
    caps.sort(key=lambda q: (q["id"] in pool.answered, pool.answered.get(q["id"], "")))
    if caps[0]["id"] not in pool.answered:
        return caps[0]
    row = db.query_one("SELECT reviews FROM progress WHERE skill_id = ?", (skill_id,))
    return caps[0] if row and int(row["reviews"] or 0) % 2 == 1 else None


def _review_set(skill_id: str) -> list[str]:
    """One question per knowledge point where possible: never answered first, then least recently answered; a
    capstone (when included, see _review_capstone) replaces one of them and comes last. A proof skill's review
    includes one written (free-response) question when it has one (E1)."""
    pool = _pool(skill_id)
    answered = pool.answered
    practice = pool.practice()
    practice.sort(key=lambda q: (q["id"] in answered, answered.get(q["id"], "")))  # stable: pool order breaks ties
    capstone = _review_capstone(skill_id, pool)
    size = REVIEW_SIZE - (1 if capstone else 0)
    chosen: list[dict[str, Any]] = []
    written = _review_written_question(skill_id, practice, answered) if _is_proof_skill(_skill_row(skill_id)) else None
    if written is not None and size > 0:
        chosen.append(written)
        # one written answer per review is enough: the rest of the set prefers questions that are checked
        practice = [q for q in practice if q["kind"] != "free_response"] + [q for q in practice if q["kind"] == "free_response"]
    for kp in sorted({q["kp_index"] for q in practice} - {q["kp_index"] for q in chosen}):
        best = next((q for q in practice if q["kp_index"] == kp), None)
        if best and len(chosen) < size:
            chosen.append(best)
    for q in practice:
        if len(chosen) >= size:
            break
        if q not in chosen:
            chosen.append(q)
    chosen.sort(key=lambda q: (q["kp_index"], q["tier"]))
    if capstone is not None:
        chosen.append(capstone)
    return [q["id"] for q in chosen]


def start_review(skill_id: str, reason: str, now: datetime, st: dict[str, Any] | None = None) -> str:
    with lock:
        return _start_review(skill_id, reason, now, st)


def _start_review(skill_id: str, reason: str, now: datetime, st: dict[str, Any] | None = None) -> str:
    skill = _skill_row(skill_id)
    st = st if st is not None else _review_initial_state(skill)
    task_id = _insert_task(skill["course_id"], skill_id, "review", "practice", reason, st, now)
    if _unseen_count(skill_id, exclude=set(st["set"])) < REVIEW_SIZE:
        request_top_up(skill_id)
    log.info("started review task %s for skill %s (%s)", task_id, skill["key"], reason)
    return task_id


def abandon(task_id: str, now: datetime) -> Task:
    with lock:
        task = _task_row(task_id)
        if task["status"] == "active":
            _complete(task, _safe_loads(task["state_json"], {}), "abandoned", 0, now)
            log.info("abandoned task %s", task_id)
        return build_task(task)


# ---------------------------------------------------------------------------
# Lesson flow
# ---------------------------------------------------------------------------


def begin_practice(task_id: str, now: datetime) -> Task:
    with lock:
        task = _task_row(task_id)
        if task["status"] != "active":
            raise Conflict("This task is already finished.")
        if task["phase"] == "learn":
            st = _load_state(task)
            if not st.get("current") and not _serve_next(st, _pool(task["skill_id"])):
                raise Conflict("No practice questions are available for this lesson right now.")
            _save_state(task_id, st, phase="practice")
            task = _task_row(task_id)
        return build_task(task)


def _lesson_progress(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime, *, count_failure: bool = True
) -> _Finish | None:
    """After an answer (or a voided one): halt the lesson when 80 % is out of reach, serve the next planned
    question, or finish."""
    pool = _pool(skill["id"])
    if _best_possible(st, pool) < PASS_MARK - EPS:
        return _finish_lesson_fail(task, st, skill, now, pool, counted=count_failure and bool(_live(st)))
    if st.get("current"):
        return None
    if _serve_next(st, pool):
        return None
    return _finish_lesson_pass(task, st, skill, now, pool, count_failure=count_failure)


PROOF_WAIVED_TEXT = (
    "A written proof in this lesson isn't secure yet, so it comes back in your first review — "
    "scheduled a little sooner than usual."
)


def _finish_lesson_pass(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime, pool: _Pool, *, count_failure: bool = True
) -> _Finish:
    points, max_points = _totals(st)
    score = _ratio(points, max_points)
    if score < PASS_MARK - EPS:  # nothing left to serve and below the pass mark
        return _finish_lesson_fail(task, st, skill, now, pool, counted=count_failure and max_points > 0)
    waived = sorted(set(st.get("proof_waived") or []) | {k for k in st["playable"] if _proof_state(st, pool, k) == "waived"})
    st["proof_waived"] = waived
    rating = Rating.Good if score >= GOOD_SCORE - EPS and not waived else Rating.Hard
    amount = xp.lesson_xp(skill["est_minutes"], score)
    _, log_id = scheduler.apply_rating(skill["course_id"], skill["id"], rating, now)
    db.execute(
        "UPDATE progress SET lesson_passed_at = ?, cooldown_until = NULL, updated_at = ? WHERE skill_id = ?",
        (scheduler.iso(now), scheduler.iso(now), skill["id"]),
    )
    st["kp"] = st["kp_total"]
    st["stage"] = "done"
    st["result"] = {
        "review_log_id": log_id, "score": score, "rating": int(rating), "proof_waived": bool(waived),
        "wrong": sum(1 for e in _live(st) if e["res"] == "wrong"),
    }  # fmt: skip
    _complete(task, st, "passed", amount, now)
    xp.award(skill["course_id"], task["id"], amount, now)
    scheduler.apply_success_credit(skill["course_id"], skill["id"], now)
    scheduler.clear_remedial(target=skill["id"])
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("lesson passed: skill %s, score %.2f -> %s, %d XP%s", skill["key"], score, rating.name, amount,
             " (written proof waived)" if waived else "")  # fmt: skip
    return _Finish(
        xp=amount, suggestion_text=PROOF_WAIVED_TEXT if waived else None, note=f" Lesson passed with {_pct(score)}%."
    )


def _lesson_failures(progress: dict[str, Any]) -> dict[str, int]:
    raw = _safe_loads(progress["kp_failures_json"], {})
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _finish_lesson_fail(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime, pool: _Pool, *, counted: bool = True
) -> _Finish:
    stage = st.get("stage", "kp")
    key = CAPSTONE_FAIL_KEY if stage == "capstone" else str(st["kp"])
    progress = _progress_row(skill)
    learned = progress["status"] in scheduler.LEARNED
    failures = _lesson_failures(progress)
    counted = counted and not learned
    if counted:
        failures[key] = failures.get(key, 0) + 1
    if not learned:
        db.execute(
            "UPDATE progress SET kp_failures_json = ?, cooldown_until = ?, updated_at = ? WHERE skill_id = ?",
            (db.dumps(failures), scheduler.iso(now + LESSON_COOLDOWN), scheduler.iso(now), skill["id"]),
        )
    points, max_points = _totals(st)
    st["fail"] = {
        "kp": st["kp"], "key": key, "stage": stage, "counted": counted, "prev_cooldown": progress["cooldown_until"],
        "remediated": [], "score": _ratio(points, max_points),
    }  # fmt: skip
    _complete(task, st, "failed", 0, now)
    if stage != "capstone" and _unseen_count(skill["id"], kp=st["kp"]) < _target(st, st["kp"]) + 1:
        request_top_up(skill["id"], st["kp"])
    finish = _Finish(
        suggestion="retry_later",
        suggestion_text="80% is out of reach on this attempt, so the lesson pauses here. It comes back in about "
        "2 hours with fresh questions — work on something else in the meantime.",
    )
    total = sum(failures.values())
    if counted and total >= REMEDIATION_AFTER_FAILURES:
        chosen = scheduler.remediate(skill["course_id"], skill["id"], now)
        st["fail"]["remediated"] = chosen
        _save_state(task["id"], st)
        if chosen:
            names = " and ".join(f"{r['key']} ({r['title']})" for r in (_skill_row(c) for c in chosen))
            due = "it is now due as a review" if len(chosen) == 1 else "they are now due as reviews"
            finish = _Finish(
                suggestion="review_prerequisite",
                suggestion_text=f"This lesson has stopped short of 80% twice. Refresh {names} first — {due}. "
                "The lesson returns later with fresh questions.",
            )
        else:
            finish.suggestion_text = (
                "This lesson has stopped short of 80% twice. Re-read the source passage for this skill before "
                "the lesson returns."
            )
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("lesson failed: skill %s at %s (failures of this lesson: %d)", skill["key"], key, total)
    return finish


def _needed_written_question(st: dict[str, Any], pool: _Pool) -> bool:
    """Is the current question the KP's last chance at its required free-response answer?"""
    kp = st["kp"]
    fr = _kp_written(pool, kp)
    if st.get("current") not in fr or _proof_state(st, pool, kp) != "pending":
        return False
    served = set(st["served"])
    return not any(qid not in served for qid in fr)


def _easier_candidate(st: dict[str, Any], pool: _Pool) -> dict[str, Any] | None:
    """Lowest-tier unseen question of the current KP, strictly easier than the current question."""
    if st.get("stage") == "capstone" or _needed_written_question(st, pool):
        return None
    served = set(st["served"])
    current = pool.by_id.get(st.get("current") or "")
    max_tier = current["tier"] if current else None
    candidates = [
        (pos, q)
        for pos, q in enumerate(pool.practice(st["kp"]))
        if q["id"] not in served and (max_tier is None or q["tier"] < max_tier)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda pq: (pq[1]["tier"], pq[1]["id"] in pool.answered, pq[0]))[1]


def _easier_blocker(st: dict[str, Any], pool: _Pool) -> str | None:
    """Why POST /easier would be refused right now (None: it would succeed). The task must be an ongoing lesson."""
    if not st.get("current"):
        return "There is no current lesson question to swap."
    if st.get("stage") == "capstone":
        return "The final problem has no easier version — use a step's hint if you're stuck."
    if int(st.get("step") or 0) > 0:
        return "Finish this problem first — use the step's hint if you're stuck."
    if not any(e["res"] == "wrong" for e in _kp_done(st, st["kp"])):
        return "Try this question first."
    if _needed_written_question(st, pool):
        return (
            "This written answer is needed to finish the step, so it can't be swapped for an easier question. "
            "Re-read the worked example — or use a hint: it still earns half a point."
        )
    if _easier_candidate(st, pool) is None:
        return "No easier question is left in this step — try a hint or show the solution."
    return None


def easier_step(task_id: str, now: datetime) -> Task:
    with lock:
        task = _task_row(task_id)
        if task["type"] != "lesson":
            raise Conflict("An easier step is only available during a lesson.")
        st = _load_state(task)
        if task["status"] != "active" or task["phase"] != "practice" or not st.get("current"):
            raise Conflict("There is no current lesson question to swap.")
        pool = _pool(task["skill_id"])
        blocker = _easier_blocker(st, pool)
        if blocker is not None:
            raise Conflict(blocker)
        candidate = _easier_candidate(st, pool)
        assert candidate is not None
        slot = st["slots"].setdefault(str(st["kp"]), [])
        if st["current"] in slot:
            slot.remove(st["current"])
        slot.append(candidate["id"])
        st["served"].append(candidate["id"])
        st["easier"].append(candidate["id"])
        _set_current(st, candidate["id"])
        st["wrong_run"] = 0
        _save_state(task_id, st)
        log.info("task %s: easier step -> question %s (tier %s)", task_id, candidate["key"], candidate["tier"])
        return build_task(_task_row(task_id))


# ---------------------------------------------------------------------------
# Review flow
# ---------------------------------------------------------------------------


def _replacement_review_question(skill_id: str, st: dict[str, Any], voided: dict[str, Any]) -> str | None:
    """A capstone is replaced by another capstone when there is one, else by a practice question; a practice
    question by a practice question (same KP first)."""
    pool = _pool(skill_id)
    served = set(st["served"])
    order = lambda q: (q["kp_index"] != voided["kp_index"], q["id"] in pool.answered, pool.answered.get(q["id"], ""))  # noqa: E731
    practice = sorted((q for q in pool.practice() if q["id"] not in served), key=order)
    if _is_capstone(voided):
        caps = sorted((q for q in pool.capstones() if q["id"] not in served), key=order)
        practice = caps + practice
    return practice[0]["id"] if practice else None


def _review_score(st: dict[str, Any]) -> float:
    live = _live(st)
    return scheduler.review_score([e["credit"] for e in live], [e["w"] for e in live])


def _apply_review_result(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> tuple[float, Rating]:
    score = _review_score(st)
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


def _apply_fire(skill: dict[str, Any], rating: Rating, now: datetime) -> list[dict[str, Any]]:
    """FIRe-lite after a review; the events are kept so a re-score can undo them."""
    if rating == Rating.Again:
        events = scheduler.apply_failure_penalty(skill["course_id"], skill["id"], now)
    else:
        events = scheduler.apply_success_credit(skill["course_id"], skill["id"], now)
    return [asdict(e) for e in events]


def _review_outcome(score: float) -> str:
    return "passed" if score >= PASS_MARK - EPS else "failed"


def _finish_review(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> _Finish:
    score, rating = _apply_review_result(task, st, skill, now)
    st["result"]["fire"] = _apply_fire(skill, rating, now)
    amount = xp.review_xp(score)
    outcome = _review_outcome(score)
    _complete(task, st, outcome, amount, now)
    xp.award(skill["course_id"], task["id"], amount, now)
    scheduler.clear_remedial(skill["id"])
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("review %s: skill %s score %.2f -> %s, %d XP", task["id"], skill["key"], score, rating.name, amount)
    if outcome == "passed":
        return _Finish(xp=amount, note=f" Review score {_pct(score)}%.")
    if rating == Rating.Hard:
        text = f"Recall was a little shaky ({_pct(score)}%). This skill comes back a bit sooner than usual."
    else:
        text = (
            f"Recall was shaky ({_pct(score)}%). This skill comes back for review soon — "
            "reopen its lesson's worked examples before then."
        )
    return _Finish(xp=amount, suggestion="reread_example", suggestion_text=text)


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


_SELF_GRADE_FEEDBACK = {
    "correct": "Self-assessed as correct.",
    "partial": "Self-assessed as partly correct — half a point.",
    "incorrect": "Self-assessed as incorrect. Study the model solution.",
}


def _norm_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _nothing_submitted_message(kind: str, answer: str) -> str:
    if _is_choice_kind(kind):
        return "Choose one of the options."
    if kind == "true_false_set":
        return grading.true_false_set_request(answer)
    return "Enter an answer first."


def _submitted_nothing(kind: str, sub: AnswerSubmit) -> bool:
    return sub.choice is None if _is_choice_kind(kind) else not (sub.response or "").strip()


def _auto_check(q: dict[str, Any], sub: AnswerSubmit) -> grading.GradeResult | None:
    """Correctness of an auto-graded answer; None for free response or when nothing was submitted.
    Called WITHOUT holding `lock` (math grading is bounded by grading.GRADE_TIMEOUT_SECONDS)."""
    if q["kind"] == "free_response" or _submitted_nothing(q["kind"], sub):
        return None
    return grading.check_answer(
        q["kind"],
        answer=q["answer"],
        correct_choice=q["correct_choice"],
        tolerance=q["tolerance"],
        response=sub.response,
        choice=sub.choice,
        require_simplified=True,
    )


def _step_check(step: dict[str, Any], sub: AnswerSubmit) -> grading.GradeResult | None:
    """Correctness of one multistep step (WITHOUT holding `lock`); None when nothing was submitted."""
    if _submitted_nothing(step["kind"], sub):
        return None
    tolerance = step.get("tolerance")
    return grading.check_answer(
        step["kind"],
        answer=str(step.get("answer") or ""),
        correct_choice=_int_or_none(step.get("correct_choice")),
        tolerance=float(tolerance) if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool) else None,
        response=sub.response,
        choice=sub.choice,
        require_simplified=True,
    )


def _points(correct: bool, hints: int, revealed: bool) -> float:
    if revealed or not correct:
        return 0.0
    return HINT_POINTS if hints > 0 else 1.0


def _grade(q: dict[str, Any], sub: AnswerSubmit, st: dict[str, Any], check: grading.GradeResult | None) -> _Graded:
    """Combine the correctness check with the server-side record of help for the current attempt (D1, D2)."""
    hints = max(0, sub.hints_used, int(st.get("hints") or 0))
    revealed = bool(sub.revealed_solution or st.get("revealed"))
    seconds = max(0.0, sub.seconds)
    if q["kind"] == "free_response":
        if sub.self_grade is None:
            raise Invalid("Self-assess your answer against the checklist: correct, partial or incorrect.")
        compared = st.get("compared")
        if compared is not None and (sub.response or "").strip() and _norm_text(sub.response) != compared:
            revealed = True  # the answer was changed after seeing the model solution
        correct = sub.self_grade == "correct"
        too_short = len(re.sub(r"\s", "", sub.response or "")) < COMPARE_MIN_CHARS  # not a written answer (E1)
        independent = correct and hints == 0 and not revealed and not too_short
        res = "correct" if independent else ("assisted" if correct else "wrong")
        if revealed:
            pts = 0.0
        elif sub.self_grade == "correct":
            pts = 1.0 if independent else HINT_POINTS
        else:
            pts = HINT_POINTS if sub.self_grade == "partial" else 0.0
        feedback = _SELF_GRADE_FEEDBACK[sub.self_grade]
        return _Graded(correct, "self_graded", independent, feedback, res, sub.response or "", revealed, hints, pts, seconds)
    if check is None:
        if not revealed:
            raise Invalid(_nothing_submitted_message(q["kind"], q["answer"]))
        correct, feedback = False, "Solution viewed — no answer submitted."
    else:
        if check.invalid:
            raise Invalid(check.feedback)
        correct, feedback = check.correct, check.feedback
    independent = correct and hints == 0 and not revealed
    res = "correct" if independent else ("assisted" if correct else "wrong")
    response = str(sub.choice) if _is_choice(q) and sub.choice is not None else (sub.response or "")
    return _Graded(
        correct, "correct" if correct else "incorrect", independent, feedback, res, response, revealed, hints,
        _points(correct, hints, revealed), seconds,
    )  # fmt: skip


def _grade_step(index: int, step: dict[str, Any], sub: AnswerSubmit, st: dict[str, Any], check) -> dict[str, Any]:
    hinted = bool(st.get("step_hint")) or sub.hints_used > 0
    revealed = bool(st.get("step_revealed")) or sub.revealed_solution
    if check is None:
        if not revealed:
            raise Invalid(_nothing_submitted_message(step["kind"], str(step.get("answer") or "")))
        correct, feedback = False, "Answer viewed — nothing submitted."
    else:
        if check.invalid:
            raise Invalid(check.feedback)
        correct, feedback = check.correct, check.feedback
    score = _points(correct, int(hinted), revealed)
    if correct and revealed:
        feedback += " The answer was shown first, so no point for this step."
    elif correct and hinted:
        feedback += " With the hint, so half a point for this step."
    response = str(sub.choice) if _is_choice_kind(step["kind"]) and sub.choice is not None else (sub.response or "")
    return {
        "index": index, "correct": correct, "score": score, "feedback": feedback,
        "answer_display": _step_display(step), "explanation": str(step.get("explanation") or ""),
        "response": response[:1000], "hinted": hinted, "revealed": revealed,
    }  # fmt: skip


def _graded_multistep(st: dict[str, Any]) -> _Graded:
    results = st["step_results"]
    pts = sum(r["score"] for r in results) / len(results)
    independent = all(r["score"] >= 1 - EPS for r in results)
    if independent:
        res = "correct"
    elif pts <= EPS:
        res = "wrong"
    else:
        res = "assisted" if all(r["correct"] for r in results) else "partial"
    response = json.dumps(
        {"steps": [{k: r[k] for k in ("index", "response", "correct", "score", "hinted", "revealed")} for r in results]},
        ensure_ascii=False,
    )
    return _Graded(
        correct=independent, status="correct" if independent else "incorrect", independent=independent, feedback="",
        res=res, response=response, revealed=any(r["revealed"] for r in results),
        hints=sum(1 for r in results if r["hinted"]), pts=pts, seconds=float(st.get("step_seconds") or 0.0),
    )  # fmt: skip


def _assisted_note(revealed: bool, *, helped: bool = True) -> str:
    if not helped:  # a self-graded written answer too short to count (no hint, no reveal)
        return " It's too short to count as a written answer, so half a point."
    return " The solution was shown first, so no points." if revealed else " With a hint, so half a point."


def _record_attempt(task: dict[str, Any], q: dict[str, Any], g: _Graded, now: datetime) -> None:
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, independent, response, hints_used, revealed, seconds, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            db.new_id(), task["id"], q["id"], q["skill_id"], int(g.correct), g.status, int(g.independent),
            g.response[:4000], g.hints, int(g.revealed), g.seconds, scheduler.iso(now),
        ),
    )  # fmt: skip


def _answerable(task: dict[str, Any], st: dict[str, Any], sub: AnswerSubmit) -> None:
    if task["status"] != "active":
        raise Conflict("This task is already finished.")
    if task["phase"] != "practice":
        raise Conflict("Start the practice first.")
    if sub.question_id != st.get("current"):
        raise Conflict("That question is not the current one — reload the task.")


def _current_step(q: dict[str, Any], st: dict[str, Any], sub: AnswerSubmit) -> tuple[int, dict[str, Any]]:
    steps = _steps(q)
    index = int(st.get("step") or 0)
    if not steps or not 0 <= index < len(steps):
        raise Conflict("This problem has no step to answer — reload the task.")
    if sub.step is None or sub.step != index:
        raise Conflict(f"That step is not the current one (step {index + 1}) — reload the task.")
    return index, steps[index]


def submit_answer(task_id: str, sub: AnswerSubmit, now: datetime) -> AnswerResult:
    with lock:
        task = _task_row(task_id)
        st = _load_state(task)
        _answerable(task, st, sub)
        q = _question_row(sub.question_id)
        step = _current_step(q, st, sub) if q["kind"] == "multistep" else None
    check = _step_check(step[1], sub) if step else _auto_check(q, sub)  # the slow part runs without the lock
    if check is not None and check.invalid:
        raise Invalid(check.feedback)
    with lock:
        task = _task_row(task_id)
        st = _load_state(task)
        _answerable(task, st, sub)  # it may have changed while grading (another tab, a flag ...)
        q = _question_row(sub.question_id)
        skill = _skill_row(task["skill_id"])
        if q["kind"] == "multistep":
            index, step_def = _current_step(q, st, sub)
            if step is None or index != step[0]:
                raise Conflict("That step is not the current one — reload the task.")
            return _submit_step(task, st, skill, q, index, step_def, sub, check, now)
        return _commit_answer(task, st, skill, q, _grade(q, sub, st, check), sub, now)


def _submit_step(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], q: dict[str, Any], index: int,
    step: dict[str, Any], sub: AnswerSubmit, check, now: datetime,
) -> AnswerResult:  # fmt: skip
    result = _grade_step(index, step, sub, st, check)
    st["step_results"].append(result)
    st["step_seconds"] = float(st.get("step_seconds") or 0.0) + max(0.0, sub.seconds)
    public = _step_result_public(result)
    correct_choice = _int_or_none(step.get("correct_choice")) if _is_choice_kind(step["kind"]) else None
    if index + 1 < len(_steps(q)):
        st["step"] = index + 1
        st["step_hint"] = False
        st["step_revealed"] = False
        _save_state(task["id"], st)
        running = sum(r["score"] for r in st["step_results"]) / len(st["step_results"])
        return AnswerResult(
            correct=result["correct"], status="step", independent=result["score"] >= 1 - EPS, feedback=result["feedback"],
            correct_answer=result["answer_display"], correct_choice=correct_choice, solution=result["explanation"],
            task=build_task(_task_row(task["id"])), score=running, step_result=public, question_done=False,
        )  # fmt: skip
    graded = _graded_multistep(st)
    n_right = sum(1 for r in st["step_results"] if r["correct"])
    graded.feedback = f"{result['feedback']} Problem complete: {n_right} of {len(st['step_results'])} steps right."
    return _commit_answer(
        task, st, skill, q, graded, sub, now,
        step=(public, result["answer_display"], correct_choice, q["solution"] or result["explanation"]),
    )  # fmt: skip


def _commit_answer(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], q: dict[str, Any], graded: _Graded,
    sub: AnswerSubmit, now: datetime, *, step: tuple[StepResult, str, int | None, str] | None = None,
) -> AnswerResult:  # fmt: skip
    """Record a finished question attempt and move the task on."""
    _record_attempt(task, q, graded, now)
    if q["kind"] == "free_response" and sub.self_grade:
        st["self_grades"][q["id"]] = sub.self_grade
    st["current"] = None
    finish = (_after_lesson_answer if task["type"] == "lesson" else _after_review_answer)(task, st, skill, q, graded, now)
    if task["status"] == "active":
        _save_state(task["id"], st)
    feedback = graded.feedback
    if graded.res == "assisted" and q["kind"] != "multistep":
        feedback += _assisted_note(graded.revealed, helped=graded.revealed or graded.hints > 0)
    if q["kind"] == "free_response" and sub.self_grade == "incorrect" and task["status"] == "active":
        feedback = feedback.rstrip(".") + ", then try the next one."
    if step is not None:
        step_result, correct_answer, correct_choice, solution = step
    else:
        step_result, correct_answer = None, _answer_display(q)
        correct_choice, solution = (q["correct_choice"] if _is_choice(q) else None), q["solution"]
    return AnswerResult(
        correct=graded.correct,
        status=graded.status,
        independent=graded.independent,
        feedback=feedback + finish.note,
        correct_answer=correct_answer,
        correct_choice=correct_choice,
        solution=solution,
        task=build_task(_task_row(task["id"])),
        xp_awarded=finish.xp,
        suggestion=finish.suggestion,
        suggestion_text=finish.suggestion_text,
        score=graded.pts,
        step_result=step_result,
        question_done=True,
    )


def _after_lesson_answer(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], q: dict[str, Any], g: _Graded, now: datetime
) -> _Finish:
    pool = _pool(skill["id"])
    kp = st["kp"]
    capstone = _is_capstone(q)
    stage_before = st.get("stage", "kp")
    in_slot = not capstone and q["id"] in _slot(st, kp)
    plan_before = _kp_plan(st, pool, kp) if in_slot else 0
    st["log"].append({
        "q": q["id"], "kp": q["kp_index"], "res": g.res, "pts": g.pts, "w": CAPSTONE_WEIGHT if capstone else 1,
        "ok": g.correct, "void": False, "scaffold": q["id"] in st["easier"], "cap": capstone,
    })  # fmt: skip
    st["wrong_run"] = st.get("wrong_run", 0) + 1 if g.res == "wrong" else 0
    extra = in_slot and _kp_plan(st, pool, kp) > plan_before
    finish = _lesson_progress(task, st, skill, now)
    if finish is not None:
        return finish
    note = ""
    same_kp = st.get("stage") != "capstone" and st["kp"] == kp
    if extra and same_kp:
        note = " One more question on this step comes next, so you can make up for it."
    if same_kp and q["kind"] == "free_response" and g.res != "correct":
        proof = _proof_state(st, pool, kp)
        if proof == "pending":
            note += " This step still needs one written answer without help — another written question comes up soon."
        elif proof == "waived":
            note += " No other written question is left in this step — the proof comes back in your first review."
    elif same_kp and _proof_state(st, pool, kp) == "pending" and len(_kp_done(st, kp)) >= _kp_plan(st, pool, kp):
        note += " To finish this step, also answer one written question in your own words."
    if stage_before != "capstone" and st.get("stage") == "capstone":
        note += " Last: one problem that puts the lesson together, step by step — it counts double."
    if g.res == "wrong" and not capstone and st.get("stage") != "capstone":
        if _easier_blocker(st, pool) is None:  # the same test as TaskProgress.easier_available
            return _Finish(
                suggestion="easier_step",
                suggestion_text="Missed that one. If the next question looks hard, take an easier step: re-read the "
                "worked example, then try a simpler question.",
                note=note,
            )
        if st["wrong_run"] >= REREAD_AFTER_WRONG:
            return _Finish(
                suggestion="reread_example",
                suggestion_text="Two misses in a row — re-read the worked example above before the next question.",
                note=note,
            )
    return _Finish(note=note)


def _after_review_answer(
    task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], q: dict[str, Any], g: _Graded, now: datetime
) -> _Finish:
    capstone = _is_capstone(q)
    st["log"].append({
        "q": q["id"], "kp": q["kp_index"], "res": g.res, "credit": g.pts, "pts": g.pts,
        "w": CAPSTONE_WEIGHT if capstone else 1, "ok": g.correct, "void": False, "cap": capstone,
    })  # fmt: skip
    st["pos"] += 1
    if st["pos"] >= len(st["set"]):
        return _finish_review(task, st, skill, now)
    _set_current(st, st["set"][st["pos"]])
    return _Finish()


# ---------------------------------------------------------------------------
# Hints, reveal, flag
# ---------------------------------------------------------------------------


def _tasks_on_question(q: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Active tasks whose current question is q, with their state."""
    out = []
    for task in db.query(
        "SELECT * FROM tasks WHERE skill_id = ? AND status = 'active' AND state_json LIKE ?", (q["skill_id"], f"%{q['id']}%")
    ):
        st = _load_state(task)
        if st.get("current") == q["id"]:
            out.append((task, st))
    return out


def hint(question_id: str, now: datetime, step: int | None = None) -> HintResponse:
    """Show the next hint for the current attempt and record it server-side (D2). A multistep question has one hint
    per step: `step` (default: the current step) must be the current step."""
    with lock:
        q = _question_row(question_id)
        current = _tasks_on_question(q)
        if not current:
            raise Conflict("Hints are available for the question you're answering — reload the task.")
        if q["kind"] == "multistep":
            return _step_hint(q, current, step)
        hints = [h for h in _safe_loads(q["hints_json"], []) if isinstance(h, str)]
        if not hints:
            raise Conflict("This question has no hints.")
        used = 0
        for task, st in current:
            used = max(used, min(len(hints), int(st.get("hints") or 0) + 1))
        for task, st in current:
            if int(st.get("hints") or 0) != used:
                st["hints"] = used
                _save_state(task["id"], st)
        log.info("question %s: hint %d of %d shown", q["key"], used, len(hints))
        return HintResponse(hint=hints[used - 1], hints_used=used)


def _step_hint(q: dict[str, Any], current: list[tuple[dict[str, Any], dict[str, Any]]], step: int | None) -> HintResponse:
    steps = _steps(q)
    index = int(current[0][1].get("step") or 0)
    if step is not None and step != index:
        raise Conflict(f"That step is not the current one (step {index + 1}) — reload the task.")
    if not 0 <= index < len(steps):
        raise Conflict("This problem has no step to answer — reload the task.")
    text = str(steps[index].get("hint") or "").strip()
    if not text:
        raise Conflict("This step has no hint.")
    for task, st in current:
        if int(st.get("step") or 0) == index and not st.get("step_hint"):
            st["step_hint"] = True
            _save_state(task["id"], st)
    log.info("question %s: hint for step %d shown", q["key"], index + 1)
    return HintResponse(hint=text, hints_used=1)


def reveal(question_id: str, now: datetime, response: str | None = None) -> RevealResponse:
    """Show the model solution. For free response, a committed answer (>= COMPARE_MIN_CHARS non-space characters)
    makes this a comparison for self-grading; any other reveal before an answer marks the attempt as revealed (D1).
    A multistep question reveals only its current step's answer (that step then scores 0)."""
    with lock:
        q = _question_row(question_id)
        current = _tasks_on_question(q)
        if q["kind"] == "multistep":
            steps = _steps(q)
            if current and steps:
                index = max(0, min(int(current[0][1].get("step") or 0), len(steps) - 1))
                for task, st in current:
                    if not st.get("step_revealed"):
                        st["step_revealed"] = True
                        _save_state(task["id"], st)
                        log.info("task %s: answer of step %d revealed for question %s", task["id"], index + 1, q["key"])
                step = steps[index]
                return RevealResponse(solution=str(step.get("explanation") or ""), correct_answer=_step_display(step))
            return RevealResponse(solution=q["solution"], correct_answer=_answer_display(q))
        committed = q["kind"] == "free_response" and len(re.sub(r"\s", "", response or "")) >= COMPARE_MIN_CHARS
        for task, st in current:
            if st.get("revealed") or st.get("compared") is not None:
                continue  # already revealed, or the answer was committed before (a later reveal changes nothing)
            if committed:
                st["compared"] = _norm_text(response)
                st["compared_raw"] = (response or "").strip()[:4000]
                log.info("task %s: free-response answer committed and compared for question %s", task["id"], q["key"])
            else:
                st["revealed"] = True
                log.info("task %s: solution revealed for question %s", task["id"], q["key"])
            _save_state(task["id"], st)
        return RevealResponse(solution=q["solution"], correct_answer=_answer_display(q))


def _task_serving(question_id: str, skill_id: str) -> dict[str, Any] | None:
    rows = db.query(
        "SELECT * FROM tasks WHERE skill_id = ? AND state_json LIKE ? ORDER BY status = 'active' DESC, created_at DESC, rowid DESC",
        (skill_id, f"%{question_id}%"),
    )
    return next((r for r in rows if question_id in _safe_loads(r["state_json"], {}).get("served", [])), None)


def flag(question_id: str, reason: str, now: datetime) -> Task:
    """Exclude a question the learner believes is wrong; its attempts in the task never count against them."""
    with lock:
        q = _question_row(question_id)
        task = _task_serving(question_id, q["skill_id"])
        if task is None:
            raise Conflict("This question hasn't been served in a task yet.")
        db.execute(
            "UPDATE questions SET flagged = 1, flag_reason = ? WHERE id = ?",
            (f"learner: {(reason or '').strip()}"[:500], question_id),
        )
        db.execute("UPDATE attempts SET status = 'voided' WHERE task_id = ? AND question_id = ?", (task["id"], question_id))
        log.info("question %s flagged by learner (task %s): %s", q["key"], task["id"], reason)
        st = _load_state(task)
        for entry in st["log"]:
            if entry["q"] == question_id:
                entry["void"] = True
        skill = _skill_row(task["skill_id"])
        if task["type"] == "lesson":
            _void_in_lesson(task, st, skill, question_id, now)
        else:
            _void_in_review(task, st, skill, q, now)
        request_top_up(skill["id"], None if _is_capstone(q) else q["kp_index"])
        return build_task(_task_row(task["id"]))


def _reopen_failed_lesson(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> bool:
    """A lesson that failed partly because of a now-flagged question resumes where it stopped."""
    fail = st.get("fail")
    if task["outcome"] != "failed" or not fail or active_task_row(task["course_id"]) is not None:
        return False
    if fail.get("counted"):
        progress = _progress_row(skill)
        failures = _lesson_failures(progress)
        key = str(fail.get("key", fail.get("kp")))
        failures[key] = max(0, failures.get(key, 0) - 1)
        db.execute(
            "UPDATE progress SET kp_failures_json = ?, cooldown_until = ?, updated_at = ? WHERE skill_id = ?",
            (db.dumps({k: v for k, v in failures.items() if v}), fail.get("prev_cooldown"), scheduler.iso(now), skill["id"]),
        )
    if isinstance(fail.get("kp"), int):
        st["kp"] = fail["kp"]
    st["stage"] = fail.get("stage", st.get("stage", "kp"))
    st["fail"] = None
    db.execute(
        "UPDATE tasks SET status = 'active', phase = 'practice', outcome = NULL, completed_at = NULL WHERE id = ?", (task["id"],)
    )
    task.update(status="active", phase="practice", outcome=None)
    log.info("task %s reopened after a flag", task["id"])
    return True


def _void_in_lesson(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], qid: str, now: datetime) -> None:
    answered_here = any(e["q"] == qid for e in st["log"])
    for slot in st["slots"].values():
        if qid in slot:
            slot.remove(qid)
    if task["status"] == "completed":
        if task["outcome"] == "passed":
            _regrade_passed_lesson(task, st, skill, now)
            _save_state(task["id"], st)
            return
        reachable = task["outcome"] == "failed" and _best_possible(st, _pool(skill["id"])) >= PASS_MARK - EPS
        if not (answered_here and reachable and _reopen_failed_lesson(task, st, skill, now)):
            _save_state(task["id"], st)
            return
    if st.get("current") == qid:
        _set_current(st, None)
    run = 0
    for e in reversed(_kp_done(st, st["kp"])):
        if e["res"] != "wrong":
            break
        run += 1
    st["wrong_run"] = run
    # A pool emptied by flags is not the learner's failure: it pauses the lesson without counting toward remediation.
    _lesson_progress(task, st, skill, now, count_failure=False)
    if task["status"] == "active":
        _save_state(task["id"], st)


def _regrade_passed_lesson(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], now: datetime) -> None:
    """Voiding an answer of a passed lesson can only raise its score: top the XP up to the new score's."""
    points, max_points = _totals(st)
    score = _ratio(points, max_points)
    amount = xp.lesson_xp(skill["est_minutes"], score)
    if amount > task["xp_awarded"]:
        xp.award(skill["course_id"], task["id"], amount - task["xp_awarded"], now)
        db.execute("UPDATE tasks SET xp_awarded = ? WHERE id = ?", (amount, task["id"]))
        st["result"]["score"] = score
        st["result"]["wrong"] = sum(1 for e in _live(st) if e["res"] == "wrong")


def _void_in_review(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any], q: dict[str, Any], now: datetime) -> None:
    qid = q["id"]
    if task["status"] == "completed":
        _rescore_review(task, st, skill)
        return
    if qid in st["set"]:
        idx = st["set"].index(qid)
        replacement = _replacement_review_question(skill["id"], st, q)
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
    if st.get("current") != st["set"][st["pos"]]:
        _set_current(st, st["set"][st["pos"]])
    _save_state(task["id"], st)


def _rescore_review(task: dict[str, Any], st: dict[str, Any], skill: dict[str, Any]) -> None:
    """Re-score a finished review without the voided answer, if nothing has touched the skill's card since.
    The FIRe effects of the original result on other skills are undone and those of the new result applied."""
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
    scheduler.revert_implicit(result.get("fire", []))
    xp.revoke(task["id"])
    reviewed_at = db.parse_iso(result["reviewed_at"])
    assert reviewed_at is not None
    if not _live(st):
        _complete(task, st, "abandoned", 0, reviewed_at)
        skill_state.recompute_unlocks(skill["course_id"])
        log.info("review %s voided entirely after a flag", task["id"])
        return
    score, rating = _apply_review_result(task, st, skill, reviewed_at)
    st["result"]["fire"] = _apply_fire(skill, rating, reviewed_at)
    amount = xp.review_xp(score)
    _complete(task, st, _review_outcome(score), amount, reviewed_at)
    xp.award(skill["course_id"], task["id"], amount, reviewed_at)
    skill_state.recompute_unlocks(skill["course_id"])
    log.info("review %s re-scored after a flag: %.2f -> %s", task["id"], score, rating.name)
