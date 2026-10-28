import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import type { TranscriptSegment } from "@/types";

const segment: TranscriptSegment = {
  id: "growing", revision: 3, start: 1, end: 4, speaker: "LIVE_PROVISIONAL_1",
  text: "The conversation continues.", provisional: true, segment_source: "live",
};
const props = () => ({
  recordingId: "rec-1", segments: [segment], currentTime: -1, isPlaying: false,
  onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), speakerMap: {}, speakers: [],
  globalSpeakers: [], onRenameSpeaker: vi.fn(), onUpdateSegmentSpeaker: vi.fn(),
  onUpdateSegmentText: vi.fn(), onFindAndReplace: vi.fn(), speakerColors: { LIVE_PROVISIONAL_1: "blue" },
  onUndo: vi.fn(), onRedo: vi.fn(), canUndo: true, canRedo: true, onExport: vi.fn(),
});

describe("shared live capabilities", () => {
  it("keeps disabled playback visible and export independent from playback", () => {
    const handlers = props();
    const view = render(<TranscriptView {...handlers} disableSegmentPlayback disableExport live={{ hasLoaded: true }} />);
    const play = screen.getByRole("button", { name: /Play Speaker 1 at/ });
    expect(play).toBeDisabled();
    fireEvent.click(play);
    const exportButton = screen.getByRole("button", { name: "Export transcript disabled" });
    expect(exportButton).toBeDisabled();
    fireEvent.click(exportButton);
    expect(handlers.onPlaySegment).not.toHaveBeenCalled();
    expect(handlers.onExport).not.toHaveBeenCalled();
    view.rerender(<TranscriptView {...handlers} disableSegmentPlayback disableExport={false} live={{ hasLoaded: true }} />);
    expect(screen.getByRole("button", { name: /Play Speaker 1 at/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Export transcript" }));
    expect(handlers.onExport).toHaveBeenCalledTimes(1);
    view.rerender(<TranscriptView {...handlers} disableSegmentPlayback={false} disableExport live={{ hasLoaded: true }} />);
    fireEvent.click(screen.getByRole("button", { name: /Play Speaker 1 at/ }));
    expect(handlers.onPlaySegment).toHaveBeenCalledWith(1, 4);
    expect(screen.getByRole("button", { name: "Export transcript disabled" })).toBeDisabled();
    expect(handlers.onExport).toHaveBeenCalledTimes(1);
  });

  it("keeps search available but refuses bulk corrections and history in live speech", () => {
    const handlers = props();
    render(<TranscriptView {...handlers} live={{ hasLoaded: true }} disableSegmentPlayback disableExport />);
    expect(screen.getByRole("button", { name: "Find & Replace" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "conversation" } });
    expect(screen.getByText("1 of 1")).toBeInTheDocument();
    expect(handlers.onFindAndReplace).not.toHaveBeenCalled();
  });

  it("retains an unlabelled live line alongside a labelled one", () => {
    render(<TranscriptView {...props()} segments={[segment, { ...segment, id: "unknown", start: 5, end: 6,
      speaker: "UNKNOWN", provisional: false, text: "A voice without a label." }]}
      live={{ hasLoaded: true }} />);
    expect(screen.getByText("A voice without a label.")).toBeInTheDocument();
  });

  it("preserves an idless growing line and its rename draft when timing and attribution change", () => {
    const handlers = props();
    const live = { hasLoaded: true, onRenameLiveSpeaker: vi.fn(async () => true) };
    const first = { ...segment, id: undefined };
    const view = render(<TranscriptView {...handlers} segments={[first]} live={live} />);
    const paragraph = screen.getByText("The conversation continues.");
    fireEvent.click(screen.getByRole("button", { name: "Rename Speaker 1" }));
    const editor = screen.getByRole("combobox");
    fireEvent.change(editor, { target: { value: "Alex" } });
    view.rerender(<TranscriptView {...handlers} segments={[{ ...first, end: 8,
      speaker: "LIVE_PROVISIONAL_2", text: "The conversation continues with more words." }]} live={live} />);
    expect(screen.getByText("The conversation continues with more words.")).toBe(paragraph);
    expect(screen.getByRole("combobox")).toBe(editor);
    expect(editor).toHaveValue("Alex");
  });
});
