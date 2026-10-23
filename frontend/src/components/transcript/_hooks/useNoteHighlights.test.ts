import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TimelineNote, TranscriptSegment } from "@/types";

import { useNoteHighlights } from "./useNoteHighlights";

const SEGMENTS: TranscriptSegment[] = [
  { id: "u1", speaker: "SPEAKER_1", start: 60, end: 64, text: "We need a much bigger budget" },
];

const GONE: TimelineNote = {
  id: "n-gone",
  recording_id: "rec-1",
  kind: "selection",
  body: "Ask finance",
  start_ms: 61_000,
  end_ms: 62_000,
  quoted_text: "forty thousand",
  revision: 1,
  created_at: "2026-10-23T10:00:00",
  updated_at: "2026-10-23T10:00:00",
};

describe("useNoteHighlights", () => {
  it("reads what an unanchored note's span says now once, not on every render", () => {
    const notes = [GONE];
    const { result, rerender } = renderHook(() => useNoteHighlights(SEGMENTS, notes));

    const first = result.current.wordsChanged("n-gone");
    expect(first?.nowReads).toBe("need a much");
    expect(result.current.wordsChanged("n-gone")).toBe(first);
    // A playback tick renders the view again with the same lines and notes.
    rerender();
    expect(result.current.wordsChanged("n-gone")).toBe(first);
    expect(result.current.wordsChanged("n-other")).toBeNull();
  });
});
