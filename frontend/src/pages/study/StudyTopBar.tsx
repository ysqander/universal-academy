import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { ProgressBar } from "../../components/ProgressBar";
import { TASK_LABEL } from "../../lib/format";
import type { SessionInfo, Task } from "../../types";

interface StudyTopBarProps {
  courseId: string;
  task: Task | null;
  session: SessionInfo | null;
  onEndTask: (() => void) | null;
}

export function StudyTopBar({ courseId, task, session, onEndTask }: StudyTopBarProps) {
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
                <Icon name={task.type === "lesson" ? "lesson" : "review"} size={13} /> {TASK_LABEL[task.type]}
              </span>
              <span className="study-bar__title">
                <Markdown inline>{task.skill.title}</Markdown>
              </span>
              {task.reason && <span className="study-bar__reason">{task.reason}</span>}
            </>
          ) : (
            <span className="study-bar__title muted">Study session</span>
          )}
        </div>

        {session && (
          <div className="study-bar__xp" title="XP today toward your daily goal">
            <span className="study-bar__xp-value">
              <Icon name="spark" size={13} /> {session.xp_today}/{session.daily_goal}
            </span>
            <ProgressBar value={session.xp_today} max={session.daily_goal} label="XP toward today's goal" size="sm" tone={session.goal_reached ? "learned" : "accent"} />
          </div>
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
