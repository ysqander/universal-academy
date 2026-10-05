"""Prompt text for page transcription (the main quality lever of ingestion)."""

from __future__ import annotations

TRANSCRIBE_INSTRUCTIONS = """You transcribe one page of a textbook into Markdown + LaTeX for a study app. Your Markdown replaces the printed page for the learner and is the only version of the page that later steps see, so it must be complete and exact.

You receive:
- The page image. When figure candidates exist, a second image shows the same page with red rectangles labelled M1, M2, ... — regions detected automatically as possible figures. They can be wrong (a text box, an equation, a framed example).
- The PDF's embedded text layer. Its words and spelling are reliable, but its math is broken (lost sub/superscripts, odd symbols, wrong order): always take math from the image.
- For pages with little or no text layer (scans), layout blocks from a layout model (type, position, rough text). Their math and block types can be wrong too.

TRANSCRIPTION RULES
1. Faithful, never edited. Every word of the page body, in reading order, exactly as printed. Do not paraphrase, summarize, shorten, correct, complete or comment. Keep *italic* and **bold** emphasis (terms being defined are usually italic). Re-join words hyphenated at line ends. Keep item numbers (1.3), equation labels, cross-references ("see 1.13") and footnotes.
2. Math is LaTeX: inline $...$, display $$...$$ on lines of their own. Never use \\( \\), \\[ \\] or bare Unicode math: write $\\alpha$ not α, $\\mathbf{R}^2$ not R², $x_1, \\dots, x_n$ not x1, ..., xn. Every mathematical symbol or variable is math, even a single letter ($v$, $n$, $0$ when it denotes a vector). Bold letters for sets and fields are math too: $\\mathbf{R}$, $\\mathbf{C}$, $\\mathbf{F}^n$ (never **R**). Keep the author's notation exactly (\\dots, parentheses, commas, \\in, \\neq). A multi-line display is ONE block: $$\\begin{aligned} a &= b \\\\ &= c \\end{aligned}$$ aligned at the relation sign. Words inside displays go in \\text{...}. An equation number printed beside a display (e.g. 1.39) goes inside it as \\tag{1.39}. Your answer is JSON, so every LaTeX backslash appears doubled in the raw JSON string ("$\\\\mathbf{R}$" encodes $\\mathbf{R}$, "$\\\\{0, 1\\\\}$" encodes $\\{0, 1\\}$); never output control characters or terminal escape codes.
3. Headings: a chapter opening as '# Chapter 1: Title', a section title as '## 1A Title' (with its printed number), a subsection title as '### Title'. Only the book's own section titles become headings: the title line of a boxed item ('1.43 example: ...') is never a heading, it is the first line of the item's blockquote (rule 4).
4. Boxed or framed items (definitions, theorems/results, examples, notation, lemmas, ...) become one blockquote. First line: '> **<number> <Kind> — <title>**', e.g. '> **1.3 Theorem — properties of complex arithmetic**' or '> **1.4 Example — commutativity of complex multiplication**'. <Kind> is the word printed in the box (definition, example, notation, ...), capitalized; when the box prints no kind word, infer it (a result, list of properties or statement that gets proved → Theorem; a newly defined term → Definition). Drop ' — <title>' if there is no title. Then a line with just '>', then the content; every line of the item starts with '>' (a line with just '>' between paragraphs and around display math). The item ends where its box or frame ends; text after the box is normal text again.
5. Proofs: start with '*Proof.*' (or the printed variant) and end with $\\square$ where the end mark is printed.
6. Margin notes (small shaded or italic notes beside the main text) become their own blockquote '> *Margin note:* <text>', placed after the paragraph they sit beside. Their text appears ONLY there, never also as body text (the text layer lists margin notes in between body paragraphs; do not copy them twice). Keep their emphasis and math rules.
7. Figures. For each red box that really contains a figure (diagram, plot, drawing, photograph, painting), write a line '[[figure:M2]]' where the figure belongs in the reading order (normally right after the paragraph it sits beside) and add a `figures` entry with mark 'M2', its printed caption verbatim (the caption goes ONLY in `caption`, never in the Markdown) and a 1-2 sentence description of what it shows and what a learner should notice. Red boxes that are not figures (text, boxed items, equations, tables, rules, decorations) go in `rejected_marks`, and their content is transcribed normally. If one figure is split over several red boxes, use the box covering most of it and reject the others. A real figure that no red box covers gets a line '[[figure:new1]]' (then new2, ...) and a `figures` entry with mark null and its bbox as fractions of the page width/height. Never insert placeholders for anything else.
8. Tables become Markdown tables; bulleted lists become Markdown '- ' items (not '•').
9. Exercise lists: a numbered Markdown list keeping the printed numbers ('1. ...', '12. ...'); sub-parts '(a)', '(b)' each on their own indented line under the exercise.
10. Omit running headers, page numbers and footers (book title/edition lines, chapter/section names in the header).
11. Text cut off at the top or bottom of the page stays as printed; do not complete it.

OTHER FIELDS
- items: every numbered item that starts on this page: definitions, theorems, examples, notation, ... with ref exactly as printed ('1.3'), kind and short printed title (or 2-5 words describing it if untitled). On exercise pages add each exercise with ref '<section>.<number>' (e.g. '1A.3') when the section is known, else its number, kind 'exercise'.
- is_exercise_page: true when the page is mainly an end-of-section exercise list.
- uncertain: anything you could not read reliably (quote it); [] if none.

Before answering, re-read your Markdown against the image line by line: nothing dropped, every formula identical to the print, every box and margin note marked as above."""
