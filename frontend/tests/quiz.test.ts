// Quizzes (src/lib/quiz.ts, SPEC §6.7): the countdown synced to the server, and the results summary.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  QUIZ_MARK_GLYPH,
  activeConflict,
  clockAnnouncement,
  clockTone,
  durationLabel,
  formatCountdown,
  isOptionalRetakeIntro,
  isQuizFirstMessage,
  isRetakeOption,
  localDeadline,
  noAnswerLabel,
  openQuizId,
  rememberOpenQuiz,
  forgetOpenQuiz,
  optionKey,
  optionMinutes,
  optionTitle,
  quizChipLabel,
  quizContentLock,
  quizGate,
  quizMinutes,
  quizPosition,
  quizScoreView,
  quizSummary,
  resultMark,
  secondsLeft,
  syncDeadline,
  taskTitle,
} from "../src/lib/quiz.ts";
import type { QuizInfo, QuizQuestionResult, SkillRef, Task, TaskOption, TaskProgress } from "../src/types.ts";

const skill = (key: string): SkillRef => ({ id: `id-${key}`, key, title: `Skill ${key}`, status: "learned" });

const quizInfo = (over: Partial<QuizInfo> = {}): QuizInfo => ({
  kind: "quiz",
  question_count: 7,
  time_limit_seconds: 540,
  started_at: "2026-10-06T10:00:00Z",
  deadline: "2026-10-06T10:09:00Z",
  seconds_left: 540,
  skills: [],
  results: [],
  missed_skills: [],
  retake_available: false,
  ...over,
});

const result = (answered: boolean, score: number, over: Partial<QuizQuestionResult> = {}): QuizQuestionResult => ({
  question_id: Math.random().toString(36).slice(2),
  skill: skill("S1"),
  kind: "numeric",
  prompt: "Compute it.",
  choices: [],
  response: answered ? "2" : null,
  answered,
  score,
  correct_answer: "2",
  solution: "Because.",
  seconds: 30,
  ...over,
});

// ---------- the countdown ----------

test("formatCountdown: m:ss, partial seconds round up, 0:00 only when the time is over", () => {
  assert.equal(formatCountdown(540), "9:00");
  assert.equal(formatCountdown(539.2), "9:00"); // a fresh 9-minute clock never starts at 8:59
  assert.equal(formatCountdown(538.9), "8:59");
  assert.equal(formatCountdown(60), "1:00");
  assert.equal(formatCountdown(59.01), "1:00");
  assert.equal(formatCountdown(59), "0:59");
  assert.equal(formatCountdown(0.2), "0:01");
  assert.equal(formatCountdown(0), "0:00");
  assert.equal(formatCountdown(-4), "0:00");
  assert.equal(formatCountdown(Number.NaN), "0:00");
  assert.equal(formatCountdown(3723), "1:02:03");
});

test("durationLabel / quizMinutes: the time limit in words", () => {
  assert.equal(durationLabel(540), "9 minutes");
  assert.equal(durationLabel(60), "1 minute");
  assert.equal(durationLabel(61), "2 minutes");
  assert.equal(durationLabel(45), "45 seconds");
  assert.equal(quizMinutes({ time_limit_seconds: 540 }), 9);
  assert.equal(quizMinutes({ time_limit_seconds: 810 }), 14); // 13.5 → whole minutes, rounded up
  assert.equal(quizMinutes({ time_limit_seconds: 0 }), 1);
});

test("localDeadline: counted from the server's seconds_left at arrival, so a wrong device clock doesn't matter", () => {
  const arrived = 1_000_000;
  // The device clock is an hour off: the ISO deadline would be meaningless here, seconds_left is not.
  assert.equal(localDeadline(quizInfo({ seconds_left: 300, deadline: "1999-01-01T00:00:00Z" }), arrived), arrived + 300_000);
  assert.equal(localDeadline(quizInfo({ seconds_left: -3 }), arrived), arrived); // already over
  // Without seconds_left the ISO deadline is the fallback.
  assert.equal(localDeadline(quizInfo({ seconds_left: null }), arrived), Date.parse("2026-10-06T10:09:00Z"));
  // Before "Start the clock" there is no deadline at all.
  assert.equal(localDeadline(quizInfo({ started_at: null, deadline: null, seconds_left: 540 }), arrived), null);
  assert.equal(localDeadline(quizInfo({ seconds_left: null, deadline: "garbage" }), arrived), null);
});

test("syncDeadline: whole-second rounding and network delay don't make the countdown jump; a real correction does", () => {
  const t0 = 5_000_000;
  // Started: 540 s left, received at t0.
  let deadline = syncDeadline(null, t0 + 540_000);
  assert.equal(deadline, t0 + 540_000);
  // 30.4 s later an answer comes back with seconds_left 509 (the server floored 509.6): 600 ms apart — keep the clock.
  deadline = syncDeadline(deadline, localDeadline(quizInfo({ seconds_left: 509 }), t0 + 30_400));
  assert.equal(deadline, t0 + 540_000);
  // The device slept for a while and its timers drifted: the server says only 200 s are left at t0 + 330 s.
  deadline = syncDeadline(deadline, localDeadline(quizInfo({ seconds_left: 200 }), t0 + 330_000));
  assert.equal(deadline, t0 + 530_000);
  // No reading keeps what is on screen.
  assert.equal(syncDeadline(deadline, null), t0 + 530_000);
  assert.equal(syncDeadline(null, null), null);
});

test("secondsLeft / clockTone / clockAnnouncement: warn for the last minute, say it once", () => {
  assert.equal(secondsLeft(10_000, 4_000), 6);
  assert.equal(secondsLeft(10_000, 12_000), 0);
  assert.equal(clockTone(null), "calm");
  assert.equal(clockTone(61), "calm");
  assert.equal(clockTone(60), "warning");
  assert.equal(clockTone(0.4), "warning");
  assert.equal(clockTone(0), "over");
  assert.equal(clockAnnouncement(60.2, 59.9), "One minute left.");
  assert.equal(clockAnnouncement(59.9, 59.6), null); // only when crossing
  assert.equal(clockAnnouncement(0.1, 0), "Time is up.");
  assert.equal(clockAnnouncement(0, 0), null);
  assert.equal(clockAnnouncement(null, 30), null);
  // A jump from far above straight past zero says time is up, not "one minute left".
  assert.equal(clockAnnouncement(120, 0), "Time is up.");
});

test("quizPosition: the next question, passed ones included, never past the last", () => {
  const progress = { answered: 2, total_questions: 7 } as TaskProgress;
  assert.deepEqual(quizPosition({ progress, quiz: quizInfo() }), { index: 3, total: 7 });
  assert.deepEqual(quizPosition({ progress: { ...progress, answered: 7 }, quiz: quizInfo() }), { index: 7, total: 7 });
  assert.deepEqual(quizPosition({ progress: { ...progress, answered: 0 }, quiz: null }), { index: 1, total: 7 });
});

// ---------- results ----------

test("resultMark: right, not right, or passed (no answer)", () => {
  assert.equal(resultMark({ answered: true, score: 1 }), "correct");
  assert.equal(resultMark({ answered: true, score: 0 }), "incorrect");
  assert.equal(resultMark({ answered: false, score: 0 }), "passed");
  assert.equal(QUIZ_MARK_GLYPH.correct, "✓");
  assert.equal(QUIZ_MARK_GLYPH.incorrect, "✗");
  assert.equal(QUIZ_MARK_GLYPH.passed, "–");
  // Unanswered: passed / on screen when the time ran out, or never reached (the server logs 0 s for those).
  assert.equal(noAnswerLabel({ seconds: 12.5 }), "No answer");
  assert.equal(noAnswerLabel({ seconds: 0 }), "Not reached");
});

test("quizSummary: counts, score and the 80 % pass mark", () => {
  const results = [result(true, 1), result(true, 1), result(true, 1), result(true, 1), result(true, 0), result(false, 0), result(true, 1)];
  const summary = quizSummary(results);
  assert.equal(summary.total, 7);
  assert.equal(summary.correct, 5);
  assert.equal(summary.incorrect, 1);
  assert.equal(summary.passed, 1);
  assert.ok(Math.abs(summary.fraction - 5 / 7) < 1e-9);
  assert.equal(summary.pass, false); // 71 % < 80 %
  assert.equal(summary.countLabel, "5 of 7 right");
  assert.equal(summary.breakdown, "5 right · 1 not right · 1 unanswered");

  // Exactly 80 % passes.
  const eight = [...Array(8)].map(() => result(true, 1)).concat([result(true, 0), result(false, 0)]);
  assert.equal(quizSummary(eight).pass, true);
  // All right: no "not right" / "passed" parts.
  assert.equal(quizSummary([result(true, 1), result(true, 1)]).breakdown, "2 right");
  // No results (older server): the task's score is used.
  const empty = quizSummary([], 0.85);
  assert.equal(empty.total, 0);
  assert.equal(empty.fraction, 0.85);
  assert.equal(empty.pass, true);
  assert.equal(empty.countLabel, "");
});

test("quizScoreView: the shared meter, never rounding a fail up onto the pass mark", () => {
  const atMark = quizScoreView(quizSummary([result(true, 1), result(true, 1), result(true, 1), result(true, 1), result(true, 0)]));
  assert.equal(atMark.tone, "pass"); // 4 of 5 = 80 %
  assert.equal(atMark.label, "Score 80%");
  assert.equal(atMark.passLabel, "pass at 80%");
  const fail = quizScoreView(quizSummary([...Array(79)].map(() => result(true, 1)).concat([...Array(21)].map(() => result(true, 0)))));
  assert.equal(fail.tone, "below");
  assert.equal(fail.label, "Score 79%");
  assert.equal(fail.remainingLabel, "79 of 100 right");
  assert.equal(quizScoreView(quizSummary([])).tone, "none");
});

// ---------- the course page and menus ----------

test("isQuizFirstMessage: the server's 409 while a quiz is due", () => {
  assert.equal(isQuizFirstMessage("Take the quiz first — about 9 minutes."), true);
  assert.equal(isQuizFirstMessage("Finish the quiz first — about 4 minutes left."), true);
  assert.equal(isQuizFirstMessage("Finish or leave the quiz first."), true);
  // engine.abandon refusing to leave a quiz (or a retake whose clock runs): information, not an error.
  assert.equal(isQuizFirstMessage("A quiz can't be left — finish it (the clock keeps running once it has started)."), true);
  // An optional retake at its intro can be left: its 409 opens the "Leave it and start" dialog instead (regression:
  // it used to be a dead-end info toast).
  assert.equal(isQuizFirstMessage("Finish or leave the quiz retake first."), false);
  assert.equal(isQuizFirstMessage("This lesson comes back at 14:43 (local time) with fresh questions."), false);
  assert.equal(isQuizFirstMessage("Finish or leave 'Complex numbers' first."), false);
});

test("activeConflict: the open task a 409 names — a skill in quotes, or the optional retake", () => {
  assert.deepEqual(activeConflict("Finish or leave 'Complex numbers' first."), { activeTitle: "Complex numbers", activeType: null });
  assert.deepEqual(activeConflict("Finish or leave ‘Vector spaces’ first"), { activeTitle: "Vector spaces", activeType: null });
  assert.deepEqual(activeConflict(" Finish or leave the quiz retake first. "), { activeTitle: "Quiz retake", activeType: "quiz" });
  assert.equal(activeConflict("Finish or leave the quiz first."), null); // a regular quiz can't be left
  assert.equal(activeConflict("Take the quiz first — about 9 minutes."), null);
  assert.equal(activeConflict("This lesson comes back at 14:43 (local time) with fresh questions."), null);
});

test("menu options: a quiz has no skill — its own key, title and minutes", () => {
  const quiz: TaskOption = { skill: null, type: "quiz", reason: "Quiz due — 7 skills since the last one", xp_value: 9, importance: 99 };
  const retake: TaskOption = { ...quiz, reason: "Optional retake of the last quiz" };
  const lesson: TaskOption = { skill: skill("S4"), type: "lesson", reason: "Next new skill", xp_value: 12, importance: 2 };
  assert.equal(optionKey(quiz), "quiz:quiz");
  assert.equal(optionKey(lesson), "id-S4:lesson");
  assert.equal(optionTitle(quiz), "Quiz");
  assert.equal(optionTitle(retake), "Quiz retake");
  assert.equal(optionTitle(lesson), "Skill S4");
  assert.equal(isRetakeOption(retake), true);
  assert.equal(isRetakeOption(quiz), false);
  assert.equal(isRetakeOption({ ...lesson, reason: "Retake of an old idea" }), false); // a skill option is never the retake
  assert.equal(optionMinutes(quiz), 9);
  assert.equal(optionMinutes({ xp_value: 0 }), null);
});

test("taskTitle: the skill's title, or Quiz / Quiz retake", () => {
  assert.equal(taskTitle({ type: "lesson", skill: skill("S2"), quiz: null }), "Skill S2");
  assert.equal(taskTitle({ type: "quiz", skill: null, quiz: quizInfo() }), "Quiz");
  assert.equal(taskTitle({ type: "quiz", skill: null, quiz: quizInfo({ kind: "retake" }) }), "Quiz retake");
});

const openTask = (type: Task["type"], phase: Task["phase"] = "practice", kind: QuizInfo["kind"] | null = type === "quiz" ? "quiz" : null) => ({
  type,
  phase,
  quiz: kind ? quizInfo({ kind, started_at: phase === "learn" ? null : "2026-10-06T10:00:00Z" }) : null,
});

test("quizGate: a due or open quiz blocks other starts; the session wins over the course stats", () => {
  const open = openTask;
  // Not due: "Quiz in 40 XP".
  let gate = quizGate({ quiz_due: false, xp_until_quiz: 40 }, { quiz_due: true, xp_until_quiz: 0 }, null);
  assert.deepEqual(gate, { due: false, active: false, optionalRetake: false, xpUntil: 40, blocksOthers: false });
  assert.equal(quizChipLabel(gate), "Quiz in 40 XP");
  // Not enough skills learned yet: no chip.
  gate = quizGate({ quiz_due: false, xp_until_quiz: null }, null, null);
  assert.equal(quizChipLabel(gate), null);
  // Due (from the stats while /next hasn't loaded): forced.
  gate = quizGate(undefined, { quiz_due: true, xp_until_quiz: 0 }, null);
  assert.deepEqual(gate, { due: true, active: false, optionalRetake: false, xpUntil: null, blocksOthers: true });
  assert.equal(quizChipLabel(gate), "Quiz due");
  // Due while a lesson is open: the lesson may be finished first, but nothing new can start.
  gate = quizGate({ quiz_due: true, xp_until_quiz: 0 }, null, open("lesson"));
  assert.equal(gate.blocksOthers, true);
  assert.equal(gate.active, false);
  // An open quiz (the server may no longer call it "due").
  gate = quizGate({ quiz_due: false, xp_until_quiz: 150 }, null, open("quiz", "learn"));
  assert.deepEqual(gate, { due: true, active: true, optionalRetake: false, xpUntil: null, blocksOthers: true });
  assert.equal(quizChipLabel(gate), "Quiz in progress");
  // A task without quiz info (an older server) is taken as a regular quiz: forced.
  gate = quizGate({ quiz_due: false, xp_until_quiz: 150 }, null, { type: "quiz", phase: "learn" });
  assert.equal(gate.blocksOthers, true);
  // A finished quiz doesn't block anything.
  gate = quizGate({ quiz_due: false, xp_until_quiz: 150 }, null, open("quiz", "done"));
  assert.equal(gate.blocksOthers, false);
});

test("quizGate: an optional retake at its intro blocks nothing; once its clock runs it does (SPEC §6.7: not forced)", () => {
  // Regression: opening the retake's intro used to lock the course page (menu swapped for the retake, Learn/Review
  // disabled, starts refused) although the server says nothing is due and lets the retake be left.
  let gate = quizGate({ quiz_due: false, xp_until_quiz: 150 }, null, openTask("quiz", "learn", "retake"));
  assert.deepEqual(gate, { due: false, active: true, optionalRetake: true, xpUntil: 150, blocksOthers: false });
  assert.equal(quizChipLabel(gate), "Optional retake open");
  // The clock is running: it can't be left any more, so it comes first.
  gate = quizGate({ quiz_due: false, xp_until_quiz: 150 }, null, openTask("quiz", "practice", "retake"));
  assert.deepEqual(gate, { due: true, active: true, optionalRetake: false, xpUntil: null, blocksOthers: true });
  assert.equal(quizChipLabel(gate), "Quiz in progress");
  // A finished retake: nothing open.
  gate = quizGate({ quiz_due: false, xp_until_quiz: 150 }, null, openTask("quiz", "done", "retake"));
  assert.deepEqual(gate, { due: false, active: false, optionalRetake: false, xpUntil: 150, blocksOthers: false });
  // Should the server call a quiz due meanwhile, that wins.
  gate = quizGate({ quiz_due: true, xp_until_quiz: 0 }, null, openTask("quiz", "learn", "retake"));
  assert.equal(gate.blocksOthers, true);
  assert.equal(quizChipLabel(gate), "Quiz in progress");
});

test("isOptionalRetakeIntro: only a retake whose clock hasn't started", () => {
  assert.equal(isOptionalRetakeIntro(openTask("quiz", "learn", "retake")), true);
  assert.equal(isOptionalRetakeIntro(openTask("quiz", "practice", "retake")), false);
  assert.equal(isOptionalRetakeIntro(openTask("quiz", "done", "retake")), false);
  assert.equal(isOptionalRetakeIntro(openTask("quiz", "learn", "quiz")), false);
  assert.equal(isOptionalRetakeIntro(openTask("lesson", "learn")), false);
  assert.equal(isOptionalRetakeIntro(null), false);
});

test("quizContentLock: no lesson preview or book page while a quiz or retake is open (SPEC §6.7)", () => {
  // Regression: the skill drawer's "Preview lesson" and page buttons stayed open while the quiz clock ran.
  for (const task of [openTask("quiz", "learn"), openTask("quiz", "practice"), openTask("quiz", "practice", "retake")]) {
    const lock = quizContentLock(task);
    assert.ok(lock, `${task.quiz?.kind} in ${task.phase}`);
    assert.equal(lock.lesson, "Lessons open again when the quiz is over");
    assert.equal(lock.source, "The book opens again when the quiz is over");
  }
  // The retake's intro already lists the quizzed skills: closed too, until the retake is left.
  const intro = quizContentLock(openTask("quiz", "learn", "retake"));
  assert.ok(intro);
  assert.match(intro.lesson, /^Lessons open again when the quiz is over \(or once you leave the retake\)$/);
  assert.match(intro.note, /leave the retake to read them/);
  // Nothing closed otherwise.
  assert.equal(quizContentLock(null), null);
  assert.equal(quizContentLock(openTask("quiz", "done")), null);
  assert.equal(quizContentLock(openTask("lesson", "practice")), null);
  assert.equal(quizContentLock(openTask("review", "practice")), null);
});

test("a quiz that ran out while away: remembered per course until its results were shown", () => {
  const data = new Map<string, string>();
  const storage = {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
  assert.equal(openQuizId("c1", storage), null);
  rememberOpenQuiz("c1", "quiz-7", storage);
  assert.equal(openQuizId("c1", storage), "quiz-7");
  assert.equal(openQuizId("c2", storage), null); // per course
  forgetOpenQuiz("c1", storage);
  assert.equal(openQuizId("c1", storage), null);
  // Blocked storage (private window, previews): never throws, nothing remembered.
  const blocked = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
    removeItem: () => {
      throw new Error("blocked");
    },
  };
  rememberOpenQuiz("c1", "quiz-7", blocked);
  assert.equal(openQuizId("c1", blocked), null);
  forgetOpenQuiz("c1", blocked);
  assert.equal(openQuizId("c1", null), null);
});
