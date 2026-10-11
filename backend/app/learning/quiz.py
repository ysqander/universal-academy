"""Quizzes (SPEC §6.7): timed, forced, cumulative checks about every 150 XP — reviews consolidate, quizzes validate.

Due      >= QUIZ_XP_INTERVAL course XP since the last regular quiz finished (XP of quiz tasks — quizzes and retakes —
         excluded) AND >= QUIZ_MIN_NEW_SKILLS skills whose lesson was passed since then AND at least
         QUIZ_MIN_QUESTIONS learned skills with a quiz-able question (else a quiz could not be built and would block
         the learner). A due quiz is forced (learning/selector.py): an active lesson/review may be finished first,
         then only the quiz can be started, and it cannot be abandoned.
Content  one single-answer question (numeric, expression, multiple_choice, true_false, true_false_set — never
         multistep, free response or a capstone) per skill: skills not quizzed yet first, earliest learned first (so
         skills left out when more than 8 are new lead the next quiz), then already-quizzed skills with the lowest
         recall; 6-8 questions (5 when only 5 learned skills qualify).
         Per skill: a question the learner has not seen first, then the tier preference (default tier >= 2; last quiz
         >= 90 % -> tier 3 first; < 70 % -> tiers 1-2 first), then pool order. Questions come in learning order.
         Skills left with few unseen quiz-able questions get a question top-up (engine.request_top_up).
Clock    60 s (multiple choice, true/false) or 90 s (numeric, expression, true/false set) per question × the
         quiz_time_factor setting (0.5-3.0, default 1.0), rounded up to whole minutes; frozen when POST /practice
         starts the clock (before that, the intro shows the limit for the current setting). The server enforces the
         deadline (+ DEADLINE_GRACE for network latency): a later answer gets 409 "Time is up." and finishes the quiz;
         reading the task, the menu or the course stats finishes an expired quiz too. Unanswered questions score 0.
         The task's reason ("6 questions, about 6 minutes") follows the setting until the clock starts.
Conduct  one question at a time; no hints, solution or easier step (409); `skip` scores 0; flagging a question voids
         it (dropped from the score, not replaced). Answers return status "recorded" and nothing about correctness. A
         value that is right but not simplified scores 0 — the lessons' 422 "simplify" would tell the learner the value
         is right.
End      score = mean of 1/0 over the non-voided questions; pass mark 80 %; XP = round(time-limit minutes × score).
         Every question not reached gets an unanswered attempt row: the results show all answers and solutions, so
         lessons and reviews treat the whole set as seen. Missed skills (wrong, skipped, unanswered) -> remedial review
         due now, top of the menu ("Missed in the quiz on 6 Oct"; scheduler.mark_quiz_remedial). A right answer on a
         skill due within 3 days -> FSRS Good (explicit review, review_log) and it settles a remedial mark left by an
         earlier quiz; otherwise the answer is only recorded. What the quiz changed on each card is kept
         (result.effects), so a question flagged after the end is undone (card, review_log, mark) unless the card has
         changed since. Below 80 %, once every missed skill has had a
         completed review, the latest quiz offers one optional retake (same skills, fresh questions, never forced,
         can be left before its clock starts — then it is still on offer —, does not reset the 150-XP counter).

The task row has type 'quiz' and skill_id = the first quiz skill (the column is NOT NULL); the API shows skill None.
State (tasks.state_json):
    {"kind": "quiz" | "retake", "of": quiz task id (retake) | None, "set": [qid…], "skills": [sid…] (parallel to set),
     "pos": index of the current question in set, "current", "served": [qid shown…], "served_at": iso (current),
     "time_limit": seconds | None (frozen at start), "started_at", "deadline",
     "log": [{"q", "skill", "pts", "answered", "skipped", "response", "seconds"}], "voided": [qid…],
     "result": {"score", "correct", "total", "xp", "missed": [sid…], "credited": [sid…], "timed_out", "finished_at",
                "effects": {sid: {"before": {card_json, status, reviews}, "after_card", "review_log_id", "mark"}}}}

A quiz row closed as 'abandoned' (only maintenance scripts can do that) validated nothing: it neither resets the
150-XP counter nor counts its skills as quizzed.

Every mutating function runs under engine.lock (re-entrant) and takes an explicit `now`.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fsrs import Rating

from .. import db, views
from ..schemas import AnswerResult, AnswerSubmit, QuestionPublic, QuizInfo, QuizQuestionResult, SkillRef, Task, TaskProgress
from . import engine, grading, scheduler, xp
from . import state as skill_state

log = logging.getLogger("ua.learning.quiz")

QUIZ_XP_INTERVAL = 150
QUIZ_MIN_NEW_SKILLS = 5
QUIZ_MIN_QUESTIONS = 5  # fewer learned skills with quiz-able questions: no quiz
QUIZ_TARGET_QUESTIONS = 6
QUIZ_MAX_QUESTIONS = 8
QUIZ_KINDS = ("numeric", "expression", "multiple_choice", "true_false", "true_false_set")
SECONDS_PER_QUESTION = {"multiple_choice": 60, "true_false": 60, "numeric": 90, "expression": 90, "true_false_set": 90}
TIME_FACTOR_KEY = "quiz_time_factor"
DEFAULT_TIME_FACTOR = 1.0
MIN_TIME_FACTOR, MAX_TIME_FACTOR = 0.5, 3.0
DEADLINE_GRACE = timedelta(seconds=2)  # an answer sent just before the deadline still counts
PASS_MARK = 0.8
CREDIT_WINDOW = timedelta(days=3)  # a right answer counts as an FSRS review only when the skill is due within this
HARDER_FROM, EASIER_BELOW = 0.9, 0.7  # last quiz score -> tier preference
TOPUP_BELOW_UNSEEN = 2  # unseen quiz-able questions left for a skill (besides the one chosen)
QUIZ_OPTION_IMPORTANCE = 100.0
RETAKE_OPTION_IMPORTANCE = 0.5
EPS = 1e-9
_SKILL_COLUMNS = "s.id, s.course_id, s.key, s.title, s.order_index"  # enough for SkillRef + ordering (no lesson JSON)

_STATE_DEFAULTS: dict[str, Any] = {
    "kind": "quiz", "of": None, "set": [], "skills": [], "pos": 0, "current": None, "served": [], "served_at": None,
    "time_limit": None, "started_at": None, "deadline": None, "log": [], "voided": [], "result": {},
}  # fmt: skip


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _clamp_factor(value: float) -> float:
    return min(MAX_TIME_FACTOR, max(MIN_TIME_FACTOR, value))


def get_time_factor() -> float:
    raw = db.kv_get(TIME_FACTOR_KEY)
    try:
        value = float(raw) if raw is not None else DEFAULT_TIME_FACTOR
    except ValueError:
        return DEFAULT_TIME_FACTOR
    return _clamp_factor(value) if math.isfinite(value) else DEFAULT_TIME_FACTOR


def set_time_factor(value: float) -> float:
    value = round(_clamp_factor(float(value)), 2)
    db.kv_set(TIME_FACTOR_KEY, str(value))
    return value


def time_limit_seconds(kinds: list[str], factor: float | None = None) -> int:
    """Σ per-question allowance × factor, rounded up to whole minutes (at least one)."""
    factor = get_time_factor() if factor is None else factor
    raw = round(sum(SECONDS_PER_QUESTION.get(k, 90) for k in kinds) * factor, 6)
    return max(1, math.ceil(raw / 60)) * 60


def _minutes_text(minutes: int) -> str:
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _size_text(questions: int, minutes: int) -> str:
    return f"{questions} question{'s' if questions != 1 else ''}, about {_minutes_text(minutes)}"


_SIZE_RE = re.compile(r"\d+ questions?, about \d+ minutes?")


def _reason_with_size(reason: str, questions: int, minutes: int) -> str:
    """The task's reason with its size part ("6 questions, about 6 minutes") set to these values."""
    return _SIZE_RE.sub(lambda _: _size_text(questions, minutes), reason or "", count=1)


def quiz_day(dt: datetime | None) -> str:
    """'6 Oct' in the machine's local time zone."""
    if dt is None:
        return "an earlier day"
    local = scheduler.utc(dt).astimezone()
    return f"{local.day} {local.strftime('%b')}"


# ---------------------------------------------------------------------------
# State + quiz rows
# ---------------------------------------------------------------------------


def load_state(task: dict[str, Any]) -> dict[str, Any]:
    st = engine._safe_loads(task["state_json"], {})
    for key, value in _STATE_DEFAULTS.items():
        if key not in st or (isinstance(value, (list, dict)) and not isinstance(st[key], type(value))):
            st[key] = type(value)() if isinstance(value, (list, dict)) else value
    if st["kind"] not in ("quiz", "retake"):
        st["kind"] = "quiz"
    return st


@dataclass
class _QuizRow:
    row: dict[str, Any]
    st: dict[str, Any]

    @property
    def id(self) -> str:
        return self.row["id"]

    @property
    def kind(self) -> str:
        return self.st["kind"]

    @property
    def completed(self) -> bool:
        return self.row["status"] == "completed"

    @property
    def finished(self) -> bool:
        """Completed with a result (not closed as abandoned, e.g. by a maintenance script or a retake left at its
        intro)."""
        return self.completed and self.row["outcome"] != "abandoned"


def _quiz_rows(course_id: str) -> list[_QuizRow]:
    rows = db.query("SELECT * FROM tasks WHERE course_id = ? AND type = 'quiz' ORDER BY created_at, rowid", (course_id,))
    return [_QuizRow(r, load_state(r)) for r in rows]


def _last_regular(rows: list[_QuizRow]) -> _QuizRow | None:
    done = [r for r in rows if r.kind == "quiz" and r.finished]
    return done[-1] if done else None


def _quizzed(rows: list[_QuizRow]) -> set[str]:
    """Skills a finished regular quiz has validated (a voided question validated nothing)."""
    out: set[str] = set()
    for r in rows:
        if r.kind == "quiz" and r.finished:
            voided = set(r.st["voided"])
            out.update(sid for qid, sid in zip(r.st["set"], r.st["skills"]) if qid not in voided)
    return out


def _last_score(rows: list[_QuizRow]) -> float | None:
    done = [r for r in rows if r.finished and isinstance(r.st["result"].get("score"), (int, float))]
    return float(done[-1].st["result"]["score"]) if done else None


def _live(st: dict[str, Any]) -> list[str]:
    voided = set(st["voided"])
    return [qid for qid in st["set"] if qid not in voided]


# ---------------------------------------------------------------------------
# Due computation (stats + selector)
# ---------------------------------------------------------------------------


def _learned(course_id: str) -> list[dict[str, Any]]:
    """Learned skills with their card and how many quiz-able questions they have."""
    marks = ",".join("?" * len(QUIZ_KINDS))
    return db.query(
        f"SELECT {_SKILL_COLUMNS}, p.lesson_passed_at, p.card_json,"
        f" (SELECT COUNT(*) FROM questions q WHERE q.skill_id = s.id AND q.flagged = 0 AND q.kind IN ({marks})"
        "   AND COALESCE(q.role, 'practice') != 'capstone') AS quiz_questions"
        " FROM progress p JOIN skills s ON s.id = p.skill_id"
        " WHERE p.course_id = ? AND p.status IN ('learned','mastered') AND p.card_json IS NOT NULL"
        " ORDER BY s.order_index, s.key",
        (*QUIZ_KINDS, course_id),
    )


def _passed_since(row: dict[str, Any], since: str | None) -> bool:
    passed = row.get("lesson_passed_at")
    return bool(passed) and (since is None or passed > since)


@dataclass
class QuizStatus:
    due: bool  # a regular quiz is active, or one must be taken next
    active: dict[str, Any] | None  # the active quiz task row (quiz or retake)
    xp_since: int
    skills_since: int
    eligible_skills: int
    xp_until: int | None
    last_score: float | None
    since: str | None  # when the last regular quiz finished


def quiz_status(course_id: str, now: datetime) -> QuizStatus:
    rows = _quiz_rows(course_id)
    active = next((r for r in rows if not r.completed), None)
    last = _last_regular(rows)
    since = last.row["completed_at"] if last else None
    sql = (
        "SELECT COALESCE(SUM(x.xp), 0) AS n FROM xp_log x LEFT JOIN tasks t ON t.id = x.task_id"
        " WHERE x.course_id = ? AND COALESCE(t.type, '') != 'quiz'"
    )
    if since:
        sql, params = sql + " AND x.created_at > ?", (course_id, since)
    else:
        params = (course_id,)
    xp_since = int(db.query_one(sql, params)["n"])
    learned = _learned(course_id)
    skills_since = sum(1 for r in learned if _passed_since(r, since))
    eligible = sum(1 for r in learned if r["quiz_questions"])
    enough = skills_since >= QUIZ_MIN_NEW_SKILLS and eligible >= QUIZ_MIN_QUESTIONS
    regular_active = active is not None and active.kind == "quiz"
    due = regular_active or (enough and xp_since >= QUIZ_XP_INTERVAL)
    xp_until = 0 if due else (max(0, QUIZ_XP_INTERVAL - xp_since) if enough else None)
    return QuizStatus(
        due=due, active=active.row if active else None, xp_since=xp_since, skills_since=skills_since,
        eligible_skills=eligible, xp_until=xp_until, last_score=_last_score(rows), since=since,
    )  # fmt: skip


# ---------------------------------------------------------------------------
# Question selection
# ---------------------------------------------------------------------------


def _quiz_questions(skill_id: str) -> list[dict[str, Any]]:
    """The skill's quiz-able questions in pool order."""
    return [
        q for q in engine._questions(skill_id) if q["kind"] in QUIZ_KINDS and not q["flagged"] and not engine._is_capstone(q)
    ]


def _seen(course_id: str, rows: list[_QuizRow]) -> set[str]:
    """Questions the learner has seen: answered anywhere, or shown in a quiz (a finished quiz shows all of them)."""
    seen = {
        r["question_id"]
        for r in db.query(
            "SELECT DISTINCT a.question_id FROM attempts a JOIN skills s ON s.id = a.skill_id"
            " WHERE s.course_id = ? AND a.status != 'voided'",
            (course_id,),
        )
    }
    for r in rows:
        seen.update(r.st["set"] if r.finished else r.st["served"])
    return seen


def tier_rank(tier: Any, last_score: float | None) -> int:
    """Lower is preferred: default tier >= 2; after a quiz >= 90 % tier 3 first; after one < 70 % tiers 1-2 first."""
    try:
        t = max(1, min(3, int(tier)))
    except (TypeError, ValueError):
        t = 1
    if last_score is not None and last_score >= HARDER_FROM - EPS:
        return {3: 0, 2: 1, 1: 2}[t]
    if last_score is not None and last_score < EASIER_BELOW - EPS:
        return {1: 0, 2: 0, 3: 1}[t]
    return {2: 0, 3: 0, 1: 1}[t]


def _pick(
    skill_id: str, seen: set[str], last_score: float | None, avoid: frozenset[str] | set[str] = frozenset()
) -> dict[str, Any] | None:
    """Not avoided (a retake avoids the quiz's own questions), unseen, preferred tier, pool order."""
    pool = _quiz_questions(skill_id)
    if not pool:
        return None
    ranked = sorted(
        enumerate(pool), key=lambda iq: (iq[1]["id"] in avoid, iq[1]["id"] in seen, tier_rank(iq[1]["tier"], last_score), iq[0])
    )
    return ranked[0][1]


@dataclass
class QuizPlan:
    kind: str  # quiz | retake
    of: str | None  # retake: the quiz task it retakes
    skills: list[dict[str, Any]]  # skill rows, in question order
    questions: list[dict[str, Any]]  # question rows, parallel to skills
    seen: set[str]
    of_date: str = ""  # retake: the day of the quiz it retakes
    of_score: float | None = None

    def time_limit(self, factor: float | None = None) -> int:
        return time_limit_seconds([q["kind"] for q in self.questions], factor)

    def minutes(self) -> int:
        return self.time_limit() // 60


def _order(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return sorted(pairs, key=lambda p: (p[0]["order_index"], p[0]["key"]))


def build_plan(course_id: str, now: datetime, rows: list[_QuizRow] | None = None) -> QuizPlan | None:
    """The regular quiz that would start now (no side effects); None when fewer than QUIZ_MIN_QUESTIONS learned
    skills have a quiz-able question."""
    rows = _quiz_rows(course_id) if rows is None else rows
    learned = [r for r in _learned(course_id) if r["quiz_questions"]]
    if len(learned) < QUIZ_MIN_QUESTIONS:
        return None

    def recall(r: dict[str, Any]) -> tuple[float, int, str]:
        value = scheduler.retrievability(scheduler.load_card(r["card_json"]), now)
        return (value if value is not None else 0.0, r["order_index"], r["key"])

    def learned_at(r: dict[str, Any]) -> tuple[str, int, str]:
        return (r["lesson_passed_at"] or "", r["order_index"], r["key"])

    # Not quizzed yet, earliest learned first: a skill left out because more than QUIZ_MAX_QUESTIONS were new leads
    # the next quiz instead of competing as an "old" skill (where the newest skills, with the highest recall, lose).
    quizzed = _quizzed(rows)
    new = sorted((r for r in learned if r["id"] not in quizzed), key=learned_at)
    old = sorted((r for r in learned if r["id"] in quizzed), key=recall)
    n = min(QUIZ_MAX_QUESTIONS, max(QUIZ_TARGET_QUESTIONS, len(new)), len(learned))
    seen = _seen(course_id, rows)
    last_score = _last_score(rows)
    pairs = []
    for skill in (new + old)[:n]:
        q = _pick(skill["id"], seen, last_score)
        if q is not None:
            pairs.append((skill, q))
    pairs = _order(pairs)
    return QuizPlan("quiz", None, [s for s, _ in pairs], [q for _, q in pairs], seen)


def _retake_plan(course_id: str, quiz: _QuizRow, rows: list[_QuizRow]) -> QuizPlan | None:
    """Same skills as the quiz (voided questions aside), fresh questions: none of the quiz's own first, then unseen."""
    avoid = set(quiz.st["set"])
    for r in rows:
        if r.kind == "retake" and r.st["of"] == quiz.id:
            avoid.update(r.st["set"])
    seen = _seen(course_id, rows)
    score = quiz.st["result"].get("score")
    voided = set(quiz.st["voided"])
    skills = {s["id"]: s for s in db.query(f"SELECT {_SKILL_COLUMNS} FROM skills s WHERE s.course_id = ?", (course_id,))}
    pairs = []
    for qid, sid in zip(quiz.st["set"], quiz.st["skills"]):
        if qid in voided or sid not in skills:
            continue
        q = _pick(sid, seen, score, avoid)
        if q is not None:
            pairs.append((skills[sid], q))
    if not pairs:
        return None
    pairs = _order(pairs)
    return QuizPlan(
        "retake", quiz.id, [s for s, _ in pairs], [q for _, q in pairs], seen,
        of_date=quiz_day(db.parse_iso(quiz.row["completed_at"])), of_score=score,
    )  # fmt: skip


def plan_reason(plan: QuizPlan, *, first: bool = False) -> str:
    size = _size_text(len(plan.questions), plan.minutes())
    if plan.kind == "retake":
        pct = engine.score_percent(plan.of_score or 0.0)
        return f"Optional retake of the quiz on {plan.of_date} ({pct}%): {size} — fresh questions on the same skills"
    since = "since you started" if first else "since your last quiz"
    return f"Quiz — {QUIZ_XP_INTERVAL} XP {since}: {size}"


# ---------------------------------------------------------------------------
# Retakes
# ---------------------------------------------------------------------------


def _missed_reviews_done(quiz: _QuizRow) -> bool:
    """Every skill missed in the quiz has had a completed review since (a skill with no review questions left
    cannot be reviewed and does not hold the retake back)."""
    finished = quiz.row["completed_at"] or ""
    for sid in quiz.st["result"].get("missed") or []:
        done = db.query_one(
            "SELECT 1 FROM tasks WHERE skill_id = ? AND type = 'review' AND status = 'completed'"
            " AND outcome IN ('passed','failed') AND completed_at >= ? LIMIT 1",
            (sid, finished),
        )
        if not done and engine._pool(sid).practice():
            return False
    return True


def _retake_open(quiz: _QuizRow, rows: list[_QuizRow]) -> bool:
    if quiz.kind != "quiz" or not quiz.completed:
        return False
    score = quiz.st["result"].get("score")
    if not isinstance(score, (int, float)) or score >= PASS_MARK - EPS:
        return False
    position = next((i for i, r in enumerate(rows) if r.id == quiz.id), None)
    if position is None or any(r.kind == "quiz" for r in rows[position + 1 :]):
        return False  # only the latest quiz can be retaken
    if any(r.kind == "retake" and r.st["of"] == quiz.id and r.row["outcome"] != "abandoned" for r in rows):
        return False  # one retake per quiz (one left at its intro, before the clock started, doesn't count)
    return _missed_reviews_done(quiz)


def retake_offer(course_id: str, now: datetime, rows: list[_QuizRow] | None = None) -> QuizPlan | None:
    """The optional retake the menu offers now, if any (no side effects)."""
    rows = _quiz_rows(course_id) if rows is None else rows
    last = _last_regular(rows)
    if last is None or not _retake_open(last, rows):
        return None
    return _retake_plan(course_id, last, rows)


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


def prepare(course_id: str, now: datetime, kind: str = "quiz") -> QuizPlan:
    """The plan of the quiz (or retake) to start now; Conflict when there is none (no side effects)."""
    rows = _quiz_rows(course_id)
    if kind == "retake":
        plan = retake_offer(course_id, now, rows)
        if plan is None:
            raise engine.Conflict("No quiz retake is available right now.")
        return plan
    plan = build_plan(course_id, now, rows)
    if plan is None:
        raise engine.Conflict("There aren't enough learned skills with quiz questions for a quiz yet.")
    return plan


def start(course_id: str, now: datetime, plan: QuizPlan | None = None, *, kind: str = "quiz") -> str:
    """Insert the quiz task (phase learn: intro, clock not started). Returns its id."""
    with engine.lock:
        plan = plan or prepare(course_id, now, kind)
        first = plan.kind == "quiz" and _last_regular(_quiz_rows(course_id)) is None
        st = dict(_STATE_DEFAULTS, kind=plan.kind, of=plan.of, set=[q["id"] for q in plan.questions],
                  skills=[s["id"] for s in plan.skills], served=[], log=[], voided=[], result={})  # fmt: skip
        task_id = engine._insert_task(course_id, plan.skills[0]["id"], "quiz", "learn", plan_reason(plan, first=first), st, now)
        for skill, q in zip(plan.skills, plan.questions):
            unseen = sum(1 for other in _quiz_questions(skill["id"]) if other["id"] != q["id"] and other["id"] not in plan.seen)
            if unseen < TOPUP_BELOW_UNSEEN:
                engine.request_top_up(skill["id"])
        keys = ", ".join(s["key"] for s in plan.skills)
        log.info("started %s %s: %d questions (%s)", plan.kind, task_id, len(plan.questions), keys)
        return task_id


def take_first_message(course_id: str, now: datetime, status: QuizStatus | None = None) -> str:
    """Why nothing else can start while a quiz is due (409 on a manual start)."""
    status = status or quiz_status(course_id, now)
    active = status.active
    if active is not None and active["type"] == "quiz" and load_state(active)["kind"] == "quiz":
        st = load_state(active)
        deadline = db.parse_iso(st["deadline"])
        if deadline is not None:
            left = max(1, math.ceil(max(0.0, (deadline - scheduler.utc(now)).total_seconds()) / 60))
            return f"Finish the quiz first — about {_minutes_text(left)} left."
        rows = {q["id"]: q for q in db.query(_in_sql(st["set"]), tuple(st["set"]))}
        minutes = time_limit_seconds([rows[q]["kind"] for q in _live(st) if q in rows]) // 60
        return f"Take the quiz first — about {_minutes_text(minutes)}."
    plan = build_plan(course_id, now)
    return f"Take the quiz first — about {_minutes_text(plan.minutes() if plan else 1)}."


def _in_sql(ids: list[str]) -> str:
    return f"SELECT * FROM questions WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})"


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------


def _deadline(st: dict[str, Any]) -> datetime | None:
    return db.parse_iso(st.get("deadline"))


def _expired(st: dict[str, Any], now: datetime) -> bool:
    deadline = _deadline(st)
    return deadline is not None and scheduler.utc(now) > deadline + DEADLINE_GRACE


REFERENCE_CLOSED = "Lessons and book pages are closed while the quiz is open."


def blocks_reference(course_id: str, now: datetime) -> bool:
    """SPEC §6.7: no lesson or source view during a quiz — a regular quiz from its intro on, a retake once its clock
    runs (a retake left at its intro is optional and blocks nothing). An expired quiz no longer blocks."""
    for task in db.query("SELECT * FROM tasks WHERE course_id = ? AND type = 'quiz' AND status = 'active'", (course_id,)):
        st = load_state(task)
        if _expired(st, now):
            continue
        if st["kind"] == "quiz" or st.get("started_at"):
            return True
    return False


def expire(task: dict[str, Any], now: datetime) -> bool:
    """Finish an active quiz whose time is up. True when this call finished it."""
    if task["type"] != "quiz" or task["status"] != "active" or task["phase"] != "practice":
        return False
    with engine.lock:
        task = engine._task_row(task["id"])
        if task["status"] != "active":
            return False
        st = load_state(task)
        if not _expired(st, now):
            return False
        _finish(task, st, now, timed_out=True)
        return True


def expire_active(course_id: str, now: datetime) -> bool:
    """Finish the course's active quiz if its time is up (before anything reads due reviews or quiz_due)."""
    active = engine.active_task_row(course_id)
    return active is not None and expire(active, now)


def _serve_from(st: dict[str, Any], start_index: int, now: datetime) -> bool:
    """Show the first servable question at or after start_index (a question flagged or removed meanwhile is voided)."""
    rows = {q["id"]: q for q in db.query(_in_sql(st["set"][start_index:]), tuple(st["set"][start_index:]))}
    for i in range(max(0, start_index), len(st["set"])):
        qid = st["set"][i]
        if qid in st["voided"]:
            continue
        row = rows.get(qid)
        if row is None or row["flagged"]:
            st["voided"].append(qid)
            continue
        st["pos"], st["current"], st["served_at"] = i, qid, scheduler.iso(now)
        if qid not in st["served"]:
            st["served"].append(qid)
        return True
    st["pos"], st["current"], st["served_at"] = len(st["set"]), None, None
    return False


def begin(task_id: str, now: datetime) -> Task:
    """POST /practice: leave the intro and start the clock (later calls only check it)."""
    with engine.lock:
        task = engine._task_row(task_id)
        if task["status"] != "active":
            raise engine.Conflict("This quiz is already finished.")
        if task["phase"] == "learn":
            st = load_state(task)
            rows = {q["id"]: q for q in db.query(_in_sql(st["set"]), tuple(st["set"]))}
            limit = time_limit_seconds([rows[q]["kind"] for q in _live(st) if q in rows])
            deadline = scheduler.utc(now) + timedelta(seconds=limit)
            st.update(time_limit=limit, started_at=scheduler.iso(now), deadline=scheduler.iso(deadline))
            reason = _reason_with_size(task["reason"], len(_live(st)), limit // 60)  # frozen with the clock
            served = _serve_from(st, 0, now)
            engine._save_state(task_id, st, phase="practice", reason=reason)
            task = engine._task_row(task_id)
            if not served:  # every question was removed before the start
                _finish(task, st, now, timed_out=False)
            log.info("quiz %s: clock started (%d s)", task_id, limit)
        else:
            expire(task, now)
        return build_task(engine._task_row(task_id), now)


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def _check_open(task: dict[str, Any], st: dict[str, Any], now: datetime) -> None:
    if task["status"] != "active":
        raise engine.Conflict("Time is up." if st["result"].get("timed_out") else "This quiz is already finished.")
    if task["phase"] != "practice":
        raise engine.Conflict("Start the quiz first.")
    if _expired(st, now):
        _finish(task, st, now, timed_out=True)
        raise engine.Conflict("Time is up.")


def _check_current(st: dict[str, Any], sub: AnswerSubmit) -> None:
    if sub.question_id != st.get("current"):
        raise engine.Conflict("That question is not the current one — reload the quiz.")


def _grade(q: dict[str, Any], sub: AnswerSubmit) -> float:
    """Points of a quiz answer, 0–1 (WITHOUT holding the lock): 1 right, a true_false_set's share of right statements,
    else 0. Unreadable input is a 422 (asked again, like in lessons); a right value that is not simplified scores 0 —
    the "simplify" message would give the answer away."""
    if engine._submitted_nothing(q["kind"], sub):
        raise engine.Invalid(engine._nothing_submitted_message(q["kind"], q["answer"]))

    def check(simplified: bool) -> grading.GradeResult:
        return grading.check_answer(
            q["kind"], answer=q["answer"], correct_choice=q["correct_choice"], tolerance=q["tolerance"],
            response=sub.response, choice=sub.choice, require_simplified=simplified,
        )  # fmt: skip

    result = check(True)
    if not result.invalid:
        return 1.0 if result.correct else max(0.0, min(1.0, result.credit or 0.0))
    if q["kind"] in ("numeric", "expression"):
        loose = check(False)
        if not loose.invalid and loose.correct:
            return 0.0
    raise engine.Invalid(result.feedback)


def _raw_response(q: dict[str, Any], sub: AnswerSubmit) -> str:
    if engine._is_choice(q):
        return "" if sub.choice is None else str(sub.choice)
    return (sub.response or "").strip()[:1000]


def _seconds(st: dict[str, Any], now: datetime) -> float:
    served = db.parse_iso(st.get("served_at"))
    if served is None:
        return 0.0
    spent = (scheduler.utc(now) - served).total_seconds()
    return round(max(0.0, min(spent, float(st.get("time_limit") or spent))), 1)


def _record_attempt(task: dict[str, Any], q: dict[str, Any], correct: bool, response: str, seconds: float, now: datetime) -> None:
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, independent, response, hints_used,"
        " revealed, seconds, created_at) VALUES (?,?,?,?,?,?,?,?,0,0,?,?)",
        (db.new_id(), task["id"], q["id"], q["skill_id"], int(correct), "correct" if correct else "incorrect", int(correct),
         response, seconds, scheduler.iso(now)),
    )  # fmt: skip


def submit_answer(task_id: str, sub: AnswerSubmit, now: datetime) -> AnswerResult:
    with engine.lock:
        task = engine._task_row(task_id)
        st = load_state(task)
        _check_open(task, st, now)
        _check_current(st, sub)
        q = engine._question_row(sub.question_id)
    pts = 0.0 if sub.skip else _grade(q, sub)  # SymPy runs outside the lock
    correct = pts >= 1 - EPS
    with engine.lock:
        task = engine._task_row(task_id)
        st = load_state(task)
        _check_open(task, st, now)  # it may have changed while grading (another tab, a flag, the clock …)
        _check_current(st, sub)
        seconds = _seconds(st, now)
        response = "" if sub.skip else _raw_response(q, sub)
        st["log"].append({
            "q": q["id"], "skill": q["skill_id"], "pts": pts, "answered": not sub.skip,
            "skipped": bool(sub.skip), "response": response, "seconds": seconds,
        })  # fmt: skip
        _record_attempt(task, q, correct, response, seconds, now)
        finished = not _serve_from(st, st["pos"] + 1, now)
        amount, text = 0, None
        if finished:
            amount, text = _finish(task, st, now, timed_out=False)
        else:
            engine._save_state(task_id, st)
        feedback = "Skipped — no point for this one." if sub.skip else "Answer saved."
        result = st["result"]
        note = ""
        if finished:
            note = f"Quiz finished: {result['correct']} of {result['total']} right ({engine.score_percent(result['score'])}%)."
        return AnswerResult(
            correct=False, status="recorded", independent=False, feedback=f"{feedback} {note}".strip(), correct_answer="",
            correct_choice=None, solution="", task=build_task(engine._task_row(task_id), now), xp_awarded=amount,
            suggestion=None, suggestion_text=text, score=0.0, question_done=True, note=note,
        )  # fmt: skip


# ---------------------------------------------------------------------------
# Finishing
# ---------------------------------------------------------------------------


def _finish(task: dict[str, Any], st: dict[str, Any], now: datetime, *, timed_out: bool) -> tuple[int, str | None]:
    """Score the quiz, award XP, apply its effects (remedial reviews, FSRS credit) and complete the task.
    A timed-out quiz finishes at its deadline. Returns (XP, the message about what comes next)."""
    deadline = _deadline(st)
    end = min(scheduler.utc(now), deadline) if timed_out and deadline is not None else scheduler.utc(now)
    rows = {q["id"]: q for q in db.query(_in_sql(st["set"]), tuple(st["set"]))}
    logged = {e["q"] for e in st["log"]}
    current = st.get("current")
    if current and current not in logged and current not in st["voided"] and current in rows:
        # shown when the time ran out: recorded as seen and unanswered
        seconds = _seconds(st, end)
        _record_attempt(task, rows[current], False, "", seconds, end)
        st["log"].append({"q": current, "skill": rows[current]["skill_id"], "pts": 0.0, "answered": False,
                          "skipped": False, "response": "", "seconds": seconds})  # fmt: skip
        logged.add(current)
    for qid, sid in zip(st["set"], st["skills"]):
        if qid in logged or qid in st["voided"]:
            continue
        if qid not in rows or rows[qid]["flagged"]:  # removed before it was shown: it would have been skipped
            st["voided"].append(qid)
        else:
            # never reached: the results show its answer and solution, so it is recorded as seen (unanswered) —
            # lessons and the remedial review then prefer other questions
            _record_attempt(task, rows[qid], False, "", 0.0, end)
            st["log"].append({"q": qid, "skill": sid, "pts": 0.0, "answered": False, "skipped": False, "response": "",
                              "seconds": 0.0})  # fmt: skip
    order = {qid: i for i, qid in enumerate(st["set"])}
    st["log"].sort(key=lambda e: order.get(e["q"], len(order)))
    st["served"] = list(dict.fromkeys(st["served"] + st["set"]))  # the results show every question
    live = [e for e in st["log"] if e["q"] not in st["voided"]]
    correct = sum(1 for e in live if e["pts"] >= 1 - EPS)
    score = sum(e["pts"] for e in live) / len(live) if live else 0.0  # a true_false_set's right statements count
    minutes = int(st.get("time_limit") or 0) // 60
    amount = xp.quiz_xp(minutes, score)
    missed, credited, effects = _apply_effects(task, live, end)
    st["result"] = {
        "score": score, "correct": correct, "total": len(live), "xp": amount, "missed": missed, "credited": credited,
        "timed_out": timed_out, "finished_at": scheduler.iso(end), "effects": effects,
    }  # fmt: skip
    st["current"], st["served_at"] = None, None
    engine._complete(task, st, "passed" if score >= PASS_MARK - EPS else "failed", amount, end)
    xp.award(task["course_id"], task["id"], amount, end)
    skill_state.recompute_unlocks(task["course_id"])
    log.info(
        "%s %s finished%s: %d/%d (%.2f), %d XP, missed %s, FSRS credit %s", st["kind"], task["id"],
        " (time up)" if timed_out else "", correct, len(live), score, amount, missed, credited,
    )  # fmt: skip
    return amount, _next_steps_text(st, missed, score)


def _progress_snapshot(skill_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT card_json, status, reviews FROM progress WHERE skill_id = ?", (skill_id,))


def _apply_effects(
    task: dict[str, Any], live: list[dict[str, Any]], end: datetime
) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
    """Missed skills -> remedial review due now; a right answer on a skill due within CREDIT_WINDOW -> FSRS Good
    (it also settles a remedial mark an earlier quiz left on the skill). Returns (missed, credited, effects), where
    effects[skill] records what the quiz changed so that a question flagged later can be undone (_undo_effects)."""
    missed: list[str] = []
    credited: list[str] = []
    effects: dict[str, dict[str, Any]] = {}
    for e in live:
        sid = e["skill"]
        before = _progress_snapshot(sid)
        if before is None:
            continue
        mark = scheduler.remedial_info(sid)
        earlier = {k: mark[k] for k in ("quiz", "quiz_at") if k in mark} if mark.get("quiz") else None
        effect: dict[str, Any] = {}
        if e["pts"] < 1 - EPS:
            if not scheduler.mark_quiz_remedial(sid, task["id"], end):
                continue
            missed.append(sid)
            after = _progress_snapshot(sid)
            if after is not None and after["card_json"] != before["card_json"]:
                effect = {"before": dict(before), "after_card": after["card_json"]}
        else:
            card = scheduler.load_card(before["card_json"])
            if card is None or scheduler.utc(card.due) > end + CREDIT_WINDOW:
                continue  # only recorded
            _, log_id = scheduler.apply_rating(task["course_id"], sid, Rating.Good, end)
            db.execute("UPDATE progress SET reviews = reviews + 1, updated_at = ? WHERE skill_id = ?", (scheduler.iso(end), sid))
            if earlier:  # the right answer is the review that earlier quiz's mark was waiting for
                scheduler.clear_quiz_remedial(sid, earlier["quiz"])
            credited.append(sid)
            after = _progress_snapshot(sid)
            effect = {"before": dict(before), "after_card": after["card_json"] if after else None, "review_log_id": log_id}
        if earlier:
            effect["mark"] = earlier  # replaced (missed again) or settled (credited) by this quiz
        if effect:
            effects[sid] = effect
    return missed, credited, effects


def _undo_effects(task: dict[str, Any], result: dict[str, Any], skill_id: str) -> None:
    """A skill whose only question was voided after the end: it is no longer missed or credited. Its card (and the
    explicit review_log row of a credit) goes back to how it was before the quiz — unless the card has changed since
    (e.g. the remedial review already ran), which is then left alone, as in engine._rescore_review. A remedial mark
    of an earlier quiz that this quiz replaced or settled comes back while that review is still owed."""
    cleared = False
    if skill_id in (result.get("missed") or []):
        result["missed"].remove(skill_id)
        cleared = scheduler.remedial_info(skill_id).get("quiz") == task["id"]
        scheduler.clear_quiz_remedial(skill_id, task["id"])
    if skill_id in (result.get("credited") or []):
        result["credited"].remove(skill_id)
    effects = result.get("effects")
    effect = effects.pop(skill_id, None) if isinstance(effects, dict) else None
    if not isinstance(effect, dict):
        return  # nothing recorded (no change, or a quiz finished before effects were kept)
    restored = False
    before = effect.get("before")
    if isinstance(before, dict) and effect.get("after_card"):
        now_row = _progress_snapshot(skill_id)
        if now_row is not None and now_row["card_json"] == effect["after_card"]:
            db.execute(
                "UPDATE progress SET card_json = ?, status = ?, reviews = ? WHERE skill_id = ?",
                (before["card_json"], before["status"], before["reviews"], skill_id),
            )
            if effect.get("review_log_id"):
                db.execute("DELETE FROM review_log WHERE id = ?", (effect["review_log_id"],))
            restored = True
            log.info("quiz %s: effect on skill %s undone after a flag", task["id"], skill_id)
    earlier = effect.get("mark")
    if isinstance(earlier, dict) and earlier.get("quiz") and (cleared or restored):
        if not scheduler.remedial_info(skill_id).get("quiz"):
            scheduler.set_quiz_remedial_mark(skill_id, earlier["quiz"], earlier.get("quiz_at"))


def _next_steps_text(st: dict[str, Any], missed: list[str], score: float) -> str | None:
    if not missed:
        return None
    marks = ",".join("?" * len(missed))
    keys = [r["key"] for r in db.query(f"SELECT key FROM skills WHERE id IN ({marks}) ORDER BY order_index", tuple(missed))]
    names = keys[0] if len(keys) == 1 else ", ".join(keys[:-1]) + " and " + keys[-1]
    due = "is due as a review now" if len(keys) == 1 else "are due as reviews now"
    text = f"{names} {due} — they come first."
    if st["kind"] == "quiz" and score < PASS_MARK - EPS:
        text += " After those reviews you can retake the quiz with fresh questions (optional)."
    return text


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def void_question(task: dict[str, Any], question: dict[str, Any], now: datetime) -> None:
    """A flagged quiz question leaves the quiz (no replacement). During the quiz the next question is served; a
    finished quiz is re-scored without it (XP only ever topped up) and its skill is no longer counted as missed."""
    with engine.lock:
        task = engine._task_row(task["id"])
        st = load_state(task)
        qid = question["id"]
        if qid not in st["set"]:
            return
        if qid not in st["voided"]:
            st["voided"].append(qid)
        if task["status"] == "active":
            if task["phase"] == "practice" and _expired(st, now):
                _finish(task, st, now, timed_out=True)
            elif st.get("current") == qid and task["phase"] == "practice":
                if not _serve_from(st, st["pos"] + 1, now):
                    _finish(task, st, now, timed_out=False)
                else:
                    engine._save_state(task["id"], st)
            else:
                engine._save_state(task["id"], st)
            log.info("quiz %s: question %s voided by a flag", task["id"], question["key"])
            return
        _rescore(task, st, question["skill_id"], now)


def _rescore(task: dict[str, Any], st: dict[str, Any], skill_id: str, now: datetime) -> None:
    result = st["result"]
    live = [e for e in st["log"] if e["q"] not in st["voided"]]
    correct = sum(1 for e in live if e["pts"] >= 1 - EPS)
    score = sum(e["pts"] for e in live) / len(live) if live else 0.0  # a true_false_set's right statements count
    if not any(e["skill"] == skill_id for e in live):
        _undo_effects(task, result, skill_id)
    amount =max(int(task["xp_awarded"] or 0), xp.quiz_xp(int(st.get("time_limit") or 0) // 60, score))
    if amount > (task["xp_awarded"] or 0):
        xp.award(task["course_id"], task["id"], amount - int(task["xp_awarded"] or 0), now)
    result.update(score=score, correct=correct, total=len(live), xp=amount)
    outcome = "passed" if score >= PASS_MARK - EPS else "failed"
    engine._save_state(task["id"], st, xp_awarded=amount, outcome=outcome)
    log.info("quiz %s re-scored after a flag: %.2f, %d XP", task["id"], score, amount)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _question_public(row: dict[str, Any]) -> QuestionPublic:
    """The current quiz question: no hints, checklist or book reference."""
    question = views.question_public(row)
    question.hints = []
    question.hint_count = 0
    question.checklist = []
    question.source_exercise = None
    question.hints_used = 0
    question.revealed = False
    return question


def _response_display(q: dict[str, Any], raw: str) -> str:
    if engine._is_choice(q):
        choices = engine._safe_loads(q["choices_json"], [])
        index = engine._int_or_none(raw)
        return str(choices[index]) if index is not None and 0 <= index < len(choices) else raw
    if q["kind"] == "true_false_set":
        marks = grading.parse_true_false_set(raw)
        return grading.true_false_set_display(marks) if marks else raw
    return raw


def _results(st: dict[str, Any], qrows: dict[str, dict], skills: dict[str, SkillRef]) -> list[QuizQuestionResult]:
    out = []
    for e in st["log"]:
        q = qrows.get(e["q"])
        if q is None or e["q"] in st["voided"] or e["skill"] not in skills:
            continue
        answered = bool(e.get("answered"))
        out.append(
            QuizQuestionResult(
                question_id=q["id"], skill=skills[e["skill"]], kind=q["kind"], prompt=q["prompt"],
                choices=engine._safe_loads(q["choices_json"], []),
                response=_response_display(q, e.get("response") or "") if answered else None, answered=answered,
                score=float(e["pts"]), correct_answer=engine._answer_display(q), solution=q["solution"] or "",
                seconds=float(e.get("seconds") or 0.0),
            )
        )  # fmt: skip
    return out


def build_task(task: dict[str, Any], now: datetime | None = None) -> Task:
    now = scheduler.utc(now or datetime.now(timezone.utc))
    st = load_state(task)
    active = task["status"] == "active"
    finished = task["status"] == "completed"
    qrows = {q["id"]: q for q in db.query(_in_sql(st["set"]), tuple(st["set"]))}
    marks = ",".join("?" * len(st["skills"])) or "NULL"
    srows = {s["id"]: s for s in db.query(f"SELECT {_SKILL_COLUMNS} FROM skills s WHERE s.id IN ({marks})", tuple(st["skills"]))}
    refs = {sid: views.skill_ref(row) for sid, row in srows.items()}
    live = _live(st)
    voided = set(st["voided"])
    skills = list(dict.fromkeys(sid for qid, sid in zip(st["set"], st["skills"]) if qid not in voided and sid in refs))
    limit = int(st["time_limit"] or time_limit_seconds([qrows[q]["kind"] for q in live if q in qrows]))
    reason = task["reason"]
    if active and not st["time_limit"]:  # the intro: the limit (and so the reason) follows quiz_time_factor
        reason = _reason_with_size(reason, len(live), limit // 60)
    deadline = _deadline(st)
    seconds_left = max(0, math.ceil((deadline - now).total_seconds())) if active and deadline is not None else None
    question = None
    if active and task["phase"] == "practice" and st.get("current") in qrows:
        question = _question_public(qrows[st["current"]])
    entries = [e for e in st["log"] if e["q"] not in voided]
    answered = sum(1 for e in entries if e.get("answered") or e.get("skipped"))
    correct = sum(1 for e in entries if e["pts"] >= 1 - EPS) if finished else 0
    points = float(sum(e["pts"] for e in entries)) if finished else 0.0  # a true_false_set's right statements count
    max_points = float(len(live)) if finished else 0.0
    rows = _quiz_rows(task["course_id"]) if finished and st["kind"] == "quiz" else []
    quiz = QuizInfo(
        kind=st["kind"], question_count=len(live), time_limit_seconds=limit, started_at=st["started_at"],
        deadline=st["deadline"], seconds_left=seconds_left, skills=[refs[s] for s in skills],
        results=_results(st, qrows, refs) if finished else [],
        missed_skills=[refs[s] for s in st["result"].get("missed") or [] if s in refs] if finished else [],
        retake_available=bool(rows) and _retake_open(next(r for r in rows if r.id == task["id"]), rows),
    )  # fmt: skip
    progress = TaskProgress(
        kp_index=0, kp_total=1, streak=0, needed_in_row=0,
        kp_questions_used=min(len(live), answered + (1 if question else 0)), kp_questions_available=len(live),
        answered=answered, correct=correct, total_questions=len(live), points=points, max_points=max_points,
        score=engine._ratio(points, max_points), planned=len(live), pass_mark=PASS_MARK,
    )  # fmt: skip
    return Task(
        id=task["id"], course_id=task["course_id"], type="quiz", phase=task["phase"], skill=None, reason=reason,
        xp_value=int(task["xp_awarded"]) if finished else limit // 60, lesson=None, question=question,
        progress=progress, outcome=task["outcome"], cooldown_until=None, quiz=quiz,
    )  # fmt: skip
