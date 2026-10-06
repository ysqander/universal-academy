import { createContext, useContext } from "react";
import type { Figure } from "../types";

export interface CourseContextValue {
  courseId: string;
  /** Inclusive 0-based PDF page range of the course (for source-viewer paging). */
  pageRange: [number, number] | null;
  /** Open the source viewer (book page image + transcription) at a 0-based PDF page. Does nothing while locked. */
  openSource: (page: number) => void;
  /** Why the book is closed right now (a quiz is open — SPEC §6.7: no source view), else null. */
  sourceLock: string | null;
}

/** Course-scoped services for deeply nested content (figure URLs, "open source passage"). */
export const CourseContext = createContext<CourseContextValue | null>(null);

export const useCourseContext = (): CourseContextValue | null => useContext(CourseContext);

/** Known figure metadata (captions, pages) for `[[figure:id]]` placeholders inside the current content. */
export const FigureCatalogContext = createContext<Record<string, Figure>>({});

export const useFigureCatalog = (): Record<string, Figure> => useContext(FigureCatalogContext);
