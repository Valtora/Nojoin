import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RecordingTranscriptWithLiveBody from "@/test/RecordingTranscriptWithLiveBody";
import type { LiveTranscript } from "./transcript/_hooks/useLiveTranscript";
import { ClientStatus, type Recording, RecordingStatus } from "@/types";

const getDocuments = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocuments: (...args: unknown[]) => getDocuments(...args),
}));
vi.mock("./MeetingEdgePanel", () => ({
  default: () => <div data-testid="meeting-edge-panel" />,
}));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(), recordingId: null, pausedRecording: null, runtimeActive: false,
  }),
}));

const buildRecording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-05-26T10:00:00Z", updated_at: "2026-05-26T10:00:00Z",
  name: "Pipeline recording", meeting_uid: "meeting-1", audio_path: "/tmp/audio.wav",
  status: RecordingStatus.PROCESSING, upload_progress: 100, processing_progress: 48,
  processing_step: "Catching up speaker windows...", is_archived: false, is_deleted: false,
  ...overrides,
});
// A capture's transcript row: "processing" from capture start until its run publishes.
const captureTranscript = {
  id: 1, recording_id: "rec-1", created_at: "2026-05-26T10:00:00Z", updated_at: "2026-05-26T10:00:00Z",
  segments: [], transcript_status: "processing",
};

const feed: LiveTranscript = {
  segments: [{ id: "line-1", start: 0, end: 2, text: "The meeting continues", speaker: "UNKNOWN" }],
  speakers: [], hasLoaded: true, liveSpeakerNames: {}, refresh: vi.fn(), applyLiveSpeakerNames: vi.fn(),
};
const show = (recording = buildRecording()) =>
  render(<RecordingTranscriptWithLiveBody recording={recording} liveTranscript={feed} onSaveMeetingEdgeFocus={vi.fn()} />);

describe("RecordingTranscriptWithLiveBody shared-shell body", () => {
  beforeEach(() => {
    getDocuments.mockReset().mockResolvedValue([]);
  });

  it("retains Meeting Edge and My notes in their panels while progress occupies the header", () => {
    show();
    expect(screen.getByTestId("meeting-edge-panel")).toBeInTheDocument();
    expect(screen.getByTestId("my-notes")).toBeInTheDocument();
    expect(screen.getByText("The meeting continues")).toBeInTheDocument();
    expect(screen.queryByText("Progress")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to Recordings" })).not.toBeInTheDocument();
  });

  it.each([
    ["recording", RecordingStatus.UPLOADING, ClientStatus.RECORDING, false],
    ["paused", RecordingStatus.PAUSED, ClientStatus.PAUSED, true],
    ["uploading", RecordingStatus.UPLOADING, ClientStatus.UPLOADING, false],
    ["queued", RecordingStatus.QUEUED, ClientStatus.IDLE, false],
    ["processing", RecordingStatus.PROCESSING, ClientStatus.IDLE, false],
  ] as const)("keeps the supplied transcript visible while %s", (_, status, client_status, paused) => {
    show(buildRecording({ status, client_status, transcript: captureTranscript }));
    expect(screen.getByText("The meeting continues")).toBeInTheDocument();
    expect(screen.getByTestId("live-transcript-panel")).toHaveAttribute("data-paused", String(paused));
  });

  it("keeps transcript identity across Stop and pipeline updates", () => {
    const view = show(buildRecording({ status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING }));
    const transcript = screen.getByTestId("live-transcript-panel");
    view.rerender(<RecordingTranscriptWithLiveBody recording={buildRecording({ status: RecordingStatus.QUEUED })}
      liveTranscript={feed} onSaveMeetingEdgeFocus={vi.fn()} />);
    expect(screen.getByTestId("live-transcript-panel")).toBe(transcript);
    expect(transcript).toHaveTextContent("The meeting continues");
  });

  it("shows the empty Documents state when nothing is attached", async () => {
    show();
    await waitFor(() => expect(getDocuments).toHaveBeenCalledWith("rec-1"));
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
  });

  it("refreshes attached documents from the header upload without reopening the page", async () => {
    show();
    await waitFor(() => expect(getDocuments).toHaveBeenCalledTimes(1));
    getDocuments.mockResolvedValue([{ id: 7, title: "Agenda.pdf", original_filename: "Agenda.pdf",
      status: "PENDING", pages_parsed: 0, created_at: "2026-05-26T10:00:00Z" }]);
    act(() => window.dispatchEvent(new CustomEvent("recording-documents-updated", {
      detail: { recordingId: "another-recording" },
    })));
    expect(getDocuments).toHaveBeenCalledTimes(1);
    act(() => window.dispatchEvent(new CustomEvent("recording-documents-updated", {
      detail: { recordingId: "rec-1" },
    })));
    expect(await screen.findByText("Agenda.pdf")).toBeInTheDocument();
    expect(screen.getByText("Starting")).toBeInTheDocument();
    expect(getDocuments).toHaveBeenCalledTimes(2);
  });

  it("keeps the Meeting Edge panel when disabled and preserves the composer", () => {
    render(<RecordingTranscriptWithLiveBody recording={buildRecording()} liveTranscript={feed}
      onSaveMeetingEdgeFocus={vi.fn()} showMeetingEdge={false} />);
    expect(screen.getByTestId("meeting-edge-panel")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "New note" })).toBeInTheDocument();
  });

  it("still recognises a capture paused before client status was recorded", () => {
    show(buildRecording({ status: RecordingStatus.PAUSED, client_status: undefined }));
    expect(screen.getByTestId("live-transcript-panel")).toHaveAttribute("data-paused", "true");
    expect(screen.getByRole("textbox", { name: "New note" })).toBeInTheDocument();
  });

  it("shows the in-flight feed and general composer for a file import", () => {
    show(buildRecording({ status: RecordingStatus.UPLOADING, client_status: undefined }));
    expect(screen.getByText("The meeting continues")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "New note" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Place note" })).toHaveValue("head");
  });
});
