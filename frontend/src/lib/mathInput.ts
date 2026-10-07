// Live preview of typed answers: converts calculator-style input ("-7+22*i", "sqrt(2)/2", "(1, -2, 3)",
// "x^2 + 3x", "e^(i*pi)") into LaTeX. Purely presentational — grading happens on the server with SymPy.

type Token =
  | { t: "num"; v: string }
  | { t: "id"; v: string }
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

function normalizeUnicode(input: string): string {
  return input
    .replace(/[−–—]/g, "-")
    .replace(/[×·⋅∙]/g, "*")
    .replace(/÷/g, "/")
    .replace(/√/g, "sqrt")
    .replace(/π/g, "pi")
    .replace(/∞/g, "oo")
    .replace(/\*\*/g, "^");
}

function tokenize(src: string): Token[] | null {
  const tokens: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (/\s/.test(ch)) {
      i++;
      continue;
    }
    const num = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(src.slice(i));
    if (num) {
      tokens.push({ t: "num", v: num[0] });
      i += num[0].length;
      continue;
    }
    const id = /^[A-Za-z][A-Za-z]*/.exec(src.slice(i));
    if (id) {
      tokens.push({ t: "id", v: id[0] });
      i += id[0].length;
      continue;
    }
    if ("+-*/^(),[]|!".includes(ch)) {
      tokens.push({ t: "op", v: ch });
      i++;
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
    return this.identifier(tok.v);
  }

  private identifier(name: string): Node {
    const lower = name.toLowerCase();
    const fn = FUNCTIONS[lower];
    if ((fn || NAMED_FUNCTIONS.has(lower)) && (this.isOp("(") || this.startsFactor())) {
      const arg = this.isOp("(") ? this.group("(", ")") : this.power();
      const body = arg.bare ?? arg.tex;
      return { tex: fn ? fn(body) : `\\${lower}\\left(${body}\\right)`, prec: 4, startsWithDigit: false };
    }
    if (CONSTANTS[lower]) return { tex: CONSTANTS[lower], prec: 4, startsWithDigit: false };
    if (GREEK.has(name)) return { tex: `\\${name}`, prec: 4, startsWithDigit: false };
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
