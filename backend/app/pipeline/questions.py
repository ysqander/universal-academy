"""Post-validation of generated questions and their storage in the `questions` table."""

from __future__ import annotations

import logging
import re
import sqlite3

from .. import db
from ..learning.grading import AnswerParseError, check_answer, parse_answer, parse_true_false_set
from ..llm_schemas import MultistepStep, QuestionDraft

log = logging.getLogger("ua.pipeline.questions")

STEP_KINDS = {"multiple_choice", "true_false", "true_false_set", "numeric", "expression"}
# multistep: every step is one of STEP_KINDS, so the whole question is machine-checked (and verified step by step)
MACHINE_CHECKABLE = STEP_KINDS | {"multistep"}
MULTISTEP_STEPS = (2, 4)  # a guided multistep problem has 2-4 steps
CAPSTONE_KEY = "cap"
TRUE_FALSE = ["True", "False"]
MAX_CHOICES = 6
MAX_CHECKLIST = 5
TF_SET_STATEMENTS = (2, 5)  # true_false_set: the learner marks 2-5 statements true or false
MAX_MC_SHARE = 0.4  # target: at most ~40% of a lesson's questions are choice-based (multiple choice + true/false)
MAX_CHOICE_SHARE_HARD = 0.5  # above this a lesson draft is rejected and regenerated (guessable practice)
CHOICE_KINDS = {"multiple_choice", "true_false"}
# "How many of these are true / belong to …" hides which statements the learner got wrong: use true_false_set.
_COUNT_THE_TRUE = re.compile(
    r"\bhow\s+many\s+of\s+(?:these|those|the\s+(?:following|statements|claims|equalities|equations|items|"
    r"objects|listed|given|above|below|numbers|lists|sets|vectors))\b",
    re.IGNORECASE,
)


def is_choice_based(kind: str, n_choices: int = 0) -> bool:
    """Guessable formats that count toward the choice-question cap: multiple choice, true/false, and a
    true_false_set of only 2 statements (with 3 or more, guessing every letter right is unlikely)."""
    return kind in CHOICE_KINDS or (kind == "true_false_set" and n_choices < 3)


def choice_share(pools: list[list[QuestionDraft]]) -> float:
    questions = [q for pool in pools for q in pool]
    return sum(1 for q in questions if is_choice_based(q.kind, len(q.choices))) / len(questions) if questions else 0.0


def counts_true_statements(prompt: str) -> bool:
    """A 'How many of these are true / belong to …' question (forbidden: use true_false_set instead)."""
    return bool(_COUNT_THE_TRUE.search(prompt or ""))


LENGTH_CUE_RATIO = 1.4  # the correct option is this much longer than every distractor …
LENGTH_CUE_CHARS = 25  # … and at least this many characters longer: answerable without solving
PENDING_REASON = "verification: pending"


class InvalidQuestion(ValueError):
    pass


# "Show your working" — only the typed answer is checked, so an instruction to show working is never assessed
# (the learner's review of the S2 lessons: such questions are not wanted). Warned about in format_warnings.
_SHOW_WORKING = re.compile(
    r"\b(?:show|write\s+out|write\s+down)\s+(?:all\s+)?(?:of\s+)?(?:your|the)\s+(?:working|work|steps|products)\b"
    r"|\byour\s+working\s+must\b|\bworked\s+evaluation\b",
    re.IGNORECASE,
)


def asks_to_show_working(prompt: str) -> bool:
    return bool(_SHOW_WORKING.search(prompt or ""))


def is_machine_checkable(kind: str) -> bool:
    return kind in MACHINE_CHECKABLE


def clean_question(q: QuestionDraft) -> QuestionDraft:
    """Return a normalized copy or raise InvalidQuestion with the reason."""
    if not q.prompt.strip():
        raise InvalidQuestion("empty prompt")
    if q.kind != "true_false_set" and counts_true_statements(q.prompt):
        raise InvalidQuestion(
            "asks how many statements are true (hides which ones the learner got wrong) — use true_false_set"
        )
    update: dict = {"tier": min(3, max(1, int(q.tier))), "hints": [h for h in q.hints if h.strip()][:3], "steps": []}
    if q.kind == "multistep":
        low, high = MULTISTEP_STEPS
        if not low <= len(q.steps) <= high:
            raise InvalidQuestion(f"multistep needs {low}-{high} guided steps, got {len(q.steps)}")
        steps = []
        for i, step in enumerate(q.steps, start=1):
            try:
                steps.append(clean_step(step))
            except InvalidQuestion as e:
                raise InvalidQuestion(f"step {i}: {e}") from e
        solution = q.solution.strip() or "\n\n".join(f"**Step {i}.** {s.explanation}" for i, s in enumerate(steps, start=1))
        update |= {
            "steps": steps,
            "answer": "",
            "answer_display": q.answer_display.strip() or steps[-1].answer_display,
            "choices": [],
            "correct_choice": None,
            "tolerance": None,
            "checklist": [],
            "hints": [],  # each step carries its own hint
            "solution": solution,
        }
    elif q.kind == "multiple_choice":
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
    elif q.kind == "true_false_set":
        statements = [c.strip() for c in q.choices if c.strip()]
        low, high = TF_SET_STATEMENTS
        if not low <= len(statements) <= high or len(statements) != len(q.choices):
            raise InvalidQuestion(f"true_false_set needs {low}-{high} non-empty statements")
        if len({_norm(c) for c in statements}) != len(statements):
            raise InvalidQuestion("duplicate statements")
        letters = parse_true_false_set(q.answer.strip().strip("$")) or ""
        if len(letters) != len(statements):
            raise InvalidQuestion(
                f"true_false_set answer must be one letter T or F per statement ({len(statements)}), got {q.answer!r}"
            )
        if not q.solution.strip():
            raise InvalidQuestion("true_false_set needs a solution that explains each statement")
        update |= {"choices": statements, "answer": letters, "correct_choice": None, "tolerance": None, "checklist": []}
    elif q.kind in ("numeric", "expression"):
        answer = _strip_math(q.answer)
        if not answer:
            raise InvalidQuestion(f"{q.kind} question without an answer")
        if q.kind == "expression" and _is_tuple(answer) and answers_in_any_order(q.prompt):
            answer = "{" + answer[1:-1].strip() + "}"  # "enter them in any order": the grader compares a set
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
    cleaned = q.model_copy(update=update)
    mismatch = display_mismatch(
        cleaned.kind,
        answer=cleaned.answer,
        answer_display=cleaned.answer_display,
        choices=cleaned.choices,
        correct_choice=cleaned.correct_choice,
        tolerance=cleaned.tolerance,
    )
    if mismatch:
        raise InvalidQuestion(f"answer key and displayed answer disagree: {mismatch}")
    return cleaned


# ---------------------------------------------------------------------------
# Key vs. displayed answer (no model call): a key that contradicts the question's own displayed answer is wrong in
# one of the two places. Conservative — only a display that clearly reads as an answer is compared.
# ---------------------------------------------------------------------------

_KNOWN_WORDS = {"sqrt", "pi", "sin", "cos", "tan", "exp", "log", "ln", "re", "im", "arg", "abs"}
# Inequalities, ± and text in a display: not a single value.
_NOT_A_VALUE = re.compile(r"[<>]|\\(?:le|ge|ne|leq|geq|neq|pm|mp|text|in|notin)(?![A-Za-z])")


def _displayed_value(shown: str) -> str | None:
    """The value a numeric/expression answer_display shows ('$h(x)=(-x-1)+(x+2)i$.' -> '(-x-1)+(x+2)i'), or None
    when the display is not just a value (prose, several alternatives, an inequality)."""
    text = shown.strip().rstrip(".").strip()
    text = re.sub(r"\\boxed\{(.*)\}", r"\1", text)
    text = text.replace("$", "").strip().rstrip(".,;").strip()
    if not text or _NOT_A_VALUE.search(text):
        return None
    # Words outside LaTeX commands ("or", "and", "both", "coordinates", …) mean prose, not a single value.
    words = re.findall(r"[A-Za-z]{2,}", re.sub(r"\\[A-Za-z]+", " ", text))
    if any(w.lower() not in _KNOWN_WORDS for w in words):
        return None
    if "=" in text:
        text = text.rsplit("=", 1)[1].strip()  # "z = 3-2i" / "h(x) = …" -> the value after the last "="
    return text or None


def _symbol_names(text: str) -> set[str] | None:
    try:
        node = parse_answer(text)
    except (AnswerParseError, ValueError, TypeError):
        return None
    return {str(sym) for scalar in node.scalars() if scalar.expr is not None for sym in scalar.expr.free_symbols}


def _same_symbols(shown_value: str, answer: str) -> bool:
    """The display uses only the key's variables. A question may ask the learner to type renamed variables (λ, x_k
    typed as l, u), and then the display (in the book's symbols) cannot be compared with the key."""
    shown, key = _symbol_names(shown_value), _symbol_names(answer)
    return shown is not None and key is not None and shown <= key


def display_mismatch(
    kind: str,
    *,
    answer: str,
    answer_display: str,
    choices: list[str],
    correct_choice: int | None,
    tolerance: float | None,
) -> str | None:
    """Why the answer key contradicts the question's own displayed answer, or None (consistent or not comparable)."""
    shown = (answer_display or "").strip()
    if not shown:
        return None
    if kind == "true_false":
        word = re.sub(r"[^a-z]", "", re.sub(r"\\text\{([^}]*)\}", r"\1", shown).lower())
        if word in ("true", "false") and correct_choice in (0, 1) and (word == "true") != (correct_choice == 0):
            return f"the key says {TRUE_FALSE[correct_choice]} but the answer shown is {shown!r}"
        return None
    if kind == "multiple_choice":
        matches = [i for i, c in enumerate(choices) if _norm(_strip_math(c)) == _norm(_strip_math(shown))]
        if len(matches) == 1 and matches[0] != correct_choice:
            return f"the answer shown is option {matches[0]} but the key is option {correct_choice}"
        return None
    if kind == "true_false_set":
        letters = parse_true_false_set(_strip_math(shown))
        if letters and len(letters) == len(answer) and letters != answer:
            return f"the answer shown reads {letters} but the key is {answer}"
        return None
    if kind in ("numeric", "expression"):
        value = _displayed_value(shown)
        if not value or not _same_symbols(value, answer):
            return None
        result = check_answer(
            kind, answer=answer, correct_choice=None, tolerance=tolerance, response=value, choice=None,
            require_simplified=False,
        )
        if result.invalid or result.correct:
            return None
        return f"the answer shown {shown!r} does not equal the key {answer!r}"
    return None


def clean_step(step: MultistepStep) -> MultistepStep:
    """Validate one step of a multistep question with the rules of its kind; hint, explanation and answer_display are
    required (the hint halves the step's point, the explanation is shown after the step). Raises InvalidQuestion."""
    if step.kind not in STEP_KINDS:
        raise InvalidQuestion(f"unknown step kind {step.kind}")
    for name in ("hint", "explanation", "answer_display"):
        if not getattr(step, name).strip():
            raise InvalidQuestion(f"empty {name}")
    cleaned = clean_question(
        QuestionDraft(
            key="step",
            tier=1,
            kind=step.kind,
            prompt=step.prompt,
            choices=step.choices,
            correct_choice=step.correct_choice,
            answer=step.answer,
            answer_display=step.answer_display,
            tolerance=step.tolerance,
            hints=[step.hint],
            solution=step.explanation,
            checklist=[],
            targets="",
            source_exercise=None,
            steps=[],
        )
    )
    return MultistepStep(
        prompt=cleaned.prompt.strip(),
        kind=step.kind,
        choices=cleaned.choices,
        correct_choice=cleaned.correct_choice,
        answer=cleaned.answer,
        answer_display=step.answer_display.strip(),
        tolerance=cleaned.tolerance,
        hint=step.hint.strip(),
        explanation=step.explanation.strip(),
    )


# "Enter both roots in any order": the order of the ANSWERS does not matter, so a tuple key becomes a set. Only a
# sentence that is about entering the answers counts — "you may multiply the factors in either order. Give the pair
# (Re, Im)" or "lists are ordered: the entries cannot be entered in any order" keep their ordered tuple.
_ANSWERS = r"(?:roots?|solutions?|answers?|values|zeros|eigenvalues|numbers|them)"
_ENTER = r"(?:enter(?:ed)?|give|given|list(?:ed)?|write|written|type[d]?|input|report|state)"
_ANY_ORDER = r"(?:in\s+)?(?:either|any)\s+order"
_IRRELEVANT = r"(?:does\s*n[o’']t|is\s+not|isn[’']t)\s+(?:matter|important|relevant)"
_UNORDERED_ENTRY = (
    # "Enter both roots, separated by a comma, in any order" / "Give the solutions (order does not matter)"
    re.compile(rf"\b{_ENTER}\b[^.;:!?]*?\b{_ANSWERS}\b[^.;:!?]*?(?:\b{_ANY_ORDER}|\border\s+{_IRRELEVANT})\b", re.IGNORECASE),
    # "The roots may be entered in any order"
    re.compile(rf"\b{_ANSWERS}\b[^.;:!?]*?\b(?:may|can)\s+be\s+{_ENTER}\s+{_ANY_ORDER}\b", re.IGNORECASE),
    # "The order of the roots does not matter" / "the order of your answers is not important"
    re.compile(rf"\border\s+of\s+(?:the|your)\s+(?:\w+\s+)?{_ANSWERS}\s+{_IRRELEVANT}", re.IGNORECASE),
)
_NEGATED_ORDER = re.compile(
    rf"\b(?:not|never|cannot|can[’']t|don[’']t|doesn[’']t|mustn[’']t|no)\b(?:\W+\w+){{0,6}}?\W+{_ANY_ORDER}\b", re.IGNORECASE
)


def answers_in_any_order(prompt: str) -> bool:
    """The prompt says the order of the answers (roots, solutions, values) does not matter — not merely that some
    operation may be done "in either order", and not a negated "cannot be entered in any order"."""
    for sentence in re.split(r"(?<=[.;!?])\s+|\n+", prompt or ""):
        for i, pattern in enumerate(_UNORDERED_ENTRY):
            m = pattern.search(sentence)
            if m and (i == 2 or not _NEGATED_ORDER.search(sentence[: m.end()])):  # negation: in any order only
                return True
    return False


def _is_tuple(answer: str) -> bool:
    """'(a, b)' with a comma at the top level (not '(1+2i)')."""
    if not (answer.startswith("(") and answer.endswith(")")):
        return False
    depth, comma = 0, False
    for i, ch in enumerate(answer):
        depth += ch in "([{"
        depth -= ch in ")]}"
        if depth == 0 and i < len(answer) - 1:
            return False  # '(a)(b)' or '(a, b)*(c, d)': the outer parentheses do not enclose everything
        comma = comma or (ch == "," and depth == 1)
    return comma


# ---------------------------------------------------------------------------
# The capstone (the lesson's final multistep problem)
# ---------------------------------------------------------------------------


def clean_capstone(q: QuestionDraft) -> QuestionDraft:
    """The lesson's final problem: a valid multistep question (2-4 machine-checked steps, each with a hint and an
    explanation), stored with key 'cap' and tier 3. Raises InvalidQuestion. Content rules: capstone_issues."""
    if q.kind != "multistep":
        raise InvalidQuestion(f"must be a multistep question (2–4 guided, machine-checked steps), not {q.kind}")
    return clean_question(q).model_copy(update={"key": CAPSTONE_KEY, "tier": 3})


# Math in a prompt: $$…$$ or $…$. A value counts as "stated" only when it is written as a literal — a number such as
# 4-3i, \frac15+\frac25i, \frac{1-2i}{5} or a tuple of such — never an expression to evaluate ((1+2i)(1-2i), 2\cdot3).
_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL)
_I_SQUARED = re.compile(r"i\s*\^\s*\{?\s*2\s*\}?\s*=\s*-\s*1")  # "use $i^2=-1$" states no result
_SPLIT_RE = re.compile(r"\\text\{[^{}]*\}|\\(?:quad|qquad|neq|ne|approx|to|Rightarrow|implies|iff|text)\b|[=;]")
_LATEX_NOISE = re.compile(r"\\left|\\right|\\[,;:!]|\\displaystyle")
_LITERAL_CHARS = re.compile(r"[0-9.+\-/ i{}(),]*")
_PRODUCT = re.compile(r"\)\s*[\d({]|[\d}i]\s*\(")  # (1+2i)(1-2i), 2(3+i), i(7+i), \frac{1}{5}(1-2i)
_FRAC_DENOMINATORS = re.compile(r"\\frac\s*(?:\{[^{}]*\}|\d)\s*(?:\{([^{}]*)\}|(\d))")
_TYPED = {"numeric", "expression"}


def _split_top_commas(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for k, ch in enumerate(text):
        depth += ch in "([{"
        depth -= ch in ")]}"
        if ch == "," and depth == 0:
            parts.append(text[start:k])
            start = k + 1
    return parts + [text[start:]]


def _math_pieces(text: str) -> list[str]:
    """The math of a prompt cut at relation signs, separators and top-level commas (a tuple's commas are kept)."""
    pieces: list[str] = []
    for m in _MATH_RE.finditer(text or ""):
        math = _I_SQUARED.sub(" ; ", m.group(1) or m.group(2) or "")
        for part in _SPLIT_RE.split(math):
            pieces += [p.strip().rstrip(".") for p in _split_top_commas(part or "") if p and p.strip().rstrip(".")]
    return pieces


def _literal_value(piece: str) -> tuple[complex, ...] | None:
    """The value of a math piece that is a literal number (or a tuple of literal numbers), else None."""
    piece = re.sub(r"\\[dt]frac", r"\\frac", _LATEX_NOISE.sub("", piece))
    bare = piece.replace("\\frac", "").replace("\\sqrt", "")
    if not bare.strip() or not _LITERAL_CHARS.fullmatch(bare) or not re.search(r"[\di]", bare) or _PRODUCT.search(bare):
        return None
    # a quotient by anything but a plain positive integer is a computation (1/(1+2i), \frac{1}{2-i}), not a value
    if re.search(r"/\s*[^\d\s]", piece) or any(not (a or b).strip().isdigit() for a, b in _FRAC_DENOMINATORS.findall(piece)):
        return None
    return _value(piece)


def _value(text: str) -> tuple[complex, ...] | None:
    """A scalar answer or a tuple of scalars as complex numbers; None when it has symbols, is a set, or does not
    parse."""
    try:
        node = parse_answer(text)
    except AnswerParseError:
        return None
    except Exception:  # noqa: BLE001 — anything else unreadable is simply not a value either
        return None
    if node.kind == "scalar":
        scalars = [node]
    elif node.kind == "tuple" and all(i.kind == "scalar" for i in node.items):
        scalars = node.items
    else:
        return None
    values = []
    for scalar in scalars:
        if scalar.expr is None or scalar.expr.free_symbols:
            return None
        try:
            values.append(complex(scalar.expr.evalf()))
        except (TypeError, ValueError):
            return None
    return tuple(values)


def _same(a: tuple[complex, ...], b: tuple[complex, ...]) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= 1e-9 * max(1.0, abs(y)) for x, y in zip(a, b))


def _integer(v: tuple[complex, ...]) -> bool:
    return len(v) == 1 and abs(v[0].imag) < 1e-12 and abs(v[0].real - round(v[0].real)) < 1e-12


def _coordinate_pair(value: tuple[complex, ...] | None, context: str) -> bool:
    """A 2-tuple of real numbers asked for in a text about real and imaginary coordinates: a complex number entered as
    "(real coordinate, imaginary coordinate)" instead of in the form a+bi."""
    return (
        value is not None
        and len(value) == 2
        and all(abs(x.imag) < 1e-12 for x in value)
        and bool(re.search(r"\bimaginary\b", context, re.IGNORECASE))
    )


def capstone_issues(q: QuestionDraft) -> list[tuple[int, str]]:
    """Content rules of a capstone's typed (numeric / expression) steps, the learner's iteration-3 review — returns
    (0-based step index, problem):
    - known before it is computed: the answer is written as a literal in the problem or in a step prompt up to this
      one (e.g. "check that $(1+2i)z=4-3i$" → 4-3i), or equals an earlier step's result (an integer result may
      coincide by chance and is not counted);
    - forced by a definition: the answer is 1 and two of those known values are reciprocals (a number times its
      reciprocal), or 0 and two are negatives;
    - a complex number asked for as a coordinate pair: a 2-tuple of real numbers in a step about real and imaginary
      coordinates (complex numbers are entered in the form a+bi)."""
    issues: list[tuple[int, str]] = []
    stated: list[tuple[complex, ...]] = [v for p in _math_pieces(q.prompt) if (v := _literal_value(p)) is not None]
    results: list[tuple[complex, ...]] = []
    for i, step in enumerate(q.steps):
        stated += [v for p in _math_pieces(step.prompt) if (v := _literal_value(p)) is not None]
        answer = _value(step.answer) if step.kind in _TYPED else None
        if answer is None:
            continue
        where = f"step {i + 1} ({step.answer_display or step.answer})"
        pair = _coordinate_pair(answer, f"{q.prompt} {step.prompt}")
        if pair:  # (real coordinate, imaginary coordinate): judged as the complex number it stands for
            answer = (complex(answer[0].real, answer[1].real),)
        if any(_same(answer, v) for v in stated):
            issues.append((i, f"{where}: its answer is already written in the problem or a step prompt — a scored "
                              "step must ask for something the learner computes"))
        elif any(_same(answer, v) for v in results) and not _integer(answer):
            issues.append((i, f"{where}: its answer is an earlier step's result again (known before it is computed)"))
        elif len(answer) == 1 and _forced(answer[0], stated + results):
            kind = "a number times its reciprocal is 1" if abs(answer[0] - 1) < 1e-9 else "a number plus its negative is 0"
            issues.append((i, f"{where}: its answer is forced by a definition ({kind}) — put such a check in the "
                              "explanation, not in a scored step"))
        if pair:
            issues.append((i, f"{where}: asks for a complex number as a coordinate pair — ask for it in the form "
                              "$a+bi$ (answer 'a+b*i'), or for one coordinate as a number"))
        results.append(answer)
    return issues


def _forced(value: complex, known: list[tuple[complex, ...]]) -> bool:
    scalars = list(dict.fromkeys(v[0] for v in known if len(v) == 1))
    if abs(value - 1) < 1e-9:
        return any(abs(a * b - 1) < 1e-9 for j, a in enumerate(scalars) for b in scalars[j + 1 :])
    if abs(value) < 1e-12:
        return any(abs(a + b) < 1e-9 and abs(a) > 1e-12 for j, a in enumerate(scalars) for b in scalars[j + 1 :])
    return False


def pair_form_warnings(questions: list[QuestionDraft]) -> list[str]:
    """Practice questions (or multistep steps) that ask for a complex number as a coordinate pair."""
    keys = []
    for q in questions:
        parts = q.steps if q.kind == "multistep" else [q]
        for part in parts:
            if part.kind not in _TYPED:
                continue
            if _coordinate_pair(_value(part.answer), f"{q.prompt} {part.prompt}"):
                keys.append(q.key)
                break
    return [f"a complex number asked for as a coordinate pair (use the form a+bi) in {', '.join(keys)}"] if keys else []


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
    choice = [q for q in questions if is_choice_based(q.kind, len(q.choices))]
    notes = []
    if questions and len(choice) / len(questions) > MAX_MC_SHARE:
        notes.append(
            f"{len(choice)} of {len(questions)} questions are multiple choice or true/false "
            f"(target ≤ {MAX_MC_SHARE:.0%}); prefer typed numeric/expression answers"
        )
    uniform = [q.key for q in questions if q.kind == "true_false_set" and len(set(q.answer)) == 1 and len(q.answer) >= 3]
    if uniform:
        notes.append(f"every statement has the same truth value in {', '.join(uniform)}")
    cued = [q.key for q in mc if length_cue(q)]
    if cued:
        notes.append(f"correct option much longer than the distractors in {', '.join(cued)}")
    working = [q.key for q in questions if asks_to_show_working(q.prompt)]
    if working:
        notes.append(f"asks the learner to show working (only the answer is checked) in {', '.join(working)}")
    longest = [q for q in mc if correct_is_longest(q)]
    if len(mc) >= 3 and len(longest) / len(mc) > 0.5:
        notes.append(f"the correct option is the longest in {len(longest)} of {len(mc)} multiple-choice questions")
    return notes


def insert_questions(
    conn: sqlite3.Connection,
    skill_id: str,
    kp_index: int,
    questions: list[QuestionDraft],
    *,
    pending: bool = False,
    role: str = "practice",
) -> list[str]:
    """Insert questions of one knowledge point; returns their ids. Free-response questions are marked as not
    needing verification. pending=True keeps machine-checkable questions out of serving (flagged 'verification:
    pending') until verify_question records its verdict. role 'capstone' marks the lesson's final multistep problem
    (stored with kp_index = the number of knowledge points); a multistep question stores its steps in steps_json."""
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
            "flagged, flag_reason, verify_json, created_at, steps_json, role) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                db.dumps([step.model_dump() for step in q.steps]),
                role,
            ),
        )
        ids.append(qid)
    return ids
