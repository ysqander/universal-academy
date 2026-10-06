import { useEffect, useReducer, useRef, useState, type FormEvent } from "react";
import { getHint, isApiError, submitAnswer } from "../../api";
import { ChoiceList, MathInput, TfKeyList, TrueFalseSetInput } from "../../components/answers/AnswerInputs";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { useHotkeys } from "../../hooks/useHotkeys";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useStopwatch } from "../../hooks/useStopwatch";
import { choiceMarksFromText } from "../../lib/choices";
import { stripStockVerdict } from "../../lib/feedback";
import { formatClock } from "../../lib/format";
import {
  activeStep,
  initialTrail,
  questionDone,
  revealedStepIndexes,
  STEP_MARK_GLYPH,
  STEP_MARK_LABEL,
  stepMark,
  stepPips,
  stepResultOf,
  stepRevealed,
  stepsSummary,
  trailItems,
  trailReducer,
} from "../../lib/multistep";
import { stepPointsLabel } from "../../lib/score";
import { emptyTfMarks, encodeTfMarks, markNextUnmarked, parseTfKey, resolveTfKey, tfRowResults, type TfLetter, type TfMark } from "../../lib/trueFalseSet";
import type { AnswerResult, MultistepStepPublic, QuestionPublic, StepResult, Task } from "../../types";
import { FeedbackPanel, type SuggestionAction } from "./FeedbackPanel";
import { FlagDialog } from "./FlagDialog";

interface MultistepCardProps {
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

/**
 * A multistep question (the lesson's capstone, or a guided walkthrough): the problem on top, then its steps one at a
 * time. Each step is machine-checked on its own (POST /answer with `step`), shows right / wrong, the result and why,
 * then "Next step"; answered steps stay above as a compact trail. After the last step: the question's summary.
 */
export function MultistepCard({ task, question, position, onAnswered, onContinue, onSuggestion, onEasier, onTaskReplaced }: MultistepCardProps) {
  const [trail, dispatch] = useReducer(trailReducer, question, initialTrail);
  const [final, setFinal] = useState<AnswerResult | null>(null);
  // Steps answered with "Show solution" (0 points; their result line says so) — rebuilt from the server's step
  // results after a reload, so such a step never turns into "not right".
  const [revealedSteps, setRevealedSteps] = useState<ReadonlySet<number>>(() => revealedStepIndexes(question.step_results ?? []));
  // A hint or a shown answer on this attempt: the easier question is no longer offered (it would wipe out that help).
  const [helped, setHelped] = useState(() => question.hints_used > 0 || question.revealed);
  const [flagOpen, setFlagOpen] = useState(false);
  const [easing, setEasing] = useState(false);
  const { seconds } = useStopwatch(final === null);
  const cardRef = useRef<HTMLElement>(null);
  const capstone = question.role === "capstone";
  const steps = question.steps;
  const active = activeStep(trail);
  const shownIndex = trail.open ?? active;
  const shownStep = shownIndex === null ? null : (steps[shownIndex] ?? null);
  const shownResult = shownIndex === null ? null : (trail.results.find((r) => r.index === shownIndex) ?? null);
  const pips = stepPips(trail, revealedSteps);
  const startedHere = trail.results.length === 0;

  // A new question after scrolling through feedback: bring its prompt back into view.
  useEffect(() => {
    const card = cardRef.current;
    if (card && card.getBoundingClientRect().top < 0) card.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const onStepAnswered = (result: AnswerResult, index: number, revealed: boolean) => {
    const stepResult = stepResultOf(result, index);
    if (revealed || stepRevealed(stepResult)) setRevealedSteps((prev) => new Set(prev).add(index));
    dispatch({ type: "answered", result: stepResult });
    if (questionDone(result)) {
      setFinal(result);
      onAnswered(result);
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

  return (
    <article
      ref={cardRef}
      className="question-card card multistep"
      data-role={question.role}
      data-state={final ? "answered" : "open"}
      aria-labelledby={`q-${question.id}`}
    >
      <header className="question-card__head">
        <span className="question-card__kicker">
          {capstone ? (
            <span className="multistep__badge">
              <Icon name="layers" size={13} /> Put it together
            </span>
          ) : null}
          {capstone ? position && <span className="multistep__position">{position}</span> : (position ?? "Your turn")}
        </span>
        <span className="question-card__timer" aria-label={`Time on this problem: ${formatClock(seconds)}`}>
          <Icon name="clock" size={14} /> {formatClock(seconds)}
        </span>
      </header>

      {capstone && (
        <p className="multistep__intro">
          One problem in {trail.total} steps that chains this lesson's ideas. Each step is checked before the next — it counts double in your score.
        </p>
      )}

      <div id={`q-${question.id}`} className="question-card__prompt">
        <Markdown>{question.prompt}</Markdown>
      </div>
      {question.source_exercise && <p className="question-card__source">Adapted from {question.source_exercise}</p>}

      <div className="multistep__steps">
        <div className="multistep__progress">
          <span className="multistep__count">{final ? stepsSummary(trail.results, trail.total) : `${trail.total} steps`}</span>
          <ol className="multistep__pips" aria-hidden="true">
            {pips.map((pip, i) => (
              <li key={i} data-state={pip} />
            ))}
          </ol>
        </div>

        {trailItems(trail).length > 0 && (
          <ol className="step-trail" aria-label="Steps answered">
            {trailItems(trail).map((r) => (
              <StepTrailItem key={r.index} step={steps[r.index]} result={r} revealed={revealedSteps.has(r.index)} />
            ))}
          </ol>
        )}

        {shownStep && shownIndex !== null && (
          <StepCard
            key={shownStep.index}
            taskId={task.id}
            questionId={question.id}
            step={shownStep}
            index={shownIndex}
            total={trail.total}
            result={shownResult}
            revealed={revealedSteps.has(shownIndex)}
            // The server records the current step's hint / shown answer (QuestionPublic.hints_used / revealed).
            hintUsed={shownIndex === question.step_index && question.hints_used > 0}
            answerShown={shownIndex === question.step_index && question.revealed}
            done={final !== null}
            onAnswered={(result, revealed) => onStepAnswered(result, shownIndex, revealed)}
            onHint={() => setHelped(true)}
            onNext={() => dispatch({ type: "next" })}
          />
        )}
      </div>

      {onEasier && startedHere && !final && !helped && revealedSteps.size === 0 && (
        <div className="question-card__tools">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void askEasier()} disabled={easing}>
            {easing ? <Spinner size={14} label="Finding an easier step" /> : <Icon name="steps" size={15} />} Give me an easier question
          </button>
        </div>
      )}

      {final && (
        <FeedbackPanel
          result={final}
          answerShown
          steps={{ results: trail.results, total: trail.total, capstone, revealed: revealedSteps }}
          before={task}
          onContinue={() => onContinue(final)}
          onSuggestion={(action) => onSuggestion(action, final)}
        />
      )}

      {final?.status !== "voided" && (
        <footer className="question-card__footer">
          <span className="question-card__keys">
            {final || shownResult ? null : shownStep?.kind === "true_false_set" ? (
              <>
                <kbd>T</kbd>/<kbd>F</kbd> mark a statement · <kbd>Enter</kbd> check
              </>
            ) : shownStep?.kind === "multiple_choice" || shownStep?.kind === "true_false" ? (
              <>
                <kbd>1</kbd>–<kbd>{Math.max(shownStep.choices.length, 2)}</kbd> choose · <kbd>Enter</kbd> check
              </>
            ) : (
              <>
                <kbd>Enter</kbd> check
              </>
            )}
          </span>
          <button type="button" className="btn btn--quiet btn--sm question-card__flag" onClick={() => setFlagOpen(true)}>
            <Icon name="flag" size={14} /> {final ? "Answer key or problem seems wrong?" : "This problem seems wrong"}
          </button>
        </footer>
      )}

      <FlagDialog open={flagOpen} questionId={question.id} onClose={() => setFlagOpen(false)} onFlagged={onTaskReplaced} />
    </article>
  );
}

/** An answered step, compact: mark, "Step 1", its result; opens to the step's prompt and explanation. */
function StepTrailItem({ step, result, revealed }: { step: MultistepStepPublic | undefined; result: StepResult; revealed: boolean }) {
  const mark = stepMark(result.score, revealed);
  const tfKey = step?.kind === "true_false_set" ? parseTfKey(result.answer_display, step.choices.length) : null;
  return (
    <li className="step-trail__item" data-mark={mark}>
      <details className="step-trail__details">
        <summary className="step-trail__summary">
          <span className="step-trail__mark" aria-hidden="true">
            {mark === "shown" ? <Icon name="eye" size={14} strokeWidth={2.25} /> : STEP_MARK_GLYPH[mark]}
          </span>
          <span className="step-trail__label">
            Step {result.index + 1}
            <span className="visually-hidden">: {STEP_MARK_LABEL[mark]}.</span>
          </span>
          <span className="step-trail__result">
            {tfKey ? <TfKeyList answerKey={tfKey} /> : result.answer_display.trim() ? <Markdown inline>{result.answer_display}</Markdown> : null}
          </span>
        </summary>
        <div className="step-trail__body">
          {step && <Markdown className="step-trail__prompt">{step.prompt}</Markdown>}
          {result.explanation.trim() && <Markdown className="step-trail__explanation">{result.explanation}</Markdown>}
          <p className="step-trail__points">{stepPointsLabel(result.score, revealed)}</p>
        </div>
      </details>
    </li>
  );
}

interface StepCardProps {
  taskId: string;
  questionId: string;
  step: MultistepStepPublic;
  index: number;
  total: number;
  /** Set once this step is answered (the card then shows its result until "Next step"). */
  result: StepResult | null;
  revealed: boolean;
  /** This step's hint was already shown in this attempt (before a reload). */
  hintUsed: boolean;
  /** This step's answer was already shown in this attempt: it earns 0 points whatever is entered. */
  answerShown: boolean;
  /** The whole question is answered (the summary follows; no "Next step"). */
  done: boolean;
  onAnswered: (result: AnswerResult, revealed: boolean) => void;
  /** The step's hint was shown (the card then stops offering an easier question). */
  onHint: () => void;
  onNext: () => void;
}

/** The current step: its prompt and answer input, one hint (½ point), Show solution (0 points), then its result. */
function StepCard({ taskId, questionId, step, index, total, result, revealed, hintUsed, answerShown, done, onAnswered, onHint, onNext }: StepCardProps) {
  const toast = useToast();
  const finePointer = useMediaQuery("(pointer: fine)");
  const kind = step.kind;
  const isChoice = kind === "multiple_choice" || kind === "true_false";
  const isTfSet = kind === "true_false_set";
  const choices = kind === "true_false" && step.choices.length !== 2 ? ["True", "False"] : step.choices;
  const [choice, setChoice] = useState<number | null>(null);
  const [response, setResponse] = useState("");
  const [tfMarks, setTfMarks] = useState<TfMark[]>(() => emptyTfMarks(isTfSet ? choices.length : 0));
  // The server records the hint (POST /hint?step=i), so after a reload it is shown again.
  const [hint, setHint] = useState<string | null>(() => (hintUsed && step.hint.trim() ? step.hint : null));
  const [hinting, setHinting] = useState(false);
  const [submitting, setSubmitting] = useState<"answer" | "reveal" | null>(null);
  // A response the grader can't take yet ("simplify it", "mark every statement"): said here, not in a toast.
  const [invalid, setInvalid] = useState<string | null>(null);
  const { elapsed } = useStopwatch(result === null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const answered = result !== null;
  const tfResponse = isTfSet ? encodeTfMarks(tfMarks) : null;
  const ready = isChoice ? choice !== null : isTfSet ? tfResponse !== null : response.trim().length > 0;
  const busy = submitting !== null;
  const canSubmit = !answered && !busy && ready;
  const canHint = !answered && !busy && !hinting && hint === null && step.hint.trim().length > 0;
  const last = index >= total - 1;

  // Answered: move focus to "Next step" (Enter continues) or, on the last step, leave it to the summary below.
  useEffect(() => {
    if (!answered) return;
    if (!last) nextRef.current?.focus({ preventScroll: true });
    resultRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [answered, last]);

  const send = async (reveal: boolean) => {
    if (answered || busy) return;
    setSubmitting(reveal ? "reveal" : "answer");
    setInvalid(null);
    try {
      const outcome = await submitAnswer(taskId, {
        question_id: questionId,
        step: index,
        // Showing the solution answers the step with nothing: it earns 0 points and shows the result.
        choice: reveal || !isChoice ? null : choice,
        response: reveal || isChoice ? null : isTfSet ? tfResponse : response.trim(),
        hints_used: hint !== null ? 1 : 0,
        revealed_solution: reveal,
        seconds: Math.round(elapsed() * 10) / 10,
      });
      onAnswered(outcome, reveal);
    } catch (err) {
      if (isApiError(err, 422)) setInvalid(err.message);
      else toast.error(err);
    } finally {
      setSubmitting(null);
    }
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (canSubmit) void send(false);
  };

  const showHint = async () => {
    if (!canHint) return;
    setHinting(true);
    try {
      const { hint: text } = await getHint(questionId, index);
      setHint(text.trim() ? text : step.hint);
      onHint();
    } catch (err) {
      if (isApiError(err, 404) || isApiError(err, 405) || isApiError(err, 422)) {
        // A server without per-step hints: show it locally; the answer still reports it (hints_used).
        setHint(step.hint);
        onHint();
      } else {
        toast.error(err);
      }
    } finally {
      setHinting(false);
    }
  };

  useHotkeys(
    {
      t: () => {
        if (!isTfSet || busy) return false;
        setTfMarks((prev) => markNextUnmarked(prev, "T" as TfLetter));
      },
      f: () => {
        if (!isTfSet || busy) return false;
        setTfMarks((prev) => markNextUnmarked(prev, "F" as TfLetter));
      },
      "1": () => pick(0),
      "2": () => pick(1),
      "3": () => pick(2),
      "4": () => pick(3),
      "5": () => pick(4),
      h: () => {
        if (!canHint) return false;
        void showHint();
      },
      Enter: (event) => {
        if (event.target !== document.body || !canSubmit) return false;
        submit();
      },
    },
    !answered,
  );

  function pick(i: number): false | void {
    if (answered || busy || !isChoice || i >= choices.length) return false;
    setChoice(i);
    setInvalid(null);
  }

  // `revealed`: "Show solution" answered this step with nothing; `answerShown`: its answer was shown before a reload.
  const shown = revealed || answerShown;
  const tone = !result ? "open" : result.score >= 1 ? "correct" : result.score > 0 ? "partial" : shown ? "neutral" : "incorrect";
  const marks = result && isChoice ? choiceMarksFromText(choices, choice, revealed ? null : result.correct, result.answer_display) : [];
  const sentTf = revealed ? null : tfResponse;
  const tfKey = isTfSet && result ? resolveTfKey(result.answer_display, result.feedback, sentTf, result.correct, choices.length) : null;
  const tfResults = isTfSet && result && !revealed ? tfRowResults(sentTf, tfKey, result.correct, choices.length) : undefined;
  const feedback = result ? stripStockVerdict(result.feedback) : "";
  const verdict =
    tone === "correct"
      ? "Right"
      : tone === "partial"
        ? "Right"
        : tone === "neutral"
          ? result?.correct
            ? "Right — after the answer was shown"
            : "Solution shown"
          : "Not quite";

  return (
    <section className="step-card reveal" data-tone={tone} aria-labelledby={`step-${questionId}-${index}`}>
      <form className="step-card__form" onSubmit={submit} noValidate>
        <div className="step-card__head">
          <span className="step-card__label" id={`step-${questionId}-${index}`}>
            Step {index + 1} of {total}
          </span>
        </div>
        <div className="step-card__prompt">
          <Markdown>{step.prompt}</Markdown>
        </div>

        {isChoice ? (
          <ChoiceList
            choices={choices}
            selected={choice}
            onSelect={(i) => {
              setChoice(i);
              setInvalid(null);
            }}
            onSubmit={() => submit()}
            disabled={answered || busy}
            marks={marks}
            variant={kind === "true_false" ? "boolean" : "multiple"}
          />
        ) : isTfSet ? (
          <TrueFalseSetInput
            statements={choices}
            marks={tfMarks}
            onChange={(next) => {
              setTfMarks(next);
              setInvalid(null);
            }}
            onSubmit={() => submit()}
            disabled={answered || busy}
            results={tfResults}
            answerKey={tfKey}
          />
        ) : (
          <MathInput
            value={response}
            onChange={(value) => {
              setResponse(value);
              setInvalid(null);
            }}
            disabled={answered || busy}
            kind={kind}
            autoFocus={finePointer && !answered}
            compact
            label={`Your answer to step ${index + 1}`}
          />
        )}

        {hint !== null && (
          <div className="hint step-card__hint reveal">
            <span className="hint__label">
              Hint<span className="hint__cost"> · ½ point for this step</span>
            </span>
            <Markdown>{hint}</Markdown>
          </div>
        )}

        {answerShown && !answered && (
          <p className="step-card__note">
            <Icon name="eye" size={15} /> This step's answer was already shown, so it earns 0 points.
          </p>
        )}

        {invalid && !answered && (
          <p className="step-card__invalid" role="alert">
            <Icon name="info" size={15} /> {invalid}
          </p>
        )}

        {!answered && (
          <div className="step-card__actions">
            <button type="submit" className="btn btn--primary" disabled={!canSubmit}>
              {submitting === "answer" && <Spinner size={14} label="Checking" />} Check step
            </button>
            <div className="question-card__tools">
              {step.hint.trim() && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => void showHint()}
                  disabled={!canHint}
                  title={hint === null ? "With the hint, this step earns ½ point" : undefined}
                >
                  {hinting ? <Spinner size={14} label="Loading hint" /> : <Icon name="bulb" size={15} />}
                  {hint === null ? "Hint" : "Hint shown"}
                  {hint === null && <kbd>H</kbd>}
                </button>
              )}
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => void send(true)}
                disabled={busy}
                title="Shows this step's result — the step earns 0 points"
              >
                {submitting === "reveal" ? <Spinner size={14} label="Loading the solution" /> : <Icon name="eye" size={15} />} Show solution
              </button>
            </div>
          </div>
        )}
      </form>

      <div className="step-card__live" aria-live="polite">
        {result && (
          <div ref={resultRef} className="step-card__result reveal">
            <p className="step-card__verdict">
              <Icon name={tone === "correct" || tone === "partial" ? "check" : tone === "neutral" ? "eye" : "x"} size={16} strokeWidth={2.25} />
              <span>{verdict}</span>
              <span className="step-card__points">{stepPointsLabel(result.score, shown)}</span>
            </p>
            {/* A right step's feedback only restates the points line ("With the hint, so half a point"). */}
            {feedback && !shown && !result.correct && <Markdown className="step-card__feedback">{feedback}</Markdown>}
            {result.answer_display.trim() && !(isTfSet && tfKey && !revealed) && (
              <div className="step-card__answer">
                <span className="step-card__answer-label">Result</span>
                {isTfSet && tfKey ? <TfKeyList answerKey={tfKey} /> : <Markdown inline>{result.answer_display}</Markdown>}
              </div>
            )}
            {result.explanation.trim() && <Markdown className="step-card__explanation">{result.explanation}</Markdown>}
            {!last && !done && (
              <div className="step-card__next">
                <button ref={nextRef} type="button" className="btn btn--primary" onClick={onNext}>
                  Next step <Icon name="arrowRight" size={16} />
                </button>
                <span className="feedback__hint">
                  <kbd>Enter</kbd>
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
