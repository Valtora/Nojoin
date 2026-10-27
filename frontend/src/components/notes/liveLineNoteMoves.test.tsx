import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RecordingStatusDisplay from "@/components/RecordingStatusDisplay";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import { resetNotesServer, server } from "@/test/fakeNotesServer";
import { ClientStatus, RecordingStatus, type Recording, type TimelineNote, type TimelineNoteUpdate } from "@/types";

const getTranscriptUtterances = vi.fn();
const updateTimelineNote = vi.fn();

vi.mock("@/lib/api/transcript", () => ({
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
  getDocuments: () => Promise.resolve([]),
  updateTimelineNote: (...args: unknown[]) => updateTimelineNote(...args),
}));
vi.mock("@/components/Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/LiveAudioWaveform", () => ({ default: () => <div /> }));
vi.mock("@/components/LiveMeetingControls", () => ({ default: () => <div /> }));
vi.mock("@/components/MeetingEdgePanel", () => ({ default: () => <div /> }));
vi.mock("@/components/HeadNotePanel", () => ({ default: () => <div /> }));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ cancel: vi.fn(), recordingId: null, pausedRecording: null, runtimeActive: false }),
}));

const RECORDING: Recording = {
  id: "rec-1", name: "Live meeting", meeting_uid: "live-meeting", audio_path: "/tmp/live.wav",
  created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING,
  is_archived: false, is_deleted: false,
};

function LivePage() {
  const noteEdits = useNoteEdits(RECORDING.id);
  const applied = noteEdits.apply([...server.notes.values()]);
  return <>
    <RecordingStatusDisplay recording={RECORDING} onSaveMeetingEdgeFocus={vi.fn()}
      timelineNotes={[]} noteEdits={noteEdits} showMeetingEdge={false} />
    <output data-testid="applied-span">{applied.map((note) => `${note.start_ms}:${note.end_ms}`).join(",")}</output>
  </>;
}

const originalElementFromPoint = document.elementFromPoint;
let under: Element | null = null;

beforeEach(() => {
  forgetNoteOutboxes();
  resetNotesServer();
  updateTimelineNote.mockReset();
  under = null;
  document.elementFromPoint = () => under;
});
afterEach(() => {
  document.elementFromPoint = originalElementFromPoint;
});

const loadLines = (marked: boolean) => {
  getTranscriptUtterances.mockResolvedValue({ recording_id: RECORDING.id, revision: 8,
    ...(marked ? { settling_required: true } : {}), tombstones: [], speakers: [], utterances: [
      { id: "u1", revision: 3, start: 12, end: 15.5, text: "Budget still growing", speaker: "UNKNOWN",
        ...(marked ? { settling_required: true, provisional: true, locked: false } : {}) },
      { id: "u2", revision: 4, start: 20, end: 25, text: "Legal still considering", speaker: "UNKNOWN",
        ...(marked ? { settling_required: true, provisional: true, locked: false } : {}) },
    ] });
};

describe("moving a newly saved note during live capture", () => {
  it("takes an open line span through the composer and PATCHes its drag onto another open line", async () => {
    loadLines(true);
    let answer!: (note: TimelineNote) => void;
    updateTimelineNote.mockImplementation(() => new Promise<TimelineNote>((resolve) => { answer = resolve; }));
    render(<LivePage />);
    await screen.findByText("Budget still growing");
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:12" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(screen.getByTestId("note-line-chip")).toHaveTextContent("00:12–00:15");
    const box = screen.getByRole("textbox", { name: "New note" });
    fireEvent.change(box, { target: { value: "Check this figure" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(server.creates).toHaveLength(1));
    expect(server.creates[0]).toMatchObject({ kind: "point", anchor_ms: 12_000,
      start_ms: 12_000, end_ms: 15_500, body: "Check this figure" });
    const note = (await screen.findByText("Check this figure")).closest<HTMLElement>('[data-testid="timeline-note"]')!;
    await waitFor(() => expect(note).toHaveAttribute("data-note-status", "saved"));
    expect(within(note).queryByTestId("timeline-note-quote")).toBeNull();
    const grip = note.querySelector<HTMLElement>("[data-note-grip]")!;
    expect(grip).toBeInTheDocument();
    fireEvent.pointerDown(grip, { pointerType: "mouse", button: 0 });
    under = document.querySelector('[data-order-index="1"]');
    expect(under).toBeInTheDocument();
    fireEvent.pointerMove(window, { clientX: 20, clientY: 40, buttons: 1 });
    fireEvent.pointerUp(window);
    await waitFor(() => expect(updateTimelineNote).toHaveBeenCalledTimes(1));
    const created = [...server.notes.values()][0];
    expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", created.id, {
      expected_revision: 1, anchor_ms: 20_000, target_utterance_id: "u2", target_utterance_revision: 4,
    } satisfies TimelineNoteUpdate);
    expect(screen.getByTestId("applied-span")).toHaveTextContent("20000:23500");
    await act(async () => answer({ ...created, anchor_ms: 20_000, start_ms: 20_000, end_ms: 23_500, revision: 2 }));
    expect(screen.getByTestId("applied-span")).toHaveTextContent("20000:23500");
    const saved = screen.getByText("Check this figure").closest<HTMLElement>('[data-testid="timeline-note"]')!;
    expect(saved).toHaveTextContent("00:20");
    expect(within(saved).queryByTestId("timeline-note-quote")).toBeNull();
    const rows = Array.from(screen.getByTestId("live-transcript-scroll")
      .querySelectorAll('[data-order-index], [data-testid="timeline-note"]'));
    expect(rows.indexOf(saved)).toBeGreaterThan(rows.indexOf(under!));
  });

  it("keeps unmarked live point notes without move controls", async () => {
    loadLines(false);
    render(<LivePage />);
    await screen.findByText("Budget still growing");
    const box = screen.getByRole("textbox", { name: "New note" });
    fireEvent.change(box, { target: { value: "Plain live note" } });
    fireEvent.keyDown(box, { key: "Enter" });
    const note = (await screen.findByText("Plain live note")).closest<HTMLElement>('[data-testid="timeline-note"]')!;
    await waitFor(() => expect(note).toHaveAttribute("data-note-status", "saved"));
    expect(server.creates[0]).toMatchObject({ kind: "point" });
    expect(server.creates[0]).not.toHaveProperty("start_ms");
    expect(note.querySelector("[data-note-grip]")).toBeNull();
    expect(within(note).queryByRole("button", { name: /^Move note/ })).toBeNull();
    expect(updateTimelineNote).not.toHaveBeenCalled();
  });
});
