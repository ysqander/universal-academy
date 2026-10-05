import type { JobState, JobStatus } from "../types";

const ACTIVE_STATES: JobState[] = ["queued", "running"];

export const isActiveState = (state: JobState | null | undefined): boolean => state != null && ACTIVE_STATES.includes(state);

export const isJobActive = (job: JobStatus | null | undefined): boolean => isActiveState(job?.state);
