import dagre from "@dagrejs/dagre";
import type { FoundationNode, SkillEdge, SkillNode } from "../types";

export const NODE_W = 212;
export const NODE_H = 76;
export const FOUNDATION_W = 180;
export const FOUNDATION_H = 52;
const RANK_SEP = 64;
const NODE_SEP = 28;
const LANE_GAP = 72;

export interface Positioned {
  id: string;
  x: number;
  y: number;
}

export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GraphLayout {
  skills: Positioned[];
  foundations: Positioned[];
  /** Top-left of the "Assumed knowledge" lane label, when there are foundations. */
  laneLabel: { x: number; y: number } | null;
  bounds: Bounds;
}

function boundsOf(boxes: { x: number; y: number; w: number; h: number }[]): Bounds {
  if (!boxes.length) return { x: 0, y: 0, width: NODE_W, height: NODE_H };
  const x = Math.min(...boxes.map((b) => b.x));
  const y = Math.min(...boxes.map((b) => b.y));
  const width = Math.max(...boxes.map((b) => b.x + b.w)) - x;
  const height = Math.max(...boxes.map((b) => b.y + b.h)) - y;
  return { x, y, width, height };
}

export const foundationNodeId = (key: string): string => `foundation:${key}`;

/**
 * Top-to-bottom layered layout (prerequisites above dependents) of the reduced edges,
 * with foundations in their own lane above the chapter's skills.
 */
export function layoutGraph(skills: SkillNode[], edges: SkillEdge[], foundations: FoundationNode[]): GraphLayout {
  const g = new dagre.graphlib.Graph<object, { width: number; height: number; x: number; y: number }>();
  g.setGraph({ rankdir: "TB", ranksep: RANK_SEP, nodesep: NODE_SEP, marginx: 0, marginy: 0 });
  g.setDefaultEdgeLabel(() => ({}));

  const ordered = [...skills].sort((a, b) => a.order_index - b.order_index);
  const ids = new Set(ordered.map((s) => s.id));
  for (const skill of ordered) g.setNode(skill.id, { width: NODE_W, height: NODE_H, x: 0, y: 0 });
  for (const edge of edges) {
    if (edge.reduced && ids.has(edge.source) && ids.has(edge.target) && edge.source !== edge.target) {
      g.setEdge(edge.source, edge.target, { weight: Math.max(1, Math.round(edge.weight * 4)) });
    }
  }
  dagre.layout(g);

  const positioned = ordered.map((skill) => {
    const { x, y } = g.node(skill.id);
    return { id: skill.id, x: x - NODE_W / 2, y: y - NODE_H / 2 };
  });

  const minX = positioned.length ? Math.min(...positioned.map((p) => p.x)) : 0;
  const maxX = positioned.length ? Math.max(...positioned.map((p) => p.x + NODE_W)) : NODE_W * 3;
  const minY = positioned.length ? Math.min(...positioned.map((p) => p.y)) : 0;

  const skillBoxes = positioned.map((p) => ({ x: p.x, y: p.y, w: NODE_W, h: NODE_H }));
  if (!foundations.length) return { skills: positioned, foundations: [], laneLabel: null, bounds: boundsOf(skillBoxes) };

  // Foundations: wrap into rows no wider than the skill graph (at least two per row).
  const width = Math.max(maxX - minX, FOUNDATION_W * 2 + NODE_SEP);
  const perRow = Math.max(2, Math.floor((width + NODE_SEP) / (FOUNDATION_W + NODE_SEP)));
  const rows = Math.ceil(foundations.length / perRow);
  const laneHeight = rows * FOUNDATION_H + (rows - 1) * 16;
  const laneTop = minY - LANE_GAP - laneHeight;
  const placed = foundations.map((f, i) => {
    const row = Math.floor(i / perRow);
    const inRow = Math.min(perRow, foundations.length - row * perRow);
    const rowWidth = inRow * FOUNDATION_W + (inRow - 1) * NODE_SEP;
    const left = minX + (maxX - minX - rowWidth) / 2;
    return {
      id: foundationNodeId(f.key),
      x: left + (i % perRow) * (FOUNDATION_W + NODE_SEP),
      y: laneTop + row * (FOUNDATION_H + 16),
    };
  });
  const laneLeft = Math.min(...placed.map((p) => p.x));
  const laneLabel = { x: laneLeft, y: laneTop - 30 };
  const bounds = boundsOf([
    ...skillBoxes,
    ...placed.map((p) => ({ x: p.x, y: p.y, w: FOUNDATION_W, h: FOUNDATION_H })),
    { x: laneLabel.x, y: laneLabel.y, w: FOUNDATION_W, h: 20 },
  ]);
  return { skills: positioned, foundations: placed, laneLabel, bounds };
}
