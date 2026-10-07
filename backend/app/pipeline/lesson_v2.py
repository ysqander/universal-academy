"""Lesson generation v2 (experimental — docs/lesson-v2.md, SPEC §4.2b).

    plan_skill(course, skill, *, allow_split=True) -> (TeachingPlan, notes)     planner (role "graph"), validated
    write_lesson(course, spec, *, skill, prefix, revision=None) -> LessonV2Draft  writer (role "lesson", cached prefix)
    review_lesson(spec, draft, *, key, revised=False) -> LessonReview          missing-bridge reviewer (role "graph")
    await generate_v2_lesson(ctx, skill_id, spec, prefix) -> V2Result          write → review → (revise once → review)
                                                                                → validate → persist → verify

Stored as skills.lesson_json = {"format": "v2", title, objective, goal_line, blocks (inline checks with their answers —
never sent to the browser), notation, knowledge_points (without questions, with target_questions), common_mistakes
(always [] — no such section any more), connection, deeper, source_refs, plan (the TeachingSpec), review (the final
LessonReview), initial_review (when revised), why_it_matters/idea/missing_steps: ""}. Practice questions use the v1
question path (validation, insert, retire, verification); the capstone (the final multistep problem, key 'cap') is
a question with role 'capstone' and kp_index = the number of knowledge points; viz_json = {"format": "none"}
because visuals live in the blocks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from .. import db, llm
from ..llm_schemas import (
    BridgeIssue,
    CapstoneRepair,
    DependentLink,
    InlineCheck,
    LessonDraft,
    LessonReview,
    LessonV2Draft,
    MultistepStep,
    PlannedPrereq,
    QuestionDraft,
    TeachingPlan,
    TeachingSpec,
)
from ..views import lesson_version
from .context import (
    BuildContext,
    CourseInfo,
    FigureInfo,
    course_figures,
    course_map,
    figure_line,
    focus_pages,
    key_number,
    page_marker,
)
from .lessons import (
    RETIRED_REASON,
    InvalidLesson,
    abandon_active_tasks,
    capstone_problems,
    clamp_target,
    figures_for_skill,
    free_response_problem,
    is_proof_skill,
    resolve_figures,
    retire_questions,
    validate_capstone,
    validate_lesson,
)
from .prompts_v2 import (
    CAPSTONE_REPAIR_NOTE,
    PLANNER_INSTRUCTIONS,
    REVIEWER_INSTRUCTIONS,
    REVISION_NOTE,
    WRITER_INSTRUCTIONS,
)
from .questions import InvalidQuestion, clean_capstone, clean_question, insert_questions, is_choice_based
from .textutil import clean_source_refs, normalize_sibling_refs, sibling_keys
from .verify import verify_skill
from .visuals import FINALIZE_TIMEOUT, finalize_plan

log = logging.getLogger("ua.pipeline.lesson_v2")

MAX_SPLIT = 5
LOCAL_IDS = "abcde"
MIN_BLOCKS, MAX_BLOCKS = 3, 14
CHECK_BLOCKS = {"check", "faded_example"}
FADED_CHECK_KINDS = {"numeric", "expression", "true_false_set", "multiple_choice", "true_false"}  # machine-checked
IDEA_BUDGET = 4  # about this many new ideas per lesson (the learner's S3b review, docs/DECISIONS.md §2)
# Lesson length and revision budget (evaluation of the 2026-10-07 re-plans: 11-13 blocks read as long; blocking issues
# the final review still found were stored as they were).
TARGET_BLOCKS, MAX_TEACHING_BLOCKS = 9, 10
TARGET_PROSE_WORDS, MAX_PROSE_WORDS = 350, 420
MAX_REVISIONS = 2  # the second revision runs only for blocking issues or validation problems the first one left
_MATH_SPAN = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_QUESTION_KEY = re.compile(r"\bk\d+q\d+\b")
_CAPSTONE_LOCATION = re.compile(r"\bcap\b|capstone", re.IGNORECASE)
MISCONCEPTIONS_HEADER = (
    "Known misconceptions (a line starting \"Learner's miss\" is what this learner actually got wrong: re-test it with "
    "NEW numbers that set the same trap, in the format it was missed (typed when it was typed), as a question served "
    "first in its knowledge point or a capstone step — the missed problem itself with the wrong answer as an option "
    "is not a test):"
)
MAX_SHOWN_MISSES = 10  # learner misses on this skill (any lesson version) shown to the planner
MIN_WEIGHT, MAX_WEIGHT = 0.05, 1.0
V2_VIZ = {"format": "none"}  # skills.viz_json of a v2 lesson: its visuals live in the blocks
_GOAL_PREFIX = re.compile(r"^\s*(?:\*\*)?\s*your goal\s*:?\s*(?:\*\*)?\s*:?\s*", re.IGNORECASE)


class InvalidPlan(ValueError):
    pass


# ---------------------------------------------------------------------------
# Graph context of the node being planned
# ---------------------------------------------------------------------------


@dataclass
class PlanGraph:
    """The original node's neighbourhood: keys of the course, direct prerequisites/dependents (key → edge weight)
    and every skill reachable from the node (a new skill may not depend on those: it would close a cycle)."""

    key: str
    keys: set[str]
    prereqs: dict[str, float]
    dependents: dict[str, float]
    descendants: set[str]
    pages: set[int]


def plan_graph(course: CourseInfo, skill: dict[str, Any]) -> PlanGraph:
    rows = db.query("SELECT id, key FROM skills WHERE course_id = ?", (course.id,))
    key_of = {r["id"]: r["key"] for r in rows}
    graph = nx.DiGraph()
    graph.add_nodes_from(key_of.values())
    edges = db.query("SELECT src, dst, weight FROM edges WHERE course_id = ?", (course.id,))
    for e in edges:
        if e["src"] in key_of and e["dst"] in key_of:
            graph.add_edge(key_of[e["src"]], key_of[e["dst"]], weight=e["weight"])
    key = skill["key"]
    return PlanGraph(
        key=key,
        keys=set(key_of.values()),
        prereqs={u: graph.edges[u, key]["weight"] for u in graph.predecessors(key)},
        dependents={v: graph.edges[key, v]["weight"] for v in graph.successors(key)},
        descendants=nx.descendants(graph, key),
        pages=set(course.pages),
    )


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def _skill_lines(rows: list[dict[str, Any]], weights: dict[str, float] | None = None) -> list[str]:
    lines = []
    for r in rows:
        weight = f" (encompassing weight {weights[r['key']]:g})" if weights and r["key"] in weights else ""
        lines.append(f"- {r['key']} · {r['title']} — {r['objective']}{weight}")
    return lines


def _neighbours(skill_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prereqs = db.query(
        "SELECT s.key, s.title, s.objective, e.weight FROM edges e JOIN skills s ON s.id = e.src WHERE e.dst = ? "
        "ORDER BY s.order_index",
        (skill_id,),
    )
    dependents = db.query(
        "SELECT s.key, s.title, s.objective, e.weight FROM edges e JOIN skills s ON s.id = e.dst WHERE e.src = ? "
        "ORDER BY s.order_index",
        (skill_id,),
    )
    return prereqs, dependents


def _source_pages_text(course: CourseInfo, pages: list[int]) -> list[str]:
    focus = focus_pages(pages, course)
    if not focus:
        return []
    marks = ",".join("?" * len(focus))
    rows = db.query(
        f"SELECT page_index, label, markdown, is_exercise_page FROM pages WHERE course_id = ? AND page_index IN ({marks}) "
        "ORDER BY page_index",
        (course.id, *focus),
    )
    lines = []
    for r in rows:
        lines += [page_marker(r["page_index"], r["label"] or course.label(r["page_index"]), bool(r["is_exercise_page"]))]
        lines += [r["markdown"].strip(), ""]
    return lines


def plan_request(course: CourseInfo, skill: dict[str, Any], *, allow_split: bool, blockers: list[str] | None = None) -> str:
    pages = db.loads(skill["source_pages_json"], [])
    prereqs, dependents = _neighbours(skill["id"])
    lines = [
        f"Plan the teaching of skill {skill['key']}: {skill['title']}",
        "",
        f"Objective: {skill['objective']}",
        f"Kind: {skill['kind']} · difficulty {skill['difficulty']}/5 · about {skill['est_minutes']} minutes",
        f"Summary: {skill['summary']}",
    ]
    refs = db.loads(skill["source_refs_json"], [])
    if refs:
        lines.append("Book items: " + ", ".join(refs))
    if pages:
        lines.append("Taught on: " + ", ".join(page_marker(p, course.label(p)) for p in pages))
    misconceptions = db.loads(skill["misconceptions_json"], [])
    if misconceptions:
        lines += ["Known misconceptions:"] + [f"- {m}" for m in misconceptions]
    current = db.loads(skill["lesson_json"], None) or {}
    if current.get("knowledge_points"):
        lines += ["", "The current lesson practises these knowledge points:"]
        lines += [f"- {kp.get('title', '')}: {kp.get('explanation', '')}" for kp in current["knowledge_points"]]
    misses = learner_misses(skill["id"])
    if misses:
        lines += ["", "How the learner did on this skill so far (current and earlier lesson versions) — the questions they "
                  "missed or needed help with:"]
        lines += misses
    feedback = db.query(
        "SELECT block_index, note, lesson_hash FROM block_feedback WHERE skill_id = ? AND note != '' "
        "ORDER BY created_at, rowid",
        (skill["id"],),
    )
    version = lesson_version(skill["lesson_json"])
    current = [f for f in feedback if f["lesson_hash"] == version]
    earlier = [f for f in feedback if f["lesson_hash"] != version]  # block numbers of another version mean nothing
    if current:
        lines += ["", "Learner feedback on the current lesson (\"This step isn't clear\"):"]
        lines += [f"- block {f['block_index'] + 1}: {f['note']}" for f in current]
    if earlier:
        lines += ["", "Learner feedback on an earlier version of this lesson (\"This step isn't clear\"):"]
        lines += [f"- {f['note']}" for f in earlier]
    lines += ["", "Direct prerequisites (in-chapter):"]
    lines += _skill_lines(prereqs, {p["key"]: p["weight"] for p in prereqs}) or ["(none)"]
    lines += [
        "",
        f"Skills that depend on {skill['key']} (dependents — when you split, map EVERY one of them in `dependents`):",
    ]
    lines += _skill_lines(dependents, {d["key"]: d["weight"] for d in dependents}) or ["(none)"]
    foundations = db.query("SELECT key, title, description FROM foundations WHERE course_id = ? ORDER BY key", (course.id,))
    lines += ["", "Assumed outside knowledge (foundations of the course):"]
    lines += [f"- {f['key']} · {f['title']}: {f['description']}" for f in foundations] or ["(none)"]
    lines += [""]
    if allow_split:
        lines.append("Splitting: allowed (2–5 narrower skills; they get the keys "
                     f"{skill['key']}a, {skill['key']}b, … in your order).")
    else:
        reason = "; ".join(blockers or []) or "the run was started with --no-split"
        lines.append(f"Splitting: NOT allowed ({reason}) — return decision 'keep' with exactly one spec.")
    lines += ["", course_map(course.id)]
    lines += ["## Source pages (the skill's pages and their neighbours)"] + _source_pages_text(course, pages)
    figures = [f for f in course_figures(course) if f.page in set(focus_pages(pages, course))]
    lines += ["## Figures on these pages"] + ([figure_line(f) for f in figures] or ["(none)"])
    return "\n".join(lines)


def _json_or(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _choice_text(choices: list[str], response: str) -> str:
    """The option the learner picked (choice answers are stored as the option's index)."""
    if response.strip().isdigit() and 0 <= int(response) < len(choices):
        return choices[int(response)]
    return response


def _tf_marks(statements: list[str], response: str, key: str) -> str:
    """'1. False (wrong); 2. False; 3. True (wrong)' — no lining up of letters needed."""
    letters, key = response.strip().upper(), key.strip().upper()
    if not letters or set(letters) - {"T", "F"}:
        return response
    marks = []
    for i, c in enumerate(letters):
        wrong = i < len(key) and c != key[i]
        marks.append(f"{i + 1}. {'True' if c == 'T' else 'False'}{' (wrong)' if wrong else ''}")
    return "; ".join(marks)


def _miss_line(r: dict[str, Any]) -> str:
    kind = r["kind"]
    prompt = " ".join((r["prompt"] or "").split())[:500]
    earlier = "[earlier lesson version] " if (r["key"] or "").startswith("old-") else ""
    response = r["response"] or ""
    if kind == "multistep":
        data = _json_or(response, {})
        taken = {s.get("index"): s for s in (data.get("steps") or [])} if isinstance(data, dict) else {}
        steps = _json_or(r["steps_json"], []) or []
        wrong = any(not s.get("correct") for s in taken.values())
        how = "a step wrong" if wrong else ("solution shown first" if r["revealed"] else "right with help")
        parts = []
        for i, step in enumerate(steps):
            s = taken.get(i)
            if s is None:
                continue
            answer = s.get("response") or "—"
            if step.get("kind") in ("multiple_choice", "true_false"):
                answer = _choice_text(step.get("choices") or [], answer)
            elif step.get("kind") == "true_false_set":
                answer = _tf_marks(step.get("choices") or [], answer, step.get("answer") or "")
            note = ""
            if not s.get("correct"):
                note = f" (wrong; correct: {step.get('answer_display') or '—'})"
            elif s.get("revealed"):
                note = " (solution shown)"
            elif s.get("hinted"):
                note = " (hint)"
            parts.append(f"step {i + 1}: {' '.join((step.get('prompt') or '').split())[:200]} → {answer[:120]}{note}")
        return f"- {earlier}({kind}, {how}) {prompt} — " + (" | ".join(parts) or "no step answered")
    if not r["correct"]:
        how = "solution viewed, no answer" if r["revealed"] and not response.strip() else "wrong"
    else:
        how = "solution shown first" if r["revealed"] else "right with a hint"
    choices = _json_or(r["choices_json"], []) or []
    if kind == "true_false_set":
        prompt += " Statements: " + " | ".join(f"{i + 1}. {st}" for i, st in enumerate(choices))
        shown = _tf_marks(choices, response, r["answer"] or "")
    elif kind in ("multiple_choice", "true_false"):
        shown = _choice_text(choices, response)
    else:
        shown = response
    return f"- {earlier}({kind}, {how}) {prompt} — learner answered: {shown[:200] or '—'} — correct: {r['answer_display'] or '—'}"


def learner_misses(skill_id: str, limit: int = MAX_SHOWN_MISSES) -> list[str]:
    """The learner's missed or assisted answers on this skill (current and earlier lesson versions — the earlier ones
    marked), oldest first, for the planner: evidence of what the lesson did not teach. Left out: voided attempts,
    unanswered quiz questions (skipped, timed out or never reached: no response), questions flagged as faulty (their
    key may be wrong) and self-graded legacy free-response answers (no key, self-assessed)."""
    rows = db.query(
        "SELECT a.correct, a.independent, a.hints_used, a.revealed, a.response, a.created_at, "
        "q.key, q.kind, q.prompt, q.choices_json, q.answer, q.answer_display, q.steps_json FROM attempts a "
        "JOIN questions q ON q.id = a.question_id "
        "WHERE a.skill_id = ? AND a.status NOT IN ('voided', 'self_graded') AND (a.correct = 0 OR a.independent = 0) "
        "AND (COALESCE(a.response, '') != '' OR a.revealed = 1) AND (q.flagged = 0 OR q.flag_reason = ?) "
        "ORDER BY a.created_at DESC, a.rowid DESC LIMIT ?",
        (skill_id, RETIRED_REASON, limit),
    )
    return [_miss_line(r) for r in reversed(rows)]


async def plan_skill(
    course: CourseInfo, skill: dict[str, Any], *, allow_split: bool = True, blockers: list[str] | None = None
) -> tuple[TeachingPlan, list[str]]:
    """Planner call (high effort), validated against the graph; one retry with the validation error."""
    text = plan_request(course, skill, allow_split=allow_split, blockers=blockers)
    context = plan_graph(course, skill)
    error: Exception | None = None
    for attempt in range(2):
        request = text
        if isinstance(error, InvalidPlan):
            request += f"\n\nA previous plan was rejected: {error}. Fix that in this plan."
        try:
            plan = await llm.structured(
                role="graph",
                instructions=PLANNER_INSTRUCTIONS,
                text=request,
                schema=TeachingPlan,
                max_output_tokens=32000,
                label=f"plan {skill['key']}",
                stream=True,
            )
            return validate_plan(plan, context, allow_split=allow_split)
        except Exception as e:  # noqa: BLE001 — retried once
            error = e
            log.warning("plan %s attempt %d failed: %s", skill["key"], attempt + 1, e)
    raise InvalidPlan(f"planning {skill['key']} failed: {error}")


def _weight(value: float) -> float:
    try:
        return round(min(MAX_WEIGHT, max(MIN_WEIGHT, float(value))), 3)
    except (TypeError, ValueError):
        return 0.5


def validate_plan(plan: TeachingPlan, graph: PlanGraph, *, allow_split: bool = True) -> tuple[TeachingPlan, list[str]]:
    """Normalize a plan: 1 spec when kept, 2-5 when split (local ids a, b, … in order), prerequisites only on existing
    keys that do not depend on the original (no cycles) or on earlier siblings, every original prerequisite covered
    by some new skill, and every original dependent mapped (an unmapped one needs every new skill). Raises
    InvalidPlan when nothing usable is left."""
    notes: list[str] = []
    specs = list(plan.skills)
    if not specs:
        raise InvalidPlan("the plan has no skills")
    decision = plan.decision
    if not allow_split and (decision == "split" or len(specs) > 1):
        raise InvalidPlan("splitting is not allowed for this skill: return decision 'keep' with exactly one spec")
    if decision == "keep" and len(specs) > 1:
        notes.append(f"decision 'keep' came with {len(specs)} specs; kept the first")
        specs = specs[:1]
    elif decision == "split" and len(specs) < 2:
        notes.append("decision 'split' came with a single spec; treated as 'keep'")
        decision = "keep"
    elif decision == "split" and len(specs) > MAX_SPLIT:
        raise InvalidPlan(f"a split has 2–{MAX_SPLIT} skills, got {len(specs)}")

    remap: dict[str, str] = {}
    for i, spec in enumerate(specs):
        old = spec.local_id.strip().lower()
        if old in remap:
            notes.append(f"duplicate local id {old!r} renamed to {LOCAL_IDS[i]!r}")
        else:
            remap[old] = LOCAL_IDS[i]
    for i in range(len(specs)):  # siblings named by their future keys (S2a, S2b, …)
        remap.setdefault(f"{graph.key}{LOCAL_IDS[i]}".lower(), LOCAL_IDS[i])
    forbidden = graph.descendants | {graph.key}
    cleaned: list[TeachingSpec] = []
    for i, spec in enumerate(specs):
        local = LOCAL_IDS[i]
        prereqs: list[PlannedPrereq] = []
        seen: set[str] = set()
        for link in spec.prerequisites:
            raw = link.key.strip()
            if raw.lower() in remap and raw not in graph.keys:
                ref = remap[raw.lower()]
                if LOCAL_IDS.index(ref) >= i:
                    notes.append(f"{local}: dropped prerequisite on {ref!r} (not an earlier sibling)")
                    continue
            elif raw in graph.keys:
                if raw in forbidden:
                    notes.append(f"{local}: dropped prerequisite {raw} ({'the original skill' if raw == graph.key else 'it depends on the original skill — a cycle'})")
                    continue
                ref = raw
            else:
                notes.append(f"{local}: dropped prerequisite on unknown key {raw!r}")
                continue
            if ref not in seen:
                seen.add(ref)
                prereqs.append(PlannedPrereq(key=ref, weight=_weight(link.weight)))
        pages = sorted({p for p in spec.source_pages if p in graph.pages})
        if not pages:
            notes.append(f"{local}: no valid source pages")
        cleaned.append(
            spec.model_copy(
                update={
                    "local_id": local,
                    "title": spec.title.strip()[:80] or f"Part {local}",
                    "objective": spec.objective.strip(),
                    "prerequisites": prereqs,
                    "source_pages": pages,
                    "source_refs": clean_source_refs(spec.source_refs),
                    "representations": spec.representations[:2],
                    "practice_steps": [p for p in spec.practice_steps if p.strip()][:3] or [spec.title.strip()],
                    "difficulty": min(5, max(1, int(spec.difficulty))),
                    "est_minutes": min(30, max(3, int(spec.est_minutes))),
                }
            )
        )
        if not cleaned[-1].objective:
            raise InvalidPlan(f"skill {local} has no objective")

    for key, weight in sorted(graph.prereqs.items(), key=lambda kv: (key_number(kv[0]), kv[0])):
        if not any(p.key == key for s in cleaned for p in s.prerequisites):
            first = cleaned[0]
            cleaned[0] = first.model_copy(
                update={"prerequisites": [*first.prerequisites, PlannedPrereq(key=key, weight=_weight(weight))]}
            )
            notes.append(f"the plan dropped the original prerequisite {key}; added it to skill a")

    dependents: list[DependentLink] = []
    if decision == "split":
        mapped: dict[str, list[str]] = {}
        for link in plan.dependents:
            dep = link.dependent_key.strip()
            if dep not in graph.dependents:
                notes.append(f"ignored a mapping for {dep!r} (not a dependent of {graph.key})")
                continue
            needs = list(dict.fromkeys(remap[n.strip().lower()] for n in link.needs if n.strip().lower() in remap))
            if needs:
                mapped[dep] = sorted(set(mapped.get(dep, [])) | set(needs), key=LOCAL_IDS.index)
        for dep in sorted(graph.dependents, key=lambda k: (key_number(k), k)):
            if dep not in mapped:
                mapped[dep] = [s.local_id for s in cleaned]
                notes.append(f"the plan did not map dependent {dep}; it now needs every new skill")
            dependents.append(DependentLink(dependent_key=dep, needs=mapped[dep]))
    validated = plan.model_copy(update={"decision": decision, "skills": cleaned, "dependents": dependents})
    # "Builds on" chips: the planner's local ids of siblings ("a (S2a): …", "b: …") become the final keys
    final = new_keys(graph.key, validated, graph.keys - {graph.key}) if decision == "split" else {}
    local_keys = {old: final[new] for old, new in remap.items() if new in final}
    skills = [
        s.model_copy(update={"assumed_prerequisites": normalize_sibling_refs(s.assumed_prerequisites, local_keys)})
        for s in validated.skills
    ]
    return validated.model_copy(update={"skills": skills}), notes


def describe_plan(plan: TeachingPlan, key: str) -> list[str]:
    """Human-readable plan (CLI report)."""
    lines = [f"Split test: {plan.split_test}", f"Decision: {plan.decision} — {plan.rationale}"]
    keys = new_keys(key, plan, set()) if plan.decision == "split" else {"a": key}
    for spec in plan.skills:
        lines += [
            "",
            f"[{keys.get(spec.local_id, spec.local_id)}] {spec.title}  ({spec.kind}, difficulty {spec.difficulty}, "
            f"~{spec.est_minutes} min, pages {spec.source_pages}, refs {', '.join(spec.source_refs) or '—'})",
            f"  objective: {spec.objective}",
            "  prerequisites: "
            + (", ".join(f"{keys.get(p.key, p.key)} ({p.weight:g})" for p in spec.prerequisites) or "—"),
            f"  assumed: {'; '.join(spec.assumed_prerequisites) or '—'}",
            f"  out of scope: {'; '.join(spec.out_of_scope) or '—'}",
            f"  starting point: {spec.starting_point}",
            "  reasoning sequence:",
            *[f"    {i}. {step}" for i, step in enumerate(spec.reasoning_sequence, start=1)],
            f"  success check: {spec.success_check}",
            "  representations: "
            + ("; ".join(f"{r.format} — notice {r.helps_notice}" for r in spec.representations) or "none"),
            f"  practice steps: {' → '.join(spec.practice_steps)}",
        ]
    if plan.dependents:
        lines += ["", "Dependents:"]
        lines += [f"  {d.dependent_key} needs {', '.join(keys.get(n, n) for n in d.needs)}" for d in plan.dependents]
    return lines


def new_keys(key: str, plan: TeachingPlan, taken: set[str]) -> dict[str, str]:
    """local id → new skill key: S2 → S2a, S2b, … (skipping keys already taken)."""
    result: dict[str, str] = {}
    letters = iter("abcdefghijklmnopqrstuvwxyz")
    for spec in plan.skills:
        candidate = f"{key}{next(letters)}"
        while candidate in taken:
            candidate = f"{key}{next(letters)}"
        result[spec.local_id] = candidate
    return result


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def spec_figures(course: CourseInfo, skill: dict[str, Any], spec: TeachingSpec) -> list[FigureInfo]:
    """Figures the lesson may use: attached to the skill, or printed on the spec's source pages."""
    pseudo = {"figure_ids_json": skill["figure_ids_json"], "source_pages_json": db.dumps(spec.source_pages)}
    return figures_for_skill(course_figures(course), pseudo)


def write_request(course: CourseInfo, skill: dict[str, Any], spec: TeachingSpec, figures: list[FigureInfo]) -> str:
    lines = [
        f"Write the lesson for skill {skill['key']}: {spec.title}",
        "",
        "TEACHING SPEC (planned for this lesson — follow it)",
        f"Objective: {spec.objective}",
        f"Kind: {spec.kind} · difficulty {spec.difficulty}/5 · about {spec.est_minutes} minutes",
        "Assumed prerequisites (already learned — use them freely, do not re-teach them):",
        *([f"- {a}" for a in spec.assumed_prerequisites] or ["- (none)"]),
        "Out of scope (must not appear anywhere in this lesson):",
        *([f"- {o}" for o in spec.out_of_scope] or ["- (none)"]),
        f"Starting point: {spec.starting_point}",
        "Reasoning sequence (the bridges, in order — each one belongs in the main path):",
        *[f"{i}. {step}" for i, step in enumerate(spec.reasoning_sequence, start=1)],
        f"Success check — the planner's idea for the capstone (where it conflicts with the CAPSTONE rules, the rules "
        f"win: no step whose answer is known in advance, no practice question split into steps, complex results in "
        f"the form $a+bi$): {spec.success_check}",
        "Planned representations:",
        *(
            [f"- {r.format}: helps the learner notice {r.helps_notice} — read it as: {r.how_to_read}" for r in spec.representations]
            or ["- none"]
        ),
        "Practice steps (one knowledge point each, in this order):",
        *[f"{i}. {step}" for i, step in enumerate(spec.practice_steps, start=1)],
    ]
    if spec.misconceptions:
        lines += [MISCONCEPTIONS_HEADER] + [f"- {m}" for m in spec.misconceptions]
    if spec.source_refs:
        lines.append("Book items: " + ", ".join(spec.source_refs))
    if spec.source_pages:
        around = [p for p in focus_pages(spec.source_pages, course) if p not in spec.source_pages]
        lines.append(
            "Taught on: "
            + ", ".join(page_marker(p, course.label(p)) for p in spec.source_pages)
            + " — read these closely"
            + (f" (and their neighbours: pages {', '.join(map(str, around))})." if around else ".")
        )
    prereqs, dependents = _neighbours(skill["id"])
    lines += ["", "In-chapter prerequisites (learned before this lesson):"]
    lines += _skill_lines(prereqs, {p["key"]: p["weight"] for p in prereqs}) or ["(none — a starting skill of the course)"]
    lines += ["", "Later skills that build on this one (do not teach them; the connection may point to them):"]
    lines += _skill_lines(dependents) or ["(none)"]
    if figures:
        lines += [
            "",
            "Book figures for this lesson — the only figures you may refer to (place one with [[figure:ID]] on its own "
            "line in a block body, or use a book_figure visual with book_figure_id):",
            *[figure_line(f) for f in figures],
        ]
    else:
        lines += ["", "Book figures for this lesson: none — do not refer to any figure."]
    if is_proof_skill({"kind": spec.kind, "objective": spec.objective}):
        lines += ["", "This is a PROOF skill: follow PROOF SKILLS in the instructions (multistep walkthroughs of the "
                  "proofs — never free_response)."]
    exercise_pages = [
        r["page_index"]
        for r in db.query("SELECT page_index FROM pages WHERE course_id = ? AND is_exercise_page = 1", (course.id,))
    ]
    if exercise_pages:
        lines += [
            "",
            "Book exercises are on pages "
            + ", ".join(page_marker(p, course.label(p), True) for p in exercise_pages)
            + " — adapt the ones that fit this objective (and use only taught methods) and cite them.",
        ]
    return "\n".join(lines)


def revision_request(request: str, draft: LessonV2Draft, review: LessonReview, problems: list[str]) -> str:
    lines = [request, "", REVISION_NOTE, "", "## Your previous draft (JSON)", draft.model_dump_json(), "", "## Review"]
    lines += review_lines(review) or ["(no issues reported)"]
    if problems:
        lines += ["", "## Validation problems (the app cannot use the draft until these are fixed)"]
        lines += [f"- {p}" for p in problems]
    return "\n".join(lines)


async def write_lesson(
    course: CourseInfo,
    spec: TeachingSpec,
    *,
    skill: dict[str, Any],
    prefix: str,
    figures: list[FigureInfo] | None = None,
    revision: tuple[LessonV2Draft, LessonReview, list[str]] | None = None,
) -> LessonV2Draft:
    """Writer call (role "lesson"): the chapter text + course map is the cached shared prefix, as in v1."""
    figures = spec_figures(course, skill, spec) if figures is None else figures
    text = write_request(course, skill, spec, figures)
    if revision is not None:
        text = revision_request(text, *revision)
    return await llm.structured(
        role="lesson",
        instructions=WRITER_INSTRUCTIONS,
        shared_prefix=prefix,
        text=text,
        schema=LessonV2Draft,
        max_output_tokens=48000,
        label=f"lesson-v2 {skill['key']}{' revision' if revision else ''}",
        stream=True,
    )


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------


def _check_lines(check: InlineCheck | dict[str, Any], indent: str = "   ") -> list[str]:
    c = check.model_dump() if isinstance(check, InlineCheck) else check
    lines = [f"{indent}CHECK ({c['kind']}): {c['prompt']}"]
    if c["kind"] == "true_false_set":
        lines += [f"{indent}  statement {i}. {s}" for i, s in enumerate(c.get("choices") or [], start=1)]
    elif c.get("choices"):
        lines += [f"{indent}  option {i}. {s}" for i, s in enumerate(c.get("choices") or [])]
    key = c.get("answer") or (f"option {c['correct_choice']}" if c.get("correct_choice") is not None else "")
    lines.append(f"{indent}  answer: {key or '—'} · shown as: {c.get('answer_display', '')}")
    lines.append(f"{indent}  explanation after responding: {c.get('explanation', '')}")
    return lines


def _visual_lines(viz: dict[str, Any] | None, indent: str = "   ") -> list[str]:
    if not viz or viz.get("format") == "none":
        return []
    lines = [f"{indent}VISUAL ({viz.get('format')}): {viz.get('purpose', '')} · caption: {viz.get('caption', '')}"]
    table = viz.get("table") or {}
    if table:
        lines.append(f"{indent}  | " + " | ".join(table.get("headers", [])) + " |")
        lines += [f"{indent}  | " + " | ".join(row) + " |" for row in table.get("rows", [])]
    lines += [f"{indent}  $${s.get('latex', '')}$$  ({s.get('note', '')})" for s in viz.get("steps") or []]
    if viz.get("book_figure_id"):
        lines.append(f"{indent}  book figure: {viz['book_figure_id']}")
    if viz.get("diagram"):
        d = viz["diagram"]
        lines.append(
            f"{indent}  diagram: vectors {[(v.get('label'), v.get('start'), v.get('end')) for v in d.get('vectors') or []]}, "
            f"points {[(p.get('label'), p.get('at')) for p in d.get('points') or []]}"
        )
    for frame in viz.get("frames") or []:
        lines.append(f"{indent}  frame {frame.get('title')}: {frame.get('latex') or ''} {frame.get('note') or ''}")
    if viz.get("plot"):
        lines.append(f"{indent}  plot: {[f.get('expr') for f in viz['plot'].get('functions') or []]}")
    lines.append(f"{indent}  learner prompt: {viz.get('learner_prompt', '')}")
    return lines


def _question_lines(q: QuestionDraft | dict[str, Any], indent: str = "  ") -> list[str]:
    """A practice question or the capstone as text (multistep: every step with its answer, hint and explanation)."""
    q = q.model_dump() if isinstance(q, QuestionDraft) else q
    lines = [f"{indent}{q['key']} [{q['kind']}, tier {q['tier']}]: {q['prompt']}"]
    if q["kind"] == "true_false_set":
        lines += [f"{indent}    statement {i}. {s}" for i, s in enumerate(q.get("choices") or [], start=1)]
    elif q.get("choices"):
        lines += [f"{indent}    option {i}. {s}" for i, s in enumerate(q.get("choices") or [])]
    for i, step in enumerate(q.get("steps") or [], start=1):
        lines.append(f"{indent}    step {i} [{step['kind']}]: {step['prompt']}")
        if step["kind"] == "true_false_set":
            lines += [f"{indent}        statement {j}. {c}" for j, c in enumerate(step.get("choices") or [], start=1)]
        elif step.get("choices"):
            lines += [f"{indent}        option {j}. {c}" for j, c in enumerate(step.get("choices") or [])]
        key = step.get("answer") or (f"option {step['correct_choice']}" if step.get("correct_choice") is not None else "")
        lines.append(f"{indent}        answer: {key or '—'} · {step.get('answer_display', '')}")
        lines.append(f"{indent}        hint: {step.get('hint', '')} · explanation: {step.get('explanation', '')}")
    if q["kind"] != "multistep":
        key = q.get("answer") or (f"option {q['correct_choice']}" if q.get("correct_choice") is not None else "")
        lines.append(f"{indent}    answer: {key or '—'} · {q.get('answer_display', '')}")
    lines.append(f"{indent}    solution: {q.get('solution', '')}")
    return lines


def render_lesson(
    lesson: LessonV2Draft | dict[str, Any],
    questions: dict[int, list[QuestionDraft | dict]] | None = None,
    capstone: QuestionDraft | dict[str, Any] | None = None,
) -> str:
    """Readable text of a draft (or a stored v2 lesson dict + its question pools and capstone, see
    stored_questions) for the reviewer and reports."""
    d = lesson.model_dump() if isinstance(lesson, LessonV2Draft) else lesson
    lines = [f"Title: {d.get('title', '')}", f"Your goal: {d.get('goal_line', '')}", "", "TEACHING SEQUENCE"]
    for i, b in enumerate(d.get("blocks") or [], start=1):
        heading = f" — {b['heading']}" if b.get("heading") else ""
        idea = f"  (declared new idea: {b['new_idea']})" if b.get("new_idea") else ""
        lines += [f"Block {i} [{b['kind']}]{heading}{idea}", b.get("body", "")]
        lines += _visual_lines(b.get("visual"))
        if b.get("check"):
            lines += _check_lines(b["check"])
        lines.append("")
    notation = d.get("notation") or []
    if notation:
        lines += ["Notation: " + "; ".join(f"{n['symbol']} = {n['meaning']}" for n in notation)]
    lines += [f"Go deeper (optional section): {d.get('deeper') or '—'}"]
    lines += [f"Connection: {d.get('connection', '')}", "", "PRACTICE (after the sequence)"]
    for k, kp in enumerate(d.get("knowledge_points") or []):
        lines += [
            f"Knowledge point {k + 1}: {kp.get('title', '')} — target_questions {kp.get('target_questions', '?')} "
            "(served first; the rest only after a miss and in reviews)",
            f"  Reminder: {kp.get('explanation', '')}",
        ]
        example = kp.get("worked_example") or {}
        lines.append(f"  Worked example: {example.get('problem', '')}")
        lines += [f"    · {s.get('math', '')}  — {s.get('why', '')}" for s in example.get("steps") or []]
        lines.append(f"    takeaway: {example.get('takeaway', '')}")
        pool = kp.get("questions") if questions is None else questions.get(k, [])
        for q in pool or []:
            lines += _question_lines(q)
        lines.append("")
    cap = d.get("capstone") if capstone is None and questions is None else capstone
    lines.append("CAPSTONE (the final multistep problem, after the practice)")
    lines += _question_lines(cap) if cap else ["  (none)"]
    return "\n".join(lines)


def stored_questions(skill_id: str) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any] | None]:
    """The skill's current (not retired) questions as render_lesson takes them: pools by KP and the capstone."""
    rows = db.query(
        "SELECT * FROM questions WHERE skill_id = ? AND key NOT LIKE 'old-%' ORDER BY kp_index, rowid", (skill_id,)
    )
    pools: dict[int, list[dict[str, Any]]] = {}
    capstone = None
    for r in rows:
        q = {
            **r,
            "choices": db.loads(r["choices_json"], []),
            "steps": db.loads(r.get("steps_json"), []) or [],
            "key": r["key"] + (" (flagged)" if r["flagged"] else ""),
        }
        if r.get("role") == "capstone":
            capstone = q
        else:
            pools.setdefault(r["kp_index"], []).append(q)
    return pools, capstone


def review_request(
    spec: TeachingSpec, draft: LessonV2Draft, *, key: str, revised: bool, prereqs: list[str] | None = None
) -> str:
    lines = [
        f"Review this lesson draft ({'revised after an earlier review' if revised else 'first draft'}).",
        "",
        "SPEC",
        f"Skill {key}: {spec.title}",
        f"Objective: {spec.objective}",
        f"Kind: {spec.kind}",
        "Assumed prerequisites — the learner knows ONLY these (plus ordinary school algebra):",
        *([f"- {a}" for a in spec.assumed_prerequisites] or ["- (none)"]),
        *(
            ["In-chapter prerequisite skills the learner has learned (their methods count as taught):", *prereqs]
            if prereqs
            else []
        ),
        "Out of scope:",
        *([f"- {o}" for o in spec.out_of_scope] or ["- (none)"]),
        "Planned reasoning sequence:",
        *[f"{i}. {s}" for i, s in enumerate(spec.reasoning_sequence, start=1)],
        f"Success check (the planner's idea for the capstone; the capstone rules take precedence): {spec.success_check}",
        f"Starting point (the outcome problem the lesson should pose and solve): {spec.starting_point}",
        *([MISCONCEPTIONS_HEADER] + [f"- {m}" for m in spec.misconceptions] if spec.misconceptions else []),
        "",
        "COUNTS FOR THE DENSITY PASS (from the writer's own labels — judge the content too)",
        *idea_lines(draft),
        "",
        "LESSON DRAFT",
        render_lesson(draft),
    ]
    return "\n".join(lines)


def idea_lines(draft: LessonV2Draft) -> list[str]:
    """The blocks' declared new ideas, counted against the lesson budget (the reviewer's density pass)."""
    ideas = [(i, b.new_idea.strip()) for i, b in enumerate(draft.blocks, start=1) if b.new_idea.strip()]
    lines = [f"{len(draft.blocks)} blocks; {len(ideas)} declare a new idea (budget: about {IDEA_BUDGET} per lesson)."]
    lines += [f"- block {i}: {idea}" for i, idea in ideas]
    words = sum(prose_words(b.body) for b in draft.blocks)
    lines.append(
        f"Length: {len(draft.blocks)} blocks (at most {TARGET_BLOCKS}), about {words} words of prose outside the math "
        f"(about {TARGET_PROSE_WORDS})."
    )
    return lines


async def review_lesson(
    spec: TeachingSpec, draft: LessonV2Draft, *, key: str = "", revised: bool = False, prereqs: list[str] | None = None
) -> LessonReview:
    """Missing-bridge and density review (role "graph", high effort) of a draft as a first explanation."""
    return await llm.structured(
        role="graph",
        instructions=REVIEWER_INSTRUCTIONS,
        text=review_request(spec, draft, key=key or spec.local_id, revised=revised, prereqs=prereqs),
        schema=LessonReview,
        max_output_tokens=32000,
        label=f"review {key or spec.local_id}{' (revised)' if revised else ''}",
        stream=True,
    )


def needs_revision(review: LessonReview) -> bool:
    """Revise once when the review reports any missing bridge (minor ones too — e.g. notation used before it is
    introduced), any density problem (too many new ideas, a second method, something used before it is taught, …),
    an exercise needing an untaught method, out-of-scope content, reasoning that sits only in the optional section,
    or a math error."""
    return bool(
        review.issues
        or review.density_issues
        or review.untaught_method_exercises
        or review.out_of_scope_violations
        or review.optional_content_needed
        or review.math_errors
    )


def as_review(review: LessonReview | dict[str, Any]) -> LessonReview:
    """A review object (reviews stored before the density pass existed have no density_issues)."""
    if isinstance(review, LessonReview):
        return review
    return LessonReview.model_validate({"density_issues": [], **review})


def review_lines(review: LessonReview | dict[str, Any] | None) -> list[str]:
    if review is None:
        return []
    r = as_review(review)
    lines = []
    for issue in r.issues:
        lines.append(
            f"- [{issue.severity}] {issue.location}: the learner must infer {issue.inference_required} — "
            f"{issue.why_insufficient} → repair: {issue.repair}"
        )
    for issue in r.density_issues:
        lines.append(
            f"- [density, {issue.severity}] {issue.location}: the learner must handle {issue.inference_required} — "
            f"{issue.why_insufficient} → repair: {issue.repair}"
        )
    for label, items in (
        ("exercise needs an untaught method", r.untaught_method_exercises),
        ("out of scope", r.out_of_scope_violations),
        ("needed reasoning only in 'deeper'", r.optional_content_needed),
        ("math error", r.math_errors),
    ):
        lines += [f"- [{label}] {item}" for item in items]
    if r.split_recommended:
        lines.append(f"- [split recommended] {r.split_reason}")
    return lines


def review_summary(review: LessonReview | dict[str, Any] | None) -> str:
    if review is None:
        return "not reviewed"
    r = as_review(review)
    counts = Counter(i.severity for i in r.issues)
    parts = [f"{counts['blocking']} blocking", f"{counts['minor']} minor"]
    if r.density_issues:
        dense = Counter(i.severity for i in r.density_issues)
        parts.append(f"{len(r.density_issues)} density ({dense['blocking']} blocking)")
    for label, items in (
        ("untaught-method exercises", r.untaught_method_exercises),
        ("out of scope", r.out_of_scope_violations),
        ("needed-but-optional", r.optional_content_needed),
        ("math errors", r.math_errors),
    ):
        if items:
            parts.append(f"{len(items)} {label}")
    if r.split_recommended:
        parts.append("split recommended")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def clean_inline_check(check: InlineCheck) -> dict[str, Any]:
    """Validate an inline check with the practice-question rules of its kind ('reflect' needs a model answer or an
    explanation). Returns the stored dict (with the answer: it never leaves the server). Raises InvalidQuestion."""
    explanation = check.explanation.strip()
    if check.kind == "reflect":
        if not check.prompt.strip():
            raise InvalidQuestion("empty prompt")
        if not (check.answer_display.strip() or explanation):
            raise InvalidQuestion("a reflect check needs a model answer or an explanation")
        return {
            "kind": "reflect",
            "prompt": check.prompt.strip(),
            "choices": [],
            "correct_choice": None,
            "answer": "",
            "answer_display": check.answer_display.strip(),
            "explanation": explanation,
        }
    draft = QuestionDraft(
        key="check",
        tier=1,
        kind=check.kind,
        prompt=check.prompt,
        choices=check.choices,
        correct_choice=check.correct_choice,
        answer=check.answer,
        answer_display=check.answer_display,
        tolerance=None,
        hints=[],
        solution=explanation or check.answer_display or "-",
        checklist=[],
        targets="",
        source_exercise=None,
        steps=[],
    )
    q = clean_question(draft)
    return {
        "kind": q.kind,
        "prompt": q.prompt.strip(),
        "choices": q.choices,
        "correct_choice": q.correct_choice,
        "answer": q.answer,
        "answer_display": q.answer_display.strip(),
        "explanation": explanation,
    }


def content_problems(draft: LessonV2Draft) -> list[str]:
    """The content rules a strict validation may not reach after its first problem: no free_response anywhere, and a
    valid multistep capstone with no step whose answer is known in advance (capstone_problems)."""
    problems = [
        free_response_problem(i, [q.key or "?" for q in kp.questions if q.kind == "free_response"])
        for i, kp in enumerate(draft.knowledge_points)
        if any(q.kind == "free_response" for q in kp.questions)
    ]
    return problems + capstone_problems(draft.capstone)


def clean_goal(text: str) -> str:
    return _GOAL_PREFIX.sub("", text or "", count=1).strip()


async def validate_v2(
    draft: LessonV2Draft, spec: TeachingSpec, figures: list[FigureInfo], *, proof: bool = False, strict: bool = True
) -> tuple[dict[str, Any], list[list[QuestionDraft]], QuestionDraft | None, list[str]]:
    """Validate a draft: inline checks (rules of their kind), block visuals (finalized like v1 visual plans), the
    knowledge points + questions with the v1 machinery (targets, no free_response, choice cap, refs) and the capstone
    (a multistep question). A faded_example's check must be machine-checked (never 'reflect'). strict=True raises
    InvalidLesson on the first problem; strict=False repairs what it can (a check block without a valid check becomes
    an explain/demonstrate block, a faded_example with a reflect check a check block, free_response questions are
    dropped, an invalid capstone is left out) and reports notes. Returns (lesson_json without plan/review, question
    pools in serving order, the capstone or None, notes)."""
    notes: list[str] = []
    if len(draft.blocks) > MAX_BLOCKS:
        notes.append(f"kept the first {MAX_BLOCKS} of {len(draft.blocks)} blocks")
    blocks: list[dict[str, Any]] = []
    for i, block in enumerate(draft.blocks[:MAX_BLOCKS]):
        where = f"block {i + 1}" + (f" ({block.heading.strip()})" if block.heading and block.heading.strip() else "")
        check = None
        if block.check is not None:
            try:
                check = clean_inline_check(block.check)
            except InvalidQuestion as e:
                if strict:
                    raise InvalidLesson(f"{where}: invalid inline check — {e}") from e
                notes.append(f"{where}: dropped an invalid inline check ({e})")
        kind = block.kind
        if kind in CHECK_BLOCKS and check is None:
            if strict:
                raise InvalidLesson(f"{where}: a {kind} block needs a valid check")
            kind = "demonstrate" if kind == "faded_example" else "explain"
            notes.append(f"{where}: {block.kind} block without a valid check shown as {kind}")
        elif kind == "faded_example" and check is not None and check["kind"] not in FADED_CHECK_KINDS:
            # the learner must carry out the missing step and get it graded — "think it through, then reveal" is not
            # a partially completed example (complete example → faded example → independent practice)
            if strict:
                raise InvalidLesson(
                    f"{where}: a faded_example's check must be machine-checked — the learner enters the result of the "
                    f"missing step (numeric / expression, or true_false_set / multiple_choice for a decision), not "
                    f"'{check['kind']}'; put an explanation question in its own check block"
                )
            kind = "check"
            notes.append(f"{where}: faded_example with a '{check['kind']}' check shown as a check block")
        visual = None
        if block.visual is not None:
            viz, warnings = await asyncio.wait_for(
                asyncio.to_thread(finalize_plan, block.visual, figures, None), timeout=FINALIZE_TIMEOUT
            )
            notes += [f"{where} visual: {w}" for w in warnings]
            visual = viz if viz["format"] != "none" else None
        body = block.body.strip()
        if not body and visual is None and check is None:
            notes.append(f"{where}: empty block dropped")
            continue
        blocks.append(
            {
                "kind": kind,
                "job": block.job.strip(),
                "new_idea": block.new_idea.strip(),
                "heading": (block.heading or "").strip() or None,
                "body": body,
                "visual": visual,
                "check": check,
            }
        )
    if len(blocks) < MIN_BLOCKS:
        raise InvalidLesson(f"the teaching sequence has only {len(blocks)} usable blocks (at least {MIN_BLOCKS})")
    checks = [b["check"] for b in blocks if b["check"]]
    if not checks:
        if strict:
            raise InvalidLesson("the teaching sequence has no inline check (1–3 are needed before the practice)")
        notes.append("the teaching sequence has no inline check")
    elif len(checks) > 4:
        notes.append(f"{len(checks)} inline checks (1–3 planned)")
    if checks and not any(c["kind"] == "reflect" for c in checks):
        notes.append("no explanation ('reflect') check")
    if not any(b["kind"] == "faded_example" for b in blocks):
        notes.append("no faded example")
    if not clean_goal(draft.goal_line):
        notes.append("empty goal line")

    as_v1 = LessonDraft(
        why_it_matters="",
        idea="",
        notation=draft.notation,
        knowledge_points=draft.knowledge_points,
        missing_steps="",
        common_mistakes=[],
        capstone=draft.capstone,
        connection=draft.connection,
        source_refs=draft.source_refs or spec.source_refs,
    )
    base, pools, kp_notes = validate_lesson(as_v1, proof=proof, strict=strict)
    notes += [n for n in kp_notes if not n.startswith("proof skill without a multistep")]
    capstone, cap_notes = validate_capstone(draft.capstone, strict=strict)
    notes += cap_notes
    lesson = {
        "format": "v2",
        "title": spec.title,
        "objective": spec.objective,
        "goal_line": clean_goal(draft.goal_line),
        "blocks": blocks,
        "notation": base["notation"],
        "knowledge_points": base["knowledge_points"],
        "common_mistakes": [],
        "connection": draft.connection.strip(),
        "deeper": draft.deeper.strip(),
        "source_refs": base["source_refs"] or clean_source_refs(spec.source_refs),
        "why_it_matters": "",
        "idea": "",
        "missing_steps": "",
    }
    return lesson, pools, capstone, notes


def prose_words(text: str) -> int:
    """Words of prose outside math and tables (the reading load of a block)."""
    return len(_WORD.findall(_TABLE_ROW.sub(" ", _MATH_SPAN.sub(" ", text or ""))))


def served_questions(draft: LessonV2Draft) -> list[QuestionDraft]:
    """The questions served first: the first target_questions of each knowledge point, in order."""
    return [q for kp in draft.knowledge_points for q in kp.questions[: clamp_target(kp.target_questions)]]


def quality_problems(draft: LessonV2Draft) -> list[str]:
    """Rules a draft must meet before it is stored as is (each one sends it to the revision; a last draft that still
    breaks them is stored anyway): a teaching sequence of at most MAX_TEACHING_BLOCKS blocks and MAX_PROSE_WORDS words
    of prose, and questions served first that make the learner produce answers — at most one choice question among
    them and at least one typed answer."""
    problems = []
    n, words = len(draft.blocks), sum(prose_words(b.body) for b in draft.blocks)
    if n > MAX_TEACHING_BLOCKS or words > MAX_PROSE_WORDS:
        problems.append(
            f"the teaching sequence is too long ({n} blocks, about {words} words of prose; at most {TARGET_BLOCKS} "
            f"blocks and about {TARGET_PROSE_WORDS} words): merge or cut blocks that repeat a move on another case, "
            "duplicate a worked example or restate the generalize block"
        )
    served = served_questions(draft)
    choice = [q.key for q in served if is_choice_based(q.kind, len(q.choices))]
    if len(choice) > 1:
        problems.append(
            f"{len(choice)} of the questions served first are choice questions ({', '.join(choice)}); at most one — "
            "make the others typed (the predicted, corrected or missing value)"
        )
    if served and not any(q.kind in ("numeric", "expression", "multistep") for q in served):
        problems.append(
            "no question served first asks for a typed answer: lead knowledge point 1 with one independent, typed "
            "application of the method on fresh numbers"
        )
    return problems


def blocking_count(review: LessonReview) -> int:
    """Problems a stored lesson must not keep: blocking issues of either pass, exercises needing an untaught method,
    math errors, out-of-scope content, needed reasoning that sits only in 'deeper'."""
    return (
        sum(1 for i in [*review.issues, *review.density_issues] if i.severity == "blocking")
        + len(review.untaught_method_exercises)
        + len(review.math_errors)
        + len(review.out_of_scope_violations)
        + len(review.optional_content_needed)
    )


def blocked_locations(review: LessonReview) -> tuple[dict[str, str], list[str]]:
    """Question keys the review blocks (key → reason) and its blocking findings about the capstone."""
    keys: dict[str, str] = {}
    capstone: list[str] = []
    for issue in [*review.issues, *review.density_issues]:
        if issue.severity != "blocking":
            continue
        text = f"{issue.location}: {issue.why_insufficient} → repair: {issue.repair}"
        for key in _QUESTION_KEY.findall(issue.location):
            keys.setdefault(key, text)
        if _CAPSTONE_LOCATION.search(issue.location):
            capstone.append(text)
    for item in [*review.untaught_method_exercises, *review.math_errors]:
        for key in _QUESTION_KEY.findall(item):
            keys.setdefault(key, item)
        if _CAPSTONE_LOCATION.search(item.split(":", 1)[0]):
            capstone.append(item)
    return keys, capstone


def drop_blocked_questions(draft: LessonV2Draft, blocked: dict[str, str]) -> tuple[LessonV2Draft, list[str]]:
    """Leave out the questions the final review still blocks, as long as each knowledge point keeps target + 1
    questions (one spare for a miss). A blocked question served first is replaced by the next question of its point."""
    notes: list[str] = []
    points = []
    for kp in draft.knowledge_points:
        target = clamp_target(kp.target_questions)
        questions = list(kp.questions)
        # served-first questions first: they are what the learner certainly meets
        for q in sorted((q for q in kp.questions if q.key in blocked), key=lambda q: kp.questions.index(q)):
            if len(questions) - 1 < target + 1:
                notes.append(f"kept {q.key} although the final review blocks it (its knowledge point has no spare left)")
                continue
            questions.remove(q)
            notes.append(f"left out {q.key} — final review: {blocked[q.key][:200]}")
        points.append(kp.model_copy(update={"questions": questions}))
    return draft.model_copy(update={"knowledge_points": points}), notes


async def draft_problems(draft: LessonV2Draft, spec: TeachingSpec, figures: list[FigureInfo], *, proof: bool) -> list[str]:
    """Strict validation as a list of problems ([] = usable as is): the first problem the strict pass meets, plus the
    content rules it may not have reached (free_response questions, the capstone) — one revision fixes them all."""
    problems: list[str] = []
    try:
        await validate_v2(draft, spec, figures, proof=proof, strict=True)
    except (InvalidLesson, InvalidQuestion) as e:
        problems.append(str(e))
    for problem in content_problems(draft) + quality_problems(draft):
        if problem not in problems:
            problems.append(problem)
    return problems


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_v2_lesson(
    skill_id: str,
    lesson: dict[str, Any],
    pools: list[list[QuestionDraft]],
    *,
    spec: TeachingSpec,
    figure_ids: list[str],
    capstone: QuestionDraft | None = None,
) -> int:
    """Store a v2 lesson in one transaction, like persist_lesson: active tasks on the skill are abandoned, questions of
    the previous version deleted (answered ones retired), the new questions inserted (the capstone with role
    'capstone' and kp_index = the number of knowledge points), and the skill row updated from the spec (title,
    objective, kind, difficulty, minutes, pages, refs, misconceptions; summary = the goal line).
    Returns the number of abandoned tasks."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        abandoned = abandon_active_tasks(conn, skill_id)
        retire_questions(conn, skill_id)
        for kp_index, pool in enumerate(pools):
            insert_questions(conn, skill_id, kp_index, pool)
        if capstone is not None:
            insert_questions(conn, skill_id, len(pools), [capstone], role="capstone")
        conn.execute(
            "UPDATE skills SET title = ?, objective = ?, kind = ?, difficulty = ?, est_minutes = ?, source_pages_json = ?, "
            "source_refs_json = ?, misconceptions_json = ?, summary = ?, figure_ids_json = ?, lesson_json = ?, "
            "lesson_status = 'ready', viz_json = ? WHERE id = ?",
            (
                spec.title,
                spec.objective,
                spec.kind,
                spec.difficulty,
                spec.est_minutes,
                db.dumps(spec.source_pages),
                db.dumps(lesson.get("source_refs") or spec.source_refs),
                db.dumps(spec.misconceptions),
                lesson.get("goal_line") or spec.objective,
                db.dumps(figure_ids),
                db.dumps(lesson),
                db.dumps(V2_VIZ),
                skill_id,
            ),
        )
    return abandoned


# ---------------------------------------------------------------------------
# Orchestration: write → review → (revise once → review) → validate → persist → verify
# ---------------------------------------------------------------------------


@dataclass
class V2Result:
    key: str
    skill_id: str
    ok: bool = False
    revised: bool = False
    revisions: int = 0
    initial_review: LessonReview | None = None
    final_review: LessonReview | None = None
    notes: list[str] = field(default_factory=list)
    verify_notes: list[str] = field(default_factory=list)
    error: str | None = None
    abandoned_tasks: int = 0


async def generate_v2_lesson(ctx: BuildContext, skill_id: str, spec: TeachingSpec, prefix: str) -> V2Result:
    """Write, review, revise once when needed, validate, persist and verify a v2 lesson for one skill. A failure keeps
    a previously ready lesson; a skill without a lesson is marked failed (the app's Resume writes a v1 lesson)."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if skill is None:
        raise LookupError(f"Skill {skill_id} not found")
    key = skill["key"]
    result = V2Result(key=key, skill_id=skill_id)
    course, tracker = ctx.course, ctx.tracker
    # a spec stored before plan validation rewrote sibling local ids ("a (S2a): …") is regenerated with the keys
    course_keys = [r["key"] for r in db.query("SELECT key FROM skills WHERE course_id = ?", (skill["course_id"],))]
    assumed = normalize_sibling_refs(spec.assumed_prerequisites, sibling_keys(key, course_keys))
    if assumed != list(spec.assumed_prerequisites):
        spec = spec.model_copy(update={"assumed_prerequisites": assumed})
    figures = spec_figures(course, skill, spec)
    proof = is_proof_skill({"kind": spec.kind, "objective": spec.objective})
    prereqs, _ = _neighbours(skill_id)
    prereq_lines = _skill_lines(prereqs)
    try:
        draft = await _write_with_retry(course, spec, skill, prefix, figures)
        problems = await draft_problems(draft, spec, figures, proof=proof)
        review = await review_lesson(spec, draft, key=key, prereqs=prereq_lines)
        result.initial_review = final_review = review
        final = draft
        for round_ in range(MAX_REVISIONS):  # the second round only for what is still blocking (or invalid)
            if not (problems or (needs_revision(final_review) if round_ == 0 else blocking_count(final_review))):
                break
            reasons = review_summary(final_review) + (f"; validation: {'; '.join(problems)}" if problems else "")
            tracker.log(f"Lesson {key} (v2): revising{' again' if round_ else ''} — {reasons}")
            revised = await write_lesson(
                course, spec, skill=skill, prefix=prefix, figures=figures, revision=(final, final_review, problems)
            )
            revised_problems = await draft_problems(revised, spec, figures, proof=proof)
            if revised_problems and not problems:
                result.notes.append(f"revision {round_ + 1} did not validate ({'; '.join(revised_problems)}); kept the previous draft")
                break
            revised_review = await review_lesson(spec, revised, key=key, revised=True, prereqs=prereq_lines)
            if round_ and not problems and blocking_count(revised_review) > blocking_count(final_review):
                result.notes.append("the second revision left more blocking issues than the first; kept the first")
                break
            final, final_review, problems = revised, revised_review, revised_problems
            result.revised, result.revisions = True, result.revisions + 1
        result.final_review = final_review
        blocked, capstone_blockers = blocked_locations(final_review)
        final, dropped = drop_blocked_questions(final, blocked)
        result.notes += dropped
        cap_problems = capstone_problems(final.capstone) + capstone_blockers
        if cap_problems:  # rewrite just the capstone rather than drop steps (or end the lesson without one)
            try:
                rewritten = await rewrite_capstone(course, skill, spec, render_lesson(final), prefix, cap_problems)
                final = final.model_copy(update={"capstone": rewritten})
                result.notes.append(f"capstone rewritten ({'; '.join(cap_problems)})")
            except Exception as e:  # noqa: BLE001 — the lenient validation below keeps what it can
                result.notes.append(f"capstone repair failed ({e})")
        lesson, pools, capstone, notes = await validate_v2(final, spec, figures, proof=proof, strict=False)
        result.notes += notes
    except Exception as e:  # noqa: BLE001 — one skill's failure never stops the others
        log.exception("v2 lesson %s failed", key)
        result.error = str(e)
        if skill["lesson_status"] != "ready":
            db.execute("UPDATE skills SET lesson_status = 'failed' WHERE id = ? AND lesson_status != 'ready'", (skill_id,))
            tracker.log(f"Lesson {key} (v2) failed: {e} — Resume in the app writes a v1 lesson", level=logging.ERROR)
        else:
            tracker.log(f"Lesson {key} (v2) failed: {e} — the previous lesson is kept", level=logging.ERROR)
        return result

    lesson["plan"] = spec.model_dump()
    lesson["review"] = final_review.model_dump()
    if result.revised and result.initial_review is not None:
        lesson["initial_review"] = result.initial_review.model_dump()
    lesson, figure_ids, figure_notes = resolve_figures(lesson, {**skill, "source_pages_json": db.dumps(spec.source_pages)}, figures)
    result.notes += figure_notes
    visual_figures = [b["visual"]["book_figure_id"] for b in lesson["blocks"] if b.get("visual") and b["visual"].get("book_figure_id")]
    figure_ids = list(dict.fromkeys(figure_ids + visual_figures))
    result.abandoned_tasks = persist_v2_lesson(skill_id, lesson, pools, spec=spec, figure_ids=figure_ids, capstone=capstone)
    ctx.abandoned_tasks += result.abandoned_tasks
    for note in result.notes:
        tracker.log(f"Lesson {key} (v2): {note}", level=logging.WARNING)
    n_checks = sum(1 for b in lesson["blocks"] if b.get("check"))
    targets = "/".join(str(kp["target_questions"]) for kp in lesson["knowledge_points"])
    tracker.log(
        f"Lesson {key} (v2) ready — {len(lesson['blocks'])} blocks, {n_checks} checks, {len(pools)} knowledge points "
        f"(targets {targets}), {sum(len(p) for p in pools)} questions, "
        + (f"a {len(capstone.steps)}-step capstone" if capstone else "no capstone")
        + f" · final review: {review_summary(final_review)}"
    )
    result.verify_notes = await verify_skill(skill_id)
    if capstone_defects(skill_id):  # e.g. withheld by verification: one capstone-only repair, verified again
        result.verify_notes.append(await repair_stored_capstone(ctx, skill_id, prefix))
    for note in result.verify_notes:
        tracker.log(f"Verify {key}: {note}")
    result.ok = True
    return result


async def _write_with_retry(
    course: CourseInfo, spec: TeachingSpec, skill: dict[str, Any], prefix: str, figures: list[FigureInfo]
) -> LessonV2Draft:
    error: Exception | None = None
    for attempt in range(2):
        try:
            return await write_lesson(course, spec, skill=skill, prefix=prefix, figures=figures)
        except Exception as e:  # noqa: BLE001 — retried once
            error = e
            log.warning("v2 lesson %s attempt %d failed: %s", skill["key"], attempt + 1, e)
    assert error is not None
    raise error


# ---------------------------------------------------------------------------
# Capstone repair: rewrite only the final problem instead of dropping its steps or serving the lesson without one
# ---------------------------------------------------------------------------

_STEP_DEFAULTS: dict[str, Any] = {
    "choices": [], "correct_choice": None, "answer": "", "answer_display": "", "tolerance": None, "hint": "", "explanation": ""
}


def question_draft(row: dict[str, Any]) -> QuestionDraft:
    """A stored question row as a QuestionDraft (to re-validate or repair it)."""
    return QuestionDraft(
        key=row["key"],
        tier=row["tier"],
        kind=row["kind"],
        prompt=row["prompt"],
        choices=db.loads(row["choices_json"], []),
        correct_choice=row["correct_choice"],
        answer=row["answer"] or "",
        answer_display=row["answer_display"] or "",
        tolerance=row["tolerance"],
        hints=db.loads(row["hints_json"], []),
        solution=row["solution"] or "",
        checklist=db.loads(row["checklist_json"], []),
        targets=row["targets"] or "",
        source_exercise=row["source_exercise"],
        steps=[MultistepStep.model_validate(_STEP_DEFAULTS | s) for s in db.loads(row.get("steps_json"), []) or []],
    )


def capstone_defects(skill_id: str) -> list[str]:
    """What is wrong with a skill's stored capstone ([] = fine): missing, withheld by verification, or failing the
    capstone rules (a step whose answer is known in advance, a reference to a step that does not exist, …)."""
    row = db.query_one(
        "SELECT * FROM questions WHERE skill_id = ? AND role = 'capstone' AND key NOT LIKE 'old-%' ORDER BY rowid DESC",
        (skill_id,),
    )
    if row is None:
        return ["the lesson has no capstone"]
    defects = [f"it is withheld from the learner — {row['flag_reason']}"] if row["flagged"] else []
    try:
        return defects + capstone_problems(question_draft(row))
    except ValueError as e:
        return defects + [f"capstone: unreadable ({e})"]


async def rewrite_capstone(
    course: CourseInfo, skill: dict[str, Any], spec: TeachingSpec, lesson_text: str, prefix: str, problems: list[str]
) -> QuestionDraft:
    """Writer call (role "lesson") for a new capstone only, validated strictly; one retry with the new draft's
    problems. Raises InvalidLesson when both drafts fail."""
    base = "\n".join(
        [
            write_request(course, skill, spec, spec_figures(course, skill, spec)),
            "",
            CAPSTONE_REPAIR_NOTE,
            "## The lesson (final except its capstone)",
            lesson_text,
            "",
            "## Problems with the current capstone",
            *[f"- {p}" for p in problems],
        ]
    )
    request, issues = base, problems
    for attempt in range(2):
        out = await llm.structured(
            role="lesson",
            instructions=WRITER_INSTRUCTIONS,
            shared_prefix=prefix,
            text=request,
            schema=CapstoneRepair,
            max_output_tokens=16000,
            label=f"capstone {skill['key']}{' (retry)' if attempt else ''}",
            stream=True,
        )
        issues = capstone_problems(out.capstone)
        if not issues:
            capstone, _ = validate_capstone(out.capstone, strict=True)
            assert capstone is not None
            return capstone
        request = base + "\n\n## Your new capstone was rejected\n" + "\n".join(f"- {i}" for i in issues)
    raise InvalidLesson("capstone repair failed: " + "; ".join(issues))


def replace_capstone(skill_id: str, capstone: QuestionDraft) -> int:
    """Store a new capstone in one transaction: active tasks on the skill are abandoned (as when a lesson is
    replaced), the old capstone is retired when the learner answered it (kept as flagged history, key 'old-capN') and
    deleted otherwise. Returns the number of abandoned tasks."""
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (skill_id,))["lesson_json"], {}) or {}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        abandoned = abandon_active_tasks(conn, skill_id)
        old = conn.execute(
            "SELECT id FROM questions WHERE skill_id = ? AND role = 'capstone' AND key NOT LIKE 'old-%'", (skill_id,)
        ).fetchall()
        for row in old:
            if conn.execute("SELECT 1 FROM attempts WHERE question_id = ? LIMIT 1", (row["id"],)).fetchone():
                n = conn.execute(
                    "SELECT COUNT(*) FROM questions WHERE skill_id = ? AND key LIKE 'old-cap%'", (skill_id,)
                ).fetchone()[0]
                conn.execute(
                    "UPDATE questions SET flagged = 1, flag_reason = ?, key = ? WHERE id = ?",
                    (RETIRED_REASON, f"old-cap{n + 1}", row["id"]),
                )
            else:
                conn.execute("DELETE FROM questions WHERE id = ?", (row["id"],))
        insert_questions(conn, skill_id, len(lesson.get("knowledge_points") or []), [capstone], role="capstone")
    return abandoned


async def repair_stored_capstone(
    ctx: BuildContext, skill_id: str, prefix: str, notes: list[str] | None = None, *, force: bool = False
) -> str:
    """Rewrite a stored v2 lesson's capstone when capstone_defects finds a problem (or `notes` name one, or force),
    store and verify it; a new capstone that verification withholds gets one more rewrite. The old capstone stays
    when the rewrite fails. Returns a one-line outcome for the report."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    key = skill["key"]
    lesson = db.loads(skill["lesson_json"], None) or {}
    spec = stored_spec(lesson)
    if spec is None:
        return f"{key}: not a v2 lesson — capstone left as is"
    problems = capstone_defects(skill_id) + list(notes or [])
    if not problems and not force:
        return f"{key}: capstone fine — left as is"
    for _attempt in range(2):
        pools, current = stored_questions(skill_id)
        try:
            capstone = await rewrite_capstone(
                ctx.course, skill, spec, render_lesson(lesson, pools, current), prefix, problems or ["a rewrite was requested"]
            )
        except Exception as e:  # noqa: BLE001 — the old capstone stays
            log.warning("capstone repair %s failed: %s", key, e)
            return f"{key}: capstone repair failed ({e}) — the old capstone stays"
        ctx.abandoned_tasks += replace_capstone(skill_id, capstone)
        await verify_skill(skill_id)
        problems = capstone_defects(skill_id)
        if not problems:
            return f"{key}: new {len(capstone.steps)}-step capstone stored and verified"
    return f"{key}: new capstone stored, but {'; '.join(problems)}"


def stored_spec(lesson: dict[str, Any]) -> TeachingSpec | None:
    """The TeachingSpec stored with a v2 lesson (None for v1 lessons)."""
    if lesson.get("format") != "v2" or not lesson.get("plan"):
        return None
    try:
        return TeachingSpec.model_validate(lesson["plan"])
    except Exception:  # noqa: BLE001 — a damaged plan means: no v2 regeneration
        return None


def is_v2(lesson_json: str | None) -> bool:
    return bool(lesson_json) and (db.loads(lesson_json, {}) or {}).get("format") == "v2"


__all__ = [
    "BridgeIssue",
    "IDEA_BUDGET",
    "blocked_locations",
    "blocking_count",
    "capstone_defects",
    "drop_blocked_questions",
    "prose_words",
    "quality_problems",
    "served_questions",
    "question_draft",
    "repair_stored_capstone",
    "replace_capstone",
    "rewrite_capstone",
    "InvalidPlan",
    "clean_capstone",
    "content_problems",
    "PlanGraph",
    "V2Result",
    "clean_inline_check",
    "as_review",
    "describe_plan",
    "generate_v2_lesson",
    "idea_lines",
    "is_v2",
    "learner_misses",
    "needs_revision",
    "new_keys",
    "persist_v2_lesson",
    "plan_graph",
    "plan_request",
    "plan_skill",
    "render_lesson",
    "review_lesson",
    "review_lines",
    "review_summary",
    "stored_questions",
    "stored_spec",
    "validate_plan",
    "validate_v2",
    "write_lesson",
    "write_request",
]
