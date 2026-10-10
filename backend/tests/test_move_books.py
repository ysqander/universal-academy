"""scripts/move_books.py: books stored the old way (<data>/books/<id>, recorded paths) move into the books folder."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.api import books
from app.config import settings

from test_ingest_books_api import small_pdf, text_pdf

BACKEND = Path(__file__).resolve().parents[1]


def run_tool(*extra: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "scripts/move_books.py", "--data-dir", str(settings.data_dir), "--books-dir",
           str(settings.books_dir), "--force", *extra]
    return subprocess.run(cmd, cwd=BACKEND, env={**os.environ}, capture_output=True, text=True)


def test_old_layout_books_are_copied_into_the_books_folder(tmp_path: Path) -> None:
    client = TestClient(FastAPI())
    client.app.include_router(books.router)
    with text_pdf(tmp_path / "axler.pdf", title="LADR").open("rb") as fh:
        old = client.post("/api/books", files={"file": ("axler.pdf", fh, "application/pdf")}).json()
    with small_pdf(tmp_path / "gone.pdf").open("rb") as fh:
        gone = client.post("/api/books", files={"file": ("gone.pdf", fh, "application/pdf")}).json()
    # as stored before 2026-10-10: the PDF in <data>/books/<id>, an absolute path into a folder that has moved since
    legacy = settings.legacy_books_dir / old["id"]
    legacy.parent.mkdir(parents=True)
    (settings.books_dir / old["id"]).rename(legacy)
    db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", ("/Users/x/project/data/books/old/book.pdf", old["id"]))
    db.execute("DELETE FROM kv WHERE key LIKE 'book_pages:%'")  # imported before page signatures existed
    assert db.kv_get(f"book_pages:{old['id']}") is None
    (settings.books_dir / gone["id"] / "book.pdf").unlink()

    first = run_tool("--yes", "--no-backup")
    assert first.returncode == 1, first.stdout + first.stderr  # one book has no PDF anywhere: not a clean run
    assert (settings.books_dir / old["id"] / "book.pdf").read_bytes() == (legacy / "book.pdf").read_bytes()
    assert (legacy / "book.pdf").exists()  # copied, never moved
    row = db.query_one("SELECT pdf_path FROM books WHERE id = ?", (old["id"],))
    assert row["pdf_path"] == f"{old['id']}/book.pdf"
    assert db.kv_get(f"book_pages:{old['id']}") not in (None, "none")  # a re-saved copy will be recognised
    assert "NOT FOUND" in first.stdout and gone["id"] in first.stdout

    again = run_tool("--yes", "--no-backup")  # idempotent
    assert "Nothing to move" in again.stdout and again.returncode == 1  # the missing book is still reported
    db.execute("DELETE FROM books WHERE id = ?", (gone["id"],))
    assert run_tool("--yes", "--no-backup").returncode == 0


def test_a_file_that_is_not_the_imported_one_is_not_copied(tmp_path: Path) -> None:
    client = TestClient(FastAPI())
    client.app.include_router(books.router)
    with text_pdf(tmp_path / "a.pdf").open("rb") as fh:
        book = client.post("/api/books", files={"file": ("a.pdf", fh, "application/pdf")}).json()
    legacy = settings.legacy_books_dir / book["id"]
    legacy.mkdir(parents=True)
    (settings.books_dir / book["id"]).rename(tmp_path / "elsewhere")
    text_pdf(legacy / "book.pdf", seed="matrix")  # some other PDF where the book used to be
    result = run_tool("--yes", "--no-backup")
    assert "PDF DAMAGED" in result.stdout and result.returncode == 1  # not the imported file: never copied
    assert not (settings.books_dir / book["id"] / "book.pdf").exists()


def test_a_book_in_place_is_signed_and_a_damaged_one_is_copied_again(tmp_path: Path) -> None:
    client = TestClient(FastAPI())
    client.app.include_router(books.router)
    with text_pdf(tmp_path / "s.pdf", pages=4).open("rb") as fh:
        signed = client.post("/api/books", files={"file": ("s.pdf", fh, "application/pdf")}).json()
    with text_pdf(tmp_path / "d.pdf", pages=4, seed="matrix").open("rb") as fh:
        damaged = client.post("/api/books", files={"file": ("d.pdf", fh, "application/pdf")}).json()
    db.execute("DELETE FROM kv WHERE key LIKE 'book_pages:%'")
    good = settings.books_dir / damaged["id"] / "book.pdf"
    legacy = settings.legacy_books_dir / damaged["id"]
    legacy.mkdir(parents=True)
    (legacy / "book.pdf").write_bytes(good.read_bytes())  # an intact older copy
    good.write_bytes(good.read_bytes()[: good.stat().st_size // 2])  # the copy in place is cut short
    result = run_tool("--yes", "--no-backup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "record its page signatures" in result.stdout and f"copy from {legacy / 'book.pdf'}" in result.stdout
    assert db.kv_get(f"book_pages:{signed['id']}") not in (None, "none")
    assert good.read_bytes() == (legacy / "book.pdf").read_bytes()


def test_a_book_in_place_with_an_old_path_only_gets_its_path_recorded(tmp_path: Path) -> None:
    client = TestClient(FastAPI())
    client.app.include_router(books.router)
    with text_pdf(tmp_path / "p.pdf").open("rb") as fh:
        book = client.post("/api/books", files={"file": ("p.pdf", fh, "application/pdf")}).json()
    db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", (f"books/{book['id']}/book.pdf", book["id"]))
    result = run_tool("--yes", "--no-backup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert db.query_one("SELECT pdf_path FROM books WHERE id = ?", (book["id"],))["pdf_path"] == f"{book['id']}/book.pdf"


def test_the_tool_refuses_while_the_app_answers(tmp_path: Path) -> None:
    import http.server
    import threading

    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        cmd = [sys.executable, "scripts/move_books.py", "--data-dir", str(settings.data_dir), "--books-dir",
               str(settings.books_dir), "--yes", "--server-url", url]
        result = subprocess.run(cmd, cwd=BACKEND, env={**os.environ}, capture_output=True, text=True)
    finally:
        server.shutdown()
    assert result.returncode == 1 and "Stop it first" in result.stdout
