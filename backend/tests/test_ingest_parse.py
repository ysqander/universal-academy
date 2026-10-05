"""Page parsing: fake mode, transcription assembly (figures, placeholders), fallbacks, math normalization."""

from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image

from app import llm
from app.config import settings
from app.ingest import parse, pdf
from app.llm_schemas import BoxedItem, FigureRef, PageTranscription


def make_figure_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=375, height=636)
    page.insert_text((30, 60), "Some body text that explains the figure below in a few words.", fontsize=9)
    shape = page.new_shape()
    shape.draw_line((100, 300), (250, 200))
    shape.draw_line((100, 300), (250, 300))
    shape.finish(color=(0.8, 0.1, 0.1), width=0.6)
    shape.commit()
    page.insert_text((30, 400), "Text after the figure.", fontsize=9)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def live_mode() -> Iterator[None]:
    """Run parse_page down the non-fake path (the LLM itself is replaced per test)."""
    object.__setattr__(settings, "fake_llm", False)
    try:
        yield
    finally:
        object.__setattr__(settings, "fake_llm", True)


def canned(monkeypatch: pytest.MonkeyPatch, *results: PageTranscription | Exception) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    queue = list(results)

    async def fake_structured(**kwargs: Any) -> PageTranscription:
        calls.append(kwargs)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm, "structured", fake_structured)
    return calls


def transcription(markdown: str, figures: list[FigureRef] | None = None, rejected: list[str] | None = None) -> PageTranscription:
    return PageTranscription(
        markdown=markdown,
        figures=figures or [],
        rejected_marks=rejected or [],
        items=[BoxedItem(ref="1.3", kind="theorem", title="properties")],
        is_exercise_page=False,
        uncertain=[],
    )


def png_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


# ---------------------------------------------------------------------------
# Fake mode
# ---------------------------------------------------------------------------


def test_fake_mode_crops_every_candidate(axler_pdf: Path, tmp_path: Path) -> None:
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    assert page.engine == "fake" and page.label == "8" and page.items == []
    assert [f.id for f in page.figures] == ["p21-f1", "p21-f2", "p21-f3"]
    assert re.findall(r"\[\[figure:([^\]]+)\]\]", page.markdown) == ["p21-f1", "p21-f2", "p21-f3"]
    for fig in page.figures:
        assert fig.image_path == tmp_path / "figures" / f"{fig.id}.png"
        x0, y0, x1, y1 = fig.bbox
        w, h = png_size(fig.image_path)
        assert abs(w - (x1 - x0 + 8) * 3) <= 3 and abs(h - (y1 - y0 + 8) * 3) <= 3
    assert (tmp_path / "pages" / "21.png").exists()
    assert "A picture can aid our intuition" in page.markdown
    assert "Linear Algebra Done Right, fourth edition" not in page.markdown  # footer dropped
    assert "𝐑" not in page.markdown  # math alphanumerics normalized


def test_fake_mode_synthetic_pdf(tmp_path: Path) -> None:
    path = make_figure_pdf(tmp_path / "fig.pdf")
    page = asyncio.run(parse.parse_page(path, 0, "1", tmp_path / "work"))
    assert [f.id for f in page.figures] == ["p0-f1"]
    lines = page.markdown.split("\n\n")
    assert lines[1] == "[[figure:p0-f1]]" and lines[2] == "Text after the figure."
    assert page.figures[0].image_path.exists()


# ---------------------------------------------------------------------------
# Transcription assembly
# ---------------------------------------------------------------------------


def test_transcription_figures_and_placeholders(
    axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None
) -> None:
    body = " ".join(b.text for b in parse._text_blocks(axler_pdf, 21))
    markdown = (
        body + " With $\x01mathbf{R}^2$ and $\\\\{0\\\\}$.\n\n"
        "[[figure:M3]]\n\n"
        "Middle [[figure:M2]] text.\n\n"
        "[[figure:M1]]\n\n"
        "Again [[figure:M3]]\n\n"
        "[[figure:new1]]\n\n"
        "[[figure:M9]]"
    )
    figures = [
        FigureRef(mark="M3", bbox=None, caption="The sum of two vectors.", description="Head to tail."),
        FigureRef(mark=None, bbox=[0.05, 0.62, 0.5, 0.7], caption="", description="Unmarked."),
        FigureRef(mark=None, bbox=[0.5, 0.5, 0.501, 0.501], caption="Tiny", description="Unusable box."),
    ]
    calls = canned(monkeypatch, transcription(markdown, figures, rejected=["M2"]))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))

    assert page.engine == f"openai:{settings.model_vision}"
    assert len(calls) == 1 and len(calls[0]["images"]) == 2  # clean page + set-of-mark overlay
    assert "M1:" in calls[0]["text"] and "PDF text layer" in calls[0]["text"]
    assert (tmp_path / "marks" / "21.png").exists()

    assert [f.id for f in page.figures] == ["p21-f1", "p21-f2", "p21-f3"]
    f1, f2, f3 = page.figures
    candidates = {c.label: c for c in pdf.figure_candidates(axler_pdf, 21).candidates}
    assert f1.bbox == pytest.approx(list(candidates["M3"].bbox), abs=0.01)
    assert (f1.caption, f1.description) == ("The sum of two vectors.", "Head to tail.")
    assert f2.bbox == pytest.approx(list(candidates["M1"].bbox), abs=0.01)
    w, h = pdf.page_size(axler_pdf, 21)
    assert f3.bbox == pytest.approx([0.05 * w, 0.62 * h, 0.5 * w, 0.7 * h], abs=0.01)  # no drawings there to snap to
    assert all(f.image_path.exists() for f in page.figures)

    assert re.findall(r"\[\[figure:([^\]]+)\]\]", page.markdown) == ["p21-f1", "p21-f2", "p21-f3"]
    assert "Middle  text." in page.markdown and "M9" not in page.markdown and "M2" not in page.markdown
    assert "$\\mathbf{R}^2$" in page.markdown and "$\\{0\\}$" in page.markdown
    assert any("Tiny" in u for u in page.uncertain)
    assert page.items == [{"ref": "1.3", "kind": "theorem", "title": "properties"}]
    assert page.raw["coverage"] >= 0.95 and page.raw["attempts"] == 1


def test_unplaced_accepted_figure_is_appended(axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None) -> None:
    good = " ".join(b.text for b in parse._text_blocks(axler_pdf, 21))
    figures = [FigureRef(mark="M2", bbox=None, caption="A vector.", description="Two parallel arrows.")]
    canned(monkeypatch, transcription(good, figures, rejected=["M1", "M3"]))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    assert [f.caption for f in page.figures] == ["A vector."]
    assert page.markdown.endswith("[[figure:p21-f1]]")
    assert page.raw["coverage"] >= 0.95 and page.raw["attempts"] == 1


def test_snap_to_content_tightens_rough_box(axler_pdf: Path) -> None:
    m1 = pdf.figure_candidates(axler_pdf, 21).candidates[0].bbox
    with pdf.open_pdf(axler_pdf) as doc:
        snapped = pdf.snap_to_content(doc[21], (m1[0] - 15, m1[1] - 10, m1[2] + 5, m1[3] + 12))
    assert snapped == pytest.approx(m1, abs=1.0)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_vision_failure_falls_back_to_text_layer(
    axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None
) -> None:
    canned(monkeypatch, llm.LLMError("vision call failed after retries"))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    assert page.engine == "textlayer"
    assert len(page.figures) == 3
    assert page.uncertain and "Vision transcription failed" in page.uncertain[0]
    assert page.raw["vision_error"]


def test_degenerate_transcription_is_retried(axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None) -> None:
    good = " ".join(b.text for b in parse._text_blocks(axler_pdf, 21))
    calls = canned(monkeypatch, transcription("Thus $\\[?]$"), transcription(good))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    assert len(calls) == 2 and page.raw["attempts"] == 2
    assert page.engine.startswith("openai:") and page.uncertain == []


def test_dropped_paragraph_is_retried_then_reported(
    axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None
) -> None:
    blocks = parse._text_blocks(axler_pdf, 21)
    note = next(b.text for b in blocks if b.text.startswith("Mathematical models of the economy"))
    without_note = " ".join(b.text for b in blocks if b.text != note)
    full = " ".join(b.text for b in blocks)
    calls = canned(monkeypatch, transcription(without_note), transcription(full))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    assert len(calls) == 2 and page.uncertain == [] and page.raw["missing_blocks"] == []

    calls = canned(monkeypatch, transcription(without_note), transcription(without_note))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path / "again"))
    assert len(calls) == 2 and page.engine.startswith("openai:")
    assert page.raw["missing_blocks"] == [note]
    assert len(page.uncertain) == 1 and "Mathematical models of the economy" in page.uncertain[0]


def test_garbage_twice_uses_text_layer(axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None) -> None:
    canned(monkeypatch, transcription("Thus"), transcription("Thus $x$ and"))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    assert page.engine == "textlayer"
    assert "incomplete" in page.uncertain[0]
    assert "A picture can aid our intuition" in page.markdown


def test_page_without_candidates_sends_one_image(axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None) -> None:
    good = " ".join(b.text for b in parse._text_blocks(axler_pdf, 16))
    calls = canned(monkeypatch, transcription(good + " [[figure:M1]]"))
    page = asyncio.run(parse.parse_page(axler_pdf, 16, "3", tmp_path))
    assert len(calls[0]["images"]) == 1
    assert page.figures == [] and "[[figure" not in page.markdown
    assert not (tmp_path / "marks" / "16.png").exists()


def test_nemotron_only_engine(axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None) -> None:
    from app.ingest import nemotron
    from app.ingest.nemotron import Block

    blocks = [
        Block("Page-header", [0.05, 0.03, 0.4, 0.05], "8 Chapter 1 Vector Spaces"),
        Block("Text", [0.05, 0.08, 0.52, 0.27], "A picture can aid our intuition in **R**<sup>2</sup>."),
        Block("Formula", [0.3, 0.5, 0.6, 0.55], "\\(x+(-x)=0.\\)"),
        Block("Caption", [0.6, 0.87, 0.9, 0.89], "_The sum of two vectors._"),
    ]

    async def fake_layout(png: bytes) -> list[Block]:
        return blocks

    monkeypatch.setattr(nemotron, "enabled", lambda: True)
    monkeypatch.setattr(nemotron, "parse_layout", fake_layout)
    calls = canned(monkeypatch)
    object.__setattr__(settings, "parse_engine", "nemotron")
    try:
        page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    finally:
        object.__setattr__(settings, "parse_engine", "auto")
    assert calls == [] and page.engine == "nemotron"
    assert page.markdown.startswith("A picture can aid our intuition in **R**^2.")
    assert "$$x+(-x)=0.$$" in page.markdown and "Chapter 1" not in page.markdown
    assert len(page.figures) == 3 and page.figures[2].caption == "The sum of two vectors."
    assert page.raw["nemotron"][0]["type"] == "Page-header"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"set $\\{0, 1\\}$ and $\\alpha$", r"set $\{0, 1\}$ and $\alpha$"),
        (r"$$\begin{aligned} a &= b \\ &= c \end{aligned}$$", r"$$\begin{aligned} a &= b \\ &= c \end{aligned}$$"),
        (r"inline \( x^2 \) and \[ y \]", r"inline $x^2$ and $$y$$"),
        (r"outside math \\alpha stays", r"outside math \\alpha stays"),
        ("$\x01mathbf{R}$ and $\x01{0\x01}$", r"$\mathbf{R}$ and $\{0\}$"),
        ("$\theta + \x0crac{1}{2}$, $\x08eta \neq 0$", r"$\theta + \frac{1}{2}$, $\beta \neq 0$"),
        ("two lines\nof text $x$", "two lines\nof text $x$"),
        ("over $\textbf{F}$ and $\x08oldsymbol{R}^n$", r"over $\mathbf{F}$ and $\mathbf{R}^n$"),
        ("in $\x1b[1m\\mathbf{F}^n\x1b[0m$: addition", r"in $\mathbf{F}^n$: addition"),
        ("as desired. $\x00$\n\nNext", "as desired. $\\square$\n\nNext"),
        ("a $\x00$ b", "a \u27e8?\u27e9 b"),  # a lost formula stays visible
        # D13: runs of junk / ANSI-only formulas never collapse to "$$" (which would open a display)
        ("Thus $\x00\x00$ and $x$ are.\n\nLater $$y$$", "Thus \u27e8?\u27e9 and $x$ are.\n\nLater $$y$$"),
        ("over $\x1b[1m\x1b[0m$ and $x$", "over \u27e8?\u27e9 and $x$"),
        ("Thus $\x1b[?]$ and", "Thus \u27e8?\u27e9 and"),
        # a lost formula at a line end that does not finish a sentence is not the end-of-proof mark
        ("the field $\x00$\n\nNext", "the field \u27e8?\u27e9\n\nNext"),
        ("as desired. $\x1b[0m\\square$", "as desired. $\\square$"),
        # junk before a correctly escaped command is dropped; DEL works like the other control characters
        ("fields $\x00\\mathbf{R}$ and $\x7f\\textbf{F}^S$, $\x7fmathbf{C}$", r"fields $\mathbf{R}$ and $\mathbf{F}^S$, $\mathbf{C}$"),
        ("lone \x00 junk", "lone \u27e8?\u27e9 junk"),
        # display lines may start with u, i, e: \nu / \ni / \ne are only repaired in inline math
        ("$$\nu + v = v + u\n$$", "$$\nu + v = v + u\n$$"),
        ("$$\ni^2 = -1\n$$", "$$\ni^2 = -1\n$$"),
        ("$$\ne^{x} = 1\n$$", "$$\ne^{x} = 1\n$$"),
        ("$$\\begin{aligned}\nu &= v \\\\\ne &= f\n\\end{aligned}$$", "$$\\begin{aligned}\nu &= v \\\\\ne &= f\n\\end{aligned}$$"),
        ("$$a \neq b$$ and $$\nabla f = 0$$", "$$a \\neq b$$ and $$\\nabla f = 0$$"),
        # a row break followed by a variable is not an over-escaped command
        (r"$$\begin{bmatrix}a & b\\c & d\end{bmatrix}$$", r"$$\begin{bmatrix}a & b\\c & d\end{bmatrix}$$"),
        (r"$$\begin{cases} x & y\\z & w\end{cases}$$", r"$$\begin{cases} x & y\\z & w\end{cases}$$"),
        (r"$f(x)=\begin{cases}1\\x\end{cases}$", r"$f(x)=\begin{cases}1\\x\end{cases}$"),
        (r"$$\sum_{\substack{i\\j}}$$", r"$$\sum_{\substack{i\\j}}$$"),
        (r"$$\\frac{1}{2} + \\lambda$$", r"$$\frac{1}{2} + \lambda$$"),
        # one unpaired or escaped '$' does not merge list items, quote lines or table rows
        ("1. Prove $a$ and $b.\n2. Show $c$.", "1. Prove $a$ and $b$.\n2. Show $c$."),
        ("> The inverse in $U.\n>\n> Then $x$ is a vector.", "> The inverse in $U$.\n>\n> Then $x$ is a vector."),
        ("- costs \\$5 each\n- buy $n$ items", "- costs \\$5 each\n- buy $n$ items"),
        ("> **1.3 Theorem**\n>\n> For all $a, b \\in V$ and $c.\n> Then $x = y$.", "> **1.3 Theorem**\n>\n> For all $a, b \\in V$ and $c$.\n> Then $x = y$."),
        ("It costs $5.", "It costs $5."),
        (r"space $\\\textbf{\\{0\\}}$", r"space $\textbf{\{0\}}$"),
        ("$$a \\\\\n b\\\\ c$$ and $x \neq 0$", "$$a \\\\\n b\\\\ c$$ and $x \\neq 0$"),
        ("subspace of $\n\\mathbf{F}^n$ of", r"subspace of $\mathbf{F}^n$ of"),
        (r"then $-u$ is in $U$. Hence in $U.", r"then $-u$ is in $U$. Hence in $U$."),
        ("> inverse in $U, so\n\n$$a$$", "> inverse in $U$, so\n\n$$a$$"),
        (r"price is $5 and $x + y gets", r"price is $5 and $x + y gets"),
    ],
)
def test_normalize_math(raw: str, expected: str) -> None:
    assert parse.normalize_math(raw) == expected


def test_lost_formulas_lists_context() -> None:
    raw = "Thus $\x00$ and $\x00$ are fields. Proof done. $\x00$\n\nNext $\x01mathbf{R}$."
    lost = parse.lost_formulas(raw)
    assert len(lost) == 2 and all("Thus" in c for c in lost)  # the end-of-proof mark and the repairable one are not lost
    assert parse.lost_formulas("clean $x$") == []


def test_unbalanced_math_lines() -> None:
    md = "ok $x$ line\nIt costs $5.\nbroken $x + y gets\n$$\na $ b\n$$\n> also $a$ and $b"
    assert parse.unbalanced_math_lines(md) == ["broken $x + y gets", "> also $a$ and $b"]


def test_transcription_quality_flags_lost_formulas() -> None:
    blocks = [parse._TextBlock((0, 0, 100, 100), "Thus R and C are fields")]
    bad = transcription("Thus $\x00$ and $\x00$ are fields")
    quality = parse.transcription_quality(bad, blocks)
    assert len(quality.lost) == 2 and not quality.ok
    good = parse.transcription_quality(transcription("Thus $\\mathbf{R}$ and $\\mathbf{C}$ are fields"), blocks)
    assert good.ok and good.rank() > quality.rank()


def test_lost_formulas_are_retried_with_escape_reminder(
    axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None
) -> None:
    body = " ".join(b.text for b in parse._text_blocks(axler_pdf, 23))
    broken = transcription(body + " Thus $\x00$ and $\x00$ are fields.")
    fixed = transcription(body + " Thus $\\mathbf{R}$ and $\\mathbf{C}$ are fields.")
    calls = canned(monkeypatch, broken, fixed)
    page = asyncio.run(parse.parse_page(axler_pdf, 23, "10", tmp_path))
    assert len(calls) == 2 and page.raw["attempts"] == 2
    assert "IMPORTANT" not in calls[0]["text"] and "\\\\mathbf{R}" in calls[1]["text"]  # JSON-escaping reminder
    assert "\u27e8?\u27e9" in calls[1]["text"] and "Thus" in calls[1]["text"]
    assert "$\\mathbf{R}$ and $\\mathbf{C}$" in page.markdown and page.uncertain == []
    # the text-layer hint carries no Unicode math-bold letters (𝐑 → \mathbf{R})
    assert "\U0001d411" not in calls[0]["text"] and "\\mathbf{R} and \\mathbf{C} are fields" in calls[0]["text"]

    calls = canned(monkeypatch, broken, broken)
    page = asyncio.run(parse.parse_page(axler_pdf, 23, "10", tmp_path / "again"))
    assert len(calls) == 2 and page.markdown.count("\u27e8?\u27e9") == 2  # visible, never deleted
    assert page.raw["lost_formulas"] and any("2 formula(s) could not be read" in u for u in page.uncertain)


def test_hint_text_maps_math_alphanumerics() -> None:
    assert parse._hint_text("Thus \U0001d411and \U0001d402are fields, \U0001d465 \u2208 \u211d") == (
        "Thus \\mathbf{R} and \\mathbf{C} are fields, x \u2208 \\mathbb{R}"
    )


def test_hallucinated_mark_does_not_shift_new_figures(
    axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None
) -> None:
    body = " ".join(b.text for b in parse._text_blocks(axler_pdf, 16))  # no candidates on this page
    markdown = body + "\n\n[[figure:new1]]\n\nMore text at the end."
    figures = [
        FigureRef(mark="M2", bbox=None, caption="", description="invented"),
        FigureRef(mark=None, bbox=[0.1, 0.5, 0.6, 0.8], caption="A sketch", description="Unmarked."),
    ]
    canned(monkeypatch, transcription(markdown, figures))
    page = asyncio.run(parse.parse_page(axler_pdf, 16, "3", tmp_path))
    assert [f.caption for f in page.figures] == ["A sketch"]
    assert page.markdown.endswith("[[figure:p16-f1]]\n\nMore text at the end.")
    assert any("'M2'" in u for u in page.uncertain)


def test_nemotron_garbage_never_aborts_a_page(
    axler_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_mode: None
) -> None:
    from app.ingest import nemotron

    async def broken_layout(png: bytes) -> list:
        raise ValueError("Expecting ',' delimiter: line 1 column 9000")  # e.g. a truncated tool-call JSON

    monkeypatch.setattr(nemotron, "enabled", lambda: True)
    monkeypatch.setattr(nemotron, "parse_layout", broken_layout)
    good = " ".join(b.text for b in parse._text_blocks(axler_pdf, 21))
    canned(monkeypatch, transcription(good))
    page = asyncio.run(parse.parse_page(axler_pdf, 21, "8", tmp_path))
    assert page.engine == f"openai:{settings.model_vision}"  # no "+nemotron"
    assert page.raw["nemotron_error"].startswith("ValueError") and "nemotron" not in page.raw


def test_tidy_drops_headings_that_repeat_an_item() -> None:
    md = "## 1.43 Example: *a direct sum*\n\n> **1.43 Example — a direct sum**\n>\n> text\n\n## 1C Subspaces\n\n\n\nBody   \n"
    assert parse._tidy_markdown(md) == "> **1.43 Example — a direct sum**\n>\n> text\n\n## 1C Subspaces\n\nBody"


def test_draw_marks_outlines_candidates(axler_pdf: Path) -> None:
    png = pdf.render_page_png(axler_pdf, 21, 100)
    scan = pdf.figure_candidates(axler_pdf, 21)
    w, h = pdf.page_size(axler_pdf, 21)
    marked = parse.draw_marks(png, scan.candidates, w, h)
    with Image.open(io.BytesIO(marked)) as im, Image.open(io.BytesIO(png)) as orig:
        assert im.size == orig.size
        x0, y0, x1, y1 = scan.candidates[1].bbox
        sx, sy = im.width / w, im.height / h
        r, g, b = im.convert("RGB").getpixel((round((x0 + x1) / 2 * sx), round(y1 * sy)))
        assert r > 180 and g < 80 and b < 80  # red outline on the bottom edge
