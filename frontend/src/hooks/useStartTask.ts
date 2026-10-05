import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { continueLearning, startTask } from "../api";
import { useToast } from "../context/toast";
import type { Task, TaskType } from "../types";

export interface StudyLocationState {
  task?: Task;
}

/** Start (or resume) a task and open the study screen with it. Errors become toasts. */
export function useStartTask(courseId: string) {
  const navigate = useNavigate();
  const toast = useToast();
  const [pending, setPending] = useState<string | null>(null);

  const open = useCallback(
    async (pendingKey: string, start: () => Promise<Task>) => {
      setPending(pendingKey);
      try {
        const task = await start();
        const state: StudyLocationState = { task };
        navigate(`/courses/${encodeURIComponent(courseId)}/study`, { state });
      } catch (err) {
        toast.error(err);
      } finally {
        setPending(null);
      }
    },
    [courseId, navigate, toast],
  );

  const continueNext = useCallback(() => open("continue", () => continueLearning(courseId)), [open, courseId]);
  const startSkill = useCallback(
    (skillId: string, type: TaskType | null = null) => open(`${skillId}:${type ?? "auto"}`, () => startTask(courseId, { skill_id: skillId, type })),
    [open, courseId],
  );

  return { continueNext, startSkill, pending };
}
