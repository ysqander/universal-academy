import { useEffect, useRef, useState, type FormEvent } from "react";
import { getHint, isApiError, revealSolution, submitAnswer } from "../../api";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { useHotkeys } from "../../hooks/useHotkeys";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useStopwatch } from "../../hooks/useStopwatch";
import { formatClock } from "../../lib/format";
import type { AnswerResult, QuestionPublic, RevealResponse, SelfGrade, Task } from "../../types";
import { ChoiceList, FreeResponseInput, MathInput, type ChoiceMark } from "./AnswerInputs";
import { FeedbackPanel, type SuggestionAction } from "./FeedbackPanel";
import { FlagDialog } from "./FlagDialog";

interface QuestionCardProps {
  task: Task;
  question: QuestionPublic;
  /** e.g. "Question 2 of 3" for reviews. */
  position: string | null;
  onAnswered: (result: AnswerResult) => void;
  onContinue: (result: AnswerResult) => void;
  onSuggestion: (action: SuggestionAction, result: AnswerResult) => void;
  onEasier: (() => Promise<void>) | null;
  onTaskReplaced: (task: Task) => void;
}

/** A free-response answer shorter than this (non-space characters) is not a real attempt: comparing it counts as
 * viewing the solution (backend D1 rule, same threshold). */
const MIN_COMMITTED_CHARS = 15;
const committedLength = (text: string): number => text.replace(/\s+/g, "").length;

const GRADES: { grade: SelfGrade; label: string; hint: string }[] = [
  { grade: "correct", label: "I got it", hint: "Meets every point on the checklist" },
  { grade: "partial", label: "Partly", hint: "Right idea, something missing" },
  { grade: "incorrect", label: "Not yet", hint: "Different from the model answer" },
];

function choiceMarks(choices: string[], chosen: number | null, result: AnswerResult | null): ChoiceMark[] {
  if (!result || chosen === null) return [];
  const answer = result.correct_answer.trim();
  const correctIndex = result.correct
    ? chosen
    : (result.correct_choice ?? choices.findIndex((c) => c.trim() === answer));
  return choices.map((_, i) => (i === correctIndex ? "correct" : i === chosen && !result.correct ? "incorrect" : null));
}

export function QuestionCard({ task, question, position, onAnswered, onContinue, onSuggestion, onEasier, onTaskReplaced }: QuestionCardProps) {
  const toast = useToast();
  const finePointer = useMediaQuery("(pointer: fine)");
  const [choice, setChoice] = useState<number | null>(null);
  const [response, setResponse] = useState("");
  // Hints come from POST /hint (recorded server-side, so a reload can't erase them); texts by hint index.
  const [hintTexts, setHintTexts] = useState<string[]>([]);
  const [hinting, setHinting] = useState(false);
  const [revealed, setRevealed] = useState<RevealResponse | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [modelSolution, setModelSolution] = useState<RevealResponse | null>(null);
  const [checked, setChecked] = useState<boolean[]>(() => question.checklist.map(() => false));
  const [submitting, setSubmitting] = useState(false);
  const [easing, setEasing] = useState(false);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [flagOpen, setFlagOpen] = useState(false);
  const { seconds, elapsed } = useStopwatch(result === null);
  const cardRef = useRef<HTMLElement>(null);

  // A new question after scrolling through feedback: bring its prompt back into view.
  useEffect(() => {
    const card = cardRef.current;
    if (card && card.getBoundingClientRect().top < 0) card.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const kind = question.kind;
  const isChoice = kind === "multiple_choice" || kind === "true_false";
  const choices = kind === "true_false" && question.choices.length !== 2 ? ["True", "False"] : question.choices;
  const isFree = kind === "free_response";
  const answered = result !== null;
  const grading = isFree && modelSolution !== null && !answered;
  const canSubmit = !answered && !submitting && (isChoice ? choice !== null : response.trim().length > 0);
  const hintsShown = hintTexts.length;
  const hintTotal = Math.max(question.hints.length, hintsShown);
  const shortFreeAnswer = isFree && committedLength(response) < MIN_COMMITTED_CHARS;

  const send = async (selfGrade: SelfGrade | null) => {
    setSubmitting(true);
    try {
      const outcome = await submitAnswer(task.id, {
        question_id: question.id,
        choice: isChoice ? choice : null,
        response: isChoice ? null : response,
        self_grade: selfGrade,
        hints_used: hintsShown,
        // Comparing a (near-)empty free response is the same as looking at the solution first (D1).
        revealed_solution: revealed !== null || (isFree && shortFreeAnswer),
        seconds: Math.round(elapsed() * 10) / 10,
      });
      setResult(outcome);
      onAnswered(outcome);
    } catch (err) {
      toast.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  // Free response: commit the written answer, then compare it with the model solution and self-grade.
  const commitFreeResponse = async () => {
    if (!response.trim() || modelSolution || submitting) return;
    setSubmitting(true);
    try {
      // The committed answer makes this a comparison for self-grading, not assistance (D1).
      setModelSolution(revealed ?? (await revealSolution(question.id, response)));
    } catch (err) {
      toast.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSubmit) return;
    if (isFree) void commitFreeResponse();
    else void send(null);
  };

  const showSolution = async () => {
    if (revealed || revealing) return;
    setRevealing(true);
    try {
      setRevealed(await revealSolution(question.id));
    } catch (err) {
      toast.error(err);
    } finally {
      setRevealing(false);
    }
  };

  const canHint = !answered && !grading && !hinting && hintsShown < question.hints.length;

  const nextHint = async () => {
    if (!canHint) return;
    setHinting(true);
    try {
      const { hint, hints_used: used } = await getHint(question.id);
      setHintTexts((prev) => {
        // The server's count wins: hints taken before a reload come back too (texts from the question).
        const count = Math.max(used, prev.length);
        return Array.from({ length: count }, (_, i) => (i === used - 1 ? hint : (prev[i] ?? question.hints[i] ?? "")));
      });
    } catch (err) {
      if (isApiError(err, 404) || isApiError(err, 405)) {
        // Older server without POST /hint: show it locally; the count is still reported with the answer.
        setHintTexts((prev) => [...prev, question.hints[prev.length] ?? ""]);
      } else {
        toast.error(err);
      }
    } finally {
      setHinting(false);
    }
  };

  const askEasier = async () => {
    if (!onEasier) return;
    setEasing(true);
    try {
      await onEasier();
    } finally {
      setEasing(false);
    }
  };

  const pickByNumber = (n: number) => () => {
    if (answered || !isChoice || n > choices.length) return false;
    setChoice(n - 1);
  };

  useHotkeys(
    {
      "1": pickByNumber(1),
      "2": pickByNumber(2),
      "3": pickByNumber(3),
      "4": pickByNumber(4),
      "5": pickByNumber(5),
      h: () => {
        // Not while self-grading: a hint then would silently turn the answer into "with help".
        if (!canHint) return false;
        void nextHint();
      },
      Enter: (event) => {
        if (event.target !== document.body || !canSubmit) return false;
        submit();
      },
    },
    !answered && !grading,
  );

  const marks = choiceMarks(choices, choice, result);
  const busy = submitting || easing;

  return (
    <article ref={cardRef} className="question-card card" data-state={answered ? "answered" : "open"} aria-labelledby={`q-${question.id}`}>
      <header className="question-card__head">
        <span className="question-card__kicker">
          {position ?? "Your turn"}
          {question.tier >= 3 ? " · stretch" : ""}
        </span>
        <span className="question-card__timer" aria-label={`Time on this question: ${formatClock(seconds)}`}>
          <Icon name="clock" size={14} /> {formatClock(seconds)}
        </span>
      </header>

      <div id={`q-${question.id}`} className="question-card__prompt">
        <Markdown>{question.prompt}</Markdown>
      </div>
      {question.source_exercise && <p className="question-card__source">Adapted from {question.source_exercise}</p>}

      <form className="question-card__form" onSubmit={submit} noValidate>
        {isChoice ? (
          <ChoiceList
            choices={choices}
            selected={choice}
            onSelect={setChoice}
            onSubmit={() => submit()}
            disabled={answered || busy}
            marks={marks}
            variant={kind === "true_false" ? "boolean" : "multiple"}
          />
        ) : isFree ? (
          <FreeResponseInput
            value={response}
            onChange={setResponse}
            readOnly={modelSolution !== null || answered}
            onCommit={() => submit()}
            autoFocus={finePointer}
            note={
              !modelSolution && !revealed && response.trim() && shortFreeAnswer
                ? "Write out your whole answer before comparing — comparing a very short answer counts as viewing the solution."
                : null
            }
          />
        ) : (
          <MathInput value={response} onChange={setResponse} disabled={answered || busy} kind={kind} autoFocus={finePointer} />
        )}

        {hintsShown > 0 && (
          <ol className="hints" aria-label="Hints">
            {hintTexts.map((hint, i) => (
              <li key={i} className="hint reveal">
                <span className="hint__label">
                  Hint {i + 1} of {hintTotal}
                </span>
                <Markdown>{hint}</Markdown>
              </li>
            ))}
          </ol>
        )}

        {revealed && !grading && !answered && (
          <div className="question-card__solution reveal">
            <span className="feedback__label">Solution</span>
            <Markdown>{revealed.solution}</Markdown>
            {revealed.correct_answer && (
              <p className="question-card__answer-line">
                <strong>Answer:</strong> <Markdown inline>{revealed.correct_answer}</Markdown>
              </p>
            )}
            <p className="question-card__assist-note">You can still answer — it won't count toward the two-in-a-row streak.</p>
          </div>
        )}

        {grading && modelSolution && (
          <div className="self-grade reveal">
            <div className="self-grade__model">
              <span className="feedback__label">Model solution</span>
              <Markdown>{modelSolution.solution}</Markdown>
            </div>
            {question.checklist.length > 0 && (
              <fieldset className="checklist">
                <legend className="feedback__label">Does your answer…</legend>
                {question.checklist.map((item, i) => (
                  <label key={i} className="checklist__item">
                    <input
                      type="checkbox"
                      checked={checked[i] ?? false}
                      onChange={(event) => setChecked(checked.map((c, j) => (j === i ? event.target.checked : c)))}
                    />
                    <Markdown inline>{item}</Markdown>
                  </label>
                ))}
              </fieldset>
            )}
            <div className="self-grade__buttons" role="group" aria-label="Grade your answer">
              {GRADES.map((g) => (
                <button
                  key={g.grade}
                  type="button"
                  className="btn self-grade__button"
                  data-grade={g.grade}
                  onClick={() => void send(g.grade)}
                  disabled={submitting}
                  title={g.hint}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {!answered && !grading && (
          <div className="question-card__actions">
            <button type="submit" className="btn btn--primary btn--lg" disabled={!canSubmit || busy}>
              {submitting ? <Spinner size={16} label="Checking" /> : null}
              {isFree ? "Compare with the model answer" : "Check answer"}
            </button>
            <div className="question-card__tools">
              {question.hints.length > 0 && (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => void nextHint()} disabled={!canHint}>
                  {hinting ? <Spinner size={14} label="Loading hint" /> : <Icon name="bulb" size={15} />}
                  {hintsShown === 0 ? "Hint" : hintsShown < question.hints.length ? `Next hint (${hintsShown + 1} of ${question.hints.length})` : "No more hints"}
                  {hintsShown < question.hints.length && <kbd>H</kbd>}
                </button>
              )}
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => void showSolution()} disabled={revealed !== null || revealing}>
                {revealing ? <Spinner size={14} label="Loading solution" /> : <Icon name="eye" size={15} />} Show solution
              </button>
              {onEasier && (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => void askEasier()} disabled={busy}>
                  {easing ? <Spinner size={14} label="Finding an easier step" /> : <Icon name="steps" size={15} />} Give me an easier step
                </button>
              )}
            </div>
          </div>
        )}
      </form>

      {result && (
        <FeedbackPanel
          result={result}
          before={task}
          onContinue={() => onContinue(result)}
          onSuggestion={(action) => onSuggestion(action, result)}
        />
      )}

      {/* Stays after answering: a wrong answer key is usually only noticed once the solution is shown. */}
      {result?.status !== "voided" && (
        <footer className="question-card__footer">
          <span className="question-card__keys">
            {grading || answered ? null : isChoice ? (
              <>
                <kbd>1</kbd>–<kbd>{choices.length}</kbd> choose · <kbd>Enter</kbd> check
              </>
            ) : isFree ? (
              <>
                <kbd>⌘</kbd>/<kbd>Ctrl</kbd>+<kbd>Enter</kbd> compare
              </>
            ) : (
              <>
                <kbd>Enter</kbd> check
              </>
            )}
          </span>
          <button type="button" className="btn btn--quiet btn--sm question-card__flag" onClick={() => setFlagOpen(true)}>
            <Icon name="flag" size={14} /> {answered ? "Answer key or question seems wrong?" : "This question seems wrong"}
          </button>
        </footer>
      )}

      <FlagDialog open={flagOpen} questionId={question.id} onClose={() => setFlagOpen(false)} onFlagged={onTaskReplaced} />
    </article>
  );
}
