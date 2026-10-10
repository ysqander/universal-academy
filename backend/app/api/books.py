"""Books API: import a PDF (upload or local path), list/read books, page thumbnails, delete.

A book's PDF is kept at <books_dir>/<id>/book.pdf (settings.books_dir, by default the project's books/ folder), so a
new course from another chapter reuses it. An import that is already in the library — the same file, or the same
book saved again (same text, other bytes) — returns the book on file instead of adding a duplicate."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from .. import db
from ..config import settings
from ..ingest import pdf
from ..schemas import Book, BookSummary, ImportPathRequest, OutlineNode

log = logging.getLogger("ua.books")

router = APIRouter()

_CHUNK = 1 << 20
_SHA_KEY = "book_sha256:{}"  # sha256 of a file → the book it is (several files can name one book)
_PAGES_KEY = "book_pages:{}"  # book id → its page signatures (JSON list; "none" without a text layer)
_PDF_MISSING = "The PDF of this book is missing or damaged in the books folder. Upload the same file again to restore it."
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


Match = Literal["identical_file", "same_text"]


def _book(row: dict[str, Any], match: Match | None = None) -> Book:
    return Book(
        **_summary(row).model_dump(),
        outline=_current_outline(row),
        outline_source=row["outline_source"],
        has_text_layer=bool(row["has_text_layer"]),
        already_in_library=match,
        pdf_missing=not pdf.locate_book_pdf(row["id"], row["pdf_path"], row["page_count"])[1],
    )


def _book_pdf(row: dict[str, Any]) -> Path:
    """The book's PDF, located by id in the books dir — an intact copy first (older locations as fallbacks, see
    pdf.locate_book_pdf)."""
    return pdf.locate_book_pdf(row["id"], row["pdf_path"], row["page_count"])[0]


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
    settings.upload_tmp_dir.mkdir(parents=True, exist_ok=True)  # received outside the (synced) books folder
    fd, tmp_name = tempfile.mkstemp(dir=settings.upload_tmp_dir, prefix=".upload-", suffix=".pdf")
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
        return await _import(tmp, digest.hexdigest(), filename)
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
    return await _import(source, sha, source.name)


@router.get("/api/books/{book_id}", response_model=Book)
def get_book(book_id: str) -> Book:
    return _book(_get_row(book_id))


@router.get("/api/books/{book_id}/pages/{page}.png")
def page_png(book_id: str, page: int, dpi: int = Query(60)) -> FileResponse:
    row = _get_row(book_id)
    if not 0 <= page < row["page_count"]:
        raise HTTPException(status_code=404, detail=f"Page {page} is out of range (0..{row['page_count'] - 1}).")
    dpi = min(MAX_DPI, max(MIN_DPI, dpi))
    source = _book_pdf(row)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=_PDF_MISSING)
    try:
        path = pdf.cached_page_png(book_id, source, page, dpi)
    except (pdf.PdfError, RuntimeError, ValueError) as e:  # a damaged copy: say what to do, not where it is
        log.warning("could not render page %d of book %s: %s", page, book_id, e)
        raise HTTPException(status_code=404, detail=_PDF_MISSING) from e
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
        conn.execute("DELETE FROM kv WHERE key IN (?, ?)", (_OUTLINE_KEY.format(book_id), _PAGES_KEY.format(book_id)))
    # only folders named by the id: never a recorded path (older rows may name the learner's original file)
    for root in (settings.books_dir, settings.page_cache_dir, settings.legacy_books_dir):
        shutil.rmtree(_id_dir(root, book_id), ignore_errors=True)
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


def _id_dir(root: Path, book_id: str) -> Path:
    """<root>/<book_id>, refusing anything that is not a plain id (the id comes from the DB, but a path is built)."""
    if not book_id or Path(book_id).name != book_id or book_id in (".", ".."):
        raise ValueError(f"invalid book id {book_id!r}")
    return root / book_id


def _existing(sha: str) -> dict[str, Any] | None:
    book_id = db.kv_get(_SHA_KEY.format(sha))
    return db.query_one(f"{_SELECT} WHERE b.id = ?", (book_id,)) if book_id else None


def _signatures(path: Path) -> list[str] | None:
    """The file's page signatures (pdf.page_signatures); None without a text layer. Raises pdf.UnreadablePdf."""
    return pdf.page_signatures(path)


def _stored_signatures(row: dict[str, Any]) -> list[str] | None:
    """A book's page signatures, computed once and kept in kv. Not recorded while its PDF cannot be read (the next
    import tries again), so a passing read failure never hides the book from duplicate checks for good."""
    stored = db.kv_get(_PAGES_KEY.format(row["id"]))
    if stored is not None:
        return None if stored == "none" else db.loads(stored, None)
    path = pdf.book_pdf_path(row["id"], row["pdf_path"])
    if not path.is_file():
        return None
    try:
        signatures = _signatures(path)
    except pdf.UnreadablePdf as e:
        log.warning("book %s: page signatures not recorded yet (%s)", row["id"], e)
        return None
    db.kv_set(_PAGES_KEY.format(row["id"]), db.dumps(signatures) if signatures else "none")
    return signatures


def sign_library() -> None:
    """Record the page signatures of every book that has none yet (books imported before signatures existed)."""
    for row in db.query("SELECT id, pdf_path FROM books"):
        _stored_signatures(row)


def _same_book(signatures: list[str]) -> dict[str, Any] | None:
    """The book on file that this text is (same page count, ≥ 90 % of the text pages agreeing), if any."""
    for row in db.query(f"{_SELECT} WHERE b.page_count = ? ORDER BY b.created_at", (len(signatures),)):
        theirs = _stored_signatures(row)
        if theirs is not None and pdf.same_book(signatures, theirs):
            return row
    return None


def _store(source: Path, book_id: str) -> Path:
    """Copy source to <books_dir>/<id>/book.pdf through a temp file in the same folder (a reader never sees half a
    file); readable by the app's other processes."""
    book_dir = _id_dir(settings.books_dir, book_id)
    book_dir.mkdir(parents=True, exist_ok=True)
    target = book_dir / "book.pdf"
    part = book_dir / ".book.pdf.part"
    try:
        shutil.copyfile(source, part)
        os.chmod(part, 0o644)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return target


async def _restore_if_unusable(row: dict[str, Any], source: Path) -> None:
    """A book already on file whose PDF is gone or damaged (a books folder not deployed or restored, a truncated sync
    copy) gets this file — the same book, by its bytes or its pages."""
    current, intact = await asyncio.to_thread(pdf.locate_book_pdf, row["id"], row["pdf_path"], row["page_count"])
    if intact:
        return
    pdf.forget_pdf(current)
    target = await asyncio.to_thread(_store, source, row["id"])
    pdf.forget_pdf(target)
    db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", (pdf.stored_book_path(row["id"]), row["id"]))
    db.execute("DELETE FROM kv WHERE key = ?", (_PAGES_KEY.format(row["id"]),))  # may come from the bad copy
    log.warning("restored the missing or damaged PDF of book %s (%s)", row["id"], row["title"])


async def _adopt_outline(row: dict[str, Any], source: Path, filename: str) -> None:
    """The same book uploaded with bookmarks, when the book on file has none from its PDF: take its outline (the pages
    are the same, so courses stay valid)."""
    if row["outline_source"] == "pdf":
        return
    try:
        meta = await asyncio.to_thread(pdf.read_book_meta, source, filename)
    except Exception as e:  # noqa: BLE001 — the book on file stays as it is
        log.warning("outline of %s not read: %s", filename, e)
        return
    if meta.outline_source == "pdf" and meta.outline and meta.page_count == row["page_count"]:
        db.execute(  # with the copy's page labels, which the outline's printed page numbers come from
            "UPDATE books SET outline_json = ?, outline_source = 'pdf', page_labels_json = ? WHERE id = ?",
            (db.dumps([n.model_dump() for n in meta.outline]), db.dumps(meta.page_labels), row["id"]),
        )
        db.kv_set(_OUTLINE_KEY.format(row["id"]), OUTLINE_VERSION)
        log.info("book %s: took the bookmarks of %s as its outline", row["id"], filename)


async def _import(source: Path, sha: str, filename: str) -> Book:
    """Validate, find the book if it is already on file (the same bytes, or the same pages: the same book saved again,
    see pdf.page_signatures), else store it as <books_dir>/<id>/book.pdf and read its metadata + outline."""
    async with _import_lock():
        existing = _existing(sha)
        if existing is not None:
            await _restore_if_unusable(existing, source)
            log.info("import of %s: identical to existing book %s", filename, existing["id"])
            return _book(_get_row(existing["id"]), "identical_file")
        try:
            await asyncio.to_thread(pdf.validate_pdf, source, filename)
        except pdf.PdfError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            signatures = await asyncio.to_thread(_signatures, source)
        except pdf.UnreadablePdf as e:  # only the byte-for-byte check then
            log.warning("no page signatures for %s: %s", filename, e)
            signatures = None
        finally:
            pdf.forget_pdf(source)  # an upload's temp file is deleted next; the user's own file is not ours to hold open
        if signatures is not None:
            same = await asyncio.to_thread(_same_book, signatures)
            if same is not None:
                db.kv_set(_SHA_KEY.format(sha), same["id"])  # this file is now recognised at once (and counts as intact)
                try:
                    await _restore_if_unusable(same, source)
                    await _adopt_outline(same, source, filename)
                finally:
                    pdf.forget_pdf(source)
                log.info("import of %s: the same book as %s (another copy of it)", filename, same["id"])
                return _book(_get_row(same["id"]), "same_text")

        book_id = db.new_id()
        book_dir = _id_dir(settings.books_dir, book_id)
        target = book_dir / "book.pdf"
        try:
            await asyncio.to_thread(_store, source, book_id)
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
                    pdf.stored_book_path(book_id),  # relative to the books dir
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
            db.kv_set(_PAGES_KEY.format(book_id), db.dumps(signatures) if signatures else "none")
        except BaseException:
            pdf.forget_pdf(target)
            shutil.rmtree(book_dir, ignore_errors=True)
            raise
        log.info(
            "imported %s as book %s: %d pages, outline %s (%d top-level)",
            filename, book_id, meta.page_count, outline_source, len(outline),
        )
        return _book(_get_row(book_id))
