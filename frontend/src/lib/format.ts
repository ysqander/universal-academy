import type { SkillStatus, TaskType } from "../types";

export const pageRangeLabel = (start: string, end: string): string =>
  start === end ? `p. ${start}` : `pp. ${start}–${end}`;

export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

export const percent = (fraction: number): string => `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;

export const formatClock = (totalSeconds: number): string => {
  const m = Math.floor(totalSeconds / 60);
  const s = Math.floor(totalSeconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
};

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** "in 3 days", "2 hours ago", "now" — for review due dates and history. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const diffSeconds = (new Date(iso).getTime() - now) / 1000;
  if (!Number.isFinite(diffSeconds)) return "";
  const abs = Math.abs(diffSeconds);
  if (abs < 45) return "now";
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["minute", 60],
    ["hour", 3600],
    ["day", 86400],
    ["week", 604800],
    ["month", 2629800],
    ["year", 31557600],
  ];
  let chosen = units[0];
  for (const unit of units) if (abs >= unit[1]) chosen = unit;
  return rtf.format(Math.round(diffSeconds / chosen[1]), chosen[0]);
}

export const formatDateTime = (iso: string): string =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export const STATUS_LABEL: Record<SkillStatus, string> = {
  locked: "Locked",
  available: "Available",
  in_progress: "In progress",
  learned: "Learned",
  mastered: "Mastered",
};

export const TASK_LABEL: Record<TaskType, string> = {
  lesson: "Lesson",
  review: "Review",
};

const KIND_LABEL: Record<string, string> = {
  concept: "Concept",
  definition: "Definition",
  procedure: "Procedure",
  computation: "Computation",
  distinction: "Distinction",
  interpretation: "Interpretation",
  proof: "Proof",
};

export const kindLabel = (kind: string): string =>
  KIND_LABEL[kind] ?? (kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "Skill");

/** Build-time estimate shown before starting a course (~45 s and ~$0.10 per page, measured on Axler ch. 1). */
export function buildEstimate(pages: number): { minutes: number; dollars: number } {
  return { minutes: Math.max(1, Math.round((pages * 45) / 60)), dollars: Math.round(pages * 0.1 * 100) / 100 };
}
