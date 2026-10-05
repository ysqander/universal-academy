import { useId } from "react";
import type { VizDiagram } from "../../types";
import { Markdown } from "../Markdown";
import { formatTick, isPoint, safeColor, svgId, ticks, validRange, VIZ_COLORS } from "./scale";

const WIDTH = 480;
const MAX_HEIGHT = 400;
const PAD = 30;
const LABEL_OFFSET = 14;

interface Label {
  key: string;
  x: number;
  y: number;
  text: string;
  tone: string;
}

/** 2D vector diagram with equal axis scaling (angles and lengths stay honest). */
export function Diagram2D({ diagram, title }: { diagram: VizDiagram; title?: string }) {
  const id = svgId(useId());
  const [xmin, xmax] = validRange(diagram.x_range, [-5, 5]);
  const [ymin, ymax] = validRange(diagram.y_range, [-5, 5]);
  const unit = Math.min((WIDTH - 2 * PAD) / (xmax - xmin), (MAX_HEIGHT - 2 * PAD) / (ymax - ymin));
  const width = (xmax - xmin) * unit + 2 * PAD;
  const height = (ymax - ymin) * unit + 2 * PAD;
  const sx = (x: number) => PAD + (x - xmin) * unit;
  const sy = (y: number) => PAD + (ymax - y) * unit;

  const xTicks = ticks([xmin, xmax], 8);
  const yTicks = ticks([ymin, ymax], 8);
  const axisX = sy(ymin <= 0 && ymax >= 0 ? 0 : ymin);
  const axisY = sx(xmin <= 0 && xmax >= 0 ? 0 : xmin);

  const labels: Label[] = [];
  const vectors = diagram.vectors.filter((v) => isPoint(v.start) && isPoint(v.end));
  const points = diagram.points.filter((p) => isPoint(p.at));
  const segments = diagram.segments.filter((s) => isPoint(s.start) && isPoint(s.end));

  // Vector labels sit beside the arrow's midpoint (on its left-hand side), so arrows that share a tip don't collide.
  vectors.forEach((v, i) => {
    if (!v.label) return;
    const [x0, y0, x1, y1] = [sx(v.start[0]), sy(v.start[1]), sx(v.end[0]), sy(v.end[1])];
    const len = Math.hypot(x1 - x0, y1 - y0) || 1;
    labels.push({
      key: `v${i}`,
      x: (x0 + x1) / 2 + ((y1 - y0) / len) * LABEL_OFFSET,
      y: (y0 + y1) / 2 - ((x1 - x0) / len) * LABEL_OFFSET,
      text: v.label,
      tone: safeColor(v.color),
    });
  });
  points.forEach((p, i) => {
    if (p.label) labels.push({ key: `p${i}`, x: sx(p.at[0]) + 12, y: sy(p.at[1]) - 12, text: p.label, tone: safeColor(p.color) });
  });
  segments.forEach((s, i) => {
    if (!s.label) return;
    const [x0, y0, x1, y1] = [sx(s.start[0]), sy(s.start[1]), sx(s.end[0]), sy(s.end[1])];
    const len = Math.hypot(x1 - x0, y1 - y0) || 1;
    labels.push({
      key: `s${i}`,
      x: (x0 + x1) / 2 - ((y1 - y0) / len) * LABEL_OFFSET,
      y: (y0 + y1) / 2 + ((x1 - x0) / len) * LABEL_OFFSET,
      text: s.label,
      tone: "muted",
    });
  });
  const [xLabel, yLabel] = diagram.axis_labels;
  if (xLabel) labels.push({ key: "ax", x: width - PAD + 10, y: axisX - 12, text: xLabel, tone: "axis" });
  if (yLabel) labels.push({ key: "ay", x: axisY + 14, y: PAD - 14, text: yLabel, tone: "axis" });

  return (
    <div className="viz-canvas" style={{ aspectRatio: `${width} / ${height}`, maxWidth: width * 1.35 }}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title || "Diagram"}>
        <defs>
          {VIZ_COLORS.map((color) => (
            <marker
              key={color}
              id={`${id}-arrow-${color}`}
              viewBox="0 0 10 10"
              refX="8.6"
              refY="5"
              markerWidth="11"
              markerHeight="11"
              markerUnits="userSpaceOnUse"
              orient="auto-start-reverse"
            >
              <path d="M0 0.8 L10 5 L0 9.2 L2.2 5 z" className={`viz-fill--${color}`} />
            </marker>
          ))}
        </defs>
        {diagram.show_grid && (
          <g className="viz-grid">
            {xTicks.map((t) => (
              <line key={`gx${t}`} x1={sx(t)} x2={sx(t)} y1={PAD} y2={height - PAD} />
            ))}
            {yTicks.map((t) => (
              <line key={`gy${t}`} x1={PAD} x2={width - PAD} y1={sy(t)} y2={sy(t)} />
            ))}
          </g>
        )}
        <g className="viz-axis">
          <line x1={PAD - 6} x2={width - PAD + 6} y1={axisX} y2={axisX} />
          <line x1={axisY} x2={axisY} y1={PAD - 6} y2={height - PAD + 6} />
          {xTicks
            .filter((t) => t !== 0)
            .map((t) => (
              <g key={`tx${t}`}>
                <line x1={sx(t)} x2={sx(t)} y1={axisX - 3} y2={axisX + 3} />
                <text x={sx(t)} y={axisX + 14} textAnchor="middle">
                  {formatTick(t)}
                </text>
              </g>
            ))}
          {yTicks
            .filter((t) => t !== 0)
            .map((t) => (
              <g key={`ty${t}`}>
                <line x1={axisY - 3} x2={axisY + 3} y1={sy(t)} y2={sy(t)} />
                <text x={axisY - 6} y={sy(t) + 3.5} textAnchor="end">
                  {formatTick(t)}
                </text>
              </g>
            ))}
        </g>
        {segments.map((s, i) => (
          <line
            key={`seg${i}`}
            className="viz-segment"
            x1={sx(s.start[0])}
            y1={sy(s.start[1])}
            x2={sx(s.end[0])}
            y2={sy(s.end[1])}
            strokeDasharray={s.dashed ? "5 4" : undefined}
          />
        ))}
        {vectors.map((v, i) => {
          const color = safeColor(v.color);
          return (
            <line
              key={`vec${i}`}
              className={`viz-vector viz-stroke--${color}`}
              x1={sx(v.start[0])}
              y1={sy(v.start[1])}
              x2={sx(v.end[0])}
              y2={sy(v.end[1])}
              markerEnd={`url(#${id}-arrow-${color})`}
            />
          );
        })}
        {points.map((p, i) => (
          <circle key={`pt${i}`} className={`viz-point viz-fill--${safeColor(p.color)}`} cx={sx(p.at[0])} cy={sy(p.at[1])} r={4.2} />
        ))}
      </svg>
      {labels.map((label) => (
        <span
          key={label.key}
          className={`viz-label viz-label--${label.tone}`}
          style={{ left: `${(label.x / width) * 100}%`, top: `${(label.y / height) * 100}%` }}
        >
          <Markdown inline>{label.text}</Markdown>
        </span>
      ))}
    </div>
  );
}
