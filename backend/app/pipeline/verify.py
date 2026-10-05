"""Verify stage: every machine-checkable question is re-solved by a second model that sees only the prompt and the
choices; disagreements and reported defects are flagged (kept out of serving and scoring).

Flags are shared with the learner: a question the learner reported (any flag whose reason does not start with
'verification:') is never un-flagged or re-verified here — verification only manages its own flags."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .. import db, llm
from ..learning.grading import check_answer
from ..llm_schemas import IndependentAnswer
from .prompts import VERIFY_INSTRUCTIONS
from .questions import is_machine_checkable

log = logging.getLogger("ua.pipeline.verify")

MIN_CONFIDENCE = 0.5
MIN_UNFLAGGED_PER_KP = 2
VERIFICATION_PREFIX = "verification:"
_NO_PROBLEM = {"", "none", "none.", "n/a", "no", "no problems", "no problems.", "-"}


@dataclass
class Verdict:
    flagged: bool
    reason: str
    record: dict[str, Any]


def learner_flagged(row: dict[str, Any]) -> bool:
    """Flagged for a reason other than verification (the learner reported it, or it was retired)."""
    return bool(row["flagged"]) and not (row["flag_reason"] or "").startswith(VERIFICATION_PREFIX)


def needs_verification(row: dict[str, Any]) -> bool:
    return is_machine_checkable(row["kind"]) and not db.loads(row["verify_json"], {}) and not learner_flagged(row)


def verify_request(row: dict[str, Any]) -> str:
    lines = [f"Question type: {row['kind']}", "", row["prompt"]]
    choices = db.loads(row["choices_json"], [])
    if choices:
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


async def verify_question(row: dict[str, Any]) -> Verdict | None:
    """Independent solve (retried once). None when the call failed — the question stays unverified."""
    for attempt in range(2):
        try:
            independent = await llm.structured(
                role="verify",
                instructions=VERIFY_INSTRUCTIONS,
                text=verify_request(row),
                schema=IndependentAnswer,
                max_output_tokens=16000,
                label=f"verify {row['key']}",
                stream=True,
            )
        except Exception as e:  # noqa: BLE001 — retried once, then left unverified
            log.warning("verification of question %s failed (attempt %d): %s", row["id"], attempt + 1, e)
            continue
        verdict = judge(row, independent)
        # The CASE reads the current row, so a learner flag set while the call was in flight is kept as well.
        db.execute(
            "UPDATE questions SET verify_json = ?, "
            "flagged = CASE WHEN flagged = 1 AND flag_reason NOT LIKE 'verification:%' THEN 1 ELSE ? END, "
            "flag_reason = CASE WHEN flagged = 1 AND flag_reason NOT LIKE 'verification:%' THEN flag_reason ELSE ? END "
            "WHERE id = ?",
            (db.dumps(verdict.record), int(verdict.flagged), verdict.reason, row["id"]),
        )
        return verdict
    return None


async def verify_skill(skill_id: str) -> list[str]:
    """Verify the skill's pending questions concurrently, then keep every knowledge point servable.
    Returns log notes (questions kept despite disagreement)."""
    rows = [r for r in db.query("SELECT * FROM questions WHERE skill_id = ?", (skill_id,)) if needs_verification(r)]
    if rows:
        await asyncio.gather(*(verify_question(r) for r in rows))
    return keep_kps_servable(skill_id)


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
    if rows:
        await asyncio.gather(*(verify_question(r) for r in rows))
    return keep_kps_servable(skill_id)


def keep_kps_servable(skill_id: str) -> list[str]:
    """Never leave a knowledge point with fewer than 2 unflagged questions: un-flag the most plausible verification
    rejects (status 'disagree_kept'). Questions flagged by the learner are never un-flagged."""
    notes = []
    rows = db.query("SELECT * FROM questions WHERE skill_id = ? ORDER BY kp_index, key", (skill_id,))
    for kp in sorted({r["kp_index"] for r in rows}):
        pool = [r for r in rows if r["kp_index"] == kp]
        missing = MIN_UNFLAGGED_PER_KP - sum(1 for r in pool if not r["flagged"])
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
