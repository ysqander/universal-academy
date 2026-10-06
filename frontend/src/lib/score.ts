import { cooldownEnd, returnTimePhrase } from "./cooldown.ts";
// Score-based lessons and reviews (SPEC §6.3–§6.4): 1 point for an independent right answer, ½ with a hint, 0 when
// wrong or after the solution was shown; a multistep question earns the mean of its steps; the capstone counts double.
// Pass mark 80 %. Everything here is pure formatting of what the server sends (TaskProgress / AnswerResult).
import type { AnswerResult, KnowledgePoint, Task, TaskOutcome, TaskProgress } from "../types";

export const DEFAULT_PASS_MARK = 0.8;

const clamp01 = (n: number): number => (Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0);
const num = (n: unknown): number => (typeof n === "number" && Number.isFinite(n) ? n : 0);

/** A count in halves: 2.5 → "2½", 0.5 → "½", 3 → "3" (rounded to the nearest half). */
export function halves(value: number): string {
  const doubled = Math.round(Math.max(0, num(value)) * 2);
  const whole = Math.floor(doubled / 2);
  if (doubled % 2 === 0) return String(whole);
  return whole === 0 ? "½" : `${whole}½`;
}

const EPS = 1e-9;

/**
 * Whole percent by the server's rule (engine.score_percent), so the meter, the feedback and the results page always
 * say the same number: round half up (0.285 → 29, 0.9167 → 92), except that a score never rounds up onto the pass
 * mark or onto 100 % (0.796 → 79, 0.996 → 99) — a failing score never reads as passing, an imperfect one never as perfect.
 */
export function scorePercent(fraction: number, passMark: number = DEFAULT_PASS_MARK): number {
  const score = clamp01(fraction);
  const pct = Math.floor(score * 100 + 0.5 + EPS);
  const mark = Math.round(passMark * 100);
  if (score < passMark - EPS && pct >= mark) return mark - 1;
  if (score < 1 - EPS && pct >= 100) return 99;
  return pct;
}

export const percentLabel = (fraction: number, passMark: number = DEFAULT_PASS_MARK): string => `${scorePercent(fraction, passMark)}%`;

/** The task's pass mark (0.8 unless the server says otherwise). */
export const passMarkOf = (progress: Partial<TaskProgress>): number => {
  const mark = num(progress.pass_mark);
  return mark > 0 && mark <= 1 ? mark : DEFAULT_PASS_MARK;
};

export interface ScoreView {
  /** At least one answered question carries points. */
  hasScore: boolean;
  /** Score of the answered questions (0–1). */
  fraction: number;
  /** "Score 86%" / "Score —". */
  label: string;
  /** "pass at 80%". */
  passLabel: string;
  passMark: number;
  tone: "none" | "pass" | "below";
  answered: number;
  /** Questions planned for the whole task; 0 = not known (older server). */
  planned: number;
  /** "3 of 7 answered" / "Last question" / "Stopped after 2 questions" / null when the plan isn't known. */
  remainingLabel: string | null;
  /** The lesson stopped early (80 % out of reach with planned questions left): not every planned question was asked. */
  halted: boolean;
  /** Spoken summary for the meter. */
  ariaText: string;
}

/** How a finished task ended, for the meter: its outcome and the plan just before its last answer. */
export interface TaskEnd {
  outcome: TaskOutcome | null;
  /** Questions planned just before the last answer (a halted task may report only what was answered). */
  plannedBefore?: number;
}

/** The lesson stopped before its plan was done: the server says so (`halted`), or planned questions were left. */
export function wasHalted(progress: Partial<TaskProgress>, plannedBefore = 0): boolean {
  if (typeof progress.halted === "boolean") return progress.halted;
  const planned = Math.max(Math.round(num(progress.planned)), Math.round(num(plannedBefore)));
  return planned > 0 && Math.round(num(progress.answered)) < planned;
}

/**
 * Score of the answered questions vs the pass mark, plus how much of the plan is left. `end`: the task has finished —
 * a lesson halted early then says "Stopped after n questions" rather than claiming the plan was answered.
 */
export function scoreView(progress: Partial<TaskProgress>, end?: TaskEnd | null): ScoreView {
  const passMark = passMarkOf(progress);
  const maxPoints = num(progress.max_points);
  const hasScore = maxPoints > 0;
  const fraction = hasScore ? clamp01(num(progress.score) || num(progress.points) / maxPoints) : 0;
  const answered = Math.max(0, Math.round(num(progress.answered)));
  const planned = Math.max(0, Math.round(num(progress.planned)));
  const label = hasScore ? `Score ${percentLabel(fraction, passMark)}` : "Score —";
  const passLabel = `pass at ${percentLabel(passMark)}`;
  const halted = end?.outcome === "failed" && wasHalted(progress, end.plannedBefore);
  let remainingLabel: string | null = null;
  if (halted) {
    remainingLabel = `Stopped after ${answered} ${answered === 1 ? "question" : "questions"}`;
  } else if (planned > 0) {
    const left = Math.max(0, planned - answered);
    remainingLabel = left === 1 && answered > 0 && !end ? "Last question" : `${Math.min(answered, planned)} of ${planned} answered`;
  }
  const tone = !hasScore ? "none" : fraction + 1e-9 >= passMark ? "pass" : "below";
  const ariaText = hasScore
    ? `Score ${percentLabel(fraction, passMark)} of the questions answered${halted ? "; the lesson stopped early" : end ? "" : " so far"}; ${passLabel}.`
    : `No answers scored yet; ${passLabel}.`;
  return { hasScore, fraction, label, passLabel, passMark, tone, answered, planned, remainingLabel, halted, ariaText };
}

/** The task ends with the multistep capstone (known once it is served, or when the server announces it). */
export const hasCapstone = (progress: Partial<TaskProgress>): boolean => Boolean(progress.in_capstone || progress.has_capstone);

export interface KpPosition {
  /** "Question 2 of 3", "Question 3 of 3 · extra question", "Question 2" (plan not known). */
  label: string;
  /** This question was added after a miss in the knowledge point (beyond its target). */
  extra: boolean;
  number: number;
}

/**
 * Where the current question sits in its knowledge point: n of the KP's target (`target_questions`, or the server's
 * `kp_target`). A question beyond the target was added after a miss so the learner can recover — said as such.
 */
export function kpPosition(progress: Partial<TaskProgress>, kp?: Pick<KnowledgePoint, "target_questions"> | null): KpPosition {
  const n = Math.max(Math.round(num(progress.kp_questions_used)), 1);
  const base = Math.round(num(kp?.target_questions)) || Math.round(num(progress.kp_target));
  const planned = Math.max(Math.round(num(progress.kp_target)), base);
  const extra = base > 0 && n > base;
  const of = planned >= n ? `Question ${n} of ${planned}` : `Question ${n}`;
  return { label: extra ? `${of} · extra question` : of, extra, number: n };
}

export interface Stage {
  key: string;
  state: "done" | "current" | "todo";
  capstone: boolean;
}

/** Stepper segments: one per knowledge point, then the capstone ("Put it together") when the lesson has one. */
export function lessonStages(progress: Partial<TaskProgress>, kpCount: number, withCapstone: boolean): Stage[] {
  const total = Math.max(Math.round(num(progress.kp_total)), kpCount, 1);
  const kp = Math.round(num(progress.kp_index));
  const inCapstone = Boolean(progress.in_capstone);
  const stages: Stage[] = Array.from({ length: total }, (_, i) => ({
    key: `kp${i}`,
    state: inCapstone || i < kp ? "done" : i === kp ? "current" : "todo",
    capstone: false,
  }));
  if (withCapstone || inCapstone) stages.push({ key: "capstone", state: inCapstone ? "current" : "todo", capstone: true });
  return stages;
}

type ScoredResult = Pick<AnswerResult, "status" | "correct" | "independent"> & { score?: number | null };

/** Points this question earned (1 / 0.5 / 0); falls back to the verdict when an older server sends no score. */
export function questionPoints(result: ScoredResult): number {
  const fromVerdict = result.correct ? (result.independent ? 1 : 0.5) : 0;
  if (typeof result.score !== "number" || !Number.isFinite(result.score)) return fromVerdict;
  // An independent right answer is always a full point (guards a server that leaves the default 0).
  if (result.correct && result.independent && result.status !== "self_graded") return Math.max(result.score, 1);
  return clamp01(result.score);
}

/**
 * The points line under a verdict: "1 point", "½ point — with a hint", "0 points — solution shown", "0 points".
 * `revealed`: the learner looked at the solution before answering. Null for voided answers and multistep steps.
 */
export function pointsLabel(result: ScoredResult, revealed = false): string | null {
  if (result.status === "voided" || result.status === "step") return null;
  const points = questionPoints(result);
  if (points >= 1) return "1 point";
  if (points > 0) {
    if (result.status === "self_graded" && !result.correct) return `${halves(points)} point — partly right`;
    return `${halves(points)} point — with a hint`;
  }
  return revealed || result.correct ? "0 points — solution shown" : "0 points";
}

/** One multistep step: "1 point", "½ point — with the hint", "0 points — solution shown", "0 points". */
export function stepPointsLabel(score: number, revealed = false): string {
  if (score >= 1) return "1 point";
  if (score > 0) return "½ point — with the hint";
  return revealed ? "0 points — solution shown" : "0 points";
}

export interface LessonEnd {
  /** The attempt stopped early: the pass mark could no longer be reached. */
  halted: boolean;
  text: string;
}

/**
 * Why a lesson paused (outcome "failed"): stopped early because 80 % became unreachable, or finished below it.
 * `plannedBefore`: the plan of the task just before its last answer (a halted task may report only what was answered).
 */
export function lessonEndNote(
  progress: Partial<TaskProgress>,
  plannedBefore = 0,
  cooldownUntil?: string | null,
  now: number = Date.now(),
): LessonEnd {
  const view = scoreView(progress);
  // The server sends Task.cooldown_until: a time when the lesson pauses, null when it does not (e.g. after a flag).
  const end = cooldownUntil === undefined ? undefined : cooldownEnd(cooldownUntil, now);
  const back =
    end === undefined
      ? "it comes back in about 2 hours with fresh questions"
      : end === null
        ? "this attempt doesn't count against you, so start it again whenever you like"
        : `it comes back ${returnTimePhrase(end, now)} with fresh questions`;
  const halted = wasHalted(progress, plannedBefore);
  if (halted) return { halted, text: `${percentLabel(view.passMark)} is no longer reachable in this attempt — ${back}.` };
  const score = view.hasScore ? `Your score, ${percentLabel(view.fraction, view.passMark)}, is` : "Your score is";
  return { halted, text: `${score} below the ${percentLabel(view.passMark)} pass mark — ${back}.` };
}

const PAUSE_SAID = /\bpauses? here\b|\bout of reach\b|\bno longer reachable\b|\bstopped short\b/i;
const PASS_SAID = /\blesson passed\b/i;
/** The server's own end-of-lesson sentence (engine: " Lesson passed with 91%."). */
const PASS_NOTE = /\s*\bLesson passed with \d+(?:\.\d+)?\s?%\.?/gi;

/** The server's text without its "Lesson passed with N%." sentence: the panel says the end of the lesson once, in its
 * own progress note ("That completes the lesson — score N%."). */
export const withoutPassNote = (text: string): string => text.replace(PASS_NOTE, "").trim();

/** What the answer changed in the task: a knowledge point finished, the end of the task. (The server's note —
 * AnswerResult.note, also at the end of its feedback — announces an extra question after a miss and the capstone;
 * they are not said twice.) */
export function progressNote(
  before: Pick<Task, "type" | "progress">,
  after: Pick<Task, "type" | "phase" | "outcome" | "progress">,
  /** The server's text shown with it (feedback, suggestion): when it already says the lesson pauses, not twice. */
  serverFeedback = "",
): string | null {
  if (after.phase === "done") {
    if (after.outcome === "passed") {
      if (before.type !== "lesson") return "That completes the review.";
      if (PASS_SAID.test(serverFeedback)) return null; // the server's text already says it (unrecognised wording)
      const view = scoreView(after.progress);
      return view.hasScore ? `That completes the lesson — score ${percentLabel(view.fraction, view.passMark)}.` : "That completes the lesson.";
    }
    if (after.outcome === "failed") {
      if (before.type !== "lesson") return "That ends the review.";
      return PAUSE_SAID.test(serverFeedback) ? null : `This lesson pauses here: ${lessonEndNote(after.progress, before.progress.planned).text}`;
    }
    return null;
  }
  if (after.type === "review") return null;
  const b = before.progress;
  const a = after.progress;
  if (a.in_capstone && !b.in_capstone) return null; // the server's note announces the capstone
  if (a.kp_index > b.kp_index) return "Knowledge point complete — on to the next step.";
  if (a.kp_index < b.kp_index) return "Stepping back to an earlier knowledge point.";
  // An extra question after a miss is announced by the server's note; the next card says "extra question".
  return null;
}
