import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  abandonTask,
  continueLearning,
  easierStep,
  getCourse,
  getNext,
  isApiError,
  startPractice,
  startTask,
} from "../../api";
import { CourseProvider } from "../../components/CourseProvider";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { Spinner } from "../../components/Spinner";
import { ErrorState, LoadingState, StateMessage } from "../../components/StateMessage";
import { useToast } from "../../context/toast";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { useResource } from "../../hooks/useResource";
import { plainText } from "../../lib/markdown";
import type { StudyLocationState } from "../../hooks/useStartTask";
import type { AnswerResult, NextTaskResponse, SessionInfo, Task, TaskOption } from "../../types";
import { CompletionScreen } from "./CompletionScreen";
import { LearnView } from "./LearnView";
import { PracticeView } from "./PracticeView";
import { StudyTopBar } from "./StudyTopBar";
import "./StudyPage.css";

interface Initial {
  task: Task | null;
  next: NextTaskResponse;
}

type BootState = { status: "loading" } | { status: "error"; error: unknown } | { status: "ready"; next: NextTaskResponse | null };

/** Resume the active task, or start the top option; null when there is nothing to do. */
async function loadInitial(courseId: string, provided: Task | null): Promise<Initial> {
  const next = await getNext(courseId);
  if (provided) return { task: provided, next };
  if (next.task) return { task: next.task, next };
  if (next.options.length === 0) return { task: null, next };
  try {
    return { task: await continueLearning(courseId), next };
  } catch (err) {
    if (isApiError(err, 409)) return { task: null, next };
    throw err;
  }
}

export default function StudyPage({ courseId }: { courseId: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();
  const [provided] = useState<Task | null>(() => {
    const task = (location.state as StudyLocationState | null)?.task ?? null;
    return task && task.course_id === courseId ? task : null;
  });
  const [task, setTask] = useState<Task | null>(provided);
  const [boot, setBoot] = useState<BootState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [xp, setXp] = useState<{ taskId: string; earned: number }>({ taskId: "", earned: 0 });
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const bootPromise = useRef<Promise<Initial> | null>(null);
  const autoStarted = useRef<string | null>(null);
  const course = useResource(`course:${courseId}`, (signal) => getCourse(courseId, signal));
  useDocumentTitle(task ? `${task.type === "lesson" ? "Lesson" : "Review"}: ${plainText(task.skill.title)}` : "Study");

  // Router state carries a freshly started task; drop it so a reload resumes from the server instead.
  useEffect(() => {
    if (location.state) navigate(location.pathname, { replace: true, state: null });
  }, [location.state, location.pathname, navigate]);

  useEffect(() => {
    let cancelled = false;
    bootPromise.current ??= loadInitial(courseId, provided);
    bootPromise.current.then(
      (initial) => {
        if (cancelled) return;
        setSession(initial.next.session);
        setTask((current) => current ?? initial.task);
        setBoot({ status: "ready", next: initial.next });
      },
      (error: unknown) => {
        if (!cancelled) setBoot({ status: "error", error });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [courseId, provided, attempt]);

  const retryBoot = () => {
    bootPromise.current = null;
    setBoot({ status: "loading" });
    setAttempt((n) => n + 1);
  };

  const replaceTask = useCallback((next: Task) => setTask(next), []);

  // A new task or phase starts at the top of the page.
  const view = task ? `${task.id}:${task.phase}` : null;
  const lastView = useRef(view);
  useEffect(() => {
    if (lastView.current !== null && view !== lastView.current) window.scrollTo({ top: 0, behavior: "smooth" });
    lastView.current = view;
  }, [view]);

  const beginPractice = useCallback(
    async (current: Task) => {
      setBusy("practice");
      try {
        replaceTask(await startPractice(current.id));
      } catch (err) {
        toast.error(err);
      } finally {
        setBusy(null);
      }
    },
    [replaceTask, toast],
  );

  // Reviews have no reading phase: go straight to the first question.
  useEffect(() => {
    if (task && task.type === "review" && task.phase === "learn" && autoStarted.current !== task.id) {
      autoStarted.current = task.id;
      void beginPractice(task);
    }
  }, [task, beginPractice]);

  const onAnswered = useCallback((result: AnswerResult) => {
    if (result.xp_awarded > 0) {
      setXp((prev) => ({ taskId: result.task.id, earned: (prev.taskId === result.task.id ? prev.earned : 0) + result.xp_awarded }));
    }
  }, []);

  const onEasier = useCallback(async () => {
    if (!task) return false;
    try {
      replaceTask(await easierStep(task.id));
      return true;
    } catch (err) {
      toast.error(err);
      return false;
    }
  }, [task, replaceTask, toast]);

  const startNext = async (key: string, start: () => Promise<Task>) => {
    setBusy(key);
    try {
      setTask(await start());
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(null);
    }
  };

  const endTask = async () => {
    if (!task) return;
    setBusy("end");
    try {
      await abandonTask(task.id);
      toast.show("Task set aside. It will be offered again later.", "info");
      navigate(`/courses/${encodeURIComponent(courseId)}`);
    } catch (err) {
      toast.error(err);
      setBusy(null);
    }
  };

  const goToMap = useCallback(() => navigate(`/courses/${encodeURIComponent(courseId)}`), [navigate, courseId]);
  const onSession = useCallback((next: NextTaskResponse) => setSession(next.session), []);
  const pageRange: [number, number] | null = course.data ? [course.data.start_page, course.data.end_page] : null;
  const ongoing = task !== null && task.phase !== "done";

  let body;
  if (task === null) {
    if (boot.status === "error") {
      body = <ErrorState error={boot.error} onRetry={retryBoot} title="Couldn't start a task" />;
    } else if (boot.status === "loading") {
      body = <LoadingState label="Preparing your next task…" />;
    } else {
      body = (
        <StateMessage
          icon="check"
          title={boot.next?.session.goal_reached ? "Done for today" : "Nothing to study right now"}
          actions={
            <Link to={`/courses/${encodeURIComponent(courseId)}`} className="btn btn--primary">
              <Icon name="graph" size={16} /> Back to the map
            </Link>
          }
        >
          {boot.next?.message || "All caught up. New lessons and reviews will appear here when they're ready."}
        </StateMessage>
      );
    }
  } else if (task.phase === "done") {
    body = (
      <CompletionScreen
        key={task.id}
        task={task}
        xpEarned={xp.taskId === task.id ? xp.earned : 0}
        onSession={onSession}
        onContinue={() => void startNext("continue", () => continueLearning(courseId))}
        onStart={(option: TaskOption) =>
          void startNext(`${option.skill.id}:${option.type}`, () => startTask(courseId, { skill_id: option.skill.id, type: option.type }))
        }
        pendingKey={busy}
      />
    );
  } else if (task.phase === "learn") {
    body =
      task.type === "lesson" && task.lesson ? (
        <LearnView task={task} lesson={task.lesson} onStart={() => void beginPractice(task)} starting={busy === "practice"} />
      ) : task.type === "lesson" ? (
        <StateMessage
          icon="clock"
          title="This lesson isn't ready yet"
          actions={
            <button type="button" className="btn" onClick={goToMap}>
              Back to the map
            </button>
          }
        >
          It is still being written. Try another task from the map in the meantime.
        </StateMessage>
      ) : (
        <LoadingState label="Starting your review…" />
      );
  } else {
    body = <PracticeView task={task} onAnswered={onAnswered} onAdvance={replaceTask} onEasier={onEasier} onGoToMap={goToMap} />;
  }

  return (
    <CourseProvider courseId={courseId} pageRange={pageRange}>
      <StudyTopBar courseId={courseId} task={task} session={session} onEndTask={ongoing ? () => setConfirmEnd(true) : null} />
      <main id="main" className="study" data-phase={task?.phase ?? "none"}>
        {body}
      </main>
      <Dialog
        open={confirmEnd}
        onClose={() => setConfirmEnd(false)}
        title={task?.type === "review" ? "End this review?" : "Set this lesson aside?"}
        size="sm"
        footer={
          <>
            <button type="button" className="btn btn--ghost" onClick={() => setConfirmEnd(false)}>
              Keep going
            </button>
            <button type="button" className="btn btn--primary" onClick={() => void endTask()} disabled={busy === "end"}>
              {busy === "end" && <Spinner size={14} label="Ending" />} End task
            </button>
          </>
        }
      >
        <p>
          Progress inside this task is discarded and it earns no XP. It will be offered again later. To pause instead, just go back
          to the map — the task stays open and <strong>Continue learning</strong> resumes it.
        </p>
      </Dialog>
    </CourseProvider>
  );
}
