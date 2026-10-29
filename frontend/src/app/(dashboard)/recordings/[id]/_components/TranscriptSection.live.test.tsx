import { createRef, type ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import type { LiveTranscript } from "@/components/transcript/_hooks/useLiveTranscript";
import { RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

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
  // The page merges live names into the map.
  speakerMap: { LIVE_PROVISIONAL_1: "Alex" }, speakerColors: {}, globalSpeakers: [], canUndo: false, canRedo: false,
  deferredTranscriptUtteranceIds: [], onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(),
  onRenameSpeaker: vi.fn(), onRefresh: vi.fn(), onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
  onFindAndReplace: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(), onExport: vi.fn(),
  onActiveEditUtteranceChange: vi.fn(), liveTranscript: feed, canPlayback: false, canExport: false,
});

describe("shared section in-flight source", () => {
  it("offers a supplied pending name during capture and removes it once capture stops", () => {
    const props = base();
    const liveTranscript = { ...feed, liveSpeakerNames: {}, liveSpeakerSuggestions: { LIVE_PROVISIONAL_1: "Alex" } };
    const { rerender } = render(<TranscriptSection {...props} speakerMap={{}} liveTranscript={liveTranscript} />);
    expect(screen.getByText("Alex?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rename Speaker 1" })).toBeInTheDocument();
    rerender(<TranscriptSection {...props} speakerMap={{}} liveTranscript={liveTranscript}
      recording={{ ...props.recording, status: RecordingStatus.QUEUED }} />);
    expect(screen.queryByText("Alex?")).not.toBeInTheDocument();
  });

  it("keeps a retained published transcript read-only under the physical processing guard", () => {
    const props = base();
    render(<TranscriptSection {...props} liveTranscript={undefined} speakerMap={{}}
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

  it("targets the carried composer from a locked line", () => {
    const boxRef = createRef<HTMLTextAreaElement>();
    const props = base();
    render(<TranscriptSection {...props} liveNoteBoxRef={boxRef}
      transcriptSegments={[{ ...line, locked: true }]} inFlightContent={<textarea ref={boxRef} aria-label="New note" />} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(screen.getByRole("textbox", { name: "New note" })).toHaveFocus();
  });
});
