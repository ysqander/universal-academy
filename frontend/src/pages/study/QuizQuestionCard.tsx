import { useEffect, useRef, useState, type FormEvent } from "react";
import { isApiError, submitAnswer } from "../../api";
import { ChoiceList, MathInput, TrueFalseSetInput } from "../../components/answers/AnswerInputs";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/toast";
import { useHotkeys } from "../../hooks/useHotkeys";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useStopwatch } from "../../hooks/useStopwatch";
import { emptyTfMarks, encodeTfMarks, markNextUnmarked, type TfLetter, type TfMark } from "../../lib/trueFalseSet";
import type { AnswerResult, QuestionPublic, Task } from "../../types";
import { FlagDialog } from "./FlagDialog";

interface QuizQuestionCardProps {
  task: Task;
  question: QuestionPublic;
  /** "Question 3 of 7". */
  position: string;
  /** The answer (or the pass) was recorded; `result.task` carries the next question or the finished quiz. */
  onSubmitted: (result: AnswerResult, skipped: boolean) => void;
  /** 409: the clock ran out on the server, or the page is behind — ask the server for the task again. */
  onStale: () => void;
  /** A flagged question is voided; the server's task (with the next question) replaces this one. */
  onTaskReplaced: (task: Task) => void;
}

/**
 * One quiz question (SPEC §6.7): the answer inputs of practice, but no hints, no solution, no easier step, and no
 * right/wrong — the answer is recorded and the next question follows at once. "Pass" skips it (0 points).
 */
export function QuizQuestionCard({ task, question, position, onSubmitted, onStale, onTaskReplaced }: QuizQuestionCardProps) {
  const toast = useToast();
  const finePointer = useMediaQuery("(pointer: fine)");
  const [choice, setChoice] = useState<number | null>(null);
  const [response, setResponse] = useState("");
  const [tfMarks, setTfMarks] = useState<TfMark[]>(() => emptyTfMarks(question.kind === "true_false_set" ? question.choices.length : 0));
  const [sending, setSending] = useState<"answer" | "pass" | null>(null);
  const [flagOpen, setFlagOpen] = useState(false);
  const { elapsed } = useStopwatch(true);
  const cardRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const card = cardRef.current;
    if (card && card.getBoundingClientRect().top < 0) card.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const kind = question.kind;
  const isChoice = kind === "multiple_choice" || kind === "true_false";
  const isTfSet = kind === "true_false_set";
  const isMath = kind === "numeric" || kind === "expression";
  // Quizzes serve single-answer questions only; anything else can only be passed.
  const answerable = isChoice || isTfSet || isMath;
  const choices = kind === "true_false" && question.choices.length !== 2 ? ["True", "False"] : question.choices;
  const tfResponse = isTfSet ? encodeTfMarks(tfMarks) : null;
  const busy = sending !== null;
  const canSubmit = answerable && !busy && (isChoice ? choice !== null : isTfSet ? tfResponse !== null : response.trim().length > 0);

  const send = async (skip: boolean) => {
    if (busy || (!skip && !canSubmit)) return;
    setSending(skip ? "pass" : "answer");
    try {
      const result = await submitAnswer(task.id, {
        question_id: question.id,
        choice: !skip && isChoice ? choice : null,
        response: skip || isChoice ? null : isTfSet ? tfResponse : response,
        hints_used: 0,
        revealed_solution: false,
        seconds: Math.round(elapsed() * 10) / 10,
        skip,
      });
      onSubmitted(result, skip);
      setSending(null);
    } catch (err) {
      setSending(null);
      if (isApiError(err, 409)) {
        // Past the deadline (the quiz is over) or a stale page: the server's task says which.
        toast.show(err.message, "info");
        onStale();
      } else {
        // 422 "simplify" / unreadable input: nothing was recorded — fix the answer and send it again.
        toast.error(err);
      }
    }
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    void send(false);
  };

  const pickByNumber = (n: number) => () => {
    if (busy || !isChoice || n > choices.length) return false;
    setChoice(n - 1);
  };

  const markNext = (mark: TfLetter) => () => {
    if (busy || !isTfSet) return false;
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
      Enter: (event) => {
        if (event.target !== document.body || !canSubmit) return false;
        submit();
      },
    },
    !busy,
  );

  return (
    <article ref={cardRef} className="question-card card quiz-card" aria-labelledby={`q-${question.id}`} aria-busy={sending !== null || undefined}>
      <header className="question-card__head">
        <span className="question-card__kicker">{position}</span>
        <span className="quiz-card__rule">No hints · results at the end</span>
      </header>

      <div id={`q-${question.id}`} className="question-card__prompt">
        <Markdown>{question.prompt}</Markdown>
      </div>

      <form className="question-card__form" onSubmit={submit} noValidate>
        {isChoice ? (
          <ChoiceList
            choices={choices}
            selected={choice}
            onSelect={setChoice}
            onSubmit={() => submit()}
            disabled={busy}
            marks={[]}
            variant={kind === "true_false" ? "boolean" : "multiple"}
          />
        ) : isTfSet ? (
          <TrueFalseSetInput statements={choices} marks={tfMarks} onChange={setTfMarks} onSubmit={() => submit()} disabled={busy} />
        ) : isMath ? (
          <MathInput value={response} onChange={setResponse} disabled={busy} kind={kind} autoFocus={finePointer} />
        ) : (
          <p className="callout callout--info">
            <Icon name="info" size={18} />
            <span>This question can't be answered inside a quiz. Pass it — it won't hold you back.</span>
          </p>
        )}

        <div className="question-card__actions quiz-card__actions">
          <button type="submit" className="btn btn--primary btn--quiz btn--lg" disabled={!canSubmit}>
            {sending === "answer" ? <Spinner size={16} label="Saving" /> : null}
            Submit answer
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => void send(true)}
            disabled={busy}
            title="Skip this question: it scores 0, and you can't come back to it"
          >
            {sending === "pass" ? <Spinner size={14} label="Passing" /> : <Icon name="arrowRight" size={15} />} Pass
          </button>
        </div>
      </form>

      <footer className="question-card__footer">
        <span className="question-card__keys">
          {isChoice ? (
            <>
              <kbd>1</kbd>–<kbd>{choices.length}</kbd> choose · <kbd>Enter</kbd> submit
            </>
          ) : isTfSet ? (
            <>
              <kbd>T</kbd>/<kbd>F</kbd> mark a statement · <kbd>Enter</kbd> submit
            </>
          ) : isMath ? (
            <>
              <kbd>Enter</kbd> submit
            </>
          ) : null}
        </span>
        <button type="button" className="btn btn--quiet btn--sm question-card__flag" onClick={() => setFlagOpen(true)} disabled={busy}>
          <Icon name="flag" size={14} /> This question seems wrong
        </button>
      </footer>

      <FlagDialog
        open={flagOpen}
        questionId={question.id}
        onClose={() => setFlagOpen(false)}
        onFlagged={onTaskReplaced}
        description="The question is set aside and doesn't count toward your score; the quiz moves on. The clock keeps running."
      />
    </article>
  );
}
