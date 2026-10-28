import { clampQuote } from "./lineSelection";
import { selectionOf, type NoteSelection } from "./noteOutbox";

/**
 * Maps a text selection in a transcript to the span and quote of a selection
 * note.
 *
 * Every view draws a line's text in one `p[data-segment-id]` whose text is the
 * line's text, with the line's span in `data-start-ms` and `data-end-ms`
 * (`TranscriptLineText`). So a DOM `Range` goes to the lines it touches, then
 * to character offsets in each, then to ms through the line's words.
 *
 * Marked lines expose exact word identities and times in the DOM. Older
 * recordings retain character interpolation across the line's span.
 */

/** A word of a line, with its character range and its time on the audio timeline. */
export interface TimedToken {
  id?: string;
  locked?: boolean;
  text: string;
  /** Character offsets into the line's text, `to` exclusive. */
  from: number;
  to: number;
  startMs: number;
  endMs: number;
}

/** The words of a line's text, timed by interpolating over its span. */
export const interpolatedTokens = (text: string, startMs: number, endMs: number): TimedToken[] => {
  const span = Math.max(0, endMs - startMs);
  const at = (offset: number) =>
    Math.round(startMs + (text.length === 0 ? 0 : (span * offset) / text.length));
  return Array.from(text.matchAll(/\S+/g), (match) => {
    const from = match.index;
    const to = from + match[0].length;
    return { text: match[0], from, to, startMs: at(from), endMs: at(to) };
  });
};

/** A line as the page draws it. */
export interface DrawnLine {
  /** An inline punctuation fragment touches the preceding original source line. */
  joinsPrevious?: boolean;
  words?: TimedToken[];
  revision?: number;
  settling?: boolean;
  segmentId: string;
  text: string;
  startMs: number;
  endMs: number;
}

/** What a selection takes of one line: the characters `from`..`to` of its text. */
export interface LinePick {
  line: DrawnLine;
  from: number;
  to: number;
}

/** A pick's time, from its first word to its last, and its text; null for whitespace only. */
export const pickSpan = ({ line, from, to }: LinePick) => {
  const words = (line.words ?? interpolatedTokens(line.text, line.startMs, line.endMs)).filter(
    (token) => token.from < to && token.to > from,
  );
  if (words.length === 0) return null;
  const startMs = line.words ? Math.min(...words.map((word) => word.startMs)) : words[0].startMs;
  const endMs = line.words ? Math.max(...words.map((word) => word.endMs)) : words[words.length - 1].endMs;
  return {
    startMs,
    // An identity can name zero-duration punctuation; notes still need a span.
    endMs: line.words ? Math.max(startMs + 1, endMs) : endMs,
    text: line.text.slice(from, to).trim(),
  };
};

/**
 * The note a selection makes: from the earliest start of the lines it takes
 * to the latest end, quoting their text in time order, which is not always
 * page order (side-by-side lanes of overlapping speech). Null when it takes no
 * words, or the words have no span to put a note on.
 */
export const selectionFromPicks = (picks: readonly LinePick[]): NoteSelection | null => {
  const spans = picks
    .map((pick, index) => ({ span: pickSpan(pick), index, joinsPrevious: pick.line.joinsPrevious }))
    .flatMap(({ span, index, joinsPrevious }) => (span ? [{ ...span, index, joinsPrevious }] : []))
    .sort((left, right) => left.startMs - right.startMs || left.index - right.index);
  if (spans.length === 0) return null;
  return selectionOf({
    startMs: Math.min(...spans.map((span) => span.startMs)),
    endMs: Math.max(...spans.map((span) => span.endMs)),
    quotedText: clampQuote(spans.map((span, at) => {
      const separator = at === 0 || (span.joinsPrevious && span.index === spans[at - 1].index + 1) ? "" : " ";
      return `${separator}${span.text}`;
    }).join("")),
    ...(picks.some((pick) => pick.line.revision !== undefined) ? {
      sourceUtterances: picks.flatMap(({ line, from, to }) => {
        if (line.revision === undefined || !pickSpan({ line, from, to })) return [];
        const words = line.words?.filter((word) => word.from < to && word.to > from);
        return [{ id: line.segmentId, revision: line.revision, ...(words ? {
          word_ids: words.map((word) => word.id!),
          word_texts: Object.fromEntries(words.map((word) => [word.id!, word.text.trim()])),
        } : {}) }];
      }),
    } : {}),
  });
};

const LINE_TEXT = "p[data-segment-id]";

/** Where a boundary of `range` falls in `paragraph`'s text; `outside` when not in it. */
const offsetIn = (paragraph: Element, node: Node, offset: number, outside: number): number => {
  if (!paragraph.contains(node)) return outside;
  const before = paragraph.ownerDocument.createRange();
  before.selectNodeContents(paragraph);
  before.setEnd(node, offset);
  return before.toString().length;
};

const numberOf = (value: string | undefined) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

/** The lines inside `root` that `range` takes characters of, in page order. */
export const linePicksOfRange = (range: Range, root: Element): LinePick[] =>
  Array.from(root.querySelectorAll<HTMLElement>(LINE_TEXT)).flatMap((paragraph) => {
    if (!range.intersectsNode(paragraph)) return [];
    const startMs = numberOf(paragraph.dataset.startMs);
    const endMs = numberOf(paragraph.dataset.endMs);
    const segmentId = paragraph.dataset.segmentId;
    if (startMs === null || endMs === null || !segmentId) return [];
    const text = paragraph.textContent ?? "";
    const wordElements = Array.from(paragraph.querySelectorAll<HTMLElement>("[data-word-id]"));
    const words = wordElements.length ? wordElements.map((word): TimedToken => ({
      id: word.dataset.wordId,
      text: word.textContent ?? "",
      from: Number(word.dataset.wordFrom),
      to: Number(word.dataset.wordTo),
      startMs: numberOf(word.dataset.wordStartMs) ?? startMs,
      endMs: numberOf(word.dataset.wordEndMs) ?? endMs,
      locked: word.dataset.wordLocked === "true",
    })) : undefined;
    // A boundary outside this line is before it (the start) or after it (the end).
    const from = offsetIn(paragraph, range.startContainer, range.startOffset, 0);
    const to = offsetIn(paragraph, range.endContainer, range.endOffset, text.length);
    const revision = paragraph.dataset.revision === undefined ? undefined : Number(paragraph.dataset.revision);
    return from < to ? [{
      line: { segmentId, text, startMs, endMs, revision, words,
        settling: paragraph.dataset.settling === "true",
        ...(paragraph.dataset.joinsPrevious === "true" ? { joinsPrevious: true } : {}) }, from, to,
    }] : [];
  });
