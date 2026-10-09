// Score-based lesson progress and feedback copy (src/lib/score.ts, SPEC §6.3–§6.4).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  halves,
  hasCapstone,
  hintTotal,
  kpPosition,
  lessonEndNote,
  lessonStages,
  percentLabel,
  pointsLabel,
  progressNote,
  questionPoints,
  scorePercent,
  scoreView,
  stepPointsLabel,
  wasHalted,
  withoutPassNote,
} from "../src/lib/score.ts";
import type { TaskProgress } from "../src/types.ts";

const progress = (over: Partial<TaskProgress> = {}): TaskProgress => ({
  kp_index: 0,
  kp_total: 2,
  streak: 0,
  needed_in_row: 2,
  kp_questions_used: 1,
  kp_questions_available: 4,
  answered: 0,
  correct: 0,
  total_questions: 0,
  kp_wrong: 0,
  easier_available: false,
  kp_target: 2,
  points: 0,
  max_points: 0,
  score: 0,
  planned: 5,
  pass_mark: 0.8,
  in_capstone: false,
  ...over,
});

// The progress object an older server sends: no score fields at all (legacy lessons, no capstone).
const legacy = {
  kp_index: 1,
  kp_total: 2,
  streak: 1,
  needed_in_row: 2,
  kp_questions_used: 2,
  kp_questions_available: 4,
  answered: 3,
  correct: 2,
  total_questions: 0,
  kp_wrong: 1,
  easier_available: true,
} as unknown as TaskProgress;

test("halves: counts in halves with the ½ sign", () => {
  assert.equal(halves(0), "0");
  assert.equal(halves(0.5), "½");
  assert.equal(halves(1), "1");
  assert.equal(halves(2.5), "2½");
  assert.equal(halves(2.4), "2½");
  assert.equal(halves(3), "3");
  assert.equal(halves(-1), "0");
});

test("percentLabel: rounds half up, as the server does, so every screen shows the same number", () => {
  assert.equal(percentLabel(0.857), "86%");
  assert.equal(percentLabel(0.8), "80%");
  assert.equal(percentLabel(1), "100%");
  assert.equal(percentLabel(2 / 3), "67%");
  assert.equal(percentLabel(0.285), "29%");
  // The review that read "Review score 92%" on the card and "Score 91%" on the results page.
  assert.equal(percentLabel(11 / 12), "92%");
  assert.equal(percentLabel(Number.NaN), "0%");
  assert.equal(percentLabel(-0.2), "0%");
  // Never up onto the pass mark or 100 %.
  assert.equal(percentLabel(0.796), "79%");
  assert.equal(percentLabel(0.795), "79%");
  assert.equal(percentLabel(0.996), "99%");
  assert.equal(percentLabel(0.7999999999), "80%"); // within the server's EPS of the mark: it passes there too
  assert.equal(percentLabel(0.896, 0.9), "89%");
});

/** backend engine.score_percent, transcribed. */
const serverPercent = (score: number, passMark = 0.8): number => {
  const s = Math.max(0, Math.min(1, score));
  const pct = Math.floor(s * 100 + 0.5 + 1e-9);
  const mark = Math.round(passMark * 100);
  if (s < passMark - 1e-9 && pct >= mark) return mark - 1;
  if (s < 1 - 1e-9 && pct >= 100) return 99;
  return pct;
};

test("scorePercent matches the server's score_percent for every reachable score", () => {
  // Lesson scores are points / max points with points in halves, multistep steps in thirds and the capstone double.
  for (let max = 1; max <= 40; max++) {
    for (let sixths = 0; sixths <= max * 6; sixths++) {
      const score = sixths / 6 / max;
      assert.equal(scorePercent(score), serverPercent(score), `score ${sixths}/6 of ${max}`);
    }
  }
});

test("scoreView: score of the answered questions, the pass mark and what is left of the plan", () => {
  const start = scoreView(progress());
  assert.equal(start.hasScore, false);
  assert.equal(start.label, "Score —");
  assert.equal(start.passLabel, "pass at 80%");
  assert.equal(start.tone, "none");
  assert.equal(start.remainingLabel, "0 of 5 answered");

  const mid = scoreView(progress({ answered: 3, points: 2.5, max_points: 3, score: 2.5 / 3 }));
  assert.equal(mid.halted, false);
  assert.equal(mid.label, "Score 83%");
  assert.equal(mid.tone, "pass");
  assert.equal(mid.remainingLabel, "3 of 5 answered");

  const below = scoreView(progress({ answered: 2, points: 1.5, max_points: 2, score: 0.75 }));
  assert.equal(below.label, "Score 75%");
  assert.equal(below.tone, "below");

  // Exactly at the pass mark passes.
  assert.equal(scoreView(progress({ answered: 5, points: 4, max_points: 5, score: 0.8 })).tone, "pass");
  // One question left.
  assert.equal(scoreView(progress({ answered: 4, points: 4, max_points: 4, score: 1 })).remainingLabel, "Last question");
  // Score derived from points when the server sends no fraction.
  assert.equal(scoreView(progress({ answered: 2, points: 1, max_points: 2, score: 0 })).label, "Score 50%");
});

test("scoreView: a legacy progress object (no score fields) shows no score and no plan", () => {
  const view = scoreView(legacy);
  assert.equal(view.hasScore, false);
  assert.equal(view.label, "Score —");
  assert.equal(view.passMark, 0.8);
  assert.equal(view.remainingLabel, null);
});

test("kpPosition: question n of the knowledge point's target, extras said as such", () => {
  assert.deepEqual(kpPosition(progress({ kp_questions_used: 1, kp_target: 2 }), { target_questions: 2 }), {
    label: "Question 1 of 2",
    extra: false,
    number: 1,
  });
  // kp_target grows with the extra question.
  assert.equal(kpPosition(progress({ kp_questions_used: 3, kp_target: 3 }), { target_questions: 2 }).label, "Question 3 of 3 · extra question");
  // kp_target stays at the target: no "of".
  const extra = kpPosition(progress({ kp_questions_used: 3, kp_target: 2 }), { target_questions: 2 });
  assert.equal(extra.label, "Question 3 · extra question");
  assert.equal(extra.extra, true);
  // A one-question knowledge point.
  assert.equal(kpPosition(progress({ kp_questions_used: 1, kp_target: 1 }), { target_questions: 1 }).label, "Question 1 of 1");
  // No knowledge point data: the server's target.
  assert.equal(kpPosition(progress({ kp_questions_used: 2, kp_target: 3 })).label, "Question 2 of 3");
  // Legacy: no target anywhere.
  assert.equal(kpPosition(legacy).label, "Question 2");
  assert.equal(kpPosition(legacy).extra, false);
});

test("lessonStages: one segment per knowledge point, then the capstone", () => {
  const states = (p: TaskProgress, capstone: boolean) => lessonStages(p, 2, capstone).map((s) => `${s.capstone ? "C" : "K"}:${s.state}`);
  assert.deepEqual(states(progress({ kp_index: 1 }), true), ["K:done", "K:current", "C:todo"]);
  assert.deepEqual(states(progress({ kp_index: 1, in_capstone: true }), false), ["K:done", "K:done", "C:current"]);
  // Legacy: no capstone segment.
  assert.deepEqual(states(legacy, hasCapstone(legacy)), ["K:done", "K:current"]);
  assert.equal(hasCapstone(legacy), false);
  assert.equal(hasCapstone(progress({ has_capstone: true })), true);
  assert.equal(hasCapstone(progress({ in_capstone: true })), true);
});

test("pointsLabel: 1 point / ½ with a hint / 0 after the solution / 0", () => {
  const result = (over: object) => ({ status: "correct" as const, correct: true, independent: true, score: 1, ...over });
  assert.equal(pointsLabel(result({})), "1 point");
  assert.equal(pointsLabel(result({ independent: false, score: 0.5 })), "½ point — with a hint");
  assert.equal(pointsLabel(result({ independent: false, score: 0 }), true), "0 points — solution shown");
  assert.equal(pointsLabel(result({ status: "incorrect", correct: false, independent: false, score: 0 })), "0 points");
  assert.equal(pointsLabel(result({ status: "incorrect", correct: false, independent: false, score: 0 }), true), "0 points — solution shown");
  assert.equal(pointsLabel(result({ status: "self_graded", correct: false, independent: false, score: 0.5 })), "½ point — partly right");
  assert.equal(pointsLabel(result({ status: "voided" })), null);
  assert.equal(pointsLabel(result({ status: "step" })), null);
  // A server that leaves the default 0 on an independent right answer still shows a full point.
  assert.equal(questionPoints(result({ score: 0 })), 1);
  // No score at all (older server): from the verdict.
  assert.equal(questionPoints({ status: "correct", correct: true, independent: false }), 0.5);
});

test("stepPointsLabel: per-step points", () => {
  assert.equal(stepPointsLabel(1), "1 point");
  assert.equal(stepPointsLabel(0.5), "½ point — with the hint");
  assert.equal(stepPointsLabel(0), "0 points");
  assert.equal(stepPointsLabel(0, true), "0 points — solution shown");
});

test("lessonEndNote: halted early vs finished below the pass mark", () => {
  const halted = lessonEndNote(progress({ answered: 3, planned: 7, points: 1, max_points: 3, score: 1 / 3 }));
  assert.equal(halted.halted, true);
  assert.equal(halted.text, "80% is no longer reachable in this attempt — it comes back in about 2 hours with fresh questions.");
  const finished = lessonEndNote(progress({ answered: 5, planned: 5, points: 3.5, max_points: 5, score: 0.7 }));
  assert.equal(finished.halted, false);
  assert.match(finished.text, /^Your score, 70%, is below the 80% pass mark/);
  // A halted task may report only what was answered as its plan: the plan before the last answer tells.
  const reported = progress({ answered: 2, planned: 2, points: 0, max_points: 2, score: 0 });
  assert.equal(lessonEndNote(reported).halted, false);
  assert.equal(lessonEndNote(reported, 5).halted, true);
  // Legacy progress: no plan, so never "no longer reachable".
  assert.equal(lessonEndNote(legacy).halted, false);
});

test("progressNote: knowledge point done, end of the lesson; no second note for the capstone or an extra question", () => {
  const lesson = (p: TaskProgress, over: object = {}) => ({ type: "lesson" as const, phase: "practice" as const, outcome: null, progress: p, ...over });
  const before = lesson(progress({ kp_index: 0, kp_target: 2 }));
  assert.equal(progressNote(before, lesson(progress({ kp_index: 1 }))), "Knowledge point complete — on to the next step.");
  // The server's feedback announces the capstone.
  assert.equal(progressNote(before, lesson(progress({ kp_index: 1, in_capstone: true }))), null);
  // The server's feedback announces an extra question; no second note.
  assert.equal(progressNote(before, lesson(progress({ kp_index: 0, kp_target: 3 }))), null);
  assert.equal(progressNote(before, lesson(progress({ kp_index: 0, kp_target: 2 }))), null);
  const passed = lesson(progress({ answered: 5, planned: 5, points: 4.5, max_points: 5, score: 0.9 }), { phase: "done", outcome: "passed" });
  assert.equal(progressNote(before, passed), "That completes the lesson — score 90%.");
  const failed = lesson(progress({ answered: 3, planned: 6, points: 1, max_points: 3, score: 1 / 3 }), { phase: "done", outcome: "failed" });
  assert.match(progressNote(before, failed) ?? "", /^This lesson pauses here: 80% is no longer reachable/);
  // The plan before the last answer: halted, though the finished task reports planned = answered.
  const reportedFailed = lesson(progress({ answered: 2, planned: 2, points: 0, max_points: 2, score: 0 }), { phase: "done", outcome: "failed" });
  assert.match(progressNote(lesson(progress({ answered: 1, planned: 5 })), reportedFailed) ?? "", /no longer reachable/);
  // Not twice when the server's feedback already says so.
  assert.equal(progressNote(before, failed, "80% is out of reach on this attempt, so the lesson pauses here."), null);
  // The second failure's remediation text says it too.
  assert.equal(progressNote(before, failed, "This lesson has stopped short of 80% twice. Refresh S2b first."), null);
  // Legacy progress objects: nothing changed, nothing to say.
  assert.equal(progressNote(lesson(legacy), lesson(legacy)), null);
});

test("scoreView after an early stop: 'Stopped after n questions', never 'n of n answered'", () => {
  // S3b: Q1 wrong (an extra question is added: 4 planned), the extra one wrong too — 80 % is out of reach. The halted
  // task reports planned = answered = 2; the plan before the last answer was 4.
  const halted = progress({ answered: 2, planned: 2, points: 0, max_points: 2, score: 0 });
  const view = scoreView(halted, { outcome: "failed", plannedBefore: 4 });
  assert.equal(view.halted, true);
  assert.equal(view.remainingLabel, "Stopped after 2 questions");
  assert.match(view.ariaText, /stopped early/);
  assert.equal(scoreView(progress({ answered: 1, planned: 1, points: 0, max_points: 1 }), { outcome: "failed", plannedBefore: 3 }).remainingLabel, "Stopped after 1 question");
  // The server's own flag wins over the plan arithmetic.
  assert.equal(scoreView(progress({ answered: 2, planned: 2, halted: true }), { outcome: "failed" }).remainingLabel, "Stopped after 2 questions");
  assert.equal(wasHalted(progress({ answered: 2, planned: 6, halted: false })), false);
  // Every planned question answered, below the pass mark: not halted.
  const below = scoreView(progress({ answered: 5, planned: 5, points: 3.5, max_points: 5, score: 0.7 }), { outcome: "failed", plannedBefore: 5 });
  assert.equal(below.halted, false);
  assert.equal(below.remainingLabel, "5 of 5 answered");
  // Passed: the plan is done (no "Last question" on a finished task).
  const passed = scoreView(progress({ answered: 5, planned: 5, points: 4.5, max_points: 5, score: 0.9 }), { outcome: "passed", plannedBefore: 5 });
  assert.equal(passed.halted, false);
  assert.equal(passed.remainingLabel, "5 of 5 answered");
  // While the task runs, no end: the usual labels.
  assert.equal(scoreView(halted).remainingLabel, "2 of 2 answered");
});

test("the end of a lesson without a capstone is said once: the server's 'Lesson passed with N%.' gives way", () => {
  // S2c: the panel showed "That completes the lesson — score 80%." and "Lesson passed with 80%." together.
  assert.equal(withoutPassNote("Lesson passed with 80%."), "");
  assert.equal(withoutPassNote("With a hint, so half a point. Lesson passed with 81%."), "With a hint, so half a point.");
  assert.equal(withoutPassNote("Problem complete. One more question comes next."), "Problem complete. One more question comes next.");
  const lesson = (p: TaskProgress, over: object = {}) => ({ type: "lesson" as const, phase: "practice" as const, outcome: null, progress: p, ...over });
  const passed = lesson(progress({ answered: 5, planned: 5, points: 4, max_points: 5, score: 0.8 }), { phase: "done", outcome: "passed" });
  const before = lesson(progress({ answered: 4, planned: 5 }));
  assert.equal(progressNote(before, passed, withoutPassNote("Lesson passed with 80%.")), "That completes the lesson — score 80%.");
  // Wording it doesn't recognise: the server's text stays and the panel's own note steps back.
  assert.equal(progressNote(before, passed, "Lesson passed — 80%."), null);
});

test("lessonEndNote: the pause text follows Task.cooldown_until", () => {
  const p = progress({ answered: 5, planned: 5, points: 3.5, max_points: 5, score: 0.7 });
  const now = Date.parse("2026-10-06T10:00:00Z");
  assert.match(lessonEndNote(p, 0, null, now).text, /whenever you like/);
  assert.match(lessonEndNote(p, 0, "2026-10-06T12:00:00Z", now).text, /comes back at \d{2}:\d{2} with fresh questions/);
  assert.match(lessonEndNote(p).text, /about 2 hours/); // older servers: no field
});

test("scoreView of a proof lesson with scored steps: steps answered, then the variant", () => {
  const guided = (over: Partial<TaskProgress>) =>
    progress({ guided_total: 5, guided_done: 0, in_guided: true, variant: true, planned: 6, has_capstone: true, ...over });
  assert.equal(scoreView(guided({ guided_done: 2, answered: 2, points: 1.5, max_points: 2, score: 0.75 })).remainingLabel, "2 of 5 steps answered");
  assert.equal(scoreView(guided({ guided_done: 5, answered: 5, in_guided: false })).remainingLabel, "Variant problem next");
  assert.equal(scoreView(guided({ guided_done: 5, answered: 5, in_guided: false, in_capstone: true })).remainingLabel, "Last problem");
  // Finished: the plan, as for any lesson.
  const passed = guided({ guided_done: 5, answered: 6, in_guided: false, points: 6, max_points: 7, score: 6 / 7 });
  assert.equal(scoreView(passed, { outcome: "passed", plannedBefore: 6 }).remainingLabel, "6 of 6 answered");
  // Ended when the steps left 80 % out of reach.
  const ended = guided({ guided_done: 5, answered: 5, in_guided: false, points: 2, max_points: 5, score: 0.4, halted: true });
  assert.equal(scoreView(ended, { outcome: "failed", plannedBefore: 6 }).remainingLabel, "Stopped after 5 proof steps");
  // Halted inside the variant: questions again.
  assert.equal(scoreView(guided({ answered: 6, halted: true }), { outcome: "failed" }).remainingLabel, "Stopped after 6 questions");
});

test("hintTotal: the server's count, never fewer than are on screen; older servers sent every hint", () => {
  assert.equal(hintTotal({ hints: [], hint_count: 2 }), 2);
  assert.equal(hintTotal({ hints: ["First"], hint_count: 2 }, 1), 2);
  assert.equal(hintTotal({ hints: ["First", "Second"] }), 2);
  assert.equal(hintTotal({ hints: [], hint_count: 1 }, 2), 2);
  assert.equal(hintTotal({ hints: [], hint_count: 0 }), 0);
});
