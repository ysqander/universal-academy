# Lesson generation v2 — design (experimental)

Source: the learner's review of the first iteration (lesson S2 "Multiply complex numbers and use their powers" was
dense, mixed several skills, and put needed reasoning in optional sections). Principle at the top of every prompt:

> **Reduce the number of new ideas the learner must handle at once; do not reduce the reasoning that connects them.**
> A summary tells you what someone who understands the subject should remember. A lesson helps you become that person.

## What went wrong in v1

1. **One lesson, several skills.** Multiplication, powers, checking roots and finding square roots were taught together.
   Connected ≠ teach together; mastery scores become uninformative.
2. **Formula before a concrete case.** `(a+bi)(c+di) = (ac−bd)+(ad+bc)i` appeared before any worked numerical product
   showing where the four products come from and why one becomes negative. Describing a procedure ≠ demonstrating it.
3. **Representations before their question.** A table of "four candidates with a²−b²=3" appeared before the problem and the
   role of the candidates were established.
4. **"Show the missing steps" held essential reasoning.** Rule: if an explanation is necessary for the objective, it
   belongs in the main path (or an explicit prerequisite). Optional depth = alternative explanation / deeper
   derivation / reminder only.
5. Fixed headings ("Why this matters", "The idea", "See it") do not by themselves produce a teaching sequence.

## Pipeline (per skill)

1. **Planner** (`TeachingPlan`) — teaching spec per node: objective, assumed prerequisites, out of scope, starting
   example/question, reasoning sequence (bridges), success check, representation choice ("helps the learner notice
   ___", how to read it), practice steps. Skills are outcomes: the objective names a task the learner can do at the end
   (solve, find, construct, prove — a proof is an outcome); evaluating or checking is a step inside it. Split test:
   *does this node hold separate outcomes — tasks someone could do one of and not the other, each worth doing alone?*
   → may return `split` with 2–5 narrower skills (each a meaningful task — "Multiply two complex numbers", not
   "multiply the first two terms") and, for every dependent of the original node, which new skills it needs. The
   planner also sees learner evidence on the skill: "This step isn't clear" notes and the questions the learner missed
   or needed help with (their answer and the correct one).
2. **Writer** (`LessonV2Draft`) — an ordered teaching sequence of blocks, each with a job (setup / explain /
   demonstrate / check / faded example / generalize / summary), not filled-in headings. Rules: keep one running
   example; explain consequential transitions where they happen ("because i² = −1" next to the substitution);
   introduce notation before relying on it (say a, b, c, d are real; don't import the source's α, β, r, s, w, z
   without need); make the learner think before the final exercises (1–3 inline checks; complete example → partially
   completed example → independent problem); include one explanation question ("why did the bottom-right entry
   become real?"). Structure depends on the kind of skill — do not hard-code example-before-formula:
   - procedure / computation: concrete case → steps → generalize to the formula ("the formula is a record of the steps")
     → faded example → independent practice;
   - definition: the question the definition answers → the definition (quoted faithfully) → examples and non-examples →
     check;
   - distinction: the two confusable ideas side by side on the same object → contrasting examples → check;
   - proof: the statement and why it is plausible (small case) → the strategy → the proof with every step justified →
     check of one step's justification;
   - interpretation: the picture ↔ algebra correspondence, read explicitly.
   Every visual must finish "This representation helps the learner notice ___" and say how it is read; "no additional
   visual" is valid; an annotated equation sequence may be best.
3. **Reviewer** (`LessonReview`) — two passes. The first reviews the lesson as a first explanation for a learner who
   knows only the stated prerequisites. For each equation transition, new symbol, example, table and exercise: is its meaning and required
   reasoning already established? Reports *location → what the learner must infer → why the explanation is
   insufficient → the smallest adequate repair*; flags exercises needing untaught methods, tables with unexplained
   candidates, essential reasoning only in optional sections, out-of-scope content, math errors; recommends a split
   when a repair would add another substantial objective. No "clarity scores". The second pass (`density_issues`)
   reads the lesson as a whole for *too much at once or out of order*: a block with more than one new idea (the writer
   declares each block's `new_idea`; the request counts them against a budget of about four), a second method or
   layout, a long routine justification, something used before it is taught, "you already know" without the fact,
   shifting notation, a named misconception nothing tests, the outcome problem never solved.
4. **Revision** — the writer revises once with the review; the final review is stored with the lesson.
5. Questions are verified as before (a `true_false_set` disagreement is re-solved once by the stronger model;
   `agree_on_recheck` keeps it servable); the learner can mark any block "This step isn't clear" (stored per block
   with the lesson version; the planner sees notes left on the current version when a skill is re-planned).

Rules added after the first live review: any reviewer issue (minor ones too, e.g. notation used before it is
introduced) triggers the one revision; a faded example's missing step is always machine-checked (never a 'reflect'
check); non-obvious algebraic moves are motivated before they are made ("if we also knew a²+b², adding and
subtracting would isolate a² and b²"), and first encounters show every algebra line; a lesson about *checking*
supplied answers includes a near miss (a candidate matching one coordinate but not the other) and says why it fails;
split skills keep the original's place in the learning order and follow the planner's letter order.

Rules added after the third round (S3b, complex quotients — "a little dense and unclear", 2026-10-07): skills are
outcomes (above); one new idea per block and about four per lesson; one method shown one way; routine justifications
are a clause unless the skill is a proof; teach before use; recall a prerequisite fact in one line; stable notation;
every misconception the lesson names is tested; the lesson opens with the outcome problem and solves it before
generalizing. Any density issue triggers the one revision, like a missing bridge. Counting new ideas: a recalled
prerequisite fact or the same move on another case is not new; in a proof the strategy counts once and the laws it
cites are the content. A learner's wrong answer on the skill becomes a "Learner's miss" misconception that the lesson
must test with exactly that answer as a wrong option or near miss. A "show that" about one given object is a
computation (kind `computation`), not a proof.

## The proof track (branch `proof-lessons`, 2026-10-08 — experimental)

The learner on the first S4a ("Prove complex arithmetic laws by comparing coordinates"): "quite convoluted" — a faded
step that was trivial regrouping, 12-term expansions in six letters, new letters in every question, statements made
"new" by shifting inputs, and on paper "got lost interchanging the symbols" between the two calculations. Wanted:
understand a SMALL proof and construct its calculations; numbers first, then what counts as a proof, then a special
case you build, then the general proof as a worked solution, then transfer.

Proof skills (`is_proof_skill`) get their own instructions (`pipeline/prompts_proof.py`, assembled in `prompts_v2.py`
as `PROOF_PLANNER_INSTRUCTIONS`, `PROOF_WRITER_INSTRUCTIONS`, `PROOF_REVIEWER_INSTRUCTIONS`) and run one reasoning
notch higher on the same model (`UA_PROOF_EFFORT_NOTCHES`, default 1: planner and reviewer xhigh, writer high).

- **Proof plan** (`TeachingSpec.proof`, required for a proof skill; a defective plan is re-planned once): one of six
  canonical patterns — two_routes "Two routes, same destination", find_then_check "Find it, then check it",
  suppose_two "Suppose two, show they are one", law_chain "One law per line", two_halves "Two halves, one at a time",
  conditions_or_counterexample "Check the conditions, or break one" — the ONE statement the teaching proves, the
  picture, may use / may not use, one letter set (never e, i, j, p, z…, w…, or the slot names), the numbers, the
  guided case (one input fixed, at most 4 letters) and its key line, the general case, three scope claims, 3–6
  practice statements and the capstone statement.
- **Teaching** (9 blocks): setup (the picture; may / may not use — the only circularity warning) → slots on the
  numbers → the learner finishes route B on numbers (graded) → reflect: why one example is not a proof → letters for
  every input → faded example = route B's last leg in letters (printed nowhere before) → route A and the comparison
  table → scope true_false_set → the general case as one worked table ending with the method (no summary block).
  Slot tables (product | x | y | u | v | real xu−yv | imaginary xv+yu, "reset the slots" between products) fade:
  filled → "?" in the faded example → hints only in practice → none in the capstone.
- **Practice**: knowledge point 1 "Transfer" (a walkthrough of another statement of the family, one input fixed);
  knowledge point 2 "What a proof establishes" (a true_false_set about shown arguments: numeric check, fixed input
  generalized, circular step, real law inside a coordinate). Only route legs the statement does not force are scored:
  route A's result, route B's first leg, the slots of route B's last product — never route B's final value.
- **Capstone**: an earlier skill computes the fixed input → route A → route B's slots or first leg → USE the proved
  statement for a value the learner could not get otherwise.
- **Checks** (`pipeline/proof_track.py`, hard and soft like the other validation): letters outside the plan's set,
  more than 6 letters (4 in the teaching, knowledge point 1's first question and the capstone), more than 4 terms per
  coordinate (6 in spares), numbers first, the faded check a typed route leg not printed before, arc order, served
  scope, walkthrough and capstone shape, inflated statements, "fully expand", method-description options, one "which
  law" choice, the warning once. Repair messages keep the lesson's letters.
- **Grading** (all lessons): for a key with letters, a response that still holds a complex computation —
  '(a+bi)(c+di)', 'c(a+bi)', '(a+bi)^2' — is "Not yet computed" (asked again, not counted); unexpanded coordinates
  such as 'a(−d)−bc+(ac+b(−d))i' are accepted. A real key with letters takes no i.
- Offline fixture: `app/fake_proof.py` (the S4a design) passes every check; `tests/test_proof_track.py`.
- First live sample (S4a, Sol +1 notch, $1.31, 2026-10-08): both blind learner judges preferred it "much better" than
  the old S4a (clarity 8 vs 4, convoluted 3 vs 8, longest typed coordinate 4 terms vs 6–12, no letter changes); no
  math errors in 99 recomputed items. The design audit's two blocking defects — a faded slot row that printed u and v,
  and a "Transfer" knowledge point that served a numeric repair instead of the transfer walkthrough — and its minor
  ones (λ reused for a computed number, 5 served items, an explanation answering the next step, an FTFFF scope set)
  are now checks and prompt rules.

## Reference example (approved by the learner) — procedure skill

### Multiply two complex numbers

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

*Not included:* roots, fourth powers, exponent rules, a second example needing a new method.

## Reference structure — checking supplied answers (a knowledge point of the skill that finds them)

"We want to check suggested solutions to $z^2=3+4i$. Someone has suggested the four numbers below; we check whether
these suggestions work. A suggestion works only if squaring it gives the entire target number." Work one candidate fully ($(2+i)^2=4+2i+2i+i^2=3+4i$), then the comparison table
(suggestion | what we get when we square it | matches?), then the point: *getting the real part right is not enough —
the imaginary part, including its sign, must match too.*
