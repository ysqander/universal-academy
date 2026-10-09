// Mirror of backend/app/schemas.py (+ the visualization pieces of llm_schemas.py). Keep in sync.

// ---------- Books ----------
export interface OutlineNode {
  id: string;
  title: string;
  level: number;
  start_page: number; // 0-based PDF page index, inclusive
  end_page: number; // inclusive
  label_start: string; // printed page label
  label_end: string;
  children: OutlineNode[];
}

export interface BookSummary {
  id: string;
  title: string;
  author: string;
  page_count: number;
  created_at: string;
  course_count: number;
}

export interface Book extends BookSummary {
  outline: OutlineNode[];
  outline_source: "pdf" | "detected" | "none";
  has_text_layer: boolean;
}

// ---------- Courses + build job ----------
export type JobStageKey = "render" | "parse" | "graph" | "lessons" | "visuals" | "verify" | "finalize";
export type StageStatus = "pending" | "running" | "done" | "failed" | "skipped";
export type JobState = "queued" | "running" | "done" | "failed" | "interrupted";
export type CourseStatus = "building" | "ready" | "failed";

export interface CourseCreate {
  book_id: string;
  title: string;
  start_page: number;
  end_page: number;
  sections: string[];
}

export interface JobStage {
  key: JobStageKey;
  label: string;
  status: StageStatus;
  done: number;
  total: number;
  message: string;
}

export interface JobStatus {
  course_id: string;
  state: JobState;
  stages: JobStage[];
  error: string | null;
  log: string[];
  updated_at: string;
}

export interface CourseStats {
  total_skills: number;
  learned: number;
  mastered: number;
  available: number;
  in_progress: number;
  locked: number;
  due_reviews: number;
  lessons_ready: number;
  xp_today: number;
  xp_total: number;
  daily_goal: number;
  streak_days: number;
  next_review_at: string | null;
  quiz_due: boolean; // a forced quiz comes before new lessons or reviews
  xp_until_quiz: number | null; // XP still to earn before the next quiz; null when not enough skills learned yet
  last_quiz_score: number | null;
}

export interface CourseSummary {
  id: string;
  book_id: string;
  book_title: string;
  title: string;
  status: CourseStatus;
  start_page: number;
  end_page: number;
  label_start: string;
  label_end: string;
  created_at: string;
  stats: CourseStats;
}

export interface Course extends CourseSummary {
  sections: string[];
  big_picture: string;
  excluded: string[];
  job: JobStatus | null;
}

// ---------- Knowledge graph ----------
export type SkillStatus = "locked" | "available" | "in_progress" | "learned" | "mastered";
export type LessonStatus = "pending" | "ready" | "failed";

export interface SkillNode {
  id: string;
  key: string;
  title: string;
  objective: string;
  kind: string;
  summary: string;
  difficulty: number;
  est_minutes: number;
  order_index: number;
  source_pages: number[];
  page_labels: string[];
  source_refs: string[];
  figure_ids: string[];
  foundations: string[];
  misconceptions: string[]; // not shown (SPEC §6.3: no common-mistake lists)
  lesson_status: LessonStatus;
  status: SkillStatus;
  due: boolean;
  due_at: string | null;
  retrievability: number | null;
  stability_days: number | null;
  reviews: number;
  // A halted lesson is not offered again before this (ISO, UTC); starting it earlier is a 409 unless force=true.
  // Optional: older servers don't send it (then only the 409 tells).
  cooldown_until?: string | null;
}

export interface SkillEdge {
  source: string; // prerequisite skill id
  target: string; // dependent skill id
  weight: number;
  reduced: boolean; // draw only reduced edges by default
}

export interface FoundationNode {
  key: string;
  title: string;
  description: string;
  check_question: string;
  check_answer: string;
  known: boolean | null;
}

export interface CourseGraph {
  course_id: string;
  skills: SkillNode[];
  edges: SkillEdge[];
  foundations: FoundationNode[];
}

// ---------- Lessons / visuals ----------
export interface Figure {
  id: string;
  page: number;
  page_label: string;
  caption: string;
  description: string;
  url: string;
}

export type VizColor = "primary" | "accent" | "muted" | "danger";
export type VizFormat =
  | "none"
  | "book_figure"
  | "comparison_table"
  | "equation_steps"
  | "diagram_2d"
  | "function_plot"
  | "frames";

export interface VizVector {
  start: number[];
  end: number[];
  label: string;
  color: VizColor;
}
export interface VizPoint {
  at: number[];
  label: string;
  color: VizColor;
}
export interface VizSegment {
  start: number[];
  end: number[];
  dashed: boolean;
  label: string;
}
export interface VizDiagram {
  x_range: number[];
  y_range: number[];
  show_grid: boolean;
  axis_labels: string[];
  vectors: VizVector[];
  points: VizPoint[];
  segments: VizSegment[];
}
export interface VizParameter {
  name: string;
  min: number;
  max: number;
  step: number;
  initial: number;
}
export interface VizTable {
  headers: string[];
  rows: string[][];
}
export interface VizEqStep {
  latex: string;
  note: string;
}
export interface PlotCurve {
  label: string;
  param_value: number | null;
  points: number[][];
}
export interface VisualizationFrame {
  title: string;
  diagram: VizDiagram | null;
  latex: string | null;
  note: string;
}
export interface Visualization {
  format: VizFormat;
  misunderstanding: string;
  purpose: string;
  rationale: string;
  learner_prompt: string;
  caption: string;
  figure: Figure | null;
  table: VizTable | null;
  steps: VizEqStep[];
  diagram: VizDiagram | null;
  plot_x_range: number[] | null;
  plot_y_range: number[] | null;
  plot_parameter: VizParameter | null;
  plot_curves: PlotCurve[];
  frames: VisualizationFrame[];
}

export interface NotationItem {
  symbol: string;
  meaning: string;
}
export interface WorkedStep {
  math: string;
  why: string;
}
export interface WorkedExample {
  problem: string;
  steps: WorkedStep[];
  takeaway: string;
}

export type QuestionKind =
  | "multiple_choice"
  | "true_false"
  | "true_false_set"
  | "numeric"
  | "expression"
  | "free_response"
  | "multistep";

export type StepKind = "multiple_choice" | "true_false" | "true_false_set" | "numeric" | "expression";

export interface MultistepStepPublic {
  index: number;
  prompt: string;
  kind: StepKind;
  choices: string[];
  // Using the hint (POST /api/questions/{qid}/hint?step=i) halves the step's point. Its text is sent only once used
  // (has_hint says whether there is one); a step after the current one comes without prompt, choices or hint.
  hint: string;
  has_hint: boolean;
}

export interface StepResult {
  index: number;
  correct: boolean;
  score: number; // 1 independent, 0.5 with the hint, 0 wrong
  feedback: string;
  answer_display: string;
  explanation: string;
  revealed?: boolean; // the step's answer was shown ("Show solution"): 0 points (older servers: absent)
}

export interface QuestionPublic {
  id: string;
  skill_id: string;
  key: string;
  kp_index: number;
  tier: number;
  kind: QuestionKind;
  prompt: string;
  choices: string[];
  hints: string[]; // the hints already shown for this attempt (hints_used of them); the next comes from POST /hint
  hint_count: number; // how many hints the question has
  checklist: string[];
  source_exercise: string | null;
  flagged: boolean;
  hints_used: number; // hints already shown for this attempt (server-recorded)
  revealed: boolean; // solution revealed for the current attempt (server-recorded)
  committed_response: string | null; // free_response: answer committed before a comparison reveal
  role: "practice" | "capstone"; // capstone = the lesson's final multistep problem
  steps: MultistepStepPublic[]; // multistep only
  step_index: number; // multistep: next step to answer (server-recorded)
  step_results: StepResult[]; // multistep: steps already answered in this attempt
}

export interface SkillRef {
  id: string;
  key: string;
  title: string;
  status: SkillStatus;
}

export interface KnowledgePoint {
  index: number;
  target_questions: number; // questions answered to demonstrate this KP
  title: string;
  explanation: string;
  worked_example: WorkedExample;
  question_count: number;
}

/** The server's record of a scored inline check (a proof lesson's guided step, SPEC §6.3b). */
export interface GuidedCheckState {
  status: "open" | "retry" | "done"; // retry: the first answer was wrong; one more try for ½ point
  tries: number; // answers given (an unreadable answer is not a try)
  first_response: string | null; // the wrong first answer (choice checks: the option index as a string)
  response: string | null; // done: the answer that ended the check (null: the answer was shown instead)
  correct: boolean | null; // done: whether that answer was right (null: shown without an answer)
  points: number | null; // done: 1 first try / 0.5 second try / 0
  revealed: boolean; // done through "Show answer"
  feedback: string; // the grader's message for the last answer
  hint: string; // sent once the first answer was wrong
  answer_display: string; // done only
  explanation: string; // done only
  voided: boolean; // reported as wrong by the learner: not counted
  scored: boolean; // false on a retry of the lesson (shown with its answer, not scored again)
}

export interface InlineCheckPublic {
  kind: "numeric" | "expression" | "multiple_choice" | "true_false" | "true_false_set" | "reflect";
  prompt: string;
  choices: string[];
  /** A proof lesson's guided step: answered via POST /api/tasks/{tid}/blocks/{i}/check and counted in the score. */
  scored: boolean;
  second_try: boolean; // scored: a wrong first answer gets one more try for ½ point
  state: GuidedCheckState | null; // scored: the server's record (null in a preview of a skill never started)
}

export type TeachingBlockKind = "setup" | "explain" | "demonstrate" | "check" | "faded_example" | "generalize" | "summary";

export interface TeachingBlockView {
  index: number;
  kind: TeachingBlockKind;
  heading: string | null;
  body: string;
  visualization: Visualization | null;
  check: InlineCheckPublic | null;
}

export interface BlockCheckRequest {
  response?: string | null; // numeric / expression / true_false_set ("TFT")
  choice?: number | null; // multiple_choice / true_false
  version?: string | null; // Lesson.version
}

export interface BlockCheckResult {
  correct: boolean | null; // null for "reflect"
  feedback: string;
  answer_display: string;
  explanation: string;
}

export interface GuidedRevealRequest {
  version: string; // Lesson.version (required)
}

/** POST /api/tasks/{tid}/blocks/{i}/check (body: BlockCheckRequest with version) and .../reveal. */
export interface GuidedCheckResult {
  check: GuidedCheckState;
  final: boolean; // the check is done (its points are decided)
  task: Task; // updated task: progress, and the lesson with the next blocks once the check is done
}

export interface BlockFeedbackRequest {
  kind?: "unclear";
  note?: string;
  version?: string | null; // Lesson.version
}

export interface Lesson {
  skill_id: string;
  skill_key: string;
  title: string;
  objective: string;
  format: "v1" | "v2"; // v2 = planned teaching sequence (blocks)
  version: string; // hash of the stored lesson; send with block checks/feedback (409 when stale)
  /** Proof lessons: "scored" = the checks are scored guided steps and blocks after the first open one are not sent
   * yet (blocks_total counts them); "retry" = a later attempt: the checks are shown answered, not scored. */
  guided: "scored" | "retry" | null;
  blocks_total: number; // every block of the lesson, including those not sent yet
  goal_line: string;
  blocks: TeachingBlockView[];
  assumed: string[];
  out_of_scope: string[];
  deeper: string;
  why_it_matters: string;
  idea: string;
  notation: NotationItem[];
  knowledge_points: KnowledgePoint[];
  missing_steps: string;
  common_mistakes: string[]; // legacy: no longer shown (SPEC §6.3)
  connection: string;
  visualization: Visualization | null;
  source_refs: string[];
  source_pages: number[];
  page_labels: string[];
  figures: Figure[];
  prerequisites: SkillRef[];
  unlocks: SkillRef[];
  question_count: number;
}

export interface SourcePage {
  page: number;
  label: string;
  markdown: string;
  image_url: string;
  uncertain: string[];
}

// ---------- Study loop ----------
export type TaskType = "lesson" | "review" | "quiz";

// SPEC §6.7 — one quiz question after the quiz has finished (correctness is not shown before).
export interface QuizQuestionResult {
  question_id: string;
  skill: SkillRef;
  kind: QuestionKind;
  prompt: string;
  choices: string[];
  response: string | null; // null when passed / unanswered
  answered: boolean; // false: passed, or time ran out
  score: number; // 1 correct, 0 otherwise
  correct_answer: string;
  solution: string;
  seconds: number;
}

export interface QuizInfo {
  kind: "quiz" | "retake";
  question_count: number;
  time_limit_seconds: number;
  started_at: string | null; // null until the clock is started (POST /practice)
  deadline: string | null; // ISO UTC
  seconds_left: number | null; // server-computed
  skills: SkillRef[];
  results: QuizQuestionResult[]; // filled once finished
  missed_skills: SkillRef[]; // reviewed straight away after the quiz
  retake_available: boolean;
}
export type TaskPhase = "learn" | "practice" | "done";
export type TaskOutcome = "passed" | "failed" | "abandoned";

// Lesson (SPEC §6.3): each knowledge point serves its target number of questions (+1 after a miss), then the
// multistep capstone; points 1 / 0.5 with a hint / 0 wrong or revealed; pass at score >= pass_mark.
// streak / needed_in_row are legacy (kept for older clients).
export interface TaskProgress {
  kp_index: number;
  kp_total: number;
  streak: number;
  needed_in_row: number;
  kp_questions_used: number;
  kp_questions_available: number;
  answered: number;
  correct: number;
  total_questions: number; // reviews: size of the set; lessons: 0
  kp_wrong: number; // wrong answers so far in the current KP
  easier_available: boolean; // POST /easier would succeed now
  kp_target: number; // questions planned for the current KP
  points: number; // points earned so far in this task
  max_points: number; // points possible for the questions answered so far
  score: number; // points / max_points (0-1)
  planned: number; // questions planned for the whole task (lesson: KP targets + capstone)
  pass_mark: number; // 0.8
  in_capstone: boolean; // the final multistep problem is being answered
  has_capstone: boolean; // the task ends with (or includes) a capstone
  halted?: boolean; // lesson stopped early (80 % became unreachable with planned questions left); older servers: absent
  // Proof lessons with scored guided steps: the steps count like questions, then one variant problem (weight 2).
  guided_total: number; // scored guided steps in this attempt (0: none, or a retry where they are not scored)
  guided_done: number; // of them, answered (or shown)
  in_guided: boolean; // guided steps are still open: the practice can't start yet
  variant: boolean; // the final problem is the proof lesson's variant (same idea, something changes)
  best_possible: number; // the score if everything still planned is answered right
}

export interface Task {
  id: string;
  course_id: string;
  type: TaskType;
  phase: TaskPhase;
  skill: SkillRef | null; // null for quizzes (see quiz.skills)
  reason: string;
  xp_value: number;
  lesson: Lesson | null;
  question: QuestionPublic | null;
  progress: TaskProgress;
  outcome: TaskOutcome | null;
  cooldown_until?: string | null; // failed lesson: when it comes back (ISO UTC); null = no pause (e.g. after a flag)
  quiz: QuizInfo | null; // type === "quiz"
}

export interface SessionInfo {
  xp_today: number;
  daily_goal: number;
  goal_reached: boolean;
  due_reviews: number;
  quiz_due: boolean; // the next task is a forced quiz (after any active task)
  xp_until_quiz: number | null;
}

export interface TaskOption {
  skill: SkillRef | null; // null for a quiz (a due quiz is the only option)
  type: TaskType;
  reason: string;
  xp_value: number;
  importance: number;
}

export interface NextTaskResponse {
  task: Task | null; // currently active (unfinished) task, if any
  options: TaskOption[]; // ranked task menu (up to 5)
  session: SessionInfo;
  message: string;
}

export interface StartTaskRequest {
  skill_id: string;
  type?: TaskType | null;
  replace_active?: boolean; // another task is active: 409 unless true (then it is abandoned)
  force?: boolean; // learn a locked skill anyway / start a lesson during its cooldown
}

export type SelfGrade = "correct" | "partial" | "incorrect";

export interface AnswerSubmit {
  question_id: string;
  choice?: number | null;
  response?: string | null;
  self_grade?: SelfGrade | null;
  hints_used: number;
  revealed_solution: boolean;
  seconds: number;
  step?: number | null; // multistep: index of the step being answered
  skip?: boolean; // quiz only: pass on this question (0 points)
}

export interface AnswerResult {
  correct: boolean;
  // "step": a multistep step, more to come; "recorded": a quiz answer was saved (correctness shown at the end)
  status: "correct" | "incorrect" | "self_graded" | "voided" | "step" | "recorded";
  independent: boolean;
  feedback: string;
  correct_answer: string;
  correct_choice: number | null;
  solution: string;
  task: Task;
  xp_awarded: number;
  suggestion: "easier_step" | "review_prerequisite" | "reread_example" | "retry_later" | null;
  suggestion_text: string | null;
  score: number; // points for this question (1 / 0.5 / 0; multistep: mean of its steps)
  step_result: StepResult | null; // multistep: the step just answered
  question_done: boolean; // false while a multistep question still has steps
  // What the answer changed in the task (extra question next, capstone next, lesson passed …); also appended to
  // `feedback`. Older servers: absent (multistep notes are then read from the end of `feedback`).
  note?: string;
}

// free_response: send the committed answer so the reveal counts as a comparison, not as assistance.
export interface RevealRequest {
  response?: string | null;
}

export interface RevealResponse {
  solution: string;
  correct_answer: string;
}

export interface HintResponse {
  hint: string;
  hints_used: number;
}

export interface Health {
  openai: boolean;
  anthropic: boolean;
  provider: string;
  llm_ready: boolean;
  missing_keys: string[];
  nvidia: boolean;
  fake_llm: boolean;
  parse_engine: string;
  max_course_pages: number;
  models: Record<string, string>;
}
