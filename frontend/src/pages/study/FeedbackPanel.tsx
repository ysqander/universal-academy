import { useEffect, useRef } from "react";
import { Icon, type IconName } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { stripStockVerdict } from "../../lib/feedback";
import { earnedSteps, multistepNote, stepsSummary } from "../../lib/multistep";
import { pointsLabel, progressNote, withoutPassNote } from "../../lib/score";
import type { AnswerResult, StepResult, Task } from "../../types";

export type SuggestionAction = "easier" | "reread" | "map";

/** A finished multistep question: its steps (the summary replaces the one-answer verdict). */
export interface StepsSummary {
  results: StepResult[];
  total: number;
  capstone: boolean;
  /** Steps answered with "Show solution". */
  revealed?: ReadonlySet<number>;
}

interface FeedbackPanelProps {
  result: AnswerResult;
  /** The card already shows the correct answer in place (e.g. true/false statements marked one by one). */
  answerShown?: boolean;
  /** The solution was shown before answering (the answer earns 0 points). */
  revealed?: boolean;
  /** Multistep: the steps answered. */
  steps?: StepsSummary | null;
  before: Task;
  onContinue: () => void;
  onSuggestion: (action: SuggestionAction) => void;
}

interface Tone {
  tone: "correct" | "incorrect" | "partial" | "neutral";
  icon: IconName;
  title: string;
  note: string | null;
}

function toneOf(result: AnswerResult, steps: StepsSummary | null): Tone {
  if (result.status === "voided") {
    return { tone: "neutral", icon: "flag", title: "Not counted", note: "This question was set aside and doesn't affect your progress." };
  }
  if (steps) {
    const earned = earnedSteps(steps.results);
    if (earned >= steps.total) return { tone: "correct", icon: "check", title: `All ${steps.total} steps right`, note: null };
    if (earned > 0) return { tone: "partial", icon: "steps", title: stepsSummary(steps.results, steps.total), note: null };
    const allShown = steps.results.length > 0 && steps.results.every((r) => steps.revealed?.has(r.index));
    return allShown
      ? { tone: "neutral", icon: "eye", title: stepsSummary(steps.results, steps.total), note: "Every step's solution was shown — read it through once more below." }
      : { tone: "incorrect", icon: "x", title: stepsSummary(steps.results, steps.total), note: "Read the steps above once more — the full solution is below." };
  }
  // Help used is said once, in the points line under the title ("½ point — with a hint").
  if (result.correct) return { tone: "correct", icon: "check", title: "Correct", note: null };
  if (result.status === "self_graded") {
    return { tone: "incorrect", icon: "review", title: "Not there yet", note: "Compare your answer with the model solution below." };
  }
  return { tone: "incorrect", icon: "x", title: "Not quite", note: null };
}

const SUGGESTION_ACTIONS: Partial<Record<NonNullable<AnswerResult["suggestion"]>, { action: SuggestionAction; label: string; icon: IconName }>> = {
  easier_step: { action: "easier", label: "Try an easier step", icon: "steps" },
  reread_example: { action: "reread", label: "Re-read the worked example", icon: "lesson" },
  review_prerequisite: { action: "map", label: "See what to review", icon: "graph" },
};

export function FeedbackPanel({ result, answerShown = false, revealed = false, steps = null, before, onContinue, onSuggestion }: FeedbackPanelProps) {
  const continueRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const tone = toneOf(result, steps);
  // "Not quite." under the title "Not quite" only repeats the verdict: show what the grader adds to it, if anything.
  // Multistep: each step's feedback is in its step card and the summary is the title, so of the server's
  // question-level text only its note is kept (extra question next, the capstone next…), not the last step's
  // verdict and the recap. The server's "Lesson passed with N%." is said once, by the progress note below.
  const feedback = withoutPassNote(steps ? multistepNote(result) : stripStockVerdict(result.feedback));
  const toneNote = tone.note;
  const points = steps ? (steps.capstone ? "Counts double in your score" : null) : pointsLabel(result, revealed);
  const note = progressNote(before, result.task, `${feedback} ${result.suggestion_text ?? ""}`);
  const suggestion = result.suggestion ? SUGGESTION_ACTIONS[result.suggestion] : undefined;
  const taskOngoing = result.task.phase !== "done";
  const suggestionAvailable = suggestion && (suggestion.action === "map" || taskOngoing);
  const showAnswer = !steps && !answerShown && !result.correct && result.correct_answer.trim() && result.status !== "voided";

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
          {points && <p className="feedback__points">{points}</p>}
          {note && <p className="feedback__progress">{note}</p>}
        </div>
      </header>

      {feedback && <Markdown className="feedback__text">{feedback}</Markdown>}
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
        <details className="disclosure feedback__solution" open={steps ? false : !result.correct}>
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
