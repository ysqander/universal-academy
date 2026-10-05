"""Books API: import a PDF (upload or local path), list/read books, page thumbnails, delete."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from .. import db
from ..config import settings
from ..ingest import pdf
from ..schemas import Book, BookSummary, ImportPathRequest, OutlineNode

log = logging.getLogger("ua.books")

router = APIRouter()

_CHUNK = 1 << 20
_SHA_KEY = "book_sha256:{}"
# Bump when outline building changes (v2: sections starting mid-page share it, prettified titles): stored
# outlines of older imports are rebuilt from the PDF once, on the next read.
OUTLINE_VERSION = "2"
_OUTLINE_KEY = "book_outline_version:{}"
_UPLOAD_INSTEAD = "Upload the file instead."
_PAGE_CACHE_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable"}
MIN_DPI, MAX_DPI = 30, 200

_import_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _import_lock() -> asyncio.Lock:
    """Serializes imports (per event loop) so two identical uploads cannot both pass the duplicate check."""
    loop = asyncio.get_running_loop()
    lock = _import_locks.get(loop)
    if lock is None:
        _import_locks.clear()
        lock = _import_locks[loop] = asyncio.Lock()
    return lock


# ---------------------------------------------------------------------------
# Row → API models
# ---------------------------------------------------------------------------


def _summary(row: dict[str, Any]) -> BookSummary:
    return BookSummary(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        page_count=row["page_count"],
        created_at=row["created_at"],
        course_count=row.get("course_count", 0) or 0,
    )


def _book(row: dict[str, Any]) -> Book:
    return Book(
        **_summary(row).model_dump(),
        outline=_current_outline(row),
        outline_source=row["outline_source"],
        has_text_layer=bool(row["has_text_layer"]),
    )


def _book_pdf(row: dict[str, Any]) -> Path:
    """The stored copy, located by id (data/books/<id>/book.pdf), so a moved data dir keeps working."""
    return pdf.book_pdf_path(row["id"], row["pdf_path"])


def _current_outline(row: dict[str, Any]) -> list[OutlineNode]:
    nodes = [OutlineNode.model_validate(n) for n in db.loads(row["outline_json"], [])]
    key = _OUTLINE_KEY.format(row["id"])
    if not nodes or db.kv_get(key) == OUTLINE_VERSION:
        return nodes
    try:
        rebuilt = pdf.read_book_meta(_book_pdf(row)).outline if row["outline_source"] == "pdf" else []
        if not rebuilt:  # detected outlines need the LLM to rebuild: only prettify their titles
            rebuilt = [_prettified(n) for n in nodes]
        db.execute("UPDATE books SET outline_json = ? WHERE id = ?", (db.dumps([n.model_dump() for n in rebuilt]), row["id"]))
        db.kv_set(key, OUTLINE_VERSION)
        log.info("rebuilt the outline of book %s (outline version %s)", row["id"], OUTLINE_VERSION)
        return rebuilt
    except Exception as e:  # never fail a read over this: the stored outline is still usable
        log.warning("could not rebuild the outline of book %s: %s", row["id"], e)
        return nodes


def _prettified(node: OutlineNode) -> OutlineNode:
    return node.model_copy(
        update={"title": pdf.prettify_title(node.title), "children": [_prettified(c) for c in node.children]}
    )


def _is_local_client(request: Request) -> bool:
    """The request comes from this computer (and not through a local proxy forwarding someone else's)."""

    def loopback(host: str) -> bool:
        host = host.strip().strip("[]")
        if host in ("localhost", "testclient"):  # "testclient": Starlette's TestClient
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return ip.is_loopback

    if request.client is None or not loopback(request.client.host):
        return False
    forwarded = [h for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return all(loopback(h) for h in forwarded)


_SELECT = "SELECT b.*, (SELECT COUNT(*) FROM courses c WHERE c.book_id = b.id) AS course_count FROM books b"


def _get_row(book_id: str) -> dict[str, Any]:
    row = db.query_one(f"{_SELECT} WHERE b.id = ?", (book_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/books", response_model=list[BookSummary])
def list_books() -> list[BookSummary]:
    return [_summary(r) for r in db.query(f"{_SELECT} ORDER BY b.created_at DESC, b.rowid DESC")]


@router.post("/api/books", response_model=Book)
async def upload_book(file: UploadFile) -> Book:
    filename = Path(file.filename or "book.pdf").name  # the content is validated, not the extension
    settings.books_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=settings.books_dir, prefix=".upload-", suffix=".pdf")
    tmp = Path(tmp_name)
    try:
        digest = hashlib.sha256()
        size = 0
        with open(fd, "wb") as out:
            while chunk := await file.read(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail=f"{filename} is empty.")
        return await _import(tmp, digest.hexdigest(), filename, move=True)
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/api/books/import-path", response_model=Book)
async def import_path(req: ImportPathRequest, request: Request) -> Book:
    """Import a PDF by local path: only for requests from this computer and files under the user's home
    folder (the server may be reachable from the LAN, without authentication)."""
    if not _is_local_client(request):
        raise HTTPException(status_code=403, detail=f"Import by path only works on the computer running the app. {_UPLOAD_INSTEAD}")
    try:
        source = Path(req.path.strip()).expanduser().resolve()
        home = Path.home().resolve()
    except (RuntimeError, OSError, ValueError) as e:  # "~nosuchuser/...", embedded NUL, ...
        raise HTTPException(status_code=400, detail=f"That path cannot be read. {_UPLOAD_INSTEAD}") from e
    if not source.is_relative_to(home):
        raise HTTPException(status_code=403, detail=f"Only files in your home folder can be imported by path. {_UPLOAD_INSTEAD}")
    if source.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail=f"{source.name} is not a .pdf file.")
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {source}")
    try:
        sha = await asyncio.to_thread(_sha256_file, source)
    except OSError as e:  # unreadable (permissions, cloud placeholder, ...)
        raise HTTPException(status_code=400, detail=f"{source.name} cannot be read. {_UPLOAD_INSTEAD}") from e
    return await _import(source, sha, source.name, move=False)


@router.get("/api/books/{book_id}", response_model=Book)
def get_book(book_id: str) -> Book:
    return _book(_get_row(book_id))


@router.get("/api/books/{book_id}/pages/{page}.png")
def page_png(book_id: str, page: int, dpi: int = Query(60)) -> FileResponse:
    row = _get_row(book_id)
    if not 0 <= page < row["page_count"]:
        raise HTTPException(status_code=404, detail=f"Page {page} is out of range (0..{row['page_count'] - 1}).")
    dpi = min(MAX_DPI, max(MIN_DPI, dpi))
    path = pdf.cached_page_png(book_id, _book_pdf(row), page, dpi)
    return FileResponse(path, media_type="image/png", headers=_PAGE_CACHE_HEADERS)


@router.delete("/api/books/{book_id}", status_code=204)
def delete_book(book_id: str) -> Response:
    row = _get_row(book_id)
    if row["course_count"]:
        raise HTTPException(
            status_code=409, detail=f"Delete the {row['course_count']} course(s) built from this book first."
        )
    pdf.forget_pdf(_book_pdf(row))
    with db.connect() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.execute("DELETE FROM kv WHERE key LIKE 'book_sha256:%' AND value = ?", (book_id,))
        conn.execute("DELETE FROM kv WHERE key = ?", (_OUTLINE_KEY.format(book_id),))
    shutil.rmtree(settings.books_dir / book_id, ignore_errors=True)
    log.info("deleted book %s (%s)", book_id, row["title"])
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _existing(sha: str) -> dict[str, Any] | None:
    book_id = db.kv_get(_SHA_KEY.format(sha))
    return db.query_one(f"{_SELECT} WHERE b.id = ?", (book_id,)) if book_id else None


async def _import(source: Path, sha: str, filename: str, *, move: bool) -> Book:
    """Validate, deduplicate by content hash, store as data/books/<id>/book.pdf, read metadata + outline."""
    async with _import_lock():
        existing = _existing(sha)
        if existing is not None:
            log.info("import of %s: identical to existing book %s", filename, existing["id"])
            return _book(existing)
        try:
            await asyncio.to_thread(pdf.validate_pdf, source, filename)
        except pdf.PdfError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        book_id = db.new_id()
        book_dir = settings.books_dir / book_id
        book_dir.mkdir(parents=True, exist_ok=True)
        target = book_dir / "book.pdf"
        try:
            await asyncio.to_thread(shutil.move if move else shutil.copyfile, source, target)
            meta = await asyncio.to_thread(pdf.read_book_meta, target, filename)
            outline, outline_source = meta.outline, meta.outline_source
            if not outline and meta.has_text_layer:
                outline = await pdf.detect_outline(target, meta.page_labels)
                outline_source = "detected" if outline else "none"
            db.execute(
                "INSERT INTO books(id, title, author, filename, pdf_path, page_count, outline_json, outline_source,"
                " page_labels_json, has_text_layer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    book_id,
                    meta.title,
                    meta.author,
                    filename,
                    pdf.stored_path(target),  # relative to the data dir
                    meta.page_count,
                    db.dumps([n.model_dump() for n in outline]),
                    outline_source,
                    db.dumps(meta.page_labels),
                    int(meta.has_text_layer),
                    db.now_iso(),
                ),
            )
            db.kv_set(_SHA_KEY.format(sha), book_id)
            db.kv_set(_OUTLINE_KEY.format(book_id), OUTLINE_VERSION)
        except BaseException:
            pdf.forget_pdf(target)
            shutil.rmtree(book_dir, ignore_errors=True)
            raise
        log.info(
            "imported %s as book %s: %d pages, outline %s (%d top-level)",
            filename, book_id, meta.page_count, outline_source, len(outline),
        )
        return _book(_get_row(book_id))
