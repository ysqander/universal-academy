import katex from "katex";

/** Axler-style shorthands that KaTeX doesn't define (\R and \C already exist as blackboard bold). */
export const KATEX_MACROS: Record<string, string> = {
  "\\F": "\\mathbf{F}",
};

const WRAPPERS: [string, string][] = [
  ["$$", "$$"],
  ["\\[", "\\]"],
  ["\\(", "\\)"],
  ["$", "$"],
];

/**
 * Bare TeX for KaTeX: equation-step and frame latex sometimes arrives wrapped in Markdown delimiters
 * (`$$\frac{3+2i}{1-2i} = …$$`), which KaTeX rejects ("Can't use function '$' in math mode").
 */
export function bareLatex(tex: string): string {
  let out = tex.trim();
  for (let changed = true; changed; ) {
    changed = false;
    for (const [open, close] of WRAPPERS) {
      if (out.length > open.length + close.length && out.startsWith(open) && out.endsWith(close) && !out.endsWith(`\\${close}`)) {
        out = out.slice(open.length, out.length - close.length).trim();
        changed = true;
      }
    }
  }
  // Stray unescaped dollars left in the middle ("$$a$$ = $$b$$") — but `$` is legal inside \text{…}.
  if (!out.includes("\\text") && /(?<!\\)\$/.test(out)) out = out.replace(/(?<!\\)\$+/g, " ").trim();
  return out;
}

/** Render a TeX string to HTML (never throws; bad input renders as a red error span). */
export function renderTex(tex: string, displayMode = false): string {
  return katex.renderToString(bareLatex(tex), {
    displayMode,
    throwOnError: false,
    strict: "ignore",
    macros: { ...KATEX_MACROS },
    output: "htmlAndMathml",
  });
}
