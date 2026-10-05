import { useState } from "react";
import { bookPageUrl } from "../../api";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { PageImage } from "../../components/PageImage";
import type { PageLabeler } from "../../lib/outline";

const MAX_THUMBS = 80;
const THUMB_DPI = 40;
const PREVIEW_DPI = 110;

interface ThumbnailStripProps {
  bookId: string;
  start: number;
  end: number;
  labelOf: PageLabeler;
}

const pageName = (labelOf: PageLabeler, page: number): string => {
  const label = labelOf(page);
  return label ? `p. ${label}` : `PDF ${page + 1}`;
};

/** Horizontally scrolling, lazily loaded thumbnails of the selected pages; click for a larger preview. */
export function ThumbnailStrip({ bookId, start, end, labelOf }: ThumbnailStripProps) {
  const [preview, setPreview] = useState<number | null>(null);
  const count = end - start + 1;
  const shown = Array.from({ length: Math.min(count, MAX_THUMBS) }, (_, i) => start + i);

  return (
    <>
      <div className="thumbs" aria-label="Selected pages">
        {shown.map((page) => (
          <button
            key={page}
            type="button"
            className="thumb"
            onClick={() => setPreview(page)}
            aria-label={`Preview ${pageName(labelOf, page)}`}
          >
            <span className="thumb__frame">
              <img src={bookPageUrl(bookId, page, THUMB_DPI)} alt="" loading="lazy" decoding="async" />
            </span>
            <span className="thumb__label">{pageName(labelOf, page)}</span>
          </button>
        ))}
        {count > MAX_THUMBS && <div className="thumb thumb--more">+{count - MAX_THUMBS} more pages</div>}
      </div>
      <Dialog
        open={preview !== null}
        onClose={() => setPreview(null)}
        title={preview !== null ? pageName(labelOf, preview) : ""}
        kicker={preview !== null ? `PDF page ${preview + 1}` : undefined}
        size="lg"
        footer={
          <>
            <button
              type="button"
              className="btn btn--sm"
              disabled={preview === null || preview <= start}
              onClick={() => setPreview((p) => (p === null ? p : p - 1))}
            >
              <Icon name="chevronLeft" size={16} /> Previous
            </button>
            <button
              type="button"
              className="btn btn--sm"
              disabled={preview === null || preview >= end}
              onClick={() => setPreview((p) => (p === null ? p : p + 1))}
            >
              Next <Icon name="chevronRight" size={16} />
            </button>
          </>
        }
      >
        {preview !== null && <PageImage src={bookPageUrl(bookId, preview, PREVIEW_DPI)} alt={`Page ${pageName(labelOf, preview)}`} />}
      </Dialog>
    </>
  );
}
