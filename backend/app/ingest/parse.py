"""Page parsing (SPEC §3.3): render → figure candidates (+ Nemotron layout) → set-of-mark overlay →
OpenAI vision transcription → figure crops + placeholder rewriting.

parse_page() is idempotent for a given work_dir (files are overwritten) and never raises for a failed vision
call: it falls back to the PDF text layer (engine "textlayer") and records the error in `uncertain`.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from .. import llm
from ..config import settings
from ..llm_schemas import FigureRef, PageTranscription
from . import nemotron, pdf
from .prompts import TRANSCRIBE_INSTRUCTIONS

log = logging.getLogger("ua.ingest")

TRANSCRIBE_ATTEMPTS = 2
MIN_COVERAGE = 0.8  # below: retry the vision call (degenerate / truncated output)
MIN_USABLE_COVERAGE = 0.5  # below even after the retry: use the text layer instead
MIN_WORDS_FOR_COVERAGE = 15
BLOCK_MIN_WORDS = 5  # paragraphs shorter than this are not checked individually
BLOCK_MIN_COVERAGE = 0.6  # a checked paragraph with fewer of its words in the transcription counts as missing
LAYOUT_HINT_MAX_WORDS = 50  # Nemotron's text goes into the prompt only when the text layer is (nearly) empty
FIGURE_PADDING_PT = 4.0
FIGURE_ZOOM = 3.0
MIN_NEW_FIGURE_PT = 20.0
_HEADER_FOOTER_BAND = 0.07  # fraction of page height treated as running header / footer zone
_NEMOTRON_HINT_CHARS = 8000
_PLACEHOLDER_RE = re.compile(r"\[\[\s*figure\s*:\s*([A-Za-z0-9_.-]+)\s*\]\]")


@dataclass
class ParsedFigure:
    id: str  # "p21-f1"
    page_index: int
    bbox: list[float]  # [x0, y0, x1, y1] in PDF points
    caption: str
    description: str
    image_path: Path


@dataclass
class ParsedPage:
    page_index: int
    label: str
    markdown: str
    figures: list[ParsedFigure]
    items: list[dict[str, Any]]  # BoxedItem dicts
    uncertain: list[str]
    is_exercise_page: bool
    engine: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class _TextBlock:
    rect: tuple[float, float, float, float]
    text: str


@dataclass
class _Accepted:
    key: str  # "M2" / "new1"
    bbox: tuple[float, float, float, float]
    caption: str
    description: str


async def parse_page(pdf_path: Path, page_index: int, label: str, work_dir: Path) -> ParsedPage:
    pdf_path, work_dir = Path(pdf_path), Path(work_dir)
    raw: dict[str, Any] = {}

    page_png_path = work_dir / "pages" / f"{page_index}.png"
    page_png = await asyncio.to_thread(_ensure_page_render, pdf_path, page_index, page_png_path)
    width_pt, height_pt = await asyncio.to_thread(pdf.page_size, pdf_path, page_index)

    blocks, nemotron_engine = await _nemotron_blocks(pdf_path, page_index, width_pt, height_pt, raw)
    scan = await asyncio.to_thread(pdf.figure_candidates, pdf_path, page_index, blocks)
    raw["candidates"] = [c.to_dict() for c in scan.candidates]
    raw["rejected_candidates"] = [{"bbox": [round(v, 1) for v in b], "reason": r} for b, r in scan.rejected]
    text_blocks = await asyncio.to_thread(_text_blocks, pdf_path, page_index)

    if settings.fake_llm:
        return await _text_layer_page(pdf_path, page_index, label, work_dir, scan, text_blocks, "fake", [], raw)
    if settings.parse_engine == "nemotron" and blocks:
        return await _nemotron_page(pdf_path, page_index, label, work_dir, scan, blocks, height_pt, raw)

    images = [page_png]
    if scan.candidates:
        marked = draw_marks(page_png, scan.candidates, width_pt, height_pt)
        await asyncio.to_thread(pdf.write_atomic, work_dir / "marks" / f"{page_index}.png", marked)
        images.append(marked)
    prompt = _user_prompt(page_index, label, scan.candidates, text_blocks, blocks, width_pt, height_pt)
    try:
        result, quality, attempts = await _transcribe(images, prompt, page_index, text_blocks)
    except llm.LLMError as e:
        log.warning("vision transcription failed for page %d: %s — using the text layer", page_index, e)
        raw["vision_error"] = str(e)
        uncertain = [f"Vision transcription failed ({e}); this page shows the raw PDF text layer, math may be garbled."]
        return await _text_layer_page(
            pdf_path, page_index, label, work_dir, scan, text_blocks, "textlayer", uncertain, raw
        )

    raw["transcription"] = result.model_dump()
    raw["coverage"] = quality.coverage
    raw["missing_blocks"] = quality.missing
    raw["lost_formulas"] = quality.lost
    raw["attempts"] = attempts
    if quality.coverage is not None and quality.coverage < MIN_USABLE_COVERAGE:
        log.warning("page %d: transcription unusable (coverage %.0f%%) — using the text layer", page_index, quality.coverage * 100)
        uncertain = [
            f"The vision transcription was incomplete ({quality.coverage:.0%} of the words); "
            "this page shows the raw PDF text layer."
        ]
        return await _text_layer_page(
            pdf_path, page_index, label, work_dir, scan, text_blocks, "textlayer", uncertain, raw
        )
    engine = f"openai:{settings.model_vision}" + (f"+{nemotron_engine}" if nemotron_engine else "")
    page = await _assemble(pdf_path, page_index, label, work_dir, scan, result, width_pt, height_pt, engine, raw)
    if quality.coverage is not None and quality.coverage < MIN_COVERAGE:
        page.uncertain.append(f"The transcription may be incomplete: only {quality.coverage:.0%} of the page's words were found.")
    page.uncertain += [f"Possibly missing from the transcription: \u201c{text[:80]}…\u201d" for text in quality.missing]
    if quality.lost:
        page.uncertain.append(
            f"{len(quality.lost)} formula(s) could not be read and are shown as {LOST_MARK}, e.g. near "
            f"\u201c{quality.lost[0]}\u201d; check the page image."
        )
    page.uncertain += [f"Unbalanced math delimiter ($) in: \u201c{line[:100]}\u201d" for line in unbalanced_math_lines(page.markdown)]
    return page


@dataclass
class _Quality:
    coverage: float | None  # share of text-layer words found; None if the page has too little text to judge
    missing: list[str]  # text-layer paragraphs (margin notes, ...) that are mostly absent
    lost: list[str] = field(default_factory=list)  # contexts of formulas destroyed by escape glitches (LOST_MARK)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.lost and (self.coverage is None or self.coverage >= MIN_COVERAGE)

    def rank(self) -> tuple[int, int, float]:
        return (-len(self.missing), -len(self.lost), self.coverage if self.coverage is not None else 1.0)


def _escape_reminder(lost: Sequence[str]) -> str:
    """Appended to the prompt of the retry when the first answer lost formulas to JSON escape glitches."""
    examples = "; ".join(f"“{c}”" for c in lost[:6])
    return (
        f"\n\nIMPORTANT: in your previous answer for this page {len(lost)} formula(s) were destroyed (shown here as "
        f"{LOST_MARK}): {examples}. This happens when a LaTeX backslash is not escaped inside the JSON string "
        "(\\m, \\{ or \\b become control characters). Your answer is JSON: write every backslash of LaTeX as two "
        'backslashes in the JSON source, e.g. "$\\\\mathbf{R}$", "$\\\\{0, 1\\\\}$", "$\\\\beta$". Never output '
        "control characters (\\u0000-\\u001f, \\u007f) or terminal/ANSI escape codes. Transcribe those formulas "
        "from the image again."
    )


async def _transcribe(
    images: list[bytes], prompt: str, page_index: int, text_blocks: Sequence[_TextBlock]
) -> tuple[PageTranscription, _Quality, int]:
    """Vision call with a completeness check against the text layer; one retry (with an explicit reminder about
    JSON escaping when formulas were lost), the better attempt wins. Raises llm.LLMError only if the first
    attempt fails."""
    best: tuple[PageTranscription, _Quality] | None = None
    attempts = 0
    text = prompt
    for attempt in range(TRANSCRIBE_ATTEMPTS):
        try:
            result = await llm.structured(
                role="vision",
                instructions=TRANSCRIBE_INSTRUCTIONS,
                text=text,
                schema=PageTranscription,
                images=images,
                max_output_tokens=16000,
                label=f"page {page_index}" + (f" retry {attempt}" if attempt else ""),
            )
        except llm.LLMError:
            if best is None:
                raise
            break
        attempts += 1
        quality = transcription_quality(result, text_blocks)
        if quality.ok:
            return result, quality, attempts
        log.warning(
            "page %d: transcription incomplete (coverage %s, %d paragraph(s) missing, %d formula(s) lost)",
            page_index, quality.coverage, len(quality.missing), len(quality.lost),
        )
        if best is None or quality.rank() > best[1].rank():
            best = (result, quality)
        text = prompt + (_escape_reminder(quality.lost) if quality.lost else "")
    assert best is not None
    return best[0], best[1], attempts


_WORD_RE = re.compile(r"[A-Za-z]{4,}")


def transcription_quality(result: PageTranscription, text_blocks: Sequence[_TextBlock]) -> _Quality:
    """Compare the transcription (incl. captions) with the text layer's plain words (4+ ASCII letters): overall
    coverage, and paragraphs of at least BLOCK_MIN_WORDS words of which less than BLOCK_MIN_COVERAGE was found.
    Also lists formulas lost to JSON escape glitches (markdown, captions, item titles)."""
    produced = result.markdown + " " + " ".join(f.caption for f in result.figures)
    vocabulary = {w.lower() for w in _WORD_RE.findall(re.sub(r"\\[A-Za-z]+", " ", produced))}
    lost = lost_formulas(result.markdown)
    for extra in [f.caption for f in result.figures] + [i.title for i in result.items]:
        lost += lost_formulas(extra)
    reference: list[str] = []
    missing: list[str] = []
    for block in text_blocks:
        words = [w.lower() for w in _WORD_RE.findall(block.text)]
        reference += words
        if len(words) >= BLOCK_MIN_WORDS and sum(w in vocabulary for w in words) / len(words) < BLOCK_MIN_COVERAGE:
            missing.append(block.text)
    if len(reference) < MIN_WORDS_FOR_COVERAGE:
        return _Quality(coverage=None, missing=missing, lost=lost)
    return _Quality(coverage=round(sum(w in vocabulary for w in reference) / len(reference), 3), missing=missing, lost=lost)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _ensure_page_render(pdf_path: Path, page_index: int, path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    png = pdf.render_page_png(pdf_path, page_index, settings.render_dpi)
    pdf.write_atomic(path, png)
    return png


async def _nemotron_blocks(
    pdf_path: Path, page_index: int, width_pt: float, height_pt: float, raw: dict[str, Any]
) -> tuple[list[nemotron.Block], str]:
    if not nemotron.enabled():
        return [], ""
    try:
        png = await asyncio.to_thread(
            pdf.render_page_zoom_png, pdf_path, page_index, nemotron.canvas_zoom(width_pt, height_pt)
        )
        blocks = await nemotron.parse_layout(png)
    except Exception as e:  # layout blocks are only a hint: any failure means "no layout", never a lost page
        log.warning("nemotron failed for page %d: %s: %s", page_index, type(e).__name__, e)
        raw["nemotron_error"] = f"{type(e).__name__}: {e}"
        return [], ""
    if not blocks:
        return [], ""
    raw["nemotron"] = [b.to_dict() for b in blocks]
    return blocks, "nemotron"


def _text_blocks(pdf_path: Path, page_index: int) -> list[_TextBlock]:
    """Body text blocks of the PDF text layer (running header/footer zone dropped), lines joined."""
    with pdf.open_pdf(pdf_path) as doc:
        page = doc[page_index]
        height = page.rect.height
        raw_blocks = page.get_text("blocks", sort=True)
    out: list[_TextBlock] = []
    for x0, y0, x1, y1, text, _no, kind in raw_blocks:
        if kind != 0 or not text.strip():
            continue
        in_band = y1 <= _HEADER_FOOTER_BAND * height or y0 >= (1 - _HEADER_FOOTER_BAND) * height
        if in_band and len(text) < 120:
            continue
        out.append(_TextBlock((x0, y0, x1, y1), _join_lines(text)))
    return out


def _join_lines(text: str) -> str:
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)
    return " ".join(text.split())


def _user_prompt(
    page_index: int,
    label: str,
    candidates: Sequence[pdf.FigureCandidate],
    text_blocks: Sequence[_TextBlock],
    blocks: Sequence[nemotron.Block],
    width_pt: float,
    height_pt: float,
) -> str:
    parts = [f"Page: PDF index {page_index}, printed page {label}."]
    if candidates:
        lines = []
        for c in candidates:
            x0, y0, x1, y1 = c.bbox
            where = f"x {x0 / width_pt:.2f}–{x1 / width_pt:.2f}, y {y0 / height_pt:.2f}–{y1 / height_pt:.2f}"
            kind = {"vector": "vector drawing", "raster": "embedded image", "nemotron": "layout model picture"}
            sources = ", ".join(kind.get(s, s) for s in c.sources)
            caption = f'; caption guess: "{c.caption}"' if c.caption else ""
            lines.append(f"- {c.label}: {where} ({sources}{caption})")
        parts.append("Red candidate boxes (second image):\n" + "\n".join(lines))
    else:
        parts.append("No figure candidates were detected on this page (only one image is attached).")
    layer = "\n\n".join(b.text for b in text_blocks)
    parts.append("PDF text layer (words reliable, math broken):\n<<<\n" + (_hint_text(layer) or "(empty)") + "\n>>>")
    hint = _nemotron_hint(blocks) if len(_WORD_RE.findall(layer)) < LAYOUT_HINT_MAX_WORDS else ""
    if hint:
        parts.append("Layout model blocks (type, vertical extent, rough text):\n" + hint)
    return "\n\n".join(parts)


def _hint_text(text: str) -> str:
    r"""Text-layer hint without Unicode math alphanumerics: 𝐑 → \mathbf{R}, ℝ → \mathbb{R}, 𝑥 → x.
    Bold set letters in the hint correlate with the vision model emitting broken JSON escapes there."""
    out: list[str] = []
    for i, ch in enumerate(text):
        if ord(ch) < 0x2100:
            out.append(ch)
            continue
        name = unicodedata.name(ch, "")
        plain = unicodedata.normalize("NFKC", ch)
        if len(plain) == 1 and plain.isascii() and plain.isalpha():
            if name.startswith(("MATHEMATICAL BOLD CAPITAL", "MATHEMATICAL BOLD SMALL")):
                plain = f"\\mathbf{{{plain}}}"
            elif "DOUBLE-STRUCK" in name:
                plain = f"\\mathbb{{{plain}}}"
            if plain.startswith("\\") and i + 1 < len(text) and text[i + 1].isalpha():
                plain += " "  # the text layer drops the space after a math letter ("𝐑and 𝐂are fields")
        out.append(plain)
    return "".join(out)


def _nemotron_hint(blocks: Sequence[nemotron.Block]) -> str:
    lines: list[str] = []
    used = 0
    for b in blocks:
        if b.type in ("Page-header", "Page-footer"):
            continue
        text = " ".join(b.text.split())
        line = f"- [{b.type}] y {b.bbox[1]:.2f}–{b.bbox[3]:.2f}: {text[:400]}"
        if used + len(line) > _NEMOTRON_HINT_CHARS:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Set-of-mark overlay
# ---------------------------------------------------------------------------


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow built without FreeType
        return ImageFont.load_default()


def draw_marks(page_png: bytes, candidates: Sequence[pdf.FigureCandidate], width_pt: float, height_pt: float) -> bytes:
    """Red rectangles + 'M1'.. tags (white on red) at each candidate's top-left corner."""
    with Image.open(io.BytesIO(page_png)) as im:
        img = im.convert("RGB")
    sx, sy = img.width / width_pt, img.height / height_pt
    line_w = max(2, round(img.width / 400))
    font = _font(max(14, round(img.width / 45)))
    draw = ImageDraw.Draw(img)
    red = (220, 20, 20)
    for c in candidates:
        x0, y0, x1, y1 = c.bbox
        box = (x0 * sx - line_w, y0 * sy - line_w, x1 * sx + line_w, y1 * sy + line_w)
        draw.rectangle(box, outline=red, width=line_w)
        tx0, ty0, tx1, ty1 = draw.textbbox((0, 0), c.label, font=font)
        tw, th = tx1 - tx0, ty1 - ty0
        pad = max(3, line_w)
        tag_h = th + 2 * pad
        # inside the top-left corner (keeps the text around the figure readable) unless the box is too small
        inside = box[3] - box[1] >= 2.5 * tag_h and box[2] - box[0] >= 2 * (tw + 2 * pad)
        tag_y = box[1] if inside or box[1] - tag_h < 0 else box[1] - tag_h
        tag = (box[0], tag_y, box[0] + tw + 2 * pad, tag_y + tag_h)
        draw.rectangle(tag, fill=red)
        draw.text((tag[0] + pad - tx0, tag[1] + pad - ty0), c.label, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Output assembly
# ---------------------------------------------------------------------------


async def _assemble(
    pdf_path: Path,
    page_index: int,
    label: str,
    work_dir: Path,
    scan: pdf.FigureScan,
    result: PageTranscription,
    width_pt: float,
    height_pt: float,
    engine: str,
    raw: dict[str, Any],
) -> ParsedPage:
    uncertain = [u for u in result.uncertain if u.strip()]
    accepted = _accepted_figures(scan, result, width_pt, height_pt, uncertain)
    snapped = await asyncio.to_thread(_snap_new_figures, pdf_path, page_index, accepted)
    markdown, ordered = _rewrite_placeholders(normalize_math(result.markdown), snapped)
    figures = await _crop_figures(pdf_path, page_index, work_dir, ordered)
    markdown = _rewrite_ids(markdown, {a.key: f.id for a, f in zip(ordered, figures)})
    return ParsedPage(
        page_index=page_index,
        label=label,
        markdown=markdown,
        figures=figures,
        items=[{**item.model_dump(), "title": normalize_math(item.title)} for item in result.items],
        uncertain=uncertain,
        is_exercise_page=result.is_exercise_page,
        engine=engine,
        raw=raw,
    )


_NEW_KEY_RE = re.compile(r"^new(\d+)$", re.I)


def _accepted_figures(
    scan: pdf.FigureScan, result: PageTranscription, width_pt: float, height_pt: float, uncertain: list[str]
) -> list[_Accepted]:
    """Resolve the model's figure refs and placeholders to bboxes (marks → candidate bbox, new → its bbox).

    Refs with mark null are the unmarked figures new1, new2, ... in order; a mark the scan does not know (an
    invented 'M7') is reported and ignored without consuming a new-figure number."""
    by_label = {c.label: c for c in scan.candidates}
    placed = set(_PLACEHOLDER_RE.findall(result.markdown))
    rejected = {m.strip() for m in result.rejected_marks}
    accepted: dict[str, _Accepted] = {}
    new_index = 0
    for ref in result.figures:
        mark = (ref.mark or "").strip()
        caption, description = normalize_math(ref.caption.strip()), normalize_math(ref.description.strip())
        if mark not in by_label and mark.upper() in by_label:
            mark = mark.upper()  # 'm2' → 'M2'
        if mark in by_label:
            if mark in rejected and mark not in placed:
                continue
            cand = by_label[mark]
            accepted.setdefault(mark, _Accepted(mark, cand.bbox, caption or cand.caption, description))
            continue
        new_key = _NEW_KEY_RE.match(mark)
        if new_key:  # the model used its placeholder key as the mark
            key = f"new{int(new_key.group(1))}"
            new_index = max(new_index, int(new_key.group(1)))
        elif mark:
            uncertain.append(f"Figure entry with unknown mark '{mark}' was ignored.")
            continue
        else:
            new_index += 1
            while f"new{new_index}" in accepted:
                new_index += 1
            key = f"new{new_index}"
        bbox = _fraction_bbox(ref, width_pt, height_pt)
        if bbox is None:
            uncertain.append(f"Figure '{caption or key}' had no usable position and was not cropped.")
            continue
        accepted[key] = _Accepted(key, bbox, caption, description)
    for mark in placed:  # placeholders for marks the model forgot to list
        if mark in by_label and mark not in accepted and mark not in rejected:
            cand = by_label[mark]
            accepted[mark] = _Accepted(mark, cand.bbox, cand.caption, "")
    return list(accepted.values())


def _fraction_bbox(ref: FigureRef, width_pt: float, height_pt: float) -> tuple[float, float, float, float] | None:
    if not ref.bbox or len(ref.bbox) != 4:
        return None
    values = [float(v) for v in ref.bbox]
    if max(values) > 1.5:  # the model answered in percent
        values = [v / 100 for v in values]
    x0, x1 = sorted(min(1.0, max(0.0, v)) for v in (values[0], values[2]))
    y0, y1 = sorted(min(1.0, max(0.0, v)) for v in (values[1], values[3]))
    rect = (x0 * width_pt, y0 * height_pt, x1 * width_pt, y1 * height_pt)
    if rect[2] - rect[0] < MIN_NEW_FIGURE_PT or rect[3] - rect[1] < MIN_NEW_FIGURE_PT:
        return None
    return rect


def _snap_new_figures(pdf_path: Path, page_index: int, accepted: list[_Accepted]) -> list[_Accepted]:
    """Model-estimated boxes are rough: tighten them to the drawings/images they contain, if any."""
    if not any(a.key.startswith("new") for a in accepted):
        return accepted
    with pdf.open_pdf(pdf_path) as doc:
        page = doc[page_index]
        return [
            _Accepted(a.key, pdf.snap_to_content(page, a.bbox), a.caption, a.description) if a.key.startswith("new") else a
            for a in accepted
        ]


def _rewrite_placeholders(markdown: str, accepted: list[_Accepted]) -> tuple[str, list[_Accepted]]:
    """Keep the first placeholder of each accepted figure (as a temporary key), drop all others; append
    accepted figures that have no placeholder. Returns the markdown and figures in reading order."""
    by_key = {a.key: a for a in accepted}
    ordered: list[_Accepted] = []

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        fig = by_key.get(key)
        if fig is None or fig in ordered:
            return ""
        ordered.append(fig)
        return f"[[figure:{key}]]"

    out = _PLACEHOLDER_RE.sub(repl, markdown)
    for fig in accepted:
        if fig not in ordered:
            ordered.append(fig)
            out = out.rstrip() + f"\n\n[[figure:{fig.key}]]"
    return _tidy_markdown(out), ordered


def _rewrite_ids(markdown: str, ids: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: f"[[figure:{ids.get(m.group(1), m.group(1))}]]", markdown)


_ITEM_HEADING_RE = re.compile(r"(?m)^#{1,6} [^\n]*?\b(\d+\.\d+)\b[^\n]*\n+(?=> \*\*\1\b)")


def _tidy_markdown(text: str) -> str:
    """Trailing spaces, runs of blank lines, and headings that merely repeat the next boxed item's number."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return _ITEM_HEADING_RE.sub("", text).strip()


# Shown where the vision model's output destroyed a formula (never silently deleted); flagged in `uncertain`.
LOST_MARK = "\u27e8?\u27e9"  # ⟨?⟩ — renders as text and inside KaTeX math

# Pairs $$…$$ / $…$; an escaped dollar (\$5) neither opens nor closes math.
_MATH_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$|(?<![\\$])\$(?!\$)(.+?)(?<!\\)\$", re.S)
# One unit of what the model emits in place of a backslash or a whole formula: a control/DEL character or an
# ANSI/terminal escape sequence (also the malformed "ESC[?]").
_JUNK = r"(?:[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|\x1b\[[0-9;?]*[A-Za-z\]]?)"
_LOST_GLYPH_RE = re.compile(r"(?<![\w}$\\])(?P<d>\$\$?)[ ]*" + _JUNK + r"(?:[ ]*" + _JUNK + r")*[ ]*(?P=d)(?![\w$])")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # formatting codes around valid content: dropped
_ESC_GARBAGE_RE = re.compile(r"\x1b\[[0-9;?]*\]?")
_CTRL = "[\x00-\x07\x0b\x0e-\x1f\x7f]"  # \b \t \n \f \r are repaired separately (they start real commands)
_CTRL_BEFORE_COMMAND_RE = re.compile(_CTRL + r"(?=[A-Za-z{}])")  # "\x01mathbf" → "\mathbf", "\x01{" → "\{"
_CTRL_BEFORE_BACKSLASH_RE = re.compile(_CTRL + r"+(?=\\)")  # "\x00\mathbf" (escaped correctly + junk)
_CONTROL_RE = re.compile(_CTRL + "+")
_END_OF_SENTENCE_RE = re.compile(r"[.!?][)\]*_]*$")
# A newline right before these words was a JSON "\n" that ate the backslash of \neq, \nabla, \not, ...
# Single letters (\nu, \ne, \ni) are only repaired in inline math: a display line may legitimately start with
# the vector u, e^x or i^2 (multi-line $$…$$ blocks and environments).
_EATEN_NEWLINE_RE = re.compile(r"\n(?=(?:eq|e|abla|u|ot|otin|eg|i|mid|leq|geq|subseteq|exists|ewline|orm)\b)")
_EATEN_NEWLINE_DISPLAY_RE = re.compile(r"\n(?=(?:eq|abla|ot|otin|eg|mid|leq|geq|subseteq|exists|ewline|orm)\b)")
# Over-escaped commands ("\\frac", "\\{"): collapsed only before a known command name or a brace, so a row
# break followed by a variable ("a & b\\c & d", "\begin{cases}1\\x\end{cases}") is never touched.
_OVERESCAPED_CMDS = (
    "frac|dfrac|tfrac|binom|sqrt|mathbf|mathbb|mathcal|mathfrak|mathscr|mathrm|mathit|mathsf|text|textbf|textit|"
    "emph|boldsymbol|bm|operatorname|displaystyle|"
    "alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|"
    "tau|upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega|"
    "in|notin|ni|ne|neq|le|leq|ge|geq|subset|subseteq|subsetneq|supset|supseteq|cup|cap|bigcup|bigcap|setminus|"
    "emptyset|varnothing|times|cdot|cdots|ldots|dots|vdots|ddots|pm|mp|infty|sum|prod|int|iint|oint|lim|limsup|"
    "liminf|sup|inf|max|min|det|dim|ker|deg|log|ln|exp|sin|cos|tan|to|mapsto|forall|exists|nexists|neg|lnot|"
    "implies|iff|Rightarrow|Leftarrow|Leftrightarrow|rightarrow|leftarrow|leftrightarrow|approx|equiv|sim|simeq|"
    "cong|propto|perp|parallel|oplus|otimes|circ|ast|star|wedge|vee|partial|nabla|ell|hbar|"
    "overline|underline|bar|hat|widehat|tilde|widetilde|vec|dot|ddot|left|right|big|Big|bigg|Bigg|"
    "langle|rangle|lvert|rvert|lVert|rVert|vert|Vert|mid|nmid|colon|lfloor|rfloor|lceil|rceil|"
    "begin|end|square|blacksquare|boxed|qquad|quad|tag|label|prime"
)
_DISPLAY_DOUBLE_RE = re.compile(r"(?<!\\)\\\\(?=(?:" + _OVERESCAPED_CMDS + r")(?![A-Za-z])|[{}])")
_INLINE_MULTI_RE = re.compile(r"\\{2,}(?=(?:" + _OVERESCAPED_CMDS + r")(?![A-Za-z])|[{}])")
_BOLD_LETTER_RE = re.compile(r"\\(?:textbf|boldsymbol|bm)\{([A-Z])\}")
# A "$…$" match running into another paragraph, quote line, table row, heading or list item: a '$' is
# mismatched, so the span is left alone (and _close_dangling_dollars repairs the line that lost its '$').
_BLOCK_LINE_RE = re.compile(r"\n[ \t]*(?:>|\||#|[-*+][ \t]|\d+[.)][ \t]|\([a-z]\)[ \t])")


def _lost_glyph(m: re.Match[str]) -> str:
    """A formula reduced to control characters. At the end of a line that finishes a sentence it was the
    end-of-proof mark ("as desired. $\x00$"); anywhere else a symbol was lost: keep a visible marker."""
    text = m.string
    line_start = text.rfind("\n", 0, m.start()) + 1
    line_end = text.find("\n", m.end())
    after = text[m.end() : line_end if line_end >= 0 else len(text)]
    before = text[line_start : m.start()].rstrip()
    if m.group("d") == "$" and not after.strip() and _END_OF_SENTENCE_RE.search(before):
        return "$\\square$"
    return LOST_MARK


def _repair_controls(text: str) -> str:
    text = _LOST_GLYPH_RE.sub(_lost_glyph, text)
    text = _ANSI_RE.sub("", text)
    text = _ESC_GARBAGE_RE.sub(LOST_MARK, text)
    text = text.replace("\x08", "\\b").replace("\x0c", "\\f")
    text = _CTRL_BEFORE_COMMAND_RE.sub(lambda _: "\\", text)
    text = _CTRL_BEFORE_BACKSLASH_RE.sub("", text)
    return _CONTROL_RE.sub(LOST_MARK, text)


def normalize_math(text: str) -> str:
    r"""Repair what vision models do to backslashes in JSON output, then normalize delimiters.

    A LaTeX backslash written unescaped in the JSON string turns "\textbf" into TAB+"extbf", "\beta" into
    backspace+"eta", "\frac" into form feed+"rac", "\neq" into newline+"eq"; other glitches are stray control
    or DEL characters or ANSI codes in place of a backslash, and doubled backslashes ("\\{"). Anything that
    cannot be repaired (a formula reduced to "\x00") becomes LOST_MARK, never an empty string. Also: \( \) → $ $,
    \[ \] → $$ $$, and bold capital letters as \mathbf (the prompt asks for all of this; this is the safety net).
    """
    text = _repair_controls(text)
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: f"$${m.group(1).strip()}$$", text, flags=re.S)
    text = re.sub(r"\\\((.+?)\\\)", lambda m: f"${m.group(1).strip()}$", text, flags=re.S)

    def fix(m: re.Match[str]) -> str:
        display = m.group(1) is not None
        span = m.group(0)
        if not display and ("\n\n" in span or _BLOCK_LINE_RE.search(span)):
            return span  # mismatched '$': do not glue a list item / quote line / paragraph into one formula
        math = re.sub(r"[\t\r](?=[A-Za-z])", lambda e: "\\t" if e.group(0) == "\t" else "\\r", span)
        math = (_EATEN_NEWLINE_DISPLAY_RE if display else _EATEN_NEWLINE_RE).sub(lambda _: "\\n", math)
        math = (_DISPLAY_DOUBLE_RE if display else _INLINE_MULTI_RE).sub(lambda _: "\\", math)
        if not display and "\n" in math:  # line break inside inline math
            math = "$" + " ".join(math[1:-1].split()) + "$"
        return _BOLD_LETTER_RE.sub(lambda b: f"\\mathbf{{{b.group(1)}}}", math)

    return _close_dangling_dollars(_MATH_RE.sub(fix, text).replace("\r", ""))


def lost_formulas(text: str) -> list[str]:
    """Context (~40 characters each side) of every formula normalize_math cannot repair (shown as LOST_MARK)."""
    out = normalize_math(text)
    new = out.count(LOST_MARK) - text.count(LOST_MARK)
    if new <= 0:
        return []
    contexts = [" ".join(out[max(0, m.start() - 40) : m.end() + 40].split()) for m in re.finditer(re.escape(LOST_MARK), out)]
    return contexts[:new]


def unbalanced_math_lines(markdown: str) -> list[str]:
    """Lines outside $$ displays that still have an unpaired '$' opening math (not a price like '$5')."""
    out: list[str] = []
    in_display = False
    for line in markdown.split("\n"):
        displays = line.count("$$")
        if displays % 2:
            in_display = not in_display
        if displays or in_display:
            continue
        singles = [m.start() for m in re.finditer(r"(?<!\\)\$", line)]
        if len(singles) % 2 and not line[singles[-1] + 1 : singles[-1] + 2].isdigit():
            out.append(line.strip())
    return out


_DANGLING_RE = re.compile(r"\$([^\s$]{1,24}?)([.,;:!?)\]]*)(?=\s|$)")
_AMOUNT_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _close_dangling_dollars(text: str) -> str:
    """'... inverse in $U.' → '... inverse in $U$.': a line outside displays whose last inline '$' is unpaired and
    opens a single short token gets its closing '$' back (the model dropped it). A plain amount ('costs $5.')
    is a price, not math."""
    lines = text.split("\n")
    in_display = False
    for i, line in enumerate(lines):
        displays = line.count("$$")
        if displays % 2:
            in_display = not in_display
        if displays or in_display:
            continue
        singles = [m.start() for m in re.finditer(r"(?<!\\)\$", line)]
        if len(singles) % 2 == 0:
            continue
        pos = singles[-1]
        m = _DANGLING_RE.match(line, pos)
        if m and not _AMOUNT_RE.fullmatch(m.group(1)):
            lines[i] = f"{line[:pos]}${m.group(1)}${m.group(2)}{line[m.end():]}"
    return "\n".join(lines)


async def _crop_figures(pdf_path: Path, page_index: int, work_dir: Path, ordered: list[_Accepted]) -> list[ParsedFigure]:
    figures: list[ParsedFigure] = []
    for n, fig in enumerate(ordered, start=1):
        fid = f"p{page_index}-f{n}"
        x0, y0, x1, y1 = fig.bbox
        clip = (x0 - FIGURE_PADDING_PT, y0 - FIGURE_PADDING_PT, x1 + FIGURE_PADDING_PT, y1 + FIGURE_PADDING_PT)
        png = await asyncio.to_thread(pdf.render_clip_png, pdf_path, page_index, clip, FIGURE_ZOOM, fig.bbox)
        path = work_dir / "figures" / f"{fid}.png"
        await asyncio.to_thread(pdf.write_atomic, path, png)
        figures.append(
            ParsedFigure(
                id=fid,
                page_index=page_index,
                bbox=[round(v, 2) for v in fig.bbox],
                caption=fig.caption,
                description=fig.description,
                image_path=path,
            )
        )
    return figures


async def _text_layer_page(
    pdf_path: Path,
    page_index: int,
    label: str,
    work_dir: Path,
    scan: pdf.FigureScan,
    text_blocks: list[_TextBlock],
    engine: str,
    uncertain: list[str],
    raw: dict[str, Any],
) -> ParsedPage:
    """No vision model: text layer as markdown, every candidate accepted as a figure, placed by position."""
    accepted = [_Accepted(c.label, c.bbox, c.caption, "") for c in scan.candidates]
    figures = await _crop_figures(pdf_path, page_index, work_dir, accepted)
    chunks: list[tuple[float, str]] = []
    for b in text_blocks:
        if any(_inside(b.rect, a.bbox) for a in accepted):
            continue  # labels inside figures
        chunks.append((b.rect[1], b.text))
    chunks += [(fig.bbox[3], f"[[figure:{fig.id}]]") for fig in figures]
    chunks.sort(key=lambda c: c[0])
    # math alphanumerics of the text layer (𝑥, 𝐑) → plain letters; the hint for the vision model keeps them
    markdown = unicodedata.normalize("NFKC", "\n\n".join(text for _, text in chunks))
    return ParsedPage(
        page_index=page_index,
        label=label,
        markdown=markdown,
        figures=figures,
        items=[],
        uncertain=uncertain,
        is_exercise_page=bool(re.search(r"(?m)^Exercises\b", "\n".join(b.text for b in text_blocks[:3]))),
        engine=engine,
        raw=raw,
    )


_SKIP_LAYOUT_TYPES = {"Page-header", "Page-footer", "Picture", "Chart", "Caption"}


async def _nemotron_page(
    pdf_path: Path,
    page_index: int,
    label: str,
    work_dir: Path,
    scan: pdf.FigureScan,
    blocks: Sequence[nemotron.Block],
    height_pt: float,
    raw: dict[str, Any],
) -> ParsedPage:
    """UA_PARSE_ENGINE=nemotron: the layout model's own text (no OpenAI call); figures = all candidates."""
    accepted = [_Accepted(c.label, c.bbox, c.caption, "") for c in scan.candidates]
    figures = await _crop_figures(pdf_path, page_index, work_dir, accepted)
    chunks = [(b.bbox[1] * height_pt, _layout_text(b)) for b in blocks if b.type not in _SKIP_LAYOUT_TYPES and b.text]
    chunks += [(fig.bbox[3], f"[[figure:{fig.id}]]") for fig in figures]
    chunks.sort(key=lambda c: c[0])
    return ParsedPage(
        page_index=page_index,
        label=label,
        markdown=_tidy_markdown(normalize_math("\n\n".join(text for _, text in chunks))),
        figures=figures,
        items=[],
        uncertain=["Transcribed by the layout model only (UA_PARSE_ENGINE=nemotron); math may be imprecise."],
        is_exercise_page=False,
        engine="nemotron",
        raw=raw,
    )


def _layout_text(block: nemotron.Block) -> str:
    """Nemotron markdown → our conventions: HTML sub/superscripts as ^/_, Formula blocks as one $$ display."""
    text = re.sub(r"<sup>(.*?)</sup>", r"^\1", block.text)
    text = re.sub(r"<sub>(.*?)</sub>", r"_\1", text)
    if block.type != "Formula":
        return re.sub(r"<br\s*/?>", " ", text)
    rows = [re.sub(r"\\[()\[\]]", "", row).strip() for row in re.split(r"<br\s*/?>|\n", text)]
    rows = [row for row in rows if row]
    body = rows[0] if len(rows) == 1 else "\\begin{gathered} " + " \\\\ ".join(rows) + " \\end{gathered}"
    return f"$${body}$$"


def _inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> bool:
    return pdf.inside_fraction(pymupdf.Rect(inner), pymupdf.Rect(outer)) >= 0.6
