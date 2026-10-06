// Multistep questions (kind "multistep"; the lesson's capstone): one problem, 2–4 guided steps answered one at a time.
// The learner sees each step's result before the next one; answered steps stay above as a compact trail.
// The server records every step (QuestionPublic.step_index / step_results), so a reload resumes at the right step.
import type { AnswerResult, QuestionPublic, StepResult } from "../types";
import { halves } from "./score.ts";

export interface StepTrail {
  total: number;
  /** Answered steps, ordered by index. */
  results: StepResult[];
  /** The next step to answer (== total once every step is answered). */
  current: number;
  /** A step answered in this view whose result is still shown in full, until "Next step". */
  open: number | null;
}

export type TrailAction = { type: "answered"; result: StepResult } | { type: "next" };

type TrailSource = Pick<QuestionPublic, "steps"> & Partial<Pick<QuestionPublic, "step_index" | "step_results">>;

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n));

/** One result per step (the latest wins), only for steps that exist, in step order. */
function normalize(results: readonly StepResult[], total: number): StepResult[] {
  const byIndex = new Map<number, StepResult>();
  for (const r of results) if (Number.isInteger(r.index) && r.index >= 0 && r.index < total) byIndex.set(r.index, r);
  return [...byIndex.values()].sort((a, b) => a.index - b.index);
}

/** The trail as the server left it (fresh question, or a reload in the middle of the problem). */
export function initialTrail(question: TrailSource): StepTrail {
  const total = question.steps.length;
  const results = normalize(question.step_results ?? [], total);
  const current = clamp(Math.max(Math.round(question.step_index ?? 0), results.length), 0, total);
  return { total, results, current, open: null };
}

export function trailReducer(state: StepTrail, action: TrailAction): StepTrail {
  switch (action.type) {
    case "answered": {
      const { index } = action.result;
      if (!Number.isInteger(index) || index < 0 || index >= state.total) return state;
      const results = normalize([...state.results.filter((r) => r.index !== index), action.result], state.total);
      return { ...state, results, current: clamp(Math.max(state.current, index + 1), 0, state.total), open: index };
    }
    case "next":
      return state.open === null ? state : { ...state, open: null };
  }
}

/** The step whose input is showing: none while a just-answered step waits for "Next step", or when all are done. */
export const activeStep = (state: StepTrail): number | null => (state.open === null && state.current < state.total ? state.current : null);

/** The steps shown compactly above the current one (everything answered except the step still open in full). */
export const trailItems = (state: StepTrail): StepResult[] => state.results.filter((r) => r.index !== state.open);

export const allAnswered = (state: StepTrail): boolean => state.results.length >= state.total && state.total > 0;

/**
 * The step was answered with "Show solution" (0 points): the server's flag (StepResult.revealed), else — older servers —
 * what its result says: right but worth nothing, or the stock "Answer viewed" verdict.
 */
export function stepRevealed(result: Pick<StepResult, "correct" | "score" | "feedback"> & { revealed?: boolean | null }): boolean {
  if (typeof result.revealed === "boolean") return result.revealed;
  if ((result.score || 0) > 0) return false;
  return result.correct || /^\s*Answer viewed\b/i.test(result.feedback) || /\banswer was shown first\b/i.test(result.feedback);
}

/** Steps answered with "Show solution", by index (rebuilt from the server's results after a reload). */
export const revealedStepIndexes = (results: readonly StepResult[]): Set<number> =>
  new Set(results.filter((r) => stepRevealed(r)).map((r) => r.index));

export type StepMark = "full" | "half" | "none" | "shown";

/** `revealed`: the step's solution was shown (0 points) — said as such, not as a wrong answer. */
export const stepMark = (score: number, revealed = false): StepMark =>
  score >= 1 ? "full" : score > 0 ? "half" : revealed ? "shown" : "none";

export const STEP_MARK_GLYPH: Record<StepMark, string> = { full: "✓", half: "½", none: "✗", shown: "–" };

export const STEP_MARK_LABEL: Record<StepMark, string> = {
  full: "right",
  half: "right with the hint",
  none: "not right",
  shown: "solution shown (0 points)",
};

/** Step points earned so far (1 / 0.5 / 0 each). */
export const earnedSteps = (results: readonly StepResult[]): number => results.reduce((sum, r) => sum + Math.min(1, Math.max(0, r.score || 0)), 0);

/** "2½ of 3 steps". */
export const stepsSummary = (results: readonly StepResult[], total: number): string => `${halves(earnedSteps(results))} of ${total} steps`;

/** Pips over the steps: answered ones carry their mark, then the current one, then the rest. `revealed`: steps whose
 * solution was shown. */
export function stepPips(state: StepTrail, revealed: ReadonlySet<number> = new Set()): (StepMark | "current" | "todo")[] {
  const byIndex = new Map(state.results.map((r) => [r.index, r]));
  return Array.from({ length: state.total }, (_, i) => {
    const r = byIndex.get(i);
    if (r) return stepMark(r.score, revealed.has(i));
    return i === activeStep(state) ? "current" : "todo";
  });
}

/** The step's result from an answer response; older servers without `step_result` still return the verdict. */
export function stepResultOf(result: AnswerResult, index: number): StepResult {
  if (result.step_result) return result.step_result;
  const score = result.correct ? (result.independent ? 1 : 0.5) : 0;
  return { index, correct: result.correct, score, feedback: result.feedback, answer_display: result.correct_answer, explanation: result.solution };
}

const STEPS_RECAP = /\bProblem complete:\s*\d+\s+of\s+\d+\s+steps?\s+right\.?/i;

/**
 * What the server says the finished multistep question changed in the task — "One more question on this step comes
 * next…", "Last: one problem that puts the lesson together…", "Lesson passed with 91%." — without the last step's
 * verdict and the steps recap (the card shows both). `AnswerResult.note` when sent; older servers: the text after
 * "Problem complete: n of m steps right." in the feedback.
 */
export function multistepNote(result: Pick<AnswerResult, "feedback"> & { note?: string | null }): string {
  if (typeof result.note === "string") return result.note.trim();
  const recap = STEPS_RECAP.exec(result.feedback);
  return recap ? result.feedback.slice(recap.index + recap[0].length).trim() : "";
}

/** The response finished the whole question (the last step, or the server ended it). */
export const questionDone = (result: AnswerResult): boolean => result.status !== "step" && result.question_done !== false;
