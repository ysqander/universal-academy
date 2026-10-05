import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import {
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { kindLabel, STATUS_LABEL } from "../../lib/format";
import { prettyTitle } from "../../lib/markdown";
import { FOUNDATION_H, FOUNDATION_W, foundationNodeId, layoutGraph, NODE_H, NODE_W, type Bounds } from "../../lib/graphLayout";
import type { CourseGraph, FoundationNode, SkillNode } from "../../types";
import type { GraphSelection } from "./selection";
import "./SkillMap.css";

type Emphasis = "selected" | "related" | "dimmed" | null;

// Node clicks (mouse, or Enter/Space on the inner button, which bubbles) are handled by ReactFlow's onNodeClick.
type SkillData = { skill: SkillNode; emphasis: Emphasis };
type FoundationData = { foundation: FoundationNode; emphasis: Emphasis };
type LaneData = { label: string };

type SkillFlowNode = Node<SkillData, "skill">;
type FoundationFlowNode = Node<FoundationData, "foundation">;
type LaneFlowNode = Node<LaneData, "lane">;
type FlowNode = SkillFlowNode | FoundationFlowNode | LaneFlowNode;

const SkillNodeView = memo(function SkillNodeView({ data }: NodeProps<SkillFlowNode>) {
  const { skill, emphasis } = data;
  const statusText = `${STATUS_LABEL[skill.status]}${skill.due ? ", review due" : ""}`;
  return (
    <div
      className="skill-node"
      data-status={skill.status}
      data-due={skill.due || undefined}
      data-emphasis={emphasis ?? undefined}
      style={{ width: NODE_W, height: NODE_H }}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <button type="button" className="skill-node__button">
        <span className="skill-node__top">
          <span className="skill-node__key">{skill.key}</span>
          <span className="skill-node__kind">{kindLabel(skill.kind)}</span>
          {skill.lesson_status === "pending" && <Spinner size={11} label="Lesson being written" className="skill-node__pending" />}
          {skill.lesson_status === "failed" && (
            <span className="skill-node__failed" title="The lesson for this skill could not be generated">
              <Icon name="warning" size={13} />
            </span>
          )}
          {skill.status === "locked" && <Icon name="lock" size={12} className="skill-node__lock" />}
        </span>
        <span className="skill-node__title">
          <Markdown inline>{prettyTitle(skill.title)}</Markdown>
        </span>
        <span className="visually-hidden">{statusText}</span>
      </button>
      {skill.due && <span className="skill-node__due">Due</span>}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
});

const FoundationNodeView = memo(function FoundationNodeView({ data }: NodeProps<FoundationFlowNode>) {
  const { foundation, emphasis } = data;
  const state = foundation.known === true ? "known" : foundation.known === false ? "refresh" : "unchecked";
  return (
    <div className="foundation-node" data-state={state} data-emphasis={emphasis ?? undefined} style={{ width: FOUNDATION_W, height: FOUNDATION_H }}>
      <button type="button" className="foundation-node__button">
        <span className="visually-hidden">Assumed knowledge</span>
        <span className="foundation-node__key">
          {foundation.key}
          {state === "known" && <Icon name="check" size={12} />}
          {state === "refresh" && <Icon name="review" size={12} />}
        </span>
        <span className="foundation-node__title">
          <Markdown inline>{prettyTitle(foundation.title)}</Markdown>
        </span>
      </button>
    </div>
  );
});

function LaneNodeView({ data }: NodeProps<LaneFlowNode>) {
  return (
    <div className="lane-label">
      <Icon name="layers" size={14} /> {data.label}
    </div>
  );
}

const NODE_TYPES = { skill: SkillNodeView, foundation: FoundationNodeView, lane: LaneNodeView };

const PADDING = 24;
/** Below this zoom node titles stop being readable; tall graphs are then shown from the top instead. */
const READABLE_ZOOM = 0.72;

/**
 * Initial viewport: fit the whole graph when that keeps it readable, otherwise fit the width (up to READABLE_ZOOM)
 * and anchor the top, where the learner starts. Re-applied when the set of nodes changes, and when the pane is
 * resized (window resize, iPad rotation) as long as the learner hasn't panned or zoomed — polling never undoes
 * their view. The Controls' fit button still shows everything.
 */
interface InitialViewportProps {
  signature: string;
  bounds: Bounds;
  /** Whether the learner has panned/zoomed since the last fit; `reset` marks a fresh fit. */
  userMoved: (reset?: boolean) => boolean;
}

function InitialViewport({ signature, bounds, userMoved }: InitialViewportProps) {
  const { setViewport } = useReactFlow();
  // React Flow measures its pane with a ResizeObserver; these update on every container resize.
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const applied = useRef<{ signature: string; width: number; height: number } | null>(null);
  const { x: bx, y: by, width: bw, height: bh } = bounds;

  useEffect(() => {
    if (!width || !height) return;
    const prev = applied.current;
    if (prev && prev.signature === signature) {
      if (prev.width === width && prev.height === height) return;
      if (userMoved()) return;
    }
    applied.current = { signature, width, height };
    userMoved(true);
    const fitAll = Math.min((width - 2 * PADDING) / bw, (height - 2 * PADDING) / bh);
    const fitWidth = (width - 2 * PADDING) / bw;
    const zoom = Math.min(1, fitAll >= READABLE_ZOOM ? fitAll : Math.max(fitAll, Math.min(READABLE_ZOOM, fitWidth)));
    const x = (width - bw * zoom) / 2 - bx * zoom;
    const y = bh * zoom <= height - 2 * PADDING ? (height - bh * zoom) / 2 - by * zoom : PADDING - by * zoom;
    void setViewport({ x, y, zoom }, { duration: 200 });
  }, [signature, width, height, bx, by, bw, bh, setViewport, userMoved]);
  return null;
}

interface SkillMapProps {
  graph: CourseGraph;
  selection: GraphSelection | null;
  onSelect: (selection: GraphSelection) => void;
}

export function SkillMap({ graph, selection, onSelect }: SkillMapProps) {
  const layout = useMemo(() => layoutGraph(graph.skills, graph.edges, graph.foundations), [graph.skills, graph.edges, graph.foundations]);
  const drawnEdges = useMemo(
    () => graph.edges.filter((e) => e.reduced && e.source !== e.target),
    [graph.edges],
  );

  const related = useMemo(() => {
    const ids = new Set<string>();
    if (selection?.kind === "skill") {
      for (const e of drawnEdges) {
        if (e.source === selection.id) ids.add(e.target);
        if (e.target === selection.id) ids.add(e.source);
      }
      const skill = graph.skills.find((s) => s.id === selection.id);
      for (const key of skill?.foundations ?? []) ids.add(foundationNodeId(key));
    } else if (selection?.kind === "foundation") {
      for (const s of graph.skills) if (s.foundations.includes(selection.key)) ids.add(s.id);
    }
    return ids;
  }, [selection, drawnEdges, graph.skills]);

  const selectedId = selection ? (selection.kind === "skill" ? selection.id : foundationNodeId(selection.key)) : null;
  const emphasisOf = (id: string): Emphasis => {
    if (!selectedId) return null;
    if (id === selectedId) return "selected";
    return related.has(id) ? "related" : "dimmed";
  };

  const skillById = useMemo(() => new Map(graph.skills.map((s) => [s.id, s])), [graph.skills]);
  const nodes: FlowNode[] = [
    ...layout.skills.flatMap((p): SkillFlowNode[] => {
      const skill = skillById.get(p.id);
      // Known sizes: fresh node objects (every poll / selection) otherwise lose their measurement and React Flow
      // hides them (visibility: hidden) until a ResizeObserver callback that may never come.
      return skill
        ? [{ id: p.id, type: "skill", position: { x: p.x, y: p.y }, width: NODE_W, height: NODE_H, data: { skill, emphasis: emphasisOf(p.id) } }]
        : [];
    }),
    ...graph.foundations.map((foundation, i): FoundationFlowNode => {
      const id = foundationNodeId(foundation.key);
      const p = layout.foundations[i];
      return {
        id,
        type: "foundation",
        position: { x: p.x, y: p.y },
        width: FOUNDATION_W,
        height: FOUNDATION_H,
        data: { foundation, emphasis: emphasisOf(id) },
      };
    }),
    ...(layout.laneLabel
      ? [
          {
            id: "lane:foundations",
            type: "lane" as const,
            position: layout.laneLabel,
            initialWidth: 200,
            initialHeight: 24,
            data: { label: "Assumed knowledge" },
            selectable: false,
            focusable: false,
          } satisfies LaneFlowNode,
        ]
      : []),
  ];

  const edges: Edge[] = drawnEdges.map((e) => {
    const active = selectedId !== null && (e.source === selectedId || e.target === selectedId);
    const target = skillById.get(e.target);
    const tone = active ? "var(--accent)" : target?.status === "locked" ? "var(--line-strong)" : "var(--faint)";
    return {
      id: `${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      className: active ? "skill-edge skill-edge--active" : selectedId ? "skill-edge skill-edge--dimmed" : "skill-edge",
      style: { strokeWidth: 1 + Math.max(0.05, Math.min(1, e.weight)) * 2.5, stroke: tone },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: tone },
      focusable: false,
      selectable: false,
    };
  });

  const signature = `${graph.skills.length}:${graph.foundations.length}`;
  const interacted = useRef(false);
  const userMoved = useCallback((reset = false) => {
    const moved = interacted.current;
    if (reset) interacted.current = false;
    return moved;
  }, []);

  return (
    <div className="skill-map">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={(_, node: FlowNode) => {
          if (node.type === "skill") onSelect({ kind: "skill", id: node.id });
          else if (node.type === "foundation") onSelect({ kind: "foundation", key: node.data.foundation.key });
        }}
        minZoom={0.2}
        maxZoom={1.8}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        preventScrolling={false}
        zoomOnDoubleClick={false}
        panOnDrag
        zoomOnPinch
        onMove={(event) => {
          // Only user gestures carry an event; programmatic fits (ours, the fit button) don't.
          if (event) interacted.current = true;
        }}
        colorMode="system"
        aria-label="Knowledge graph"
      >
        <Controls showInteractive={false} position="top-right" />
        <InitialViewport signature={signature} bounds={layout.bounds} userMoved={userMoved} />
      </ReactFlow>
    </div>
  );
}
