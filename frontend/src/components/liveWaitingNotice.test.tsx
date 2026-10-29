import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import RecordingStatusDisplay from "./RecordingStatusDisplay";
import { LIVE_TRANSCRIPT_POLL_INTERVAL_MS } from "./transcript/_hooks/useLiveTranscript";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

// The live read's `live_waiting` comes from the real endpoint
// (backend/tests/test_live_waiting_api.py); here only the network is stubbed.
const getTranscriptUtterances = vi.fn();

vi.mock("@/lib/api/transcript", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/transcript")>()),
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
}));

vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("./LiveAudioWaveform", () => ({ default: () => null }));
vi.mock("./LiveMeetingControls", () => ({ default: () => null }));
vi.mock("./MeetingEdgePanel", () => ({ default: () => null }));
vi.mock("./ProcessingNotesPanel", () => ({ default: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocuments: () => Promise.resolve([]),
}));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ cancel: vi.fn(), recordingId: null, pausedRecording: null, runtimeActive: false }),
}));

const NOTICE = "Live transcript is waiting for the server to finish another task. Nothing is lost; it will catch up.";

const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1",
  created_at: "2026-10-29T10:00:00Z",
  updated_at: "2026-10-29T10:00:00Z",
  name: "Live meeting",
  meeting_uid: "meeting-1",
  audio_path: "/tmp/audio.wav",
  status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING,
  upload_progress: 10,
  processing_progress: 0,
  is_archived: false,
  is_deleted: false,
  ...overrides,
});

const read = (liveWaiting: boolean, texts: string[] = []) => ({
  recording_id: "rec-1",
  revision: texts.length,
  utterances: texts.map((text, index) => ({
    id: `line-${index}`, start: index, end: index + 1, start_ms: index * 1000, end_ms: (index + 1) * 1000,
    text, speaker: "UNKNOWN", state: "provisional", revision: index + 1, provisional: true, segment_source: "live",
  })),
  tombstones: [],
  speakers: [],
  ...(liveWaiting ? { live_waiting: true } : {}),
});

const poll = () => act(async () => { await vi.advanceTimersByTimeAsync(LIVE_TRANSCRIPT_POLL_INTERVAL_MS); });
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });

describe("live transcript waiting notice", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getTranscriptUtterances.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("appears while the live read says it is waiting and clears when lines arrive", async () => {
    getTranscriptUtterances
      .mockResolvedValueOnce(read(true))
      .mockResolvedValueOnce(read(false, ["Caught up at last."]));
    render(<RecordingStatusDisplay recording={recording()} onSaveMeetingEdgeFocus={vi.fn()} />);
    await settle();
    expect(screen.getByText(NOTICE)).toHaveAttribute("role", "status");

    await poll();
    expect(screen.getByText("Caught up at last.")).toBeInTheDocument();
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });

  it("does not show on a paused capture, even before the next read", async () => {
    getTranscriptUtterances.mockResolvedValue(read(true));
    const view = render(<RecordingStatusDisplay recording={recording()} onSaveMeetingEdgeFocus={vi.fn()} />);
    await settle();
    expect(screen.getByText(NOTICE)).toBeInTheDocument();

    view.rerender(
      <RecordingStatusDisplay
        recording={recording({ status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED })}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });
});
