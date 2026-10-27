import { noteLength } from "./headNoteSync";
import { NOTE_QUOTE_MAX_CHARS, selectionOf, type NoteSelection } from "./noteOutbox";
import { segmentEndMs, segmentStartMs, type PlacementSegment } from "./placement";
import type { TranscriptSegment } from "@/types";
import { sourceReference } from "@/lib/transcript/settling";

/**
 * A quote cut to what a note keeps: a longer one ends in "…" at the limit,
 * counted in characters as the API counts them.
 */
export const clampQuote = (text: string): string => {
  if (noteLength(text) <= NOTE_QUOTE_MAX_CHARS) return text;
  return `${Array.from(text).slice(0, NOTE_QUOTE_MAX_CHARS - 1).join("").trimEnd()}…`;
};

/**
 * The selection a note on a whole line takes: the line's span and its text.
 * Null for a line a selection note can't sit on, one with no text or no span.
 */
export const lineSelection = (segment: PlacementSegment & Partial<TranscriptSegment>): NoteSelection | null => {
  const reference = sourceReference(segment as TranscriptSegment);
  return selectionOf({
    startMs: segmentStartMs(segment),
    endMs: segmentEndMs(segment),
    quotedText: clampQuote((segment.text ?? "").trim()),
    ...(reference ? { sourceUtterances: [reference] } : {}),
  });
};
