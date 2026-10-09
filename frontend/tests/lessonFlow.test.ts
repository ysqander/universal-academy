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
  sequenceKey,
  stripGoalLabel,
  visibleBlocks,
  type CheckOutcome,
} from "../src/lib/lessonFlow.ts";
import type { GuidedCheckState, InlineCheckPublic, TeachingBlockView } from "../src/types.ts";

const check: InlineCheckPublic = { kind: "numeric", prompt: "What does $6i^2$ become?", choices: [], scored: false, second_try: false, state: null };
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

// A proof lesson's scored steps (lib/guided.ts): the server's record gates, and the server holds the later blocks back.
const record = (status: GuidedCheckState["status"], scored = true): GuidedCheckState => ({
  status,
  tries: status === "open" ? 0 : 1,
  first_response: null,
  response: null,
  correct: status === "done" ? true : null,
  points: status === "done" ? 1 : null,
  revealed: false,
  feedback: "",
  hint: "",
  answer_display: "",
  explanation: "",
  voided: false,
  scored,
});
const scoredBlock = (index: number, state: GuidedCheckState | null): TeachingBlockView => ({
  ...block(index, true),
  check: { ...check, kind: "expression", scored: true, second_try: true, state },
});

test("a scored step gates until the server says it is done: skips and stored answers don't open it", () => {
  // The server sent blocks 0–2 of 7 (the first open step is block 2).
  const sent = [block(0), block(1), scoredBlock(2, record("open"))];
  let state = flowReducer(initialFlow({ 2: outcome(true) }), { type: "skipped", index: 2 });
  const view = visibleBlocks(sent, state, true, 7);
  assert.equal(view.gate?.index, 2);
  assert.equal(view.hidden, 4, "the steps the server holds back are counted from blocks_total");
  // A wrong first answer with one more try still holds the lesson back.
  assert.equal(visibleBlocks([block(0), block(1), scoredBlock(2, record("retry"))], state, true, 7).gate?.index, 2);
  // Done: the next blocks arrive from the server and the next open step gates.
  const more = [block(0), block(1), scoredBlock(2, record("done")), block(3), scoredBlock(4, record("open"))];
  state = initialFlow();
  const next = visibleBlocks(more, state, true, 7);
  assert.equal(next.gate?.index, 4);
  assert.equal(next.hidden, 2);
  // An unscored check before a scored one still gates softly (answered or skipped).
  const mixed = [block(0, true), scoredBlock(1, record("open"))];
  assert.equal(gatePosition(mixed, initialFlow()), 0);
  assert.equal(gatePosition(mixed, flowReducer(initialFlow(), { type: "skipped", index: 0 })), 1);
});

test("a retry of the lesson shows its steps answered: nothing is gated by them", () => {
  const all = [block(0), scoredBlock(1, record("done", false)), block(2), scoredBlock(3, record("open", false))];
  const view = visibleBlocks(all, initialFlow(), true, 4);
  assert.equal(view.gate, null);
  assert.equal(view.shown.length, 4);
});

test("a scored lesson keeps its key while it grows; other lessons are keyed by their blocks", () => {
  const sent = { skill_id: "sk1", version: "abc12345", guided: "scored" as const, blocks: [block(0), scoredBlock(1, record("open"))] };
  const grown = { ...sent, blocks: [...sent.blocks.slice(0, 1), scoredBlock(1, record("done")), block(2)] };
  assert.equal(sequenceKey(sent, "t1"), sequenceKey(grown, "t1"), "answering a step must not remount the sequence");
  assert.notEqual(sequenceKey(sent, "t1"), sequenceKey(sent, "t2"), "another attempt starts afresh");
  assert.notEqual(sequenceKey(sent, "t1"), sequenceKey({ ...sent, version: "def67890" }, "t1"), "a regenerated lesson too");
  const plain = { skill_id: "sk1", version: "abc12345", guided: null, blocks };
  assert.equal(sequenceKey(plain), lessonFingerprint(plain));
  assert.equal(sequenceKey({ ...plain, guided: "retry" as const }, "t1"), lessonFingerprint(plain));
});
