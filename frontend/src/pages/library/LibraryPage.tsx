import { useState } from "react";
import { listBooks, listCourses } from "../../api";
import { ApiKeysDialog } from "../../components/ApiKeysDialog";
import { Icon } from "../../components/Icon";
import { ErrorState } from "../../components/StateMessage";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { useHealth } from "../../hooks/useHealth";
import { useResource } from "../../hooks/useResource";
import { BookList } from "./BookList";
import { CourseCard } from "./CourseCard";
import { ExampleCourses } from "./ExampleCourses";
import { ImportPanel } from "./ImportPanel";
import "./LibraryPage.css";

const BUILDING_POLL_MS = 4000;

export function LibraryPage() {
  useDocumentTitle("Library");
  const health = useHealth();
  // Poll only while some course is still building, so its card's progress stays current.
  const [anyBuilding, setAnyBuilding] = useState(false);
  const courses = useResource(
    "courses",
    async (signal) => {
      const list = await listCourses(signal);
      setAnyBuilding(list.some((c) => c.status === "building"));
      return list;
    },
    { pollMs: anyBuilding ? BUILDING_POLL_MS : null },
  );
  const books = useResource("books", listBooks);

  const unreachable = Boolean(courses.error && books.error && !courses.data && !books.data);
  // Until the lists arrive, assume the common case (a returning learner) so the layout doesn't jump.
  const hasCourses = courses.data ? courses.data.length > 0 : true;
  const noBooks = books.data !== undefined && books.data.length === 0;
  const missingKeys = health.data && !health.data.llm_ready ? health.data.missing_keys : [];
  const missingKey = missingKeys.length > 0;
  const [keysOpen, setKeysOpen] = useState(false);

  if (unreachable) {
    return (
      <div className="page">
        <ErrorState
          error={courses.error}
          title="The study server isn't answering"
          onRetry={() => {
            void courses.reload();
            void books.reload();
            void health.reload();
          }}
        />
      </div>
    );
  }

  return (
    <div className="page library">
      <section className="library__intro reveal">
        <p className="kicker">Your study desk</p>
        <h1>{hasCourses ? "Pick up where you left off" : "Turn a chapter into a course"}</h1>
        <p className="library__lede">
          {hasCourses
            ? "Each course is one chapter, mapped into small skills. Continue and the app chooses the most useful next step — a new lesson or a review that's due."
            : "Import a textbook you own, choose a chapter, and Universal Academy maps it into small skills with lessons, checked practice and spaced reviews."}
        </p>
      </section>

      {missingKey && (
        <div className="callout callout--warning library__notice">
          <Icon name="warning" size={18} />
          <span>
            <strong>{missingKeys.join(" and ")} {missingKeys.length > 1 ? "are" : "is"} not set.</strong> Importing books works, but
            building a course needs {missingKeys.length > 1 ? "the keys" : "the key"}.
          </span>
          <button type="button" className="btn btn--sm library__notice-action" onClick={() => setKeysOpen(true)}>
            <Icon name="key" size={16} /> Enter API {missingKeys.length > 1 ? "keys" : "key"}
          </button>
        </div>
      )}
      <ApiKeysDialog open={keysOpen} onClose={() => setKeysOpen(false)} />

      {hasCourses && (
        <section className="library__section" aria-labelledby="courses-heading">
          <div className="section-title">
            <h2 id="courses-heading">Continue learning</h2>
          </div>
          {courses.data ? (
            <div className="course-grid">
              {courses.data.map((course) => (
                <CourseCard
                  key={course.id}
                  course={course}
                  onDeleted={() => {
                    void courses.reload();
                    void books.reload(); // the book's course count changed
                  }}
                />
              ))}
            </div>
          ) : courses.error ? (
            <ErrorState error={courses.error} onRetry={() => void courses.reload()} title="Couldn't load your courses" />
          ) : (
            <div className="course-grid">
              <div className="skeleton course-skeleton" />
              <div className="skeleton course-skeleton" />
            </div>
          )}
        </section>
      )}

      <ExampleCourses />

      <section className={`library__section library__books${noBooks ? " library__books--empty" : ""}`} aria-labelledby="books-heading">
        {!noBooks && (
          <div className="library__books-list">
            <div className="section-title">
              <h2 id="books-heading">Your books</h2>
            </div>
            {books.error && !books.data ? (
              <ErrorState error={books.error} onRetry={() => void books.reload()} title="Couldn't load your books" />
            ) : (
              <BookList books={books.data} onDeleted={() => void books.reload()} />
            )}
          </div>
        )}
        <div className="library__import">
          <div className="section-title">
            <h2 id={noBooks ? "books-heading" : undefined}>Add a textbook</h2>
          </div>
          <ImportPanel />
        </div>
      </section>
    </div>
  );
}
