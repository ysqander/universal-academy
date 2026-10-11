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
    # Set on an import that found the book already on file: the same file, or the same book saved again (same text).
    already_in_library: Literal["identical_file", "same_text"] | None = None
    # The PDF is missing or damaged in the books folder (not one of the files imported as this book): uploading the
    # same file again restores it.
    pdf_missing: bool = False


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
    quiz_due: bool = False  # a (forced) quiz must be taken before new lessons or reviews
    xp_until_quiz: int | None = None  # XP still to earn before the next quiz; None when not enough skills learned yet
    last_quiz_score: float | None = None


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
    cooldown_until: str | None = None  # a halted lesson comes back then (ISO UTC); earlier starts need force
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


QuestionKindT = Literal["multiple_choice", "true_false", "true_false_set", "numeric", "expression", "free_response", "multistep"]


class MultistepStepPublic(BaseModel):
    index: int
    prompt: str
    kind: Literal["multiple_choice", "true_false", "true_false_set", "numeric", "expression"]
    choices: list[str]
    # One hint per step; using it (POST /api/questions/{qid}/hint?step=i) halves the step's point. Its text is sent
    # only once it was used (has_hint says whether there is one); a step after the current one is sent without its
    # prompt, choices or hint (only its index and kind), so a later step can never give an earlier answer away.
    hint: str
    has_hint: bool = False


class StepResult(BaseModel):
    index: int
    correct: bool
    score: float  # 1 independent, 0.5 with the hint, 0 wrong
    feedback: str
    answer_display: str
    explanation: str
    revealed: bool = False  # the step's answer was shown (Show solution): 0 points
    hinted: bool = False  # the step's hint was used: at most ½ point


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
    hints: list[str]  # the hints already shown for the current attempt (hints_used of them); the next comes from POST /hint
    hint_count: int = 0  # how many hints the question has
    checklist: list[str]  # free_response self-assessment criteria (shown after submission)
    source_exercise: str | None = None
    flagged: bool = False
    hints_used: int = 0  # hints already shown for the current attempt (server-recorded; survives reloads)
    revealed: bool = False  # the solution was revealed for the current attempt (server-recorded)
    lesson_looked: bool = False  # review: the learner opened the lesson on this question (counts as a hint)
    committed_response: str | None = None  # free_response: the answer committed before a comparison reveal
    role: Literal["practice", "capstone"] = "practice"  # capstone = the lesson's final multistep problem
    steps: list[MultistepStepPublic] = Field(default_factory=list)  # multistep only
    step_index: int = 0  # multistep: the step to answer next in the current attempt (server-recorded)
    step_results: list[StepResult] = Field(default_factory=list)  # multistep: steps already answered (for reloads)


class KnowledgePoint(BaseModel):
    index: int
    target_questions: int = 2  # questions the learner answers to demonstrate this KP
    title: str
    explanation: str
    worked_example: WorkedExample
    question_count: int


class GuidedCheckState(BaseModel):
    """The server's record of a scored inline check (a proof lesson's guided steps, SPEC §6.3b). The answer, its
    explanation and the hint are sent only when the learner may see them."""

    status: Literal["open", "retry", "done"]  # retry: the first answer was wrong; one more try for ½ point
    tries: int = 0  # answers given (an unreadable answer is not a try)
    first_response: str | None = None  # the wrong first answer (retry / done), as typed (choice: the option index)
    response: str | None = None  # done: the answer that ended the check (None: the answer was shown instead)
    correct: bool | None = None  # done: whether that answer was right (None: shown without an answer)
    points: float | None = None  # done: 1 first try / 0.5 second try / 0 (wrong twice, or "Show answer")
    revealed: bool = False  # done through "Show answer"
    feedback: str = ""  # the grader's message for the last answer (a "not yet" never names the wrong statements)
    hint: str = ""  # sent once the first answer was wrong
    answer_display: str = ""  # done only
    explanation: str = ""  # done only
    voided: bool = False  # taken out of the score (a key found wrong on review); a learner's report alone never does it
    reported: bool = False  # the learner reported the key as wrong: kept for review, the score is unchanged
    scored: bool = True  # False on a retry of the lesson (the check is shown with its answer, not scored again)


class InlineCheckPublic(BaseModel):
    """An inline check inside a v2 teaching sequence. Formative (answered via POST /api/skills/{sid}/blocks/{i}/check,
    never scored) unless `scored`: a proof lesson's guided step, answered via POST /api/tasks/{tid}/blocks/{i}/check
    and counted toward the lesson's score (SPEC §6.3b)."""

    kind: Literal["numeric", "expression", "multiple_choice", "true_false", "true_false_set", "reflect"]
    prompt: str
    choices: list[str]
    scored: bool = False
    second_try: bool = False  # scored: a wrong first answer gets one more try for ½ point (typed answers, statement sets)
    state: GuidedCheckState | None = None  # the server's record (None when the check is not scored)


class TeachingBlockView(BaseModel):
    index: int  # 0-based position in the sequence (used by the check/feedback endpoints)
    kind: Literal["setup", "explain", "demonstrate", "check", "faded_example", "generalize", "summary"]
    heading: str | None = None
    body: str
    visualization: Visualization | None = None
    check: InlineCheckPublic | None = None


class Lesson(BaseModel):
    skill_id: str
    skill_key: str
    title: str
    objective: str
    format: Literal["v1", "v2"] = "v1"  # v2 = planned teaching sequence (blocks) instead of the fixed sections
    version: str = ""  # hash of the stored lesson; sent back with inline checks / block feedback (409 when stale)
    # v2 proof lessons: "scored" = the inline checks are scored guided steps; the blocks after the first open one are
    # not sent yet (blocks_total counts them). "retry" = a later attempt: the checks are shown answered, not scored.
    guided: Literal["scored", "retry"] | None = None
    blocks_total: int = 0  # v2: every block of the lesson, including those not sent yet
    goal_line: str = ""  # v2
    blocks: list[TeachingBlockView] = Field(default_factory=list)  # v2
    assumed: list[str] = Field(default_factory=list)  # v2: prerequisites the lesson treats as learned
    out_of_scope: list[str] = Field(default_factory=list)  # v2: shown as "Not in this lesson"
    deeper: str = ""  # v2: optional "Go deeper" (never needed for the objective)
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

TaskType = Literal["lesson", "review", "quiz"]
TaskPhase = Literal["learn", "practice", "done"]
TaskOutcome = Literal["passed", "failed", "abandoned"]


class TaskProgress(BaseModel):
    """Lesson (SPEC §6.3): each knowledge point serves its target number of questions (+1 after a miss), then the
    multistep capstone; points 1 independent / 0.5 with a hint / 0 wrong or revealed; pass at score >= pass_mark.
    Review: `answered`/`total_questions` over a short mixed set, same points and pass mark.
    streak / needed_in_row are legacy (kept for older clients)."""

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
    kp_target: int = 0  # questions planned for the current KP (lesson)
    points: float = 0.0  # points earned so far in this task
    max_points: float = 0.0  # points possible for the questions answered so far
    score: float = 0.0  # points / max_points (0-1)
    planned: int = 0  # questions planned for the whole task (lesson: sum of KP targets + capstone)
    pass_mark: float = 0.8
    in_capstone: bool = False  # the lesson's final multistep problem is being answered
    has_capstone: bool = False  # the task ends with (or includes) a capstone
    halted: bool = False  # the lesson stopped early (80 % became unreachable with planned questions left)
    # Proof lessons with scored guided steps (SPEC §6.3b): the steps count like questions (points, answered, planned),
    # then one variant problem (the capstone slot, weight 2) ends the lesson.
    guided_total: int = 0  # scored guided steps in this attempt (0: none, or a retry where they are not scored)
    guided_done: int = 0  # of them, answered (or shown)
    in_guided: bool = False  # guided steps are still open: the practice can't start yet
    variant: bool = False  # the final problem is the proof lesson's variant (same idea, something changes)
    best_possible: float = 0.0  # the score if everything still planned is answered right (lessons only; 0 otherwise)
    suggestion_text: str = ""  # a finished lesson that failed: what to do next (e.g. prerequisites made due)


class QuizQuestionResult(BaseModel):
    """One quiz question after the quiz has finished (nothing about correctness is shown before)."""

    question_id: str
    skill: SkillRef
    kind: QuestionKindT
    prompt: str
    choices: list[str]
    response: str | None  # what the learner answered (choice text for choice questions); None when passed / unanswered
    answered: bool  # False: passed, or time ran out
    score: float  # 1 correct, 0 wrong / passed / unanswered
    correct_answer: str
    solution: str
    seconds: float


class QuizInfo(BaseModel):
    """SPEC §6.7 — a timed, cumulative check every ~150 XP (forced); a retake is optional."""

    kind: Literal["quiz", "retake"]
    question_count: int
    time_limit_seconds: int
    started_at: str | None  # None until the learner starts the clock (POST /practice)
    deadline: str | None  # ISO UTC; answers after it are not accepted
    seconds_left: int | None  # server-computed when the response was built
    skills: list[SkillRef]
    results: list[QuizQuestionResult] = Field(default_factory=list)  # filled once the quiz is finished
    missed_skills: list[SkillRef] = Field(default_factory=list)  # reviewed straight away after the quiz
    retake_available: bool = False


class Task(BaseModel):
    id: str
    course_id: str
    type: TaskType
    phase: TaskPhase
    skill: SkillRef | None  # None for quizzes (they span several skills — see quiz.skills)
    reason: str  # transparent "why this task now", e.g. "Review due — retention estimated at 82%"
    xp_value: int
    lesson: Lesson | None = None  # included for type == "lesson"
    question: QuestionPublic | None = None  # the current question (practice phase / review)
    progress: TaskProgress
    outcome: TaskOutcome | None = None
    cooldown_until: str | None = None  # failed lesson: when it comes back (ISO UTC); None = no pause (e.g. after a flag)
    quiz: QuizInfo | None = None  # type == "quiz"


class SessionInfo(BaseModel):
    xp_today: int
    daily_goal: int
    goal_reached: bool
    due_reviews: int
    quiz_due: bool = False  # the next task is a forced quiz (after any active task)
    xp_until_quiz: int | None = None


class TaskOption(BaseModel):
    """One entry of the Math-Academy-style task menu (up to 5). A due quiz is the only entry."""

    skill: SkillRef | None  # None for a quiz
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
    step: int | None = None  # multistep: index of the step being answered (one submission per step)
    skip: bool = False  # quiz only: pass on this question (0 points)


class AnswerResult(BaseModel):
    correct: bool
    # "step": a multistep step, more to come; "recorded": a quiz answer was saved — correctness is shown at the end
    status: Literal["correct", "incorrect", "self_graded", "voided", "step", "recorded"]
    independent: bool  # counted toward passing (no hints, no reveal)
    feedback: str  # short message, e.g. "Equivalent to the expected answer." / "Close: check the sign of the imaginary part."
    correct_answer: str  # answer_display
    correct_choice: int | None = None  # multiple_choice / true_false: index of the right option
    solution: str
    task: Task  # updated task (next question, progress, phase/outcome)
    xp_awarded: int = 0
    suggestion: Literal["easier_step", "review_prerequisite", "reread_example", "retry_later"] | None = None
    suggestion_text: str | None = None
    score: float = 0.0  # points earned for this question (1 / 0.5 / 0; multistep: mean of its steps)
    step_result: StepResult | None = None  # multistep: the step just answered
    question_done: bool = True  # False while a multistep question still has steps to answer
    note: str = ""  # the engine's progress note (extra question after a miss, capstone next, …); also in feedback


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


class BlockCheckRequest(BaseModel):
    """POST /api/skills/{sid}/blocks/{index}/check — answer an inline check (formative; nothing is recorded)."""

    response: str | None = None  # numeric / expression / true_false_set ('TFT')
    choice: int | None = None  # multiple_choice / true_false
    version: str | None = None  # Lesson.version the learner is looking at; 409 when the lesson changed since


class BlockCheckResult(BaseModel):
    correct: bool | None  # None for 'reflect' checks (self-assessed)
    feedback: str
    answer_display: str
    explanation: str


class GuidedRevealRequest(BaseModel):
    """POST /api/tasks/{tid}/blocks/{index}/reveal — show a scored check's answer (the check then earns 0)."""

    version: str | None = None  # Lesson.version (required: 409 when missing or stale)


class GuidedCheckResult(BaseModel):
    """POST /api/tasks/{tid}/blocks/{index}/check (body: BlockCheckRequest, version required) and .../reveal."""

    check: GuidedCheckState
    final: bool  # the check is done (its points are decided)
    task: Task  # updated task: progress, and the lesson with the next blocks once the check is done


class BlockFeedbackRequest(BaseModel):
    """POST /api/skills/{sid}/blocks/{index}/feedback — "This step isn't clear"."""

    kind: Literal["unclear"] = "unclear"
    note: str = ""
    version: str | None = None  # Lesson.version; 409 when the lesson changed since


class FoundationKnownRequest(BaseModel):
    known: bool


class Health(BaseModel):
    openai: bool
    anthropic: bool = False
    provider: str = "openai"  # UA_LLM_PROVIDER (a role may override it: see models)
    llm_ready: bool = False  # every key a build needs is set (the providers in use + NVIDIA), or the fake LLM answers
    missing_keys: list[str] = []  # e.g. ["ANTHROPIC_API_KEY"]
    nvidia: bool
    fake_llm: bool
    parse_engine: str
    max_course_pages: int = 80
    models: dict[str, str]
    agent_route: bool = False  # UA_AGENT_DIR: model calls are answered by Claude Code agents (/build-course)
    lesson_generation: str = "v2"  # UA_LESSON_GENERATION: the in-app build's lessons (v2, or the cheaper v1)


Visualization.model_rebuild()
Lesson.model_rebuild()


class ExampleCourse(BaseModel):
    """GET /api/examples: a course bundle shipped in the repo's examples/<name>/ (app/bundle.py), studied without a key."""

    name: str  # the folder under examples/
    course_id: str
    title: str  # the course (chapter) title
    book_title: str
    author: str
    description: str = ""
    license: str = ""  # short name, e.g. "CC BY-NC 4.0"
    license_url: str = ""
    skills: int
    imported: bool  # the course is in this library


class ExampleImportResult(BaseModel):
    """POST /api/examples/{name}/import: the course (and its book, unless already there) added with fresh progress."""

    course_id: str
    already_imported: bool  # nothing changed: the course was already in the library
    message: str
