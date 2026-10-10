# books/

The app keeps every imported textbook here, one folder per book: `books/<book_id>/book.pdf`
(`UA_BOOKS_DIR` overrides the location; a relative value is taken from the project root).

- The database (in the data dir, `UA_DATA_DIR`) records each book by id with the path `<book_id>/book.pdf`, relative to
  this folder, so the folder can move with the project or live on a server.
- A new course from another chapter of a book reuses the PDF here: nothing is uploaded again.
- Importing a book that is already here — the same file, or the same book saved again (another download, a re-save,
  notes or stamps added: matched page by page on its words) — opens the book on file instead of adding a duplicate. If a
  book's PDF is missing or damaged, uploading it again restores it. Scans without a text layer match only byte for byte.
- Page renders are not kept here: they are rebuilt on demand in `<data dir>/page-cache/`.
- The PDFs are not in git (`.gitignore`). Back this folder up together with the data dir; on a server, deploy or
  restore both and set `UA_BOOKS_DIR` (with a custom `UA_DATA_DIR` and no `UA_BOOKS_DIR` of its own — one from `.env`
  counts only with `.env`'s data dir — books go to `<data>/books`, so a sandbox copy of the database never touches this
  folder). `backend/scripts/move_books.py` copies books from the pre-2026-10-10 location (`<data dir>/books/`).
- Delete a book from the Library (it removes its folder here), not by hand: the database would still list it.
