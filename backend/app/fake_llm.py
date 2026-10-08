"""Deterministic canned LLM outputs (UA_FAKE_LLM=1) for offline tests and UI development.

respond() dispatches on the schema class name. Lesson generation v2: the planner splits a skill whose title joins
several abilities with " and " (like "Multiply complex numbers and use their powers") into 3 narrower skills and keeps
any other (its specs name earlier siblings the way real planners do, "a (S2a): …", which plan validation rewrites to
"S2a: …"); the writer returns a teaching sequence with checks of several kinds (expression, true_false_set,
reflect), a faded example and a visual, knowledge points with target_questions 2 and 1, and a 3-step capstone of
mixed kinds (expression, multiple_choice, true_false_set) whose later steps use the first result and none of whose
answers is known in advance; the reviewer reports one blocking issue on a first draft and none on a revised one. v1
lessons have targets 2 and 3 and the same capstone; no generated question is free_response (proofs are multistep
walkthroughs). The content is a small, realistic course on complex numbers and
F^n (Axler §1A style). Every generated question — and every step of a multistep question — carries an invisible
Markdown marker
    [//]: # "ua-fake|<choice>|<answer>"
(a link reference definition, which Markdown renderers do not display) so the fake verifier can agree with it (it
reads the LAST marker of a request: a step's verification request ends with the current step).
Unknown schemas get a minimal valid instance.
"""

from __future__ import annotations

import re
import types
import zlib
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from .llm_schemas import (
    BridgeIssue,
    CapstoneRepair,
    DependentLink,
    QuestionRepair,
    Foundation,
    IndependentAnswer,
    InlineCheck,
    KnowledgeGraphDraft,
    KnowledgePointDraft,
    LessonDraft,
    LessonReview,
    LessonV2Draft,
    MultistepStep,
    NotationItem,
    PageTranscription,
    PlannedPrereq,
    PrereqLink,
    QuestionDraft,
    RepresentationChoice,
    SkillDraft,
    TeachingBlock,
    TeachingPlan,
    TeachingSpec,
    VisualizationPlan,
    VizDiagram,
    VizEqStep,
    VizFrame,
    VizFunction,
    VizParameter,
    VizPlot,
    VizPoint,
    VizSegment,
    VizTable,
    VizVector,
    WorkedExample,
    WorkedStep,
)

_MARKER_RE = re.compile(r'\[//\]: # "ua-fake\|([^|"]*)\|([^"]*)"')
_PAGE_RE = re.compile(r"<<page (\d+)")
_FIGURE_RE = re.compile(r"^- (\S+) · page (\d+)", re.MULTILINE)
_SKILL_RE = re.compile(r"skill (S\d+): (.+)", re.IGNORECASE)
_PROOF_RE = re.compile(r"^Kind: proof\b|proof skill", re.IGNORECASE | re.MULTILINE)


def respond(
    *, schema: type[BaseModel], role: str, instructions: str, text: str, shared_prefix: str | None = None
) -> BaseModel:
    handler = _HANDLERS.get(schema.__name__)
    if handler is None:
        return _minimal(schema)
    result = handler(text, shared_prefix or "")
    return schema.model_validate(result.model_dump()) if type(result) is not schema else result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed(*parts: str) -> int:
    return zlib.crc32("|".join(parts).encode())


def _marker(choice: int | None, answer: str) -> str:
    return f'\n\n[//]: # "ua-fake|{"" if choice is None else choice}|{answer}"'


def _complex(re_: int, im: int) -> tuple[str, str]:
    """(SymPy answer, LaTeX display) of re_ + im·i."""
    sign = "+" if im >= 0 else "-"
    return f"{re_}{sign}{abs(im)}*i", f"${re_} {sign} {abs(im)}i$"


def _cx(a: int, b: int) -> str:
    return f"{a} {'+' if b >= 0 else '-'} {abs(b)}i"


def _vec(v: list[int]) -> str:
    return "(" + ", ".join(str(x) for x in v) + ")"


def _with_distractors(correct: str, candidates: list[str], count: int = 3) -> list[str]:
    """correct + the first `count` distinct wrong options."""
    wrong = [c for c in dict.fromkeys(candidates) if c != correct][:count]
    return [correct, *wrong]


def _question(**fields: Any) -> QuestionDraft:
    defaults: dict[str, Any] = {
        "choices": [],
        "correct_choice": None,
        "answer": "",
        "tolerance": None,
        "checklist": [],
        "source_exercise": None,
        "steps": [],
    }
    return QuestionDraft(**(defaults | fields))


def _step(kind: str, prompt: str, *, display: str, hint: str, explanation: str, choices: list[str] | None = None,
          correct: int | None = None, answer: str = "") -> MultistepStep:
    """One step of a multistep question, with its own verification marker."""
    return MultistepStep(
        prompt=prompt + _marker(correct, answer),
        kind=kind,
        choices=choices or [],
        correct_choice=correct,
        answer=answer,
        answer_display=display,
        tolerance=None,
        hint=hint,
        explanation=explanation,
    )


def _multistep(key: str, prompt: str, steps: list[MultistepStep], *, tier: int = 3, display: str, solution: str,
               targets: str) -> QuestionDraft:
    return _question(key=key, tier=tier, kind="multistep", prompt=prompt, steps=steps, answer_display=display,
                     hints=[], solution=solution, targets=targets)


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

_SKILLS: list[dict[str, Any]] = [
    {
        "title": "Add and multiply complex numbers",
        "objective": "Compute sums and products of complex numbers written as $a+bi$.",
        "kind": "computation",
        "summary": "Complex arithmetic follows from $i^2=-1$ and the usual rules; every later computation in $\\mathbf{C}^n$ uses it.",
        "refs": ["1.1", "1.2"],
        "prereqs": [],
        "foundations": ["A1"],
        "misconceptions": ["Computes $(a+bi)(c+di)$ as $ac+bdi$", "Forgets that $i^2=-1$ changes the sign of $bd$"],
    },
    {
        "title": "Justify commutativity of complex multiplication",
        "objective": "Prove that $\\alpha\\beta=\\beta\\alpha$ for all $\\alpha,\\beta\\in\\mathbf{C}$ from the definition.",
        "kind": "proof",
        "summary": "Shows how properties of $\\mathbf{C}$ are inherited from properties of $\\mathbf{R}$.",
        "refs": ["1.3", "1.4"],
        "prereqs": [("S1", 0.25)],
        "foundations": ["A1"],
        "misconceptions": ["Assumes commutativity of complex multiplication while proving it"],
    },
    {
        "title": "Find additive and multiplicative inverses in C",
        "objective": "Compute $-\\alpha$ and $1/\\alpha$ for a nonzero complex number $\\alpha$.",
        "kind": "computation",
        "summary": "Inverses make subtraction and division possible, completing the arithmetic of $\\mathbf{C}$.",
        "refs": ["1.5"],
        "prereqs": [("S1", 0.75)],
        "foundations": [],
        "misconceptions": ["Takes $1/(a+bi)$ to be $1/a+(1/b)i$"],
    },
    {
        "title": "Distinguish lists from sets",
        "objective": "Decide whether two lists are equal and explain how lists differ from sets.",
        "kind": "distinction",
        "summary": "Order and repetition matter in a list but not in a set — the basis of $\\mathbf{F}^n$.",
        "refs": ["1.8"],
        "prereqs": [],
        "foundations": ["A2"],
        "misconceptions": ["Treats $(1,2)$ and $(2,1)$ as the same list", "Thinks $(3,3)$ has length 1"],
    },
    {
        "title": "Add vectors and scale them in F^n",
        "objective": "Compute $x+y$ and $\\lambda x$ for vectors $x,y\\in\\mathbf{F}^n$ and $\\lambda\\in\\mathbf{F}$.",
        "kind": "computation",
        "summary": "Coordinatewise operations turn lists into vectors that can be added and scaled.",
        "refs": ["1.13", "1.18"],
        "prereqs": [("S1", 0.5), ("S4", 0.25)],
        "foundations": [],
        "misconceptions": ["Adds vectors of different lengths", "Multiplies only the first coordinate by $\\lambda$"],
    },
    {
        "title": "Prove properties of addition in F^n",
        "objective": "Prove that addition in $\\mathbf{F}^n$ is commutative using the definition coordinate by coordinate.",
        "kind": "proof",
        "summary": "The vector-space axioms for $\\mathbf{F}^n$ come from the field properties of $\\mathbf{F}$.",
        "refs": ["1.14"],
        "prereqs": [("S5", 0.5), ("S2", 0.25)],
        "foundations": [],
        "misconceptions": ["Checks one example instead of arbitrary vectors"],
    },
]

_FOUNDATIONS = [
    Foundation(
        key="A1",
        title="Real-number arithmetic",
        description="Commutativity, associativity and distributivity of $+$ and $\\cdot$ on $\\mathbf{R}$.",
        check_question="Expand $(2+3)(4-1)$ using the distributive property.",
        check_answer="$2\\cdot4-2\\cdot1+3\\cdot4-3\\cdot1=15$.",
    ),
    Foundation(
        key="A2",
        title="Sets and set notation",
        description="What a set is, membership $x\\in S$, and set-builder notation.",
        check_question="Is $\\{1,2,2\\}=\\{2,1\\}$?",
        check_answer="Yes: sets ignore order and repetition.",
    ),
]


def _graph(text: str, _prefix: str) -> KnowledgeGraphDraft:
    pages = sorted({int(p) for p in _PAGE_RE.findall(text)}) or [0]
    figures = [(fid, int(page)) for fid, page in _FIGURE_RE.findall(text)]
    skills = []
    for i, spec in enumerate(_SKILLS):
        own = sorted({pages[(i * len(pages)) // len(_SKILLS)], pages[min(len(pages) - 1, ((i + 1) * len(pages)) // len(_SKILLS))]})
        skills.append(
            SkillDraft(
                key=f"S{i + 1}",
                title=spec["title"],
                objective=spec["objective"],
                kind=spec["kind"],
                summary=spec["summary"],
                source_pages=own,
                source_refs=spec["refs"],
                figure_ids=[fid for fid, page in figures if page in own],
                prerequisites=[PrereqLink(skill=k, weight=w) for k, w in spec["prereqs"]],
                foundations=spec["foundations"],
                difficulty=2 + (spec["kind"] == "proof"),
                est_minutes=8 + 2 * (spec["kind"] == "proof"),
                misconceptions=spec["misconceptions"],
            )
        )
    return KnowledgeGraphDraft(
        chapter_title="Vector Spaces: $\\mathbf{R}^n$ and $\\mathbf{C}^n$",
        big_picture=(
            "Linear algebra studies vector spaces. Before the abstract definition, these pages build the two "
            "examples everything else imitates: $\\mathbf{R}^n$ and $\\mathbf{C}^n$. Complex numbers are introduced "
            "so that every polynomial has a root, and lists of numbers become vectors you can add and scale."
        ),
        foundations=_FOUNDATIONS,
        skills=skills,
        excluded=["Digression on fields — optional generalisation beyond $\\mathbf{R}$ and $\\mathbf{C}$."],
    )


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------


def _skill_from(text: str) -> tuple[str, str]:
    m = _SKILL_RE.search(text)
    return (m.group(1), m.group(2).strip()) if m else ("S1", "Complex arithmetic")


def _numbers(seed: int, count: int, low: int = -5, high: int = 6) -> list[int]:
    out, state = [], seed
    while len(out) < count:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        value = low + state % (high - low)
        if value != 0:
            out.append(value)
    return out


def _kp_complex(seed: int) -> KnowledgePointDraft:
    a, b, c, d, e, f, g, h = _numbers(seed, 8)
    re1, im1 = a * c - b * d, a * d + b * c
    ans1, disp1 = _complex(re1, im1)
    re2, im2 = e * g - f * h, e * h + f * g
    ans2, disp2 = _complex(re2, im2)
    misconceptions = [(a * c + b * d, im1), (a * c, b * d), (re1, -im1), (-re1, im1), (re1 + 2, im1), (im1, re1)]
    choices = _with_distractors(disp1, [_complex(r, m)[1] for r, m in misconceptions])
    order = seed % 4
    choices.insert(order, choices.pop(0))
    return KnowledgePointDraft(
        title="Multiplying complex numbers",
        target_questions=2,
        explanation=(
            "To multiply $(a+bi)(c+di)$, expand with the distributive property and replace $i^2$ by $-1$:\n\n"
            "$$(a+bi)(c+di)=(ac-bd)+(ad+bc)i.$$\n\nThere is no need to memorise the formula — rederive it from $i^2=-1$."
        ),
        worked_example=WorkedExample(
            problem="Compute $(2+3i)(4+5i)$.",
            steps=[
                WorkedStep(math="$(2+3i)(4+5i)=2\\cdot4+2\\cdot5i+3i\\cdot4+(3i)(5i)$", why="Distributive property (1.3)."),
                WorkedStep(math="$=8+10i+12i+15i^2$", why="Multiply the real factors."),
                WorkedStep(math="$=8+22i-15=-7+22i$", why="Use $i^2=-1$ and collect real and imaginary parts."),
            ],
            takeaway="Expand, replace $i^2$ by $-1$, then collect the real and imaginary parts.",
        ),
        questions=[
            _question(
                key="k1q1",
                tier=1,
                kind="multiple_choice",
                prompt=f"Compute $({_cx(a, b)})({_cx(c, d)})$." + _marker(order, ""),
                choices=choices,
                correct_choice=order,
                answer_display=disp1,
                hints=["Expand as you would $(x+y)(z+w)$.", f"The $i^2$ term is ${b * d}i^2$.", f"Real part: ${a}\\cdot{c}-({b})({d})$."],
                solution=f"Expanding and using $i^2=-1$ gives {disp1}.",
                targets="product formula; the sign of the $bd$ term",
            ),
            _question(
                key="k1q2",
                tier=1,
                kind="true_false_set",
                prompt="Mark each statement true or false." + _marker(None, "TFT"),
                choices=[
                    "For all real $a,b$: $(a+bi)(a-bi)=a^2+b^2$.",
                    "$i^2=1$.",
                    f"$({_cx(a, b)})\\cdot 0=0$.",
                ],
                answer="TFT",
                answer_display="1 true, 2 false, 3 true",
                hints=[
                    "Expand statement 1; recall the definition of $i$ for statement 2.",
                    "In 1 the cross terms $-abi$ and $abi$ cancel; $i$ was defined by $i^2=-1$.",
                    "1: $-b^2i^2=b^2$, true. 2: false. 3: anything times $0$ is $0$, true.",
                ],
                solution=(
                    "1. True: $(a+bi)(a-bi)=a^2-abi+abi-b^2i^2=a^2+b^2$. 2. False: $i^2=-1$. "
                    "3. True: multiplying by $0$ gives $0$."
                ),
                targets="using $i^2=-1$; which claims about products hold",
            ),
            _question(
                key="k1q3",
                tier=2,
                kind="numeric",
                prompt=f"What is the real part of $({_cx(e, f)})({_cx(g, h)})$?" + _marker(None, str(re2)),
                answer=str(re2),
                answer_display=f"${re2}$",
                hints=["The real part is $ac-bd$.", f"Here $ac={e * g}$.", f"$bd={f * h}$, so subtract."],
                solution=f"The real part is ${e}\\cdot{g}-({f})({h})={re2}$.",
                targets="real part of a product",
            ),
            _question(
                key="k1q4",
                tier=3,
                kind="expression",
                prompt=f"Compute $({_cx(e, f)})({_cx(g, h)})$. Enter your answer in the form $a+bi$."
                + _marker(None, ans2),
                answer=ans2,
                answer_display=disp2,
                hints=["Expand and use $i^2=-1$.", f"The real part is ${re2}$.", f"The imaginary part is ${e}\\cdot{h}+{f}\\cdot{g}$."],
                solution=f"$({_cx(e, f)})({_cx(g, h)})={_cx(re2, im2)}$.",
                targets="full product",
                source_exercise="Exercise 1A.7",
            ),
        ],
    )


def _kp_vectors(seed: int) -> KnowledgePointDraft:
    x = _numbers(seed + 7, 3)
    y = _numbers(seed + 11, 3)
    lam = _numbers(seed + 13, 1, 2, 5)[0]
    total = [xi + lam * yi for xi, yi in zip(x, y)]
    neg = [-xi for xi in x]
    wrong = [[abs(xi) for xi in x], list(reversed(neg)), x, [x[0], *neg[1:]], [2 * v for v in neg]]
    choices = [f"${c}$" for c in _with_distractors(_vec(neg), [_vec(w) for w in wrong])]
    order = (seed // 4) % 4
    choices.insert(order, choices.pop(0))
    return KnowledgePointDraft(
        title="Adding and scaling vectors in $\\mathbf{F}^n$",
        target_questions=3,
        explanation=(
            "Vectors in $\\mathbf{F}^n$ are added and scaled coordinate by coordinate:\n\n"
            "$$(x_1,\\dots,x_n)+(y_1,\\dots,y_n)=(x_1+y_1,\\dots,x_n+y_n),\\qquad \\lambda(x_1,\\dots,x_n)=(\\lambda x_1,\\dots,\\lambda x_n).$$"
        ),
        worked_example=WorkedExample(
            problem="Compute $(1,2,3)+2(0,-1,4)$ in $\\mathbf{R}^3$.",
            steps=[
                WorkedStep(math="$2(0,-1,4)=(0,-2,8)$", why="Scalar multiplication multiplies every coordinate (1.18)."),
                WorkedStep(math="$(1,2,3)+(0,-2,8)=(1,0,11)$", why="Addition is coordinatewise (1.13)."),
            ],
            takeaway="Scale first, then add coordinate by coordinate.",
        ),
        questions=[
            _question(
                key="k2q1",
                tier=1,
                kind="expression",
                prompt=f"Compute ${_vec(x)}+{lam}{_vec(y)}$ in $\\mathbf{{R}}^3$. Enter the list as $(x_1, x_2, x_3)$."
                + _marker(None, _vec(total)),
                answer=_vec(total),
                answer_display=f"${_vec(total)}$",
                hints=["Scale the second vector first.", f"${lam}{_vec(y)}={_vec([lam * v for v in y])}$.", "Now add coordinatewise."],
                solution=f"${_vec(x)}+{_vec([lam * v for v in y])}={_vec(total)}$.",
                targets="coordinatewise addition and scaling",
                source_exercise="Exercise 1A.9",
            ),
            _question(
                key="k2q2",
                tier=2,
                kind="numeric",
                prompt=f"Let $x={_vec(x)}$ and $y={_vec(y)}$. What is the second coordinate of $x+{lam}y$?"
                + _marker(None, str(total[1])),
                answer=str(total[1]),
                answer_display=f"${total[1]}$",
                hints=["Only the second coordinates matter.", f"Compute ${x[1]}+{lam}\\cdot({y[1]})$.", f"That is ${x[1]}+{lam * y[1]}$."],
                solution=f"${x[1]}+{lam}\\cdot({y[1]})={total[1]}$.",
                targets="reading off one coordinate",
            ),
            _question(
                key="k2q3",
                tier=2,
                kind="multiple_choice",
                prompt=f"Which vector is the additive inverse of $x={_vec(x)}$ in $\\mathbf{{R}}^3$?" + _marker(order, ""),
                choices=choices,
                correct_choice=order,
                answer_display=f"${_vec(neg)}$",
                hints=["The inverse $-x$ satisfies $x+(-x)=0$.", "Negate each coordinate.", f"$-x={_vec(neg)}$."],
                solution=f"$-x={_vec(neg)}$ because $x+(-x)=(0,0,0)$.",
                targets="additive inverse in $\\mathbf{F}^n$",
            ),
            _question(
                key="k2q4",
                tier=3,
                kind="numeric",
                prompt="For which $n$ is $(1,2,0,4)+(3,4,5,6)$ an element of $\\mathbf{R}^n$? Enter $n$." + _marker(None, "4"),
                answer="4",
                answer_display="$n=4$",
                hints=["Addition is defined in $\\mathbf{F}^n$ for one fixed $n$.", "Both lists have the same length.", "Count the coordinates: 4."],
                solution="Both lists have length 4, so their sum is defined and lies in $\\mathbf{R}^4$.",
                targets="lengths must match",
            ),
            _vector_walkthrough("k2q5"),
        ],
    )


def _vector_walkthrough(key: str) -> QuestionDraft:
    """A proof of commutativity in F^n as a guided walkthrough (replaces the former written proof)."""
    return _multistep(
        key,
        "Walk through a proof that $x+y=y+x$ for all $x,y\\in\\mathbf{F}^n$, with $x=(x_1,\\dots,x_n)$ and "
        "$y=(y_1,\\dots,y_n)$.",
        [
            _step(
                "multiple_choice",
                "Which definition gives the $k$-th coordinate of $x+y$?",
                choices=[
                    "Addition in $\\mathbf{F}^n$ is coordinatewise: $(x+y)_k=x_k+y_k$",
                    "Scalar multiplication: $(\\lambda x)_k=\\lambda x_k$",
                    "Commutativity of addition in $\\mathbf{F}$",
                    "Two lists are equal when they have the same length",
                ],
                correct=0,
                display="$(x+y)_k=x_k+y_k$",
                hint="The proof starts from how $x+y$ is defined.",
                explanation="Addition in $\\mathbf{F}^n$ is defined coordinate by coordinate (1.13).",
            ),
            _step(
                "expression",
                "For $x=(1,2)$ and $y=(3,-5)$ in $\\mathbf{R}^2$, enter $x+y$ as $(a, b)$.",
                answer="(4, -3)",
                display="$(4,-3)$",
                hint="Add the first coordinates, then the second ones.",
                explanation="$(1+3, 2+(-5))=(4,-3)$; $y+x$ gives the same list.",
            ),
            _step(
                "multiple_choice",
                "Which fact justifies $x_k+y_k=y_k+x_k$ in every coordinate?",
                choices=[
                    "Commutativity of addition in $\\mathbf{F}$",
                    "Associativity of addition in $\\mathbf{F}$",
                    "The definition of addition in $\\mathbf{F}^n$",
                    "Every element of $\\mathbf{F}$ has an additive inverse",
                ],
                correct=0,
                display="Commutativity of addition in $\\mathbf{F}$",
                hint="Each coordinate is a sum of two numbers in $\\mathbf{F}$.",
                explanation="$x_k,y_k\\in\\mathbf{F}$ and addition in $\\mathbf{F}$ is commutative, so the lists agree.",
            ),
        ],
        display="$x+y=y+x$",
        solution=(
            "$x+y=(x_1+y_1,\\dots,x_n+y_n)=(y_1+x_1,\\dots,y_n+x_n)=y+x$, using the definition of addition in "
            "$\\mathbf{F}^n$ and commutativity of addition in $\\mathbf{F}$."
        ),
        targets="proof from the definition, as a walkthrough",
    )


def _proof_question(key: str) -> QuestionDraft:
    """Commutativity of complex multiplication as a guided proof walkthrough (proof skills; no written proofs)."""
    return _multistep(
        key,
        "Walk through the proof that $\\alpha\\beta=\\beta\\alpha$ for all $\\alpha,\\beta\\in\\mathbf{C}$, "
        "with $\\alpha=a+bi$ and $\\beta=c+di$ ($a,b,c,d\\in\\mathbf{R}$).",
        [
            _step(
                "multiple_choice",
                "By the definition of multiplication, which expression is $\\alpha\\beta$?",
                choices=["$(ac-bd)+(ad+bc)i$", "$(ac+bd)+(ad-bc)i$", "$ac+bdi$", "$(ac-bd)+(ad-bc)i$"],
                correct=0,
                display="$(ac-bd)+(ad+bc)i$",
                hint="Expand and use $i^2=-1$.",
                explanation="$(a+bi)(c+di)=ac+adi+bci+bdi^2=(ac-bd)+(ad+bc)i$.",
            ),
            _step(
                "true_false_set",
                "Mark each statement true or false (for all real $a,b,c,d$).",
                choices=["$ac-bd=ca-db$", "$ad+bc=cb+da$", "$ad+bc=ad-bc$"],
                answer="TTF",
                display="True, true, false",
                hint="Real multiplication and addition can be done in either order.",
                explanation="1 and 2 hold by commutativity in $\\mathbf{R}$; 3 fails unless $bc=0$.",
            ),
            _step(
                "multiple_choice",
                "Which property of $\\mathbf{R}$ justifies the true equalities?",
                choices=[
                    "Commutativity of real addition and multiplication",
                    "Existence of additive inverses in $\\mathbf{R}$",
                    "Distributivity alone",
                    "Associativity of real multiplication alone",
                ],
                correct=0,
                display="Commutativity in $\\mathbf{R}$",
                hint="The two sides contain the same products in a different order.",
                explanation="Both equalities only swap factors and summands, which commutativity in $\\mathbf{R}$ allows.",
            ),
        ],
        tier=2,
        display="$\\alpha\\beta=\\beta\\alpha$",
        solution=(
            "$\\alpha\\beta=(ac-bd)+(ad+bc)i$ and $\\beta\\alpha=(ca-db)+(cb+da)i$; by commutativity of real "
            "addition and multiplication the real and imaginary parts agree."
        ),
        targets="proof from the definition, without assuming the result",
    )


def _capstone() -> QuestionDraft:
    """The lesson's final problem (v1 and v2): three chained steps of mixed kinds — compute a product, decide which
    factor makes it real, then read facts off the product (no step's answer is stated or forced in advance)."""
    return _multistep(
        "cap",
        "Let $z=1+2i$ and $w=3-i$. Find $zw$, then use the result to answer two questions without starting over.",
        [
            _step(
                "expression",
                "Compute $zw=(1+2i)(3-i)$. Enter your answer in the form $a+bi$.",
                answer="5+5*i",
                display="$5+5i$",
                hint="Four products; the product of the two $i$ terms contains $i^2$.",
                explanation="$3-i+6i-2i^2=3+5i+2=5+5i$.",
            ),
            _step(
                "multiple_choice",
                "Which of these numbers, multiplied by $zw$, gives a real number?",
                choices=["$1-i$", "$1+i$", "$5+5i$", "$i$"],
                correct=0,
                display="$1-i$",
                hint="Multiply each option by $5+5i$ and look at the imaginary part.",
                explanation="$(5+5i)(1-i)=5-5i+5i-5i^2=10$ is real; the other products have a nonzero imaginary part.",
            ),
            _step(
                "true_false_set",
                "Mark each statement about $zw=5+5i$ true or false.",
                choices=["The real part of $zw$ is $5$", "$zw$ is a real number", "$i\\cdot zw=-5+5i$"],
                answer="TFT",
                display="True, false, true",
                hint="Multiply $5+5i$ by $i$ and use $i^2=-1$.",
                explanation="$\\operatorname{Re}(zw)=5$; $zw$ has a nonzero imaginary part; $i(5+5i)=5i-5$.",
            ),
        ],
        display="$zw=5+5i$",
        solution="$zw=5+5i$; $(5+5i)(1-i)=10$ is real; $\\operatorname{Re}(zw)=5$ and $i\\,zw=-5+5i$.",
        targets="chains multiplication with a decision about the product and $i^2=-1$",
    )


def _lesson(text: str, _prefix: str) -> LessonDraft:
    key, title = _skill_from(text)
    seed = _seed(key, title)
    proof = bool(_PROOF_RE.search(text))
    figures = [fid for fid, _ in _FIGURE_RE.findall(text)]
    first_kp, second_kp = _lesson_kps(seed, proof)
    figure_line = f"\n\n[[figure:{figures[0]}]]" if figures else ""
    return LessonDraft(
        why_it_matters=(
            f"*{title}* answers a question the book raises directly: what is the least we must assume to work "
            "with square roots of negative numbers, and how do lists of numbers become objects we can compute with? "
            "Getting this right now prevents sign errors in every later computation."
        ),
        idea=(
            "**Definition 1.1** — a complex number is an ordered pair $(a,b)$ with $a,b\\in\\mathbf{R}$, written "
            "$a+bi$. Addition and multiplication are defined by\n\n"
            "$$(a+bi)+(c+di)=(a+c)+(b+d)i,\\qquad (a+bi)(c+di)=(ac-bd)+(ad+bc)i.$$"
            + figure_line
            + "\n\nLists of such numbers form $\\mathbf{F}^n$, where operations act coordinate by coordinate."
        ),
        notation=[
            NotationItem(symbol="$\\mathbf{C}$", meaning="the set of complex numbers"),
            NotationItem(symbol="$\\mathbf{F}^n$", meaning="lists of length $n$ with entries in $\\mathbf{F}$"),
        ],
        knowledge_points=[first_kp, second_kp],
        missing_steps=(
            "Why $i^2=-1$ follows from the definition: $(0+1i)(0+1i)=(0\\cdot0-1\\cdot1)+(0\\cdot1+1\\cdot0)i=-1$."
        ),
        common_mistakes=[],
        capstone=_capstone(),
        connection="Coordinatewise operations on $\\mathbf{F}^n$ are the model for the abstract vector space in 1B.",
        # the last two are the kind of caption/page note real models emit: the pipeline keeps item numbers only
        source_refs=["1.1", "1.3", "1.13", "1.13 Definition — addition in F^n", "Printed p.2: complex numbers"],
    )


def _lesson_kps(seed: int, proof: bool, *, v2: bool = False) -> tuple[KnowledgePointDraft, KnowledgePointDraft]:
    """KP 1 (target 2, 4 questions) and KP 2 (v1: target 3, 5 questions ending with a proof walkthrough; v2: target 1,
    3 questions). Proof skills get a multistep proof walkthrough as KP 1's second question."""
    first_kp = _kp_complex(seed)
    second_kp = _kp_vectors(seed)
    if v2:
        by_key = {q.key: q for q in second_kp.questions}
        second_kp.target_questions = 1
        second_kp.questions = [by_key["k2q1"], by_key["k2q3"], by_key["k2q5"]]
    if proof:
        first_kp.questions.insert(1, _proof_question("k1q5"))
    return first_kp, second_kp


# ---------------------------------------------------------------------------
# Lesson generation v2: planner, writer, reviewer
# ---------------------------------------------------------------------------

_PLAN_RE = re.compile(r"Plan the teaching of skill (\S+): (.+)")
_V2_SKILL_RE = re.compile(r"Write the lesson for skill (\S+): (.+)")
_LISTED_SKILL_RE = re.compile(r"^- (S\d+[a-z]*) · .*?(?:\(encompassing weight ([\d.]+)\))?$")


def _section(text: str, header: str) -> list[tuple[str, float]]:
    """Skill keys (and weights) listed under a header line of a plan request, up to the next blank line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(header):
            found = []
            for item in lines[i + 1 :]:
                if not item.strip():
                    break
                m = _LISTED_SKILL_RE.match(item.strip())
                if m:
                    found.append((m.group(1), float(m.group(2) or 0.5)))
            return found
    return []


def _field(text: str, name: str, default: str = "") -> str:
    m = re.search(rf"^{name}: (.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else default


def _spec(local_id: str, title: str, objective: str, kind: str, prereqs: list[tuple[str, float]], pages: list[int], **extra: Any) -> TeachingSpec:
    fields: dict[str, Any] = {
        "local_id": local_id,
        "title": title,
        "objective": objective,
        "kind": kind,
        "assumed_prerequisites": ["Expand $(x+y)(z+w)$ for real numbers", *[f"{k}: an earlier skill" for k, _ in prereqs if k.startswith("S")]],
        "prerequisites": [PlannedPrereq(key=k, weight=w) for k, w in prereqs],
        "out_of_scope": ["Square roots and cube roots", "Exponent rules"],
        "starting_point": "Work out $(2+3i)(1+2i)$.",
        "reasoning_sequence": [
            "Each term of the first bracket multiplies each term of the second: four products.",
            "$(3i)(2i)$ has two factors of $i$, so it contains $i^2$.",
            "Replacing $i^2$ by $-1$ makes that product real.",
            "Collect the real terms and the terms containing $i$.",
        ],
        "success_check": "Multiply two complex numbers given as $a+bi$ and explain where the real part comes from.",
        "representations": [
            RepresentationChoice(
                format="grid_table",
                helps_notice="that there are four products and only one contains $i^2$",
                how_to_read="row term times column term",
            )
        ],
        "practice_steps": ["Multiplying two complex numbers", "Products with a negative imaginary part"],
        "source_pages": pages,
        "source_refs": ["1.1", "Example 1.2"],
        "difficulty": 2,
        "est_minutes": 8,
        "misconceptions": ["Uses $i^2=1$"],
    }
    return TeachingSpec(**(fields | extra))


def _plan(text: str, _prefix: str) -> TeachingPlan:
    m = _PLAN_RE.search(text)
    key, title = (m.group(1), m.group(2).strip()) if m else ("S1", "Skill")
    prereqs = _section(text, "Direct prerequisites")
    dependents = [k for k, _ in _section(text, f"Skills that depend on {key}")]
    pages = sorted({int(p) for p in re.findall(r"<<page (\d+)", _field(text, "Taught on"))})
    kind = _field(text, "Kind", "computation").split(" ")[0]
    allowed = "Splitting: NOT allowed" not in text
    if allowed and " and " in title:

        def sibling(local: str, ability: str) -> list[str]:  # a real planner's style: local id + future key
            return [f"{local} ({key}{local}): {ability}", "Expand $(x+y)(z+w)$ for real numbers"]

        skills = [
            _spec("a", "Multiply two complex numbers", "Compute the product of two complex numbers written as $a+bi$.",
                  "computation", prereqs, pages),
            _spec("b", "Compute powers of a complex number", "Compute a positive-integer power of a complex number by repeated multiplication.",
                  "computation", [("a", 1.0)], pages, out_of_scope=["Finding roots"],
                  assumed_prerequisites=sibling("a", "multiply two complex numbers in the form $a+bi$.")),
            _spec("c", "Check a proposed root", "Decide whether a given complex number is a square or cube root of another by computing its power.",
                  "procedure", [("b", 0.75)], pages, out_of_scope=["Finding roots from scratch"],
                  assumed_prerequisites=sibling("b", "compute positive-integer powers of complex numbers.")),
        ]
        return TeachingPlan(
            split_test=f"Yes: multiplying, computing powers and checking roots need different reasoning ({title}).",
            decision="split",
            rationale="Checking a supplied root is a different task from multiplying.",
            skills=skills,
            dependents=[DependentLink(dependent_key=d, needs=["a"] if i % 2 == 0 else ["b", "c"]) for i, d in enumerate(dependents)],
        )
    objective = _field(text, "Objective", title)
    spec = _spec("a", title, objective, kind if kind in ("concept", "definition", "procedure", "computation", "distinction", "interpretation", "proof") else "computation", prereqs, pages)
    if spec.kind == "proof":  # the proof track's plan (fake_proof: the S4a design)
        from .fake_proof import proof_plan

        spec = spec.model_copy(update={"proof": proof_plan(), "practice_steps": ["Transfer: prove a distributive law with two routes", "What a proof establishes"]})
    return TeachingPlan(
        split_test="No: one family of questions assesses the whole objective.",
        decision="keep",
        rationale="A single coherent ability.",
        skills=[spec],
        dependents=[],
    )


def _check(**fields: Any) -> InlineCheck:
    defaults: dict[str, Any] = {"choices": [], "correct_choice": None, "answer": "", "answer_display": "", "explanation": ""}
    return InlineCheck(**(defaults | fields))


def _block(kind: str, heading: str | None, body: str, *, check: InlineCheck | None = None, visual: VisualizationPlan | None = None) -> TeachingBlock:
    idea = "" if kind in ("check", "faded_example", "summary") else (heading or body[:40])
    return TeachingBlock(
        kind=kind, job=f"{kind}: {heading or body[:40]}", new_idea=idea, heading=heading, body=body, visual=visual, check=check
    )


def _lesson_v2(text: str, _prefix: str) -> LessonV2Draft:
    m = _V2_SKILL_RE.search(text)
    key, title = (m.group(1), m.group(2).strip()) if m else ("S1", "Skill")
    objective = _field(text, "Objective", title)
    if "PROOF PLAN (binding" in text:  # the proof track
        from .fake_proof import proof_lesson

        return proof_lesson(title, objective, key)
    seed = _seed(key, title)
    proof = "This is a PROOF skill" in text
    figures = [fid for fid, _ in _FIGURE_RE.findall(text)]
    first_kp, second_kp = _lesson_kps(seed, proof, v2=True)
    figure = f"\n\n[[figure:{figures[0]}]]" if figures else ""
    steps = VisualizationPlan(
        misunderstanding="Treating $i^2$ as $+1$.",
        purpose="This representation helps the learner notice that only the $bd\\,i^2$ term changes sign.",
        format="equation_steps",
        rationale="The generalization is one line of algebra; highlighting the changed term shows where $-bd$ comes from.",
        book_figure_id=None,
        learner_prompt="Which term turns real?",
        caption="Read top to bottom: the highlighted part changes in each line.",
        table=None,
        steps=[
            VizEqStep(latex="(a+bi)(c+di)=ac+ad\\,i+bc\\,i+\\color{#d9480f}{bd\\,i^2}", note="Four products"),
            VizEqStep(latex="=ac+ad\\,i+bc\\,i\\color{#d9480f}{-bd}", note="Replace $i^2$ by $-1$"),
            VizEqStep(latex="=\\color{#d9480f}{(ac-bd)+(ad+bc)i}", note="Collect"),
        ],
        diagram=None,
        plot=None,
        frames=[],
    )
    blocks = [
        _block("setup", "What changes when we multiply?",
               "You already multiply brackets term by term. The one new step is $i^2=-1$. Let's work out $(2+3i)(1+2i)$." + figure),
        _block("demonstrate", "First, account for all four products",
               "| $\\times$ | $1$ | $2i$ |\n|---|---:|---:|\n| $2$ | $2$ | $4i$ |\n| $3i$ | $3i$ | $6i^2$ |\n\n"
               "The bottom-right entry has two factors of $i$, so it contains $i^2$."),
        _block("check", "Next, use $i^2=-1$", "Replace the $i^2$ term.",
               check=_check(kind="expression", prompt="What does $6i^2$ become? Enter a number.", answer="-6",
                            answer_display="$-6$", explanation="$6i^2=6(-1)=-6$.")),
        _block("demonstrate", "Finally, collect the terms", "$2+4i+3i-6=-4+7i$."),
        _block("generalize", "Where the formula comes from", "With real numbers $a,b,c,d$ the same steps give the formula.", visual=steps),
        _block("faded_example", "Your turn — one step left", "$(1+2i)(3+i)=3+i+6i+2i^2$.",
               check=_check(kind="expression", prompt="Finish $(1+2i)(3+i)=3+i+6i+2i^2$. Enter your answer in the form $a+bi$.",
                            answer="1+7*i", answer_display="$1+7i$", explanation="$2i^2=-2$, so $3-2+7i=1+7i$.")),
        _block("check", None, "Which of these are right?",
               check=_check(kind="true_false_set", prompt="Mark each statement true or false.",
                            choices=["$i^2=-1$", "$(3i)(2i)=6i$", "$(1+i)(1-i)=2$"], answer="TFT",
                            answer_display="True, false, true", explanation="$(3i)(2i)=6i^2=-6$.")),
        _block("check", None, "Explain it in your own words.",
               check=_check(kind="reflect", prompt="Why did the bottom-right entry become a real number?",
                            answer_display="It had two factors of $i$, and $i\\cdot i=i^2=-1$ is real.",
                            explanation="Two factors of $i$ make $i^2=-1$.")),
        _block("summary", None, "Expand, replace $i^2$ by $-1$, collect terms."),
    ]
    revised = "REVISION:" in text
    return LessonV2Draft(
        title=title,
        objective=objective,
        goal_line="Your goal: Calculate a product such as $(2+3i)(1+2i)$" + (" and explain the real part." if revised else "."),
        blocks=blocks,
        notation=[NotationItem(symbol="$i$", meaning="a number with $i^2=-1$")],
        knowledge_points=[first_kp, second_kp],
        capstone=_capstone(),
        connection=f"{key} prepares powers of complex numbers.",
        deeper="An alternative view: multiplication by $i$ rotates by a quarter turn.",
        source_refs=["1.1", "Example 1.2"],
    )


def _review(text: str, _prefix: str) -> LessonReview:
    first = "(first draft)" in text
    issues = (
        [
            BridgeIssue(
                location="Block 2 — First, account for all four products",
                inference_required="why $(3i)(2i)$ contains $i^2$",
                why_insufficient="the grid shows $6i^2$ without saying the two factors of $i$ multiply",
                repair="add one sentence: $(3i)(2i)=3\\cdot2\\cdot i\\cdot i=6i^2$",
                severity="blocking",
            )
        ]
        if first
        else []
    )
    return LessonReview(
        issues=issues,
        density_issues=[],
        untaught_method_exercises=[],
        out_of_scope_violations=[],
        optional_content_needed=[],
        math_errors=[],
        split_recommended=False,
        split_reason="",
    )


# ---------------------------------------------------------------------------
# Visualization plans
# ---------------------------------------------------------------------------

_ROTATION = ["comparison_table", "equation_steps", "diagram_2d", "function_plot", "book_figure", "none"]


def _viz(text: str, _prefix: str) -> VisualizationPlan:
    key, _title = _skill_from(text)
    figure_ids = [fid for fid, _ in _FIGURE_RE.findall(text)]
    fmt = _ROTATION[(int(key[1:]) - 1) % len(_ROTATION)] if key[1:].isdigit() else "none"
    if fmt == "book_figure" and not figure_ids:
        fmt = "frames"
    elif fmt == "none" and figure_ids:
        fmt = "book_figure"
    base = {
        "misunderstanding": "Treating $i^2$ as $+1$, or mixing up lists and sets.",
        "purpose": "Make the one step that changes the sign visible.",
        "format": fmt,
        "rationale": "The confusion is about one specific step, so the simplest representation of that step wins.",
        "book_figure_id": figure_ids[0] if fmt == "book_figure" else None,
        "learner_prompt": "Which step introduces the minus sign?",
        "caption": "",
        "table": None,
        "steps": [],
        "diagram": None,
        "plot": None,
        "frames": [],
    }
    if fmt == "comparison_table":
        base |= {
            "caption": "Lists versus sets",
            "learner_prompt": "Which rows explain why $(3,5)\\ne(5,3)$?",
            "table": VizTable(
                headers=["", "List", "Set"],
                rows=[["Order matters", "yes", "no"], ["Repetition matters", "yes", "no"], ["Example", "$(3,5)\\ne(5,3)$", "$\\{3,5\\}=\\{5,3\\}$"]],
            ),
        }
    elif fmt == "equation_steps":
        base |= {
            "caption": "Where the sign flips: $(1-2i)(3+i)$",
            "steps": [
                VizEqStep(latex="(1-2i)(3+i)", note="Start"),
                VizEqStep(latex="3+i-6i-2\\color{#d9480f}{i^2}", note="Distribute"),
                VizEqStep(latex="3-5i\\color{#d9480f}{+2}", note="Replace $i^2$ by $-1$: $-2i^2=+2$"),
                VizEqStep(latex="\\color{#d9480f}{5}-5i", note="Collect the real part"),
            ],
        }
    elif fmt == "diagram_2d":
        base |= {
            "caption": "Adding $z=2+3i$ and $w=3-i$ in the complex plane",
            "learner_prompt": "Why is $z+w$ the diagonal of the parallelogram?",
            "diagram": VizDiagram(
                x_range=[-1, 6],
                y_range=[-2, 4],
                show_grid=True,
                axis_labels=["Re", "Im"],
                vectors=[
                    VizVector(start=[0, 0], end=[2, 3], label="$z$", color="primary"),
                    VizVector(start=[0, 0], end=[3, -1], label="$w$", color="primary"),
                    VizVector(start=[0, 0], end=[5, 2], label="$z+w$", color="accent"),
                ],
                points=[VizPoint(at=[5, 2], label="$5+2i$", color="accent")],
                segments=[
                    VizSegment(start=[2, 3], end=[5, 2], dashed=True, label=""),
                    VizSegment(start=[3, -1], end=[5, 2], dashed=True, label=""),
                ],
            ),
        }
    elif fmt == "function_plot":
        base |= {
            "caption": "$|t z|$ grows linearly with the scalar $t$",
            "learner_prompt": "How does the steepness change as $a$ grows?",
            "plot": VizPlot(
                x_range=[-3, 3],
                y_range=[-1, 9],
                functions=[VizFunction(expr="Abs(a*x)", label="$|a x|$"), VizFunction(expr="x**2", label="$x^2$")],
                parameter=VizParameter(name="a", min=0, max=3, step=0.5, initial=1),
            ),
        }
    elif fmt == "frames":
        base |= {
            "caption": "Building $(1,2)+2(1,-1)$ step by step",
            "frames": [
                VizFrame(title="Start", diagram=_frame_diagram([[1, 2]]), latex="x=(1,2)", note="The vector $x$."),
                VizFrame(title="Scale", diagram=_frame_diagram([[1, 2], [2, -2]]), latex="2(1,-1)=(2,-2)", note="Scale $y$."),
                VizFrame(title="Add", diagram=_frame_diagram([[1, 2], [2, -2], [3, 0]]), latex="(1,2)+(2,-2)=(3,0)", note="Add."),
            ],
        }
    elif fmt == "none":
        base |= {"rationale": "The definition is already clear from the worked example.", "learner_prompt": ""}
    return VisualizationPlan(**base)


def _frame_diagram(ends: list[list[int]]) -> VizDiagram:
    colors = ["primary", "muted", "accent"]
    return VizDiagram(
        x_range=[-1, 4],
        y_range=[-3, 3],
        show_grid=True,
        axis_labels=["x_1", "x_2"],
        vectors=[VizVector(start=[0, 0], end=e, label="", color=colors[i]) for i, e in enumerate(ends)],
        points=[],
        segments=[],
    )


# ---------------------------------------------------------------------------
# Verification + top-up
# ---------------------------------------------------------------------------


def _independent(text: str, _prefix: str) -> IndependentAnswer:
    markers = _MARKER_RE.findall(text)  # the last one: a multistep step's request ends with the current step
    if not markers:
        return IndependentAnswer(choice=None, answer="", confidence=0.3, problems="Could not determine an answer.")
    choice, answer = markers[-1]
    return IndependentAnswer(choice=int(choice) if choice else None, answer=answer, confidence=0.95, problems="")


def _repaired_question(text: str, prefix: str) -> QuestionDraft:
    """A fresh typed question for the slot the repair request names (like a top-up question)."""
    m = re.search(r"## The slot: question (k\d+q\d+)", text)
    key = m.group(1) if m else "k1q1"
    return _top_up(f"{key}\n{text[-400:]}", prefix).questions[0]


class _TopUp(BaseModel):
    questions: list[QuestionDraft]


def _top_up(text: str, _prefix: str) -> _TopUp:
    keys = re.findall(r"\bk\d+q\d+\b", text.split("\n", 1)[0]) or ["k1q9"]
    questions = []
    if _PROOF_RE.search(text) and len(keys) > 1:  # proof skills: a walkthrough (never a written proof)
        proof = _proof_question(keys.pop())
        questions.append(proof.model_copy(update={"prompt": f"{proof.prompt} (Variant {proof.key}.)"}))
    for key in keys:
        a, b, c, d = _numbers(_seed(key, text[:200]), 4, -9, 10)
        re_, im = a * c - b * d, a * d + b * c
        answer, display = _complex(re_, im)
        questions.append(
            _question(
                key=key,
                tier=2,
                kind="expression",
                prompt=f"Compute $({_cx(a, b)})({_cx(c, d)})$. Enter your answer in the form $a+bi$." + _marker(None, answer),
                answer=answer,
                answer_display=display,
                hints=["Expand and use $i^2=-1$.", f"The real part is ${a}\\cdot{c}-({b})({d})$.", f"The real part is ${re_}$."],
                solution=f"$({_cx(a, b)})({_cx(c, d)})={_cx(re_, im)}$.",
                targets="product of complex numbers",
            )
        )
    return _TopUp(questions=questions)


# ---------------------------------------------------------------------------
# Page transcription (used only if a caller sends one in fake mode) and generic fallback
# ---------------------------------------------------------------------------


def _transcription(text: str, _prefix: str) -> PageTranscription:
    body = "\n".join(line for line in text.splitlines() if line.strip())
    return PageTranscription(
        markdown=body[:4000],
        figures=[],
        rejected_marks=sorted(set(re.findall(r"\bM\d+\b", text))),
        items=[],
        is_exercise_page=bool(re.search(r"^\s*Exercises\b", text, re.MULTILINE)),
        uncertain=[],
    )


def _minimal(schema: type[BaseModel]) -> BaseModel:
    return schema.model_validate({name: _minimal_value(f.annotation) for name, f in schema.model_fields.items()})


def _minimal_value(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = get_args(annotation)
        return None if type(None) in args else _minimal_value(args[0])
    if origin is Literal:
        return get_args(annotation)[0]
    if origin in (list, set, tuple):
        return []
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return _minimal(annotation).model_dump()
        if issubclass(annotation, bool):
            return False
        if issubclass(annotation, (int, float)):
            return 0
        if issubclass(annotation, str):
            return ""
    return None


_HANDLERS = {
    "KnowledgeGraphDraft": _graph,
    "LessonDraft": _lesson,
    "TeachingPlan": _plan,
    "LessonV2Draft": _lesson_v2,
    "LessonReview": _review,
    "CapstoneRepair": lambda _text, _prefix: CapstoneRepair(capstone=_capstone()),
    "QuestionRepair": lambda text, prefix: QuestionRepair(question=_repaired_question(text, prefix)),
    "VisualizationPlan": _viz,
    "IndependentAnswer": _independent,
    "TopUpDraft": _top_up,
    "PageTranscription": _transcription,
}
