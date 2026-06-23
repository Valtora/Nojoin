import { TranscriptSegment } from "@/types";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";

/** A transcript segment paired with its original index and stable key. */
export interface IndexedTranscriptSegment {
  segment: TranscriptSegment;
  index: number;
  segmentId: string;
}

/** A consecutive run of segments grouped for rendering (handles overlaps). */
export interface TranscriptGroup {
  start: number;
  end: number;
  involved: Set<string>;
  items: IndexedTranscriptSegment[];
}

const areSetsEqual = (a: Set<string>, b: Set<string>): boolean =>
  a.size === b.size && [...a].every((value) => b.has(value));

/**
 * Annotate segments with their original index and stable key. Extracted from
 * {@link TranscriptView} (FE-012); output is unchanged.
 */
export function indexSegments(
  segments: TranscriptSegment[],
): IndexedTranscriptSegment[] {
  return segments.map((segment, index) => ({
    segment,
    index,
    segmentId: getTranscriptSegmentKey(segment, index),
  }));
}

/**
 * Group consecutive (time-overlapping or same-overlap-event) display segments
 * into render groups. Extracted verbatim from {@link TranscriptView} (FE-012)
 * so the grouping is unchanged.
 */
export function buildTranscriptGroups(
  displaySegments: IndexedTranscriptSegment[],
): TranscriptGroup[] {
  const trackGroups: TranscriptGroup[] = [];
  let currentGroup: TranscriptGroup | null = null;

  for (const item of displaySegments) {
    const involvedArray = [
      item.segment.speaker,
      ...(item.segment.overlapping_speakers || []),
    ];
    const involvedSet = new Set(involvedArray);

    if (!currentGroup) {
      currentGroup = {
        start: item.segment.start,
        end: item.segment.end,
        involved: involvedSet,
        items: [item],
      };
    } else {
      const isTimeOverlap = item.segment.start < currentGroup.end;
      const isSameOverlapEvent =
        involvedSet.size > 1 &&
        currentGroup.involved.size > 1 &&
        areSetsEqual(involvedSet, currentGroup.involved);

      if (isTimeOverlap || isSameOverlapEvent) {
        currentGroup.items.push(item);
        currentGroup.end = Math.max(currentGroup.end, item.segment.end);
        involvedSet.forEach((spk) => currentGroup!.involved.add(spk));
      } else {
        trackGroups.push(currentGroup);
        currentGroup = {
          start: item.segment.start,
          end: item.segment.end,
          involved: involvedSet,
          items: [item],
        };
      }
    }
  }
  if (currentGroup) trackGroups.push(currentGroup);

  return trackGroups;
}
