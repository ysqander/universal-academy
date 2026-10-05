"""Structured-output contracts for every LLM call in the pipeline.

These models are passed to OpenAI Structured Outputs (strict JSON schema), so:
- every field is required (no defaults); optional values are expressed as `X | None`,
- no free-form dicts; only lists of typed objects,
- descriptions double as instructions to the model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Stage 1 — page transcription (vision)
# ---------------------------------------------------------------------------

ItemKind = Literal[
    "definition",
    "theorem",
    "proposition",
    "lemma",
    "corollary",
    "example",
    "notation",
    "remark",
    "exercise",
    "proof",
    "other",
]


class FigureRef(BaseModel):
    mark: str | None = Field(
        description="Label of the red candidate box (e.g. 'M2') that contains this figure, or null if the figure is not marked."
    )
    bbox: list[float] | None = Field(
        description="Only when mark is null: [x0, y0, x1, y1] of the figure as fractions (0-1) of page width/height."
    )
    caption: str = Field(description="Caption printed next to the figure, verbatim; empty string if none.")
    description: str = Field(description="1-2 sentences: what the figure shows and what a learner should notice.")


class BoxedItem(BaseModel):
    ref: str = Field(description="Number exactly as printed, e.g. '1.3'; empty string if unnumbered.")
    kind: ItemKind
    title: str = Field(description="Short descriptive name printed with the item, e.g. 'properties of complex arithmetic'.")


class PageTranscription(BaseModel):
    markdown: str = Field(
        description=(
            "Faithful Markdown transcription of the page body in reading order. Math as LaTeX: inline $...$, display $$...$$. "
            "Boxed items as blockquotes starting with '> **1.3 Theorem — title**'. Margin notes as '> *Margin note:* ...'. "
            "Insert a placeholder line '[[figure:M2]]' (or '[[figure:new1]]' for unmarked figures, numbered in order) where each figure sits. "
            "Omit running headers, footers and page numbers."
        )
    )
    figures: list[FigureRef]
    rejected_marks: list[str] = Field(
        description="Candidate box labels that are NOT figures (text boxes, definition/theorem boxes, rules, decorations)."
    )
    items: list[BoxedItem] = Field(description="Numbered definitions/theorems/examples/exercises appearing on this page.")
    is_exercise_page: bool = Field(description="True if the page is mainly an end-of-section exercise list.")
    uncertain: list[str] = Field(description="Anything you could not read reliably (equations, labels). Empty if none.")


# ---------------------------------------------------------------------------
# Stage 2 — knowledge graph
# ---------------------------------------------------------------------------

SkillKind = Literal["concept", "definition", "procedure", "computation", "distinction", "interpretation", "proof"]


class PrereqLink(BaseModel):
    skill: str = Field(description="Key of the prerequisite skill in this chapter, e.g. 'S3'.")
    weight: float = Field(
        description=(
            "Encompassing weight 0-1: how much of the prerequisite is implicitly practised when doing this skill. "
            "1.0 = every problem of this skill fully exercises the prerequisite; 0.2 = only background familiarity needed."
        )
    )


class SkillDraft(BaseModel):
    key: str = Field(description="Stable key 'S1', 'S2', ... in recommended learning order.")
    title: str = Field(description="Short name (max ~60 chars) naming the ability, not the book heading.")
    objective: str = Field(
        description="One testable ability starting with a verb: Calculate / Distinguish / Explain / Decide / Prove / Interpret / Apply ..."
    )
    kind: SkillKind
    summary: str = Field(description="1-2 sentences: what this is and why it matters at this point in the chapter.")
    source_pages: list[int] = Field(description="PDF page indexes (from the <<page N>> markers) where this is taught.")
    source_refs: list[str] = Field(description="Printed item numbers this skill draws on, e.g. ['1.1', '1.2'].")
    figure_ids: list[str] = Field(description="Ids of book figures (from the figure list) that help teach this skill.")
    prerequisites: list[PrereqLink] = Field(
        description="Direct prerequisites INSIDE this chapter only. An edge means 'needed before', not 'related'."
    )
    foundations: list[str] = Field(description="Keys of assumed-knowledge items (A1, A2, ...) this skill relies on.")
    difficulty: int = Field(description="1 (very easy) to 5 (hard).")
    est_minutes: int = Field(description="Minutes for a focused learner to learn it: lesson + practice (typically 5-20).")
    misconceptions: list[str] = Field(description="Specific confusions learners commonly have with this skill.")


class Foundation(BaseModel):
    key: str = Field(description="'A1', 'A2', ...")
    title: str
    description: str = Field(description="What exactly is assumed, in one or two sentences.")
    check_question: str = Field(description="A quick self-check question (Markdown + LaTeX).")
    check_answer: str = Field(description="Its answer with a one-line explanation.")


class KnowledgeGraphDraft(BaseModel):
    chapter_title: str
    big_picture: str = Field(
        description="3-5 sentences for the learner: what problem this chapter solves, why it matters, and where it leads."
    )
    foundations: list[Foundation] = Field(description="Knowledge assumed from outside the selected pages (keep it short).")
    skills: list[SkillDraft]
    excluded: list[str] = Field(description="Content deliberately left out (e.g. digressions) with a short reason.")


# ---------------------------------------------------------------------------
# Stage 3 — lesson package per skill
# ---------------------------------------------------------------------------

QuestionKind = Literal["multiple_choice", "true_false", "numeric", "expression", "free_response"]


class NotationItem(BaseModel):
    symbol: str = Field(description="LaTeX, e.g. '$\\mathbf{F}^n$'.")
    meaning: str


class WorkedStep(BaseModel):
    math: str = Field(description="The step (Markdown + LaTeX).")
    why: str = Field(description="Why this step is valid / what was done.")


class WorkedExample(BaseModel):
    problem: str
    steps: list[WorkedStep]
    takeaway: str = Field(description="One sentence: the pattern to remember.")


class QuestionDraft(BaseModel):
    key: str = Field(description="Unique within the lesson: 'k1q1', 'k1q2', ... (knowledge point number + question number).")
    tier: int = Field(
        description="1 = accessible first application, 2 = independent application, 3 = conceptual / changed context / transfer."
    )
    kind: QuestionKind
    prompt: str = Field(description="Markdown + LaTeX. Must be fully self-contained.")
    choices: list[str] = Field(
        description="multiple_choice: 3-5 options (Markdown + LaTeX). true_false: exactly ['True', 'False']. Otherwise []."
    )
    correct_choice: int | None = Field(description="0-based index of the correct choice for multiple_choice/true_false; else null.")
    answer: str = Field(
        description=(
            "Machine-checkable answer. numeric: a plain number ('-3.5', '2/3'). expression: SymPy syntax using i for the "
            "imaginary unit, e.g. '-7+22*i', 'sqrt(2)/2', or a tuple '(1, -2, 3)'. Other kinds: ''."
        )
    )
    answer_display: str = Field(description="The correct answer formatted for display (Markdown + LaTeX).")
    tolerance: float | None = Field(description="Absolute tolerance for numeric answers (e.g. 0.01); null otherwise.")
    hints: list[str] = Field(description="Exactly 3 progressive hints: a pointer, a partial step, then nearly the full solution.")
    solution: str = Field(description="Complete worked solution (Markdown + LaTeX).")
    checklist: list[str] = Field(
        description="free_response only: 2-5 criteria a correct answer must meet (for self-assessment). Otherwise []."
    )
    targets: str = Field(description="Which part of the objective or which misconception this question assesses.")
    source_exercise: str | None = Field(description="If adapted from a book exercise, its reference (e.g. 'Exercise 1A.3'); else null.")


class KnowledgePointDraft(BaseModel):
    title: str = Field(description="Short name of this step of the skill, e.g. 'Multiplying two complex numbers'.")
    explanation: str = Field(description="2-6 sentences (Markdown + LaTeX) introducing just this knowledge point.")
    worked_example: WorkedExample
    questions: list[QuestionDraft] = Field(
        description=(
            "Exactly 4 questions of the same problem type as the worked example, different numbers/objects each, "
            "all at this knowledge point's difficulty. At least 3 machine-checkable (not free_response)."
        )
    )


class LessonDraft(BaseModel):
    why_it_matters: str = Field(
        description="2-4 sentences framing the question this skill answers or the confusion it resolves. No fake real-world fluff."
    )
    idea: str = Field(
        description="The core explanation (Markdown + LaTeX), concise, using the author's notation; quote key definitions faithfully."
    )
    notation: list[NotationItem]
    knowledge_points: list[KnowledgePointDraft] = Field(
        description="2-3 knowledge points of increasing difficulty (e.g. basic case -> general case -> conceptual/transfer)."
    )
    missing_steps: str = Field(description="Optional deeper detail / omitted justifications, shown behind 'Show the missing steps'.")
    common_mistakes: list[str]
    connection: str = Field(description="1-2 sentences: what this enables next / how it connects to earlier skills.")
    source_refs: list[str]


# ---------------------------------------------------------------------------
# Stage 4 — visualization planner (separate, deliberately constrained step)
# ---------------------------------------------------------------------------

VizColor = Literal["primary", "accent", "muted", "danger"]
VizFormat = Literal["none", "book_figure", "comparison_table", "equation_steps", "diagram_2d", "function_plot", "frames"]


class VizVector(BaseModel):
    start: list[float] = Field(description="[x, y]")
    end: list[float] = Field(description="[x, y]")
    label: str = Field(description="Short label; may contain inline LaTeX like '$u+v$'.")
    color: VizColor


class VizPoint(BaseModel):
    at: list[float] = Field(description="[x, y]")
    label: str
    color: VizColor


class VizSegment(BaseModel):
    start: list[float]
    end: list[float]
    dashed: bool
    label: str


class VizDiagram(BaseModel):
    x_range: list[float] = Field(description="[xmin, xmax]")
    y_range: list[float] = Field(description="[ymin, ymax]")
    show_grid: bool
    axis_labels: list[str] = Field(description="[x label, y label], e.g. ['Re', 'Im']; [] for none.")
    vectors: list[VizVector]
    points: list[VizPoint]
    segments: list[VizSegment]


class VizFunction(BaseModel):
    expr: str = Field(description="SymPy expression in x (and optionally the slider parameter name), e.g. 'a*x**2'.")
    label: str


class VizParameter(BaseModel):
    name: str
    min: float
    max: float
    step: float
    initial: float


class VizPlot(BaseModel):
    x_range: list[float]
    y_range: list[float] | None
    functions: list[VizFunction]
    parameter: VizParameter | None = Field(description="Only if moving it answers the learner prompt; otherwise null.")


class VizTable(BaseModel):
    headers: list[str]
    rows: list[list[str]]


class VizEqStep(BaseModel):
    latex: str = Field(description="One display equation; highlight the changed part with \\color{#d9480f}{...}.")
    note: str


class VizFrame(BaseModel):
    title: str
    diagram: VizDiagram | None
    latex: str | None
    note: str


class VisualizationPlan(BaseModel):
    misunderstanding: str = Field(description="What a learner is most likely to misunderstand in this skill.")
    purpose: str = Field(description="What the visual must make clear (one sentence).")
    format: VizFormat
    rationale: str = Field(description="Why this format beats the alternatives here (one or two sentences).")
    book_figure_id: str | None = Field(description="Figure id if format is book_figure (or if a book figure accompanies another format).")
    learner_prompt: str = Field(description="What the learner should notice, phrased as a question or instruction.")
    caption: str
    table: VizTable | None
    steps: list[VizEqStep]
    diagram: VizDiagram | None
    plot: VizPlot | None
    frames: list[VizFrame]


# ---------------------------------------------------------------------------
# Stage 5 — independent verification of machine-checkable questions
# ---------------------------------------------------------------------------


class IndependentAnswer(BaseModel):
    choice: int | None = Field(description="0-based index for multiple_choice/true_false, else null.")
    answer: str = Field(description="For numeric/expression: the answer in SymPy syntax (i = imaginary unit); else ''.")
    confidence: float = Field(description="0-1")
    problems: str = Field(description="Any defect in the question itself (ambiguous, unsolvable, multiple correct choices); '' if none.")
