import { useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { pageRangeLabel, percent, plural, relativeTime, TASK_LABEL } from "../../lib/format";
import { prettyTitle } from "../../lib/markdown";
import { isRetakeOption, optionMinutes, quizChipLabel, quizMinutes, taskTitle, type QuizGate } from "../../lib/quiz";
import type { Course, NextTaskResponse } from "../../types";

interface CourseHeaderProps {
  course: Course;
  next: NextTaskResponse | undefined;
  /** Shown under "Continue learning" when there is nothing to start (no active task, empty menu). */
  idleMessage: string;
  onContinue: () => void;
  continuing: boolean;
  /** Study settings (daily goal, quiz time). */
  onEditSettings: () => void;
  /** Quiz state (SPEC §6.7): the chip, and "Start quiz" when one is due. */
  quiz: QuizGate;
}

export function CourseHeader({ course, next, idleMessage, onContinue, continuing, onEditSettings, quiz }: CourseHeaderProps) {
  const { stats } = course;
  const [expanded, setExpanded] = useState(false);
  // The daily goal counts XP across all courses: only the session total (from /next) is shown against it, never the
  // per-course stats.xp_today (which would flip from "0/30" to "8/30" once /next arrives).
  const session = next?.session;
  const goal = session?.daily_goal ?? stats.daily_goal;
  const goalReached = session?.goal_reached ?? false;
  const active = next?.task ?? null;
  const top = next?.options[0];
  const canContinue = Boolean(active || top);
  const learnedFraction = stats.total_skills ? stats.learned / stats.total_skills : 0;
  const quizChip = quizChipLabel(quiz);
  // /continue resumes an open task first, then starts a due quiz (the menu then offers only the quiz) — or the optional
  // retake when it is the only thing left on the menu (not forced: it is worded as an offer).
  const quizNext = !active && top?.type === "quiz";
  const retakeNext = quizNext && top !== undefined && isRetakeOption(top);
  const activeRetake = active?.type === "quiz" && active.quiz?.kind === "retake";
  const quizMin = active?.type === "quiz" && active.quiz ? quizMinutes(active.quiz) : quizNext && top ? optionMinutes(top) : null;
  const continueLabel = active
    ? activeRetake
      ? "Resume retake"
      : `Resume ${TASK_LABEL[active.type].toLowerCase()}`
    : quizNext
      ? `${retakeNext ? "Retake the quiz" : "Start quiz"}${quizMin ? ` · about ${quizMin} min` : ""}`
      : "Continue learning";

  return (
    <header className="course-header">
      <div className="course-header__text">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <Link to="/">Library</Link>
          <Icon name="chevronRight" size={14} />
          <span aria-current="page">{course.book_title}</span>
        </nav>
        <p className="kicker">Course · {pageRangeLabel(course.label_start, course.label_end)}</p>
        <h1 className="course-header__title">
          <Markdown inline>{prettyTitle(course.title)}</Markdown>
        </h1>
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
          {quizChip && (
            <span
              className={`chip chip--quiz${quiz.due ? " chip--quiz-due" : ""}`}
              title={
                quiz.due
                  ? "A timed quiz comes before new lessons and reviews"
                  : quiz.optionalRetake
                    ? "The optional retake is open but not started — you can start something else instead"
                    : "A timed quiz on what you've learned comes about every 150 XP (with at least 5 new skills)"
              }
            >
              <Icon name="stopwatch" size={14} /> {quizChip}
            </span>
          )}
          <button
            type="button"
            className={`chip chip--button${goalReached ? " chip--goal" : ""}`}
            onClick={onEditSettings}
            title="Change your daily goal"
          >
            <Icon name="spark" size={14} /> {session ? session.xp_today : "–"}/{goal} XP today
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
          <button type="button" className="chip chip--button chip--icon" onClick={onEditSettings} aria-label="Study settings" title="Study settings: daily goal, quiz time">
            <Icon name="sliders" size={14} />
          </button>
        </div>

        <button
          type="button"
          className={`btn btn--primary btn--lg btn--block course-header__continue${quizNext || active?.type === "quiz" ? " btn--quiz" : ""}`}
          onClick={onContinue}
          disabled={!canContinue || continuing}
        >
          {continuing ? <Spinner size={18} label="Starting" /> : <Icon name={quizNext || active?.type === "quiz" ? "stopwatch" : "play"} size={18} />}
          {continueLabel}
        </button>
        <p className="course-header__next">
          {active?.type === "quiz" ? (
            active.quiz?.started_at ? (
              `The ${activeRetake ? "retake" : "quiz"} clock is running — finish it to see your results.`
            ) : (
              `${activeRetake ? "Optional retake. " : ""}Timed${quizMin ? `, about ${quizMin} min` : ""} — the clock starts when you begin.`
            )
          ) : active ? (
            <>
              {active.skill && <span className="key">{active.skill.key}</span>} <Markdown inline>{prettyTitle(taskTitle(active))}</Markdown>
              {quiz.due && <span className="course-header__then"> Then a quiz.</span>}
            </>
          ) : top ? (
            top.reason
          ) : next ? (
            idleMessage
          ) : (
            "Finding your next step…"
          )}
        </p>
        {next && !active && !top && stats.learned > 0 && stats.next_review_at && <p className="course-header__next muted">Next review {relativeTime(stats.next_review_at)}.</p>}
      </aside>
    </header>
  );
}
