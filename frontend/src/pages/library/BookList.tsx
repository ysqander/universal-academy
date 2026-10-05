import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { plural } from "../../lib/format";
import type { BookSummary } from "../../types";

/** Imported books; `undefined` while loading. */
export function BookList({ books }: { books: BookSummary[] | undefined }) {
  if (!books) {
    return (
      <div className="book-list" aria-busy="true">
        <div className="skeleton book-skeleton" />
        <div className="skeleton book-skeleton" />
      </div>
    );
  }
  return (
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
        </li>
      ))}
    </ul>
  );
}
