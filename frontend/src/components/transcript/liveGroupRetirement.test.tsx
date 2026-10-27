import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import type { TranscriptSegment } from "@/types";

vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(), getGlobalSpeakers: vi.fn(async () => []),
}));

const lines: TranscriptSegment[] = [
  { id: "a", revision: 3, start: 0, end: 4, speaker: "LIVE_PROVISIONAL_1", text: "Alpha beta.",
    settling_required: true, provisional: true, locked: true },
  { id: "b", revision: 4, start: 1, end: 3, speaker: "LIVE_PROVISIONAL_2", text: "Overlapping words.",
    settling_required: true, provisional: true, locked: true },
];
const props = () => ({
  recordingId: "r1", segments: lines, currentTime: -1, isPlaying: false,
  onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), speakerMap: {}, speakers: [],
  globalSpeakers: [], onRenameSpeaker: vi.fn(), onUpdateSegmentSpeaker: vi.fn(),
  onUpdateSegmentText: vi.fn(), onFindAndReplace: vi.fn(), speakerColors: {},
  onUndo: vi.fn(), onRedo: vi.fn(), canUndo: false, canRedo: false, onExport: vi.fn(),
});
const live = () => ({ hasLoaded: true, onNoteOnLine: vi.fn(),
  onRenameLiveSpeaker: vi.fn().mockResolvedValue(true), lineHandlers: {
    editText: vi.fn().mockResolvedValue(true), changeSpeaker: vi.fn().mockResolvedValue(true),
    split: vi.fn().mockResolvedValue(true),
  } });
const playButtons = () => screen.getAllByRole("button", { name: /^Play / });
const openText = () => {
  fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
};

describe("retiring live playback groups", () => {
  it("ignores an old assignment completion after a different line editor opens", async () => {
    const shared = props();
    const presentation = live();
    let finish!: (accepted: boolean) => void;
    presentation.lineHandlers.changeSpeaker.mockImplementation(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    render(<TranscriptView {...shared} live={presentation} disableSegmentPlayback />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change speaker" }));
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "Alex" } });
    fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Enter" });
    fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Escape" });
    openText();
    const editor = screen.getByRole("textbox", { name: "Line text" });
    fireEvent.change(editor, { target: { value: "Newer draft" } });
    await act(async () => finish(true));
    expect(screen.getByRole("textbox", { name: "Line text" })).toBe(editor);
    expect(editor).toHaveValue("Newer draft");
  });
  it("restores the finished overlap group immediately when no live interaction is open", () => {
    const shared = props();
    const view = render(<TranscriptView {...shared} live={live()} disableSegmentPlayback />);
    expect(playButtons()).toHaveLength(2);
    view.rerender(<TranscriptView {...shared} />);
    expect(playButtons()).toHaveLength(1);
    fireEvent.click(playButtons()[0]);
    expect(shared.onPlaySegment).toHaveBeenCalledWith(0, 4);
  });

  it("keeps a carried text editor mounted until it closes, then restores overlap playback", async () => {
    const shared = props();
    const view = render(<TranscriptView {...shared} live={live()} disableSegmentPlayback />);
    openText();
    const editor = screen.getByRole("textbox", { name: "Line text" });
    fireEvent.change(editor, { target: { value: "My carried draft" } });
    view.rerender(<TranscriptView {...shared} />);
    expect(screen.getByRole("textbox", { name: "Line text" })).toBe(editor);
    expect(editor).toHaveValue("My carried draft");
    expect(playButtons()).toHaveLength(2);
    expect(screen.getByRole("button", { name: /^Export transcript/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Find & Replace" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(playButtons()).toHaveLength(1));
    expect(screen.getByRole("button", { name: /^Export transcript/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Actions for Speaker 1's line at 00:00" })).toHaveFocus();
  });

  it("keeps a carried naming editor mounted until its accepted rename", async () => {
    const shared = props();
    const presentation = live();
    const view = render(<TranscriptView {...shared} live={presentation} disableSegmentPlayback />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Speaker 1" }));
    const editor = screen.getByRole("combobox");
    fireEvent.change(editor, { target: { value: "Alex" } });
    view.rerender(<TranscriptView {...shared} />);
    expect(screen.getByRole("combobox")).toBe(editor);
    fireEvent.keyDown(editor, { key: "Enter" });
    await waitFor(() => expect(playButtons()).toHaveLength(1));
  });

  it.each(["empty", "unknown"] as const)("retains draft context through an authoritative %s handoff", async (kind) => {
    const shared = props();
    const view = render(<TranscriptView {...shared} live={live()} disableSegmentPlayback />);
    openText();
    const editor = screen.getByRole("textbox", { name: "Line text" });
    fireEvent.change(editor, { target: { value: "Keep these words" } });
    const settled = kind === "empty" ? [] : [{ ...lines[0], speaker: "UNKNOWN", provisional: false },
      { ...lines[1], speaker: "SPEAKER_1", provisional: false }];
    view.rerender(<TranscriptView {...shared} segments={settled} />);
    expect(screen.getByRole("textbox", { name: "Line text" })).toBe(editor);
    expect(editor).toHaveValue("Keep these words");
    if (kind === "empty") expect(screen.getByText(
      "This line is no longer in the current transcript. Your draft is kept.",
    )).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Line text" })).not.toBeInTheDocument());
    expect(screen.queryByText("Alpha beta.")).not.toBeInTheDocument();
    if (kind === "empty") expect(screen.getByText("No transcript segments")).toBeInTheDocument();
  });
});
