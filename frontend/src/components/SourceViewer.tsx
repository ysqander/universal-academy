import { useEffect, useState } from "react";
import { getSourcePage } from "../api";
import { useResource } from "../hooks/useResource";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { PageImage } from "./PageImage";
import { ErrorState, LoadingState } from "./StateMessage";
import "./SourceViewer.css";

interface SourceViewerProps {
  courseId: string;
  page: number | null;
  pageRange: [number, number] | null;
  onPageChange: (page: number) => void;
  onClose: () => void;
}

type View = "image" | "text";

/** "Open source passage": the book page image, with a toggle to the transcribed Markdown. */
export function SourceViewer({ courseId, page, pageRange, onPageChange, onClose }: SourceViewerProps) {
  const [view, setView] = useState<View>("image");
  const source = useResource(page === null ? null : `${courseId}:${page}`, (signal) => getSourcePage(courseId, page ?? 0, signal), {
    keepPrevious: true,
  });

  const canPrev = page !== null && (pageRange ? page > pageRange[0] : page > 0);
  const canNext = page !== null && (pageRange ? page < pageRange[1] : true);

  useEffect(() => {
    if (page === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "ArrowLeft" && canPrev) onPageChange(page - 1);
      if (event.key === "ArrowRight" && canNext) onPageChange(page + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [page, canPrev, canNext, onPageChange]);

  // keepPrevious keeps the old page around while the next one loads; never show it under the new page's heading.
  const data = source.data && source.data.page === page ? source.data : undefined;
  const label = data ? `p. ${data.label}` : page !== null ? `PDF page ${page + 1}` : "";

  return (
    <Dialog
      open={page !== null}
      onClose={onClose}
      size="lg"
      kicker={<span>Source passage</span>}
      title={label}
      className="source-viewer"
      footer={
        <div className="source-viewer__nav">
          <button type="button" className="btn btn--sm" disabled={!canPrev} onClick={() => page !== null && onPageChange(page - 1)}>
            <Icon name="chevronLeft" size={16} /> Previous
          </button>
          <span className="source-viewer__page muted">{page !== null ? `PDF page ${page + 1}` : ""}</span>
          <button type="button" className="btn btn--sm" disabled={!canNext} onClick={() => page !== null && onPageChange(page + 1)}>
            Next <Icon name="chevronRight" size={16} />
          </button>
        </div>
      }
    >
      <div className="segmented" role="group" aria-label="Source view">
        <button type="button" aria-pressed={view === "image"} onClick={() => setView("image")}>
          Page image
        </button>
        <button type="button" aria-pressed={view === "text"} onClick={() => setView("text")}>
          Transcription
        </button>
      </div>
      <div className="source-viewer__content">
        {source.error ? (
          <ErrorState error={source.error} onRetry={() => void source.reload()} title="Couldn't open this page" />
        ) : !data ? (
          <LoadingState label="Opening the page…" />
        ) : view === "image" ? (
          <PageImage src={data.image_url} alt={`Book page ${data.label}`} />
        ) : (
          <div className="source-viewer__text">
            {data.markdown.trim() ? <Markdown>{data.markdown}</Markdown> : <p className="muted">No transcription is available for this page.</p>}
            {data.uncertain.length > 0 && (
              <div className="callout callout--warning source-viewer__uncertain">
                <Icon name="warning" size={16} />
                <div>
                  <strong>Parts the reader wasn't sure about</strong>
                  <ul>
                    {data.uncertain.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
