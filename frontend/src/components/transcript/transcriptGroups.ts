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

/** Old reads and captures with no resume cuts share the initial interval. */
export const sameCaptureInterval = (left: TranscriptSegment, right: TranscriptSegment): boolean =>
  (left.capture_interval ?? 0) === (right.capture_interval ?? 0);

/**
 * Separates the line keys the view joins into one string per group (the
 * lines playback is in, the lines revised lately), so a group compares them
 * by value. A key never holds it.
 */
export const LINE_KEY_SEPARATOR = "\n";

/**
 * Whether two groups hold the same lines, as the same segment objects in the
 * same places. A group's span and speakers come from its lines, so they match
 * too. Grouping runs again whenever any line changes; a group this leaves
 * alone need not redraw.
 */
export function sameGroup(left: TranscriptGroup, right: TranscriptGroup): boolean {
  return (
    left === right ||
    (left.items.length === right.items.length &&
      left.items.every((item, at) => {
        const other = right.items[at];
        return (
          item.segment === other.segment &&
          item.index === other.index &&
          item.segmentId === other.segmentId
        );
      }))
  );
}

/**
 * Annotate segments with their original index and stable key, for
 * {@link TranscriptView}.
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
 * Group consecutive time-overlapping display segments
 * into render groups, for {@link TranscriptView}.
 *
 * A group always ends at a line in `breakAfter` (original indexes): a note
 * renders on its own line after the line it follows, so it splits the group
 * that line would otherwise have continued.
 */
export function buildTranscriptGroups(
  displaySegments: IndexedTranscriptSegment[],
  breakAfter: ReadonlySet<number> = new Set(),
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
      const previous = currentGroup.items[currentGroup.items.length - 1];

      if (!breakAfter.has(previous.index) && isTimeOverlap && sameCaptureInterval(previous.segment, item.segment)) {
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
