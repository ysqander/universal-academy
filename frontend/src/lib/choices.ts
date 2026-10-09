// Marking options after grading when the server returns the answer as text only (inline checks, multistep steps).
import type { ChoiceMark } from "../components/answers/AnswerInputs";

const normalize = (text: string): string => text.replace(/[$\s]/g, "").toLowerCase();

/** The option whose text is the answer (-1 when none matches). */
export const keyIndexFromText = (choices: string[], answerDisplay: string): number => {
  const answer = normalize(answerDisplay);
  return choices.findIndex((c) => normalize(c) === answer);
};

/** Right / wrong marks for the options: the chosen one, and the option whose text matches the answer. */
export function choiceMarksFromText(choices: string[], chosen: number | null, correct: boolean | null, answerDisplay: string): ChoiceMark[] {
  if (chosen === null || correct === null) return [];
  const keyIndex = correct ? chosen : keyIndexFromText(choices, answerDisplay);
  return choices.map((_, i) => (i === keyIndex ? "correct" : i === chosen && !correct ? "incorrect" : null));
}
