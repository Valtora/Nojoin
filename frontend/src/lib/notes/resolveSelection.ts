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
 * last a word that starts with it, and a one-word quote any word holding it,
 * when it is `PART_WORD_MIN_CHARS` long or more; such a match is no edit, but
 * a whole word wins a tie over one. The match is the run of words with the
 * fewest word edits from the quote (approximate substring matching, Sellers),
 * scored `1 - edits / quote words`; a tie goes to the run nearest the span's
 * middle. A score of 1 is `exact`; at least `floor` is `fuzzy`; anything lower
 * is `unanchored`, and nothing is marked: a highlight on the wrong words would
 * be worse than none. Word times are interpolated across each line
 * (`interpolatedTokens`), as a selection's are.
 *
 * Speakers' lines that overlap in time are drawn side by side, and a drag over
 * them quotes what it takes of each in time order (#616), which no order of
 * whole lines reproduces: from inside one speaker's line into another's, the
 * second line's first words come before the first's last. So where lines are
 * drawn side by side, a match may move from one line to another as such a drag
 * does (`movesOf`), at a part-word's cost (a tie-break, no edit), and what it
 * takes of each line may start or end inside a word, as the quote's ends may.
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

/**
 * The shortest part of a word a one-word quote is found in, so "a" is never
 * found in "and". On the same selections and 3,000 drags: wrong marks fell
 * from 124 to 119 of the 7,500 and from 39 to 18 of the drags, which leave
 * unmarked 105 drags inside a word that took one or two of its letters.
 */
export const PART_WORD_MIN_CHARS = 3;

export interface ResolveOptions {
  marginMs?: number;
  floor?: number;
}

interface WindowToken {
  segmentId: string;
  /** The token's line, numbered in time order among the lines searched. */
  line: number;
  /** The group of lines overlapping in time its line is drawn in, numbered in time order. */
  group: number;
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

/**
 * Every word of `lines` timed within `[fromMs, toMs]`, lines in time order.
 * A line that starts before the lines of its group so far end is drawn in
 * that group, as the view groups lines (`buildTranscriptGroups`).
 */
const tokensWithin = (lines: readonly DrawnLine[], fromMs: number, toMs: number): WindowToken[] => {
  const tokens: WindowToken[] = [];
  let group = -1;
  let groupEndMs = -Infinity;
  lines
    .map((line, index) => ({ line, index }))
    // A word's time is within its line's span, so no other line has one here.
    .filter(({ line }) => line.endMs >= fromMs && line.startMs <= toMs)
    .sort((left, right) => left.line.startMs - right.line.startMs || left.index - right.index)
    .forEach(({ line }, place) => {
      if (line.startMs < groupEndMs) {
        groupEndMs = Math.max(groupEndMs, line.endMs);
      } else {
        group += 1;
        groupEndMs = line.endMs;
      }
      for (const token of interpolatedTokens(line.text, line.startMs, line.endMs)) {
        const word = comparableWord(token.text);
        if (word === "" || token.endMs < fromMs || token.startMs > toMs) continue;
        const { from, to, startMs, endMs } = token;
        tokens.push({ segmentId: line.segmentId, line: place, group, from, to, startMs, endMs, word });
      }
    });
  return tokens;
};

/** Tokens as one range per line, lines in the order first given. */
const rangesOf = (tokens: readonly WindowToken[]): LineRange[] => {
  const ranges = new Map<string, LineRange>();
  for (const token of tokens) {
    const range = ranges.get(token.segmentId);
    if (range) {
      range.from = Math.min(range.from, token.from);
      range.to = Math.max(range.to, token.to);
    } else {
      ranges.set(token.segmentId, { segmentId: token.segmentId, from: token.from, to: token.to });
    }
  }
  return [...ranges.values()];
};

interface Match {
  /** The words matched, in the quote's order. */
  tokens: WindowToken[];
  edits: number;
}

// Tie-breaks in the match: a part-word match, and a move to another line. A
// match of `count` quote words holds at most `count` of the one and
// `count - 1` of the other, so an edit, `2 * count`, outweighs them all.
const PART_WORD = 1;
const LINE_MOVE = 1;

/**
 * Where a match may move from one line to another, as a drag over lines drawn
 * side by side quotes them: inside a group, from any word of a line to any
 * word of another, and into the next group, from a line's last word to a
 * line's first, past the lines of the group the drag left out. Each line's
 * first and last cells (a cell is a token's index plus one); null when no
 * group holds more than one line, so a match goes along its lines in order.
 */
interface Moves {
  groups: number[][];
  firstCell: Int32Array;
  lastCell: Int32Array;
}

const movesOf = (tokens: readonly WindowToken[]): Moves | null => {
  const groups: number[][] = [];
  const lineCount = tokens[tokens.length - 1].line + 1;
  const firstCell = new Int32Array(lineCount).fill(-1);
  const lastCell = new Int32Array(lineCount).fill(-1);
  tokens.forEach((token, index) => {
    const lines = (groups[token.group] ??= []);
    if (lines[lines.length - 1] !== token.line) lines.push(token.line);
    if (firstCell[token.line] < 0) firstCell[token.line] = index + 1;
    lastCell[token.line] = index + 1;
  });
  return groups.some((lines) => lines.length > 1) ? { groups, firstCell, lastCell } : null;
};

// How a cell was reached, to follow the match back to its words.
const SAME_LINE = 1;
const MOVED = 2;
const SKIPPED = 3;
const EXTRA = 4;

/** One row of the match: per cell, its cost, how it was reached, and its match's first cell (0 for none yet). */
interface Row {
  cost: Float64Array;
  steps: Uint8Array;
  first: Int32Array;
}

const rowOf = (width: number): Row => ({
  cost: new Float64Array(width),
  steps: new Uint8Array(0),
  first: new Int32Array(width),
});

/** `row` emptied for the next row of the match, its steps kept in `steps`. */
const reset = (row: Row, steps: Uint8Array, firstCost: number): Row => {
  row.cost.fill(Infinity);
  row.first.fill(0);
  row.steps = steps;
  row.cost[0] = firstCost;
  row.steps[0] = SKIPPED;
  return row;
};

/**
 * Fills cell `k` of `next`, a row after `open` and `closed`, with the
 * cheapest way into it, the earlier on a tie: along the line for `along`
 * more, by a move from cell `from` of `closed` (-1 for none) for `moved`
 * more, a quote word skipped, or an extra word after cell `k - 1` of
 * `sameRow`, the open row `next` is beside.
 */
const fillCell = (
  next: Row, k: number, open: Row, closed: Row, from: number, along: number, moved: number, edit: number, sameRow: Row,
) => {
  let cost = open.cost[k - 1] + along;
  let step = SAME_LINE;
  let first = open.first[k - 1] || k;
  if (from >= 0 && closed.cost[from] + moved < cost) {
    cost = closed.cost[from] + moved;
    step = MOVED;
    first = closed.first[from] || k;
  }
  if (open.cost[k] + edit < cost) {
    cost = open.cost[k] + edit;
    step = SKIPPED;
    first = open.first[k];
  }
  if (sameRow.cost[k - 1] + edit < cost) {
    cost = sameRow.cost[k - 1] + edit;
    step = EXTRA;
    first = sameRow.first[k - 1] || k;
  }
  next.cost[k] = cost;
  next.steps[k] = step;
  next.first[k] = first;
};

/** Of cells `a` and `b` of `row` (-1 for none), the cheaper. */
const cheaper = (a: number, b: number, row: Row) => (b >= 0 && (a < 0 || row.cost[b] < row.cost[a]) ? b : a);

/**
 * The words of `tokens` with the fewest word edits from `quote` (Sellers'
 * approximate substring match), along its lines and moving between them as
 * `movesOf` allows; ties to the match with fewer part-word matches and moves,
 * then to the one whose middle is nearest `midMs`.
 *
 * Each cell is scored `open`, its quote word going on along the line, and,
 * where it may differ, `closed`, its quote word ending what the match takes
 * of the line, so that it may be the start of a word. A move leaves a
 * `closed` cell.
 */
const bestMatch = (quote: readonly string[], tokens: readonly WindowToken[], midMs: number): Match => {
  const count = quote.length;
  const width = tokens.length + 1;
  const edit = 2 * count;
  const alone = count === 1;
  const moves = movesOf(tokens);
  // Per row: how each cell was reached, and the cell a move came from.
  const openSteps: Uint8Array[] = [new Uint8Array(width)];
  const closedSteps: Uint8Array[] = [];
  const movedFrom: Int32Array[] = [];
  // Two rows of each kind, the one before and the one being filled.
  const openRows = [rowOf(width), rowOf(width)];
  const closedRows = [rowOf(width), rowOf(width)];
  // Row 0: no quote word matched yet, so a match may start anywhere.
  let open = reset(rowOf(width), openSteps[0], 0);
  open.cost.fill(0);
  let closed = open;
  for (let i = 1; i <= count; i += 1) {
    const quoteWord = quote[i - 1];
    // Without moves, only the quote's last word ends what a match takes of a line.
    const needClosed = moves !== null || i === count;
    // For each line, its cheapest cell to move from.
    const cheapest = new Int32Array(moves && i > 1 ? moves.firstCell.length : 0).fill(-1);
    if (moves && i > 1) {
      tokens.forEach(({ line }, index) => (cheapest[line] = cheaper(cheapest[line], index + 1, closed)));
    }
    const nextOpen = reset(openRows[i % 2], (openSteps[i] = new Uint8Array(width)), i * edit);
    const nextClosed = needClosed
      ? reset(closedRows[i % 2], (closedSteps[i] = new Uint8Array(width)), i * edit)
      : nextOpen;
    const moved = (movedFrom[i] = new Int32Array(moves && i > 1 ? width : 0));
    for (let k = 1; k < width; k += 1) {
      const token = tokens[k - 1];
      let from = -1;
      if (moves && i > 1) {
        for (const line of moves.groups[token.group]) {
          if (line !== token.line) from = cheaper(from, cheapest[line], closed);
        }
        if (moves.firstCell[token.line] === k) {
          for (const line of moves.groups[token.group - 1] ?? []) from = cheaper(from, moves.lastCell[line], closed);
        }
        moved[k] = from;
      }
      const word = token.word;
      const whole = quoteWord === word;
      const inside = alone && quoteWord.length >= PART_WORD_MIN_CHARS && word.includes(quoteWord);
      const cost = whole ? 0 : inside ? PART_WORD : edit;
      // Where a selection may cut it: the end of a word opens what a match takes of a line, its start closes it.
      const asEnd = !whole && !alone && (i === 1 || from >= 0) && word.endsWith(quoteWord);
      const asStart = !whole && !alone && needClosed && word.startsWith(quoteWord);
      const along = asEnd && i === 1 ? PART_WORD : cost;
      fillCell(nextOpen, k, open, closed, from, along, LINE_MOVE + (asEnd ? PART_WORD : cost), edit, nextOpen);
      if (!needClosed) continue;
      if (asStart) {
        fillCell(nextClosed, k, open, closed, from, PART_WORD, LINE_MOVE + PART_WORD, edit, nextOpen);
      } else {
        // It costs the same at the end of what is taken of a line.
        nextClosed.cost[k] = nextOpen.cost[k];
        nextClosed.steps[k] = nextOpen.steps[k];
        nextClosed.first[k] = nextOpen.first[k];
      }
    }
    open = nextOpen;
    closed = nextClosed;
  }
  let best = -1;
  let bestCost = count * edit;
  let bestDistance = Infinity;
  for (let k = 1; k < width; k += 1) {
    const first = closed.first[k];
    if (first === 0 || closed.cost[k] > bestCost) continue;
    const distance = Math.abs((tokens[first - 1].startMs + tokens[k - 1].endMs) / 2 - midMs);
    if (closed.cost[k] < bestCost || distance < bestDistance) {
      best = k;
      bestCost = closed.cost[k];
      bestDistance = distance;
    }
  }
  if (best < 0) return { tokens: [], edits: count };
  // Back from the match's last word to its first.
  const matched: WindowToken[] = [];
  for (let i = count, k = best, steps = closedSteps; i > 0; ) {
    const step = steps[i][k];
    if (step !== SKIPPED) matched.push(tokens[k - 1]);
    if (step === MOVED) k = movedFrom[i][k];
    else if (step !== SKIPPED) k -= 1;
    if (step !== EXTRA) i -= 1;
    steps = step === MOVED ? closedSteps : openSteps;
  }
  return { tokens: matched.reverse(), edits: Math.floor(bestCost / edit) };
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
  return { state, ranges: rangesOf(match.tokens), score, bestGuess };
}
