// Text clean-up applied before Markdown + KaTeX rendering of generated content.

const DISPLAY_BRACKETS = /(?<!\\)\\\[([\s\S]+?)(?<!\\)\\\]/g;
const INLINE_PARENS = /(?<!\\)\\\(([\s\S]+?)(?<!\\)\\\)/g;
const DOLLARS = /(?<!\\)\$\$/g;
const FIGURE_PLACEHOLDER = /\[\[figure:([A-Za-z0-9_.:-]+)\]\]/g;
// Blockquote markers / indentation in front of a line (kept on every line a display block is split into).
// Linear: each `>` iteration must consume a `>`, and `.` with the s flag lets `(.*)$` match any rest (even a stray \r).
const LINE_PREFIX = /^([ \t]*(?:>[ \t]?)*)(.*)$/s;
// Any mix of spaces, tabs and `>` before a fence. One character class, so a long indent can be split only one way
// (the old `(?:[ \t]*>?[ \t]*)*` backtracked exponentially on lines of spaces: 16 spaces froze the tab for seconds).
const CODE_FENCE = /^[ \t>]*(```|~~~)/;
const INLINE_CODE = /`[^`\n]*`/g;
const LOST_MATH = /(?<![\\$])\$\\\$(?=[\s.,;:)]|$)/gm;
const UNREADABLE = "⟨?⟩";

export const FIGURE_ALT_PREFIX = "figure:";

/** Positions of `$$` delimiters; with `skipCode`, a `$$` inside an inline code span (`` `$$` ``) is not one. */
const dollarPositions = (text: string, skipCode = false): number[] => {
  const scanned = skipCode && text.includes("`") ? text.replace(INLINE_CODE, (m) => "_".repeat(m.length)) : text;
  return Array.from(scanned.matchAll(DOLLARS), (m) => m.index ?? 0);
};

const splitPrefix = (line: string): [prefix: string, rest: string] => {
  const m = LINE_PREFIX.exec(line);
  return m ? [m[1], m[2]] : ["", line];
};

/**
 * Puts every `$$` display fence on a line of its own (keeping the blockquote/indent prefix), so remark-math sees a
 * real display block. Transcriptions often write `$$\begin{aligned}` … `\end{aligned}$$` or `> $$x = 1.$$`;
 * remark-math treats text after an opening `$$` as fence "meta" (dropping `\begin{aligned}`) and never finds the
 * closing fence, which turns the rest of the page into one red KaTeX error.
 *
 * An odd `$$` opens a display only when a later line closes it: a stray or truncated `$$` in running text ("costs
 * $$ more") stays literal text instead of swallowing every following paragraph. Runs in linear time.
 */
function splitDisplayFences(md: string): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let inMath = false;
  let inCode = false;

  // Does a display opened just before line `from` close later? A `$$` opened mid-line must close within its
  // paragraph (remark-math leaves an unclosed mid-line `$$` as text); a `$$` at the start of a line is a fence for
  // remark-math anyway, so any later `$$` outside code closes it.
  const closesLater = (from: number, withinParagraph: boolean): boolean => {
    let code = false;
    for (let j = from; j < lines.length; j++) {
      if (CODE_FENCE.test(lines[j])) {
        if (withinParagraph) return false;
        code = !code;
        continue;
      }
      if (code) continue;
      const rest = splitPrefix(lines[j])[1];
      if (withinParagraph && !rest.trim()) return false;
      if (dollarPositions(rest, true).length) return true;
    }
    return false;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!inMath && CODE_FENCE.test(line)) {
      inCode = !inCode;
      out.push(line);
      continue;
    }
    if (inCode || !line.includes("$$")) {
      out.push(line);
      continue;
    }
    const [prefix, rest] = splitPrefix(line);
    const positions = dollarPositions(rest, !inMath);
    if (positions.length === 0) {
      out.push(line);
      continue;
    }
    if (!inMath) {
      const trimmed = rest.trim();
      // `$$…$$` alone on a line → a display block (inline `$$` would otherwise render inline).
      if (positions.length === 2 && trimmed.startsWith("$$") && trimmed.endsWith("$$") && trimmed.length > 4) {
        out.push(`${prefix}$$`, `${prefix}${trimmed.slice(2, -2).trim()}`, `${prefix}$$`);
        continue;
      }
      // Balanced `$$` pairs inside running text stay inline.
      if (positions.length % 2 === 0) {
        out.push(line);
        continue;
      }
      // An odd count opens a display block on this line: split at the last `$$` — if something closes it.
      const at = positions[positions.length - 1];
      const before = rest.slice(0, at).trimEnd();
      if (!closesLater(i + 1, before.trim() !== "")) {
        out.push(line); // stray / truncated `$$`: literal text, as remark-math would read it
        continue;
      }
      const after = rest.slice(at + 2).trim();
      if (before.trim()) out.push(`${prefix}${before}`);
      out.push(`${prefix}$$`);
      if (after) out.push(`${prefix}${after}`);
      inMath = true;
      continue;
    }
    // Inside a display block: the first `$$` closes it.
    const at = positions[0];
    const before = rest.slice(0, at).trimEnd();
    const after = rest.slice(at + 2).trim();
    if (before.trim()) out.push(`${prefix}${before}`);
    out.push(`${prefix}$$`);
    inMath = false;
    // Text after the closing fence ("$$ and then $$" …) is processed as the next line, against the real lines below.
    if (after) lines.splice(i + 1, 0, `${prefix}${after}`);
  }
  return out.join("\n");
}

/**
 * - restores LaTeX commands whose backslash was eaten as a JSON escape (\f → form feed, \b → backspace),
 * - converts \( \) and \[ \] to the $ / $$ delimiters remark-math understands,
 * - puts `$$` display fences on their own lines (one-line displays, `$$\begin{aligned}` openers, blockquotes).
 */
export function normalizeMath(md: string): string {
  const text = md
    .replaceAll("\u000c", "\\f")
    .replaceAll("\u0008", "\\b")
    // Older transcriptions lost whole commands to that glitch (`$\mathbf{R}$` stored as `$\$`): show a visible marker.
    .replace(LOST_MATH, UNREADABLE)
    .replace(DISPLAY_BRACKETS, (_m, tex: string) => `\n$$\n${tex.trim()}\n$$\n`)
    .replace(INLINE_PARENS, (_m, tex: string) => `$${tex.trim()}$`);
  return text.includes("$$") ? splitDisplayFences(text) : text;
}

/** Replace `[[figure:<id>]]` placeholders with Markdown images whose alt text carries the figure id. */
export function expandFigurePlaceholders(md: string, urlFor: ((figureId: string) => string) | null): string {
  return md.replace(FIGURE_PLACEHOLDER, (_m, id: string) =>
    urlFor ? `![${FIGURE_ALT_PREFIX}${id}](${urlFor(id)})` : "*(figure)*",
  );
}

export const figureIdsIn = (md: string): string[] => Array.from(md.matchAll(FIGURE_PLACEHOLDER), (m) => localFigureId(m[1]));

/** "3ba2fbf05f21-p21-f1" (database id, older API responses) → "p21-f1" (the id used by [[figure:…]] placeholders). */
export const localFigureId = (id: string): string => /p\d+-f\d+$/.exec(id)?.[0] ?? id;

const SUPERSCRIPTS: Record<string, string> = {
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", n: "ⁿ", "+": "⁺", "-": "⁻",
};
const SUBSCRIPTS: Record<string, string> = {
  "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉", n: "ₙ", k: "ₖ", j: "ⱼ", i: "ᵢ",
};
const SYMBOLS: Record<string, string> = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", theta: "θ", lambda: "λ", mu: "μ", pi: "π",
  sigma: "σ", phi: "φ", varphi: "φ", psi: "ψ", omega: "ω", Gamma: "Γ", Delta: "Δ", Lambda: "Λ", Sigma: "Σ", Omega: "Ω",
  in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", cap: "∩", cup: "∪", oplus: "⊕", times: "×", cdot: "·", to: "→",
  rightarrow: "→", mapsto: "↦", le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠", infty: "∞", forall: "∀", exists: "∃",
  ldots: "…", dots: "…", cdots: "⋯", bar: "", overline: "", F: "F",
};

const mapChars = (text: string, table: Record<string, string>): string | null => {
  const out = [...text].map((c) => table[c]);
  return out.every(Boolean) ? out.join("") : null;
};

/** Best-effort plain text for Markdown + TeX (document titles, accessible names): "$\mathbf{R}^2$" → "R²". */
export function plainText(md: string): string {
  return md
    .replace(/\$+/g, "")
    .replace(/\\(?:mathbf|mathbb|mathrm|mathit|mathcal|text|operatorname|boldsymbol)\{([^{}]*)\}/g, "$1")
    .replace(/\^\{?([0-9n+-]+)\}?/g, (m, p: string) => mapChars(p, SUPERSCRIPTS) ?? m)
    .replace(/_\{?([0-9nkji]+)\}?/g, (m, p: string) => mapChars(p, SUBSCRIPTS) ?? m)
    .replace(/\\([A-Za-z]+)/g, (_m, name: string) => SYMBOLS[name] ?? name)
    .replace(/[{}]/g, "")
    .replace(/\*\*|__|`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Single-character exponents that have a Unicode superscript (titles only: "R^n" → "Rⁿ", "x^2" → "x²").
const TITLE_SUPERSCRIPTS: Record<string, string> = {
  ...SUPERSCRIPTS,
  a: "ᵃ", b: "ᵇ", c: "ᶜ", d: "ᵈ", e: "ᵉ", f: "ᶠ", g: "ᵍ", h: "ʰ", i: "ⁱ", j: "ʲ", k: "ᵏ", l: "ˡ", m: "ᵐ", o: "ᵒ", p: "ᵖ",
  r: "ʳ", s: "ˢ", t: "ᵗ", u: "ᵘ", v: "ᵛ", w: "ʷ", x: "ˣ", y: "ʸ", z: "ᶻ", T: "ᵀ",
};
const TITLE_EXPONENT = /\^(?:\{(\S)\}|(\w)(?!\w))/g;
const TITLE_INFINITY = /\^\s*(?:\\infty\b|\{\s*\\infty\s*\}|\{\s*∞\s*\})/g;
// $…$ / $$…$$ spans (already proper math) are left alone.
const MATH_SPAN = /(\$\$[\s\S]+?\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$)/;

/**
 * Titles from PDF bookmarks and older courses spell exponents TeX-style outside math ("R^n and C^n", "F^\infty").
 * Rewrites single-character exponents to Unicode superscripts ("Rⁿ and Cⁿ") and \infty to "∞"; text inside $…$ is kept.
 */
export function prettyTitle(title: string): string {
  if (!title.includes("^")) return title;
  return title
    .split(MATH_SPAN)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(TITLE_INFINITY, "^∞")
            .replace(TITLE_EXPONENT, (m, braced: string | undefined, bare: string | undefined) => TITLE_SUPERSCRIPTS[braced ?? bare ?? ""] ?? m),
    )
    .join("");
}
