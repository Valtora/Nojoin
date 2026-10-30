import { describe, expect, it } from "vitest";

import type { TranscriptSegment } from "@/types";

import { liveTextActivityMark } from "./liveTranscriptPresentation";

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
