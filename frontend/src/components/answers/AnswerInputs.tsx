import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import { asciiToLatex } from "../../lib/mathInput";
import { encodeTfMarks, setTfMark, tfKeyAction, type RowResult, type TfLetter, type TfMark } from "../../lib/trueFalseSet";
import { Icon } from "../Icon";
import { Markdown } from "../Markdown";
import { Tex } from "../Tex";
import "./AnswerInputs.css";

// Answer inputs shared by practice questions (QuestionCard) and the inline checks of v2 lessons.

export type ChoiceMark = "correct" | "incorrect" | null;

interface ChoiceListProps {
  choices: string[];
  selected: number | null;
  onSelect: (index: number) => void;
  /** Enter on a focused choice submits instead of re-selecting it. */
  onSubmit: () => void;
  disabled: boolean;
  marks: ChoiceMark[];
  variant: "multiple" | "boolean";
}

export function ChoiceList({ choices, selected, onSelect, onSubmit, disabled, marks, variant }: ChoiceListProps) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" && selected !== null && !disabled) {
      event.preventDefault();
      onSubmit();
    }
  };
  return (
    <div className={`choices choices--${variant}`} role="radiogroup" aria-label="Answer choices" onKeyDown={onKeyDown}>
      {choices.map((choice, i) => (
        <button
          key={i}
          type="button"
          role="radio"
          aria-checked={selected === i}
          className="choice"
          data-mark={marks[i] ?? undefined}
          onClick={() => onSelect(i)}
          disabled={disabled}
        >
          <span className="choice__key" aria-hidden="true">
            {i + 1}
          </span>
          <span className="choice__text">
            {variant === "boolean" ? choice : <Markdown inline>{choice}</Markdown>}
          </span>
        </button>
      ))}
    </div>
  );
}

interface TrueFalseSetInputProps {
  /** The statements, in order; the response is one letter per statement ("FTT"). */
  statements: string[];
  marks: TfMark[];
  onChange: (marks: TfMark[]) => void;
  /** Enter on a row, once every statement is marked. */
  onSubmit: () => void;
  disabled: boolean;
  /** After grading: right / wrong per statement (null = not known). */
  results?: RowResult[];
  /** The answer key when known: a wrong row also shows the right verdict. */
  answerKey?: TfLetter[] | null;
  autoFocus?: boolean;
}

const VERDICTS: { value: TfLetter; label: string }[] = [
  { value: "T", label: "True" },
  { value: "F", label: "False" },
];

/** "Mark each statement true or false": one True / False switch per statement. Keys: T / F (then the next row), arrows. */
export function TrueFalseSetInput({ statements, marks, onChange, onSubmit, disabled, results, answerKey = null, autoFocus = false }: TrueFalseSetInputProps) {
  const id = useId();
  const groups = useRef<(HTMLDivElement | null)[]>([]);
  const complete = encodeTfMarks(marks) !== null;

  const focusIn = (row: number, value?: TfLetter) => {
    const group = groups.current[row];
    const target = value ? group?.querySelector<HTMLButtonElement>(`[data-value="${value}"]`) : group?.querySelector<HTMLButtonElement>('[tabindex="0"]');
    target?.focus({ preventScroll: false });
  };

  useEffect(() => {
    if (!autoFocus) return;
    groups.current[0]?.querySelector<HTMLButtonElement>('[tabindex="0"]')?.focus({ preventScroll: true });
  }, [autoFocus]);

  const onKeyDown = (row: number) => (event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "Enter") {
      event.preventDefault();
      if (complete) onSubmit();
      return;
    }
    const next = tfKeyAction(event.key, marks[row] ?? null);
    if (!next) return;
    event.preventDefault();
    onChange(setTfMark(marks, row, next));
    // A letter answers this row and moves on; an arrow only switches the answer of the focused row.
    if (event.key.length === 1 && row + 1 < statements.length) focusIn(row + 1);
    else focusIn(row, next);
  };

  return (
    <ol className="tfset" aria-label="Mark each statement true or false" data-graded={results ? "" : undefined}>
      {statements.map((statement, i) => {
        const mark = marks[i] ?? null;
        const result = results?.[i] ?? null;
        const truth = answerKey?.[i] ?? null;
        const numId = `${id}-n${i}`;
        const textId = `${id}-s${i}`;
        return (
          <li key={i} className="tfset__row" data-result={result ?? undefined}>
            <span className="tfset__num" id={numId}>
              <span className="visually-hidden">Statement </span>
              {i + 1}
            </span>
            <div className="tfset__statement" id={textId}>
              <Markdown inline>{statement}</Markdown>
            </div>
            <div
              ref={(el) => {
                groups.current[i] = el;
              }}
              className="tfset__toggle"
              role="radiogroup"
              aria-labelledby={`${numId} ${textId}`}
              onKeyDown={onKeyDown(i)}
            >
              {VERDICTS.map(({ value, label }) => {
                const chosen = mark === value;
                // After grading: the learner's pick is coloured right/wrong; on a wrong row the key's verdict is outlined.
                const tone = result && chosen ? result : result === "incorrect" && truth === value ? "key" : undefined;
                return (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={chosen}
                    tabIndex={(mark ?? "T") === value ? 0 : -1}
                    data-value={value}
                    data-tone={tone}
                    className="tfset__option"
                    onClick={() => onChange(setTfMark(marks, i, value))}
                    disabled={disabled}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            {result && (
              <span className="tfset__verdict">
                <Icon name={result === "correct" ? "check" : "x"} size={14} strokeWidth={2.25} />
                {result === "correct"
                  ? "Right"
                  : truth
                    ? `Marked ${mark === "T" ? "true" : "false"} — it's ${truth === "T" ? "true" : "false"}`
                    : "Marked wrong"}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** A true/false key as a compact list ("1 False · 2 True · 3 True"); never Markdown, so "1." can't become a list. */
export function TfKeyList({ answerKey }: { answerKey: TfLetter[] }) {
  return (
    <ol className="tfkey" aria-label="Answer key">
      {answerKey.map((value, i) => (
        <li key={i} className="tfkey__item" data-value={value}>
          <span className="tfkey__n">{i + 1}</span>
          {value === "T" ? "True" : "False"}
        </li>
      ))}
    </ol>
  );
}

const PALETTE: { label: string; insert: string; caret?: number; title: string }[] = [
  { label: "√", insert: "sqrt()", caret: 5, title: "Square root" },
  { label: "xⁿ", insert: "^", title: "Power" },
  { label: "a⁄b", insert: "/", title: "Fraction" },
  { label: "i", insert: "i", title: "Imaginary unit" },
  { label: "π", insert: "pi", title: "Pi" },
  { label: "( , )", insert: "(, )", caret: 1, title: "List / tuple" },
  { label: "∅", insert: "∅", title: "Empty set (no value works)" },
];

interface MathInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  kind: "numeric" | "expression";
  autoFocus: boolean;
  /** Inline checks: no input help line under the field. */
  compact?: boolean;
  label?: string;
}

/** Calculator-style answer box with a live KaTeX preview of what was typed. */
export function MathInput({ value, onChange, disabled, kind, autoFocus, compact = false, label = "Your answer" }: MathInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const id = useId();
  const latex = asciiToLatex(value);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus({ preventScroll: true });
  }, [autoFocus]);

  const insert = (text: string, caret = text.length) => {
    const input = inputRef.current;
    if (!input) return;
    const start = input.selectionStart ?? value.length;
    const end = input.selectionEnd ?? value.length;
    const next = value.slice(0, start) + text + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      input.focus();
      input.setSelectionRange(start + caret, start + caret);
    });
  };

  return (
    <div className={`math-input${compact ? " math-input--compact" : ""}`}>
      <label htmlFor={id} className="visually-hidden">
        {label}
      </label>
      <input
        ref={inputRef}
        id={id}
        className="input math-input__field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        placeholder={kind === "numeric" ? "e.g. 2/3 or -1.5" : "e.g. -7 + 22i or (1, -2, 3)"}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        aria-describedby={compact ? `${id}-preview` : `${id}-help ${id}-preview`}
      />
      {!disabled && (
        <div className="math-input__palette" role="group" aria-label="Insert symbol">
          {PALETTE.filter((p) => kind === "expression" || ["√", "a⁄b", "π", "xⁿ"].includes(p.label)).map((p) => (
            <button key={p.label} type="button" className="math-input__key" onClick={() => insert(p.insert, p.caret)} title={p.title}>
              {p.label}
            </button>
          ))}
        </div>
      )}
      <div id={`${id}-preview`} className="math-input__preview" aria-live="polite">
        <span className="math-input__preview-label">Preview</span>
        {value.trim() === "" ? (
          <span className="muted">Your answer will appear here as math.</span>
        ) : latex ? (
          <Tex tex={latex} />
        ) : (
          <span className="math-input__raw">{value}</span>
        )}
      </div>
      {!compact && (
        <p id={`${id}-help`} className="field__hint">
          Use <code>i</code> for √−1 and <code>(a, b)</code> for lists. Powers with <code>^</code>, roots with <code>sqrt()</code>,
          and <code>{"{}"}</code> or ∅ for the empty set.
        </p>
      )}
    </div>
  );
}

interface FreeResponseProps {
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
  onCommit: () => void;
  autoFocus: boolean;
  /** Short advice shown under the field (e.g. the answer is too short to count as an attempt). */
  note?: string | null;
}

export function FreeResponseInput({ value, onChange, readOnly, onCommit, autoFocus, note = null }: FreeResponseProps) {
  const id = useId();
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (autoFocus) ref.current?.focus({ preventScroll: true });
  }, [autoFocus]);
  return (
    <div className="free-response">
      <label htmlFor={id} className="field__label">
        Your answer
      </label>
      <textarea
        ref={ref}
        id={id}
        className="textarea free-response__field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        readOnly={readOnly}
        rows={7}
        placeholder="Write your argument in words and symbols. $…$ renders as math."
        aria-describedby={note ? `${id}-note` : undefined}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            onCommit();
          }
        }}
      />
      {note && (
        <p id={`${id}-note`} className="field__hint free-response__note">
          {note}
        </p>
      )}
      {value.includes("$") && (
        <div className="free-response__preview">
          <span className="math-input__preview-label">Preview</span>
          <Markdown>{value}</Markdown>
        </div>
      )}
    </div>
  );
}
