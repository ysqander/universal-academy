import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { continueLearning, isApiError, isCooldownConflict, isQuizFirstConflict, startQuiz, startTask } from "../api";
import { Dialog } from "../components/Dialog";
import { Spinner } from "../components/Spinner";
import { useToast } from "../context/toast";
import { isCooldownMessage } from "../lib/cooldown";
// Plain text only: this hook is used on eagerly loaded pages (library), which shouldn't pull in Markdown + KaTeX.
import { plainText, prettyTitle } from "../lib/markdown";
import { activeConflict, isOptionalRetakeIntro, optionKey, taskTitle } from "../lib/quiz";
import type { Task, TaskOption, TaskType } from "../types";

export interface StudyLocationState {
  task?: Task;
}

export interface StartOptions {
  /** Learn a locked skill anyway, or start a lesson during its cooldown ("Start anyway"): the learner's override. */
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

/**
 * Start (or resume) a task and open the study screen with it. Errors become toasts.
 * Starting a different task while one is open first asks the learner to leave it (D6): pass the currently
 * active task when known; a 409 from the server opens the same confirmation. Render `replaceDialog`.
 * A lesson still cooling down (409 "This lesson comes back at …") is said as information, not as an error, and
 * reported to `onCooldown` (e.g. to refresh the map so the drawer shows the return time and "Start anyway").
 * A due quiz comes first (SPEC §6.7): the server's 409 "Take the quiz first …" is said as information and reported to
 * `onQuizFirst` (refresh the menu so it offers the quiz); an open quiz is never offered to be left (it can't be),
 * except an optional retake still at its intro: starting something else then asks to leave it like any other task.
 */
export function useStartTask(
  courseId: string,
  active: Task | null = null,
  onCooldown?: (skillId: string, message: string) => void,
  onQuizFirst?: (message: string) => void,
) {
  const navigate = useNavigate();
  const toast = useToast();
  const [pending, setPending] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<ReplacePrompt | null>(null);
  const onCooldownRef = useRef(onCooldown);
  const onQuizFirstRef = useRef(onQuizFirst);
  useEffect(() => {
    onCooldownRef.current = onCooldown;
    onQuizFirstRef.current = onQuizFirst;
  });
  // The toast itself comes from `open`; this only tells the page (refresh the map, offer "Start anyway").
  const cooldownNoticed = useCallback((skillId: string, message: string) => {
    if (isCooldownMessage(message)) onCooldownRef.current?.(skillId, message);
  }, []);

  const open = useCallback(
    async (pendingKey: string, start: () => Promise<Task>, onConflict?: (message: string) => boolean) => {
      setPending(pendingKey);
      try {
        const task = await start();
        const state: StudyLocationState = { task };
        navigate(`/courses/${encodeURIComponent(courseId)}/study`, { state });
      } catch (err) {
        if (isQuizFirstConflict(err)) {
          toast.show(err.message, "info");
          onQuizFirstRef.current?.(err.message);
          return;
        }
        if (onConflict && isApiError(err, 409) && onConflict(err.message)) return;
        if (isCooldownConflict(err)) toast.show(err.message, "info");
        else toast.error(err);
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
      if (openTask?.type === "quiz" && !isOptionalRetakeIntro(openTask)) {
        // A quiz can't be set aside: finish it first (Continue / Resume quiz opens it).
        toast.show("Finish the quiz first — its clock keeps running once started.", "info");
        return Promise.resolve();
      }
      if (openTask && !(openTask.skill?.id === skillId && (type === null || type === openTask.type))) {
        setPrompt({ skillId, type, force, activeTitle: taskTitle(openTask), activeType: openTask.type });
        return Promise.resolve();
      }
      return open(
        `${skillId}:${type ?? "auto"}`,
        () => startTask(courseId, { skill_id: skillId, type, force }),
        (message) => {
          const other = activeConflict(message);
          if (!other) {
            cooldownNoticed(skillId, message);
            return false;
          }
          setPrompt({ skillId, type, force, ...other });
          return true;
        },
      );
    },
    [open, courseId, active, cooldownNoticed, toast],
  );

  /**
   * A menu option: a skill's lesson / review, or the quiz (which has no skill). An open quiz is resumed; an open
   * lesson or review may be finished first — starting the quiz instead asks to leave it, like any other start.
   */
  const startOption = useCallback(
    (option: TaskOption) => {
      if (option.skill) return startSkill(option.skill.id, option.type);
      const openTask = active && active.phase !== "done" ? active : null;
      if (openTask?.type === "quiz") return continueNext();
      if (openTask) {
        setPrompt({ skillId: "", type: "quiz", force: false, activeTitle: taskTitle(openTask), activeType: openTask.type });
        return Promise.resolve();
      }
      return open(optionKey(option), () => startQuiz(courseId), (message) => {
        const other = activeConflict(message);
        if (!other) return false;
        setPrompt({ skillId: "", type: "quiz", force: false, ...other });
        return true;
      });
    },
    [open, startSkill, continueNext, courseId, active],
  );

  const confirmReplace = () => {
    if (!prompt) return;
    const { skillId, type, force } = prompt;
    if (type === "quiz") {
      void open(optionKey({ skill: null, type }), () => startQuiz(courseId, true)).then(() => setPrompt(null));
      return;
    }
    void open(
      `${skillId}:${type ?? "auto"}`,
      () => startTask(courseId, { skill_id: skillId, type, force, replace_active: true }),
      (message) => {
        cooldownNoticed(skillId, message);
        return false;
      },
    ).then(() => setPrompt(null));
  };

  const resumeActive = () => {
    setPrompt(null);
    void continueNext();
  };

  const what = prompt?.activeType === "review" ? "review" : prompt?.activeType === "lesson" ? "lesson" : prompt?.activeType === "quiz" ? "retake" : "task";
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
            {pending !== null && pending !== "continue" && <Spinner size={14} label="Starting" />}{" "}
            {prompt?.type === "quiz" ? "Leave it and start the quiz" : "Leave it and start"}
          </button>
        </>
      }
    >
      {what === "retake" ? (
        // Only an optional retake at its intro can be left (its clock hasn't started): nothing is lost.
        <p>
          The retake hasn't started, so nothing is lost — it stays on offer at the end of your menu for when you want it.
        </p>
      ) : (
        <p>
          Progress on this {what} will be lost. It will be offered again later. To keep it, resume it now and finish it first
          {prompt?.type === "quiz" ? " — the quiz comes straight after" : ""}.
        </p>
      )}
    </Dialog>
  );

  return { continueNext, startSkill, startOption, pending, replaceDialog };
}
