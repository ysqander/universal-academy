"""Books API on a minimal FastAPI app that mounts only the books router."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.api import books
from app.config import settings


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(books.router)
    return TestClient(app)


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Import by path only accepts files under the home folder: make tmp_path the home folder."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def small_pdf(path: Path, pages: int = 3, title: str = "", toc: list[list] | None = None) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=300, height=400).insert_text((40, 60), f"Page {i + 1} " * 40, fontsize=8)
    if title:
        doc.set_metadata({"title": title, "author": "A. Author"})
    if toc:
        doc.set_toc(toc)
    doc.save(path)
    doc.close()
    return path


def test_import_path_axler(client: TestClient, axler_pdf: Path) -> None:
    resp = client.post("/api/books/import-path", json={"path": str(axler_pdf)})
    assert resp.status_code == 200, resp.text
    book = resp.json()
    assert book["title"] == "Linear Algebra Done Right" and book["author"] == "Sheldon Axler"
    assert book["page_count"] == 404 and book["outline_source"] == "pdf" and book["has_text_layer"]
    assert book["course_count"] == 0
    stored = settings.books_dir / book["id"] / "book.pdf"
    assert stored.exists() and stored.stat().st_size == axler_pdf.stat().st_size
    assert book["already_in_library"] is None
    assert db.query_one("SELECT pdf_path FROM books WHERE id = ?", (book["id"],))["pdf_path"] == f"{book['id']}/book.pdf"
    chapter = next(n for n in book["outline"] if n["title"] == "Vector Spaces")
    assert (chapter["start_page"], chapter["end_page"]) == (14, 39)
    assert chapter["children"][0]["label_start"] == "2"

    again = client.post("/api/books/import-path", json={"path": str(axler_pdf)})
    assert again.status_code == 200 and again.json()["id"] == book["id"]  # same sha256 → same book
    assert again.json()["already_in_library"] == "identical_file"
    assert len(client.get("/api/books").json()) == 1

    labels = db.loads(db.query_one("SELECT page_labels_json FROM books WHERE id = ?", (book["id"],))["page_labels_json"])
    assert labels[14] == "1"


def test_upload_list_get_delete(client: TestClient, tmp_path: Path) -> None:
    first = small_pdf(tmp_path / "first.pdf", title="First Book", toc=[[1, "One", 1], [1, "Two", 3]])
    second = small_pdf(tmp_path / "second_book.pdf", pages=5)
    with first.open("rb") as fh:
        r1 = client.post("/api/books", files={"file": ("first.pdf", fh, "application/pdf")})
    with second.open("rb") as fh:
        r2 = client.post("/api/books", files={"file": ("second_book.pdf", fh, "application/pdf")})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    b1, b2 = r1.json(), r2.json()
    assert b1["title"] == "First Book" and b1["author"] == "A. Author"
    assert [(n["title"], n["start_page"], n["end_page"]) for n in b1["outline"]] == [("One", 0, 1), ("Two", 2, 2)]
    assert b2["title"] == "Second Book" and b2["outline"] == [] and b2["outline_source"] == "none"
    assert not list(settings.upload_tmp_dir.glob(".upload-*"))  # temp upload removed once copied into place
    assert not list(settings.books_dir.glob("*/.book.pdf.part"))
    assert b1["already_in_library"] is None

    listed = client.get("/api/books").json()
    assert [b["id"] for b in listed] == [b2["id"], b1["id"]]  # newest first
    assert set(listed[0]) == {"id", "title", "author", "page_count", "created_at", "course_count"}

    got = client.get(f"/api/books/{b1['id']}")
    assert got.status_code == 200 and got.json() == b1

    assert client.delete(f"/api/books/{b1['id']}").status_code == 204
    assert client.get(f"/api/books/{b1['id']}").status_code == 404
    assert not (settings.books_dir / b1["id"]).exists()
    assert not (settings.page_cache_dir / b1["id"]).exists()
    with first.open("rb") as fh:  # re-importing after delete creates a fresh book
        r3 = client.post("/api/books", files={"file": ("first.pdf", fh, "application/pdf")})
    assert r3.status_code == 200 and r3.json()["id"] != b1["id"]


def test_delete_with_courses_conflicts(client: TestClient, tmp_path: Path, tmp_home: Path) -> None:
    book = client.post("/api/books/import-path", json={"path": str(small_pdf(tmp_path / "c.pdf"))}).json()
    db.execute(
        "INSERT INTO courses(id, book_id, title, start_page, end_page, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("course1", book["id"], "Course", 0, 1, db.now_iso()),
    )
    assert client.get("/api/books").json()[0]["course_count"] == 1
    resp = client.delete(f"/api/books/{book['id']}")
    assert resp.status_code == 409 and "course" in resp.json()["detail"]


def test_page_thumbnails(client: TestClient, tmp_path: Path, tmp_home: Path) -> None:
    book = client.post("/api/books/import-path", json={"path": str(small_pdf(tmp_path / "t.pdf"))}).json()
    resp = client.get(f"/api/books/{book['id']}/pages/1.png")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    assert "max-age" in resp.headers["cache-control"]
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (settings.page_cache_dir / book["id"] / "1@60.png").exists()  # renders stay in the data dir

    assert client.get(f"/api/books/{book['id']}/pages/0.png?dpi=5").status_code == 200
    assert (settings.page_cache_dir / book["id"] / "0@30.png").exists()  # dpi clamped to 30..200
    assert client.get(f"/api/books/{book['id']}/pages/0.png?dpi=900").status_code == 200
    assert (settings.page_cache_dir / book["id"] / "0@200.png").exists()
    assert [p.name for p in (settings.books_dir / book["id"]).iterdir()] == ["book.pdf"]  # the books folder holds the PDF only

    assert client.get(f"/api/books/{book['id']}/pages/3.png").status_code == 404
    assert client.get(f"/api/books/{book['id']}/pages/-1.png").status_code == 404
    assert client.get("/api/books/nope/pages/0.png").status_code == 404


def test_invalid_inputs(client: TestClient, tmp_path: Path, tmp_home: Path) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4 definitely not really a pdf")
    with fake.open("rb") as fh:
        resp = client.post("/api/books", files={"file": ("fake.pdf", fh, "application/pdf")})
    assert resp.status_code == 400 and "fake.pdf" in resp.json()["detail"]

    resp = client.post("/api/books", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 400

    resp = client.post("/api/books", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert resp.status_code == 400 and "empty" in resp.json()["detail"]

    assert client.post("/api/books/import-path", json={"path": str(tmp_path / "missing.pdf")}).status_code == 404
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    assert client.post("/api/books/import-path", json={"path": str(txt)}).status_code == 400
    with fake.open("rb") as fh:
        assert client.post("/api/books/import-path", json={"path": str(fake)}).status_code == 400
    assert client.get("/api/books").json() == []
    assert not settings.books_dir.exists() or not list(settings.books_dir.iterdir())  # nothing left behind


def test_import_path_expands_user(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    small_pdf(tmp_path / "home.pdf")
    resp = client.post("/api/books/import-path", json={"path": "~/home.pdf"})
    assert resp.status_code == 200 and resp.json()["title"] == "Home"


# ---------------------------------------------------------------------------
# import-path is local-only (D14), stored files are found by id, legacy outlines are rebuilt once
# ---------------------------------------------------------------------------


def test_import_path_rejects_remote_clients(tmp_path: Path, tmp_home: Path) -> None:
    app = FastAPI()
    app.include_router(books.router)
    path = str(small_pdf(tmp_path / "remote.pdf"))
    remote = TestClient(app, client=("192.168.1.23", 50000))
    resp = remote.post("/api/books/import-path", json={"path": path})
    assert resp.status_code == 403 and "Upload the file instead" in resp.json()["detail"]
    local = TestClient(app, client=("127.0.0.1", 50000))
    forwarded = local.post("/api/books/import-path", json={"path": path}, headers={"X-Forwarded-For": "10.0.0.7"})
    assert forwarded.status_code == 403  # a local proxy relaying a LAN request
    assert TestClient(app, client=("::1", 50000)).post("/api/books/import-path", json={"path": path}).status_code == 200


def test_import_path_only_under_home(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    monkeypatch.setenv("HOME", str(home))
    secret = small_pdf(outside / "taxes.pdf")
    resp = client.post("/api/books/import-path", json={"path": str(secret)})
    assert resp.status_code == 403 and "Upload the file instead" in resp.json()["detail"]
    missing = client.post("/api/books/import-path", json={"path": str(outside / "nope.pdf")})
    assert missing.status_code == 403  # existence outside home is not revealed
    (home / "link.pdf").symlink_to(secret)
    assert client.post("/api/books/import-path", json={"path": str(home / "link.pdf")}).status_code == 403
    assert client.post("/api/books/import-path", json={"path": "~nosuchuser123/x.pdf"}).status_code == 400
    assert client.get("/api/books").json() == []


def test_moved_data_dir_still_serves_pages(client: TestClient, tmp_path: Path, tmp_home: Path) -> None:
    book = client.post("/api/books/import-path", json={"path": str(small_pdf(tmp_path / "m.pdf"))}).json()
    db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", ("/old/location/book.pdf", book["id"]))
    assert client.get(f"/api/books/{book['id']}/pages/0.png").status_code == 200
    assert client.delete(f"/api/books/{book['id']}").status_code == 204


# ---------------------------------------------------------------------------
# The books folder (UA_BOOKS_DIR): portable paths, older locations, the same book never added twice
# ---------------------------------------------------------------------------


WORDS = ["vector", "space", "field", "scalar", "basis", "span", "linear", "map", "kernel", "range", "dual", "norm"]


def text_pdf(path: Path, pages: int = 3, title: str = "", seed: str = "vector", stamp: str = "",
             note_on: int | None = None, toc: list[list] | None = None, note_over_text: bool = False,
             labels: list[dict] | None = None) -> Path:
    """A PDF with a real text layer: different words on every page (a few hundred characters each), an optional
    footer stamp on every page and an optional FreeText note."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        words = " ".join(WORDS[(i * 5 + k) % len(WORDS)] for k in range(40))
        page.insert_textbox(pymupdf.Rect(20, 20, 280, 340), f"Page {i + 1}. {seed} {words}", fontsize=8)
        if stamp:
            page.insert_text((20, 380), stamp, fontsize=6)
        if note_on == i:
            rect = pymupdf.Rect(40, 60, 220, 90) if note_over_text else pymupdf.Rect(150, 345, 290, 375)
            page.add_freetext_annot(rect, "My note: compare with the proof of 2.17")
    if title:
        doc.set_metadata({"title": title, "author": "A. Author"})
    if toc:
        doc.set_toc(toc)
    if labels:
        doc.set_page_labels(labels)
    doc.save(path)
    doc.close()
    return path


def upload(client: TestClient, path: Path) -> dict:
    with path.open("rb") as fh:
        resp = client.post("/api/books", files={"file": (path.name, fh, "application/pdf")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_same_book_saved_again_is_recognised(client: TestClient, tmp_path: Path) -> None:
    first = upload(client, text_pdf(tmp_path / "a.pdf", title="Algebra"))
    resaved = text_pdf(tmp_path / "a-again.pdf", title="Algebra (downloaded again)")  # other bytes, same text
    assert resaved.read_bytes() != (tmp_path / "a.pdf").read_bytes()
    second = upload(client, resaved)
    assert second["id"] == first["id"] and second["already_in_library"] == "same_text"
    assert upload(client, resaved)["already_in_library"] == "identical_file"  # this file is now known by its hash
    assert len(client.get("/api/books").json()) == 1
    assert [p.name for p in settings.books_dir.iterdir()] == [first["id"]]  # no second copy on disk

    other = upload(client, text_pdf(tmp_path / "b.pdf", title="Algebra", seed="matrix"))  # same title, other text
    assert other["id"] != first["id"] and other["already_in_library"] is None
    scan = small_pdf(tmp_path / "scan.pdf")  # too little text to fingerprint: only the exact file is recognised
    assert upload(client, scan)["already_in_library"] is None
    assert upload(client, small_pdf(tmp_path / "scan-again.pdf", title="Scan"))["already_in_library"] is None


def test_books_imported_before_fingerprints_are_recognised(client: TestClient, tmp_path: Path) -> None:
    first = upload(client, text_pdf(tmp_path / "old.pdf", title="Old"))
    db.execute("DELETE FROM kv WHERE key LIKE 'book_pages:%'")  # as imported by an older version
    second = upload(client, text_pdf(tmp_path / "old-copy.pdf", title="Old, saved again"))
    assert second["id"] == first["id"] and second["already_in_library"] == "same_text"


def test_a_copy_with_download_stamps_or_notes_is_the_same_book(client: TestClient, tmp_path: Path) -> None:
    first = upload(client, text_pdf(tmp_path / "d1.pdf", pages=6, stamp="Downloaded by alice@uni.edu on 2026-10-01 10:02"))
    stamped = text_pdf(tmp_path / "d2.pdf", pages=6, stamp="Downloaded by alice@uni.edu on 2026-10-09 18:45")
    assert upload(client, stamped)["id"] == first["id"]
    noted = text_pdf(tmp_path / "d3.pdf", pages=6, note_on=2)  # a text box added in a viewer
    assert upload(client, noted)["already_in_library"] == "same_text"
    for page in range(6):  # a note over the page's own words on every page: only the note's words are left out
        over = text_pdf(tmp_path / f"d4-{page}.pdf", pages=6, note_on=page, note_over_text=True)
        assert upload(client, over)["id"] == first["id"]
    assert len(client.get("/api/books").json()) == 1


def test_letters_survive_a_re_encoded_font() -> None:
    from app.ingest import pdf

    # macOS PDFKit re-saves drop math fonts' Unicode maps: 𝑛 comes out as ֙ and 2 as ӝ; the words stay
    assert pdf.page_letters("Suppose 𝑛 ∈ 𝐍 and 2 ≤ 𝑛; ﬁnd the basis.") == pdf.page_letters("Suppose ֙ ∈ ֓ and ӝ ≤ ֙; find the basis.")
    assert pdf.same_book(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"], ["a", "b", "c", "d", "e", "f", "g", "h", "i", "x"])
    assert not pdf.same_book(["a", "b", "c", "d", "e"], ["a", "b", "c", "x", "y"])  # 60 % of the pages: another book
    assert not pdf.same_book(["a", "b", "c"], ["a", "b", "c", ""])  # another page count


def test_a_book_on_file_without_bookmarks_takes_them_from_a_bookmarked_copy(client: TestClient, tmp_path: Path) -> None:
    plain = upload(client, text_pdf(tmp_path / "plain.pdf", pages=4))
    assert plain["outline_source"] != "pdf"
    labels = [{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
              {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1}]
    marked = upload(client, text_pdf(tmp_path / "marked.pdf", pages=4, toc=[[1, "Ch 1", 3], [1, "Ch 2", 4]], labels=labels))
    assert marked["id"] == plain["id"] and marked["already_in_library"] == "same_text"
    assert marked["outline_source"] == "pdf" and [n["title"] for n in marked["outline"]] == ["Ch 1", "Ch 2"]
    stored = db.loads(db.query_one("SELECT page_labels_json FROM books WHERE id = ?", (plain["id"],))["page_labels_json"])
    assert stored == ["i", "ii", "1", "2"]  # the outline's printed page numbers and the course's agree


def test_a_damaged_stored_pdf_is_repaired_by_uploading_it_again(client: TestClient, tmp_path: Path) -> None:
    source = text_pdf(tmp_path / "big.pdf", pages=12)
    book = upload(client, source)
    stored = settings.books_dir / book["id"] / "book.pdf"
    stored.write_bytes(source.read_bytes()[: stored.stat().st_size // 3])  # a truncated sync copy
    resp = client.get(f"/api/books/{book['id']}/pages/9.png")
    assert resp.status_code == 404 and "missing or damaged" in resp.json()["detail"]
    assert upload(client, source)["already_in_library"] == "identical_file"
    assert stored.read_bytes() == source.read_bytes()
    assert client.get(f"/api/books/{book['id']}/pages/9.png").status_code == 200


def test_a_stored_file_that_still_opens_but_is_not_the_book_is_repaired(client: TestClient, tmp_path: Path) -> None:
    """A copy cut short by a sync can still open with every page (MuPDF repairs it): only its hash shows the damage."""
    source = text_pdf(tmp_path / "s.pdf", pages=5)
    book = upload(client, source)
    stored = settings.books_dir / book["id"] / "book.pdf"
    text_pdf(stored, pages=5, seed="garbled")  # opens, same page count, other bytes
    assert client.get(f"/api/books/{book['id']}").json()["pdf_missing"] is True
    assert upload(client, source)["already_in_library"] == "identical_file"
    assert stored.read_bytes() == source.read_bytes()
    assert client.get(f"/api/books/{book['id']}").json()["pdf_missing"] is False


def test_a_same_book_copy_restores_a_missing_pdf(client: TestClient, tmp_path: Path) -> None:
    book = upload(client, text_pdf(tmp_path / "orig.pdf", pages=4, title="Orig"))
    (settings.books_dir / book["id"] / "book.pdf").unlink()
    copy = text_pdf(tmp_path / "copy.pdf", pages=4, title="Downloaded again")
    again = upload(client, copy)
    assert again["id"] == book["id"] and again["already_in_library"] == "same_text" and again["pdf_missing"] is False
    assert (settings.books_dir / book["id"] / "book.pdf").read_bytes() == copy.read_bytes()
    assert client.get(f"/api/books/{book['id']}/pages/0.png").status_code == 200


def test_a_book_unreadable_for_a_moment_is_signed_later(client: TestClient, tmp_path: Path) -> None:
    import os

    first = upload(client, text_pdf(tmp_path / "x.pdf", title="X"))
    db.execute("DELETE FROM kv WHERE key LIKE 'book_pages:%'")
    stored = settings.books_dir / first["id"] / "book.pdf"
    from app.ingest import pdf

    pdf.forget_pdf(stored)  # not held open from the import (a fresh app start)
    os.chmod(stored, 0)  # e.g. a cloud placeholder that cannot be fetched right now
    try:
        upload(client, text_pdf(tmp_path / "other.pdf", seed="matrix"))  # its duplicate check reads the library
    finally:
        os.chmod(stored, 0o644)
    assert db.kv_get(f"book_pages:{first['id']}") is None  # not recorded as 'no text layer'
    assert upload(client, text_pdf(tmp_path / "x-again.pdf", title="X again"))["id"] == first["id"]


def test_a_missing_pdf_is_reported_and_restored_by_uploading_it_again(client: TestClient, tmp_path: Path) -> None:
    source = small_pdf(tmp_path / "lost.pdf")
    book = upload(client, source)
    (settings.books_dir / book["id"] / "book.pdf").unlink()  # e.g. a server checkout without the books folder
    assert client.get(f"/api/books/{book['id']}").json()["pdf_missing"] is True
    resp = client.get(f"/api/books/{book['id']}/pages/0.png")
    assert resp.status_code == 404 and "missing or damaged" in resp.json()["detail"]
    assert str(settings.books_dir) not in resp.text  # no server path in the message
    again = upload(client, source)
    assert again["id"] == book["id"] and again["already_in_library"] == "identical_file"
    assert (settings.books_dir / book["id"] / "book.pdf").read_bytes() == source.read_bytes()
    assert client.get(f"/api/books/{book['id']}/pages/0.png").status_code == 200
    assert client.get(f"/api/books/{book['id']}").json()["pdf_missing"] is False


def test_paths_are_stored_relative_to_the_books_folder_which_can_move(client: TestClient, tmp_path: Path) -> None:
    import shutil

    book = upload(client, small_pdf(tmp_path / "p.pdf"))
    assert db.query_one("SELECT pdf_path FROM books WHERE id = ?", (book["id"],))["pdf_path"] == f"{book['id']}/book.pdf"
    moved = tmp_path / "server-books"
    shutil.copytree(settings.books_dir, moved)
    shutil.rmtree(settings.books_dir)
    object.__setattr__(settings, "books_dir", moved)
    assert client.get(f"/api/books/{book['id']}/pages/0.png").status_code == 200


def test_books_in_the_older_data_dir_location_still_work(client: TestClient, tmp_path: Path) -> None:
    book = upload(client, small_pdf(tmp_path / "l.pdf"))
    legacy = settings.legacy_books_dir / book["id"]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    (settings.books_dir / book["id"]).rename(legacy)
    for recorded in (f"books/{book['id']}/book.pdf", "/gone/data/books/x/book.pdf"):  # both older stored forms
        db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", (recorded, book["id"]))
        assert client.get(f"/api/books/{book['id']}/pages/1.png").status_code == 200
    assert client.delete(f"/api/books/{book['id']}").status_code == 204
    assert not legacy.exists() and not (settings.page_cache_dir / book["id"]).exists()


def test_delete_never_touches_the_recorded_path(client: TestClient, tmp_path: Path) -> None:
    """Rows written by scripts may record the learner's ORIGINAL file: delete removes only folders named by the id."""
    original = small_pdf(tmp_path / "original.pdf")
    book = upload(client, original)
    db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", (str(original), book["id"]))
    assert client.delete(f"/api/books/{book['id']}").status_code == 204
    assert original.exists()


def test_legacy_outline_is_rebuilt_once(client: TestClient, tmp_path: Path, tmp_home: Path) -> None:
    toc = [[1, "R^n and C^n", 1], [1, "Two", 3]]
    book = client.post("/api/books/import-path", json={"path": str(small_pdf(tmp_path / "o.pdf", toc=toc))}).json()
    assert [n["title"] for n in book["outline"]] == ["Rⁿ and Cⁿ", "Two"]
    legacy = [dict(n, title=t) for n, t in zip(book["outline"], ["R^n and C^n", "Two"])]
    db.execute("UPDATE books SET outline_json = ? WHERE id = ?", (db.dumps(legacy), book["id"]))
    db.execute("DELETE FROM kv WHERE key = ?", (f"book_outline_version:{book['id']}",))
    assert [n["title"] for n in client.get(f"/api/books/{book['id']}").json()["outline"]] == ["Rⁿ and Cⁿ", "Two"]
    stored = db.loads(db.query_one("SELECT outline_json FROM books WHERE id = ?", (book["id"],))["outline_json"])
    assert stored[0]["title"] == "Rⁿ and Cⁿ"
