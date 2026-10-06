// A halted lesson's cooldown (SPEC §6.3): after a failed attempt the lesson comes back about 2 hours later with fresh
// questions. The graph sends SkillNode.cooldown_until; starting the lesson before then is a 409 "This lesson comes back
// at HH:MM (local time) with fresh questions." unless the start sends force=true ("Start anyway").

/** selector.start_task's 409 while the lesson cools down. */
const COOLDOWN_CONFLICT = /\bcomes back at\b/i;

export const isCooldownMessage = (message: string): boolean => COOLDOWN_CONFLICT.test(message);

/** When the cooldown ends (ms since the epoch) while it is still ahead; null when there is none or it is over. */
export function cooldownEnd(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null;
  const end = Date.parse(iso);
  return Number.isFinite(end) && end > now ? end : null;
}

const sameDay = (a: Date, b: Date): boolean =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** "at 14:43" (local time, 24 h as in the server's message); "tomorrow at 00:15" / "on Wed at 09:00" when not today. */
export function returnTimePhrase(end: number, now: number = Date.now(), locale?: string): string {
  const at = new Date(end);
  const today = new Date(now);
  const time = at.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  if (sameDay(at, today)) return `at ${time}`;
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  if (sameDay(at, tomorrow)) return `tomorrow at ${time}`;
  return `on ${at.toLocaleDateString(locale, { weekday: "short" })} at ${time}`;
}

/** "at 14:43" from the server's 409 ("… comes back at 14:43 (local time) …"), when it names a time. */
export function returnTimeFromMessage(message: string): string | null {
  const match = /\bcomes back at\s+(\d{1,2}[:.]\d{2}(?:\s?[AaPp]\.?[Mm]\.?)?)/.exec(message);
  return match ? `at ${match[1]}` : null;
}
