import { useCallback, useEffect, useState } from "react";

/** Seconds since mount, ticking once per second while `running`; `elapsed()` is exact (fractional). */
export function useStopwatch(running: boolean): { seconds: number; elapsed: () => number } {
  const [start] = useState(() => Date.now());
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [running, start]);

  const elapsed = useCallback(() => (Date.now() - start) / 1000, [start]);
  return { seconds, elapsed };
}
