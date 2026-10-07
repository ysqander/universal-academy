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
SQLite (<data>/academy.sqlite3) + files (<data>/books/<book_id>/…, <data>/courses/<course_id>/…); <data> = UA_DATA_DIR, default ~/Library/Application Support/UniversalAcademy (outside the cloud-synced project)
External: OpenAI Responses API · NVIDIA Nemotron Parse (build.nvidia.com, optional)
```

Run: `./run.sh` (builds frontend if needed, starts uvicorn on :8000). Dev: `uvicorn app.main:app --reload` + `npm run dev`.

## 2. Files on disk (DB paths are stored relative to the data dir, so the data folder can move)

```
<data>/books/<book_id>/book.pdf
<data>/books/<book_id>/pages/<page>@<dpi>.png        # cached renders (thumbnails + source viewer)
<data>/courses/<course_id>/pages/<page>.png          # parse-resolution renders (UA_RENDER_DPI, default 200)
<data>/courses/<course_id>/marks/<page>.png          # renders with numbered candidate boxes (set-of-mark prompt)
<data>/courses/<course_id>/figures/<figure_id>.png   # cropped figures (2x scale, small padding)
```

## 3. Ingestion

### 3.1 Book import
- `POST /api/books` (multipart `file`) or `POST /api/books/import-path {path}` (local file path; copied into <data>/).
- Extract metadata title/author (fallback: filename), page count, page labels (`page.get_label()` → fallback str(i+1)).
- Outline: `doc.get_toc()` → nested `OutlineNode` tree. end_page of a node = (nearest later start at the same or higher level, in page order) − 1, or that start page itself when the next section begins below the top of its page (TOC destination y; if unknown, the shared page is included); clamped into the parent's range; the last top-level node ends at the last page. Ids `n0..nN` in TOC order. Titles are prettified at import (R^n → Rⁿ, x^2 → x²).
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
- Figures accepted by the model are cropped (bbox + 4pt padding, rendered at 3× zoom) → `figures` rows (stored as `<course_id>-p21-f1`, exposed by the API as the local id `p21-f1`); placeholders rewritten to `[[figure:p21-f1]]`.
- Escape glitch guard: the vision model occasionally emits LaTeX backslashes as JSON control escapes (destroying `\mathbf{R}`, `\{0,1\}`). `normalize_math` repairs what it can; any formula still lost triggers one retry with an explicit escaping reminder, and anything still unreadable is kept visible as `⟨?⟩` and listed in `uncertain` — content is never silently deleted.
- Nemotron is optional and never fails a page: malformed responses fall back to no layout blocks; after 3 consecutive failed pages it is switched off for the rest of the run (circuit breaker).
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
| verify | per machine-checkable question: first a no-model check that the answer key agrees with the question's own displayed answer (else `key_mismatch` → flagged); then an independent solve (`IndependentAnswer`, model `gpt-6-luna`, given the course's notation such as "F: R or C" and told to work inside it); compare with the grader (§6.1). Disagreement or reported problem → `flagged=1` (kept out of scoring) | questions.verify_json/flagged |
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

### 4.2b Lesson generation v2 (experimental — see [lesson-v2.md](lesson-v2.md))
Planner (`TeachingPlan`, may split a node) → writer (`LessonV2Draft`: ordered teaching blocks with inline checks and
faded examples, then practice knowledge points) → missing-bridge reviewer (`LessonReview`) → one revision. Stored as
`lesson_json.format = "v2"`; the API returns `Lesson.format/blocks/goal_line/assumed/out_of_scope/deeper`. Applied per
skill with `backend/scripts/replan.py --course <id> --skill S2 [--dry-run] [--no-split]`; a split
replaces the node by narrower skills (keys S2a, S2b, …, kept at the original's place in the learning order), rewires
edges, recomputes order/reduction and unlocks, and is only allowed while the original skill has no answered attempts
and no FSRS card. `scripts/regenerate.py` rewrites a v2 skill from its stored teaching spec. Inline checks and block
feedback carry `Lesson.version`; a stale page gets 409 and a reload prompt.

Question rules (all generators): no free_response "show your working" questions (computations are typed answers; proofs become multistep walkthroughs); each KP sets `target_questions` (1–3: how much there really is to demonstrate) and gets target + 2 questions that each ask something different (new case, prediction, spot the error, reverse problem — not the same procedure with new numbers); every new lesson ends with a multistep capstone (`role='capstone'`, 2–4 machine-checked steps, each with a hint and an explanation) that chains the lesson's ideas with earlier skills; no "common mistakes" section (v1 and v2 lessons both end with a capstone). Complex-number answers are always asked and keyed as a+bi. Capstone validation rejects steps whose answer is given away (printed in the problem or an earlier step, or forced by a definition such as "multiply back and get 1"); the capstone must chain this lesson's idea with an earlier skill using fresh numbers, not replay a practice question. A halted lesson's cooldown is honoured on manual starts too (409 "comes back at HH:MM" unless `force`). Never ask "how many of these are true / belong to …" — use `true_false_set`
(the learner marks each statement true or false; answer = one letter per statement, e.g. "FTT"; feedback names the
statements that were marked wrong). With ≥ 3 statements it does not count toward the choice-question cap. A tuple
answer becomes an unordered set only when the prompt says the order of the answers does not matter.

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
| POST `/api/courses/{cid}/continue` | → `Task` (resume active task, else a due quiz, else the top option; 409 if nothing to do) |
| POST `/api/courses/{cid}/tasks` | `StartTaskRequest` → `Task` (manual override: learn/review a chosen skill; 409 if another task is active unless `replace_active`; locked skills need `force`) |
| GET `/api/tasks/{tid}` | → `Task` |
| GET `/api/settings` / PUT `/api/settings` | `{daily_goal}` |
| POST `/api/tasks/{tid}/practice` | → `Task` (lesson read → practice phase, first question served) |
| POST `/api/tasks/{tid}/answer` | `AnswerSubmit` → `AnswerResult` (multistep: one submission per step with `step`; 409 if it is not the current step) |
| POST `/api/tasks/{tid}/easier` | → `Task` (swap current question for an unseen lower-tier one) |
| POST `/api/tasks/{tid}/abandon` | → `Task` |
| POST `/api/questions/{qid}/hint[?step=i]` | → `HintResponse` (next hint, or the hint of multistep step *i*; recorded server-side for the current attempt) |
| POST `/api/questions/{qid}/reveal` | `RevealRequest` → `RevealResponse` (marks the current attempt as revealed, unless it is a free-response comparison after a committed answer) |
| POST `/api/skills/{sid}/blocks/{i}/check` | `BlockCheckRequest` → `BlockCheckResult` (v2 inline check; formative, not recorded) |
| POST `/api/skills/{sid}/blocks/{i}/feedback` | `BlockFeedbackRequest` → 204 ("This step isn't clear") |
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

### 6.3 Lesson task (score-based — iteration 3, after the learner's review of S2)
A lesson = the teaching part (v2: the teaching sequence; v1: intro sections) + 1–3 **knowledge points** (KP), each = short explanation + worked example + a pool of questions, + (new lessons) a final **capstone**: one multistep problem (2–4 guided, machine-checked steps) that chains the lesson's ideas and earlier skills.
- Phase `learn` shows the teaching part; `POST /practice` moves to phase `practice`. The UI shows the current KP's explanation + worked example above the question.
- **What is served.** KP *i* has a target *tᵢ* (`knowledge_points[i].target_questions`, 1–3; legacy lessons: 2). Serve *tᵢ* questions of the KP in pool order; after a wrong or assisted answer in a KP, one extra question of that KP is added (at most +2 per KP, while unseen questions remain) so the learner can recover. Then the next KP. After the last KP: the capstone (question with `role = 'capstone'`, kind `multistep`) when present and unflagged.
- **Points.** 1 = independent correct · 0.5 = correct with ≥ 1 hint (no reveal) · 0 = wrong, or answered after revealing the solution. Legacy free_response self-grade: correct 1 (0.5 if assisted), partial 0.5, incorrect 0. Multistep: each step 1 / 0.5 (its hint used) / 0 (wrong or revealed); the question's points = mean of its steps; the capstone has **weight 2** in the score.
- **Score** = Σ weight·points / Σ weight over answered questions (voided excluded). **Pass mark 80 %.**
- **Pass**: everything planned answered and score ≥ 0.8 → learned; FSRS card created with Good (score ≥ 0.9) or Hard (0.8–0.9); XP = round(est_minutes × score), clamped 3–20, +2 when the score is 100 %.
- **Fail**: as soon as 0.8 is unreachable even with every remaining planned question answered independently (or everything is answered and the score is below 0.8) → lesson halted: 0 XP, `cooldown_until = now + 2h`, retried later with fresh questions; on the **second failure** of the same lesson → remediation (top-2 prerequisites by weight made due now) + `suggestion="review_prerequisite"`.
- Easier step: after a wrong answer in the current KP, `POST /easier` swaps in an unseen, strictly lower-tier question of that KP; it is scored normally.
- Integrity: hints are recorded server-side (`POST /api/questions/{qid}/hint`, `?step=i` for a multistep step; `QuestionPublic.hints_used`); revealing the solution before answering makes the attempt worth 0. Answers equal in value but not simplified get a 422 "simplify" message and are not counted.
- Legacy proof rule (only for lessons that still contain free_response questions): a KP with a free_response question also needs one independent self-graded "correct" written answer; if none can be obtained the requirement is waived and the first FSRS rating is Hard.
- New lessons contain **no free_response questions** (no "show your working"); proofs are taught through multistep walkthroughs (choose the justified next step / the law used) and the capstone. No "common mistakes" section is generated or shown.
- Questions are served in pool order, never repeating a question within a task; on a later retry of the lesson prefer questions never seen before.

### 6.4 Review task (spaced repetition)
- FSRS (`fsrs` 6.x, desired_retention 0.9, `learning_steps=()` and `relearning_steps=()` so cards stay in Review state; fuzzing on).
- A review = 3 questions of the skill (mixed KPs; when the skill has a capstone, it may replace one of them — weight 2), preferring questions never answered before, then least-recently answered; flagged excluded. When fewer than 3 unseen questions remain for a skill, request a background top-up (pipeline `request_top_up`).
- **Order-sensitive score** (Math Academy): answer i (1-based) has order weight i (× 2 for a capstone); points as in §6.3 (1 / 0.5 / 0; multistep = mean of steps); `score = Σ wᵢcᵢ / Σ wᵢ`.
- Rating: score ≥ 0.8 → Good (Easy optional when perfect and fast); 0.6 ≤ score < 0.8 → Hard; < 0.6 → Again. XP: 4 if ≥ 0.8 (+1 if perfect), 2 if ≥ 0.6, 1 otherwise.

### 6.7 Quizzes (timed, forced — validate what reviews consolidate)
Reviews consolidate one skill at a time on the FSRS schedule (untimed, help at half credit). Quizzes validate: a
timed, cumulative, mixed check with no help, after Math Academy (≈ every 150 XP).
- **Due** when the course has earned ≥ 150 XP since the last quiz (quiz XP excluded) **and** ≥ 5 skills were learned
  since the last quiz. A due quiz is **forced**: an active lesson/review may be finished first, then `/continue`
  starts the quiz, the menu (`/next`) offers only the quiz, and manual starts of other tasks get 409 ("Take the quiz
  first — about N minutes"). A quiz cannot be abandoned.
- **Content**: 6–8 single-answer questions (no multistep, no free response), one per skill: skills never quizzed yet
  first (earliest learned first, so every skill gets quizzed once), then already-quizzed skills with the lowest recall; unseen questions, tier ≥ 2 preferred; difficulty
  nudged towards an ~80 % average (last quiz ≥ 90 % → harder tiers, < 70 % → easier).
- **Timed**: allowance 60 s (multiple choice, true/false) or 90 s (numeric, expression, true_false_set) per question ×
  `quiz_time_factor` (settings, default 1.0), rounded up to whole minutes. The clock starts with `POST /practice`
  (intro screen first) and runs on the server: answers after the deadline get 409 and the quiz is finished;
  unanswered questions score 0. Leaving the page does not stop the clock.
- **Conduct**: one question at a time; no hints, no solutions, no easier step, no lesson or source view (the lesson and
  source endpoints answer 409 while a quiz is open — a regular quiz from its intro, a retake once its clock runs);
  "Pass" = 0; flagging a question voids it. Correctness is not shown until the end (`AnswerResult.status =
  "recorded"`). Answers are accepted up to 2 s after the deadline (network delay). An unsimplified but correct value
  scores 0 in a quiz (a "simplify" prompt would reveal that the value is right); unreadable input is still asked again.
- No quiz is due while fewer than 5 learned skills have a quiz-eligible question (the learner must never be stuck).
- **Score**: 1 correct / 0 otherwise; pass mark 80 %. XP = round(time-limit minutes × score).
- **Afterwards** (`Task.quiz.results`): every missed skill gets an immediate remedial review (top of the menu); a
  correct answer counts as an FSRS review (Good) only if that skill was due within 3 days. Below 80 % an optional
  **retake** (same skills, fresh questions, not forced, does not reset the 150-XP counter) unlocks once the
  remedial reviews are done; a retake can be left at its intro (it stays on offer) but not once its clock runs.
  Missed skills' reviews rank above everything else, the lesson floor is suspended and they get no FIRe implicit
  credit until reviewed. Every question shown in a finished quiz counts as seen (not re-served in its reviews).

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
Locked skills are never offered (a learner may still start one with "Learn anyway" = `force`; once started, it can be resumed without `force`).
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

## 9. Maintenance

`backend/scripts/regenerate.py --course <id> [--skills S1,S4] [--only lessons,visuals,verify] [--dry-run]` rewrites a course's lessons/visuals/verification with the current prompts while keeping learner progress (answered questions are retired, not deleted; a DB backup is written first). It refuses to write while the app answers on :8000 (stop it first, or `--force`); questions stay servable during re-verification and Ctrl-C leaves a resumable job. `backend/scripts/try_parse.py <pdf> <page>…` shows how a page is transcribed.

## 10. Testing

- `backend/tests/`: pytest, offline by default (`UA_FAKE_LLM=1`, small synthetic PDF + the Axler PDF when present for outline/figure tests). Graph validation, grading, scheduler/FIRe-lite, selector, API smoke (TestClient), full pipeline with the fake LLM.
- `backend/scripts/e2e_real.py`: real build of Axler §1A (pages 15-24) against the live APIs.
- Frontend: `npm run build` (tsc strict) must pass; manual E2E through the browser.
