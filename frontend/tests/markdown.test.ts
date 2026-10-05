// Regression tests for the Markdown/TeX normaliser (src/lib/markdown.ts). Run: npm test (Node >= 23, type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeMath } from "../src/lib/markdown.ts";

const timed = (input: string): { out: string; ms: number } => {
  const start = performance.now();
  const out = normalizeMath(input);
  return { out, ms: performance.now() - start };
};

test("long runs of indentation normalise in linear time (no regex backtracking)", () => {
  const lines = [
    " ".repeat(2000),
    " ".repeat(2000) + "text",
    "\t".repeat(2000) + "text",
    " >".repeat(1000) + " text",
    "> \t".repeat(700) + "x",
    " ".repeat(2000) + "\r",
  ];
  for (const line of lines) {
    for (const input of [`$$x$$\n\n${line}`, `${line}\n$$x = 1$$`, `Text $$\n${line}\n$$ end`]) {
      normalizeMath(input); // warm-up (JIT)
      const { ms } = timed(input);
      assert.ok(ms < 20, `took ${ms.toFixed(1)} ms for a ${line.length}-character line`);
    }
  }
});

test("a deeply nested list next to a display block only gets the display split", () => {
  const list = "- a\n    - b\n        - c\n            - d\n                - e: the deepest case";
  const { out, ms } = timed(`The key identity is\n\n$$x^2 \\geq 0$$\n\nCases:\n\n${list}`);
  assert.ok(ms < 20, `took ${ms.toFixed(1)} ms`);
  assert.equal(out, `The key identity is\n\n$$\nx^2 \\geq 0\n$$\n\nCases:\n\n${list}`);
  const inline = "- a\n    - b\n        - c\n            - d\n                - e: $$x^2$$ text";
  assert.equal(normalizeMath(inline), inline);
});

test("fenced code keeps its $$ lines untouched, also inside a blockquote", () => {
  const code = "$$x$$\n\n```python\nprice = '$$'\n                cost = '$$ 5'\n```\nafter";
  assert.equal(normalizeMath(code), "$$\nx\n$$\n\n```python\nprice = '$$'\n                cost = '$$ 5'\n```\nafter");
  const quoted = "> ```\n> a $$ b\n> ```\n> $$y$$";
  assert.equal(normalizeMath(quoted), "> ```\n> a $$ b\n> ```\n> $$\n> y\n> $$");
});

test("an unmatched mid-line $$ stays literal text instead of swallowing the rest", () => {
  const stray = "The cost formula uses $$ signs.\n\nNext paragraph with $x$ and more text.";
  assert.equal(normalizeMath(stray), stray);
  const truncated = "We get $$x = \\frac{1}{2\n\nThe next paragraph.";
  assert.equal(normalizeMath(truncated), truncated);
  // A stray `$$` before a valid display: the stray one stays, the display is still split.
  assert.equal(normalizeMath("Costs $$ more.\n\n$$x = 1$$"), "Costs $$ more.\n\n$$\nx = 1\n$$");
});

test("$$ inside inline code is not a delimiter, even when a later line has one", () => {
  const input = "Use `$$` for display math.\n$$x = 1$$";
  assert.equal(normalizeMath(input), "Use `$$` for display math.\n$$\nx = 1\n$$");
});

test("display openers and closers are put on their own lines", () => {
  assert.equal(
    normalizeMath("$$\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}$$"),
    "$$\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n$$",
  );
  assert.equal(normalizeMath("> $$x = 1.$$"), "> $$\n> x = 1.\n> $$");
  assert.equal(normalizeMath("So $$\nx = 1\n$$ follows."), "So\n$$\nx = 1\n$$\nfollows.");
  // A closing fence followed by text that opens the next display.
  assert.equal(normalizeMath("A $$\nx\n$$ B $$\ny\n$$"), "A\n$$\nx\n$$\nB\n$$\ny\n$$");
  // Already-fenced displays and balanced inline pairs are left alone.
  assert.equal(normalizeMath("$$\nx = 1\n$$"), "$$\nx = 1\n$$");
  assert.equal(normalizeMath("Both $$a$$ and $$b$$ inline."), "Both $$a$$ and $$b$$ inline.");
});

test("\\( \\) and \\[ \\] become $ and $$ delimiters", () => {
  assert.equal(normalizeMath("Let \\(x\\) be \\[x^2\\] done"), "Let $x$ be \n$$\nx^2\n$$\n done");
});
