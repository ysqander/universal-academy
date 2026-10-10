"""PDF ingestion: metadata, outline tree, labels, rendering cache, figure candidates."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pymupdf
import pytest

from app.config import settings
from app.ingest import pdf
from app.ingest.nemotron import Block
from app.schemas import OutlineNode


def make_pdf(path: Path, pages: int = 3, toc: list[list] | None = None, footer_offset: int | None = None) -> Path:
    """Synthetic 375x636 pt pages: body text, a shaded box with text, a vector figure (diagonal arrow) on page 0."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=375, height=636)
        page.insert_text((30, 60), f"Body text on page {i}. " * 3, fontsize=9)
        if footer_offset is not None:
            page.insert_text((180, 620), str(i - footer_offset + 1), fontsize=9)
        if i == 0:
            box = pymupdf.Rect(20, 100, 355, 200)
            page.draw_rect(box, color=(0.2, 0.2, 0.2), fill=(1.0, 0.98, 0.76), radius=0.05)
            page.insert_text((30, 130), "1.1 definition: a shaded box with plenty of text inside", fontsize=9)
            page.insert_text((30, 150), "More words that belong to the definition box itself.", fontsize=9)
            shape = page.new_shape()
            shape.draw_line((220, 380), (340, 300))
            shape.draw_line((220, 380), (340, 380))
            shape.draw_polyline([(340, 300), (332, 300), (338, 307), (340, 300)])
            shape.finish(color=(0.8, 0.1, 0.1), width=0.5)
            shape.commit()
    if toc:
        doc.set_toc(toc)
    doc.save(path)
    doc.close()
    return path


def find(nodes: list[OutlineNode], title: str) -> OutlineNode:
    for node in nodes:
        if node.title == title:
            return node
        try:
            return find(node.children, title)
        except KeyError:
            continue
    raise KeyError(title)


# ---------------------------------------------------------------------------
# Axler (real textbook)
# ---------------------------------------------------------------------------


def test_axler_metadata_and_labels(axler_pdf: Path) -> None:
    meta = pdf.read_book_meta(axler_pdf)
    assert meta.title == "Linear Algebra Done Right"
    assert meta.author == "Sheldon Axler"
    assert meta.page_count == 404
    assert meta.has_text_layer
    assert len(meta.page_labels) == 404
    assert meta.page_labels[13] == "xvii"
    assert meta.page_labels[14] == "1"
    assert meta.page_labels[21] == "8"
    assert meta.outline_source == "pdf"


def test_axler_outline_tree(axler_pdf: Path) -> None:
    meta = pdf.read_book_meta(axler_pdf)
    chapter = find(meta.outline, "Vector Spaces")
    assert (chapter.level, chapter.start_page, chapter.end_page) == (1, 14, 39)
    assert (chapter.label_start, chapter.label_end) == ("1", "26")
    sections = {c.title: c for c in chapter.children}
    assert list(sections) == ["Rⁿ and Cⁿ", "Definition of Vector Space", "Subspaces"]  # bookmark 'R^n and C^n'
    s1a = sections["Rⁿ and Cⁿ"]
    assert (s1a.level, s1a.start_page, s1a.end_page, s1a.label_start, s1a.label_end) == (2, 15, 24, "2", "11")
    assert (sections["Definition of Vector Space"].start_page, sections["Definition of Vector Space"].end_page) == (25, 30)
    assert (sections["Subspaces"].start_page, sections["Subspaces"].end_page) == (31, 39)
    # siblings starting on the same page: the earlier one ends there instead of before its start
    digression, exercises = find(s1a.children, "Digression on Fields"), find(s1a.children, "Exercises 1A")
    assert (digression.start_page, digression.end_page) == (23, 23)
    assert (exercises.start_page, exercises.end_page) == (23, 24)
    assert find(s1a.children, "Fⁿ").start_page == 19
    # subsections that start mid-page share that page with the previous one (TOC destination y)
    span = find(meta.outline, "Linear Combinations and Span")
    assert (span.start_page, span.end_page) == (41, 44)  # index 44 holds 2.11; 'Linear Independence' starts below it
    assert (find(meta.outline, "Linear Independence").start_page, find(meta.outline, "Linear Independence").end_page) == (44, 49)
    sums = find(sections["Subspaces"].children, "Sums of Subspaces")
    assert (sums.start_page, sums.end_page) == (32, 34)
    # ids n0..nN in TOC order, unique; the last top-level node runs to the last page
    ids: list[str] = []

    def walk(nodes: list[OutlineNode]) -> None:
        for n in nodes:
            ids.append(n.id)
            assert n.start_page <= n.end_page
            walk(n.children)

    walk(meta.outline)
    assert ids == [f"n{i}" for i in range(len(ids))]
    assert meta.outline[-1].end_page == 403


def test_axler_figure_candidates_page_21(axler_pdf: Path) -> None:
    scan = pdf.figure_candidates(axler_pdf, 21)
    assert [c.label for c in scan.candidates] == ["M1", "M2", "M3"]
    assert all(c.sources == ["vector"] for c in scan.candidates)
    tops = [c.bbox[1] for c in scan.candidates]
    assert tops == sorted(tops)
    for c in scan.candidates:  # all three figures sit in the right half of the page
        assert c.bbox[0] > 190 and c.bbox[2] < 360
    top, middle, bottom = (c.bbox for c in scan.candidates)
    assert top[3] < 130 and 160 < middle[1] < middle[3] < 240 and bottom[1] > 460
    # the grey margin note (≈198,288–352,376) is rejected as a box, not offered as a figure
    assert any(reason == "box" and abs(b[0] - 198) < 2 and abs(b[1] - 288) < 2 for b, reason in scan.rejected)
    assert not any(c.bbox[1] < 300 < c.bbox[3] for c in scan.candidates)


def test_axler_definition_page_has_no_figures(axler_pdf: Path) -> None:
    scan = pdf.figure_candidates(axler_pdf, 16)
    assert scan.candidates == []
    reasons = {reason for _, reason in scan.rejected}
    assert {"box", "frame"} <= reasons  # theorem box background + example frame


def test_axler_raster_painting_page_14(axler_pdf: Path) -> None:
    scan = pdf.figure_candidates(axler_pdf, 14)
    assert len(scan.candidates) == 1
    cand = scan.candidates[0]
    assert "raster" in cand.sources
    assert cand.bbox == pytest.approx((55.07, 321.92, 310.71, 559.08), abs=1.0)


def test_axler_fragments_grouped_into_one_figure(axler_pdf: Path) -> None:
    """Page 22 'Scalar multiplication' is three separate arrows → one candidate."""
    scan = pdf.figure_candidates(axler_pdf, 22)
    assert len(scan.candidates) == 2
    scalar = scan.candidates[1]
    assert scalar.bbox[0] < 205 and scalar.bbox[2] > 350 and scalar.bbox[1] > 520


def test_axler_chapter_1_figure_count(axler_pdf: Path) -> None:
    counts = {i: len(pdf.figure_candidates(axler_pdf, i).candidates) for i in range(14, 40)}
    assert {i: n for i, n in counts.items() if n} == {14: 1, 21: 3, 22: 2}


def test_nemotron_blocks_merge(axler_pdf: Path) -> None:
    w, h = pdf.page_size(axler_pdf, 21)

    def block(kind: str, x0: float, y0: float, x1: float, y1: float, text: str = "") -> Block:
        return Block(kind, [x0 / w, y0 / h, x1 / w, y1 / h], text)

    blocks = [
        block("Picture", 225, 160, 360, 245),  # too wide box around the middle figure: drawing extent wins
        block("Picture", 195, 285, 355, 380),  # over the margin-note box: not a figure
        block("Picture", 30, 590, 120, 630),  # where no drawing exists: kept as Nemotron's own box
        block("Caption", 250, 552, 340, 562, "The sum of two vectors."),
    ]
    scan = pdf.figure_candidates(axler_pdf, 21, blocks)
    by_label = {c.label: c for c in scan.candidates}
    assert len(scan.candidates) == 4
    assert by_label["M2"].sources == ["vector", "nemotron"]
    assert by_label["M2"].bbox[0] > 230  # still the tight drawing extent
    assert by_label["M3"].caption == "The sum of two vectors."
    assert by_label["M4"].sources == ["nemotron"]
    assert any(reason == "nemotron-in-box" for _, reason in scan.rejected)


# ---------------------------------------------------------------------------
# Synthetic PDFs
# ---------------------------------------------------------------------------


def test_synthetic_box_rejected_vector_figure_kept(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "s.pdf")
    scan = pdf.figure_candidates(path, 0)
    assert len(scan.candidates) == 1
    x0, y0, x1, y1 = scan.candidates[0].bbox
    assert 215 <= x0 <= 222 and 295 <= y0 <= 302 and 338 <= x1 <= 345 and 378 <= y1 <= 385
    assert any(reason == "box" for _, reason in scan.rejected)
    assert pdf.figure_candidates(path, 1).candidates == []


def test_synthetic_meta_without_outline(tmp_path: Path) -> None:
    meta = pdf.read_book_meta(make_pdf(tmp_path / "my_great-book.pdf"), display_name="my_great_book.pdf")
    assert meta.title == "My Great Book"
    assert meta.outline == [] and meta.outline_source == "none"
    assert meta.page_labels == ["1", "2", "3"]
    assert not meta.has_text_layer  # < 200 chars per page


def test_synthetic_outline_from_toc(tmp_path: Path) -> None:
    toc = [[1, "Part A", 1], [2, "Chapter 1", 1], [3, "Deep", 2], [2, "Chapter 2", 3], [1, "Part B", 5], [2, "Missing dest", -1], [2, "Chapter 3", 6]]
    meta = pdf.read_book_meta(make_pdf(tmp_path / "t.pdf", pages=8, toc=toc))
    part_a, part_b = meta.outline
    assert (part_a.start_page, part_a.end_page) == (0, 3)
    ch1, ch2 = part_a.children
    assert (ch1.start_page, ch1.end_page) == (0, 1)
    assert (ch1.children[0].start_page, ch1.children[0].end_page) == (1, 1)
    assert (ch2.start_page, ch2.end_page) == (2, 3)
    assert (part_b.start_page, part_b.end_page) == (4, 7)
    missing, ch3 = part_b.children
    assert missing.start_page == 5 and ch3.start_page == 5  # no destination → next known start
    assert ch3.end_page == 7


def test_build_outline_level_jumps_and_clamping() -> None:
    labels = ["i", "ii", "1", "2", "3", "4"]
    nodes = pdf.build_outline([(1, "Ch", 2), (3, "Sub", 3), (1, "Ch2", 1)], 6, labels)
    ch, ch2 = nodes
    assert (ch.start_page, ch.end_page) == (2, 5)  # out-of-order TOC: ends by page order, not TOC order
    sub = ch.children[0]
    assert sub.level == 3 and (sub.start_page, sub.end_page) == (3, 5)
    assert (ch2.start_page, ch2.end_page, ch2.label_start, ch2.label_end) == (1, 1, "ii", "ii")


def ranges(nodes: list[OutlineNode]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for n in nodes:
        out[n.title] = (n.start_page, n.end_page)
        out.update(ranges(n.children))
    return out


def test_build_outline_out_of_order_entries() -> None:
    labels = [str(i + 1) for i in range(20)]
    assert ranges(pdf.build_outline([(1, "Ch1", 0), (1, "Index", 18), (1, "Ch2", 9)], 20, labels)) == {
        "Ch1": (0, 8), "Index": (18, 19), "Ch2": (9, 17),
    }
    assert ranges(pdf.build_outline([(1, "Ch1", 1), (1, "Ch2", 9), (1, "Cover", 0)], 20, labels)) == {
        "Ch1": (1, 8), "Ch2": (9, 19), "Cover": (0, 0),
    }
    # a child whose bookmark points before its parent stays inside the parent's range
    tree = ranges(pdf.build_outline([(1, "Ch1", 0), (1, "Ch2", 10), (2, "Typo", 3), (2, "Sec", 12)], 20, labels))
    assert tree["Ch2"] == (10, 19) and tree["Typo"] == (10, 11) and tree["Sec"] == (12, 19)


def test_build_outline_mid_page_starts_share_the_page() -> None:
    labels = [str(i + 1) for i in range(10)]
    entries = [(1, "Ch", 0, False), (2, "A", 0, False), (2, "B", 3, True), (2, "C", 6, False), (1, "Ch2", 8, False)]
    assert ranges(pdf.build_outline(entries, 10, labels)) == {
        "Ch": (0, 7), "A": (0, 3), "B": (3, 5), "C": (6, 7), "Ch2": (8, 9),
    }
    # 3-tuples (detected outlines without positions) keep the plain rule
    assert ranges(pdf.build_outline([(1, "A", 0), (1, "B", 3)], 10, labels)) == {"A": (0, 2), "B": (3, 9)}


def test_synthetic_mid_page_destination(tmp_path: Path) -> None:
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=375, height=636)
        page.insert_text((30, 60), f"Body text at the top of page {i}.", fontsize=9)
        if i == 1:
            page.insert_text((30, 330), "Second Section", fontsize=12)
    doc.set_toc([
        [1, "First Section", 1],
        [1, "Second Section", 2, {"kind": pymupdf.LINK_GOTO, "page": 1, "to": pymupdf.Point(30, 318)}],
        [1, "Third", 4],
    ])
    doc.save(tmp_path / "mid.pdf")
    doc.close()
    meta = pdf.read_book_meta(tmp_path / "mid.pdf")
    assert ranges(meta.outline) == {"First Section": (0, 1), "Second Section": (1, 2), "Third": (3, 3)}
    with pdf.open_pdf(tmp_path / "mid.pdf") as doc:  # without a destination the title is searched for
        assert pdf.starts_mid_page(doc[1], None, "Second Section")
        assert not pdf.starts_mid_page(doc[0], None, "Body text at the top")
        assert pdf.starts_mid_page(doc[0], None, "Nowhere to be found")  # unknown: include the shared page


def test_prettify_title() -> None:
    assert pdf.prettify_title("R^n and C^n") == "Rⁿ and Cⁿ"
    assert pdf.prettify_title("F^\\infty") == pdf.prettify_title("F^{\\infty}") == "F^∞"
    assert pdf.prettify_title("x^2 and y^{3}") == "x² and y³"
    assert pdf.prettify_title("R^{m,n} and F^S") == "R^{m,n} and F^S"  # multi-character / no superscript form
    assert pdf.prettify_title("Cost $x^2$") == "Cost $x^2$"
    nodes = pdf.build_outline([(1, "F^n", 0)], 1, ["1"])
    assert nodes[0].title == "Fⁿ"


@pytest.mark.parametrize(
    ("line", "number"),
    [("- 5 -", 5), ("5 | Chapter 1", 5), ("Title · 5", 5), ("Page 5 of 300", 5), ("[12]", 12), ("Chapter", None)],
)
def test_page_number_formats(line: str, number: int | None) -> None:
    assert pdf._page_number(line) == number


def test_resolve_printed_page_never_guesses_without_offset() -> None:
    plain = [str(i + 1) for i in range(30)]
    assert pdf.resolve_printed_page("1", plain, None, 30) is None


def test_anchored_offset_from_headings(tmp_path: Path) -> None:
    doc = pymupdf.open()
    titles = ["Getting Started", "Vectors and Matrices", "Linear Maps"]
    page = doc.new_page(width=375, height=636)
    for k, t in enumerate(titles):  # a table of contents page
        page.insert_text((30, 60 + 20 * k), f"{t} .......... {1 + 4 * k}", fontsize=9)
    for i in range(1, 16):  # printed page n at PDF index n + 2 (2 pages of front matter after the TOC)
        page = doc.new_page(width=375, height=636)
        printed = i - 2
        if printed >= 1 and (printed - 1) % 4 == 0 and (printed - 1) // 4 < len(titles):
            page.insert_text((30, 60), titles[(printed - 1) // 4], fontsize=14)
        page.insert_text((30, 200), "Some running text on the page.", fontsize=9)
        page.insert_text((180, 620), f"Page {printed} of 13", fontsize=9) if printed >= 1 else None
    doc.save(tmp_path / "anchor.pdf")
    doc.close()
    titled = [(t, str(1 + 4 * k)) for k, t in enumerate(titles)]
    assert pdf._anchored_offset(tmp_path / "anchor.pdf", titled) == 2
    with pdf.open_pdf(tmp_path / "anchor.pdf") as d:
        assert pdf.detect_page_offset(d) == 2  # "Page N of M" footers vote on N


# ---------------------------------------------------------------------------
# Figure candidates: charts, glyph labels, equations beside figures
# ---------------------------------------------------------------------------


def make_bar_chart(path: Path, square: bool = False) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=375, height=636)
    page.insert_text((30, 60), "A paragraph of body text above the chart that explains the data.", fontsize=9)
    if square:  # a commutative square drawn with axis-aligned lines, corner labels outside
        shape = page.new_shape()
        for a, b in [((100, 250), (250, 250)), ((250, 250), (250, 380)), ((100, 380), (250, 380)), ((100, 250), (100, 380))]:
            shape.draw_line(a, b)
        shape.finish(color=(0, 0, 0), width=0.8)
        shape.commit()
        for x, y, t in [(88, 248, "V"), (254, 248, "W"), (88, 392, "X"), (254, 392, "Y")]:
            page.insert_text((x, y), t, fontsize=10)
    else:
        shape = page.new_shape()
        shape.draw_line((60, 400), (320, 400))
        shape.draw_line((60, 400), (60, 200))
        shape.finish(color=(0, 0, 0), width=0.8)
        for k, h in enumerate([60, 120, 90, 170, 40]):
            shape.draw_rect(pymupdf.Rect(80 + 45 * k, 400 - h, 110 + 45 * k, 400))
        shape.finish(color=None, fill=(0.3, 0.5, 0.8))
        shape.commit()
        for k in range(5):
            page.insert_text((90 + 45 * k, 412), str(k + 1), fontsize=8)
    page.insert_text((30, 470), "Text after the figure.", fontsize=9)
    doc.save(path)
    doc.close()
    return path


def test_bar_chart_is_a_candidate(tmp_path: Path) -> None:
    path = make_bar_chart(tmp_path / "bars.pdf")
    scan = pdf.figure_candidates(path, 0)
    assert len(scan.candidates) == 1 and scan.candidates[0].sources == ["vector"]
    x0, y0, x1, y1 = scan.candidates[0].bbox
    assert x0 <= 61 and y0 <= 201 and x1 >= 319 and y1 >= 399  # axes and bars
    w, h = pdf.page_size(path, 0)
    picture = Block("Picture", [50 / w, 190 / h, 330 / w, 420 / h], "")
    scan = pdf.figure_candidates(path, 0, [picture])
    assert len(scan.candidates) == 1 and scan.candidates[0].sources == ["vector", "nemotron"]
    assert not any(reason == "nemotron-in-box" for _, reason in scan.rejected)


def test_rectilinear_diagram_is_a_candidate(tmp_path: Path) -> None:
    scan = pdf.figure_candidates(make_bar_chart(tmp_path / "square.pdf", square=True), 0)
    assert len(scan.candidates) == 1


def test_glyph_tick_labels_join_the_figure(axler_pdf: Path) -> None:
    """Page 302 (printed 289): tick labels 0.3/1.3/2.3 and 0.5/1.5 are drawn as glyph outlines."""
    m1 = pdf.figure_candidates(axler_pdf, 302).candidates[0]
    assert m1.bbox[0] < 41 and m1.bbox[3] > 568


def test_equation_beside_figure_is_not_a_label(axler_pdf: Path, tmp_path: Path) -> None:
    """Page 208 (printed 195): the identity a² + b² = ½c² + 2d² above the triangle stays text."""
    m1 = pdf.figure_candidates(axler_pdf, 208).candidates[0]
    assert m1.bbox[1] >= 375
    from app.ingest import parse

    page = asyncio.run(parse.parse_page(axler_pdf, 208, "195", tmp_path))  # fake mode: text layer
    assert "+ b" in page.markdown and "2d" in page.markdown


def test_page_offset_detection_and_printed_page_resolution(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "o.pdf", pages=10, footer_offset=3)  # PDF index 3 prints "1"
    with pdf.open_pdf(path) as doc:
        offset = pdf.detect_page_offset(doc)
    assert offset == 2
    plain = [str(i + 1) for i in range(10)]
    assert pdf.resolve_printed_page("1", plain, offset, 10) == 3
    assert pdf.resolve_printed_page("7", plain, offset, 10) == 9
    assert pdf.resolve_printed_page("8", plain, offset, 10) is None
    assert pdf.resolve_printed_page("xii", plain, offset, 10) is None
    labelled = ["i", "ii", "iii", "1", "2", "3"]
    assert pdf.resolve_printed_page("2", labelled, None, 6) == 4
    assert pdf.resolve_printed_page("ii", labelled, None, 6) == 1


def test_detect_outline_is_empty_in_fake_mode(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "f.pdf")
    assert asyncio.run(pdf.detect_outline(path, ["1", "2", "3"])) == []


def test_validate_pdf_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(pdf.PdfError):
        pdf.validate_pdf(bad)
    assert pdf.validate_pdf(make_pdf(tmp_path / "ok.pdf", pages=2)) == 2


def test_prettify_filename() -> None:
    assert pdf.prettify_filename("Linear_Algebra_Done_Right_Sheldon.pdf") == "Linear Algebra Done Right Sheldon"
    assert pdf.prettify_filename("calculus-vol-2.pdf") == "Calculus Vol 2"
    assert pdf.prettify_filename("Real Analysis (Rudin).pdf") == "Real Analysis (Rudin)"


def test_render_and_cache(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "r.pdf")
    first = pdf.cached_page_png("book1", path, 1, 72)
    assert first == settings.page_cache_dir / "book1" / "1@72.png"  # a derived file: in the data dir, not the books folder
    stamp = first.stat().st_mtime_ns
    assert pdf.cached_page_png("book1", path, 1, 72).stat().st_mtime_ns == stamp
    pix = pymupdf.Pixmap(str(first))
    assert (pix.width, pix.height) == (375, 636)
    with pytest.raises(IndexError):
        pdf.render_page_png(path, 99, 72)


def test_wide_picture_block_keeps_separate_figures(axler_pdf: Path) -> None:
    """Page 300 (printed 287): two complete ellipse plots side by side under one Nemotron Picture box."""
    plain = pdf.figure_candidates(axler_pdf, 300).candidates
    assert len(plain) == 3
    w, h = pdf.page_size(axler_pdf, 300)
    wide = Block("Picture", [30 / w, 440 / h, 340 / w, 580 / h], "")
    caption = Block("Caption", [40 / w, 580 / h, 170 / w, 600 / h], "_The ellipsoid E(_2_f_<sub>1</sub>_, f_<sub>2</sub>) in_ R<sup>2</sup>")
    scan = pdf.figure_candidates(axler_pdf, 300, [wide, caption])
    assert [c.bbox for c in scan.candidates] == [c.bbox for c in plain]
    left = min(scan.candidates[1:], key=lambda c: c.bbox[0])
    assert left.caption == "The ellipsoid E(2f_1, f_2) in R^2"


def make_equation_over_figure(path: Path) -> Path:
    """A display equation with a drawn fraction bar right above a triangle whose apex lies inside the equation's
    line band (horizontally clear of it) — the layout of Axler p.195 (PDF index 208)."""
    doc = pymupdf.open()
    page = doc.new_page(width=375, height=636)
    page.insert_text((40, 340), "Body text of the paragraph that introduces the identity below.", fontsize=10)
    page.insert_text((150, 380), "a2 + b2 = 1", fontsize=11)  # the numerator 1 ends the line, above the bar
    page.insert_text((201, 386), "2", fontsize=8)  # the denominator
    page.insert_text((212, 380), "c2 + 2d2.", fontsize=11)
    page.draw_line((199.5, 377), (205.5, 377), color=(0, 0, 0), width=0.5)  # the fraction bar
    shape = page.new_shape()
    shape.draw_polyline([(300, 376), (150, 560), (320, 560), (300, 376)])
    shape.finish(color=(0.8, 0.1, 0.1), width=1)
    shape.commit()
    page.insert_text((205, 470), "a", fontsize=10)  # labels inside the figure
    page.insert_text((316, 470), "b", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def _dark_pixels(png: bytes, clip: tuple[float, float, float, float], region: tuple[float, float, float, float], zoom: float) -> int:
    pix = pymupdf.Pixmap(png)
    x0, y0 = int((region[0] - clip[0]) * zoom), int((region[1] - clip[1]) * zoom)
    x1, y1 = int((region[2] - clip[0]) * zoom), int((region[3] - clip[1]) * zoom)
    return sum(1 for y in range(max(0, y0), min(pix.height, y1)) for x in range(max(0, x0), min(pix.width, x1)) if min(pix.pixel(x, y)) < 200)


def test_figure_crop_masks_a_neighbouring_equation_and_its_fraction_bar(tmp_path: Path) -> None:
    path = make_equation_over_figure(tmp_path / "eq.pdf")
    [cand] = pdf.figure_candidates(path, 0).candidates
    figure = cand.bbox
    assert 368 < figure[1] < 389  # the bbox starts inside the equation's line band (the apex lies there)
    clip = (figure[0] - 4, figure[1] - 4, figure[2] + 4, figure[3] + 4)
    equation = (clip[0], clip[1], 270.0, 390.0)  # the equation and its fraction bar — left of the apex at x=300
    plain = pdf.render_clip_png(path, 0, clip, 3.0)
    masked = pdf.render_clip_png(path, 0, clip, 3.0, figure)
    assert _dark_pixels(plain, clip, equation, 3.0) > 0  # without the mask the crop shows half the equation
    assert _dark_pixels(masked, clip, equation, 3.0) == 0  # glyphs and the fraction bar are gone
    apex = (290.0, 374.0, 310.0, 392.0)
    assert _dark_pixels(masked, clip, apex, 3.0) > 0  # the figure's strokes inside the band survive
    for label in ((203.0, 460.0, 212.0, 472.0), (314.0, 460.0, 322.0, 472.0)):
        assert _dark_pixels(masked, clip, label, 3.0) > 0  # its own labels are kept
    # a crop with nothing foreign in it is rendered exactly as before
    lone = (150.0, 400.0, 330.0, 560.0)
    assert pdf.render_clip_png(path, 0, lone, 2.0, lone) == pdf.render_clip_png(path, 0, lone, 2.0)
