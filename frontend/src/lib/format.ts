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
  quiz: "Quiz",
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
/** Rough build time and API cost of a page range. v2 lessons (the default) are planned, written, reviewed and revised:
 * about $5 and 8 minutes per page with Claude (chapter 1 of Axler: 26 pages, 42 skills, ~$130). On the Claude Code
 * route there is no API cost, and agents take about 20 minutes per page. v1 lessons: one call each, ~45 s and $0.10. */
export function buildEstimate(pages: number, generation = "v2", agentRoute = false): { minutes: number; dollars: number } {
  if (generation === "v1") {
    return { minutes: Math.max(1, Math.round((pages * 45) / 60)), dollars: Math.round(pages * 0.1 * 100) / 100 };
  }
  return { minutes: Math.max(1, pages * (agentRoute ? 20 : 8)), dollars: agentRoute ? 0 : pages * 5 };
}

/** "45 min" or "3.5 h". */
export function durationLabel(minutes: number): string {
  return minutes < 90 ? `${minutes} min` : `${Math.round(minutes / 30) / 2} h`;
}
