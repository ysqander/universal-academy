"""Scored guided steps of proof lessons (SPEC §6.3b).

A v2 lesson written on the proof track with lesson_json["scoring"] == "guided" scores its graded inline checks: the
steps the learner answers while working through the proof count toward the lesson's 80 % like questions (weight 1),
and one short variant problem (the capstone slot, weight 2) ends the lesson; the knowledge points' questions are left
for a retry and for reviews. A check whose answer key can't be graded automatically is not scored (it stays formative).

Points: right on the first answer 1; a wrong first answer to a typed check (numeric, expression) or a statement set
gets "not yet", the check's hint and one more try for ½; wrong again, or "Show answer", 0. A choice check
(multiple_choice, true_false) has one try — a second would be won by elimination. An unreadable answer is not a try.

The task keeps the record in state_json["guided"]:

    {"version": <lesson hash>, "mode": "scored" | "retry", "order": [block index, ...],
     "checks": {"<index>": {"status": "open" | "retry" | "done", "tries", "first_response", "response", "correct",
                            "pts", "revealed", "feedback", "voided"}}}

"retry" is a later attempt after a finished one (passed or failed) of the same lesson version: the checks are shown
with the answers they had, not scored again, and the attempt is scored on fresh questions. This module holds the pure
parts (which checks count, grading one, the public state); learning/engine.py runs the flow and views.py the gating.
"""

from __future__ import annotations

from typing import Any

from ..schemas import BlockCheckRequest, GuidedCheckState
from . import grading

GUIDED = "guided"  # lesson_json["scoring"] of a proof lesson whose inline checks are scored
GRADED_KINDS = ("numeric", "expression", "multiple_choice", "true_false", "true_false_set")
SECOND_TRY_KINDS = ("numeric", "expression", "true_false_set")
MIN_SET_STATEMENTS = 3  # a statement set with fewer has no meaningful second try
STALE_LESSON = "This lesson was updated — reload to see the new version."
NOT_YET = "Not yet — one more try for ½ point. The hint below may help."
SCORED_IN_LESSON = "This step counts toward the lesson's score — answer it in the lesson."
NOT_SHOWN_YET = "The lesson hasn't reached this step yet — answer the step before it first."


def is_guided(lesson: dict[str, Any] | None) -> bool:
    return isinstance(lesson, dict) and lesson.get("format") == "v2" and lesson.get("scoring") == GUIDED


def blocks_of(lesson: dict[str, Any] | None) -> list[Any]:
    blocks = lesson.get("blocks") if isinstance(lesson, dict) else None
    return blocks if isinstance(blocks, list) else []


def check_of(block: Any) -> dict[str, Any] | None:
    check = block.get("check") if isinstance(block, dict) else None
    return check if isinstance(check, dict) else None


def choices(check: dict[str, Any]) -> list[str]:
    options = [str(c) for c in check.get("choices") or []]
    if check.get("kind") == "true_false" and not options:
        return ["True", "False"]
    return options


def correct_choice(check: dict[str, Any]) -> int | None:
    value = check.get("correct_choice")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def answer_display(check: dict[str, Any]) -> str:
    """The check's answer as shown after a response: for a choice check the correct option's own text (the client
    marks the right option by matching it — a paraphrase would mark none after a wrong pick), otherwise the writer's
    answer_display when given."""
    kind, answer = check["kind"], str(check.get("answer") or "")
    options = choices(check)
    correct = correct_choice(check)
    if kind in ("multiple_choice", "true_false") and correct is not None and 0 <= correct < len(options):
        return options[correct]
    if str(check.get("answer_display") or "").strip():
        return str(check["answer_display"])
    if kind == "true_false_set":
        return grading.true_false_set_display(answer)
    return answer


def gradeable(check: dict[str, Any]) -> bool:
    """Can this check be scored: a graded kind whose answer key the grader can check."""
    kind = check.get("kind")
    if kind not in GRADED_KINDS:
        return False
    options = choices(check)
    if kind in ("multiple_choice", "true_false"):
        correct = correct_choice(check)
        return correct is not None and 0 <= correct < len(options)
    statements = options if kind == "true_false_set" else None
    return grading.is_checkable(kind, str(check.get("answer") or ""), correct_choice(check), statements)


def scored_indices(lesson: dict[str, Any] | None) -> list[int]:
    """The blocks whose checks are scored, in lesson order ([] unless the lesson is guided)."""
    if not is_guided(lesson):
        return []
    out = []
    for i, block in enumerate(blocks_of(lesson)):
        check = check_of(block)
        if check is not None and not check.get("withheld") and gradeable(check):
            out.append(i)
    return out


def second_try(check: dict[str, Any]) -> bool:
    if check.get("kind") == "true_false_set":
        return len(choices(check)) >= MIN_SET_STATEMENTS
    return check.get("kind") in SECOND_TRY_KINDS


def grade_check(check: dict[str, Any], body: BlockCheckRequest) -> grading.GradeResult:
    """Grade one response with the learner-facing grader (an unsimplified but right value gets the same "simplify"
    request as a practice question). Call it WITHOUT holding the engine lock (math grading has its own budget)."""
    kind, answer = check["kind"], str(check.get("answer") or "")
    if kind in ("multiple_choice", "true_false"):
        n = len(choices(check))
        if body.choice is None or (n and not 0 <= body.choice < n):
            return grading.GradeResult(False, "Choose one of the options.", invalid=True)
    elif not (body.response or "").strip():
        message = grading.true_false_set_request(answer) if kind == "true_false_set" else "Enter an answer first."
        return grading.GradeResult(False, message, invalid=True)
    return grading.check_answer(
        kind,
        answer=answer,
        correct_choice=correct_choice(check),
        tolerance=None,
        response=body.response,
        choice=body.choice,
        require_simplified=True,
    )


def response_of(check: dict[str, Any], body: BlockCheckRequest) -> str:
    if check.get("kind") in ("multiple_choice", "true_false"):
        return "" if body.choice is None else str(body.choice)
    return (body.response or "").strip()[:1000]


def not_yet_feedback(check: dict[str, Any], result: grading.GradeResult) -> str:
    """The message after a wrong first answer: "not yet" and nothing more — the check's hint is the help the ½ point
    pays for. The grader's diagnosis ("your answer is the conjugate of the expected one") would turn the second try into
    a sign flip, and a set's statement-by-statement verdict would be its answer key; both come once the step is done."""
    return NOT_YET


def new_record() -> dict[str, Any]:
    return {
        "status": "open", "tries": 0, "first_response": None, "response": None, "correct": None, "pts": None,
        "revealed": False, "feedback": "", "voided": False,
    }  # fmt: skip


def new_state(lesson: dict[str, Any], version: str) -> dict[str, Any] | None:
    """A first attempt's record: every scored check open. None when no check can be scored."""
    order = scored_indices(lesson)
    if not order:
        return None
    return {"version": version, "mode": "scored", "order": order, "checks": {str(i): new_record() for i in order}}


def retry_state(previous: dict[str, Any], lesson: dict[str, Any]) -> dict[str, Any]:
    """A later attempt's record: the previous attempt's answers, shown and not scored again."""
    order = scored_indices(lesson)
    old = previous.get("checks") if isinstance(previous.get("checks"), dict) else {}
    checks = {}
    for i in order:
        rec = {**new_record(), **(old.get(str(i)) or {})}
        checks[str(i)] = rec
    return {"version": previous.get("version", ""), "mode": "retry", "order": order, "checks": checks}


def record(state: dict[str, Any] | None, index: int) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    checks = state.get("checks")
    rec = checks.get(str(index)) if isinstance(checks, dict) else None
    return rec if isinstance(rec, dict) else None


def open_index(state: dict[str, Any] | None) -> int | None:
    """The first scored check still to answer (scored mode), None when all are done."""
    if not isinstance(state, dict) or state.get("mode") != "scored":
        return None
    for i in state.get("order") or []:
        rec = record(state, i)
        if rec is None or rec.get("status") != "done":
            return int(i)
    return None


def counts(state: dict[str, Any] | None) -> tuple[int, int]:
    """(scored checks, of them done) in scored mode; (0, 0) otherwise. Voided checks count in neither."""
    if not isinstance(state, dict) or state.get("mode") != "scored":
        return 0, 0
    recs = [record(state, i) or new_record() for i in state.get("order") or []]
    live = [r for r in recs if not r.get("voided")]
    return len(live), sum(1 for r in live if r.get("status") == "done")


def visible_blocks(state: dict[str, Any] | None, n_blocks: int) -> int:
    """How many blocks the learner may see: up to and including the first open scored check (scored mode)."""
    first = open_index(state)
    return n_blocks if first is None else min(n_blocks, first + 1)


def public_state(check: dict[str, Any], rec: dict[str, Any] | None, *, scored: bool) -> GuidedCheckState:
    """The learner's view of a check's record: the answer and explanation only once it is done, the hint once the
    first answer was wrong."""
    rec = {**new_record(), **(rec or {})}
    done = rec["status"] == "done"
    missed_first = rec.get("first_response") is not None
    return GuidedCheckState(
        status=rec["status"] if rec["status"] in ("open", "retry", "done") else "open",
        tries=int(rec.get("tries") or 0),
        first_response=rec.get("first_response"),
        response=rec.get("response") if done else None,
        correct=rec.get("correct") if done else None,
        points=float(rec["pts"]) if done and rec.get("pts") is not None else None,
        revealed=bool(rec.get("revealed")),
        feedback=str(rec.get("feedback") or ""),
        hint=str(check.get("hint") or "") if missed_first else "",
        answer_display=answer_display(check) if done else "",
        explanation=str(check.get("explanation") or "") if done else "",
        voided=bool(rec.get("voided")),
        reported=bool(rec.get("reported")),
        scored=scored,
    )
