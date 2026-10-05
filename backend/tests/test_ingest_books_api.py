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
    chapter = next(n for n in book["outline"] if n["title"] == "Vector Spaces")
    assert (chapter["start_page"], chapter["end_page"]) == (14, 39)
    assert chapter["children"][0]["label_start"] == "2"

    again = client.post("/api/books/import-path", json={"path": str(axler_pdf)})
    assert again.status_code == 200 and again.json()["id"] == book["id"]  # same sha256 → same book
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
    assert not list(settings.books_dir.glob(".upload-*"))  # temp upload moved into place

    listed = client.get("/api/books").json()
    assert [b["id"] for b in listed] == [b2["id"], b1["id"]]  # newest first
    assert set(listed[0]) == {"id", "title", "author", "page_count", "created_at", "course_count"}

    got = client.get(f"/api/books/{b1['id']}")
    assert got.status_code == 200 and got.json() == b1

    assert client.delete(f"/api/books/{b1['id']}").status_code == 204
    assert client.get(f"/api/books/{b1['id']}").status_code == 404
    assert not (settings.books_dir / b1["id"]).exists()
    with first.open("rb") as fh:  # re-importing after delete creates a fresh book
        r3 = client.post("/api/books", files={"file": ("first.pdf", fh, "application/pdf")})
    assert r3.status_code == 200 and r3.json()["id"] != b1["id"]


def test_delete_with_courses_conflicts(client: TestClient, tmp_path: Path) -> None:
    book = client.post("/api/books/import-path", json={"path": str(small_pdf(tmp_path / "c.pdf"))}).json()
    db.execute(
        "INSERT INTO courses(id, book_id, title, start_page, end_page, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("course1", book["id"], "Course", 0, 1, db.now_iso()),
    )
    assert client.get("/api/books").json()[0]["course_count"] == 1
    resp = client.delete(f"/api/books/{book['id']}")
    assert resp.status_code == 409 and "course" in resp.json()["detail"]


def test_page_thumbnails(client: TestClient, tmp_path: Path) -> None:
    book = client.post("/api/books/import-path", json={"path": str(small_pdf(tmp_path / "t.pdf"))}).json()
    resp = client.get(f"/api/books/{book['id']}/pages/1.png")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    assert "max-age" in resp.headers["cache-control"]
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (settings.books_dir / book["id"] / "pages" / "1@60.png").exists()

    assert client.get(f"/api/books/{book['id']}/pages/0.png?dpi=5").status_code == 200
    assert (settings.books_dir / book["id"] / "pages" / "0@30.png").exists()  # dpi clamped to 30..200
    assert client.get(f"/api/books/{book['id']}/pages/0.png?dpi=900").status_code == 200
    assert (settings.books_dir / book["id"] / "pages" / "0@200.png").exists()

    assert client.get(f"/api/books/{book['id']}/pages/3.png").status_code == 404
    assert client.get(f"/api/books/{book['id']}/pages/-1.png").status_code == 404
    assert client.get("/api/books/nope/pages/0.png").status_code == 404


def test_invalid_inputs(client: TestClient, tmp_path: Path) -> None:
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
    assert not [p for p in settings.books_dir.iterdir()]  # nothing left behind


def test_import_path_expands_user(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    small_pdf(tmp_path / "home.pdf")
    resp = client.post("/api/books/import-path", json={"path": "~/home.pdf"})
    assert resp.status_code == 200 and resp.json()["title"] == "Home"
