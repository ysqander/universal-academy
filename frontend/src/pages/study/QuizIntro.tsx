import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { percentLabel } from "../../lib/score";
import { prettyTitle } from "../../lib/markdown";
import { QUIZ_PASS_MARK, durationLabel, quizMinutes } from "../../lib/quiz";
import type { QuizInfo, Task } from "../../types";

interface QuizIntroProps {
  task: Task;
  quiz: QuizInfo;
  onStart: () => void;
  starting: boolean;
  /** An optional retake only: leave it before its clock starts ("Not now"); it stays on offer in the menu. */
  onLeave: () => void;
  leaving: boolean;
}

/** Before the clock starts (SPEC §6.7): what the quiz is, how long, and the rules — then "Start the clock". */
export function QuizIntro({ task, quiz, onStart, starting, onLeave, leaving }: QuizIntroProps) {
  const retake = quiz.kind === "retake";
  const minutes = quizMinutes(quiz);
  const count = quiz.question_count || quiz.skills.length;
  const maxXp = task.xp_value > 0 ? task.xp_value : minutes;

  return (
    <div className="quiz-intro">
      <div className="quiz-intro__hero">
        <span className="quiz-intro__badge" aria-hidden="true">
          <Icon name="stopwatch" size={30} strokeWidth={1.9} />
        </span>
        <p className="kicker">{retake ? "Optional retake" : "Quiz"}</p>
        <h1 className="quiz-intro__title">{retake ? "Retake the quiz" : "Time for a quiz"}</h1>
        <p className="quiz-intro__lead">
          {retake
            ? `The same skills with fresh questions. It's optional and doesn't move your next quiz.`
            : `${count} questions on what you've learned lately, mixed together. It checks what you can do on your own, against the clock.`}
        </p>
      </div>

      <dl className="quiz-intro__facts">
        <div>
          <dt>Questions</dt>
          <dd>{count}</dd>
        </div>
        <div>
          <dt>Time</dt>
          <dd>{minutes} min</dd>
        </div>
        <div>
          <dt>Pass mark</dt>
          <dd>{percentLabel(QUIZ_PASS_MARK)}</dd>
        </div>
        <div>
          <dt>XP</dt>
          <dd>up to {maxXp}</dd>
        </div>
      </dl>

      <section className="quiz-intro__rules card" aria-labelledby="quiz-rules">
        <h2 id="quiz-rules" className="lesson__h">
          How it works
        </h2>
        <ul className="quiz-rules">
          <li>
            <Icon name="clock" size={18} />
            <span>
              <strong>Timed.</strong> You have {durationLabel(quiz.time_limit_seconds)} for all {count} questions. The clock starts
              when you press <em>Start the clock</em> and runs on the server — <strong>it keeps running if you leave this page.</strong>
            </span>
          </li>
          <li>
            <Icon name="lock" size={18} />
            <span>
              <strong>No help.</strong> No hints, no solutions, no easier steps, no lesson or book pages.
            </span>
          </li>
          <li>
            <Icon name="arrowRight" size={18} />
            <span>
              <strong>One question at a time.</strong> Submit an answer or <em>Pass</em> (it scores 0). You can't go back.
            </span>
          </li>
          <li>
            <Icon name="eye" size={18} />
            <span>
              <strong>Results at the end.</strong> You'll see what was right — with the correct answers and solutions — once the quiz is
              over.
            </span>
          </li>
          <li>
            <Icon name="review" size={18} />
            <span>
              <strong>Afterwards.</strong> Anything you miss gets a short review straight away.
              {retake ? "" : ` Below ${percentLabel(QUIZ_PASS_MARK)}, an optional retake opens once those reviews are done.`}
            </span>
          </li>
        </ul>
        <p className="quiz-intro__setting muted">
          Need more time to read or type math? Choose <strong>Quiz time</strong> in the study settings on the course page.
        </p>
      </section>

      {quiz.skills.length > 0 && (
        <details className="disclosure quiz-intro__skills">
          <summary>Skills in this quiz ({quiz.skills.length})</summary>
          <div className="disclosure__body">
            <ul className="quiz-skill-list">
              {quiz.skills.map((skill) => (
                <li key={skill.id}>
                  <span className="key">{skill.key}</span>
                  <Markdown inline>{prettyTitle(skill.title)}</Markdown>
                </li>
              ))}
            </ul>
          </div>
        </details>
      )}

      <div className="quiz-intro__start">
        <button type="button" className="btn btn--primary btn--quiz btn--lg" onClick={onStart} disabled={starting || leaving}>
          {starting ? <Spinner size={16} label="Starting the clock" /> : <Icon name="stopwatch" size={18} />} Start the clock
        </button>
        {retake ? (
          // Leaves the retake (it isn't forced): it goes back to the end of the menu, so it can be taken later.
          <button type="button" className="btn btn--ghost btn--lg" onClick={onLeave} disabled={starting || leaving}>
            {leaving && <Spinner size={16} label="Leaving" />} Not now
          </button>
        ) : (
          <p className="quiz-intro__when muted">
            {minutes} {minutes === 1 ? "minute" : "minutes"}, starting the moment you press it.
          </p>
        )}
      </div>
    </div>
  );
}
