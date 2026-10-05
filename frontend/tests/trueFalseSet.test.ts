// "Mark each statement true or false" (src/lib/trueFalseSet.ts): response encoding, reading the key, per-row results.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  decodeTfResponse,
  emptyTfMarks,
  encodeTfMarks,
  markNextUnmarked,
  parseTfKey,
  resolveTfKey,
  setTfMark,
  tfKeyAction,
  tfKeyFromFeedback,
  tfRowResults,
  wrongRowsLabel,
} from "../src/lib/trueFalseSet.ts";

test("the response is one letter per statement, only once every statement is marked", () => {
  let marks = emptyTfMarks(3);
  assert.equal(encodeTfMarks(marks), null);
  marks = setTfMark(marks, 0, "F");
  marks = setTfMark(marks, 2, "T");
  assert.equal(encodeTfMarks(marks), null, "statement 2 is still unmarked");
  marks = setTfMark(marks, 1, "T");
  assert.equal(encodeTfMarks(marks), "FTT");
  assert.equal(encodeTfMarks([]), null);
});

test("a stored response decodes back to marks; anything else stays unmarked", () => {
  assert.deepEqual(decodeTfResponse("FTT", 3), ["F", "T", "T"]);
  assert.deepEqual(decodeTfResponse("ftt", 3), ["F", "T", "T"]);
  assert.deepEqual(decodeTfResponse("FT", 3), [null, null, null]);
  assert.deepEqual(decodeTfResponse(null, 2), [null, null]);
});

test("the answer key is read from the usual ways of writing it", () => {
  const ftt = ["F", "T", "T"];
  for (const text of ["FTT", "1 False · 2 True · 3 True", "1. False; 2. True; 3. True", "F, T, T", "(F, T, T)", "1. F 2. T 3. T", "False, True, True", "1 — false; 2 — true; 3 — true", "**F**, **T**, **T**", "$\\text{F T T}$", "1) false\n2) true\n3) true"]) {
    assert.deepEqual(parseTfKey(text, 3), ftt, text);
  }
});

test("an unreadable key, or one with the wrong number of verdicts, is unknown", () => {
  assert.equal(parseTfKey("FT", 3), null);
  assert.equal(parseTfKey("FTTF", 3), null);
  assert.equal(parseTfKey("Only the second statement is false", 3), null);
  assert.equal(parseTfKey("Statement 1 is false because order matters", 3), null);
  assert.equal(parseTfKey("", 3), null);
  assert.equal(parseTfKey(null, 3), null);
});

test("rows are marked against the key; without a key only an all-right answer marks them", () => {
  assert.deepEqual(tfRowResults("TTT", ["F", "T", "T"], false, 3), ["incorrect", "correct", "correct"]);
  assert.deepEqual(tfRowResults("FTT", ["F", "T", "T"], true, 3), ["correct", "correct", "correct"]);
  assert.deepEqual(tfRowResults("FTT", null, true, 3), ["correct", "correct", "correct"]);
  assert.deepEqual(tfRowResults("TTT", null, false, 3), [null, null, null]);
  assert.deepEqual(tfRowResults("TTT", ["F", "T"], false, 3), [null, null, null], "a key of the wrong length is ignored");
});

test("the wrong statements are named for screen readers", () => {
  assert.equal(wrongRowsLabel(["incorrect", "correct", "correct"]), "Statement 1");
  assert.equal(wrongRowsLabel(["incorrect", "correct", "incorrect", "incorrect"]), "Statements 1, 3 and 4");
  assert.equal(wrongRowsLabel(["correct", null]), null);
});

test("keyboard: T and F set a row, arrow keys switch it", () => {
  assert.equal(tfKeyAction("t", null), "T");
  assert.equal(tfKeyAction("F", "T"), "F");
  assert.equal(tfKeyAction("ArrowRight", "T"), "F");
  assert.equal(tfKeyAction("ArrowRight", null), "F");
  assert.equal(tfKeyAction("ArrowLeft", "F"), "T");
  assert.equal(tfKeyAction("ArrowDown", "F"), "T", "wraps like a radio group");
  assert.equal(tfKeyAction("x", "T"), undefined);
  assert.equal(tfKeyAction("Enter", "T"), undefined);
});

test("the grader's feedback names the statements marked wrong, which rebuilds the key", () => {
  // backend grading._check_true_false_set: "Statement 2 is actually false; statement 3 is actually true."
  assert.deepEqual(tfKeyFromFeedback("Statement 2 is actually false; statement 3 is actually true.", "TTF", 3), ["T", "F", "T"]);
  assert.equal(tfKeyFromFeedback("Correct.", "FTT", 3), null);
  assert.equal(tfKeyFromFeedback("Statement 4 is actually true.", "FTT", 3), null, "no such statement");
  assert.equal(tfKeyFromFeedback("Statement 1 is actually true.", "FT", 3), null, "incomplete response");
});

test("the key comes from the most authoritative source", () => {
  const feedback = "Statement 1 is actually false.";
  // a wrong answer: the grader's feedback wins over a prose answer_display
  assert.deepEqual(resolveTfKey("Only (1,9)=(9,1) is false", feedback, "TTT", false, 3), ["F", "T", "T"]);
  assert.deepEqual(resolveTfKey("1. False; 2. True; 3. True", "Not quite.", "TTT", false, 3), ["F", "T", "T"]);
  assert.deepEqual(resolveTfKey("1 False · 2 True · 3 True", "Not quite.", "TTT", false, 3), ["F", "T", "T"]);
  // a right answer is its own key
  assert.deepEqual(resolveTfKey("prose", "Correct.", "FTT", true, 3), ["F", "T", "T"]);
  // not graded yet (a revealed solution): the answer text only
  assert.deepEqual(resolveTfKey("F, T, T", "", null, null, 3), ["F", "T", "T"]);
  assert.equal(resolveTfKey("prose", "Not quite.", "TTT", false, 3), null);
  const key = resolveTfKey("", feedback, "TTT", false, 3);
  assert.deepEqual(tfRowResults("TTT", key, false, 3), ["incorrect", "correct", "correct"]);
});

test("T / F with nothing focused mark the next unmarked statement", () => {
  let marks = emptyTfMarks(3);
  marks = markNextUnmarked(marks, "F");
  marks = markNextUnmarked(marks, "T");
  assert.deepEqual(marks, ["F", "T", null]);
  marks = markNextUnmarked(setTfMark(marks, 0, null), "T");
  assert.deepEqual(marks, ["T", "T", null], "fills a gap first");
  marks = markNextUnmarked(markNextUnmarked(marks, "F"), "F");
  assert.deepEqual(marks, ["T", "T", "F"], "all marked: unchanged");
});
