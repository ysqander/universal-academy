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
from collections import Counter, OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel, Field

from .. import llm
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
        toc = doc.get_toc(simple=True)
        page_count = doc.page_count
    outline = outline_from_toc(toc, page_count, labels) if len(toc) >= 2 else []
    title = _clean_meta_title(meta.get("title") or "") or prettify_filename(display_name or pdf_path.name)
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


def outline_from_toc(toc: Sequence[Sequence[Any]], page_count: int, labels: list[str]) -> list[OutlineNode]:
    """Build the OutlineNode tree from get_toc() rows [level, title, 1-based page]."""
    flat: list[tuple[int, str, int | None]] = []
    for row in toc:
        level, title, page = int(row[0]), " ".join(str(row[1]).split()), int(row[2])
        flat.append((max(1, level), title or "Untitled", page - 1 if page >= 1 else None))
    return build_outline(flat, page_count, labels)


def build_outline(entries: Sequence[tuple[int, str, int | None]], page_count: int, labels: list[str]) -> list[OutlineNode]:
    """entries: (level, title, 0-based start or None) in TOC order. end = next start at same/higher level − 1,
    clamped to the parent's end; the last top-level node ends at the last page. Ids n0..nN in order."""
    if not entries or page_count <= 0:
        return []
    last = page_count - 1
    starts: list[int] = []
    for i, (_, _, start) in enumerate(entries):
        if start is None:  # no destination: take the next known start (or the previous one)
            start = next((s for _, _, s in entries[i + 1 :] if s is not None), starts[-1] if starts else 0)
        starts.append(min(max(start, 0), last))

    roots: list[OutlineNode] = []
    stack: list[OutlineNode] = []
    for i, (level, title, _) in enumerate(entries):
        nxt = next((starts[j] for j in range(i + 1, len(entries)) if entries[j][0] <= level), None)
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        end = (nxt - 1) if nxt is not None else last
        if parent is not None:
            end = min(end, parent.end_page)
        end = max(starts[i], end)
        node = OutlineNode(
            id=f"n{i}",
            title=title,
            level=level,
            start_page=starts[i],
            end_page=end,
            label_start=_label(labels, starts[i]),
            label_end=_label(labels, end),
        )
        (parent.children if parent else roots).append(node)
        stack.append(node)
    return roots


def _label(labels: list[str], page: int) -> str:
    return labels[page] if 0 <= page < len(labels) and labels[page] else str(page + 1)


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


_DETECT_INSTRUCTIONS = """You read the extracted text of the first pages of a book and reconstruct its table of contents.
Return every chapter and section listed in the printed table of contents, in order, with its level (1 = chapter or
part, 2 = section, 3 = subsection) and the page number exactly as printed. Keep chapter/section numbers that are part
of the title (e.g. '1A Vectors', 'Chapter 2: Limits'). Skip dotted leaders. Do not invent entries: if the text has no
table of contents, return found=false and no entries."""


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
            instructions=_DETECT_INSTRUCTIONS,
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
    offset = await _detect_page_offset(Path(pdf_path))
    entries = [
        (max(1, e.level), " ".join(e.title.split()), resolve_printed_page(e.printed_page, page_labels, offset, page_count))
        for e in toc.entries
    ]
    entries = [e for e in entries if e[2] is not None]
    log.info("detected outline for %s: %d entries (offset %s)", Path(pdf_path).name, len(entries), offset)
    return build_outline(entries, page_count, page_labels)


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


def detect_page_offset(doc: pymupdf.Document, max_pages: int = 120) -> int | None:
    """Most common (pdf index − printed number) over pages whose first/last text lines carry a page number
    (a bare number, or a running header/footer starting or ending with one). None without 3 agreeing pages."""
    votes: Counter[int] = Counter()
    for i in range(min(max_pages, doc.page_count)):
        lines = [ln.strip() for ln in doc[i].get_text("text", sort=True).splitlines() if ln.strip()]
        for candidate in (lines[:2] + lines[-2:]) if lines else []:
            m = (
                re.fullmatch(r"(\d{1,4})", candidate)
                or re.match(r"^(\d{1,4})\s+[A-Za-z]", candidate)
                or re.search(r"[A-Za-z]\S*\s+(\d{1,4})$", candidate)
            )
            if m:
                votes[i - int(m.group(1))] += 1
                break
    if not votes:
        return None
    offset, count = votes.most_common(1)[0]
    return offset if count >= 3 else None


def resolve_printed_page(printed: str, labels: list[str], offset: int | None, page_count: int) -> int | None:
    """Printed page → PDF index: exact page label match first (labels from /PageLabels), else number + offset."""
    printed = printed.strip()
    if not printed:
        return None
    has_real_labels = any(lbl != str(i + 1) for i, lbl in enumerate(labels))
    if has_real_labels:
        matches = [i for i, lbl in enumerate(labels) if lbl == printed]
        if matches:
            return matches[-1] if printed.isdigit() else matches[0]
    if not printed.isdigit():
        return None
    index = int(printed) + (offset if offset is not None else -1)
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


def render_clip_png(pdf_path: Path, page_index: int, clip: Rect4, zoom: float) -> bytes:
    with open_pdf(pdf_path) as doc:
        page = doc[page_index]
        rect = pymupdf.Rect(clip) & page.rect
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, alpha=False).tobytes("png")


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
    if drawings:
        for rect in page.cluster_drawings(drawings=drawings):
            rect = rect & page_rect
            reason = _cluster_rejection(rect, _members(drawings, rect), page_rect)
            if reason:
                rejected.append((_r4(rect), reason))
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
        cand.bbox = _r4(_absorb_labels(pymupdf.Rect(cand.bbox), lines) & page_rect)
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


def _cluster_rejection(rect: pymupdf.Rect, members: Sequence[dict[str, Any]], page_rect: pymupdf.Rect) -> str | None:
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
    if all(_is_box_shape(p) for p in members):
        return "frame"
    if all(p.get("color") is None and max(p["rect"].width, p["rect"].height) <= 14 for p in members):
        return "glyphs"
    return None


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
    for ln in lines:
        if len(ln.text) <= MAX_LABEL_CHARS:
            continue
        if inside_fraction(ln.rect, union) >= 0.3 and not any(inside_fraction(ln.rect, p) >= 0.3 for p in parts):
            return True
    return False


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
    """Extend a figure to short text labels drawn at its edge (axis names, point labels)."""
    grown = rect + (-LABEL_MARGIN, -LABEL_MARGIN, LABEL_MARGIN, LABEL_MARGIN)
    out = pymupdf.Rect(rect)
    for ln in lines:
        if len(ln.text) <= MAX_LABEL_CHARS and inside_fraction(ln.rect, grown) >= 0.6:
            out |= ln.rect
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
