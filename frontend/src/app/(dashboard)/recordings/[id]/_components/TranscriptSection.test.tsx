import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";
import {
  RecordingStatus,
  type Recording,
  type TranscriptSegment,
} from "@/types";

vi.mock("@/components/TranscriptView", () => ({
  default: () => <div data-testid="transcript-view" />,
}));
// The retry dialog's own reads; nothing is sent in these tests.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getSettings: vi.fn().mockResolvedValue({}),
  getLanguageOptions: vi.fn().mockReturnValue(new Promise(() => {})),
  reprocessRecording: vi.fn(),
}));

import TranscriptSection from "@/test/TranscriptSectionWithEdits";

const OOM_MESSAGE =
  "Transcription failed: the GPU ran out of memory (CUDA out of memory) while running parakeet.";
const NO_SPEECH = "No speech was detected in this recording.";
const NOT_YET = "No transcript available yet.";

const recordingWith = (
  transcript: Partial<Recording["transcript"]>,
  status: RecordingStatus = RecordingStatus.PROCESSED,
) =>
  ({
    id: "rec-1",
    name: "Weekly sync",
    status,
    speakers: [],
    transcript: { segments: [], ...transcript },
  }) as unknown as Recording;

const failedRecording = (transcript: Partial<Recording["transcript"]> = {}) =>
  recordingWith(
    { transcript_status: "error", error_message: OOM_MESSAGE, ...transcript },
    RecordingStatus.ERROR,
  );

const renderSection = (
  recording: Recording,
  transcriptSegments: TranscriptSegment[] = [],
  transcriptIsAuthoritative = false,
  inFlightContent?: ReactNode,
) =>
  renderWithProviders(
    <TranscriptSection
      active
      recording={recording}
      transcriptSegments={transcriptSegments}
      transcriptIsAuthoritative={transcriptIsAuthoritative}
      inFlightContent={inFlightContent}
      currentTime={0}
      isPlaying={false}
      speakerMap={{}}
      speakerColors={{}}
      globalSpeakers={[]}
      canUndo={false}
      canRedo={false}
      deferredTranscriptUtteranceIds={[]}
      onPlaySegment={vi.fn()}
      onPause={vi.fn()}
      onResume={vi.fn()}
      onRenameSpeaker={vi.fn()}
      onRefresh={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      onExport={vi.fn()}
      onActiveEditUtteranceChange={vi.fn()}
    />,
  );

describe("TranscriptSection transcription failure", () => {
  it("reports why transcription failed instead of an empty transcript", () => {
    renderSection(failedRecording());

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Transcription failed");
    expect(alert).toHaveTextContent(OOM_MESSAGE);
    expect(alert).not.toHaveTextContent(/recordings list/);
    expect(screen.queryByText(NOT_YET)).not.toBeInTheDocument();
    expect(screen.queryByText(NO_SPEECH)).not.toBeInTheDocument();
  });

  it("retries from the recording's own page, as a retry rather than a reprocess", async () => {
    renderSection(failedRecording());

    fireEvent.click(screen.getByRole("button", { name: "Retry processing" }));

    expect(await screen.findByRole("dialog", { name: "Retry processing" })).toBeInTheDocument();
    expect(screen.queryByText(/replaces the current transcript/)).not.toBeInTheDocument();
  });

  it("warns that a retry replaces a transcript still shown from an earlier run", async () => {
    const kept = { start: 0, end: 1, speaker: "SPEAKER_00", text: "kept" } as TranscriptSegment;
    renderSection(failedRecording(), [kept]);

    fireEvent.click(screen.getByRole("button", { name: "Retry processing" }));

    expect(await screen.findByText(/replaces the current transcript/)).toBeInTheDocument();
  });

  it("reports a failure at a later step with the step it stopped at", () => {
    renderSection({
      ...recordingWith({ transcript_status: "pending" }, RecordingStatus.ERROR),
      processing_step: "System Error: diarisation stopped",
    } as Recording);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Processing failed");
    expect(alert).toHaveTextContent("System Error: diarisation stopped");
    expect(screen.getByRole("button", { name: "Retry processing" })).toBeInTheDocument();
    expect(screen.queryByText(NOT_YET)).not.toBeInTheDocument();
  });

  it("keeps the provisional transcript visible under the failure notice", () => {
    const liveSegment = {
      start: 0,
      end: 1,
      speaker: "UNKNOWN",
      text: "hello",
    } as TranscriptSegment;

    renderSection(failedRecording(), [liveSegment]);

    expect(screen.getByRole("alert")).toHaveTextContent(OOM_MESSAGE);
    expect(screen.getByTestId("transcript-view")).toBeInTheDocument();
  });

  it("keeps transcript text without segments visible under the notice", () => {
    renderSection(failedRecording({ text: "hello from the live lane" }));

    expect(screen.getByRole("alert")).toHaveTextContent(OOM_MESSAGE);
    expect(screen.getByText("hello from the live lane")).toBeInTheDocument();
  });

  it("reports failure while the last feed is retained after a failed settled read", () => {
    renderSection(failedRecording(), [], false, <p>Retained live words</p>);

    expect(screen.getByRole("alert")).toHaveTextContent(OOM_MESSAGE);
    expect(screen.getByText("Retained live words")).toBeInTheDocument();
  });

  it("drops the notice once the recording is being processed again", () => {
    renderSection(
      recordingWith(
        { transcript_status: "error", error_message: OOM_MESSAGE },
        RecordingStatus.PROCESSING,
      ),
    );

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
describe("TranscriptSection empty states", () => {
  it("does not revive stale metadata text after an authoritative empty handoff", () => {
    renderSection(recordingWith({ transcript_status: "completed", text: "Stale live words" }), [], true);

    expect(screen.queryByText("Stale live words")).not.toBeInTheDocument();
    expect(screen.getByText(NO_SPEECH)).toBeInTheDocument();
  });
  it("says no speech was detected for a completed, empty transcript", () => {
    renderSection(recordingWith({ transcript_status: "completed", text: "" }));

    expect(screen.getByText(NO_SPEECH)).toBeInTheDocument();
    expect(screen.queryByText(NOT_YET)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says the transcript is not available yet while it is still pending", () => {
    renderSection(recordingWith({ transcript_status: "pending" }));

    expect(screen.getByText(NOT_YET)).toBeInTheDocument();
    expect(screen.queryByText(NO_SPEECH)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
