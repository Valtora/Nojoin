import { render, screen } from "@testing-library/react";
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
  it("surfaces collapsed pipeline lane visibility when window state is available", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording({
          pipeline_state: {
            transcript_revision: 42,
            total_window_count: 4,
            sealed_window_count: 2,
            partial_window_count: 1,
            first_sequence: 0,
            latest_sequence: 6,
            asr: {
              total_windows: 4,
              processed_windows: 3,
              processing_windows: 0,
              failed_windows: 0,
              pending_windows: 1,
              coverage_ratio: 0.75,
              status_counts: { live_processed: 3, pending: 1 },
            },
            diarization: {
              total_windows: 4,
              processed_windows: 1,
              processing_windows: 1,
              failed_windows: 1,
              pending_windows: 1,
              coverage_ratio: 0.25,
              status_counts: { processed: 1, processing: 1, failed: 1, pending: 1 },
            },
          },
        })}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    expect(screen.getByTestId("pipeline-visibility")).toBeInTheDocument();
    expect(screen.getByText("Processing details")).toBeInTheDocument();
    expect(screen.getByText("Rev 42")).toBeInTheDocument();
    expect(screen.getByText("Sequences 0-6")).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-lane-live-asr")).toHaveTextContent(
      "3/4 windows complete",
    );
    expect(screen.getByTestId("pipeline-lane-speaker-windows")).toHaveTextContent(
      "1/4 windows complete",
    );
  });

  it("keeps processing details hidden when no pipeline state is available", () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording()}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("pipeline-visibility")).not.toBeInTheDocument();
  });
});