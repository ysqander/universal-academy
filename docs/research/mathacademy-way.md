# The Math Academy Way: pedagogy and algorithms (research notes for Universal Academy)

**Sources.** All retrieved 2026-10-05.
- **[MAW]** *The Math Academy Way*, current working draft, read in full text from the live Google Doc: https://docs.google.com/document/d/1LLZK_34Oer9LwuqAv-pqxfXlR8n7V8zJ_MO323R7egI/edit. The older PDF is at https://justinmath.com/files/the-math-academy-way.pdf. Chapters used: 4 (Knowledge Graph), 13 (Mastery Learning), 14 (Cognitive Load), 16 (Layering), 17 (Non-Interference), 18 (Spaced Repetition), 19 (Interleaving), 20 (Testing Effect), 21 (Targeted Remediation), 22 (Gamification), 29–32 (Technical Deep Dives on HSRS, Diagnostics, Learning Efficiency, Core Topics), plus the FAQ and Glossary.
- **[FIRe]** Skycak, "Hierarchical Spaced Repetition (HSRS): Spaced Repetition Compression and Fractional Implicit Repetition (FIRe)", https://www.justinmath.com/individualized-spaced-repetition-in-hierarchical-knowledge-structures/. First published 2023-10-05 under the title "Optimized, Individualized Spaced Repetition…" and renamed to HSRS in 2026. It includes a section of clarifications written after the July 2024 HN discussion.
- **[AI]** https://www.mathacademy.com/how-our-ai-works
- **[HIW]** https://mathacademy.com/how-it-works
- **[PED]** https://www.mathacademy.com/pedagogy
- **[MS]** https://justinmath.com/the-future-of-multistep-tasks-on-math-academy/ (podcast transcript, Nov 2024)
- **[HN]** https://news.ycombinator.com/item?id=40954571
- Secondary observations, labelled where used:
  - Andy Matuschak, https://notes.andymatuschak.org/Math_Academy
  - L-M-Sherlock (the FSRS author), https://lmsherlock.substack.com/p/my-first-month-with-math-academy
  - Frank Hecker, https://frankhecker.com/2025/02/14/math-academy-part-7/

**What is not public.** Math Academy says the implementation is proprietary ([FIRe]). The FSRS maintainer said the same: only the outline is known (https://github.com/orgs/open-spaced-repetition/discussions/28). No interval table, rawDelta values or selection weights have been published. Anything below marked **UNVERIFIED** or **DESIGN CHOICE** is my inference or proposal, not Math Academy's.

---

## 1. Knowledge graph model

| Element | Definition (paraphrased from source) | Source |
|---|---|---|
| **Topic** | The atomic unit of learning. One topic has one lesson, split into 3–4 knowledge points (KPs). A course is just a subset of the global graph, e.g. about 300 topics for AP Calc BC. | [MAW ch.4], [AI] |
| **Prerequisite edge** | The "forward" graph: what you are ready to learn next. A topic becomes learnable only when all its prerequisites have been shown. | [MAW ch.4, 13], [FIRe] clarifications |
| **Key prerequisite** | Each **KP** (not the topic) links to 1+ topics whose knowledge it uses most directly. These are often several steps back in the hierarchy. Used to target remediation. Weights are set mainly along "direct and key prerequisite" edges. | [MAW ch.4, 21, 29, Glossary] |
| **Encompassing edge (advanced → simpler, weight w ∈ [0,1])** | The "backward" graph. The simpler topic is practised *implicitly as a component skill* when you do the advanced one. Read w loosely as the probability that a random problem from the advanced topic exercises a random problem from the simpler one. w = 1 is a full encompassing; w < 1 is partial. | [MAW ch.29], [FIRe] |
| Encompassing ≠ prerequisite | Encompassed topics are usually prerequisites, but prerequisites are often not fully encompassed. A direct or key prerequisite edge with **w = 0** is normal (you need the concept but don't execute the skill). Running FIRe on the prerequisite graph gives large amounts of wrong credit; Math Academy hit exactly this early on. A domain expert (Skycak) maintains the encompassing graph by hand, and the curriculum director maintains the prerequisite graph. | [MAW ch.29], [FIRe] clarifications, [HN] |
| Which weights to set | Only set a weight explicitly when all three hold: (1) it is non-trivial, (2) repetition flow can't infer it transitively, (3) the two topics are close in the prerequisite graph. Every other weight is 0 unless implied by flow. In practice these are the direct and key prerequisite edges, so the count is O(#topics). | [MAW ch.29] |
| Non-ancestor encompassings | Full-weight edges between "equivalent" topics in different courses that are not prerequisites of each other, e.g. algebra-based vs calculus-based statistics. | [MAW ch.29] |
| Mastery floor | Lower-course topics far enough back (or below the simplest diagnosable topics) that they are treated as mastered automatically. | [MAW ch.29, FAQ] |
| Module / leaf topic | Grouping used in the diagnostic: a correct answer on a leaf topic gives credit to other leaf topics in the same module. | [MAW ch.30] |
| Core vs supplemental | Core topics are taught first so they get more practice. Rule: every ancestor of a core topic must also be core, and each course must keep a balance of core and supplemental. The algorithm itself is proprietary. | [MAW ch.32] |

**Student-side concepts**
- **Knowledge profile.** The repetitions accumulated on each topic. The UI shading gets darker with more successful practice ([AI], [MAW ch.13]).
- **Knowledge frontier / edge of mastery.** The set of topics whose prerequisites are mastered but which are not yet learned. Math Academy says "knowledge frontier", not "learning frontier". New lessons always come from the frontier ([MAW ch.4, 13]).
- **Zone of mastery.** It has a conservative edge, which the placement diagnostic measures, and an aggressive edge, which mastery learning with layering works on. Passing a lesson means baseline mastery, not automaticity ([MAW ch.30, FAQ]).

## 2. Principles and how each becomes a mechanism

| Principle | Mechanism in Math Academy | Source |
|---|---|---|
| Mastery learning | Lessons are served only for frontier topics. A KP is passed by answering enough questions correctly with few enough mistakes. If you get stuck, you work on topics that don't depend on the stuck one. | [MAW ch.13, Glossary] |
| Minimizing cognitive load and **minimum effective dose** | About 1000 steps for calculus (about 300 topics × 3–4 KPs). Each KP is a short explanation (a minimum effective dose), then a worked example with subgoal labels and dual-coding visuals, then active problem-solving within minutes. Scaffolding is removed gradually. | [MAW ch.14, 10, 12, FAQ], [PED] |
| Layering | You move forward as soon as prerequisites are mastered. The curriculum is highly connected (application topics, "connection" topics, multi-part problems). Each lesson covers *all* problem types a student who knows the topic should be able to solve, which creates the encompassings. | [MAW ch.16] |
| Non-interference | Dissimilar concepts are taught side by side and related ones are spaced apart; the task menu is diverse and non-overlapping. **How "relatedness" is computed is not published (UNVERIFIED).** | [MAW ch.17], [AI] |
| Interleaving | Lessons use blocked practice (a few blocks of rising difficulty). Reviews and quizzes are interleaved. **Macro-interleaving** comes from a breadth-first path through many small topics. **Micro-interleaving** happens inside reviews and quizzes. Accepted trade-off: not every topic gets an explicit interleaved review before a quiz, because compression is worth more. | [MAW ch.19, FAQ] |
| Retrieval practice (testing effect) | Reviews are meant to be done from memory, with reference material used only as a "spotter". Quizzes are timed and offer no reference. | [MAW ch.20, FAQ] |
| Spaced repetition | Hierarchical SR: FIRe plus compression plus per-student-per-topic speeds (§4–6). | [MAW ch.18, 29] |
| Targeted remediation | Corrective, preventative, foundational and content remediation (§9). | [MAW ch.21] |
| Gamification | XP, tiered awards, penalties, optional leagues (§11). | [MAW ch.22] |

## 3. Lessons, knowledge points and question counts

- A lesson is: introduction → worked example → **2–5 practice questions** of the same type. The worked example plus its practice is one **KP**. A typical lesson has **about 3–4 KPs**, getting harder: the first covers the simplest case and later ones extend it ([MAW FAQ "How does a lesson work?"]).
- **To pass a KP:** answer **2 practice problems correctly in a row**, with **up to 5** problems per worked example ([HIW]). The number of questions adapts: correct answers reduce it and wrong answers add more ([AI], [MAW ch.21]).
- **To fail:** if mastery isn't shown within 2–5 questions, the lesson is failed. The learner does other lessons, then retries later with *different questions after a delay* ([MAW FAQ, ch.22]). Andy Matuschak observed that three misses ended a lesson (observation only).
- **Size:** an initial lesson is about **10 questions** in total, and later repetitions are reviews of **3–5 questions** ([FIRe]). On average a new topic takes about **20 minutes** (about 6000 XP / 300 topics for AP Calc BC, including review and quizzes) ([FIRe]).
- **Pass rates** (an internal content-quality target): **95% pass first try, 99% within two tries**. Pausing alone gives about an **80%** pass rate on the second attempt ([MAW ch.21, FAQ]).

## 4. The spaced repetition model (published structure)

From [MAW ch.29] and [FIRe]:

```
repNum ← max(0, repNum + speed · decay^failed · rawDelta)
memory ← max(0, memory + rawDelta) · 0.5^(days / interval)
```

- **repNum.** The number of successful rounds of spaced repetition. A *repetition* means a successful review at the appropriate time.
- **days.** Days since the previous repetition.
- **interval.** The ideal gap between repetition repNum and repNum+1. The published text implies it is a function of repNum; the function itself is **UNVERIFIED**.
- **memory.** Expected recall now. It decays exponentially, with a half-life equal to `interval`. A review becomes **due** when memory is "sufficiently low"; the threshold is **UNVERIFIED**. Memory is also used to **discount early repetitions**.
- **speed.** The student-topic learning speed (§6).
- **failed.** 1 or 0.
- **rawDelta.** Raw credit for the repetition. It is positive if passed and negative if failed. Its magnitude grows with the quality of work (good passes, or very bad fails). It is **discounted if done early**, i.e. while memory is still high.
- **decay.** Starts at 1 and **grows as the review becomes more overdue** (memory very low). It multiplies only failures, so you move backwards faster. It models the "summer slide".
- An earlier draft named this variable `netWork` (work/XP gained or lost), according to Frank Hecker's summary of the Jan 2025 PDF; the current draft uses `rawDelta`.
- **Behavioural facts from the FAQ** ([MAW FAQ]):
  - Intervals can grow to **a year or more**.
  - Strong performers may wait weeks for the first review and months for the second.
  - An easy, aced topic's repetition-1 interval can equal a hard, barely-passed topic's repetition-5 interval. This implies speed scales how far repNum moves, while interval is a fixed function of repNum.
  - The goal is *not* 100% freshness. It is the minimum review that keeps forgetting from becoming a wall or forcing a full re-learn.
  - A "test-prep mode" serves early reviews deliberately.
  - Reviews are supposed to feel hard ("wait-lifting").
- After a long break, the recommendation is to **retake the diagnostic**, which peels the frontier back ([FIRe] clarifications).

## 5. FIRe: how credit and penalties flow

From [MAW ch.29], [FIRe] and [AI]:

- **Credit flows down.** Passing a repetition on an advanced topic T also gives credit to every topic T encompasses. Example: passing a review of "2-digit × 1-digit multiplication" credits "1-digit multiplication" and "adding a 1-digit number to a 2-digit number".
- **Penalties flow up.** Failing a repetition on a simpler topic S also penalizes every topic that encompasses S: if you can't add, you can't do the multiplication.
- Failing an advanced topic is **not** described as penalizing its components. The asymmetry runs only one way: down for credit, up for penalties.
- **Multi-layer flow.** Credit and penalties continue to 2nd- and 3rd-order topics. Credit travels "like lightning bolts" down a trunk of full encompassings; penalties spread "like growing trees" upward.
- **Partial encompassings.** Credit passes unchanged along w = 1 edges and fades along partial edges. The natural reading is the product of weights along a path, but this is **UNVERIFIED**. How multiple paths combine (max, sum or noisy-OR) is **not published**.
- **Early discount applies to implicit credit.** Implicit reps usually arrive before the encompassed topic is due, so they are partial. They still push the next due date out ([MAW FAQ]).
- **Speed gate.** If the student-topic speed is **< 1**, that topic **discards all incoming implicit credit** and gets explicit reviews instead. The reason: weaker students on hard topics don't reliably absorb implicit reps ([MAW ch.18], [FIRe]).
- **Speed scaling.** A review on a topic with speed 2× counts as 2 repetitions; at 0.5× it counts as 0.5 ([MAW ch.18]).
- **Answer-level flow for ability tracking.** Correct answers propagate down to encompassed topics and incorrect answers propagate up ([MAW ch.29]).
- In quizzes and multisteps **each question is linked to its own topic**, so one task can add and remove credit on several topics at once ([MAW FAQ]).
- Skycak describes FIRe as a "physical" model: each answer is a fractional repetition flowing through the graph. He moved to this after starting with Bayesian Knowledge Tracing for the diagnostic and finding the complexity unmanageable ([FIRe] clarifications).

## 6. Student-topic learning speed

- `speed = (speed-up from student ability) / (slow-down from topic difficulty)`. As a grid: strong student on an easy topic is fastest, weak student on a hard topic is slowest, and the diagonal is the baseline ([MAW ch.29]).
- **Ability** is measured per topic. It is accuracy across answers, weighted towards recent ones, with correct answers propagated down and incorrect answers up. A new topic's starting value is **predicted from its neighbourhood**: direct prerequisites, key prerequisites, encompassings, and topics in the same module ([MAW ch.29]).
- **Difficulty** is a topic's accuracy across all serious students' assessment answers. It improves the starting prediction and acts as a self-correcting factor ([MAW ch.29]). This **needs a population**, which a single-user app does not have.
- **Preventative remediation.** A low predicted speed on a new topic comes from low-speed neighbours. Those neighbours are already getting more frequent explicit reviews, which works as "pre-remediation" ([MAW ch.21]).

## 7. Spaced Repetition Compression and task selection

**Objective** ([MAW ch.18, 29, 31], [FIRe]):
1. Gather all due repetitions.
2. Choose **reviews *and new lessons*** whose implicit repetitions cover as many of them as possible ("dominoes").
3. Once nothing is due, choose tasks that **hold off upcoming reviews** while **climbing the graph evenly**, keeping a broad set of future choices.
4. Maximise the overall gain in repetitions across the profile.

**Published rules and behaviours** ([MAW FAQ], [AI]):
- If a due review can be knocked out by a lesson, **only the lesson is offered**. A review made redundant by a lesson already on the dashboard is never shown.
- A due review on an old topic may be served as a review of a *newer, not-yet-due* topic that encompasses it. The early, discounted partial rep on the newer topic is a bonus.
- **Safeguard against waiting:** if something is due and nothing can knock it out, an explicit review is served now.
- **Backlog throttle:** a lesson is forced about **every 3 reviews** on average even with a large backlog. Lessons get **at least about 25% of the time** (hard rule).
- **Dashboard.** It is a menu, not a queue. Tasks usually **stay until done** because selection isn't instant, people avoid tasks, removed tasks frustrate users, and long-standing tasks feel fairer. The efficiency loss is negligible.
- Lessons are ordered by **"importance"**: how much review they knock out, how much later content depends on them, plus other factors. In practice the order doesn't matter much.
- **Foundational gaps.** Course topics that don't depend on missing foundations come first, to build momentum; the gaps are filled when they start blocking progress ([MAW ch.21], [AI]).
- **Review load.** Early on there are almost only lessons. In the long run expect "a couple reviews per lesson" ([MAW FAQ]). Empirically most courses need about **one explicit review per topic** on average ([MAW ch.31]).
- Reviews cluster on recent topics: they need more reps and have fewer encompassers built on top of them yet ([MAW FAQ]).
- **Upper bound.** With a fully encompassed chain, no explicit review is ever needed as long as new topics remain.
- **Lower bound.** Independent flashcards with no encompassings: every review is explicit. A modest minority of encompassings already gets close to the upper bound ([MAW ch.31]).
- **Pace and efficiency.** Measured pace effect: efficiency multiplier ≈ `(pace/40)^0.1`, so doubling pace gives about +7% ([MAW ch.31]). Near the end of a course there are few frontier lessons left, so compression gets weaker ([MAW ch.22]).

## 8. Reviews and quizzes

**Reviews** ([MAW FAQ], [FIRe]):
- **3–5 questions**, interleaved across the topic's KPs.
- Several questions are needed to show consistency and to resist guessing.
- **Order matters for pass/fail**: wrong-right-wrong-right-right reads as learning happening, while right-right-wrong-right-wrong reads as struggling. The exact rule is **UNVERIFIED**.
- Questions differ every time.

**Quizzes** ([AI], [HIW], [MAW ch.20, FAQ]):
- **Frequency:** one becomes available **every 150 XP**. There is a window of about 30 XP to start it.
- **Coverage:** **cumulative**, drawing from all learned topics and not just recent ones. Topics are picked randomly from a diverse set, with priority to the enrolled course. They are timed, with no reference and no scaffolding.
- **Difficulty** is adapted so the average score is **about 80%** (80–85% is the "sweet spot"). Scoring above 80% makes the next quiz harder; scoring below makes it easier.
- **Follow-up:** each missed question **immediately triggers a remedial review** on its topic. If the result isn't good, a **retake** is offered after the reviews.
- **Purpose:** measure automaticity and retention, and feed the pace and speed adaptation.
- **Observed sizes** (observations only): 6 questions in 8 minutes (Matuschak); 8 questions in 14 minutes (L-M-Sherlock).

**Multisteps** ([MS]): mini-projects of about 8–12 scaffolded questions, each linked to a previously learned topic. They are served roughly every 100 XP.

## 9. Remediation

From [MAW ch.4, 21, FAQ]:

1. **Struggling inside a task:** more questions.
2. **Failing a lesson:** pause that path, let the learner make progress elsewhere, then retry with new questions after a delay.
3. **Failing again at the same KP with no further progress (a "plateau"):** immediately assign **remedial reviews on that KP's key prerequisites**. If those reviews fail, assign the prerequisite *lessons*, and so on downward. Peeling back is **deliberately slow** when there is strong evidence the prerequisites are known, to resist students failing tasks on purpose to get easier work.
4. **Missed quiz question:** immediate remedial review on that topic.
5. **Conditionally completed topics** (from the diagnostic): one failure **revokes credit quickly**. These are topics the student only just placed out of.
6. **The bar is never lowered.** There are no hints and no "explain it differently". Content is fixed when more than a sliver of students fail: an intermediate KP is added or the topic is split.

## 10. Diagnostic

From [MAW ch.30, FAQ], [AI]:
- **Size:** 20–40 questions for lower-grade courses and 40–60 for higher ones. A naive bottom-up approach would need 500+.
- **Inference rules:**
  - A correct answer adds to the topic and **all its ancestors** (prerequisites, transitively).
  - An incorrect answer subtracts from the topic and **all its descendants** (post-requisites).
  - A correct answer on a leaf topic also gives credit to other leaf topics in the same module.
- **Graph compression beforehand:** keep the smallest set of topics that "covers" the course. A topic counts as covered if it has both an ancestor and a descendant in the compressed set, each within **3 prerequisite edges**.
- **Plus-minus balance per topic:**
  - Each answer has weight 1 by default.
  - A **slow correct answer gets a smaller weight**, which handles accuracy-vs-time conflicts.
  - The sign of the balance is the prediction (known or not) and its magnitude is the confidence.
  - At the end, topics with a positive balance are **credited with repNum = balance**.
  - Topics that only just come out positive are **conditionally completed**.
- **Question choice:** the *simplest* question that would convince an expert of mastery **and** exercises all of the topic's prerequisites. Not the hardest question, because silly mistakes get more likely.
- **Supplemental diagnostics:** a handful of questions when the graph changes and a topic loses its evidence.

## 11. XP calibration and gamification

From [MAW ch.22, 31, FAQ]:
- **1 XP ≈ 1 minute of fully focused, productive work for an average serious-but-imperfect student *at that level of math*.** Faster students earn more than 1 XP per minute. The XP/minute rate falls at higher levels because the reference population gets stronger.
- **Award tiers:**
  - **bonus** XP for perfect performance;
  - **full** XP for nearly perfect;
  - **most** XP for passable;
  - **a little** XP for nearly passable;
  - **0** XP for poor;
  - **negative** XP for blowing off a task (rushing or guessing).
  - Penalties are calibrated to how frustrated a tutor sitting next to the student would be. After penalties were introduced, adversarial students' pass rates rose from under 50% to over 90%.
- **Benchmarks:**
  - baseline pace is 40 XP per weekday, and a minimum of 15 is recommended;
  - a typical course is about 3000 XP (prealgebra about 2000, precalculus about 4000, AP Calc BC about 6000).
- **Warning signs:**
  - failing more than 1 in 10 lessons;
  - any negative XP;
  - earning half an XP per minute or less.
- **Progress ≠ XP.** Progress (the % of topics completed) rises only when a lesson is passed. Course XP changes with performance because SR speeds up or slows down.

## 12. Notes specific to an Axler-style (proof-heavy) chapter

- Math Academy itself does procedural fluency first and proofs in a later course. It calls Axler effectively a second linear algebra course ([MAW FAQ]).
- **Encompassings are sparse in proof-based math.** Skycak's own example: using Av = λv inside a proof does not practise *computing* eigenvectors. Likewise 2×2 diagonalization gives only fractional credit towards a characteristic-equation topic that is mostly 3×3 ([MAW FAQ]).
- Expect **less compression and more explicit reviews**. Set weights conservatively, w = 0 is fine, and don't infer encompassings from prerequisites.
- Math Academy explicitly avoids LLM dialogue and uses fixed, bite-size explanations at the right moment ([MAW FAQ]). For this app that means: use the LLM **offline, to author** KPs, worked examples, question variants and graph edges; at runtime use deterministic grading wherever possible.

---

## 13. Minimal faithful implementation for an MVP (one chapter, 10–25 micro-skills)

### 13.1 Data model (SQLite)

```
topic(id, chapter_id, slug, title, summary, section_ref, page_span,
      is_core BOOL, cluster TEXT,            -- section/module, used for breadth and diagnostic
      confusable_group TEXT NULL,            -- non-interference tag (LLM-proposed, human-checked)
      difficulty_prior REAL DEFAULT 1.0,     -- stands in for population difficulty, range 0.8–1.25
      est_lesson_xp INT)

knowledge_point(id, topic_id, ord, intro_md, worked_example_md,   -- intro = minimum effective dose (≤ ~200 words)
      subgoal_labels JSON, est_minutes_per_q REAL)
kp_key_prereq(kp_id, prereq_topic_id)          -- for targeted remediation

question(id, kp_id, topic_id, kind,            -- numeric | mcq | matrix | expr | proof_step | short_llm
      stem_md, answer JSON, solution_md, difficulty INT, est_minutes REAL,
      usable_in JSON)                          -- lesson / review / quiz / diagnostic
                                               -- target ≥ 10 variants per KP

prereq_edge(topic_id, prereq_id, is_direct BOOL)
encompass_edge(advanced_id, simpler_id, weight REAL CHECK(weight BETWEEN 0 AND 1),
      rationale TEXT, reviewed BOOL)           -- only along direct/key prereq edges

student_topic(topic_id PK, status,             -- locked | available | in_lesson | learned | cooldown
                                               --   | relearn | conditional
      rep_num REAL DEFAULT 0, memory REAL DEFAULT 0, last_update TS,
      acc REAL NULL, speed REAL DEFAULT 1,
      lesson_attempts INT, last_fail_kp_ord INT NULL, max_kp_reached INT,
      cooldown_until TS NULL, not_before TS NULL)

task(id, type,                                 -- lesson | review | remedial | quiz | quiz_retake
      topic_ids JSON, created_at, status, menu_slot INT,
      score REAL, raw_delta REAL, xp_awarded INT, started_at, finished_at)
answer(id, task_id, question_id, topic_id, kp_id, correct BOOL, seconds REAL, ts)   -- append-only
meta(xp_total, xp_since_quiz, consecutive_reviews, lessons_done, tasks_done)
```

**Event log:** keep `answer` and `task` append-only, and make `student_topic` a pure fold over them. Then you can change any parameter and rebuild state by replay. This matters because the parameters below are guesses.

### 13.2 Parameters (all are DESIGN CHOICES unless a source is given)

| Name | Value | Note |
|---|---|---|
| DUE | 0.5 | A topic is due when memory ≤ 0.5, i.e. exactly one interval after a full refresh |
| interval(r) | `max(1, 2·2.5^(r−1))` days | r = 1→2 d, 2→5 d, 3→12.5 d, 4→31 d, 5→78 d, 6→195 d. Fits "weeks then months" at speed about 2 |
| DECAY_MAX | 3 | decay = clamp(days/interval, 1, 3) |
| speed | `clamp(2^((acc−0.80)/0.15) / difficulty_prior, 0.5, 2)` | acc 0.95→2×, 0.80→1×, 0.65→0.5× |
| acc EWMA α | 0.25 | Propagated updates are scaled by edge weight |
| KP rule | pass on 2 consecutive correct; fail after 5 questions without passing | [HIW], [MAW] |
| Review | 4 questions (3–5), spread across KPs | [FIRe] |
| Quiz | 6 questions, about 1.5 min each, every **100 XP** | Math Academy uses 150 XP; scaled down because a chapter is only about 300–600 XP |
| Lesson share | force a lesson after 3 consecutive reviews; ≥ 25% lessons | [MAW FAQ, ch.22] |
| MIN_W | 0.1 | Ignore weaker flow paths |
| KNOCK_MIN | 0.5 | Effective implicit credit at or above this counts as a knock-out |
| Menu size | 4 tasks, persistent | |

### 13.3 Core state functions

```python
def mem_now(s, now):
    return s.memory * 0.5 ** (days(now - s.last_update) / interval(s.rep_num))

def early(m):                        # 0 when fresh, 1 at or after due  (published: discount if memory is high)
    return clamp((1 - m) / (1 - DUE), 0, 1)

def apply(s, raw, now):              # one credit/penalty event on one topic
    m = mem_now(s, now)
    if raw > 0:
        raw *= early(m)
        s.rep_num += s.speed * raw
    else:
        decay = clamp(days(now - s.last_update) / interval(s.rep_num), 1, DECAY_MAX)
        s.rep_num = max(0, s.rep_num + s.speed * decay * raw)
    s.memory = clamp(m + raw, 0, 1)
    s.last_update = now

# W_down[a][s]: max over paths of the product of encompassing weights (a encompasses s).
# Precompute by DFS on the DAG; with ≤ 25 nodes this is trivial. W_up is the transpose.
def fire(topic, raw, now):
    apply(st[topic], raw, now)
    if raw > 0:                                    # credit flows DOWN
        for s, w in W_down[topic].items():
            t = st[s]
            if t.status in LEARNED and t.speed >= 1.0 and w >= MIN_W:   # speed gate (published)
                apply(t, raw * w, now)
    else:                                          # penalty flows UP
        for a, w in W_up[topic].items():
            if st[a].status in LEARNED and w >= MIN_W:
                apply(st[a], raw * w, now)

def on_answer(topic, correct, secs):               # ability tracking (published propagation directions)
    upd_acc(topic, correct, 1.0)
    for s, w in (W_down if correct else W_up)[topic].items():
        upd_acc(s, correct, w)                     # acc += α·w·(x − acc)
    recompute_speed(affected)

def init_acc(topic):                               # neighbourhood prediction
    nb = direct_prereqs ∪ key_prereqs ∪ encompassed ∪ same_cluster
    return mean(acc over nb with data) or 0.80
```

Path combination by max-product, and memory refresh by `m + raw`, are interpretations of the published formula, not confirmed by Math Academy.

### 13.4 Lesson runner

```python
def run_lesson(topic):
    for kp in topic.kps[resume_from:]:
        show(kp.intro_md, kp.worked_example_md)    # minimum effective dose, then practice straight away
        streak = n = 0
        while streak < 2:
            if n == 5:
                return lesson_fail(topic, kp.ord)
            q = fresh_variant(kp)
            n += 1
            ok = grade(q)
            on_answer(topic, ok, ...)
            streak = streak + 1 if ok else 0
    score = correct / asked
    raw = 1.25 if score == 1 else 1.0 if score >= 0.8 else 0.75     # mirrors the XP tiers
    st[topic].status = LEARNED
    fire(topic, raw, now)            # lesson = first repetition; also gives implicit credit downward (layering)
    unlock_children(topic)

def lesson_fail(topic, kp_ord):
    s = st[topic]
    plateau = s.last_fail_kp_ord is not None and kp_ord <= s.last_fail_kp_ord
    s.last_fail_kp_ord = kp_ord
    s.status = COOLDOWN
    s.cooldown_until = now + 1 day          # and require ≥ 2 other tasks first
    if plateau:
        for p in key_prereqs(topic.kps[kp_ord]):
            enqueue(RemedialReview(p))
            # if that remedial review fails: st[p].status = RELEARN (peel back one level)
```

### 13.5 Review grading (order-sensitive)

```python
def grade_review(results):                   # list of bools in answer order
    w = range(1, n + 1)                       # later answers weigh more
    score = Σ w_i·c_i / Σ w_i
    if score >= 0.6:
        raw = 0.75 + 0.5 * (score - 0.6) / 0.4                  # 0.75 … 1.25
    else:
        raw = -0.5 - 0.5 * (0.6 - score) / 0.6                  # −0.5 … −1.0
        st[t].not_before = now + 12h                            # no immediate re-review loop
    fire(t, raw, now)
```

Worked check: ✖✔✖✔✔ gives 11/15 = 0.73, a pass. ✔✔✖✔✖ gives 7/15 = 0.47, a fail. This matches the source's examples.

### 13.6 Quiz

```python
def build_quiz():
    pool = learned topics, weighted by (1 - mem_now) + 0.5·[no explicit practice in 7 days]
    pick 6 distinct topics (spread across clusters)
    difficulty: harder variants if last quiz score > 0.8, easier if < 0.8   # target ~80%
    timed (~1.5 min/question), no reference shown

def grade_quiz():
    for each answer:
        correct   -> fire(topic, +0.3, now)
        incorrect -> fire(topic, -0.5, now); enqueue(RemedialReview(topic), priority=TOP)
    if score < 0.6:
        schedule QuizRetake(same topics, new variants) after remedials + 1 day
```

### 13.7 Task selector (simplified compression)

```python
def refresh_menu(now):
    menu = [t for t in menu if not t.done]                       # persistent menu
    if xp_since_quiz >= 100 and n_learned >= 4:
        add(Quiz)
    add(all pending RemedialReview)                               # highest priority
    D = {t ∈ learned : mem_now(t) <= DUE and now >= t.not_before}
    F = {t : status ∈ {available, relearn} and all prereqs learned and now >= cooldown_until}
    cands = [Lesson(t) for t in F] + [Review(t) for t in learned]   # a not-yet-due review may knock out due ones
    while len(menu) < 4 and cands:
        force_lesson = consecutive_reviews >= 3 or lesson_share < 0.25
        best = argmax(score(c, D, menu) for c in cands if not (force_lesson and c.is_review))
        if best is None or best.score <= 0:
            break
        menu.append(best)
        D -= knocked(best, D)
        cands.remove(best)
    for d in D:                                   # safeguard: never wait for a knock-out
        add(Review(d))
    drop reviews whose topic is knocked out by a Lesson already in the menu

def knocked(c, D):
    return {d ∈ D : d == c.topic or (W_down[c.topic][d] * early(mem_now(d)) >= KNOCK_MIN
                                     and st[d].speed >= 1)}

def score(c, D, menu):
    k = |knocked(c, D)|
    fend = Σ W_down[c.topic][s] for s learned with DUE < mem_now(s) < 0.65   # holds off upcoming reviews
    if c.is_review and k == 0:
        return -inf                               # explicit reviews only when they clear due work
    imp = (descendants_in_chapter(c.topic) / N) + 0.5·is_core if c.is_lesson else 0
    breadth = -0.5 · max(0, depth(c.topic) - median_frontier_depth - 1)       # climb evenly
    interf = -3 if c.is_lesson and c.topic.confusable_group in groups(lessons in menu
                                                                   or done in last 24h) else 0
    same_cluster = -1 if c.topic.cluster == last_task.cluster else 0          # macro-interleaving
    return 10·k + 2·fend + 3·imp + breadth + interf + same_cluster
```

### 13.8 XP

- Estimate XP per task as the sum of expected question-minutes. Each question's `est_minutes` starts as an LLM estimate. A lesson is about 3 questions per KP plus reading time; a review is 4 questions; a quiz is 6.
- **Award by tier** (DESIGN CHOICE): perfect ×1.2, ≥ 0.8 ×1.0, pass ×0.8, near-pass ×0.25, poor 0, rushed (e.g. wrong answers in under 15% of `est_minutes`) −0.25 × XP.
- **Calibration:** every 20 tasks, rescale `est_minutes` for each question kind so your median XP per *active* minute is about 1. Measure active time with a 2-minute idle cutoff.
- Show **progress** as % of chapter topics learned, separately from XP.

### 13.9 Optional mini-diagnostic (only if the learner already knows part of the chapter)

```python
# plus-minus balance; ask about 6–10 questions out of 25 topics
pick the most informative topic: the one with the most still-uncertain ancestors + descendants
correct (weight 1, reduced if slow) -> +w to the topic and all its ancestors
incorrect                           -> -w to the topic and all its descendants
stop when every |balance| >= 1, or the budget is used up
balance > 0 -> learned, rep_num = balance; if balance < 1 -> status CONDITIONAL
                                            (one later failure resets it to available)
```

### 13.10 Content and graph authoring (LLM offline, human check)

- **Micro-skill granularity.** One micro-skill (topic) is something a student can be asked to *do* and that can be graded automatically. Give each topic 2–4 KPs, each with a short intro, one worked example with subgoal labels, and **at least 10 question variants**.
- **Key prerequisites.** Tag them per KP.
- **Encompassing weights.** Propose them **only along direct and key prerequisite edges**, using the rubric "fraction of advanced-topic problems that fully execute the simpler skill". Use the buckets {0, 0.25, 0.5, 0.75, 1}, store a rationale, and default to 0 for "conceptual-only" use (typical in proofs).
- **Confusable groups.** Tag pairs like span vs. linear independence, or sum vs. direct sum.

### 13.11 Defer

- Population topic difficulty (use `difficulty_prior`).
- Exact set-cover or look-ahead optimisation (greedy is enough at 25 nodes).
- Non-ancestor encompassings and mastery floors (only matter across chapters or courses).
- Supplemental diagnostics.
- Core-topic detection (use the LLM `is_core` flag).
- Multisteps (later, as chapter capstones of 8–12 questions).
- Leagues and leaderboards, and anti-gaming penalties beyond the simple "rushed" rule.
- Test-prep mode.
- Explicit automaticity or speed checks outside quizzes.
- Dashboard persistence subtleties.
- Fitting the interval and decay parameters to your own data (do this once the answer log has a few hundred reviews).

### 13.12 Acceptance tests

1. **Full-chain knock-out.** Chain A ⊂ B ⊂ C, all w = 1, all three due. Exactly one task, Review(C), is offered. Passing it clears all three.
2. **Upward penalty.** Fail a review on A. B and C lose rep_num and memory in proportion to w, and may become due.
3. **Speed gate.** Set acc(A) to 0.6, so speed < 1. Then C's credit does not reach A, and A still needs an explicit review.
4. **Early discount.** Review a topic one day after its lesson. rep_num barely increases (early ≈ 0).
5. **Plateau remediation.** Fail a lesson twice at KP 2. Remedial reviews appear on KP 2's key prerequisites.
6. **Backlog throttle.** With 10 due reviews and frontier lessons available, the menu never shows more than 3 reviews in a row.
7. **Missed quiz question.** A missed question creates a top-priority remedial review.
8. **Deterministic replay.** Rebuilding state from the `answer` log reproduces `student_topic` exactly.