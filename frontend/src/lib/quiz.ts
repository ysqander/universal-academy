// Quizzes (SPEC §6.7): a timed, forced, cumulative check about every 150 XP — 6–8 single-answer questions, one per
// skill, no help, correctness shown only at the end, pass mark 80 %. The clock runs on the server; the page counts
// down from the server's `seconds_left` and asks the server again when it reaches zero. Pure helpers only.
import type { CourseStats, QuizInfo, QuizQuestionResult, SessionInfo, Task, TaskOption, TaskType } from "../types";
import { percentLabel, type ScoreView } from "./score.ts";

export const QUIZ_PASS_MARK = 0.8;
/** The countdown turns to a warning for the last minute. */
export const QUIZ_WARN_SECONDS = 60;

const pad2 = (n: number): string => n.toString().padStart(2, "0");

/**
 * Time left as "9:00", "0:59", "0:00" ("1:02:03" past an hour). Partial seconds round up, so "0:00" appears only once
 * the time is really over and a fresh 9-minute clock reads "9:00", not "8:59".
 */
export function formatCountdown(seconds: number): string {
  const whole = Number.isFinite(seconds) ? Math.max(0, Math.ceil(seconds - 1e-6)) : 0;
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h > 0 ? `${h}:${pad2(m)}:${pad2(s)}` : `${m}:${pad2(s)}`;
}

/** "9 minutes", "1 minute", "45 seconds" — for the intro and the time-limit line. */
export function durationLabel(seconds: number): string {
  const s = Math.max(0, Math.round(Number.isFinite(seconds) ? seconds : 0));
  if (s < 60) return `${s} ${s === 1 ? "second" : "seconds"}`;
  const minutes = Math.ceil(s / 60 - 1e-9);
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

/** The time limit in whole minutes (the server already rounds it up to whole minutes). */
export const quizMinutes = (quiz: Pick<QuizInfo, "time_limit_seconds">): number =>
  Math.max(1, Math.ceil(Math.max(0, quiz.time_limit_seconds || 0) / 60 - 1e-9));

/**
 * When the running clock reaches zero, on this device's clock (ms since the epoch). Counted from the server's
 * `seconds_left` at the moment the response arrived (`receivedAt`), so a device clock that is minutes off doesn't
 * matter; the ISO `deadline` is used only when `seconds_left` is missing. Null while the clock hasn't started.
 */
export function localDeadline(quiz: Pick<QuizInfo, "started_at" | "deadline" | "seconds_left">, receivedAt: number): number | null {
  if (!quiz.started_at && !quiz.deadline) return null;
  if (typeof quiz.seconds_left === "number" && Number.isFinite(quiz.seconds_left)) {
    return receivedAt + Math.max(0, quiz.seconds_left) * 1000;
  }
  if (quiz.deadline) {
    const at = Date.parse(quiz.deadline);
    if (Number.isFinite(at)) return at;
  }
  return null;
}

/**
 * A fresh server reading of the deadline (after every answer) against the one on screen. Whole-second rounding and
 * network delay make readings differ by up to about a second: keep the current one then, so the countdown never
 * jumps back and forth; a larger difference (the server knows better, e.g. the device slept) is taken as it is.
 */
export function syncDeadline(current: number | null, reading: number | null, toleranceMs = 1000): number | null {
  if (reading === null) return current;
  if (current === null) return reading;
  return Math.abs(reading - current) <= toleranceMs ? current : reading;
}

/** Seconds left (fractional, never negative). */
export const secondsLeft = (deadline: number, now: number): number => Math.max(0, (deadline - now) / 1000);

export type ClockTone = "calm" | "warning" | "over";

/** Calm, then a warning for the last minute, then over. */
export function clockTone(seconds: number | null): ClockTone {
  if (seconds === null) return "calm";
  if (seconds <= 0) return "over";
  return seconds <= QUIZ_WARN_SECONDS ? "warning" : "calm";
}

/** What a screen reader hears when the clock crosses a threshold (once each), else null. */
export function clockAnnouncement(before: number | null, now: number | null): string | null {
  if (before === null || now === null) return null;
  if (before > 0 && now <= 0) return "Time is up.";
  if (before > QUIZ_WARN_SECONDS && now <= QUIZ_WARN_SECONDS && now > 0) return "One minute left.";
  return null;
}

/** "Question 3 of 7": the next unanswered question (passed questions count as answered). */
export function quizPosition(task: Pick<Task, "progress" | "quiz">): { index: number; total: number } {
  const total = Math.max(1, task.quiz?.question_count || task.progress.total_questions || 1);
  const index = Math.min(Math.max(1, Math.round(task.progress.answered || 0) + 1), total);
  return { index, total };
}

// ---------- results ----------

export type QuizMark = "correct" | "partial" | "incorrect" | "passed";

/** ✓ right, ◐ partly right (some statements of a true/false set), ✗ wrong, – passed (or the time ran out first). */
export function resultMark(result: Pick<QuizQuestionResult, "answered" | "score">): QuizMark {
  if (!result.answered) return "passed";
  if (result.score >= 1 - 1e-9) return "correct";
  return result.score > 1e-9 ? "partial" : "incorrect";
}

export const QUIZ_MARK_GLYPH: Record<QuizMark, string> = { correct: "✓", partial: "◐", incorrect: "✗", passed: "–" };
export const QUIZ_MARK_LABEL: Record<QuizMark, string> = { correct: "Right", partial: "Partly right", incorrect: "Not right", passed: "No answer" };

/** An unanswered question: passed (or on screen when the time ran out), or never reached (no time spent on it). */
export const noAnswerLabel = (result: Pick<QuizQuestionResult, "seconds">): string =>
  (result.seconds || 0) > 0 ? "No answer" : "Not reached";

export interface QuizSummary {
  total: number;
  correct: number;
  incorrect: number;
  /** Not answered: passed, or the time ran out first. */
  passed: number;
  /** Score 0–1 (each question 1, its share of right statements, or 0). */
  fraction: number;
  /** Reached the pass mark (80 %). */
  pass: boolean;
  /** "5 of 7 right". */
  countLabel: string;
  /** "5 right · 1 not right · 1 unanswered" (zero counts left out). */
  breakdown: string;
}

/**
 * Totals of a finished quiz from its per-question results. `fallbackScore` (Task.progress.score) is used only when
 * the results list is empty (e.g. an older server).
 */
export function quizSummary(results: readonly Pick<QuizQuestionResult, "answered" | "score">[], fallbackScore = 0): QuizSummary {
  const marks = results.map(resultMark);
  const correct = marks.filter((m) => m === "correct").length;
  const partial = marks.filter((m) => m === "partial").length;
  const incorrect = marks.filter((m) => m === "incorrect").length;
  const passed = marks.filter((m) => m === "passed").length;
  const total = results.length;
  const points = results.reduce((sum, r) => sum + Math.min(1, Math.max(0, Number.isFinite(r.score) ? r.score : 0)), 0);
  const fraction = total > 0 ? points / total : Math.min(1, Math.max(0, Number.isFinite(fallbackScore) ? fallbackScore : 0));
  const parts = [
    correct ? `${correct} right` : "",
    partial ? `${partial} partly right` : "",
    incorrect ? `${incorrect} not right` : "",
    passed ? `${passed} unanswered` : "",
  ].filter(Boolean);
  return {
    total,
    correct,
    incorrect,
    passed,
    fraction,
    pass: fraction + 1e-9 >= QUIZ_PASS_MARK,
    countLabel: total > 0 ? `${correct} of ${total} right` : "",
    breakdown: parts.join(" · "),
  };
}

/** The finished quiz on the shared score meter (pass mark drawn at 80 %). */
export function quizScoreView(summary: QuizSummary): ScoreView {
  const hasScore = summary.total > 0 || summary.fraction > 0;
  const label = hasScore ? `Score ${percentLabel(summary.fraction, QUIZ_PASS_MARK)}` : "Score —";
  const passLabel = `pass at ${percentLabel(QUIZ_PASS_MARK)}`;
  return {
    hasScore,
    fraction: summary.fraction,
    fill: hasScore ? summary.fraction : 0,
    label,
    passLabel,
    passMark: QUIZ_PASS_MARK,
    tone: !hasScore ? "none" : summary.pass ? "pass" : "below",
    answered: summary.total - summary.passed,
    planned: summary.total,
    remainingLabel: summary.countLabel || null,
    halted: false,
    ariaText: hasScore ? `Quiz score ${percentLabel(summary.fraction, QUIZ_PASS_MARK)}; ${passLabel}.` : `No score; ${passLabel}.`,
  };
}

// ---------- quiz state on the course page and in menus ----------

/**
 * selector.start_task's 409s while a quiz is due or open — "Take the quiz first — about 9 minutes.", "Finish the quiz
 * first — about 4 minutes left.", "Finish or leave the quiz first." — and engine.abandon's "A quiz can't be left — …".
 * Not "Finish or leave the quiz retake first.": an optional retake still at its intro can be left, so that one opens
 * the "Leave it and start" dialog like any other open task.
 */
const QUIZ_FIRST = /\b(?:take|finish|leave) the quiz first\b|\bquiz can[’']t be left\b/i;
export const isQuizFirstMessage = (message: string): boolean => QUIZ_FIRST.test(message);

/** selector.start_task's 409 while another task is open: "Finish or leave '<skill title>' first.", or "Finish or leave
 * the quiz retake first." for an optional retake at its intro (a regular quiz says "the quiz" — it can't be left). */
const ACTIVE_CONFLICT = /^Finish or leave (?:['‘’"“](.*)['‘’"”]|(the quiz retake)) first\.?$/s;

/** The open task a 409 names, for the "Leave it and start" dialog; null when the message is about something else. */
export function activeConflict(message: string): { activeTitle: string | null; activeType: TaskType | null } | null {
  const match = ACTIVE_CONFLICT.exec(message.trim());
  if (!match) return null;
  if (match[2]) return { activeTitle: "Quiz retake", activeType: "quiz" };
  return { activeTitle: match[1] || null, activeType: null };
}

/** Menu key of an option: "<skillId>:<type>", or "quiz:<type>" for a quiz (it has no skill). */
export const optionKey = (option: Pick<TaskOption, "skill" | "type">): string =>
  option.skill ? `${option.skill.id}:${option.type}` : `quiz:${option.type}`;

/** The quiz option's length in minutes: its XP value is the time limit in minutes (XP = minutes × score). */
export const optionMinutes = (option: Pick<TaskOption, "xp_value">): number | null =>
  option.xp_value > 0 ? Math.round(option.xp_value) : null;

/** An open task as the course page knows it (`quiz` may be missing on hand-made values). */
type OpenTaskLike = Pick<Task, "type" | "phase"> & { quiz?: Pick<QuizInfo, "kind"> | null };

/**
 * The open task is an optional retake whose clock hasn't started (phase "learn", its intro). It is not forced
 * (SPEC §6.7): it can be left ("Not now", or starting something else), and it blocks nothing.
 */
export const isOptionalRetakeIntro = (task: OpenTaskLike | null | undefined): boolean =>
  Boolean(task && task.type === "quiz" && task.quiz?.kind === "retake" && task.phase === "learn");

export interface QuizGate {
  /** A quiz must come next (forced): a due quiz, an open regular quiz, or a retake whose clock is running. Other tasks
   * can't be started (an open lesson/review may still be finished). */
  due: boolean;
  /** A quiz or retake is open (started, not finished) — its intro included. */
  active: boolean;
  /** The open quiz is an optional retake still at its intro: it can be left, and nothing else is blocked. */
  optionalRetake: boolean;
  /** XP still to earn before the next quiz; null when not enough skills were learned yet (or unknown). */
  xpUntil: number | null;
  /** Nothing but the quiz (or the open quiz) can be started now. */
  blocksOthers: boolean;
}

/**
 * Quiz state for the course page: the session (from /next, refreshed after every action) wins over the course stats
 * (loaded once). A due quiz doesn't block resuming an open lesson or review — that may be finished first. An optional
 * retake at its intro blocks nothing (the server doesn't call it due and lets it be left); once its clock runs it
 * can't be left, so then it blocks like a quiz.
 */
export function quizGate(
  session: Pick<SessionInfo, "quiz_due" | "xp_until_quiz"> | null | undefined,
  stats: Pick<CourseStats, "quiz_due" | "xp_until_quiz"> | null | undefined,
  active: OpenTaskLike | null | undefined,
): QuizGate {
  const openTask = active && active.phase !== "done" ? active : null;
  const quizOpen = openTask?.type === "quiz";
  const optionalRetake = isOptionalRetakeIntro(openTask);
  const due = Boolean(session ? session.quiz_due : stats?.quiz_due) || (quizOpen && !optionalRetake);
  const rawXp = session ? session.xp_until_quiz : stats?.xp_until_quiz;
  const xpUntil = typeof rawXp === "number" && Number.isFinite(rawXp) ? Math.max(0, Math.round(rawXp)) : null;
  return { due, active: quizOpen, optionalRetake, xpUntil: due ? null : xpUntil, blocksOthers: due };
}

/** Header chip: "Quiz in progress" / "Optional retake open" / "Quiz due" / "Quiz in 40 XP" / null (not enough skills
 * learned yet). */
export function quizChipLabel(gate: QuizGate): string | null {
  if (gate.active && gate.optionalRetake && !gate.due) return "Optional retake open";
  if (gate.active) return "Quiz in progress";
  if (gate.due) return "Quiz due";
  if (gate.xpUntil === null) return null;
  return gate.xpUntil <= 0 ? "Quiz soon" : `Quiz in ${gate.xpUntil} XP`;
}

/**
 * While a quiz or retake is open — its intro included, which already lists the quizzed skills — lessons and the book
 * are closed (SPEC §6.7: no lesson or source view). The reasons are shown as titles / notes; null = nothing closed.
 */
export interface QuizContentLock {
  lesson: string;
  source: string;
  /** One line for the skill drawer. */
  note: string;
}

export function quizContentLock(active: OpenTaskLike | null | undefined): QuizContentLock | null {
  if (!active || active.phase === "done" || active.type !== "quiz") return null;
  const leave = isOptionalRetakeIntro(active) ? " (or once you leave the retake)" : "";
  return {
    lesson: `Lessons open again when the quiz is over${leave}`,
    source: `The book opens again when the quiz is over${leave}`,
    note: `Lessons and book pages are closed while a quiz is open${leave ? " — leave the retake to read them" : ""}.`,
  };
}

/** The task's name in titles and bars: the skill's title, or "Quiz" / "Quiz retake". */
export function taskTitle(task: Pick<Task, "type" | "skill" | "quiz">): string {
  if (task.skill) return task.skill.title;
  if (task.type === "quiz") return task.quiz?.kind === "retake" ? "Quiz retake" : "Quiz";
  return "Task";
}

/** The menu option is the optional retake (read from the server's reason: "Optional retake of the quiz on …"). */
export const isRetakeOption = (option: Pick<TaskOption, "type" | "skill" | "reason">): boolean =>
  option.type === "quiz" && !option.skill && /\bretake\b/i.test(option.reason);

/** The menu option's name: the skill's title, or "Quiz" / "Quiz retake" (read from the server's reason). */
export function optionTitle(option: Pick<TaskOption, "type" | "skill" | "reason">): string {
  if (option.skill) return option.skill.title;
  if (option.type === "quiz") return isRetakeOption(option) ? "Quiz retake" : "Quiz";
  return "Task";
}

// ---------- a quiz that ran out while the learner was away ----------
// The server closes a quiz whose time ran out the next time anything is read (then /next no longer reports it as the
// open task). This browser remembers the quiz whose clock it showed, so its results can still be shown afterwards.
// Per-viewer convenience only: without storage the results are simply not offered again.

type QuizStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const openQuizKey = (courseId: string): string => `ua:open-quiz:${courseId}`;

function localStorageOrNull(): QuizStorage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** Remember the quiz whose clock is running in this course. */
export function rememberOpenQuiz(courseId: string, taskId: string, storage: QuizStorage | null = localStorageOrNull()): void {
  try {
    storage?.setItem(openQuizKey(courseId), taskId);
  } catch {
    // Storage full or blocked: nothing to remember.
  }
}

/** The quiz last seen running in this course, until its results have been shown. */
export function openQuizId(courseId: string, storage: QuizStorage | null = localStorageOrNull()): string | null {
  try {
    return storage?.getItem(openQuizKey(courseId)) || null;
  } catch {
    return null;
  }
}

/** Its results were shown (or it is gone): stop offering them. */
export function forgetOpenQuiz(courseId: string, storage: QuizStorage | null = localStorageOrNull()): void {
  try {
    storage?.removeItem(openQuizKey(courseId));
  } catch {
    // Nothing to forget.
  }
}
