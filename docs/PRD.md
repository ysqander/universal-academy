# Universal Academy — PRD (MVP)

**One line:** pick a chapter of a textbook you own; the app turns it into a Math-Academy-style knowledge graph of micro-skills with real lessons, checked practice and spaced repetition — and then simply tells you what to do next.

## Problem
- Textbooks explain well (context, motivation, figures) but give no continuity: you choose exercises yourself, they are often too hard, motivation fades.
- Math Academy has continuity (knowledge graph, mastery gating, spaced repetition, "next task" queue) but lessons are drill-heavy and light on *why*; reviewers call it "a sea of isolated facts", procedural, a black-box queue.
- Wanted: Math Academy's system **plus** the textbook's understanding, generated from the books already on my laptop.

## User
One self-directed adult learner (me), studying math/physics from PDFs (e.g. Axler, *Linear Algebra Done Right*: proof-based, LaTeX-dense, definition/theorem boxes, vector figures, margin notes). Desktop first; same web UI must work on iPad/phone.

## MVP scope (must)
1. **Import a textbook** — drag-and-drop/upload a PDF (or give a local path). Text-born PDFs with math, boxes and figures are the target; scanned books are best-effort.
2. **Select a chapter easily** — the book's outline as a clickable tree (chapter → sections, printed page numbers), page thumbnails of the selection, manual page range fallback. One click on "Chapter 1" selects its pages.
3. **Book → course pipeline** with visible progress: render pages → parse (layout, LaTeX, figures — Nemotron Parse + OpenAI vision) → knowledge graph → lessons → visualization plans → answer verification. The graph is visible as soon as it exists; lessons fill in.
4. **Knowledge graph you can see** — nodes are *abilities* ("Distinguish $z^2$ from $|z|^2$"), not headings; arrows mean "needed before", with encompassing weights; status colours (locked / available / in progress / learned / mastered / due); assumed outside knowledge shown as separate "foundation" nodes with a quick self-check. List view for phones.
5. **Lessons attached to every node** — Why it matters → The idea (author's notation, source references) → one purposeful visual → 2-3 knowledge points, each a worked example + practice → Connection (what this unlocks). "Show the missing steps", "Open source passage" (the actual book page), book figures reused.
6. **Practice that is checked** — multiple choice, true/false, numeric, symbolic expressions (SymPy equivalence), free-response proofs with a self-assessment checklist. Progressive hints, worked solutions, "give me an easier step", "this question seems wrong" (excluded from scoring). Auto-gradable answers are independently re-solved by a second model; disagreements are withheld.
7. **Progress + spaced repetition** — Math Academy rules: a knowledge point passes after 2 independent correct answers in a row; a failed lesson halts and returns later; repeated failure triggers prerequisite review. FSRS schedules reviews per skill; FIRe-lite gives implicit review credit to prerequisites. "Continue learning" picks the next task (menu of ≤5 with a reason for each), XP (1 XP ≈ 1 focused minute), daily goal, clean session end.

## Explicitly later
Multi-chapter/whole-library graphs, accounts & sync, native apps/offline, quizzes and multistep tasks, diagnostic placement, animations and generated videos, AI grading of proofs as the sole judge, editing the graph by hand, leagues/streak gamification, public sharing of book-derived content (kept private).

## Visualization principle
A dedicated planner step first names the likely misunderstanding, then picks the *simplest* representation that fixes it: book figure, comparison table, highlighted equation steps, 2D diagram, function plot (one constrained slider only when moving it answers a question), ordered frames — or nothing. Never decorative, never 3D by default.

## Success test (one chapter, one week)
1. I can open the app and start meaningful work without deciding what to study.
2. When stuck, it offers a useful smaller step instead of frustration.
3. Days later I can answer a *different* question on the same idea without rereading.
4. Build quality: a chapter like Axler §1A–1C (~25 pages) builds in < 15 min for < $5, with ≥ 90% of auto-checked questions verified.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| App shape | FastAPI + SQLite + React (Vite) SPA, served by one process | simple local app now, hostable later, responsive web for iPad/phone |
| Page parsing | PyMuPDF figure candidates (vector drawings) + Nemotron Parse (layout, pictures, captions) + `gpt-6-luna` vision transcription with numbered candidate boxes | math books draw figures as vectors; Nemotron finds figures well but garbles math; Luna is cheap and strong at OCR but weak at boxes |
| Reasoning models | `gpt-6.1-sol` (graph: high effort; lessons: medium; visuals: low), `gpt-6-luna` (verification) | near-top quality at ~1/5 of Astra's price; all configurable in `.env` |
| Scheduler | FSRS (py `fsrs` 6.x) + FIRe-lite | proven scheduler; Math Academy's key idea (implicit repetition through the graph) in a simple form |
| Content | generated once per course, stored, served statically | stable history; no live chat dependency |

Details: [SPEC.md](SPEC.md).
