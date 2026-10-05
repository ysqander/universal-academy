// Soft gating of a v2 teaching sequence: content after an inline check that is neither answered nor skipped stays
// collapsed. Gating state lives per lesson view; answered checks are kept for the browser session (sessionStorage),
// skips are not (a reload closes skipped gates again).
import type { BlockCheckResult, Lesson } from "../types";

export interface CheckOutcome {
  /** What was sent: numeric / expression / true_false_set response ("TFT"), or the chosen index. */
  response: string | null;
  choice: number | null;
  result: BlockCheckResult;
}

export interface FlowState {
  answered: Record<number, CheckOutcome>;
  skipped: Record<number, true>;
}

export type FlowAction =
  | { type: "answered"; index: number; outcome: CheckOutcome }
  | { type: "skipped"; index: number }
  | { type: "reset"; answered?: Record<number, CheckOutcome> };

export const initialFlow = (answered: Record<number, CheckOutcome> = {}): FlowState => ({ answered, skipped: {} });

export function flowReducer(state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case "answered":
      return { ...state, answered: { ...state.answered, [action.index]: action.outcome } };
    case "skipped":
      if (state.skipped[action.index] || state.answered[action.index]) return state;
      return { ...state, skipped: { ...state.skipped, [action.index]: true } };
    case "reset":
      return initialFlow(action.answered);
  }
}

interface GateBlock {
  index: number;
  check: unknown;
}

/**
 * Position (in `blocks`, not the block's own index) of the first block whose inline check is still open — neither
 * answered nor skipped. Everything after it (later blocks, "Go deeper", the start of practice) is behind the gate.
 * Null when nothing is gated.
 */
export function gatePosition(blocks: readonly GateBlock[], state: FlowState): number | null {
  const at = blocks.findIndex((b) => b.check !== null && b.check !== undefined && !state.answered[b.index] && !state.skipped[b.index]);
  return at < 0 ? null : at;
}

/** The blocks to render now, how many are still collapsed behind the gate, and the gating block (null = open). */
export function visibleBlocks<T extends GateBlock>(blocks: readonly T[], state: FlowState, gating: boolean): { shown: T[]; hidden: number; gate: T | null } {
  const at = gating ? gatePosition(blocks, state) : null;
  if (at === null) return { shown: [...blocks], hidden: 0, gate: null };
  return { shown: blocks.slice(0, at + 1), hidden: blocks.length - at - 1, gate: blocks[at] };
}

/** Short, stable fingerprint of the teaching sequence: a regenerated lesson must not inherit old answers. */
export function lessonFingerprint(lesson: Pick<Lesson, "skill_id" | "blocks">): string {
  const text = JSON.stringify([lesson.skill_id, lesson.blocks.map((b) => [b.index, b.kind, b.body, b.check?.kind ?? null, b.check?.prompt ?? null, b.check?.choices ?? null])]);
  let h1 = 0x811c9dc5;
  let h2 = 5381;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = (Math.imul(h2, 33) + c) >>> 0;
  }
  return `${h1.toString(36)}${h2.toString(36)}`;
}

const storageKey = (fingerprint: string): string => `ua:v2-checks:${fingerprint}`;

const isOutcome = (value: unknown): value is CheckOutcome => {
  if (!value || typeof value !== "object") return false;
  const v = value as Partial<CheckOutcome>;
  return Boolean(v.result && typeof v.result === "object" && typeof v.result.feedback === "string");
};

/** Answered checks of this lesson in this browser session ({} when storage is unavailable or unreadable). */
export function loadAnswered(fingerprint: string, storage: Pick<Storage, "getItem"> | null = sessionStorageOrNull()): Record<number, CheckOutcome> {
  if (!storage) return {};
  try {
    const raw = storage.getItem(storageKey(fingerprint));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<number, CheckOutcome> = {};
    for (const [key, value] of Object.entries(parsed)) {
      const index = Number(key);
      if (Number.isInteger(index) && isOutcome(value)) out[index] = value;
    }
    return out;
  } catch {
    return {};
  }
}

export function saveAnswered(fingerprint: string, answered: Record<number, CheckOutcome>, storage: Pick<Storage, "setItem"> | null = sessionStorageOrNull()): void {
  if (!storage) return;
  try {
    storage.setItem(storageKey(fingerprint), JSON.stringify(answered));
  } catch {
    // Private mode / quota: answers just won't survive a reload.
  }
}

function sessionStorageOrNull(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

/** "Your goal: …" is the label the page already shows; drop it from the generated line. */
export const stripGoalLabel = (line: string): string =>
  line.replace(/^\s*(?:\*\*|__)?\s*your goal\s*:?\s*(?:\*\*|__)?\s*:?\s*/i, "").trim();
