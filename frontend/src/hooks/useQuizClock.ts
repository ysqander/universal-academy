import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { clockAnnouncement, localDeadline, secondsLeft, syncDeadline } from "../lib/quiz";
import type { QuizInfo } from "../types";

export interface QuizClock {
  /** Seconds left (fractional); null while no clock runs. */
  secondsLeft: number | null;
  /** The countdown reached zero (the server then has the last word). */
  expired: boolean;
  /** Set when the clock crosses one minute left / zero, for a polite live region. */
  announcement: string | null;
}

interface Reading {
  quiz: QuizInfo | null;
  receivedAt: number;
  deadline: number | null;
}

const TICK_MS = 250;

/**
 * Countdown of a running quiz, synced to the server: every new QuizInfo (each answer returns one) is a fresh reading of
 * `seconds_left`, counted from `receivedAt` — when that response arrived (lib/quiz localDeadline / syncDeadline).
 * Pass null when no quiz clock runs (intro, results, other tasks). `onExpired` is called once per deadline when the
 * countdown reaches zero (then ask the server for the results).
 */
export function useQuizClock(quiz: QuizInfo | null, receivedAt: number, onExpired?: () => void): QuizClock {
  const [reading, setReading] = useState<Reading>(() => ({ quiz, receivedAt, deadline: quiz ? localDeadline(quiz, receivedAt) : null }));
  const [now, setNow] = useState(() => Date.now());
  const [announcement, setAnnouncement] = useState<string | null>(null);
  const onExpiredRef = useRef(onExpired);
  useLayoutEffect(() => {
    onExpiredRef.current = onExpired;
  });

  // A new reading (or the clock stopping): adjusted while rendering, from values the caller recorded.
  if (reading.quiz !== quiz || reading.receivedAt !== receivedAt) {
    setReading({ quiz, receivedAt, deadline: quiz ? syncDeadline(reading.deadline, localDeadline(quiz, receivedAt)) : null });
  }

  const { deadline } = reading;
  useEffect(() => {
    if (deadline === null) return;
    let last = Date.now();
    let reported = false;
    const id = window.setInterval(() => {
      const current = Date.now();
      const said = clockAnnouncement(secondsLeft(deadline, last), secondsLeft(deadline, current));
      last = current;
      setNow(current);
      if (said) setAnnouncement(said);
      if (!reported && current >= deadline) {
        reported = true;
        onExpiredRef.current?.();
      }
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [deadline]);

  // `now` may predate the reading (no clock was ticking before it): never count from earlier than its arrival.
  const left = deadline === null ? null : secondsLeft(deadline, Math.max(now, reading.receivedAt));
  return { secondsLeft: left, expired: left !== null && left <= 0, announcement: deadline === null ? null : announcement };
}
