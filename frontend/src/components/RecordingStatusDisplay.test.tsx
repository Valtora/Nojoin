import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import RecordingStatusDisplay from "./RecordingStatusDisplay";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

const getTranscriptUtterances = vi.fn();

vi.mock("@/lib/api/transcript", () => ({
  getTranscriptUtterances: (...args: unknown[]) =>
    getTranscriptUtterances(...args),
}));

vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

vi.mock("./LiveAudioWaveform", () => ({
  default: () => <div data-testid="live-audio-waveform" />,
}));

vi.mock("./LiveMeetingControls", () => ({
  default: () => <div data-testid="live-meeting-controls" />,
}));

vi.mock("./LiveTranscriptPanel", () => ({
  default: () => <div data-testid="live-transcript-panel" />,
}));

vi.mock("./MeetingEdgePanel", () => ({
  default: () => <div data-testid="meeting-edge-panel" />,
}));

vi.mock("./ProcessingNotesPanel", () => ({
  default: () => <div data-testid="processing-notes-panel" />,
}));

// RecordingStatusDisplay drives discard through useRecordingActions, which reads
// capture state; stub the provider so the component renders without one.
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(),
    recordingId: null,
    pausedRecording: null,
    runtimeActive: false,
  }),
}));

const buildRecording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1",
  created_at: "2026-05-26T10:00:00Z",
  updated_at: "2026-05-26T10:00:00Z",
  name: "Pipeline recording",
  meeting_uid: "meeting-1",
  audio_path: "/tmp/audio.wav",
  status: RecordingStatus.PROCESSING,
  upload_progress: 100,
  processing_progress: 48,
  processing_step: "Catching up speaker windows...",
  is_archived: false,
  is_deleted: false,
  ...overrides,
});

describe("RecordingStatusDisplay", () => {
  beforeEach(() => {
    getTranscriptUtterances.mockReset();
    getTranscriptUtterances.mockResolvedValue({
      recording_id: "rec-1",
      revision: 0,
      utterances: [],
      tombstones: [],
      speakers: [],
    });
  });

  it("renders Meeting Edge and notes panels by default", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording()}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    expect(screen.getByTestId("meeting-edge-panel")).toBeInTheDocument();
    expect(screen.getByTestId("processing-notes-panel")).toBeInTheDocument();
  });

  it("states the capture state without a headline", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording({
          status: RecordingStatus.RECORDING,
          client_status: ClientStatus.RECORDING,
        })}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    // The state is a word on the console, not a text-4xl sentence above it.
    expect(screen.getByText("Recording")).toBeInTheDocument();
    expect(
      screen.queryByText("Meeting is being recorded"),
    ).not.toBeInTheDocument();
  });

  it("puts pipeline progress where the transcript sits while recording", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording({
          status: RecordingStatus.PROCESSING,
          client_status: undefined,
          processing_progress: 40,
        })}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    // Pressing Stop reflows the middle column rather than re-laying out the
    // page, so the grid keeps its shape across the two states.
    expect(screen.queryByTestId("live-transcript-panel")).not.toBeInTheDocument();
    expect(screen.getByText("Progress")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    // The console keeps the state word; the panel does not repeat it.
    expect(screen.getAllByText("Processing")).toHaveLength(1);
  });

  it("hides Meeting Edge when disabled", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording()}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
        showMeetingEdge={false}
      />,
    );

    expect(screen.queryByTestId("meeting-edge-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("processing-notes-panel")).toBeInTheDocument();
  });

  it("renders a mobile back button when requested", () => {
    const onBack = vi.fn();

    render(
      <RecordingStatusDisplay
        recording={buildRecording()}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
        onBack={onBack}
        showMobileBackButton
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Back to Recordings" }));

    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("shows the live transcript panel during a browser capture", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording({
          status: RecordingStatus.UPLOADING,
          client_status: ClientStatus.RECORDING,
        })}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    expect(screen.getByTestId("live-transcript-panel")).toBeInTheDocument();
    expect(screen.getByTestId("live-audio-waveform")).toBeInTheDocument();
    expect(screen.getByTestId("meeting-edge-panel")).toBeInTheDocument();
  });

  it("hides the live transcript panel once processing starts", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording({
          status: RecordingStatus.QUEUED,
          client_status: ClientStatus.IDLE,
        })}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    expect(
      screen.queryByTestId("live-transcript-panel"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("meeting-edge-panel")).toBeInTheDocument();
  });

  it("treats an uploading file import as processing, not as a live capture", () => {
    // Imports also sit in UPLOADING but never carry a capture client status.
    // Before this was pinned, an import rendered the recording UI -- heading,
    // waveform and transport controls -- for the whole upload.
    render(
      <RecordingStatusDisplay
        recording={buildRecording({
          status: RecordingStatus.UPLOADING,
          client_status: undefined,
        })}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    expect(
      screen.queryByTestId("live-transcript-panel"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-audio-waveform")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("live-meeting-controls"),
    ).not.toBeInTheDocument();
    expect(getTranscriptUtterances).not.toHaveBeenCalled();
  });

  it("still recognises a capture paused before client status was recorded", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording({
          status: RecordingStatus.PAUSED,
          client_status: undefined,
        })}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    expect(screen.getByTestId("live-transcript-panel")).toBeInTheDocument();
  });
});
