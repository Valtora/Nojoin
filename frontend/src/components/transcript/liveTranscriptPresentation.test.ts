import { describe, expect, it } from "vitest";

import type { TranscriptSegment } from "@/types";

import { buildLiveTranscriptPresentation, liveTextActivityMark } from "./liveTranscriptPresentation";

const line = (overrides: Partial<TranscriptSegment> = {}): TranscriptSegment => ({
  id: "line-1", start: 0, end: 4, text: "We should book the", speaker: "UNKNOWN", ...overrides,
});

describe("the live text activity mark (issue #849 follow-up)", () => {
  it("is null before any live line", () => {
    expect(liveTextActivityMark([])).toBeNull();
  });

  it("moves when a line is added or the last one grows", () => {
    const first = liveTextActivityMark([line()]);
    expect(liveTextActivityMark([line(), line({ id: "line-2", start: 4, end: 6, text: "Yes." })])).not.toBe(first);
    expect(liveTextActivityMark([line({ end: 6, text: "We should book the room" })])).not.toBe(first);
  });

  it("moves when settling revises the last line in place without moving its end", () => {
    const before = liveTextActivityMark([line({ text: "We should book the" })]);
    expect(liveTextActivityMark([line({ text: "We should book the rooms" })])).not.toBe(before);
  });

  it("stays put when a read returns the same lines", () => {
    expect(liveTextActivityMark([line()])).toBe(liveTextActivityMark([line()]));
  });
});

describe("the line that offers a live speaker's pending name", () => {
  const A = "LIVE_PROVISIONAL_1";
  const B = "LIVE_PROVISIONAL_2";
  // A speaks at 0-4, 8-12 and 16-20; B at 4-8 and 12-16.
  const lines = [0, 4, 8, 12, 16].map((start, i) => line({
    id: `line-${i}`, start, end: start + 4, text: `Line ${i}`, speaker: i % 2 ? B : A,
  }));
  const pending = { [A]: "Alex", [B]: "Blair" };
  const offering = (starts?: Record<string, number>) =>
    [...buildLiveTranscriptPresentation(lines, pending, starts).entries()]
      .filter(([, presentation]) => presentation.offersSuggestion).map(([key]) => key);

  it("is the line that holds where the name was heard", () => {
    expect(offering({ [A]: 8 })).toEqual(["line-2", "line-3"]);
    expect(offering({ [A]: 9.25 })).toEqual(["line-2", "line-3"]);
  });

  it("is the later of two of the speaker's lines that touch at that time", () => {
    const touching = [line({ id: "first", start: 0, end: 4, speaker: A }), line({ id: "second", start: 4, end: 6, speaker: A })];
    const shown = buildLiveTranscriptPresentation(touching, pending, { [A]: 4 });
    expect(shown.get("first")?.offersSuggestion).toBe(false);
    expect(shown.get("second")?.offersSuggestion).toBe(true);
  });

  it("is the speaker's last line with no time, or when no line of theirs holds it", () => {
    expect(offering()).toEqual(["line-3", "line-4"]);
    // 13 s is B's line now, and 30 s is past every line.
    expect(offering({ [A]: 13 })).toEqual(["line-3", "line-4"]);
    expect(offering({ [A]: 30 })).toEqual(["line-3", "line-4"]);
  });

  it("is no line for a speaker with no pending name", () => {
    const shown = [...buildLiveTranscriptPresentation(lines, { [B]: "Blair" }).values()];
    expect(shown.map((presentation) => presentation.offersSuggestion))
      .toEqual([false, false, false, true, false]);
  });
});
