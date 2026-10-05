import { useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { pageRangeLabel, percent, plural, relativeTime, TASK_LABEL } from "../../lib/format";
import type { Course, NextTaskResponse } from "../../types";

interface CourseHeaderProps {
  course: Course;
  next: NextTaskResponse | undefined;
  onContinue: () => void;
  continuing: boolean;
  onEditGoal: () => void;
}

export function CourseHeader({ course, next, onContinue, continuing, onEditGoal }: CourseHeaderProps) {
  const { stats } = course;
  const [expanded, setExpanded] = useState(false);
  const xpToday = next?.session.xp_today ?? stats.xp_today;
  const goal = next?.session.daily_goal ?? stats.daily_goal;
  const goalReached = next?.session.goal_reached ?? xpToday >= goal;
  const active = next?.task ?? null;
  const top = next?.options[0];
  const canContinue = Boolean(active || top);
  const learnedFraction = stats.total_skills ? stats.learned / stats.total_skills : 0;

  return (
    <header className="course-header">
      <div className="course-header__text">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <Link to="/">Library</Link>
          <Icon name="chevronRight" size={14} />
          <span aria-current="page">{course.book_title}</span>
        </nav>
        <p className="kicker">Course · {pageRangeLabel(course.label_start, course.label_end)}</p>
        <h1 className="course-header__title">{course.title}</h1>
        <p className="course-header__book">{course.book_title}</p>
        {course.big_picture ? (
          <div className="course-header__big-picture" data-expanded={expanded || undefined}>
            <Markdown>{course.big_picture}</Markdown>
            {/* Phones clamp the overview so "Continue learning" stays near the top. */}
            <button type="button" className="btn btn--quiet btn--sm course-header__more" onClick={() => setExpanded(!expanded)}>
              {expanded ? "Show less" : "Read the overview"}
            </button>
          </div>
        ) : course.status === "building" ? (
          <p className="course-header__placeholder">The big picture of this chapter appears once it has been mapped.</p>
        ) : null}
      </div>

      <aside className="course-header__panel card" aria-label="Your progress">
        {stats.total_skills > 0 ? (
          <div className="course-header__progress">
            <div className="course-header__progress-line">
              <span className="course-header__big-number">{percent(learnedFraction)}</span>
              <span className="muted">
                {stats.learned} of {plural(stats.total_skills, "skill")} learned
              </span>
            </div>
            <ProgressBar value={stats.learned} max={stats.total_skills} label="Skills learned" tone="learned" size="lg" />
          </div>
        ) : (
          <p className="course-header__no-skills muted">Your progress appears here once the chapter has been mapped into skills.</p>
        )}

        <div className="course-header__chips">
          <span className={`chip${stats.due_reviews ? " chip--due" : ""}`}>
            <Icon name="review" size={14} /> {stats.due_reviews ? `${plural(stats.due_reviews, "review")} due` : "No reviews due"}
          </span>
          <button
            type="button"
            className={`chip chip--button${goalReached ? " chip--goal" : ""}`}
            onClick={onEditGoal}
            title="Change your daily goal"
          >
            <Icon name="spark" size={14} /> {xpToday}/{goal} XP today
          </button>
          {stats.streak_days > 0 && (
            <span className="chip">
              <Icon name="flame" size={14} /> {stats.streak_days}-day streak
            </span>
          )}
          {stats.mastered > 0 && (
            <span className="chip">
              <Icon name="check" size={14} /> {stats.mastered} mastered
            </span>
          )}
        </div>

        <button
          type="button"
          className="btn btn--primary btn--lg btn--block course-header__continue"
          onClick={onContinue}
          disabled={!canContinue || continuing}
        >
          {continuing ? <Spinner size={18} label="Starting" /> : <Icon name="play" size={18} />}
          {active ? `Resume ${TASK_LABEL[active.type].toLowerCase()}` : "Continue learning"}
        </button>
        <p className="course-header__next">
          {active ? (
            <>
              <span className="key">{active.skill.key}</span> <Markdown inline>{active.skill.title}</Markdown>
            </>
          ) : top ? (
            top.reason
          ) : next ? (
            next.message || "Nothing to do right now."
          ) : (
            "Finding your next step…"
          )}
        </p>
        {next && !active && !top && stats.learned > 0 && stats.next_review_at && <p className="course-header__next muted">Next review {relativeTime(stats.next_review_at)}.</p>}
      </aside>
    </header>
  );
}
