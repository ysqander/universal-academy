import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { abandonTask, continueLearning, easierStep, getCourse, getNext, isApiError, startPractice, startTask } from "../../api";
import { CourseProvider } from "../../components/CourseProvider";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { ErrorState, LoadingState, StateMessage } from "../../components/StateMessage";
import { TaskMenu } from "../../components/TaskMenu";
import { useToast } from "../../context/toast";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { useResource } from "../../hooks/useResource";
import { TASK_LABEL } from "../../lib/format";
import { plainText, prettyTitle } from "../../lib/markdown";
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

/**
 * The task handed over by an explicit start (router state), else the already active task. Never starts a task:
 * a reload, browser Back or a bookmark must not create one (D10) — without an open task the page offers a menu.
 */
async function loadInitial(courseId: string, provided: Task | null): Promise<Initial> {
  const next = await getNext(courseId);
  return { task: provided ?? next.task, next };
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
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);
  // The last answer finished the task while its feedback is still on screen (task is replaced on "See results").
  const [finishedId, setFinishedId] = useState<string | null>(null);
  // The plan of a task just before it finished (a halted lesson reports only what was answered).
  const [planBefore, setPlanBefore] = useState<{ id: string; planned: number } | null>(null);
  const taskRef = useRef<Task | null>(task);
  const bootPromise = useRef<Promise<Initial> | null>(null);
  const autoStarted = useRef<string | null>(null);
  const course = useResource(`course:${courseId}`, (signal) => getCourse(courseId, signal));
  useDocumentTitle(task ? `${task.type === "lesson" ? "Lesson" : "Review"}: ${plainText(prettyTitle(task.skill.title))}` : "Study");

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

  useEffect(() => {
    taskRef.current = task;
  }, [task]);

  const replaceTask = useCallback((next: Task) => {
    const prev = taskRef.current;
    if (prev && prev.id === next.id && prev.phase === "practice" && next.phase === "done") {
      setPlanBefore({ id: prev.id, planned: prev.progress.planned ?? 0 });
    }
    setTask(next);
    setFinishedId(null);
  }, []);

  const refreshSession = useCallback(() => {
    getNext(courseId).then(
      (next) => setSession(next.session),
      () => undefined,
    );
  }, [courseId]);

  // A flag can void an answer that earned XP or re-grade the task: take the server's task and fresh session totals.
  const onFlagged = useCallback(
    (next: Task) => {
      replaceTask(next);
      refreshSession();
    },
    [replaceTask, refreshSession],
  );

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
    if (result.task.phase === "done") {
      setFinishedId(result.task.id);
      setConfirmEnd(false);
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
      replaceTask(await start());
    } catch (err) {
      toast.error(err);
      // 409 "Finish or leave '…' first": another task is open after all — pick it up instead.
      if (isApiError(err, 409) && task === null) retryBoot();
    } finally {
      setBusy(null);
    }
  };

  const endTask = async () => {
    if (!task) return;
    setBusy("end");
    try {
      const after = await abandonTask(task.id);
      setConfirmEnd(false);
      setBusy(null);
      if (after.outcome !== "abandoned") {
        // The task had already finished (passed / failed): show its results rather than "set aside".
        replaceTask(after);
        return;
      }
      toast.show("Task set aside. It will be offered again later.", "info");
      // Replace: browser Back should not land on a study page for the task that was just left.
      navigate(`/courses/${encodeURIComponent(courseId)}`, { replace: true });
    } catch (err) {
      toast.error(err);
      setBusy(null);
    }
  };

  const goToMap = useCallback(() => navigate(`/courses/${encodeURIComponent(courseId)}`), [navigate, courseId]);
  const onSession = useCallback((next: NextTaskResponse) => setSession(next.session), []);
  const pageRange: [number, number] | null = course.data ? [course.data.start_page, course.data.end_page] : null;
  const ongoing = task !== null && task.phase !== "done" && finishedId !== task.id;

  let body;
  if (task === null) {
    if (boot.status === "error") {
      body = <ErrorState error={boot.error} onRetry={retryBoot} title="Couldn't start a task" />;
    } else if (boot.status === "loading") {
      body = <LoadingState label="Preparing your next task…" />;
    } else if (boot.next && boot.next.options.length > 0) {
      const { options, session: info } = boot.next;
      const top = options[0];
      body = (
        <div className="study-ready">
          <div className="study-ready__head">
            <p className="kicker">{info.goal_reached ? "Daily goal reached — anything more is a bonus" : "Ready when you are"}</p>
            <h1 className="study-ready__title">Your next step</h1>
            <p className="study-ready__reason">
              Next: {TASK_LABEL[top.type].toLowerCase()} <span className="key">{top.skill.key}</span>{" "}
              <Markdown inline>{prettyTitle(top.skill.title)}</Markdown> — {top.reason}
            </p>
            <button
              type="button"
              className="btn btn--primary btn--lg"
              onClick={() => void startNext("continue", () => continueLearning(courseId))}
              disabled={busy !== null}
            >
              {busy === "continue" ? <Spinner size={16} label="Starting" /> : <Icon name="play" size={18} />} Continue learning
            </button>
          </div>
          {options.length > 1 && (
            <section className="completion__section">
              <h2 className="lesson__h">Or choose</h2>
              <TaskMenu
                options={options}
                pendingKey={busy && busy !== "continue" ? busy : null}
                disabled={busy !== null}
                onStart={(option: TaskOption) =>
                  void startNext(`${option.skill.id}:${option.type}`, () => startTask(courseId, { skill_id: option.skill.id, type: option.type }))
                }
              />
            </section>
          )}
          <Link to={`/courses/${encodeURIComponent(courseId)}`} className="btn btn--ghost study-ready__map">
            <Icon name="graph" size={16} /> Back to the map
          </Link>
        </div>
      );
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
        plannedBefore={planBefore?.id === task.id ? planBefore.planned : 0}
        // The server's figure for the finished task: includes re-scoring after a flag, the lesson bonus, and 0 for a
        // voided or abandoned task (a local sum of answer XP would go stale).
        xpEarned={task.xp_value}
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
    body = (
      <PracticeView task={task} onAnswered={onAnswered} onAdvance={replaceTask} onFlagged={onFlagged} onEasier={onEasier} onGoToMap={goToMap} />
    );
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
          to the map — the task stays open and <strong>Continue learning</strong> resumes it (starting a different task there asks
          first).
        </p>
      </Dialog>
    </CourseProvider>
  );
}
