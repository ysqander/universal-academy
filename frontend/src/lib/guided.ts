// Scored guided steps of proof lessons (SPEC §6.3b): the inline checks the learner answers while working through the
// proof count toward the lesson's 80 % like questions — 1 right first time, ½ right on the second try (typed answers
// and statement sets get one; a choice check has one try), 0 wrong or after "Show answer" — then one variant problem
// (the capstone slot, counting double) ends the lesson. The server keeps each step's record (InlineCheckPublic.state)
// and sends the blocks after the first open step only once it is done. Everything here is pure formatting of what it
// sends.
import type { ChoiceMark } from "../components/answers/AnswerInputs";
import type { GuidedCheckState, InlineCheckPublic, Lesson, Task, TaskProgress } from "../types";
import { keyIndexFromText, choiceMarksFromText } from "./choices.ts";
import { STALE_LESSON_MESSAGE, stripStockVerdict } from "./feedback.ts";
import { passMarkOf, percentLabel, type Stage } from "./score.ts";

const EPS = 1e-9;
const num = (n: unknown): number => (typeof n === "number" && Number.isFinite(n) ? n : 0);

/** What a scored step needs from the study page: the task it is answered in, and where the server's task goes. */
export interface GuidedHandlers {
  taskId: string;
  /** The server's task after an answer: progress, and the next blocks once the step is done. */
  onTask: (task: Task) => void;
  /** "Report a wrong answer" on a done step (the page opens the report dialog). */
  onFlag: (blockIndex: number) => void;
}

type CheckLike = Pick<InlineCheckPublic, "kind"> & { scored?: boolean; state?: Pick<GuidedCheckState, "status" | "scored"> | null };

/** A proof lesson's guided step: answered in the task and driven by the server's record, never by the browser (also
 * when a retry of the lesson sends it answered with a record). */
export const isScoredCheck = (check: CheckLike | null | undefined): boolean =>
  Boolean(check && check.kind !== "reflect" && (check.scored || check.state));

/** The step still holds the lesson back: scored this attempt and not done (a retry's steps are shown answered). */
export const holdsGate = (check: CheckLike): boolean => check.state?.scored !== false && check.state?.status !== "done";

/** The lesson's steps are scored in this attempt (a retry shows them answered instead). */
export const isScoredLesson = (lesson: Pick<Lesson, "guided"> | null | undefined): boolean => lesson?.guided === "scored";

/** A lesson task whose proof steps are scored (or its variant is being answered). */
export const isGuidedProgress = (progress: Partial<TaskProgress>): boolean => num(progress.guided_total) > 0 || Boolean(progress.variant);

/** Leaving the task keeps the answers of a proof lesson with guided steps (scored, or a retry): starting it again
 * picks up where it stopped. */
export const keepsAnswersOnLeave = (task: Pick<Task, "type" | "lesson">): boolean =>
  task.type === "lesson" && (task.lesson?.guided === "scored" || task.lesson?.guided === "retry");

const RANK: Record<GuidedCheckState["status"], number> = { open: 0, retry: 1, done: 2 };

/**
 * The newer of the server's record (the step as the task last sent it) and the record the last answer returned —
 * the latter shows straight away, before the replaced task catches up. On a tie the task's record wins (a report
 * marks it reported).
 */
export function latestState(server: GuidedCheckState | null | undefined, local: GuidedCheckState | null | undefined): GuidedCheckState | null {
  if (!local) return server ?? null;
  if (!server) return local;
  return RANK[local.status] > RANK[server.status] ? local : server;
}

/** Points of a done step: the server's, else from the verdict (1 first time, ½ on a second try). */
export function stepPoints(state: Pick<GuidedCheckState, "points" | "correct" | "tries" | "revealed">): number {
  if (typeof state.points === "number" && Number.isFinite(state.points)) return Math.min(1, Math.max(0, state.points));
  if (state.revealed || !state.correct) return 0;
  return state.tries > 1 ? 0.5 : 1;
}

/** The points line under a done step: "1 point", "½ point — second try", "0 points — answer shown", "0 points";
 * null for a step not scored this time (or not done yet). */
export function guidedPointsLabel(state: GuidedCheckState | null): string | null {
  if (!state || state.status !== "done" || !state.scored) return null;
  if (state.voided) return "Not counted — the answer key was wrong";
  if (state.revealed) return "0 points — answer shown";
  const points = stepPoints(state);
  if (points >= 1) return "1 point";
  if (points > 0) return "½ point — second try";
  return "0 points";
}

/** The tag next to a scored step's label: it counts, and how many tries it has. */
export const scoredTag = (check: Pick<InlineCheckPublic, "second_try">): string =>
  check.second_try ? "Counts toward your score · a second try for ½" : "Counts toward your score · one try";

/** After the learner reported a step's answer key as wrong: the report is kept for review, the score is unchanged. */
export const REPORTED_NOTE = "Reported — thank you. The key will be checked; your score doesn't change now.";

export type GuidedTone = "open" | "retry" | "correct" | "partial" | "incorrect" | "neutral";

export interface GuidedVerdict {
  tone: GuidedTone;
  /** "Right", "Not yet — one more try for ½ point", "Answer shown", … (null while open). */
  title: string | null;
  points: string | null;
}

export const NOT_YET_TITLE = "Not yet — one more try for ½ point";

/** Verdict, tone and points line of a step's record. */
export function guidedVerdict(state: GuidedCheckState | null): GuidedVerdict {
  if (!state || state.status === "open") return { tone: "open", title: null, points: null };
  if (state.status === "retry") return { tone: "retry", title: NOT_YET_TITLE, points: null };
  const points = guidedPointsLabel(state);
  if (!state.scored) return { tone: "neutral", title: "Not scored this time", points };
  if (state.revealed || state.correct === null) return { tone: "neutral", title: "Answer shown", points };
  if (state.correct) return stepPoints(state) >= 1 ? { tone: "correct", title: "Right", points } : { tone: "partial", title: "Right on the second try", points };
  return { tone: "incorrect", title: "Not quite", points };
}

// The server's stock lines that the verdict and the points line already say.
const NOT_YET_LINE = /\s*Not yet\s*[—–-]+\s*one more try for ½ point\.?/gi;
const HINT_BELOW = /\s*The hint below may help\.?/gi;
const SECOND_TRY_NOTE = /\s*On the second try, so ½ point\.?/gi;

/**
 * What the grader adds to the verdict: on a retry its nudge for a typed answer ("Check the sign.") without the stock
 * "not yet" line (a statement set's never names the wrong statements); once done its message without the stock
 * verdict and the second-try note. Nothing for a shown answer or a step not scored this time.
 */
export function guidedFeedback(state: GuidedCheckState | null): string {
  if (!state || state.status === "open") return "";
  if (state.status === "retry") return stripStockVerdict(state.feedback.replace(NOT_YET_LINE, " ").replace(HINT_BELOW, " ").replace(/\s+/g, " "));
  if (!state.scored || state.revealed) return "";
  return stripStockVerdict(state.feedback.replace(SECOND_TRY_NOTE, ""));
}

const choiceIndex = (value: string | null | undefined): number | null => (value && /^\d+$/.test(value.trim()) ? Number(value.trim()) : null);

const isChoiceKind = (kind: InlineCheckPublic["kind"]): boolean => kind === "multiple_choice" || kind === "true_false";

/**
 * What a step's inputs show for its record: the answer that ended it (done), the wrong first answer to correct
 * (retry; a choice is picked again), nothing while open or when the answer was shown instead. `response` is the
 * typed answer, or a statement set's marks ("TFT").
 */
export function restoredAnswer(kind: InlineCheckPublic["kind"], state: GuidedCheckState | null): { choice: number | null; response: string } {
  const sent = state?.status === "done" ? state.response : state?.status === "retry" ? state.first_response : null;
  if (isChoiceKind(kind)) return { choice: state?.status === "done" ? choiceIndex(sent) : null, response: "" };
  return { choice: null, response: sent ?? "" };
}

/**
 * Marks on a choice step's options. On a retry only the first pick, as not right — never the answer. Once done the
 * pick and the key (an earlier wrong pick stays marked); a shown answer marks the key alone.
 */
export function guidedChoiceMarks(choices: string[], state: GuidedCheckState | null): ChoiceMark[] {
  if (!state || state.status === "open") return [];
  const first = choiceIndex(state.first_response);
  if (state.status === "retry") return choices.map((_, i) => (i === first ? "incorrect" : null));
  const chosen = choiceIndex(state.response);
  if (chosen === null) {
    const key = keyIndexFromText(choices, state.answer_display);
    return choices.map((_, i) => (i === key ? "correct" : null));
  }
  const marks = choiceMarksFromText(choices, chosen, state.correct, state.answer_display);
  return choices.map((_, i) => marks[i] ?? (i === first ? "incorrect" : null));
}

/** A 409 of a scored step that only a reload fixes: the lesson changed since the page loaded it, or the step was
 * answered in another window ("… — reload the lesson."). */
export const isStaleLessonMessage = (message: string): boolean => message.trim() === STALE_LESSON_MESSAGE || /\breload\b/i.test(message);

/** "1 more step" / "3 more steps". */
export const moreSteps = (n: number): string => (n === 1 ? "1 more step" : `${n} more steps`);

export type StepDot = "open" | "retry" | "done" | "half" | "missed" | "shown";

/** The step's dot on the lesson's rail (TeachingBlock data-check). */
export function guidedDot(state: GuidedCheckState | null | undefined): StepDot {
  if (!state || state.status === "open") return "open";
  if (state.status === "retry") return "retry";
  if (!state.scored) return "done";
  if (state.voided || state.revealed || state.correct === null) return "shown";
  if (!state.correct) return "missed";
  return stepPoints(state) >= 1 ? "done" : "half";
}

/** Stepper segments: one per proof step, then the variant problem (drawn like a capstone). */
export function guidedStages(progress: Partial<TaskProgress>): Stage[] {
  const total = Math.max(Math.round(num(progress.guided_total)), 0);
  const done = Math.min(Math.round(num(progress.guided_done)), total);
  const inVariant = Boolean(progress.in_capstone);
  const stages: Stage[] = Array.from({ length: total }, (_, i) => ({
    key: `step${i}`,
    state: inVariant || i < done ? "done" : i === done ? "current" : "todo",
    capstone: false,
  }));
  if (progress.variant || inVariant) stages.push({ key: "variant", state: inVariant ? "current" : "todo", capstone: true });
  return stages;
}

/** "Proof step 3 of 5" while steps are open, "Proof steps done" after the last. */
export function guidedStepLabel(progress: Partial<TaskProgress>): string {
  const total = Math.max(Math.round(num(progress.guided_total)), 0);
  const done = Math.min(Math.round(num(progress.guided_done)), total);
  return done >= total ? "Proof steps done" : `Proof step ${done + 1} of ${total}`;
}

/** What follows the steps, after the step label: "then a variant problem (counts double)" (null: no variant). */
export function guidedThen(progress: Partial<TaskProgress>): string | null {
  if (!progress.variant) return null;
  const total = Math.max(Math.round(num(progress.guided_total)), 0);
  return num(progress.guided_done) >= total ? "next: the variant problem (counts double)" : "then a variant problem (counts double)";
}

/** Even a right variant (when there is one) can't lift the score to the pass mark: going on ends this attempt. */
export const variantOutOfReach = (progress: Partial<TaskProgress>): boolean =>
  num(progress.guided_total) > 0 && !progress.in_guided && typeof progress.best_possible === "number" && progress.best_possible < passMarkOf(progress) - EPS;

export interface GuidedStart {
  title: string;
  text: string;
  button: string;
  /** The button ends the attempt (the server stops it: 80 % is out of reach). */
  ends: boolean;
}

/** The card after a scored proof lesson's steps: the variant problem next — or, when 80 % is out of reach even with
 * it right, the end of this attempt, said before the click. Without a variant (none could be served) the steps
 * decide the lesson: "Finish the lesson". Null for other lessons. */
export function guidedStart(progress: Partial<TaskProgress>): GuidedStart | null {
  if (!isGuidedProgress(progress)) return null;
  const mark = percentLabel(passMarkOf(progress));
  if (variantOutOfReach(progress)) {
    return {
      title: "Proof steps done",
      text: `${mark} is out of reach on this attempt${progress.variant ? ", even with the variant right" : ""}. The lesson comes back after a pause with fresh questions.`,
      button: "End this attempt",
      ends: true,
    };
  }
  if (!progress.variant) {
    return { title: "Proof steps done", text: `Your steps decide this lesson: ${mark} passes.`, button: "Finish the lesson", ends: false };
  }
  return {
    title: "Proof steps done",
    text: `Last: one variant problem — the same idea with one thing changed. It counts double. ${mark} passes.`,
    button: "Start the variant problem",
    ends: false,
  };
}
