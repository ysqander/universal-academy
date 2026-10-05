# Universal Academy — Technical Spec (MVP)

Companion to [PRD.md](PRD.md). Contracts live in code and win over this document if they disagree:
`backend/app/schemas.py` (HTTP API), `backend/app/llm_schemas.py` (LLM structured outputs),
`backend/app/db.py` (SQLite schema), `frontend/src/types.ts` (TS mirror of the API).

## 1. Architecture

```
Browser (React 19 + Vite + TS, responsive: desktop / iPad / phone)
   │  JSON over /api  (Vite dev proxy → :8000; prod: FastAPI serves frontend/dist)
FastAPI (Python 3.12, uvicorn)
   ├─ ingest/      PDF open, outline → chapter tree, page render, figure candidates, page parsing
   ├─ pipeline/    build job: render → parse → graph → lessons → visuals → verify → finalize
   ├─ learning/    grading, FSRS + FIRe-lite scheduler, task selector, XP
   ├─ api/         routers: books, courses, study
   └─ llm.py       OpenAI wrapper (structured outputs, retries, concurrency, fake mode)
SQLite (data/academy.sqlite3) + files (data/books/<book_id>/…, data/courses/<course_id>/…)
External: OpenAI Responses API · NVIDIA Nemotron Parse (build.nvidia.com, optional)
```

Run: `./run.sh` (builds frontend if needed, starts uvicorn on :8000). Dev: `uvicorn app.main:app --reload` + `npm run dev`.

## 2. Files on disk

```
data/books/<book_id>/book.pdf
data/books/<book_id>/pages/<page>@<dpi>.png        # cached renders (thumbnails + source viewer)
data/courses/<course_id>/pages/<page>.png          # parse-resolution renders (UA_RENDER_DPI, default 200)
data/courses/<course_id>/marks/<page>.png          # renders with numbered candidate boxes (set-of-mark prompt)
data/courses/<course_id>/figures/<figure_id>.png   # cropped figures (2x scale, small padding)
```

## 3. Ingestion

### 3.1 Book import
- `POST /api/books` (multipart `file`) or `POST /api/books/import-path {path}` (local file path; copied into data/).
- Extract metadata title/author (fallback: filename), page count, page labels (`page.get_label()` → fallback str(i+1)).
- Outline: `doc.get_toc()` → nested `OutlineNode` tree. end_page of a node = (start of next node at same or higher level) − 1, clamped to the parent's end; the last top-level node ends at the last page. Ids `n0..nN` in TOC order.
- No outline → `outline_source="detected"`: send the text layer of the first ~25 pages to the LLM to detect the table of contents (chapters/sections with printed page numbers), then map printed numbers → PDF indexes via page labels (or by detecting the offset where printed number "1" appears). If no text layer, `outline_source="none"` and the UI offers manual page ranges.
- `has_text_layer`: any of the first 30 pages yields >200 chars of text.

### 3.2 Figure candidates (deterministic, PyMuPDF)
Per page, in PDF points:
1. `page.cluster_drawings()` → rects. Reject: thin rules (w<4 or h<4), tiny (<400 pt²), near-full-page, and "boxes" — a cluster containing a filled path whose rect covers ≥70% of the cluster area (definition/theorem/margin-note backgrounds).
2. Raster images: `page.get_image_info()` bboxes ≥ 40×40 pt.
3. Nemotron (when available): every `Picture` block (normalized bbox × page size) — if it overlaps a cluster, use the union of overlapping clusters (tighter for vector art); otherwise keep Nemotron's bbox. `Caption` blocks are attached to the nearest figure (below or beside).
4. Merge overlapping candidates (IoU > 0.3 or containment), label `M1..Mk` in reading order.

### 3.3 Page transcription (OpenAI vision, set-of-mark)
- Input: page render at 200 dpi with candidate boxes drawn in red and labelled M1..Mk; the PyMuPDF text layer as a hint ("may have broken math; trust the image"); Nemotron's blocks (type + text) as a second hint when available.
- Output: `PageTranscription` (markdown with `[[figure:M2]]` placeholders, figures, rejected_marks, items, uncertain).
- Figures accepted by the model are cropped (bbox + 4pt padding, rendered at 3× zoom) → `figures` rows with id `p{page}-f{n}`; placeholders rewritten to `[[figure:p21-f1]]`.
- Pages are processed concurrently (UA_LLM_CONCURRENCY). Each page result is persisted immediately → re-runs skip finished pages.

## 4. Build pipeline (background job)

`POST /api/courses` creates the course + job and starts `asyncio.create_task(run_build(course_id))`. Stages (JobStage keys) and what they persist:

| Stage | Work | Persists |
|---|---|---|
| render | render each selected page at UA_RENDER_DPI | course page PNGs |
| parse | §3.2 + §3.3 per page | `pages`, `figures` |
| graph | one high-reasoning call over the whole chapter markdown (`<<page N · p.L>>` markers, figure list) → `KnowledgeGraphDraft`; then validation | `skills`, `edges`, `foundations`, course.big_picture/excluded |
| lessons | per skill (concurrent): `LessonDraft` from skill spec + its source pages (±1 page) + prerequisite/dependent skills + book exercises on those pages | skills.lesson_json, `questions` |
| visuals | per skill: separate **visualization planner** call → `VisualizationPlan`; function plots are pre-sampled with SymPy (≤ 21 parameter values × 200 points) | skills.viz_json |
| verify | per machine-checkable question: independent solve (`IndependentAnswer`) with a different prompt; compare with the grader (§6.1). Disagreement or reported problem → `flagged=1` (kept out of scoring) | questions.verify_json/flagged |
| finalize | initialize `progress` rows (available if no in-chapter prerequisites, else locked), course.status=ready | progress |

Rules:
- The graph appears in the UI as soon as the graph stage finishes; lessons fill in as they complete (`lesson_status`).
- Every stage is idempotent and resumable: `POST /api/courses/{id}/resume` re-runs skipping completed work. On server start, jobs left `running` become `interrupted`.
- Failures of single pages/skills are logged and retried once; the job only fails if the graph stage fails or >50% of lessons fail.
- Job log lines are human readable ("Parsed p.7 — 2 figures, 3 boxed items").

### 4.1 Graph validation (deterministic, networkx)
- Unique keys; drop prerequisite links to unknown keys; clamp weights to [0.05, 1].
- Cycles: repeatedly remove the lowest-weight edge of any cycle (log it).
- `order_index` = topological order, ties broken by first source page then key.
- `reduced` = edge is in the transitive reduction (drawn by default).
- Skill count sanity: if < 5 or > 40, log a warning (no retry in MVP).

### 4.2 Prompting principles (graph + lessons)
- Nodes are **abilities**, not headings: objective starts with a verb; split when abilities can be learned/forgotten independently; stop when one small family of questions assesses it.
- Include conceptual abilities (explain / distinguish / predict / interpret / justify), not only procedures. Proof-oriented books (Axler) need "prove/justify" skills with free-response questions + checklists.
- Edges mean "needed before"; encompassing weight says how much practising the dependent skill exercises the prerequisite (drives FIRe-lite).
- Preserve the author's notation and definitions; cite printed item numbers (1.3) and pages.
- Lessons: Why it matters → The idea → Worked example → Your turn (tier ramp 1→3) → Connection; short main path; depth behind "Show the missing steps". Questions must be self-contained, assess the stated objective, use only taught or prerequisite material; prefer machine-checkable forms; adapt book exercises when suitable (cite them).

### 4.3 Visualization planner (its own step)
Asks first "what will the learner likely misunderstand, and which representation makes *that* clearer?", then picks one format:

| Need | Default format |
|---|---|
| Two easily-confused ideas | comparison_table |
| Follow an algebraic argument | equation_steps (changed terms highlighted) |
| Spatial relationship | diagram_2d (or the book's figure) |
| How a quantity varies | function_plot |
| A sequence / transformation | frames (ordered stills; animation later) |
| Genuine parameter dependency | function_plot with one constrained parameter + a question |
| Definition already clear | none |

Prefer the book's own figure when one fits. Never 3D. Interactivity only when moving it answers the learner prompt.

## 5. HTTP API

| Method & path | Body → Response |
|---|---|
| GET `/api/health` | → `Health` |
| GET `/api/books` | → `BookSummary[]` |
| POST `/api/books` | multipart file → `Book` |
| POST `/api/books/import-path` | `ImportPathRequest` → `Book` |
| GET `/api/books/{bid}` | → `Book` |
| GET `/api/books/{bid}/pages/{page}.png?dpi=60` | → PNG (cached; thumbnails dpi≈40-60, viewer 150) |
| POST `/api/courses` | `CourseCreate` → `Course` (job started) |
| GET `/api/courses` | → `CourseSummary[]` |
| GET `/api/courses/{cid}` | → `Course` (includes job + stats) |
| GET `/api/courses/{cid}/job` | → `JobStatus` |
| POST `/api/courses/{cid}/resume` | → `JobStatus` |
| DELETE `/api/courses/{cid}` | → 204 |
| GET `/api/courses/{cid}/graph` | → `CourseGraph` |
| GET `/api/courses/{cid}/figures/{fid}.png` | → PNG |
| GET `/api/courses/{cid}/source/{page}` | → `SourcePage` |
| GET `/api/skills/{sid}/lesson` | → `Lesson` (404 until ready) |
| POST `/api/foundations/{cid}/{key}` | `FoundationKnownRequest` → `FoundationNode` |
| GET `/api/courses/{cid}/next` | → `NextTaskResponse` (active task + ranked menu; no side effects) |
| POST `/api/courses/{cid}/continue` | → `Task` (resume active task or start the top option; 409 if nothing to do) |
| POST `/api/courses/{cid}/tasks` | `StartTaskRequest` → `Task` (manual override: learn/review a chosen skill; 409 if another task is active unless `replace_active`; locked skills need `force`) |
| GET `/api/tasks/{tid}` | → `Task` |
| GET `/api/settings` / PUT `/api/settings` | `{daily_goal}` |
| POST `/api/tasks/{tid}/practice` | → `Task` (lesson read → practice phase, first question served) |
| POST `/api/tasks/{tid}/answer` | `AnswerSubmit` → `AnswerResult` |
| POST `/api/tasks/{tid}/easier` | → `Task` (swap current question for an unseen lower-tier one) |
| POST `/api/tasks/{tid}/abandon` | → `Task` |
| POST `/api/questions/{qid}/hint` | → `HintResponse` (next hint; recorded server-side for the current attempt) |
| POST `/api/questions/{qid}/reveal` | `RevealRequest` → `RevealResponse` (marks the current attempt as revealed, unless it is a free-response comparison after a committed answer) |
| POST `/api/questions/{qid}/flag` | `FlagRequest` → `Task` (question excluded; attempts on it voided; replacement served) |

Errors: JSON `{"detail": "..."}` with proper status codes. All times UTC ISO strings.

## 6. Learning engine

### 6.1 Grading (`learning/grading.py`)
- multiple_choice / true_false: index equality.
- numeric: parse both with SymPy (`2/3`, `sqrt(2)`, `1e-3`), |a−b| ≤ tolerance (default 1e-6 relative-ish: `max(tol, 1e-9*|b|)`); complex supported.
- expression: normalize learner input (`^`→`**`, unicode − × ·, `i`/`j` → `I`, implicit multiplication via SymPy parser transformations, LaTeX-ish `\frac{a}{b}`, `\sqrt{}` best-effort); tuples/vectors compared elementwise; equivalence by `simplify(a−b)==0`, falling back to numeric sampling at random points. Never `eval`.
- free_response: self-graded ("correct" | "partial" | "incorrect") against the checklist after seeing the model solution; "partial" counts as incorrect for passing but is logged.
- Feedback strings are short and specific when cheap (e.g. "Your answer is the conjugate of the expected one").

### 6.2 Skill states
`locked` (some in-chapter prerequisite not learned) → `available` → `in_progress` (lesson attempted) → `learned` (lesson passed; FSRS card created) → `mastered` (card stability ≥ 21 days). Unlocking is recomputed after every task. Learned skills never re-lock.

### 6.3 Lesson task (Math Academy anatomy)
A lesson = intro (why it matters, the idea, notation, visual) + 2-3 **knowledge points** (KP), each = short explanation + worked example + a pool of 4 questions.
- Phase `learn` shows the intro; `POST /practice` moves to phase `practice` and serves KP 1's first question. The UI shows the current KP's explanation + worked example above the question (scroll back any time).
- **KP pass**: 2 *independent* correct answers **in a row** (no hints, no solution reveal). Then the next KP starts. All KPs passed → lesson **passed**: status learned, FSRS card created (`review_card(Rating.Good)`), XP = est_minutes (clamped 3-20) + 2 bonus if no wrong answers.
- A hint-assisted or revealed correct answer resets the streak to 0 but isn't "wrong". A wrong answer resets the streak.
- **KP fail**: the KP's pool is exhausted (max 5 questions incl. any easier-step swaps) without 2 in a row → lesson **halted**: outcome failed, 0 XP, `cooldown_until = now + 2h` (it returns later — the selector re-offers it), `kp_failures[kp] += 1`. On the **second failure at the same KP** → remediation: the skill's direct prerequisites with the highest encompassing weights (top 2) are made due now (remedial reviews) and the result carries `suggestion="review_prerequisite"`.
- After 2 consecutive wrong answers the result carries `suggestion="easier_step"`; `POST /easier` re-shows the worked example and swaps in an unseen question from the current or previous KP with the lowest tier.
- Questions are served in pool order, never repeating a question within a task; on a later retry of the lesson prefer questions never seen before.

### 6.4 Review task (spaced repetition)
- FSRS (`fsrs` 6.x, desired_retention 0.9, `learning_steps=()` and `relearning_steps=()` so cards stay in Review state; fuzzing on).
- A review = 3 questions of the skill, one per KP where possible (mixed), preferring questions never answered before, then least-recently answered; flagged excluded. When fewer than 3 unseen questions remain for a skill, request a background top-up (pipeline `request_top_up`).
- **Order-sensitive score** (Math Academy): answer i (1-based) has weight i; credit 1 for independent correct, 0.5 for hint-assisted correct, 0 otherwise; `score = Σ wᵢcᵢ / Σ wᵢ`. ✖✔✖✔✔ → 0.73 (pass), ✔✔✖✔✖ → 0.47 (fail).
- Rating: score = 1 → Good (Easy if also every answer was faster than half the expected time — optional); 0.6 ≤ score < 1 → Hard; < 0.6 → Again. XP: 4 if ≥ 0.6 (+1 if perfect), 1 otherwise.

### 6.5 FIRe-lite (implicit repetition, after Math Academy: credit flows down, penalties flow up)
Let `w(S→P)` be the effective encompassing weight from skill S down to ancestor P (max over paths of the product of edge weights; ignore < 0.3).
- **Credit flows down.** On success on S (lesson pass, or review Good/Hard): for each learned ancestor P with w ≥ 0.5 whose card is due within 3 days → FSRS `review_card(P, Good)`, logged `implicit=1` (at most once per P per day). For 0.3 ≤ w < 0.5 → push `P.due` later by `w × (P.due − P.last_review)`, capped at 7 days. Every implicit credit is logged in `review_log`.
- **Penalties flow up.** On a failed review of S (Again): for each learned dependent D (S is an ancestor of D) with w(D→S) ≥ 0.5 → `D.due = min(D.due, now + 1 day)`.
- **Remediation flows down** only after a lesson plateau (§6.3: second failure at the same KP → top-2 prerequisites due now).

### 6.6 Task selection — menu + "Continue learning"
`GET /api/courses/{cid}/next` (no side effects) returns the active task (if any) + a ranked menu of up to 5 `TaskOption`s; `POST /api/courses/{cid}/continue` resumes the active task or starts the top option.
Candidates:
- **Reviews**: learned skills with due ≤ now. importance = 1 + (1 − retrievability) × 2 + 0.2 × #dependents.
- **Lessons**: available/in_progress skills with lesson ready and not in cooldown. importance = 1 + 0.3 × #transitive dependents + **repetition compression** bonus Σ w(S→P) over due prerequisites P (a lesson that implicitly practises due reviews is preferred; if it covers a due review with w ≥ 0.5, that review is dropped from the menu — the lesson's success will credit it).
- Lesson floor: if the last 3 completed tasks were all reviews and a lesson exists, the top option is a lesson.
- Interleave: no two consecutive options for the same skill; ties by order_index.
Every option carries a human-readable `reason` ("Review due — estimated recall 78%", "Next new skill — unlocks 3 others", "Also refreshes S2 and S4 which are due").
Nothing available → `message`: "All caught up — next review …" or "Lessons are still being generated…".
Session: daily goal 30 XP (kv `daily_goal`); `goal_reached` lets the UI show a clean stopping point. Course progress % = learned skills / total (Math Academy shows topics completed, not XP). Estimated completion = remaining XP (Σ est_minutes of unlearned skills) / avg XP per active day (last 7 days, default 30).

## 7. Frontend

Routes:
- `/` Library — books + courses, drop zone / file picker / local path import, per course: progress + "Continue".
- `/books/:bookId` Chapter picker — outline tree (click a chapter = select its range; checkboxes for several consecutive sections), page thumbnails of the selection, manual range, title, "Build course".
- `/courses/:courseId` Course — header (big picture, stats, **Continue learning**), build progress panel while building, **Knowledge graph** (React Flow + dagre, top→bottom; node colour = status; due badge; foundations as dashed nodes) with **List** tab (default on phones), skill drawer (objective, kind, source pages, prerequisites, unlocks, Learn/Review buttons, open source passage).
- `/courses/:courseId/study` Study — one task at a time: lesson sections (Why it matters → The idea → Visual → Worked example (step-by-step reveal) → Show the missing steps → Practice) then question cards (MC buttons / numeric & expression input with live KaTeX preview / free response + self-grade checklist), hint ladder, Show solution, Give me an easier step, This question seems wrong, Open source passage (page image modal), XP bar, end-of-session screen.
Markdown everywhere through one `<Markdown>` component (react-markdown + remark-gfm + remark-math + rehype-katex; normalizes `\( \)`/`\[ \]` to `$`/`$$`). Visuals through `<VisualizationView>` (figure / table / equation steps / SVG diagram / SVG plot with slider / frames stepper).

## 8. Configuration (.env at project root)

`OPENAI_API_KEY` (required), `NVIDIA_API_KEY` (optional), `UA_MODEL_VISION` (gpt-6-luna), `UA_MODEL_GRAPH`/`UA_MODEL_LESSON`/`UA_MODEL_VIZ` (gpt-6.1-sol), `UA_MODEL_VERIFY` (gpt-6-luna), `UA_EFFORT_*` per role, `UA_MODEL_VISION`, `UA_MODEL_GRAPH`, `UA_MODEL_LESSON`, `UA_MODEL_VIZ`, `UA_MODEL_VERIFY`, `UA_PARSE_ENGINE` (auto|openai|nemotron), `UA_LLM_CONCURRENCY`, `UA_RENDER_DPI`, `UA_MAX_COURSE_PAGES`, `UA_DATA_DIR`, `UA_FAKE_LLM` (1 = offline canned responses for tests/UI work).

## 9. Testing

- `backend/tests/`: pytest, offline by default (`UA_FAKE_LLM=1`, small synthetic PDF + the Axler PDF when present for outline/figure tests). Graph validation, grading, scheduler/FIRe-lite, selector, API smoke (TestClient), full pipeline with the fake LLM.
- `backend/scripts/e2e_real.py`: real build of Axler §1A (pages 15-24) against the live APIs.
- Frontend: `npm run build` (tsc strict) must pass; manual E2E through the browser.
