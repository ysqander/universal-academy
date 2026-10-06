import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getNext } from "../../api";
import { Icon, type IconName } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { TaskMenu } from "../../components/TaskMenu";
import { useResource } from "../../hooks/useResource";
import { plural } from "../../lib/format";
import { lessonEndNote, percentLabel, scoreView } from "../../lib/score";
import { prettyTitle } from "../../lib/markdown";
import type { NextTaskResponse, Task, TaskOption } from "../../types";
import { ScoreMeter } from "./ScoreMeter";

interface CompletionScreenProps {
  task: Task;
  /** Questions planned just before the last answer (a halted lesson reports only what was answered). */
  plannedBefore?: number;
  xpEarned: number;
  onSession: (next: NextTaskResponse) => void;
  onContinue: () => void;
  onStart: (option: TaskOption) => void;
  pendingKey: string | null;
}

/** Counts up to `target` once, quickly (respects reduced motion). */
function useCountUp(target: number, durationMs = 900): number {
  const [animate] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [value, setValue] = useState(animate ? 0 : target);
  useEffect(() => {
    if (!animate || target <= 0) return;
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      setValue(Math.round(target * (1 - (1 - t) ** 3)));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [animate, target, durationMs]);
  return animate ? value : target;
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

export function CompletionScreen({ task, plannedBefore = 0, xpEarned, onSession, onContinue, onStart, pendingKey }: CompletionScreenProps) {
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
          {task.type === "lesson" ? "Lesson" : "Review"} · <span className="key">{task.skill.key}</span>
        </p>
        <h1 className="completion__title">{head.title}</h1>
        <p className="completion__skill">
          <Markdown inline>{prettyTitle(task.skill.title)}</Markdown>
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

      {goalReached && (
        <div className="completion__done callout callout--success">
          <Icon name="check" size={20} />
          <div>
            <strong>Done for today.</strong> You reached your daily goal — this is a clean place to stop. Spaced reviews will be
            waiting when you come back.
          </div>
        </div>
      )}

      <div className="completion__actions">
        {goalReached ? (
          <>
            <Link to={courseHref} className="btn btn--primary btn--lg">
              Finish for today
            </Link>
            {(options.length > 0 || next.data?.task) && (
              <button type="button" className="btn btn--lg" onClick={onContinue} disabled={pendingKey !== null}>
                {pendingKey === "continue" && <Spinner size={16} label="Starting" />} Keep going
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

      {!goalReached && options.length > 1 && (
        <section className="completion__section">
          <h2 className="lesson__h">Or choose</h2>
          <TaskMenu options={options} onStart={onStart} pendingKey={pendingKey && pendingKey !== "continue" ? pendingKey : null} />
        </section>
      )}
    </div>
  );
}

