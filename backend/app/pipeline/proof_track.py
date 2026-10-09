"""Mechanical checks of the proof track (app/prompts/<provider>/proof.py, docs/lesson-v2.md "The proof track").

The first S4a ("quite convoluted") broke rules a program can see: new letters in every question, 12-term expansions,
inflated statements, a faded step that was a regrouping, "which continuation" choices between long method sentences.
These checks find them in a draft so that the revision fixes them:
- plan_problems: the planner's ProofPlan (a defective plan is re-planned once);
- proof_problems: a lesson draft against its plan → (hard, soft). Hard problems keep a revision that introduces them
  from replacing a draft without them; both kinds go to the revision.

Since 2026-10-09 (the learner, after studying S4a) a proof lesson's graded inline checks are its scored steps and the
capstone slot holds a short variant (rules.GUIDED_PROOF_RULE; learning/guided.py): scored_check_problems and
variant_problems check those; the knowledge points are the retry and review pool.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import sympy as sp

from ..learning.grading import parse_answer, parse_true_false_set
from ..llm_schemas import InlineCheck, LessonV2Draft, MultistepStep, ProofPlan, QuestionDraft
from .lessons import clamp_target
from ..prompts.rules import PROOF_PATTERN_NAMES
from .questions import _forced, _literal_value, _math_pieces, _printed_in, _value, typed_back, visible_length

RESERVED_LETTERS = {"e", "E", "i", "I", "j", "p"}  # grader constants; p next to i reads as π
SLOT_NAMES = {"x", "y", "u", "v"}
ABSTRACT_PATTERNS = {"law_chain", "suppose_two"}
TEACHING_LETTERS, ANY_LETTERS = 4, 6
TERMS, SPARE_TERMS = 4, 6
MIN_SCORED, MAX_SCORED = 4, 6  # scored steps in the teaching (rules.GUIDED_PROOF_RULE)
VARIANT_STEPS = (2, 3)
MAX_OPTION_CHARS, KEY_LENGTH_RATIO = 90, 1.3

_GREEK = r"(?:\\alpha|\\beta|\\gamma|\\lambda|\\mu|α|β|γ|λ|μ)"
# an input shifted by a constant (α+8, 11i+β, β−3): the first S4a made statements "new" this way
_INFLATED = re.compile(
    rf"{_GREEK}\s*[+\-−]\s*[1-9]\d*(?:\s*i)?(?![\d\w])|(?<![\w.])[1-9]\d*\s*i?\s*[+\-−]\s*{_GREEK}"
)
_EXPAND_DEMAND = re.compile(r"fully\s+expand|expanded\s+(?:real\s+|algebraic\s+)*expression", re.IGNORECASE)
_WARNING = re.compile(
    r"without\s+(?:assuming|using|invoking|citing)|not\s+by\s+citing|rather\s+than\s+(?:distributing|expanding)"
    r"|unproved\s+complex\s+law",
    re.IGNORECASE,
)
_METHOD_WORDS = ("compute", "calculate", "expand", "compare", "match")
_LAW_WORDS = ("commutat", "associat", "distribut", "identity", "regroup", "reorder")
_NUMBERED_LINE = re.compile(r"^\s*\d+\.\s", re.MULTILINE)
_SLOT_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_RESET = re.compile(r"reset\s+the\s+slots", re.IGNORECASE)
_GREEK_NAME = re.compile(r"\\(alpha|beta|gamma|lambda|mu)\b|([αβγλμ])")
_GREEK_TO_NAME = {"α": "alpha", "β": "beta", "γ": "gamma", "λ": "lambda", "μ": "mu"}
_CONDITIONAL_ANSWER = re.compile(
    r"\bif\b[^.]{0,200}\benter\b[^.]{0,200}\binstead\b|\botherwise,?\s+enter\b|submit\s+one\s+answer", re.IGNORECASE
)
_COMPARISON_TABLE = re.compile(r"^\s*\|[^\n]*route\s*a[^\n]*\|[^\n]*route\s*b", re.IGNORECASE | re.MULTILINE)


def _unknown_cells(body: str) -> int:
    """The most '?' cells in one row of the block's Markdown tables (a faded slot row)."""
    best = 0
    for row in _SLOT_ROW.findall(body or ""):
        cells = [c.strip().strip("$").strip() for c in row.strip().strip("|").split("|")]
        best = max(best, sum(1 for c in cells if c == "?"))
    return best


_COORDINATES = re.compile(r"=\s*\$?\s*[a-z]\s*\+\s*[a-z]\s*\\?,?\s*i\b")


def _plan_greek(plan: ProofPlan) -> set[str]:
    """The Greek letters the plan gives coordinates (α = a+bi): arbitrary inputs. A letter the plan reserves for a
    fixed number ('μ: capstone only, μ = (2−i)(1+i)') is not one."""
    names = set()
    for entry in plan.letters:
        if not _COORDINATES.search(entry):
            continue
        for latex, char in _GREEK_NAME.findall(entry.split("=", 1)[0]):
            names.add(latex or _GREEK_TO_NAME[char])
    return names


def _canonical(pattern: str) -> str:
    return PROOF_PATTERN_NAMES.get(pattern, "")


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def _letter_problem(name: str, pattern: str) -> str | None:
    if name in RESERVED_LETTERS:
        return f"{name!r} (the grader reads e as Euler's number, i and j as the imaginary unit, p next to i as π)"
    if name[:1].lower() in ("z", "w"):
        return f"{name!r} (a name starting with z or w is complex for the grader)"
    if pattern == "two_routes" and name in SLOT_NAMES:
        return f"{name!r} (x, y, u, v are the slot names)"
    return None


def _norm_statement(text: str) -> str:
    text = re.sub(r"\\(?:left|right|,|;|!|quad)", "", text or "")
    return re.sub(r"[\s$]+", "", text).lower()


def plan_problems(plan: ProofPlan, n_practice_steps: int) -> list[str]:
    """Why a proof plan cannot be used (empty = usable). The strategy name and halves are normalized, not refused."""
    problems: list[str] = []
    letters = [c.strip() for c in plan.coordinate_letters if c.strip()]
    if not letters:
        problems.append("proof.coordinate_letters is empty: list every letter a typed answer may contain")
    for name in letters:
        why = _letter_problem(name, plan.pattern)
        if why:
            problems.append(f"proof.coordinate_letters may not contain {why}")
    if plan.pattern == "two_halves" and (len(plan.halves) != 2 or any(h.pattern == "two_halves" for h in plan.halves)):
        problems.append("two_halves needs exactly 2 halves in proof.halves, each proved with another pattern")
    if not 3 <= len(plan.practice_statements) <= 6:
        problems.append(f"proof.practice_statements has {len(plan.practice_statements)} statements; plan 3–6")
    taken = {_norm_statement(plan.statement), _norm_statement(plan.guided_case)}
    for s in plan.practice_statements:
        if not 1 <= s.knowledge_point <= max(1, n_practice_steps):
            problems.append(f"practice statement {s.statement[:60]!r} names knowledge point {s.knowledge_point}, which does not exist")
        if _norm_statement(s.statement) in taken:
            problems.append(f"practice statement {s.statement[:60]!r} is the teaching's statement or its guided case")
    variant = _norm_statement(plan.capstone_statement)
    if not variant:
        problems.append("proof.capstone_statement is empty: the variant that ends the lesson (the same strategy, one thing changed)")
    elif variant in taken | {_norm_statement(s.statement) for s in plan.practice_statements}:
        problems.append("proof.capstone_statement (the variant) repeats the teaching's statement, its guided case or a practice statement")
    marks = {s.true for s in plan.scope_statements}
    if not 3 <= len(plan.scope_statements) <= 4 or marks != {True, False}:
        problems.append("proof.scope_statements needs 3 'We have proved …' claims, both true and false ones, and a 4th "
                        "that shows a move the proof may not make")
    if plan.guided_key.strip():
        names = _symbol_names(plan.guided_key)
        if names is None:
            problems.append(f"proof.guided_key {plan.guided_key!r} does not parse as a SymPy answer")
        elif letters and not names <= set(letters):
            problems.append(f"proof.guided_key uses {sorted(names - set(letters))}, which are not in coordinate_letters")
        elif len(names) > TEACHING_LETTERS:
            problems.append(f"the guided case has {len(names)} letters; at most {TEACHING_LETTERS}")
    return problems


def normalize_plan(plan: ProofPlan) -> ProofPlan:
    """The canonical strategy name for the pattern, halves only for two_halves, letters stripped."""
    return plan.model_copy(
        update={
            "strategy_name": _canonical(plan.pattern) or plan.strategy_name,
            "halves": plan.halves if plan.pattern == "two_halves" else [],
            "coordinate_letters": [c.strip() for c in plan.coordinate_letters if c.strip()],
        }
    )


def plan_lines(plan: ProofPlan) -> list[str]:
    """The proof plan as the writer and the reviewer read it."""
    lines = [
        "PROOF PLAN (binding — follow THE PROOF TRACK)",
        f"Pattern: {plan.pattern} — \"{plan.strategy_name}\"",
        f"Statement the teaching proves: {plan.statement}",
        f"Picture: {plan.picture}",
        "We may use:", *[f"- {u}" for u in plan.may_use],
        "We may not use:", *[f"- {u}" for u in plan.may_not_use],
        "Letters (fixed for the whole lesson): " + "; ".join(plan.letters),
        "Letters a typed answer may contain: " + ", ".join(plan.coordinate_letters),
        f"Numbers: {plan.numbers}",
        f"Guided case: {plan.guided_case}" + (f" — key line {plan.guided_key}" if plan.guided_key else ""),
        f"General case (the worked table): {plan.general_case}",
        "Scope statements (the true_false_set after the guided case):",
        *[f"- {s.statement} — {'T' if s.true else 'F'}" for s in plan.scope_statements],
        "Practice statements:",
        *[f"- knowledge point {s.knowledge_point} ({s.role}): {s.statement}" for s in plan.practice_statements],
        f"Variant (the capstone field — the final problem, counts double): {plan.capstone_statement}"
        + (f" — what changes: {plan.variant_change}" if plan.variant_change.strip() else ""),
    ]
    for h in plan.halves:
        lines.append(f"Half {h.label}: assume {h.assume}; show {h.show}; pattern {h.pattern}")
    return lines


# ---------------------------------------------------------------------------
# Typed keys
# ---------------------------------------------------------------------------


def _scalars(answer: str) -> list[sp.Expr] | None:
    try:
        node = parse_answer(answer)
    except Exception:  # noqa: BLE001 — an unreadable key is the strict validation's business
        return None
    exprs = [s.expr for s in node.scalars()]
    return None if any(e is None for e in exprs) else exprs  # type: ignore[return-value]


def _symbol_names(answer: str) -> set[str] | None:
    exprs = _scalars(answer)
    if exprs is None:
        return None
    return {s.name for e in exprs for s in e.free_symbols}


def _terms(expr: sp.Expr) -> int:
    """The most terms of the real or the imaginary coordinate once expanded (letters real)."""
    real = expr.xreplace({s: sp.Symbol(s.name, real=True) for s in expr.free_symbols})
    if sp.count_ops(real) > 200:
        return 99
    try:
        re_part, im_part = sp.expand(real).as_real_imag()
    except Exception:  # noqa: BLE001
        return 0
    return max(len(sp.Add.make_args(sp.expand(p))) if p != 0 else 0 for p in (re_part, im_part))


@dataclass
class Typed:
    where: str
    answer: str
    prompt: str
    role: str  # "inline" | "faded" | "served" (a knowledge point's first questions: a retry's) | "spare" | "capstone" (the variant)
    first_kp1: bool = False


def _parts(q: QuestionDraft) -> list[QuestionDraft | MultistepStep]:
    return list(q.steps) if q.kind == "multistep" else [q]


def typed_keys(draft: LessonV2Draft) -> Iterator[Typed]:
    for n, b in enumerate(draft.blocks, start=1):
        c = b.check
        if c is not None and c.kind in ("numeric", "expression") and c.answer.strip():
            yield Typed(f"block {n} check", c.answer, c.prompt, "faded" if b.kind == "faded_example" else "inline")
    for index, kp in enumerate(draft.knowledge_points):
        target = clamp_target(kp.target_questions)
        for position, q in enumerate(kp.questions):
            role = "served" if position < target else "spare"
            for s, part in enumerate(_parts(q), start=1):
                if part.kind in ("numeric", "expression") and part.answer.strip():
                    where = f"{q.key}" + (f" step {s}" if q.kind == "multistep" else "")
                    yield Typed(where, part.answer, f"{q.prompt} {part.prompt if part is not q else ''}", role,
                                first_kp1=index == 0 and position == 0)
    cap = draft.capstone
    for s, step in enumerate(cap.steps, start=1):
        if step.kind in ("numeric", "expression") and step.answer.strip():
            yield Typed(f"cap step {s}", step.answer, f"{cap.prompt} {step.prompt}", "capstone")


def _letter_problems(draft: LessonV2Draft, plan: ProofPlan) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    allowed = set(plan.coordinate_letters)
    for t in typed_keys(draft):
        names = _symbol_names(t.answer)
        if names is None:
            continue
        foreign = names - allowed
        if foreign and allowed:
            hard.append(
                f"{t.where}: its key uses {', '.join(sorted(foreign))}, which are not the lesson's letters "
                f"({', '.join(plan.coordinate_letters)}) — keep the lesson's letters; to avoid a printed answer use another "
                "practice statement or fixed input, never new letters"
            )
        if len(names) > ANY_LETTERS:
            hard.append(f"{t.where}: its key has {len(names)} letters; at most {ANY_LETTERS} in any typed answer")
        elif len(names) > TEACHING_LETTERS and (t.role in ("inline", "faded", "capstone") or t.first_kp1):
            soft.append(
                f"{t.where}: {len(names)} letters; at most {TEACHING_LETTERS} in the teaching checks, knowledge point 1's "
                "first question and the variant (fix one input at a simple value)"
            )
        exprs = _scalars(t.answer) or []
        terms = max((_terms(e) for e in exprs if e.free_symbols), default=0)
        limit = SPARE_TERMS if t.role == "spare" else TERMS
        if terms > SPARE_TERMS:
            hard.append(f"{t.where}: {terms} terms in one coordinate of its key; at most {TERMS} ({SPARE_TERMS} in a spare) — algebra is not the content")
        elif terms > limit:
            soft.append(f"{t.where}: {terms} terms in one coordinate of its key; at most {TERMS} here (fix an input, or ask for a slot tuple or a first leg)")
    return hard, soft


# ---------------------------------------------------------------------------
# The teaching arc
# ---------------------------------------------------------------------------


def _tf_set_ok(choices: list[str], answer: str, minimum: int = 3) -> bool:
    marks = parse_true_false_set(answer) or ""
    return len(choices) >= minimum and len(marks) == len(choices) and "T" in marks and "F" in marks


def _graded(c: InlineCheck | None) -> bool:
    return c is not None and c.kind != "reflect"


def _arc_problems(draft: LessonV2Draft, plan: ProofPlan) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    blocks = draft.blocks
    graded = [n for n, b in enumerate(blocks) if _graded(b.check)]
    first = graded[0] if graded else None
    if first is not None and blocks[first].check.kind in ("numeric", "expression"):  # type: ignore[union-attr]
        if _symbol_names(blocks[first].check.answer):  # type: ignore[union-attr]
            hard.append(
                f"block {first + 1}: the first graded check already uses letters — numbers first: the learner finishes "
                "route B on the plan's numbers (a constant key) before any letter appears"
            )
    elif first is not None:
        soft.append(f"block {first + 1}: the first graded check should finish a route on the plan's numbers (numeric or expression)")
    if first is not None and plan.pattern == "two_routes" and _SLOT_ROW.search(blocks[first].body or "") and _unknown_cells(blocks[first].body) < 2:
        hard.append(
            f"block {first + 1}: the slot table of the first graded check prints the arithmetic it asks for — show the "
            "product's slots and leave both results as '?', so the learner applies the definition (complaint 14)"
        )
    faded = [n for n, b in enumerate(blocks) if b.kind == "faded_example"]
    if len(faded) != 1:
        soft.append(f"the teaching has {len(faded)} faded examples; exactly one — the guided case's key line")
    for n in faded[:1]:
        c = blocks[n].check
        if c is None:
            continue
        if plan.pattern in ABSTRACT_PATTERNS:
            if c.kind not in ("multiple_choice", "true_false_set"):
                hard.append(f"block {n + 1}: the faded check of an abstract {plan.pattern} lesson is a choice of the next line or its law")
        elif c.kind not in ("numeric", "expression"):
            hard.append(f"block {n + 1}: the faded check is the guided case's key line, typed (numeric or expression)")
        elif plan.pattern == "two_routes" and len(_symbol_names(c.answer) or ()) < 2:
            hard.append(
                f"block {n + 1}: the faded check has a key with fewer than 2 letters — it must be route B's last leg in "
                "letters (the plan's guided_key), not a regrouping or a number"
            )
        if plan.pattern == "two_routes" and _unknown_cells(blocks[n].body) < 3:
            hard.append(
                f"block {n + 1}: the faded slot row prints the inputs the learner should identify — fill only x and y "
                "(the first factor) and show u, v and both results as '?': reading u and v off route B's own intermediate "
                "is where the routes get mixed (the learner's report)"
            )
    reflect = [n for n, b in enumerate(blocks) if b.check is not None and b.check.kind == "reflect"]
    scope = [n for n, b in enumerate(blocks) if b.check is not None and b.check.kind == "true_false_set"
             and _tf_set_ok(b.check.choices, b.check.answer)]
    general = [n for n, b in enumerate(blocks) if b.kind == "generalize"]
    if blocks and blocks[0].kind != "setup":
        soft.append("block 1 is not the setup: open with the strategy's name, the routes and what may and may not be used")
    if len(reflect) != 1:
        soft.append(f"the teaching has {len(reflect)} reflect checks; exactly one — why agreement on numbers is not a proof")
    if not scope:
        soft.append("no scope check in the teaching: after the guided case, a true_false_set of the plan's 3 'We have proved …' claims")
    order = [("a graded check on numbers", first), ("the reflect check", reflect[0] if reflect else None),
             ("the faded example", faded[0] if faded else None), ("the scope check", scope[0] if scope else None),
             ("the generalize block", general[0] if general else None)]
    present = [(name, n) for name, n in order if n is not None]
    for (a, x), (b, y) in zip(present, present[1:]):
        if x >= y:
            soft.append(f"arc order: {a} (block {x + 1}) comes after {b} (block {y + 1}); the order is numbers → reflect → faded example → scope → generalize")
            break
    return hard, soft


# ---------------------------------------------------------------------------
# Practice and capstone
# ---------------------------------------------------------------------------


def _walkthrough_problems(q: QuestionDraft, plan: ProofPlan, *, capstone: bool = False) -> list[str]:
    if q.kind != "multistep":
        return []
    problems: list[str] = []
    steps = q.steps
    typed = sum(1 for s in steps if s.kind in ("numeric", "expression"))
    choices = sum(1 for s in steps if s.kind == "multiple_choice")
    tf_steps = [s for s in steps if s.kind == "true_false_set"]
    abstract = plan.pattern in ABSTRACT_PATTERNS
    low, high = VARIANT_STEPS if capstone else (3, 4)
    if not low <= len(steps) <= high:
        problems.append(
            f"{q.key}: {len(steps)} steps; " + ("the variant has 2–3 (the strategy's moves on the changed statement)"
                                                if capstone else "a proof walkthrough has 3–4")
        )
    # an abstract derivation has no typed vector lines: a true_false_set of line justifications stands in for them
    stand_in = abstract and any(_tf_set_ok(s.choices, s.answer) for s in tf_steps)
    if typed < 2 and not stand_in and not (capstone and abstract and typed >= 1):
        problems.append(f"{q.key}: {typed} typed steps; at least 2 (route A's result, route B's first leg or the slots of its last product)")
    if choices > 1:
        problems.append(f"{q.key}: {choices} multiple_choice steps; at most 1")
    for s in tf_steps:
        if not _tf_set_ok(s.choices, s.answer) or len(s.choices) > 5:
            problems.append(f"{q.key}: a true_false_set step needs 3–5 statements, both true and false ones")
    for i, st in enumerate(steps, start=1):
        if _CONDITIONAL_ANSWER.search(st.prompt or ""):
            problems.append(
                f"{q.key} step {i}: its answer depends on a condition ('if …, enter …; otherwise enter … instead') — one "
                "step asks for one result; split it or drop the condition"
            )
    slot_tuples: list[tuple[str, ...]] = []
    for i, s in enumerate(steps, start=1):
        if s.kind != "expression" or "slot" not in f"{s.prompt} {q.prompt}".lower():
            continue
        exprs = _scalars(s.answer)
        if exprs is None or len(exprs) != 4:
            continue
        if all(e.is_Symbol or e.is_number for e in exprs):
            problems.append(
                f"{q.key} step {i}: the slot tuple {s.answer} has no computed entry — the inputs are read off the "
                "statement; ask for the slots of the product whose first factor the learner computed"
            )
        key = tuple(str(sp.expand(e)) for e in exprs)
        if key in slot_tuples:
            problems.append(f"{q.key} step {i}: the same slot tuple twice")
        slot_tuples.append(key)
    if capstone and steps:
        if _SLOT_ROW.search(q.prompt) or any(_SLOT_ROW.search(s.prompt) or _RESET.search(s.prompt) for s in steps) or _RESET.search(q.prompt):
            problems.append("cap: the variant shows slot rows or 'reset the slots' — remove the scaffold (a slot tuple may still be asked for)")
    return problems


def _option_problems(q: QuestionDraft | MultistepStep, where: str) -> list[str]:
    if q.kind != "multiple_choice" or not q.choices:
        return []
    problems: list[str] = []
    lengths = [visible_length(c) for c in q.choices]
    if max(lengths) > MAX_OPTION_CHARS:
        problems.append(f"{where}: an option has {max(lengths)} characters; at most {MAX_OPTION_CHARS}, no computed lines")
    if any("$$" in c or "\\begin" in c for c in q.choices):
        problems.append(f"{where}: an option holds display math — options are short law names or single equations")
    if any(sum(w in c.lower() for w in _METHOD_WORDS) >= 2 for c in q.choices):
        problems.append(f"{where}: an option describes a method ('compute … and compare …') — no 'which continuation' choices")
    key = q.correct_choice
    if key is not None and 0 <= key < len(lengths) and len(lengths) > 1:
        others = max(n for j, n in enumerate(lengths) if j != key)
        if others and lengths[key] > KEY_LENGTH_RATIO * others:
            problems.append(f"{where}: the correct option is much longer than the others")
    return problems


def _law_choice(q: QuestionDraft | MultistepStep | InlineCheck) -> bool:
    return q.kind == "multiple_choice" and sum(any(w in c.lower() for w in _LAW_WORDS) for c in q.choices) >= 2


def question_problems(q: QuestionDraft, plan: ProofPlan, *, served: bool) -> tuple[list[str], list[str]]:
    """(hard, soft) proof-track problems of one practice question (served = among the questions served first)."""
    hard: list[str] = []
    soft: list[str] = []
    (hard if served else soft).extend(_walkthrough_problems(q, plan))
    soft.extend(_explanation_problems(q))
    for i, part in enumerate(_parts(q), start=1):
        where = q.key + (f" step {i}" if q.kind == "multistep" else "")
        (hard if served else soft).extend(_option_problems(part, where))
    text = " ".join([q.prompt, *[s.prompt for s in q.steps]])
    if _INFLATED.search(q.prompt):
        (hard if served else soft).append(
            f"{q.key}: an input shifted by a constant ({_INFLATED.search(q.prompt).group(0)}) — never inflate a statement; "  # type: ignore[union-attr]
            "use a practice statement from the plan"
        )
    if _EXPAND_DEMAND.search(text):
        soft.append(f"{q.key}: asks to fully expand — ask for standard form with the coordinates unexpanded")
    if len(_NUMBERED_LINE.findall(q.prompt)) >= 3 and _small_integer_tuple(q.answer):
        hard.append(f"{q.key}: a bank of numbered lines to order — use the next line as a choice among single equations")
    return hard, soft


def _explanation_problems(q: QuestionDraft) -> list[str]:
    """A step explanation that prints the route comparison before a later step asks about it (the judges of the first
    proof-track S4a: the true/false step was answered by the explanation just shown)."""
    problems = []
    for i, st in enumerate(q.steps[:-1], start=1):
        later = [j for j, x in enumerate(q.steps[i:], start=i + 1) if x.kind in ("true_false_set", "multiple_choice", "true_false")]
        if later and _COMPARISON_TABLE.search(st.explanation or ""):
            problems.append(
                f"{q.key} step {i}: its explanation prints the route comparison before step {later[0]} asks about it — "
                "move the comparison table to the last step's explanation and the solution"
            )
    return problems


def capstone_proof_problems(cap: QuestionDraft, plan: ProofPlan) -> list[str]:
    """The capstone slot of a proof lesson holds the variant: 2–3 steps, typed where the pattern computes, no scaffold
    in its prompts, short options."""
    problems = _walkthrough_problems(cap, plan, capstone=True)
    for i, step in enumerate(cap.steps, start=1):
        problems += _option_problems(step, f"cap step {i}")
    return problems


def _small_integer_tuple(answer: str) -> bool:
    exprs = _scalars(answer)
    return bool(exprs) and len(exprs) >= 2 and all(e.is_Integer and abs(int(e)) < 20 for e in exprs)


def scored_check_problems(draft: LessonV2Draft) -> tuple[list[str], list[str]]:
    """(hard, soft) problems of the teaching's scored steps (every graded inline check): how many there are, their
    hints, and that none can be read before the learner answers it — the app sends the lesson one scored step at a
    time, so only the blocks up to the step, earlier checks' prompts, explanations and answers can give it away."""
    hard: list[str] = []
    soft: list[str] = []
    blocks = draft.blocks
    graded = [n for n, b in enumerate(blocks) if _graded(b.check)]
    if len(graded) < MIN_SCORED:
        hard.append(
            f"the teaching has {len(graded)} scored steps (graded checks); {MIN_SCORED}–{MAX_SCORED} — the proof's real "
            "moves: the concrete case's key computation(s), the guided case's key line, scope, and one '?' entry of the "
            "general table"
        )
    elif len(graded) > MAX_SCORED:
        soft.append(f"the teaching has {len(graded)} scored steps; at most {MAX_SCORED}")
    general = [n for n, b in enumerate(blocks) if b.kind == "generalize"]
    if general and not any(n >= general[0] for n in graded):
        soft.append(
            f"block {general[0] + 1}: the general case has no scored '?' entry — leave one short cell for the learner "
            "(a slot tuple, a solved coordinate, a coordinate of at most 4 terms) and ask for it"
        )
    for n in graded:
        block, c = blocks[n], blocks[n].check
        assert c is not None
        where = f"block {n + 1}"
        if not c.hint.strip():
            hard.append(f"{where}: the scored check has no hint — one sentence shown after a wrong first answer, pointing to the move or the inputs")
        if c.kind == "true_false":
            hard.append(f"{where}: a scored true_false check is a coin flip — use a true_false_set of 3–4 claims or a multiple_choice of 4")
        elif c.kind == "multiple_choice" and len(c.choices) < 4:
            soft.append(f"{where}: a scored multiple_choice has {len(c.choices)} options; 4")
        elif c.kind == "true_false_set" and not _tf_set_ok(c.choices, c.answer):
            soft.append(f"{where}: a scored true_false_set needs 3–4 claims, both true and false ones")
        if c.kind not in ("numeric", "expression") or not c.answer.strip():
            continue
        shown = [x.body for x in blocks[: n + 1]] + [x.check.prompt for x in blocks[: n + 1] if x.check is not None]
        shown += [t for x in blocks[:n] if x.check is not None for t in (x.check.explanation, x.check.answer_display)]
        letters = bool(_symbol_names(c.answer))  # a short letter key ("-b") is printed for other reasons too often
        value = _value(c.answer)
        known = [v for t in shown for piece in _math_pieces(t) if (v := _literal_value(piece)) is not None]
        tuple_key = value is None and len(_scalars(c.answer) or []) > 1
        if _printed_in(c.answer, shown, small_integers=False, letters_only=letters):
            hard.append(
                f"{where}: the scored check's answer {c.answer_display or c.answer} is printed before the learner answers "
                "it — move the line or table that holds it to a later block (or into the check's explanation)"
            )
        elif value is not None and len(value) == 1 and _forced(value[0], known):
            hard.append(
                f"{where}: the scored check's answer {c.answer_display or c.answer} is forced (a number times its inverse "
                "is 1, a number plus its negative is 0) — ask for a value the learner computes"
            )
        elif (back := typed_back(c.kind, c.answer, [c.prompt, block.body])) is not None:
            hard.append(f"{where}: typing ${back}$ from the block back is accepted as the answer — ask for a result the learner works out")
        elif tuple_key and any(typed_back(c.kind, c.answer, [f"${piece}$"]) is not None  # tuple-like pieces only: a
                               for t in shown for piece in _math_pieces(t) if "," in piece):  # long table would use up the probes
            hard.append(f"{where}: the scored check's tuple {c.answer_display or c.answer} is printed before the learner answers it")
        elif value is not None and _printed_in(c.answer, [block.body, c.prompt], small_integers=True):
            soft.append(f"{where}: the scored check's answer {c.answer_display or c.answer} appears in its own block — make sure it is not readable there")
        if c.hint.strip() and _printed_in(c.answer, [c.hint], small_integers=True, letters_only=letters):
            hard.append(f"{where}: the hint gives the answer away — point to the move or the inputs instead")
    return hard, soft


def proof_problems(draft: LessonV2Draft, plan: ProofPlan) -> tuple[list[str], list[str]]:
    """(hard, soft) proof-track problems of a lesson draft against its plan."""
    hard, soft = _letter_problems(draft, plan)
    h, s = _arc_problems(draft, plan)
    hard += h
    soft += s
    h, s = scored_check_problems(draft)
    hard += h
    soft += s
    served_scope = False
    law_choices = 0
    points = draft.knowledge_points
    if points and plan.pattern not in ABSTRACT_PATTERNS and points[0].questions:
        first = points[0].questions[0]
        typed = sum(1 for st in first.steps if st.kind in ("numeric", "expression")) if first.kind == "multistep" else 0
        if typed < 2:
            hard.append(
                f"knowledge point 1 leads with {first.key} ({first.kind}): the question a retry serves there is the "
                "transfer walkthrough of a practice statement (3–4 steps, at least 2 typed); a repair or slot-error "
                "question is a spare"
            )
    served_count = sum(clamp_target(kp.target_questions) for kp in points)
    if served_count > len(points) or len(points) > 3:
        soft.append(
            f"{served_count} practice questions lead the knowledge points; a retry serves one per knowledge point "
            "(knowledge point 1's walkthrough, knowledge point 2's true_false_set) — set target_questions to 1 and move "
            "further statements to worked examples or spares"
        )
    for kp_index, kp in enumerate(draft.knowledge_points, start=1):
        target = clamp_target(kp.target_questions)
        single = False
        for position, q in enumerate(kp.questions):
            h, s = question_problems(q, plan, served=position < target)
            hard += h
            soft += s
            if q.kind in ("numeric", "expression") or (q.kind == "true_false_set" and _tf_set_ok(q.choices, q.answer)):
                single = True
            law_choices += sum(_law_choice(p) for p in _parts(q))
            if position < target:  # (what a proof establishes is tested in the lesson: the scope check, the variant)
                marks = (parse_true_false_set(q.answer) or "") if q.kind == "true_false_set" else ""
                if len(marks) >= 4 and min(marks.count("T"), marks.count("F")) < 2:
                    soft.append(f"{q.key}: {marks} — at least two true and two false claims, so marking everything one way fails")
        if not single:
            soft.append(f"knowledge point {kp_index} has no single-answer question (typed, or a true_false_set of 3+) — quizzes never serve multistep questions")
    cap = draft.capstone
    hard += capstone_proof_problems(cap, plan)
    soft += _explanation_problems(cap)
    for name in sorted(_plan_greek(plan)):
        char = next(k for k, v in _GREEK_TO_NAME.items() if v == name)
        assigned = rf"\s*=\s*\(?\s*[-−\d(]"  # λ = (2−i)(1+i), λ = 3+i — not αβ = 2−5i
        if re.search(rf"(?<![A-Za-z}}])\\{name}{assigned}", cap.prompt) or re.search(rf"(?<![\w}}])\\?{char}{assigned}", cap.prompt):
            soft.append(f"cap: \\{name} names a computed number here but an arbitrary input elsewhere — give the fixed input a new letter (\\mu)")
    served_scope |= any(s.kind == "true_false_set" and _tf_set_ok(s.choices, s.answer) for s in cap.steps)
    served_scope |= any(_graded(b.check) and b.check.kind == "true_false_set" and _tf_set_ok(b.check.choices, b.check.answer)
                        for b in draft.blocks)  # type: ignore[union-attr]  # the scored scope check
    law_choices += sum(_law_choice(s) for s in cap.steps) + sum(_law_choice(b.check) for b in draft.blocks if b.check)
    if not served_scope:
        hard.append(
            "no scored step and no variant step tests what a proof establishes: the scope check after the guided case is "
            "a true_false_set of the plan's three 'We have proved …' claims"
        )
    if law_choices > 1:
        soft.append(f"{law_choices} 'which law' choices in the lesson; at most one")
    warnings = sum(len(_WARNING.findall(t)) for t in _texts_after_setup(draft))
    if warnings > 1:
        soft.append(f"the circularity warning ('without assuming …') appears {warnings} times after the setup; state it once, in the setup")
    for n, b in enumerate(draft.blocks, start=1):
        if b.check is not None and _EXPAND_DEMAND.search(b.check.prompt):
            soft.append(f"block {n}: asks to fully expand — ask for standard form with the coordinates unexpanded")
    return list(dict.fromkeys(hard)), [p for p in dict.fromkeys(soft) if p not in hard]


def _texts_after_setup(draft: LessonV2Draft) -> Iterator[str]:
    for b in draft.blocks[1:]:
        yield b.body
        if b.check is not None:
            yield b.check.prompt
            yield from b.check.choices
    for kp in draft.knowledge_points:
        for q in kp.questions:
            yield q.prompt
            yield from q.choices
            for s in q.steps:
                yield s.prompt
                yield from s.choices
    yield draft.capstone.prompt
    for s in draft.capstone.steps:
        yield s.prompt
        yield from s.choices
