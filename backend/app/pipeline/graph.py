"""Graph stage: one high-reasoning call over the chapter text → KnowledgeGraphDraft, then deterministic validation
with networkx (SPEC §4.1) and persistence of skills, edges and foundations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import networkx as nx

from .. import db, llm
from ..learning import state
from ..llm_schemas import Foundation, KnowledgeGraphDraft, SkillDraft
from .context import BuildContext, BuildError, chapter_text, course_figures, figure_db_id, key_number
from .prompts import GRAPH_INSTRUCTIONS

log = logging.getLogger("ua.pipeline.graph")

MIN_WEIGHT, MAX_WEIGHT = 0.05, 1.0
SANE_SKILLS = (5, 40)


@dataclass
class GraphEdge:
    src: str  # prerequisite key
    dst: str  # dependent key
    weight: float
    reduced: bool


@dataclass
class ValidatedGraph:
    skills: list[SkillDraft]  # renumbered S1..Sn in topological order; prerequisites cleaned
    edges: list[GraphEdge]
    foundations: list[Foundation]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation (pure, unit-tested)
# ---------------------------------------------------------------------------


def validate_graph(draft: KnowledgeGraphDraft, *, valid_pages: set[int], figure_ids: set[str]) -> ValidatedGraph:
    warnings: list[str] = []
    foundations = _unique_foundations(draft.foundations, warnings)
    foundation_keys = {f.key for f in foundations}
    skills = _unique_skills(draft.skills, warnings)
    if not skills:
        raise BuildError("The knowledge graph has no skills.")
    by_key = {s.key: s for s in skills}

    graph = nx.DiGraph()
    graph.add_nodes_from(by_key)
    for s in skills:
        for link in s.prerequisites:
            if link.skill not in by_key:
                warnings.append(f"{s.key}: dropped prerequisite on unknown skill '{link.skill}'")
                continue
            if link.skill == s.key:
                warnings.append(f"{s.key}: dropped self-prerequisite")
                continue
            weight = min(MAX_WEIGHT, max(MIN_WEIGHT, float(link.weight)))
            if graph.has_edge(link.skill, s.key):
                weight = max(weight, graph.edges[link.skill, s.key]["weight"])
            graph.add_edge(link.skill, s.key, weight=weight)

    _break_cycles(graph, warnings)

    def first_page(key: str) -> int:
        pages = [p for p in by_key[key].source_pages if p in valid_pages]
        return min(pages) if pages else 10**6

    order = list(nx.lexicographical_topological_sort(graph, key=lambda k: (first_page(k), key_number(k), k)))
    reduction = nx.transitive_reduction(graph)
    rename = {old: f"S{i + 1}" for i, old in enumerate(order)}

    cleaned: list[SkillDraft] = []
    for old in order:
        s = by_key[old]
        pages = sorted({p for p in s.source_pages if p in valid_pages})
        if not pages:
            warnings.append(f"{old}: no valid source pages")
        cleaned.append(
            s.model_copy(
                update={
                    "key": rename[old],
                    "source_pages": pages,
                    "figure_ids": [f for f in dict.fromkeys(s.figure_ids) if f in figure_ids],
                    "foundations": [f for f in dict.fromkeys(s.foundations) if f in foundation_keys],
                    "prerequisites": [],
                    "difficulty": min(5, max(1, int(s.difficulty))),
                    "est_minutes": min(30, max(3, int(s.est_minutes))),
                    "source_refs": list(dict.fromkeys(r.strip() for r in s.source_refs if r.strip())),
                }
            )
        )
    edges = [
        GraphEdge(rename[u], rename[v], round(data["weight"], 3), reduction.has_edge(u, v))
        for u, v, data in graph.edges(data=True)
    ]
    edges.sort(key=lambda e: (key_number(e.dst), key_number(e.src)))
    if not SANE_SKILLS[0] <= len(cleaned) <= SANE_SKILLS[1]:
        warnings.append(f"unusual skill count: {len(cleaned)} (expected {SANE_SKILLS[0]}–{SANE_SKILLS[1]})")
    return ValidatedGraph(skills=cleaned, edges=edges, foundations=foundations, warnings=warnings)


def _unique_skills(skills: list[SkillDraft], warnings: list[str]) -> list[SkillDraft]:
    seen: set[str] = set()
    result = []
    for s in skills:
        key = s.key.strip()
        if not key or key in seen:
            new_key = _free_key("S", seen | {x.key.strip() for x in skills})
            warnings.append(f"skill key '{s.key}' is empty or duplicated; renamed to {new_key}")
            key = new_key
        seen.add(key)
        result.append(s.model_copy(update={"key": key}))
    return result


def _unique_foundations(foundations: list[Foundation], warnings: list[str]) -> list[Foundation]:
    seen: set[str] = set()
    result = []
    for f in foundations:
        key = f.key.strip()
        if not key or key in seen:
            warnings.append(f"dropped duplicate foundation '{f.key}'")
            continue
        seen.add(key)
        result.append(f.model_copy(update={"key": key}))
    return result


def _free_key(prefix: str, taken: set[str]) -> str:
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _break_cycles(graph: nx.DiGraph, warnings: list[str]) -> None:
    """Repeatedly remove the lowest-weight edge of some cycle until the graph is a DAG."""
    while True:
        try:
            cycle = nx.find_cycle(graph)
        except nx.NetworkXNoCycle:
            return
        u, v = min(((a, b) for a, b, *_ in cycle), key=lambda e: (graph.edges[e]["weight"], key_number(e[1])))
        warnings.append(f"cycle {' → '.join(a for a, *_ in cycle)}: removed {u} → {v} (weight {graph.edges[u, v]['weight']})")
        graph.remove_edge(u, v)


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


def graph_exists(course_id: str) -> bool:
    return db.query_one("SELECT 1 FROM skills WHERE course_id = ? LIMIT 1", (course_id,)) is not None


async def run_graph_stage(ctx: BuildContext) -> None:
    course, tracker = ctx.course, ctx.tracker
    if graph_exists(course.id):
        n = db.query_one("SELECT COUNT(*) AS n FROM skills WHERE course_id = ?", (course.id,))["n"]
        tracker.update("graph", status="done", done=1, total=1, message=f"{n} skills")
        state.init_progress(course.id)
        return

    tracker.update("graph", status="running", done=0, total=1, message="Designing the skill graph…")
    tracker.log("Building the knowledge graph from the chapter text…")
    figures = course_figures(course)
    text = chapter_text(course) + "\n" + size_guidance(course.id)
    draft: KnowledgeGraphDraft | None = None
    error: Exception | None = None
    for attempt in range(2):
        try:
            draft = await llm.structured(
                role="graph",
                instructions=GRAPH_INSTRUCTIONS,
                text=text,
                schema=KnowledgeGraphDraft,
                max_output_tokens=64000,
                label=f"graph {course.id}",
                stream=True,
            )
            break
        except Exception as e:  # noqa: BLE001 — any failure of the call is retried once, then fails the build
            error = e
            tracker.log(f"Graph attempt {attempt + 1} failed: {e}", level=logging.WARNING)
    if draft is None:
        raise BuildError(f"Knowledge graph failed: {error}")

    graph = validate_graph(draft, valid_pages=set(course.pages), figure_ids={f.local_id for f in figures})
    for warning in graph.warnings:
        tracker.log(f"Graph check: {warning}", level=logging.WARNING)
    persist_graph(course.id, draft, graph)
    state.init_progress(course.id)
    n_reduced = sum(1 for e in graph.edges if e.reduced)
    message = f"{len(graph.skills)} skills · {n_reduced} links · {len(graph.foundations)} foundations"
    tracker.update("graph", status="done", done=1, total=1, message=message)
    tracker.log(f"Graph: {len(graph.skills)} skills · ${ctx.cost('graph'):.2f}")


def skill_target(content_pages: int) -> tuple[int, int]:
    """Skill-count range for a page range: about one skill per page of exposition (SPEC §4.2 granularity)."""
    return max(4, round(0.8 * content_pages)), max(6, round(1.5 * content_pages))


def size_guidance(course_id: str) -> str:
    rows = db.query("SELECT is_exercise_page FROM pages WHERE course_id = ?", (course_id,))
    content = sum(1 for r in rows if not r["is_exercise_page"])
    low, high = skill_target(content)
    return (
        f"## Size target\n{content} pages of exposition and {len(rows) - content} exercise pages → aim for "
        f"{low}–{high} skills."
    )


def persist_graph(course_id: str, draft: KnowledgeGraphDraft, graph: ValidatedGraph) -> dict[str, str]:
    """Store skills, edges and foundations in one transaction; returns key → skill id."""
    ids = {s.key: db.new_id() for s in graph.skills}
    with db.connect() as conn:
        conn.execute("DELETE FROM edges WHERE course_id = ?", (course_id,))
        conn.execute("DELETE FROM skills WHERE course_id = ?", (course_id,))
        conn.execute("DELETE FROM foundations WHERE course_id = ?", (course_id,))
        for order, s in enumerate(graph.skills):
            conn.execute(
                "INSERT INTO skills(id, course_id, key, title, objective, kind, summary, difficulty, est_minutes, "
                "order_index, source_pages_json, source_refs_json, figure_ids_json, foundations_json, "
                "misconceptions_json, lesson_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (
                    ids[s.key],
                    course_id,
                    s.key,
                    s.title.strip(),
                    s.objective.strip(),
                    s.kind,
                    s.summary.strip(),
                    s.difficulty,
                    s.est_minutes,
                    order,
                    db.dumps(s.source_pages),
                    db.dumps(s.source_refs),
                    db.dumps([figure_db_id(course_id, f) for f in s.figure_ids]),
                    db.dumps(s.foundations),
                    db.dumps(s.misconceptions),
                ),
            )
        conn.executemany(
            "INSERT INTO edges(course_id, src, dst, weight, reduced) VALUES (?, ?, ?, ?, ?)",
            [(course_id, ids[e.src], ids[e.dst], e.weight, int(e.reduced)) for e in graph.edges],
        )
        conn.executemany(
            "INSERT INTO foundations(course_id, key, title, description, check_question, check_answer) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(course_id, f.key, f.title, f.description, f.check_question, f.check_answer) for f in graph.foundations],
        )
        conn.execute(
            "UPDATE courses SET big_picture = ?, excluded_json = ? WHERE id = ?",
            (draft.big_picture.strip(), db.dumps(draft.excluded), course_id),
        )
    return ids
