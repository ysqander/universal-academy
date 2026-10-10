import { useCallback, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { createCourse, errorMessage, getBook } from "../../api";
import { Icon } from "../../components/Icon";
import { Spinner } from "../../components/Spinner";
import { ErrorState, LoadingState } from "../../components/StateMessage";
import { useToast } from "../../context/toast";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { DEFAULT_MAX_COURSE_PAGES, useHealth } from "../../hooks/useHealth";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useResource } from "../../hooks/useResource";
import { buildEstimate, pageRangeLabel, plural } from "../../lib/format";
import { prettyTitle } from "../../lib/markdown";
import {
  ancestorIds,
  coveredSections,
  flattenOutline,
  makePageLabeler,
  selectionFromNodes,
  type FlatNode,
  type PageLabeler,
} from "../../lib/outline";
import type { Book, OutlineNode } from "../../types";
import { OutlineTree } from "./OutlineTree";
import { ThumbnailStrip } from "./ThumbnailStrip";
import "./ChapterPickerPage.css";

/** Above this a course still builds, but one chapter usually works better. */
const LARGE_SELECTION = 40;

type Pick =
  | { mode: "none" }
  | { mode: "outline"; anchorId: string; focusId: string }
  | { mode: "manual"; from: string; to: string };

interface Range {
  start: number;
  end: number;
}

export function ChapterPickerPage() {
  const { bookId = "" } = useParams();
  const book = useResource(`book:${bookId}`, (signal) => getBook(bookId, signal));
  useDocumentTitle(book.data ? `New course · ${book.data.title}` : "New course");

  if (book.error && !book.data) {
    return (
      <div className="page">
        <ErrorState error={book.error} onRetry={() => void book.reload()} title="Couldn't open this book" />
      </div>
    );
  }
  if (!book.data) return <LoadingState label="Opening the book…" />;
  return <Picker key={book.data.id} book={book.data} />;
}

function rangeLabel(labelOf: PageLabeler, { start, end }: Range): string {
  const a = labelOf(start);
  const b = labelOf(end);
  return a && b ? pageRangeLabel(a, b) : `PDF pages ${start + 1}–${end + 1}`;
}

function parseManual(from: string, to: string, pageCount: number): Range | string {
  const a = Number(from);
  const b = Number(to);
  if (!from.trim() || !to.trim()) return "Enter both the first and the last page.";
  if (!Number.isInteger(a) || !Number.isInteger(b)) return "Page numbers must be whole numbers.";
  if (a < 1 || b > pageCount) return `Pages must be between 1 and ${pageCount}.`;
  if (a > b) return "The first page must come before the last page.";
  return { start: a - 1, end: b - 1 };
}

interface Resolved {
  range: Range | null;
  sections: OutlineNode[];
  manualError: string | null;
}

function resolvePick(pick: Pick, flat: FlatNode[], pageCount: number): Resolved {
  if (pick.mode === "outline") {
    const selection = selectionFromNodes(flat, pick.anchorId, pick.focusId);
    return selection
      ? { range: { start: selection.start, end: selection.end }, sections: selection.sections, manualError: null }
      : { range: null, sections: [], manualError: null };
  }
  if (pick.mode === "manual") {
    const parsed = parseManual(pick.from, pick.to, pageCount);
    return typeof parsed === "string"
      ? { range: null, sections: [], manualError: parsed }
      : { range: parsed, sections: coveredSections(flat, parsed.start, parsed.end), manualError: null };
  }
  return { range: null, sections: [], manualError: null };
}

function Picker({ book }: { book: Book }) {
  const navigate = useNavigate();
  const toast = useToast();
  const titleId = useId();
  const fromId = useId();
  const toId = useId();
  const flat = useMemo(() => flattenOutline(book.outline), [book.outline]);
  const labelOf = useMemo(() => makePageLabeler(flat, book.page_count), [flat, book.page_count]);
  const hasOutline = book.outline_source !== "none" && flat.length > 0;

  const [pick, setPick] = useState<Pick>({ mode: "none" });
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(book.outline.length === 1 ? [book.outline[0].id] : []));
  const [title, setTitle] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Touch screens have no Shift key: "Select several" makes every tap extend the selection instead.
  const [extendMode, setExtendMode] = useState(false);
  const coarsePointer = useMediaQuery("(pointer: coarse)");
  const formRef = useRef<HTMLFormElement>(null);

  const { range, sections, manualError } = useMemo(() => resolvePick(pick, flat, book.page_count), [pick, flat, book.page_count]);
  const pageCount = range ? range.end - range.start + 1 : 0;
  // Server limit (UA_MAX_COURSE_PAGES, reported by /api/health): larger selections are rejected by POST /api/courses.
  const maxPages = useHealth().data?.max_course_pages ?? DEFAULT_MAX_COURSE_PAGES;
  const tooLarge = pageCount > maxPages;

  const defaultTitle = useMemo(() => {
    if (sections.length === 1) return prettyTitle(sections[0].title);
    if (sections.length > 1) return `${prettyTitle(sections[0].title)} – ${prettyTitle(sections[sections.length - 1].title)}`;
    return range ? `${book.title}, ${rangeLabel(labelOf, range)}` : "";
  }, [sections, range, book.title, labelOf]);
  // An emptied title falls back to the selection's default rather than blocking the build.
  const effectiveTitle = title?.trim() || defaultTitle;

  const onToggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const onSelect = useCallback(
    (id: string, extend: boolean) => {
      setSubmitError(null);
      setPick((prev) =>
        (extend || extendMode) && prev.mode === "outline" ? { ...prev, focusId: id } : { mode: "outline", anchorId: id, focusId: id },
      );
    },
    [extendMode],
  );

  const endpoints = useMemo(() => new Set(pick.mode === "outline" ? [pick.anchorId, pick.focusId] : []), [pick]);
  const visibleExpanded = useMemo(() => {
    if (pick.mode !== "outline") return expanded;
    const merged = new Set(expanded);
    for (const id of ancestorIds(flat, [pick.anchorId, pick.focusId])) merged.add(id);
    return merged;
  }, [expanded, pick, flat]);

  const manualFrom = pick.mode === "manual" ? pick.from : range ? String(range.start + 1) : "";
  const manualTo = pick.mode === "manual" ? pick.to : range ? String(range.end + 1) : "";
  const setManual = (from: string, to: string) => {
    setSubmitError(null);
    setPick({ mode: "manual", from, to });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!range || !effectiveTitle || tooLarge) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const course = await createCourse({
        book_id: book.id,
        title: effectiveTitle,
        start_page: range.start,
        end_page: range.end,
        sections: sections.map((s) => prettyTitle(s.title)),
      });
      toast.show("Course build started. The map appears as soon as the chapter is parsed.", "success");
      navigate(`/courses/${encodeURIComponent(course.id)}`);
    } catch (err) {
      setSubmitError(errorMessage(err));
      setSubmitting(false);
    }
  };

  const estimate = buildEstimate(pageCount);
  const summaryTitle =
    sections.length === 1
      ? prettyTitle(sections[0].title)
      : sections.length > 1
        ? `${prettyTitle(sections[0].title)} → ${prettyTitle(sections[sections.length - 1].title)}`
        : "Custom page range";

  return (
    <div className="page picker">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/">Library</Link>
        <Icon name="chevronRight" size={14} />
        <span aria-current="page">New course</span>
      </nav>
      <header className="picker__header">
        <p className="kicker">Choose what to study</p>
        <h1>{book.title}</h1>
        <p className="picker__meta">
          {[book.author, plural(book.page_count, "page")].filter(Boolean).join(" · ")}
        </p>
      </header>

      <div className="picker__layout">
        <section className="picker__outline card" aria-labelledby="outline-heading">
          <div className="picker__panel-head">
            <h2 id="outline-heading">Contents</h2>
            {hasOutline && (
              <div className="picker__panel-tools">
                <span className="picker__panel-hint">
                  {extendMode ? (
                    `${coarsePointer ? "Tap" : "Click"} more sections to extend the selection`
                  ) : coarsePointer ? (
                    "Tap a chapter to select it"
                  ) : (
                    <>
                      Click a chapter to select it · <kbd>Shift</kbd>-click to extend
                    </>
                  )}
                </span>
                <button
                  type="button"
                  className="btn btn--sm picker__extend"
                  aria-pressed={extendMode}
                  onClick={() => setExtendMode(!extendMode)}
                  title="When on, each section you choose is added to the selection (with everything in between)"
                >
                  <Icon name={extendMode ? "check" : "plus"} size={14} /> Select several
                </button>
              </div>
            )}
          </div>
          {book.pdf_missing && (
            <p className="callout callout--warning picker__detected" role="alert">
              <Icon name="info" size={16} />
              <span>
                This book's PDF is missing or damaged in the books folder, so its pages can't be shown or built. Upload the
                same file again in the Library to restore it.
              </span>
            </p>
          )}
          {hasOutline ? (
            <>
              {book.outline_source === "detected" && (
                <p className="callout callout--info picker__detected">
                  <Icon name="info" size={16} />
                  <span>This PDF has no bookmarks, so the contents were read from its table of contents. Check the page ranges.</span>
                </p>
              )}
              <OutlineTree
                nodes={book.outline}
                expanded={visibleExpanded}
                onToggle={onToggle}
                onSelect={onSelect}
                range={range}
                endpoints={endpoints}
              />
            </>
          ) : (
            <div className="picker__no-outline">
              <Icon name="info" size={18} />
              <p>
                {book.has_text_layer
                  ? "No table of contents could be found in this PDF."
                  : "This PDF has no text layer (it looks scanned), so its contents can't be read."}{" "}
                Enter the page range of the chapter you want to study below. Use the PDF page numbers your viewer shows.
              </p>
            </div>
          )}
        </section>

        <form ref={formRef} className="picker__selection card" onSubmit={submit} aria-labelledby="selection-heading">
          <div className="picker__panel-head">
            <h2 id="selection-heading">Your selection</h2>
          </div>

          {range ? (
            <div className="picker__summary">
              <p className="picker__summary-title">{summaryTitle}</p>
              <p className="picker__summary-meta">
                {rangeLabel(labelOf, range)} · {plural(pageCount, "page")}
              </p>
            </div>
          ) : (
            <p className="picker__summary picker__summary--empty">
              {hasOutline ? "Select a chapter or section from the contents." : "Enter a page range to begin."}
            </p>
          )}

          {range && <ThumbnailStrip bookId={book.id} start={range.start} end={range.end} labelOf={labelOf} />}

          <fieldset className="picker__range">
            <legend className="field__label">Page range (PDF page numbers)</legend>
            <div className="picker__range-row">
              <label className="field" htmlFor={fromId}>
                <span className="field__hint">From</span>
                <input
                  id={fromId}
                  className="input"
                  inputMode="numeric"
                  value={manualFrom}
                  onChange={(e) => setManual(e.target.value, manualTo)}
                  aria-invalid={manualError ? true : undefined}
                />
                <span className="field__hint picker__printed">{range && labelOf(range.start) ? `printed p. ${labelOf(range.start)}` : " "}</span>
              </label>
              <span className="picker__range-dash" aria-hidden="true">
                –
              </span>
              <label className="field" htmlFor={toId}>
                <span className="field__hint">To</span>
                <input
                  id={toId}
                  className="input"
                  inputMode="numeric"
                  value={manualTo}
                  onChange={(e) => setManual(manualFrom, e.target.value)}
                  aria-invalid={manualError ? true : undefined}
                />
                <span className="field__hint picker__printed">{range && labelOf(range.end) ? `printed p. ${labelOf(range.end)}` : " "}</span>
              </label>
            </div>
            {manualError && <p className="picker__error">{manualError}</p>}
          </fieldset>

          <div className="field">
            <label className="field__label" htmlFor={titleId}>
              Course title
            </label>
            <input
              id={titleId}
              className="input picker__title-input"
              value={title ?? defaultTitle}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={defaultTitle || "e.g. Chapter 1 · Vector Spaces"}
              maxLength={160}
            />
          </div>

          {range && tooLarge ? (
            <div className="callout callout--danger" role="alert">
              <Icon name="warning" size={18} />
              <span>
                That's {pageCount} pages — a course can have at most {maxPages}. Choose a single chapter or a few sections.
              </span>
            </div>
          ) : range && pageCount > LARGE_SELECTION ? (
            <div className="callout callout--warning">
              <Icon name="warning" size={18} />
              <span>
                That's {pageCount} pages. Courses work best for one chapter — consider a smaller range for a faster, cheaper build and a
                more focused map.
              </span>
            </div>
          ) : null}

          {range && !tooLarge && (
            <p className="picker__estimate">
              <Icon name="clock" size={16} />
              <span>
                Roughly <strong>{estimate.minutes} min</strong> and <strong>${estimate.dollars.toFixed(2)}</strong> to build
                <span className="muted"> (about 45 s and $0.10 per page; you can start studying before it finishes)</span>
              </span>
            </p>
          )}

          {submitError && (
            <p className="callout callout--danger" role="alert">
              <Icon name="warning" size={18} />
              <span>{submitError}</span>
            </p>
          )}

          <button type="submit" className="btn btn--primary btn--lg btn--block" disabled={!range || !effectiveTitle || tooLarge || submitting}>
            {submitting ? <Spinner size={16} label="Starting the build" /> : <Icon name="graph" size={18} />}
            {submitting ? "Starting…" : "Build course"}
          </button>
        </form>
      </div>

      {/* Phones / narrow tablets: the selection panel sits below the whole outline, so keep a summary in reach. */}
      {range && (
        <div className="picker__bar">
          <div className="picker__bar-text">
            <span className="picker__bar-title">{summaryTitle}</span>
            <span className="picker__bar-meta">
              {rangeLabel(labelOf, range)} · {plural(pageCount, "page")}
              {tooLarge ? ` · max ${maxPages}` : ""}
            </span>
          </div>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
          >
            Review &amp; build <Icon name="chevronDown" size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
