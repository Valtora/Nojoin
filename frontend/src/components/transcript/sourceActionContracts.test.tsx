import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import type { TranscriptSegment } from "@/types";

const line: TranscriptSegment = {
  id: "u1", revision: 3, start: 0, end: 2, text: "Alpha beta.", speaker: "LIVE_PROVISIONAL_1",
  settling_required: true, locked: true,
  words: [
    { id: "w1", word: "Alpha", start_ms: 0, end_ms: 500, order: 0, locked: true },
    { id: "w2", word: "beta", start_ms: 700, end_ms: 1600, order: 1, locked: true },
  ],
};
const props = () => ({
  recordingId: "r1", segments: [line], currentTime: -1, isPlaying: false,
  onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), speakerMap: {}, speakers: [],
  globalSpeakers: [], onRenameSpeaker: vi.fn(), onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
  onFindAndReplace: vi.fn(), speakerColors: {}, onUndo: vi.fn(), onRedo: vi.fn(),
  canUndo: false, canRedo: false, onExport: vi.fn(),
});

describe("source-specific action contracts", () => {
  it("captures the raw finished split callback rather than a latest-callback wrapper", async () => {
    const shared = props();
    const original = vi.fn().mockResolvedValue(false);
    const replacement = vi.fn().mockResolvedValue(true);
    const view = render(<TranscriptView {...shared} onSplitSegment={original} />);
    fireEvent.click(screen.getByRole("button", { name: /Actions for Speaker 1/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Split line" }));
    view.rerender(<TranscriptView {...shared} onSplitSegment={replacement} />);
    fireEvent.click(screen.getByRole("button", { name: "Split" }));
    await waitFor(() => expect(original).toHaveBeenCalledWith(line, "w2"));
    expect(replacement).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Split line" })).toBeInTheDocument();
  });

  it("honours explicit read-only capability for locked live correction and naming controls", () => {
    const handlers = { editText: vi.fn(), changeSpeaker: vi.fn(), split: vi.fn() };
    const rename = vi.fn();
    render(<TranscriptView {...props()} readOnly live={{
      hasLoaded: true, lineHandlers: handlers, onRenameLiveSpeaker: rename, onNoteOnLine: vi.fn(),
    }} />);
    fireEvent.click(screen.getByRole("button", { name: /Actions for the line at/ }));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Note on this line"]);
    expect(screen.queryByRole("button", { name: "Rename Speaker 1" })).not.toBeInTheDocument();
    expect(rename).not.toHaveBeenCalled();
  });
});
