import type { VizColor } from "../../types";

export type Range = [number, number];

/** A usable [min, max] range from model output (falls back when missing, reversed or degenerate). */
export function validRange(range: number[] | null | undefined, fallback: Range): Range {
  if (!range || range.length < 2) return fallback;
  const [a, b] = range;
  if (!Number.isFinite(a) || !Number.isFinite(b) || a === b) return fallback;
  return a < b ? [a, b] : [b, a];
}

/** 1, 2 or 5 × 10^k step giving roughly `target` intervals over `span`. */
function niceStep(span: number, target = 8): number {
  const raw = span / Math.max(1, target);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const residual = raw / magnitude;
  const nice = residual >= 5 ? 10 : residual >= 2 ? 5 : residual >= 1 ? 2 : 1;
  return nice * magnitude;
}

export function ticks([min, max]: Range, target = 8): number[] {
  const step = niceStep(max - min, target);
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : Number(v.toPrecision(12)));
  }
  return out;
}

export const formatTick = (v: number): string => {
  if (Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-3 && v !== 0)) return v.toExponential(0);
  return String(Number(v.toFixed(4)));
};

export function dataExtent(values: number[], pad = 0.08): Range | null {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return null;
  let min = Math.min(...finite);
  let max = Math.max(...finite);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const margin = (max - min) * pad;
  return [min - margin, max + margin];
}

export const VIZ_COLORS: VizColor[] = ["primary", "accent", "muted", "danger"];

export const safeColor = (color: string): VizColor => (VIZ_COLORS.includes(color as VizColor) ? (color as VizColor) : "primary");

/** useId() output contains characters that are awkward inside url(#…) references. */
export const svgId = (reactId: string): string => `v${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}`;

export const isPoint = (p: number[] | null | undefined): p is [number, number] =>
  Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]);
