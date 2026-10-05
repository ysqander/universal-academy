import { useEffect, useRef } from "react";
import { Icon, type IconName } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import type { AnswerResult, Task } from "../../types";

export type SuggestionAction = "easier" | "reread" | "map";

interface FeedbackPanelProps {
  result: AnswerResult;
  before: Task;
  onContinue: () => void;
  onSuggestion: (action: SuggestionAction) => void;
}

interface Tone {
  tone: "correct" | "incorrect" | "neutral";
  icon: IconName;
  title: string;
  note: string | null;
}

function toneOf(result: AnswerResult): Tone {
  if (result.status === "voided") {
    return { tone: "neutral", icon: "flag", title: "Not counted", note: "This question was set aside and doesn't affect your progress." };
  }
  if (result.correct) {
    // Never "it isn't wrong" next to "this lesson pauses here": when the task just ended without passing, only
    // say why the answer didn't count (E1 — keep the copy consistent).
    const paused = result.task.phase === "done" && result.task.outcome === "failed";
    return result.independent
      ? { tone: "correct", icon: "check", title: "Correct", note: null }
      : {
          tone: "correct",
          icon: "check",
          title: "Correct — with help",
          note: paused
            ? "Answers after a hint or the solution don't count toward two in a row."
            : "Answers after a hint or the solution don't count toward two in a row, but they aren't wrong either.",
        };
  }
  if (result.status === "self_graded") {
    return { tone: "incorrect", icon: "review", title: "Not there yet", note: "Compare your answer with the model solution below." };
  }
  return { tone: "incorrect", icon: "x", title: "Not quite", note: null };
}

function progressNote(before: Task, after: Task): string | null {
  if (after.phase === "done") {
    if (after.outcome === "passed") return before.type === "lesson" ? "That completes the lesson." : "That completes the review.";
    if (after.outcome === "failed") return before.type === "lesson" ? "This lesson pauses here — it will come back later with fresh questions." : "That ends the review.";
    return null;
  }
  if (after.type === "review") return null;
  if (after.progress.kp_index > before.progress.kp_index) return "Knowledge point complete — on to the next step.";
  if (after.progress.kp_index < before.progress.kp_index) return "Stepping back to an earlier knowledge point.";
  if (after.progress.streak > 0) return `${after.progress.streak} of ${after.progress.needed_in_row} in a row.`;
  return null;
}

const SUGGESTION_ACTIONS: Partial<Record<NonNullable<AnswerResult["suggestion"]>, { action: SuggestionAction; label: string; icon: IconName }>> = {
  easier_step: { action: "easier", label: "Try an easier step", icon: "steps" },
  reread_example: { action: "reread", label: "Re-read the worked example", icon: "lesson" },
  review_prerequisite: { action: "map", label: "See what to review", icon: "graph" },
};

export function FeedbackPanel({ result, before, onContinue, onSuggestion }: FeedbackPanelProps) {
  const continueRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const tone = toneOf(result);
  // The server's feedback already explains "with help" answers; don't say it twice.
  const toneNote = tone.note && !(result.correct && !result.independent && result.feedback.trim()) ? tone.note : null;
  const note = progressNote(before, result.task);
  const suggestion = result.suggestion ? SUGGESTION_ACTIONS[result.suggestion] : undefined;
  const taskOngoing = result.task.phase !== "done";
  const suggestionAvailable = suggestion && (suggestion.action === "map" || taskOngoing);
  const showAnswer = !result.correct && result.correct_answer.trim() && result.status !== "voided";

  useEffect(() => {
    continueRef.current?.focus({ preventScroll: true });
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);

  return (
    <section ref={panelRef} className="feedback" data-tone={tone.tone} aria-live="polite">
      <header className="feedback__head">
        <span className="feedback__icon">
          <Icon name={tone.icon} size={20} strokeWidth={2.25} />
        </span>
        <div className="feedback__heading">
          <h3 className="feedback__title">{tone.title}</h3>
          {note && <p className="feedback__progress">{note}</p>}
        </div>
      </header>

      {result.feedback && <Markdown className="feedback__text">{result.feedback}</Markdown>}
      {toneNote && <p className="feedback__note">{toneNote}</p>}

      {showAnswer && (
        <div className="feedback__answer">
          <span className="feedback__label">Correct answer</span>
          <Markdown>{result.correct_answer}</Markdown>
        </div>
      )}

      {result.suggestion_text && (
        <div className="feedback__suggestion">
          <Icon name="bulb" size={18} />
          <span className="feedback__suggestion-text">{result.suggestion_text}</span>
          {suggestionAvailable && suggestion && (
            <button type="button" className="btn btn--sm" onClick={() => onSuggestion(suggestion.action)}>
              <Icon name={suggestion.icon} size={14} /> {suggestion.label}
            </button>
          )}
        </div>
      )}

      {result.solution.trim() && (
        <details className="disclosure feedback__solution" open={!result.correct}>
          <summary>Full solution</summary>
          <div className="disclosure__body">
            <Markdown>{result.solution}</Markdown>
          </div>
        </details>
      )}

      <div className="feedback__actions">
        <button ref={continueRef} type="button" className="btn btn--primary btn--lg" onClick={onContinue}>
          {taskOngoing ? "Continue" : "See results"} <Icon name="arrowRight" size={18} />
        </button>
        <span className="feedback__hint">
          <kbd>Enter</kbd>
        </span>
      </div>
    </section>
  );
}
