import { getHealth } from "../api";
import type { Health } from "../types";
import { useResource, type Resource } from "./useResource";

/** Server limit for a course's page range when an older server doesn't report `max_course_pages`. */
export const DEFAULT_MAX_COURSE_PAGES = 80;

let shared: Promise<Health> | null = null;

/** GET /api/health once per page load, shared by every caller; a failed request is retried on the next call. */
export function loadHealth(): Promise<Health> {
  shared ??= getHealth().catch((err: unknown) => {
    shared = null;
    throw err;
  });
  return shared;
}

/** Server capabilities and limits (API keys present, offline mode, max course pages). */
export function useHealth(): Resource<Health> {
  return useResource("health", () => loadHealth());
}
