"""Verify stage: every machine-checkable question is re-solved by a second model that sees only the prompt and the
choices; disagreements and reported defects are flagged (kept out of serving and scoring).

A true_false_set disagreement is re-solved once, statement by statement against the stated definitions (role
"graph"): a single misread statement of the first solver must not flag a correct question. When that careful second
solve agrees with the key the question stays servable (status 'agree_on_recheck', both solves recorded, logged).
A "reported problem" (the solver agreed with the key but named a defect, e.g. "option 3 is also correct", or was
unsure) is re-checked the same way, with the reported defect shown: one weak-model false alarm must not withhold a
question — or a whole capstone.

A multistep question (a guided problem, e.g. the lesson's capstone) is verified step by step: the solver gets the
problem, the earlier steps with their correct results and the current step — never its answer — and its answer is
compared with the step's key by the learner-facing grader. A disagreeing step is re-solved once by the stronger model;
a step that still disagrees (or reports a defect) flags the whole question. verify_json = {status, steps: [per-step
records]}.

Before any model call, a question whose answer key contradicts its own displayed answer is flagged (status
'key_mismatch'; questions.display_mismatch). The solver gets the course's notation (course_setting, e.g. "F: R or C")
as a cached prefix and is told to work inside it, so it does not object about cases the textbook excludes.

Flags are shared with the learner: a question the learner reported (any flag whose reason does not start with
'verification:') is never un-flagged or re-verified here — verification only manages its own flags."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .. import db, llm
from ..learning.grading import check_answer
from ..llm_schemas import IndependentAnswer
from .lessons import LEGACY_TARGET, kp_targets
from .prompts import (
    VERIFY_INSTRUCTIONS,
    VERIFY_STATEMENTS_RECHECK,
    VERIFY_STEP_INSTRUCTIONS,
    VERIFY_STEP_RECHECK,
    VERIFY_STEP_STATEMENTS_RECHECK,
)
from .questions import display_mismatch, is_machine_checkable

log = logging.getLogger("ua.pipeline.verify")

MIN_CONFIDENCE = 0.5
MIN_UNFLAGGED_PER_KP = LEGACY_TARGET  # a KP without a stored target keeps at least this many questions servable
VERIFICATION_PREFIX = "verification:"
RECHECK_KINDS = {"true_false_set"}  # a disagreement on these is re-solved once before the question is flagged
_NO_PROBLEM = {"", "none", "none.", "n/a", "no", "no problems", "no problems.", "-"}
SETTING_MAX_SYMBOLS = 60
SETTING_TTL_SECONDS = 60.0
_setting_cache: dict[str, tuple[float, str]] = {}


@dataclass
class Verdict:
    flagged: bool
    reason: str
    record: dict[str, Any]
    note: str = ""  # for the job log (e.g. a disagreement the careful re-check overruled)


def learner_flagged(row: dict[str, Any]) -> bool:
    """Flagged for a reason other than verification (the learner reported it, or it was retired)."""
    return bool(row["flagged"]) and not (row["flag_reason"] or "").startswith(VERIFICATION_PREFIX)


def needs_verification(row: dict[str, Any]) -> bool:
    return is_machine_checkable(row["kind"]) and not db.loads(row["verify_json"], {}) and not learner_flagged(row)


def course_setting(course_id: str) -> str:
    """The textbook's notation as the course's lessons introduce it (first meaning of each symbol, in learning order),
    e.g. "F: either R or C". The independent solver works inside this setting instead of objecting about cases the
    book excludes (fields of characteristic 2 when F means R or C). Cached briefly per course."""
    cached = _setting_cache.get(course_id)
    if cached and time.monotonic() - cached[0] < SETTING_TTL_SECONDS:
        return cached[1]
    meanings: dict[str, str] = {}
    rows = db.query(
        "SELECT lesson_json FROM skills WHERE course_id = ? AND lesson_json IS NOT NULL ORDER BY order_index", (course_id,)
    )
    for r in rows:
        for item in db.loads(r["lesson_json"], {}).get("notation", []) or []:
            symbol, meaning = str(item.get("symbol", "")).strip(), str(item.get("meaning", "")).strip()
            if symbol and meaning and symbol not in meanings and len(meanings) < SETTING_MAX_SYMBOLS:
                meanings[symbol] = meaning
    text = ""
    if meanings:
        lines = "\n".join(f"- {symbol}: {meaning}" for symbol, meaning in meanings.items())
        text = f"COURSE SETTING — the textbook's notation and conventions (work inside them):\n{lines}"
    _setting_cache[course_id] = (time.monotonic(), text)
    return text


def _setting_for(row: dict[str, Any]) -> str:
    skill = db.query_one("SELECT course_id FROM skills WHERE id = ?", (row.get("skill_id"),)) if row.get("skill_id") else None
    return course_setting(skill["course_id"]) if skill else ""


def key_mismatch(row: dict[str, Any]) -> str | None:
    """The question's answer key contradicts its own displayed answer (no model call) — the reason, or None."""
    if row["kind"] == "multistep":
        for i, step in enumerate(db.loads(row.get("steps_json"), []) or [], start=1):
            mismatch = display_mismatch(
                step["kind"],
                answer=step.get("answer", ""),
                answer_display=step.get("answer_display", ""),
                choices=step.get("choices") or [],
                correct_choice=step.get("correct_choice"),
                tolerance=step.get("tolerance"),
            )
            if mismatch:
                return f"step {i}: {mismatch}"
        return None
    return display_mismatch(
        row["kind"],
        answer=row.get("answer") or "",
        answer_display=row.get("answer_display") or "",
        choices=db.loads(row.get("choices_json"), []) or [],
        correct_choice=row.get("correct_choice"),
        tolerance=row.get("tolerance"),
    )


def verify_request(row: dict[str, Any]) -> str:
    lines = [f"Question type: {row['kind']}", "", row["prompt"]]
    choices = db.loads(row["choices_json"], [])
    if row["kind"] == "true_false_set":
        lines += ["", "Statements (mark each true or false; answer one letter per statement, in this order):"]
        lines += [f"{i}. {c}" for i, c in enumerate(choices, start=1)]
    elif choices:
        lines += ["", "Options (0-based index):"] + [f"{i}. {c}" for i, c in enumerate(choices)]
    return "\n".join(lines)


def judge(row: dict[str, Any], independent: IndependentAnswer) -> Verdict:
    """Compare an independent answer with the stored one using the learner-facing grader."""
    problems = independent.problems.strip()
    if problems.lower() in _NO_PROBLEM:
        problems = ""
    grade = check_answer(
        row["kind"],
        answer=row["answer"],
        correct_choice=row["correct_choice"],
        tolerance=row["tolerance"],
        response=independent.answer,
        choice=independent.choice,
        require_simplified=False,  # the solver may answer in any equivalent form; only learners must simplify
    )
    record: dict[str, Any] = {"independent": independent.model_dump()}
    if not grade.correct:
        theirs = _describe(row, independent)
        reason = f"verification: independent solver answered {theirs}" + (f"; noted: {problems}" if problems else "")
        return Verdict(True, reason, {"status": "disagree", **record})
    if problems:
        return Verdict(True, f"verification: reported problem — {problems}", {"status": "problem", **record})
    if independent.confidence < MIN_CONFIDENCE:
        reason = f"verification: low confidence ({independent.confidence:.2f})"
        return Verdict(True, reason, {"status": "problem", **record})
    return Verdict(False, "", {"status": "agree", **record})


def _describe(row: dict[str, Any], independent: IndependentAnswer) -> str:
    if row["kind"] in ("multiple_choice", "true_false"):
        choices = db.loads(row["choices_json"], [])
        if independent.choice is not None and 0 <= independent.choice < len(choices):
            return f"option {independent.choice} ({choices[independent.choice]})"
        return f"option {independent.choice}"
    return repr(independent.answer)


def _reported(first: Verdict) -> str:
    """The defect a first solve reported while agreeing with the key ('' for a disagreement)."""
    if first.record.get("status") != "problem":
        return ""
    independent = first.record.get("independent") or {}
    return (independent.get("problems") or "").strip() or f"low confidence ({independent.get('confidence', 0):.2f})"


async def recheck(
    row: dict[str, Any], first: Verdict, *, text: str | None = None, instructions: str = VERIFY_STATEMENTS_RECHECK
) -> Verdict:
    """Second, careful solve by the stronger 'graph' model of a disagreement (a true_false_set, or a step of a
    multistep question) or of a reported problem (shown to the second solver, who judges whether it is real).
    Agreement with the key and no confirmed defect keep the question servable; a failed call keeps the first
    verdict."""
    request = text if text is not None else verify_request(row)
    reported = _reported(first)
    if reported:
        request += (
            f"\n\nA first solver reached the same answer but reported a possible defect: {reported}\n"
            "Decide whether it is real (for a choice: is another option also correct, or none of them?). Put a "
            "defect in `problems` only if it is real; otherwise leave `problems` empty."
        )
    try:
        second = await llm.structured(
            role="graph",
            instructions=instructions,
            text=request,
            shared_prefix=_setting_for(row) or None,
            schema=IndependentAnswer,
            max_output_tokens=32000,
            label=f"verify {row['key']} (re-check)",
            stream=True,
        )
    except Exception as e:  # noqa: BLE001 — the first verdict stands
        log.warning("re-check of question %s failed: %s", row["id"], e)
        return first
    verdict = judge(row, second)
    record = {**first.record, "status": verdict.record["status"], "recheck": second.model_dump()}
    first_answer = _describe(row, IndependentAnswer.model_validate(first.record["independent"]))
    if not verdict.flagged:
        first_said = f"reported {reported!r}" if reported else f"answered {first_answer}"
        note = (
            f"{row['key']}: the independent solver {first_said}, a careful re-check agreed with the key "
            f"{row['answer'] or row['correct_choice']!r} and found no defect — kept"
        )
        log.info("verification: %s", note)
        return Verdict(False, "", {**record, "status": "agree_on_recheck"}, note)
    if verdict.record["status"] == "disagree":
        reason = f"{first.reason}; a careful re-check answered {_describe(row, second)}"
        return Verdict(True, reason, {**record, "status": "disagree"})
    return Verdict(True, verdict.reason, record)  # the re-check agreed but reported a defect or was unsure


async def _solve(row: dict[str, Any], text: str, *, instructions: str, label: str) -> IndependentAnswer | None:
    """One independent solve (role 'verify'), retried once; None when both calls failed."""
    for attempt in range(2):
        try:
            return await llm.structured(
                role="verify",
                instructions=instructions,
                text=text,
                shared_prefix=_setting_for(row) or None,
                schema=IndependentAnswer,
                max_output_tokens=16000,
                label=label,
                stream=True,
            )
        except Exception as e:  # noqa: BLE001 — retried once, then left unverified
            log.warning("verification of question %s failed (attempt %d): %s", row["id"], attempt + 1, e)
    return None


async def _verify_single(row: dict[str, Any]) -> Verdict | None:
    independent = await _solve(row, verify_request(row), instructions=VERIFY_INSTRUCTIONS, label=f"verify {row['key']}")
    if independent is None:
        return None
    verdict = judge(row, independent)
    status = verdict.record["status"]
    if (status == "disagree" and row["kind"] in RECHECK_KINDS) or status == "problem":
        instructions = VERIFY_STATEMENTS_RECHECK if row["kind"] == "true_false_set" else VERIFY_INSTRUCTIONS
        verdict = await recheck(row, verdict, instructions=instructions)
    return verdict


# ---------------------------------------------------------------------------
# Multistep questions: every step is solved independently, given the earlier steps' correct results
# ---------------------------------------------------------------------------


def step_row(row: dict[str, Any], steps: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """One step of a multistep question shaped like a question row (for judge / recheck / verify_request)."""
    step = steps[index]
    return {
        "id": row["id"],
        "skill_id": row.get("skill_id"),
        "key": f"{row['key']} step {index + 1}",
        "kind": step["kind"],
        "prompt": step.get("prompt", ""),
        "choices_json": db.dumps(step.get("choices") or []),
        "answer": step.get("answer", ""),
        "correct_choice": step.get("correct_choice"),
        "tolerance": step.get("tolerance"),
    }


def _step_result(step: dict[str, Any]) -> str:
    """An earlier step's correct result, as the solver of a later step sees it."""
    choices = step.get("choices") or []
    index = step.get("correct_choice")
    if step["kind"] in ("multiple_choice", "true_false") and index is not None and 0 <= index < len(choices):
        return choices[index]
    if step["kind"] == "true_false_set":
        return f"{step.get('answer', '')} ({step.get('answer_display', '')})"
    return step.get("answer_display") or step.get("answer", "")


def step_request(row: dict[str, Any], steps: list[dict[str, Any]], index: int) -> str:
    """The problem, the earlier steps with their correct results, and the current step — never its answer."""
    lines = ["PROBLEM", row["prompt"]]
    if index:
        lines += ["", "EARLIER STEPS (already answered — these results are correct)"]
        for j, step in enumerate(steps[:index], start=1):
            lines.append(f"Step {j}: {step.get('prompt', '')}")
            choices = step.get("choices") or []
            if step["kind"] == "true_false_set":
                lines += [f"   statement {k}. {c}" for k, c in enumerate(choices, start=1)]
            elif choices:
                lines += [f"   option {k}. {c}" for k, c in enumerate(choices)]
            lines.append(f"   Correct result of step {j}: {_step_result(step)}")
    lines += ["", f"CURRENT STEP {index + 1} of {len(steps)} — solve only this step", verify_request(step_row(row, steps, index))]
    return "\n".join(lines)


def _step_recheck_instructions(kind: str) -> str:
    return VERIFY_STEP_STATEMENTS_RECHECK if kind == "true_false_set" else VERIFY_STEP_RECHECK


async def verify_multistep(row: dict[str, Any]) -> Verdict | None:
    """Verify every step (concurrently); a disagreeing step is re-checked once by the stronger model. Flagged when a
    step still disagrees, reports a defect or is unsure. None when a solve failed twice (the question stays
    unverified)."""
    steps = db.loads(row.get("steps_json"), []) or []
    if not steps:
        return Verdict(True, f"{VERIFICATION_PREFIX} a multistep question without steps", {"status": "problem", "steps": []})
    texts = [step_request(row, steps, i) for i in range(len(steps))]
    answers = await asyncio.gather(
        *(
            _solve(row, text, instructions=VERIFY_STEP_INSTRUCTIONS, label=f"verify {row['key']} step {i + 1}")
            for i, text in enumerate(texts)
        )
    )
    if any(a is None for a in answers):
        return None

    async def judged(i: int, independent: IndependentAnswer) -> Verdict:
        srow = step_row(row, steps, i)
        verdict = judge(srow, independent)
        if verdict.record["status"] in ("disagree", "problem"):
            verdict = await recheck(srow, verdict, text=texts[i], instructions=_step_recheck_instructions(steps[i]["kind"]))
        return verdict

    verdicts = await asyncio.gather(*(judged(i, a) for i, a in enumerate(answers)))
    records = [{"index": i, **v.record} for i, v in enumerate(verdicts)]
    flagged = [(i, v) for i, v in enumerate(verdicts) if v.flagged]
    if flagged:
        reason = "; ".join(f"step {i + 1}: {v.reason.removeprefix(VERIFICATION_PREFIX).strip()}" for i, v in flagged)
        status = "disagree" if any(v.record["status"] == "disagree" for _, v in flagged) else "problem"
        return Verdict(True, f"{VERIFICATION_PREFIX} {reason}", {"status": status, "steps": records})
    rechecked = any(v.record["status"] == "agree_on_recheck" for v in verdicts)
    note = "; ".join(v.note for v in verdicts if v.note)
    return Verdict(False, "", {"status": "agree_on_recheck" if rechecked else "agree", "steps": records}, note)


async def verify_question(row: dict[str, Any]) -> Verdict | None:
    """Independent solve (retried once; a true_false_set disagreement is re-checked once; a multistep question is
    verified step by step). None when the call failed — the question stays unverified."""
    mismatch = key_mismatch(row)
    if mismatch:
        reason = f"{VERIFICATION_PREFIX} answer key and displayed answer disagree — {mismatch}"
        verdict: Verdict | None = Verdict(True, reason, {"status": "key_mismatch"})
    else:
        verdict = await (verify_multistep(row) if row["kind"] == "multistep" else _verify_single(row))
    if verdict is None:
        return None
    # The CASE reads the current row, so a learner flag set while the call was in flight is kept as well.
    db.execute(
        "UPDATE questions SET verify_json = ?, "
        "flagged = CASE WHEN flagged = 1 AND flag_reason NOT LIKE 'verification:%' THEN 1 ELSE ? END, "
        "flag_reason = CASE WHEN flagged = 1 AND flag_reason NOT LIKE 'verification:%' THEN flag_reason ELSE ? END "
        "WHERE id = ?",
        (db.dumps(verdict.record), int(verdict.flagged), verdict.reason, row["id"]),
    )
    return verdict


async def verify_skill(skill_id: str) -> list[str]:
    """Verify the skill's pending questions concurrently, then keep every knowledge point servable.
    Returns log notes (questions kept despite disagreement)."""
    rows = [r for r in db.query("SELECT * FROM questions WHERE skill_id = ?", (skill_id,)) if needs_verification(r)]
    verdicts = await asyncio.gather(*(verify_question(r) for r in rows)) if rows else []
    return _verdict_notes(verdicts) + keep_kps_servable(skill_id)


def _verdict_notes(verdicts: list[Verdict | None]) -> list[str]:
    return [v.note for v in verdicts if v is not None and v.note]


async def reverify_skill(skill_id: str) -> list[str]:
    """Re-check every machine-checkable question of the skill in place (regeneration after prompt fixes). Nothing is
    taken out of serving meanwhile: each row keeps its current verdict and flag until verify_question writes the new
    ones in a single UPDATE, so an interruption leaves every row with its old or its new verdict, and a failed call
    keeps the old one. Learner flags and retired questions are left alone."""
    rows = [
        r
        for r in db.query("SELECT * FROM questions WHERE skill_id = ?", (skill_id,))
        if is_machine_checkable(r["kind"]) and not learner_flagged(r)
    ]
    verdicts = await asyncio.gather(*(verify_question(r) for r in rows)) if rows else []
    return _verdict_notes(verdicts) + keep_kps_servable(skill_id)


def keep_kps_servable(skill_id: str) -> list[str]:
    """Never leave a knowledge point with fewer unflagged questions than its target (target_questions; 2 for legacy
    lessons): un-flag the most plausible verification rejects (status 'disagree_kept'). Questions flagged by the
    learner are never un-flagged; a flagged capstone stays flagged (the lesson then ends after the practice)."""
    notes = []
    skill = db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (skill_id,))
    targets = kp_targets(db.loads(skill["lesson_json"], None) or {}) if skill else []
    rows = db.query(
        "SELECT * FROM questions WHERE skill_id = ? AND role != 'capstone' ORDER BY kp_index, key", (skill_id,)
    )
    for kp in sorted({r["kp_index"] for r in rows}):
        pool = [r for r in rows if r["kp_index"] == kp]
        need = targets[kp] if 0 <= kp < len(targets) else MIN_UNFLAGGED_PER_KP
        missing = need - sum(1 for r in pool if not r["flagged"])
        if missing <= 0:
            continue
        candidates = [r for r in pool if r["flagged"] and r["flag_reason"].startswith(VERIFICATION_PREFIX)]
        candidates.sort(key=_keep_rank)
        for r in candidates[:missing]:
            record = db.loads(r["verify_json"], {})
            record.update({"status": "disagree_kept", "was": record.get("status"), "reason": r["flag_reason"]})
            db.execute(
                "UPDATE questions SET flagged = 0, flag_reason = '', verify_json = ? "
                "WHERE id = ? AND flag_reason LIKE 'verification:%'",
                (db.dumps(record), r["id"]),
            )
            notes.append(f"kept {r['key']} (KP {kp + 1}) despite {r['flag_reason'].removeprefix('verification: ')}")
    return notes


def _keep_rank(row: dict[str, Any]) -> tuple[int, float]:
    """Prefer plain disagreements without a reported defect, then those where the solver was least sure."""
    record = db.loads(row["verify_json"], {})
    independent = record.get("independent", {})
    has_problem = bool((independent.get("problems") or "").strip().lower() not in _NO_PROBLEM)
    return (int(has_problem), float(independent.get("confidence", 1.0)))
