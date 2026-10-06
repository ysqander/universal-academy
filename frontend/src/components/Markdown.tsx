import { memo, useMemo, useState, type ComponentProps } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { figureUrl } from "../api";
import { useCourseContext, useFigureCatalog } from "../context/course";
import { FIGURE_ALT_PREFIX, expandFigurePlaceholders, localFigureId, normalizeMath } from "../lib/markdown";
import { KATEX_MACROS } from "../lib/katex";
import { Icon } from "./Icon";
import "./Markdown.css";

const REMARK_PLUGINS: Options["remarkPlugins"] = [remarkGfm, remarkMath];
const REHYPE_PLUGINS: Options["rehypePlugins"] = [[rehypeKatex, { strict: "ignore", macros: KATEX_MACROS }]];

interface MarkdownProps {
  children: string | null | undefined;
  /** Render without block wrappers (titles, choices, table cells). */
  inline?: boolean;
  className?: string;
}

function BookFigureImage({ figureId, src }: { figureId: string; src: string }) {
  const figure = useFigureCatalog()[localFigureId(figureId)];
  const course = useCourseContext();
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="md-figure md-figure--missing">
        <Icon name="warning" size={16} /> Figure {figureId} is unavailable.
      </span>
    );
  }
  return (
    <span className="md-figure">
      <img src={src} alt={figure?.description || figure?.caption || "Figure from the book"} loading="lazy" onError={() => setFailed(true)} />
      {figure && (
        <span className="md-figure__caption">
          {figure.caption && <Markdown inline>{figure.caption}</Markdown>}
          {course && course.sourceLock === null && (
            <button type="button" className="md-figure__source" onClick={() => course.openSource(figure.page)}>
              p. {figure.page_label}
            </button>
          )}
        </span>
      )}
    </span>
  );
}

const BLOCK_COMPONENTS: Components = {
  img: ({ src, alt }) => {
    const url = typeof src === "string" ? src : "";
    if (alt?.startsWith(FIGURE_ALT_PREFIX)) return <BookFigureImage figureId={alt.slice(FIGURE_ALT_PREFIX.length)} src={url} />;
    return <img src={url} alt={alt ?? ""} loading="lazy" />;
  },
  table: ({ node: _node, ...props }) => (
    <div className="md-table-wrap">
      <table {...props} />
    </div>
  ),
  a: ({ node: _node, href, ...props }) => {
    const external = typeof href === "string" && /^https?:\/\//.test(href);
    return <a href={href} {...props} {...(external ? { target: "_blank", rel: "noreferrer" } : {})} />;
  },
};

const INLINE_COMPONENTS: Components = {
  ...BLOCK_COMPONENTS,
  p: ({ node: _node, ...props }: ComponentProps<"p"> & { node?: unknown }) => <span {...props} />,
};

/** The one renderer for generated text: Markdown + GFM tables + KaTeX math + book-figure placeholders. */
export const Markdown = memo(function Markdown({ children, inline = false, className }: MarkdownProps) {
  const course = useCourseContext();
  const courseId = course?.courseId ?? null;
  const source = useMemo(() => {
    const text = normalizeMath(children ?? "");
    return expandFigurePlaceholders(text, courseId ? (id) => figureUrl(courseId, id) : null);
  }, [children, courseId]);

  if (!source.trim()) return null;
  const Wrapper = inline ? "span" : "div";
  return (
    <Wrapper className={`md${inline ? " md--inline" : ""}${className ? ` ${className}` : ""}`}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={inline ? INLINE_COMPONENTS : BLOCK_COMPONENTS}
      >
        {source}
      </ReactMarkdown>
    </Wrapper>
  );
});
