import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import { LIVE_TRANSCRIPT_POLL_INTERVAL_MS } from "@/components/transcript/_hooks/useLiveTranscript";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import { useRecordingTranscript } from "../_hooks/useRecordingTranscript";

// The live read's `live_behind` comes from the real endpoint
// (backend/tests/test_live_behind_api.py); here only the network is stubbed.
const getTranscriptUtterances = vi.fn();

vi.mock("@/lib/api/transcript", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/transcript")>()),
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
}));

const NOTICE = "Live transcript is behind the recording. Nothing is lost: the full transcript is made when the meeting ends.";
const noop = () => {};

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
  speakers: [],
  ...overrides,
});

const read = (liveBehind: boolean, texts: string[] = []) => ({
  recording_id: "rec-1",
  revision: texts.length,
  utterances: texts.map((text, index) => ({
    id: `line-${index}`, start: index, end: index + 1, start_ms: index * 1000, end_ms: (index + 1) * 1000,
    text, speaker: "UNKNOWN", state: "provisional", revision: index + 1, provisional: true, segment_source: "live",
  })),
  tombstones: [],
  speakers: [],
  ...(liveBehind ? { live_behind: true } : {}),
});

/** The Transcript tab as the recording page wires it: its reader, then the shared section. */
function LiveTranscriptTab({ recording }: { recording: Recording }) {
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

const poll = () => act(async () => { await vi.advanceTimersByTimeAsync(LIVE_TRANSCRIPT_POLL_INTERVAL_MS); });
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });

describe("live transcript behind notice", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getTranscriptUtterances.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("appears while the live read says it is behind and clears when lines arrive", async () => {
    getTranscriptUtterances
      .mockResolvedValueOnce(read(true))
      .mockResolvedValueOnce(read(false, ["Caught up at last."]));
    render(<LiveTranscriptTab recording={recording()} />);
    await settle();
    expect(screen.getByText(NOTICE)).toHaveAttribute("role", "status");

    await poll();
    expect(screen.getByText("Caught up at last.")).toBeInTheDocument();
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });

  it("does not show on a paused capture, even before the next read", async () => {
    getTranscriptUtterances.mockResolvedValue(read(true));
    const view = render(<LiveTranscriptTab recording={recording()} />);
    await settle();
    expect(screen.getByText(NOTICE)).toBeInTheDocument();

    view.rerender(
      <LiveTranscriptTab recording={recording({ status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED })} />,
    );
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });
});
