import { useCourseContext } from "../context/course";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import "./SourceRefs.css";

interface SourceRefsProps {
  refs: string[];
  pages: number[];
  labels: string[];
  compact?: boolean;
}

/** "From the book": cited items (1.3, Example 1.4, …) as chips, then page buttons that open the source viewer. */
export function SourceRefs({ refs, pages, labels, compact = false }: SourceRefsProps) {
  const course = useCourseContext();
  if (!refs.length && !pages.length) return null;
  return (
    <div className={`source-refs${compact ? " source-refs--compact" : ""}`}>
      <span className="source-refs__label">
        <Icon name="source" size={14} /> From the book
      </span>
      {refs.length > 0 && (
        <ul className="source-refs__items" aria-label="Cited from the book">
          {refs.map((ref, i) => (
            <li key={i} className="source-refs__ref">
              <Markdown inline>{ref}</Markdown>
            </li>
          ))}
        </ul>
      )}
      {pages.map((page, i) => (
        <button
          key={page}
          type="button"
          className="source-refs__page"
          onClick={() => course?.openSource(page)}
          // Closed while a quiz is open (SPEC §6.7): the reason is the button's title.
          disabled={!course || course.sourceLock !== null}
          title={course?.sourceLock ?? undefined}
          aria-label={`Open source passage, page ${labels[i] ?? page + 1}`}
        >
          p. {labels[i] ?? page + 1}
        </button>
      ))}
    </div>
  );
}
