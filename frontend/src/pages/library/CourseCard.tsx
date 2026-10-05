import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { useStartTask } from "../../hooks/useStartTask";
import { pageRangeLabel, plural } from "../../lib/format";
import type { CourseSummary } from "../../types";

export function CourseCard({ course }: { course: CourseSummary }) {
  const { stats } = course;
  const { continueNext, pending } = useStartTask(course.id);
  const href = `/courses/${encodeURIComponent(course.id)}`;
  const building = course.status === "building";
  const failed = course.status === "failed";
  const canStudy = !failed && (stats.lessons_ready > 0 || stats.learned > 0);

  return (
    <article className="course-card card" data-status={course.status}>
      <div className="course-card__top">
        <span className="course-card__state">
          {building ? (
            <>
              <Spinner size={12} label="Building" /> Building
            </>
          ) : failed ? (
            <>
              <Icon name="warning" size={14} /> Build stopped
            </>
          ) : stats.learned === stats.total_skills && stats.total_skills > 0 ? (
            <>
              <Icon name="check" size={14} /> Chapter learned
            </>
          ) : (
            <>
              <Icon name="graph" size={14} /> Ready
            </>
          )}
        </span>
        <span className="course-card__pages">{pageRangeLabel(course.label_start, course.label_end)}</span>
      </div>

      <h3 className="course-card__title">
        <Link to={href}>{course.title}</Link>
      </h3>
      <p className="course-card__book">{course.book_title}</p>

      {stats.total_skills > 0 ? (
        <div className="course-card__progress">
          <ProgressBar value={stats.learned} max={stats.total_skills} label="Skills learned" tone="learned" />
          <span className="course-card__progress-label">
            {stats.learned} of {plural(stats.total_skills, "skill")} learned
          </span>
        </div>
      ) : (
        <p className="course-card__hint muted">
          {building ? "Mapping the chapter into skills…" : failed ? "No skills were created." : "No skills yet."}
        </p>
      )}

      <div className="course-card__chips">
        {stats.due_reviews > 0 && (
          <span className="chip chip--due">
            <Icon name="review" size={14} /> {plural(stats.due_reviews, "review")} due
          </span>
        )}
        <span className="chip" title="Experience points today (1 XP ≈ 1 focused minute)">
          <Icon name="spark" size={14} /> {stats.xp_today}/{stats.daily_goal} XP today
        </span>
        {stats.streak_days > 1 && (
          <span className="chip">
            <Icon name="flame" size={14} /> {stats.streak_days}-day streak
          </span>
        )}
        {building && stats.total_skills > 0 && (
          <span className="chip">
            {stats.lessons_ready}/{stats.total_skills} lessons ready
          </span>
        )}
      </div>

      <div className="course-card__actions">
        {canStudy ? (
          <button type="button" className="btn btn--primary" onClick={() => void continueNext()} disabled={pending !== null}>
            {pending ? <Spinner size={14} label="Starting" /> : <Icon name="play" size={14} />}
            Continue
          </button>
        ) : null}
        <Link to={href} className="btn">
          {building || failed ? "View progress" : "Open map"}
        </Link>
      </div>
    </article>
  );
}
