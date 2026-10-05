import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { isAbortError } from "../api";

export interface Resource<T> {
  data: T | undefined;
  error: unknown;
  /** True while the first load for the current key is in flight (not during background polls). */
  loading: boolean;
  reload: () => Promise<void>;
  mutate: (next: T | undefined | ((prev: T | undefined) => T | undefined)) => void;
}

interface Options {
  /** Re-fetch every `pollMs` while set; pauses while the tab is hidden. */
  pollMs?: number | null;
  /** Keep showing the previous key's data while the new key loads. */
  keepPrevious?: boolean;
}

interface State<T> {
  key: string | null;
  data: T | undefined;
  error: unknown;
  loading: boolean;
}

/**
 * Fetch data identified by `key` (null = disabled). The loader may change between renders;
 * only a change of `key` or `pollMs` restarts loading. Stale responses are discarded.
 */
export function useResource<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  { pollMs = null, keepPrevious = false }: Options = {},
): Resource<T> {
  const loaderRef = useRef(load);
  const keyRef = useRef(key);
  const [state, setState] = useState<State<T>>({ key, data: undefined, error: null, loading: key !== null });

  useLayoutEffect(() => {
    loaderRef.current = load;
    keyRef.current = key;
  });

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    let timer: number | undefined;

    const run = async (initial: boolean) => {
      if (!initial && document.hidden) {
        timer = window.setTimeout(() => void run(false), pollMs ?? 0);
        return;
      }
      if (initial) {
        setState((prev) => ({
          key,
          data: prev.key === key || keepPrevious ? prev.data : undefined,
          error: null,
          loading: true,
        }));
      }
      try {
        const data = await loaderRef.current(controller.signal);
        if (!controller.signal.aborted) setState({ key, data, error: null, loading: false });
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((prev) => ({ ...prev, key, error, loading: false }));
      }
      if (pollMs && !controller.signal.aborted) {
        timer = window.setTimeout(() => void run(false), pollMs);
      }
    };

    void run(true);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [key, pollMs, keepPrevious]);

  const reload = useCallback(async () => {
    const current = keyRef.current;
    if (current === null) return;
    try {
      const data = await loaderRef.current(new AbortController().signal);
      if (keyRef.current === current) setState({ key: current, data, error: null, loading: false });
    } catch (error) {
      if (keyRef.current === current) setState((prev) => ({ ...prev, error, loading: false }));
    }
  }, []);

  const mutate = useCallback((next: T | undefined | ((prev: T | undefined) => T | undefined)) => {
    setState((prev) => ({
      ...prev,
      data: typeof next === "function" ? (next as (p: T | undefined) => T | undefined)(prev.data) : next,
    }));
  }, []);

  const visible = state.key === key || keepPrevious;
  return {
    data: visible ? state.data : undefined,
    error: state.key === key ? state.error : null,
    loading: key !== null && (state.key !== key || state.loading),
    reload,
    mutate,
  };
}
