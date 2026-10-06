// Score-based lessons and reviews (SPEC §6.3–§6.4): 1 point for an independent right answer, ½ with a hint, 0 when
// wrong or after the solution was shown; a multistep question earns the mean of its steps; the capstone counts double.
// Pass mark 80 %. Everything here is pure formatting of what the server sends (TaskProgress / AnswerResult).
import type { AnswerResult, KnowledgePoint, Task, TaskProgress } from "../types";

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

/** Whole percent, rounded down so a score just under the pass mark never reads as the pass mark (0.795 → "79%"). */
export const scorePercent = (fraction: number): number => Math.floor(clamp01(fraction) * 100 + 1e-9);

export const percentLabel = (fraction: number): string => `${scorePercent(fraction)}%`;

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
  /** "3 of 7 answered" / "Last question" / null when the plan isn't known. */
  remainingLabel: string | null;
  /** Spoken summary for the meter. */
  ariaText: string;
}

/** Score of the answered questions vs the pass mark, plus how much of the plan is left. */
export function scoreView(progress: Partial<TaskProgress>): ScoreView {
  const passMark = passMarkOf(progress);
  const maxPoints = num(progress.max_points);
  const hasScore = maxPoints > 0;
  const fraction = hasScore ? clamp01(num(progress.score) || num(progress.points) / maxPoints) : 0;
  const answered = Math.max(0, Math.round(num(progress.answered)));
  const planned = Math.max(0, Math.round(num(progress.planned)));
  const label = hasScore ? `Score ${percentLabel(fraction)}` : "Score —";
  const passLabel = `pass at ${percentLabel(passMark)}`;
  let remainingLabel: string | null = null;
  if (planned > 0) {
    const left = Math.max(0, planned - answered);
    remainingLabel = left === 1 && answered > 0 ? "Last question" : `${Math.min(answered, planned)} of ${planned} answered`;
  }
  const tone = !hasScore ? "none" : fraction + 1e-9 >= passMark ? "pass" : "below";
  const ariaText = hasScore
    ? `Score ${percentLabel(fraction)} of the questions answered so far; ${passLabel}.`
    : `No answers scored yet; ${passLabel}.`;
  return { hasScore, fraction, label, passLabel, passMark, tone, answered, planned, remainingLabel, ariaText };
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
export function lessonEndNote(progress: Partial<TaskProgress>, plannedBefore = 0): LessonEnd {
  const view = scoreView(progress);
  const back = "it comes back in about 2 hours with fresh questions";
  const planned = Math.max(view.planned, Math.round(num(plannedBefore)));
  const halted = planned > 0 && view.answered < planned;
  if (halted) return { halted, text: `${percentLabel(view.passMark)} is no longer reachable in this attempt — ${back}.` };
  const score = view.hasScore ? `Your score, ${percentLabel(view.fraction)}, is` : "Your score is";
  return { halted, text: `${score} below the ${percentLabel(view.passMark)} pass mark — ${back}.` };
}

const PAUSE_SAID = /\bpauses? here\b|\bout of reach\b|\bno longer reachable\b/i;

/** What the answer changed in the task: a knowledge point finished, the end of the task. (The server's feedback
 * announces an extra question after a miss and the capstone; they are not said twice.) */
export function progressNote(
  before: Pick<Task, "type" | "progress">,
  after: Pick<Task, "type" | "phase" | "outcome" | "progress">,
  /** The server's text shown with it (feedback, suggestion): when it already says the lesson pauses, not twice. */
  serverFeedback = "",
): string | null {
  if (after.phase === "done") {
    if (after.outcome === "passed") {
      if (before.type !== "lesson") return "That completes the review.";
      const view = scoreView(after.progress);
      return view.hasScore ? `That completes the lesson — score ${percentLabel(view.fraction)}.` : "That completes the lesson.";
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
  if (a.in_capstone && !b.in_capstone) return null; // the server's feedback announces the capstone
  if (a.kp_index > b.kp_index) return "Knowledge point complete — on to the next step.";
  if (a.kp_index < b.kp_index) return "Stepping back to an earlier knowledge point.";
  // An extra question after a miss is announced by the server's feedback; the next card says "extra question".
  return null;
}
