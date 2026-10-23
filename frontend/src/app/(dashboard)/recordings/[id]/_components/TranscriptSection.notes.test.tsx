import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";
import { RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

import TranscriptSection from "./TranscriptSection";

const createTimelineNote = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createTimelineNote: (...args: unknown[]) => createTimelineNote(...args),
  deleteTimelineNote: () => Promise.resolve(),
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const originalScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
});

const NO_ANSWER = { isAxiosError: true, message: "Network Error" };

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "Good morning" },
  { id: "u1", speaker: "SPEAKER_1", start: 65, end: 67.5, text: "Forty thousand for the venue" },
];

const RECORDING = {
  id: "rec-1",
  name: "Weekly sync",
  status: RecordingStatus.PROCESSED,
  duration_seconds: 600,
  speakers: [],
} as unknown as Recording;

const renderSection = (transcriptSegments: TranscriptSegment[] = SEGMENTS) =>
  render(
    <TranscriptSection
      active
      recording={RECORDING}
      timelineNotes={[]}
      transcriptSegments={transcriptSegments}
      currentTime={-1}
      isPlaying={false}
      speakerMap={{ SPEAKER_0: "Alex", SPEAKER_1: "Blair" }}
      speakerColors={{}}
      globalSpeakers={[]}
      canUndo={false}
      canRedo={false}
      deferredTranscriptUtteranceIds={[]}
      onPlaySegment={vi.fn()}
      onPause={vi.fn()}
      onResume={vi.fn()}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      onExport={vi.fn()}
      onActiveEditUtteranceChange={vi.fn()}
    />,
  );

describe("a note on a line of the processed transcript", () => {
  beforeEach(() => {
    forgetNoteOutboxes();
    createTimelineNote.mockReset();
  });

  it("goes out through the recording's outbox, and a failed one waits on its line with Retry", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    renderSection();

    fireEvent.click(screen.getByRole("button", { name: "Actions for Blair's line at 01:05" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    const box = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(box, { target: { value: "Check the figure" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));
    expect(createTimelineNote.mock.calls[0][1]).toMatchObject({
      kind: "selection",
      body: "Check the figure",
      start_ms: 65_000,
      end_ms: 67_500,
      quoted_text: "Forty thousand for the venue",
    });
    const note = await screen.findByTestId("timeline-note");
    await within(note).findByText("Not saved: no answer from Nojoin.");
    expect(within(note).getByRole("button", { name: /^Retry note/ })).toBeInTheDocument();
    // Shown on its line, so not listed again above the transcript.
    expect(screen.queryByTestId("unsaved-notes")).toBeNull();

    createTimelineNote.mockImplementationOnce(async (_id: string, payload: { id: string }) => ({
      ...payload,
      recording_id: "rec-1",
      revision: 1,
      created_at: "2026-10-22T10:00:00",
      updated_at: "2026-10-22T10:00:00",
    }));
    fireEvent.click(within(note).getByRole("button", { name: /^Retry note/ }));
    await waitFor(() =>
      expect(screen.getByTestId("timeline-note")).toHaveAttribute("data-note-status", "saved"),
    );
    expect(createTimelineNote.mock.calls[1][1].id).toBe(createTimelineNote.mock.calls[0][1].id);
  });

  it("puts focus on the transcript when a failed note on a line is discarded", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    renderSection();

    fireEvent.click(screen.getByRole("button", { name: "Actions for Blair's line at 01:05" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    const box = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(box, { target: { value: "Check the figure" } });
    fireEvent.keyDown(box, { key: "Enter" });
    const note = await screen.findByTestId("timeline-note");
    const discard = await within(note).findByRole("button", { name: /^Discard note/ });
    discard.focus();
    fireEvent.click(discard);

    await waitFor(() => expect(screen.queryByTestId("timeline-note")).toBeNull());
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toContainElement(screen.getByText("Forty thousand for the venue"));
  });

  it("is listed above the transcript, with Retry, when there are no lines to show it on", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    noteOutboxFor("rec-1").submit({
      kind: "selection",
      body: "Check the figure",
      anchorMs: null,
      selection: { startMs: 65_000, endMs: 67_500, quotedText: "Forty thousand for the venue" },
    });
    renderSection([]);

    const listed = await screen.findByTestId("unsaved-note");
    await within(listed).findByText("Not saved: no answer from Nojoin.");
    expect(listed).toHaveTextContent("Check the figure");
    expect(within(listed).getByRole("button", { name: /^Retry note/ })).toBeInTheDocument();
  });
});
