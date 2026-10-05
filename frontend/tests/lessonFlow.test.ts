// Soft gating of v2 teaching sequences (src/lib/lessonFlow.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  flowReducer,
  gatePosition,
  initialFlow,
  lessonFingerprint,
  loadAnswered,
  saveAnswered,
  stripGoalLabel,
  visibleBlocks,
  type CheckOutcome,
} from "../src/lib/lessonFlow.ts";
import type { InlineCheckPublic, TeachingBlockView } from "../src/types.ts";

const check: InlineCheckPublic = { kind: "numeric", prompt: "What does $6i^2$ become?", choices: [] };
const block = (index: number, withCheck = false): TeachingBlockView => ({
  index,
  kind: withCheck ? "check" : "explain",
  heading: null,
  body: `Step ${index}`,
  visualization: null,
  check: withCheck ? check : null,
});
// setup, demonstrate, check (2), explain, faded example with a check (4), summary
const blocks = [block(0), block(1), block(2, true), block(3), block(4, true), block(5)];
const outcome = (correct: boolean | null): CheckOutcome => ({
  response: "-6",
  choice: null,
  result: { correct, feedback: "", answer_display: "-6", explanation: "Because i² = −1." },
});

test("content after the first open check is behind the gate", () => {
  const state = initialFlow();
  assert.equal(gatePosition(blocks, state), 2);
  const view = visibleBlocks(blocks, state, true);
  assert.deepEqual(view.shown.map((b) => b.index), [0, 1, 2]);
  assert.equal(view.hidden, 3);
  assert.equal(view.gate?.index, 2);
});

test("answering (right or wrong) or skipping opens a gate; the next check gates again", () => {
  let state = flowReducer(initialFlow(), { type: "answered", index: 2, outcome: outcome(false) });
  assert.equal(gatePosition(blocks, state), 4);
  assert.deepEqual(visibleBlocks(blocks, state, true).shown.map((b) => b.index), [0, 1, 2, 3, 4]);
  state = flowReducer(state, { type: "skipped", index: 4 });
  const open = visibleBlocks(blocks, state, true);
  assert.equal(open.gate, null);
  assert.equal(open.hidden, 0);
  assert.equal(open.shown.length, blocks.length);
});

test("a check in the last block still gates what follows the sequence", () => {
  const tail = [block(0), block(1, true)];
  const view = visibleBlocks(tail, initialFlow(), true);
  assert.equal(view.hidden, 0);
  assert.equal(view.gate?.index, 1, "the start of practice waits for it");
});

test("a reflect check revealed (correct = null) counts as answered", () => {
  const state = flowReducer(initialFlow(), { type: "answered", index: 2, outcome: outcome(null) });
  assert.equal(gatePosition(blocks, state), 4);
});

test("skipping an answered check changes nothing; reset keeps only answers", () => {
  const answered = flowReducer(initialFlow(), { type: "answered", index: 2, outcome: outcome(true) });
  assert.equal(flowReducer(answered, { type: "skipped", index: 2 }), answered);
  const skipped = flowReducer(answered, { type: "skipped", index: 4 });
  const reset = flowReducer(skipped, { type: "reset", answered: skipped.answered });
  assert.deepEqual(reset.skipped, {});
  assert.equal(gatePosition(blocks, reset), 4, "a reload closes skipped gates again");
});

test("without gating (lesson preview) every block is shown", () => {
  const view = visibleBlocks(blocks, initialFlow(), false);
  assert.equal(view.shown.length, blocks.length);
  assert.equal(view.gate, null);
});

test("answered checks round-trip through session storage, per lesson version", () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
  const lesson = { skill_id: "sk1", blocks };
  const fp = lessonFingerprint(lesson);
  saveAnswered(fp, { 2: outcome(true) }, storage);
  assert.deepEqual(loadAnswered(fp, storage), { 2: outcome(true) });
  const regenerated = lessonFingerprint({ skill_id: "sk1", blocks: [...blocks.slice(0, 5), { ...block(5), body: "A new summary" }] });
  assert.notEqual(regenerated, fp, "a regenerated lesson doesn't inherit old answers");
  assert.deepEqual(loadAnswered(regenerated, storage), {});
  assert.notEqual(lessonFingerprint({ skill_id: "sk2", blocks }), fp);
});

test("unreadable or unavailable storage means nothing answered", () => {
  const broken = { getItem: () => "{not json" };
  assert.deepEqual(loadAnswered("x", broken), {});
  const junk = { getItem: () => JSON.stringify({ 2: { result: null }, a: outcome(true), 3: outcome(false) }) };
  assert.deepEqual(Object.keys(loadAnswered("x", junk)), ["3"]);
  const throwing = { getItem: () => { throw new Error("SecurityError"); } };
  assert.deepEqual(loadAnswered("x", throwing), {});
  assert.deepEqual(loadAnswered("x", null), {});
  assert.doesNotThrow(() => saveAnswered("x", {}, { setItem: () => { throw new Error("QuotaExceeded"); } }));
});

test("the goal line's own 'Your goal:' label is dropped", () => {
  assert.equal(stripGoalLabel("**Your goal:** Calculate $(2+3i)(1+2i)$."), "Calculate $(2+3i)(1+2i)$.");
  assert.equal(stripGoalLabel("Your goal: explain why."), "explain why.");
  assert.equal(stripGoalLabel("Calculate a product."), "Calculate a product.");
});
