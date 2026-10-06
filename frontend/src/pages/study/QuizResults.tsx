import { useEffect } from "react";
import { Link } from "react-router-dom";
import { getNext } from "../../api";
import { TfKeyList, TrueFalseSetInput } from "../../components/answers/AnswerInputs";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { ProgressBar } from "../../components/ProgressBar";
import { Spinner } from "../../components/Spinner";
import { Tex } from "../../components/Tex";
import { useCountUp } from "../../hooks/useCountUp";
import { useResource } from "../../hooks/useResource";
import { plural } from "../../lib/format";
import { prettyTitle } from "../../lib/markdown";
import { asciiToLatex } from "../../lib/mathInput";
import { QUIZ_MARK_GLYPH, QUIZ_MARK_LABEL, noAnswerLabel, quizMinutes, quizScoreView, quizSummary, resultMark } from "../../lib/quiz";
import { decodeTfResponse, parseTfKey, tfRowResults, type TfLetter } from "../../lib/trueFalseSet";
import type { NextTaskResponse, QuizInfo, QuizQuestionResult, Task } from "../../types";
import { ScoreMeter } from "./ScoreMeter";

interface QuizResultsProps {
  task: Task;
  quiz: QuizInfo;
  onSession: (next: NextTaskResponse) => void;
  /** Next task: the reviews of what was missed come first. */
  onContinue: () => void;
  /** Start the optional retake. */
  onRetake: () => void;
  pendingKey: string | null;
}

/** A typed answer as the learner wrote it, shown as math when it reads as math. */
function TypedAnswer({ text }: { text: string }) {
  const tex = asciiToLatex(text);
  return tex ? <Tex tex={tex} /> : <span className="quiz-result__raw">{text}</span>;
}

function noop() {}

function TfResult({ result }: { result: QuizQuestionResult }) {
  const count = result.choices.length;
  // The server shows the response like the key ("1 False · 2 True · 3 True"); a plain "FTT" reads too.
  const parsed = parseTfKey(result.response, count);
  const response = parsed ? parsed.join("") : result.response;
  const marks = decodeTfResponse(response, count);
  const right = result.score >= 1 - 1e-9;
  const fullMarks = marks.every((m) => m !== null) ? (marks as TfLetter[]) : null;
  // The key from the answer text; a fully right response is the key itself.
  const key = parseTfKey(result.correct_answer, count) ?? (right && result.answered ? fullMarks : null);
  if (!result.answered) {
    return (
      <>
        <TrueFalseSetInput statements={result.choices} marks={marks} onChange={noop} onSubmit={noop} disabled />
        {key ? (
          <div className="quiz-result__key">
            <span className="quiz-result__label">Correct answer</span> <TfKeyList answerKey={key} />
          </div>
        ) : (
          <p className="quiz-result__line">
            <span className="quiz-result__label">Correct answer</span> <Markdown inline>{result.correct_answer}</Markdown>
          </p>
        )}
      </>
    );
  }
  return (
    <TrueFalseSetInput
      statements={result.choices}
      marks={marks}
      onChange={noop}
      onSubmit={noop}
      disabled
      results={tfRowResults(response, key, right, count)}
      answerKey={key}
    />
  );
}

function ResultItem({ result, index }: { result: QuizQuestionResult; index: number }) {
  const mark = resultMark(result);
  const typed = result.kind === "numeric" || result.kind === "expression";
  return (
    <li className="quiz-result" data-mark={mark}>
      <div className="quiz-result__head">
        <span className="quiz-result__mark" aria-hidden="true">
          {QUIZ_MARK_GLYPH[mark]}
        </span>
        <span className="quiz-result__n">Question {index + 1}</span>
        <span className="quiz-result__verdict">{mark === "passed" ? noAnswerLabel(result) : QUIZ_MARK_LABEL[mark]}</span>
        <span className="quiz-result__skill">
          <span className="key">{result.skill.key}</span> <Markdown inline>{prettyTitle(result.skill.title)}</Markdown>
        </span>
      </div>
      <div className="quiz-result__prompt">
        <Markdown>{result.prompt}</Markdown>
      </div>
      {result.kind === "true_false_set" ? (
        <TfResult result={result} />
      ) : (
        <div className="quiz-result__answers">
          <p className="quiz-result__line" data-tone={mark}>
            <span className="quiz-result__label">Your answer</span>{" "}
            {result.answered && result.response ? (
              typed ? (
                <TypedAnswer text={result.response} />
              ) : (
                <Markdown inline>{result.response}</Markdown>
              )
            ) : (
              <span className="muted">{(result.seconds || 0) > 0 ? "none — passed, or the time ran out" : "none — the time ran out first"}</span>
            )}
          </p>
          {mark !== "correct" && result.correct_answer && (
            <p className="quiz-result__line">
              <span className="quiz-result__label">Correct answer</span> <Markdown inline>{result.correct_answer}</Markdown>
            </p>
          )}
        </div>
      )}
      {result.solution && (
        <details className="disclosure quiz-result__solution">
          <summary>Solution</summary>
          <div className="disclosure__body">
            <Markdown>{result.solution}</Markdown>
          </div>
        </details>
      )}
    </li>
  );
}

/** After the quiz (SPEC §6.7): score vs 80 %, XP, every question with the right answer and its solution, what gets
 * reviewed next, and whether the optional retake is open. */
export function QuizResults({ task, quiz, onSession, onContinue, onRetake, pendingKey }: QuizResultsProps) {
  const next = useResource(`after:${task.id}`, (signal) => getNext(task.course_id, signal));
  const summary = quizSummary(quiz.results, task.progress.score);
  const view = quizScoreView(summary);
  const xp = useCountUp(task.xp_value);
  const retake = quiz.kind === "retake";
  const missed = quiz.missed_skills;
  const session = next.data?.session;
  const options = next.data?.options ?? [];
  const courseHref = `/courses/${encodeURIComponent(task.course_id)}`;
  const reviewNext = missed.length > 0 && options[0]?.type === "review";

  useEffect(() => {
    if (next.data) onSession(next.data);
  }, [next.data, onSession]);

  return (
    <div className="completion quiz-results" data-tone={summary.pass ? "success" : "pause"}>
      <div className="completion__hero">
        <span className="completion__badge">
          <Icon name={summary.pass ? "check" : "review"} size={30} strokeWidth={2} />
        </span>
        <p className="kicker">
          {retake ? "Quiz retake" : "Quiz"} · {plural(summary.total || quiz.question_count, "question")} · {quizMinutes(quiz)} min
        </p>
        <h1 className="completion__title">{summary.pass ? (retake ? "Retake passed" : "Quiz passed") : "Below the pass mark this time"}</h1>
        <p className="completion__skill">
          {summary.pass
            ? `${summary.countLabel}. What you've learned holds up under time pressure.`
            : `${summary.countLabel}. The reviews below will shore up what slipped.`}
        </p>
        {task.xp_value > 0 && (
          <p className="completion__xp" aria-label={`${task.xp_value} XP earned`}>
            <span aria-hidden="true">+{xp}</span> <span className="completion__xp-unit">XP</span>
          </p>
        )}
      </div>

      <section className="completion__score" data-outcome={summary.pass ? "passed" : "failed"}>
        <ScoreMeter view={view} size="lg" aside={summary.countLabel || null} />
        <p className="completion__note completion__score-note">
          {summary.breakdown}
          {summary.breakdown ? ". " : ""}XP = {quizMinutes(quiz)} min × your score.
        </p>
      </section>

      {missed.length > 0 && (
        <section className="quiz-missed" aria-labelledby="quiz-missed-title">
          <h2 id="quiz-missed-title" className="lesson__h">
            Reviews come next
          </h2>
          <p className="quiz-missed__lead">
            {missed.length === 1 ? "This skill gets" : "Each of these skills gets"} a short review straight away — {missed.length === 1 ? "it's" : "they're"} at the top of
            your menu.
          </p>
          <ul className="quiz-skill-list quiz-skill-list--missed">
            {missed.map((skill) => (
              <li key={skill.id}>
                <Icon name="review" size={15} />
                <span className="key">{skill.key}</span>
                <Markdown inline>{prettyTitle(skill.title)}</Markdown>
              </li>
            ))}
          </ul>
        </section>
      )}

      {quiz.retake_available ? (
        <section className="quiz-retake card">
          <div>
            <h2 className="quiz-retake__title">Optional retake</h2>
            <p className="muted">The same skills with fresh questions, timed the same way. It doesn't move your next quiz.</p>
          </div>
          <button type="button" className="btn btn--quiz btn--primary" onClick={onRetake} disabled={pendingKey !== null}>
            {pendingKey === "retake" ? <Spinner size={14} label="Starting" /> : <Icon name="stopwatch" size={16} />} Retake the quiz
          </button>
        </section>
      ) : !summary.pass && !retake ? (
        <p className="quiz-retake__later muted">
          <Icon name="info" size={15} /> An optional retake opens once the reviews of what you missed are done.
        </p>
      ) : null}

      <section className="completion__section" aria-labelledby="quiz-answers-title">
        <h2 id="quiz-answers-title" className="lesson__h">
          Your answers
        </h2>
        {quiz.results.length > 0 ? (
          <ol className="quiz-results__list">
            {quiz.results.map((result, i) => (
              <ResultItem key={result.question_id} result={result} index={i} />
            ))}
          </ol>
        ) : (
          <p className="muted">The answers of this quiz aren't available.</p>
        )}
        <p className="quiz-results__fsrs muted">
          A right answer also counts as the review of its skill when that review was due within the next three days.
        </p>
      </section>

      <section className="completion__section completion__session">
        {session ? (
          <>
            <div className="completion__goal-line">
              <span>
                <strong>{session.xp_today}</strong> of {session.daily_goal} XP today
              </span>
              {session.due_reviews > 0 && <span className="muted">{plural(session.due_reviews, "review")} due</span>}
            </div>
            <ProgressBar value={session.xp_today} max={session.daily_goal} label="Daily goal" tone={session.goal_reached ? "learned" : "accent"} size="lg" />
          </>
        ) : (
          <div className="skeleton completion__session-skeleton" />
        )}
      </section>

      <div className="completion__actions">
        {(options.length > 0 || next.data?.task) && (
          <button type="button" className="btn btn--primary btn--lg" onClick={onContinue} disabled={pendingKey !== null}>
            {pendingKey === "continue" ? <Spinner size={16} label="Starting" /> : <Icon name={reviewNext ? "review" : "play"} size={18} />}
            {reviewNext ? "Start the reviews" : "Next task"}
          </button>
        )}
        <Link to={courseHref} className="btn btn--lg">
          <Icon name="graph" size={18} /> Back to the map
        </Link>
      </div>
      {next.data && options.length === 0 && !next.data.task && next.data.message && <p className="completion__message muted">{next.data.message}</p>}
    </div>
  );
}
