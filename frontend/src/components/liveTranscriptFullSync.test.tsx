import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { applyTranscriptDelta, type LocalTranscriptState } from "@/lib/transcriptState";
import type { TranscriptUtterance } from "@/types";

import LiveTranscriptPanel from "./LiveTranscriptPanel";

// The panel places the notes once for each new list of lines, so counting the
// placements says whether it recomputed what it derives from the lines.
let placements = 0;
vi.mock("@/lib/notes/placement", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/notes/placement")>();
  return {
    ...actual,
    placeTimelineNotes: (...args: Parameters<typeof actual.placeTimelineNotes>) => {
      placements += 1;
      return actual.placeTimelineNotes(...args);
    },
  };
});
beforeEach(() => {
  placements = 0;
});

// The live lines as a full read hands them over, new objects every time.
const utterances = (): TranscriptUtterance[] =>
  ["We start with the budget.", "Then the hiring plan.", "Any questions?"].map((text, at) => ({
    id: `u${at}`,
    start: at * 4,
    end: at * 4 + 3,
    text,
    speaker: "UNKNOWN",
    revision: 1,
    provisional: true,
    segment_source: "live",
    source_channel: "microphone",
  }));
const fullRead = (currentState: LocalTranscriptState | null, next: TranscriptUtterance[]) =>
  applyTranscriptDelta({
    currentState,
    recordingId: "rec-1",
    fallbackSegments: [],
    delta: { recording_id: "rec-1", revision: 1, utterances: next, tombstones: [], speakers: [] },
    mode: "full",
  });

describe("the live transcript after a full read", () => {
  it("recomputes nothing when the read hands over the same lines, and shows one it changed", () => {
    const first = fullRead(null, utterances());
    const { rerender } = render(<LiveTranscriptPanel segments={first.segments} hasLoaded />);
    placements = 0;

    const same = fullRead(first, utterances());
    rerender(<LiveTranscriptPanel segments={same.segments} hasLoaded />);

    expect(placements).toBe(0);

    const next = utterances();
    next[2] = { ...next[2], text: "Any questions before we close?", revision: 2 };
    rerender(<LiveTranscriptPanel segments={fullRead(same, next).segments} hasLoaded />);

    expect(placements).toBe(1);
    expect(screen.getByText("Any questions before we close?")).toBeInTheDocument();
  });
});
