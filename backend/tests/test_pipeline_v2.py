"""Lesson generation v2 (offline, fake LLM): plan validation, split + rewiring + order/reduction + progress, refusal
with learner history, v2 persistence and the lesson view (no answers leaked), the revision loop, true_false_set and
the question-format rule, regeneration of a v2 lesson, and the replan CLI's server guard."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import networkx as nx
import pytest
from test_pipeline_build import build, llm_calls, tiny_course  # noqa: F401  (fixture)

from app import db, fake_llm
from app.llm_schemas import (
    BridgeIssue,
    DependentLink,
    InlineCheck,
    LessonReview,
    LessonV2Draft,
    PlannedPrereq,
    QuestionDraft,
    TeachingPlan,
)
from app.pipeline import jobs
from app.pipeline import lesson_v2 as lesson_v2_module
from app.pipeline.context import key_number, load_course
from app.pipeline.lesson_v2 import (
    InvalidPlan,
    PlanGraph,
    blocked_locations,
    blocking_count,
    clean_inline_check,
    drop_blocked_questions,
    idea_lines,
    quality_problems,
    learner_misses,
    needs_revision,
    plan_request,
    render_lesson,
    review_lines,
    review_request,
    review_summary,
    validate_plan,
    validate_v2,
)
from app.pipeline.lessons import InvalidLesson
from app.pipeline.questions import InvalidQuestion, choice_share, clean_question, format_warnings
from app.pipeline.replan import SplitRefused, apply_split, check_plan, plan_replan, replan, report_lines
from app.views import lesson_view

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def built_course(tmp_path: Path) -> str:
    cid = tiny_course(tmp_path)
    assert build(cid).state == "done"
    return cid


def skill_by_title(cid: str, prefix: str) -> dict:
    row = db.query_one("SELECT * FROM skills WHERE course_id = ? AND title LIKE ?", (cid, prefix + "%"))
    assert row is not None, prefix
    return row


def key_of(cid: str) -> dict[str, str]:
    return {s["id"]: s["key"] for s in db.query("SELECT id, key FROM skills WHERE course_id = ?", (cid,))}


def edge_keys(cid: str) -> set[tuple[str, str]]:
    keys = key_of(cid)
    return {(keys[e["src"]], keys[e["dst"]]) for e in db.query("SELECT src, dst FROM edges WHERE course_id = ?", (cid,))}


def fake_plan(text: str) -> TeachingPlan:
    return fake_llm.respond(schema=TeachingPlan, role="graph", instructions="", text=text)


def split_plan() -> TeachingPlan:
    return fake_plan("Plan the teaching of skill S2: Multiply complex numbers and use their powers\nTaught on: <<page 1 · p.2>>")


def graph(**kw) -> PlanGraph:
    base = dict(
        key="S2",
        keys={"S1", "S2", "S3", "S4", "S5", "S9"},
        prereqs={"S1": 0.5, "S9": 0.25},
        dependents={"S3": 0.75, "S5": 0.5},
        descendants={"S3", "S4", "S5"},
        pages={0, 1, 2},
    )
    return PlanGraph(**(base | kw))


def question(**kw) -> QuestionDraft:
    base = dict(
        key="k1q1", tier=1, kind="true_false_set", prompt="Mark each equality true or false.",
        choices=["$(1,9)=(9,1)$", "$\\{1,9\\}=\\{9,1\\}$", "$(1,9)=(1,9)$"], correct_choice=None, answer="FTT",
        answer_display="F, T, T", tolerance=None, hints=["a", "b", "c"], solution="1 false; 2 true; 3 true.",
        checklist=[], targets="", source_exercise=None, steps=[],
    )
    return QuestionDraft(**(base | kw))


# ---------------------------------------------------------------------------
# true_false_set and the question-format rule (all generators)
# ---------------------------------------------------------------------------


def test_true_false_set_validation():
    q = clean_question(question(answer="F, T, t", correct_choice=1, tolerance=0.5, checklist=["x"]))
    assert (q.answer, q.correct_choice, q.tolerance, q.checklist) == ("FTT", None, None, [])
    for bad, reason in [
        (dict(answer="FT"), "one letter T or F per statement"),
        (dict(answer="FTX"), "one letter T or F per statement"),
        (dict(choices=["only one"], answer="T"), "2-5 non-empty statements"),
        (dict(choices=list("abcdef"), answer="TTTFFF"), "2-5 non-empty statements"),
        (dict(choices=["a", "a", "b"]), "duplicate statements"),
        (dict(solution=""), "explains each statement"),
    ]:
        with pytest.raises(InvalidQuestion, match=reason):
            clean_question(question(**bad))


def test_counting_true_statements_is_rejected():
    counting = question(kind="numeric", choices=[], answer="2", prompt="How many of these equalities are true? 1. ...")
    with pytest.raises(InvalidQuestion, match="use true_false_set"):
        clean_question(counting)
    with pytest.raises(InvalidQuestion, match="use true_false_set"):
        clean_question(counting.model_copy(update={"prompt": "How many of the following lists belong to $\\mathbf{F}^2$?"}))
    assert clean_question(counting.model_copy(update={"prompt": "How many elements does $\\{1,2,2\\}$ have?"})).answer == "2"


def test_order_free_tuple_answers_become_sets():
    roots = question(kind="expression", choices=[], answer="(1+i, -1-i)", prompt="Find both square roots of $2i$. Enter them in any order.")
    assert clean_question(roots).answer == "{1+i, -1-i}"
    assert clean_question(roots.model_copy(update={"prompt": "Enter the list as $(x, y)$."})).answer == "(1+i, -1-i)"
    single = roots.model_copy(update={"answer": "(1+2*i)", "prompt": "Compute it; factors in either order."})
    assert clean_question(single).answer == "(1+2*i)"


def test_choice_share_counts_every_true_false_set():
    three = clean_question(question())
    two = clean_question(question(choices=["$1=1$", "$1=2$"], answer="TF", answer_display="T, F"))
    typed = clean_question(question(kind="numeric", choices=[], answer="3", prompt="Compute $1+2$."))
    mc = clean_question(question(kind="multiple_choice", choices=["a", "b", "c"], correct_choice=0, answer=""))
    # each statement scores on its own (2026-10-11): marking at random earns half — every set is a choice question
    assert choice_share([[three, typed]]) == 0.5
    assert choice_share([[two, typed]]) == 0.5
    assert choice_share([[mc, three, three, typed]]) == 0.75
    assert not any("multiple choice" in n for n in format_warnings([[three, typed, typed]]))
    assert any("same truth value" in n for n in format_warnings([[clean_question(question(answer="TTT", answer_display="T, T, T"))]]))


def test_question_format_rule_is_in_every_question_generating_prompt():
    from app.prompts.openai import (
        COUNT_QUESTION_RULE,
        LESSON_INSTRUCTIONS,
        ONE_ANSWER_RULE,
        TOPUP_INSTRUCTIONS,
        UNORDERED_RULE,
        VERIFY_INSTRUCTIONS,
    )
    from app.prompts.openai import REVIEWER_INSTRUCTIONS, WRITER_INSTRUCTIONS

    assert "How many of these equalities are true?" in COUNT_QUESTION_RULE and '"FTT"' in COUNT_QUESTION_RULE
    for prompt in (LESSON_INSTRUCTIONS, TOPUP_INSTRUCTIONS, WRITER_INSTRUCTIONS):
        assert COUNT_QUESTION_RULE in prompt and "true_false_set" in prompt
        assert ONE_ANSWER_RULE in prompt and UNORDERED_RULE in prompt
    assert WRITER_INSTRUCTIONS.count(COUNT_QUESTION_RULE) >= 2  # practice questions and inline checks
    assert WRITER_INSTRUCTIONS.count(ONE_ANSWER_RULE) >= 2
    assert "true_false_set" in VERIFY_INSTRUCTIONS and "How many of these are true" in REVIEWER_INSTRUCTIONS


def test_v2_prompts_follow_the_design():
    from app.prompts.openai import GRAPH_INSTRUCTIONS
    from app.prompts.openai import PLANNER_INSTRUCTIONS, REFERENCE_EXAMPLE, REVIEWER_INSTRUCTIONS, WRITER_INSTRUCTIONS

    principle = "Reduce the number of new ideas the learner must handle at once; do not reduce the reasoning that connects"
    for prompt in (PLANNER_INSTRUCTIONS, WRITER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS):
        assert principle in prompt
    # skills are outcomes (the learner's S3b review): checking / evaluating is a step inside the task, a proof is a task
    assert "Checking a supplied answer and constructing an answer are different" not in PLANNER_INSTRUCTIONS
    for prompt in (PLANNER_INSTRUCTIONS, GRAPH_INSTRUCTIONS):
        assert "SKILLS ARE OUTCOMES" in prompt and "a proof is an outcome" in prompt
    assert "separate OUTCOMES" in PLANNER_INSTRUCTIONS and "A step on the way to the node's outcome stays" in PLANNER_INSTRUCTIONS
    assert REFERENCE_EXAMPLE in WRITER_INSTRUCTIONS and "never copy its content" in WRITER_INSTRUCTIONS
    writer, reviewer = (" ".join(t.split()) for t in (WRITER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS))
    assert "the formula is a record of the steps" in writer and "Explain consequential transitions where they happen" in writer
    assert "This representation helps the learner notice ___" in writer and "Nothing essential in `deeper`" in writer
    assert "the smallest adequate repair" in reviewer and "No clarity scores" not in reviewer and "no clarity scores" in reviewer
    # density (the learner's S3b review): one new idea per block, one method, teach before use, a pass that checks it
    assert "DENSITY — ONE NEW IDEA AT A TIME" in writer and "One method, shown one way" in writer
    assert "Teach before use" in writer and "Recall in one line" in writer and "Every misconception the lesson names" in writer
    assert "Second pass (density and outcome, `density_issues`)" in reviewer and "the outcome missing" in reviewer
    for prompt in (PLANNER_INSTRUCTIONS, WRITER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS):
        assert "AFTER THE THIRD ROUND" in prompt


def test_verification_lists_true_false_set_statements(tmp_path):
    from app.pipeline.verify import judge, verify_request
    from app.llm_schemas import IndependentAnswer

    row = {"kind": "true_false_set", "prompt": "Mark each.", "choices_json": db.dumps(["A", "B", "C"]), "answer": "FTT",
           "correct_choice": None, "tolerance": None}
    assert "1. A\n2. B\n3. C" in verify_request(row)
    agree = judge(row, IndependentAnswer(choice=None, answer="F T T", confidence=0.9, problems=""))
    assert not agree.flagged
    disagree = judge(row, IndependentAnswer(choice=None, answer="TTT", confidence=0.9, problems=""))
    assert disagree.flagged and "'TTT'" in disagree.reason


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def test_split_plan_is_normalized_against_the_graph():
    plan = split_plan()
    skills = [s.model_copy(update={"local_id": lid}) for s, lid in zip(plan.skills, ["x", "y", "z"])]
    skills[0] = skills[0].model_copy(update={"prerequisites": [
        PlannedPrereq(key="S1", weight=7), PlannedPrereq(key="S3", weight=0.5),  # S3 depends on S2: a cycle
        PlannedPrereq(key="S2", weight=0.5), PlannedPrereq(key="nope", weight=0.5), PlannedPrereq(key="y", weight=0.5),
    ]})
    skills[1] = skills[1].model_copy(update={"prerequisites": [PlannedPrereq(key="x", weight=1)]})
    skills[2] = skills[2].model_copy(update={"prerequisites": [PlannedPrereq(key="S2b", weight=0.75)]})  # future key
    plan = plan.model_copy(update={"skills": skills, "dependents": [
        DependentLink(dependent_key="S3", needs=["x", "Z"]), DependentLink(dependent_key="S4", needs=["x"]),
    ]})
    validated, notes = validate_plan(plan, graph())
    a, b, c = validated.skills
    assert [s.local_id for s in validated.skills] == ["a", "b", "c"]
    assert [(p.key, p.weight) for p in a.prerequisites] == [("S1", 1.0), ("S9", 0.25)]  # S9 was forgotten: added
    assert [p.key for p in b.prerequisites] == ["a"] and [p.key for p in c.prerequisites] == ["b"]
    assert {d.dependent_key: d.needs for d in validated.dependents} == {"S3": ["a", "c"], "S5": ["a", "b", "c"]}
    text = "\n".join(notes)
    for expected in ("dropped prerequisite S3", "the original skill", "unknown key 'nope'", "not an earlier sibling",
                     "dropped the original prerequisite S9", "ignored a mapping for 'S4'", "did not map dependent S5"):
        assert expected in text, expected


def test_plan_decision_rules():
    plan = split_plan()
    with pytest.raises(InvalidPlan, match="not allowed"):
        validate_plan(plan, graph(), allow_split=False)
    kept, notes = validate_plan(plan.model_copy(update={"decision": "keep"}), graph())
    assert kept.decision == "keep" and len(kept.skills) == 1 and kept.dependents == [] and "kept the first" in notes[0]
    single, _ = validate_plan(plan.model_copy(update={"skills": plan.skills[:1]}), graph())
    assert single.decision == "keep"
    with pytest.raises(InvalidPlan, match="2–5"):
        validate_plan(plan.model_copy(update={"skills": plan.skills * 2}), graph())
    with pytest.raises(InvalidPlan, match="no skills"):
        validate_plan(plan.model_copy(update={"skills": []}), graph())


def test_split_keys_sort_after_their_parent():
    assert sorted(["S10", "S2b", "S3", "S2a", "S1"], key=lambda k: (key_number(k), k)) == ["S1", "S2a", "S2b", "S3", "S10"]


# ---------------------------------------------------------------------------
# Split: rewiring, order, reduction, progress, persistence, lesson view
# ---------------------------------------------------------------------------


def test_split_rewires_the_graph_and_writes_v2_lessons(tmp_path, llm_calls):
    cid = built_course(tmp_path)
    original = skill_by_title(cid, "Add and multiply")
    key = original["key"]
    keys = key_of(cid)
    old_prereqs = {keys[e["src"]] for e in db.query("SELECT src FROM edges WHERE dst = ?", (original["id"],))}
    old_dependents = {keys[e["dst"]]: e["weight"] for e in db.query("SELECT dst, weight FROM edges WHERE src = ?", (original["id"],))}
    assert old_dependents  # the fake graph has dependents of this skill
    # learner progress elsewhere (must survive untouched) and an unanswered, active task on the original
    other = skill_by_title(cid, "Distinguish lists")
    from datetime import UTC, datetime

    from fsrs import Card

    card = Card(card_id=1, due=datetime.now(UTC)).to_json()
    db.execute("UPDATE progress SET status = 'learned', card_json = ?, lesson_attempts = 2 WHERE skill_id = ?", (card, other["id"]))
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, state_json, created_at) VALUES (?, ?, ?, 'lesson', 'active', '{}', ?)",
        (db.new_id(), cid, original["id"], db.now_iso()),
    )
    before = {r["skill_id"]: dict(r) for r in db.query("SELECT * FROM progress WHERE course_id = ? AND skill_id != ?", (cid, original["id"]))}
    llm_calls.clear()

    report = asyncio.run(replan(cid, key))

    assert report.decision == "split" and list(report.keys.values()) == [f"{key}a", f"{key}b", f"{key}c"]
    assert report.deleted_tasks == 1 and all(r.ok for r in report.results)
    assert db.query_one("SELECT 1 FROM skills WHERE id = ?", (original["id"],)) is None
    for table in ("questions", "progress", "tasks"):
        assert not db.query(f"SELECT 1 FROM {table} WHERE skill_id = ?", (original["id"],))
    # edges: spec prerequisites + the dependents mapping (original weights), every original link represented
    edges = edge_keys(cid)
    a, b, c = (f"{key}{x}" for x in "abc")
    assert {(a, b), (b, c)} <= edges
    assert all(any((p, n) in edges for n in (a, b, c)) for p in old_prereqs)
    for i, (dep, weight) in enumerate(sorted(old_dependents.items(), key=lambda kv: (key_number(kv[0]), kv[0]))):
        needs = [a] if i % 2 == 0 else [b, c]
        assert all((n, dep) in edges for n in needs)
        rows = db.query(
            "SELECT e.weight FROM edges e JOIN skills s ON s.id = e.src JOIN skills d ON d.id = e.dst WHERE d.key = ? AND s.key IN (?, ?, ?)",
            (dep, a, b, c),
        )
        assert rows and all(r["weight"] == weight for r in rows)
    # order: a topological order over the whole course, 0..n-1; reduced = transitive reduction
    skills = db.query("SELECT id, key, order_index FROM skills WHERE course_id = ? ORDER BY order_index", (cid,))
    assert [s["order_index"] for s in skills] == list(range(len(skills)))
    order = {s["key"]: s["order_index"] for s in skills}
    g = nx.DiGraph(list(edges))
    assert all(order[u] < order[v] for u, v in g.edges)
    reduction = nx.transitive_reduction(g)
    keys = key_of(cid)
    for e in db.query("SELECT * FROM edges WHERE course_id = ?", (cid,)):
        assert bool(e["reduced"]) == reduction.has_edge(keys[e["src"]], keys[e["dst"]])
    # progress: new rows; the learner's other rows untouched
    after = {r["skill_id"]: dict(r) for r in db.query("SELECT * FROM progress WHERE course_id = ?", (cid,))}
    assert all(after[sid] == row for sid, row in before.items())
    statuses = {keys[sid]: r["status"] for sid, r in after.items()}
    assert statuses[a] == ("available" if not old_prereqs else statuses[a]) and statuses[b] == "locked"
    # lessons: v2, questions inserted and verified, the job is done
    for local, sid in report.skill_ids.items():
        row = db.query_one("SELECT * FROM skills WHERE id = ?", (sid,))
        lesson = db.loads(row["lesson_json"])
        assert row["lesson_status"] == "ready" and lesson["format"] == "v2" and db.loads(row["viz_json"]) == {"format": "none"}
        assert lesson["plan"]["local_id"] == local and lesson["review"]["issues"] == []
        assert lesson["initial_review"]["issues"][0]["severity"] == "blocking"
        assert row["summary"] == lesson["goal_line"] and not lesson["goal_line"].lower().startswith("your goal")
        questions = db.query("SELECT * FROM questions WHERE skill_id = ?", (sid,))
        assert questions and all(db.loads(q["verify_json"]) for q in questions)
        assert not any(q["flagged"] for q in questions)
    assert llm_calls["LessonV2Draft"] == 6 and llm_calls["LessonReview"] == 6 and llm_calls["TeachingPlan"] == 1
    job = jobs.get_job(cid)
    assert job.state == "done" and any(f"Split {key} into" in line for line in job.log)
    assert any("Edges:" in line for line in report_lines(report))


def test_v2_lesson_view_never_leaks_check_answers(tmp_path):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Add and multiply")["key"]
    report = asyncio.run(replan(cid, key))
    sid = report.skill_ids["a"]
    view = lesson_view(sid)
    assert view.format == "v2" and view.goal_line and view.deeper and view.visualization is None
    assert view.why_it_matters == "" and view.idea == "" and view.missing_steps == ""
    assert view.assumed and view.out_of_scope == ["Square roots and cube roots", "Exponent rules"]
    assert [b.index for b in view.blocks] == list(range(len(view.blocks)))
    kinds = {b.check.kind for b in view.blocks if b.check}
    assert {"expression", "true_false_set", "reflect"} <= kinds
    tf = next(b.check for b in view.blocks if b.check and b.check.kind == "true_false_set")
    assert tf.choices == ["$i^2=-1$", "$(3i)(2i)=6i$", "$(1+i)(1-i)=2$"]
    visual = next(b.visualization for b in view.blocks if b.visualization)
    assert visual.format == "equation_steps" and len(visual.steps) == 3
    dumped = view.model_dump_json()
    for secret in ('"answer"', "1+7*i", "TFT", "$1+7i$", "two factors of $i$, and", "correct_choice", "answer_display"):
        assert secret not in dumped, secret
    assert all(set(b.check.model_dump()) == {"kind", "prompt", "choices", "scored", "second_try", "state"} for b in view.blocks if b.check)
    assert all(not b.check.scored and b.check.state is None for b in view.blocks if b.check)  # not a guided proof lesson
    assert view.guided is None and view.blocks_total == len(view.blocks)
    assert view.knowledge_points and all(kp.question_count >= 2 for kp in view.knowledge_points)
    assert {p.key for p in view.unlocks} or {p.key for p in view.prerequisites} is not None
    # the learning agent's check endpoint reads the stored block (answers stay server side)
    stored = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
    faded = next(b for b in stored["blocks"] if b["kind"] == "faded_example")
    assert faded["check"]["answer"] == "1+7*i" and faded["check"]["explanation"]


def test_split_is_refused_while_the_original_has_learner_history(tmp_path, llm_calls):
    cid = built_course(tmp_path)
    original = skill_by_title(cid, "Add and multiply")
    key, sid = original["key"], original["id"]
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, state_json, created_at) VALUES (?, ?, ?, 'lesson', 'active', '{}', ?)",
        (task_id, cid, sid, db.now_iso()),
    )
    answered = db.query_one("SELECT id, key FROM questions WHERE skill_id = ? ORDER BY rowid", (sid,))
    db.execute(
        "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, created_at) VALUES (?, ?, ?, ?, 1, 'correct', ?)",
        (db.new_id(), task_id, answered["id"], sid, db.now_iso()),
    )
    with pytest.raises(SplitRefused, match="1 answered attempt"):
        apply_split(cid, key, split_plan())
    with pytest.raises(SplitRefused):
        check_plan(cid, key, split_plan())
    planned = asyncio.run(plan_replan(cid, key))
    assert not planned.split_allowed and planned.plan.decision == "keep" and "cannot be split" in planned.notes[0]

    report = asyncio.run(replan(cid, key, plan=split_plan()))  # a saved split plan falls back to a kept skill
    assert report.decision == "keep" and report.keys == {"a": key} and report.results[0].ok
    row = db.query_one("SELECT * FROM skills WHERE id = ?", (sid,))
    assert db.loads(row["lesson_json"])["format"] == "v2"
    old = db.query_one("SELECT * FROM questions WHERE id = ?", (answered["id"],))
    assert old["flagged"] == 1 and old["key"].startswith("old-")  # retired, never deleted
    assert db.query_one("SELECT status FROM tasks WHERE id = ?", (task_id,))["status"] == "completed"
    assert report.results[0].abandoned_tasks == 1
    # a voided attempt is no history: it does not block a split
    db.execute("UPDATE attempts SET status = 'voided' WHERE question_id = ?", (answered["id"],))
    assert check_plan(cid, key, split_plan()).split_allowed


def test_no_split_keeps_the_skill_and_its_edges(tmp_path, llm_calls):
    cid = built_course(tmp_path)
    original = skill_by_title(cid, "Add and multiply")
    edges = edge_keys(cid)
    report = asyncio.run(replan(cid, original["key"], no_split=True))
    assert report.decision == "keep" and report.results[0].ok
    assert edge_keys(cid) == edges
    plan_text = None

    def capture(**kwargs):
        nonlocal plan_text
        if kwargs["schema"] is TeachingPlan:
            plan_text = kwargs["text"]
        return original_respond(**kwargs)

    original_respond = fake_llm.respond
    fake_llm.respond = capture
    try:
        asyncio.run(plan_replan(cid, original["key"], no_split=True))
    finally:
        fake_llm.respond = original_respond
    assert "Splitting: NOT allowed" in plan_text and "## Course map" in plan_text and "## Source pages" in plan_text


def test_no_revision_when_the_first_review_is_clean(tmp_path, llm_calls, monkeypatch):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    original = fake_llm.respond

    def clean_reviews(**kwargs):
        if kwargs["schema"] is LessonReview:
            kwargs = {**kwargs, "text": kwargs["text"].replace("(first draft)", "(clean)")}
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", clean_reviews)
    llm_calls.clear()
    report = asyncio.run(replan(cid, key))
    result = report.results[0]
    assert report.decision == "keep" and result.ok and not result.revised
    assert llm_calls["LessonV2Draft"] == 1 and llm_calls["LessonReview"] == 1
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (result.skill_id,))["lesson_json"])
    assert "initial_review" not in lesson and lesson["review"]["issues"] == []


def test_review_triggers_revision_rules():
    clean = LessonReview(issues=[], density_issues=[], untaught_method_exercises=[], out_of_scope_violations=[], optional_content_needed=[],
                         math_errors=[], split_recommended=False, split_reason="")
    assert not needs_revision(clean)
    for field in ("untaught_method_exercises", "out_of_scope_violations", "optional_content_needed", "math_errors"):
        assert needs_revision(clean.model_copy(update={field: ["x"]}))
    dense = BridgeIssue(location="Block 4", inference_required="a second layout", why_insufficient="two methods at once",
                        repair="move the second layout to deeper", severity="minor")
    assert needs_revision(clean.model_copy(update={"density_issues": [dense]}))
    with_density = clean.model_copy(update={"density_issues": [dense]})
    assert "1 density (0 blocking)" in review_summary(with_density)
    assert any(line.startswith("- [density, minor] Block 4") for line in review_lines(with_density))
    # reviews stored before the density pass existed still render
    legacy = clean.model_dump()
    del legacy["density_issues"]
    assert review_lines(legacy) == [] and review_summary(legacy).startswith("0 blocking")
    minor = fake_llm.respond(schema=LessonReview, role="graph", instructions="", text="(first draft)")
    assert needs_revision(minor)
    minor.issues[0].severity = "minor"
    assert needs_revision(minor)  # minor bridges (e.g. notation before introduction) are repaired too


def test_density_pass_sees_the_declared_new_ideas(tmp_path):
    draft = fake_llm.respond(schema=LessonV2Draft, role="lesson", instructions="", text="Write the lesson for skill S1: Multiply")
    declared = [b for b in draft.blocks if b.new_idea]
    assert declared and all(not b.new_idea for b in draft.blocks if b.kind in ("check", "faded_example", "summary"))
    lines = idea_lines(draft)
    assert lines[0] == f"{len(draft.blocks)} blocks; {len(declared)} declare a new idea (budget: about 4 per lesson)."
    assert len(lines) == 2 + len(declared) and lines[-1].startswith(f"Length: {len(draft.blocks)} blocks (at most 9;")
    spec = fake_llm.respond(schema=TeachingPlan, role="graph", instructions="", text="Plan the teaching of skill S1: Multiply").skills[0]
    text = review_request(spec, draft, key="S1", revised=False)
    assert "COUNTS FOR THE DENSITY PASS" in text and lines[1] in text and f"Starting point (the outcome problem" in text
    assert f"(declared new idea: {declared[0].new_idea})" in render_lesson(draft)


def test_the_planner_sees_the_learners_misses(tmp_path):
    cid = built_course(tmp_path)
    skill = skill_by_title(cid, "Add and multiply")
    sid = skill["id"]
    task_id = db.new_id()
    db.execute(
        "INSERT INTO tasks(id, course_id, skill_id, type, status, state_json, created_at) VALUES (?, ?, ?, 'lesson', 'completed', '{}', ?)",
        (task_id, cid, sid, db.now_iso()),
    )
    questions = db.query("SELECT * FROM questions WHERE skill_id = ? ORDER BY rowid", (sid,))
    typed = next(q for q in questions if q["kind"] in ("numeric", "expression"))
    choice = next((q for q in questions if q["kind"] == "multiple_choice"), None)

    def attempt(q, response, *, correct=0, independent=0, status="incorrect", hints=0, revealed=0):
        db.execute(
            "INSERT INTO attempts(id, task_id, question_id, skill_id, correct, status, independent, response, hints_used, "
            "revealed, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (db.new_id(), task_id, q["id"], sid, correct, status, independent, response, hints, revealed, db.now_iso()),
        )

    attempt(typed, "wrong-typed-answer")
    attempt(typed, "helped-answer", correct=1, independent=0, status="correct", hints=1)
    attempt(typed, "independent-right", correct=1, independent=1, status="correct")
    attempt(typed, "voided-answer", status="voided")
    if choice is not None:
        attempt(choice, str((choice["correct_choice"] + 1) % len(db.loads(choice["choices_json"], []))))
    lines = learner_misses(sid)
    text = "\n".join(lines)
    assert "wrong-typed-answer" in text and "(" + typed["kind"] + ", wrong)" in text
    assert "helped-answer" in text and "right with a hint" in text
    assert "independent-right" not in text and "voided-answer" not in text
    if choice is not None:  # a choice is shown as the option's text, not its index
        wrong = db.loads(choice["choices_json"], [])[(choice["correct_choice"] + 1) % len(db.loads(choice["choices_json"], []))]
        assert f"learner answered: {wrong[:50]}" in text
    # never reached in a quiz (no response): not a miss
    attempt(typed, "", status="incorrect")
    assert len(learner_misses(sid)) == len(lines)
    # a question flagged as faulty (its key may be wrong) is left out
    other = next(q for q in questions if q["id"] != typed["id"] and q["kind"] in ("numeric", "expression"))
    attempt(other, "answer-to-a-flagged-question")
    db.execute("UPDATE questions SET flagged = 1, flag_reason = 'learner: wrong key' WHERE id = ?", (other["id"],))
    assert "answer-to-a-flagged-question" not in "\n".join(learner_misses(sid))
    # a capstone answered right with one hinted step is no 'wrong'; each step shows the option text and the help used
    steps = [
        {"kind": "expression", "prompt": "Enter $w$.", "choices": [], "answer": "1+i", "answer_display": "$1+i$"},
        {"kind": "multiple_choice", "prompt": "Which quotient?", "choices": ["$a/b$", "$b/a$"], "correct_choice": 1, "answer": "",
         "answer_display": "$b/a$"},
    ]
    cap_id = db.new_id()
    db.execute(
        "INSERT INTO questions(id, skill_id, kp_index, key, kind, tier, prompt, choices_json, steps_json, role, answer, "
        "answer_display, created_at) VALUES (?, ?, 9, 'cap', 'multistep', 3, 'Solve it.', '[]', ?, 'capstone', '', '', ?)",
        (cap_id, sid, json.dumps(steps), db.now_iso()),
    )
    taken = {"steps": [{"index": 0, "response": "1+i", "correct": True, "hinted": True, "revealed": False},
                       {"index": 1, "response": "1", "correct": True, "hinted": False, "revealed": False}]}
    attempt({"id": cap_id}, json.dumps(taken), correct=0, independent=0, status="correct")
    cap_line = learner_misses(sid)[-1]
    assert "(multistep, right with help)" in cap_line and "→ 1+i (hint)" in cap_line and "→ $b/a$" in cap_line
    request = plan_request(load_course(cid), skill, allow_split=False, blockers=["1 answered attempt"])
    assert "How the learner did on this skill so far" in request and "wrong-typed-answer" in request
    assert "found overloaded" not in request


def _fake_draft(key: str = "S1") -> LessonV2Draft:
    return fake_llm.respond(schema=LessonV2Draft, role="lesson", instructions="", text=f"Write the lesson for skill {key}: x")


def test_quality_rules_for_length_and_typed_answers_served_first():
    draft = _fake_draft()
    assert quality_problems(draft) == []
    long = draft.model_copy(update={"blocks": draft.blocks + draft.blocks[1:3]})  # 11 blocks
    assert any("too long (11 blocks" in p for p in quality_problems(long))
    wordy = draft.model_copy(update={"blocks": [draft.blocks[0].model_copy(update={"body": "word " * 430})] + draft.blocks[1:]})
    assert any("too long" in p and "words of prose" in p for p in quality_problems(wordy))
    # math does not count as prose
    mathy = draft.model_copy(update={"blocks": [draft.blocks[0].model_copy(update={"body": "$$" + "x+" * 500 + "x$$"})] + draft.blocks[1:]})
    assert quality_problems(mathy) == []
    kp1, kp2 = draft.knowledge_points[:2]
    mc = next(q for kp in draft.knowledge_points for q in kp.questions if q.kind == "multiple_choice")
    two_choice = draft.model_copy(update={"knowledge_points": [
        kp1.model_copy(update={"target_questions": 1, "questions": [mc, *kp1.questions]}),
        kp2.model_copy(update={"target_questions": 1, "questions": [mc.model_copy(update={"key": "k2q9"}), *kp2.questions]}),
    ]})
    problems = quality_problems(two_choice)
    assert any("2 of the questions served first are choice questions" in p for p in problems)
    assert any("no question served first asks for a typed answer" in p for p in problems)


def _issue(location: str, severity: str = "blocking") -> BridgeIssue:
    return BridgeIssue(location=location, inference_required="x", why_insufficient="a repeat of k1q1", repair="replace it",
                       severity=severity)


def test_only_blocked_spares_are_left_out_and_only_the_question_a_location_names():
    draft = _fake_draft()
    kp = draft.knowledge_points[0]
    keys = [q.key for q in kp.questions]
    target = kp.target_questions
    assert target == 2 and len(keys) == 4  # the fake lesson: two served, two spares
    review = LessonReview(
        issues=[_issue(keys[0]), _issue(keys[-1]), _issue("Block 2"), _issue(keys[1], "minor"),
                _issue(f"{keys[1]} and {keys[2]}")],  # a location naming several questions blocks none of them
        density_issues=[_issue("cap — step 2")],
        untaught_method_exercises=[f"{keys[-2]}: needs division, like {keys[0]}"],  # only the key before ':' counts
        out_of_scope_violations=[], optional_content_needed=[],
        math_errors=["Block 3: the value $5$ is also what k2q1 asks for"],  # a block's error blocks no question
        split_recommended=False, split_reason="")
    assert blocking_count(review) == 7
    blocked, capstone = blocked_locations(review)
    assert set(blocked) == {keys[0], keys[-1], keys[-2]} and len(capstone) == 1 and capstone[0].startswith("cap — step 2")
    trimmed, notes = drop_blocked_questions(draft, blocked)
    left = [q.key for q in trimmed.knowledge_points[0].questions]
    assert keys[0] in left and left[0] == keys[0]  # a question served first is kept (noted), never swapped for a spare
    assert any(n.startswith(f"kept {keys[0]} (served first)") for n in notes)
    assert len(left) >= target + 1 and (keys[-1] not in left or keys[-2] not in left)
    assert [q.key for q in trimmed.knowledge_points[1].questions] == [q.key for q in draft.knowledge_points[1].questions]
    # an invalid question does not count as a spare, and is always left out when blocked
    broken = kp.questions[-1].model_copy(update={"kind": "multiple_choice", "choices": ["a", "b"], "correct_choice": 9,
                                                  "answer": ""})
    tight = draft.model_copy(update={"knowledge_points": [
        kp.model_copy(update={"target_questions": 1, "questions": [kp.questions[0], kp.questions[1], broken]}),
        *draft.knowledge_points[1:]]})
    trimmed, notes = drop_blocked_questions(tight, {kp.questions[1].key: "drill", broken.key: "wrong key"})
    assert [q.key for q in trimmed.knowledge_points[0].questions] == [kp.questions[0].key, kp.questions[1].key]
    assert any(n.startswith(f"kept {kp.questions[1].key}") and "no spare left" in n for n in notes)


def test_a_second_revision_runs_only_for_what_is_still_blocking(tmp_path, llm_calls, monkeypatch):
    monkeypatch.setattr(lesson_v2_module, "MAX_REVISIONS", 2)  # the two-round machinery (default: one round)
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    original = fake_llm.respond
    reviews = {"n": 0}

    def still_blocking_once(**kwargs):
        if kwargs["schema"] is LessonReview:
            reviews["n"] += 1
            if reviews["n"] == 2:  # the first revision's review still finds a blocking issue
                kwargs = {**kwargs, "text": kwargs["text"] + " (first draft)"}
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", still_blocking_once)
    llm_calls.clear()
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revisions == 2
    assert llm_calls["LessonV2Draft"] == 3 and llm_calls["LessonReview"] == 3
    assert blocking_count(result.final_review) == 0
    # the review gets the in-chapter prerequisites, so their methods count as taught
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (result.skill_id,))
    lesson = db.loads(skill["lesson_json"])
    assert lesson["initial_review"]["issues"] and lesson["review"]["issues"] == []


def _with_calls(monkeypatch, draft_edit=None, review_edit=None):
    """Wrap the fake LLM: draft_edit(n, draft) / review_edit(n, kwargs) for the n-th writer / reviewer call."""
    original = fake_llm.respond
    calls = {"draft": 0, "review": 0}

    def wrapped(**kwargs):
        if kwargs["schema"] is LessonReview:
            calls["review"] += 1
            if review_edit:
                kwargs = review_edit(calls["review"], kwargs)
            return original(**kwargs)
        result = original(**kwargs)
        if kwargs["schema"] is LessonV2Draft:
            calls["draft"] += 1
            if draft_edit:
                result = draft_edit(calls["draft"], result)
        return result

    monkeypatch.setattr(fake_llm, "respond", wrapped)


def _long(draft: LessonV2Draft) -> LessonV2Draft:
    return draft.model_copy(update={"blocks": draft.blocks + draft.blocks[1:3]})  # 11 blocks: a soft problem


def _review_as(blocking: bool):
    return lambda kwargs: {**kwargs, "text": kwargs["text"].replace("(first draft)", "") + (" (first draft)" if blocking else "")}


def test_a_revision_that_fixes_the_review_is_kept_although_it_runs_long(tmp_path, llm_calls, monkeypatch):
    monkeypatch.setattr(lesson_v2_module, "MAX_REVISIONS", 2)  # the two-round machinery (default: one round)
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    # first draft: blocking review; revision 1: 11 blocks but a clean review; revision 2 trims it
    _with_calls(monkeypatch, draft_edit=lambda n, d: _long(d) if n == 2 else d,
                review_edit=lambda n, kw: _review_as(n == 1)(kw))
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revisions == 2 and blocking_count(result.final_review) == 0
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (result.skill_id,))["lesson_json"])
    assert len(lesson["blocks"]) == 9 and not any(n.startswith("stored although") for n in result.notes)


def test_a_revision_with_a_worse_review_is_not_taken(tmp_path, llm_calls, monkeypatch):
    monkeypatch.setattr(lesson_v2_module, "MAX_REVISIONS", 2)  # the two-round machinery (default: one round)
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    # first draft: clean review but too long; both revisions are shorter but their reviews find a blocking problem
    _with_calls(monkeypatch, draft_edit=lambda n, d: _long(d) if n == 1 else d,
                review_edit=lambda n, kw: _review_as(n > 1)(kw))
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revisions == 0 and blocking_count(result.final_review) == 0
    assert sum("left more blocking issues (1, proof-track problems included) than the draft before it (0)" in n for n in result.notes) == 2
    assert any(n.startswith("stored although: the teaching sequence is too long (11 blocks") for n in result.notes)


def test_a_failing_second_revision_keeps_the_first(tmp_path, llm_calls, monkeypatch):
    monkeypatch.setattr(lesson_v2_module, "MAX_REVISIONS", 2)  # the two-round machinery (default: one round)
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]

    def boom(n, d):
        if n == 3:
            raise RuntimeError("writer unavailable")
        return _long(d) if n == 2 else d  # revision 1 is long, so a second round is wanted

    _with_calls(monkeypatch, draft_edit=boom, review_edit=lambda n, kw: _review_as(n == 1)(kw))
    result = asyncio.run(replan(cid, key)).results[0]
    assert result.ok and result.revisions == 1 and any("revision 2 failed (writer unavailable)" in n for n in result.notes)


def test_review_request_lists_the_prerequisite_skills():
    draft = _fake_draft()
    spec = fake_llm.respond(schema=TeachingPlan, role="graph", instructions="", text="Plan the teaching of skill S1: x").skills[0]
    text = review_request(spec, draft, key="S1", revised=False, prereqs=["- S7 · Add vectors — Compute sums"])
    assert "In-chapter prerequisite skills the learner has learned" in text and "- S7 · Add vectors" in text
    assert "In-chapter prerequisite skills" not in review_request(spec, draft, key="S1", revised=False)


def test_invalid_draft_is_revised_with_the_validation_problem(tmp_path, monkeypatch, llm_calls):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    original = fake_llm.respond
    revision_texts = []

    def broken_first_draft(**kwargs):
        result = original(**kwargs)
        if kwargs["schema"] is LessonV2Draft:
            if "REVISION:" in kwargs["text"]:
                revision_texts.append(kwargs["text"])
            else:  # the first draft's faded example has an unreadable answer
                for block in result.blocks:
                    if block.kind == "faded_example":
                        block.check.answer = "((("
        return result

    monkeypatch.setattr(fake_llm, "respond", broken_first_draft)
    report = asyncio.run(replan(cid, key))
    assert report.results[0].ok and report.results[0].revised
    assert revision_texts and "Validation problems" in revision_texts[0] and "invalid inline check" in revision_texts[0]
    assert "## Review" in revision_texts[0] and "## Your previous draft (JSON)" in revision_texts[0]


def test_inline_check_validation():
    reflect = InlineCheck(kind="reflect", prompt="Why?", choices=["x"], correct_choice=2, answer="a", answer_display="Because.", explanation="")
    assert clean_inline_check(reflect) == {"kind": "reflect", "prompt": "Why?", "choices": [], "correct_choice": None,
                                           "answer": "", "answer_display": "Because.", "explanation": "", "hint": ""}
    with pytest.raises(InvalidQuestion):
        clean_inline_check(reflect.model_copy(update={"answer_display": "", "explanation": ""}))
    tf = InlineCheck(kind="true_false", prompt="True?", choices=[], correct_choice=1, answer="", answer_display="False", explanation="e")
    assert clean_inline_check(tf)["choices"] == ["True", "False"]
    with pytest.raises(InvalidQuestion, match="one letter"):
        clean_inline_check(InlineCheck(kind="true_false_set", prompt="Mark.", choices=["a", "b"], correct_choice=None,
                                       answer="T", answer_display="", explanation="e"))
    with pytest.raises(InvalidQuestion, match="use true_false_set"):
        clean_inline_check(InlineCheck(kind="numeric", prompt="How many of these are true? 1. a", choices=[], correct_choice=None,
                                       answer="1", answer_display="1", explanation="e"))


def test_v2_validation_strict_and_lenient():
    draft = fake_llm.respond(schema=LessonV2Draft, role="lesson", instructions="", text="Write the lesson for skill S1: x")
    spec = split_plan().skills[0]
    lesson, pools, capstone, notes = asyncio.run(validate_v2(draft, spec, []))
    assert lesson["format"] == "v2" and len(lesson["blocks"]) == 9 and len(pools) == 2
    assert lesson["blocks"][4]["visual"]["format"] == "equation_steps" and lesson["goal_line"].startswith("Calculate")
    assert "questions" not in lesson["knowledge_points"][0]
    assert [kp["target_questions"] for kp in lesson["knowledge_points"]] == [2, 1] and lesson["common_mistakes"] == []
    assert capstone.key == "cap" and capstone.kind == "multistep" and len(capstone.steps) == 3
    broken = draft.model_copy(deep=True)
    broken.blocks[2].check = None  # a check block without a check
    with pytest.raises(InvalidLesson, match="block 3"):
        asyncio.run(validate_v2(broken, spec, []))
    lesson, _, _, notes = asyncio.run(validate_v2(broken, spec, [], strict=False))
    assert lesson["blocks"][2]["kind"] == "explain" and any("shown as explain" in n for n in notes)
    no_checks = draft.model_copy(update={"blocks": [b.model_copy(update={"check": None, "kind": "explain"}) for b in draft.blocks]})
    with pytest.raises(InvalidLesson, match="no inline check"):
        asyncio.run(validate_v2(no_checks, spec, []))
    with pytest.raises(InvalidLesson, match="usable blocks"):
        asyncio.run(validate_v2(draft.model_copy(update={"blocks": draft.blocks[:2]}), spec, [], strict=False))


def test_regenerate_rewrites_a_v2_lesson_from_its_stored_plan(tmp_path, llm_calls):
    from app.pipeline.regenerate import regenerate

    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    asyncio.run(replan(cid, key))
    llm_calls.clear()
    report = asyncio.run(regenerate(cid, [key]))
    assert report.lessons_regenerated == [key]
    assert llm_calls["LessonV2Draft"] == 2 and llm_calls["LessonDraft"] == 0 and llm_calls["VisualizationPlan"] == 0
    row = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = ?", (cid, key))
    assert db.loads(row["lesson_json"])["format"] == "v2"


def test_resume_after_an_interrupted_split_writes_missing_lessons(tmp_path, monkeypatch):
    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Add and multiply")["key"]
    original = fake_llm.respond

    def failing_writer(**kwargs):
        if kwargs["schema"] is LessonV2Draft:
            raise RuntimeError("simulated")
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", failing_writer)
    report = asyncio.run(replan(cid, key))
    assert report.decision == "split" and not any(r.ok for r in report.results)
    assert {r["lesson_status"] for r in db.query("SELECT lesson_status FROM skills WHERE key LIKE ?", (f"{key}_",))} == {"failed"}
    assert jobs.get_job(cid).state == "done"
    monkeypatch.undo()
    job = asyncio.run(jobs.run_build(cid))  # Resume in the app: v1 lessons for the new skills
    assert job.state == "done"
    assert {r["lesson_status"] for r in db.query("SELECT lesson_status FROM skills WHERE course_id = ?", (cid,))} == {"ready"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_replan_cli_refuses_while_the_app_server_runs_and_applies_otherwise(tmp_path):
    import http.server
    import threading

    from app.config import settings

    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Add and multiply")["key"]

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    script = Path(__file__).resolve().parents[1] / "scripts" / "replan.py"

    def run(url: str, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), "--data-dir", str(settings.data_dir), "--course", cid, "--skill", key,
             "--yes", "--no-backup", "--server-url", url, *extra],
            capture_output=True, text=True, timeout=300, env={**os.environ, "UA_FAKE_LLM": "1"},
        )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        log_before = jobs.get_job(cid).log
        refused = run(f"http://127.0.0.1:{server.server_address[1]}/api/health")
        assert refused.returncode == 1 and "app server is running" in refused.stdout, refused.stdout + refused.stderr
        assert jobs.get_job(cid).log == log_before and skill_by_title(cid, "Add and multiply")["key"] == key
    finally:
        server.shutdown()
        server.server_close()

    free = f"http://127.0.0.1:{_free_port()}/api/health"
    dry = run(free, "--dry-run")
    assert dry.returncode == 0 and "Decision: split" in dry.stdout, dry.stdout + dry.stderr
    saved = sorted((settings.data_dir / "replans").glob(f"{cid}-{key}-*.json"))
    assert saved and json.loads(saved[-1].read_text())["plan"]["decision"] == "split"
    assert skill_by_title(cid, "Add and multiply")["key"] == key  # nothing applied

    applied = run(free, "--plan", str(saved[-1]))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "Using the saved plan" in applied.stdout and f"SPLIT: {key} → {key}a, {key}b, {key}c" in applied.stdout
    assert db.query_one("SELECT 1 FROM skills WHERE course_id = ? AND key = ?", (cid, key)) is None
    assert list((settings.data_dir / "replans").glob("*-report.txt"))


def test_a_split_written_in_a_sandbox_is_applied_then_its_lessons_imported(tmp_path):
    """replan --plan --apply-only applies the split without writing lessons; import_lesson then brings in each new
    skill's lesson from the sandbox where the same plan was run (nothing is generated twice)."""
    import shutil

    from app.config import settings

    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Add and multiply")["key"]
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    free = f"http://127.0.0.1:{_free_port()}/api/health"

    def run(script: str, data_dir: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(scripts / script), "--data-dir", str(data_dir), "--course", cid, "--yes", "--no-backup",
             "--server-url", free, *extra],
            capture_output=True, text=True, timeout=300, env={**os.environ, "UA_FAKE_LLM": "1"},
        )

    assert run("replan.py", settings.data_dir, "--skill", key, "--apply-only").returncode == 2  # needs --plan
    assert run("replan.py", settings.data_dir, "--skill", key, "--dry-run").returncode == 0
    plan = sorted((settings.data_dir / "replans").glob(f"{cid}-{key}-*.json"))[-1]
    sandbox = tmp_path / "sandbox"
    shutil.copytree(settings.data_dir, sandbox)
    written = run("replan.py", sandbox, "--skill", key, "--plan", str(plan))
    assert written.returncode == 0, written.stdout + written.stderr

    applied = run("replan.py", settings.data_dir, "--skill", key, "--plan", str(plan), "--apply-only")
    assert applied.returncode == 0 and f"Split {key} into {key}a, {key}b, {key}c" in applied.stdout, applied.stdout + applied.stderr
    new = db.query_one("SELECT lesson_json FROM skills WHERE course_id = ? AND key = ?", (cid, f"{key}a"))
    assert new is not None and not new["lesson_json"]  # no lesson written yet
    for part in ("a", "b", "c"):
        done = run("import_lesson.py", settings.data_dir, "--from-data-dir", str(sandbox), "--skill", f"{key}{part}")
        assert done.returncode == 0, done.stdout + done.stderr
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE course_id = ? AND key = ?", (cid, f"{key}a"))["lesson_json"], {})
    assert lesson.get("format") == "v2"


def test_proof_skills_reason_one_notch_higher_on_the_same_model():
    """The learner, 2026-10-08: proofs need more care — "use sol at one higher reasoning notch" (not a bigger model)."""
    from app.llm import raise_effort
    from app.pipeline.lesson_v2 import effort_notches

    assert [raise_effort(e, 1) for e in ("low", "medium", "high", "xhigh")] == ["medium", "high", "xhigh", "xhigh"]
    assert raise_effort("high", 0) == "high"
    assert effort_notches("proof", "Prove the laws of 1.3") == 1
    assert effort_notches("computation", "Compute a quotient") == 0


def test_import_lesson_copies_a_sandbox_lesson_with_its_verdicts(tmp_path):
    """A lesson generated and reviewed in a sandbox copy is imported as it is (not generated again)."""
    import shutil
    import sqlite3

    from app.config import settings

    cid = built_course(tmp_path)
    key = skill_by_title(cid, "Distinguish lists")["key"]
    asyncio.run(replan(cid, key))  # a v2 lesson in the target
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(sandbox / "academy.sqlite3")
    src.backup(dst)
    dst.close()
    src.close()
    shutil.copytree(settings.data_dir / "courses", sandbox / "courses", dirs_exist_ok=True)
    # the sandbox's version differs: another title, another first question, a verdict
    conn = sqlite3.connect(sandbox / "academy.sqlite3")
    sid = conn.execute("SELECT id, lesson_json FROM skills WHERE course_id = ? AND key = ?", (cid, key)).fetchone()
    lesson = json.loads(sid[1])
    lesson["title"] = "Sandbox title"
    conn.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (json.dumps(lesson), sid[0]))
    qid = conn.execute("SELECT id FROM questions WHERE skill_id = ? AND kp_index = 0 AND key NOT LIKE 'old-%' ORDER BY rowid", (sid[0],)).fetchone()[0]
    conn.execute("UPDATE questions SET prompt = 'Sandbox prompt', verify_json = '{\"status\": \"agree\"}' WHERE id = ?", (qid,))
    conn.commit()
    conn.close()

    script = Path(__file__).resolve().parents[1] / "scripts" / "import_lesson.py"
    run = subprocess.run(
        [sys.executable, str(script), "--from-data-dir", str(sandbox), "--data-dir", str(settings.data_dir), "--course", cid,
         "--skill", key, "--yes", "--no-backup", "--server-url", f"http://127.0.0.1:{_free_port()}/api/health"],
        capture_output=True, text=True, timeout=300, env={**os.environ, "UA_FAKE_LLM": "1"},
    )
    assert run.returncode == 0 and "Imported." in run.stdout, run.stdout + run.stderr
    stored = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid[0],))["lesson_json"])
    assert stored["title"] == "Sandbox title"
    first = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND kp_index = 0 AND key NOT LIKE 'old-%' ORDER BY rowid", (sid[0],))
    assert first["prompt"] == "Sandbox prompt" and db.loads(first["verify_json"])["status"] == "agree"
