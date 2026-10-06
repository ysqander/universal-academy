import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { checkBlock, isApiError } from "../../api";
import { useToast } from "../../context/toast";
import { choiceMarksFromText } from "../../lib/choices";
import { stripStockVerdict } from "../../lib/feedback";
import type { CheckOutcome } from "../../lib/lessonFlow";
import { decodeTfResponse, encodeTfMarks, resolveTfKey, tfRowResults, wrongRowsLabel, type TfMark } from "../../lib/trueFalseSet";
import type { BlockCheckRequest, BlockCheckResult, InlineCheckPublic } from "../../types";
import { ChoiceList, MathInput, TrueFalseSetInput, type ChoiceMark } from "../answers/AnswerInputs";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";
import { Spinner } from "../Spinner";
import { StaleLessonNotice } from "./StaleLessonNotice";

interface InlineCheckProps {
  skillId: string;
  /** Lesson.version: sent with the answer, so a lesson regenerated since this page loaded answers 409, not a
   * verdict for some other block. Empty = not known (sent without one). */
  version: string;
  blockIndex: number;
  check: InlineCheckPublic;
  /** Already answered in this session (restored), else null. */
  outcome: CheckOutcome | null;
  onAnswered: (outcome: CheckOutcome) => void;
  /** "Quick check", "Your step", … */
  label: string;
}

/** Inline checks return the answer as text only: find the matching option to mark it. */
const inlineChoiceMarks = (choices: string[], chosen: number | null, result: BlockCheckResult | null): ChoiceMark[] =>
  result ? choiceMarksFromText(choices, chosen, result.correct, result.answer_display) : [];

/**
 * A formative check inside the teaching sequence: graded by the server, never recorded or scored. Every kind of
 * practice answer works here; "reflect" asks the learner to think it through, then reveals one way to say it.
 */
export function InlineCheck({ skillId, version, blockIndex, check, outcome, onAnswered, label }: InlineCheckProps) {
  const toast = useToast();
  const promptId = useId();
  const kind = check.kind;
  const choices = kind === "true_false" && check.choices.length !== 2 ? ["True", "False"] : check.choices;
  const [choice, setChoice] = useState<number | null>(outcome?.choice ?? null);
  const [response, setResponse] = useState(kind === "numeric" || kind === "expression" ? (outcome?.response ?? "") : "");
  const [tfMarks, setTfMarks] = useState<TfMark[]>(() => (kind === "true_false_set" ? decodeTfResponse(outcome?.response, choices.length) : []));
  const [submitting, setSubmitting] = useState(false);
  // A response the grader can't take yet ("simplify it", "mark every statement"): said here, not in a toast.
  const [invalid, setInvalid] = useState<string | null>(null);
  // 409: the lesson changed on the server since this page loaded it; only a reload helps.
  const [stale, setStale] = useState(false);
  const result = outcome?.result ?? null;
  const answered = result !== null;
  const formRef = useRef<HTMLFormElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  // Where focus was when the answer went off: the controls are disabled while it is checked, which drops focus.
  const focusBefore = useRef<HTMLElement | null>(null);
  const answeredHere = useRef(false);

  const isChoice = kind === "multiple_choice" || kind === "true_false";
  const tfResponse = kind === "true_false_set" ? encodeTfMarks(tfMarks) : null;
  const ready =
    kind === "reflect" ? true : isChoice ? choice !== null : kind === "true_false_set" ? tfResponse !== null : response.trim().length > 0;

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!ready || submitting || answered || stale) return;
    const body: BlockCheckRequest =
      kind === "reflect"
        ? {}
        : isChoice
          ? { choice }
          : { response: kind === "true_false_set" ? tfResponse : response.trim() };
    const active = document.activeElement;
    focusBefore.current = active instanceof HTMLElement && formRef.current?.contains(active) ? active : null;
    setSubmitting(true);
    setInvalid(null);
    try {
      const graded = await checkBlock(skillId, blockIndex, version ? { ...body, version } : body);
      answeredHere.current = true;
      onAnswered({ response: body.response ?? null, choice: body.choice ?? null, result: graded });
    } catch (err) {
      if (isApiError(err, 409)) setStale(true);
      else if (isApiError(err, 422)) setInvalid(err.message);
      else toast.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  // Answered: the controls are now disabled, so focus moves to the verdict (keyboard and screen-reader users keep
  // their place; Tab continues into the steps the answer revealed). Not answered (invalid, error): back to where it was.
  useEffect(() => {
    if (submitting) return;
    if (answered && answeredHere.current) {
      answeredHere.current = false;
      focusBefore.current = null;
      const target = resultRef.current;
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    const previous = focusBefore.current;
    focusBefore.current = null;
    if (!stale && previous?.isConnected && !(previous as HTMLButtonElement).disabled && document.activeElement === document.body) {
      previous.focus({ preventScroll: true });
    }
  }, [submitting, answered, stale]);

  // Number keys pick an option, as in practice (the options show 1, 2, …): anywhere in this check, never in a text field.
  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (!isChoice || answered || submitting || stale || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (!/^[1-9]$/.test(event.key)) return;
    const target = event.target instanceof HTMLElement ? event.target : null;
    if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
    const index = Number(event.key) - 1;
    if (index >= choices.length) return;
    event.preventDefault();
    setChoice(index);
    setInvalid(null);
    // Roving focus within the options follows the pick (Enter then checks it).
    if (target?.getAttribute("role") === "radio") formRef.current?.querySelectorAll<HTMLElement>('[role="radio"]')[index]?.focus();
  };

  const tfKey =
    kind === "true_false_set" && result ? resolveTfKey(result.answer_display, result.feedback, outcome?.response, result.correct, choices.length) : null;
  const tfResults = kind === "true_false_set" && result ? tfRowResults(outcome?.response, tfKey, result.correct, choices.length) : undefined;
  const wrongRows = tfResults ? wrongRowsLabel(tfResults) : null;
  const tone = !result ? "open" : result.correct === null ? "neutral" : result.correct ? "correct" : "incorrect";
  // The answer line is redundant when the statements already show the key, or when the learner's answer was right.
  const showAnswer = Boolean(result && result.answer_display.trim() && result.correct !== true && !tfKey);
  // The grader's stock lines ("Correct.", "Not quite.", "Compare with the explanation.") only repeat the verdict.
  const feedback = result ? stripStockVerdict(result.feedback) : "";

  return (
    <form
      ref={formRef}
      className="inline-check"
      data-tone={tone}
      data-kind={kind}
      onSubmit={submit}
      onKeyDown={onKeyDown}
      noValidate
      aria-labelledby={promptId}
    >
      <div className="inline-check__label">
        <Icon name={kind === "reflect" ? "bulb" : "target"} size={14} /> {label}
      </div>
      <div id={promptId} className="inline-check__prompt">
        <Markdown>{check.prompt}</Markdown>
      </div>

      {isChoice ? (
        <ChoiceList
          choices={choices}
          selected={choice}
          onSelect={(index) => {
            setChoice(index);
            setInvalid(null);
          }}
          onSubmit={() => void submit()}
          disabled={answered || submitting || stale}
          marks={inlineChoiceMarks(choices, choice, result)}
          variant={kind === "true_false" ? "boolean" : "multiple"}
        />
      ) : kind === "true_false_set" ? (
        <TrueFalseSetInput
          statements={choices}
          marks={tfMarks}
          onChange={(marks) => {
            setTfMarks(marks);
            setInvalid(null);
          }}
          onSubmit={() => void submit()}
          disabled={answered || submitting || stale}
          results={tfResults}
          answerKey={tfKey}
        />
      ) : kind === "numeric" || kind === "expression" ? (
        <MathInput
          value={response}
          onChange={(value) => {
            setResponse(value);
            setInvalid(null);
          }}
          disabled={answered || submitting || stale}
          kind={kind}
          autoFocus={false}
          compact
          label="Your answer to the check"
        />
      ) : null}

      {stale && !answered && <StaleLessonNotice />}

      {invalid && !answered && !stale && (
        <p className="inline-check__invalid" role="alert">
          <Icon name="info" size={15} /> {invalid}
        </p>
      )}

      {!answered && !stale && (
        <div className="inline-check__actions">
          <button type="submit" className={`btn ${kind === "reflect" ? "" : "btn--primary"} btn--sm inline-check__submit`} disabled={!ready || submitting}>
            {submitting ? <Spinner size={14} label="Checking" /> : kind === "reflect" ? <Icon name="eye" size={15} /> : null}
            {kind === "reflect" ? "Think it through, then reveal" : "Check"}
          </button>
          {kind === "reflect" && <span className="inline-check__aside">Say or jot your answer first — nothing is graded.</span>}
        </div>
      )}

      <div className="inline-check__live" aria-live="polite">
        {result && (
          <div ref={resultRef} tabIndex={-1} className="inline-check__result reveal">
            <p className="inline-check__verdict">
              {tone === "correct" ? (
                <Icon name="check" size={16} strokeWidth={2.25} />
              ) : tone === "incorrect" ? (
                <Icon name="x" size={16} strokeWidth={2.25} />
              ) : (
                <Icon name="bulb" size={16} />
              )}
              {tone === "correct" ? "Right" : tone === "incorrect" ? "Not quite" : "Compare with your thinking"}
              {wrongRows && tone === "incorrect" && <span className="visually-hidden">: {wrongRows} marked wrong</span>}
            </p>
            {feedback && <Markdown className="inline-check__feedback">{feedback}</Markdown>}
            {showAnswer && (
              <p className="inline-check__answer">
                <span className="inline-check__answer-label">{tone === "neutral" ? "One way to say it" : "Answer"}</span>
                <Markdown inline>{result.answer_display}</Markdown>
              </p>
            )}
            {result.explanation.trim() && <Markdown className="inline-check__explanation">{result.explanation}</Markdown>}
          </div>
        )}
      </div>
    </form>
  );
}
