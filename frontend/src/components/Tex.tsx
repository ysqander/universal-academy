import { memo, useMemo } from "react";
import "katex/dist/katex.min.css";
import { renderTex } from "../lib/katex";

interface TexProps {
  tex: string;
  display?: boolean;
  className?: string;
}

/** A single KaTeX expression (for equation steps, previews and frame formulas). */
export const Tex = memo(function Tex({ tex, display = false, className }: TexProps) {
  const html = useMemo(() => renderTex(tex, display), [tex, display]);
  const Tag = display ? "div" : "span";
  return <Tag className={`tex${display ? " tex--display" : ""}${className ? ` ${className}` : ""}`} dangerouslySetInnerHTML={{ __html: html }} />;
});
