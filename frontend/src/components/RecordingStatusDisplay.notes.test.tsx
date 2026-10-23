import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { AudioClock } from "@/lib/notes/audioClock";
import { type OutboxEntry } from "@/lib/notes/noteOutbox";
import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";
import {
  ClientStatus,
  RecordingStatus,
  type Recording,
  type TimelineNote,
  type TimelineNoteCreate,
} from "@/types";

import RecordingStatusDisplay from "./RecordingStatusDisplay";

const getTranscriptUtterances = vi.fn();
const createTimelineNote = vi.fn();

vi.mock("@/lib/api/transcript", () => ({
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocuments: () => Promise.resolve([]),
  createTimelineNote: (...args: unknown[]) => createTimelineNote(...args),
  deleteTimelineNote: () => Promise.resolve(),
}));

vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("./LiveAudioWaveform", () => ({ default: () => <div /> }));
vi.mock("./LiveMeetingControls", () => ({ default: () => <div /> }));
vi.mock("./MeetingEdgePanel", () => ({ default: () => <div /> }));
// The Notes box, reduced to the meeting-wide notes it is handed to list.
vi.mock("./HeadNotePanel", () => ({
  default: ({ pendingNotes = [] }: { pendingNotes?: readonly OutboxEntry[] }) => (
    <div data-testid="notes-box">
      {pendingNotes.map((entry) => (
        <p key={entry.key} data-testid="notes-box-pending">
          {entry.kind}: {entry.body}
        </p>
      ))}
    </div>
  ),
}));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(),
    recordingId: null,
    pausedRecording: null,
    runtimeActive: false,
  }),
}));

const NO_ANSWER = { isAxiosError: true, message: "Network Error" };

const buildRecording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1",
  created_at: "2026-10-21T10:00:00Z",
  updated_at: "2026-10-21T10:00:00Z",
  name: "Weekly sync",
  meeting_uid: "meeting-1",
  audio_path: "/tmp/audio.wav",
  status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING,
  live_audio_ms: 61_000,
  is_archived: false,
  is_deleted: false,
  ...overrides,
});

const stopped = () =>
  buildRecording({
    status: RecordingStatus.PROCESSING,
    client_status: undefined,
    live_audio_ms: null,
    duration_seconds: 600,
  });

const savedNote = (payload: TimelineNoteCreate): TimelineNote => ({
  id: payload.id!,
  recording_id: "rec-1",
  kind: payload.kind,
  body: payload.body,
  anchor_ms: payload.anchor_ms ?? null,
  revision: 1,
  last_write_token: payload.write_token ?? null,
  created_at: "2026-10-21T10:01:00",
  updated_at: "2026-10-21T10:01:00",
});

let monotonicMs = 0;

const renderLive = (
  recording = buildRecording(),
  timelineNotes: readonly TimelineNote[] | null = [],
) => {
  const clock = new AudioClock(() => monotonicMs);
  clock.observe(recording);
  const view = render(
    <RecordingStatusDisplay
      recording={recording}
      onSaveMeetingEdgeFocus={vi.fn()}
      audioClock={clock}
      timelineNotes={timelineNotes}
    />,
  );
  const show = async (next: Recording) => {
    clock.observe(next);
    await act(async () => {
      view.rerender(
        <RecordingStatusDisplay
          recording={next}
          onSaveMeetingEdgeFocus={vi.fn()}
          audioClock={clock}
          timelineNotes={timelineNotes}
        />,
      );
    });
  };
  return { ...view, clock, show };
};

const box = () => screen.getByRole("textbox", { name: "New note" });

const addNote = (text: string) => {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
};

describe("RecordingStatusDisplay live notes", () => {
  beforeEach(() => {
    forgetNoteOutboxes();
    monotonicMs = 0;
    getTranscriptUtterances.mockReset();
    getTranscriptUtterances.mockResolvedValue({
      recording_id: "rec-1",
      revision: 1,
      utterances: [
        { id: "u1", start: 50, end: 58, start_ms: 50_000, end_ms: 58_000, text: "We need the budget.", speaker: "UNKNOWN", revision: 1 },
        { id: "u2", start: 60, end: 66, start_ms: 60_000, end_ms: 66_000, text: "Legal signs off Friday.", speaker: "UNKNOWN", revision: 1 },
      ],
      tombstones: [],
      speakers: [],
    });
    createTimelineNote.mockReset();
  });

  it("anchors a note on the capture clock at the first keystroke and shows it after that line", async () => {
    createTimelineNote.mockImplementation(async (_id: string, payload: TimelineNoteCreate) =>
      savedNote(payload),
    );
    renderLive();
    await screen.findByText("Legal signs off Friday.");

    monotonicMs = 1_500; // 1.5 s after the read: the clock has run on.
    fireEvent.change(box(), { target: { value: "B" } });
    monotonicMs = 30_000;
    fireEvent.change(box(), { target: { value: "Budget approved" } });
    fireEvent.keyDown(box(), { key: "Enter" });

    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));
    expect(createTimelineNote.mock.calls[0][1]).toMatchObject({
      kind: "point",
      body: "Budget approved",
      anchor_ms: 62_500,
    });
    const items = screen.getAllByRole("listitem").map((item) => item.textContent ?? "");
    const noteAt = items.findIndex((text) => text.includes("Budget approved"));
    expect(items[noteAt - 1]).toContain("Legal signs off Friday.");
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Note added at 01:02.");
  });

  it("empties the box in the same change that queues its note", async () => {
    createTimelineNote.mockImplementation(async (_id: string, payload: TimelineNoteCreate) =>
      savedNote(payload),
    );
    renderLive();
    await screen.findByText("We need the budget.");
    // A kept copy of the outbox is written from each change: none may hold
    // both the note and the text it was sent from.
    const outbox = noteOutboxFor("rec-1");
    const both: boolean[] = [];
    const stop = outbox.subscribe(() => {
      const { entries, draft } = outbox.getSnapshot();
      both.push(entries.length > 0 && draft.text !== "");
    });

    addNote("Budget approved");
    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));
    stop();

    expect(both.length).toBeGreaterThan(0);
    expect(both).not.toContain(true);
  });

  it("before the clock's first read, anchors a note at the latest line heard", async () => {
    createTimelineNote.mockImplementation(async (_id: string, payload: TimelineNoteCreate) =>
      savedNote(payload),
    );
    // A clock that has read nothing yet: the page had no answer from the server.
    render(
      <RecordingStatusDisplay
        recording={buildRecording()}
        onSaveMeetingEdgeFocus={vi.fn()}
        audioClock={new AudioClock(() => monotonicMs)}
        timelineNotes={[]}
      />,
    );
    await screen.findByText("Legal signs off Friday.");

    addNote("Budget approved");

    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));
    expect(createTimelineNote.mock.calls[0][1]).toMatchObject({ kind: "point", anchor_ms: 60_000 });
  });

  it("keeps a note whose save failed where it was written, and retries it under the same id", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    renderLive();
    await screen.findByText("We need the budget.");

    addNote("Ask about headcount");

    const note = await screen.findByText("Ask about headcount");
    const line = note.closest("[data-testid='timeline-note']") as HTMLElement;
    await within(line).findByText("Not saved: no answer from Nojoin.");
    expect(box()).toHaveValue("");
    // Said in the box's live status line, which was there before it failed.
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Note at 01:01 not saved.");

    createTimelineNote.mockImplementationOnce(async (_id: string, payload: TimelineNoteCreate) =>
      savedNote(payload),
    );
    const retry = within(line).getByRole("button", { name: /^Retry note/ });
    retry.focus();
    fireEvent.click(retry);
    // Retry hides its own button: focus stays on the note.
    expect(document.activeElement).toBe(line);

    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(2));
    expect(createTimelineNote.mock.calls[1][1]).toEqual(createTimelineNote.mock.calls[0][1]);
    await waitFor(() =>
      expect(
        screen.getByText("Ask about headcount").closest("[data-testid='timeline-note']"),
      ).toHaveAttribute("data-note-status", "saved"),
    );
  });

  it("names each failed note's buttons after the note, and returns focus to the box on Discard", async () => {
    createTimelineNote.mockRejectedValue(NO_ANSWER);
    renderLive();
    await screen.findByText("We need the budget.");
    addNote("First note");
    addNote("Second note");
    await waitFor(() =>
      expect(screen.getAllByText("Not saved: no answer from Nojoin.")).toHaveLength(2),
    );

    const names = screen
      .getAllByRole("button", { name: /^Retry note/ })
      .map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual([
      "Retry note at 01:01, “First note”",
      "Retry note at 01:01, “Second note”",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Discard note at 01:01, “First note”" }));
    expect(screen.queryByText("First note")).toBeNull();
    expect(document.activeElement).toBe(box());
  });

  it("keeps a half-typed note when capture stops, and adds it at its anchor from the progress card", async () => {
    createTimelineNote.mockImplementation(async (_id: string, payload: TimelineNoteCreate) =>
      savedNote(payload),
    );
    const { show } = renderLive();
    await screen.findByText("We need the budget.");
    monotonicMs = 2_000;
    fireEvent.change(box(), { target: { value: "Half a thought about the budget" } });

    // Stop, pressed here or on another device: the next poll ends the live view.
    await show(stopped());

    const draft = screen.getByTestId("unsaved-draft");
    expect(draft).toHaveTextContent("Half a thought about the budget");
    expect(draft).toHaveTextContent("Not sent: at 01:03");
    fireEvent.click(within(draft).getByRole("button", { name: /^Add note/ }));

    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));
    expect(createTimelineNote.mock.calls[0][1]).toMatchObject({
      kind: "point",
      body: "Half a thought about the budget",
      anchor_ms: 63_000,
    });
    expect(screen.queryByTestId("unsaved-draft")).toBeNull();
  });

  it("still offers a failed note once capture has stopped", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    const { show } = renderLive();
    await screen.findByText("We need the budget.");
    addNote("Send the deck");
    await screen.findByText("Not saved: no answer from Nojoin.");

    await show(stopped());

    expect(screen.getByText("Notes not saved yet")).toBeInTheDocument();
    const unsaved = screen.getByTestId("unsaved-note");
    expect(unsaved).toHaveTextContent("Send the deck");
    expect(
      within(unsaved).getByRole("button", { name: "Retry note at 01:01, “Send the deck”" }),
    ).toBeInTheDocument();
  });

  it("moves focus to the next unsaved note on Discard, and to the progress card after the last", async () => {
    createTimelineNote.mockRejectedValue(NO_ANSWER);
    const { show } = renderLive();
    await screen.findByText("We need the budget.");
    addNote("First note");
    addNote("Second note");
    await waitFor(() =>
      expect(screen.getAllByText("Not saved: no answer from Nojoin.")).toHaveLength(2),
    );
    await show(stopped());

    const [, second] = screen.getAllByTestId("unsaved-note");
    fireEvent.click(screen.getByRole("button", { name: "Discard note at 01:01, “First note”" }));
    expect(document.activeElement).toBe(second);

    fireEvent.click(screen.getByRole("button", { name: "Discard note at 01:01, “Second note”" }));
    expect(screen.queryByTestId("unsaved-note")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Progress" }));
  });

  it("lists a meeting-wide note on its way with the Notes box, not in the live card", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    renderLive();
    await screen.findByText("We need the budget.");
    fireEvent.change(screen.getByLabelText("Place note"), { target: { value: "tail" } });
    addNote("Follow up with legal");

    const pending = await screen.findByTestId("notes-box-pending");
    expect(pending).toHaveTextContent("tail: Follow up with legal");
    expect(
      within(screen.getByTestId("live-note-composer")).queryByText("Follow up with legal"),
    ).toBeNull();
  });

  it("puts a note on a line from its menu, and keeps a failed one with the lines, not the Notes box", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    const { show } = renderLive();
    await screen.findByText("Legal signs off Friday.");

    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 01:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(box()).toHaveFocus();
    expect(screen.getByTestId("note-selection-chip")).toHaveTextContent("Legal signs off Friday.");
    addNote("Who signs?");

    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));
    expect(createTimelineNote.mock.calls[0][1]).toMatchObject({
      kind: "selection",
      body: "Who signs?",
      start_ms: 60_000,
      end_ms: 66_000,
      quoted_text: "Legal signs off Friday.",
    });
    const note = await screen.findByTestId("live-transcript-note");
    expect(note).toHaveTextContent("Not saved: no answer from Nojoin.");
    expect(within(note).getByTestId("timeline-note-quote")).toHaveTextContent("Legal signs off Friday.");
    expect(screen.queryByTestId("notes-box-pending")).toBeNull();

    await show(stopped());
    expect(screen.getByTestId("unsaved-note")).toHaveTextContent("Who signs?");
    expect(screen.queryByTestId("notes-box-pending")).toBeNull();
  });

  it("holds the Notes box back until the recording's notes have loaded", async () => {
    renderLive(buildRecording(), null);
    await screen.findByText("We need the budget.");
    expect(screen.queryByTestId("notes-box")).toBeNull();
    expect(box()).toBeInTheDocument();
  });
});
