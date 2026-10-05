import katex from "katex";

/** Axler-style shorthands that KaTeX doesn't define (\R and \C already exist as blackboard bold). */
export const KATEX_MACROS: Record<string, string> = {
  "\\F": "\\mathbf{F}",
};

/** Render a TeX string to HTML (never throws; bad input renders as a red error span). */
export function renderTex(tex: string, displayMode = false): string {
  return katex.renderToString(tex, {
    displayMode,
    throwOnError: false,
    strict: "ignore",
    macros: { ...KATEX_MACROS },
    output: "htmlAndMathml",
  });
}
