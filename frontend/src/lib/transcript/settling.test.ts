// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyTranscriptDelta } from "@/lib/transcriptState";
import { moveTargets } from "@/lib/notes/noteMoves";
import { lineIsSettling, splitWords } from "./settling";
import type { TranscriptSegment, TranscriptUtteranceList } from "@/types";

const segment: TranscriptSegment = {
  id: "u1", revision: 1, start: 0, end: 3, text: "one two", speaker: "UNKNOWN",
  provisional: true, settling_required: true, locked: false,
};

describe("settling policy and projection", () => {
  it("keeps public word and review facts through full reads, deferred edits and deltas", () => {
    const delta: TranscriptUtteranceList = {
      recording_id: "r1", revision: 1, speakers: [], tombstones: [], settling_required: true,
      utterances: [{ ...segment, id: "u1", revision: 1,
        words: [{ id: "w1", word: "one two", order: 0, start_ms: null, end_ms: null, locked: true }],
        text_locked: true, final_review: { text: "Other wording", reason: "ambiguous_alignment" },
      }],
    };
    const full = applyTranscriptDelta({ currentState: null, recordingId: "r1", fallbackSegments: [], delta, mode: "full" });
    expect(full.segments[0]).toMatchObject({ words: delta.utterances[0].words, locked: false, text_locked: true, settling_required: true, final_review: delta.utterances[0].final_review });
    const next = { ...delta, revision: 2, utterances: [{ ...delta.utterances[0], locked: true, revision: 2 }] };
    const deferred = applyTranscriptDelta({ currentState: full, recordingId: "r1", fallbackSegments: [], delta: next, activeEditUtteranceId: "u1" });
    expect(deferred.deferredById.u1).toMatchObject({ words: delta.utterances[0].words, locked: true });
    const unchanged = applyTranscriptDelta({ currentState: full, recordingId: "r1", fallbackSegments: [], delta, mode: "full" });
    expect(unchanged.segments).toBe(full.segments);
  });

  it("preserves legacy and CPU-off policy, guards missing provisional evidence, and permits final lines", () => {
    expect(lineIsSettling({ ...segment, settling_required: false })).toBe(false);
    expect(lineIsSettling({ ...segment, settling_required: undefined })).toBe(false);
    expect(lineIsSettling({ ...segment, locked: undefined })).toBe(true);
    expect(lineIsSettling({ ...segment, locked: undefined, provisional: false })).toBe(false);
    expect(lineIsSettling({ ...segment, locked: false, provisional: false })).toBe(true);
  });

  it("clears marked output on an empty full read but retains the legacy fallback contract", () => {
    const delta: TranscriptUtteranceList = { recording_id: "r1", revision: 2, speakers: [], utterances: [], tombstones: [], settling_required: true };
    const marked = applyTranscriptDelta({ currentState: null, recordingId: "r1", fallbackSegments: [segment], delta, mode: "full" });
    expect(marked.segments).toEqual([]);
    const legacy = applyTranscriptDelta({ currentState: null, recordingId: "r1", fallbackSegments: [segment], delta: { ...delta, settling_required: false }, mode: "full" });
    expect(legacy.segments).toEqual([segment]);
  });

  it("omits unlocked move targets and names the locked destination revision", () => {
    const segments = [segment, { ...segment, id: "u2", start: 4, end: 6, locked: true, revision: 9 }];
    expect(moveTargets(segments, [0, 1])).toEqual([{ afterIndex: 1, anchorMs: 4000, source: { id: "u2", revision: 9 } }]);
    expect(moveTargets(segments.map((line) => ({ ...line, settling_required: undefined })), [0, 1]))
      .toEqual([{ afterIndex: 0, anchorMs: 0 }, { afterIndex: 1, anchorMs: 4000 }]);
  });

  it("never offers an interior boundary inside an untimed human replacement atom", () => {
    expect(splitWords({ ...segment, locked: true, words: [
      { id: "human", word: "one corrected phrase", order: 0, start_ms: null, end_ms: null, locked: true },
    ] })).toEqual([]);
  });
});
