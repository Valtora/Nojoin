import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import type { TranscriptSegment } from "@/types";

const opened: TranscriptSegment = {
  id: "named-line", revision: 3, start: 0, end: 2, text: "The first words.",
  speaker: "LIVE_PROVISIONAL_1", provisional: true, settling_required: true, locked: true,
};

const props = () => ({
  recordingId: "r1", segments: [opened], currentTime: -1, isPlaying: false,
  onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), speakerMap: {}, speakers: [],
  globalSpeakers: [], onRenameSpeaker: vi.fn(), onUpdateSegmentSpeaker: vi.fn(),
  onUpdateSegmentText: vi.fn(), onFindAndReplace: vi.fn(), speakerColors: {},
  onUndo: vi.fn(), onRedo: vi.fn(), canUndo: false, canRedo: false, onExport: vi.fn(),
});

describe("live header continuity", () => {
  it("retains a naming draft through settled handoff and refusal", async () => {
    const finished = props();
    const rename = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const view = render(<TranscriptView {...finished} live={{ hasLoaded: true, onRenameLiveSpeaker: rename }} />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Speaker 1" }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "Alex" } });

    view.rerender(<TranscriptView {...finished}
      segments={[{ ...opened, revision: 9, speaker: "SPEAKER_0", provisional: false }]} />);

    expect(screen.getByRole("combobox")).toHaveValue("Alex");
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    await waitFor(() => expect(rename).toHaveBeenCalledTimes(1));
    expect(rename).toHaveBeenLastCalledWith("LIVE_PROVISIONAL_1", "Alex");
    expect(screen.getByRole("combobox")).toHaveValue("Alex");
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
    expect(rename).toHaveBeenCalledTimes(2);
    expect(finished.onRenameSpeaker).not.toHaveBeenCalled();
  });
});
