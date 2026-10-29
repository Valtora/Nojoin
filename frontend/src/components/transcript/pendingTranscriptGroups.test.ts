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
  it.each([
    [undefined, undefined], [undefined, 0], [0, undefined], [0, 0], [1, 1],
  ])("joins pending continuations within compatible intervals (%s, %s)", (before, after) => {
    const indexed = indexSegments(lines(before, after));
    const groups = buildTranscriptGroups(indexed);
    expect(appendPendingTranscriptGroups(groups, new Set(), new Set())).toHaveLength(1);
    expect(pendingTranscriptRuns(indexed)).toHaveLength(1);
  });

  it.each([false, true])("keeps capture intervals separate even with overlap=%s", (overlaps) => {
    const indexed = indexSegments(lines(0, 1, overlaps));
    const groups = buildTranscriptGroups(indexed);
    expect(groups).toHaveLength(2);
    expect(appendPendingTranscriptGroups(groups, new Set(), new Set())).toHaveLength(2);
    expect(pendingTranscriptRuns(indexed)).toHaveLength(2);
  });
});
