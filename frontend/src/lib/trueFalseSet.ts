// "Mark each statement true or false" (QuestionKind / InlineCheck kind "true_false_set").
// The response is one letter per statement in order ("FTT"); the answer key comes back as text in
// AnswerResult.correct_answer / BlockCheckResult.answer_display, so it is read leniently here.

export type TfLetter = "T" | "F";
export type TfMark = TfLetter | null;
export type RowResult = "correct" | "incorrect" | null;

/** Nothing marked yet, one slot per statement. */
export const emptyTfMarks = (count: number): TfMark[] => Array.from({ length: count }, () => null);

/** The response to send ("TFT"), or null while any statement is still unmarked. */
export function encodeTfMarks(marks: readonly TfMark[]): string | null {
  if (marks.length === 0 || marks.some((m) => m === null)) return null;
  return marks.join("");
}

/** A stored response ("TFT") back to marks for `count` statements; anything unreadable stays unmarked. */
export function decodeTfResponse(response: string | null | undefined, count: number): TfMark[] {
  const letters = (response ?? "").toUpperCase().replace(/[^TF]/g, "");
  return Array.from({ length: count }, (_, i) => (letters.length === count ? (letters[i] as TfLetter) : null));
}

/** T / F pressed with nothing focused: mark the first statement still unmarked (unchanged when all are marked). */
export function markNextUnmarked(marks: readonly TfMark[], mark: TfLetter): TfMark[] {
  const row = marks.indexOf(null);
  return row < 0 ? [...marks] : setTfMark(marks, row, mark);
}

/** Set (or clear, with null) the mark of one statement. */
export const setTfMark = (marks: readonly TfMark[], index: number, mark: TfMark): TfMark[] =>
  marks.map((m, i) => (i === index ? mark : m));

const stripMarkup = (text: string): string =>
  text
    .replace(/\\(?:text|mathrm|textbf|mathbf|operatorname)\s*\{([^{}]*)\}/g, " $1 ")
    .replace(/\\[A-Za-z]+/g, " ") // other TeX commands (\quad, \checkmark …) are not letters of the key
    .replace(/[$*_`~]/g, " ");

/**
 * The answer key as one letter per statement, read from the server's answer text: "FTT", "F, T, T", "(F, T, T)",
 * "1. F 2. T 3. T", "False, True, True", "1 — false; 2 — true; 3 — true". Null when the text doesn't name exactly
 * `count` verdicts (then the rows aren't marked individually; the feedback text still names the wrong statements).
 */
export function parseTfKey(text: string | null | undefined, count: number): TfLetter[] | null {
  if (!text || count <= 0) return null;
  const plain = stripMarkup(text);
  const words = plain.match(/\b(?:true|false)\b/gi);
  if (words && words.length === count) return words.map((w) => (w[0].toUpperCase() === "T" ? "T" : "F"));
  if (words && words.length > 0) return null; // prose with verdict words, but not one per statement
  const tokens = plain.split(/[^A-Za-z]+/).filter(Boolean);
  const letters = tokens.join("");
  if (!/^[TF]+$/i.test(letters) || letters.length !== count) return null;
  return Array.from(letters.toUpperCase(), (c) => c as TfLetter);
}

// The grader names exactly the statements marked wrong: "Statement 2 is actually false; statement 3 is actually true."
const ACTUALLY = /\bstatement\s+(\d+)\s+is\s+actually\s+(true|false)\b/gi;

/**
 * The key rebuilt from a graded response: every statement the grader's feedback names as "actually true/false" is
 * the other way, every other statement was marked right. Null when the feedback names none (or an impossible one).
 */
export function tfKeyFromFeedback(feedback: string | null | undefined, response: string | null | undefined, count: number): TfLetter[] | null {
  const marks = decodeTfResponse(response, count);
  if (count <= 0 || marks.some((m) => m === null)) return null;
  const named = Array.from((feedback ?? "").matchAll(ACTUALLY));
  if (named.length === 0) return null;
  const key = marks as TfLetter[];
  for (const [, number, verdict] of named) {
    const row = Number(number) - 1;
    if (!(row >= 0 && row < count)) return null;
    key[row] = verdict[0].toUpperCase() === "T" ? "T" : "F";
  }
  return key;
}

/**
 * The key of a graded true/false set, from the most authoritative source: a fully right response is the key; for a
 * wrong one the grader's feedback (it names exactly the statements marked wrong), else the answer text.
 */
export function resolveTfKey(
  answerText: string | null | undefined,
  feedback: string | null | undefined,
  response: string | null | undefined,
  correct: boolean | null,
  count: number,
): TfLetter[] | null {
  if (correct === true) {
    const marks = decodeTfResponse(response, count);
    if (count > 0 && marks.every((m) => m !== null)) return marks as TfLetter[];
  }
  if (correct === false) {
    const fromFeedback = tfKeyFromFeedback(feedback, response, count);
    if (fromFeedback) return fromFeedback;
  }
  return parseTfKey(answerText, count);
}

/**
 * Per-statement result after grading: compared with the key when it is known; all right when the whole answer
 * was right; otherwise unknown (null) for every row.
 */
export function tfRowResults(response: string | null | undefined, key: readonly TfLetter[] | null, correct: boolean | null, count: number): RowResult[] {
  const marks = decodeTfResponse(response, count);
  if (key && key.length === count) return marks.map((m, i) => (m === null ? null : m === key[i] ? "correct" : "incorrect"));
  if (correct === true) return marks.map((m) => (m === null ? null : "correct"));
  return marks.map(() => null);
}

/** "Statements 1 and 3" style list of the rows marked wrong (for screen-reader summaries). */
export function wrongRowsLabel(results: readonly RowResult[]): string | null {
  const wrong = results.flatMap((r, i) => (r === "incorrect" ? [i + 1] : []));
  if (wrong.length === 0) return null;
  if (wrong.length === 1) return `Statement ${wrong[0]}`;
  return `Statements ${wrong.slice(0, -1).join(", ")} and ${wrong[wrong.length - 1]}`;
}

/** Keyboard on a focused row: T / F set the mark; arrow keys switch between True and False. */
export function tfKeyAction(key: string, current: TfMark): TfMark | undefined {
  const k = key.length === 1 ? key.toUpperCase() : key;
  if (k === "T") return "T";
  if (k === "F") return "F";
  if (k === "ArrowLeft" || k === "ArrowUp") return current === "F" ? "T" : current === "T" ? "F" : "T";
  if (k === "ArrowRight" || k === "ArrowDown") return current === "T" ? "F" : current === "F" ? "T" : "F";
  return undefined;
}
