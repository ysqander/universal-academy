import { useEffect, useRef, useState, type FormEvent } from "react";
import { getHint, isApiError, revealSolution, submitAnswer } from "../../api";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { useHotkeys } from "../../hooks/useHotkeys";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useStopwatch } from "../../hooks/useStopwatch";
import { ChoiceList, FreeResponseInput, MathInput, TfKeyList, TrueFalseSetInput, type ChoiceMark } from "../../components/answers/AnswerInputs";
import { formatClock } from "../../lib/format";
import { emptyTfMarks, encodeTfMarks, markNextUnmarked, parseTfKey, resolveTfKey, tfRowResults, type TfLetter, type TfMark } from "../../lib/trueFalseSet";
import type { AnswerResult, QuestionPublic, RevealResponse, SelfGrade, Task } from "../../types";
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
  // The server records this attempt's help and commitments (hints, a revealed solution, a free-response answer
  // committed for comparison), so a reload or a resumed task starts from them rather than from a blank card.
  const committed = question.kind === "free_response" && question.committed_response ? question.committed_response : null;
  const [choice, setChoice] = useState<number | null>(null);
  const [response, setResponse] = useState(committed ?? "");
  // true_false_set: one True/False mark per statement, sent as "TFT".
  const [tfMarks, setTfMarks] = useState<TfMark[]>(() => emptyTfMarks(question.kind === "true_false_set" ? question.choices.length : 0));
  // Hints come from POST /hint (recorded server-side, so a reload can't erase them); texts by hint index.
  const [hintTexts, setHintTexts] = useState<string[]>(() => question.hints.slice(0, question.hints_used));
  const [hinting, setHinting] = useState(false);
  const [revealed, setRevealed] = useState<RevealResponse | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [modelSolution, setModelSolution] = useState<RevealResponse | null>(null);
  // Fetching the solution text for a reveal / comparison recorded before a reload (the question carries only flags).
  const [restoring, setRestoring] = useState(() => Boolean(committed || question.revealed));
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

  // Restore a reveal / comparison the server recorded for this attempt. Repeating the reveal changes nothing
  // server-side (it is already recorded) and returns the solution text: a committed free response reopens in
  // self-grading mode, a revealed solution shows again with its "won't count" note.
  const wasRevealed = question.revealed;
  useEffect(() => {
    if (!committed && !wasRevealed) return;
    let cancelled = false;
    revealSolution(question.id, committed).then(
      (solution) => {
        if (cancelled) return;
        if (committed) setModelSolution(solution);
        if (wasRevealed) setRevealed(solution);
        setRestoring(false);
      },
      (err: unknown) => {
        if (cancelled) return;
        toast.error(err);
        setRestoring(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [question.id, committed, wasRevealed, toast]);

  const kind = question.kind;
  const isChoice = kind === "multiple_choice" || kind === "true_false";
  const choices = kind === "true_false" && question.choices.length !== 2 ? ["True", "False"] : question.choices;
  const isFree = kind === "free_response";
  const isTfSet = kind === "true_false_set";
  const tfResponse = isTfSet ? encodeTfMarks(tfMarks) : null;
  const answered = result !== null;
  const grading = isFree && modelSolution !== null && !answered;
  const canSubmit =
    !answered && !submitting && !restoring && (isChoice ? choice !== null : isTfSet ? tfResponse !== null : response.trim().length > 0);
  const hintsShown = hintTexts.length;
  const hintTotal = Math.max(question.hints.length, hintsShown);
  const shortFreeAnswer = isFree && committedLength(response) < MIN_COMMITTED_CHARS;

  const send = async (selfGrade: SelfGrade | null) => {
    setSubmitting(true);
    try {
      const outcome = await submitAnswer(task.id, {
        question_id: question.id,
        choice: isChoice ? choice : null,
        response: isChoice ? null : isTfSet ? tfResponse : response,
        self_grade: selfGrade,
        hints_used: hintsShown,
        // Comparing a (near-)empty free response is the same as looking at the solution first (D1).
        revealed_solution: revealed !== null || question.revealed || (isFree && shortFreeAnswer),
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
    if (revealed || revealing || restoring) return;
    setRevealing(true);
    try {
      setRevealed(await revealSolution(question.id));
    } catch (err) {
      toast.error(err);
    } finally {
      setRevealing(false);
    }
  };

  const canHint = !answered && !grading && !hinting && !restoring && hintsShown < question.hints.length;

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

  // true_false_set without focus in the list: T / F mark the next unmarked statement (then Enter checks).
  const markNext = (mark: TfLetter) => () => {
    if (answered || !isTfSet || busy) return false;
    setTfMarks((prev) => markNextUnmarked(prev, mark));
  };

  useHotkeys(
    {
      t: markNext("T"),
      f: markNext("F"),
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
  // true_false_set: mark each statement right/wrong once graded (from the grader's feedback / the correct answer).
  const tfKey =
    isTfSet && result && result.status !== "voided" ? resolveTfKey(result.correct_answer, result.feedback, tfResponse, result.correct, choices.length) : null;
  const revealedKey = isTfSet && revealed ? parseTfKey(revealed.correct_answer, choices.length) : null;
  const tfResults = isTfSet && result && result.status !== "voided" ? tfRowResults(tfResponse, tfKey, result.correct, choices.length) : undefined;

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
        ) : isTfSet ? (
          <TrueFalseSetInput
            statements={choices}
            marks={tfMarks}
            onChange={setTfMarks}
            onSubmit={() => submit()}
            disabled={answered || busy}
            results={tfResults}
            answerKey={tfKey}
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
        ) : kind === "numeric" || kind === "expression" ? (
          <MathInput value={response} onChange={setResponse} disabled={answered || busy} kind={kind} autoFocus={finePointer} />
        ) : null}

        {hintsShown > 0 && (
          <ol className="hints" aria-label="Hints">
            {hintTexts.map((hint, i) => (
              <li key={i} className="hint reveal">
                <span className="hint__label">
                  Hint {i + 1} of {hintTotal}
                  {i === 0 && !answered && <span className="hint__cost"> · ½ point for this question</span>}
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
            {revealedKey ? (
              <div className="question-card__answer-line">
                <strong>Answer:</strong> <TfKeyList answerKey={revealedKey} />
              </div>
            ) : (
              revealed.correct_answer && (
                <p className="question-card__answer-line">
                  <strong>Answer:</strong> <Markdown inline>{revealed.correct_answer}</Markdown>
                </p>
              )
            )}
            <p className="question-card__assist-note">You can still answer — after seeing the solution it's worth 0 points.</p>
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
              {submitting || (restoring && committed) ? <Spinner size={16} label={restoring ? "Restoring your answer" : "Checking"} /> : null}
              {restoring && committed ? "Reopening your answer…" : isFree ? "Compare with the model answer" : "Check answer"}
            </button>
            <div className="question-card__tools">
              {question.hints.length > 0 && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => void nextHint()}
                  disabled={!canHint}
                  title={hintsShown === 0 ? "With a hint, a right answer earns ½ point" : undefined}
                >
                  {hinting ? <Spinner size={14} label="Loading hint" /> : <Icon name="bulb" size={15} />}
                  {hintsShown === 0 ? "Hint" : hintsShown < question.hints.length ? `Next hint (${hintsShown + 1} of ${question.hints.length})` : "No more hints"}
                  {hintsShown < question.hints.length && <kbd>H</kbd>}
                </button>
              )}
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => void showSolution()}
                disabled={revealed !== null || revealing || restoring}
                title={revealed ? undefined : "After seeing the solution, this question earns 0 points"}
              >
                {revealing || (restoring && question.revealed) ? <Spinner size={14} label="Loading solution" /> : <Icon name="eye" size={15} />}{" "}
                {revealed ? "Solution shown" : "Show solution"}
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
          // The statements already show the key row by row.
          answerShown={tfKey !== null}
          revealed={revealed !== null || question.revealed}
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
            ) : isTfSet ? (
              <>
                <kbd>T</kbd>/<kbd>F</kbd> mark a statement · <kbd>←</kbd>
                <kbd>→</kbd> switch · <kbd>Enter</kbd> check
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
