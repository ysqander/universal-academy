# Decisions — lessons, questions, scoring, verification

Why the app teaches and checks the way it does. Most of these came from the learner studying the generated Axler ch. 1
course (S1–S3) and reviewing it (Oct 2026). Read this before changing generation prompts, question validation, the
learning engine or verification. Behaviour details are in [SPEC.md](SPEC.md) (§4.2b, §6.3, §6.4); the lesson-writing
design and the approved reference lesson are in [lesson-v2.md](lesson-v2.md).

When you change one of these decisions, update this file (and the SPEC section it points to).

## 1. Where generation runs

| What | Runs where | Notes |
|---|---|---|
| In-app course build (Build course button) | The API (or Claude Code agents on the agent route) + NVIDIA, inside the app | Since 2026-10-11: **v2 lessons** (`pipeline/build_v2.py`, below). Until then lessons in the **v1** format (fixed sections) — still available with `UA_LESSON_GENERATION=v1`. |
| Lesson generation **v2** (planner → writer → reviewer with a missing-bridge pass and a density/outcome pass → one revision (a second round until 2026-10-11, see below); may split a skill) | Command line only: `backend/scripts/replan.py`, `backend/scripts/regenerate.py` | Run per skill by a developer (so far from Claude Code). Applied to S2 and S3 of the real course. ~$0.25–0.40 per skill vs ~$0.10 for v1. |
| Pedagogy / code / browser reviews by Claude Code agents | Development only | Not part of the app. |

Decision (2026-10-06): keep v2 off the in-app build for now; use it per skill from the command line while the
generator is still being tuned. **Replaced 2026-10-11** (the learner: "make the button use v2"): every lesson of the
in-app build is a v2 lesson. Skills are planned along the graph: a skill as soon as all of its prerequisites are
planned, so a split's keys and edges are final when its dependents are planned (independent skills plan in parallel,
splits are applied one at a time), and each lesson is written, reviewed, revised, stored and verified by
`generate_v2_lesson` as soon as its plan is applied. The spec a lesson is written from is kept (kv `v2_spec:<skill
id>`, also for the parts of a `replan.py` split) until it is stored, so Resume rewrites a failed lesson without planning
(or splitting) it again; an interrupted build cancels its plans and writers. Cost: about $2–5 per skill with Claude (a 40-skill
chapter ≈ $100–150) instead of ~$0.10–0.30 for v1; `UA_LESSON_GENERATION=v1` keeps the old builder.

**Agent route (2026-10-10, the learner: "use my Claude Code credits instead of the API … do the same work as you
would do through the API, but do it yourself with agents").** With `UA_AGENT_DIR` set (`app/agent_llm.py`), every
model call is written as a request file — the same instructions, context, images and output schema — and answered by
Claude Code agents; the answer is checked against the same schema (`python -m app.agent_llm validate`). API keys are
blanked. Nothing else changes: planning, writing, reviews, revisions, repairs and verification all run, so an
agent-made lesson goes through every check an API one does. A Claude Code workflow claims the requests and dispatches
one agent per call, matching the Anthropic mapping (Haiku for verification, the session model otherwise, at the
role's effort). A lesson takes about 40–60 minutes (several run in parallel). Used for S14–S18.

**Model per stage and cost (2026-10-11, the learner asked where Sonnet 5.5 could replace Opus 5.5 before the next
rounds of content).** Measured on the S9–S12 API runs (108 Opus calls) and 951 agent-route requests: a v2 lesson costs
about $2.05–2.55 on the API ($4.7 for a proof skill), a chapter about $130–150 (lessons ≈ 97 %), and Opus output
(thinking) is 73 % of it; on the agent route a lesson takes ~55 minutes, most of it the planner and the two reviews.
Every quality complaint (S2/S3b density, S4a "convoluted", S12a's paragraph, the S16–S28 leaks, the S10 variant) came
from the planner, the writer, the reviews and the capstone writer, and an effort notch lower there had already lost
blind judgments — so they stay on Opus. Sonnet 5.5 was tried where the output is narrow and checked in code, on a copy
of the course with the same requests: **practice top-ups** (12 knowledge points × 3 questions): at medium effort 12 of
35 rejected by verification, 7 of them answer keys the independent solver disputed (Opus: 4, 1); at high effort 5 of 35
(all readable answers, caught in code) for half Opus's cost at the same speed, but blind judges preferred Opus's sets
9 of 12 (Sonnet's repeated existing demands or were plug-ins); **visuals** (S1, S5, S6): Opus 2 of 3, and the lost
Sonnet plan drew the correct steps in the "wrong answer" colour. Both stay on Opus; the new `topup` role
(`UA_ANTHROPIC_MODEL_TOPUP`, …) keeps them configurable. What saves money and time without a quality trade instead:
(1) **the prompt cache** — the system blocks are instructions, chapter prefix, then a prompted-mode schema, with a
breakpoint after the prefix, so a question / capstone / block repair reads the draft's cache entry instead of writing a
~50K-token one of its own (a repair was ~70 % cache writes: capstone repair $0.50 → ~$0.16, question repair $0.35 →
~$0.14); (2) **one revision round** for every lesson (`MAX_REVISIONS = 1`): a second round cleared its blocking issues
in 0 of 4 non-proof lessons and cost ~$0.73 and 15–20 minutes; (3) **the agent route uses the effort each request asks
for** (it ran every planner and review at xhigh — 3–8× slower than the API — and pages on the session model), reads
pages and checks answers on Haiku and Opus for the rest, and tells agents to read each request file once (a planner
agent took ~25 turns, each re-reading its context). Sonnet's cache-read price in the cost estimate is $0.20/MTok.

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
**Time per lesson (2026-10-09, the learner: "we can't have as default 1h40 per lesson").** Measured on the guided S4b:
39 minutes of generation (plan 6.7, write 6.3, three reviews 18.9, two revisions 6.4 min — all Opus, all in sequence;
time follows output tokens, 30–50K per call at about 115 per second) and about 16 more of Claude Code's audit (four
judges, a variant rewrite, re-verification). Sol is no faster (32–34 min). Across ten proof runs a second revision
round fixed what was left in 3 and changed nothing, was discarded as worse or failed validation in the others, at
~10 minutes each. Decided: a proof lesson **revises once**; the review of that revision runs at the role's base effort
(high, not xhigh: it checks, it does not write); blocking issues it still locates in teaching blocks get a **block
repair** (one writer call that returns only those blocks, kept only if the mechanical checks do not get worse —
`lesson_v2.patch_blocks`); the four-judge audit is no longer the default — only for the first lesson of a proof
pattern on this format, or when a blocking issue survives the repairs; lessons are generated ahead and in parallel.
Measured on S10 (next on the learner's path), both run in parallel: **21.5 minutes and $3.94** (plan 7.1, write 4.2,
review 5.9, revision 2.9, final review 1.3 min), final review 0 blocking — against 39 minutes and $6.53 for S4b.
The same with the planner and reviews one level lower (`UA_ANTHROPIC_EFFORT_GRAPH=medium`) saved nothing: its planner
produced two invalid plans before a usable one (9.2 minutes of planning), it cost $4.52, and its final review kept 3
blocking issues and a proof-track rule broken. Decision: keep the planner and the first review at xhigh.

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
  keep the original's place in the learning order, in the planner's letter order. A numbered key splits into letters
  (S12 → S12a, S12b); a lettered key into numbers (S12a → S12a1, S12a2, S12a3; decided 2026-10-10). The lesson opens with the outcome
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
  once. One named strategy, one letter set for the whole lesson, slot tables before every product, a scored check of
  which moves the proof may make (until 2026-10-10 a "why is one example not a proof" reflect and a scope check —
  dropped, see "No 'one example does not make a law' point" below); practice transfers the strategy to statements
  the teaching did not prove. Proof skills run one reasoning notch higher on the same model (Sol; the learner chose this over Astra).
  **Replaced** "the teaching proves 1–2 representative statements in full".
- **The proved result is never what the learner types** (the learner, 2026-10-08: "obviously the student doesn't need
  to type the expected final result which is what is expected to be proven. The steps before are what matters"). In a
  proof walkthrough the second route's final value equals the first route's by the statement itself, so it is shown
  in the explanation, never scored; the learner types route A's result, route B's first leg and the inputs of route
  B's last product.
- **Proof lessons: the proof is the scored exercise, then one variant** (decided 2026-10-09 by the learner after
  studying the proof-track S4a: what they disliked was "going through the exercises where we basically repeat the same
  thing again"; "whenever we're working out the proof, we are already scoring points, and then at the end … one
  wrinkle or variant … not repeating the same as before"). A proof lesson's graded inline checks are its **scored
  steps** — 4–6 of the proof's real moves: the concrete case's key computation(s), the guided case's key line, which
  moves the proof may make, and one '?' entry of the general table — each with a hint shown after a wrong first
  answer. The lesson ends with **one variant** in the capstone slot: a statement the teaching did not prove where the
  same strategy works but one thing changes, 2–3 steps, weight 2, no earlier-skill computation. The knowledge points'
  walkthroughs and true/false sets are still written but not served in the lesson: they are the pool of a retry and of
  the reviews. Teaching: 9 blocks (10 with the reflect, until 2026-10-10). Rule text once, for both providers:
  `prompts/rules.py` `GUIDED_PROOF_RULE`; engine `learning/guided.py`; points §5. **Replaced** "practice transfers the strategy" (the variant does it, once) and the
  4-step proof capstone (an earlier skill computes μ → route A → route B → USE). Lessons written before keep their
  old flow (S4a; lesson_json has no `"scoring": "guided"`).
- **No "one example does not make a law" point** (the learner, 2026-10-10, after S10 and S13: "a student knows this
  fact: one specific example does not make a law" — "redundant"; "It's more important to understand that a specific
  example should exemplify a general rule"; the S13 reflect "Why is knowing h(1/2)=3 not enough to specify h on
  [0,1]?"). No block, check, question, claim, option, misconception or note makes that point, in any lesson, and none
  asks why one value, example or special case does not prove or determine the general case. Numbers show the moves and
  the letters repeat them without a transition speech ("The same moves with letters"); the numbers may be named as one
  instance of the letters (α = 2+i is a = 2, b = 1); a definition says positively what determines its object. A
  counterexample that refutes "always" is a different point and stays. Proof track: no reflect check (9 blocks); the
  scope claims "the numbers / a fixed input proved it for every input" went — the scored check after the guided case
  asks which moves the proof may make (a real law inside a coordinate, the statement itself or an unproved law shown as
  a move, what equality needs, which stage proved what, where a hypothesis was used); knowledge point 2 is "What the
  proof rests on"; the misconception "an example taken as a proof" is not planned. Rule text once: `prompts/rules.py`
  `EXAMPLE_FEEDBACK` (rule 17, in every planner, writer and reviewer) and `EXAMPLE_POINT_RULE` (the v1 lesson and
  review top-up prompts); `pipeline/lesson_v2.example_point_problems` finds the wordings the stored lessons used and
  sends such a draft to the revision.
- **Deciding whether something meets a definition** (the learner, 2026-10-10, after S12a: "I don't understand this
  paragraph nor why ab is only applied to x1"; then a review of the lesson by GPT-6 Pro that the learner endorsed).
  The lesson teaches the decision process, not only the calculations: what the problem supplies (the proposed
  operations) and what is tested (the definition's requirements), and what settles the question — an equality that
  holds needs an argument for every input, one that fails needs one allowed choice, and a structure meets the
  definition only when every requirement is justified, the operations returning elements of the set included. The
  supplied operation keeps its own symbol ($a\star v$, $u\oplus v$), its rule is restated where it is used and said in
  words. Each requirement is first translated into a plain question as two procedures; an equality is compared
  coordinate by coordinate, naming the law of $\mathbf{F}$ that makes the coordinates agree, and never uses the law
  being tested. An element that must exist (identity, inverse) is found by solving the equation that defines its role,
  checked to lie in the set and verified; none exists when that equation has no allowed solution — trying candidates
  is not the method. Every choice of requirement or inputs gets a reason, and every worked example ends with what it
  established. One reasoning pattern per lesson — refute by a counterexample, verify for every input, find a required
  element — as separate short lessons in that order, with a genuine "yes" case; assessment asks for the decisions and
  transfers to a changed operation whose first natural test passes. Rule text once: `prompts/rules.py`
  `DEFINITION_CHECK_RULE` (planner, writer and reviewer of both providers; the split test names the three patterns).
  First applied to S12a → S12a1–S12a3 (plan written by hand from the review, lessons by Claude Code agents). Rule 17
  still holds: the review's "does (4,0) prove the requirement?" check and "an unsuccessful candidate does not
  establish nonexistence" went; their content is said positively (verify with letters; solve for the element).
- **Nothing essential in optional sections.** "Go deeper" may only hold an alternative explanation or a deeper
  derivation. (v1's "Show the missing steps" hid needed reasoning — the original S2 complaint.)
- **Checks inside the teaching sequence.** 1–2 graded inline checks plus one ungraded "why" reflect check, all within
  the 9 blocks; a faded example's missing step is always machine-checked and is the lesson's key move, not printed
  arithmetic. The reflect never asks why one example does not prove the general case (above). Proof lessons: 4–6
  scored steps and no reflect, in 9 blocks (above).
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
  may never be served, so neither counts as testing a misconception. In a proof lesson the scored steps and the
  variant are what is served (its knowledge points only in a retry and in reviews).
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

**Proof lessons** (2026-10-09, §2): the capstone slot holds the variant — the same strategy on a statement with one
thing changed, 2–3 steps, no earlier skill required (`proof_track.capstone_proof_problems` checks its shape; a variant
that breaks it, or whose answer the lesson prints, is rewritten like a capstone).

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
- **A retake gets fresh variants** (2026-10-11, the learner: students must not memorize answers): a failed lesson
  asks for new practice questions for every knowledge point with fewer never-answered questions than its target + 1
  (not only the one it stopped in, also after a capstone fail), and — once every capstone was answered — for one new
  capstone variant (v2 lessons; same decisions and step kinds, new numbers or case; held until verified; keys `cap2`,
  `cap3`, …). The same request runs again when the lesson starts after an earlier attempt (a top-up that failed is
  retried). Never-answered questions and capstones are served first; with no API key the retake reuses answered ones.
- **First review:** FSRS Good when the lesson score ≥ 0.9, Hard for 0.8–0.9.
- **Reviews:** 3 questions (a capstone may replace one), order-weighted (later answers count more); ≥ 0.8 Good,
  0.6–0.8 Hard, < 0.6 Again.
- **XP:** lesson = round(est_minutes × score) clamped 3–20, +2 at 100 %; review 4 (+1 perfect) / 2 / 1.
- This replaced Math Academy's "two correct in a row" rule for all lessons (decided 2026-10-06, at the learner's
  request). Legacy lessons have target 2 per KP, so two wrong answers there now fail the lesson.
- Integrity: hints and reveals are recorded server-side; an answer equal in value but not simplified (e.g. the
  question's own expression retyped) gets a 422 "simplify" and is not counted.
- **Guided proof lessons** (2026-10-09, §2): the scored steps count like questions (weight 1): right first time 1; a
  wrong first answer to a typed step or a statement set gets "not yet" and the step's hint — never the grader's
  diagnosis or which statements were wrong, which come once the step is done — then ½ if right; wrong twice or "Show
  answer" 0. A choice step has one try (a second would be won by elimination). An unreadable answer is not a try. The
  variant weighs 2. No early stop inside the teaching: when the steps leave 80 % out of reach even with the variant
  right, the attempt ends at "Start the variant" (failure key `guided`; 2-hour pause; counts toward remediation).
  Leaving an attempt keeps its answers — the next start continues it. After a finished attempt (passed or failed) the
  next one of the same lesson version is a retry: the steps are shown answered and not scored; the knowledge points'
  questions (and a capstone never answered before) are scored. Integrity: the server sends the lesson only up to the
  next open scored step (task and preview alike, "Go deeper" and the knowledge points' material withheld while the
  attempt is scored), the skill-scoped check endpoint refuses scored steps until the skill is learned, a step's answer
  and explanation reach the client only once it is done, and a done step is never graded again. A learner who
  believes a step's key is wrong reports it: the report is kept for review and shown to the planner
  (`block_feedback` kind `wrong_key`), but the score does not change — the branch review found that voiding on a
  report let a learner reveal every answer, report them all and pass on the variant alone. Leaving puts the open
  question back on the next start (the variant at its step), and its solution is not handed out in between.
- **Hint texts are sent only once used** (2026-10-09, no-bypass, every lesson and review): a question's hints and a
  multistep step's hint come from POST /hint, which records them (`QuestionPublic.hint_count`,
  `MultistepStepPublic.has_hint`); a later step's prompt and options are sent only when it is reached.
- **Marked passed by the learner** (`scripts/mark_passed.py`, app stopped): the weakest pass — Hard card (first
  review the next day), the XP of a pass at 80 %, no implicit credit to prerequisites. First used for S4a on
  2026-10-09: the learner had worked through the proof lesson twice and did not want to repeat practice that replayed
  it.
- **A faulty question found after the attempt** (`scripts/flag_question.py`, app stopped): flagged on the learner's
  behalf through the same path as the app's Flag button (`engine.flag`), so the attempt is re-scored without it — a
  failure in a flagged capstone is never the learner's. First used for S10 on 2026-10-10, with the learner's consent:
  its variant renamed x_k as c and had two ambiguous statements; without it the scored steps gave 90 % (pass, Good).

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
- **Readable answers are caught at verification too** (2026-10-09: a review served "Compute −7+5i as the sum of the
  real number −7 and the purely imaginary number 5i", written on 2026-10-05, before generation checked for it). Before
  any model call, verification runs generation's `answer_known_problems` on the question (with the lesson's text for a
  v2 lesson) and flags a hit (status `readable`; `keep_kps_servable` never releases it — better no question than one
  that gives its answer). `scripts/audit_questions.py` runs the same check over a stored course (no model call) and
  flags what it finds; the real course had 10 of 336 servable questions (S1, S6, S11, S16 ×3, S17, S24, S26 ×2).
- **Scored proof steps are verified too** (2026-10-09): they cost points now, so each is solved like a multistep step
  from the lesson text up to it, with the earlier steps' results; a step that still disagrees after the stronger
  model's re-check is withheld from scoring (`check.withheld`, records in `lesson_json.guided_verify`) and stays in the
  lesson as an unscored check. It runs before the lesson is stored (`verify_guided_lesson`) — rewriting a stored
  lesson changes its version under a running attempt. Re-verifying a stored lesson (`regenerate --only verify`, the
  build's resume when records are missing) carries running attempts over to the new version
  (`migrate_guided_attempts`); a step already withheld stays so while attempts exist.

## 7. Grader conventions

- `ell`, `\ell` and `ℓ` are the variable `l`.
- **Indexed names** (2026-10-10): a digit index is canonical (`x1` = `x_1`); a one-letter index glued to its
  coefficient splits off like a digit one (`ax_k` = a·x_k, `a_kx_k` = a_k·x_k); a longer index is one name (`a_ij`,
  `x_ky`, `a_{ij}`); braces or a Unicode subscript end an index (`x_{k}y` and `xₖy` = x_k·y, `x_{1}2` = x_1·2). Greek
  names keep their index (`lambda_k`, `θ_1`). Glued letters that spell a keyword stay letters, and a number before `j`
  or `x` is a coefficient, never a Python literal (`2j_k`, `0x_1`). An unknown name that splits into the key's symbols
  is invalid input with a hint ("write a·x_k"), not wrong.
- **Subscript key** (2026-10-10, the learner: "first type k then push button and it turns the k into subscript in the
  preview and is recognized well by the grader"). The answer box's key x<sub>k</sub> turns the index typed just
  before the caret into the subscript of the letter before it (`xk` → `x_k`, `x12` → `x_12`; a number coefficient is
  skipped: `2xk` → `2x_k`); pressed again it undoes. It does nothing where the grader would read the result
  differently and says why under the box: the index would split a name (`theta`, `ln x`), run into the letters after
  it (`xky`), change how an earlier index is read (`a_ijxj`), or sit after `pi`/`sqrt` (a number), inside a longer
  number, after a backslash or inside braces. The preview draws names exactly as the grader reads them
  (known names, Greek, roots that take one number or word, a function without brackets taking the rest of the
  product), so what is shown is what is graded. Seven adversarial review rounds compared the preview and the grader
  on generated inputs and on every stored key. Not handled (rare in this course): capital Greek, comma indices
  `A_{j,k}`, hand-typed LaTeX, `∞`, factorials.
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
  fake LLM, blank keys, a temporary data dir (also when `UA_DATA_DIR` names the live dir) and a temporary books dir
  (any `UA_BOOKS_DIR` outside the system temp dir is replaced), even if
  `tests/conftest.py` was bypassed. Why: a review agent imported a test module from outside `backend/tests`; the run
  wrote a "Tiny" test course into the live database and made paid OpenAI calls. Run tests from `backend/` as in
  CLAUDE.md.
- The project may live in a cloud-synced folder: after parallel edits, look for `*conflict*` copies and re-run all tests.
- **Live data lives outside the synced project folder** (decided 2026-10-07): `~/Library/Application Support/
  UniversalAcademy` on macOS (`UA_DATA_DIR` overrides). Why: on 2026-10-06 the cloud sync left two torn "Edit conflict"
  copies of the live SQLite database while a script was writing (the main file was intact). `run.sh` writes one closed
  snapshot per day to the project's `backups/` (kept 14 days) — that folder is what gets synced. The pre-move folder is
  kept as `data-moved-2026-10-07/` until the learner deletes it.
- **Book PDFs live in the project's `books/` folder** (decided 2026-10-10, the learner: "assume the app runs on a
  server … save the docs simply in a folder of the project"; so another chapter needs no new upload, and a book uploaded
  again is recognised): `books/<book_id>/book.pdf` (`UA_BOOKS_DIR` overrides), recorded as `<book_id>/book.pdf` and
  found by id. The exception to the rule above is safe because a PDF is written once (temp file + rename); the
  database, course files and page renders (`<data>/page-cache`) stay in the data dir. Not in git (private textbooks,
  GitHub's 100 MB limit, server uploads never round-trip): back up `books/` with the data dir; a server sets
  `UA_BOOKS_DIR` explicitly. Dedupe: the same file (sha256) or the same book saved again returns the book on file —
  per-page signatures of the letters only, so a Preview re-save (it re-encodes math fonts: 372 of 404 Axler pages
  changed their extracted text), download stamps and notes still match (SPEC §3.1); scans match only byte for
  byte. An upload restores a missing or damaged PDF. Tests always get a temporary books dir (`config.py` refuses any
  other under pytest); any other process with its own `UA_DATA_DIR` and no `UA_BOOKS_DIR` keeps books in
  `<data>/books` and only reads the project's (a sandbox DB shares book ids: its delete must not remove the real PDF).
  The Axler PDF was copied from `<data>/books/611eab851f87/` by `scripts/move_books.py` (its stored path still
  pointed into the old project `data/` folder; the app had found it by id).

## 9. Open items

- Real course: lessons generated before 2026-10-06 predate §3–§4 — 12 of them still serve free-response questions
  (S2a and the proof skills S4, S10, S15–S18, S20, S22, S23, S25, S28) and only S3a/S3b have a capstone. Regenerating
  a lesson (`scripts/regenerate.py`, keeps progress) applies the current rules. Three S27 multiple-choice questions
  with several correct options are withheld and need rewriting.
- Generation-time check for "more than one correct option" (§3).
- Questions in the real course whose prompt prints the answer (bypass audit 2026-10-08): ~~S8b k3q3~~ (rewritten the
  same day), S1 k1q4, S11 k2q1, S24 k1q1 (v1 lessons — fixed when re-planned);
  inline checks S3b block 8 and ~~S8b block 9~~ ask for a value their block prints (S8b's fixed by hand on
  2026-10-10, before the learner started it: the block no longer computes the candidate).
- The Library had no way to delete a course or a book (course deletion sat only at the very bottom of the course
  page; books had none). Added 2026-10-07: a delete button on every course card and book row, each with a
  confirmation; a book can be deleted once no course is built from it.
- S2c ("verify a supplied cube root") predates the outcome rule. The learner has attempted it, so `replan.py` can't
  split or remove it; it is re-planned as a kept skill whose objective is the computation (a "show that" about one
  given number is a computation, not a proof). Folding it into S2b/S2d would need a "merge" operation (not built).
- Roll v2 out beyond S2/S3 skill by skill if the learner approves (§1).
- The proof track's S4a (sample 3 of three live samples, judged blind "much better" than the old lesson) is in the real
  course since 2026-10-08; the learner studied it and marked it passed on 2026-10-09 (`mark_passed.py`). It predates
  scored steps and keeps the old flow.
- S4b, the first guided proof lesson (scored steps + variant), is in the real course since 2026-10-09: Opus, $6.53 to
  generate plus $0.55 to rewrite the variant. Four judges: every key recomputed correct; blind "much better" than the
  best old-format S4b (own work 8 vs 4, repetition 8 vs 4). Their two blocking findings — the variant's last step
  could be read off the explanation just shown, and the additive inverse (half the objective) was proved nowhere
  served — were fixed by making the additive inverse the variant (`regenerate --only capstone --note`); five minor
  wording fixes were made by hand in the sandbox before import. Still open there: the learner never does a check
  themselves, and the numbers case's slot row is shown filled. Patched by hand on 2026-10-10 for rule 17 (§2), before
  the learner started it: the reflect block went (8 blocks), the scope claim "every nonzero complex number has exactly
  one inverse — not yet" became "the real equation ac − d = 1 alone determines c and d" (false), and knowledge point 2
  is "What the proof rests on"; its five scored steps re-verified (all agree) and imported.
- Rule 17 in stored lessons (2026-10-10): S4a k2q1 (a numeric check and a fixed input offered as proofs) rewritten in
  place ($0.61; claims about the moves a proof may make). S4a's and S13's reflects stay — both lessons are learned and
  the teaching is not replayed. S10 (2026-10-10, the learner's choice): its failed attempt (74 %) counts as passed —
  the variant renamed x_k as c (the learner typed (ak+bk)x) and step 3 had two ambiguous statements, so it was flagged
  on their behalf (`flag_question.py`), leaving the scored steps at 90 % (Good). For the reviews the variant was
  rewritten without renaming ($0.88, its last step by hand so that no claim can be read off an earlier explanation),
  k2q1/k2q2 rewritten, knowledge point 2 renamed "What the proof rests on"; its teaching keeps the reflect (learned,
  and its scored-step records are indexed by block). The grader now splits 'ax_k' like 'ax_1' — '(ax_k, bx_k)' was
  graded as unknown names.
- Rolling regeneration ahead of the learner (the learner's choice on 2026-10-10: about three lessons ahead, so later
  lessons get the rules feedback adds): S9 ($2.09) and S11 ($2.05) re-planned as kept v2 skills in sandboxes and
  imported ("Compute with sequences in F^∞", "Locate arrows for vector operations in the plane"); S12 split into S12a
  "Decide whether given operations make a vector space" and S12b, a guided proof "Prove the complexification of R^n is
  a complex vector space" (applied on the live course from the sandbox's saved plan, $6.60; the sandbox run stopped
  after $0.66 — a split cannot be imported). Final reviews: S9, S11 and S12a each had their blocking findings in the
  capstone, rewritten and verified; S12b 0 blocking (one wording slip fixed by hand). S14–S28 followed (below).
- **S14–S18 made by Claude Code agents** (2026-10-10, the agent route of §1, no API cost), each re-planned as a kept
  skill in a sandbox and imported (none had attempts): S14 "Compute with the scalar 0 and the zero vector"
  (computation; 4 questions flagged by verification, every knowledge point still at target), S15 "Prove pointwise
  constructions are vector spaces", and the first lesson of three proof patterns — S16 "Prove uniqueness and cancel
  vector addition" (suppose_two), S17 "Prove the zero and negative-scalar identities" (law_chain), S18 "Prove the
  three-condition subspace test" (two_halves). Final reviews: 0 blocking except S18's k1q1 and k2q1, rewritten in their
  slots and verified. The spot check of S16–S18 (a solver who recomputed every key and ran the grader on several
  typed forms, a pedagogy judge, a skeptic per finding) found no wrong key and no correct answer graded wrong. It did
  confirm one pattern in all three: scored steps answerable from text already on screen — the setup's "We may not
  use" list stating the very moves a "Which moves may this proof make?" step asks about, an explanation or the notation
  panel naming the next step's key, a variant whose prompt prints its own chain, a prompt that states the equation
  forcing its key — and true/false hints whose last hint lists every verdict (½ point for copying; a course-wide fix
  is a separate task). The final reviews had flagged several of these as minor, and the revision left them. All
  fixed by hand before the learner reached them, in three rounds — each round's edits were re-checked by an
  adversarial reviewer with skeptics, and the first two rounds' edits opened new leaks of the same kind (a key
  copyable from the line printed just above it, a recall in the setup that prints the key, an explanation listing
  the laws the next statement asks about, a stale hint): claims that apply the rules instead of repeating them, keys
  moved out of the notation, the setup and earlier explanations, a line for the learner to produce in S17's finish
  and in S16's and S17's variants, S16's guided case rewritten so that both routes use an outside inverse $y$ like
  the numbers and the learner builds route 1's first line by translating the numbers' (the book's one chain went to
  "Go deeper"; letting $x$ play $y$'s part was the symbol swap of complaint 16), S18's first scored step now asks
  which condition puts $(-2,2)$ into $U$ (the inherited inverse is an option), hints without verdicts, S17 k2q3 and
  S18 k2q3 out of serving. After each round the lessons passed the mechanical checks and the pipeline's verification
  again (agents); then they were re-imported. Left open (minor): S17's variant has no typed step (its lines are
  abstract vectors the grader cannot check, so its scored lines are choices); S18's faded step $(-b,-c)$ is still
  light; the stored `plan` of S16–S18 still describes the generated version, so a later `regenerate --only
  capstone/questions` reads it.
  The S17 judge's proposed claim "the chain could start from $(-1)v$ alone and reach $-v$ one law per line (F)" was
  wrong — $(-1)v=(-1)v+(v+(-v))=\dots=0+(-v)=-v$ needs no uniqueness — a reminder that review findings' fixes are
  checked before they are applied.
- S12a block 1 rewritten by hand (2026-10-10, the learner: "I don't understand this paragraph nor why ab is only
  applied to x1, and the second coordinate becomes zero"): the invented multiplication $\lambda(x_1,x_2)=(\lambda x_1,0)$
  was defined only in block 0 and used two screens later without a word; the block now restates it and says it throws
  the second coordinate away, separates the addition laws, the 1.19 check and the four scalar laws, and spells out
  $a(bv)$ as the rule applied twice. Open for the generator: a block that computes with an invented operation should
  restate it where it is used.
- **S19–S28 made by Claude Code agents** (2026-10-10, the learner asked to use the remaining Claude Code credits for
  them): ten re-plans in sandboxes, run from a checkout pinned at the pushed commit while the books-folder change was
  made in the main tree. Four skills split — S20 → S20a/b/c (intersections; unions of two; unions of three), S21 →
  S21a/b, S25 → S25a/b, S26 → S26a/b — applied on the live course with `replan.py --plan <sandbox plan> --apply-only`
  and the sandbox lessons brought in with `import_lesson.py` (nothing generated twice). Every final review's remaining
  blocking issue was handled by the pipeline (capstone or served question rewritten, spare left out); the rule-17 scan
  was clean; verification agreed on every key but one question (left out). One spot-check round (solver + judge +
  skeptic, no second round for lack of time): no wrong key; the confirmed blocking findings — scored steps or capstone
  steps answerable from the screen, served questions copying the worked example shown above them, a capstone step
  that does not need the steps before it, true/false hints giving every verdict — were fixed in the sandboxes by
  agents (math checked with SymPy), re-verified by the pipeline and re-imported. The 43 confirmed major findings are
  still open, listed per lesson in [spot-check-S19-S28.md](spot-check-S19-S28.md): fix them before the learner reaches
  each lesson. S20a–c and S25a–b were checked for blocking problems only (the weekly agent limit was reached at 19:00):
  give them a full check before the learner reaches them. The whole chapter now has v2 lessons except S1, S5 and S6 (learned).
- S10 (two_routes) is in the real course since 2026-10-09, generated with the faster default (21.5 minutes, $3.94)
  plus a variant rewrite ($0.76) after the spot check: the variant's step 2 explanation named the law of F that step 3
  asked about — now a hard check. Spot-check minor findings left open: Block 4 and Block 10's scored steps are close to
  trivial (a sum of given pairs; the guided cell renamed), Block 9's statements paraphrase text on screen, and the
  objective's other laws (commutativity, associativity, identities, inverses) appear only in practice and reviews.
  Next: one proof skill per remaining pattern (S16 suppose_two, S17 law_chain, S18 two_halves, S20
  conditions_or_counterexample), generated together in parallel; the first of each pattern spot-checked.
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
