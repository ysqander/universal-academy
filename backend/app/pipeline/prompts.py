"""Instruction texts for the build pipeline's LLM calls (SPEC §4.2, §4.3)."""

from __future__ import annotations

MATH_FORMAT = (
    "Formatting: Markdown. Math only as $...$ (inline) or $$...$$ (display) — never \\( \\), \\[ \\] or bare LaTeX. "
    "Keep the author's notation (e.g. $\\mathbf{F}^n$, $\\mathbf{C}$, 'list', 'length'). Words go outside math: no "
    "\\text{...} phrases inside $...$ (they cannot wrap on a phone)."
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
    "number (why it is true, or a counterexample). With 3 or more statements it is not a guessable choice question."
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
    "computation (is it real? which sign? which is larger?); spotting the error in a shown working (typed: the "
    "corrected value; multiple_choice \"which line is wrong\" only when locating the error is itself the demand); a reverse problem (find the missing number or factor that "
    "gives a stated result); interpreting a result; deciding which method or fact applies. Questions stay inside the "
    "objective: a different demand, not a new method. Every question needs THIS knowledge point's idea to answer: "
    "not one whose answer follows from a prerequisite or outside fact alone, or is the only special value the prompt "
    'allows (e.g. "for real $a,b$ with $a^2+b^2=0$, what is $b$?" needs only that real squares are nonnegative) — '
    "make such a question a decision with a near miss that needs the lesson's idea."
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
    "choice-based (multiple_choice, true_false, or a true_false_set of only 2 statements), and at least one is a typed "
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
$\\beta/\\alpha$ is how you solve $\\alpha z=\\beta$; checking a candidate root is how you confirm the roots you found."""

GRAPH_INSTRUCTIONS = f"""\
You design the knowledge graph of a Math-Academy-style course generated from pages of a textbook the learner owns.
The user message holds a faithful transcription of the selected pages: page markers <<page N · p.L>> (N = PDF page
index — use it in source_pages; L = printed page label; "· exercises" marks exercise pages), figure placeholders
[[figure:ID]], and a figure list with ids, captions and descriptions.

The graph drives everything: each node becomes one ~10-minute lesson (1–3 knowledge points, each a short explanation,
a worked example and checked practice) followed by spaced reviews; a node unlocks when its prerequisites are learned;
reviews of a node give implicit review credit to its prerequisites in proportion to the edge weights.

WHAT A NODE IS
- An ABILITY the learner can be asked to DO, which a small family of questions can check — not a heading or a topic
  name. "Multiply two complex numbers given as $a+bi$" is a node; "Complex numbers" is not.
{OUTCOME_RULE}
- objective: one sentence starting with a verb that names the task (Solve, Compute, Find, Construct, Prove …) and
  the objects and conditions precisely enough that two teachers would write similar questions for it. "Decide
  whether", "Determine whether", "Evaluate", "Identify", "Recognize" or "Verify" only when that judgement is itself
  the hard part (is this set with these operations a vector space? is this sum direct?); otherwise name the task it
  serves ("Solve $\\alpha z=\\beta$ for $z$", not "Evaluate a quotient"). Prove / Show / Justify only for proof skills
  (kind 'proof'); a "show that" about one given object is a computation.
- Split when the parts are separate outcomes — tasks that can be learned or forgotten independently, or cases that
  are practised separately (adding vs. multiplying; a computation vs. proving the general property; real vs.
  complex scalars). A step on the way to a task stays with the task. Merge when one family of problems assesses
  both. Stop splitting when a single problem type checks the ability.
- Include conceptual abilities, not only procedures — as tasks: predict or interpret a result, prove a result. Proof-oriented books need "prove / justify" skills (kind 'proof'); they are practised with guided
  multistep walkthroughs of the proofs.
- A confusable pair (e.g. a list vs. a set, the number 0 vs. the zero vector, $\\mathbf{{F}}^n$ vs. $\\mathbf{{F}}^\\infty$)
  is taught where it matters: a knowledge point (a decision with a near miss) of the skill that uses it. It gets its
  own 'distinction' skill only when telling the two apart is itself the hard part and the book treats it as such.
- SIZE: each skill is worth a ~10-minute lesson with 2–3 knowledge points (basic case → harder case → transfer).
  Cases that are practised with the same kind of problem are KNOWLEDGE POINTS of one skill, not separate skills:
  "prove commutativity / associativity / distributivity of complex arithmetic" is ONE proof skill; "multiply a
  vector by a real scalar" and "… by a complex scalar" are one skill. Something a learner masters in under ~5
  minutes is a knowledge point of a neighbouring skill. Aim for about one skill per page of exposition (exercise
  pages do not count) — the user message states the target range for these pages; stay inside it unless the
  material clearly demands otherwise. Every definition, result and worked example should be covered by some skill
  or listed in `excluded` with a reason.
- Exercise pages are not skills. Read them to calibrate what learners are expected to be able to do, and make sure
  the abilities the exercises demand exist as skills.
- Digressions and historical remarks: fold the motivation into a skill's summary, or exclude them with a reason.

EDGES (prerequisites)
- P is a prerequisite of S when S's explanations or problems actually USE P, so P must be learned first. Not
  "related", not "comes earlier in the book".
- List DIRECT prerequisites only; never add an edge implied by transitivity.
- weight = encompassing weight: the fraction of S's problems that fully exercise P as a component step. Use only the
  buckets 0.25, 0.5, 0.75, 1. Example: computing $(1+2i)\\cdot(3-i, 2i)$ performs complex multiplication every time
  → 1. Proving a general property only cites the definition of P → 0.25. Be conservative in proof-based material:
  citing a fact does not practise computing with it.
- Skills at the start of the pages have no prerequisites; outside knowledge goes to `foundations`.

FOUNDATIONS: knowledge assumed from OUTSIDE the selected pages (e.g. real-number algebra, set-builder notation,
ordered pairs). Keep it short (1–5 items), each with a quick self-check question and its answer.

FIELDS
- keys S1, S2, … in a sensible learning order (prerequisites first); foundation keys A1, A2, ….
- title: ≤ 60 characters, names the ability.
- summary: 1–2 sentences — what it is and why it matters at this point; keep the author's motivation.
- source_pages: the PDF indexes (from the markers) where the skill is taught, usually 1–3.
- source_refs: printed item numbers exactly as in the book (e.g. "1.3", "1.12", "Example 1.4"); never captions,
  page numbers or notes; [] if none.
- figure_ids: ids from the figure list that help teach this skill; [] if none.
- misconceptions: specific, real confusions (e.g. "computes $(a+bi)(c+di)$ as $ac+bdi$"), never generic ones.
- difficulty 1–5; est_minutes 5–20 for lesson + practice.
- big_picture: 3–5 sentences for the learner — what problem these pages solve, why it matters, where it leads.
- excluded: content deliberately left out, each with a short reason; [] if none.
{MATH_FORMAT}
"""

LESSON_INSTRUCTIONS = f"""\
You write the lesson package for ONE skill of a Math-Academy-style course generated from a textbook the learner owns.
Below this message: the transcription of the selected pages (page markers <<page N · p.L>>, figure placeholders,
figure list) and the course map. The user message names the skill, its prerequisites and dependents, and the pages
that teach it.

WHY THIS APP EXISTS: drill systems like Math Academy keep learners moving but feel like "a sea of isolated facts";
the textbook has the meaning. Keep the book's motivation and insight, delivered in Math Academy's format: short,
active, checked.

LESSON ANATOMY
- why_it_matters (2–4 sentences): the real question this skill answers or the confusion it resolves, in the
  author's spirit (e.g. "we want square roots of negative numbers — what is the least we must assume?"). Use the
  author's own motivation, margin-note insights and figures. No fake real-world fluff ("engineers use this daily"),
  no hype, no "in this lesson we will".
- idea: the core explanation (about 80–250 words) in the author's notation. Quote the key definition or result
  faithfully and cite its number ("Definition 1.1", "1.3").
- Book figures: refer only to figures in the request's figure list for this skill — place one with [[figure:ID]] on
  its own line where it helps (it is displayed there). Never describe a figure that is not in that list.
- notation: the symbols used, with meanings ([] if none).
- knowledge_points: 1–3 steps of increasing difficulty, each adding something NEW the learner must demonstrate (a
  basic case → a case that needs different thinking → a conceptual / transfer / proof step). A knowledge point that
  would only repeat the previous one with harder numbers is left out. Each has:
  - title, and an explanation of 2–6 sentences about just this step;
  - worked_example: a problem, steps (each: the math + why it is valid — label the sub-goals) and a one-sentence
    takeaway. Check every computation;
  - target_questions: {TARGET_RULE}
  - questions: target_questions + 2 questions about THIS knowledge point, tiers ramping 1 → 3, served in this order
    (see QUESTIONS).
- missing_steps: the deeper justification or omitted algebra, for learners who click "Show the missing steps".
- capstone: the lesson's final multistep problem, served after the knowledge points (see CAPSTONE below).
- common_mistakes: [] (always empty — the app no longer has such a section).
- connection: 1–2 sentences — what this enables next (name later skills from the course map) and what it builds on.
- source_refs: printed item numbers used — short references only: "1.3", "Example 1.4", "Exercise 1A.3". Never
  captions, page numbers ("p.8") or notes.

PROOF SKILLS (the request says "Kind: proof", or the objective starts with Prove / Justify)
- The learner never writes a proof as free text. Every knowledge point has a multistep walkthrough of a statement (or
  a variant) that its worked example did not prove: 2–4 machine-checked steps — which definition to apply first, the next justified line
  (computed: "what is the first coordinate of $x+y$?"), which law or hypothesis justifies a step.
- The other questions check the ingredients with different demands: which law a step uses, true_false or
  true_false_set claims about a step (the reason or a counterexample in the solution), spotting the invalid step in
  a shown argument, a computed instance.
- The capstone walks through a statement of the family that nothing else in the lesson proves, or uses a proved
  result in a computation; its proof step asks for a line the learner computes (typed), never a choice among complete
  proofs that differ in one sign.

QUESTIONS
- {NO_WRITTEN_ANSWERS_RULE}
- {DISTINCT_DEMANDS_RULE}
- Self-contained: restate every object and value needed. Never refer to "the example above", the book or a figure.
- Assess the stated objective, using only this lesson and its prerequisites.
- Make the learner produce the answer. Computations → numeric or expression (typed answers). Conceptual claims →
  true_false whose solution gives the reason or a counterexample, or true_false_set for several related claims.
  multiple_choice only when choosing among options IS the skill (which hypothesis is missing, which step is
  invalid, which object fits a definition). Choice-based questions (multiple_choice, true_false and a
  true_false_set of only 2 statements) are guessable: at most 40% of the lesson's questions, and at most one of
  each per knowledge point. Even conceptual knowledge points can usually be checked with typed answers: ask for a
  concrete instance (compute the element, give the length, the coordinate, the missing value); "explain why"
  becomes a multistep walkthrough.
- {TYPED_DEMAND_RULE} {SERVED_FIRST_RULE}
- {COUNT_QUESTION_RULE}
- {ONE_ANSWER_RULE}
- Every question is machine-checkable:
  - numeric: answer is a plain number in SymPy syntax ("-3.5", "2/3", "sqrt(2)"); tolerance (e.g. 0.001) only
    when a decimal approximation is expected, else null.
  - expression: answer in SymPy syntax — ** for powers, * for every product, i for the imaginary unit ("-7+22*i"),
    sqrt(2), pi; a list or vector as a tuple "(1, -2, 3)". The prompt must state the exact form to enter (e.g.
    "Enter your answer in the form $a+bi$" or "Enter the list as $(x_1, x_2, x_3)$"). {COMPLEX_FORM_RULE}
    {UNORDERED_RULE}
  - multiple_choice: 3–5 choices; distractors come from real misconceptions (sign error, conjugate, coordinate-wise
    product, swapped order, a hypothesis dropped), never random values; exactly one correct; correct_choice is its
    0-based index; vary its position across questions. All options have similar length, wording and level of
    detail, so none stands out: the correct option must not be the longest or the only detailed one. When the
    options are arguments, justifications or methods, every option is a complete argument and each wrong one
    contains exactly one specific flaw (a circular step inside an otherwise full argument, only $k=1$ instead of
    an arbitrary index, a single example, a wrong law cited, a dropped hypothesis or uniqueness step).
  - true_false: choices exactly ["True", "False"]; correct_choice 0 for True, 1 for False. Good for "always /
    sometimes" claims; the solution gives the reason or a counterexample.
  - {TF_SET_KIND}
  - {MULTISTEP_FORMAT}
- multiple_choice and true_false: answer "" and tolerance null. numeric and expression: choices [] and
  correct_choice null. true_false_set: answer is the T/F letters, correct_choice null. checklist: always [].
- hints: exactly 3, progressive — (1) which idea applies, (2) a partial step, (3) nearly the whole solution
  (multistep: [] — each step has its own hint).
- solution: complete and correct. answer_display: the answer formatted with LaTeX.
- targets: which part of the objective or which misconception the question checks.
- When an exercise from the exercise pages asks for exactly this kind of task, adapt it (change numbers if
  needed) and cite it in source_exercise like "Exercise 1A.7". Cite an exercise only for a direct adaptation of
  its task — a computation is not an adaptation of a proof exercise on the same topic; otherwise null.
- Before writing each answer, solve the question again from scratch and make sure answer, answer_display, the
  correct choice and the solution all agree.
- Keys: "k1q1", "k1q2", … for knowledge point 1, "k2q1", … for knowledge point 2.

{CAPSTONE_RULE}
{MATH_FORMAT}
"""

VIZ_INSTRUCTIONS = f"""\
You plan at most ONE visual for a lesson of a course generated from a textbook. First name what the learner is most
likely to misunderstand in this skill; then pick the SIMPLEST representation that makes exactly that clearer — or none.

DECISION TABLE (need → format)
- two easily-confused ideas → comparison_table (2–4 columns, ≤ 8 rows, short cells, LaTeX allowed)
- following an algebraic argument → equation_steps (2–8 display equations; wrap the part that changed in each step
  in \\color{{#d9480f}}{{...}}; one short note per step)
- a spatial relationship (vectors, points, regions in the plane) → diagram_2d, or the book's own figure
- how a quantity varies → function_plot
- a sequence or transformation → frames (2–6 ordered stills)
- a genuine parameter dependency → function_plot with ONE constrained parameter and a learner_prompt that asks
  what happens as it moves
- the definition is already clear → none

RULES
- ADD SOMETHING NEW. The lesson already shows its worked examples step by step; a visual that re-typesets a worked
  example (the same computation as equation_steps) adds nothing. Pick a representation the lesson lacks — a
  picture, a contrast table, a varying parameter, the book's figure — or none.
- Geometric content (vectors as arrows, points or regions in the plane, $\\mathbf{{R}}^2$, the complex plane,
  parallelograms, lengths and directions) gets a diagram_2d or the book's own figure.
- Prefer the book's own figure when it already shows the point: format book_figure with book_figure_id from the
  figure list. Only listed ids are valid.
- 'none' is a valid and often correct answer. Nothing decorative. Never 3D. No gratuitous interactivity — a parameter
  only when moving it answers the learner_prompt.
- Must be mathematically correct and use the lesson's notation.
- diagram_2d: small coordinate ranges (e.g. -6..6), axis_labels like ["Re", "Im"] or [] when axes carry no meaning;
  at most 8 vectors, 10 points, 10 segments; colors: primary = main objects, accent = the result, muted = helpers,
  danger = the misconception.
- function_plot: expressions in SymPy syntax in x (and the parameter name) using only + - * / ** and sin, cos, tan,
  exp, log, sqrt, Abs, pi, E; at most 3 functions; x_range and y_range show the interesting part; parameter with
  min < max, a step giving at most 21 values, initial inside the range.
- Fill only the fields of the chosen format; set the others to null or [] (book_figure_id may accompany another
  format when the book figure helps too).
- steps[].latex and frames[].latex: bare LaTeX for one display equation, WITHOUT $ or $$ delimiters (they are
  rendered as display math). Notes, captions, prompts and table cells are Markdown with $...$.
- learner_prompt: what to notice, as a question or instruction. caption: one line.
{MATH_FORMAT}
"""

VERIFY_INSTRUCTIONS = """\
You independently check an auto-generated practice question. Solve it yourself, carefully, from scratch.
- multiple_choice / true_false: choice = 0-based index of the correct option (for true/false: 0 = True, 1 = False);
  answer = "".
- numeric: answer = the value as a plain number in SymPy syntax ("-3.5", "2/3", "sqrt(2)"); choice = null.
- expression: answer in SymPy syntax in the form the question asks for — ** for powers, * for products, i for the
  imaginary unit ("-7+22*i"), tuples for lists "(1, -2, 3)", several values in any order separated by commas;
  choice = null.
- true_false_set (mark each numbered statement true or false): answer = one letter per statement in order, T or F,
  e.g. "FTT"; choice = null.
- Work inside the course's setting: the message may start with the textbook's notation and conventions. Do not
  report a defect for cases that setting excludes — e.g. when F means R or C, a claim that would fail over other
  fields (finite fields, characteristic 2) is not a defect, and an answer that relies on F being R or C is correct.
- confidence: your probability (0-1) that your answer is correct.
- problems: any defect of the question itself — ambiguous wording, missing information, no correct option, several
  correct options, an unclear answer format, or a correct option that can be recognized without solving (it is the
  only complete or much longer argument while the other options are one-line slogans); "" if there is none. A
  statement or option that is false on purpose is not a defect: true_false_set questions mix true and false
  statements, and multiple-choice distractors are wrong by design.
"""

# Second solve of a true_false_set question whose first independent solve disagreed with the key (verify.py): a
# careful statement-by-statement check against the stated definitions, so one misread statement does not flag a
# correct question.
STATEMENT_RULES = """
THIS IS A CAREFUL SECOND CHECK of a true_false_set question. Decide every statement on its own, one at a time:
- Name the definition (or result) the statement depends on and apply it exactly as stated — e.g. $\\mathbf{R}^n$ is
  the set of lists of n REAL numbers, so a list with a non-real entry is not in it; $\\mathbf{C}^n$ contains every
  list of n complex numbers (real ones included); the length of a list is part of what the list is; a list is
  ordered, a set is not.
- Recompute every number, product and coordinate the statement involves; check every part of the statement and
  every case it covers. It is true only if it holds exactly as written; otherwise it is false. Do not reinterpret
  the wording to make it true or false.
- Only after deciding each statement separately, write one letter per statement, in order.
"""
VERIFY_STATEMENTS_RECHECK = VERIFY_INSTRUCTIONS + STATEMENT_RULES

# One step of a multistep question (verify.py): the solver sees the problem, the earlier steps with their correct
# results and the current step — never its answer.
VERIFY_STEP_INSTRUCTIONS = VERIFY_INSTRUCTIONS + """
THIS IS ONE STEP of a guided multistep problem. The message gives the whole problem, the earlier steps with their
correct results (take them as given — the learner has already seen them) and the CURRENT STEP. Answer only the current
step, in the format its question type asks for. That a step builds on earlier results is intended, not a defect;
report a problem only when the current step itself is ambiguous, unsolvable from what is given, or has no / several
correct options.
"""
VERIFY_STEP_RECHECK = VERIFY_STEP_INSTRUCTIONS + """
THIS IS A CAREFUL SECOND CHECK of that step: a first solver disagreed with the stored answer. Re-read the problem and
the current step word by word, recompute every number from scratch (signs, $i^2=-1$, which quantity the step asks
for), and answer exactly what the step asks — not a later or earlier step's quantity.
"""
VERIFY_STEP_STATEMENTS_RECHECK = VERIFY_STEP_INSTRUCTIONS + STATEMENT_RULES

TOPUP_INSTRUCTIONS = f"""\
You write additional practice questions for one knowledge point of a lesson in a Math-Academy-style course generated
from a textbook. The user message gives the skill, the knowledge point (explanation and worked example), the source
passage and the questions that already exist.

- Write exactly the requested number of NEW questions about this knowledge point, at its difficulty. Each makes a
  demand different from every existing question and from the other new ones (no rewordings, and not the same
  procedure with new numbers). {DISTINCT_DEMANDS_RULE}
- {NO_WRITTEN_ANSWERS_RULE}
- Self-contained; assess the stated objective; only use this lesson and its prerequisites.
- Machine-checkable, and the learner produces the answer: computations → numeric/expression,
  conceptual claims → true_false (reason or counterexample in the solution) or true_false_set for several related
  claims, multiple_choice only when choosing is the skill; choice-based questions (multiple_choice, true_false, a
  true_false_set of only 2 statements) at most 40% — prefer typed answers. {TYPED_DEMAND_RULE} numeric: a plain number in SymPy
  syntax. expression: SymPy syntax with ** for powers, * for products, i for the imaginary unit, tuples
  "(1, -2, 3)" for lists, and the prompt states the form to enter. {COMPLEX_FORM_RULE} {UNORDERED_RULE}
  multiple_choice: 3–5 choices with
  misconception-based distractors, correct_choice = 0-based index; all options of similar length and detail (the
  correct one must not be the longest or only detailed one; argument options are all complete arguments, each
  wrong one with exactly one specific flaw). true_false: choices exactly ["True", "False"]. {TF_SET_KIND}
  {MULTISTEP_FORMAT}
- {COUNT_QUESTION_RULE}
- {ONE_ANSWER_RULE}
- Exactly 3 progressive hints (multistep: [] — one hint per step); a complete solution; answer_display in LaTeX;
  targets; checklist []; source_exercise null unless adapted from a book exercise.
- Solve each question again before writing its answer.
{MATH_FORMAT}
"""
