import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import RecordingStatusDisplay from "./RecordingStatusDisplay";
import { Recording, RecordingStatus } from "@/types";

vi.mock("./AmbientWorkspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

vi.mock("./LiveAudioWaveform", () => ({
  default: () => <div data-testid="live-audio-waveform" />,
}));

vi.mock("./LiveMeetingControls", () => ({
  default: () => <div data-testid="live-meeting-controls" />,
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
});
