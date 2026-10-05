"""HTTP API contracts (backend <-> frontend). Mirrored 1:1 in frontend/src/types.ts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .llm_schemas import NotationItem, VizDiagram, VizEqStep, VizFormat, VizParameter, VizTable, WorkedExample

# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------


class OutlineNode(BaseModel):
    id: str  # stable within the book, e.g. "n12"
    title: str
    level: int  # 1 = chapter / top level
    start_page: int  # 0-based PDF page index, inclusive
    end_page: int  # 0-based PDF page index, inclusive
    label_start: str  # printed page label, e.g. "1" or "xiv"
    label_end: str
    children: list[OutlineNode] = Field(default_factory=list)


class BookSummary(BaseModel):
    id: str
    title: str
    author: str
    page_count: int
    created_at: str
    course_count: int = 0


class Book(BookSummary):
    outline: list[OutlineNode]
    outline_source: Literal["pdf", "detected", "none"]
    has_text_layer: bool


class ImportPathRequest(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Courses + build job
# ---------------------------------------------------------------------------

JobStageKey = Literal["render", "parse", "graph", "lessons", "visuals", "verify", "finalize"]
StageStatus = Literal["pending", "running", "done", "failed", "skipped"]
JobState = Literal["queued", "running", "done", "failed", "interrupted"]
CourseStatus = Literal["building", "ready", "failed"]


class CourseCreate(BaseModel):
    book_id: str
    title: str
    start_page: int  # 0-based inclusive
    end_page: int  # 0-based inclusive
    sections: list[str] = Field(default_factory=list)  # titles of selected outline nodes, for display/prompting


class JobStage(BaseModel):
    key: JobStageKey
    label: str
    status: StageStatus
    done: int = 0
    total: int = 0
    message: str = ""


class JobStatus(BaseModel):
    course_id: str
    state: JobState
    stages: list[JobStage]
    error: str | None = None
    log: list[str] = Field(default_factory=list)  # last ~50 human-readable log lines
    updated_at: str


class CourseStats(BaseModel):
    total_skills: int = 0
    learned: int = 0  # learned + mastered
    mastered: int = 0
    available: int = 0
    in_progress: int = 0
    locked: int = 0
    due_reviews: int = 0
    lessons_ready: int = 0
    xp_today: int = 0
    xp_total: int = 0
    daily_goal: int = 30
    streak_days: int = 0
    next_review_at: str | None = None


class CourseSummary(BaseModel):
    id: str
    book_id: str
    book_title: str
    title: str
    status: CourseStatus
    start_page: int
    end_page: int
    label_start: str
    label_end: str
    created_at: str
    stats: CourseStats


class Course(CourseSummary):
    sections: list[str]
    big_picture: str = ""
    excluded: list[str] = Field(default_factory=list)
    job: JobStatus | None = None


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

SkillStatus = Literal["locked", "available", "in_progress", "learned", "mastered"]
LessonStatus = Literal["pending", "ready", "failed"]


class SkillNode(BaseModel):
    id: str
    key: str  # "S1"
    title: str
    objective: str
    kind: str
    summary: str
    difficulty: int
    est_minutes: int
    order_index: int
    source_pages: list[int]
    page_labels: list[str]
    source_refs: list[str]
    figure_ids: list[str]
    foundations: list[str]
    misconceptions: list[str]
    lesson_status: LessonStatus
    status: SkillStatus
    due: bool = False
    due_at: str | None = None
    retrievability: float | None = None  # 0-1, None if never learned
    stability_days: float | None = None
    reviews: int = 0


class SkillEdge(BaseModel):
    source: str  # prerequisite skill id
    target: str  # dependent skill id
    weight: float  # encompassing weight 0-1
    reduced: bool  # True if kept by transitive reduction (draw these; others are implied)


class FoundationNode(BaseModel):
    key: str
    title: str
    description: str
    check_question: str
    check_answer: str
    known: bool | None = None  # None = not yet answered


class CourseGraph(BaseModel):
    course_id: str
    skills: list[SkillNode]
    edges: list[SkillEdge]
    foundations: list[FoundationNode]


# ---------------------------------------------------------------------------
# Lessons, figures, source passages
# ---------------------------------------------------------------------------


class Figure(BaseModel):
    id: str
    page: int
    page_label: str
    caption: str
    description: str
    url: str  # /api/courses/{cid}/figures/{fid}.png


class PlotCurve(BaseModel):
    label: str
    param_value: float | None  # None when there is no slider parameter
    points: list[list[float]]  # [[x, y], ...]; breaks in the curve are encoded as separate curves


class Visualization(BaseModel):
    format: VizFormat
    misunderstanding: str
    purpose: str
    rationale: str
    learner_prompt: str
    caption: str
    figure: Figure | None = None
    table: VizTable | None = None
    steps: list[VizEqStep] = Field(default_factory=list)
    diagram: VizDiagram | None = None
    # function_plot is pre-sampled on the backend (safe: no expression evaluation in the browser)
    plot_x_range: list[float] | None = None
    plot_y_range: list[float] | None = None
    plot_parameter: VizParameter | None = None
    plot_curves: list[PlotCurve] = Field(default_factory=list)
    frames: list[VisualizationFrame] = Field(default_factory=list)


class VisualizationFrame(BaseModel):
    title: str
    diagram: VizDiagram | None = None
    latex: str | None = None
    note: str = ""


QuestionKindT = Literal["multiple_choice", "true_false", "numeric", "expression", "free_response"]


class QuestionPublic(BaseModel):
    """A question as shown to the learner — never includes the answer or solution."""

    id: str
    skill_id: str
    key: str
    kp_index: int
    tier: int
    kind: QuestionKindT
    prompt: str
    choices: list[str]
    hints: list[str]  # revealed progressively client-side; usage is reported with the answer
    checklist: list[str]  # free_response self-assessment criteria (shown after submission)
    source_exercise: str | None = None
    flagged: bool = False
    hints_used: int = 0  # hints already shown for the current attempt (server-recorded; survives reloads)
    revealed: bool = False  # the solution was revealed for the current attempt (server-recorded)
    committed_response: str | None = None  # free_response: the answer committed before a comparison reveal


class KnowledgePoint(BaseModel):
    index: int
    title: str
    explanation: str
    worked_example: WorkedExample
    question_count: int


class Lesson(BaseModel):
    skill_id: str
    skill_key: str
    title: str
    objective: str
    why_it_matters: str
    idea: str
    notation: list[NotationItem]
    knowledge_points: list[KnowledgePoint]
    missing_steps: str
    common_mistakes: list[str]
    connection: str
    visualization: Visualization | None
    source_refs: list[str]
    source_pages: list[int]
    page_labels: list[str]
    figures: list[Figure]
    prerequisites: list[SkillRef]
    unlocks: list[SkillRef]
    question_count: int


class SkillRef(BaseModel):
    id: str
    key: str
    title: str
    status: SkillStatus


class SourcePage(BaseModel):
    page: int
    label: str
    markdown: str
    image_url: str  # /api/books/{bid}/pages/{page}.png?dpi=...
    uncertain: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Study loop
# ---------------------------------------------------------------------------

TaskType = Literal["lesson", "review"]
TaskPhase = Literal["learn", "practice", "done"]
TaskOutcome = Literal["passed", "failed", "abandoned"]


class TaskProgress(BaseModel):
    """Lesson: per knowledge point, 2 independent correct answers in a row pass the KP (Math Academy rule).
    Review: `answered`/`total_questions` over a short mixed set."""

    kp_index: int  # current knowledge point (lesson) — 0 for reviews
    kp_total: int  # number of knowledge points (lesson) — 1 for reviews
    streak: int  # independent correct answers in a row within the current KP
    needed_in_row: int  # 2
    kp_questions_used: int  # questions served in the current KP
    kp_questions_available: int  # pool size of the current KP (non-flagged)
    answered: int  # total answered in this task (excl. voided)
    correct: int  # total correct in this task
    total_questions: int  # reviews: questions in the set; lessons: 0 (open-ended)
    kp_wrong: int = 0  # wrong answers so far in the current KP (lesson)
    easier_available: bool = False  # POST /easier would succeed now (after a miss, and an easier question exists)


class Task(BaseModel):
    id: str
    course_id: str
    type: TaskType
    phase: TaskPhase
    skill: SkillRef
    reason: str  # transparent "why this task now", e.g. "Review due — retention estimated at 82%"
    xp_value: int
    lesson: Lesson | None = None  # included for type == "lesson"
    question: QuestionPublic | None = None  # the current question (practice phase / review)
    progress: TaskProgress
    outcome: TaskOutcome | None = None


class SessionInfo(BaseModel):
    xp_today: int
    daily_goal: int
    goal_reached: bool
    due_reviews: int


class TaskOption(BaseModel):
    """One entry of the Math-Academy-style task menu (up to 5)."""

    skill: SkillRef
    type: TaskType
    reason: str
    xp_value: int
    importance: float


class NextTaskResponse(BaseModel):
    task: Task | None  # the currently active (unfinished) task, if any — GET /next has no side effects
    options: list[TaskOption]  # ranked menu (excluding `task`)
    session: SessionInfo
    message: str  # e.g. "All caught up — next review due tomorrow at 09:12"


class StartTaskRequest(BaseModel):
    skill_id: str
    type: TaskType | None = None  # None -> lesson if not learned yet, else review
    replace_active: bool = False  # another task is active: 409 unless True (then it is abandoned)
    force: bool = False  # learn a locked skill anyway (learner override of prerequisite gating)


SelfGrade = Literal["correct", "partial", "incorrect"]


class AnswerSubmit(BaseModel):
    question_id: str
    choice: int | None = None
    response: str | None = None
    self_grade: SelfGrade | None = None  # free_response only
    hints_used: int = 0
    revealed_solution: bool = False
    seconds: float = 0.0


class AnswerResult(BaseModel):
    correct: bool
    status: Literal["correct", "incorrect", "self_graded", "voided"]
    independent: bool  # counted toward passing (no hints, no reveal)
    feedback: str  # short message, e.g. "Equivalent to the expected answer." / "Close: check the sign of the imaginary part."
    correct_answer: str  # answer_display
    correct_choice: int | None = None  # multiple_choice / true_false: index of the right option
    solution: str
    task: Task  # updated task (next question, progress, phase/outcome)
    xp_awarded: int = 0
    suggestion: Literal["easier_step", "review_prerequisite", "reread_example", "retry_later"] | None = None
    suggestion_text: str | None = None


class RevealRequest(BaseModel):
    """free_response: the learner's committed answer. When present (non-trivial), the reveal is a comparison for
    self-grading and does not count as assistance; otherwise the current attempt is marked as revealed."""

    response: str | None = None


class RevealResponse(BaseModel):
    solution: str
    correct_answer: str


class HintResponse(BaseModel):
    """POST /api/questions/{qid}/hint — records server-side that the next hint was shown for the current attempt."""

    hint: str
    hints_used: int


class FlagRequest(BaseModel):
    reason: str = ""


class FoundationKnownRequest(BaseModel):
    known: bool


class Health(BaseModel):
    openai: bool
    nvidia: bool
    fake_llm: bool
    parse_engine: str
    max_course_pages: int = 80
    models: dict[str, str]


Visualization.model_rebuild()
Lesson.model_rebuild()
