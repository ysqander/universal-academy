// Scored guided steps of proof lessons (src/lib/guided.ts, SPEC §6.3b).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  guidedChoiceMarks,
  guidedDot,
  guidedFeedback,
  guidedPointsLabel,
  guidedStages,
  guidedStart,
  guidedStepLabel,
  guidedThen,
  guidedVerdict,
  holdsGate,
  isGuidedProgress,
  isScoredCheck,
  isStaleLessonMessage,
  keepsAnswersOnLeave,
  latestState,
  moreSteps,
  NOT_YET_TITLE,
  restoredAnswer,
  stepPoints,
  variantOutOfReach,
} from "../src/lib/guided.ts";
import type { GuidedCheckState, InlineCheckPublic, Lesson, TaskProgress } from "../src/types.ts";

const record = (over: Partial<GuidedCheckState> = {}): GuidedCheckState => ({
  status: "open",
  tries: 0,
  first_response: null,
  response: null,
  correct: null,
  points: null,
  revealed: false,
  feedback: "",
  hint: "",
  answer_display: "",
  explanation: "",
  voided: false,
  scored: true,
  ...over,
});

const done = (over: Partial<GuidedCheckState> = {}): GuidedCheckState =>
  record({ status: "done", tries: 1, response: "-a*d-b*c", correct: true, points: 1, answer_display: "$-ad-bc$", explanation: "Expand.", ...over });

const check = (over: Partial<InlineCheckPublic> = {}): InlineCheckPublic => ({
  kind: "expression",
  prompt: "Build route B's last leg.",
  choices: [],
  scored: true,
  second_try: true,
  state: record(),
  ...over,
});

const progress = (over: Partial<TaskProgress> = {}): TaskProgress => ({
  kp_index: 0,
  kp_total: 1,
  streak: 0,
  needed_in_row: 2,
  kp_questions_used: 0,
  kp_questions_available: 0,
  answered: 0,
  correct: 0,
  total_questions: 0,
  kp_wrong: 0,
  easier_available: false,
  kp_target: 0,
  points: 0,
  max_points: 0,
  score: 0,
  planned: 6,
  pass_mark: 0.8,
  in_capstone: false,
  has_capstone: true,
  guided_total: 5,
  guided_done: 0,
  in_guided: true,
  variant: true,
  best_possible: 1,
  ...over,
});

test("a scored step is driven by the server; reflect checks and formative checks are not", () => {
  assert.equal(isScoredCheck(check()), true);
  assert.equal(isScoredCheck(check({ scored: false, state: null })), false);
  assert.equal(isScoredCheck(check({ scored: false, state: done({ scored: false }) })), true, "a retry's answered step");
  assert.equal(isScoredCheck(check({ kind: "reflect" })), false);
  assert.equal(isScoredCheck(null), false);
  // It holds the lesson back until done; a retry of the lesson shows it answered and never holds anything back.
  assert.equal(holdsGate(check()), true);
  assert.equal(holdsGate(check({ state: record({ status: "retry", tries: 1 }) })), true);
  assert.equal(holdsGate(check({ state: done() })), false);
  assert.equal(holdsGate(check({ state: record({ scored: false }) })), false);
  // In a preview of a skill never started there is no record yet: still open.
  assert.equal(holdsGate(check({ state: null })), true);
});

test("the newer record wins: the last answer's until the replaced task catches up, then the task's", () => {
  const open = record();
  const retry = record({ status: "retry", tries: 1, first_response: "ad+bc" });
  const finished = done();
  assert.equal(latestState(open, retry), retry);
  assert.equal(latestState(retry, finished), finished);
  assert.equal(latestState(finished, retry), finished, "an older answer never undoes a done step");
  const reported = done({ voided: true });
  assert.equal(latestState(reported, finished), reported, "on a tie the task's record wins (a report voids it)");
  assert.equal(latestState(null, retry), retry);
  assert.equal(latestState(open, null), open);
  assert.equal(latestState(undefined, undefined), null);
});

test("points and verdicts: 1 first time, ½ on the second try, 0 wrong or shown", () => {
  assert.deepEqual(guidedVerdict(done()), { tone: "correct", title: "Right", points: "1 point" });
  assert.deepEqual(guidedVerdict(done({ tries: 2, points: 0.5, first_response: "ad+bc" })), {
    tone: "partial",
    title: "Right on the second try",
    points: "½ point — second try",
  });
  assert.deepEqual(guidedVerdict(done({ correct: false, points: 0, tries: 2 })), { tone: "incorrect", title: "Not quite", points: "0 points" });
  assert.deepEqual(guidedVerdict(done({ revealed: true, response: null, correct: null, points: 0 })), {
    tone: "neutral",
    title: "Answer shown",
    points: "0 points — answer shown",
  });
  assert.deepEqual(guidedVerdict(record({ status: "retry", tries: 1 })), { tone: "retry", title: NOT_YET_TITLE, points: null });
  assert.deepEqual(guidedVerdict(record()), { tone: "open", title: null, points: null });
  assert.deepEqual(guidedVerdict(null), { tone: "open", title: null, points: null });
  // A retry of the lesson: shown answered, not scored again.
  assert.deepEqual(guidedVerdict(done({ scored: false, points: null })), { tone: "neutral", title: "Not scored this time", points: null });
  // Reported as wrong: its points no longer count.
  assert.equal(guidedPointsLabel(done({ voided: true })), "Not counted — you reported the answer as wrong");
  assert.equal(guidedPointsLabel(record()), null);
  // No points from the server: from the verdict.
  assert.equal(stepPoints({ points: null, correct: true, tries: 2, revealed: false }), 0.5);
  assert.equal(stepPoints({ points: null, correct: true, tries: 1, revealed: false }), 1);
  assert.equal(stepPoints({ points: null, correct: true, tries: 1, revealed: true }), 0);
  assert.equal(stepPoints({ points: 3, correct: true, tries: 1, revealed: false }), 1);
});

test("feedback: the grader's nudge without the stock lines the verdict and points already say", () => {
  // The server's "not yet" after a typed answer: its nudge stays, the stock line goes (the title says it).
  const nudge = record({ status: "retry", tries: 1, feedback: "Check the sign of the real part. Not yet — one more try for ½ point. The hint below may help." });
  assert.equal(guidedFeedback(nudge), "Check the sign of the real part.");
  // A statement set's "not yet" never names the statements — nothing is left.
  assert.equal(guidedFeedback(record({ status: "retry", tries: 1, feedback: "Not yet — one more try for ½ point. The hint below may help." })), "");
  assert.equal(guidedFeedback(done({ tries: 2, points: 0.5, feedback: "Correct. On the second try, so ½ point." })), "");
  assert.equal(guidedFeedback(done({ correct: false, points: 0, feedback: "Not quite. Statement 2 is actually false." })), "Statement 2 is actually false.");
  assert.equal(guidedFeedback(done({ revealed: true, correct: null, feedback: "Answer shown — this step earns 0 points." })), "");
  assert.equal(guidedFeedback(done({ scored: false, feedback: "Correct." })), "");
  assert.equal(guidedFeedback(record()), "");
});

test("the inputs show the answer that ended the step, or the wrong first answer to correct", () => {
  assert.deepEqual(restoredAnswer("expression", done()), { choice: null, response: "-a*d-b*c" });
  assert.deepEqual(restoredAnswer("expression", record({ status: "retry", tries: 1, first_response: "ad+bc" })), { choice: null, response: "ad+bc" });
  assert.deepEqual(restoredAnswer("true_false_set", record({ status: "retry", tries: 1, first_response: "TFT" })), { choice: null, response: "TFT" });
  // A choice is picked again on a retry; once done the pick shows.
  assert.deepEqual(restoredAnswer("multiple_choice", record({ status: "retry", tries: 1, first_response: "2" })), { choice: null, response: "" });
  assert.deepEqual(restoredAnswer("multiple_choice", done({ response: "1" })), { choice: 1, response: "" });
  // Shown instead of answered: nothing to show.
  assert.deepEqual(restoredAnswer("numeric", done({ revealed: true, response: null, correct: null })), { choice: null, response: "" });
  assert.deepEqual(restoredAnswer("numeric", record()), { choice: null, response: "" });
  assert.deepEqual(restoredAnswer("numeric", null), { choice: null, response: "" });
});

test("choice marks: a retry marks only the first pick as not right, never the answer", () => {
  const choices = ["$ad+bc$", "$-ad-bc$", "$ac-bd$"];
  assert.deepEqual(guidedChoiceMarks(choices, record({ status: "retry", tries: 1, first_response: "0" })), ["incorrect", null, null]);
  assert.deepEqual(guidedChoiceMarks(choices, record()), []);
  assert.deepEqual(guidedChoiceMarks(choices, done({ response: "1", answer_display: "$-ad-bc$" })), [null, "correct", null]);
  assert.deepEqual(guidedChoiceMarks(choices, done({ response: "2", correct: false, points: 0, answer_display: "$-ad-bc$" })), [null, "correct", "incorrect"]);
  // Right on the second try: the first pick stays marked.
  assert.deepEqual(
    guidedChoiceMarks(choices, done({ tries: 2, first_response: "0", response: "1", points: 0.5, answer_display: "$-ad-bc$" })),
    ["incorrect", "correct", null],
  );
  // Shown: the key alone.
  assert.deepEqual(guidedChoiceMarks(choices, done({ revealed: true, response: null, correct: null, answer_display: "$-ad-bc$" })), [null, "correct", null]);
});

test("the step's dot on the rail", () => {
  assert.equal(guidedDot(record()), "open");
  assert.equal(guidedDot(null), "open");
  assert.equal(guidedDot(record({ status: "retry", tries: 1 })), "retry");
  assert.equal(guidedDot(done()), "done");
  assert.equal(guidedDot(done({ tries: 2, points: 0.5 })), "half");
  assert.equal(guidedDot(done({ correct: false, points: 0 })), "missed");
  assert.equal(guidedDot(done({ revealed: true, correct: null, response: null, points: 0 })), "shown");
  assert.equal(guidedDot(done({ voided: true })), "shown");
  assert.equal(guidedDot(done({ scored: false, correct: false })), "done");
});

test("stepper: one segment per proof step, then the variant; the step line", () => {
  const states = (p: TaskProgress) => guidedStages(p).map((s) => `${s.capstone ? "V" : "S"}:${s.state}`);
  assert.deepEqual(states(progress({ guided_total: 3, guided_done: 1 })), ["S:done", "S:current", "S:todo", "V:todo"]);
  assert.deepEqual(states(progress({ guided_total: 2, guided_done: 2, in_guided: false })), ["S:done", "S:done", "V:todo"]);
  assert.deepEqual(states(progress({ guided_total: 2, guided_done: 2, in_guided: false, in_capstone: true })), ["S:done", "S:done", "V:current"]);
  assert.deepEqual(states(progress({ guided_total: 1, guided_done: 0, variant: false })), ["S:current"]);
  assert.equal(guidedStepLabel(progress({ guided_done: 2 })), "Proof step 3 of 5");
  assert.equal(guidedStepLabel(progress({ guided_done: 5 })), "Proof steps done");
  assert.equal(guidedThen(progress({ guided_done: 2 })), "then a variant problem (counts double)");
  assert.equal(guidedThen(progress({ guided_done: 5 })), "next: the variant problem (counts double)");
  assert.equal(guidedThen(progress({ variant: false })), null);
  assert.equal(moreSteps(1), "1 more step");
  assert.equal(moreSteps(4), "4 more steps");
});

test("the card after the steps says before the click whether the variant can still pass the lesson", () => {
  const reachable = progress({ guided_done: 5, in_guided: false, answered: 5, points: 4, max_points: 5, score: 0.8, best_possible: 6 / 7 });
  const start = guidedStart(reachable);
  assert.equal(start?.ends, false);
  assert.equal(start?.button, "Start the variant problem");
  assert.match(start?.text ?? "", /one variant problem — the same idea with one thing changed\. It counts double\. 80% passes\./);

  // 2 of 5 points: even 2 more for the variant gives 4 of 7 (57 %).
  const lost = progress({ guided_done: 5, in_guided: false, answered: 5, points: 2, max_points: 5, score: 0.4, best_possible: 4 / 7 });
  assert.equal(variantOutOfReach(lost), true);
  const end = guidedStart(lost);
  assert.equal(end?.ends, true);
  assert.equal(end?.button, "End this attempt");
  assert.equal(end?.text, "80% is out of reach on this attempt, even with the variant right. The lesson comes back after a pause with fresh questions.");
  // Exactly at the pass mark is still reachable.
  assert.equal(variantOutOfReach(progress({ in_guided: false, best_possible: 0.8 })), false);
  // While steps are open nothing is decided yet; other lessons have no such card.
  assert.equal(variantOutOfReach(progress({ in_guided: true, best_possible: 0.5 })), false);
  assert.equal(guidedStart(progress({ guided_total: 0, variant: false })), null);
  assert.equal(isGuidedProgress(progress({ guided_total: 0, variant: false })), false);
  assert.equal(isGuidedProgress(progress({ guided_total: 0, variant: true, in_capstone: true })), true);
});

test("leaving keeps the answers of a scored proof lesson only", () => {
  const lesson = (guided: Lesson["guided"]) => ({ guided }) as Lesson;
  assert.equal(keepsAnswersOnLeave({ type: "lesson", lesson: lesson("scored") }), true);
  assert.equal(keepsAnswersOnLeave({ type: "lesson", lesson: lesson("retry") }), false);
  assert.equal(keepsAnswersOnLeave({ type: "lesson", lesson: lesson(null) }), false);
  assert.equal(keepsAnswersOnLeave({ type: "review", lesson: null }), false);
});

test("a 409 that only a reload fixes is told apart from the others", () => {
  assert.equal(isStaleLessonMessage("This lesson was updated — reload to see the new version."), true);
  assert.equal(isStaleLessonMessage("This step was answered in another window — reload the lesson."), true);
  assert.equal(isStaleLessonMessage("Answer the earlier step first."), false);
  assert.equal(isStaleLessonMessage("This task is already finished."), false);
});
