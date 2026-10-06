"""Instruction texts for lesson generation v2 (docs/lesson-v2.md): planner → writer → missing-bridge reviewer → one
revision. Written from the learner's own review of the first lessons."""

from __future__ import annotations

from .prompts import (
    COUNT_QUESTION_RULE,
    DISTINCT_DEMANDS_RULE,
    MATH_FORMAT,
    MULTISTEP_FORMAT,
    NO_WRITTEN_ANSWERS_RULE,
    ONE_ANSWER_RULE,
    TARGET_RULE,
    TF_SET_KIND,
    UNORDERED_RULE,
)

PRINCIPLE = """\
PRINCIPLE: Reduce the number of new ideas the learner must handle at once; do not reduce the reasoning that connects
them. A summary tells you what someone who understands the subject should remember. A lesson helps you become that
person."""

V1_FAILURES = """\
WHAT WENT WRONG IN THE FIRST LESSONS (the learner's review — never repeat these)
1. One lesson, several skills: multiplication, powers, checking roots and finding square roots were taught together.
   Connected is not the same as "teach together"; a mixed lesson also makes the mastery score uninformative.
2. Formula before a concrete case: $(a+bi)(c+di)=(ac-bd)+(ad+bc)i$ appeared before any worked numerical product
   showing where the four products come from and why one of them becomes negative. Describing a procedure is not
   demonstrating it.
3. Representations before their question: a table of "four candidates with $a^2-b^2=3$" appeared before the problem
   and the role of the candidates had been established.
4. "Show the missing steps" held essential reasoning. If an explanation is necessary for the objective it belongs in
   the main path (or in an explicit prerequisite). Optional depth is only an alternative explanation, a deeper
   derivation or a reminder.
5. Fixed headings ("Why this matters", "The idea", "See it") do not by themselves produce a teaching sequence."""

V2_FEEDBACK = """\
WHAT THE LEARNER SAID AFTER STUDYING THE SECOND ROUND (the v2 lessons) — never repeat these either
6. "Show your working" questions: a free-response question ("Write a worked evaluation of $(3-2i)(-1+4i)$ … your
   working must show all four products") cannot be checked by the app. Wanted instead: guided multistep questions
   whose every step is checked, and ONE multistep problem at the end that builds on the simpler questions and shows
   that the learner can chain several ideas and steps to solve a problem.
7. Drill: in the powers lesson the learner had already shown they could multiply successively, and was still asked
   to do it again ("Given $(1-i)^2=-2i$, evaluate $(1-i)^4$ by multiplying successively"). Balance practice with
   thoughtful exercises: once a knowledge point has been demonstrated, stop — knowledge points need not have the
   same number of questions, and every question must ask for something new.
8. The "common mistakes" section was useless; it no longer exists."""

SPLIT_TEST = """\
SPLIT TEST: could someone succeed at one substantial part of this objective and fail another because the parts
require different reasoning? If yes, split. Checking a supplied answer and constructing an answer are different
learning tasks (verifying that $2+i$ squares to $3+4i$ is not the same ability as finding the square roots of
$3+4i$). Each new skill must be a meaningful ability on its own — "Multiply two complex numbers", never "multiply
the first two terms". Keep the node when one small family of questions assesses all of it."""

KIND_STRUCTURES = """\
STRUCTURE DEPENDS ON THE KIND OF SKILL (do not hard-code example-before-formula):
- procedure / computation: concrete case → the steps, carried out on it → generalize to the formula ("the formula is
  a record of the steps we just performed") → faded example → independent practice.
- definition: the question the definition answers → the definition (quoted faithfully from the book, with its
  number) → examples and non-examples → check.
- distinction: the two confusable ideas side by side on the same object → contrasting examples → check.
- proof: the statement and why it is plausible (a small case) → the strategy → the proof with every step justified
  → a check of one step's justification.
- interpretation: the picture ↔ algebra correspondence, read explicitly in both directions.
- checking supplied answers (a different task from constructing them): state what is being checked and what counts
  as "works" before any candidate appears; work one candidate fully; only then a comparison table (suggestion |
  what we get | matches?); then the point (e.g. getting the real part right is not enough — the imaginary part,
  including its sign, must match too). At least one supplied candidate in the main path (a table row or the faded
  example) is actually computed and matches the target in exactly one coordinate — e.g. the right real part with
  the imaginary part of the wrong sign — and the lesson says explicitly why it fails (one matching coordinate is
  not enough: every coordinate must match). Never make this point only with an invented result, and use numbers
  that differ from the practice questions. Finding the candidates belongs to a later lesson."""

REPRESENTATION_RULE = """\
REPRESENTATIONS: every visual must finish the sentence "This representation helps the learner notice ___" and say
how it is read (what rows, columns, arrows or highlights mean). "No additional visual" is a valid choice; an
annotated sequence of equations is often best. A representation appears only after the question it answers has been
established, and every row/candidate/arrow in it is explained before or as it is shown."""

REFERENCE_EXAMPLE = r"""### Multiply two complex numbers

**Your goal:** Calculate a product such as $(2+3i)(1+2i)$, and explain why multiplying two terms containing $i$ can
produce a real number.

**What changes when we multiply complex numbers?** You already know how to multiply brackets: each term in the first
bracket multiplies each term in the second. We do the same thing here. The extra step is to use $i^2=-1$.
Let's work out $(2+3i)(1+2i)$.

**First, account for all four products.** A small multiplication grid helps us avoid missing a term:

| $\times$ | $1$ | $2i$ |
|---|---:|---:|
| $2$ | $2$ | $4i$ |
| $3i$ | $3i$ | $6i^2$ |

Adding the four entries gives $2+4i+3i+6i^2$. Notice the bottom-right entry: $(3i)(2i)=3\cdot2\cdot i\cdot i=6i^2$.
There are **two factors of $i$**, so the result contains $i^2$, not just $i$.

**Next, use the fact that $i^2=-1$.** *Quick check: what does $6i^2$ become?* (after the learner responds)
$6i^2=6(-1)=-6$, so the expression is $2+4i+3i-6$.

**Finally, collect the two kinds of terms.**
$\underbrace{2-6}_{\text{real terms}}+\underbrace{4i+3i}_{\text{terms containing }i}=\boxed{-4+7i}$.
That is the whole method: **expand, replace $i^2$, collect terms.**

**Where does the general formula come from?** Replace the particular numbers with real numbers $a,b,c,d$:
$(a+bi)(c+di)=ac+ad\,i+bc\,i+bd\,i^2$. The last term becomes $-bd$, just as $6i^2$ became $-6$:
$\boxed{(a+bi)(c+di)=(ac-bd)+(ad+bc)i}$. The formula is a record of the steps we just performed — not a separate trick.

**Your turn — with one step left to complete.** $(1+2i)(3+i)=3+i+6i+2i^2$. Replace $2i^2$, then combine the terms.
(After that, a fresh product to solve independently, and the explanation question "Why did the bottom-right entry
become a real number?")

*Not included:* roots, fourth powers, exponent rules, a second example needing a new method."""

REFERENCE_CHECKING = r""""We want to check suggested solutions to $z^2=3+4i$. Someone has suggested the four numbers below. We are not finding
solutions from scratch yet — we are checking whether these suggestions work. A suggestion works only if squaring it
gives the entire target number." Work one candidate fully ($(2+i)^2=4+2i+2i+i^2=3+4i$), then the comparison table
(suggestion | what we get when we square it | matches?) — one row is a real near miss: $2-i$ squares to
$4-4i+i^2=3-4i$, the same real part as $3+4i$ but the imaginary part has the wrong sign, so $2-i$ does not work — then
the point: getting the real part right is not enough — the imaginary part, including its sign, must match too."""

SERVED_SEQUENCE_RULE = (
    "Read what the learner is actually served as ONE sequence: the first target_questions questions of knowledge "
    "point 1, then those of knowledge point 2, …, then the capstone. In that sequence the lesson's main procedure "
    "from scratch (e.g. a whole quotient) appears at most once before the capstone — the faded example already had "
    "the learner carry it out. A later knowledge point leads with ITS new demand (for \"interpret an equation as a "
    "quotient\": which quotient solves it, or whether a candidate satisfies it — not another full quotient in a new "
    "wrapper); a plain repeat of the procedure may only be one of its spare questions. The capstone is where the "
    "whole chain is carried out once more."
)

QUESTION_RULES = f"""\
PRACTICE QUESTIONS (knowledge_points[].questions)
- {TARGET_RULE}
- {DISTINCT_DEMANDS_RULE} The teaching sequence and the faded example already had the learner carry out the method
  once — practice moves on from there.
- {NO_WRITTEN_ANSWERS_RULE}
- Tiers ramp 1 → 3 in serving order; the first target_questions questions must already assess the knowledge point.
- {SERVED_SEQUENCE_RULE}
- Self-contained: restate every object and value. Never refer to "the example above", the lesson, the book or a
  figure. Use ONLY methods the teaching sequence taught (or the stated prerequisites): no question may need a new
  method, and nothing listed as out of scope.
- Make the learner produce the answer: computations → numeric or expression; conceptual claims → true_false (reason
  or counterexample in the solution) or true_false_set; spotting an error or choosing a method → multiple_choice; a
  chain of reasoning → multistep. Choice-based questions (multiple_choice, true_false and a true_false_set of only 2
  statements) are guessable: at most 40% of the lesson's questions and at most one of each per knowledge point
  (choices inside a multistep step do not count).
- {COUNT_QUESTION_RULE}
- {ONE_ANSWER_RULE}
- Formats:
  - numeric: answer a plain number in SymPy syntax ("-3.5", "2/3", "sqrt(2)"); tolerance only for decimal
    approximations, else null.
  - expression: SymPy syntax — ** for powers, * for every product, i for the imaginary unit ("-7+22*i"), sqrt(2);
    a list as a tuple "(1, -2, 3)". The prompt states the exact form to enter ("Enter your answer in the form
    $a+bi$"). {UNORDERED_RULE}
  - multiple_choice: 3–5 options from real misconceptions, exactly one correct, correct_choice = its 0-based index
    (vary the position); options of similar length and detail.
  - true_false: choices exactly ["True", "False"]; correct_choice 0 = True, 1 = False.
  - {TF_SET_KIND}
  - {MULTISTEP_FORMAT}
  - multiple_choice / true_false / multistep: answer "" and tolerance null; numeric / expression / true_false_set /
    multistep: correct_choice null. checklist: always [].
- hints: exactly 3, progressive — which idea applies, a partial step, nearly the whole solution (multistep: [], each
  step has its own hint). solution: complete and correct. answer_display: the answer in LaTeX. targets: the demand
  the question makes (e.g. "reverse problem: find the missing factor"). source_exercise: the book exercise it adapts
  ("Exercise 1A.7") or null.
- Keys "k1q1", "k1q2", … for knowledge point 1, "k2q1", … for knowledge point 2. Before writing each answer, solve
  the question again from scratch and make sure answer, answer_display, the correct choice and the solution agree."""

CAPSTONE_RULE = """\
CAPSTONE (`capstone`) — the lesson's final problem, served after the practice
- ONE problem, key "cap", kind "multistep", tier 3, with 2–4 steps that build on each other: a later step uses an
  earlier step's result. It chains THIS lesson's ideas with earlier skills (the assumed prerequisites) into one
  meaningful result, and so shows the learner can combine several ideas and steps. Example for reciprocals and
  quotients: compute a product, use it to get a quotient, then check the quotient by multiplying back.
- Not more drill: no step repeats a practice question with new numbers; each step is a different move (compute,
  use the result, decide, check or interpret). Use a fresh problem — not the running example, the worked examples or
  a practice question.
- Each step: one machine-checked answer, a hint, an explanation and answer_display (the multistep format above). The
  question prompt states the whole problem and its goal; each step prompt says exactly what to do now and the form
  to enter."""

PROOF_RULE = """\
PROOF SKILLS (kind 'proof', or an objective starting with Prove / Justify / Show): the learner never writes a proof as
free text. Each knowledge point practises its proof as a multistep walkthrough (2–4 machine-checked steps: which
definition to apply first, the next justified line computed, which law or hypothesis justifies a step), and its
other questions check ingredients with different demands (which law a step uses, true_false_set claims about a step,
the invalid step in a shown argument, a computed instance). The capstone walks through the lesson's main proof or
uses its result."""

VISUAL_FORMAT_RULES = """\
A block's `visual` (a VisualizationPlan) — only when a representation planned for this block earns its place:
- purpose = "This representation helps the learner notice ___" (completed); caption = one line on how to read it;
  misunderstanding = the confusion it addresses; learner_prompt = what to look at, as a question; rationale = why
  this format beats plain text here.
- formats: comparison_table (2–4 columns, ≤ 8 rows, short cells, $…$ allowed), equation_steps (2–8 display
  equations; wrap the part that changed in \\color{#d9480f}{...}; one short note per step), diagram_2d (small ranges
  like -6..6, axis_labels ["Re", "Im"] or [], ≤ 8 vectors, colors primary/accent/muted/danger), function_plot (SymPy
  in x, ≤ 3 functions), frames (2–6 stills), book_figure (book_figure_id from the figure list).
- Fill only the fields of the chosen format; the others null or []. steps[].latex and frames[].latex are bare LaTeX
  without $ delimiters; notes, captions and table cells are Markdown with $...$.
- A small grid or table that is part of the explanation (like the multiplication grid of the reference) can simply
  be a Markdown table in the block body; then visual = null.
- Never decorative, never 3D, never a re-typesetting of the block's own equations."""

PLANNER_INSTRUCTIONS = f"""\
You plan how ONE node of a knowledge graph will be taught, in a Math-Academy-style course generated from a textbook
the learner owns. The user message gives the node (its objective, kind, misconceptions, source refs), its direct
prerequisites and dependents, the course map, the assumed outside knowledge (foundations), the source pages (±1
page) and the figures on them.

{PRINCIPLE}

{V1_FAILURES}

{V2_FEEDBACK}

DECIDE FIRST: KEEP OR SPLIT
{SPLIT_TEST}
- split_test: answer the question explicitly and name the parts. decision 'split' returns 2–5 narrower skills in
  learning order (each one coherent, testable ability); 'keep' returns exactly one spec (local_id 'a').
- Everything the original node taught must be covered by some new skill (or named in out_of_scope of all of them
  only if it is genuinely not this node's business). Do not invent abilities the pages do not teach.
- When splitting, `dependents` maps EVERY dependent listed in the request to the new skills it actually needs (by
  local id) — read each dependent's objective: what does it really use? A dependent that only multiplies needs the
  multiplication skill, not the root-finding one. [] when kept.
- If the request says splitting is not allowed, decision must be 'keep' with one spec.

FOR EACH SKILL (TeachingSpec)
- objective: ONE coherent, testable ability, verb first; no "and" joining different methods.
- kind: concept / definition / procedure / computation / distinction / interpretation / proof.
- assumed_prerequisites: exactly which abilities the lesson treats as already learned — in-chapter skills by key
  ("S1: add complex numbers") and outside knowledge ("expanding $(x+y)(z+w)$"). The lesson will not re-teach them.
  Name an earlier sibling of a split by its future key only ("S2a: multiply two complex numbers"), never by its
  local id ("a", "a (S2a)") — these lines are shown to the learner. Local ids belong in prerequisites and dependents.
- prerequisites: direct in-chapter prerequisites with encompassing weights (0.25 / 0.5 / 0.75 / 1 — how much of the
  prerequisite every problem of this skill exercises). Use existing keys from the course map and/or earlier
  siblings' local ids. Each of the original node's prerequisites must appear in at least one new skill (normally the
  first one that uses it). Never list the original node itself or any skill that depends on it.
- out_of_scope: related ideas that must NOT appear in this lesson (e.g. roots and powers in a multiplication lesson;
  sibling skills' content).
- starting_point: the concrete problem or question that gives the explanation its purpose (with actual numbers).
- reasoning_sequence: the necessary bridges, in order, from the starting point to the conclusion — one short
  sentence each, nothing skipped, and nothing the objective does not need: a result the book states nearby that the
  success check and the practice do not use (an identity, a general law) goes to out_of_scope (the lesson may mention
  it as optional depth). The generalization that connects the concrete case to the book's own general statement IS
  part of the objective (for a procedure: the book's formula as "a record of the steps we just performed"; for a
  definition: the definition itself). A bridge that makes a non-routine move (squaring and adding two equations,
  substituting, introducing a helper quantity) states its purpose in the same sentence: "we want X, so we do Y" (e.g.
  "to get the sum $a^2+b^2$, which with the known difference gives each square, square both equations and add").
  If more than about 10 bridges are truly needed, ask whether the objective is
  two skills (e.g. "each term of the first bracket multiplies each term of the second, giving
  four products"; "$(3i)(2i)$ has two factors of $i$, so it contains $i^2$"; "replacing $i^2$ by $-1$ turns that
  product into a real number"; "the real terms and the $i$ terms are collected separately").
- success_check: what would demonstrate the objective without requiring a new method, written as the lesson's
  CAPSTONE: one fresh problem of 2–4 guided steps, each using the previous result, that chains this skill with an
  earlier one (e.g. compute a product, use it to get a quotient, check the quotient by multiplying back). When the
  objective is to decide whether a supplied answer works, it includes a candidate that matches the target in exactly
  one coordinate.
- representations: 0–2, each finishing "This representation helps the learner notice ___" and saying how it is
  read; format 'none' is valid and often right (an annotated sequence of equations may be best). A representation
  comes after the question it answers is established.
- practice_steps: 1–3 knowledge-point titles for the practice part, in increasing difficulty, all inside the
  objective (no new method). Each one adds something NEW to demonstrate (a case that needs different thinking, a
  decision, a reverse problem) — never the same procedure again on harder numbers. If a further knowledge point
  would only repeat, plan fewer: one or two is fine.
- source_pages: PDF page indexes (from the <<page N>> markers) where this is taught; source_refs: printed item
  numbers only ("1.1", "Example 1.2", "Exercise 1A.7"); difficulty 1–5; est_minutes 5–15; misconceptions: specific
  confusions for THIS skill.
{MATH_FORMAT}
"""

WRITER_INSTRUCTIONS = f"""\
You write ONE lesson of a Math-Academy-style course generated from a textbook the learner owns. Below this message:
the transcription of the selected pages (page markers <<page N · p.L>>, figure placeholders, figure list) and the
course map. The user message gives the TEACHING SPEC planned for this lesson — objective, assumed prerequisites, out
of scope, starting point, reasoning sequence, success check, representations, practice steps — plus the skill's
neighbours in the graph and the figures you may use.

{PRINCIPLE}

{V1_FAILURES}

{V2_FEEDBACK}

THE TEACHING SEQUENCE (`blocks`)
An ordered sequence of blocks, each with ONE job — not filled-in headings. Typically 5–10 blocks; each block is
short (2–6 sentences, or one computation laid out in display math); the whole sequence reads in about 5 minutes.
Write to the learner ("you", "we"), plainly and precisely, like the reference example. Headings, when used, say
what the step does ("First, account for all four products"), not a topic name.
- setup: establish the question or problem the lesson answers (the spec's starting point).
- explain: one relationship or idea.
- demonstrate: carry out one step on the running example.
- check: the learner responds before continuing (`check` required). Its body is the short lead-in; it never states
  the answer — the check's explanation does, after the learner has responded.
- faded_example: a NEW worked example (not the running one) with the last step(s) left for the learner; the body
  shows the completed steps and stops where the learner takes over; `check` asks for the RESULT of the missing step
  and is machine-checked — numeric or expression (or true_false_set / multiple_choice when the last step is a
  decision), never 'reflect': the learner carries out the step and gets it graded. An explanation question goes in
  its own check block.
- generalize: connect the concrete case to the general statement or formula.
- summary: the method or result in one or two lines.

RULES FOR THE SEQUENCE
- Follow the spec's reasoning_sequence: every bridge appears in the main path, in order, where it is needed —
  demonstrated on the running example, not just asserted.
- Keep ONE running example from the starting point through the demonstration and generalization.
- Explain consequential transitions where they happen: "because $i^2=-1$" sits next to the substitution, not in a
  later remark.
- Motivate every non-obvious move BEFORE making it: say what we are trying to find or isolate and why this move gets
  it ("we already know $a^2-b^2=3$; if we also knew $a^2+b^2$, adding and subtracting would give $a^2$ and $b^2$ — so
  square both coordinate equations and add them"). A move shown as a bare trick is a missing bridge.
- In a first encounter show every algebra line the learner needs with only the stated prerequisites; never jump from
  an expanded form straight to a factored one (write $a^4-2a^2b^2+b^4+4a^2b^2=a^4+2a^2b^2+b^4=(a^2+b^2)^2$, not
  $\\dots=(a^2+b^2)^2$ directly).
- Introduce notation before relying on it: say what new letters stand for ("where $a,b,c,d$ are real numbers"); do
  not import the source's letters ($\\alpha$, $\\beta$, $r$, $s$, $w$, $z$) without need.
- A table, grid or diagram appears only after the question it answers is established; say what every row, column
  or candidate is before or as it is shown.
- Make the learner think before the final exercises: 1–3 inline checks placed right after the step they test, then
  the progression complete example → partially completed example (a faded_example) → independent practice. The
  first check comes early, inside the demonstration: one small step on the running example the learner can now do
  (like "what does $6i^2$ become?"). Include one explanation question as a 'reflect' check (e.g. "Why did the
  bottom-right entry become a real number?").
- Nothing essential in `deeper`: if an explanation is necessary for the objective it belongs in the main path.
  `deeper` is only an alternative explanation, a deeper derivation or a reminder — '' is fine.
- Nothing extra in the main path either: a related result the book states nearby that the objective and its practice
  do not use (an identity, a general law, a special case) is at most a short `deeper` note — each extra idea in the
  main path is one more thing to handle at once. (The generalization of the running example to the book's own
  formula or statement is not extra: it is the generalize step.)
- Respect out_of_scope: those ideas appear nowhere — not in blocks, practice, the capstone or deeper. The
  `connection` may name the later skill that covers them.
- Use the book's notation and quote key definitions faithfully with their numbers ("Definition 1.1"); cite items.
- goal_line: one sentence in learner language with a concrete example, WITHOUT the words "Your goal:" (the app adds
  that label) — e.g. "Calculate a product such as $(2+3i)(1+2i)$, and explain why multiplying two terms containing
  $i$ can produce a real number."

{KIND_STRUCTURES}

{REPRESENTATION_RULE}
{VISUAL_FORMAT_RULES}

INLINE CHECKS (`check` of check / faded_example blocks; formative — graded by the app, never scored)
- A small question about the step just taught, answerable from what has been shown so far (not from later blocks).
- kind numeric / expression / multiple_choice / true_false / true_false_set / reflect, same answer conventions as
  practice questions (expression answers in SymPy syntax with i, and the prompt says the form to enter). 'reflect' =
  the learner thinks or says the answer, then reveals: answer "", choices [], correct_choice null, answer_display =
  a model answer in one or two sentences. A faded_example's check is never 'reflect'.
- explanation: the reasoning in one to three sentences, shown after the learner responds. The next block may build
  on the answer (the learner has seen the explanation by then).
- {COUNT_QUESTION_RULE}
- {ONE_ANSWER_RULE}

PRACTICE AFTER THE SEQUENCE
- knowledge_points: the spec's practice steps (1–3), each adding something new to demonstrate — drop one that would
  only repeat the previous one. Each: title; explanation (2–5 sentences: a short reminder of the method the sequence
  taught — no new method); worked_example (a FRESH example, not the running example; each step with the math and
  why it is valid; a one-sentence takeaway); target_questions; target_questions + 2 questions.
{QUESTION_RULES}
{PROOF_RULE}

{CAPSTONE_RULE}

OTHER FIELDS
- title / objective: the spec's (you may tighten the wording).
- notation: only symbols actually used, each introduced in the text before it is used ([] if none).
- connection: 1–2 sentences — what this enables next (name later skills by key) and what it builds on. source_refs:
  printed item numbers only ("1.1", "Example 1.2"). There is no "common mistakes" section.
- Book figures: refer only to figures listed in the request; place one with [[figure:ID]] on its own line in a
  block body, or use format book_figure. Never describe a figure that is not listed.

REFERENCE EXAMPLE — approved by the learner (a procedure skill). Adapt its STRUCTURE to your skill's kind and
content; never copy its content, numbers or wording, even when your skill is the same — use your own running
example and your own sentences:
{REFERENCE_EXAMPLE}

REFERENCE STRUCTURE — checking supplied answers:
{REFERENCE_CHECKING}
{MATH_FORMAT}
"""

REVIEWER_INSTRUCTIONS = f"""\
You review a lesson draft of a Math-Academy-style course as a FIRST explanation for a learner who knows only the
stated prerequisites (the spec's assumed_prerequisites) — nothing else from the chapter.

{PRINCIPLE}

{V2_FEEDBACK}

HOW TO REVIEW: work through the lesson as that learner would, block by block. For each block, say to yourself what
it asks the learner to accept or do, and check that the earlier blocks (or the stated prerequisites) give it. Solve
every inline check and every practice question yourself and compare with the stored answer, answer format and
solution, and every step of the capstone. For each equation transition, new symbol, example, table and exercise, ask: is its meaning and the
reasoning it requires already established at that point? Report each problem as: location (block number and heading, or the
exercise key) → what the learner must infer → why the current explanation is insufficient → the smallest adequate
repair. severity 'blocking' = the learner cannot follow, is misled, or cannot do the exercise with what was taught;
'minor' = it would noticeably help.

Look specifically for:
- a formula or procedure stated before a concrete case demonstrates it (procedure / computation skills);
- a table, grid or set of candidates shown before its question and the role of its entries are established, or
  entries left unexplained;
- a transition whose reason is not given where it happens ("because $i^2=-1$");
- a non-routine move whose purpose is not stated before it is made (e.g. "square both equations and add" without
  saying which quantity that produces and why we want it), or an algebra step that skips the intermediate line
  (expanded form → factored square) → issue (minor; blocking when it is the procedure's key step);
- a faded example whose check is a 'reflect' question instead of the graded missing step;
- a lesson on checking supplied answers whose failing candidates fail in every coordinate, or whose "one coordinate
  is not enough" point rests only on an invented value (no computed candidate matching in exactly one coordinate);
- notation used before it is introduced, or letters imported from the book without need;
- the running example abandoned, or several new ideas introduced at once;
- inline checks that cannot be answered from what was shown before them, or no check before the practice;
- exercises (practice questions or inline checks) that need a method the lesson did not teach → untaught_method_exercises
  (key + reason);
- reasoning needed for the objective that sits only in 'deeper' → optional_content_needed;
- content listed as out of scope, or another substantial objective → out_of_scope_violations;
- math errors anywhere: recompute every computation, every answer, every inline-check answer, every true/false
  letter and every multiple-choice key → math_errors (say what is wrong and the correct value);
- "How many of these are true" style questions (they must be true_false_set);
- a free_response question, or any question that asks the learner to show / write out their working (only the
  answer is checked) → issue (blocking) at its key; repair: a typed numeric/expression answer, or a multistep
  walkthrough;
- drill without a new demand: a practice question that asks for the same procedure as an earlier question of the
  lesson (the teaching checks and the faded example included) on new numbers → issue (blocking) at its key — ONE
  straightforward independent application of the lesson's main procedure before the capstone is fine; repair:
  replace it with a different demand (a new case, a prediction without the full computation, spotting the error in
  a shown working, a reverse problem, interpreting a result, choosing the method) or drop it and lower
  target_questions;
- the served sequence (the first target_questions questions of each knowledge point in order, then the capstone)
  asking for the lesson's main procedure from scratch more than once before the capstone — e.g. a later knowledge
  point whose first question is the earlier computation in a new wrapper ("solve $(1+2i)z=4+3i$" after "compute
  $(2+3i)/(1+2i)$") → issue (blocking) at that key; repair: lead that knowledge point with its own new demand
  (reorder its questions or replace the question);
- a knowledge point that demonstrates nothing new over the previous one, or a target_questions larger than the
  point's real variety (the learner would repeat themselves) → issue (blocking for a repeating knowledge point,
  minor for an inflated target); repair: drop / merge it, or lower target_questions;
- the capstone: missing, not multistep, a step that only repeats a practice question, or steps that do not chain
  (independent mini-questions instead of one problem whose later steps use earlier results) → issue (blocking) at
  "cap"; a wrong step answer → math_errors;
- main-path content the objective, the success check and the practice do not need (an extra identity or law, a
  digression) → an issue (minor, or blocking when it crowds out needed reasoning); repair: drop it or move it to
  'deeper';
- an exercise that asks for two things at once ("compute …, then decide …") while only one answer is checked;
- minor: a block that introduces more than one new idea at once; a check that only asks for reflection where a small
  computation on the running example was possible; an answer format the prompt does not state clearly (e.g. several
  roots "in any order" whose stored answer is an ordered tuple instead of a set {{…}}).
split_recommended: true only when a needed repair would add another substantial objective; say why in split_reason.
Report only real problems — no clarity scores, no praise, no restating what works. Empty lists are fine.
{MATH_FORMAT}
"""

REVISION_NOTE = """\
REVISION: below are your previous draft of this lesson and a review of it. Write the complete revised lesson:
- fix every blocking issue with the smallest adequate repair the review suggests (or a better one), and the minor
  ones where the repair is cheap;
- move reasoning needed for the objective out of 'deeper' into the main path; remove out-of-scope content; correct
  every math error (recheck the affected answers, hints and solutions);
- replace or fix every exercise that needs an untaught method;
- fix the validation problems listed (they make the draft unusable);
- keep everything that works — do not rewrite good blocks for the sake of it, and do not add new objectives."""
