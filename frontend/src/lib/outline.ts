import type { OutlineNode } from "../types";

export interface FlatNode {
  node: OutlineNode;
  depth: number;
  parentId: string | null;
  /** Position in document (pre-)order. */
  order: number;
}

export function flattenOutline(nodes: OutlineNode[]): FlatNode[] {
  const out: FlatNode[] = [];
  const walk = (list: OutlineNode[], depth: number, parentId: string | null) => {
    for (const node of list) {
      out.push({ node, depth, parentId, order: out.length });
      walk(node.children, depth + 1, node.id);
    }
  };
  walk(nodes, 0, null);
  return out;
}

export type PageLabeler = (page: number) => string | null;

/**
 * Printed page label for any 0-based PDF page, inferred from the outline's labelled endpoints.
 * Exact endpoint labels win; elsewhere the most common numeric offset (printed n = index − offset) is used.
 */
export function makePageLabeler(flat: FlatNode[], pageCount: number): PageLabeler {
  const exact = new Map<number, string>();
  const offsets = new Map<number, number>();
  let maxNumeric = 0;
  for (const { node } of flat) {
    for (const [page, label] of [
      [node.start_page, node.label_start],
      [node.end_page, node.label_end],
    ] as const) {
      if (!label) continue;
      exact.set(page, label);
      if (/^\d+$/.test(label)) {
        const offset = page - Number(label);
        offsets.set(offset, (offsets.get(offset) ?? 0) + 1);
        maxNumeric = Math.max(maxNumeric, Number(label));
      }
    }
  }
  let offset: number | null = null;
  let best = 0;
  for (const [candidate, count] of offsets) {
    if (count > best) {
      best = count;
      offset = candidate;
    }
  }
  return (page) => {
    if (page < 0 || page >= pageCount) return null;
    const known = exact.get(page);
    if (known) return known;
    if (offset === null) return null;
    const printed = page - offset;
    return printed >= 1 ? String(printed) : null;
  };
}

export interface Selection {
  start: number;
  end: number;
  /** Outline nodes fully inside the range whose parent is not (the "sections" of the course). */
  sections: OutlineNode[];
}

export function selectionFromNodes(flat: FlatNode[], anchorId: string, focusId: string): Selection | null {
  const anchor = flat.find((f) => f.node.id === anchorId);
  const focus = flat.find((f) => f.node.id === focusId);
  if (!anchor || !focus) return null;
  const start = Math.min(anchor.node.start_page, focus.node.start_page);
  const end = Math.max(anchor.node.end_page, focus.node.end_page);
  return { start, end, sections: coveredSections(flat, start, end) };
}

export function coveredSections(flat: FlatNode[], start: number, end: number): OutlineNode[] {
  const inside = new Set(flat.filter((f) => f.node.start_page >= start && f.node.end_page <= end).map((f) => f.node.id));
  return flat.filter((f) => inside.has(f.node.id) && (f.parentId === null || !inside.has(f.parentId))).map((f) => f.node);
}

/** Ids of every ancestor of the given nodes (to auto-expand the tree around a selection). */
export function ancestorIds(flat: FlatNode[], ids: string[]): Set<string> {
  const parent = new Map(flat.map((f) => [f.node.id, f.parentId]));
  const out = new Set<string>();
  for (const id of ids) {
    let p = parent.get(id) ?? null;
    while (p) {
      out.add(p);
      p = parent.get(p) ?? null;
    }
  }
  return out;
}
