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

// The documents list is fetched by the view now that the upload action lives on
// the toolbar rather than inside the panel. Partial mock: DocumentUploadModal
// imports from the same module.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocuments: () => Promise.resolve([]),
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
          status: RecordingStatus.UPLOADING,
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

  it("does not render a documents panel with nothing attached", async () => {
    render(
      <RecordingStatusDisplay
        recording={buildRecording()}
        onSaveProcessingNotes={vi.fn()}
        onSaveMeetingEdgeFocus={vi.fn()}
      />,
    );

    // Uploading moved to the capture toolbar, so an empty panel would have
    // nothing left to offer.
    await vi.waitFor(() => {
      expect(screen.queryByText("Documents")).not.toBeInTheDocument();
    });
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

  // DESIGN.md gives every column exactly one module that absorbs the leftover
  // height. On the left, which module that is changes with the state, and
  // getting it wrong is what stretched a progress bar and two lines of status
  // into a box most of a screen tall. Asserted against the class rather than a
  // measurement because jsdom lays nothing out, and the class is the contract.
  describe("column height absorption", () => {
    const wrapperOf = (testId: string) =>
      screen.getByTestId(testId).parentElement as HTMLElement;

    it("gives the transcript the leftover height while recording", () => {
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

      expect(wrapperOf("live-transcript-panel").className).toContain("flex-1");
      expect(wrapperOf("processing-notes-panel").className).not.toContain(
        "flex-1",
      );
    });

    it("hands it to the notes editor once recording stops", () => {
      render(
        <RecordingStatusDisplay
          recording={buildRecording({
            status: RecordingStatus.PROCESSING,
            client_status: undefined,
          })}
          onSaveProcessingNotes={vi.fn()}
          onSaveMeetingEdgeFocus={vi.fn()}
        />,
      );

      expect(wrapperOf("processing-notes-panel").className).toContain("flex-1");
      // The progress card cannot use height: it is a bar and two lines.
      expect(
        screen.getByText("Progress").closest("section")?.parentElement
          ?.className,
      ).not.toContain("flex-1");
    });

    it("lets Meeting Edge absorb rather than set the row", () => {
      render(
        <RecordingStatusDisplay
          recording={buildRecording()}
          onSaveProcessingNotes={vi.fn()}
          onSaveMeetingEdgeFocus={vi.fn()}
        />,
      );

      // Only from 54rem: below it this is one item in a stack, and the page
      // scrolls rather than the panel.
      expect(wrapperOf("meeting-edge-panel").className).toContain(
        "@min-[54rem]:flex-1",
      );
    });

    it("bounds the grid row against the window", () => {
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

      // Neither column can bound the other without this. A scroll container
      // needs a definite height to scroll against, and an auto grid row takes
      // the max-content of its items, so removing the ceiling on one column
      // without bounding the row just moves which panel grows without limit.
      const grid = wrapperOf("meeting-edge-panel").parentElement as HTMLElement;

      expect(grid.className).toContain("@min-[54rem]:min-h-0");
      expect(grid.className).toContain("@min-[54rem]:grid-rows-[minmax(0,1fr)]");
    });

    it("caps no module against the viewport", () => {
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

      // A max-height on a module in an items-stretch grid relocates the surplus
      // row height instead of removing it, which is the dead corner the column
      // model exists to prevent.
      expect(wrapperOf("live-transcript-panel").className).not.toMatch(/max-h-/);
      expect(wrapperOf("meeting-edge-panel").className).not.toMatch(/max-h-/);
      expect(wrapperOf("processing-notes-panel").className).not.toMatch(
        /max-h-/,
      );
    });
  });
});
