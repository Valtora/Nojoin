// @vitest-environment node
import { describe, expect, it } from "vitest";

import { applyTranscriptDelta, createLocalTranscriptState, flushDeferredTranscriptState } from "./transcriptState";

describe("full read edit deferral", () => {
  const line = { id: "line", start: 0, end: 1, text: "Editing", speaker: "UNKNOWN", revision: 4, settling_required: true };
  const other = { id: "other", start: 1, end: 2, text: "Other", speaker: "UNKNOWN", revision: 4 };

  it.each(["replacement", "explicit deletion", "implicit deletion"])("keeps the edited row for %s and flushes when editing ends", (kind) => {
    const state = applyTranscriptDelta({
      currentState: createLocalTranscriptState("rec-1", [line, other], 4),
      recordingId: "rec-1", fallbackSegments: [], mode: "full", activeEditUtteranceId: "line",
      delta: {
        recording_id: "rec-1", revision: 5, speakers: [], settling_required: true,
        utterances: kind === "replacement" ? [{ ...line, text: "Remote" }, other] : [other],
        tombstones: kind === "explicit deletion" ? ["line"] : [],
      },
    });
    expect(state.segments.find((segment) => segment.id === "line")).toEqual(line);
    expect(state.deferredById.line?.text ?? null).toBe(kind === "replacement" ? "Remote" : null);
    const flushed = flushDeferredTranscriptState(state);
    expect(flushed.segments.find((segment) => segment.id === "line")?.text).toBe(
      kind === "replacement" ? "Remote" : undefined,
    );
  });

  it("keeps the sole edited row when a marked full read is empty", () => {
    const state = applyTranscriptDelta({
      currentState: createLocalTranscriptState("rec-1", [line], 4),
      recordingId: "rec-1", fallbackSegments: [], mode: "full", activeEditUtteranceId: "line",
      delta: { recording_id: "rec-1", revision: 5, speakers: [], settling_required: true, utterances: [], tombstones: [] },
    });
    expect(state.segments[0]?.text).toBe("Editing");
    expect(state.deferredById.line).toBeNull();
    expect(flushDeferredTranscriptState(state).segments).toEqual([]);
  });

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
