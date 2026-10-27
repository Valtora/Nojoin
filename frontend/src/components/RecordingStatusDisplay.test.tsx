import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import RecordingStatusDisplay from "./RecordingStatusDisplay";
import type { LiveTranscript } from "./transcript/_hooks/useLiveTranscript";
import { ClientStatus, type Recording, RecordingStatus, type TranscriptSegment } from "@/types";

const getDocuments = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocuments: (...args: unknown[]) => getDocuments(...args),
}));
vi.mock("./LiveTranscriptPanel", () => ({
  default: ({ segments, isPaused, footer }: {
    segments: TranscriptSegment[]; isPaused: boolean; footer: ReactNode;
  }) => (
    <div data-testid="live-transcript-panel" data-paused={String(isPaused)}>
      {segments.map((segment) => <p key={segment.id}>{segment.text}</p>)}
      {footer}
    </div>
  ),
}));
vi.mock("./MeetingEdgePanel", () => ({
  default: () => <div data-testid="meeting-edge-panel" />,
}));
vi.mock("./ProcessingNotesPanel", () => ({
  default: () => <div data-testid="processing-notes-panel" />,
}));
vi.mock("./LiveDocumentsPanel", () => ({
  default: ({ documents }: { documents: { id: number; original_filename: string }[] }) => (
    <div data-testid="live-documents">{documents.map((document) => document.original_filename).join(",")}</div>
  ),
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

const feed: LiveTranscript = {
  segments: [{ id: "line-1", start: 0, end: 2, text: "The meeting continues", speaker: "UNKNOWN" }],
  speakers: [], hasLoaded: true, liveSpeakerNames: {}, refresh: vi.fn(), applyLiveSpeakerNames: vi.fn(),
};
const show = (recording = buildRecording()) =>
  render(<RecordingStatusDisplay recording={recording} liveTranscript={feed} onSaveMeetingEdgeFocus={vi.fn()} />);

describe("RecordingStatusDisplay shared-shell body", () => {
  beforeEach(() => {
    getDocuments.mockReset().mockResolvedValue([]);
  });

  it("retains Meeting Edge and notes while pipeline progress moves to the header", () => {
    show();
    expect(screen.getByTestId("meeting-edge-panel")).toBeInTheDocument();
    expect(screen.getByTestId("processing-notes-panel")).toBeInTheDocument();
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
    show(buildRecording({ status, client_status }));
    expect(screen.getByText("The meeting continues")).toBeInTheDocument();
    expect(screen.getByTestId("live-transcript-panel")).toHaveAttribute("data-paused", String(paused));
  });

  it("keeps transcript identity across Stop and pipeline updates", () => {
    const view = show(buildRecording({ status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING }));
    const transcript = screen.getByTestId("live-transcript-panel");
    view.rerender(<RecordingStatusDisplay recording={buildRecording({ status: RecordingStatus.QUEUED })}
      liveTranscript={feed} onSaveMeetingEdgeFocus={vi.fn()} />);
    expect(screen.getByTestId("live-transcript-panel")).toBe(transcript);
    expect(transcript).toHaveTextContent("The meeting continues");
  });

  it("does not render a documents panel with nothing attached", async () => {
    show();
    await waitFor(() => expect(getDocuments).toHaveBeenCalledWith("rec-1"));
    expect(screen.queryByTestId("live-documents")).not.toBeInTheDocument();
  });

  it("refreshes attached documents from the header upload without reopening the page", async () => {
    show();
    await waitFor(() => expect(getDocuments).toHaveBeenCalledTimes(1));
    getDocuments.mockResolvedValue([{ id: 7, original_filename: "Agenda.pdf" }]);
    act(() => window.dispatchEvent(new CustomEvent("recording-documents-updated", {
      detail: { recordingId: "another-recording" },
    })));
    expect(getDocuments).toHaveBeenCalledTimes(1);
    act(() => window.dispatchEvent(new CustomEvent("recording-documents-updated", {
      detail: { recordingId: "rec-1" },
    })));
    expect(await screen.findByText("Agenda.pdf")).toBeInTheDocument();
    expect(getDocuments).toHaveBeenCalledTimes(2);
  });

  it("hides Meeting Edge when disabled and preserves notes", () => {
    render(<RecordingStatusDisplay recording={buildRecording()} liveTranscript={feed}
      onSaveMeetingEdgeFocus={vi.fn()} showMeetingEdge={false} />);
    expect(screen.queryByTestId("meeting-edge-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("processing-notes-panel")).toBeInTheDocument();
  });

  it("still recognises a capture paused before client status was recorded", () => {
    show(buildRecording({ status: RecordingStatus.PAUSED, client_status: undefined }));
    expect(screen.getByTestId("live-transcript-panel")).toHaveAttribute("data-paused", "true");
    expect(screen.getByRole("textbox", { name: "New note" })).toBeInTheDocument();
  });

  it("shows the in-flight feed for a file import without adding a capture composer", () => {
    show(buildRecording({ status: RecordingStatus.UPLOADING, client_status: undefined }));
    expect(screen.getByText("The meeting continues")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "New note" })).not.toBeInTheDocument();
  });
});
