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
  misconceptions: string[];
  lesson_status: LessonStatus;
  status: SkillStatus;
  due: boolean;
  due_at: string | null;
  retrievability: number | null;
  stability_days: number | null;
  reviews: number;
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

export type QuestionKind = "multiple_choice" | "true_false" | "true_false_set" | "numeric" | "expression" | "free_response";

export interface QuestionPublic {
  id: string;
  skill_id: string;
  key: string;
  kp_index: number;
  tier: number;
  kind: QuestionKind;
  prompt: string;
  choices: string[];
  hints: string[];
  checklist: string[];
  source_exercise: string | null;
  flagged: boolean;
  hints_used: number; // hints already shown for this attempt (server-recorded)
  revealed: boolean; // solution revealed for the current attempt (server-recorded)
  committed_response: string | null; // free_response: answer committed before a comparison reveal
}

export interface SkillRef {
  id: string;
  key: string;
  title: string;
  status: SkillStatus;
}

export interface KnowledgePoint {
  index: number;
  title: string;
  explanation: string;
  worked_example: WorkedExample;
  question_count: number;
}

export interface InlineCheckPublic {
  kind: "numeric" | "expression" | "multiple_choice" | "true_false" | "true_false_set" | "reflect";
  prompt: string;
  choices: string[];
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
  common_mistakes: string[];
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
export type TaskType = "lesson" | "review";
export type TaskPhase = "learn" | "practice" | "done";
export type TaskOutcome = "passed" | "failed" | "abandoned";

// Lesson: per knowledge point, 2 independent correct answers in a row pass the KP (Math Academy rule).
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
}

export interface Task {
  id: string;
  course_id: string;
  type: TaskType;
  phase: TaskPhase;
  skill: SkillRef;
  reason: string;
  xp_value: number;
  lesson: Lesson | null;
  question: QuestionPublic | null;
  progress: TaskProgress;
  outcome: TaskOutcome | null;
}

export interface SessionInfo {
  xp_today: number;
  daily_goal: number;
  goal_reached: boolean;
  due_reviews: number;
}

export interface TaskOption {
  skill: SkillRef;
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
  force?: boolean; // learn a locked skill anyway
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
}

export interface AnswerResult {
  correct: boolean;
  status: "correct" | "incorrect" | "self_graded" | "voided";
  independent: boolean;
  feedback: string;
  correct_answer: string;
  correct_choice: number | null;
  solution: string;
  task: Task;
  xp_awarded: number;
  suggestion: "easier_step" | "review_prerequisite" | "reread_example" | "retry_later" | null;
  suggestion_text: string | null;
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
  nvidia: boolean;
  fake_llm: boolean;
  parse_engine: string;
  max_course_pages: number;
  models: Record<string, string>;
}
