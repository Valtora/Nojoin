import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import type { TranscriptSegment } from "@/types";

const line: TranscriptSegment = {
  id: "assigned-line", revision: 3, start: 0, end: 2, text: "Original speech.", speaker: "SPEAKER_0",
};
const props = () => ({
  recordingId: "r1", segments: [line], currentTime: -1, isPlaying: false,
  onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), speakerMap: { SPEAKER_0: "Alex" }, speakers: [],
  globalSpeakers: [], onRenameSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
  onFindAndReplace: vi.fn(), speakerColors: {}, onUndo: vi.fn(), onRedo: vi.fn(),
  canUndo: false, canRedo: false, onExport: vi.fn(),
});

describe("speaker assignment command retention", () => {
  it("does not let an old successful assignment close a newer line's picker", async () => {
    let finish!: (saved: boolean) => void;
    const save = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    const shared = props();
    render(<TranscriptView {...shared} onUpdateSegmentSpeaker={save}
      segments={[line, { ...line, id: "next-line", speaker: "SPEAKER_1", start: 3, end: 5 }]}
      speakerMap={{ SPEAKER_0: "Alex", SPEAKER_1: "Blair" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Alex" }));
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "Casey" } });
    fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Enter" });
    expect(save).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByPlaceholderText("Search or add...")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Blair" }));
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "New picker draft" } });
    await act(async () => finish(true));
    expect(screen.getByPlaceholderText("Search or add...")).toHaveValue("New picker draft");
  });

  it("keeps the opened revision and callback through a poll, then retains a refused choice for retry", async () => {
    const shared = props();
    const save = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const replacementSave = vi.fn().mockResolvedValue(true);
    const view = render(<TranscriptView {...shared} onUpdateSegmentSpeaker={save} />);
    fireEvent.click(screen.getByRole("button", { name: "Alex" }));
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "Blair" } });
    fireEvent.click(screen.getByRole("button", { name: "Whole transcript" }));
    view.rerender(<TranscriptView {...shared} segments={[{ ...line, revision: 9 }]}
      onUpdateSegmentSpeaker={replacementSave} />);

    fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Enter" });
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenLastCalledWith(line, { name: "Blair", scope: "speaker_everywhere_in_recording" });
    expect(screen.getByPlaceholderText("Search or add...")).toHaveValue("Blair");
    expect(screen.getByRole("button", { name: "Whole transcript" })).toHaveAttribute("aria-pressed", "true");
    expect(replacementSave).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Enter" });
    await waitFor(() => expect(screen.queryByPlaceholderText("Search or add...")).not.toBeInTheDocument());
    expect(save).toHaveBeenCalledTimes(2);
  });
});
