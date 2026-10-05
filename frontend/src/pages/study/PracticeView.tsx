import { useRef, useState } from "react";
import { Icon } from "../../components/Icon";
import { WorkedExampleView } from "../../components/lesson/WorkedExampleView";
import { Markdown } from "../../components/Markdown";
import { SourceRefs } from "../../components/SourceRefs";
import { StateMessage } from "../../components/StateMessage";
import type { AnswerResult, Task } from "../../types";
import type { SuggestionAction } from "./FeedbackPanel";
import { KpStepper } from "./KpStepper";
import { QuestionCard } from "./QuestionCard";

interface PracticeViewProps {
  task: Task;
  onAnswered: (result: AnswerResult) => void;
  onAdvance: (task: Task) => void;
  /** A question was flagged; the server's re-graded task replaces the current one. */
  onFlagged: (task: Task) => void;
  onEasier: () => Promise<boolean>;
  onGoToMap: () => void;
}

/** A knowledge point is failed after this many questions (SPEC §6.3), however large its pool. */
const MAX_KP_QUESTIONS = 5;

/** Practice phase (lessons) and review sets: current knowledge point's explanation + one question at a time. */
export function PracticeView({ task, onAnswered, onAdvance, onFlagged, onEasier, onGoToMap }: PracticeViewProps) {
  const lesson = task.lesson;
  const { progress, question } = task;
  const kp = lesson?.knowledge_points[progress.kp_index];
  const explainerRef = useRef<HTMLDetailsElement>(null);
  const [explainer, setExplainer] = useState<{ kp: number; open: boolean } | null>(null);
  const explainerOpen = explainer && explainer.kp === progress.kp_index ? explainer.open : progress.kp_questions_used <= 1;
  // An easier step is offered only after a miss in the current knowledge point (the server enforces it too).
  const [missedIn, setMissedIn] = useState<string | null>(null);
  const kpKey = `${task.id}:${progress.kp_index}`;
  const easierAllowed = task.type === "lesson" && missedIn === kpKey;

  const answered = (result: AnswerResult) => {
    if (task.type === "lesson" && !result.correct && result.status !== "voided") setMissedIn(kpKey);
    onAnswered(result);
  };

  const openExplainer = () => {
    setExplainer({ kp: progress.kp_index, open: true });
    requestAnimationFrame(() => explainerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const easier = async () => {
    if (await onEasier()) openExplainer();
  };

  const onSuggestion = (action: SuggestionAction, result: AnswerResult) => {
    if (action === "map") {
      onGoToMap();
      return;
    }
    onAdvance(result.task);
    if (action === "easier") void easier();
    else openExplainer();
  };

  const questionNo = progress.kp_questions_used || 1;
  const upTo = progress.kp_questions_available ? Math.max(Math.min(progress.kp_questions_available, MAX_KP_QUESTIONS), questionNo) : 0;
  const position =
    task.type === "review"
      ? `Question ${Math.min(progress.answered + 1, Math.max(progress.total_questions, 1))} of ${Math.max(progress.total_questions, 1)}`
      : `Question ${questionNo}${upTo ? ` of up to ${upTo}` : ""}`;

  return (
    <div className="practice-view">
      {task.type === "lesson" && lesson && <KpStepper points={lesson.knowledge_points} progress={progress} />}
      {task.type === "review" && (
        <div className="review-progress" aria-label={position}>
          {Array.from({ length: Math.max(progress.total_questions, 1) }, (_, i) => (
            <span key={i} data-state={i < progress.answered ? "done" : i === progress.answered ? "current" : "todo"} />
          ))}
        </div>
      )}

      {kp && (
        <details
          ref={explainerRef}
          className="disclosure kp-explainer"
          open={explainerOpen}
          onToggle={(event) => setExplainer({ kp: progress.kp_index, open: event.currentTarget.open })}
        >
          <summary>
            <span className="kp-explainer__summary">
              <span className="kicker">How this step works</span>
              <span className="kp-explainer__title">
                <Markdown inline>{kp.title}</Markdown>
              </span>
            </span>
          </summary>
          <div className="disclosure__body kp-explainer__body">
            <Markdown>{kp.explanation}</Markdown>
            <WorkedExampleView key={`${task.id}:${kp.index}`} example={kp.worked_example} />
            {lesson && <SourceRefs refs={lesson.source_refs} pages={lesson.source_pages} labels={lesson.page_labels} compact />}
          </div>
        </details>
      )}

      {question ? (
        <QuestionCard
          key={question.id}
          task={task}
          question={question}
          position={position}
          onAnswered={answered}
          onContinue={(result) => onAdvance(result.task)}
          onSuggestion={onSuggestion}
          onEasier={easierAllowed ? easier : null}
          onTaskReplaced={onFlagged}
        />
      ) : (
        <StateMessage
          icon="info"
          title="No question is available right now"
          actions={
            <button type="button" className="btn" onClick={onGoToMap}>
              <Icon name="graph" size={16} /> Back to the map
            </button>
          }
        >
          Every remaining question for this step was set aside. Try again later — new questions are generated in the background.
        </StateMessage>
      )}
    </div>
  );
}
