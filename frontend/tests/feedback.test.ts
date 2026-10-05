// Stock verdict lines under a verdict title (src/lib/feedback.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { stripStockVerdict } from "../src/lib/feedback.ts";

test("a feedback that is only the stock verdict becomes empty", () => {
  for (const text of ["Correct.", "Not quite.", "  not quite  ", "Correct", "Not this option.", "Compare with the explanation.", "Correct!"]) {
    assert.equal(stripStockVerdict(text), "", text);
  }
});

test("what follows a stock verdict is kept", () => {
  assert.equal(
    stripStockVerdict("Correct. With help, so it doesn't count toward the two in a row — but it isn't a miss."),
    "With help, so it doesn't count toward the two in a row — but it isn't a miss.",
  );
  assert.equal(stripStockVerdict("Not quite. That ends the review."), "That ends the review.");
});

test("an informative verdict is not stripped", () => {
  for (const text of [
    "Correct — equivalent to the expected answer.",
    "Correct (within the allowed tolerance).",
    "Not quite — check the sign of the imaginary part.",
    "Correctly expanded, but not simplified.",
    "Close — check your arithmetic.",
    "Not equivalent to the expected answer.",
    "",
  ]) {
    assert.equal(stripStockVerdict(text), text.trim(), text);
  }
});
