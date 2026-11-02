import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import RecordingCaptureSlot from "@/components/RecordingCaptureSlot";
import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import { useRecordingTranscript } from "../_hooks/useRecordingTranscript";

// Only the network is stubbed: the page's reader and the shared section are real.
const getTranscriptUtterances = vi.fn();
vi.mock("@/lib/api/transcript", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/transcript")>()),
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getUnfinishedCaptures: async () => [],
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings/rec-1",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: noop }),
}));

const noop = () => {};
const transcriptRow = (transcript_status: string) => ({
  id: 1, recording_id: "rec-1", created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z",
  segments: [], transcript_status,
});
const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z",
  name: "Example standup", meeting_uid: "meeting-1", audio_path: "/tmp/audio.wav",
  status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING,
  upload_progress: 10, processing_progress: 0, is_archived: false, is_deleted: false, speakers: [],
  transcript: transcriptRow("processing"),
  ...overrides,
});
const emptyRead = { recording_id: "rec-1", revision: 0, utterances: [], tombstones: [], speakers: [] };

/** The Transcript tab as the recording page wires it: its reader, then the shared section. */
function TranscriptTab({ recording }: { recording: Recording }) {
  const { liveTranscript, usesInFlightTranscript, transcriptSegments } = useRecordingTranscript(recording, noop);
  return <TranscriptSection active recording={recording}
    liveTranscript={usesInFlightTranscript ? liveTranscript : undefined}
    transcriptSegments={transcriptSegments} timelineNotes={[]} currentTime={-1} isPlaying={false}
    speakerMap={{}} speakerColors={{}} globalSpeakers={[]} canUndo={false} canRedo={false}
    deferredTranscriptUtteranceIds={[]} canPlayback={false} canExport={false}
    onPlaySegment={noop} onPause={noop} onResume={noop} onRenameSpeaker={noop} onRefresh={noop}
    onUpdateSegmentSpeaker={noop} onUpdateSegmentText={noop} onFindAndReplace={noop}
    onUndo={noop} onRedo={noop} onExport={noop} onActiveEditUtteranceChange={noop} />;
}

const settle = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe("transcript tab while a recording is in flight (#835)", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    vi.useFakeTimers();
    // What the endpoint answers before a transcript exists.
    getTranscriptUtterances.mockReset().mockRejectedValue(
      Object.assign(new Error("Transcript not found"), { response: { status: 404 } }));
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it.each([
    ["queued", RecordingStatus.QUEUED],
    ["processing", RecordingStatus.PROCESSING],
  ])("shows an import %s as processing, with no live poll", async (_, status) => {
    render(<TranscriptTab recording={recording({ status, client_status: ClientStatus.IDLE, transcript: undefined })} />);
    await settle(10_000);
    expect(screen.getByText("No transcript available yet.")).toBeInTheDocument();
    expect(screen.queryByText("Listening.")).not.toBeInTheDocument();
    expect(screen.queryByText(/transcript so far/i)).not.toBeInTheDocument();
    expect(getTranscriptUtterances).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("shows a reprocess stuck in the queue as processing, not listening", async () => {
    getTranscriptUtterances.mockResolvedValue(emptyRead);
    render(<TranscriptTab recording={recording({
      status: RecordingStatus.QUEUED, client_status: ClientStatus.IDLE, transcript: transcriptRow("pending"),
    })} />);
    await settle(10_000);
    expect(screen.queryByText("Listening.")).not.toBeInTheDocument();
    expect(screen.queryByText(/a few seconds behind the conversation/i)).not.toBeInTheDocument();
    expect(getTranscriptUtterances).not.toHaveBeenCalled();
  });

  it("listens while a capture records or pauses, and stops saying so at Stop", async () => {
    getTranscriptUtterances.mockResolvedValue(emptyRead);
    const view = render(<TranscriptTab recording={recording()} />);
    await settle();
    expect(screen.getByText("Listening.")).toBeInTheDocument();
    expect(screen.getByText(/a few seconds behind the conversation/i)).toBeInTheDocument();

    view.rerender(<TranscriptTab recording={recording({
      status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED,
    })} />);
    expect(screen.getByText("Recording is paused.")).toBeInTheDocument();

    view.rerender(<TranscriptTab recording={recording({
      status: RecordingStatus.QUEUED, client_status: ClientStatus.IDLE,
    })} />);
    await settle(3_000);
    // Its feed is still read: the live lines stay until the processed ones replace them.
    expect(getTranscriptUtterances).toHaveBeenLastCalledWith("rec-1", 0, expect.any(AbortSignal));
    expect(screen.queryByText("Listening.")).not.toBeInTheDocument();
    expect(screen.getByText("No transcript available yet.")).toBeInTheDocument();
  });

  it("shows kept settled lines on a fresh open during reprocessing", async () => {
    getTranscriptUtterances.mockResolvedValue({
      ...emptyRead, revision: 4, settling_required: true,
      utterances: [
        { id: "fence-b", revision: 2, start: 1, end: 9, text: "first", speaker: "UNKNOWN", locked: true },
        { id: "fence-c", revision: 3, start: 10, end: 14, text: "middle", speaker: "UNKNOWN", locked: true },
        { id: "fence-a", revision: 4, start: 20, end: 29, text: "Accepted correction", speaker: "UNKNOWN", locked: true },
      ],
    });
    const reprocessing = recording({
      status: RecordingStatus.PROCESSING, client_status: ClientStatus.IDLE,
      previous_publication_available: false,
      transcript: { ...transcriptRow("pending"), settling_required: true },
    });
    render(<CaptureProvider>
      <RecordingCaptureSlot recording={reprocessing} />
      <TranscriptTab recording={reprocessing} />
    </CaptureProvider>);
    await settle();
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText("middle")).toBeInTheDocument();
    expect(screen.getByText("Accepted correction")).toBeInTheDocument();
    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.getByText("Preparing your meeting transcript.")).toBeInTheDocument();
    expect(screen.queryByText("Listening.")).not.toBeInTheDocument();
    expect(screen.queryByText(/a few seconds behind the conversation/i)).not.toBeInTheDocument();
    expect(consoleError).not.toHaveBeenCalled();
  });
});
