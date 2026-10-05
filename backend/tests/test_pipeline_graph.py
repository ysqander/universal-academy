"""Graph validation (SPEC §4.1): unknown refs, weight clamping, cycle breaking, topological order, reduction."""

from __future__ import annotations

import networkx as nx
import pytest

from app.llm_schemas import Foundation, KnowledgeGraphDraft, PrereqLink, SkillDraft
from app.pipeline.context import BuildError
from app.pipeline.graph import validate_graph


def skill(key: str, pages: list[int], prereqs: list[tuple[str, float]] = (), **extra) -> SkillDraft:
    fields = dict(
        key=key,
        title=f"Skill {key}",
        objective=f"Compute thing {key}",
        kind="computation",
        summary="",
        source_pages=pages,
        source_refs=[],
        figure_ids=[],
        prerequisites=[PrereqLink(skill=k, weight=w) for k, w in prereqs],
        foundations=[],
        difficulty=2,
        est_minutes=10,
        misconceptions=[],
    )
    return SkillDraft(**(fields | extra))


def draft(skills: list[SkillDraft], foundations: list[Foundation] = ()) -> KnowledgeGraphDraft:
    return KnowledgeGraphDraft(
        chapter_title="Ch", big_picture="", foundations=list(foundations), skills=skills, excluded=[]
    )


def edge_map(graph) -> dict[tuple[str, str], tuple[float, bool]]:
    return {(e.src, e.dst): (e.weight, e.reduced) for e in graph.edges}


def test_unknown_refs_self_loops_and_weight_clamping():
    g = validate_graph(
        draft([skill("S1", [10]), skill("S2", [11], [("S1", 3.0), ("S9", 0.5), ("S2", 0.5)])]),
        valid_pages={10, 11},
        figure_ids=set(),
    )
    assert edge_map(g) == {("S1", "S2"): (1.0, True)}
    assert any("unknown skill 'S9'" in w for w in g.warnings)
    assert any("self-prerequisite" in w for w in g.warnings)

    g = validate_graph(draft([skill("S1", [10]), skill("S2", [11], [("S1", 0.0)])]), valid_pages={10, 11}, figure_ids=set())
    assert edge_map(g)[("S1", "S2")][0] == pytest.approx(0.05)


def test_cycle_is_broken_at_the_lowest_weight_edge():
    skills = [
        skill("S1", [10], [("S3", 0.25)]),
        skill("S2", [11], [("S1", 1.0)]),
        skill("S3", [12], [("S2", 0.75)]),
    ]
    g = validate_graph(draft(skills), valid_pages={10, 11, 12}, figure_ids=set())
    edges = edge_map(g)
    assert set(edges) == {("S1", "S2"), ("S2", "S3")}
    assert any("cycle" in w and "removed S3 → S1" in w for w in g.warnings)
    assert nx.is_directed_acyclic_graph(nx.DiGraph(list(edges)))


def test_order_is_topological_with_page_then_key_ties_and_keys_are_renumbered():
    # Model keys out of order: B (page 5) depends on A (page 9); C has no prerequisites but comes first by page.
    skills = [
        skill("S1", [9], title="A"),
        skill("S2", [5], [("S1", 0.5)], title="B"),
        skill("S3", [3], title="C"),
        skill("S10", [3], title="D"),
    ]
    g = validate_graph(draft(skills), valid_pages=set(range(20)), figure_ids=set())
    assert [s.title for s in g.skills] == ["C", "D", "A", "B"]
    assert [s.key for s in g.skills] == ["S1", "S2", "S3", "S4"]
    assert edge_map(g) == {("S3", "S4"): (0.5, True)}


def test_transitive_edges_are_kept_but_not_reduced():
    skills = [
        skill("S1", [1]),
        skill("S2", [2], [("S1", 1.0)]),
        skill("S3", [3], [("S2", 0.5), ("S1", 0.25)]),
    ]
    g = validate_graph(draft(skills), valid_pages={1, 2, 3}, figure_ids=set())
    edges = edge_map(g)
    assert edges[("S1", "S2")] == (1.0, True)
    assert edges[("S2", "S3")] == (0.5, True)
    assert edges[("S1", "S3")] == (0.25, False)


def test_fields_are_sanitized_and_duplicates_renamed():
    foundation = Foundation(key="A1", title="Reals", description="", check_question="", check_answer="")
    skills = [
        skill("S1", [4, 99], figure_ids=["p4-f1", "nope"], foundations=["A1", "A7"], difficulty=9, est_minutes=1),
        skill("S1", [5], [("S1", 0.5)]),
    ]
    g = validate_graph(draft(skills, [foundation, foundation]), valid_pages={4, 5}, figure_ids={"p4-f1"})
    first, second = g.skills
    assert first.source_pages == [4] and first.figure_ids == ["p4-f1"] and first.foundations == ["A1"]
    assert first.difficulty == 5 and first.est_minutes == 3
    assert second.key == "S2"
    assert edge_map(g) == {("S1", "S2"): (0.5, True)}
    assert len(g.foundations) == 1
    assert any("renamed" in w for w in g.warnings)
    assert any("unusual skill count" in w for w in g.warnings)


def test_empty_graph_fails():
    with pytest.raises(BuildError):
        validate_graph(draft([]), valid_pages={1}, figure_ids=set())
