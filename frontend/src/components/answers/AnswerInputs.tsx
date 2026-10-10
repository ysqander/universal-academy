import { useEffect, useId, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { asciiToLatex, subscriptRefusal, toggleSubscript, type SubscriptRefusal } from "../../lib/mathInput";
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

// keys that act on a focused palette key itself, never typing (Space is handled on its own)
const NOT_TYPING = new Set(["Tab", "Enter", "Escape", "Shift", "Control", "Alt", "Meta", "CapsLock", "AltGraph", "Fn"]);

type PaletteKey = { label: string; title: string; display?: ReactNode } & (
  | { insert: string; caret?: number }
  | { action: "subscript" }
);

const PALETTE: PaletteKey[] = [
  { label: "√", insert: "sqrt()", caret: 5, title: "Square root" },
  { label: "xⁿ", insert: "^", title: "Power" },
  {
    label: "xₖ",
    display: (
      <>
        x<sub>k</sub>
      </>
    ), // the serif fonts have no ₖ glyph
    action: "subscript",
    title: "Subscript: type the letter and its index (xk), then press to make x_k. Press again to undo.",
  },
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
  // the value at which the subscript key did nothing, and why: the nudge shows until the value changes
  // n counts refused presses, so a second press at the same spot is announced again
  const [nudgeAt, setNudgeAt] = useState<{ value: string; refusal: SubscriptRefusal; n: number } | null>(null);
  const keyMade = useRef<string | null>(null); // the value the subscript key last produced
  const keyedPress = useRef(false); // the subscript key was just pressed from the keyboard (focus stays on it)
  // submitted (the field disabled): an old nudge does not come back when the field is enabled again
  const [wasDisabled, setWasDisabled] = useState(disabled);
  if (wasDisabled !== disabled) {
    setWasDisabled(disabled);
    if (disabled) setNudgeAt(null);
  }
  const nudge = nudgeAt !== null && nudgeAt.value === value && !disabled ? nudgeAt.refusal : null;
  const edit = (next: string) => {
    setNudgeAt(null);
    onChange(next);
  };
  // palette keys keep the field focused (on a phone the keyboard stays open and the caret stays where it was)
  const keepFocus = (event: MouseEvent) => event.preventDefault();

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus({ preventScroll: true });
  }, [autoFocus]);

  const insert = (text: string, caret = text.length) => {
    const input = inputRef.current;
    if (!input) return;
    const start = input.selectionStart ?? value.length;
    const end = input.selectionEnd ?? value.length;
    const next = value.slice(0, start) + text + value.slice(end);
    edit(next);
    input.focus();
    requestAnimationFrame(() => input.setSelectionRange(start + caret, start + caret));
  };

  // The index typed just before the caret becomes a subscript: "xk" -> "x_k" (the grader reads "ax_k" as a·x_k).
  const subscript = (event: MouseEvent) => {
    const input = inputRef.current;
    if (!input) return;
    if (event.detail > 1 && keyMade.current === value) return; // the second click of a double-click: keep the first
    // a click returns to the field; from the keyboard (detail 0) the focus stays on the key, so Enter presses it
    // again (undo) instead of submitting the answer
    if (event.detail > 0) input.focus();
    else keyedPress.current = true;
    const start = input.selectionStart ?? value.length;
    const end = input.selectionEnd ?? value.length;
    const change = toggleSubscript(value, start, end);
    if (!change) {
      setNudgeAt({ value, refusal: subscriptRefusal(value, start, end), n: (nudgeAt?.n ?? 0) + 1 });
      return;
    }
    keyMade.current = change.value;
    edit(change.value);
    requestAnimationFrame(() => input.setSelectionRange(change.caret, change.caret));
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
        onChange={(event) => edit(event.target.value)}
        disabled={disabled}
        placeholder={kind === "numeric" ? "e.g. 2/3 or -1.5" : "e.g. -7 + 22i or (1, -2, 3)"}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        aria-describedby={[nudge ? `${id}-nudge` : "", compact ? "" : `${id}-help`, `${id}-preview`].filter(Boolean).join(" ")}
      />
      {!disabled && (
        <div className="math-input__palette" role="group" aria-label="Insert symbol">
          {PALETTE.filter((p) => kind === "expression" || ["√", "a⁄b", "π", "xⁿ"].includes(p.label)).map((p) => (
            <button
              key={p.label}
              type="button"
              className="math-input__key"
              onMouseDown={keepFocus}
              onKeyDown={(event) => {
                // typing on a key goes on in the field (never to the page's shortcuts: 'h' would take a hint) —
                // letters, Backspace, arrows, dead keys, AltGr characters; a space only right after a keyboard press of
                // the subscript key (otherwise Space presses the key). Tab, Enter, Escape and shortcuts stay as they are.
                const shortcut = event.metaKey || (event.ctrlKey && !event.altKey);
                const space = event.key === " ";
                if (shortcut || NOT_TYPING.has(event.key) || (space && !("action" in p && keyedPress.current))) return;
                inputRef.current?.focus();
              }}
              onBlur={() => {
                keyedPress.current = false;
              }}
              onClick={(event) => ("action" in p ? subscript(event) : insert(p.insert, p.caret))}
              title={p.title}
              aria-label={"action" in p ? "Subscript" : undefined}
            >
              {p.display ?? p.label}
            </button>
          ))}
        </div>
      )}
      {/* always mounted, so screen readers announce the text when it appears */}
      <p id={`${id}-nudge`} className={nudge ? "math-input__nudge" : "visually-hidden"} role="status">
        {nudge && (
          <span key={nudgeAt?.n}>
            <SubscriptAdvice refusal={nudge} />
          </span>
        )}
      </p>
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
          {kind === "expression" && (
            <>
              {" "}
              An index: type <code>xk</code> and press the subscript key{" "}
              <b>
                x<sub>k</sub>
              </b>{" "}
              (or type <code>x_k</code>).
            </>
          )}
        </p>
      )}
    </div>
  );
}

/** Why the subscript key did nothing, and what to do instead (lib/mathInput.ts subscriptRefusal). */
function SubscriptAdvice({ refusal }: { refusal: SubscriptRefusal }) {
  const key = (
    <>
      the subscript key{" "}
      <b>
        x<sub>k</sub>
      </b>
    </>
  );
  switch (refusal.why) {
    case "joins":
      return (
        <>
          The index <code>{refusal.index}</code> would run into <code>{refusal.next}</code> and be read as one name.
          Type a space (or <code>*</code>) after <code>{refusal.index}</code>, put the cursor back right after{" "}
          <code>{refusal.index}</code> and press {key} again.
        </>
      );
    case "reread":
      return (
        <>
          That would change how <code>{refusal.earlier}</code> is read. Type a space (or <code>*</code>) after{" "}
          <code>{refusal.earlier}</code>, put the cursor back at the end of <code>{refusal.at}</code> and press {key}{" "}
          again.
        </>
      );
    case "name":
      if (refusal.called) {
        return (
          <>
            <code>{refusal.name}</code> is applied as a function here, and a function takes no index.
          </>
        );
      }
      return refusal.typedAfter ? (
        <>
          <code>{refusal.name}</code> is one name. Put the cursor right after the index you typed after it, then press{" "}
          {key}.
        </>
      ) : (
        <>
          <code>{refusal.name}</code> is one name: the key turns the letter or number typed right after a name into its
          index. Type the index after it (for example <code>{refusal.name}k</code>), keep the cursor right after the
          index and press {key}.
        </>
      );
    case "number":
      return (
        <>
          A digit right after <code>{refusal.name}</code> is read as a number, not as an index.
        </>
      );
    case "partial":
      return <>That digit is part of a longer number: put the cursor after the whole number, then press {key}.</>;
    case "backslash":
      return (
        <>
          After a LaTeX command type the index yourself, for example <code>\theta_k</code>.
        </>
      );
    case "braces":
      return (
        <>
          Inside <code>{"_{ }"}</code> the key can't add an index: type it yourself.
        </>
      );
    case "other":
      return (
        <>
          The key can't make an index here: the name would run into the names next to it. Separate them with a space
          or <code>*</code> (for example <code>x_1 y_k</code>), put the cursor right after the index and press {key}{" "}
          again.
        </>
      );
    default:
      return (
        <>
          Type the letter and then its index, for example <code>xk</code>, and press {key} right after the index: it
          becomes <Tex tex="x_k" />.
        </>
      );
  }
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
