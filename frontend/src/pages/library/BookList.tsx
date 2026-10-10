import { useState } from "react";
import { Link } from "react-router-dom";
import { deleteBook } from "../../api";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { plural } from "../../lib/format";
import type { BookSummary } from "../../types";

/** Imported books; `undefined` while loading. A book can be deleted once no course is built from it. */
export function BookList({ books, onDeleted }: { books: BookSummary[] | undefined; onDeleted?: () => void }) {
  const toast = useToast();
  const [confirming, setConfirming] = useState<BookSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  if (!books) {
    return (
      <div className="book-list" aria-busy="true">
        <div className="skeleton book-skeleton" />
        <div className="skeleton book-skeleton" />
      </div>
    );
  }
  return (
    <>
      <ul className="book-list">
        {books.map((book) => (
          <li key={book.id} className="book-row">
            <span className="book-row__spine" aria-hidden="true" />
            <div className="book-row__text">
              <span className="book-row__title">{book.title}</span>
              <span className="book-row__meta">
                {[book.author, plural(book.page_count, "page"), book.course_count ? plural(book.course_count, "course") : null]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </div>
            <Link to={`/books/${encodeURIComponent(book.id)}`} className="btn btn--sm">
              <Icon name="plus" size={14} /> New course
            </Link>
            <button
              type="button"
              className="btn btn--ghost btn--sm btn--danger"
              onClick={() => setConfirming(book)}
              disabled={book.course_count > 0}
              aria-label={`Delete the book “${book.title}”`}
              title={book.course_count > 0 ? `Delete its ${plural(book.course_count, "course")} first` : "Delete this book"}
            >
              <Icon name="trash" size={14} />
            </button>
          </li>
        ))}
      </ul>
      <Dialog
        open={confirming !== null}
        onClose={() => setConfirming(null)}
        title="Delete this book?"
        size="sm"
        footer={
          <>
            <button type="button" className="btn btn--ghost" onClick={() => setConfirming(null)}>
              Keep it
            </button>
            <button
              type="button"
              className="btn btn--danger-solid"
              disabled={deleting}
              onClick={async () => {
                if (!confirming) return;
                setDeleting(true);
                try {
                  await deleteBook(confirming.id);
                  toast.show("Book deleted.", "success");
                  setConfirming(null);
                  onDeleted?.();
                } catch (err) {
                  toast.error(err);
                } finally {
                  setDeleting(false);
                }
              }}
            >
              {deleting && <Spinner size={14} label="Deleting" />} Delete book
            </button>
          </>
        }
      >
        <p>“{confirming?.title}”, its PDF in the books folder and its rendered pages will be deleted. This can't be undone.</p>
      </Dialog>
    </>
  );
}
