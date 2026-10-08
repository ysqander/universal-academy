"""OpenAI prompt set — the v2 planner, writer and reviewer instructions, repair notes and the proof-track assembly: the instruction texts tuned against GPT (gpt-6.1-sol / gpt-6-luna)."""

from __future__ import annotations

from ..rules import (
    CAPSTONE_RULE,
    COMPLEX_FORM_RULE,
    COUNT_QUESTION_RULE,
    DISTINCT_DEMANDS_RULE,
    KIND_STRUCTURES,
    MATH_FORMAT,
    MULTISTEP_FORMAT,
    NO_WRITTEN_ANSWERS_RULE,
    ONE_ANSWER_RULE,
    OUTCOME_RULE,
    PRINCIPLE,
    REFERENCE_CHECKING,
    REFERENCE_EXAMPLE,
    REPRESENTATION_RULE,
    SERVED_SEQUENCE_RULE,
    SPLIT_TEST,
    TARGET_RULE,
    TF_SET_KIND,
    TYPED_FIRST_RULE,
    UNORDERED_RULE,
    V1_FAILURES,
    V2_FEEDBACK,
    V3_FEEDBACK,
)
from .proof import (
    PROOF_CAPSTONE_REPAIR,
    PROOF_PLANNER_RULES,
    PROOF_QUESTION_REPAIR,
    PROOF_REVIEW_PASS,
    PROOF_TRACK,
)

QUESTION_RULES = f"""\
PRACTICE QUESTIONS (knowledge_points[].questions)
- {TARGET_RULE}
- {DISTINCT_DEMANDS_RULE} The teaching sequence and the faded example had the learner carry out the method with
  guidance; practice asks for it once unguided (see the served sequence below) and then moves on.
- {NO_WRITTEN_ANSWERS_RULE}
- Tiers ramp 1 → 3 in serving order; the first target_questions questions must already assess the knowledge point.
- {SERVED_SEQUENCE_RULE}
- Self-contained: restate every object and value. Never refer to "the example above", the lesson, the book or a
  figure. Use ONLY methods the teaching sequence taught (or the stated prerequisites): no question may need a new
  method, and nothing listed as out of scope.
- Make the learner produce the answer: computations → numeric or expression; a general claim with no value to type →
  true_false (reason or counterexample in the solution) or true_false_set; spotting an error → the corrected value (typed); choosing a
  method or a justification → multiple_choice; a chain of reasoning → multistep.
- {TYPED_FIRST_RULE} Choice-based questions (multiple_choice, true_false and a true_false_set of only 2
  statements) are guessable: at most 40% of the lesson's questions and at most one of each per knowledge point
  (choices inside a multistep step do not count).
- {COUNT_QUESTION_RULE}
- {ONE_ANSWER_RULE}
- Formats:
  - numeric: answer a plain number in SymPy syntax ("-3.5", "2/3", "sqrt(2)"); tolerance only for decimal
    approximations, else null.
  - expression: SymPy syntax — ** for powers, * for every product, i for the imaginary unit ("-7+22*i"), sqrt(2);
    a list as a tuple "(1, -2, 3)". The prompt states the exact form to enter ("Enter your answer in the form
    $a+bi$"). {COMPLEX_FORM_RULE} {UNORDERED_RULE}
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


PROOF_RULE = """\
PROOF SKILLS (kind 'proof', or an objective starting with Prove / Justify): the learner never writes a proof as free
text. The teaching proves 1–2 representative statements in full (for a family of only two statements, the harder
one). Its setup displays every definition the proofs
apply, with its number, and says how it relates to the way an earlier lesson used it (e.g. an earlier lesson found
the product of complex numbers by distributing brackets; here Definition 1.1 IS the product, so distributing complex
brackets would assume what we are proving). Each knowledge point is a group of statements of the family (e.g. the
addition laws); its worked example proves one of them, and its questions walk through others — statements the
teaching did NOT prove (the teaching proves commutativity of multiplication → practice proves
$(\\alpha+\\beta)\\lambda=\\alpha\\lambda+\\beta\\lambda$, or $0\\cdot\\alpha=0$) as multistep walkthroughs (2–4 machine-checked steps:
which definition to apply first, the next justified line computed, which law or hypothesis justifies a step). A
walkthrough never re-asks a proof shown in the blocks or a worked example, and its justification steps use varied
distractors (a wrong law, a wrong line, a hypothesis that is not available), not the same slogans in every step. Other
questions check ingredients with different demands (which law a step uses, true_false_set claims about a step, the
invalid step in a shown argument, a computed instance). When the family has only one or two statements, the
questions walk through variants (another case, the converse direction, the statement for a specific object), never
the proof shown in the blocks or a worked example. The capstone uses a proved statement in a computation whose input
an earlier skill produces, or walks through a variant nothing else proves; its proof step asks for one line the
learner computes (typed, e.g. the imaginary coordinate of $\\alpha(\\beta\\rho)$), optionally followed by one
justification choice — never a choice among complete proofs that differ in one sign."""


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

{V3_FEEDBACK}

{OUTCOME_RULE}

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
- A node that is only a step toward a task (checking, evaluating, identifying) and that you must keep: aim its
  objective at the task the book asks for with it, and teach the step as the way to do that task — unless that task
  is already another skill's objective in the course map (solving $u+ax=v$; showing that $\\mathbf{{F}}^S$ is a vector
  space): then the computation itself is this node's outcome ("Compute $f+g$, $\\lambda f$ and $-f$ …"), with the
  neighbour's task out of scope. A "show that" about ONE given object ("show that $\\frac{{-1+\\sqrt3\\,i}}{{2}}$ is a
  cube root of $1$") is a computation, not a proof: objective with Compute or Find, kind 'computation', and practice
  that asks for values the learner must compute (the square on the way, the cube, which of several numbers it is a
  cube root of) — never for a value printed in the problem. Only a statement about every object of a kind ("for all
  $\\alpha,\\beta\\in\\mathbf{{C}}$") is a proof.
- The request may show the learner's missed or hint-assisted answers on this skill (current and earlier lesson
  versions). Treat them as evidence. For each WRONG answer that shows a misunderstanding, put the misunderstanding
  that produces exactly that answer first in `misconceptions`, starting "Learner's miss: " (e.g. "Learner's miss:
  stops at the square — took $(-1+i)^2=-2i$ for the cube"); make the reasoning sequence teach what was missing (e.g.
  which case of a case split survives, and why), and plan a practice step that re-tests it with NEW numbers that set
  the same trap, in a format at least as demanding as the one it was missed in: a typed miss is re-tested typed; a
  miss on a choice question is re-tested typed when its demand has a value (e.g. the quotient itself), otherwise as a
  choice question on new numbers whose key differs from the miss. The missed problem may be the running example, but
  repeating it with the wrong answer as an option is not a test. An answer that was right with a hint, or given after
  the solution was shown, only shows where the learner was unsure. A slip in a prerequisite or outside skill (writing
  $5+(-8)$ as $3$) is not a Learner's miss and gets no bridge: list it in `misconceptions` as "Prerequisite slip: …"
  (it counts toward the 3), recall the fact in one line as a clause of the bridge where it is first needed, and name
  the case in the practice step that will hold one typed question served first (e.g. "Add vectors whose coordinate
  sum is negative — includes $5+(-8)$").
- If splitting is not allowed, never drop a case the learner missed or one the node's book exercises need to meet the
  idea budget: keep it — its own bridge when it needs a different decision, otherwise a knowledge point's worked
  example or a question served first.

FOR EACH SKILL (TeachingSpec)
- objective: the TASK the learner can complete after the lesson, verb first (Solve, Compute, Find, Construct,
  Prove …), naming the kind of problem precisely; no "and" joining different methods. "Decide whether", "Determine
  whether", "Evaluate", "Identify", "Recognize", "Verify" only when that judgement is itself the hard part (is this
  set with these operations a vector space? is this sum direct?). Prove / Show / Justify only for proof skills (kind
  'proof').
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
- starting_point: the outcome problem itself, with actual numbers (for a procedure or computation: a problem the
  learner cannot solve yet, e.g. "solve $(2-i)z=3+i$" — and what is missing to solve it; for a proof: the statement
  to prove; for a definition: the question the definition answers). The lesson opens with it and returns to it.
- reasoning_sequence: the necessary bridges, in order, from the starting point to the conclusion — one short
  sentence each, nothing skipped, and nothing the objective does not need. It solves the starting problem on the main
  path before generalizing. About four NEW ideas in all (a new concept, move, rule or notation; a recalled prerequisite
  fact, or the same move on another case or law, is not new; in a proof, citing a definition or law the learner
  already has is not new, and the strategy counts once however many statements it proves): one method, shown one way
  (an alternative layout or method is not a bridge: leave it out of the sequence — the writer may mention it in
  'deeper' — and list it in out_of_scope only if it must not appear even there);
  a routine justification (associativity, why a regrouping is allowed) is a clause, not a bridge, unless the skill is
  a proof; a technique (e.g. checking by multiplying back) comes before anything that relies on it; a misconception
  you name is one the practice will test. A result the book states nearby that the
  success check and the practice do not use (an identity, a general law) is not a bridge either: leave it out of the
  sequence (the writer may mention it in 'deeper'); list it in out_of_scope only if it must not appear even there. The generalization that connects the concrete case to the book's own general statement IS
  part of the objective (for a procedure: the book's formula as "a record of the steps we just performed"; for a
  definition: the definition itself). A bridge that makes a non-routine move (squaring and adding two equations,
  substituting, introducing a helper quantity) states its purpose in the same sentence: "we want X, so we do Y" (e.g.
  "to get the sum $a^2+b^2$, which with the known difference gives each square, square both equations and add"). X
  must connect to the goal: say why reaching X helps — what we can already do once we have it — not only what the
  move does (e.g. "we can already turn a nonzero real number into $1$ by dividing by it, so we want a factor that
  makes the product real; for that the imaginary cross-products must cancel, so try $2-3i$"). The first bridges set
  up the goal and what we can already do; never open with a bridge that rebuts a move the learner has not seen yet
  (a remark about a tempting move comes after that move). Bridges look like "each term of the first bracket
  multiplies each term of the second, giving four products"; "$(3i)(2i)$ has two factors of $i$, so it contains
  $i^2$"; "replacing $i^2$ by $-1$ turns that product into a real number"; "the real terms and the $i$ terms are
  collected separately". At most 7 bridges: a further case is a bridge only when it needs a different decision (a
  case the learner missed, or one whose outcome differs, e.g. a real target that forces a zero coordinate); the same
  move on another case belongs in a knowledge point's worked example or the practice. If more than 7 bridges or about
  4 new ideas seem needed, ask whether the node holds two outcomes; if it does not, drop what the outcome can do
  without. For a proof skill the teaching proves 1–2 representative statements in full; the rest of the family is
  proved in the knowledge points' worked examples and practice.
- success_check: what would demonstrate the objective without requiring a new method, written as the lesson's
  CAPSTONE: one fresh problem of 2–4 guided steps, each using the previous result, that chains this skill with an
  earlier one — the earlier skill produces the input (or uses the result) in its own step, this skill's method is
  applied once without being split into its taught sub-steps or having its key decision given away, and a last step
  makes a different move with the result (a prediction, a second use, an interpretation). E.g. for reciprocals:
  compute $w=(1+3i)(2-i)$ in the form $a+bi$, enter $1/w$, then predict $1/(5-5i)$ without a new construction. No
  step whose answer is known in advance (stated in the problem, an earlier result again, or forced by a definition:
  a number times its reciprocal is $1$, multiplying back gives the given right side), and complex results in the
  form $a+bi$, never as coordinate pairs. When the objective is to decide whether a supplied answer works, it
  includes a candidate that matches the target in exactly one coordinate.
- representations: 0–2, each finishing "This representation helps the learner notice ___" and saying how it is
  read; format 'none' is valid and often right (an annotated sequence of equations may be best). A representation
  comes after the question it answers is established.
- practice_steps: 1–3 knowledge-point titles for the practice part, in increasing difficulty, all inside the
  objective (no new method). Each one adds something NEW to demonstrate (a case that needs different thinking, a
  decision, a reverse problem) — never the same procedure again on harder numbers. If a further knowledge point
  would only repeat, plan fewer: one or two is fine.
- source_pages: PDF page indexes (from the <<page N>> markers) where this is taught; source_refs: printed item
  numbers only ("1.1", "Example 1.2", "Exercise 1A.7"); difficulty 1–5; est_minutes 5–15; misconceptions: at most 3
  specific confusions for THIS skill, each a Learner's miss or one that produces a specific wrong answer the book or
  the method invites — not generic slips the learner never showed.
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

{V3_FEEDBACK}

THE TEACHING SEQUENCE (`blocks`)
An ordered sequence of blocks, each with ONE job — not filled-in headings. At most 9 blocks (10 only when the spec
plans a case the learner missed) and about 350 words of prose outside the math; each block is short (2–6 sentences,
or one computation laid out in display math); the whole sequence reads in about 5 minutes. A typical budget: setup,
3–4 explain/demonstrate blocks (several bridges may share a block when only one of them is a new idea), 1–2 graded
checks, the reflect check, the faded example, generalize, summary. No redundant blocks: a
teaching block never shows the same case as a knowledge point's worked example; the summary never restates the
generalize block (when they would say the same, keep one); a generalize block reuses the lesson's letters or the
book's statement, never re-defines notation a prerequisite introduced (recall it in a clause), and each new symbol it
introduces counts as a new idea.
Write to the learner ("you", "we"), plainly and precisely, like the reference example. Headings, when used, say
what the step does ("First, account for all four products"), not a topic name.
- setup: pose the problem the lesson answers (the spec's starting point) — for a procedure, the task the learner
  will be able to do by the end, and what is missing to do it now.
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
- summary: the method or result in one or two lines — for a procedure, how to do the task.
Every block also names its `new_idea`: the ONE idea it introduces that the learner has not met in this lesson or its
prerequisites (a concept, a move, a rule, a notation, a justification), in a few words — '' when the block only
applies ideas already introduced (a check, a faded example, a summary usually do).

DENSITY — ONE NEW IDEA AT A TIME (see 9–12 above)
- At most one new idea per block, and about four in the whole lesson. A fact recalled from a prerequisite (a
  real-number law, an earlier definition) is not a new idea, and neither is the same move applied to a further case
  or statement. In a proof skill the new ideas are the strategy and each non-routine move (a construction, a choice,
  a case split); proving several statements with one strategy counts that strategy once. Never drop a statement the
  objective names, a case the spec plans or the treatment of a listed misconception to meet the budget — slim its
  block instead, or split it only while the sequence stays within 9 blocks; otherwise move the same move on another
  case into a knowledge point's worked example, and leave out what the outcome can do without.
- One method, shown one way: the method the objective needs. An alternative method or layout of the same
  computation (e.g. multiplying top and bottom by the same number after the quotient was computed with the
  reciprocal) goes to `deeper` (or nowhere, when the spec lists it out of scope), unless the book's objective is
  that method.
- Rigour where it is the point: in a computation or procedure lesson a routine justification of an algebra step
  (associativity, commutativity, why a regrouping is allowed) is at most a clause ("regrouping the product"); a
  derivation from the laws goes to `deeper`. In a proof skill the justifications are the content.
- Teach before use: no block, check or exercise relies on a technique the lesson shows only later (e.g. a check that
  asks the learner to multiply back before multiplying back has been shown) — move the technique earlier or the
  check later.
- Recall in one line: when a block relies on a prerequisite's result, show the needed fact again with its one-line
  reason ("$(2-i)(2+i)=4+1=5$, so $\\frac{{1}}{{2-i}}=\\frac{{2+i}}{{5}}$") — never "using what you already know" alone.
- Stable notation: stay with the running example's numbers; general letters appear once, at the generalize step, and
  the same objects are never renamed again (numbers → $\\alpha,\\beta$ → $a,b$ → $z$ is three renamings too many).
  This is for computation and procedure lessons: in a proof or definition lesson the book's letters come with the
  statement, and writing an object in coordinates ($\\alpha=a+bi$) is not a renaming.
- Every misconception the lesson names (in a block, a warning or the summary) is tested by a GRADED question the
  learner is actually served — a question served first in its knowledge point, or a capstone step — whose key differs
  from the answer a learner holding the misconception would give (inline checks are formative and spare questions
  may never be served, so neither counts). Test every misconception in the spec (at most 3) and every one you name. A
  "Learner's miss" in the spec's misconceptions is what this learner actually got wrong: re-test it with NEW numbers
  that set the same trap, in a format at least as demanding as the one it was missed in (a typed miss typed; a miss
  on a choice question typed when its demand has a value) — the missed problem itself with the wrong answer as an
  option is not a test. A "Prerequisite slip" needs no bridge: recall the fact in one line where it is first needed,
  and serve one typed question that includes the case. A 'reflect' check that asks why a tempting alternative fails names it but does not test it.
- The outcome first and last: the setup poses the task, the main path solves the starting problem before the
  generalization, and the summary says how to do the task.

RULES FOR THE SEQUENCE
- Follow the spec's reasoning_sequence: every bridge appears in the main path, in order, where it is needed —
  demonstrated on the running example, not just asserted (several bridges may share a block; when the spec has more
  than 7, a bridge that repeats a move on another case becomes a knowledge point's worked example instead).
- Keep ONE running example from the starting point through the demonstration and generalization.
- Explain consequential transitions where they happen: "because $i^2=-1$" sits next to the substitution, not in a
  later remark.
- Motivate every non-obvious move BEFORE making it: say what we are trying to find or isolate and why this move gets
  it ("we already know $a^2-b^2=3$; if we also knew $a^2+b^2$, adding and subtracting would give $a^2$ and $b^2$ — so
  square both coordinate equations and add them"). The purpose is why the intermediate result helps reach the goal
  (what we can already do once we have it), not only how the move produces it: "we cannot divide by $2+3i$ yet, but
  we can divide by a nonzero real number — so we look for a factor that makes the product real; for that the
  imaginary cross-products must cancel". A move shown as a bare trick, or justified only by its mechanism ("this
  makes the cross-products opposites"), is a missing bridge. Never refer to or rebut a move before it has been
  introduced — such a remark (also when the spec's bridge puts it first) comes after the move.
- In a first encounter show every algebra line the learner needs with only the stated prerequisites; never jump from
  an expanded form straight to a factored one (write $a^4-2a^2b^2+b^4+4a^2b^2=a^4+2a^2b^2+b^4=(a^2+b^2)^2$, not
  $\\dots=(a^2+b^2)^2$ directly).
- Introduce notation before relying on it: say what new letters stand for ("where $a,b,c,d$ are real numbers"); do
  not import the source's letters ($\\alpha$, $\\beta$, $r$, $s$, $w$, $z$) without need.
- A table, grid or diagram appears only after the question it answers is established; say what every row, column
  or candidate is before or as it is shown.
- Make the learner think before the final exercises: 1–2 graded inline checks plus the one reflect check, each right
  after the step it tests (each is a block and counts toward the 9), then
  the progression complete example → partially completed example (a faded_example) → independent practice. The
  first check comes early, inside the demonstration: one small step on the running example the learner can now do
  (like "what does $6i^2$ become?"). Include one explanation question as a 'reflect' check: it asks WHY — a reason
  the learner must supply — never for a value they could compute (that is a graded check). Ask it before the text
  spells out the reason, or ask why a tempting alternative fails (e.g. "Why did the bottom-right entry become a real
  number?", "Why is $2-3i$ not itself the reciprocal of $2+3i$?"); its model answer must not repeat a line the learner
  has just read. A graded check asks for the value or decision that a preceding block's new idea produces; neither
  its stem nor the text just before it states the answer or the decision it asks for (naming the rule to apply, as
  the reference's "use the fact that $i^2=-1$", is fine). The faded example's missing step is the lesson's
  key move or decision — not arithmetic already printed in the block.
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
  practice questions (expression answers in SymPy syntax with i, complex numbers in the form $a+bi$, and the prompt
  says the form to enter). 'reflect' =
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
stated prerequisites (the spec's assumed_prerequisites and the in-chapter prerequisite skills listed in the request) —
nothing else from the chapter.

{PRINCIPLE}

{V2_FEEDBACK}

{V3_FEEDBACK}

HOW TO REVIEW — TWO PASSES. First pass (missing bridges, `issues`): work through the lesson as that learner would, block by block. For each block, say to yourself what
it asks the learner to accept or do, and check that the earlier blocks (or the stated prerequisites) give it. Solve
every inline check and every practice question yourself and compare with the stored answer, answer format and
solution, and every step of the capstone. For each equation transition, new symbol, example, table and exercise, ask: is its meaning and the
reasoning it requires already established at that point? Report each problem as: location (block number and heading, or the
exercise key) → what the learner must infer → why the current explanation is insufficient → the smallest adequate
repair. Put a question key in the location only when that question itself is faulty (a question still blocked after
the revisions is left out of the lesson); for something missing — an untested misconception, no typed question
served first — name the knowledge point ("knowledge point 2") or the block. severity 'blocking' = the learner cannot follow, is misled, or cannot do the exercise with what was taught;
'minor' = it would noticeably help.

Look specifically for:
- a formula or procedure stated before a concrete case demonstrates it (procedure / computation skills);
- a table, grid or set of candidates shown before its question and the role of its entries are established, or
  entries left unexplained;
- a transition whose reason is not given where it happens ("because $i^2=-1$");
- a non-routine move whose purpose is not stated before it is made (e.g. "square both equations and add" without
  saying which quantity that produces and why we want it), a move justified only by its mechanism or immediate effect
  ("this makes the cross-products opposites", "to cancel the imaginary terms") without saying why that intermediate
  result helps reach the goal, a sentence that refers to or rebuts a move before the move has been introduced, or an
  algebra step that skips the intermediate line (expanded form → factored square) → issue (minor; blocking when it is
  the procedure's key step);
- a faded example whose check is a 'reflect' question instead of the graded missing step;
- a lesson whose objective is checking supplied answers, whose failing candidates fail in every coordinate, or whose "one coordinate
  is not enough" point rests only on an invented value (no computed candidate matching in exactly one coordinate);
- notation used before it is introduced, or letters imported from the book without need;
- the running example abandoned;
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
- drill without a new demand: a question served first that asks for the same procedure as an earlier question of the
  lesson (the teaching checks and the faded example included) on new numbers → issue (blocking) at its key; repair:
  replace it with a different demand (a new case, a prediction without the full computation, spotting the error in
  a shown working, a reverse problem, interpreting a result, choosing the method) or drop it and lower
  target_questions. Not drill: the ONE straightforward independent, typed application of the lesson's main procedure
  that belongs in the served sequence (even though the teaching and the faded example carried it out with guidance);
  a question that re-tests a Learner's miss or a prerequisite slip on NEW numbers (a new case); in a proof skill,
  walkthroughs of different statements; a spare question (after the first target_questions) that repeats the served
  demand once on new numbers — it is the recovery question after a miss;
- no independent, typed application of the main procedure among the questions served first (procedure and
  computation skills) → issue (blocking) at "knowledge point 1"; repair: lead knowledge point 1 with one on fresh
  numbers (the general case of the outcome, not a special case);
- the served sequence (the first target_questions questions of each knowledge point in order, then the capstone)
  asking for the lesson's main procedure from scratch more than once before the capstone — e.g. a later knowledge
  point whose first question is the earlier computation in a new wrapper ("solve $(1+2i)z=4+3i$" after "compute
  $(2+3i)/(1+2i)$") → issue (blocking) at that key; repair: lead that knowledge point with its own new demand
  (reorder its questions or replace the question);
- recognition instead of production: a served question (the first target_questions of a knowledge point) that is
  multiple_choice or true_false although its demand has a value the learner could type, more than one served choice
  question, a choice question whose options carry a computed result the learner should produce or whose distractors
  are not the answers of specific misconceptions, or a key that a learner holding the targeted misconception would also reach → issue
  (blocking for a served question, minor for a spare) at its key; repair: make it typed (the predicted, corrected or
  missing value) or rebuild the options from computed misconception answers;
- a knowledge point that demonstrates nothing new over the previous one, or a target_questions larger than the
  point's real variety (the learner would repeat themselves) → issue (blocking for a repeating knowledge point,
  minor for an inflated target); repair: drop / merge it, or lower target_questions;
- the capstone: missing, not multistep, steps that do not chain (independent mini-questions instead of one problem
  whose later steps use earlier results), steps that together re-run a served practice question, an inline check, the
  faded example or a worked example on new numbers (whole or split into its sub-steps), a step prompt or the problem
  giving away the lesson's key decision (e.g. which factor to multiply by), no earlier skill doing real work in a
  step of its own (only the arithmetic already inside this lesson's procedure), a step whose answer is known before
  it is computed (stated in the problem or a step prompt, an earlier result again, or forced by a definition: a
  number times its reciprocal is $1$, multiplying a solution back gives the right side), a hint that contains the
  whole computation, or a complex result asked as a coordinate pair instead of $a+bi$ → issue (blocking) at "cap";
  repair: the capstone pattern (an earlier skill produces the input → this lesson's method once → a different move
  with the result) or drop the offending step; a wrong step answer → math_errors;
- a question whose answer needs no idea of this lesson: it follows from a prerequisite or outside fact alone, or it
  is the only special value the prompt allows (e.g. "$a^2+b^2=0$ for real $a,b$ — what is $b$?") → issue (minor) at
  its key; repair: a decision with a near miss that needs the lesson's idea;
- main-path content the objective, the success check and the practice do not need (an extra identity or law, a
  digression) → an issue (minor, or blocking when it crowds out needed reasoning); repair: drop it or move it to
  'deeper';
- an exercise that asks for two things at once ("compute …, then decide …") while only one answer is checked;
- minor: an inline check — other than the lesson's one
  explanation ('reflect') check — that only asks for reflection where a small computation on the running example was
  possible; an answer format the prompt does not state clearly (e.g. several roots "in any order" whose stored answer
  is an ordered tuple instead of a set {{…}}), or a complex number asked for as a coordinate pair instead of in the
  form $a+bi$;
- the one explanation ('reflect') check is wanted: it is ungraded on purpose and asks WHY. Report it (minor) only when
  it asks for a value or result the learner could compute, or when its model answer repeats a sentence shown just
  before it; repair: ask for the reason before the text states it, or ask why a tempting alternative fails — never
  turn it into a numeric check.
Second pass (density and outcome, `density_issues`): read the lesson again as a whole — a lesson can have every
bridge and still be too much at once. The request lists each block's declared `new_idea`; judge the content, not
the labels (an undeclared new move counts). Report, in the same form (location → what the learner must handle →
why it is too much or out of order → the smallest repair; blocking when it would leave the learner confused about
the main method, else minor):
- a block that introduces more than one new idea (repair: split it, or move one idea out);
- more new ideas than the outcome needs — the budget is about four (recalled prerequisite facts and the same move
  repeated on another case or law do not count; in a proof skill the law cited for each line is the content, and the
  strategy counts once): name the ones that can go (to `deeper`, out of scope, or a later skill). An idea the
  objective, a planned case or a listed misconception needs is not surplus: for it, propose slimming its block
  (splitting only while the sequence stays within 9 blocks). A second method, a routine derivation or an unused result may still go;
- a second method or layout for the same computation (repair: keep one; the other to `deeper`, or drop it if the
  spec lists it out of scope);
- a routine justification spelled out at length in a non-proof lesson, e.g. a derivation from associativity
  (repair: one clause in the main path; the derivation to `deeper`);
- something used before it is taught: a check, example or exercise that needs a technique a later block introduces;
- "you already know" without the fact: a block that relies on a prerequisite's result without showing it in one line;
- a redundant block: the same move on a case with no new decision, a duplicate of a knowledge point's worked
  example, a summary that restates the generalize block, or a generalize block that re-defines a prerequisite's
  notation (repair: cut or merge; blocking when the sequence has more than 10 blocks);
- shifting notation: the same objects renamed more than once (numbers → $\\alpha,\\beta$ → $a,b$ …); in a proof or
  definition the book's letters from the statement on, and coordinates such as $\\alpha=a+bi$, are fine;
- a misconception the lesson names (a block, a warning, the summary, a 'reflect' check), or one in the spec, that no
  served question (the first target_questions of its knowledge point) or capstone step tests with a key that differs
  from the misconception's answer (inline checks and spare questions do not count); a "Learner's miss" re-tested only
  on its original numbers, a typed miss re-tested only as a choice option, or a miss re-tested only in a spare
  question → blocking; a "Prerequisite slip" with no typed question served first that includes the case → blocking
  (no bridge is needed for it);
- the outcome missing: the setup does not pose the task the objective names, or the main path never solves the
  starting problem.
split_recommended: true only when a needed repair would add another substantial objective; say why in split_reason.
Report only real problems — no clarity scores, no praise, no restating what works. Empty lists are fine.
{MATH_FORMAT}
"""


QUESTION_REPAIR_NOTE = """\
QUESTION REPAIR: the lesson below is final except one practice question. Return ONLY a new `question` for that slot
(same key, same knowledge point) that makes the demand its knowledge point needs, fixes every problem listed under it
and follows the PRACTICE QUESTIONS rules:
- typed (numeric / expression, or a multistep walkthrough with typed steps) whenever the demand has a value;
- its answer — and every step's answer — is something the learner works out: never written in the question, a step
  prompt, a bank of lines or the teaching text; never forced by a definition (a number times its reciprocal is 1, a
  number plus its negative is 0, the statement being proved); never an earlier step's result again; never reachable
  by elimination or from the wording of a shown working (show the working without naming the error);
- when it targets a misconception, the answer a learner holding it would give differs from the key;
- fresh numbers (not the running example's, a worked example's or another question's), self-contained, only methods
  the lesson taught; three progressive hints (multistep: one per step); a complete solution that agrees with the key.
"""


CAPSTONE_REPAIR_NOTE = """\
CAPSTONE REPAIR: the lesson below is final except its capstone (the final multistep problem). Return ONLY a new
`capstone` that follows the CAPSTONE rules, fits this lesson as it stands and fixes every problem listed under it:
- an earlier skill does real work in a step of its own (it computes the input — a sum or product written in the
  problem is fine: computing it is that step), this lesson's method is applied once, unguided, and a last step makes a
  different move with the result;
- fresh numbers: not the running example's, a worked example's or a practice question's, and not the template of a
  practice question;
- no step's answer is written in the problem or in a step prompt up to it, forced by a definition, or an earlier
  result again; each step asks for ONE value (never a tuple of several named quantities);
- a step refers only to earlier steps ("your result from step 1"), and only to steps that exist;
- in a choice step exactly one option is defensible: every distractor is plainly wrong (a wrong law, a wrong value),
  never a less explicit but valid version of the key;
- the lesson's notation; complex results in the form stated in the prompt;
- in a proof skill the proof step asks for one line the learner computes (typed, e.g. a coordinate of one side),
  optionally followed by one justification choice — never a choice among complete proofs that differ in one sign.
"""


REVISION_NOTE = """\
REVISION: below are your previous draft of this lesson and a review of it. Write the complete revised lesson:
- fix every blocking issue with the smallest adequate repair the review suggests (or a better one), and the minor
  ones where the repair is cheap;
- fix every density issue: slim the overloaded blocks (split one only while the sequence stays within 9 blocks; merge
  or cut a redundant block to make room), move alternative methods and routine derivations to 'deeper', move a
  technique before its first use, test every misconception in the spec and every one the lesson names (a "Learner's
  miss" is always tested, never dropped). Density repairs never add new ideas and never move a case the objective or a
  listed misconception needs into 'deeper'; a missing bridge or a one-line recall the first pass asks for is still
  added;
- move reasoning needed for the objective out of 'deeper' into the main path; remove out-of-scope content; correct
  every math error (recheck the affected answers, hints and solutions);
- replace or fix every exercise that needs an untaught method;
- fix the validation problems listed (they make the draft unusable);
- keep everything that works — do not rewrite good blocks for the sake of it, and do not add new objectives."""

SECOND_REVISION_NOTE = (
    "SECOND REVISION: fix only the [blocking] items, the untaught-method exercises, the math errors and the validation "
    "problems below; change nothing else — every other block, question and the capstone stay exactly as they are."
)


# ---------------------------------------------------------------------------
# The proof track (proof.py): proof skills get their own planner, writer and reviewer instructions — the general
# texts with the general proof rules swapped for the track. Each swap must find its text (checked at import).
# ---------------------------------------------------------------------------


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise AssertionError(f"proof track: expected one occurrence of {old[:60]!r}")
    return text.replace(old, new)


PROOF_PLANNER_INSTRUCTIONS = _swap(
    PLANNER_INSTRUCTIONS,
    "For a proof skill the teaching proves 1–2 representative statements in full; the rest of the family is\n"
    "  proved in the knowledge points' worked examples and practice.",
    "For a proof skill follow THE PROOF PLAN below.",
) + "\n" + PROOF_PLANNER_RULES


PROOF_KIND_STRUCTURE = (
    "- proof: follow THE PROOF TRACK below — one statement; numbers → why that is not a proof → a guided special case\n"
    "  the learner builds → what was proved → the general case as a worked table."
)


_PROOF_SERVED_CLAUSE = SERVED_SEQUENCE_RULE[SERVED_SEQUENCE_RULE.index("In a proof skill each knowledge point") :]


_PROOF_WALKTHROUGH_EXAMPLE = MULTISTEP_FORMAT[MULTISTEP_FORMAT.index(" A proof as a walkthrough") :]


PROOF_WRITER_INSTRUCTIONS = _swap(
    _swap(
        _swap(
            _swap(
                WRITER_INSTRUCTIONS,
                "- proof: the statement and why it is plausible (a small case) → the strategy → the proof with every step justified\n"
                "  → a check of one step's justification.",
                PROOF_KIND_STRUCTURE,
            ),
            PROOF_RULE,
            PROOF_TRACK,
        ),
        _PROOF_SERVED_CLAUSE,
        "A proof skill follows THE PROOF TRACK below.)",
    ),
    _PROOF_WALKTHROUGH_EXAMPLE,
    " A proof walkthrough follows THE PROOF TRACK below.",
)


PROOF_REVIEWER_INSTRUCTIONS = _swap(
    REVIEWER_INSTRUCTIONS,
    "split_recommended: true only when",
    PROOF_REVIEW_PASS + "\nsplit_recommended: true only when",
)


PROOF_QUESTION_REPAIR_NOTE = _swap(
    QUESTION_REPAIR_NOTE,
    "- fresh numbers (not the running example's, a worked example's or another question's), self-contained, only methods\n"
    "  the lesson taught; three progressive hints (multistep: one per step); a complete solution that agrees with the key.",
    "- " + PROOF_QUESTION_REPAIR,
)


PROOF_CAPSTONE_REPAIR_NOTE = _swap(
    CAPSTONE_REPAIR_NOTE,
    CAPSTONE_REPAIR_NOTE[CAPSTONE_REPAIR_NOTE.index("- in a proof skill the proof step") :],
    PROOF_CAPSTONE_REPAIR + "\n",
)
