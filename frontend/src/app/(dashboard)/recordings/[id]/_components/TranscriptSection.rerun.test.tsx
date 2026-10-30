import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";

import { useRecordingTranscript } from "../_hooks/useRecordingTranscript";

// Only the network is stubbed: the page's readers and the shared section are real.
const getTranscriptUtterances = vi.fn();
vi.mock("@/lib/api/transcript", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/transcript")>()),
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
}));

const noop = () => {};
const transcriptRow = (transcript_status: string) => ({
  id: 1, recording_id: "rec-1", created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z",
  segments: [], transcript_status, notes_status: "completed",
});
const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z",
  name: "Example planning call", meeting_uid: "meeting-1", audio_path: "/tmp/audio.wav",
  status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE,
  upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false, speakers: [],
  transcript: transcriptRow("completed"),
  ...overrides,
});
const lines = (count = 6): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision: count, tombstones: [], speakers: [],
  utterances: Array.from({ length: count }, (_, index) => ({
    id: `line-${index}`, revision: index + 1, start: index * 5, end: index * 5 + 4,
    text: `Example line ${index + 1}.`, speaker: `SPEAKER_0${index % 2}`,
  })),
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/** The Transcript tab as the recording page wires it: its readers, then the shared section. */
function TranscriptTab({ recording }: { recording: Recording }) {
  const { liveTranscript, usesInFlightTranscript, transcriptSegments, transcriptIsAuthoritative,
    hasRetainedPublication } = useRecordingTranscript(recording, noop);
  return <TranscriptSection active recording={recording}
    liveTranscript={usesInFlightTranscript ? liveTranscript : undefined}
    transcriptIsAuthoritative={transcriptIsAuthoritative} hasRetainedPublication={hasRetainedPublication}
    transcriptSegments={transcriptSegments} timelineNotes={[]} currentTime={-1} isPlaying={false}
    speakerMap={{}} speakerColors={{}} globalSpeakers={[]} canUndo={false} canRedo={false}
    deferredTranscriptUtteranceIds={[]} canPlayback={false} canExport={false}
    onPlaySegment={noop} onPause={noop} onResume={noop} onRenameSpeaker={noop} onRefresh={noop}
    onUpdateSegmentSpeaker={noop} onUpdateSegmentText={noop} onFindAndReplace={noop}
    onUndo={noop} onRedo={noop} onExport={noop} onActiveEditUtteranceChange={noop} />;
}

const settle = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

/** The finished presentation: its lines, none of the live view's notes, no following the tail. */
const expectFinishedView = () => {
  expect(screen.getByText("Example line 1.")).toBeInTheDocument();
  expect(screen.getByText("Example line 6.")).toBeInTheDocument();
  expect(screen.getByTestId("transcript-scroll-region")).toBeInTheDocument();
  expect(screen.queryByTestId("live-transcript-panel")).not.toBeInTheDocument();
  expect(screen.queryByText(/Provisional\. Corrected when the recording is processed/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Lines settle as the conversation continues/)).not.toBeInTheDocument();
};

/** Publication: the feed still answers, and the settled read waits until released. */
const holdSettledRead = () => {
  const settledRead = deferred<TranscriptUtteranceList>();
  // The feed passes its abort signal; the settled reader does not.
  getTranscriptUtterances.mockImplementation((_id: string, _after?: number, signal?: AbortSignal) =>
    signal ? Promise.resolve(lines()) : settledRead.promise);
  return settledRead;
};

describe("transcript tab while a finished meeting reruns (#860)", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    vi.useFakeTimers();
    getTranscriptUtterances.mockReset().mockResolvedValue(lines());
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it.each([
    ["its notes regenerate", "Generating meeting notes...", "generating"],
    ["its speakers are inferred again", "Inferring speakers...", "completed"],
  ])("opens it while %s in the finished view, and keeps it through publication", async (_, step, notesStatus) => {
    const rerun = recording({
      status: RecordingStatus.PROCESSING, processing_step: step,
      transcript: { ...transcriptRow("completed"), notes_status: notesStatus },
    });
    const view = render(<TranscriptTab recording={rerun} />);
    await settle();
    // The feed's lines, read as the finished transcript they are.
    expect(getTranscriptUtterances).toHaveBeenCalledWith("rec-1", undefined, expect.any(AbortSignal));
    expectFinishedView();

    const settledRead = holdSettledRead();
    view.rerender(<TranscriptTab recording={recording()} />);
    await settle();
    expectFinishedView();
    settledRead.resolve(lines());
    await settle();
    expectFinishedView();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("keeps an open finished meeting's view through a rerun and its publication", async () => {
    const view = render(<TranscriptTab recording={recording()} />);
    await settle();
    expectFinishedView();

    view.rerender(<TranscriptTab recording={recording({
      status: RecordingStatus.PROCESSING, processing_step: "Inferring speakers...",
    })} />);
    await settle(3_000);
    expectFinishedView();

    const settledRead = holdSettledRead();
    view.rerender(<TranscriptTab recording={recording()} />);
    await settle();
    expectFinishedView();
    settledRead.resolve(lines());
    await settle();
    expectFinishedView();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it.each([
    ["while it records", RecordingStatus.UPLOADING, ClientStatus.RECORDING],
    ["while it is paused", RecordingStatus.PAUSED, ClientStatus.PAUSED],
    ["after Stop, while its own run processes", RecordingStatus.PROCESSING, ClientStatus.IDLE],
  ])("keeps the live presentation for a capture opened %s", async (_, status, clientStatus) => {
    render(<TranscriptTab recording={recording({
      status, client_status: clientStatus, transcript: transcriptRow("processing"),
    })} />);
    await settle();
    expect(screen.getByTestId("live-transcript-panel")).toBeInTheDocument();
    expect(screen.getByText("Provisional. Corrected when the recording is processed.")).toBeInTheDocument();
    expect(screen.getByText("Example line 1.")).toBeInTheDocument();
  });
});
