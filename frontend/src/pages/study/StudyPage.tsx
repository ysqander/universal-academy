import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useBlocker, useLocation, useNavigate, type BlockerFunction } from "react-router-dom";
import {
  abandonTask,
  continueLearning,
  easierStep,
  getCourse,
  getNext,
  getTask,
  isApiError,
  isCooldownConflict,
  isQuizFirstConflict,
  startPractice,
  startQuiz,
  startTask,
} from "../../api";
import { CourseProvider } from "../../components/CourseProvider";
import { Dialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { ErrorState, LoadingState, StateMessage } from "../../components/StateMessage";
import { TaskMenu } from "../../components/TaskMenu";
import { useToast } from "../../context/toast";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { useQuizClock } from "../../hooks/useQuizClock";
import { useResource } from "../../hooks/useResource";
import { TASK_LABEL } from "../../lib/format";
import { keepsAnswersOnLeave } from "../../lib/guided";
import { plainText, prettyTitle } from "../../lib/markdown";
import {
  forgetOpenQuiz,
  formatCountdown,
  isRetakeOption,
  openQuizId,
  optionKey,
  optionMinutes,
  quizContentLock,
  rememberOpenQuiz,
  taskTitle,
} from "../../lib/quiz";
import type { StudyLocationState } from "../../hooks/useStartTask";
import type { AnswerResult, NextTaskResponse, SessionInfo, Task, TaskOption } from "../../types";
import { CompletionScreen } from "./CompletionScreen";
import { LearnView } from "./LearnView";
import { PracticeView } from "./PracticeView";
import { QuizIntro } from "./QuizIntro";
import { QuizResults } from "./QuizResults";
import { QuizView } from "./QuizView";
import { StudyTopBar } from "./StudyTopBar";
import "./StudyPage.css";
import "./Quiz.css";

interface Initial {
  task: Task | null;
  next: NextTaskResponse;
}

type BootState = { status: "loading" } | { status: "error"; error: unknown } | { status: "ready"; next: NextTaskResponse | null };

const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

/** Start a menu option: a skill's lesson / review, or the quiz (which has no skill). */
const startOptionTask = (courseId: string, option: TaskOption): Promise<Task> =>
  option.skill ? startTask(courseId, { skill_id: option.skill.id, type: option.type }) : startQuiz(courseId);

/** How many times the page asks for the results once the clock has run out (the server closes the quiz). */
const RESULT_ATTEMPTS = 6;

/**
 * The task handed over by an explicit start (router state), else the already active task. Never starts a task:
 * a reload, browser Back or a bookmark must not create one (D10) — without an open task the page offers a menu.
 * A quiz whose time ran out while the learner was away is closed by the server (it is no longer the open task): its
 * results are shown once, when this browser saw its clock running.
 */
async function loadInitial(courseId: string, provided: Task | null): Promise<Initial> {
  const next = await getNext(courseId);
  let task = provided ?? next.task;
  const quizId = task ? null : openQuizId(courseId);
  if (quizId) {
    try {
      const quiz = await getTask(quizId);
      if (quiz.type === "quiz" && quiz.phase === "done" && quiz.course_id === courseId) task = quiz;
      else forgetOpenQuiz(courseId);
    } catch {
      forgetOpenQuiz(courseId);
    }
  }
  return { task, next };
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
  // When the shown task arrived from the server: a quiz's clock counts its seconds_left from then.
  const [taskAt, setTaskAt] = useState(() => Date.now());
  const [boot, setBoot] = useState<BootState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);
  // The last answer finished the task while its feedback is still on screen (task is replaced on "See results").
  const [finishedId, setFinishedId] = useState<string | null>(null);
  // The plan of a task just before it finished (a halted lesson reports only what was answered).
  const [planBefore, setPlanBefore] = useState<{ id: string; planned: number } | null>(null);
  // The suggestion the answer that finished a task showed in its feedback (the results page doesn't repeat it).
  const [lastSuggestion, setLastSuggestion] = useState<{ id: string; text: string } | null>(null);
  const taskRef = useRef<Task | null>(task);
  const bootPromise = useRef<Promise<Initial> | null>(null);
  const autoStarted = useRef<string | null>(null);
  const course = useResource(`course:${courseId}`, (signal) => getCourse(courseId, signal));
  // Quiz (SPEC §6.7): the clock runs on the server from POST /practice; this counts down from its seconds_left.
  const quizRunning = task !== null && task.type === "quiz" && task.phase === "practice" && task.quiz !== null;
  // The countdown reached zero (or an answer came back 409): results are being fetched; `stuck` = not yet available.
  const [collect, setCollect] = useState<{ id: string; stuck: boolean } | null>(null);
  const collecting = useRef<string | null>(null);

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
        if (taskRef.current === null && initial.task) setTaskAt(Date.now());
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
    setTaskAt(Date.now());
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
      setLastSuggestion(result.suggestion_text ? { id: result.task.id, text: result.suggestion_text } : null);
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
      // A lesson cooling down ("comes back at …") or a due quiz ("Take the quiz first …") is information, not an error.
      if (isCooldownConflict(err) || isQuizFirstConflict(err)) toast.show(err.message, "info");
      else toast.error(err);
      // 409 "Finish or leave '…' first": another task is open after all — pick it up instead (or a stale menu: refresh).
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

  /**
   * "Not now" on an optional retake's intro (SPEC §6.7: not forced): leave it before its clock starts, so it doesn't
   * stay open and hold up the course page. It goes back to the end of the menu.
   */
  const leaveRetake = async () => {
    if (!task) return;
    setBusy("leave");
    try {
      const after = await abandonTask(task.id);
      setBusy(null);
      if (after.outcome !== "abandoned") {
        // It had already ended (e.g. taken in another tab): show its results instead.
        replaceTask(after);
        return;
      }
      toast.show("Retake left for now — it stays on offer at the end of your menu.", "info");
      navigate(`/courses/${encodeURIComponent(courseId)}`, { replace: true });
    } catch (err) {
      setBusy(null);
      // 409: its clock has started meanwhile (another tab) — it can't be left any more; show it as it is now.
      if (isApiError(err, 409)) {
        toast.show(err.message, "info");
        getTask(task.id).then(replaceTask, () => undefined);
      } else toast.error(err);
    }
  };

  /**
   * The quiz clock ran out: the server closes the quiz and sends its results. Ask a few times (its clock has the last
   * word — if it still has time left, the page was early and the quiz goes on with the corrected clock).
   */
  const collectResults = useCallback(
    async (id: string) => {
      if (collecting.current === id) return;
      collecting.current = id;
      for (let attempt = 0; attempt < RESULT_ATTEMPTS; attempt++) {
        await sleep(attempt === 0 ? 400 : 1500);
        if (collecting.current !== id) return;
        // "Time's up" stays on screen from here on (until now the local countdown said so).
        if (attempt === 0) setCollect({ id, stuck: false });
        try {
          const fresh = await getTask(id);
          if (collecting.current !== id) return;
          const stillRunning = fresh.phase === "practice" && (fresh.quiz?.seconds_left ?? 0) > 2;
          if (fresh.phase === "done" || stillRunning) {
            collecting.current = null;
            setCollect(null);
            replaceTask(fresh);
            return;
          }
        } catch {
          // Network hiccup: the next attempt asks again.
        }
      }
      if (collecting.current === id) {
        collecting.current = null;
        setCollect({ id, stuck: true });
      }
    },
    [replaceTask],
  );

  useEffect(() => () => void (collecting.current = null), []);

  const onQuizExpired = useCallback(() => {
    const current = taskRef.current;
    if (current?.type === "quiz") void collectResults(current.id);
  }, [collectResults]);
  const clock = useQuizClock(quizRunning ? task.quiz : null, taskAt, onQuizExpired);
  useDocumentTitle(
    task
      ? task.type === "quiz"
        ? quizRunning && clock.secondsLeft !== null
          ? `${formatCountdown(clock.secondsLeft)} left · ${taskTitle(task)}`
          : taskTitle(task)
        : `${TASK_LABEL[task.type]}: ${plainText(prettyTitle(taskTitle(task)))}`
      : "Study",
  );

  // 409 on a quiz answer: the deadline passed on the server, or this page is behind. The server's task says which.
  const refreshQuiz = useCallback(async () => {
    const current = taskRef.current;
    if (!current) return;
    try {
      const fresh = await getTask(current.id);
      if (fresh.phase === "practice" && fresh.type === "quiz" && (fresh.quiz?.seconds_left ?? 1) <= 0) void collectResults(fresh.id);
      else replaceTask(fresh);
    } catch (err) {
      toast.error(err);
    }
  }, [collectResults, replaceTask, toast]);

  // Remember the running quiz (its results are offered later if its time runs out while away); forget it once shown.
  const quizState = task?.type === "quiz" ? `${task.id}:${task.phase}` : null;
  useEffect(() => {
    if (!quizState) return;
    const [id, phase] = quizState.split(":");
    if (phase === "practice") rememberOpenQuiz(courseId, id);
    else if (phase === "done") forgetOpenQuiz(courseId);
  }, [quizState, courseId]);

  // The clock keeps running on the server while the learner is away: warn before closing / reloading the tab …
  const guardQuiz = quizRunning && !clock.expired;
  useEffect(() => {
    if (!guardQuiz) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [guardQuiz]);
  // … and before leaving for another page of the app (the map, the library).
  const shouldBlock = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation }) => guardQuiz && currentLocation.pathname !== nextLocation.pathname,
    [guardQuiz],
  );
  const blocker = useBlocker(shouldBlock);
  // The quiz ended while "Leave the quiz?" was open: nothing to protect any more.
  useEffect(() => {
    if (blocker.state === "blocked" && !guardQuiz) blocker.proceed();
  }, [blocker, guardQuiz]);

  const goToMap = useCallback(() => navigate(`/courses/${encodeURIComponent(courseId)}`), [navigate, courseId]);
  const onSession = useCallback((next: NextTaskResponse) => setSession(next.session), []);
  const pageRange: [number, number] | null = course.data ? [course.data.start_page, course.data.end_page] : null;
  // A quiz can't be set aside (SPEC §6.7): no "End task".
  const ongoing = task !== null && task.phase !== "done" && finishedId !== task.id && task.type !== "quiz";

  let body;
  if (task === null) {
    if (boot.status === "error") {
      body = <ErrorState error={boot.error} onRetry={retryBoot} title="Couldn't start a task" />;
    } else if (boot.status === "loading") {
      body = <LoadingState label="Preparing your next task…" />;
    } else if (boot.next && boot.next.options.length > 0) {
      const { session: info } = boot.next;
      // A due quiz is the only thing on offer (an older menu may still list other tasks: keep just the quiz then).
      const quizOptions = boot.next.options.filter((o) => o.type === "quiz");
      const options = info.quiz_due && quizOptions.length > 0 ? quizOptions : boot.next.options;
      const top = options[0];
      const quizMin = top.type === "quiz" ? optionMinutes(top) : null;
      // Only the optional retake left on the menu: offered, not forced (SPEC §6.7).
      const retakeTop = isRetakeOption(top);
      body = (
        <div className="study-ready">
          <div className="study-ready__head">
            <p className="kicker">
              {retakeTop
                ? "Optional retake"
                : top.type === "quiz"
                  ? "Quiz time"
                  : info.goal_reached
                    ? "Daily goal reached — anything more is a bonus"
                    : "Ready when you are"}
            </p>
            <h1 className="study-ready__title">Your next step</h1>
            <p className="study-ready__reason">
              Next: {TASK_LABEL[top.type].toLowerCase()}
              {top.skill ? (
                <>
                  {" "}
                  <span className="key">{top.skill.key}</span> <Markdown inline>{prettyTitle(top.skill.title)}</Markdown>
                </>
              ) : null}{" "}
              — {top.reason}
            </p>
            {top.type === "quiz" && (
              <p className="study-ready__quiz muted">
                {retakeTop
                  ? "It's optional: the same skills with fresh questions, and it doesn't move your next quiz."
                  : "Quizzes check what you've learned under time pressure; reviews of anything you miss come straight after."}
              </p>
            )}
            <button
              type="button"
              className={`btn btn--primary btn--lg${top.type === "quiz" ? " btn--quiz" : ""}`}
              onClick={() => void startNext("continue", () => continueLearning(courseId))}
              disabled={busy !== null}
            >
              {busy === "continue" ? <Spinner size={16} label="Starting" /> : <Icon name={top.type === "quiz" ? "stopwatch" : "play"} size={18} />}{" "}
              {top.type === "quiz"
                ? `${retakeTop ? "Retake the quiz" : "Start quiz"}${quizMin ? ` · about ${quizMin} min` : ""}`
                : "Continue learning"}
            </button>
          </div>
          {options.length > 1 && (
            <section className="completion__section">
              <h2 className="lesson__h">Or choose</h2>
              <TaskMenu
                options={options}
                pendingKey={busy && busy !== "continue" ? busy : null}
                disabled={busy !== null}
                onStart={(option: TaskOption) => void startNext(optionKey(option), () => startOptionTask(courseId, option))}
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
  } else if (task.type === "quiz" && task.quiz) {
    const quiz = task.quiz;
    body =
      task.phase === "done" ? (
        <QuizResults
          key={task.id}
          task={task}
          quiz={quiz}
          onSession={onSession}
          onContinue={() => void startNext("continue", () => continueLearning(courseId))}
          onRetake={() => void startNext("retake", () => startQuiz(courseId))}
          pendingKey={busy}
        />
      ) : task.phase === "learn" ? (
        <QuizIntro
          task={task}
          quiz={quiz}
          onStart={() => void beginPractice(task)}
          starting={busy === "practice"}
          onLeave={() => void leaveRetake()}
          leaving={busy === "leave"}
        />
      ) : (
        <QuizView
          task={task}
          timeUp={clock.expired || collect?.id === task.id}
          resultsStuck={collect?.id === task.id && collect.stuck}
          onRetryResults={() => {
            setCollect({ id: task.id, stuck: false });
            void collectResults(task.id);
          }}
          onAdvance={replaceTask}
          onStale={() => void refreshQuiz()}
          onFlagged={onFlagged}
        />
      );
  } else if (task.phase === "done") {
    body = (
      <CompletionScreen
        key={task.id}
        task={task}
        plannedBefore={planBefore?.id === task.id ? planBefore.planned : 0}
        shownSuggestion={lastSuggestion?.id === task.id ? lastSuggestion.text : null}
        // The server's figure for the finished task: includes re-scoring after a flag, the lesson bonus, and 0 for a
        // voided or abandoned task (a local sum of answer XP would go stale).
        xpEarned={task.xp_value}
        onSession={onSession}
        onContinue={() => void startNext("continue", () => continueLearning(courseId))}
        onStart={(option: TaskOption) => void startNext(optionKey(option), () => startOptionTask(courseId, option))}
        pendingKey={busy}
      />
    );
  } else if (task.phase === "learn") {
    body =
      task.type === "lesson" && task.lesson ? (
        <LearnView task={task} lesson={task.lesson} onStart={() => void beginPractice(task)} starting={busy === "practice"} onTask={replaceTask} />
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
    // An open quiz closes the book (SPEC §6.7: no source view) — e.g. a figure's page link in a question.
    <CourseProvider courseId={courseId} pageRange={pageRange} sourceLock={quizContentLock(task)?.source ?? null}>
      <StudyTopBar
        courseId={courseId}
        task={task}
        session={session}
        onEndTask={ongoing ? () => setConfirmEnd(true) : null}
        clock={quizRunning && clock.secondsLeft !== null ? { secondsLeft: clock.secondsLeft, announcement: clock.announcement } : null}
      />
      <main id="main" className="study" data-phase={task?.phase ?? "none"}>
        {body}
      </main>
      <Dialog
        open={blocker.state === "blocked"}
        onClose={() => blocker.reset?.()}
        title="Leave the quiz?"
        size="sm"
        footer={
          <>
            <button type="button" className="btn btn--ghost" onClick={() => blocker.proceed?.()}>
              Leave anyway
            </button>
            <button type="button" className="btn btn--primary" onClick={() => blocker.reset?.()}>
              Stay in the quiz
            </button>
          </>
        }
      >
        <p>
          The clock keeps running while you're away{clock.secondsLeft !== null ? ` — ${formatCountdown(clock.secondsLeft)} left` : ""}. Questions you
          don't get to score 0. <strong>Resume quiz</strong> on the course page brings you back to it.
        </p>
      </Dialog>
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
          {task && keepsAnswersOnLeave(task)
            ? "Your answers so far are kept — the lesson picks up where you left off when you start it again."
            : "Progress inside this task is discarded and it earns no XP. It will be offered again later."}{" "}
          To pause instead, just go back to the map — the task stays open and <strong>Continue learning</strong> resumes it
          (starting a different task there asks first).
        </p>
      </Dialog>
    </CourseProvider>
  );
}
