// The answer box's live preview (src/lib/mathInput.ts): the empty set in every notation the grader accepts.
import assert from "node:assert/strict";
import { test } from "node:test";
import { asciiToLatex } from "../src/lib/mathInput.ts";

test("the empty set previews as ∅ in every accepted notation", () => {
  for (const typed of ["∅", "Ø", "{}", "{ }", "empty", "Empty set", "EmptySet"]) {
    assert.equal(asciiToLatex(typed), "\\varnothing", typed);
  }
  assert.notEqual(asciiToLatex("{3}"), "\\varnothing");
  assert.notEqual(asciiToLatex("2+3i"), "\\varnothing");
});
