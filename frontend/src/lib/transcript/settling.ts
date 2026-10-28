import type { TranscriptSegment } from "@/types";

export const LINE_SETTLING_REASON = "This line is still settling.";

/** Final lines shed runtime lock evidence; provisional marked lines must lock explicitly. */
export const lineIsSettling = (segment: TranscriptSegment): boolean =>
  segment.settling_required === true &&
  (segment.locked === false || (segment.provisional === true && segment.locked !== true));

/** Split boundaries are word identities, never whitespace guesses or interpolated times. */
export const splitWords = (segment: TranscriptSegment) =>
  (segment.words ?? []).filter((word, index, words) =>
    typeof word.id === "string" && word.id.length > 0 &&
    word.start_ms != null && word.end_ms != null && !word.join_left &&
    /^[\p{L}\p{N}_]/u.test(word.word.trim()) &&
    words.slice(0, index).some((previous) => previous.start_ms != null && previous.end_ms != null &&
      /^[\p{L}\p{N}_]/u.test(previous.word.trim())),
  );
