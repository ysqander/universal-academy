"""Answer checking. STUB — the learning-engine build replaces this with the full SymPy implementation (SPEC §6.1).

Public interface (stable):
    check_answer(kind, *, answer, correct_choice, tolerance, response, choice) -> GradeResult
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GradeResult:
    correct: bool
    feedback: str


def check_answer(
    kind: str,
    *,
    answer: str,
    correct_choice: int | None,
    tolerance: float | None,
    response: str | None,
    choice: int | None,
) -> GradeResult:
    if kind in ("multiple_choice", "true_false"):
        ok = choice is not None and correct_choice is not None and int(choice) == int(correct_choice)
        return GradeResult(ok, "Correct." if ok else "Not quite.")
    if kind == "free_response":
        return GradeResult(False, "Self-assessed.")
    ok = (response or "").replace(" ", "") == (answer or "").replace(" ", "")
    return GradeResult(ok, "Correct." if ok else "Not quite.")
