// The answer box's live preview (src/lib/mathInput.ts): the empty set in every notation the grader accepts.
import assert from "node:assert/strict";
import { test } from "node:test";
import { asciiToLatex, subscriptRefusal, toggleSubscript } from "../src/lib/mathInput.ts";

test("the empty set previews as ∅ in every accepted notation", () => {
  for (const typed of ["∅", "Ø", "{}", "{ }", "empty", "Empty set", "EmptySet"]) {
    assert.equal(asciiToLatex(typed), "\\varnothing", typed);
  }
  assert.notEqual(asciiToLatex("{3}"), "\\varnothing");
  assert.notEqual(asciiToLatex("2+3i"), "\\varnothing");
});

test("indexed names preview the way the grader reads them", () => {
  const cases: [string, string | null][] = [
    ["x_k", "x_{k}"],
    ["ax_k", "a\\,x_{k}"], // a·x_k: a one-letter index glued to its coefficient splits off (grading.py)
    ["(ax_k, bx_k)", "\\left(a\\,x_{k},\\ b\\,x_{k}\\right)"],
    ["a_kx_k", "a_{k}\\,x_{k}"],
    ["(a+b)x_k", "\\left(a + b\\right)x_{k}"],
    ["x1", "x_{1}"], // the grader reads x1 as x_1, not x·1
    ["x_1", "x_{1}"],
    ["x_{12}", "x_{12}"],
    ["x₁₂", "x_{12}"],
    ["xₖ", "x_{k}"],
    ["(axₖ, bxₖ)", "\\left(a\\,x_{k},\\ b\\,x_{k}\\right)"],
    ["theta1", "\\theta_{1}"],
    ["lambda_k", "\\lambda_{k}"],
    ["lamda_k", "\\lambda_{k}"], // the grader's spelling
    ["atheta_k", "a\\theta_{k}"], // the index on the Greek name, not on its last letter
    ["pi_k", "\\pi_{k}"],
    ["sqrt2", "\\sqrt{2}"], // a digit after a function or constant is a number
    ["pi2", "\\pi \\cdot 2"],
    ["re1", "r\\,e_{1}"], // re is a function only when called
    // a longer index is one name, as the grader reads it — x_ky shows the learner the index "ky"
    ["x_ky", "x_{ky}"],
    ["a_ij", "a_{ij}"],
    ["a_{ij}", "a_{ij}"],
    ["x__1", null], // no name the grader reads: shown as typed
    ["a_b_c", null],
    ["x!", null], // the grader refuses factorials
    // braces or a subscript glyph end the index
    ["x_{k}y", "x_{k}\\,y"],
    ["xₖy", "x_{k}\\,y"],
    ["a_{ij}x_j", "a_{ij}\\,x_{j}"],
    // letters split the grader's way: functions, π and Greek names peeled from the left, case-sensitive
    ["sinx_k", "\\sin\\left(x_{k}\\right)"],
    ["Imz_k", "I\\,m\\,z_{k}"],
    ["Re1", "\\operatorname{Re}\\left(1\\right)"],
    ["betau_k", "\\beta\\,u_{k}"],
    ["alambda_k", "a\\lambda_{k}"],
    ["ell_k", "l_{k}"],
    ["pi_kx_k", null], // one name the grader does not know
    ["x_{1}2", "x_{1} \\cdot 2"], // the braces end the index: x_1·2
    ["x₁2", "x_{1} \\cdot 2"],
    // plain runs: functions and π peeled like the grader's letter runs; Greek only as a whole word (inside a run the
    // grader reads Greek only when the key uses it); letters drawn as typed, never as a function the grader won't apply
    ["lambdav", "lambdav"],
    ["thetak", "thetak"],
    ["bxi", "bxi"],
    ["theta", "\\theta"],
    ["xy", "xy"],
    ["sinx", "\\sin\\left(x\\right)"],
    ["re z", "\\operatorname{Re}\\left(z\\right)"],
    ["Rez", "Rez"],
    ["imz", "imz"],
    ["xpi_k", "xpi_{k}"], // one name for the grader
    ["x_lambda", "x_{lambda}"], // spelled as typed
    ["xₖℓ", "x_{k}\\,l"],
    ["x_ell", "x_{ell}"],
    // Greek inside a run is cut where the grader cuts (never 'the·tan'), and drawn as typed
    ["cos(thetan)", "\\cos\\left(thetan\\right)"],
    ["lambdasin(x)", "lambda\\sin\\left(x\\right)"],
    // a function without brackets takes the rest of the product, as the grader reads it
    ["2sinxcosx", "2\\sin\\left(x\\,\\cos\\left(x\\right)\\right)"],
    ["sinx/2", "\\frac{\\sin\\left(x\\right)}{2}"],
    ["sin x + 1", "\\sin\\left(x\\right) + 1"],
    // … but a root takes one number or word, as the grader reads it (√2 i is √2·i, never √(2i))
    ["√2 i", "\\sqrt{2}\\,i"],
    ["1+√3i", "1 + \\sqrt{3}\\,i"],
    ["sqrt2 i", "\\sqrt{2}\\,i"],
    ["sqrt0.5", "\\sqrt{0.5}"],
    ["cos2.5", "\\cos\\left(2.5\\right)"],
    // the grader's bare root: at most one space before the number, no "(" glued after it
    ["sqrt2 (1+i)", "\\sqrt{2}\\left(1 + i\\right)"],
    ["sqrt2(1+i)", "\\sqrt{2\\,\\left(1 + i\\right)}"],
    ["sqrt  2 i", "\\sqrt{2\\,i}"],
    // a decimal splits off only where the grader makes the digits a number: 'i0.5' is i_0·.5
    ["4+i0.5", "4 + i_{0} \\cdot .5"],
    ["pi2.5", "\\pi \\cdot 2.5"],
    // Greek glyphs and ℓ as the grader reads them
    ["θ_k", "\\theta_{k}"],
    ["aθ_k", "a\\theta_{k}"],
    ["ℓ_k", "l_{k}"],
  ];
  for (const [typed, latex] of cases) assert.equal(asciiToLatex(typed), latex, typed);
});

test("the subscript key turns the index before the caret into a subscript, and back", () => {
  const press = (value: string, start = value.length, end = start) => toggleSubscript(value, start, end);
  assert.deepEqual(press("axk"), { value: "ax_k", caret: 4 });
  assert.deepEqual(press("x12"), { value: "x_12", caret: 4 }); // a run of digits is one index
  assert.deepEqual(press("(a xk, b"), null); // nothing to index right before the caret
  assert.deepEqual(press("(a xk, b", 5), { value: "(a x_k, b", caret: 6 });
  assert.deepEqual(press("xk+1", 2), { value: "x_k+1", caret: 3 });
  assert.deepEqual(press("a_kxk"), { value: "a_kx_k", caret: 6 });
  assert.deepEqual(press("lambdak"), { value: "lambda_k", caret: 8 });
  assert.deepEqual(press("axk", 2, 3), { value: "ax_k", caret: 4 }); // a selected index
  assert.deepEqual(press("x12", 2, 3), null); // part of a number
  // pressing again undoes it
  assert.deepEqual(press("ax_k"), { value: "axk", caret: 3 });
  assert.deepEqual(press("x_{k}"), { value: "xk", caret: 2 });
  assert.deepEqual(press("x_k", 2, 3), { value: "xk", caret: 2 });
  assert.deepEqual(press("x_{ab}"), null); // a typed longer index is left alone
  // nothing to carry the index, or a word the grader would read with another index: nothing happens
  for (const [value, caret] of [["", 0], ["k", 1], ["2k", 2], ["(a+b)k", 6], ["a_kx", 4], ["axk", 2], ["x1yk", 4], ["ax1", 2], ["ax_k", 2],
    ["a_ijxj", 6], ["x_{ab}", 5], ["\\thetak", 7], ["pi_kxk", 6], ["z1sin12", 6]] as const) {
    assert.equal(press(value, caret), null, `${value} @${caret}`);
  }
  assert.deepEqual(press("a_kxk"), { value: "a_kx_k", caret: 6 }); // an earlier index keeps its reading
  assert.deepEqual(press("x_{1}yk"), { value: "x_{1}y_k", caret: 8 }); // the braces end x_1's index
  assert.deepEqual(press("(a xk"), { value: "(a x_k", caret: 6 }); // mid-typing: the bracket is not closed yet
  // a number before the name is a coefficient; Greek names and glyphs carry indices too; set braces are fine
  for (const [value, caret, result] of [["2xk", 3, "2x_k"], ["-3xk", 4, "-3x_k"], ["0.5xk", 5, "0.5x_k"], ["(2xk, 3xk)", 4, "(2x_k, 3xk)"],
    ["2thetak", 7, "2theta_k"], ["x_klambdak", 10, "x_klambda_k"], ["a_kalphak", 9, "a_kalpha_k"], ["θk", 2, "θ_k"], ["{xk}", 3, "{x_k}"]] as const) {
    assert.equal(press(value, caret)?.value, result, `${value} @${caret}`);
  }
  assert.deepEqual(press("2x_k"), { value: "2xk", caret: 3 }); // and back
  // why nothing happened — each reason has its own advice
  assert.deepEqual(subscriptRefusal("axk", 2, 2), { why: "joins", index: "x", next: "k" });
  assert.deepEqual(subscriptRefusal("xky", 2, 2), { why: "joins", index: "k", next: "y" });
  assert.deepEqual(subscriptRefusal("a_ijxj", 6, 6), { why: "reread", earlier: "a_ij", at: "xj" });
  // a two-letter name the index ends is split as typed; a called function, ell and a cursor inside a name are not
  assert.equal(press("xi")?.value, "x_i");
  assert.equal(press("2pi")?.value, "2p_i");
  for (const [value, caret, name, called, typedAfter] of [["re(z)", 2, "re", true, false], ["re z", 2, "re", true, false],
    ["ln x", 2, "ln", true, false], ["Imθ", 2, "Im", true, false], ["ell", 3, "ell", false, false],
    ["thetak", 4, "theta", false, true], ["theta", 5, "theta", false, false], ["theta", 4, "theta", false, false],
    ["lambda1", 6, "lambda", false, true]] as const) {
    assert.deepEqual(subscriptRefusal(value, caret, caret), { why: "name", name, called, typedAfter }, value);
  }

  assert.equal(subscriptRefusal("sin(x)", 3, 3).why, "name"); // never 'thet_a', 'si_n(x)'
  assert.equal(press("e^{ixk", 6)?.value, "e^{ix_k"); // an exponent group is fine; only _{…} is the learner's own index
  assert.deepEqual(subscriptRefusal("pi2", 3, 3), { why: "number", name: "pi" });
  assert.deepEqual(subscriptRefusal("x12", 2, 2), { why: "partial" });
  assert.deepEqual(subscriptRefusal("\\thetak", 7, 7), { why: "backslash" });
  assert.deepEqual(subscriptRefusal("x_{ak", 5, 5), { why: "braces" });
  assert.deepEqual(subscriptRefusal("(a+b)k", 6, 6), { why: "nothing" });
  assert.deepEqual(subscriptRefusal("", 0, 0), { why: "nothing" });
});

test("every edit of the key previews with exactly the typed index", () => {
  const letters = ["a", "x", "k", "j", "lambda", "theta", "θ", "pi", "2", "1", "0.5", "_", " ", "+", "(", ")", "{", "ₖ"];
  const inputs: string[] = [];
  for (const a of letters) for (const b of letters) for (const c of letters) inputs.push(a + b + c, `(${a}${b}${c}k, ${c}x1k)`);
  let edits = 0;
  for (const value of inputs) {
    if (asciiToLatex(value) === null) continue; // already unreadable as typed
    for (let start = 0; start <= value.length; start++) {
      for (let end = start; end <= Math.min(value.length, start + 2); end++) {
        const change = toggleSubscript(value, start, end);
        if (!change) continue;
        edits++;
        const latex = asciiToLatex(change.value);
        assert.notEqual(latex, null, `${value} [${start},${end}] -> ${change.value}`);
        if (change.value.length > value.length) {
          let at = 0; // where the "_" went in
          while (change.value[at] === value[at]) at++;
          const index = change.value.slice(at + 1, change.caret);
          assert.ok(latex!.includes(`_{${index}}`), `${value} [${start},${end}] -> ${change.value}: ${latex}`);
        }
        const back = toggleSubscript(change.value, change.caret, change.caret);
        assert.equal(back?.value, value, `round trip ${value} [${start},${end}] -> ${change.value}`);
      }
    }
  }
  assert.ok(edits > 1000, `${edits} edits`);
});
