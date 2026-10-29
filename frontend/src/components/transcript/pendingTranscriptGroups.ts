import type { TranscriptSegment } from "@/types";
import { sameCaptureInterval, type IndexedTranscriptSegment, type TranscriptGroup } from "./transcriptGroups";

/** Only explicit absent-coverage evidence permits a display-only continuation. */
export const isDiarisationPending = (segment: TranscriptSegment) =>
  segment.diarisation_pending === true && segment.speaker === "UNKNOWN";

/**
 * Attach pending source lines without changing their text, speaker or identities.
 * Notes and captured interactions keep their own row boundaries. Source changes
 * must remain visible: a microphone tail never borrows a shared-audio speaker.
 */
export function appendPendingTranscriptGroups(
  groups: readonly TranscriptGroup[],
  noteBreaks: ReadonlySet<number>,
  blockedIds: ReadonlySet<string>,
): TranscriptGroup[] {
  const result: TranscriptGroup[] = [];
  for (const group of groups) {
    const previous = result[result.length - 1];
    const first = group.items[0];
    const last = previous?.items[previous.items.length - 1];
    const canAppend = previous && last && group.items.length === 1 && isDiarisationPending(first.segment) &&
      sameCaptureInterval(last.segment, first.segment) &&
      (last.segment.source_channel ?? null) === (first.segment.source_channel ?? null) &&
      !noteBreaks.has(last.index) && !blockedIds.has(first.segmentId);
    if (canAppend) {
      result[result.length - 1] = { ...previous, end: Math.max(previous.end, group.end),
        items: [...previous.items, first] };
    } else result.push(group);
  }
  return result;
}

/** Original source fragments drawn inside each shared bubble. */
export function pendingTranscriptRuns(items: readonly IndexedTranscriptSegment[]): IndexedTranscriptSegment[][] {
  const runs: IndexedTranscriptSegment[][] = [];
  for (const item of items) {
    const previous = runs[runs.length - 1];
    const last = previous?.[previous.length - 1];
    if (last && isDiarisationPending(item.segment) &&
      sameCaptureInterval(last.segment, item.segment) &&
      (last.segment.source_channel ?? null) === (item.segment.source_channel ?? null)) previous.push(item);
    else runs.push([item]);
  }
  return runs;
}
