import { useCallback, useMemo, useState, type ReactNode } from "react";
import { CourseContext, type CourseContextValue } from "../context/course";
import { SourceViewer } from "./SourceViewer";

interface CourseProviderProps {
  courseId: string;
  pageRange: [number, number] | null;
  children: ReactNode;
}

/** Supplies course-scoped services (figure URLs, source viewer) to everything inside a course screen. */
export function CourseProvider({ courseId, pageRange, children }: CourseProviderProps) {
  const [sourcePage, setSourcePage] = useState<number | null>(null);
  const openSource = useCallback((page: number) => setSourcePage(page), []);
  const rangeStart = pageRange?.[0];
  const rangeEnd = pageRange?.[1];
  const value = useMemo<CourseContextValue>(
    () => ({
      courseId,
      pageRange: rangeStart !== undefined && rangeEnd !== undefined ? [rangeStart, rangeEnd] : null,
      openSource,
    }),
    [courseId, rangeStart, rangeEnd, openSource],
  );

  return (
    <CourseContext.Provider value={value}>
      {children}
      <SourceViewer
        courseId={courseId}
        page={sourcePage}
        pageRange={value.pageRange}
        onPageChange={setSourcePage}
        onClose={() => setSourcePage(null)}
      />
    </CourseContext.Provider>
  );
}
