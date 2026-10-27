import { createRef, type ComponentProps } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import type { LiveTranscript } from "@/components/transcript/_hooks/useLiveTranscript";
import { RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

const editText = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(), updateTranscriptUtteranceText: editText,
}));
const line: TranscriptSegment = {
  id: "u1", revision: 3, start: 0, end: 2, speaker: "LIVE_PROVISIONAL_1", text: "Live speech",
  provisional: true, settling_required: true, locked: true,
};
const feed: LiveTranscript = {
  segments: [line], speakers: [], hasLoaded: true, liveSpeakerNames: { LIVE_PROVISIONAL_1: "Alex" },
  refresh: vi.fn(), applyLiveSpeakerNames: vi.fn(),
};
const base = (): ComponentProps<typeof TranscriptSection> => ({
  active: true, recording: { id: "rec-1", status: RecordingStatus.PAUSED, speakers: [] } as unknown as Recording,
  timelineNotes: [], transcriptSegments: [line], currentTime: -1, isPlaying: false,
  speakerMap: {}, speakerColors: {}, globalSpeakers: [], canUndo: false, canRedo: false,
  deferredTranscriptUtteranceIds: [], onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(),
  onRenameSpeaker: vi.fn(), onRefresh: vi.fn(), onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
  onFindAndReplace: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(), onExport: vi.fn(),
  onActiveEditUtteranceChange: vi.fn(), liveTranscript: feed, canPlayback: false, canExport: false,
});

describe("shared section in-flight source", () => {
  it("keeps a retained published transcript read-only under the physical processing guard", () => {
    const props = base();
    render(<TranscriptSection {...props} liveTranscript={undefined}
      recording={{ ...props.recording, previous_publication_available: true, status: RecordingStatus.PROCESSING }} />);
    expect(screen.getByText("Live speech")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Actions for Speaker 1/ }));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Note on this line"]);
    expect(screen.queryByRole("button", { name: "Rename Speaker 1" })).not.toBeInTheDocument();
    expect(props.onUpdateSegmentText).not.toHaveBeenCalled();
    expect(props.onRenameSpeaker).not.toHaveBeenCalled();
  });

  it("renders the supplied feed in shared cards with the composer beneath it", () => {
    const props = base();
    render(<TranscriptSection {...props} inFlightContent={<textarea aria-label="New note" />} />);
    expect(screen.getByText("Live speech").closest('[data-ui-reading="bubble"]')).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rename Alex" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Play / })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Export transcript disabled" })).toBeDisabled();
    const transcript = screen.getByText("Live speech");
    const composer = screen.getByRole("textbox", { name: "New note" });
    expect(transcript.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("targets the carried composer from a line without waiting for its lock", () => {
    const boxRef = createRef<HTMLTextAreaElement>();
    const props = base();
    render(<TranscriptSection {...props} liveNoteBoxRef={boxRef}
      transcriptSegments={[{ ...line, locked: false }]} inFlightContent={<textarea ref={boxRef} aria-label="New note" />} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(screen.getByRole("textbox", { name: "New note" })).toHaveFocus();
  });

  it("keeps an edit and its live mutation revision through Stop and the settled handoff", async () => {
    editText.mockReset().mockRejectedValueOnce({ response: { status: 409, data: { detail: "Stale revision" } } })
      .mockResolvedValueOnce({});
    const props = base();
    const view = render(<TranscriptSection {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    const editor = screen.getByRole("textbox", { name: "Line text" });
    fireEvent.change(editor, { target: { value: "My correction" } });
    view.rerender(<TranscriptSection {...props} recording={{ ...props.recording, status: RecordingStatus.PROCESSING }} />);
    expect(screen.getByRole("textbox", { name: "Line text" })).toBe(editor);
    view.rerender(<TranscriptSection {...props} liveTranscript={undefined} canPlayback canExport
      recording={{ ...props.recording, status: RecordingStatus.PROCESSED }}
      transcriptSegments={[{ ...line, revision: 9, provisional: false }]} />);
    expect(screen.getByRole("textbox", { name: "Line text" })).toBe(editor);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(editText).toHaveBeenCalledTimes(1));
    expect(editText).toHaveBeenLastCalledWith("rec-1", "u1", "My correction", 3);
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
    expect(editor).toHaveValue("My correction");
    expect(props.onRefresh).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(editText).toHaveBeenLastCalledWith("rec-1", "u1", "My correction", 3);
    expect(props.onUpdateSegmentText).not.toHaveBeenCalled();
    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });

  it("keeps only the open draft context after an authoritative empty handoff, then shows the empty result", () => {
    const props = base();
    const view = render(<TranscriptSection {...props} />);
    const anchor = screen.getByRole("button", { name: "Actions for the line at 00:00" });
    fireEvent.click(anchor);
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    const editor = screen.getByRole("textbox", { name: "Line text" });
    fireEvent.change(editor, { target: { value: "My unsaved correction" } });
    view.rerender(<TranscriptSection {...props} liveTranscript={undefined} transcriptSegments={[]}
      transcriptIsAuthoritative recording={{ ...props.recording, status: RecordingStatus.PROCESSED,
        transcript: { transcript_status: "completed", text: "Stale metadata words" } as Recording["transcript"] }} />);
    expect(screen.getByRole("textbox", { name: "Line text" })).toBe(editor);
    expect(anchor).toBeInTheDocument();
    expect(editor).toHaveValue("My unsaved correction");
    expect(screen.getByText("This line is no longer in the current transcript. Your draft is kept.")).toBeInTheDocument();
    expect(screen.queryByText("Stale metadata words")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Live speech")).not.toBeInTheDocument();
    expect(screen.getByText("No speech was detected in this recording.")).toBeInTheDocument();
    expect(screen.getByTestId("transcript-scroll-region")).toHaveFocus();
  });
});
