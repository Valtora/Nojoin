import { describe, expect, it } from "vitest";
import type { TranscriptSegment } from "@/types";
import { appendPendingTranscriptGroups, pendingTranscriptRuns } from "./pendingTranscriptGroups";
import { buildTranscriptGroups, indexSegments } from "./transcriptGroups";

const lines = (before?: number, after?: number, overlaps = false): TranscriptSegment[] => [
  { id: "before", start: 0, end: overlaps ? 1.1 : 1, text: "Before", speaker: "LIVE_PROVISIONAL_1",
    source_channel: "microphone", capture_interval: before },
  { id: "after", start: 1, end: 2, text: "After", speaker: "UNKNOWN", diarisation_pending: true,
    source_channel: "microphone", capture_interval: after },
];

describe("capture intervals in transcript grouping", () => {
  // The server omits the interval before the first resume and numbers each one after.
  it.each([
    [undefined, 1, false], [undefined, 1, true], [1, 2, false], [1, 2, true],
  ])("keeps capture intervals %s and %s separate (overlap=%s)", (before, after, overlaps) => {
    const indexed = indexSegments(lines(before, after, overlaps));
    const groups = buildTranscriptGroups(indexed);
    expect(groups).toHaveLength(2);
    expect(appendPendingTranscriptGroups(groups, new Set(), new Set())).toHaveLength(2);
    expect(pendingTranscriptRuns(indexed)).toHaveLength(2);
  });
});
