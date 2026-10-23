import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TranscriptSegment } from "@/types";

import { useTranscriptSearch } from "./useTranscriptSearch";

const line = (id: string, start: number, text: string): TranscriptSegment => ({
  id,
  start,
  end: start + 1,
  text,
  speaker: "SPEAKER_00",
  revision: 1,
});

const lines = [
  line("utt-1", 1, "budget first, budget review"),
  line("utt-2", 3, "the budget again"),
  line("utt-3", 5, "closing"),
];

const renderSearch = () => {
  const scrollSegmentIntoView = vi.fn();
  const view = renderHook(
    ({ segments }) =>
      useTranscriptSearch({ segments, showSearch: true, scrollSegmentIntoView }),
    { initialProps: { segments: lines } },
  );
  act(() => view.result.current.setFindText("budget"));
  return { ...view, scrollSegmentIntoView };
};

describe("useTranscriptSearch scrolling", () => {
  it("scrolls to the first match of a search", () => {
    const { scrollSegmentIntoView } = renderSearch();

    expect(scrollSegmentIntoView).toHaveBeenCalledTimes(1);
    expect(scrollSegmentIntoView).toHaveBeenLastCalledWith("utt-1", "smooth");
  });

  it("leaves the view alone when a poll hands over a new list with the same current match", () => {
    const { rerender, scrollSegmentIntoView } = renderSearch();

    // Another line changed: a new list, and new matches around the same one.
    rerender({ segments: [lines[0], lines[1], line("utt-3", 5, "closing, edited")] });

    expect(scrollSegmentIntoView).toHaveBeenCalledTimes(1);
  });

  it("scrolls to the next match, on the same line or another", () => {
    const { result, scrollSegmentIntoView } = renderSearch();

    act(() => result.current.nextMatch());

    expect(scrollSegmentIntoView).toHaveBeenCalledTimes(2);
    expect(scrollSegmentIntoView).toHaveBeenLastCalledWith("utt-1", "smooth");

    act(() => result.current.nextMatch());

    expect(scrollSegmentIntoView).toHaveBeenCalledTimes(3);
    expect(scrollSegmentIntoView).toHaveBeenLastCalledWith("utt-2", "smooth");
  });
});
