// The step trail of multistep questions (src/lib/multistep.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  activeStep,
  allAnswered,
  earnedSteps,
  initialTrail,
  mergeSteps,
  multistepNote,
  questionDone,
  revealedStepIndexes,
  STEP_MARK_LABEL,
  stepMark,
  stepPips,
  stepHasHint,
  stepReached,
  stepResultOf,
  stepRevealed,
  stepsSummary,
  trailItems,
  trailReducer,
  type StepTrail,
} from "../src/lib/multistep.ts";
import type { AnswerResult, MultistepStepPublic, StepResult, Task } from "../src/types.ts";

const step = (index: number): MultistepStepPublic => ({ index, prompt: `Step ${index}`, kind: "expression", choices: [], hint: "Use i^2 = -1.", has_hint: true });
const steps = [step(0), step(1), step(2)];
const result = (index: number, score: number): StepResult => ({
  index,
  correct: score > 0,
  score,
  feedback: "",
  answer_display: `r${index}`,
  explanation: `why ${index}`,
});

test("a fresh question starts at step 1 with an empty trail", () => {
  const trail = initialTrail({ steps, step_index: 0, step_results: [] });
  assert.deepEqual(trail, { total: 3, results: [], current: 0, open: null });
  assert.equal(activeStep(trail), 0);
  assert.deepEqual(stepPips(trail), ["current", "todo", "todo"]);
});

test("a reload in the middle restores the answered steps and the step to answer", () => {
  const trail = initialTrail({ steps, step_index: 2, step_results: [result(1, 0.5), result(0, 1)] });
  assert.deepEqual(
    trail.results.map((r) => r.index),
    [0, 1],
  );
  assert.equal(trail.current, 2);
  assert.equal(trail.open, null);
  assert.equal(activeStep(trail), 2);
  assert.deepEqual(trailItems(trail).map((r) => r.index), [0, 1]);
  assert.deepEqual(stepPips(trail), ["full", "half", "current"]);
});

test("restoring tolerates a missing step_index, duplicates and out-of-range results", () => {
  const trail = initialTrail({ steps, step_results: [result(0, 0), result(0, 1), result(7, 1)] });
  assert.deepEqual(trail.results, [result(0, 1)]);
  assert.equal(trail.current, 1);
  // A legacy question object without the multistep fields.
  assert.deepEqual(initialTrail({ steps: [] }), { total: 0, results: [], current: 0, open: null });
});

test("answering a step keeps it open until Next step, then it joins the trail", () => {
  let trail: StepTrail = initialTrail({ steps, step_index: 0, step_results: [] });
  trail = trailReducer(trail, { type: "answered", result: result(0, 1) });
  assert.equal(trail.open, 0);
  assert.equal(trail.current, 1);
  assert.equal(activeStep(trail), null); // the answered step is still on screen
  assert.deepEqual(trailItems(trail), []);
  trail = trailReducer(trail, { type: "next" });
  assert.equal(trail.open, null);
  assert.equal(activeStep(trail), 1);
  assert.deepEqual(trailItems(trail).map((r) => r.index), [0]);

  trail = trailReducer(trail, { type: "answered", result: result(1, 0.5) });
  trail = trailReducer(trail, { type: "next" });
  trail = trailReducer(trail, { type: "answered", result: result(2, 0) });
  // The last step stays open next to the question's summary.
  assert.equal(trail.open, 2);
  assert.equal(activeStep(trail), null);
  assert.equal(allAnswered(trail), true);
  assert.deepEqual(stepPips(trail), ["full", "half", "none"]);
  assert.equal(earnedSteps(trail.results), 1.5);
  assert.equal(stepsSummary(trail.results, trail.total), "1½ of 3 steps");
});

test("the reducer ignores unknown steps and a Next with nothing open", () => {
  const trail = initialTrail({ steps, step_index: 0, step_results: [] });
  assert.equal(trailReducer(trail, { type: "answered", result: result(5, 1) }), trail);
  assert.equal(trailReducer(trail, { type: "next" }), trail);
});

test("step marks and the summary in halves", () => {
  assert.equal(stepMark(1), "full");
  assert.equal(stepMark(0.5), "half");
  assert.equal(stepMark(0), "none");
  assert.equal(stepsSummary([result(0, 1), result(1, 1), result(2, 0.5)], 3), "2½ of 3 steps");
  assert.equal(stepsSummary([result(0, 1), result(1, 1)], 2), "2 of 2 steps");
});

const answer = (over: Partial<AnswerResult>): AnswerResult => ({
  correct: true,
  status: "step",
  independent: true,
  feedback: "Correct.",
  correct_answer: "-3 + 14i",
  correct_choice: null,
  solution: "Expand.",
  task: {} as Task,
  xp_awarded: 0,
  suggestion: null,
  suggestion_text: null,
  score: 0,
  step_result: null,
  question_done: false,
  ...over,
});

test("answer responses: step results and when the question is done", () => {
  assert.equal(questionDone(answer({})), false);
  assert.equal(questionDone(answer({ status: "correct", question_done: true })), true);
  assert.equal(questionDone(answer({ status: "incorrect", question_done: true })), true);
  const sr = result(1, 0.5);
  assert.equal(stepResultOf(answer({ step_result: sr }), 1), sr);
  // Without step_result (older server): built from the verdict.
  assert.deepEqual(stepResultOf(answer({ independent: false }), 2), {
    index: 2,
    correct: true,
    score: 0.5,
    feedback: "Correct.",
    answer_display: "-3 + 14i",
    explanation: "Expand.",
  });
});

test("a step answered with Show solution stays 'solution shown (0 points)' after a reload, never 'not right'", () => {
  // The server's flag (StepResult.revealed) wins.
  const shown = { ...result(1, 0), correct: false, revealed: true };
  const wrong = { ...result(2, 0), correct: false, revealed: false };
  assert.equal(stepRevealed(shown), true);
  assert.equal(stepRevealed(wrong), false);
  // Older servers: no flag — "Answer viewed — nothing submitted." or a right answer worth 0 points.
  assert.equal(stepRevealed({ ...result(0, 0), correct: false, feedback: "Answer viewed — nothing submitted." }), true);
  assert.equal(stepRevealed({ ...result(0, 0), correct: true, feedback: "Correct. The answer was shown first, so no point for this step." }), true);
  assert.equal(stepRevealed({ ...result(0, 0), correct: false, feedback: "Not quite." }), false);
  // A step that earned points was not revealed, whatever the text says.
  assert.equal(stepRevealed({ ...result(0, 0.5), feedback: "Answer viewed" }), false);

  // Rebuilt on mount from the server's step results (MultistepCard).
  const restored = initialTrail({ steps, step_index: 2, step_results: [result(0, 0.5), shown] });
  const revealed = revealedStepIndexes(restored.results);
  assert.deepEqual([...revealed], [1]);
  assert.deepEqual(stepPips(restored, revealed), ["half", "shown", "current"]);
  assert.equal(stepMark(0, revealed.has(1)), "shown");
  assert.equal(STEP_MARK_LABEL[stepMark(0, true)], "solution shown (0 points)");
  assert.equal(STEP_MARK_LABEL[stepMark(0, false)], "not right");
  // Points always win over the flag.
  assert.equal(stepMark(1, true), "full");
});

test("multistepNote: the server's note for a finished multistep question, without the step verdict and the recap", () => {
  // AnswerResult.note when the server sends it.
  assert.equal(
    multistepNote({ feedback: "Correct. Problem complete: 2 of 3 steps right. One more question on this step comes next, so you can make up for it.", note: " One more question on this step comes next, so you can make up for it." }),
    "One more question on this step comes next, so you can make up for it.",
  );
  assert.equal(multistepNote({ feedback: "Correct. Problem complete: 3 of 3 steps right.", note: "" }), "");
  // Older servers: the text after the recap.
  assert.equal(
    multistepNote({ feedback: "Correct. Problem complete: 3 of 3 steps right. Last: one problem that puts the lesson together, step by step — it counts double." }),
    "Last: one problem that puts the lesson together, step by step — it counts double.",
  );
  assert.equal(multistepNote({ feedback: "Not quite. Problem complete: 1 of 3 steps right." }), "");
  assert.equal(multistepNote({ feedback: "Correct." }), "");
});

test("a later step's prompt arrives with the answer to the step before it; nothing is shown before that", () => {
  const blank = (index: number): MultistepStepPublic => ({ index, prompt: "", kind: "multiple_choice", choices: [], hint: "", has_hint: true });
  // As served: step 1 reached, its hint not used; steps 2 and 3 blank.
  const served = [{ ...step(0), hint: "" }, blank(1), blank(2)];
  assert.equal(stepReached(served[0]), true);
  assert.equal(stepReached(served[1]), false);
  assert.equal(stepReached(undefined), false);
  // The task after answering step 1 carries step 2.
  const after = [{ ...step(0), hint: "" }, { ...blank(1), prompt: "Which law?", choices: ["A", "B"] }, blank(2)];
  const merged = mergeSteps(served, after);
  assert.equal(merged[1].prompt, "Which law?");
  assert.deepEqual(merged[1].choices, ["A", "B"]);
  assert.equal(stepReached(merged[2]), false);
  // A hint already shown keeps its text when a later copy of the step is blank.
  const hinted = [{ ...step(0), hint: "Use i^2 = -1." }, blank(1), blank(2)];
  assert.equal(mergeSteps(hinted, after)[0].hint, "Use i^2 = -1.");
  // Never back to blank; a different shape is ignored.
  assert.deepEqual(mergeSteps(merged, served), merged);
  assert.deepEqual(mergeSteps(served, after.slice(0, 2)), served);
  assert.deepEqual(mergeSteps(served, null), served);
});

test("a step's hint is offered when the server says it has one (its text comes from POST /hint)", () => {
  assert.equal(stepHasHint({ hint: "", has_hint: true }), true);
  assert.equal(stepHasHint({ hint: "", has_hint: false }), false);
  // Older servers: the text, no flag.
  assert.equal(stepHasHint({ hint: "Use i^2 = -1." }), true);
});

test("a variant resumed after leaving restores its trail at the step it stopped at, with that step's prompt", () => {
  const blank = (index: number): MultistepStepPublic => ({ index, prompt: "", kind: "expression", choices: [], hint: "", has_hint: true });
  // Steps 1–2 answered (the second with its hint), step 3 current; step 4 not reached yet.
  const served = [step(0), step(1), { ...step(2), hint: "" }, blank(3)];
  const trail = initialTrail({ steps: served, step_index: 2, step_results: [result(0, 1), result(1, 0.5)] });
  assert.equal(activeStep(trail), 2);
  assert.equal(stepReached(served[activeStep(trail) ?? -1]), true);
  assert.equal(stepReached(served[3]), false);
  assert.deepEqual(stepPips(trail), ["full", "half", "current", "todo"]);
});
