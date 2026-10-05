// Grader feedback as shown under a verdict title ("Correct", "Not quite", "Right", …).

/** A leading stock verdict sentence: "Correct.", "Not quite.", "Not this option.", "Compare with the explanation."
 * Only a whole sentence (ending in "." or the end of the text) — "Correct — equivalent to …" carries information. */
const STOCK_VERDICT = /^\s*(?:correct|not quite|not this option|compare with the explanation)(?:\s*[.!](?=\s|$)|\s*$)\s*/i;

/**
 * The feedback without a leading stock verdict, which only repeats the title shown above it ("Not quite" + "Not
 * quite."). What follows it ("Correct. With help, so …") is kept. Empty when the feedback was only the verdict.
 */
export const stripStockVerdict = (feedback: string): string => feedback.replace(STOCK_VERDICT, "").trim();

/** 409 from an inline check or block feedback: the lesson changed on the server since this page loaded it. */
export const STALE_LESSON_MESSAGE = "This lesson was updated — reload to see the new version.";
