// The step trail of multistep questions (src/lib/multistep.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  activeStep,
  allAnswered,
  earnedSteps,
  initialTrail,
  questionDone,
  stepMark,
  stepPips,
  stepResultOf,
  stepsSummary,
  trailItems,
  trailReducer,
  type StepTrail,
} from "../src/lib/multistep.ts";
import type { AnswerResult, MultistepStepPublic, StepResult, Task } from "../src/types.ts";

const step = (index: number): MultistepStepPublic => ({ index, prompt: `Step ${index}`, kind: "expression", choices: [], hint: "Use i^2 = -1." });
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
