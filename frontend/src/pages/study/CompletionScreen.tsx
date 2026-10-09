import { useEffect } from "react";
import { Link } from "react-router-dom";
import { getNext } from "../../api";
import { Icon, type IconName } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { TaskMenu } from "../../components/TaskMenu";
import { useCountUp } from "../../hooks/useCountUp";
import { useResource } from "../../hooks/useResource";
import { plural } from "../../lib/format";
import { completionSuggestion, lessonEndNote, percentLabel, scoreView } from "../../lib/score";
import { prettyTitle } from "../../lib/markdown";
import { isRetakeOption, optionMinutes, taskTitle } from "../../lib/quiz";
import type { NextTaskResponse, Task, TaskOption } from "../../types";
import { ScoreMeter } from "./ScoreMeter";

interface CompletionScreenProps {
  task: Task;
  /** Questions planned just before the last answer (a halted lesson reports only what was answered). */
  plannedBefore?: number;
  /** The suggestion the last answer's feedback already showed (not said twice). */
  shownSuggestion?: string | null;
  xpEarned: number;
  onSession: (next: NextTaskResponse) => void;
  onContinue: () => void;
  onStart: (option: TaskOption) => void;
  pendingKey: string | null;
}

function headline(task: Task): { icon: IconName; title: string; tone: "success" | "pause" | "neutral" } {
  if (task.outcome === "passed") return { icon: "check", title: task.type === "lesson" ? "Lesson complete" : "Review complete", tone: "success" };
  if (task.outcome === "failed") {
    return task.type === "lesson"
      ? { icon: "clock", title: "Let's come back to this one", tone: "pause" }
      : { icon: "review", title: "This one needs another look", tone: "pause" };
  }
  return { icon: "arrowRight", title: "Task set aside", tone: "neutral" };
}

export function CompletionScreen({ task, plannedBefore = 0, shownSuggestion = null, xpEarned, onSession, onContinue, onStart, pendingKey }: CompletionScreenProps) {
  const next = useResource(`after:${task.id}`, (signal) => getNext(task.course_id, signal));
  const xp = useCountUp(xpEarned);
  const head = headline(task);
  const lesson = task.lesson;
  const unlocked = lesson?.unlocks.filter((s) => s.status !== "locked") ?? [];
  const session = next.data?.session;
  const goalReached = session?.goal_reached ?? false;
  const options = next.data?.options ?? [];
  const courseHref = `/courses/${encodeURIComponent(task.course_id)}`;
  const { progress } = task;
  const score = scoreView(progress, task.type === "lesson" ? { outcome: task.outcome, plannedBefore } : null);
  const end = task.type === "lesson" && task.outcome === "failed" ? lessonEndNote(progress, plannedBefore, task.cooldown_until) : null;
  const showScore = task.type === "lesson" && score.hasScore && (task.outcome === "passed" || task.outcome === "failed");
  // A failed lesson's next step (e.g. prerequisites made due): its only place when no answer ended the attempt.
  const suggestion = task.outcome === "failed" ? completionSuggestion(progress, shownSuggestion) : null;
  // A quiz became due (SPEC §6.7): it is the only option now, and "Next task" starts it. The optional retake is first
  // only when nothing else is left: offered, not forced.
  const quizOption = !next.data?.task && options[0]?.type === "quiz" ? options[0] : null;
  const retakeNext = quizOption !== null && isRetakeOption(quizOption);
  const quizNext = quizOption !== null && !retakeNext;
  const quizMin = quizOption ? optionMinutes(quizOption) : null;
  const retakeLabel = `Retake the quiz${quizMin ? ` · about ${quizMin} min` : ""}`;

  useEffect(() => {
    if (next.data) onSession(next.data);
  }, [next.data, onSession]);

  return (
    <div className="completion" data-tone={head.tone}>
      <div className="completion__hero">
        <span className="completion__badge">
          <Icon name={head.icon} size={30} strokeWidth={2} />
        </span>
        <p className="kicker">
          {task.type === "lesson" ? "Lesson" : "Review"}
          {task.skill && (
            <>
              {" "}
              · <span className="key">{task.skill.key}</span>
            </>
          )}
        </p>
        <h1 className="completion__title">{head.title}</h1>
        <p className="completion__skill">
          <Markdown inline>{prettyTitle(taskTitle(task))}</Markdown>
        </p>
        {xpEarned > 0 ? (
          <p className="completion__xp" aria-label={`${xpEarned} XP earned`}>
            <span aria-hidden="true">+{xp}</span> <span className="completion__xp-unit">XP</span>
          </p>
        ) : null}
        {task.type === "review" && progress.answered > 0 && (
          <p className="completion__note">
            {/* With half points and multistep questions, the score says more than a count of right answers. */}
            {score.hasScore ? `Score ${percentLabel(score.fraction)}.` : `${progress.correct} of ${progress.answered} answered correctly.`}
          </p>
        )}
      </div>

      {showScore && (
        <section className="completion__score" data-outcome={task.outcome}>
          <ScoreMeter
            view={score}
            size="lg"
            aside={task.outcome === "passed" ? "Passed" : score.halted ? score.remainingLabel : "Below the pass mark"}
          />
          {end && (
            <p className="completion__note completion__score-note">
              {end.text} No XP this time — that's normal for a hard step, and the time in between helps it settle.
            </p>
          )}
        </section>
      )}
      {!showScore && end && <p className="completion__note completion__score-note">{end.text}</p>}

      {suggestion && (
        <div className="completion__suggestion callout">
          <Icon name="bulb" size={20} />
          <div>{suggestion}</div>
        </div>
      )}

      {unlocked.length > 0 && task.outcome === "passed" && (
        <section className="completion__section">
          <h2 className="lesson__h">Unlocked</h2>
          <ul className="completion__unlocked">
            {unlocked.map((s) => (
              <li key={s.id}>
                <Icon name="unlock" size={16} />
                <span className="key">{s.key}</span>
                <Markdown inline>{prettyTitle(s.title)}</Markdown>
              </li>
            ))}
          </ul>
        </section>
      )}

      {lesson?.connection && task.outcome === "passed" && (
        <section className="completion__section">
          <h2 className="lesson__h">Where this leads</h2>
          <Markdown>{lesson.connection}</Markdown>
        </section>
      )}

      <section className="completion__section completion__session">
        {session ? (
          <>
            <div className="completion__goal-line">
              <span>
                <strong>{session.xp_today}</strong> of {session.daily_goal} XP today
              </span>
              {session.due_reviews > 0 && <span className="muted">{plural(session.due_reviews, "review")} due</span>}
            </div>
            <ProgressBar value={session.xp_today} max={session.daily_goal} label="Daily goal" tone={goalReached ? "learned" : "accent"} size="lg" />
          </>
        ) : (
          <div className="skeleton completion__session-skeleton" />
        )}
      </section>

      {goalReached && !quizNext && (
        <div className="completion__done callout callout--success">
          <Icon name="check" size={20} />
          <div>
            <strong>Done for today.</strong> You reached your daily goal — this is a clean place to stop. Spaced reviews will be
            waiting when you come back.
          </div>
        </div>
      )}

      {retakeNext && !goalReached && (
        <div className="completion__quiz callout">
          <Icon name="stopwatch" size={20} />
          <div>
            <strong>Optional retake.</strong> Nothing else is waiting right now. You can retake your last quiz — the same skills
            with fresh questions, timed. It's up to you, and it doesn't move your next quiz.
          </div>
        </div>
      )}

      {quizNext && (
        <div className="completion__quiz callout">
          <Icon name="stopwatch" size={20} />
          <div>
            <strong>Quiz time.</strong> You've earned enough XP for a timed quiz on what you've learned lately — it comes before
            anything else. Quizzes check what you've learned under time pressure; reviews of anything you miss come straight after.
          </div>
        </div>
      )}

      <div className="completion__actions">
        {quizNext ? (
          <>
            <button type="button" className="btn btn--primary btn--quiz btn--lg" onClick={onContinue} disabled={pendingKey !== null}>
              {pendingKey === "continue" ? <Spinner size={16} label="Starting" /> : <Icon name="stopwatch" size={18} />} Start quiz
              {quizMin ? ` · about ${quizMin} min` : ""}
            </button>
            <Link to={courseHref} className="btn btn--lg">
              <Icon name="graph" size={18} /> Back to the map
            </Link>
          </>
        ) : retakeNext && !goalReached ? (
          <>
            <button type="button" className="btn btn--primary btn--quiz btn--lg" onClick={onContinue} disabled={pendingKey !== null}>
              {pendingKey === "continue" ? <Spinner size={16} label="Starting" /> : <Icon name="stopwatch" size={18} />} {retakeLabel}
            </button>
            <Link to={courseHref} className="btn btn--lg">
              <Icon name="graph" size={18} /> Back to the map
            </Link>
          </>
        ) : goalReached ? (
          <>
            <Link to={courseHref} className="btn btn--primary btn--lg">
              Finish for today
            </Link>
            {(options.length > 0 || next.data?.task) && (
              <button type="button" className="btn btn--lg" onClick={onContinue} disabled={pendingKey !== null}>
                {pendingKey === "continue" && <Spinner size={16} label="Starting" />} {retakeNext ? retakeLabel : "Keep going"}
              </button>
            )}
          </>
        ) : (
          <>
            {(options.length > 0 || next.data?.task) && (
              <button type="button" className="btn btn--primary btn--lg" onClick={onContinue} disabled={pendingKey !== null}>
                {pendingKey === "continue" ? <Spinner size={16} label="Starting" /> : <Icon name="play" size={18} />} Next task
              </button>
            )}
            <Link to={courseHref} className="btn btn--lg">
              <Icon name="graph" size={18} /> Back to the map
            </Link>
          </>
        )}
      </div>

      {next.data && options.length === 0 && !next.data.task && next.data.message && <p className="completion__message muted">{next.data.message}</p>}

      {!goalReached && !quizNext && options.length > 1 && (
        <section className="completion__section">
          <h2 className="lesson__h">Or choose</h2>
          <TaskMenu options={options} onStart={onStart} pendingKey={pendingKey && pendingKey !== "continue" ? pendingKey : null} />
        </section>
      )}
    </div>
  );
}

