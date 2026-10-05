import { useCourseContext } from "../context/course";
import { Icon } from "./Icon";
import "./SourceRefs.css";

interface SourceRefsProps {
  refs: string[];
  pages: number[];
  labels: string[];
  compact?: boolean;
}

/** "Source: 1.3, 1.4 · p. 4 · p. 5" — page buttons open the source viewer. */
export function SourceRefs({ refs, pages, labels, compact = false }: SourceRefsProps) {
  const course = useCourseContext();
  if (!refs.length && !pages.length) return null;
  return (
    <div className={`source-refs${compact ? " source-refs--compact" : ""}`}>
      <span className="source-refs__label">
        <Icon name="source" size={14} /> From the book
      </span>
      {refs.length > 0 && <span className="source-refs__items">{refs.join(", ")}</span>}
      {pages.map((page, i) => (
        <button
          key={page}
          type="button"
          className="source-refs__page"
          onClick={() => course?.openSource(page)}
          disabled={!course}
          aria-label={`Open source passage, page ${labels[i] ?? page + 1}`}
        >
          p. {labels[i] ?? page + 1}
        </button>
      ))}
    </div>
  );
}
