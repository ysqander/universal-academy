"""OpenAI prompt set — graph, v1 lesson, top-up, visual and verification instructions: the instruction texts tuned against GPT (gpt-6.1-sol / gpt-6-luna)."""

from __future__ import annotations

from ..rules import (
    CAPSTONE_RULE,
    COMPLEX_FORM_RULE,
    COUNT_QUESTION_RULE,
    DISTINCT_DEMANDS_RULE,
    EXAMPLE_POINT_RULE,
    MATH_FORMAT,
    MULTISTEP_FORMAT,
    NO_WRITTEN_ANSWERS_RULE,
    ONE_ANSWER_RULE,
    OUTCOME_RULE,
    SERVED_FIRST_RULE,
    TARGET_RULE,
    TF_SET_KIND,
    TYPED_DEMAND_RULE,
    UNORDERED_RULE,
)

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
- {EXAMPLE_POINT_RULE}
- {DISTINCT_DEMANDS_RULE}
- Self-contained: restate every object and value needed. Never refer to "the example above", the book or a figure.
- Assess the stated objective, using only this lesson and its prerequisites.
- Make the learner produce the answer. Computations → numeric or expression (typed answers). Conceptual claims →
  true_false whose solution gives the reason or a counterexample, or true_false_set for several related claims.
  multiple_choice only when choosing among options IS the skill (which hypothesis is missing, which step is
  invalid, which object fits a definition). Choice-based questions (multiple_choice, true_false and a
  true_false_set — each statement scores, so guessing pays) are guessable: at most 40% of the lesson's questions, and at most one of
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
    contains exactly one specific flaw (a circular step inside an otherwise full argument, a law used before it
    is proved, a wrong law cited, a dropped hypothesis or uniqueness step).
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
- {EXAMPLE_POINT_RULE}
- Self-contained; assess the stated objective; only use this lesson and its prerequisites.
- Machine-checkable, and the learner produces the answer: computations → numeric/expression,
  conceptual claims → true_false (reason or counterexample in the solution) or true_false_set for several related
  claims, multiple_choice only when choosing is the skill; choice-based questions (multiple_choice, true_false, a
  true_false_set — each statement scores, so guessing pays) at most 40% — prefer typed answers. {TYPED_DEMAND_RULE} numeric: a plain number in SymPy
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
