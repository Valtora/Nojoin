import type { TranscriptSegment, TranscriptSourceReference } from "@/types";

export const LINE_SETTLING_REASON = "This line is still settling.";

/** Final lines shed runtime lock evidence; provisional marked lines must lock explicitly. */
export const lineIsSettling = (segment: TranscriptSegment): boolean =>
  segment.settling_required === true &&
  (segment.locked === false || (segment.provisional === true && segment.locked !== true));

/** A command names its source revision only on recordings using the new policy. */
export const sourceReference = (segment: TranscriptSegment): TranscriptSourceReference | null =>
  segment.settling_required && segment.id && segment.revision !== undefined
    ? { id: segment.id, revision: segment.revision }
    : null;

/** Split boundaries are word identities, never whitespace guesses or interpolated times. */
export const splitWords = (segment: TranscriptSegment) =>
  (segment.words ?? []).filter((word, index, words) =>
    word.start_ms !== null && word.end_ms !== null && !word.join_left &&
    /^[\p{L}\p{N}_]/u.test(word.word.trim()) &&
    words.slice(0, index).some((previous) => previous.start_ms !== null && previous.end_ms !== null &&
      /^[\p{L}\p{N}_]/u.test(previous.word.trim())),
  );
