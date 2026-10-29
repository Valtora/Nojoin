// @vitest-environment node
import { describe, expect, it } from "vitest";

import { applyTranscriptDelta, createLocalTranscriptState, flushDeferredTranscriptState } from "./transcriptState";

describe("deferred edit refresh", () => {
  const line = { id: "line", start: 0, end: 1, text: "Editing", speaker: "UNKNOWN", revision: 4, settling_required: true };

  it.each(["full", "delta"] as const)("an explicit %s refresh supersedes an older deferred edit", (mode) => {
    const state = applyTranscriptDelta({
      currentState: createLocalTranscriptState("rec-1", [line], 4, { line: { ...line, text: "Older remote" } }),
      recordingId: "rec-1", fallbackSegments: [], mode,
      delta: {
        recording_id: "rec-1", revision: 5, speakers: [], settling_required: true,
        utterances: [{ ...line, text: "Saved locally" }], tombstones: [],
      },
    });
    expect(state.segments[0].text).toBe("Saved locally");
    expect(state.deferredById).toEqual({});
    expect(flushDeferredTranscriptState(state).segments[0].text).toBe("Saved locally");
  });
});
