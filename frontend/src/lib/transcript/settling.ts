import type { TranscriptSegment } from "@/types";

/** Why a settling line's corrections wait: shown once per menu, and on the line itself. */
export const LINE_SETTLING_REASON = "Corrections open when this line locks, usually within half a minute.";

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
