"""PDF access with PyMuPDF: book metadata + outline tree, page renders, deterministic figure candidates.

PyMuPDF is not thread-safe, so every call into it holds MUPDF_LOCK (async callers run these sync helpers
through asyncio.to_thread). Page indexes are 0-based PDF indexes; geometry is in PDF points.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import threading
import unicodedata
from collections import Counter, OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel, Field

from .. import llm, prompts
from ..config import settings
from ..schemas import OutlineNode
from .nemotron import Block

log = logging.getLogger("ua.ingest")

MUPDF_LOCK = threading.RLock()

Rect4 = tuple[float, float, float, float]
OutlineSource = Literal["pdf", "detected", "none"]

_DOC_CACHE: OrderedDict[tuple[str, int, int], pymupdf.Document] = OrderedDict()
_DOC_CACHE_SIZE = 4


class PdfError(ValueError):
    """The file is not a usable PDF (message is user-facing)."""


# ---------------------------------------------------------------------------
# Opening documents
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stored paths: rows keep paths relative to settings.data_dir, so a moved or copied data dir keeps working
# ---------------------------------------------------------------------------


def stored_path(path: Path) -> str:
    """The form a file path is stored in the DB: relative to the data dir when it lies inside it."""
    path = Path(path)
    try:
        return path.resolve().relative_to(settings.data_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def resolve_stored(value: str) -> Path:
    """A stored path → a usable one: relative paths are resolved against the (current) data dir; absolute paths of
    older rows are returned unchanged."""
    path = Path(value)
    return path if path.is_absolute() else settings.data_dir / path


def book_pdf_path(book_id: str, stored: str) -> Path:
    """A book's PDF, located by id (data/books/<id>/book.pdf) so a moved data dir keeps working; the recorded path is
    only a fallback (books imported by older versions recorded an absolute path)."""
    by_id = settings.books_dir / book_id / "book.pdf"
    return by_id if by_id.exists() else resolve_stored(stored)


@contextmanager
def open_pdf(pdf_path: Path) -> Iterator[pymupdf.Document]:
    """Yield a cached, already-open document while holding MUPDF_LOCK. Do not close it."""
    with MUPDF_LOCK:
        yield _cached_doc(Path(pdf_path))


def _cached_doc(pdf_path: Path) -> pymupdf.Document:
    stat = pdf_path.stat()
    key = (str(pdf_path.resolve()), stat.st_mtime_ns, stat.st_size)
    doc = _DOC_CACHE.get(key)
    if doc is not None:
        _DOC_CACHE.move_to_end(key)
        return doc
    doc = _open_checked(pdf_path)
    _DOC_CACHE[key] = doc
    while len(_DOC_CACHE) > _DOC_CACHE_SIZE:
        _, old = _DOC_CACHE.popitem(last=False)
        old.close()
    return doc


def forget_pdf(pdf_path: Path) -> None:
    """Close cached handles of a file (call before deleting it)."""
    resolved = str(Path(pdf_path).resolve())
    with MUPDF_LOCK:
        for key in [k for k in _DOC_CACHE if k[0] == resolved]:
            _DOC_CACHE.pop(key).close()


def _open_checked(pdf_path: Path, display_name: str | None = None) -> pymupdf.Document:
    name = display_name or pdf_path.name
    try:
        doc = pymupdf.open(pdf_path, filetype="pdf")
    except Exception as e:  # pymupdf raises several types (FileDataError, RuntimeError, ...)
        log.info("cannot open %s as PDF: %s", pdf_path, e)
        raise PdfError(f"{name} is not a valid PDF file (it could not be opened).") from e
    if doc.needs_pass:
        doc.close()
        raise PdfError(f"{name} is password-protected; remove the password and import it again.")
    if doc.page_count <= 0:
        doc.close()
        raise PdfError(f"{name} has no pages.")
    return doc


def validate_pdf(pdf_path: Path, display_name: str | None = None) -> int:
    """Raise PdfError (message names `display_name`) unless the file opens as a PDF with pages; return the count."""
    with MUPDF_LOCK:
        doc = _open_checked(Path(pdf_path), display_name)
        try:
            return doc.page_count
        finally:
            doc.close()


# ---------------------------------------------------------------------------
# Book metadata + outline
# ---------------------------------------------------------------------------


@dataclass
class BookMeta:
    title: str
    author: str
    page_count: int
    page_labels: list[str]
    outline: list[OutlineNode]
    outline_source: OutlineSource
    has_text_layer: bool


def read_book_meta(pdf_path: Path, display_name: str | None = None) -> BookMeta:
    """Fast, no LLM. `display_name` (e.g. the uploaded filename) is the title fallback; else the file stem."""
    pdf_path = Path(pdf_path)
    with open_pdf(pdf_path) as doc:
        meta = doc.metadata or {}
        labels = [doc[i].get_label() or str(i + 1) for i in range(doc.page_count)]
        has_text = any(len(doc[i].get_text("text").strip()) > 200 for i in range(min(30, doc.page_count)))
        toc = toc_with_positions(doc)
        page_count = doc.page_count
    outline = outline_from_toc(toc, page_count, labels) if len(toc) >= 2 else []
    title = prettify_title(_clean_meta_title(meta.get("title") or "")) or prettify_filename(display_name or pdf_path.name)
    return BookMeta(
        title=title,
        author=" ".join((meta.get("author") or "").split()),
        page_count=page_count,
        page_labels=labels,
        outline=outline,
        outline_source="pdf" if outline else "none",
        has_text_layer=has_text,
    )


def _clean_meta_title(title: str) -> str:
    title = " ".join(title.split())
    if not title or title.lower() in {"untitled", "title", "unknown"}:
        return ""
    if re.search(r"\.(pdf|docx?|tex|dvi|ps|indd)$", title, re.I) or title.lower().startswith("microsoft word"):
        return ""
    return title


def prettify_filename(name: str) -> str:
    stem = Path(name).stem
    text = re.sub(r"[_\s]+", " ", stem)
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text) if " " not in text else text
    text = " ".join(text.split())
    if text and text == text.lower():
        text = text.title()
    return text or "Untitled book"


TOP_OF_PAGE = 0.12  # a section whose heading is this close to the top of its page starts the page
_RUNNING_HEADER_BAND = 0.07  # fraction of the page height that holds the running header


def toc_with_positions(doc: pymupdf.Document) -> list[list[Any]]:
    """get_toc rows [level, title, 1-based page, starts_mid_page]. A section starts mid-page when its
    destination lies below body text of that page (it then shares the page with the previous section)."""
    rows: list[list[Any]] = []
    for row in doc.get_toc(simple=False):
        level, title, page = row[0], row[1], row[2]
        dest = row[3] if len(row) > 3 and isinstance(row[3], dict) else {}
        mid = False
        if 1 <= page <= doc.page_count:
            pg = doc[page - 1]
            mid = starts_mid_page(pg, _dest_y(pg, dest), str(title))
        rows.append([level, title, page, mid])
    return rows


def _dest_y(page: pymupdf.Page, dest: dict[str, Any]) -> float | None:
    """Top-left y of an outline destination, None if unknown. PyMuPDF reports direct /GoTo targets in page
    (top-left) coordinates but resolved named destinations in raw PDF (bottom-left) coordinates."""
    to = dest.get("to")
    if to is None:
        return None
    try:
        point = pymupdf.Point(to)
        y = (point * page.transformation_matrix).y if dest.get("kind") == pymupdf.LINK_NAMED else point.y
    except Exception:  # malformed destination
        return None
    height = page.rect.height
    if not 0 <= y <= height or y > 0.95 * height:  # off-page, or /Fit-style "bottom-left corner" targets
        return None
    return float(y)


def starts_mid_page(page: pymupdf.Page, y: float | None, title: str) -> bool:
    """True if body text precedes the section start at `y` on its page. Without a position the title is searched
    for on the page; if it cannot be found either, the page is assumed shared (better one page too many)."""
    height = page.rect.height
    if y is None:
        hits = [r for r in page.search_for(title.strip()[:80]) if r.y0 >= _RUNNING_HEADER_BAND * height] if title.strip() else []
        if not hits:
            return True
        y = min(r.y0 for r in hits)
    if y <= TOP_OF_PAGE * height:
        return False
    for _x0, y0, _x1, y1, text, *_ in page.get_text("blocks"):
        if text.strip() and y0 >= _RUNNING_HEADER_BAND * height and y1 <= y - 2:
            return True
    return False


def outline_from_toc(toc: Sequence[Sequence[Any]], page_count: int, labels: list[str]) -> list[OutlineNode]:
    """Build the OutlineNode tree from get_toc() rows [level, title, 1-based page(, starts_mid_page)]."""
    flat: list[tuple[int, str, int | None, bool]] = []
    for row in toc:
        level, title, page = int(row[0]), " ".join(str(row[1]).split()), int(row[2])
        mid = bool(row[3]) if len(row) > 3 and isinstance(row[3], bool) else False
        flat.append((max(1, level), title or "Untitled", page - 1 if page >= 1 else None, mid))
    return build_outline(flat, page_count, labels)


def build_outline(
    entries: Sequence[tuple[int, str, int | None] | tuple[int, str, int | None, bool]], page_count: int, labels: list[str]
) -> list[OutlineNode]:
    """entries: (level, title, 0-based start or None[, starts_mid_page]) in TOC order.

    A node ends where the nearest later-starting entry at the same or higher level begins (page order, not TOC
    order, so a misplaced 'Index' bookmark cannot swallow chapters): on that entry's start page when it starts
    mid-page (the page is shared), else the page before. Clamped into the parent's range; the last top-level
    node ends at the last page. Titles are prettified (R^n → Rⁿ). Ids n0..nN in TOC order."""
    if not entries or page_count <= 0:
        return []
    last = page_count - 1
    levels = [e[0] for e in entries]
    mids = [bool(e[3]) if len(e) > 3 else False for e in entries]  # type: ignore[misc]
    starts: list[int] = []
    for i, entry in enumerate(entries):
        start = entry[2]
        if start is None:  # no destination: take the next known start (or the previous one)
            start = next((e[2] for e in entries[i + 1 :] if e[2] is not None), starts[-1] if starts else 0)
            mids[i] = False
        starts.append(min(max(start, 0), last))

    roots: list[OutlineNode] = []
    stack: list[OutlineNode] = []
    for i, entry in enumerate(entries):
        level, title = entry[0], entry[1]
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        start = starts[i]
        if parent is not None:  # a child never leaves its parent's range (TOC typos)
            start = min(max(start, parent.start_page), parent.end_page)
        nxt = min(
            (
                (starts[j], j)
                for j in range(len(entries))
                if j != i and levels[j] <= level and (starts[j] > start or (starts[j] == start and j > i))
            ),
            default=None,
        )
        if nxt is None:
            end = last
        else:
            start_j, j = nxt
            end = start_j if mids[j] and start_j > start else start_j - 1
        if parent is not None:
            end = min(end, parent.end_page)
        end = max(start, end)
        node = OutlineNode(
            id=f"n{i}",
            title=prettify_title(title) or title,
            level=level,
            start_page=start,
            end_page=end,
            label_start=_label(labels, start),
            label_end=_label(labels, end),
        )
        (parent.children if parent else roots).append(node)
        stack.append(node)
    return roots


def _label(labels: list[str], page: int) -> str:
    return labels[page] if 0 <= page < len(labels) and labels[page] else str(page + 1)


_TITLE_SUPERSCRIPTS = {
    **dict(zip("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")),
    **dict(zip("abcdefghijklmnoprstuvwxyz", "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ")),
    "T": "ᵀ",  # transpose; same table as the frontend's prettyTitle
}
_TITLE_EXPONENT_RE = re.compile(r"(?<!\\)\^(?:\{([^{}\s\\])\}|([^\s{}^\\]))(?![A-Za-z0-9])")
_TITLE_MATH_RE = re.compile(r"\$[^$]*\$")


def prettify_title(title: str) -> str:
    r"""TeX-like carets in bookmark / TOC titles as Unicode: 'R^n and C^n' → 'Rⁿ and Cⁿ', 'x^2' → 'x²',
    'F^\infty' → 'F^∞' (no superscript ∞ exists). Only single-character exponents with a Unicode superscript
    are rewritten; text inside $…$ is left alone."""
    if "^" not in title and "\\infty" not in title:
        return title

    def plain(part: str) -> str:
        part = re.sub(r"\\infty(?![A-Za-z])", "∞", part).replace("^{∞}", "^∞")
        return _TITLE_EXPONENT_RE.sub(lambda m: _TITLE_SUPERSCRIPTS.get(m.group(1) or m.group(2), m.group(0)), part)

    out: list[str] = []
    pos = 0
    for m in _TITLE_MATH_RE.finditer(title):
        out += [plain(title[pos : m.start()]), m.group(0)]
        pos = m.end()
    out.append(plain(title[pos:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# Outline detection for PDFs without bookmarks (LLM over the first pages' text)
# ---------------------------------------------------------------------------

DETECT_PAGES = 25
_DETECT_CHARS_PER_PAGE = 4000


class DetectedTocEntry(BaseModel):
    title: str = Field(description="Entry title as printed, without the leading number unless it is part of the name.")
    level: int = Field(description="1 = part/chapter, 2 = section, 3 = subsection.")
    printed_page: str = Field(description="Page number exactly as printed in the table of contents, e.g. '17' or 'xii'.")


class DetectedToc(BaseModel):
    found: bool = Field(description="True if the text contains a table of contents.")
    entries: list[DetectedTocEntry] = Field(description="Entries in table-of-contents order; [] if none was found.")


async def detect_outline(pdf_path: Path, page_labels: list[str]) -> list[OutlineNode]:
    """Outline for a PDF without bookmarks: TOC text → LLM → printed page numbers → PDF indexes. [] if impossible."""
    if settings.fake_llm:
        log.info("outline detection skipped in fake-LLM mode")
        return []
    pages_text, page_count = await _first_pages_text(Path(pdf_path))
    if sum(len(t) for t in pages_text) < 200:
        log.info("outline detection: no text layer in %s", Path(pdf_path).name)
        return []
    text = "\n\n".join(f"=== PDF page {i} ===\n{t[:_DETECT_CHARS_PER_PAGE]}" for i, t in enumerate(pages_text) if t.strip())
    try:
        toc = await llm.structured(
            role="vision",
            instructions=prompts.for_role("vision").DETECT_INSTRUCTIONS,
            text=text,
            schema=DetectedToc,
            max_output_tokens=16000,
            label="detect-outline",
        )
    except llm.LLMError as e:
        log.warning("outline detection failed for %s: %s", Path(pdf_path).name, e)
        return []
    if not toc.found or not toc.entries:
        return []
    titled = [(" ".join(e.title.split()), e.printed_page.strip()) for e in toc.entries]
    offset = await _detect_page_offset(Path(pdf_path))
    if offset is None and not _has_real_labels(page_labels):
        offset = await asyncio.to_thread(_anchored_offset, Path(pdf_path), titled)
    starts = [resolve_printed_page(printed, page_labels, offset, page_count) for _, printed in titled]
    mids = await asyncio.to_thread(_mid_page_flags, Path(pdf_path), [t for t, _ in titled], starts)
    entries = [
        (max(1, e.level), title, start, mid)
        for e, (title, _), start, mid in zip(toc.entries, titled, starts, mids)
        if start is not None
    ]
    log.info("detected outline for %s: %d entries (offset %s)", Path(pdf_path).name, len(entries), offset)
    return build_outline(entries, page_count, page_labels)


def _mid_page_flags(pdf_path: Path, titles: list[str], starts: list[int | None]) -> list[bool]:
    with open_pdf(pdf_path) as doc:
        return [starts_mid_page(doc[s], None, t) if s is not None else False for t, s in zip(titles, starts)]


def _norm_words(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).lower()).split())


def _anchored_offset(pdf_path: Path, titled: list[tuple[str, str]], max_pages: int = 400) -> int | None:
    """Offset (pdf index − printed page) from where TOC titles appear as headings: the first page after the
    table of contents whose top lines contain the title. None unless two entries agree."""
    with open_pdf(pdf_path) as doc:
        n = min(max_pages, doc.page_count)
        tops = [_norm_words(" ".join(ln for ln in doc[i].get_text("text", sort=True).splitlines()[:6])) for i in range(n)]
        bodies = [_norm_words(doc[i].get_text("text")) for i in range(min(DETECT_PAGES, n))]
    keys = [(_norm_words(t), p) for t, p in titled if p.isdigit() and len(_norm_words(t)) >= 4]
    # pages listing several of the titles are the table of contents itself: headings come after them
    toc_pages = [i for i, body in enumerate(bodies) if sum(k in body for k, _ in keys[:30]) >= 3]
    first = toc_pages[-1] + 1 if toc_pages else 0
    votes: Counter[int] = Counter()
    for key, printed in keys[:12]:
        found = next((i for i in range(first, n) if key in tops[i]), None)
        if found is not None:
            votes[found - int(printed)] += 1
    if not votes:
        return None
    offset, count = votes.most_common(1)[0]
    return offset if count >= 2 else None


async def _first_pages_text(pdf_path: Path) -> tuple[list[str], int]:
    def work() -> tuple[list[str], int]:
        with open_pdf(pdf_path) as doc:
            n = min(DETECT_PAGES, doc.page_count)
            return [doc[i].get_text("text", sort=True) for i in range(n)], doc.page_count

    return await asyncio.to_thread(work)


async def _detect_page_offset(pdf_path: Path) -> int | None:
    def work() -> int | None:
        with open_pdf(pdf_path) as doc:
            return detect_page_offset(doc)

    return await asyncio.to_thread(work)


_PAGE_NUMBER_PATTERNS = (
    re.compile(r"^(?:page|p\.)\s*(\d{1,4})(?:\s*(?:of|/)\s*\d{1,4})?$", re.I),  # "Page 5 of 300" votes on 5
    re.compile(r"(\d{1,4})"),  # bare number (after stripping "- 5 -" decorations)
    re.compile(r"^(\d{1,4})\s*[|·•–—-]"),  # "5 | Chapter 1"
    re.compile(r"[|·•–—-]\s*(\d{1,4})$"),  # "Title · 5"
    re.compile(r"^(\d{1,4})\s+[A-Za-z]"),  # "12 Chapter 1 Vector Spaces"
    re.compile(r"[A-Za-z]\S*\s+(\d{1,4})$"),  # "Section 2A Span 31"
)
_DECORATION_RE = re.compile(r"^[\s\-–—\[\(|·•]+|[\s\-–—\]\)|·•]+$")


def _page_number(candidate: str) -> int | None:
    candidate = _DECORATION_RE.sub("", candidate)
    if len(candidate) > 80:  # running text, not a header/footer
        return None
    for i, pattern in enumerate(_PAGE_NUMBER_PATTERNS):
        m = pattern.fullmatch(candidate) if i == 1 else pattern.search(candidate)
        if m:
            return int(m.group(1))
    return None


def detect_page_offset(doc: pymupdf.Document, max_pages: int = 120) -> int | None:
    """Most common (pdf index − printed number) over pages whose first/last text lines carry a page number
    (a bare or decorated number, "Page N of M", or a running header/footer starting or ending with one).
    None without 3 agreeing pages."""
    votes: Counter[int] = Counter()
    for i in range(min(max_pages, doc.page_count)):
        lines = [ln.strip() for ln in doc[i].get_text("text", sort=True).splitlines() if ln.strip()]
        numbers = {n for n in (_page_number(c) for c in (lines[:2] + lines[-2:])) if n is not None}
        for n in numbers:  # a heading ending in a number must not hide the real footer
            votes[i - n] += 1
    if not votes:
        return None
    offset, count = votes.most_common(1)[0]
    return offset if count >= 3 else None


def _has_real_labels(labels: list[str]) -> bool:
    return any(lbl != str(i + 1) for i, lbl in enumerate(labels))


def resolve_printed_page(printed: str, labels: list[str], offset: int | None, page_count: int) -> int | None:
    """Printed page → PDF index: exact page label match first (labels from /PageLabels), else number + offset.
    None when neither is known (guessing 'printed page = index + 1' maps every entry to the wrong page)."""
    printed = printed.strip()
    if not printed:
        return None
    if _has_real_labels(labels):
        matches = [i for i, lbl in enumerate(labels) if lbl == printed]
        if matches:
            return matches[-1] if printed.isdigit() else matches[0]
    if not printed.isdigit() or offset is None:
        return None
    index = int(printed) + offset
    return index if 0 <= index < page_count else None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_page_png(pdf_path: Path, page_index: int, dpi: int) -> bytes:
    with open_pdf(pdf_path) as doc:
        if not 0 <= page_index < doc.page_count:
            raise IndexError(f"page {page_index} out of range (0..{doc.page_count - 1})")
        return doc[page_index].get_pixmap(dpi=dpi, alpha=False).tobytes("png")


def render_page_zoom_png(pdf_path: Path, page_index: int, zoom: float) -> bytes:
    with open_pdf(pdf_path) as doc:
        return doc[page_index].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")


OWN_TEXT_SHARE = 0.9  # a text line this much inside a figure's bbox belongs to the figure (its label)


def render_clip_png(pdf_path: Path, page_index: int, clip: Rect4, zoom: float, figure: Rect4 | None = None) -> bytes:
    """Render a page region. With `figure` (the figure's bbox inside the clip), text lines that reach into the clip
    but do not belong to the figure (a display equation right above it, the next paragraph) are removed from a
    temporary copy of the page first, together with line art lying entirely inside them (fraction bars, radical
    vinculums). The figure's own labels — lines mostly inside its bbox — are kept, and so is every stroke that merely
    passes through such a text row (a vertex of the figure inside the equation's line band). Figure bboxes already
    contain their labels in full (see _absorb_labels), so a line counts as the figure's own only when it lies
    essentially inside the bbox."""
    with open_pdf(pdf_path) as doc:
        page = doc[page_index]
        rect = pymupdf.Rect(clip) & page.rect
        matrix = pymupdf.Matrix(zoom, zoom)
        if figure is not None:
            fig = pymupdf.Rect(figure)
            foreign = [
                ln for ln in _text_lines(page) if ln.rect.intersects(rect) and inside_fraction(ln.rect, fig) < OWN_TEXT_SHARE
            ]
            if foreign:
                try:  # whole rows: a bar between two pieces of one equation is covered too
                    return _render_without(doc, page_index, [r.rect for r in _text_rows(foreign)], rect, matrix)
                except Exception as e:  # noqa: BLE001 — an unusual page: the plain crop is still useful
                    log.warning("could not mask the text around a figure on page %d: %s", page_index, e)
        return page.get_pixmap(matrix=matrix, clip=rect, alpha=False).tobytes("png")


def _render_without(
    doc: pymupdf.Document, page_index: int, rects: Sequence[pymupdf.Rect], clip: pymupdf.Rect, matrix: pymupdf.Matrix
) -> bytes:
    """Render `clip` of a copy of the page with the text in `rects` (and line art covered by them) redacted."""
    tmp = pymupdf.open()
    try:
        tmp.insert_pdf(doc, from_page=page_index, to_page=page_index)
        copy = tmp[0]
        for r in rects:
            copy.add_redact_annot(r + (-0.5, -0.5, 0.5, 0.5), fill=False)  # no fill: never paint over the figure
        copy.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
        return copy.get_pixmap(matrix=matrix, clip=clip, alpha=False).tobytes("png")
    finally:
        tmp.close()


def page_size(pdf_path: Path, page_index: int) -> tuple[float, float]:
    with open_pdf(pdf_path) as doc:
        rect = doc[page_index].rect
        return rect.width, rect.height


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cached_page_png(book_id: str, pdf_path: Path, page_index: int, dpi: int) -> Path:
    """Render once and cache at settings.books_dir/<book_id>/pages/<page>@<dpi>.png."""
    path = settings.books_dir / book_id / "pages" / f"{page_index}@{dpi}.png"
    if not path.exists():
        write_atomic(path, render_page_png(pdf_path, page_index, dpi))
    return path


# ---------------------------------------------------------------------------
# Figure candidates (SPEC §3.2)
# ---------------------------------------------------------------------------

MIN_FIGURE_AREA = 400.0  # pt²
MIN_RULE_THICKNESS = 4.0  # pt
MIN_RASTER_SIDE = 40.0  # pt
GROUP_GAP = 60.0  # pt: max gap between fragments of one drawing (e.g. three separate arrows)
SMALL_FRAGMENT_AREA = 4000.0  # pt²: only fragments this small are grouped across larger gaps
LABEL_MARGIN = 6.0  # pt: text lines this close and mostly inside a figure are its labels
MAX_LABEL_CHARS = 16
GLYPH_LABEL_GAP = 8.0  # pt: tiny drawn glyph clusters (tick labels drawn as outlines) this close join a figure
ROW_GAP = 4.0  # pt: text lines closer than this side by side form one visual row (a split-up equation)
_RELATION_RE = re.compile(r"[=<>≤≥≠∈∉⊆⊂≈≡]")
FIGURE_CLASSES = {"Picture", "Chart"}


@dataclass
class FigureCandidate:
    bbox: Rect4  # PDF points
    sources: list[str]  # "vector" | "raster" | "nemotron"
    caption: str = ""
    label: str = ""  # "M1", "M2", ... in reading order

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "bbox": [round(v, 2) for v in self.bbox], "sources": self.sources, "caption": self.caption}


@dataclass
class FigureScan:
    candidates: list[FigureCandidate]
    rejected: list[tuple[Rect4, str]] = field(default_factory=list)  # (bbox, reason), for logs and tests


@dataclass
class _TextLine:
    rect: pymupdf.Rect
    text: str


def figure_candidates(pdf_path: Path, page_index: int, blocks: Sequence[Block] = ()) -> FigureScan:
    with open_pdf(pdf_path) as doc:
        return scan_figures(doc[page_index], blocks)


def scan_figures(page: pymupdf.Page, blocks: Sequence[Block] = ()) -> FigureScan:
    """Vector drawing clusters + raster images (+ Nemotron Picture blocks), merged and labelled M1..Mk."""
    page_rect = page.rect
    lines = _text_lines(page)
    rejected: list[tuple[Rect4, str]] = []

    paths = [p for p in page.get_drawings() if _visible(p)]
    boxes = [p for p in paths if p.get("fill") is not None and _is_box_shape(p) and _has_text_inside(p["rect"], lines)]
    for p in boxes:
        rejected.append((_r4(p["rect"] & page_rect), "box"))
    box_ids = {id(p) for p in boxes}
    drawings = [p for p in paths if id(p) not in box_ids]

    vector: list[pymupdf.Rect] = []
    label_bits: list[pymupdf.Rect] = []  # tiny rejected clusters: tick marks / glyph-outline labels near figures
    if drawings:
        for rect in page.cluster_drawings(drawings=drawings):
            rect = rect & page_rect
            reason = _cluster_rejection(rect, _members(drawings, rect), page_rect, lines)
            if reason:
                rejected.append((_r4(rect), reason))
                if reason in ("frame", "glyphs") and rect.width * rect.height < MIN_FIGURE_AREA:
                    label_bits.append(rect)
            else:
                vector.append(rect)

    candidates: list[FigureCandidate] = []
    for rect in _group_fragments(vector, lines):
        reason = _size_rejection(rect) or _texty_rejection(rect, lines)
        if reason:
            rejected.append((_r4(rect), reason))
        else:
            candidates.append(FigureCandidate(bbox=_r4(rect), sources=["vector"]))

    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & page_rect
        if rect.width >= MIN_RASTER_SIDE and rect.height >= MIN_RASTER_SIDE:
            candidates.append(FigureCandidate(bbox=_r4(rect), sources=["raster"]))
        elif not rect.is_empty:
            rejected.append((_r4(rect), "small-image"))

    if blocks:
        candidates = _merge_nemotron(candidates, blocks, page_rect, lines, rejected)
    candidates = _merge_overlapping(candidates)
    for cand in candidates:
        rect = pymupdf.Rect(cand.bbox)
        if "vector" in cand.sources:
            rect = _absorb_glyph_labels(rect, label_bits, lines)
        cand.bbox = _r4(_absorb_labels(rect, lines) & page_rect)
    if blocks:
        _attach_captions(candidates, blocks, page_rect)
    candidates = _reading_order(candidates)
    for i, cand in enumerate(candidates, start=1):
        cand.label = f"M{i}"
    return FigureScan(candidates=candidates, rejected=rejected)


def _reading_order(cands: list[FigureCandidate]) -> list[FigureCandidate]:
    """Rows of vertically overlapping candidates top to bottom, each row left to right."""
    rows: list[list[FigureCandidate]] = []
    for cand in sorted(cands, key=lambda c: c.bbox[1]):
        row = rows[-1] if rows else None
        if row is not None:
            top, bottom = min(c.bbox[1] for c in row), max(c.bbox[3] for c in row)
            overlap = min(bottom, cand.bbox[3]) - max(top, cand.bbox[1])
            if overlap >= 0.5 * min(bottom - top, cand.bbox[3] - cand.bbox[1]):
                row.append(cand)
                continue
        rows.append([cand])
    return [c for row in rows for c in sorted(row, key=lambda c: c.bbox[0])]


def _union(rects: Sequence[pymupdf.Rect]) -> pymupdf.Rect:
    """Bounding box of all rects, including degenerate ones (horizontal/vertical lines) that `|` ignores."""
    return pymupdf.Rect(
        min(r.x0 for r in rects), min(r.y0 for r in rects), max(r.x1 for r in rects), max(r.y1 for r in rects)
    )


def _r4(rect: pymupdf.Rect) -> Rect4:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _text_lines(page: pymupdf.Page) -> list[_TextLine]:
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    out: list[_TextLine] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", [])).strip()
            if text:
                out.append(_TextLine(pymupdf.Rect(line["bbox"]), text))
    return out


def _visible(path: dict[str, Any]) -> bool:
    fill, stroke = path.get("fill"), path.get("color")
    if fill is None and stroke is None:
        return False
    if (path.get("fill_opacity") == 0 or fill is None) and (path.get("stroke_opacity") == 0 or stroke is None):
        return False
    # white fill without outline: page background / knockout
    return not (stroke is None and fill is not None and min(fill) >= 0.98)


def _axis_aligned(p: pymupdf.Point, q: pymupdf.Point) -> bool:
    return abs(p.x - q.x) < 0.5 or abs(p.y - q.y) < 0.5


def _is_box_shape(path: dict[str, Any]) -> bool:
    """(Rounded) rectangle or rule: only axis-aligned lines, rects, quads and small corner curves."""
    for item in path["items"]:
        kind = item[0]
        if kind in ("re", "qu"):
            continue
        if kind == "l":
            if not _axis_aligned(item[1], item[2]):
                return False
        elif kind == "c":
            p0, p3 = item[1], item[4]
            if max(abs(p0.x - p3.x), abs(p0.y - p3.y)) > 20:
                return False
        else:
            return False
    return True


def _has_text_inside(rect: pymupdf.Rect, lines: Sequence[_TextLine], min_chars: int = 3) -> bool:
    if rect.width < 40 or rect.height < 12:
        return False
    chars = sum(len(ln.text) for ln in lines if inside_fraction(ln.rect, rect) >= 0.6)
    return chars >= min_chars


def inside_fraction(inner: pymupdf.Rect, outer: pymupdf.Rect) -> float:
    """Share of `inner`'s area that lies inside `outer` (0-1)."""
    area = inner.width * inner.height
    if area <= 0:
        return 1.0 if outer.contains(inner.tl) and outer.contains(inner.br) else 0.0
    inter = inner & outer
    return (inter.width * inter.height) / area if not inter.is_empty else 0.0


def _members(drawings: Sequence[dict[str, Any]], rect: pymupdf.Rect) -> list[dict[str, Any]]:
    grown = rect + (-1, -1, 1, 1)
    return [p for p in drawings if grown.contains(p["rect"])]


def _cluster_rejection(
    rect: pymupdf.Rect, members: Sequence[dict[str, Any]], page_rect: pymupdf.Rect, lines: Sequence[_TextLine] = ()
) -> str | None:
    if rect.is_empty or rect.width < MIN_RULE_THICKNESS or rect.height < MIN_RULE_THICKNESS:
        return "thin"
    if rect.width >= 0.9 * page_rect.width and rect.height >= 0.9 * page_rect.height:
        return "full-page"
    if not members:
        return None
    cluster_area = rect.width * rect.height
    for p in members:
        r = p["rect"] & rect
        if p.get("fill") is not None and _is_box_shape(p) and r.width * r.height >= 0.7 * cluster_area:
            return "box"
    glyphs = all(p.get("color") is None and max(p["rect"].width, p["rect"].height) <= 14 for p in members)
    if glyphs and rect.width <= 14 and rect.height <= 14:
        return "glyphs"  # a filled outline glyph (tick label), not a frame
    # rules/rectangles only: a table or example frame when it encloses text and has no coloured fills;
    # otherwise a rectilinear drawing (bar chart, histogram, grid, commutative square)
    if all(_is_box_shape(p) for p in members) and _frames_text(rect, members, lines):
        return "frame"
    if glyphs:
        return "glyphs"
    return None


def _frames_text(rect: pymupdf.Rect, members: Sequence[dict[str, Any]], lines: Sequence[_TextLine], min_chars: int = 3) -> bool:
    filled = [p for p in members if p.get("fill") is not None and min(p["fill"]) < 0.98 and not _is_rule(p)]
    empty = [p for p in filled if not any(inside_fraction(ln.rect, p["rect"]) >= 0.6 for ln in lines)]
    if len(empty) * 2 > len(filled) > 0:
        return False  # mostly empty coloured/grey rectangles: bars of a chart, not shaded table cells
    return sum(len(ln.text) for ln in lines if inside_fraction(ln.rect, rect) >= 0.6) >= min_chars


def _is_rule(path: dict[str, Any]) -> bool:
    r = path["rect"]
    return min(r.width, r.height) < MIN_RULE_THICKNESS


def _size_rejection(rect: pymupdf.Rect) -> str | None:
    if rect.width * rect.height < MIN_FIGURE_AREA or rect.width < 12 or rect.height < 12:
        return "tiny"
    return None


def _texty_rejection(rect: pymupdf.Rect, lines: Sequence[_TextLine]) -> str | None:
    """Drawings around running text (tables with rules, unfilled boxes) are not figures."""
    long_lines = [ln for ln in lines if len(ln.text) >= 25 and inside_fraction(ln.rect, rect) >= 0.6]
    return "text" if len(long_lines) >= 3 else None


def _gap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return max(dx, dy)


def _swallows_text(union: pymupdf.Rect, parts: Sequence[pymupdf.Rect], lines: Sequence[_TextLine]) -> bool:
    """True if merging would pull in body text that belongs to neither part (labels are allowed)."""
    for ln in _text_rows(lines):
        if len(ln.text) <= MAX_LABEL_CHARS:
            continue
        if inside_fraction(ln.rect, union) >= 0.3 and not any(inside_fraction(ln.rect, p) >= 0.3 for p in parts):
            return True
    return False


def _text_rows(lines: Sequence[_TextLine]) -> list[_TextLine]:
    """Lines side by side on one visual row (vertical overlap ≥ 50%, horizontal gap ≤ ROW_GAP) joined: a display
    equation split into several text lines is judged as a whole, not as a set of short labels."""
    rows: list[_TextLine] = []
    for ln in sorted(lines, key=lambda ln: (ln.rect.y0, ln.rect.x0)):
        for i, row in enumerate(rows):
            overlap = min(row.rect.y1, ln.rect.y1) - max(row.rect.y0, ln.rect.y0)
            gap = max(row.rect.x0, ln.rect.x0) - min(row.rect.x1, ln.rect.x1)
            if overlap >= 0.5 * min(row.rect.height, ln.rect.height) and gap <= ROW_GAP:
                first, second = (row, ln) if row.rect.x0 <= ln.rect.x0 else (ln, row)
                rows[i] = _TextLine(_union([row.rect, ln.rect]), f"{first.text} {second.text}")
                break
        else:
            rows.append(_TextLine(pymupdf.Rect(ln.rect), ln.text))
    return rows


def _group_fragments(rects: list[pymupdf.Rect], lines: Sequence[_TextLine]) -> list[pymupdf.Rect]:
    """Join nearby drawing fragments of one figure (e.g. separate arrows) unless text lies between them."""
    groups = [pymupdf.Rect(r) for r in rects]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = groups[i], groups[j]
                gap = _gap(a, b)
                small = min(a.width * a.height, b.width * b.height) < SMALL_FRAGMENT_AREA
                if gap > GROUP_GAP or (gap > 12 and not small):
                    continue
                union = _union([a, b])
                if _swallows_text(union, [a, b], lines):
                    continue
                groups[i] = union
                del groups[j]
                merged = True
                break
            if merged:
                break
    return groups


def _iou(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = a & b
    if inter.is_empty:
        return 0.0
    ia = inter.width * inter.height
    return ia / (a.width * a.height + b.width * b.height - ia)


def _overlaps(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    return _iou(a, b) > 0.3 or inside_fraction(a, b) >= 0.8 or inside_fraction(b, a) >= 0.8


def _merge_overlapping(cands: list[FigureCandidate]) -> list[FigureCandidate]:
    out: list[FigureCandidate] = []
    for cand in cands:
        rect = pymupdf.Rect(cand.bbox)
        sources = list(cand.sources)
        caption = cand.caption
        changed = True
        while changed:
            changed = False
            for other in out:
                orect = pymupdf.Rect(other.bbox)
                if _overlaps(rect, orect):
                    rect = _union([rect, orect])
                    sources += [s for s in other.sources if s not in sources]
                    caption = caption or other.caption
                    out.remove(other)
                    changed = True
                    break
        out.append(FigureCandidate(bbox=_r4(rect), sources=sources, caption=caption))
    return out


def _block_rect(block: Block, page_rect: pymupdf.Rect) -> pymupdf.Rect:
    x0, y0, x1, y1 = block.bbox
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return pymupdf.Rect(x0 * page_rect.width, y0 * page_rect.height, x1 * page_rect.width, y1 * page_rect.height)


def _merge_nemotron(
    cands: list[FigureCandidate],
    blocks: Sequence[Block],
    page_rect: pymupdf.Rect,
    lines: Sequence[_TextLine],
    rejected: list[tuple[Rect4, str]],
) -> list[FigureCandidate]:
    """Picture blocks: group the drawing fragments they cover (tighter than Nemotron's box), or add new ones.
    Several complete figures under one wide Picture box stay separate."""
    cands = list(cands)
    box_rects = [pymupdf.Rect(r) for r, reason in rejected if reason in ("box", "frame")]
    for block in blocks:
        if block.type not in FIGURE_CLASSES:
            continue
        pic = _block_rect(block, page_rect) & page_rect
        if pic.is_empty or pic.width * pic.height < MIN_FIGURE_AREA:
            continue
        covered = [c for c in cands if inside_fraction(pymupdf.Rect(c.bbox), pic) >= 0.5]
        if len(covered) >= 2:
            parts = [pymupdf.Rect(c.bbox) for c in covered]
            union = _union(parts)
            fragments = any(r.width * r.height < SMALL_FRAGMENT_AREA for r in parts)
            if fragments and not _swallows_text(union, parts, lines):
                for c in covered:
                    cands.remove(c)
                sources = sorted({s for c in covered for s in c.sources} | {"nemotron"})
                cands.append(FigureCandidate(bbox=_r4(union), sources=sources))
                continue
        if covered:
            for c in covered:
                if "nemotron" not in c.sources:
                    c.sources.append("nemotron")
            continue
        if any(inside_fraction(pic, b) >= 0.6 for b in box_rects):
            rejected.append((_r4(pic), "nemotron-in-box"))
            continue
        reason = _texty_rejection(pic, lines)
        if reason:
            rejected.append((_r4(pic), f"nemotron-{reason}"))
            continue
        cands.append(FigureCandidate(bbox=_r4(pic), sources=["nemotron"]))
    return cands


def _absorb_labels(rect: pymupdf.Rect, lines: Sequence[_TextLine]) -> pymupdf.Rect:
    """Extend a figure to short text labels drawn at its edge (axis names, point labels). Text is judged by
    visual rows, and a relation (an equation such as 'a² + b² = 1') lying mostly outside the drawing is text
    beside the figure, not a label."""
    grown = rect + (-LABEL_MARGIN, -LABEL_MARGIN, LABEL_MARGIN, LABEL_MARGIN)
    out = pymupdf.Rect(rect)
    for row in _text_rows(lines):
        if len(row.text) > MAX_LABEL_CHARS or inside_fraction(row.rect, grown) < 0.6:
            continue
        if _RELATION_RE.search(row.text) and inside_fraction(row.rect, rect) < 0.5:
            continue
        out = _union([out, row.rect])
    return out


def _absorb_glyph_labels(rect: pymupdf.Rect, bits: list[pymupdf.Rect], lines: Sequence[_TextLine]) -> pymupdf.Rect:
    """Pull in tiny drawn clusters (axis tick numbers drawn as glyph outlines, tick marks) next to a figure;
    repeated until nothing changes so multi-glyph labels chain in. Absorbed bits leave the pool."""
    out = pymupdf.Rect(rect)
    changed = True
    while changed:
        changed = False
        for bit in list(bits):
            if _gap(out, bit) > GLYPH_LABEL_GAP:
                continue
            union = _union([out, bit])
            if _swallows_text(union, [out, bit], lines):
                continue
            out = union
            bits.remove(bit)
            changed = True
    return out


def _attach_captions(cands: list[FigureCandidate], blocks: Sequence[Block], page_rect: pymupdf.Rect) -> None:
    """Nemotron Caption blocks → nearest figure below (or beside) it."""
    for block in blocks:
        if block.type != "Caption" or not block.text.strip():
            continue
        cap = _block_rect(block, page_rect)
        best: tuple[float, FigureCandidate] | None = None
        for cand in cands:
            fig = pymupdf.Rect(cand.bbox)
            h_overlap = min(fig.x1, cap.x1) - max(fig.x0, cap.x0)
            v_overlap = min(fig.y1, cap.y1) - max(fig.y0, cap.y0)
            if h_overlap > 0 and -4 <= cap.y0 - fig.y1 <= 30:
                dist = cap.y0 - fig.y1
            elif v_overlap > 0 and 0 <= min(abs(cap.x0 - fig.x1), abs(fig.x0 - cap.x1)) <= 20:
                dist = 20 + min(abs(cap.x0 - fig.x1), abs(fig.x0 - cap.x1))
            else:
                continue
            if best is None or dist < best[0]:
                best = (dist, cand)
        if best and not best[1].caption:
            best[1].caption = _plain_caption(block.text)


def _plain_caption(text: str) -> str:
    """Nemotron markdown → readable caption: emphasis markers dropped (it writes sub/superscripts as HTML tags),
    <sup>/<sub> as ^/_, other tags removed."""
    text = text.replace("_", "").replace("*", "")
    text = re.sub(r"<sup>(.*?)</sup>", r"^\1", text)
    text = re.sub(r"<sub>(.*?)</sub>", r"_\1", text)
    return " ".join(re.sub(r"<[^>]+>", "", text).split())


def snap_to_content(page: pymupdf.Page, bbox: Rect4) -> Rect4:
    """Tighten a rough figure box (e.g. estimated by a vision model) to the drawings/images inside it."""
    rough = pymupdf.Rect(bbox)
    grown = rough + (-0.1 * rough.width, -0.1 * rough.height, 0.1 * rough.width, 0.1 * rough.height)
    lines = _text_lines(page)
    parts = [
        p["rect"]
        for p in page.get_drawings()
        if _visible(p)
        and inside_fraction(p["rect"], grown) >= 0.6
        and not (p.get("fill") is not None and _is_box_shape(p) and _has_text_inside(p["rect"], lines))
    ]
    parts += [pymupdf.Rect(info["bbox"]) for info in page.get_image_info() if inside_fraction(pymupdf.Rect(info["bbox"]), grown) >= 0.6]
    if not parts:
        return bbox
    union = _absorb_labels(_union(parts), lines) & page.rect
    if union.width * union.height < MIN_FIGURE_AREA:
        return bbox
    return _r4(union)
