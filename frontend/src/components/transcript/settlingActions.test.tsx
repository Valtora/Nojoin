import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import LiveNoteComposer from "@/components/notes/LiveNoteComposer";
import { applyTranscriptDelta } from "@/lib/transcriptState";
import type { TranscriptSegment, TranscriptUtteranceList } from "@/types";
import type { LiveLineHandlers } from "./LiveLineEditor";

const line = (locked: boolean): TranscriptSegment => ({
  id: "u1", revision: 3, start: 0, end: 2, speaker: "LIVE_PROVISIONAL_1", text: "Alpha beta.",
  provisional: true, settling_required: true, locked, text_locked: locked,
  words: [
    { id: "w1", order: 0, word: "Alpha", start_ms: 0, end_ms: 500, locked },
    { id: "w2", order: 1, word: "beta", start_ms: 700, end_ms: 1600, locked },
    { id: "w3", order: 2, word: ".", start_ms: 1600, end_ms: 1600, locked, join_left: true },
  ],
});
const handlers = (): LiveLineHandlers => ({
  editText: vi.fn().mockResolvedValue(true), changeSpeaker: vi.fn().mockResolvedValue(true),
  split: vi.fn().mockResolvedValue(true),
});
const open = () => fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));

describe("settling line actions", () => {
  it("keeps typed notes available beneath an unlocked active line", () => {
    const submit = vi.fn(() => null);
    render(<LiveTranscriptPanel segments={[line(false)]} hasLoaded
      footer={<LiveNoteComposer readAudioNow={() => 1500} onSubmit={submit} />} />);
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "My thought" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(submit).toHaveBeenCalledWith({ kind: "point", body: "My thought", anchorMs: 1500 });
  });
  it("offers corrections on an open line, and its note once the line locks", () => {
    const actions = handlers();
    const note = vi.fn();
    const delta = (locked: boolean): TranscriptUtteranceList => ({
      recording_id: "r1", revision: locked ? 4 : 3, settling_required: true,
      utterances: [{ ...line(locked), id: "u1", revision: locked ? 4 : 3 }], speakers: [], tombstones: [],
    });
    const initial = applyTranscriptDelta({ currentState: null, recordingId: "r1", fallbackSegments: [], delta: delta(false), mode: "full" });
    const view = render(<LiveTranscriptPanel segments={initial.segments} hasLoaded onNoteOnLine={note} lineHandlers={actions} />);
    expect(screen.getByTestId("live-transcript-line")).toHaveAccessibleDescription("This line is still settling.");
    expect(screen.queryByText("Settling…")).not.toBeInTheDocument();
    open();
    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(4);
    for (const item of items) {
      expect(item).toHaveAttribute("aria-disabled", item.textContent?.startsWith("Note on this line") ? "true" : "false");
    }
    fireEvent.click(items[0]);
    expect(note).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    expect(screen.getByRole("textbox", { name: "Line text" })).toHaveValue("Alpha beta.");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Line text" }), { key: "Escape" });
    const locked = applyTranscriptDelta({ currentState: initial, recordingId: "r1", fallbackSegments: [], delta: delta(true) });
    view.rerender(<LiveTranscriptPanel segments={locked.segments} hasLoaded onNoteOnLine={note} lineHandlers={actions} />);
    expect(screen.queryByText("Settling…")).not.toBeInTheDocument();
    expect(screen.getByTestId("live-transcript-line")).not.toHaveAttribute("aria-describedby");
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(note).toHaveBeenCalledWith({ startMs: 0, endMs: 2000, quotedText: "Alpha beta." });
  });

  it("splits at an interior lexical word UUID, without offering punctuation or an untimed atom", async () => {
    const actions = handlers();
    render(<LiveTranscriptPanel segments={[line(true)]} hasLoaded onNoteOnLine={vi.fn()} lineHandlers={actions} />);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Split line" }));
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["beta"]);
    fireEvent.click(screen.getByRole("button", { name: "Split" }));
    await waitFor(() => expect(actions.split).toHaveBeenCalledWith(expect.objectContaining({ id: "u1", revision: 3 }), "w2"));
  });

  it("keeps an unmarked live line note-only even when it contains lock-like fields", () => {
    const legacy = { ...line(false), settling_required: undefined };
    render(<LiveTranscriptPanel segments={[legacy]} hasLoaded onNoteOnLine={vi.fn()} lineHandlers={handlers()} />);
    expect(screen.queryByText("Settling…")).not.toBeInTheDocument();
    open();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Note on this line"]);
    expect(screen.getByRole("menuitem")).toHaveAttribute("aria-disabled", "false");
  });

  it("keeps a failed correction in its editor and closes only once accepted", async () => {
    const actions = handlers();
    vi.mocked(actions.editText).mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    render(<LiveTranscriptPanel segments={[line(true)]} hasLoaded onNoteOnLine={vi.fn()} lineHandlers={actions} />);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Line text" }), { target: { value: "My correction" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
    expect(screen.getByRole("textbox")).toHaveValue("My correction");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("sends text against the revision the editor opened, and against the current one once that fails", async () => {
    const actions = handlers();
    vi.mocked(actions.editText).mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const first = line(false);
    const view = render(<LiveTranscriptPanel segments={[first]} hasLoaded onNoteOnLine={vi.fn()} lineHandlers={actions} />);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Line text" }), { target: { value: "My correction" } });
    view.rerender(<LiveTranscriptPanel segments={[{ ...first, revision: 9, text: "Another correction" }]}
      hasLoaded onNoteOnLine={vi.fn()} lineHandlers={actions} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(actions.editText).toHaveBeenCalledWith(expect.objectContaining({ revision: 3 }), "My correction"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: "Line text" })).toHaveValue("My correction");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(actions.editText).toHaveBeenLastCalledWith(
      expect.objectContaining({ revision: 9 }), "My correction"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("offers final wording once beside the retained protected text", () => {
    render(<LiveTranscriptPanel hasLoaded segments={[{ ...line(true), final_review: { text: "Final alternative", reason: "ambiguous_alignment" } }]} />);
    fireEvent.click(screen.getByText("Review final wording"));
    expect(screen.getByText("Final alternative")).toBeInTheDocument();
    expect(document.querySelectorAll("p[data-segment-id]")).toHaveLength(1);
    expect(document.querySelector("p[data-segment-id]")).toHaveTextContent("Alpha beta.");
  });

  it("shows the assigned recording speaker after a live correction, without using the live-label rename route", () => {
    render(<LiveTranscriptPanel hasLoaded segments={[{ ...line(true), speaker: "SPEAKER_0" }]}
      onRenameLiveSpeaker={vi.fn()} speakers={[{
        id: 1, recording_id: "r1", diarization_label: "SPEAKER_0", local_name: "Alex",
        created_at: "2026-10-26", updated_at: "2026-10-26",
      }]} />);
    expect(screen.getByTestId("live-transcript-speaker")).toHaveTextContent("Alex");
    expect(screen.queryByRole("button", { name: "Rename Alex" })).not.toBeInTheDocument();
  });

  it("names a speaker from an open line's header", async () => {
    const rename = vi.fn().mockResolvedValue(undefined);
    render(<LiveTranscriptPanel hasLoaded onRenameLiveSpeaker={rename} segments={[line(false)]} />);
    const chip = screen.getByRole("button", { name: "Rename Speaker 1" });
    expect(chip).toHaveAttribute("data-locked", "false");
    fireEvent.click(chip);
    const input = within(screen.getByTestId("live-transcript-scroll")).getByRole("combobox");
    fireEvent.change(input, { target: { value: "Alex" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(rename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Alex"));
    expect(rename).toHaveBeenCalledTimes(1);
  });
});
