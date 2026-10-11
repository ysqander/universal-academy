import { useEffect } from "react";
import { getHealth } from "../api";
import type { Health } from "../types";
import { useResource, type Resource } from "./useResource";

/** Server limit for a course's page range when an older server doesn't report `max_course_pages`. */
export const DEFAULT_MAX_COURSE_PAGES = 80;

let shared: Promise<Health> | null = null;
const reloaders = new Set<() => void>();

/** GET /api/health once per page load, shared by every caller; a failed request is retried on the next call. */
export function loadHealth(): Promise<Health> {
  shared ??= getHealth().catch((err: unknown) => {
    shared = null;
    throw err;
  });
  return shared;
}

/** Fetch /api/health again for every component showing it (after API keys are saved in the app). */
export function refreshHealth(): void {
  shared = null;
  reloaders.forEach((reload) => reload());
}

/** Server capabilities and limits (API keys present, offline mode, max course pages). */
export function useHealth(): Resource<Health> {
  const resource = useResource("health", () => loadHealth());
  const { reload } = resource;
  useEffect(() => {
    const onRefresh = () => void reload();
    reloaders.add(onRefresh);
    return () => {
      reloaders.delete(onRefresh);
    };
  }, [reload]);
  return resource;
}
