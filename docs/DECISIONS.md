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
| Lesson generation **v2** (planner → writer → reviewer with a missing-bridge pass and a density/outcome pass → one revision, a second one only for what is still blocking; may split a skill) | Command line only: `backend/scripts/replan.py`, `backend/scripts/regenerate.py` | Run per skill by a developer (so far from Claude Code). Applied to S2 and S3 of the real course. ~$0.25–0.40 per skill vs ~$0.10 for v1. |
| Pedagogy / code / browser reviews by Claude Code agents | Development only | Not part of the app. |

Decision (2026-10-06): keep v2 off the in-app build for now; use it per skill from the command line while the
generator is still being tuned.

**Providers (decided 2026-10-08, the learner: "switch between openAI and anthropic").** Every LLM role runs on OpenAI
or Anthropic (`UA_LLM_PROVIDER`, per role `UA_PROVIDER_<ROLE>`; default OpenAI until a Claude-generated lesson has been
judged). Anthropic takes Opus 5.5 where OpenAI uses Sol and Haiku 5.5 where it uses Luna, at the same per-role efforts
(Opus is twice Sol's price; Haiku costs what Luna does). **Prompts — option A:** the learner's rules are written once in
`app/prompts/rules.py` and both providers follow them; each provider's wording and the corrections tuned against its
model live in its own set (`app/prompts/openai/`, `app/prompts/anthropic/`), so tuning one never changes the other. A
rule still written inside a provider template moves to rules.py the next time it changes. The automatic checks
(proof track, answers the learner can read or type back, served sequence, grader) are code and apply to both. Before
Anthropic becomes the default: the verification benchmark (§6) is rerun with Haiku 5.5, and a Claude S4a is judged
blind against the Sol one (with GPT as a second reviewer, since the judges are Claude models).
First live check (2026-10-08, $0.17): every output schema works on Haiku 5.5 and Opus 5.5, with page images and
prompt caching (a second call read 6.4K cached tokens at a tenth of the price). Anthropic refuses to compile the
grammar of the four largest schemas (VisualizationPlan, TeachingPlan, LessonDraft, LessonV2Draft: "The compiled
grammar is too large" — stripping descriptions, enums or number types does not help), so those are answered with the
schema in the system prompt and validated as usual, a rejected reply retried with the error (`llm_anthropic`,
prompted mode). The key must belong to a workspace, or ANTHROPIC_WORKSPACE_ID must name one.
**Claude vs GPT on S4a (2026-10-08).** The proof skill S4a was generated twice with the same current rules: all-Claude
(Opus 5.5 plans, writes and reviews; Haiku verifies) and Sol. Blind judging in both orders: the two Claude judges
preferred the Opus lesson, the two GPT (Sol) judges preferred the Sol lesson, each "somewhat better" — self-preference,
so a tie on quality. No wrong key in either (89 and 84 items recomputed), no blocking design issue in either. Cost:
$7.67 for Opus vs $1.15 for Sol (Opus thinks far more: 25K-token plan, 40K-token draft, 32–40K-token reviews); $2.20
of the Opus run was reviews cut off at the 32K ceiling and re-run, so Anthropic calls now get twice the ceiling
(billing counts only tokens used). Verification: Haiku 5.5 equals Luna once its wording keeps reasoning out of
`problems` (§6).
**Opus effort for proof skills (2026-10-08, the learner has $200/month of Anthropic credits, so Claude is the
provider).** S4b generated four times on Opus 5.5: twice at the proof notch (planner and reviewer xhigh, writer high)
and twice one notch lower (high / medium). Lower: about 45% cheaper ($3.18–3.37 vs $5.15–6.53) and twice as fast, but
weaker — blind learner judges preferred the current setting in 3 of 4 judgments, the only math error was at the lower
setting, and the best lesson (1 blocking issue) was at the current setting. Decision: keep the notch for proof skills.
All four S4b lessons called cancelling α circular in the uniqueness proof (it is not: the inverse exists by
hypothesis); CIRCULARITY_RULE in app/prompts/rules.py now goes to both providers' planner, writer and reviewer.

## 2. Lessons

- **Skills are outcomes** (decided 2026-10-07, from the learner's review of S3b). A skill names a task the learner
  can do at the end — solve an equation, find all roots, construct an example, **prove or show a statement (a proof
  is an outcome, so proof skills stay separate units)**. In the learner's words: "learn to do something, not just
  identify or evaluate unless it is really really complex." Evaluating, checking a supplied answer or identifying
  are steps inside the skill that uses them (a knowledge point, an inline check, a capstone step) — e.g. computing
  $\beta/\alpha$ is part of "solve $\alpha z=\beta$", not a lesson before it. A judgement keeps its own skill only
  when it is itself the hard part (is this a vector space? is this sum direct?). Applies to the graph builder (in-app
  builds) and the v2 planner. **This replaced** the earlier rule "checking a supplied answer and constructing one are
  different skills", which had produced S2c ("verify a supplied cube root").
- **One coherent outcome per lesson.** The v2 planner's split test asks whether a node holds *separate outcomes* —
  tasks someone could do one of and not the other, each worth doing alone — and may split a skill (S2 → S2a–S2d,
  S3 → S3a–S3b). A step on the way to the outcome stays in the lesson even when it needs its own reasoning. Split parts
  keep the original's place in the learning order, in the planner's letter order. The lesson opens with the outcome
  problem and solves it on the main path.
- **Teach, don't summarise.** "Reduce the number of new ideas the learner handles at once; do not reduce the
  reasoning that connects them." Concrete case before the general formula (for procedures), one running example,
  notation introduced before use, the goal of a transformation explained before doing it (e.g. why make a product
  real), every algebra line shown on first encounter.
- **One new idea at a time** (v2 lessons; decided 2026-10-07; S3b was "a little dense and unclear": division as multiplication
  by the reciprocal + a second layout + a general formula in new letters + a derivation from associativity). Each
  block introduces at most one new idea and declares it (`new_idea`, not shown); about four per lesson. One method,
  shown one way (alternatives go to "Go deeper"); routine justifications are a clause unless the skill is a proof;
  nothing is used before it is taught; a prerequisite fact the lesson relies on is recalled in one line, not "as you
  already know"; notation stays stable; every misconception the lesson names is tested (S3b's summary warned "β/α,
  not α/β" without teaching or checking it — the learner's one miss was exactly that). Counting: a recalled
  prerequisite fact, or the same move on another case or law, is not a new idea; in a proof the strategy counts once.
- **Re-planning uses the learner's evidence.** The planner sees "This step isn't clear" notes and the learner's missed
  or hint-assisted answers on the skill (unanswered quiz questions, voided attempts and flagged questions left out).
  A wrong answer that shows a misunderstanding becomes a "Learner's miss" misconception, which the lesson re-tests
  with NEW numbers that set the same trap, in a format at least as demanding as the one it was missed in (a typed
  miss typed; a miss on a choice question typed when its demand has a value), as a question served first or a
  capstone step — the missed problem with the wrong answer as an option is not a test (first run: every miss came
  back only as a distractor on the same numbers). Such a re-test is a new case, never drill. A slip in a prerequisite
  (5+(−8) written as 3) is a "Prerequisite slip", not a Learner's miss: no bridge, a one-line recall and one typed
  question served first with the case. At most 3 misconceptions per skill (slips included), each with evidence, and
  all of them tested.
- **Short lessons** (decided 2026-10-07, after lessons of 11–13 blocks read as long): at most 9 blocks (10 when a case
  the learner missed needs its own) and about 350 words of prose outside the math. A further case is a block only
  when it needs a different decision; the same move on another case goes to a worked example or the practice. No
  block duplicates a knowledge point's worked example, and the summary does not restate the generalize block. More
  than 10 blocks or 420 words sends the draft to the revision. Proof lessons follow the proof track (below).
- **The proof track** (decided 2026-10-08 on branch `proof-lessons`, from the learner's review of S4a — "quite
  convoluted": a trivial faded step, 12-term expansions, new letters in every question, "got lost interchanging the
  symbols" between the two calculations; design in docs/lesson-v2.md). The teaching proves ONE statement three times:
  on small numbers, with one input fixed where the learner computes one route, and in general as a worked table read
  once. One named strategy, one letter set for the whole lesson, slot tables before every product, a "why is one
  example not a proof" reflect and a scope check; practice transfers the strategy to statements the teaching did not
  prove. Proof skills run one reasoning notch higher on the same model (Sol; the learner chose this over Astra).
  **Replaced** "the teaching proves 1–2 representative statements in full".
- **The proved result is never what the learner types** (the learner, 2026-10-08: "obviously the student doesn't need
  to type the expected final result which is what is expected to be proven. The steps before are what matters"). In a
  proof walkthrough the second route's final value equals the first route's by the statement itself, so it is shown
  in the explanation, never scored; the learner types route A's result, route B's first leg and the inputs of route
  B's last product.
- **Nothing essential in optional sections.** "Go deeper" may only hold an alternative explanation or a deeper
  derivation. (v1's "Show the missing steps" hid needed reasoning — the original S2 complaint.)
- **Checks inside the teaching sequence.** 1–2 graded inline checks plus one ungraded "why" reflect check, all within
  the 9 blocks; a faded example's missing step is always machine-checked and is the lesson's key move, not printed
  arithmetic.
- **No "Common mistakes" sections** (the learner found them useless). Known misconceptions still feed distractors.
- **Visuals must earn their place**: "this helps the learner notice ___"; "none" is valid; never 3D or decorative.

## 3. Questions

- **Typed answers first.** Multiple-choice + true/false together ≤ 40 % of a lesson (drafts above 50 % are rejected).
  A `true_false_set` with ≥ 3 statements does not count as choice-based. Among the questions **served first** (the
  first `target_questions` of each knowledge point) at most one is choice-based and at least one asks for a typed
  answer (v1 and v2 prompts; a draft-time check for v2) (decided 2026-10-07: with target 1 per point and "no drill", the served questions had drifted to multiple
  choice answerable by elimination). A different demand with a value is typed (prediction → the value, spot the error
  → the corrected value, reverse problem → the missing number). Distractors are computed answers of specific
  misconceptions, the key differs from all of them, and no option states the result.
- **Never "How many of these are true / belong to …".** It hides which statement was wrong. Use `true_false_set`
  (mark each statement; feedback names the wrong ones). Rejected at validation.
- **No free-response "show your working" questions** in new content. Proofs and "explain why" become multistep
  walkthroughs (choose the justified next step / the law used). Legacy free-response questions still work.
- **Honest question counts.** Each knowledge point sets `target_questions` (1–3: how much there really is to
  demonstrate) and has target + 2 questions (extras for misses and reviews).
- **Every question a different demand** — new case, prediction, spot the error in a shown working, reverse problem,
  interpretation — not the same procedure with new numbers. Among the questions served before the capstone the main
  procedure appears exactly once, as a typed answer on fresh numbers (the general case; procedure and computation
  skills — in a proof skill each knowledge point's first question walks through a statement the teaching did not
  prove). A spare may repeat a served demand once on new numbers: it is the recovery question after a miss. Every question must need this
  lesson's idea (no bare prerequisite facts). Graded means served: inline checks are formative and spare questions
  may never be served, so neither counts as testing a misconception.
- **An answer the learner can read is not a question** (2026-10-07, round-2 evaluation). A question whose answer is
  printed in its prompt, a step, a bank of lines, the teaching text or a worked example (for expressions with letters),
  forced by a definition, an earlier step's result again, or one of a step's options, is found mechanically
  (`questions.answer_known_problems`). The revision is told; afterwards a question served first that is still blocked
  (this check or the final review) is rewritten on its own in its slot, and such spares are left out while spares last.
  Stored lessons: `scripts/regenerate.py --only questions [--note "S8b k2q3: …"]` (the new version takes the old one's
  slot; the old row keeps its id and answers as retired history).
- **Answer forms.** Complex numbers are always asked and keyed as `a+bi` (never `(real, imaginary)` pairs); when the
  problem already uses the letters a or b, the prompt says "enter it as a complex number in standard form (e.g.
  $3-2i$)" instead. A tuple key
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

A broken capstone is **rewritten, not cut** (2026-10-07): when the capstone still breaks a rule after the lesson's
revision, or verification withholds it, the writer rewrites only the capstone (strictly validated, verified again).
Dropping a step left dangling "the root from Step 2" references in S2d, and a withheld capstone left S4a without one.
A step may refer only to earlier steps that exist. Stored lessons: `scripts/regenerate.py --only capstone [--note …]`.

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
- **Anthropic: `claude-haiku-5-5` at effort `high`** (benchmark rerun 2026-10-08 on the same 70 items through today's
  verify path, course notation included, two runs per setup): Luna high 8/8 caught, false flags 0 and 1; Haiku high
  8/8, false flags 1 and 0; Haiku medium 8/8, 1 and 1. Haiku's false flag (R45) was its own reasoning written into
  `problems` on a true/false question it answered correctly; one sentence in the Anthropic set's VERIFY_INSTRUCTIONS
  ("never put your reasoning in problems") removed it: 8/8 and 0 false flags in two more runs. Haiku costs about twice
  Luna ($0.014–0.018 vs $0.007 per 70 questions, about $0.06 per chapter) and answers faster (2.0 s vs 3.0 s).
- **Disagreements on `true_false_set` questions and multistep steps** are re-solved once by `gpt-6.1-sol`;
  agreement on the re-check keeps the question (`agree_on_recheck`). So is any **reported problem** (the solver agreed
  with the key but named a defect, e.g. "option 3 is also correct", or was unsure), with the defect shown to the
  re-checker (2026-10-07: one such false alarm withheld S4a's whole capstone).
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
- **No bypassing the computation, in every lesson** (2026-10-08, the learner: "Make the student do proper work and
  make it not possible to bypass the intended exercise. this is not just for proofs"). For a key with letters, an
  answer that still holds a complex computation — the question's product typed back, '(a+bi)(c+di)', 'c(a+bi)',
  '(a+bi)^2' — is "Not yet computed" (asked again, not counted), like an unsimplified number. Unexpanded real
  coordinates ('a(−d)−bc+(ac+b(−d))i') are accepted. A real key with letters takes no i.
  The same day's bypass audit typed every expression printed in the course's 411 typed questions back as the
  answer; 18 passed. Closed in the grader: list arithmetic typed back ('compute i(2,−1)' → 'i(2,-1)', '(a,b)+(c,d)')
  is "work out each component", and a one-symbol key ('v', '2v') refuses '-(-v)', 'v+0', 'x+x'. Closed at
  generation: a question whose prompt lets the learner type the answer back (`typed_back`, e.g. "forces λ = −1+2i"
  for the key {−1+2i}) is rewritten like a printed answer. Not counted: a small integer, a zero answer, "or enter ∅".
- A single value for a pair key (or vice versa) is invalid input (asked again), not wrong.
- Roots left in a denominator (e.g. 1/√2) count as simplified (Axler doesn't require rationalising).

## 8. Working on the project

- Test servers must run with blank keys (`OPENAI_API_KEY= NVIDIA_API_KEY=`) or `UA_FAKE_LLM=1`: background question
  top-ups otherwise make live API calls.
- Stop the app before `replan.py` / `regenerate.py` (they refuse while :8000 answers); both back up the database.
- **Tests can't reach live data or paid APIs** (added 2026-10-07): when `pytest` is loaded, `config.py` forces the
  fake LLM, blank keys and a temporary data dir (also when `UA_DATA_DIR` names the live dir), even if
  `tests/conftest.py` was bypassed. Why: a review agent imported a test module from outside `backend/tests`; the run
  wrote a "Tiny" test course into the live database and made paid OpenAI calls. Run tests from `backend/` as in
  CLAUDE.md.
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
- Questions in the real course whose prompt prints the answer (bypass audit 2026-10-08): ~~S8b k3q3~~ (rewritten the
  same day), S1 k1q4, S11 k2q1, S24 k1q1 (v1 lessons — fixed when re-planned);
  inline checks S3b block 8 and S8b block 9 ask for a value their block prints.
- The Library had no way to delete a course or a book (course deletion sat only at the very bottom of the course
  page; books had none). Added 2026-10-07: a delete button on every course card and book row, each with a
  confirmation; a book can be deleted once no course is built from it.
- S2c ("verify a supplied cube root") predates the outcome rule. The learner has attempted it, so `replan.py` can't
  split or remove it; it is re-planned as a kept skill whose objective is the computation (a "show that" about one
  given number is a computation, not a proof). Folding it into S2b/S2d would need a "merge" operation (not built).
- Roll v2 out beyond S2/S3 skill by skill if the learner approves (§1).
- The proof track's S4a (sample 3 of three live samples, judged blind "much better" than the old lesson) is in the real
  course since 2026-10-08. Next: one proof skill per pattern (S4b find_then_check, S16 suppose_two, S17 law_chain, S18
  two_halves, S20 conditions_or_counterexample), each run in a sandbox and judged before import.
- Quiz difficulty adaptation is a simple tier nudge; Math Academy also weighs response time (not done).
- Evaluation of the 2026-10-07 re-plans (8 lessons, independent judges; all "much better" than v1, no wrong keys):
  the patterns it found — recognition-heavy served questions, misses re-tested only on the same numbers, long
  lessons, blocking issues left after one revision, proof practice replaying the teaching — were addressed the same
  day (§2, §3; second revision for what is still blocking; blocked spare questions left out). Re-evaluated after
  regenerating the 8 lessons (round 2): all better than round 1, 8–9 blocks, at most one served choice question, misses
  re-tested typed on new numbers, no wrong keys. Still open (next iteration):
  1. ~~Served questions whose answer is printed or forced~~ — addressed the same day (§3, "An answer the learner can
     read"); elimination from a shown working's wording and line-number keys are still only caught by review.
  2. Misconceptions are often tested only by a capstone choice, a spare, or a key the misconception also reaches.
  3. ~~A served question the final review blocks is kept~~ — it is rewritten in its slot now (§3).
  4. ~~Proof practice still specialises statements the teaching proved~~ — the proof plan's `practice_statements`
     (2026-10-08, proof track).
  5. Capstone chains are often nominal (a one-line prerequisite step) or a special case.
  6. Inline checks and faded examples sometimes print the arithmetic they ask for.
  7. Proof walkthroughs: a justification choice can often be answered from the stem (a distractor uses the very law
     the prompt forbids), and a walkthrough may type one side or non-matching coordinates, leaving the comparison to
     the explanation (judges' check of the rewritten S4a questions, 2026-10-07).
