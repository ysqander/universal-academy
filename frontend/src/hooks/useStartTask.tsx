import { useCallback, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { continueLearning, isApiError, startTask } from "../api";
import { Dialog } from "../components/Dialog";
import { Spinner } from "../components/Spinner";
import { useToast } from "../context/toast";
// Plain text only: this hook is used on eagerly loaded pages (library), which shouldn't pull in Markdown + KaTeX.
import { plainText, prettyTitle } from "../lib/markdown";
import type { Task, TaskType } from "../types";

export interface StudyLocationState {
  task?: Task;
}

export interface StartOptions {
  /** Learn a locked skill anyway (the learner overrides prerequisite gating). */
  force?: boolean;
}

interface ReplacePrompt {
  skillId: string;
  type: TaskType | null;
  force: boolean;
  /** Title / type of the task that would be left (null when only the server's 409 told us about it). */
  activeTitle: string | null;
  activeType: TaskType | null;
}

// selector.start_task: 409 "Finish or leave '<skill title>' first." while another task is active.
const ACTIVE_CONFLICT = /^Finish or leave ['‘’"“](.*)['‘’"”] first\.?$/s;

/**
 * Start (or resume) a task and open the study screen with it. Errors become toasts.
 * Starting a different task while one is open first asks the learner to leave it (D6): pass the currently
 * active task when known; a 409 from the server opens the same confirmation. Render `replaceDialog`.
 */
export function useStartTask(courseId: string, active: Task | null = null) {
  const navigate = useNavigate();
  const toast = useToast();
  const [pending, setPending] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<ReplacePrompt | null>(null);

  const open = useCallback(
    async (pendingKey: string, start: () => Promise<Task>, onConflict?: (message: string) => boolean) => {
      setPending(pendingKey);
      try {
        const task = await start();
        const state: StudyLocationState = { task };
        navigate(`/courses/${encodeURIComponent(courseId)}/study`, { state });
      } catch (err) {
        if (!(onConflict && isApiError(err, 409) && onConflict(err.message))) toast.error(err);
      } finally {
        setPending(null);
      }
    },
    [courseId, navigate, toast],
  );

  const continueNext = useCallback(() => open("continue", () => continueLearning(courseId)), [open, courseId]);

  const startSkill = useCallback(
    (skillId: string, type: TaskType | null = null, { force = false }: StartOptions = {}) => {
      const openTask = active && active.phase !== "done" ? active : null;
      if (openTask && !(openTask.skill.id === skillId && (type === null || type === openTask.type))) {
        setPrompt({ skillId, type, force, activeTitle: openTask.skill.title, activeType: openTask.type });
        return Promise.resolve();
      }
      return open(
        `${skillId}:${type ?? "auto"}`,
        () => startTask(courseId, { skill_id: skillId, type, force }),
        (message) => {
          const match = ACTIVE_CONFLICT.exec(message.trim());
          if (!match) return false;
          setPrompt({ skillId, type, force, activeTitle: match[1] || null, activeType: null });
          return true;
        },
      );
    },
    [open, courseId, active],
  );

  const confirmReplace = () => {
    if (!prompt) return;
    const { skillId, type, force } = prompt;
    void open(`${skillId}:${type ?? "auto"}`, () => startTask(courseId, { skill_id: skillId, type, force, replace_active: true })).then(() =>
      setPrompt(null),
    );
  };

  const resumeActive = () => {
    setPrompt(null);
    void continueNext();
  };

  const what = prompt?.activeType === "review" ? "review" : prompt?.activeType === "lesson" ? "lesson" : "task";
  const replaceDialog: ReactNode = (
    <Dialog
      open={prompt !== null}
      onClose={() => setPrompt(null)}
      size="sm"
      title={prompt?.activeTitle ? `Leave ‘${plainText(prettyTitle(prompt.activeTitle))}’?` : "Leave your open task?"}
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={resumeActive} disabled={pending !== null}>
            Resume it instead
          </button>
          <button type="button" className="btn btn--primary" onClick={confirmReplace} disabled={pending !== null}>
            {pending !== null && pending !== "continue" && <Spinner size={14} label="Starting" />} Leave it and start
          </button>
        </>
      }
    >
      <p>
        Progress on this {what} will be lost. It will be offered again later. To keep it, resume it now and finish it first.
      </p>
    </Dialog>
  );

  return { continueNext, startSkill, pending, replaceDialog };
}
