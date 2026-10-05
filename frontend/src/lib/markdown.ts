// Text clean-up applied before Markdown + KaTeX rendering of generated content.

const DISPLAY_BRACKETS = /(?<!\\)\\\[([\s\S]+?)(?<!\\)\\\]/g;
const INLINE_PARENS = /(?<!\\)\\\(([\s\S]+?)(?<!\\)\\\)/g;
// A line holding only $$…$$ (optionally inside a blockquote or indented list item).
const LONE_DISPLAY_LINE = /^([ \t]*(?:>[ \t]?)*)\$\$(.+?)\$\$[ \t]*$/gm;
const FIGURE_PLACEHOLDER = /\[\[figure:([A-Za-z0-9_.:-]+)\]\]/g;

export const FIGURE_ALT_PREFIX = "figure:";

/**
 * - restores LaTeX commands whose backslash was eaten as a JSON escape (\f → form feed, \b → backspace),
 * - converts \( \) and \[ \] to the $ / $$ delimiters remark-math understands,
 * - turns a `$$…$$` that sits alone on a line into a real display block (inline $$ renders inline otherwise).
 */
export function normalizeMath(md: string): string {
  return md
    .replaceAll("\u000c", "\\f")
    .replaceAll("\u0008", "\\b")
    .replace(DISPLAY_BRACKETS, (_m, tex: string) => `\n$$\n${tex.trim()}\n$$\n`)
    .replace(INLINE_PARENS, (_m, tex: string) => `$${tex.trim()}$`)
    .replace(LONE_DISPLAY_LINE, (_m, prefix: string, tex: string) => `${prefix}$$\n${prefix}${tex.trim()}\n${prefix}$$`);
}

/** Replace `[[figure:<id>]]` placeholders with Markdown images whose alt text carries the figure id. */
export function expandFigurePlaceholders(md: string, urlFor: ((figureId: string) => string) | null): string {
  return md.replace(FIGURE_PLACEHOLDER, (_m, id: string) =>
    urlFor ? `![${FIGURE_ALT_PREFIX}${id}](${urlFor(id)})` : "*(figure)*",
  );
}

export const figureIdsIn = (md: string): string[] => Array.from(md.matchAll(FIGURE_PLACEHOLDER), (m) => m[1]);

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
