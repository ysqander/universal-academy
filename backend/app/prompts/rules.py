"""The learner's rules, shared by every provider's prompt set (docs/DECISIONS.md, option A of 2026-10-08).

A rule the learner gives lives here, once, and every provider follows it. Wording, emphasis and the corrections
tuned against one model live in that provider's package (app/prompts/openai/, app/prompts/anthropic/), so tuning
one provider never changes the other. A rule still written inside a provider template moves here the next time
it changes."""

from __future__ import annotations

MATH_FORMAT = (
    "Formatting: Markdown. Math only as $...$ (inline) or $$...$$ (display) — never \\( \\), \\[ \\] or bare LaTeX. "
    "Keep the author's notation (e.g. $\\mathbf{F}^n$, $\\mathbf{C}$, 'list', 'length'). Words go outside math: no "
    "\\text{...} phrases inside $...$ (they cannot wrap on a phone). More than two supplied rules, cases or facts are "
    "laid out as a Markdown table or a list — one rule per row or line, at most two short statements on a line — never "
    "one run-on sentence (the learner, 2026-10-11, could not read Exercise 1B.6's ten rules in one sentence; a table of "
    "scalar multiplication by sign and one of sums read at a glance)."
)


# Iteration-3 review (capstones asked for "(real coordinate, imaginary coordinate)" after seven lessons of a+bi, and a
# learner who typed the trained form failed the lesson): one answer form for complex numbers everywhere.
COMPLEX_FORM_RULE = (
    'A complex number is asked for and keyed in the form $a+bi$ — the prompt says "Enter it in the form $a+bi$" and '
    'the answer is SymPy "a+b*i" (e.g. "1/5+2*i/5", "-3*i", "4") — as in every lesson of the course, including '
    "multistep steps and the capstone. Never ask for a complex number as a coordinate pair \"(real coordinate, "
    'imaginary coordinate)" unless the skill is about the ordered-pair definition itself; when only one coordinate '
    "matters, ask for that coordinate alone as a numeric answer. When the problem already uses the letter $a$ or $b$ "
    '(e.g. $\\alpha=a+bi$ with arbitrary real $a,b$, or a scalar $a$), say "Enter it as a complex number in standard '
    'form (e.g. $3-2i$)" instead, or use two letters the problem does not use — never letters that name an unknown, '
    "a vector, an input or a parameter."
)


# Question format rule for every question generator (v1 lessons, top-ups, v2 lessons and their inline checks).
COUNT_QUESTION_RULE = (
    'Never ask "How many of these are true / belong to …?" or any other count of correct statements or matching '
    "items: the count hides which ones the learner got wrong. Use true_false_set instead — the learner marks each "
    'statement true or false. Example: instead of "How many of these equalities are true? 1. $(1,9)=(9,1)$ '
    '2. $\\{1,9\\}=\\{9,1\\}$ 3. $(1,9)=(1,9)$" ask "Mark each equality true or false." with choices '
    '["$(1,9)=(9,1)$", "$\\{1,9\\}=\\{9,1\\}$", "$(1,9)=(1,9)$"] and answer "FTT".'
)


ONE_ANSWER_RULE = (
    "Each question asks for exactly ONE answer, the one the app checks: never \"compute X, then decide Y\" (only "
    "`answer` is checked, so the decision goes unassessed and the expected input is unclear). To assess a decision "
    "about several candidates or claims, use true_false_set with one statement per candidate (e.g. \"$2+i$ is a square "
    "root of $3+4i$\"); for a single decision, ask for the value that settles it (typed), and true_false only when "
    "no value settles it (a general always / sometimes claim)."
)


UNORDERED_RULE = (
    'Several values whose order does not matter (both square roots, all solutions) are a set in braces, e.g. answer '
    '"{1+i, -1-i}", and the prompt says "Enter both roots separated by a comma, in any order"; a tuple "( … )" is '
    "only for ordered lists and vectors, where the order is part of the answer. A set with no element is answer \"{}\" "
    '(the prompt says "enter {} or ∅ when no value works"); the whole field is answer "C" or "R" (the prompt says '
    '"enter C if every complex number works").'
)


TF_SET_KIND = (
    'true_false_set: "Mark each statement true or false" — choices holds the 2–5 statements (each self-contained, '
    'without its own number); answer = one letter per statement in order, T or F (e.g. "FTT"); correct_choice null; '
    "tolerance null. Mix true and false statements; the hints and the solution address every statement by its "
    "number (why it is true, or a counterexample). Each statement scores on its own, so a true_false_set counts as a choice question (marking at random earns half its points). "
    "Each statement is a complete claim, true or false on its own: never a bare value, vector or input that a phrase "
    "in the prompt completes (the learner, 2026-10-11, misread 'Mark each statement true or false: testing "
    "$1\\otimes v=v$ at this vector exposes the failure' with the statement '$v=(3,3)$' as 'does $1\\otimes v=v$ "
    "hold?'). Write '$v=(3,3)$ is a counterexample to $1\\otimes v=v$ (that is, $1\\otimes v\\neq v$)'."
)


# The learner's review of the S2 lessons (iteration 3): no "show your working", fewer and more varied questions, a
# multistep problem at the end that chains the ideas. Shared by every question generator.
TARGET_RULE = (
    "target_questions (1–3) = how many answers really demonstrate THIS knowledge point. Be honest: 1 when a single "
    "answer shows it (the method was already demonstrated before the questions), 2–3 only when the "
    "point has real variety (cases that need different thinking). Write target_questions + 2 questions: the first "
    "target_questions are served as the knowledge point's test; the 2 extra ones are served only after a miss (so "
    "the learner can recover) and in later reviews. Knowledge points need not have the same number of questions — "
    "never pad with repetition."
)


DISTINCT_DEMANDS_RULE = (
    "Every question makes a DIFFERENT demand — never the same procedure again with new numbers: a learner who has "
    "shown the method once experiences a second identical exercise as pointless drill. Demands to choose from: one "
    "straightforward application of the method (once among the questions served first; otherwise only as a spare); a new case that needs a different thought (a "
    "negative or zero part, a real or pure imaginary number, an edge case); a prediction or decision without the full "
    "computation (is it real? which sign? which is larger?); a reverse problem (find the missing number or factor that "
    "gives a stated result); interpreting a result; deciding which method or fact applies. Questions stay inside the "
    "objective: a different demand, not a new method. Every question needs THIS knowledge point's idea to answer: "
    "not one whose answer follows from a prerequisite or outside fact alone, or is the only special value the prompt "
    'allows (e.g. "for real $a,b$ with $a^2+b^2=0$, what is $b$?" needs only that real squares are nonnegative) — '
    "make such a question a decision with a near miss that needs the lesson's idea. Ask for the result directly "
    "(\"Compute …\", \"Enter …\", \"Decide …\"): never open with a wrong claim to correct (\"A student writes … That "
    "is wrong. Enter the correct …\") — a red herring the learner must read past (the learner, 2026-10-11). Show a wrong "
    "working only when telling two easily confused moves apart IS the demand (locate the flawed step of an argument, "
    "or judge one classic slip) — rare."
)


# The evaluation of the 2026-10-07 re-plans: target 1 per knowledge point + "no drill" pushed every question served
# first to multiple choice — the learner recognised answers instead of producing them.
TYPED_DEMAND_RULE = (
    "TYPED ANSWERS FIRST: the learner should produce answers, not recognise them. A different demand is still a typed "
    "numeric/expression answer whenever it has a value: a prediction → the predicted value; spotting an error → the "
    "corrected value; a reverse problem → the missing number; a decision about one candidate → the value that settles "
    "it (several candidates → a true_false_set). multiple_choice and true_false only when the demand has no value to "
    "type: choosing a method, a law or a justification, or judging a general claim. A choice question's distractors "
    "are the answers a learner holding a specific misconception would give (computed, not strawmen), the key differs "
    "from every one of them, and no option carries a computed result the learner should produce (e.g. a method option "
    '"multiply by $2-3i$, giving $\\frac{7-4i}{13}$").'
)


SERVED_FIRST_RULE = (
    "Among the questions served first (the first target_questions of each knowledge point) at most one is "
    "choice-based (multiple_choice, true_false or true_false_set), and at least one is a typed "
    "independent answer (a multistep question counts when at least one of its steps is typed)."
)


TYPED_FIRST_RULE = f"{TYPED_DEMAND_RULE} {SERVED_FIRST_RULE}"


NO_WRITTEN_ANSWERS_RULE = (
    'No free_response questions and no "show your working": the app checks only the answer, so an instruction to '
    "write out the steps, the products or a proof is never assessed. A computation is a typed numeric/expression "
    "answer; a chain of reasoning (a proof, an explanation, a computation with several stages) is a multistep "
    "question whose steps are each machine-checked."
)


MULTISTEP_FORMAT = (
    "multistep: a guided problem. The prompt states the whole problem and its goal; `steps` holds 2–4 steps the "
    "learner answers one at a time (seeing each result and its explanation before the next), and a later step uses "
    "an earlier result. Each step: prompt (what to do now and the exact form to enter — a complex result in the form "
    "$a+bi$), kind numeric / expression / "
    "multiple_choice / true_false / true_false_set with that kind's conventions for choices, correct_choice, answer, "
    "answer_display and tolerance, ONE hint (using it halves the step's point) and an explanation (1–2 sentences "
    "shown after the step: why this is the result). The question itself: answer \"\", choices [], correct_choice "
    "null, tolerance null, hints [], checklist []; answer_display = the final result; solution = the full worked "
    "solution. A proof as a walkthrough: steps like \"Which definition do we apply first?\" (multiple_choice), "
    "\"What is the first coordinate of $x+y$?\" (expression), \"Which property of $\\mathbf{F}$ justifies this "
    'line?" (multiple_choice), "Which hypothesis was used?" (multiple_choice).'
)


# The lesson's final problem (v1 and v2 lessons). Iteration-3 review of S3a/S3b: both capstones were the lesson's
# own procedure split into its sub-steps (the lesson's key decision given away in a step prompt) plus a "multiply
# back" step whose answer — 1, or the right side printed in the problem — was known before computing anything.
CAPSTONE_RULE = """\
CAPSTONE (`capstone`) — the lesson's final problem, served after the practice
- ONE problem, key "cap", kind "multistep", tier 3, with 2–4 steps; a later step uses an earlier step's result.
- It CHAINS this lesson's idea with at least one earlier skill (a prerequisite named in the request) into one
  problem. The earlier skill does real work in a step of its own — it produces the input this lesson's method is
  applied to, or it uses this lesson's result — not merely the arithmetic already inside this lesson's procedure.
  This lesson's method is applied ONCE and unguided: do not split it into the sub-steps the lesson taught, and do not
  give away its key decision (e.g. which factor to multiply by) in the problem or a step prompt.
- Not a replay: taken together, the steps must not re-run a practice question, an inline check, the faded example or
  a worked example on new numbers — neither whole nor split into parts. Use a fresh problem: its numbers are not
  the running example's, a worked example's or a practice question's — not even those numbers multiplied in another
  order (a step whose result the lesson has already shown is not fresh work).
- Pattern: an earlier skill produces the input → this lesson's method on it, in one step → a different move that
  uses the result (a prediction or decision without a full recomputation, a second use of the result, an
  interpretation). Illustration for a lesson on complex reciprocals whose prerequisite is complex multiplication
  (use your own problem and numbers): "Let $w=(1+3i)(2-i)$. Step 1: enter $w$ in the form $a+bi$ ($5+5i$). Step 2:
  enter $1/w$ in the form $a+bi$ ($\\frac{1}{10}-\\frac{1}{10}i$). Step 3: $5-5i$ differs from $w$ only in the sign of
  its imaginary coordinate — without a new construction, enter $1/(5-5i)$ ($\\frac{1}{10}+\\frac{1}{10}i$)." NOT:
  "multiply $1-2i$ by $1+2i$; divide by $5$; multiply back and confirm you get $1$" — a practice question split into
  its parts, plus a step whose answer is known in advance.
- No step's answer may be known before it is computed: never stated in the problem or in a step prompt (e.g. the
  right side of the equation being solved), never an earlier step's result again, never forced by a definition (a
  number times its reciprocal is $1$, multiplying a solution back gives the right side, the reciprocal of a
  reciprocal is the number). Checking the result ("multiply back") belongs in the last step's explanation and the
  solution, not in a scored step; a checking step is allowed only when it asks for a value the learner must compute
  and that is shown nowhere. A step's hint points to the move; it never contains the whole computation of the answer.
- Each step: one machine-checked answer, ONE hint, an explanation and answer_display (the multistep format); complex
  results in the form $a+bi$, never as coordinate pairs. The question prompt states the whole problem and its goal;
  each step prompt says exactly what to do now and the form to enter."""


OUTCOME_RULE = """\
SKILLS ARE OUTCOMES (the learner's rule): a skill is defined by what the learner can DO at the end of its lesson — a
task they complete: solve an equation, compute a result, find all solutions, construct an example, prove a general
statement (a proof is an outcome). Ask "which problem can the learner solve after this lesson that they could not
solve before?" — the objective names that problem. Evaluating an expression, checking a supplied answer, identifying
or recognising something are steps on the way to such a task: they belong inside the skill that uses them (a
knowledge point, an inline check, a capstone step), not in a skill of their own — unless the judgement itself is the
hard part (deciding whether a set with given operations is a vector space; whether a sum is direct). Computing
$\\beta/\\alpha$ is how you solve $\\alpha z=\\beta$; checking a candidate root is how you confirm the roots you found.
NOTHING TRIVIAL (the learner, 2026-10-11, after "Compute a supplied candidate's cube"): a task whose method the learner
already has from earlier mathematics, applied unchanged, is not a skill — cubing a complex number is a positive-integer
power, and a cube root is the number whose cube is given, as for real numbers. Fold such a task into the skill that
uses it (a knowledge point, an inline check, a capstone step) or leave it out. Every skill teaches something new at
this course's level."""


PRINCIPLE = """\
PRINCIPLE: Reduce the number of new ideas the learner must handle at once; do not reduce the reasoning that connects
them. A summary tells you what someone who understands the subject should remember. A lesson helps you become that
person."""


V1_FAILURES = """\
WHAT WENT WRONG IN THE FIRST LESSONS (the learner's review — never repeat these)
1. One lesson, several outcomes: multiplication, powers and finding square roots (with checking roots — a step of
   finding them, see 13) were taught together.
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


V3_FEEDBACK = """\
WHAT THE LEARNER SAID AFTER THE THIRD ROUND (the complex-quotient lesson, "a little dense and unclear") — never
repeat these either
9. Too many new ideas in one lesson: division as multiplication by the reciprocal, then a second layout (multiply top
   and bottom by the same number), a general formula in new letters, and a derivation of the solving step from
   associativity — each fine alone, together dense. One new idea per block, about four per lesson, one method.
10. Used before taught: a check asked the learner to multiply back to test an answer before multiplying back had
   been shown.
11. "Using the construction you already know" without showing it: recall the needed fact in one line instead.
12. A warning in the summary ("use $\\beta/\\alpha$, not $\\alpha/\\beta$") that nothing had taught or checked — and the
   learner then chose the wrong quotient in practice.
13. Skills are outcomes: the learner wants to come out of a lesson able to DO something (solve an equation, find all
   roots, prove a statement); evaluating, checking or identifying are steps toward that, not lessons of their own. A
   proof is an outcome."""


# The learner after S10 and S13 (2026-10-10). Shared by every provider's planner, writer and reviewer, proof track
# included; pipeline/lesson_v2.example_point_problems finds the usual wordings and sends the draft to the revision.
EXAMPLE_FEEDBACK = """\
WHAT THE LEARNER SAID ON 2026-10-10 (after S10 and S13) — never repeat this either
17. "One specific example does not make a law" is a fact the learner already knows, and the lessons kept making the
   point ("Both routes reached −3+i. Why does this not yet prove the law?", "Why is knowing h(1/2) = 3 not enough to
   specify h on [0, 1]?", a claim to reject such as "the check with x = (1, −3) alone proved it for every x", a
   bridge "one example is not a proof — letters stand for every input"): "redundant". No block, check, question,
   claim, option, misconception, summary or 'deeper' note makes that point, in any lesson, and none asks why one
   value, example or special case does not prove or determine the general case. What matters is the other direction:
   a concrete case EXEMPLIFIES the general rule. Start from numbers when they show the moves, then make the same moves
   with letters without a transition speech ("this is only one case, so we replace the numbers by letters"): say
   "The same moves with letters" and, where it helps, name the numbers as one instance of the letters (α = 2+i is
   a = 2, b = 1). A definition says positively what determines its object (a function on [0, 1] is given by its value
   at every point). A counterexample that refutes an "always" claim is a different point and is still taught."""


# The same rule in one line, for the prompts without the feedback list (v1 lessons, review top-ups).
EXAMPLE_POINT_RULE = (
    "No question, claim, option or argument whose point is that one example, value or special case does not prove "
    "or determine the general case: the learner knows it and finds the point redundant (2026-10-10). A counterexample "
    "that refutes an \"always\" claim is still fine."
)


# The learner after S12a (2026-10-10), with a review they endorsed: "checking requirements for a definition" is the
# skill, and the lesson left the learner to infer what each calculation tested, why it was chosen and what it
# established. Shared by every provider's planner, writer and reviewer.
DEFINITION_CHECK_RULE = """\
DECIDING WHETHER SOMETHING MEETS A DEFINITION (the learner, 2026-10-10, after S12a): teach the decision process
explicitly. This applies to every block, question and capstone whose task is to check a definition's requirements —
a set with given operations is a vector space, a subset is a subspace, a sum is direct, an operation is defined on a
set.
1. Before any calculation, separate what is SUPPLIED from what is TESTED: the given set, scalars and operations (the
   problem defines them) versus the requirement under investigation (to be tested, never assumed). Say what would
   settle it: an equality requirement holds when an argument covers every allowed input (letters), and fails when one
   allowed choice of inputs makes the two sides differ; the object meets the definition only when every requirement
   is justified — the operations being defined on the set and returning elements of it included — and fails it with
   one failed requirement.
2. Keep the supplied operation visible: give an invented operation its own symbol ($a\\star v$, $u\\oplus v$), restate
   its rule in the block that computes with it, and say in words what it does ("multiplies the first coordinate by
   $a$ and replaces the second by $0$"). Products and sums inside coordinates are ordinary arithmetic of
   $\\mathbf{F}$, whose laws may be used there; a law being tested is never assumed for the supplied operation.
3. Translate each requirement into a plain question before the algebra, as two procedures to compare: "multiply by
   $b$, then by $a$ — the same vector as multiplying once by $ab$?"
4. Equality requirement: compute each procedure separately with the supplied rule, compare coordinate by coordinate,
   name the known law that makes each coordinate agree (or show the coordinate that differs), and end by saying what
   is established: the requirement holds for every input, or the inputs shown refute it.
5. Existence requirement (an identity, an inverse): name the unknown, write the equation that defines its role (an
   identity is ONE element that works for every input; an inverse is found for each element and may depend on it),
   solve it, check that the solution lies in the set, then verify that it does the job. No element exists when that
   equation has no allowed solution; trying candidates is not the method.
6. Choose with a reason: say why this requirement is promising to test (read what the operation does — a rule that
   erases a coordinate threatens $1v=v$) and why these inputs expose it (a nonzero second coordinate; zeros elsewhere
   to keep the arithmetic simple). Every worked example ends with one sentence saying what it established and
   whether the overall question is now settled.
7. One reasoning pattern per lesson: refuting by a counterexample, verifying an equality for every input and finding
   a required element are separate tasks, taught in that order in short lessons of their own. A lesson whose outcome
   is a yes/no decision also meets a genuine "yes" case (the set and the operations together decide).
8. Assess the decisions, not only the arithmetic: the learner chooses the requirement or the inputs, names the failed
   requirement and states the verdict, supplies the known law that makes the coordinates agree, and transfers the
   method to a changed operation whose first natural test passes (so the method is never "check $1v$ and stop")."""


SPLIT_TEST = """\
SPLIT TEST: does this node hold separate OUTCOMES — tasks someone could be able to do one of and not the other, each
worth doing on its own (multiplying vs. computing powers vs. finding square roots; a computation vs. proving the laws
it relies on; refuting a definition's requirement by a counterexample vs. verifying one for every input vs. finding a
required element — DECIDING WHETHER SOMETHING MEETS A DEFINITION, point 7)? If yes, split into those tasks. A step on the way to the node's outcome stays in the lesson, even when
it needs reasoning of its own: computing $\\beta/\\alpha$ is how you solve $\\alpha z=\\beta$, and checking a supplied
root is how you confirm the roots you found — both are knowledge points (or capstone steps) of the outcome skill,
not skills of their own. Each new skill must be a meaningful task on its own — "Multiply two complex numbers", never
"multiply the first two terms". Keep the node when one small family of problems assesses all of it. Statements
proved with the same strategy are ONE outcome ("prove commutativity, associativity, identities and distributivity of
complex arithmetic by comparing coordinates" is one skill whose groups of laws are knowledge points); a proof that needs a
different strategy may be its own. A new skill must be worth a 5–15-minute lesson: an operation learned in a few
minutes that the node's outcome uses at every step (scaling a vector inside solving $u+ax=v$) stays a step of that
outcome, and a dependent that needs only that operation maps to the skill that teaches it."""


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
- checking supplied answers (normally a knowledge point of the skill that finds the answers; a lesson of its own
  only when deciding is itself the hard part, or for an older node that must be kept — then its objective is the
  computation, e.g. "compute the cube of $w$ and find which number it is a cube root of"): state what is being checked
  and what counts as "works" before any candidate appears; work one candidate fully; only then a comparison table (suggestion |
  what we get | matches?); then the point (e.g. getting the real part right is not enough — the imaginary part,
  including its sign, must match too). At least one supplied candidate in the main path (a table row or the faded
  example) is actually computed and matches the target in exactly one coordinate — e.g. the right real part with
  the imaginary part of the wrong sign — and the lesson says explicitly why it fails (one matching coordinate is
  not enough: every coordinate must match). Never make this point only with an invented result, and use numbers
  that differ from the practice questions. When checking is a knowledge point of a skill that finds the answers, the
  main path only confirms the answer it found, in one line, after the method has been shown; the near-miss decision
  about supplied candidates goes into that knowledge point's practice (a true_false_set with one candidate that
  matches exactly one coordinate). The full structure above is for a lesson whose objective is the check."""


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


REFERENCE_CHECKING = r""""We want to check suggested solutions to $z^2=3+4i$. Someone has suggested the four numbers below; we check whether these
suggestions work. A suggestion works only if squaring it gives the entire target number." Work one candidate fully ($(2+i)^2=4+2i+2i+i^2=3+4i$), then the comparison table
(suggestion | what we get when we square it | matches?) — one row is a real near miss: $2-i$ squares to
$4-4i+i^2=3-4i$, the same real part as $3+4i$ but the imaginary part has the wrong sign, so $2-i$ does not work — then
the point: getting the real part right is not enough — the imaginary part, including its sign, must match too."""


SERVED_SEQUENCE_RULE = (
    "Read what the learner is actually served as ONE sequence: the first target_questions questions of knowledge "
    "point 1, then those of knowledge point 2, …, then the capstone. In that sequence the lesson's main procedure "
    "from scratch (e.g. a whole quotient) appears exactly once before the capstone, as one independent typed answer on "
    "fresh numbers — normally the first served question of knowledge point 1 (the faded example was guided, so it does "
    "not count). A later knowledge point leads with ITS new demand (for \"interpret an equation as a "
    "quotient\": which quotient solves it, or whether a candidate satisfies it — not another full quotient in a new "
    "wrapper); a plain repeat of the procedure may only be one of its spare questions. The capstone then applies the "
    "main procedure once more, but to an input an earlier skill produces (or a case that needs a decision), in one "
    "unguided step, and ends with a step that uses or interprets the result: it is never the sub-steps of a served "
    "question, a teaching check or the faded example re-run on new numbers. The one independent application is the "
    "general case of the outcome (e.g. a nonreal target), not a special case; a special case the learner missed is "
    "re-tested as its own question served first (raise that knowledge point's target_questions to 2 when two misses "
    "need it) or as a capstone step. (This is for procedure and computation skills. In a proof skill each knowledge "
    "point's first question is a walkthrough of a statement the teaching did not prove, with at least one typed step, "
    "and walkthroughs of different statements are not repeats; in a definition, distinction or interpretation skill a "
    "typed concrete instance is enough.)"
)


PROOF_PATTERN_NAMES = {
    "two_routes": "Two routes, same destination",
    "find_then_check": "Find it, then check it",
    "suppose_two": "Suppose two, show they are one",
    "law_chain": "One law per line",
    "two_halves": "Two halves, one at a time",
    "conditions_or_counterexample": "Check the conditions, or break one",
}


PROOF_FEEDBACK = """\
WHAT THE LEARNER SAID ABOUT THE FIRST PROOF LESSON (S4a, "quite convoluted") — never repeat these
14. The learner's part was trivial algebra inside someone else's proof: the faded step "rewrite $a(dt)+b(ct)$ with real
   products regrouped" tested nothing about the proof.
15. Long expansions instead of meaning: 12-term expansions of $(\\alpha\\beta)\\lambda$ in six letters dominated the
   lesson.
16. Symbol overload: on paper the learner "got lost interchanging the symbols" between the two calculations. The
   questions also renamed the letters every time ($p,q,r,s$; $h,k,l,m,n,t$), inflated statements
   ($(\\alpha+8)(\\beta+11i)=(11i+\\beta)(8+\\alpha)$) and asked "which continuation" between long method sentences.
What they asked for: understand a SMALL proof and construct its calculations yourself — numbers first, then a
special case you build, then the general proof as a worked solution, then transfer."""


PROOF_PATTERNS = """\
THE SIX PROOF PATTERNS — each has ONE canonical name and scaffold, used unchanged in every lesson that applies it (the
transfer between lessons is then visible; the strategy counts as one new idea):
- two_routes, "Two routes, same destination": an identity. Compute each side on its own from the definitions, then
  compare every coordinate (entry, value at a point). Scaffold: a slot table per application of a definition — for a
  complex product (x+yi)(u+vi): product | x | y | u | v | real xu−yv | imaginary xv+yu, one row per product, "reset
  the slots" between products (in F^n: entry k of each list; in F^S: the value at one point s) — and a comparison
  table: coordinate | route A | route B | the real law that makes them equal.
- find_then_check, "Find it, then check it": existence (and uniqueness) by solving the defining equations. Finding is
  scratch work (suppose it exists, turn the condition into real equations, solve); checking shows the found object
  works. Scaffold: unknown | condition | real equations | solved; two labelled boxes "Finding (scratch)" and "Checking
  (proof)". Solving shows only this can work (uniqueness); the check shows it works (existence).
- suppose_two, "Suppose two, show they are one": uniqueness. Name two candidates and evaluate one expression two ways
  (0+0′ is 0′ because 0 is an identity, and 0 because 0′ is one). Scaffold: a facts card (both candidates with their
  defining equations) and route | which fact removes which symbol | result.
- law_chain, "One law per line": a derivation from the axioms in an abstract space. State the goal and the toolbox
  (axioms by name, earlier results); motivate the one purposeful move before making it ("the only fact we have about
  the scalar 0 is 0+0=0, so write 0v as (0+0)v"); then line | what changed | the one law.
- two_halves, "Two halves, one at a time": an iff, a set equality, "the smallest … containing". An assume | show |
  pattern table with one row per half, filled before either half is proved; each half uses its own pattern.
- conditions_or_counterexample, "Check the conditions, or break one": closure for arbitrary members, or one pinned
  counterexample. A membership card per condition: have (named values) | need | link. One counterexample refutes
  "always"; a counterexample must meet the hypotheses."""


REFERENCE_PROOF = """\
REFERENCE PROOF LESSON — structure only. Its statement is not in this course (conjugates come in a later chapter):
adapt the arc, never the content. Pattern two_routes; statement conj(αβ) = conj(α)·conj(β) for all α, β ∈ C, where
conj(x+yi) = x − yi; letters α = a+bi, β = c+di.
Teaching (S = a scored step: it counts toward the lesson, and the next block comes only once it is answered)
1. setup "Two routes, same destination": route A multiplies α and β, then conjugates; route B conjugates α and β,
   then multiplies. The statement says the routes always meet; we may not assume it. We may use the definitions of
   product and conjugate, equality = both coordinates equal, real-number laws. We may not use the statement itself.
2. demonstrate "First, fill the slots": α = 2+i, β = i. Route B's first leg: conj(α) = 2 − i and conj(i) = 0 − 1i, so
   the slot row of conj(α)·conj(β) is x = 2, y = −1, u = 0, v = −1.
3. check (S, expression, constant key): "Compute the row and enter conj(α)·conj(β) in standard form." Key −1−2i. A
   learner who mixes up the inputs gets 1−2i or 1+2i. Hint: "x, y come from conj(α), u, v from conj(i)".
4. check (S, expression, constant key) "Route A, calculated separately": the slot row of αi = (2+i)(0+1i) with both
   results '?'; "enter αi in standard form". Key −1+2i. Explanation: its conjugate is −1−2i — the same destination.
5. demonstrate "The same moves with letters": keep β = i and write α = a+bi (the numbers above are a = 2, b = 1).
   Route B's first leg: conj(α) = a − bi, conj(i) = 0 − 1i. No sentence on why letters are needed.
6. faded_example (S): row x = a, y = −b, u = ?, v = ?. Check (expression): "Enter conj(α)·conj(i) in standard form; the
   coordinates may stay unexpanded." Key -b-a*i.
7. demonstrate: route A, αi with slots (a, b, 0, 1) gives −b + ai; its conjugate is −b − ai. Comparison table: real
   −b | −b; imaginary −a | −a. So conj(αi) = conj(α)·conj(i) for every α.
8. check (S, true_false_set) "Which moves may this proof make?": reorder real products inside a coordinate (T); start
   from conj(αi) = conj(α)·conj(i) and simplify both sides (F — it assumes the statement); conclude once the real
   coordinates agree (F — equality needs both); rewrite αi as iα, commutativity being proved in an earlier lesson (T).
9. generalize (S): β = c+di; route A | route B in one table — inner result, then the slots of route B's outer product
   as '?' (check: "enter the four slots (x, y, u, v) of conj(α)·conj(β)", key (a, -b, c, -d)); the coordinate rows in
   the check's explanation; the real laws named once; "the letters were arbitrary, so … for all α, β"; the method in
   one line.
Variant (the capstone slot, weight 2): conj(α·conj(β)) = conj(α)·β — one factor is conjugated before the product: s1
route A, α·conj(β) then its conjugate, key a*c+b*d+(a*d-b*c)*i; s2 the four slots of route B's product conj(α)·β, key
(a, -b, c, d); s3 a true_false_set of 3 claims about the changed proof (the imaginary coordinates now meet by the real
law −(bc − ad) = ad − bc (T); conjugating both sides of the variant is a valid first step (F — it assumes it); both
routes still need only the definitions and real-number laws (T)).
Practice (not served in the lesson — a retry and the reviews ask it): KP1 Transfer — walkthrough of
conj((1+i)α) = (1−i)·conj(α): s1 route A, key a-b-(a+b)*i; s2 the slots of route B's product, key (1, -1, a, -b); s3
the real law for one shown pair (choice). KP2 What the proof rests on — worked example conj(conj(α)) = α, each line with
what licenses it; a true_false_set of 5 claims about moves in shown arguments."""


# The planner's and writer's header above the skill's misconceptions (a Learner's miss is re-tested on NEW numbers).
MISCONCEPTIONS_HEADER = (
    "Known misconceptions (a line starting \"Learner's miss\" is what this learner actually got wrong: re-test it with "
    "NEW numbers that set the same trap, in a format at least as demanding as the one it was missed in (a typed miss "
    "typed; a miss on a choice question typed when its demand has a value), as a question served first in its "
    "knowledge point or a capstone step — the missed problem itself with the wrong answer as an option is not a test; "
    "a line starting \"Prerequisite slip\" needs no bridge: a one-line recall where it is first needed and one typed "
    "question served first that includes the case):"
)


# The same header for a proof lesson (rules.GUIDED_PROOF_RULE): its knowledge points are not served in the lesson, so a
# miss is re-tested by a scored step or the variant.
PROOF_MISCONCEPTIONS_HEADER = (
    "Known misconceptions (a line starting \"Learner's miss\" is what this learner actually got wrong: re-test it with "
    "NEW numbers that set the same trap, in a format at least as demanding as the one it was missed in, as a scored "
    "step of the teaching or a variant step whose key differs from the misconception's answer — a knowledge-point "
    "question does not count, it is served only in a retry and in reviews; a line starting \"Prerequisite slip\" "
    "needs no bridge: a one-line recall where it is first needed and a typed scored step that includes the case):"
)


# What counts as circular in a proof (2026-10-08: all four S4b lessons generated that day called "cancelling α" circular
# in the uniqueness proof of inverses, and keyed a true claim False). Shared by every provider's proof track.
CIRCULARITY_RULE = """\
WHAT IS CIRCULAR: a step is circular only when it uses the statement being proved, or a result that is not yet
available. "May not use" lists exactly those — never a step whose ingredients are already in hand. In particular, in the
uniqueness half of an existence-and-uniqueness proof the objects exist BY HYPOTHESIS: with αβ = 1 and αβ′ = 1,
β = β·1 = β(αβ′) = (βα)β′ = 1·β′ = β′ uses only associativity, commutativity and the identity (proved in S4a), so
"multiplying by β" — cancelling α — is valid there, not circular. What may not be used is the general cancellation law
or the formula 1/α before they are proved. A question never keys such a valid step as False, and a "may not use" line
never forbids it."""


# The learner's request after studying the proof-track S4a (2026-10-09): the proof is the exercise, then one variant —
# no replay of the proof in practice. Shared by every provider's proof track (planner, writer, reviewer).
GUIDED_PROOF_RULE = """\
WHAT THE LEARNER ASKED FOR AFTER STUDYING THE PROOF-TRACK S4a (2026-10-09): going through exercises that "basically
repeat the same thing again" was the part they disliked. In a proof lesson the steps the learner works out while going
through the proof ARE the scored exercise, and the lesson ends with ONE short variant that shows the understanding is
there — never a replay of the proof. So:
- SCORED STEPS. Every graded inline check of the teaching sequence (every kind but 'reflect') counts toward the
  lesson's score like a question: right first time 1 point; after a wrong first answer the learner sees "not yet" and
  the check's hint, and a right second answer earns ½ (a choice check has one try); "Show answer" earns 0. The app
  sends the lesson only up to the next unanswered scored step, so the block AFTER a scored step may show its answer.
- 4–6 SCORED STEPS, and they are the proof's real moves, never tidy-ups: the key computation(s) of the concrete case
  (1–2 steps), the guided case's key line (the faded example), what the proof rests on (the true_false_set of the
  plan's 3–4 claims about this proof's moves: a move it may make, a move it may not make — the statement itself or an
  unproved law, shown as a move — what equality needs, which stage proved what), and one entry of the general case's
  worked table (a '?' cell: a short slot tuple, a solved coordinate, or a coordinate of at most 4 terms). No reflect
  check: nothing asks or says why the numbers do not prove the law (rule 17).
- EACH SCORED STEP: its answer is printed nowhere before the learner answers — not in its own block or prompt, not in
  an earlier block, nor in an earlier check's prompt, explanation or answer (an earlier skill's result recalled in the
  setup counts as printed); never a value the statement forces (the second route's final value, the 1 of a checked
  inverse); `hint` = one sentence pointing to the move or the inputs, never the result; a typed answer's prompt says
  the form to enter; a multiple_choice has 4 options; never a true_false check (a coin flip) — a true_false_set of 3–4
  claims instead.
- THE VARIANT (the capstone field; it counts double): the plan's variant statement — a statement the teaching did
  not prove, where the same strategy works but ONE thing changes (another right-hand side, an extra term, the other
  operation, a fixed input of another kind, a hypothesis dropped and why it is needed). 2–3 steps, at least one typed
  (two in two_routes: route A's result and route B's slots or first leg), at most 4 letters, no slot rows, no earlier-skill computation needed; never the
  teaching's statement again with other numbers, never a step whose key the statement forces. Its last step asks
  what the change does (to the proof, or to the result) — a question the text just shown does not answer.
- A misconception the lesson names, and a "Learner's miss", is tested by a scored step or a variant step whose key
  differs from the misconception's answer.
- PRACTICE IS FOR LATER. The knowledge points (the transfer walkthrough, "what the proof rests on", their spares) are
  not served in the lesson: a retry of the lesson and the spaced reviews ask them. Write them as before, with
  target_questions 1."""
