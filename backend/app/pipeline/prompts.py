"""Instruction texts for the build pipeline's LLM calls (SPEC §4.2, §4.3)."""

from __future__ import annotations

MATH_FORMAT = (
    "Formatting: Markdown. Math only as $...$ (inline) or $$...$$ (display) — never \\( \\), \\[ \\] or bare LaTeX. "
    "Keep the author's notation (e.g. $\\mathbf{F}^n$, $\\mathbf{C}$, 'list', 'length')."
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
    "root of $3+4i$\"); for a single decision, true_false."
)
UNORDERED_RULE = (
    'Several values whose order does not matter (both square roots, all solutions) are a set in braces, e.g. answer '
    '"{1+i, -1-i}", and the prompt says "Enter both roots separated by a comma, in any order"; a tuple "( … )" is '
    "only for ordered lists and vectors, where the order is part of the answer."
)
TF_SET_KIND = (
    'true_false_set: "Mark each statement true or false" — choices holds the 2–5 statements (each self-contained, '
    'without its own number); answer = one letter per statement in order, T or F (e.g. "FTT"); correct_choice null; '
    "tolerance null. Mix true and false statements; the hints and the solution address every statement by its "
    "number (why it is true, or a counterexample). With 3 or more statements it is not a guessable choice question."
)

GRAPH_INSTRUCTIONS = f"""\
You design the knowledge graph of a Math-Academy-style course generated from pages of a textbook the learner owns.
The user message holds a faithful transcription of the selected pages: page markers <<page N · p.L>> (N = PDF page
index — use it in source_pages; L = printed page label; "· exercises" marks exercise pages), figure placeholders
[[figure:ID]], and a figure list with ids, captions and descriptions.

The graph drives everything: each node becomes one ~10-minute lesson (2–3 knowledge points, each a short explanation,
a worked example and checked practice) followed by spaced reviews; a node unlocks when its prerequisites are learned;
reviews of a node give implicit review credit to its prerequisites in proportion to the edge weights.

WHAT A NODE IS
- An ABILITY the learner can be asked to DO, which a small family of questions can check — not a heading or a topic
  name. "Multiply two complex numbers given as $a+bi$" is a node; "Complex numbers" is not.
- objective: one sentence starting with a verb (Compute, Decide, Distinguish, Explain, Justify, Prove, Interpret,
  Translate, Recognize, Apply …) that names the objects and conditions precisely enough that two teachers would
  write similar questions for it.
- Split when parts can be learned or forgotten independently, or when cases are practised separately (adding vs.
  multiplying; a computation vs. proving the general property; real vs. complex scalars). Merge when one family of
  questions assesses both. Stop splitting when a single problem type checks the ability.
- Include conceptual abilities, not only procedures: distinguish confusable notions, explain why a definition is
  set up the way it is, predict or interpret, justify a step, prove a result. Proof-oriented books need "prove /
  justify" skills (kind 'proof'); they are practised with short free-response proofs and checklists.
- A confusable pair (e.g. a list vs. a set, the number 0 vs. the zero vector, $\\mathbf{{F}}^n$ vs. $\\mathbf{{F}}^\\infty$)
  deserves its own 'distinction' skill.
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
- knowledge_points: 2–3 steps of increasing difficulty (basic case → general case → conceptual / transfer / proof
  step). Each has:
  - title, and an explanation of 2–6 sentences about just this step;
  - worked_example: a problem, steps (each: the math + why it is valid — label the sub-goals) and a one-sentence
    takeaway. Check every computation;
  - questions: exactly 4 questions of THIS knowledge point's problem type with different numbers or objects,
    tiers ramping 1 → 2 → 2 → 3. They are served in this order and the learner usually passes after 2 or 3, so
    the first two must already assess the knowledge point.
- missing_steps: the deeper justification or omitted algebra, for learners who click "Show the missing steps".
- common_mistakes: concrete errors (wrong sign, conjugate instead of inverse, …).
- connection: 1–2 sentences — what this enables next (name later skills from the course map) and what it builds on.
- source_refs: printed item numbers used — short references only: "1.3", "Example 1.4", "Exercise 1A.3". Never
  captions, page numbers ("p.8") or notes.

PROOF SKILLS (the request says "Kind: proof", or the objective starts with Prove / Justify / Show)
- Every knowledge point has a free_response question in which the learner writes the short proof or justification
  of that knowledge point's statement: make it q2 (tier 2), with a model proof as the solution and a checklist of
  2–5 concrete criteria (e.g. "takes arbitrary $x, y$", "applies the definition of addition coordinatewise", "cites
  commutativity in $\\mathbf{{F}}$").
- Wherever the knowledge point allows it, make q4 a SECOND free_response proof — a variant or a closely related
  statement of the same knowledge point (not a rewording of q2), with its own model proof and checklist. It is
  served when the first written proof needed a hint or the solution, or was self-graded partial/incorrect, so the
  learner gets an independent second attempt.
- The other questions check the ingredients machine-checkably: which law or hypothesis a step uses, true_false
  claims about a step (with the reason or a counterexample in the solution), a computed instance.

QUESTIONS
- Self-contained: restate every object and value needed. Never refer to "the example above", the book or a figure.
- Assess the stated objective, using only this lesson and its prerequisites.
- Make the learner produce the answer. Computations → numeric or expression (typed answers). Conceptual claims →
  true_false whose solution gives the reason or a counterexample, or true_false_set for several related claims.
  multiple_choice only when choosing among options IS the skill (which hypothesis is missing, which step is
  invalid, which object fits a definition). Choice-based questions (multiple_choice, true_false and a
  true_false_set of only 2 statements) are guessable: at most 40% of the lesson's questions, and at most one of
  each per knowledge point. Even conceptual knowledge points can usually be checked with typed answers: ask for a
  concrete instance (compute the element, give the length, the coordinate, the missing value), and use
  free_response for "explain why".
- {COUNT_QUESTION_RULE}
- {ONE_ANSWER_RULE}
- Machine-checkable whenever possible — at least 3 of the 4 questions of a knowledge point (2 of 4 for proof
  skills):
  - numeric: answer is a plain number in SymPy syntax ("-3.5", "2/3", "sqrt(2)"); tolerance (e.g. 0.001) only
    when a decimal approximation is expected, else null.
  - expression: answer in SymPy syntax — ** for powers, * for every product, i for the imaginary unit ("-7+22*i"),
    sqrt(2), pi; a list or vector as a tuple "(1, -2, 3)". The prompt must state the exact form to enter (e.g.
    "Enter your answer in the form $a+bi$" or "Enter the list as $(x_1, x_2, x_3)$"). {UNORDERED_RULE}
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
  - free_response: for prove / explain / justify objectives only; answer ""; checklist of 2–5 concrete criteria a
    correct answer must meet; solution is a model answer (the learner compares and self-grades).
- multiple_choice, true_false and free_response: answer "" and tolerance null. numeric and expression: choices []
  and correct_choice null. true_false_set: answer is the T/F letters, correct_choice null.
- hints: exactly 3, progressive — (1) which idea applies, (2) a partial step, (3) nearly the whole solution.
- solution: complete and correct. answer_display: the answer formatted with LaTeX.
- targets: which part of the objective or which misconception the question checks.
- When an exercise from the exercise pages asks for exactly this kind of task, adapt it (change numbers if
  needed) and cite it in source_exercise like "Exercise 1A.7". Cite an exercise only for a direct adaptation of
  its task — a computation is not an adaptation of a proof exercise on the same topic; otherwise null.
- Before writing each answer, solve the question again from scratch and make sure answer, answer_display, the
  correct choice and the solution all agree.
- Keys: "k1q1" … "k1q4", "k2q1" …
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
- confidence: your probability (0-1) that your answer is correct.
- problems: any defect of the question itself — ambiguous wording, missing information, no correct option, several
  correct options, an unclear answer format, or a correct option that can be recognized without solving (it is the
  only complete or much longer argument while the other options are one-line slogans); "" if there is none.
"""

TOPUP_INSTRUCTIONS = f"""\
You write additional practice questions for one knowledge point of a lesson in a Math-Academy-style course generated
from a textbook. The user message gives the skill, the knowledge point (explanation and worked example), the source
passage and the questions that already exist.

- Write exactly the requested number of NEW questions of the same problem type and difficulty as the knowledge
  point, with different numbers or objects than every existing question (no rewordings).
- Self-contained; assess the stated objective; only use this lesson and its prerequisites.
- Machine-checkable whenever possible; make the learner produce the answer: computations → numeric/expression,
  conceptual claims → true_false (reason or counterexample in the solution) or true_false_set for several related
  claims, multiple_choice only when choosing is the skill; choice-based questions (multiple_choice, true_false, a
  true_false_set of only 2 statements) at most 40% — prefer typed answers. numeric: a plain number in SymPy
  syntax. expression: SymPy syntax with ** for powers, * for products, i for the imaginary unit, tuples
  "(1, -2, 3)" for lists, and the prompt states the form to enter. {UNORDERED_RULE} multiple_choice: 3–5 choices with
  misconception-based distractors, correct_choice = 0-based index; all options of similar length and detail (the
  correct one must not be the longest or only detailed one; argument options are all complete arguments, each
  wrong one with exactly one specific flaw). true_false: choices exactly ["True", "False"]. {TF_SET_KIND}
  free_response (prove / explain only): answer "" and a 2–5 item checklist.
- {COUNT_QUESTION_RULE}
- {ONE_ANSWER_RULE}
- Exactly 3 progressive hints; a complete solution; answer_display in LaTeX; targets; source_exercise null unless
  adapted from a book exercise.
- Solve each question again before writing its answer.
{MATH_FORMAT}
"""
