import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import { Markdown } from "../../components/Markdown";
import { Tex } from "../../components/Tex";
import { asciiToLatex } from "../../lib/mathInput";

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

const PALETTE: { label: string; insert: string; caret?: number; title: string }[] = [
  { label: "√", insert: "sqrt()", caret: 5, title: "Square root" },
  { label: "xⁿ", insert: "^", title: "Power" },
  { label: "a⁄b", insert: "/", title: "Fraction" },
  { label: "i", insert: "i", title: "Imaginary unit" },
  { label: "π", insert: "pi", title: "Pi" },
  { label: "( , )", insert: "(, )", caret: 1, title: "List / tuple" },
];

interface MathInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  kind: "numeric" | "expression";
  autoFocus: boolean;
}

/** Calculator-style answer box with a live KaTeX preview of what was typed. */
export function MathInput({ value, onChange, disabled, kind, autoFocus }: MathInputProps) {
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
    <div className="math-input">
      <label htmlFor={id} className="visually-hidden">
        Your answer
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
        aria-describedby={`${id}-help ${id}-preview`}
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
      <p id={`${id}-help`} className="field__hint">
        Use <code>i</code> for √−1 and <code>(a, b)</code> for lists. Powers with <code>^</code>, roots with <code>sqrt()</code>.
      </p>
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
