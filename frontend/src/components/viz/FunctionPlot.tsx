import { useId, useMemo, useState } from "react";
import type { PlotCurve, VizParameter } from "../../types";
import { Markdown } from "../Markdown";
import { Tex } from "../Tex";
import { dataExtent, formatTick, svgId, ticks, validRange, type Range } from "./scale";

const W = 520;
const H = 320;
const M = { top: 14, right: 16, bottom: 28, left: 46 };
const SERIES = 4;

interface FunctionPlotProps {
  curves: PlotCurve[];
  xRange: number[] | null;
  yRange: number[] | null;
  parameter: VizParameter | null;
  title?: string;
}

const nearestIndex = (values: number[], target: number): number =>
  values.reduce((best, v, i) => (Math.abs(v - target) < Math.abs(values[best] - target) ? i : best), 0);

/** Pre-sampled curves (sampled with SymPy on the server); an optional slider switches between parameter values. */
export function FunctionPlot({ curves, xRange, yRange, parameter, title }: FunctionPlotProps) {
  const clipId = `${svgId(useId())}-clip`;
  const paramValues = useMemo(
    () =>
      Array.from(new Set(curves.map((c) => c.param_value).filter((v): v is number => v !== null && Number.isFinite(v)))).sort(
        (a, b) => a - b,
      ),
    [curves],
  );
  const hasSlider = parameter !== null && paramValues.length > 1;
  const [index, setIndex] = useState(() => (hasSlider ? nearestIndex(paramValues, parameter.initial) : 0));
  const active = hasSlider ? paramValues[Math.min(index, paramValues.length - 1)] : null;

  const visible = useMemo(
    () => curves.filter((c) => !hasSlider || c.param_value === null || c.param_value === active),
    [curves, hasSlider, active],
  );
  const labels = useMemo(() => Array.from(new Set(curves.map((c) => c.label))), [curves]);

  // Axis ranges stay fixed across slider positions so the motion is visible.
  const allPoints = useMemo(() => curves.flatMap((c) => c.points.filter((p) => p.length >= 2)), [curves]);
  const xr: Range = validRange(xRange, dataExtent(allPoints.map((p) => p[0]), 0) ?? [-5, 5]);
  const yr: Range = validRange(yRange, dataExtent(allPoints.map((p) => p[1])) ?? [-5, 5]);

  const sx = (x: number) => M.left + ((x - xr[0]) / (xr[1] - xr[0])) * (W - M.left - M.right);
  const sy = (y: number) => M.top + ((yr[1] - y) / (yr[1] - yr[0])) * (H - M.top - M.bottom);
  const clampY = (y: number) => Math.max(yr[0] - 10 * (yr[1] - yr[0]), Math.min(yr[1] + 10 * (yr[1] - yr[0]), y));

  const xTicks = ticks(xr, 7);
  const yTicks = ticks(yr, 6);
  const axisY = xr[0] <= 0 && xr[1] >= 0 ? sx(0) : M.left;
  const axisX = yr[0] <= 0 && yr[1] >= 0 ? sy(0) : H - M.bottom;

  return (
    <div className="viz-plot">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title || "Function plot"}>
        <defs>
          <clipPath id={clipId}>
            <rect x={M.left} y={M.top} width={W - M.left - M.right} height={H - M.top - M.bottom} />
          </clipPath>
        </defs>
        <g className="viz-grid">
          {xTicks.map((t) => (
            <line key={`gx${t}`} x1={sx(t)} x2={sx(t)} y1={M.top} y2={H - M.bottom} />
          ))}
          {yTicks.map((t) => (
            <line key={`gy${t}`} x1={M.left} x2={W - M.right} y1={sy(t)} y2={sy(t)} />
          ))}
        </g>
        <g className="viz-axis">
          <line x1={M.left} x2={W - M.right} y1={axisX} y2={axisX} />
          <line x1={axisY} x2={axisY} y1={M.top} y2={H - M.bottom} />
          {xTicks.map((t) => (
            <text key={`tx${t}`} x={sx(t)} y={H - M.bottom + 16} textAnchor="middle">
              {formatTick(t)}
            </text>
          ))}
          {yTicks.map((t) => (
            <text key={`ty${t}`} x={M.left - 6} y={sy(t) + 3.5} textAnchor="end">
              {formatTick(t)}
            </text>
          ))}
        </g>
        <g clipPath={`url(#${clipId})`}>
          {visible.map((curve, i) => {
            const pts = curve.points
              .filter((p) => p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]))
              .map((p) => `${sx(p[0]).toFixed(2)},${sy(clampY(p[1])).toFixed(2)}`)
              .join(" ");
            const series = (labels.indexOf(curve.label) % SERIES) + 1;
            return <polyline key={`${curve.label}-${curve.param_value}-${i}`} className={`viz-curve viz-series-${series}`} points={pts} />;
          })}
        </g>
      </svg>
      {labels.length > 1 || (labels.length === 1 && labels[0]) ? (
        <ul className="viz-legend">
          {labels.filter(Boolean).map((label) => (
            <li key={label}>
              <span className={`viz-legend__swatch viz-series-${(labels.indexOf(label) % SERIES) + 1}`} aria-hidden="true" />
              <Markdown inline>{label}</Markdown>
            </li>
          ))}
        </ul>
      ) : null}
      {hasSlider && active !== null && (
        <label className="viz-slider">
          <span className="viz-slider__value">
            <Tex tex={parameter.name} /> = <strong>{formatTick(active)}</strong>
          </span>
          <input
            type="range"
            min={0}
            max={paramValues.length - 1}
            step={1}
            value={index}
            onChange={(event) => setIndex(Number(event.target.value))}
            aria-valuetext={`${parameter.name} = ${formatTick(active)}`}
          />
          <span className="viz-slider__bounds">
            <span>{formatTick(paramValues[0])}</span>
            <span>{formatTick(paramValues[paramValues.length - 1])}</span>
          </span>
        </label>
      )}
    </div>
  );
}
