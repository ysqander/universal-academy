"""Post-validation of generated questions and their storage in the `questions` table."""

from __future__ import annotations

import logging
import re
import sqlite3

from .. import db
from ..learning.grading import check_answer
from ..llm_schemas import QuestionDraft

log = logging.getLogger("ua.pipeline.questions")

MACHINE_CHECKABLE = {"multiple_choice", "true_false", "numeric", "expression"}
TRUE_FALSE = ["True", "False"]
MAX_CHOICES = 6
MAX_CHECKLIST = 5
MAX_MC_SHARE = 0.4  # at most ~40% of a lesson's questions may be multiple choice
LENGTH_CUE_RATIO = 1.4  # the correct option is this much longer than every distractor …
LENGTH_CUE_CHARS = 25  # … and at least this many characters longer: answerable without solving
PENDING_REASON = "verification: pending"


class InvalidQuestion(ValueError):
    pass


def is_machine_checkable(kind: str) -> bool:
    return kind in MACHINE_CHECKABLE


def clean_question(q: QuestionDraft) -> QuestionDraft:
    """Return a normalized copy or raise InvalidQuestion with the reason."""
    if not q.prompt.strip():
        raise InvalidQuestion("empty prompt")
    update: dict = {"tier": min(3, max(1, int(q.tier))), "hints": [h for h in q.hints if h.strip()][:3]}
    if q.kind == "multiple_choice":
        choices = [c for c in q.choices if c.strip()]
        if not 2 <= len(choices) <= MAX_CHOICES or len(choices) != len(q.choices):
            raise InvalidQuestion(f"multiple choice needs 2-{MAX_CHOICES} non-empty choices")
        if len({_norm(c) for c in choices}) != len(choices):
            raise InvalidQuestion("duplicate choices")
        _require_choice_index(q.correct_choice, len(choices))
        update |= {"answer": "", "tolerance": None, "checklist": []}
    elif q.kind == "true_false":
        if [c.strip().lower() for c in q.choices] not in ([], ["true", "false"]):
            raise InvalidQuestion(f"true/false choices must be {TRUE_FALSE}, got {q.choices}")
        _require_choice_index(q.correct_choice, 2)
        update |= {"choices": TRUE_FALSE, "answer": "", "tolerance": None, "checklist": []}
    elif q.kind in ("numeric", "expression"):
        answer = _strip_math(q.answer)
        if not answer:
            raise InvalidQuestion(f"{q.kind} question without an answer")
        tolerance = abs(q.tolerance) if q.tolerance is not None and q.kind == "numeric" else None
        if not check_answer(
            q.kind,
            answer=answer,
            correct_choice=None,
            tolerance=tolerance,
            response=answer,
            choice=None,
            require_simplified=False,
        ).correct:
            raise InvalidQuestion(f"the grader cannot read the answer {answer!r}")
        update |= {"answer": answer, "tolerance": tolerance, "choices": [], "correct_choice": None, "checklist": []}
    elif q.kind == "free_response":
        checklist = [c for c in q.checklist if c.strip()][:MAX_CHECKLIST]
        if len(checklist) < 2:
            raise InvalidQuestion("free response needs a checklist of at least 2 items")
        if not q.solution.strip():
            raise InvalidQuestion("free response needs a model solution")
        update |= {"checklist": checklist, "answer": "", "tolerance": None, "choices": [], "correct_choice": None}
    else:
        raise InvalidQuestion(f"unknown kind {q.kind}")
    return q.model_copy(update=update)


def clean_questions(questions: list[QuestionDraft], context: str) -> tuple[list[QuestionDraft], list[str]]:
    """Clean a pool; invalid questions are dropped with a reason."""
    kept, dropped = [], []
    for q in questions:
        try:
            kept.append(clean_question(q))
        except InvalidQuestion as e:
            dropped.append(f"{context} {q.key or '?'}: {e}")
    return kept, dropped


def _require_choice_index(index: int | None, n: int) -> None:
    if index is None or not 0 <= index < n:
        raise InvalidQuestion(f"correct_choice {index} out of range 0..{n - 1}")


def _strip_math(text: str) -> str:
    return text.strip().strip("$").strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def normalized_prompt(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


# ---------------------------------------------------------------------------
# Answer-format balance (warnings only: dropping items would shrink the pools a knowledge point needs)
# ---------------------------------------------------------------------------


def visible_length(text: str) -> int:
    """Rough rendered length of Markdown + LaTeX: delimiters, commands and braces do not count."""
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text.replace("$", ""))
    text = re.sub(r"[{}\\^_]", "", text)
    return len(re.sub(r"\s+", " ", text).strip())


def correct_is_longest(q: QuestionDraft) -> bool:
    """multiple_choice: the correct option is strictly the longest one."""
    if q.kind != "multiple_choice" or q.correct_choice is None or len(q.choices) < 2:
        return False
    lengths = [visible_length(c) for c in q.choices]
    correct = lengths[q.correct_choice]
    return all(correct > n for i, n in enumerate(lengths) if i != q.correct_choice)


def length_cue(q: QuestionDraft) -> bool:
    """multiple_choice: the correct option stands out by length alone (much longer than every distractor)."""
    if not correct_is_longest(q):
        return False
    lengths = [visible_length(c) for c in q.choices]
    correct = lengths[q.correct_choice]  # type: ignore[index]
    longest_wrong = max(n for i, n in enumerate(lengths) if i != q.correct_choice)
    return correct >= LENGTH_CUE_RATIO * longest_wrong and correct - longest_wrong >= LENGTH_CUE_CHARS


def format_warnings(pools: list[list[QuestionDraft]]) -> list[str]:
    """Lesson-level checks of the answer formats: share of multiple choice, options that give the answer away."""
    questions = [q for pool in pools for q in pool]
    mc = [q for q in questions if q.kind == "multiple_choice"]
    notes = []
    if questions and len(mc) / len(questions) > MAX_MC_SHARE:
        notes.append(
            f"{len(mc)} of {len(questions)} questions are multiple choice (target ≤ {MAX_MC_SHARE:.0%}); "
            "prefer typed numeric/expression answers and true/false claims"
        )
    cued = [q.key for q in mc if length_cue(q)]
    if cued:
        notes.append(f"correct option much longer than the distractors in {', '.join(cued)}")
    longest = [q for q in mc if correct_is_longest(q)]
    if len(mc) >= 3 and len(longest) / len(mc) > 0.5:
        notes.append(f"the correct option is the longest in {len(longest)} of {len(mc)} multiple-choice questions")
    return notes


def first_free_response_second(pool: list[QuestionDraft]) -> list[QuestionDraft]:
    """Serving order is insertion order: put the pool's first free_response question at position 2 (it must be served
    no later than the knowledge point's second question), keeping the others in their order."""
    index = next((i for i, q in enumerate(pool) if q.kind == "free_response"), None)
    if index is None or index <= 1:
        return list(pool)
    reordered = [q for i, q in enumerate(pool) if i != index]
    reordered.insert(1, pool[index])
    return reordered


def insert_questions(
    conn: sqlite3.Connection,
    skill_id: str,
    kp_index: int,
    questions: list[QuestionDraft],
    *,
    pending: bool = False,
) -> list[str]:
    """Insert questions of one knowledge point; returns their ids. Free-response questions are marked as not
    needing verification. pending=True keeps machine-checkable questions out of serving (flagged 'verification:
    pending') until verify_question records its verdict."""
    ids = []
    now = db.now_iso()
    for q in questions:
        qid = db.new_id()
        checkable = is_machine_checkable(q.kind)
        verify = {} if checkable else {"status": "skipped", "reason": "self-graded"}
        hold = pending and checkable
        conn.execute(
            "INSERT INTO questions(id, skill_id, key, kp_index, tier, kind, prompt, choices_json, correct_choice, "
            "answer, answer_display, tolerance, hints_json, solution, checklist_json, targets, source_exercise, "
            "flagged, flag_reason, verify_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                qid,
                skill_id,
                q.key,
                kp_index,
                q.tier,
                q.kind,
                q.prompt.strip(),
                db.dumps(q.choices),
                q.correct_choice,
                q.answer,
                q.answer_display.strip(),
                q.tolerance,
                db.dumps(q.hints),
                q.solution.strip(),
                db.dumps(q.checklist),
                q.targets.strip(),
                (q.source_exercise or "").strip() or None,
                int(hold),
                PENDING_REASON if hold else "",
                db.dumps(verify),
                now,
            ),
        )
        ids.append(qid)
    return ids
