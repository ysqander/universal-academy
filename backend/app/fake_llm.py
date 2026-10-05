"""Deterministic canned LLM outputs (UA_FAKE_LLM=1) for offline tests and UI development.

respond() dispatches on the schema class name. The content is a small, realistic course on complex numbers and
F^n (Axler §1A style). Every generated question carries an invisible Markdown marker
    [//]: # "ua-fake|<choice>|<answer>"
(a link reference definition, which Markdown renderers do not display) so the fake verifier can agree with it.
Unknown schemas get a minimal valid instance.
"""

from __future__ import annotations

import re
import types
import zlib
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from .llm_schemas import (
    Foundation,
    IndependentAnswer,
    KnowledgeGraphDraft,
    KnowledgePointDraft,
    LessonDraft,
    NotationItem,
    PageTranscription,
    PrereqLink,
    QuestionDraft,
    SkillDraft,
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
    }
    return QuestionDraft(**(defaults | fields))


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
                kind="true_false",
                prompt="True or false: for all real $a,b$, $(a+bi)(a-bi)=a^2+b^2$." + _marker(0, ""),
                choices=["True", "False"],
                correct_choice=0,
                answer_display="True",
                hints=["Expand the product.", "The cross terms $-abi$ and $abi$ cancel.", "$-b^2i^2=b^2$."],
                solution="$(a+bi)(a-bi)=a^2-abi+abi-b^2i^2=a^2+b^2$.",
                targets="using $i^2=-1$",
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
                kind="true_false",
                prompt="True or false: $(1,2)+(3,4,5)$ is defined in $\\mathbf{R}^n$ for some $n$." + _marker(1, ""),
                choices=["True", "False"],
                correct_choice=1,
                answer_display="False",
                hints=["Addition is defined in $\\mathbf{F}^n$ for one fixed $n$.", "The lists have lengths 2 and 3.", "So the sum is not defined."],
                solution="Addition is only defined for two lists of the same length $n$; here the lengths differ.",
                targets="lengths must match",
            ),
            _question(
                key="k2q5",
                tier=3,
                kind="free_response",
                prompt="Prove that $x+y=y+x$ for all $x,y\\in\\mathbf{F}^n$.",
                answer_display="See the model proof.",
                hints=["Write $x=(x_1,\\dots,x_n)$ and $y=(y_1,\\dots,y_n)$.", "Use the definition of addition.", "Use commutativity in $\\mathbf{F}$ in each coordinate."],
                solution=(
                    "Let $x=(x_1,\\dots,x_n)$, $y=(y_1,\\dots,y_n)$. Then $x+y=(x_1+y_1,\\dots,x_n+y_n)"
                    "=(y_1+x_1,\\dots,y_n+x_n)=y+x$, using commutativity of addition in $\\mathbf{F}$."
                ),
                checklist=[
                    "Writes $x$ and $y$ with arbitrary coordinates",
                    "Applies the definition of addition in $\\mathbf{F}^n$",
                    "Uses commutativity in $\\mathbf{F}$ coordinate by coordinate",
                ],
                targets="proof from the definition",
                source_exercise="Exercise 1A.10",
            ),
        ],
    )


def _proof_question(key: str) -> QuestionDraft:
    return _question(
        key=key,
        tier=2,
        kind="free_response",
        prompt="Prove that $\\alpha\\beta=\\beta\\alpha$ for all $\\alpha,\\beta\\in\\mathbf{C}$, starting from the definition of multiplication.",
        answer_display="See the model proof.",
        hints=[
            "Write $\\alpha=a+bi$ and $\\beta=c+di$ with $a,b,c,d\\in\\mathbf{R}$.",
            "Compute both products with the definition.",
            "Compare real and imaginary parts using commutativity in $\\mathbf{R}$.",
        ],
        solution=(
            "Let $\\alpha=a+bi$, $\\beta=c+di$. Then $\\alpha\\beta=(ac-bd)+(ad+bc)i$ and "
            "$\\beta\\alpha=(ca-db)+(cb+da)i$. Since multiplication and addition of real numbers are commutative, "
            "$ac-bd=ca-db$ and $ad+bc=cb+da$, so $\\alpha\\beta=\\beta\\alpha$."
        ),
        checklist=[
            "Takes arbitrary $\\alpha=a+bi$ and $\\beta=c+di$ (not an example)",
            "Computes both products from the definition",
            "Uses commutativity of real addition and multiplication part by part",
        ],
        targets="proof from the definition, without assuming the result",
        source_exercise=None,
    )


def _lesson(text: str, _prefix: str) -> LessonDraft:
    key, title = _skill_from(text)
    seed = _seed(key, title)
    proof = bool(_PROOF_RE.search(text))
    figures = [fid for fid, _ in _FIGURE_RE.findall(text)]
    first_kp = _kp_complex(seed)
    if proof:  # proof skills: every knowledge point carries a free-response proof, served as its 2nd question
        first_kp.questions.insert(1, _proof_question("k1q5"))
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
        knowledge_points=[first_kp, _kp_vectors(seed)],
        missing_steps=(
            "Why $i^2=-1$ follows from the definition: $(0+1i)(0+1i)=(0\\cdot0-1\\cdot1)+(0\\cdot1+1\\cdot0)i=-1$."
        ),
        common_mistakes=["Dropping the minus sign from $i^2$", "Adding lists of different lengths"],
        connection="Coordinatewise operations on $\\mathbf{F}^n$ are the model for the abstract vector space in 1B.",
        # the last two are the kind of caption/page note real models emit: the pipeline keeps item numbers only
        source_refs=["1.1", "1.3", "1.13", "1.13 Definition — addition in F^n", "Printed p.2: complex numbers"],
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
    m = _MARKER_RE.search(text)
    if m is None:
        return IndependentAnswer(choice=None, answer="", confidence=0.3, problems="Could not determine an answer.")
    choice = int(m.group(1)) if m.group(1) else None
    return IndependentAnswer(choice=choice, answer=m.group(2), confidence=0.95, problems="")


class _TopUp(BaseModel):
    questions: list[QuestionDraft]


def _top_up(text: str, _prefix: str) -> _TopUp:
    keys = re.findall(r"\bk\d+q\d+\b", text.split("\n", 1)[0]) or ["k1q9"]
    questions = []
    if _PROOF_RE.search(text) and len(keys) > 1:
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
    "VisualizationPlan": _viz,
    "IndependentAnswer": _independent,
    "TopUpDraft": _top_up,
    "PageTranscription": _transcription,
}
