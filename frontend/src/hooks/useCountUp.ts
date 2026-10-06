import { useEffect, useState } from "react";

/** Counts up to `target` once, quickly (respects reduced motion). */
export function useCountUp(target: number, durationMs = 900): number {
  const [animate] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [value, setValue] = useState(animate ? 0 : target);
  useEffect(() => {
    if (!animate || target <= 0) return;
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      setValue(Math.round(target * (1 - (1 - t) ** 3)));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [animate, target, durationMs]);
  return animate ? value : target;
}
