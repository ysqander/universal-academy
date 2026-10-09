"""OpenAI prompt set — the proof track's planner rules, writer track and review pass (rules shared: ..rules): the instruction texts tuned against GPT (gpt-6.1-sol / gpt-6-luna)."""

from __future__ import annotations

from ..rules import (
    CIRCULARITY_RULE,
    GUIDED_PROOF_RULE,
    PROOF_FEEDBACK,
    PROOF_PATTERNS,
    REFERENCE_PROOF,
)

PROOF_PLANNER_RULES = f"""\
PROOF SKILLS (kind 'proof') — THE PROOF PLAN
Fill `proof` (TeachingSpec.proof) for every proof skill; it is null for every other kind.

{PROOF_FEEDBACK}

{GUIDED_PROOF_RULE}

Plan the lesson so that the learner understands a SMALL proof and constructs its calculations. The general proof is
read once, as a worked solution.

{PROOF_PATTERNS}

1. pattern — read the logical form of the statements and take the FIRST that fits; strategy_name is exactly the
   canonical name.
   (a) two_halves: if and only if, exactly when, equivalent, a set equality, "the smallest … containing". Fill `halves`
       with both halves (label, assume, show, pattern — never two_halves).
   (b) suppose_two: unique, only one (uniqueness alone).
   (c) find_then_check: there exists, can be written as, construct, every … has a … (when solving the defining
       equations leaves a single solution, the same work proves uniqueness).
   (d) conditions_or_counterexample: is a subspace / closed under / a vector space, determine when, prove or give a
       counterexample.
   (e) two_routes: for all …, LEFT = RIGHT, where both sides are computed from a definition in coordinates, entries or
       values at a point (C, F^n, F^S).
   (f) law_chain: LEFT = RIGHT (or an implication) in an abstract vector space, where only axioms and earlier results
       are available.
   Statements proved with one pattern are one skill. A lesson whose pattern an earlier lesson taught recalls that
   pattern's name and picture in one line (a recalled fact, not a new idea).
2. statement — the ONE statement the teaching proves, with its book item. Choose the family member whose routes have
   the most legs (associativity of multiplication rather than commutativity), so that a route reuses its own
   intermediate result. Its general form fits in 6 letters and 4 terms per coordinate. Every other statement of the
   family goes to a worked example or the practice.
3. picture — one sentence that reads the statement in the pattern's terms ("route A multiplies α and β, then
   multiplies by λ; route B multiplies β and λ, then multiplies α by that; the law says the routes always meet").
4. may_use / may_not_use. May use: each definition the proof applies (number and formula); what equality means here
   (every coordinate, entry or value); the laws of the real numbers; earlier results by key; for law_chain the axioms
   by name. May not use: the statement itself, and each not-yet-proved result a learner would reach for, each with a
   one-line reason (S4a: "expanding complex brackets with i² = −1 — that is complex distributivity, one of the laws
   being proved; in S2a you multiplied that way, here Definition 1.1 IS the product").
{CIRCULARITY_RULE}
5. letters — one set with fixed roles for teaching, practice and the variant: the book's names for the objects and
   consecutive coordinate letters (α = a+bi, β = c+di, λ = g+hi); coordinate_letters lists every single letter a
   typed answer may contain. Never e or E (the grader reads e as Euler's number), i, I or j (the imaginary unit), a
   name starting with z or w (the grader treats it as complex), p (next to i it reads as π), or — in two_routes — the
   slot names x, y, u, v.
6. numbers — the statement on small inputs: coordinates in −3..3; the fixed input i, 1 or a short pair so that each
   route is two short products; every intermediate and both final values written out; the routes' intermediates
   differ, so a mixed-up input gives a different result (S4a: α = 1+i, β = 2+i, λ = i; route A gives 1+3i, then
   −3+i; route B gives −1+2i, then −3+i).
7. guided_case and guided_key — the statement with ONE input fixed at that simple value and letters for the rest (at
   most 4 letters), plus the key line the learner builds in the faded example and its SymPy key:
   two_routes: route B's last leg (S4a: α(βi) = (−ad−bc)+(ac−bd)i, key "-a*d-b*c+(a*c-b*d)*i");
   find_then_check: a solved coordinate; law_chain (no input is fixed): the purposeful line's law or the next line
   (guided_key ''); two_halves: the key line of the easier half; conditions_or_counterexample: the condition value at
   u+w in named values.
8. general_case — the full statement (one more pair of letters), shown as a worked table.
9. scope_statements — four claims for the scored scope check. Three "We have proved …" claims: the guided case for all
   its inputs (true); the general statement (false, not yet); the numeric case for all inputs (false). two_halves: the
   half proved (true) and both halves (false); law_chain: every vector space (true) and only F² (false);
   find_then_check: solving the equations proved that no other number works (true), solving alone proved that the
   candidate works (false — the check did), the guided case's result for every input including the one the
   hypothesis excludes (false). The fourth shows a move the proof may not make, as a move (false): "starting from the
   law and simplifying both sides would also prove it", "writing β = 1/α and simplifying is a valid first step".
10. practice_statements — 3–6 statements, each with its knowledge point and role (special_case, general, repair,
   decide, scope). Each differs from the statement, its guided case and every worked example, and is a real member of
   the family or a natural special case (one input fixed at i, 1, 1+i, 2−i, a real t or a specific vector). Never
   inflate a statement: no constants added to inputs, no swapped sides, no laws composed for no purpose, and never
   the teaching's statement with an input fixed. Every law the objective names is proved for ALL its inputs
   somewhere: the chosen one in the teaching, the rest in worked examples or practice (a general law with 6 letters
   as a worked example or a spare; its fixed-input case may be the served walkthrough).
11. practice_steps — knowledge points are the DEMANDS of the arc, not groups of laws:
   - knowledge point 1 "Transfer: prove <the next statement> with <strategy>": one input fixed, at most 4 letters; its
     worked example is a different short statement of the family; its first question (the one a retry serves) is that walkthrough —
     never a repair question (the learner's report or miss is re-tested by a scored step, the variant and a spare);
   - knowledge point 2 "What a proof establishes": example vs proof, special vs general case, circular steps; its
     worked example is the short laws;
   - a third only for a part that needs a different decision (the other direction of an iff, a refutation).
   The knowledge points are not served in the lesson: a retry and the spaced reviews ask them.
12. capstone_statement, variant_change and success_check — THE VARIANT that ends the lesson: a natural statement of
   the family that nothing else proves (not the statement, its guided case, a worked example or a practice
   statement), where the same strategy works but ONE thing changes; variant_change names that change in one line
   (another right-hand side, an extra term, the other operation, a fixed input of another kind, a hypothesis dropped).
   At most 4 letters; a computed fixed input gets a new name (μ), never one of the lesson's letters; no earlier-skill
   computation is needed. It is short — 2–3 steps — and its last step may ask what the change does. (S4a, which
   proved (αβ)λ = α(βλ): the variant (1+i)(α+β) = (1+i)α + (1+i)β — a sum where the teaching had a product; for
   "every nonzero α has exactly one β with αβ = 1": for every λ exactly one β has (1+i)β = λ — the right-hand side
   changes, and the factor is the number 1+i, so the letters stay at 4.)
13. reasoning_sequence — at most 7 bridges and 4 new ideas: the picture; the slots (identify a definition's inputs
   before applying it); letters standing for every input; scope. The bridges: (1) the statement read as the pattern,
   and what may not be used; (2) the slots, on the first leg of the numbers; (3) the learner finishes route B on the
   numbers and computes route A's first leg — both reach the same value; (4) one example is not a proof — letters stand for every input;
   (5) the guided case: the learner builds route B's last leg, route A is shown, both coordinates are compared;
   (6) scope: exactly what has been proved; (7) the general case as a worked table, and the method in one line.
   Other patterns replace bridge 2's scaffold with their own and bridge 5's leg with their key line.
14. representations — the slot table ("helps the learner notice which four numbers enter each product, so the two
   routes' inputs are not mixed"; one row per product, x and y from the first factor, u and v from the second) and
   the comparison table (one row per coordinate: route A | route B | the real law). Both are Markdown tables.
15. misconceptions — at most 3, each tested by a scored step or the variant: a Learner's miss first; a learner's written report on
   the skill becomes "Learner's report: …" (S4a: "mixes the two routes' inputs when filling a product's slots"); "an
   example or a special case taken as a proof of the general law"; "uses the law being proved (expands complex
   brackets with i² = −1)"; for other patterns "assumes what the direction must show", "a counterexample that does
   not meet the hypotheses", "compares only one coordinate".
16. difficulty 3–4; est_minutes 12–15. out_of_scope: later skills' statements; a second proof of the same
   statement; shortcuts that replace the definition (multiplying by i as (x, y) → (−y, x) may go to 'deeper')."""


PROOF_TRACK = f"""\
THE PROOF TRACK (this lesson is a proof skill). Where it differs from the general rules above — the number of blocks
and checks, which checks are scored, the faded example, the served sequence, the multistep format, the capstone (here:
the variant), the summary — the proof track wins.

{PROOF_FEEDBACK}

{GUIDED_PROOF_RULE}

{PROOF_PATTERNS}

THE SPEC'S PROOF PLAN IS BINDING: pattern, strategy name, statement, picture, may use / may not use, letters, numbers,
guided case, general case, scope statements, practice statements, and the variant (capstone statement and variant
change). Use them as given; never invent other statements or letters.

RULES
1. ONE PICTURE, NAMED FIRST. The setup is headed by the canonical strategy name and says what the strategy means for
   this statement before any letter appears ("Route A multiplies α and β, then multiplies by λ; route B multiplies β
   and λ, then multiplies α by that. The law says the routes always end at the same number — we may not assume it: it
   is what we prove."). Then two short lines: "We may use …" (each definition displayed with its number — in the slot
   letters, (x+yi)(u+vi) = (xu−yv)+(xv+yu)i, because a, b, c, d are the coordinates of α and β from block 5 on; equality
   = every coordinate equal; the real-number laws) and "We may not use …" (the statement itself and every law not yet
   proved, each with a one-line reason, e.g. "expanding complex brackets with i² = −1 uses complex distributivity — in
   S2a you multiplied that way; here Definition 1.1 IS the product").
{CIRCULARITY_RULE}
   The setup is the ONLY place this warning appears: no later prompt, step or option repeats "without assuming …".
   Every later block says which route or which comparison it is doing.
2. ONE STATEMENT, THREE PASSES. The teaching proves ONE statement: (a) on small numbers; (b) with letters and one
   input fixed at a simple value — the guided case, at most 4 letters — where the learner builds one route; (c) the
   general case, read as a worked table. The faded example IS the guided case: it continues the running statement.
3. FILL THE SLOTS BEFORE APPLYING A DEFINITION. Before a definition is applied, write its template with named slots,
   fill one row of a small Markdown table, then compute. For a complex product: "(x+yi)(u+vi) = (xu−yv) + (xv+yu)i —
   Definition 1.1 with its four inputs named"; columns product | x | y | u | v | real xu−yv | imaginary xv+yu; one
   row per product; "reset the slots" between products; write i as 0+1i when it fills slots. In the teaching every
   product gets a filled row up to the faded example. The faded example's row fills only x and y (the first factor)
   and shows u, v and both results as "?": reading u and v off route B's OWN intermediate is exactly where a learner
   mixes the routes, so the learner does it. Practice
   prompts may say "reset the slots" but show no slot values (hints may). The variant shows no slot rows; a step may
   ask for a product's four slots as its answer. Other settings: in F^n the slot is entry k; for functions, the value
   at one point s; for an unknown, its coordinates.
4. LETTERS: ONE SET, FEW. Use the plan's letters with the same roles in blocks, worked examples, questions and the
   variant; never rename, never introduce other letters (a collision with a printed line is repaired with another
   statement or fixed input, not with new letters). Never e, E, i, I, j, a name starting with z or w, p, or the slot
   names as coordinates. The notation list declares the letter set and the slot names. Before the generalize block no
   display and no check uses more than 4 letters; no typed answer has more than 6.
5. ONE ROUTE AT A TIME. Compute one route completely, say "reset the slots", compute the other, then compare in a
   table: coordinate | route A | route B | the real law that makes them equal. Never interleave the routes in a
   display or a step; never ask for one coordinate of route A and the other of route B.
6. ALGEBRA IS NOT THE CONTENT. Small inputs: coordinates in −3..3, results within −9..9. At most 4 terms per coordinate
   in anything the learner types or the teaching expands before the generalize block. Never "fully expand": a route
   result is typed as a complex number in standard form whose coordinates may stay unexpanded, and the prompt says so
   ("Enter it as a complex number in standard form (e.g. $3-2i$); the coordinates may stay unexpanded." — the app
   refuses a product typed back, such as (a+bi)(c+di), as not yet computed). Never inflate a statement: no α+8 or
   β+11i, no swapped sides, no laws composed for no purpose.
7. THE LEARNER CONSTRUCTS THE PROOF — THE SCORED STEPS ARE ITS MOVES, NEVER A TIDY-UP. The learner computes the
   last leg of one route twice: on the numbers and in letters (the faded example) — both scored — plus the other
   scored steps of the sequence below. A scored step's answer is printed nowhere before the learner answers it: a
   route, table or line that holds it comes in a LATER block (or in the step's explanation). A scored step is never a
   regrouping by a real law, a sign tidy-up, arithmetic printed in the block, or a value the statement forces.
8. MEANING IS TAUGHT AND TESTED. One reflect check (never scored) asks why agreement on numbers is not a proof, before
   any text says it. The scored scope check (a true_false_set of the plan's four scope claims: three "We have proved …"
   claims and a move the proof may not make) follows the guided case. It tests both "an example or special case proves
   the law" and "the law being proved may be used"; other misconceptions are tested by a scored step or a variant step.

THE TEACHING SEQUENCE — 10 blocks, about 350 words of prose, 5 scored steps (S) and one reflect. New ideas: the
picture, the slots, letters for every input, scope.
1. setup — the picture: the statement with its number, the routes in words, the definitions displayed, may use / may
   not use. new_idea: the picture.
2. demonstrate — "First, fill the slots": the slot template in one sentence; the numbers; route B's first leg in a
   filled slot row (the one row the learner reads worked). new_idea: the slots.
3. check (S; numeric or expression with a constant key) — the learner finishes route B on the numbers: "Reset the
   slots (x, y from α; u, v from βλ) and enter α(βλ) as a complex number in standard form." Its answer is printed
   nowhere before.
4. check (S; constant key) — "Route A, calculated separately": its first product as a slot row with both results '?';
   the learner enters route A's first leg (αβ) in standard form. The explanation finishes route A and shows it
   reaching the same number as route B.
5. check (reflect) — "Both routes reached −3+i. Why does this not yet prove the law?" Model answer: one choice of
   inputs; the law claims every choice, so we need inputs that stand for every number at once.
6. demonstrate — "Letters stand for every input": keep the simple input (λ = i); give the other inputs the plan's
   letters; route B's first leg in a slot row. new_idea: letters for every input.
7. faded_example (S) — route B's last leg in letters: the row x = a, y = b, u = ?, v = ?, results ?. The check
   (expression) asks for that leg's result in standard form (the plan's guided_key). A slot mix-up gives a different
   value.
8. demonstrate — route A in slot rows, then the comparison table, then the conclusion: "for every a, b, c, d — so
   (αβ)i = α(βi) for all α, β".
9. check (S; true_false_set, the plan's 4 scope statements) — "What has been proved so far?" new_idea: scope.
10. generalize (S) — the general case as ONE two-column worked table (not a slot table with a row per product):
   columns route A | route B; rows inner result, slots of the outer product, real coordinate expanded, imaginary
   coordinate expanded (equal terms in the same order on both routes). ONE cell is '?' and the block's check asks for
   it — a short entry the rows above it do not give away (the slots of route B's outer product, or route A's inner
   result); the rows below it go in the check's explanation. Then one sentence naming the real laws once; "the
   letters were arbitrary, so … for all …"; the method in one line. There is NO separate summary block.
An 11th block only for a planned Learner's miss. 'deeper' may hold a shortcut such as "multiplying by i sends (x, y)
to (−y, x)", never the main path.
OTHER PATTERNS keep the ten slots — concrete case (scored) → why that is not a proof → the guided case where the
learner builds the key line (scored) → scope (scored) → the general case read as a worked solution with one scored
entry — and change what a route and the guided case are:
- find_then_check: block 2 turns the condition on one instance into its two real equations (shown, in the unknown |
  condition | real equations | solved table, "solved" still '?'); block 3 (S) the learner eliminates one unknown and
  enters it; block 4 (S) the learner enters the only candidate (or the other coordinate) — the check that it works
  is shown in block 4's explanation, never scored (its result, 1 or 0, is forced); the instance is one whose result no
  earlier lesson printed. Guided case = the same equations in letters with one coordinate fixed, the learner enters a
  solved coordinate (S); scope = solving proved "only this can work" (uniqueness), the check proved "it works"
  (existence), and which hypothesis was used (α ≠ 0); the general table's '?' cell is a solved coordinate or what one
  elimination gives.
- suppose_two: name both candidates with their defining equations; evaluate one expression two ways; the guided case
  leaves one line or its reason for the learner (a choice among single equations or short law names).
- law_chain: the concrete case is the same chain on a vector of F² with numbers; the reflect asks why that does not
  prove it in every vector space; motivate the purposeful move before making it; the proof is a line | the one law
  table; the guided case leaves the key line's law or the next line (a choice); no typed vector lines in an abstract
  space (the grader's algebra turns 0v into 0).
- two_halves: an assume | show table with one row per direction; the guided case is the easier half; scope asks which
  half is proved and which remains; the general case is the other half.
- conditions_or_counterexample: a membership card have | need | link; the numbers test a concrete pair (a failing pair
  is the counterexample, pinned so that exactly one answer is right); the guided case is closure under addition, with
  the condition value typed in named values (named with the plan's letters).

PRACTICE — not served in the lesson: a retry of the lesson and the spaced reviews ask these questions, so they must
stand on their own. Titles from the spec's practice steps; every statement from the plan's practice_statements; the
lesson's letters everywhere.
- Knowledge point 1 "Transfer" (target_questions 1): explanation = the method as 3–4 numbered steps usable on a new
  statement (name the routes; compute each from the definition, filling the slots; compare every coordinate, naming
  the real law; the letters were arbitrary, so it holds for all inputs). worked_example = a DIFFERENT short statement
  of the family, proved in full (S4a: αβ = βα, Example 1.4) — not the walkthrough's statement with another fixed
  input. First question (the one a retry serves): a walkthrough of the next statement with one input fixed
  at a simple value (at most 4 letters). Never a slot-error or repair question first: re-testing the learner's
  report or miss is the job of a scored step, the variant and a spare. Spares: a recovery walkthrough of another practice statement
  with the same demand; a single-answer typed question (spot the slot error in a shown route leg, the error not named
  → the corrected value).
- Knowledge point 2 "What a proof establishes" (target_questions 1 — the transfer proofs belong to knowledge point 1
  and the variant, not here): worked_example = the short laws, with a scope
  sentence and the contrast with a numeric check (S4a: (α+β)+λ = α+(β+λ); λ·1 = λ with slots g, h, 1, 0). First
  question: a true_false_set of 4–5 short claims, both true and false, each about a shown argument — a numeric check
  offered as a proof; letters for every coordinate with both coordinates compared; a fixed input generalized; a
  circular step (the law itself, or expanding complex brackets with i² = −1); a real-number law used inside a
  coordinate — at least two true and two false. A circular claim SHOWS the move so that the learner sees it is
  circular ("in proving i(α+β) = iα+iβ, expanding i((a+c)+(b+d)i) term by term and replacing i² by −1 is a valid first
  step" — false), never one that announces it ("a proof may begin by invoking that same law"), which refutes itself. Spares: a second true_false_set with NEW claims (never a claim of the
  first one again); a decide question (a proposed identity that fails: both sides typed,
  then true_false).
- A third knowledge point only when the spec plans it.
- Every law the objective names is proved for ALL its inputs somewhere — the teaching, a worked example, or a spare
  walkthrough (6 letters are allowed in a spare). A fixed-input case (the multiplier fixed at i) does not count: S4a
  must prove λ(α+β) = λα+λβ with λ = g+hi somewhere, not only i(α+β) = iα+iβ.
- Never in practice: the teaching's statement or a special case of it (after (αβ)λ = α(βλ) is proved, (αβ)(1+i) =
  α(β(1+i)) is a special case, not a transfer); a worked example's statement; an inflated
  statement; new letters; "fully expand"; a "which continuation" choice among method descriptions; a bank of lines to
  order.
SERVED SEQUENCE (the lesson's score): the scored steps of the teaching sequence → the variant (weight 2). The knowledge
points come in a retry (knowledge point 1's walkthrough → knowledge point 2's true_false_set) and in reviews, so both
keep target_questions 1.

WALKTHROUGH STEPS (multistep, 3–4 steps; at least 2 typed; at most 1 multiple_choice)
- Prompt: "Strategy: <name>." + the statement + the letters ("for all α = a+bi, β = c+di with a, b, c, d real") + the
  routes in one sentence. A prompt that asks for slots states the convention in one line ("the four slots of
  (x+yi)(u+vi) are x, y, u, v; real part xu−yv, imaginary part xv+yu"). Self-contained, no "without assuming …".
- Score only what the statement does not force: route A's result, route B's first leg, and the inputs of route B's last
  product are unknown until computed. Route B's final result equals route A's by the theorem: NEVER ask for it (nor
  for one of its coordinates). When route B ends in a product, ask for that product's four slots: "Compute αμ, then
  enter the four slots (x, y, u, v) of (αμ)β, in this order" (at least one entry computed; a learner who mixes the
  routes types a different tuple). When route B ends in a sum, ask for its first leg and show the rest in the
  explanation.
- Score the comparison at most once: either show ONE pair of coordinates from the two routes and ask which real-number
  fact makes them equal (the stem shows only the two coordinates — never the law's own instance such as
  (−1)(b+d) = (−1)b+(−1)d, which answers it; multiple_choice, 4 short law names; distractors are laws a learner wrongly reaches for,
  never a forbidden law), or a true_false_set step of 3 short claims about what this proof shows. At most one "which
  real law" choice in the whole lesson.
- Typed complex results: "as a complex number in standard form (e.g. $3-2i$); the coordinates may stay unexpanded";
  SymPy keys with * and i ("a+c-b-d+(a+b+c+d)*i"). A slot tuple is keyed "(1, 1, a+c, b+d)".
- One hint per step: the slots of that step's product, never the result. The explanation gives the slot row and the
  result. An explanation never answers a later step: the comparison table goes in the LAST step's explanation (and the
  solution), never before a step that asks about the comparison, so the learner compares on paper first.
- Other patterns: find_then_check — the condition's real equation, then a solved coordinate (both typed), then a
  true_false_set on existence / uniqueness / hypothesis. suppose_two and law_chain (abstract) — the next line
  (multiple_choice among single equations; each distractor makes one unlicensed move), the law it uses (short names),
  a true_false_set of 3 line justifications, and a typed value in a concrete F^n instance when one exists.
  two_halves — assume/show for one half (true_false_set), a concrete witness (typed), what remains (choice).
  conditions_or_counterexample — the condition value at the new vector in named values (typed), a pinned witness
  (typed tuple), which conditions hold (true_false_set). Witnesses and counterexamples are pinned ("the vector of U∩W
  whose second coordinate is 1") so that exactly one answer is right.
- multiple_choice options: at most 90 characters, similar length, no display math, no computed line, never a method
  description.

THE VARIANT (the `capstone` field of a proof skill; key 'cap') — the plan's variant statement with the scaffolds
removed (no slot table and no "reset the slots" in its prompts); at most 4 letters; a computed fixed input gets a NEW
name (μ), never one of the lesson's letters (λ is g+hi everywhere). The prompt states the statement and names the
strategy ("Strategy: <name>."); it never re-explains the method. 2–3 steps — the same strategy's moves on the changed
statement:
- two_routes: route A's result (typed); the slots of route B's last product, or its first leg (typed); optionally a
  true_false_set of 3 short claims about what the change does to the proof.
- find_then_check: the only candidate found from the real equations (typed); then a true_false_set of 3 claims about
  what the change does — which stage proved what, which hypothesis is now needed or no longer needed, and why (a
  second typed step only when the change adds a real move).
- other patterns: their walkthrough moves (below), at most 3.
Never a forced value, a choice among proofs, a long expansion, the teaching's statement again with other numbers, a
step that needs an earlier skill's computation, or a last step that only subtracts printed numbers.

EXCEPTIONS (proof skills only, on purpose): 10 blocks with 5 scored steps; the faded example continues the running
statement; the generalize block holds a scored '?' cell; the block after a scored step may show its answer (the app
sends the next block only once the step is answered); the setup is the only place for the circularity warning;
knowledge point 2 leads with a true_false_set; the knowledge points are not served in the lesson; a slot tuple
(x, y, u, v) is a valid typed answer (it lists a product's inputs; it is not a complex number written as a pair); the
generalize block ends with the method and replaces the summary.

{REFERENCE_PROOF}"""


PROOF_REVIEW_PASS = """\
PROOF PASS (proof skills) — report into `issues` and `density_issues` in the usual form (location → what the learner
must handle → why it hurts → the smallest repair).
Read the draft a third time as the learner who called the first S4a "quite convoluted" and "got lost interchanging the
symbols" between the two calculations, and check it against the spec's PROOF PLAN. Solve every check and step as that
learner would. At every product, ask whether its four inputs can be named from what is on screen and whether they
belong to this route. Count the letters and terms the learner must hold in mind. When an issue repeats one of the
learner's complaints, cite it: 14 (trivial learner part), 15 (long expansions), 16 (symbol overload).
BLOCKING
1. Order: the first calculation uses letters (no numbers pass); the general case comes before the guided case; no
   reflect check between them asking why agreement on numbers is not a proof.
2. Picture: block 1 does not name the plan's strategy and its routes (halves, stages, chain) or does not say what may
   and may not be used; a knowledge point or walkthrough uses another method or another name.
3. Slots, up to the faded example: a definition applied without its inputs identified in a slot row; a route's next
   product without "reset the slots"; route B reusing route A's intermediate.
4. The scored steps (every graded inline check): fewer than 4 or more than 6; one that is a regrouping, a sign tidy-up,
   arithmetic shown in its block, or a value the statement forces, instead of a real move of the proof (two_routes:
   route B's last leg on the numbers and in letters); an answer printed before the learner answers it (in its own
   block or prompt, an earlier block, an earlier check's prompt, explanation or answer, or an earlier skill's result
   recalled in the setup); no hint, or a hint that gives the result; a true_false check; a multiple_choice with fewer
   than 4 options; a generalize block without its scored '?' cell, or one the rows above it give away.
5. Routes: interleaved in one display or step; a step that asks for one coordinate of each route; a comparison of only
   one coordinate.
6. Letters and size: an object renamed anywhere (new letters in a question); e, i or j used as a real letter, a letter
   starting with z or w, a slot name used as a coordinate; more than 4 letters in a teaching computation before the
   generalize block, in the guided case, in knowledge point 1's first question or in the variant; more than 6 letters
   in any typed answer; more than 4 terms per coordinate in a walkthrough, a scored step or the variant (more
   than 6 in a spare); a general case longer than one table and two sentences.
7. Statements: a served walkthrough of the teaching's statement, a special case of it, or a worked example's statement;
   two served items on the same statement; a statement inflated with constants (α+8, β+11i), swapped sides or laws
   composed for no purpose; a statement of the objective's family proved nowhere.
8. Walkthroughs: fewer than 2 typed steps (abstract law_chain / suppose_two: no true_false_set of at least 3 line
   justifications either); a scored step whose key the statement forces (the second route's final result or one of its
   coordinates); a typed result whose prompt does not state the form; a typed vector line in an abstract derivation; a
   "which continuation" choice among method descriptions, a bank of lines, an option much longer than the others, or a
   distractor the prompt itself forbids; a step that asks for two things; a witness or counterexample step with more
   than one correct answer.
9. Meaning: no scope check in the teaching; "an example or special case proves the law" or "the law being proved may
   be used" not tested by a scored step or a variant step; a mixing-the-routes misconception not tested by a scored
   typed step whose key differs from the mixed answer.
10. Variant (the capstone): not the plan's variant statement; the teaching's statement again with other numbers;
   nothing changes; more than 3 steps; a step that needs an earlier skill's computation; a forced step; a choice among
   proofs; a replay of a knowledge point's walkthrough.
11. Recompute every slot row, route result, comparison, true/false letter, scored-step key and variant value; errors go
   to math_errors.
MINOR: a warning ("without assuming …") repeated after the setup; more than one "which real law" choice in the lesson;
variant prompts that still show slot rows or "reset the slots"; the comparison asserted without naming the real law
once, or a law named in every line; the general table not aligned (equal terms in different orders without a word
about it); a knowledge point with no single-answer question (quizzes never serve multistep questions); a reflect
check that asks for a value or whose model answer repeats the text just shown; a method line that names this
statement's letters instead of steps usable on a new statement.
DENSITY (second pass): the new ideas of a proof lesson are the picture, the slots, letters for every input, and scope.
A fifth idea is surplus: move it to 'deeper' or cut it (a helper shortcut such as "multiplying by i sends (x, y) to
(−y, x)", a second strategy, a second statement in the teaching, named inner results). A teaching sequence that spends
more blocks on expansions than on the routes and their comparison is blocking.
DO NOT REPORT (on purpose in the proof track): route B's final value or its coordinate comparison shown in an
explanation instead of scored — the statement forces it, so it is never typed (the learner's decision), and the
comparison is scored at most once, by the walkthrough's "which real law" choice or true_false_set step; a variant
without a comparison step or without an earlier skill (its 2–3 steps are the strategy's moves on the changed
statement); the knowledge points not being served in the lesson (a retry and the reviews ask them); a block that shows
the answer of the scored step just before it (the app sends it only once that step is answered); 10 blocks; a faded
example that continues the running statement; a faded check
whose answer equals route A's result when route A is shown only after it; a general case given only as a table; a
generalize block that ends with the method instead of a summary block; knowledge point 2 leading with a
true_false_set; a slot tuple (x, y, u, v) as a typed answer; real-number laws used freely inside coordinates; slot
names as notation; the circularity warning stated once in the setup."""


PROOF_QUESTION_REPAIR = (
    "the lesson's letters (never new ones) and a statement from the plan's practice_statements that no other served "
    "item and no worked example proves; never the second route's final value — ask for a leg the statement does not "
    "force (route A's result, route B's first leg, the slots of route B's last product); self-contained, only methods "
    "the lesson taught; one hint per step (the slots, never the result); a complete solution that agrees with the key."
)


PROOF_CAPSTONE_REPAIR = (
    "- in a proof skill the capstone is THE VARIANT: the plan's variant statement (the same strategy, one thing "
    "changed) in 2–3 steps — the strategy's moves on the changed statement (two_routes: route A's result, typed; the "
    "slots of route B's last product or its first leg, typed; find_then_check: one solved unknown, typed; the found "
    "object, typed), optionally a last true_false_set of 3 claims about what the change does. No slot rows or "
    "\"reset the slots\" in its prompts, the lesson's letters (at most 4), no earlier-skill computation, never the "
    "teaching's statement again, a forced value or a choice among proofs."
)
