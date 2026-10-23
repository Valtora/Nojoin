import { interpolatedTokens, type DrawnLine } from "./selectionRange";

/**
 * Finds the words a selection note is on in the transcript as it reads now.
 *
 * A note keeps the span the words were selected at and a snapshot of the
 * words (`start_ms`, `end_ms`, `quoted_text`). Reprocessing rebuilds the
 * transcript, and the final pass writes different words from the live one,
 * so the quote is looked for again at read time, and only near its span:
 * among the words whose time falls within `marginMs` of it. Nothing found here
 * is written back; the note keeps what the user saw.
 *
 * Words are compared case-folded with punctuation stripped. A selection may
 * start or end inside a word, and its quote keeps just the characters chosen
 * (B3c), so the quote's first word also matches a word that ends with it, its
 * last a word that starts with it, and a one-word quote any word holding it;
 * such a match is no edit, but a whole word wins a tie over one. The match is
 * the run of words with the fewest word edits from the quote (approximate
 * substring matching, Sellers), scored `1 - edits / quote words`; a tie goes
 * to the run nearest the span's middle. A score of 1 is `exact`; at least
 * `floor` is `fuzzy`; anything lower is `unanchored`, and nothing is marked:
 * a highlight on the wrong words would be worse than none. Word times are
 * interpolated across each line (`interpolatedTokens`), as a selection's are.
 */

export type AnchorState = "exact" | "fuzzy" | "unanchored";

/** Characters `from`..`to` (exclusive) of one line's text. */
export interface LineRange {
  segmentId: string;
  from: number;
  to: number;
}

export interface Resolution {
  state: AnchorState;
  /** The words matched, one range per line, in time order; empty when unanchored. */
  ranges: LineRange[];
  /** `1 - edits / quote words`, 0 when nothing matched. */
  score: number;
  /** The words now within the note's own span, for an unanchored note to show. */
  bestGuess: LineRange[];
}

/** What resolution reads of a note. */
export interface AnchoredNote {
  start_ms?: number | null;
  end_ms?: number | null;
  quoted_text?: string | null;
}

/**
 * How far either side of its span a note's words are looked for, and the
 * lowest score a match is shown at. Chosen on 7,500 selections of live and
 * processed text of three benchmark recordings, resolved on their final and
 * reprocessed text (tuning data, not a held-out test): past 2 s a wider
 * margin finds almost nothing more and marks more wrong words; a floor of 0.6
 * would refuse 1.6% more, nearly all of them right (two-word quotes with one
 * word changed), and one of 0.4 adds nothing.
 */
export const ANCHOR_MARGIN_MS = 2_000;
export const ANCHOR_SCORE_FLOOR = 0.5;

export interface ResolveOptions {
  marginMs?: number;
  floor?: number;
}

interface WindowToken {
  segmentId: string;
  from: number;
  to: number;
  startMs: number;
  endMs: number;
  word: string;
}

/** A word as compared: case-folded, letters and digits only. */
export const comparableWord = (word: string): string =>
  word.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

const comparableWords = (text: string): string[] =>
  text
    .split(/\s+/)
    .map(comparableWord)
    .filter((word) => word !== "");

/** Every word of `lines` timed within `[fromMs, toMs]`, lines in time order. */
const tokensWithin = (lines: readonly DrawnLine[], fromMs: number, toMs: number): WindowToken[] =>
  lines
    .map((line, index) => ({ line, index }))
    // A word's time is within its line's span, so no other line has one here.
    .filter(({ line }) => line.endMs >= fromMs && line.startMs <= toMs)
    .sort((left, right) => left.line.startMs - right.line.startMs || left.index - right.index)
    .flatMap(({ line }) =>
      interpolatedTokens(line.text, line.startMs, line.endMs)
        .filter((token) => token.endMs >= fromMs && token.startMs <= toMs)
        .map((token) => ({
          segmentId: line.segmentId,
          from: token.from,
          to: token.to,
          startMs: token.startMs,
          endMs: token.endMs,
          word: comparableWord(token.text),
        }))
        .filter((token) => token.word !== ""),
    );

/** Tokens as one range per line, in the order given. */
const rangesOf = (tokens: readonly WindowToken[]): LineRange[] => {
  const ranges: LineRange[] = [];
  for (const token of tokens) {
    const last = ranges[ranges.length - 1];
    if (last && last.segmentId === token.segmentId) last.to = token.to;
    else ranges.push({ segmentId: token.segmentId, from: token.from, to: token.to });
  }
  return ranges;
};

interface Match {
  start: number;
  end: number;
  edits: number;
}

// Costs in the match: an edit outweighs every part-word match a run can hold
// (two at most), so part-word matches only break ties between equal edits.
const EDIT = 4;
const PART_WORD = 1;

/** What matching quote word `index` of `count` to `word` costs. */
const wordCost = (quoteWord: string, index: number, count: number, word: string): number => {
  if (quoteWord === word) return 0;
  const partWord =
    count === 1
      ? word.includes(quoteWord)
      : (index === 0 && word.endsWith(quoteWord)) || (index === count - 1 && word.startsWith(quoteWord));
  return partWord ? PART_WORD : EDIT;
};

/**
 * The run `tokens[start..end)` with the fewest word edits from `quote`
 * (Sellers' approximate substring match), ties to the run with fewer
 * part-word matches, then to the one whose middle is nearest `midMs`.
 */
const bestMatch = (quote: readonly string[], tokens: readonly WindowToken[], midMs: number): Match => {
  const width = tokens.length + 1;
  // Row i: the cost of matching quote[0..i) ending at each token; a match may start anywhere.
  let edits = new Array<number>(width).fill(0);
  let starts = Array.from({ length: width }, (_, end) => end);
  for (let i = 1; i <= quote.length; i += 1) {
    const nextEdits = new Array<number>(width);
    const nextStarts = new Array<number>(width);
    nextEdits[0] = i * EDIT;
    nextStarts[0] = 0;
    for (let j = 1; j < width; j += 1) {
      const substitute = edits[j - 1] + wordCost(quote[i - 1], i - 1, quote.length, tokens[j - 1].word);
      const skipQuoteWord = edits[j] + EDIT;
      const extraWord = nextEdits[j - 1] + EDIT;
      if (substitute <= skipQuoteWord && substitute <= extraWord) {
        nextEdits[j] = substitute;
        nextStarts[j] = starts[j - 1];
      } else if (skipQuoteWord <= extraWord) {
        nextEdits[j] = skipQuoteWord;
        nextStarts[j] = starts[j];
      } else {
        nextEdits[j] = extraWord;
        nextStarts[j] = nextStarts[j - 1];
      }
    }
    edits = nextEdits;
    starts = nextStarts;
  }
  let best = { start: 0, end: 0, cost: quote.length * EDIT };
  let bestDistance = Infinity;
  for (let end = 1; end < width; end += 1) {
    const start = starts[end];
    if (start >= end || edits[end] > best.cost) continue;
    const distance = Math.abs((tokens[start].startMs + tokens[end - 1].endMs) / 2 - midMs);
    if (edits[end] < best.cost || distance < bestDistance) {
      best = { start, end, cost: edits[end] };
      bestDistance = distance;
    }
  }
  return { start: best.start, end: best.end, edits: Math.floor(best.cost / EDIT) };
};

/** Where a selection note's words are now in `lines`: exact, fuzzy, or nowhere. */
export function resolveSelection(
  note: AnchoredNote,
  lines: readonly DrawnLine[],
  { marginMs = ANCHOR_MARGIN_MS, floor = ANCHOR_SCORE_FLOOR }: ResolveOptions = {},
): Resolution {
  const startMs = note.start_ms ?? 0;
  const endMs = Math.max(startMs, note.end_ms ?? startMs);
  const bestGuess = rangesOf(tokensWithin(lines, startMs, endMs));
  const unanchored: Resolution = { state: "unanchored", ranges: [], score: 0, bestGuess };
  const quote = comparableWords(note.quoted_text ?? "");
  const tokens = tokensWithin(lines, startMs - marginMs, endMs + marginMs);
  if (quote.length === 0 || tokens.length === 0) return unanchored;

  const match = bestMatch(quote, tokens, (startMs + endMs) / 2);
  const score = Math.max(0, 1 - match.edits / quote.length);
  const state: AnchorState =
    match.edits === 0 ? "exact" : score >= floor ? "fuzzy" : "unanchored";
  if (state === "unanchored") return { ...unanchored, score };
  return { state, ranges: rangesOf(tokens.slice(match.start, match.end)), score, bestGuess };
}
