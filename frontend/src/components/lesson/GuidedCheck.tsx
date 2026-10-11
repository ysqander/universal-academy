import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { checkGuided, isApiError, revealGuided } from "../../api";
import { useToast } from "../../context/toast";
import {
  guidedChoiceMarks,
  guidedFeedback,
  guidedVerdict,
  isStaleLessonMessage,
  latestState,
  REPORTED_NOTE,
  restoredAnswer,
  scoredTag,
  type GuidedHandlers,
} from "../../lib/guided";
import { decodeTfResponse, emptyTfMarks, encodeTfMarks, parseTfKey, resolveTfKey, tfRowResults, type TfMark } from "../../lib/trueFalseSet";
import type { BlockCheckRequest, GuidedCheckResult, GuidedCheckState, InlineCheckPublic } from "../../types";
import { ChoiceList, MathInput, TfKeyList, TrueFalseSetInput } from "../answers/AnswerInputs";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";
import { Spinner } from "../Spinner";
import { StaleLessonNotice } from "./StaleLessonNotice";

interface GuidedCheckProps {
  /** Lesson.version, required with every answer (409 when the lesson changed since the page loaded it). */
  version: string;
  blockIndex: number;
  check: InlineCheckPublic;
  /** "Quick check", "Your step", … */
  label: string;
  /** The task the step is answered in; null in the lesson preview, where a scored step is not answered. */
  guided: GuidedHandlers | null;
}

/**
 * A scored step of a proof lesson (lib/guided.ts): answered inside the task and recorded by the server, so a reload
 * shows it as it was. Open → answer → done (verdict, points, answer, explanation); a wrong first answer to a typed
 * answer or a statement set gets "not yet", the step's hint and one more try for ½ point. "Show answer" ends it with
 * 0 points. On a retry of the lesson the step is shown answered and is not scored again.
 */
export function GuidedCheck({ version, blockIndex, check, label, guided }: GuidedCheckProps) {
  const toast = useToast();
  const promptId = useId();
  const kind = check.kind;
  const choices = kind === "true_false" && check.choices.length !== 2 ? ["True", "False"] : check.choices;
  const isChoice = kind === "multiple_choice" || kind === "true_false";
  const isTfSet = kind === "true_false_set";
  // The record the last answer returned: shown at once, until the replaced task carries it too.
  const [local, setLocal] = useState<GuidedCheckState | null>(null);
  const state = latestState(check.state, local);
  const status = state?.status ?? "open";
  const done = state !== null && status === "done";
  const scoredNow = state?.scored !== false;
  const [choice, setChoice] = useState<number | null>(() => restoredAnswer(kind, state).choice);
  const [response, setResponse] = useState(() => (isTfSet ? "" : restoredAnswer(kind, state).response));
  const [tfMarks, setTfMarks] = useState<TfMark[]>(() => (isTfSet ? decodeTfResponse(restoredAnswer(kind, state).response, choices.length) : []));
  const [submitting, setSubmitting] = useState<"answer" | "reveal" | null>(null);
  const [confirming, setConfirming] = useState(false);
  // A response the grader can't take yet ("simplify it", "mark every statement"): said here, and not a try.
  const [invalid, setInvalid] = useState<string | null>(null);
  // Any other 409 ("Answer the earlier step first.", a finished task): the server's words.
  const [conflict, setConflict] = useState<string | null>(null);
  // 409 that only a reload fixes (the lesson changed since this page loaded it, …): the server's message.
  const [stale, setStale] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const revealRef = useRef<HTMLButtonElement>(null);
  const keepTryingRef = useRef<HTMLButtonElement>(null);
  // Where focus was when the answer went off: the controls are disabled while it is checked, which drops focus.
  const focusBefore = useRef<HTMLElement | null>(null);
  const settledHere = useRef(false);
  const confirmTouched = useRef(false);

  const busy = submitting !== null;
  const answerable = guided !== null && scoredNow && !done && stale === null;
  // What ended the step (null: the answer was shown instead), for marking the options / statements.
  const sent = done ? state.response : null;
  // Once done the inputs show what was answered; options and statements stay as the context of the answer line.
  const showInputs = answerable || (done && (sent !== null || kind === "multiple_choice" || kind === "true_false" || kind === "true_false_set"));
  const tfResponse = isTfSet ? encodeTfMarks(tfMarks) : null;
  const ready = isChoice ? choice !== null : isTfSet ? tfResponse !== null : response.trim().length > 0;

  const rememberFocus = () => {
    const active = document.activeElement;
    focusBefore.current = active instanceof HTMLElement && formRef.current?.contains(active) ? active : null;
  };

  const apply = (result: GuidedCheckResult) => {
    setLocal(result.check);
    setConfirming(false);
    if (result.check.status === "done") settledHere.current = true;
    // A choice step with a second try is picked again (its first pick stays marked as not right).
    else if (result.check.status === "retry" && isChoice) setChoice(null);
    guided?.onTask(result.task);
  };

  const fail = (err: unknown) => {
    if (isApiError(err, 422)) setInvalid(err.message);
    else if (isApiError(err, 409)) {
      if (isStaleLessonMessage(err.message)) setStale(err.message);
      else setConflict(err.message);
    } else toast.error(err);
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!guided || !answerable || !ready || busy) return;
    const body: BlockCheckRequest = isChoice ? { choice, version } : { response: isTfSet ? tfResponse : response.trim(), version };
    rememberFocus();
    setSubmitting("answer");
    setInvalid(null);
    setConflict(null);
    try {
      apply(await checkGuided(guided.taskId, blockIndex, body));
    } catch (err) {
      fail(err);
    } finally {
      setSubmitting(null);
    }
  };

  const reveal = async () => {
    if (!guided || !answerable || busy) return;
    rememberFocus();
    setSubmitting("reveal");
    setInvalid(null);
    setConflict(null);
    try {
      const result = await revealGuided(guided.taskId, blockIndex, version);
      // Nothing was answered: the inputs show the key, not what was half typed.
      setChoice(null);
      setResponse("");
      setTfMarks(emptyTfMarks(isTfSet ? choices.length : 0));
      apply(result);
    } catch (err) {
      fail(err);
    } finally {
      setSubmitting(null);
    }
  };

  // Done here: the controls are gone, so focus moves to the verdict (keyboard and screen-reader users keep their
  // place; Tab continues into the steps it revealed). Not done (a retry, invalid, error): back to where it was.
  useEffect(() => {
    if (submitting) return;
    if (done && settledHere.current) {
      settledHere.current = false;
      focusBefore.current = null;
      const target = resultRef.current;
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    const previous = focusBefore.current;
    focusBefore.current = null;
    if (stale === null && previous?.isConnected && !(previous as HTMLButtonElement).disabled && document.activeElement === document.body) {
      previous.focus({ preventScroll: true });
    }
  }, [submitting, done, stale]);

  // "Show answer" asks first: focus the safe choice; cancelling returns to the button.
  useEffect(() => {
    if (!confirmTouched.current) return;
    if (confirming) keepTryingRef.current?.focus({ preventScroll: true });
    else if (revealRef.current?.isConnected) revealRef.current.focus({ preventScroll: true });
  }, [confirming]);

  const askReveal = (open: boolean) => {
    confirmTouched.current = true;
    setConfirming(open);
  };

  // Number keys pick an option, as in practice (the options show 1, 2, …): anywhere in this step, never in a text field.
  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key === "Escape" && confirming && !busy) {
      event.preventDefault();
      event.stopPropagation();
      askReveal(false);
      return;
    }
    if (!isChoice || !answerable || busy || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (!/^[1-9]$/.test(event.key)) return;
    const target = event.target instanceof HTMLElement ? event.target : null;
    if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
    const index = Number(event.key) - 1;
    if (index >= choices.length) return;
    event.preventDefault();
    setChoice(index);
    setInvalid(null);
    if (target?.getAttribute("role") === "radio") formRef.current?.querySelectorAll<HTMLElement>('[role="radio"]')[index]?.focus();
  };

  const verdict = guidedVerdict(state);
  const feedback = guidedFeedback(state);
  const tfKey = isTfSet && done ? (sent ? resolveTfKey(state.answer_display, state.feedback, sent, state.correct, choices.length) : parseTfKey(state.answer_display, choices.length)) : null;
  const tfResults = isTfSet && done && sent ? tfRowResults(sent, tfKey, state.correct, choices.length) : undefined;
  // The answer line is redundant when the statements already show the key, or when the learner's answer was right.
  const showAnswer = Boolean(done && state.answer_display.trim() && state.correct !== true && !(tfResults && tfKey));
  const disabled = !answerable || busy;

  return (
    <form
      ref={formRef}
      className="inline-check"
      data-tone={verdict.tone}
      data-kind={kind}
      onSubmit={submit}
      onKeyDown={onKeyDown}
      noValidate
      aria-labelledby={promptId}
    >
      <div className="inline-check__label">
        <Icon name="target" size={14} /> {label}
        {scoredNow && guided && <span className="inline-check__tag">{scoredTag(check)}</span>}
      </div>
      <div id={promptId} className="inline-check__prompt">
        <Markdown>{check.prompt}</Markdown>
      </div>

      {showInputs &&
        (isChoice ? (
          <ChoiceList
            choices={choices}
            selected={choice}
            onSelect={(index) => {
              setChoice(index);
              setInvalid(null);
            }}
            onSubmit={() => void submit()}
            disabled={disabled}
            marks={guidedChoiceMarks(choices, state)}
            variant={kind === "true_false" ? "boolean" : "multiple"}
          />
        ) : isTfSet ? (
          // On a retry the statements are not marked right or wrong: that would give the key away.
          <TrueFalseSetInput
            statements={choices}
            marks={tfMarks}
            onChange={(marks) => {
              setTfMarks(marks);
              setInvalid(null);
            }}
            onSubmit={() => void submit()}
            disabled={disabled}
            results={tfResults}
            answerKey={tfResults ? tfKey : null}
          />
        ) : kind === "numeric" || kind === "expression" ? (
          <MathInput
            value={response}
            onChange={(value) => {
              setResponse(value);
              setInvalid(null);
            }}
            disabled={disabled}
            kind={kind}
            autoFocus={false}
            compact
            label="Your answer to the step"
          />
        ) : null)}

      {!done && !answerable && stale === null && (
        <p className="inline-check__note">
          <Icon name="lock" size={15} />
          {scoredNow ? "Answered in the lesson — this step counts toward your score." : "Not scored this time."}
        </p>
      )}

      {stale !== null && !done && <StaleLessonNotice message={stale} />}

      {(invalid || conflict) && !done && stale === null && (
        <p className="inline-check__invalid" role="alert">
          <Icon name="info" size={15} /> {invalid ?? conflict}
        </p>
      )}

      {answerable && (
        <div className="inline-check__actions">
          <button type="submit" className="btn btn--primary btn--sm inline-check__submit" disabled={!ready || busy}>
            {submitting === "answer" && <Spinner size={14} label="Checking" />}
            {status === "retry" ? "Check again" : "Check"}
          </button>
          {!confirming && (
            <button
              ref={revealRef}
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => askReveal(true)}
              disabled={busy}
              title="Shows the answer — this step earns 0 points"
            >
              <Icon name="eye" size={15} /> Show answer
            </button>
          )}
        </div>
      )}

      {answerable && confirming && (
        <div className="inline-check__confirm reveal" role="group" aria-label="Show the answer?">
          <p className="inline-check__confirm-text">
            <Icon name="eye" size={15} /> Shows the answer — this step earns 0 points.
          </p>
          <div className="inline-check__confirm-actions">
            <button type="button" className="btn btn--sm" onClick={() => void reveal()} disabled={busy}>
              {submitting === "reveal" && <Spinner size={14} label="Loading the answer" />} Show the answer
            </button>
            <button ref={keepTryingRef} type="button" className="btn btn--ghost btn--sm" onClick={() => askReveal(false)} disabled={busy}>
              Keep trying
            </button>
          </div>
        </div>
      )}

      <div className="inline-check__live" aria-live="polite">
        {state && status === "retry" && (
          <div className="inline-check__result reveal">
            <p className="inline-check__verdict">
              <Icon name="x" size={16} strokeWidth={2.25} /> {verdict.title}
            </p>
            {feedback && <Markdown className="inline-check__feedback">{feedback}</Markdown>}
            {state.hint.trim() && (
              <div className="inline-check__hint">
                <span className="inline-check__hint-label">Hint</span>
                <Markdown>{state.hint}</Markdown>
              </div>
            )}
          </div>
        )}
        {done && (
          <div ref={resultRef} tabIndex={-1} className="inline-check__result reveal">
            <p className="inline-check__verdict">
              {verdict.tone === "correct" || (verdict.tone === "partial" && state?.correct === true) ? (
                <Icon name="check" size={16} strokeWidth={2.25} />
              ) : verdict.tone === "partial" ? (
                <Icon name="steps" size={16} strokeWidth={2.25} />
              ) : verdict.tone === "incorrect" ? (
                <Icon name="x" size={16} strokeWidth={2.25} />
              ) : (
                <Icon name={scoredNow ? "eye" : "info"} size={16} />
              )}
              {verdict.title}
              {verdict.points && <span className="inline-check__points">{verdict.points}</span>}
            </p>
            {feedback && <Markdown className="inline-check__feedback">{feedback}</Markdown>}
            {showAnswer && (
              <div className="inline-check__answer">
                <span className="inline-check__answer-label">Answer</span>
                {isTfSet && tfKey ? <TfKeyList answerKey={tfKey} /> : <Markdown inline>{state.answer_display}</Markdown>}
              </div>
            )}
            {state.explanation.trim() && <Markdown className="inline-check__explanation">{state.explanation}</Markdown>}
            {/* A report is kept for review; it never changes the score (else every shown answer could be reported). */}
            {state.reported ? (
              <p className="inline-check__note">
                <Icon name="flag" size={15} /> {REPORTED_NOTE}
              </p>
            ) : (
              guided &&
              scoredNow &&
              !state.voided && (
                <button type="button" className="btn btn--quiet btn--sm inline-check__flag" onClick={() => guided.onFlag(blockIndex)}>
                  <Icon name="flag" size={14} /> Report a wrong answer
                </button>
              )
            )}
          </div>
        )}
      </div>
    </form>
  );
}
