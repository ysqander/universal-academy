import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { TASK_ICON } from "../../components/taskIcon";
import { TASK_LABEL } from "../../lib/format";
import { prettyTitle } from "../../lib/markdown";
import { clockTone, formatCountdown, taskTitle } from "../../lib/quiz";
import type { SessionInfo, Task } from "../../types";

interface StudyTopBarProps {
  courseId: string;
  task: Task | null;
  session: SessionInfo | null;
  onEndTask: (() => void) | null;
  /** A quick look at the task's lesson (an overlay; the question stays as it is). Null: not offered (a quiz). */
  onOpenLesson?: (() => void) | null;
  /** In a review, opening the lesson counts as a hint: say so before the click. */
  lessonCostsHint?: boolean;
  lessonPending?: boolean;
  /** A running quiz's countdown (seconds left), and what to announce when it crosses a minute / zero. */
  clock?: { secondsLeft: number; announcement: string | null } | null;
}

export function StudyTopBar({ courseId, task, session, onEndTask, onOpenLesson = null, lessonCostsHint = false, lessonPending = false, clock = null }: StudyTopBarProps) {
  const tone = clock ? clockTone(clock.secondsLeft) : null;
  return (
    <header className="study-bar">
      <div className="study-bar__inner">
        <Link to={`/courses/${encodeURIComponent(courseId)}`} className="btn btn--ghost btn--sm study-bar__back" aria-label="Back to the course map">
          <Icon name="arrowLeft" size={16} />
          <span className="study-bar__back-label">Map</span>
        </Link>

        <div className="study-bar__task">
          {task ? (
            <>
              <span className="study-bar__type" data-type={task.type}>
                <Icon name={TASK_ICON[task.type]} size={13} /> {TASK_LABEL[task.type]}
              </span>
              <span className="study-bar__title">
                <Markdown inline>{prettyTitle(taskTitle(task))}</Markdown>
              </span>
              {task.reason && <span className="study-bar__reason">{task.reason}</span>}
            </>
          ) : (
            <span className="study-bar__title muted">Study session</span>
          )}
        </div>

        {clock && (
          <div className="quiz-clock" data-tone={tone} role="timer" aria-label={`Time left ${formatCountdown(clock.secondsLeft)}`}>
            <Icon name="stopwatch" size={16} strokeWidth={2} />
            <span className="quiz-clock__time" aria-hidden="true">
              {formatCountdown(clock.secondsLeft)}
            </span>
            <span className="visually-hidden" role="status">
              {clock.announcement ?? ""}
            </span>
          </div>
        )}

        {session && (
          <div className="study-bar__xp" title="XP today toward your daily goal">
            <span className="study-bar__xp-value">
              <Icon name="spark" size={13} /> {session.xp_today}/{session.daily_goal}
            </span>
            <ProgressBar value={session.xp_today} max={session.daily_goal} label="XP toward today's goal" size="sm" tone={session.goal_reached ? "learned" : "accent"} />
          </div>
        )}

        {onOpenLesson && (
          <button
            type="button"
            className="btn btn--ghost btn--sm study-bar__lesson"
            onClick={onOpenLesson}
            disabled={lessonPending}
            aria-label="Look at the lesson"
            aria-keyshortcuts="L"
            title={lessonCostsHint ? "Look at the lesson (L) — counts as a hint on this question: a right answer earns ½ point" : "Look at the lesson (L)"}
          >
            {lessonPending ? <Spinner size={14} label="Opening the lesson" /> : <Icon name="book" size={16} />}
            <span className="study-bar__back-label">Lesson</span>
          </button>
        )}

        {onEndTask && (
          <button type="button" className="btn btn--ghost btn--sm study-bar__end" onClick={onEndTask}>
            End task
          </button>
        )}
      </div>
    </header>
  );
}
