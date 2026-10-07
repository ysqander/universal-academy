# Decisions — lessons, questions, scoring, verification

Why the app teaches and checks the way it does. Most of these came from the learner studying the generated Axler ch. 1
course (S1–S3) and reviewing it (Oct 2026). Read this before changing generation prompts, question validation, the
learning engine or verification. Behaviour details are in [SPEC.md](SPEC.md) (§4.2b, §6.3, §6.4); the lesson-writing
design and the approved reference lesson are in [lesson-v2.md](lesson-v2.md).

When you change one of these decisions, update this file (and the SPEC section it points to).

## 1. Where generation runs

| What | Runs where | Notes |
|---|---|---|
| In-app course build (Build course button) | OpenAI + NVIDIA APIs, inside the app | Lessons in the **v1** format (fixed sections), but with all question rules of §3, a capstone (§4) and verification (§6). No skill splitting, no teaching-sequence writer, no reviewer pass. |
| Lesson generation **v2** (planner → writer → missing-bridge reviewer → one revision; may split a skill) | Command line only: `backend/scripts/replan.py`, `backend/scripts/regenerate.py` | Run per skill by a developer (so far from Claude Code). Applied to S2 and S3 of the real course. ~$0.25–0.40 per skill vs ~$0.10 for v1. |
| Pedagogy / code / browser reviews by Claude Code agents | Development only | Not part of the app. |

Decision (2026-10-06): keep v2 off the in-app build for now; use it per skill from the command line while the
generator is still being tuned.

## 2. Lessons

- **One coherent objective per lesson.** The v2 planner applies a split test — *could someone succeed at one
  substantial part and fail another because they need different reasoning?* — and may split a skill (S2 → S2a–S2d,
  S3 → S3a–S3b). Split parts keep the original's place in the learning order, in the planner's letter order.
  Checking a supplied answer and constructing one are different skills.
- **Teach, don't summarise.** "Reduce the number of new ideas the learner handles at once; do not reduce the
  reasoning that connects them." Concrete case before the general formula (for procedures), one running example,
  notation introduced before use, the goal of a transformation explained before doing it (e.g. why make a product
  real), every algebra line shown on first encounter.
- **Nothing essential in optional sections.** "Go deeper" may only hold an alternative explanation or a deeper
  derivation. (v1's "Show the missing steps" hid needed reasoning — the original S2 complaint.)
- **Checks inside the teaching sequence.** 1–3 inline checks before practice; a faded example's missing step is always
  machine-checked; at most one ungraded "why" reflect check.
- **No "Common mistakes" sections** (the learner found them useless). Known misconceptions still feed distractors.
- **Visuals must earn their place**: "this helps the learner notice ___"; "none" is valid; never 3D or decorative.

## 3. Questions

- **Typed answers first.** Multiple-choice + true/false together ≤ 40 % of a lesson (drafts above 50 % are rejected).
  A `true_false_set` with ≥ 3 statements does not count as choice-based.
- **Never "How many of these are true / belong to …".** It hides which statement was wrong. Use `true_false_set`
  (mark each statement; feedback names the wrong ones). Rejected at validation.
- **No free-response "show your working" questions** in new content. Proofs and "explain why" become multistep
  walkthroughs (choose the justified next step / the law used). Legacy free-response questions still work.
- **Honest question counts.** Each knowledge point sets `target_questions` (1–3: how much there really is to
  demonstrate) and has target + 2 questions (extras for misses and reviews).
- **Every question a different demand** — new case, prediction, spot the error in a shown working, reverse problem,
  interpretation — not the same procedure with new numbers. The main procedure appears at most once before the
  capstone. Every question must need this lesson's idea (no bare prerequisite facts).
- **Answer forms.** Complex numbers are always asked and keyed as `a+bi` (never `(real, imaginary)` pairs). A tuple key
  becomes an unordered set only when the prompt says the order of the answers doesn't matter. One question asks for
  one answer.
- **Known gap:** a multiple-choice question with more than one correct option is only caught by verification
  (S27 has three). A generation-time check would be cheap to add.

## 4. Capstone (end-of-lesson multistep problem)

Every new lesson (v1 and v2) ends with one multistep problem: 2–4 guided, machine-checked steps, each with a hint
and an explanation shown after the step. It must chain this lesson's idea with at least one earlier skill, use fresh
numbers, and not replay a practice question. No step may have an answer known in advance (printed in the problem or
an earlier step, or forced by a definition such as "multiply back and get 1"). Validation rejects such steps.
Why: the learner wanted guided, verifiable multi-step problems instead of "show your working", ending a series of
simpler questions so it proves they can chain concepts.

## 5. Scoring and progression (SPEC §6.3–§6.4)

- **Points:** 1 independent correct, **0.5 with a hint**, 0 wrong or answered after revealing the solution. A
  multistep question scores the mean of its steps; the capstone counts **double**.
- **Pass a lesson at 80 %** of the points of what was answered. A miss adds one extra question in that knowledge point
  (at most +2 per KP) so the learner can recover.
- **Early stop** as soon as 80 % is unreachable (also inside the capstone): 0 XP, the lesson returns after
  **2 hours** with fresh questions. The cooldown is honoured on manual starts too ("Start anyway" sends `force`). The
  second failure of the same lesson makes its top-2 prerequisites due (remediation).
- **First review:** FSRS Good when the lesson score ≥ 0.9, Hard for 0.8–0.9.
- **Reviews:** 3 questions (a capstone may replace one), order-weighted (later answers count more); ≥ 0.8 Good,
  0.6–0.8 Hard, < 0.6 Again.
- **XP:** lesson = round(est_minutes × score) clamped 3–20, +2 at 100 %; review 4 (+1 perfect) / 2 / 1.
- This replaced Math Academy's "two correct in a row" rule for all lessons (decided 2026-10-06, at the learner's
  request). Legacy lessons have target 2 per KP, so two wrong answers there now fail the lesson.
- Integrity: hints and reveals are recorded server-side; an answer equal in value but not simplified (e.g. the
  question's own expression retyped) gets a 422 "simplify" and is not counted.

## 5b. Quizzes (decided 2026-10-06)

Reviews consolidate (one skill, spaced, untimed, help at half credit); **quizzes validate** (several skills mixed,
timed, no help). Following Math Academy (a quiz about every 150 XP; missed topics are reviewed straight away; optional
retake), with the learner's choices:
- **Every 150 XP** (and at least 5 newly learned skills), **forced** ("we need a bit of discipline"): after the
  current task, nothing else can be started until the quiz is taken; it cannot be abandoned.
- **Timed** (60–90 s per question, adjustable with `quiz_time_factor`); the clock runs on the server.
- 6–8 single-answer questions, one per skill — never-quizzed skills first (coverage over recency: with more than 8
  new skills between quizzes the newest wait for the next one), then the weakest; no hints/solutions/lesson view;
  correctness shown only at the end; pass mark 80 %; missed skills → immediate review that ranks above everything;
  optional retake below 80 %.
- In a quiz an unsimplified correct value scores 0 (in lessons it gets a "simplify" prompt instead), and answers have
  a 2 s grace after the deadline.
Details: [SPEC.md §6.7](SPEC.md).

## 6. Verification

- **Model: `gpt-6-luna` at effort `high`** (unchanged). Benchmark on 70 real questions (2026-10-06): `gpt-6-luna`
  xhigh, `gpt-6.1-sol` low and `gpt-6-luna` high all caught 8/8 real problems; Luna high had the fewest false flags
  (1) and was cheapest ($0.035 per chapter vs $0.48 for Sol); Sol was 2.7× slower. There is no GPT-6 "Terra" tier.
  The test was too easy to rank models finely (8 positives, one run each).
- **Disagreements on `true_false_set` questions and multistep steps** are re-solved once by `gpt-6.1-sol`;
  agreement on the re-check keeps the question (`agree_on_recheck`).
- **No-model consistency check first:** a key that contradicts the question's own displayed answer is flagged
  (`key_mismatch`) at generation and at verification. It caught 5/6 planted wrong keys with 0 false alarms on 302
  real questions.
- **Course setting:** the solver gets the course's notation (e.g. "F: either R or C") as cached context and is told
  not to object about cases the book excludes (characteristic-2 fields). Re-checking the real course with this
  reduced verification flags from 11 to 3.
- Verification never clears a learner's own flag.

## 7. Grader conventions

- `ell`, `\ell` and `ℓ` are the variable `l`.
- Learners must simplify (`require_simplified=True`); the verifier may answer in any equivalent form.
- A single value for a pair key (or vice versa) is invalid input (asked again), not wrong.
- Roots left in a denominator (e.g. 1/√2) count as simplified (Axler doesn't require rationalising).

## 8. Working on the project

- Test servers must run with blank keys (`OPENAI_API_KEY= NVIDIA_API_KEY=`) or `UA_FAKE_LLM=1`: background question
  top-ups otherwise make live API calls.
- Stop the app before `replan.py` / `regenerate.py` (they refuse while :8000 answers); both back up the database.
- The project lives in ProtonDrive: after parallel edits, look for `*conflict*` copies and re-run all tests.
- **Live data lives outside the synced project folder** (decided 2026-10-07): `~/Library/Application Support/
  UniversalAcademy` on macOS (`UA_DATA_DIR` overrides). Why: on 2026-10-06 ProtonDrive left two torn "Edit conflict"
  copies of the live SQLite database while a script was writing (the main file was intact). `run.sh` writes one closed
  snapshot per day to the project's `backups/` (kept 14 days) — that folder is what gets synced. The pre-move folder is
  kept as `data-moved-2026-10-07/` until the learner deletes it.

## 9. Open items

- Real course: lessons generated before 2026-10-06 predate §3–§4 — 12 of them still serve free-response questions
  (S2a and the proof skills S4, S10, S15–S18, S20, S22, S23, S25, S28) and only S3a/S3b have a capstone. Regenerating
  a lesson (`scripts/regenerate.py`, keeps progress) applies the current rules. Three S27 multiple-choice questions
  with several correct options are withheld and need rewriting.
- Generation-time check for "more than one correct option" (§3).
- Roll v2 out beyond S2/S3 skill by skill if the learner approves (§1).
- Quiz difficulty adaptation is a simple tier nudge; Math Academy also weighs response time (not done).
