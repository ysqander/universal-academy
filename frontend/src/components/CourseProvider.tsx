import { useCallback, useMemo, useState, type ReactNode } from "react";
import { CourseContext, type CourseContextValue } from "../context/course";
import { SourceViewer } from "./SourceViewer";

interface CourseProviderProps {
  courseId: string;
  pageRange: [number, number] | null;
  /** The book is closed (a quiz is open): page buttons are disabled and the viewer stays shut. The reason is shown. */
  sourceLock?: string | null;
  children: ReactNode;
}

/** Supplies course-scoped services (figure URLs, source viewer) to everything inside a course screen. */
export function CourseProvider({ courseId, pageRange, sourceLock = null, children }: CourseProviderProps) {
  const [sourcePage, setSourcePage] = useState<number | null>(null);
  const locked = sourceLock !== null;
  const openSource = useCallback((page: number) => {
    if (!locked) setSourcePage(page);
  }, [locked]);
  const rangeStart = pageRange?.[0];
  const rangeEnd = pageRange?.[1];
  const value = useMemo<CourseContextValue>(
    () => ({
      courseId,
      pageRange: rangeStart !== undefined && rangeEnd !== undefined ? [rangeStart, rangeEnd] : null,
      openSource,
      sourceLock,
    }),
    [courseId, rangeStart, rangeEnd, openSource, sourceLock],
  );

  return (
    <CourseContext.Provider value={value}>
      {children}
      <SourceViewer
        courseId={courseId}
        // A viewer opened before the quiz started closes with it.
        page={locked ? null : sourcePage}
        pageRange={value.pageRange}
        onPageChange={setSourcePage}
        onClose={() => setSourcePage(null)}
      />
    </CourseContext.Provider>
  );
}
