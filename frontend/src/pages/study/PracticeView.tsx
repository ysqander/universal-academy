import { useRef, useState } from "react";
import { Icon } from "../../components/Icon";
import { WorkedExampleView } from "../../components/lesson/WorkedExampleView";
import { Markdown } from "../../components/Markdown";
import { SourceRefs } from "../../components/SourceRefs";
import { StateMessage } from "../../components/StateMessage";
import { isGuidedProgress } from "../../lib/guided";
import { kpPosition } from "../../lib/score";
import type { AnswerResult, Task } from "../../types";
import type { SuggestionAction } from "./FeedbackPanel";
import { GuidedStepper } from "./GuidedStepper";
import { KpStepper } from "./KpStepper";
import { MultistepCard } from "./MultistepCard";
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

/** Practice phase (lessons) and review sets: current knowledge point's explanation + one question at a time. */
export function PracticeView({ task, onAnswered, onAdvance, onFlagged, onEasier, onGoToMap }: PracticeViewProps) {
  const lesson = task.lesson;
  const { progress, question } = task;
  const inCapstone = Boolean(progress.in_capstone) || question?.role === "capstone";
  // A proof lesson whose steps were scored while reading: its practice is the variant problem alone.
  const guided = task.type === "lesson" && isGuidedProgress(progress);
  const kp = inCapstone || guided ? undefined : lesson?.knowledge_points[progress.kp_index];
  const explainerRef = useRef<HTMLDetailsElement>(null);
  const [explainer, setExplainer] = useState<{ kp: number; open: boolean } | null>(null);
  const explainerOpen = explainer && explainer.kp === progress.kp_index ? explainer.open : progress.kp_questions_used <= 1;
  // The latest answer's progress (score, answered) while its feedback is on screen; the task itself moves on with
  // Continue. Only while the task it answered is still the one shown (Continue, a flag or an easier step replace it).
  const [scored, setScored] = useState<{ base: Task; after: Task } | null>(null);
  const after = scored && scored.base === task ? scored.after : undefined;
  // That answer finished the lesson: the meter says how (halted early vs every planned question answered).
  const end = after && after.phase === "done" ? { outcome: after.outcome, plannedBefore: task.progress.planned } : null;
  // The server decides whether POST /easier would succeed now (a miss in this knowledge point that wasn't voided,
  // and a strictly easier unseen question left), so the button survives reloads and never leads to a 409.
  const easierAllowed = task.type === "lesson" && Boolean(progress.easier_available) && !inCapstone;

  const openExplainer = () => {
    setExplainer({ kp: progress.kp_index, open: true });
    requestAnimationFrame(() => explainerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const easier = async () => {
    if (await onEasier()) openExplainer();
  };

  const answered = (result: AnswerResult) => {
    setScored({ base: task, after: result.task });
    onAnswered(result);
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

  const total = Math.max(progress.total_questions, 1);
  const position =
    task.type === "review"
      ? `Question ${Math.min(progress.answered + 1, total)} of ${total}`
      : // Lessons: the stepper above says "Question n of target"; the card only says when this one is an extra.
        !guided && kpPosition(progress, kp).extra
        ? "Extra question"
        : null;

  const cardProps = {
    task,
    position,
    onAnswered: answered,
    onContinue: (result: AnswerResult) => onAdvance(result.task),
    onSuggestion,
    onEasier: easierAllowed ? easier : null,
    onTaskReplaced: onFlagged,
  };

  return (
    <div className="practice-view">
      {task.type === "lesson" &&
        (guided ? (
          <GuidedStepper progress={progress} scored={after?.progress} end={end} />
        ) : (
          lesson && <KpStepper points={lesson.knowledge_points} progress={progress} scored={after?.progress} end={end} />
        ))}
      {task.type === "review" && (
        <div className="review-progress" aria-label={position ?? undefined}>
          {Array.from({ length: total }, (_, i) => (
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
        question.kind === "multistep" ? (
          <MultistepCard key={question.id} question={question} {...cardProps} />
        ) : (
          <QuestionCard key={question.id} question={question} {...cardProps} />
        )
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
