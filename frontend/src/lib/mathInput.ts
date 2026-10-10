// Live preview of typed answers: converts calculator-style input ("-7+22*i", "sqrt(2)/2", "(1, -2, 3)",
// "x^2 + 3x", "e^(i*pi)", "a x_k") into LaTeX. Purely presentational — grading happens on the server with SymPy,
// and names are read the way it reads them (app/learning/grading.py _split_identifiers, mirrored here): x1, x_1, x_{1}
// and x₁ are x_1, xₖ is x_k; a one-letter index glued to its coefficient splits off ('ax_k' is a·x_k, 'a_kx_k' is
// a_k·x_k); braces or a subscript glyph end an index (x_{k}y and xₖy are x_k·y); a longer index is one name ('a_ij',
// 'x_ky' — shown as x with the index ky, so a learner who meant x_k·y sees it); letters split like the grader's
// letter runs (functions, π and Greek names peeled from the left); what the grader can't read is shown as typed.

type Token =
  | { t: "num"; v: string; gap?: string; glued?: boolean } // gap: the spaces before it; glued: "(" right after it
  | { t: "id"; v: string; sub?: string; plain?: boolean } // plain: letters the grader multiplies, drawn as typed
  | { t: "op"; v: string };

const GREEK = new Set([
  "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "mu",
  "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
  "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega",
]);

const FUNCTIONS: Record<string, (arg: string) => string> = {
  sqrt: (a) => `\\sqrt{${a}}`,
  abs: (a) => `\\left|${a}\\right|`,
  conj: (a) => `\\overline{${a}}`,
  conjugate: (a) => `\\overline{${a}}`,
  exp: (a) => `e^{${a}}`,
  re: (a) => `\\operatorname{Re}\\left(${a}\\right)`,
  im: (a) => `\\operatorname{Im}\\left(${a}\\right)`,
  arg: (a) => `\\arg\\left(${a}\\right)`,
};
const NAMED_FUNCTIONS = new Set(["sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "det", "dim"]);
const CONSTANTS: Record<string, string> = { pi: "\\pi", oo: "\\infty", inf: "\\infty", infinity: "\\infty" };

// The grader's name tables (grading.py): names never split (_KNOWN_NAMES, case-sensitive), names that are functions
// only when called (_PAREN_ONLY), and what a glued letter run peels off (_PEEL_FUNCTIONS, pi, the Greek letters).
const KNOWN = new Set([
  "sqrt", "cbrt", "root", "exp", "ln", "log", "sin", "cos", "tan", "asin", "acos", "atan", "arcsin", "arccos",
  "arctan", "sinh", "cosh", "tanh", "abs", "Abs", "conj", "conjugate", "bar", "re", "Re", "im", "Im", "arg", "Arg", "pi",
]);
const PAREN_ONLY = new Set(["re", "im", "arg", "bar"]);
const GRADER_GREEK = new Set([
  "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lamda", "mu", "nu", "xi",
  "omicron", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
]);
const PEEL_NAMES = [
  ...["conjugate", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "sqrt", "cbrt", "asin", "acos", "atan"],
  ...["conj", "root", "sin", "cos", "tan", "exp", "abs", "log", "ln", "pi"],
].sort((a, b) => b.length - a.length);
const PEEL = [...PEEL_NAMES, ...GRADER_GREEK].sort((a, b) => b.length - a.length);

const SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉";
const SUBSCRIPT_LETTERS: Record<string, string> = {
  ₐ: "a", ₑ: "e", ₒ: "o", ₓ: "x", ₕ: "h", ₖ: "k", ₗ: "l", ₘ: "m", ₙ: "n", ₚ: "p", ₛ: "s", ₜ: "t", ᵢ: "i", ᵣ: "r", ᵤ: "u",
  ᵥ: "v", ⱼ: "j",
};
// Greek glyphs as the grader reads them (grading.py _GREEK_CHARS): the name, kept glued to an index that follows.
const GREEK_GLYPHS: Record<string, string> = {
  α: "alpha", β: "beta", γ: "gamma", δ: "delta", ε: "epsilon", ϵ: "epsilon", ζ: "zeta", η: "eta", θ: "theta", ϑ: "theta",
  ι: "iota", κ: "kappa", λ: "lamda", μ: "mu", ν: "nu", ξ: "xi", ο: "omicron", π: "pi", ρ: "rho", σ: "sigma", τ: "tau",
  υ: "upsilon", φ: "phi", ϕ: "phi", χ: "chi", ψ: "psi", ω: "omega",
};
const GREEK_GLYPH_RUN = new RegExp(`[${Object.keys(GREEK_GLYPHS).join("")}]`, "g");
const SUBSCRIPT_RUN = new RegExp(`[${SUBSCRIPT_DIGITS}${Object.keys(SUBSCRIPT_LETTERS).join("")}]+`, "g");

// The grader's identifier shapes (grading.py _INDEXED_WORD, _LETTER_INDEXED_WORD; a word with one '_' and a longer
// index is kept whole there).
const INDEXED_WORD = /^[A-Za-z]+(?:_?\d+[A-Za-z]+)*(?:_?\d+)?$/;
const LETTER_INDEXED_WORD = /^(?:[A-Za-z]+_[A-Za-z])+$/;
const ONE_NAME = /^([A-Za-z]+)_([A-Za-z0-9]+)$/;

/** grading.py _peel (a letter run not followed by '('): known names whole, functions/π (and Greek) peeled from the
 * left. `greek`: also peel Greek names — the grader does so only for the Greek letters the answer key uses. */
function peel(run: string, greek = true): string[] {
  const word = run.length > 1 && !KNOWN.has(run) && KNOWN.has(run.toLowerCase()) ? run.toLowerCase() : run;
  if (PAREN_ONLY.has(word)) return [...word];
  if (word.length === 1 || KNOWN.has(word) || (greek && GRADER_GREEK.has(word))) return [word];
  const names = greek ? PEEL : PEEL_NAMES;
  const out: string[] = [];
  for (let i = 0; i < word.length; ) {
    const name = names.find((n) => word.startsWith(n, i)) ?? word[i];
    out.push(name);
    i += name.length;
  }
  return out;
}

/** A plain letter run the grader's way: a known name or a whole Greek word stays one; inside a longer run functions and
 * π are peeled ('sinx' is sin(x)) but Greek names are not (the grader reads 'bxi' as b·x·i unless the key uses ξ), and
 * the remaining single letters are drawn as typed ('xy', 'Rez' — never a function the grader does not apply). */
function runTokens(run: string, call: boolean): Token[] {
  const word = run.length > 1 && !KNOWN.has(run) && KNOWN.has(run.toLowerCase()) ? run.toLowerCase() : run;
  if (KNOWN.has(word) && (call || !PAREN_ONLY.has(word))) return [{ t: "id", v: word }];
  if (GRADER_GREEK.has(word) || GREEK.has(run)) return [{ t: "id", v: run }];
  const tokens: Token[] = [];
  for (const piece of peel(word)) {
    // cut where the grader cuts when the key uses that Greek letter ('thetan' is θ·n, never the·tan), but draw Greek
    // pieces as the letters typed: inside a run the grader reads Greek only when the key has it
    const letters = piece.length === 1 || GRADER_GREEK.has(piece);
    const prev = tokens.at(-1);
    if (letters && prev?.t === "id" && prev.plain) prev.v += piece;
    else tokens.push(letters ? { t: "id", v: piece, plain: true } : { t: "id", v: piece });
  }
  return tokens;
}

/** One identifier run as typed (letters, digits, underscores) -> tokens, or null for a name the grader can't read.
 * `call`: the run is applied to what follows ('re(z)', 're z'), so a name that is a function only when called stays one. */
function wordTokens(word: string, call = false): Token[] | null {
  if (!word.includes("_") && !/\d/.test(word)) return runTokens(word, call);
  const tokens: Token[] = [];
  if (LETTER_INDEXED_WORD.test(word)) {
    const segments = [...word.matchAll(/([A-Za-z]+)_([A-Za-z])/g)];
    for (const [, letters, sub] of segments) {
      const pieces = peel(letters);
      const last = pieces.pop()!;
      if (KNOWN.has(last) && segments.length > 1) return null; // 'pi_kx_k': one name the grader does not know
      if (KNOWN.has(last) && pieces.length) return [{ t: "id", v: letters, sub, plain: true }]; // 'xpi_k': one name
      tokens.push(...pieces.map((v) => ({ t: "id" as const, v })), { t: "id", v: last, sub });
    }
    return tokens;
  }
  if (INDEXED_WORD.test(word)) {
    for (const [, letters, index] of word.matchAll(/([A-Za-z]+)(?:_?(\d+))?/g)) {
      const pieces = peel(letters);
      if (index === undefined) {
        tokens.push(...runTokens(letters, false));
        continue;
      }
      const last = pieces.pop()!;
      tokens.push(...pieces.map((v) => ({ t: "id" as const, v })));
      if (KNOWN.has(last)) tokens.push({ t: "id", v: last }, { t: "num", v: index }); // sqrt2 is √2, Re1 is Re(1)
      else tokens.push({ t: "id", v: last, sub: index });
    }
    return tokens;
  }
  const whole = ONE_NAME.exec(word);
  return whole ? [{ t: "id", v: whole[1], sub: whole[2] }] : null; // a_ij, x_ky: one name, as the grader reads it
}

function normalizeUnicode(input: string): string {
  return input
    .replace(/ℓ/g, "l") // first, like the grader's NFKC: xₖℓ is x_k·l
    .replace(/[−–—]/g, "-")
    .replace(/[×·⋅∙]/g, "*")
    .replace(/÷/g, "/")
    .replace(/∛\s*([0-9.]+|[A-Za-z]\w*)/g, " cbrt($1)") // grading.py _replace_sqrt_sign: √ takes one number or word
    .replace(/∛/g, " cbrt")
    .replace(/√\s*([0-9.]+|[A-Za-z]\w*)/g, " sqrt($1)")
    .replace(/√/g, " sqrt")
    .replace(/∞/g, "oo")
    .replace(/\*\*/g, "^")
    .replace(SUBSCRIPT_RUN, (run, at: number, all: string) => {
      const after = all[at + run.length] ?? "";
      const index = [...run].map((c) => SUBSCRIPT_LETTERS[c] ?? SUBSCRIPT_DIGITS.indexOf(c)).join("");
      return "_" + index + (/\d/.test(after) ? "*" : /[A-Za-z]/.test(after) ? " " : ""); // the glyph ends the index
    })
    .replace(GREEK_GLYPH_RUN, (glyph, at: number, all: string) => ` ${GREEK_GLYPHS[glyph]}` + (/[_0-9]/.test(all[at + 1] ?? "") ? "" : " "))
    .replace(/_\{([A-Za-z0-9]+)\}(?=[A-Za-z])/g, "_$1 ") // the braces end the index: x_{k}y is x_k·y
    .replace(/_\{([A-Za-z0-9]+)\}(?=\d)/g, "_$1*") // x_{1}2 is x_1·2
    .replace(/_\{([A-Za-z0-9]+)\}/g, "_$1")
    .replace(/(?<![A-Za-z\\_])(?<!_\{)ell(?![A-Za-z])/g, "l") // the script letter ℓ spelled out, as in the grader
    .replace(/lambda/g, "lamda"); // the grader's spelling, glued too
}

function tokenize(src: string): Token[] | null {
  const tokens: Token[] = [];
  let i = 0;
  let gap = "";
  while (i < src.length) {
    const ch = src[i];
    if (/\s/.test(ch)) {
      gap += ch;
      i++;
      continue;
    }
    const num = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(src.slice(i));
    if (num) {
      tokens.push({ t: "num", v: num[0], gap, glued: src[i + num[0].length] === "(" });
      i += num[0].length;
      gap = "";
      continue;
    }
    let word = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i));
    const decimal = word && /[A-Za-z](\d+)$/.exec(word[0]);
    // 'sqrt0.5': the number is 0.5 — only where the grader itself splits the digits off as a number ('i0.5' is i_0·.5)
    if (word && decimal && /^\.\d/.test(src.slice(i + word[0].length)) && wordTokens(word[0])?.at(-1)?.t === "num") {
      word = /^[A-Za-z_][A-Za-z0-9_]*/.exec(word[0].slice(0, -decimal[1].length));
    }
    if (word) {
      const after = src.slice(i + word[0].length).trimStart();
      const call = after.startsWith("(") || (PAREN_ONLY.has(word[0]) && /^[A-Za-z0-9(_.]/.test(after));
      const parts = wordTokens(word[0], call);
      if (!parts) return null;
      const last = parts.at(-1);
      if (last?.t === "num") parts[parts.length - 1] = { ...last, gap: "", glued: src[i + word[0].length] === "(" }; // 'sqrt2'
      tokens.push(...parts);
      i += word[0].length;
      gap = "";
      continue;
    }
    if ("+-*/^(),[]|".includes(ch)) {
      tokens.push({ t: "op", v: ch });
      i++;
      gap = "";
      continue;
    }
    return null;
  }
  return tokens;
}

/** A rendered sub-expression plus what it starts/ends with, for spacing decisions. */
interface Node {
  tex: string;
  /** precedence: 1 sum, 2 product, 3 unary, 4 power/atom */
  prec: number;
  startsWithDigit: boolean;
  /** Body without wrapping parentheses (used for fractions and exponents). */
  bare?: string;
}

class Parser {
  private pos = 0;
  private readonly tokens: Token[];

  constructor(tokens: Token[]) {
    this.tokens = tokens;
  }

  parse(): string {
    const node = this.list();
    if (this.pos < this.tokens.length) throw new Error("trailing input");
    return node.tex;
  }

  private peek(): Token | undefined {
    return this.tokens[this.pos];
  }

  private isOp(v: string): boolean {
    const tok = this.peek();
    return tok?.t === "op" && tok.v === v;
  }

  private expectOp(v: string): void {
    if (!this.isOp(v)) throw new Error(`expected ${v}`);
    this.pos++;
  }

  private list(): Node {
    const items = [this.sum()];
    while (this.isOp(",")) {
      this.pos++;
      items.push(this.sum());
    }
    if (items.length === 1) return items[0];
    return { tex: items.map((n) => n.tex).join(",\\ "), prec: 0, startsWithDigit: false };
  }

  private sum(): Node {
    let left = this.product();
    while (this.isOp("+") || this.isOp("-")) {
      const op = (this.tokens[this.pos++] as { v: string }).v;
      const right = this.product();
      left = { tex: `${left.tex} ${op} ${right.tex}`, prec: 1, startsWithDigit: left.startsWithDigit };
    }
    return left;
  }

  private startsFactor(): boolean {
    const tok = this.peek();
    if (!tok) return false;
    return tok.t === "num" || tok.t === "id" || (tok.t === "op" && (tok.v === "(" || tok.v === "["));
  }

  private product(): Node {
    let left = this.unary();
    for (;;) {
      if (this.isOp("*")) {
        this.pos++;
        const right = this.unary();
        // "22*i" reads better as 22i; keep an explicit dot elsewhere.
        const juxtapose = /\d$/.test(left.tex) && !right.startsWithDigit && right.prec >= 4;
        left = { tex: juxtapose ? `${left.tex}${right.tex}` : `${left.tex} \\cdot ${right.tex}`, prec: 2, startsWithDigit: left.startsWithDigit };
      } else if (this.isOp("/")) {
        this.pos++;
        const right = this.unary();
        left = { tex: `\\frac{${left.bare ?? left.tex}}{${right.bare ?? right.tex}}`, prec: 4, startsWithDigit: false };
      } else if (this.startsFactor()) {
        const right = this.unary();
        const sep = right.startsWithDigit ? " \\cdot " : /[A-Za-z}]$/.test(left.tex) && /^[A-Za-z]/.test(right.tex) ? "\\," : "";
        left = { tex: `${left.tex}${sep}${right.tex}`, prec: 2, startsWithDigit: left.startsWithDigit };
      } else {
        return left;
      }
    }
  }

  private unary(): Node {
    if (this.isOp("-") || this.isOp("+")) {
      const op = (this.tokens[this.pos++] as { v: string }).v;
      const inner = this.unary();
      return { tex: `${op}${inner.tex}`, prec: 3, startsWithDigit: false };
    }
    return this.power();
  }

  private power(): Node {
    const base = this.postfix();
    if (!this.isOp("^")) return base;
    this.pos++;
    const exponent = this.unary();
    const baseTex = base.prec < 4 ? `\\left(${base.tex}\\right)` : base.tex;
    return { tex: `{${baseTex}}^{${exponent.bare ?? exponent.tex}}`, prec: 4, startsWithDigit: base.startsWithDigit };
  }

  private postfix(): Node {
    let node = this.atom();
    while (this.isOp("!")) {
      this.pos++;
      node = { tex: `${node.tex}!`, prec: 4, startsWithDigit: node.startsWithDigit };
    }
    return node;
  }

  private group(open: string, close: string): Node {
    this.expectOp(open);
    const inner = this.list();
    this.expectOp(close);
    const [l, r] = open === "[" ? ["\\left[", "\\right]"] : ["\\left(", "\\right)"];
    return { tex: `${l}${inner.tex}${r}`, prec: 4, startsWithDigit: false, bare: inner.tex };
  }

  private atom(): Node {
    const tok = this.peek();
    if (!tok) throw new Error("unexpected end");
    if (tok.t === "num") {
      this.pos++;
      const [mantissa, exp] = tok.v.toLowerCase().split("e");
      const tex = exp !== undefined ? `${mantissa} \\times 10^{${Number(exp)}}` : tok.v;
      return { tex, prec: exp !== undefined ? 2 : 4, startsWithDigit: true };
    }
    if (tok.t === "op") {
      if (tok.v === "(") return this.group("(", ")");
      if (tok.v === "[") return this.group("[", "]");
      if (tok.v === "|") {
        this.pos++;
        const inner = this.sum();
        this.expectOp("|");
        return { tex: `\\left|${inner.tex}\\right|`, prec: 4, startsWithDigit: false };
      }
      throw new Error(`unexpected ${tok.v}`);
    }
    this.pos++;
    return this.identifier(tok.v, tok.sub, tok.plain);
  }

  /** The argument of a function written without brackets: the grader (SymPy's implicit application) gives it the
   * rest of the product — juxtaposed or '*'-joined factors and powers, up to '/', '+', '-' — so 'sinxcosx' is
   * sin(x·cos x) and 'sinx/2' is sin(x)/2. Shown that way, so the preview never vouches for another reading. */
  private applied(): Node {
    let node = this.power();
    for (;;) {
      const star = this.isOp("*");
      if (!star && !this.startsFactor()) return node;
      if (star) this.pos++;
      const right = this.power();
      const sep = right.startsWithDigit ? " \\cdot " : "\\,";
      node = { tex: `${node.tex}${sep}${right.tex}`, prec: 2, startsWithDigit: node.startsWithDigit };
    }
  }

  private identifier(name: string, sub?: string, plain = false): Node {
    const typed = (s: string) => s.replace(/lamda/g, "lambda"); // the grader's spelling, shown as the learner typed it
    if (sub !== undefined) {
      const base = plain ? typed(name) : name === "lamda" ? "\\lambda" : GREEK.has(name) ? `\\${name}` : name;
      return { tex: `${base}_{${typed(sub)}}`, prec: 4, startsWithDigit: false };
    }
    if (plain) return { tex: typed(name), prec: 4, startsWithDigit: false };
    const lower = name.toLowerCase();
    const fn = FUNCTIONS[lower];
    if ((fn || NAMED_FUNCTIONS.has(lower)) && (this.isOp("(") || this.startsFactor())) {
      const next = this.peek();
      const bareRoot = (lower === "sqrt" || lower === "cbrt") && next?.t === "num" && (!next.gap || next.gap === " ") && !next.glued;
      const arg = this.isOp("(") ? this.group("(", ")") : bareRoot ? this.atom() : this.applied();
      const body = arg.bare ?? arg.tex;
      return { tex: fn ? fn(body) : `\\${lower}\\left(${body}\\right)`, prec: 4, startsWithDigit: false };
    }
    if (CONSTANTS[lower]) return { tex: CONSTANTS[lower], prec: 4, startsWithDigit: false };
    if (GREEK.has(name)) return { tex: `\\${name}`, prec: 4, startsWithDigit: false };
    if (name === "lamda") return { tex: "\\lambda", prec: 4, startsWithDigit: false };
    // Multi-letter names stay as typed (SymPy reads "xy" as x*y, which renders identically).
    return { tex: name, prec: 4, startsWithDigit: false };
  }
}

/**
 * LaTeX for a learner's typed answer, or null when it can't be parsed (the UI then shows the raw text).
 * Input that already looks like LaTeX (contains a backslash) is passed through unchanged.
 */
/** The empty set in the notations the grader accepts (∅, Ø, {}, "empty", "empty set", EmptySet). */
const EMPTY_SET = /^(?:∅|Ø|\{\s*\}|empty(?:\s+set)?|emptyset)$/i;

export function asciiToLatex(input: string): string | null {
  const text = input.trim();
  if (!text) return null;
  if (EMPTY_SET.test(text)) return "\\varnothing";
  if (text.includes("\\")) return text;
  const tokens = tokenize(normalizeUnicode(text));
  if (!tokens || tokens.length === 0) return null;
  try {
    return new Parser(tokens).parse();
  } catch {
    return null;
  }
}

/** What the subscript key does to the answer box. */
export interface SubscriptEdit {
  value: string;
  caret: number;
}

/** Why the key did nothing there — each reason has its own advice in the answer box. */
export type SubscriptRefusal =
  | { why: "nothing" } // no letter and index right before the caret
  | { why: "joins"; index: string; next: string } // the index would run into the letters after it ('xky' at k)
  | { why: "reread"; earlier: string; at: string } // an earlier index would be read differently ('a_ijxj'); at: 'xj'
  | { why: "name"; name: string; called: boolean; typedAfter: boolean } // the index would split a name ('thet_a');
  // called: a function applied to something; typedAfter: the index the learner meant is already typed after the cursor
  | { why: "number"; name: string } // a digit after a function or π is a number ('pi2', 'sqrt2')
  | { why: "partial" } // a digit inside a longer number
  | { why: "backslash" } // a LaTeX command ('\thetak')
  | { why: "braces" } // inside x_{…} or x^{…}
  | { why: "other" };

type Attempt = SubscriptEdit | SubscriptRefusal;
const isEdit = (a: Attempt): a is SubscriptEdit => "value" in a;

const GLYPHS = Object.keys(GREEK_GLYPHS).join("");
const BASE = new RegExp(`[A-Za-z${GLYPHS}]`);
const INDEX_AT_END = new RegExp(`([A-Za-z${GLYPHS}])(\\d+|[A-Za-z])$`); // the base, then its index: "xk", "x12", "θk"
const SUBSCRIPTED_AT_END = new RegExp(`([A-Za-z${GLYPHS}])_(?:\\{(\\d+|[A-Za-z])\\}|(\\d+|[A-Za-z]))$`); // "x_k", "x_{k}"
const NAME_CHAR = new RegExp(`[A-Za-z0-9_ℓ${SUBSCRIPT_DIGITS}${Object.keys(SUBSCRIPT_LETTERS).join("")}${GLYPHS}]`);

/**
 * The subscript key: the index typed just before the caret (one letter, or a run of digits) becomes the subscript of
 * the name before it — "axk" -> "ax_k", "2xk" -> "2x_k", "x12" -> "x_12", "lambdak" -> "lambda_k", "θk" -> "θ_k" — and
 * pressing it again on "x_k" undoes it. A selected index ("k" in "xk") is subscripted the same way. The key acts only
 * when the result reads, the grader's way, with exactly that index on that name and nothing else re-read; otherwise
 * subscriptRefusal says why ("axk" with the caret after x would leave 'a_xk', the name a with the index xk).
 */
export function toggleSubscript(value: string, start: number, end: number): SubscriptEdit | null {
  const a = attempt(value, start, end);
  return isEdit(a) ? a : null;
}

export function subscriptRefusal(value: string, start: number, end: number): SubscriptRefusal {
  const a = attempt(value, start, end);
  return isEdit(a) ? { why: "nothing" } : a;
}

function attempt(value: string, start: number, end: number): Attempt {
  if (start !== end) {
    const index = value.slice(start, end);
    if (!/^(?:\d+|[A-Za-z])$/.test(index)) return { why: "nothing" };
    if (value[start - 1] === "_") return undo(value, end) ?? { why: "nothing" }; // a selected index: undo
    if (/\d/.test(index) && /\d/.test((value[start - 1] ?? "") + (value[end] ?? ""))) return { why: "partial" };
    return withIndex(value, start, end);
  }
  const undone = undo(value, start);
  if (undone) return undone;
  const typed = INDEX_AT_END.exec(value.slice(0, start));
  if (!typed) return { why: "nothing" };
  if (/\d/.test(typed[2]) && /\d/.test(value[start] ?? "")) return { why: "partial" }; // 'x1|2'
  return withIndex(value, start - typed[2].length, start);
}

/** Undo at the caret: "x_k" -> "xk", only for an index the key itself would make (pressing again restores the text;
 * 'a_ak' is the name a with the index ak). null when no index ends at the caret. */
function undo(value: string, caret: number): Attempt | null {
  const done = SUBSCRIPTED_AT_END.exec(value.slice(0, caret));
  if (!done) return null;
  const index = done[2] ?? done[3];
  const from = caret - done[0].length + 1; // just after the base
  const undone = { value: value.slice(0, from) + index + value.slice(caret), caret: from + index.length };
  const braces = (s: string) => s.replace(/_\{(\d+|[A-Za-z])\}/g, "_$1"); // x_{k} and x_k are one name
  const redo = attempt(undone.value, undone.caret, undone.caret);
  return isEdit(redo) && braces(redo.value) === braces(value) ? undone : { why: "other" };
}

/** Tokens of a stretch of an answer (several words when glyphs or spaces separate them), or null. */
function wordsTokens(raw: string): Token[] | null {
  const words = normalizeUnicode(raw).trim().split(/[\s*]+/).filter(Boolean);
  const out: Token[] = [];
  for (const word of words) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(word)) return null;
    const tokens = wordTokens(word);
    if (!tokens) return null;
    out.push(...tokens);
  }
  return out;
}

/** The name the index at value[end - 1] belongs to, when subscripting it would split that name: 'theta' -> 'thet_a',
 * 'sin(x)', 're(z)' (called), 'ell', or a cursor inside a name ('thet|ak'). A two-letter name the index ends is
 * split as typed ('xi' -> x_i, 'pi' -> p_i: ξ or π with an index is typed with the index after it, 'xik'). */
function splitName(value: string, end: number): { name: string; called: boolean; typedAfter: boolean } | null {
  if (!/[A-Za-z]/.test(value[end - 1] ?? "")) return null;
  let from = end - 1;
  while (from > 0 && /[A-Za-z]/.test(value[from - 1])) from--;
  let to = end;
  while (to < value.length && /[A-Za-z]/.test(value[to])) to++;
  const run = value.slice(from, to);
  if (run === "ell" && end === to) return { name: "ell", called: false, typedAfter: /^[0-9]/.test(value.slice(end)) }; // ℓ spelled out
  const after = value.slice(to);
  // applied, the grader's way: '(' follows, or an argument after a space, a bar, a root or a Greek glyph ('ln x',
  // 'Im |z|', 'Imθ'); a name that is a function only when called (re, im, arg, bar) also takes a glued argument
  const argument = new RegExp(`^(?:\\s*\\(|\\s+[A-Za-z0-9_.|√ℓ${GLYPHS}]|[|√ℓ${GLYPHS}])`);
  const called = argument.test(after) || (PAREN_ONLY.has(run.toLowerCase()) && /^\s*[A-Za-z0-9(_.]/.test(after));
  let at = from;
  const pieces = called && PAREN_ONLY.has(run.toLowerCase()) ? [run] : peel(run.replace(/lambda/g, "lamda"));
  for (const piece of pieces) {
    const typed = piece === "lamda" && run.startsWith("lambda", at - from) ? "lambda" : piece;
    const pieceEnd = at + typed.length;
    if (end - 1 >= at && end - 1 < pieceEnd) {
      if (typed.length === 1) return null;
      const inside = end < pieceEnd;
      const known = KNOWN.has(piece) || KNOWN.has(piece.toLowerCase());
      const applied = known && called && pieceEnd === to;
      if (inside || typed.length > 2 || applied) {
        const typedAfter = pieceEnd < to || /^[0-9]/.test(value.slice(pieceEnd)); // 'theta|k', 'lambda|1' — not 'thet|a'
        return { name: piece === "lamda" ? "lambda" : typed, called: applied, typedAfter };
      }
      return null; // 'xi', 'pi', 'ln', 'mu': the last letter is the index
    }
    at = pieceEnd;
  }
  return null;
}

const subs = (tokens: Token[]) => JSON.stringify(tokens.flatMap((tok) => (tok.t === "id" && tok.sub ? [[tok.v, tok.sub]] : [])));

/** "_" before value[start:end], when the name before it can carry exactly that index (see toggleSubscript). */
function withIndex(value: string, start: number, end: number): Attempt {
  if (!BASE.test(value[start - 1] ?? "")) return { why: "nothing" };
  let from = start;
  while (from > 0 && NAME_CHAR.test(value[from - 1])) from--;
  if (value[from - 1] === "\\") return { why: "backslash" };
  if (/_\{[^{}]*$/.test(value.slice(0, start))) return { why: "braces" }; // inside an index typed as _{…}
  const named = splitName(value, end);
  if (named) return { why: "name", ...named };
  from += /^(?:\d+\.?\d*|\.\d+)?/.exec(value.slice(from, start - 1))![0].length; // a coefficient: '2xk'
  let to = end;
  while (to < value.length && NAME_CHAR.test(value[to])) to++;
  const index = value.slice(start, end);
  const next = value.slice(0, start) + "_" + value.slice(start);
  // the word up to the new index reads with exactly that index on its last name …
  const upTo = wordsTokens(next.slice(from, end + 1));
  const last = upTo?.at(-1);
  if (!upTo || !last) return { why: "other" };
  if (last.t === "num") {
    const name = upTo.at(-2);
    return { why: "number", name: name?.t === "id" ? (name.v === "lamda" ? "lambda" : name.v) : "" };
  }
  if (last.t !== "id" || last.sub !== index || last.plain) return { why: "other" }; // ('xpi_k' is one name there)
  // … what follows does not change that reading …
  const whole = wordsTokens(next.slice(from, to + 1));
  if (!whole || JSON.stringify(whole.slice(0, upTo.length)) !== JSON.stringify(upTo)) {
    return { why: "joins", index, next: value.slice(end, to) };
  }
  // … and the indices before it keep theirs (the base as typed: 'lambda' for lamda, a glyph, or one letter)
  const raw = value.slice(from, start);
  const typedBase = raw.endsWith(last.v) ? last.v.length : last.v === "lamda" && raw.endsWith("lambda") ? 6 : 1;
  const earlier = value.slice(from, start - typedBase);
  if (subs(wordsTokens(earlier) ?? []) !== subs(upTo.slice(0, -1))) {
    return { why: "reread", earlier, at: value.slice(start - typedBase, end) };
  }
  if (asciiToLatex(next) === null && asciiToLatex(value) !== null) return { why: "other" }; // (mid-typing "(a xk" is fine)
  return { value: next, caret: end + 1 };
}
