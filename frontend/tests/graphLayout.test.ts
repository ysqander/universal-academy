// Initial map viewport (src/lib/graphLayout.ts): readable on phones, whole graph on roomy panes.
import assert from "node:assert/strict";
import { test } from "node:test";
import { initialViewport, layoutGraph, MIN_FIT_ZOOM, NODE_H, NODE_W } from "../src/lib/graphLayout.ts";
import type { SkillEdge, SkillNode } from "../src/types.ts";

const skill = (i: number): SkillNode => ({
  id: `s${i}`, key: `S${i}`, title: `Skill ${i}`, objective: "", kind: "concept", summary: "", difficulty: 2, est_minutes: 10,
  order_index: i, source_pages: [], page_labels: [], source_refs: [], figure_ids: [], foundations: [], misconceptions: [],
  lesson_status: "ready", status: "available", due: false, due_at: null, retrievability: null, stability_days: null, reviews: 0,
});
const edge = (a: number, b: number): SkillEdge => ({ source: `s${a}`, target: `s${b}`, weight: 0.6, reduced: true });

// A 17-skill chapter with a wide middle rank (like Axler §1A–1C).
const skills = Array.from({ length: 17 }, (_, i) => skill(i + 1));
const edges = [edge(1, 2), edge(1, 3), edge(1, 4), edge(1, 5), edge(1, 6), edge(1, 7), edge(2, 8), edge(3, 9), edge(4, 10), edge(5, 11),
  edge(6, 12), edge(7, 13), edge(8, 14), edge(9, 15), edge(10, 16), edge(11, 17)];

test("a wide graph on a phone opens readable (zoom >= 0.6) with the first skill on screen", () => {
  const layout = layoutGraph(skills, edges, []);
  const first = layout.skills[0];
  const focus = { x: first.x, y: first.y, width: NODE_W, height: NODE_H };
  const [w, h] = [343, 617];
  const vp = initialViewport(w, h, layout.bounds, focus);
  assert.ok(vp.zoom >= MIN_FIT_ZOOM, `zoom ${vp.zoom.toFixed(3)} is unreadable`);
  const left = first.x * vp.zoom + vp.x;
  const right = (first.x + NODE_W) * vp.zoom + vp.x;
  assert.ok(left >= 0 && right <= w, `first skill at ${left.toFixed(0)}–${right.toFixed(0)} px is off screen`);
  const top = first.y * vp.zoom + vp.y;
  assert.ok(top >= 24 - 1e-6 && top + NODE_H * vp.zoom <= h, "the first row is on screen");
  // Never scrolled past the graph's edges.
  assert.ok(layout.bounds.x * vp.zoom + vp.x <= 24 + 1e-6);
  assert.ok((layout.bounds.x + layout.bounds.width) * vp.zoom + vp.x >= w - 24 - 1e-6);
});

test("a small graph on a roomy pane is fitted whole and centred", () => {
  const small = skills.slice(0, 4);
  const layout = layoutGraph(small, [edge(1, 3), edge(2, 3), edge(3, 4)], []);
  const vp = initialViewport(1100, 700, layout.bounds, layout.bounds);
  const { x, y, width, height } = layout.bounds;
  assert.ok(vp.zoom <= 1);
  assert.ok(x * vp.zoom + vp.x >= 24 - 1e-6 && (x + width) * vp.zoom + vp.x <= 1100 - 24 + 1e-6);
  assert.ok(y * vp.zoom + vp.y >= 24 - 1e-6 && (y + height) * vp.zoom + vp.y <= 700 - 24 + 1e-6);
});

test("a tall graph is anchored at the top, where the learner starts", () => {
  const chain = Array.from({ length: 12 }, (_, i) => skill(i + 1));
  const layout = layoutGraph(chain, chain.slice(1).map((_, i) => edge(i + 1, i + 2)), []);
  const vp = initialViewport(343, 617, layout.bounds, layout.bounds);
  assert.ok(vp.zoom >= MIN_FIT_ZOOM);
  assert.ok(Math.abs(layout.bounds.y * vp.zoom + vp.y - 24) < 1e-6);
});
