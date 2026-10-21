import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { AudioClock } from "@/lib/notes/audioClock";
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
vi.mock("./HeadNotePanel", () => ({ default: () => <div /> }));
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
  timeline_notes: [],
  is_archived: false,
  is_deleted: false,
  ...overrides,
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

const renderLive = (recording = buildRecording()) => {
  const clock = new AudioClock(() => monotonicMs);
  clock.observe(recording);
  const view = render(
    <RecordingStatusDisplay
      recording={recording}
      onSaveMeetingEdgeFocus={vi.fn()}
      audioClock={clock}
    />,
  );
  return { ...view, clock };
};

const box = () => screen.getByRole("textbox", { name: "New note" });

const addNote = (text: string) => {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
};

describe("RecordingStatusDisplay live notes", () => {
  beforeEach(() => {
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

    createTimelineNote.mockImplementationOnce(async (_id: string, payload: TimelineNoteCreate) =>
      savedNote(payload),
    );
    fireEvent.click(within(line).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(2));
    expect(createTimelineNote.mock.calls[1][1]).toEqual(createTimelineNote.mock.calls[0][1]);
    await waitFor(() =>
      expect(
        screen.getByText("Ask about headcount").closest("[data-testid='timeline-note']"),
      ).toHaveAttribute("data-note-status", "saved"),
    );
  });

  it("still offers a failed note once capture has stopped", async () => {
    createTimelineNote.mockRejectedValueOnce(NO_ANSWER);
    const { rerender, clock } = renderLive();
    await screen.findByText("We need the budget.");
    addNote("Send the deck");
    await screen.findByText("Not saved: no answer from Nojoin.");

    await act(async () => {
      rerender(
        <RecordingStatusDisplay
          recording={buildRecording({
            status: RecordingStatus.PROCESSING,
            client_status: undefined,
            live_audio_ms: null,
          })}
          onSaveMeetingEdgeFocus={vi.fn()}
          audioClock={clock}
        />,
      );
    });

    expect(screen.getByText("Notes not saved yet")).toBeInTheDocument();
    const unsaved = screen.getByTestId("unsaved-note");
    expect(unsaved).toHaveTextContent("Send the deck");
    expect(within(unsaved).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
