import type { TaskType } from "../types";
import type { IconName } from "./Icon";

/** The icon of each task type (menus, the study bar). */
export const TASK_ICON: Record<TaskType, IconName> = { lesson: "lesson", review: "review", quiz: "stopwatch" };
