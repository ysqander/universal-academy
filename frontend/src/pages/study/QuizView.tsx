import { useState } from "react";
import { Icon } from "../../components/Icon";
import { Spinner } from "../../components/Spinner";
import { StateMessage } from "../../components/StateMessage";
import { quizPosition } from "../../lib/quiz";
import type { AnswerResult, Task } from "../../types";
import { QuizQuestionCard } from "./QuizQuestionCard";

interface QuizViewProps {
  task: Task;
  /** The countdown reached zero: results are being fetched from the server. */
  timeUp: boolean;
  /** Fetching the results failed (or the server isn't done yet): offer to try again. */
  resultsStuck: boolean;
  onRetryResults: () => void;
  onAdvance: (task: Task) => void;
  /** Ask the server for the task again (409 after an answer: deadline passed, or the page is behind). */
  onStale: () => void;
  onFlagged: (task: Task) => void;
}

/** A running quiz: "Question n of N" with progress pips, then one question at a time (the clock is in the top bar). */
export function QuizView({ task, timeUp, resultsStuck, onRetryResults, onAdvance, onStale, onFlagged }: QuizViewProps) {
  const { index, total } = quizPosition(task);
  const answered = Math.min(Math.max(0, Math.round(task.progress.answered || 0)), total);
  // "Answer saved" / "Passed" after the last submission (the next question is already on screen).
  const [saved, setSaved] = useState<{ text: string; after: string } | null>(null);
  const question = task.question;

  const onSubmitted = (result: AnswerResult, skipped: boolean) => {
    setSaved({ text: skipped ? "Passed — on to the next one" : "Answer saved", after: result.task.question?.id ?? result.task.id });
    onAdvance(result.task);
  };

  const position = `Question ${index} of ${total}`;
  return (
    <div className="quiz-view">
      <div className="quiz-progress">
        <div className="review-progress quiz-progress__pips" role="img" aria-label={`${answered} of ${total} questions answered`}>
          {Array.from({ length: total }, (_, i) => (
            <span key={i} data-state={i < answered ? "done" : i === answered && !timeUp ? "current" : "todo"} />
          ))}
        </div>
      </div>
      <p className="quiz-saved" role="status" aria-live="polite">
        {saved && question && saved.after === question.id && !timeUp ? (
          <span key={saved.after} className="quiz-saved__text">
            <Icon name="check" size={14} /> {saved.text}
          </span>
        ) : null}
      </p>

      {timeUp ? (
        <StateMessage
          icon="clock"
          title="Time's up"
          actions={
            resultsStuck ? (
              <button type="button" className="btn btn--primary" onClick={onRetryResults}>
                See results
              </button>
            ) : null
          }
        >
          {resultsStuck ? (
            "The results aren't ready yet. Try again in a moment."
          ) : (
            <>
              <Spinner size={14} label="Collecting your results" /> Collecting your results…
            </>
          )}
        </StateMessage>
      ) : question ? (
        <QuizQuestionCard
          key={question.id}
          task={task}
          question={question}
          position={position}
          onSubmitted={onSubmitted}
          onStale={onStale}
          onTaskReplaced={onFlagged}
        />
      ) : (
        <StateMessage
          icon="info"
          title="No question to show"
          actions={
            <button type="button" className="btn btn--primary" onClick={onStale}>
              Check again
            </button>
          }
        >
          The quiz may have just finished. Check again to see where it stands.
        </StateMessage>
      )}
    </div>
  );
}
