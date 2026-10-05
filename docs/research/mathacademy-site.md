## Math Academy: website, product structure and learner UX (researched 2026-10-05)

**Primary sources used**
- The public pages, fetched directly: [home](https://www.mathacademy.com), [How It Works](https://www.mathacademy.com/how-it-works), [How Our AI Works](https://www.mathacademy.com/how-our-ai-works), [Pedagogy](https://www.mathacademy.com/pedagogy), [FAQ](https://www.mathacademy.com/faq), [Courses](https://www.mathacademy.com/courses), [Adult Students](https://www.mathacademy.com/adult-students), and individual course pages.
- *The Math Academy Way* (working draft, about 470 pp.) by Justin Skycak, who runs analytics and algorithms at Math Academy: [PDF](https://www.justinmath.com/files/the-math-academy-way.pdf). Cited below as **MAW** with page numbers. It is the most detailed primary source on product behaviour.
- Skycak's blog, [justinmath.com](https://www.justinmath.com).

---

### 1. Course catalog structure

**Hierarchy:** Course → Unit → Module → Topic → Knowledge Point (KP) → question pool.

- **Course.** Math Academy describes it as "simply a section of our knowledge graph" ([How Our AI Works](https://www.mathacademy.com/how-our-ai-works)). The whole curriculum is "multiple thousands of interlinked topics spanning 4th Grade through university-level math."
- **Units** are numbered, and each one shows a topic count (for example "1. Preliminaries — 25 topics").
- **Modules** are numbered across the whole course, not restarted inside each unit (for example 2.4 Determinants … 8.31).
- **Topics** are numbered three levels deep (for example 2.4.3). One topic is one lesson of about 10 minutes ([Adult Students](https://www.mathacademy.com/adult-students)).
- **Knowledge points.** Each lesson has "about 3 or 4 KPs increasing in difficulty" (MAW p.397).
- MAW p.449: "A typical course ranges from 150–300 lessons." The Pedagogy page puts it as "~300 topics x 3 knowledge points" ≈ 1,000 steps, against about 100 steps in a textbook.

**Measured counts.** I parsed these from each `/courses/<slug>` page on 2026-10-05.

| Course | Units | Modules | Topics |
|---|---|---|---|
| [Linear Algebra](https://www.mathacademy.com/courses/linear-algebra) | 8 | 31 | 181 |
| [Mathematics for Machine Learning](https://www.mathacademy.com/courses/mathematics-for-machine-learning) | 12 | 47 | 301 |
| [Multivariable Calculus](https://www.mathacademy.com/courses/multivariable-calculus) | 6 | 29 | 187 |
| Methods of Proof | 7 | 20 | 135 |
| Calculus I / II | 8 / 5 | 27 / 29 | 175 / 160 |
| Differential Equations | 7 | 26 | 159 |
| Discrete Mathematics | 8 | 33 | 143 |
| Probability & Statistics | 10 | 33 | 181 |
| Math Methods for Physical Sciences I / II | 13 / 12 | 46 / 40 | 294 / 232 |
| Abstract Algebra (marked "Coming soon" on /courses, but its page already lists content) | 9 | 21 | 129 |
| Mathematical Foundations I / II / III | 13 / 14 / 15 | 45 / 52 / 53 | 357 / 360 / 323 |
| AP Calculus BC | 16 | 53 | 305 |

That works out to roughly 20–25 topics per unit and roughly 4–7 topics per module.

**Linear Algebra unit breakdown:**
- Preliminaries (set theory, vector geometry): 25 topics
- Matrices (determinants, Gaussian elimination, elementary matrices, inverses, LU): 36
- Vector Spaces: 27
- Linear Transformations: 10
- Diagonalization (eigen, complex eigenvalues, generalized eigenvectors, Jordan form): 29
- Projections (inner products, orthogonality, Gram-Schmidt, QR): 25
- Quadratic Forms: 20
- Applications: 9

**How finely topics are split.** Topics are divided by case and by dimension. Examples:
- "Cramer's Rule for 2x2" / "…3x3"
- "Diagonalizing a 2x2 Matrix" / "…3x3 With Distinct Eigenvalues" / "…3x3 in the General Case"
- "Linear Independence in Abstract Vector Spaces" is a separate topic from the ℝⁿ version.

**Course page anatomy:** description, then Overview / Outcomes / Content tabs, then a prerequisite course. Each course page lists its prerequisite courses; Linear Algebra requires Calculus I, AP Calculus BC, or Mathematical Foundations III.

**Size in XP.**
- An average course is about 3,000 XP; Prealgebra is about 2,000 and Precalculus about 4,000 (MAW p.389).
- AP Calculus BC is about 6,000 XP ([FAQ](https://www.mathacademy.com/faq)).
- The Mathematical Foundations sequence is about 15,000 XP, roughly 250 hours ([justinmath](https://www.justinmath.com/dont-you-need-10000-hours-to-learn-math/), via search snippet).

**Core vs supplemental topics.** Core topics are taught first, and every ancestor of a core topic is also core. Nothing is skipped; only the order changes. Mathematical Foundations drops about one third of the 4th-grade-through-Calculus-BC topics, the ones that are not prerequisites for university math (MAW ch.32, p.391).

**Positioning relevant to Axler.**
- Math Academy's university courses other than Methods of Proof and Discrete Mathematics are "computation-based."
- A proof-based "Abstract Linear Algebra" course is planned. They joke that Axler's book should be called "Linear Algebra Done a Second Time" (MAW p.453).

**Catalog drift.** The /courses page still marks Abstract Algebra and ACT as "coming soon." The Adult Students page still marks Probability & Statistics, Discrete Mathematics and Differential Equations as in development, even though all three have full course pages.

---

### 2. Anatomy of a topic / lesson

**Format.** "A lesson is a sequence of slides with instructional text, interspersed with active problem-solving" (MAW p.397).

**Order inside a lesson.**
1. Introduction.
2. KP 1: a worked example, then **2–5 practice questions** of the same problem type.
3. KP 2, KP 3, and sometimes KP 4, each a harder case of the same skill.

**Pass rule for a KP.** "If the student gets two practice problems correct in a row, then they move on to the next example, otherwise, they're given additional problems," up to 5 ([How It Works](https://www.mathacademy.com/how-it-works)). The number of questions adapts to accuracy ([How Our AI Works](https://www.mathacademy.com/how-our-ai-works)).

**Feedback.**
- "Every question has an explanation," meaning a full worked solution is shown after answering.
- During a lesson the learner can scroll back to the worked example.
- Past lessons can be reopened in "reference mode" (MAW p.424).
- Visualizations and diagrams are used "when possible" (dual coding).

**What happens on failure.**
- If the learner cannot master a KP within 5 questions, the lesson is "halted" and gives 0 XP.
- The learner then does unrelated topics, and the lesson comes back "within a few days" with different questions after a delay.
- If the learner fails **twice at the same KP**, the system assigns remedial reviews on that KP's **key prerequisites**. Every KP is hand-linked to the prerequisite topics it most directly uses, even ones several levels back (MAW pp.76–77, 300–301).
- The bar is never lowered: no hints are given away (MAW p.467).

**Pass rates.** The FAQ says 93% pass on the first attempt and 98% on the second. MAW says 95% and 99%.

**Question formats.**
- Mostly multiple choice historically. Many typed free-response questions were added in 2025 ([Hecker](https://frankhecker.com/2026/01/10/a-year-of-math-academy/)).
- The site loads MathQuill and KaTeX CSS. UNVERIFIED that the in-app answer box is MathQuill.
- Proof courses use proof "templates" ([Matuschak](https://notes.andymatuschak.org/Math_Academy)).

**Things the product deliberately leaves out** (MAW pp.462–467):
- No "I don't know" button, except on the diagnostic.
- Learners cannot edit their knowledge profile.
- No LLM chat. Explanations are "hardcoded… into bite-size pieces."
- No human tutoring.
- All content is written in-house.

**Content quality control.** Analytics track pass rates per topic, per KP and per question. Hard spots are fixed by adding a KP or splitting the topic, not by lowering the bar (MAW pp.304–305).

---

### 3. Task types and how the next task is chosen

| Task | What it is | Cadence / rules |
|---|---|---|
| **Lesson** | New topic, 3–4 KPs | Unlocked as soon as its prerequisites are mastered ("layering") |
| **Review** | Several questions on one learned topic, mixed across its KPs | Set by the spaced-repetition engine (FIRe). Questions come from the lesson's pool but are never one the learner already answered. Scoring depends on order: ✖✔✖✔✔ ≠ ✔✔✖✔✖ (MAW p.414) |
| **Quiz** | Timed, no reference material, a random mix of previously learned topics (enrolled course first; topics already quizzed or covered by another quizzable topic come later) | About every **150 XP**. Difficulty adapts toward an **80%** average score. Every missed question becomes an **immediate remedial review**. An optional retake with different questions gives more XP. Leaving mid-quiz marks unanswered questions wrong |
| **Multistep** | A scaffolded "mini-project" of about **8–12 questions** that pulls earlier skills together (originally modelled on AP Calculus free-response questions; coding projects planned) | About one per **100 XP**, only in courses that have enough of them ([Skycak, 2024-11-23](https://www.justinmath.com/the-future-of-multistep-tasks-on-math-academy/)) |
| **Remedial review** | Review on key prerequisites | After a second failure at the same KP, or after a missed quiz question |
| **Diagnostic / supplemental diagnostic** | Placement; small top-up diagnostics when the graph changes | At the start, and rarely afterwards |
| **Tests** | Midterms and a final | Assigned automatically only to students taking a course for credit ([FAQ](https://www.mathacademy.com/faq)) |

**How the next task is chosen.** The goal is to "maximize the amount of learning per unit of time" ([How Our AI Works](https://www.mathacademy.com/how-our-ai-works)).

- **The dashboard is a menu, not a queue.** About **5 tasks** are offered, and the learner picks any of them ([Adult Students](https://www.mathacademy.com/adult-students)).
- **Ordering** is by an "importance" score that combines how much due review the task covers implicitly, how much later content depends on it, and other factors. Math Academy says the order "doesn't really matter."
- **Tasks stay put.** A task usually remains on the dashboard once it appears, to discourage cherry-picking and avoid frustration (MAW pp.422–423).
- **Repetition compression.** Due reviews are knocked out by lessons that implicitly practice them. A review is shown only if no lesson can cover it.
- **Interleaving.** Dissimilar topics are offered together and related ones are spaced apart, to minimise interference.
- **Lesson floor.** On average at least about **25% lessons**, so review never crowds out new material entirely (MAW p.314).
- **Foundations later.** Missing foundations are deferred until the enrolled course actually needs them, so the learner builds momentum first.
- **Per-topic pace.** Each student has a learning speed per topic (for example 2× or 0.5×) that changes the review frequency. Topics a student finds hard get forced explicit reviews.

**Modes and settings.**
- **Test Prep Mode** keeps the learner on reviews instead of promoting them to the next course (MAW p.451).
- **Holistic mode** also fills lower-grade gaps that are not prerequisites. It is disabled for university courses.
- **"Clean slate"** re-runs the diagnostic from scratch.
- **Gravity** lets learners name target topics; the system then prioritises paths toward them. It is a beta you request access to (reported by [Hecker](https://frankhecker.com/2026/01/10/a-year-of-math-academy/); not on official pages).
- **Accommodations** adjust quiz timing.

---

### 4. How progress is shown

**XP**
- 1 XP ≈ 1 minute of focused work for an average "serious but imperfect" student.
- Recommended pace: 20–40 XP/day, 3–5 days a week ([FAQ](https://www.mathacademy.com/faq)). For adults: about 30 XP/day, 4 days a week.
- Daily XP goal and work days are adjustable, including through a parent or teacher "oversight" account.
- XP scale (MAW p.312):
  - bonus XP for a perfect score
  - full XP for nearly perfect
  - most XP for otherwise passable
  - a little XP for nearly passable
  - 0 XP for poor performance
  - **negative XP** for rushing or blowing off a task, which fixed "XP hackers" (pass rates went from under 50% to over 90%)
- Exact XP per task type is **not published**. A lesson of about 10 minutes implies roughly 10 XP. A third-party clone encodes "lesson = 3×KP+2, review = 4" — **UNVERIFIED**.

**Course progress %**
- Progress is the **share of topics completed**, not XP. It only goes up when a lesson is completed.
- Progress is nonlinear: fast at first and slower later as reviews build up.
- Failing a task on a "conditionally completed" topic can drop progress by several percent (MAW pp.314, 448).

**Estimated completion date**
- Calculated as estimated XP remaining divided by recent XP/day. It moves as accuracy changes (MAW p.441).
- The diagnostic report shows completion dates for several paces. [Matuschak](https://notes.andymatuschak.org/Math_Academy) and [Whitmore](https://jonathanwhitmore.com/posts/2024-09-10-mathacademy/index.html) both singled this out as motivating.

**Knowledge graph / knowledge profile view**
- Topics are ovals with prerequisite arrows running bottom to top. "The darker blue the oval… the more stable the knowledge"; unlearned topics are grey or white. See the [Calculus II screenshot](https://www.mathacademy.com/img/screenshots/knowledge-graph-calculus2.png).
- After finishing a course, most ovals are still light blue: completing a topic means baseline mastery, and full mastery takes months of reviews (MAW p.405).

**Leagues**
- Weekly XP leaderboards in small leagues, with promotion and demotion each week. Total XP is never lost. Leagues are optional ([How It Works](https://www.mathacademy.com/how-it-works)).

**Other indicators**
- Activity log and completion certificates ([Hecker](https://frankhecker.com/2025/05/19/math-academy-update-1/)).
- **Streaks: UNVERIFIED.** Two reviewers mention them (one as "upcoming"); no official page does.

---

### 5. Diagnostic placement

**Length and format**
- About **30–45 minutes**, and it can be split across sessions ([How It Works](https://www.mathacademy.com/how-it-works)).
- **20–40 questions** for lower-grade courses and **40–60** for courses like Calculus (MAW p.376).
- Each answer informs about 10 topics on average. The candidate set is 500–1,000+ topics: the course plus twice as many foundation topics.

**Algorithm**
1. Compress the graph to a covering set of topics (a topic counts as "covered" if it has an ancestor and a descendant within 3 prerequisite edges).
2. Repeatedly ask the question that gives the most information.
3. A correct answer adds credit to the topic and its prerequisites; a wrong answer subtracts from the topic and its postrequisites. A correct answer to one advanced "leaf" topic also gives partial credit to other leaf topics in the same module.
4. Each topic keeps a running plus/minus balance. A topic that ends positive is credited with that many spaced repetitions.

**Timing and confidence**
- Slow correct answers count for less.
- Topics placed out of "just barely" are marked **conditionally completed** and are pruned back if the learner struggles later.
- The placement measures the *conservative* edge of mastery (MAW ch.30, pp.375–381).

**Choosing the questions.** Each question is "the simplest question that would convince an expert tutor" the topic is mastered **and** that exercises all of the topic's prerequisites (MAW p.381).

**Output: the diagnostic report**
- course recommendation
- completion dates for several daily XP paces
- foundational gaps
- mastery per topic and per unit
- which topics were assessed and how the learner did on each

After the diagnostic, a custom course is built that includes the missing foundations.

---

### 6. Learner and reviewer sentiment

**Praise**
- Finds gaps precisely and adapts placement; strong for relearning ([Whitmore/HN](https://news.ycombinator.com/item?id=41534847), [Hartl](https://www.michaelhartl.com/math-project-update-mainly-math-academy)).
- Efficient: Hecker logged 19,500 XP in a year (about 53/day) and finished 5 courses ([Hecker](https://frankhecker.com/2026/01/10/a-year-of-math-academy/)).
- Deeper than university courses on Linear Algebra and Multivariable Calculus (Hartl) and deeper than Brilliant (HN commenters).
- XP and leagues are motivating, even "addictive" ([LMSherlock](https://lmsherlock.substack.com/p/my-first-month-with-math-academy), [drgore](https://drgore.substack.com/p/my-review-of-math-academy)).
- Hierarchical spaced repetition, completion forecasts, worked examples before practice ([Matuschak](https://notes.andymatuschak.org/Math_Academy)).

**Criticism**
- **Procedural and shallow; little motivation or meaning.**
  - [Pershan](https://pershmail.substack.com/p/math-academy-wants-to-supercharge): "students are going to go faster, and learn shallowly."
  - [Dan Meyer](https://danmeyer.substack.com/p/it-is-fun-to-pretend-that-hard-things): XP rewards exercises, not reading the explanations.
  - [Oz Nova](https://newsletter.ozwrites.com/p/a-balanced-review-of-math-academy): best used as a drill tool next to a conceptually rich textbook.
  - Matuschak: "mostly lacks discussion of motivation, implication, meaning"; tasks are "over-weighted on the 'apply' dimension"; reads like "a sea of isolated facts."
- **Multiple choice hides misconceptions; no real transfer tasks; proofs are templated and cannot be graded** (Matuschak, Hartl).
- **Black-box queue.** The algorithmic task list causes "a feeling of passivity" (Matuschak). There is no tree of courses or prerequisites to browse ([Hecker](https://frankhecker.com/2025/05/19/math-academy-update-1/)). Learners cannot choose tasks or tell the system "I don't know this" (LMSherlock).
- **Look and feel.** Text and images only, no interactives (drgore). Feels like "living inside a database table," and long worked examples make readers' eyes glaze over (Matuschak). Weak mobile experience (HN). Several seconds of delay after each task (Hecker).
- **Friction.** Too few reviews for low-XP users, with no way to ask for more ([Hecker](https://frankhecker.com/2025/10/06/math-academy-update-3/)). Timed diagnostic and quizzes stress some learners. Leagues can be distracting. The diagnostic includes "insultingly easy" questions that cannot be skipped (HN). Remedial chains cannot be skipped (Oz Nova).
- **Business.** $49/month, no trial (30-day refund), perpetually "beta," slow course releases (Hecker, drgore).

---

### Design implications for a book-to-course MVP

1. **Copy the hierarchy, scaled to one chapter.** Book → Chapter (unit) → Section (module) → Topic (one micro-skill, about 10 minutes) → 2–4 KPs. Each KP is a worked example plus a pool of at least 6–8 question variants. At Math Academy's granularity (about 6 topics per module), an Axler chapter probably becomes roughly 25–50 topics. Split topics by case, as Math Academy does (2×2 → 3×3 → n×n; ℝⁿ → abstract space).
2. **Store three kinds of edges.** Topic→topic prerequisite edges, topic→topic "encompassing" edges with fractional weights (for implicit review), and KP→key-prerequisite links (for targeted remediation). Have the LLM propose them, then keep them human-editable, because they drive scheduling, remediation and diagnostics.
3. **Lesson engine rules.** A KP passes on 2 correct in a row, with at most 5 questions. On failure, halt the lesson (0 XP) and retry it later with fresh questions. On a second failure at the same KP, assign remedial reviews on that KP's key prerequisites. Generate every question with a full worked solution, and never reuse a question the learner already answered.
4. **Task types for v1:** Lesson, Review (3–5 mixed questions, scored with attention to order), and Quiz (timed, no reference, every ~150 XP, missed → immediate review, optional retake). Add **Multistep** (8–12 scaffolded questions) in v2. It suits a theorem's applications or a proof broken into steps, and it answers the "no context" criticism.
5. **Next-task selection.** Offer a menu of about 5 tasks ranked by importance (due reviews covered + downstream dependents). Keep at least ~25% lessons, interleave dissimilar topics, let lessons cancel due reviews implicitly (a simple FIRe-style approach is enough at first), and keep tasks on the dashboard until done.
6. **Skip the full adaptive diagnostic.** For a single chapter, use a short (10–20 question) prerequisite check on the chapter's outside dependencies. Mark marginal passes as "conditionally completed" so they can be pruned later. Allow "I don't know" here.
7. **XP and progress.** Set 1 XP ≈ 1 estimated minute per task. Give a bonus for perfect, 0 for a failed lesson, and drop the penalties and leagues (single adult user). Show progress as % of topics completed, plus an estimated completion date (XP remaining ÷ recent pace) and a daily goal.
8. **Show the graph to fix the black box.** Make the topic map the main view: ovals shaded by repetition count, locked/available/done states, and a "why this task" line on each dashboard card. Let the user pin goal topics (like Gravity).
9. **Use the book to answer the "drill-only" criticism.** The pipeline already has the author's motivation, margin notes and figures. Attach a short "why this matters / where it's going" excerpt to each topic, with a link back to the PDF page. Reveal worked examples step by step.
10. **Go beyond multiple choice.** Use typed answers checked symbolically (SymPy equivalence) or numerically (with tolerance). For Axler-style content, add conceptual question types: true/false with a counterexample, "which hypothesis is needed," ordering proof steps, filling a proof blank, and short proofs graded by the LLM against a rubric. Treat proof grading as the main quality risk, since Math Academy itself calls Axler a second, proof-based course it has not built yet.
11. **Pre-generate and cache content; don't chat live.** Math Academy deliberately uses fixed, tested explanations rather than LLM dialogue. Generate lessons and question pools once per chapter, validate them (answers solved and cross-checked), and serve them statically. If live LLM help exists at all, make it an optional "explain differently" that marks the attempt as assisted.
12. **Measure the content.** Log correctness, time and attempts per question and per KP. Flag items with low pass rates or that users report, and regenerate them or split the KP. Math Academy got to 95%+ first-try pass rates by analysing content this way, not by lowering the bar.