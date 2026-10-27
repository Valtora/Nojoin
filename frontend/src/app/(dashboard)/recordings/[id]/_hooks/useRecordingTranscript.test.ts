import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";
import { SETTLED_TRANSCRIPT_READ_TIMEOUT_MS } from "./useDetailTranscript";
import { useRecordingTranscript } from "./useRecordingTranscript";

const mocks = vi.hoisted(() => ({ live: vi.fn(), settled: vi.fn(), notify: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getTranscriptUtterances: (...args: unknown[]) => mocks.settled(...args),
  updateTranscriptUtteranceText: vi.fn(), updateTranscriptUtteranceSpeaker: vi.fn(),
}));
vi.mock("@/lib/api/transcript", () => ({ getTranscriptUtterances: (...args: unknown[]) => mocks.live(...args) }));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));

const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  name: "Meeting", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING, has_audio: true, speakers: [],
  upload_progress: 10, processing_progress: 0, is_archived: false, is_deleted: false, ...overrides,
});
const snapshot = (text = "Live", recordingId = "rec-1"): TranscriptUtteranceList => ({
  recording_id: recordingId, revision: 4, speakers: [], tombstones: [],
  live_speaker_names: { LIVE_1: text },
  utterances: text ? [{ id: "line", start: 0, end: 1, text, speaker: "LIVE_1", revision: 4 }] : [],
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const advance = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const setRecording = vi.fn();
const mount = (initial = recording()) => renderHook(
  ({ value, localActive }: { value: Recording; localActive: boolean }) =>
    useRecordingTranscript(value, setRecording, localActive),
  { initialProps: { value: initial, localActive: false } },
);

describe("recording transcript handoff", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.live.mockReset().mockResolvedValue(snapshot());
    mocks.settled.mockReset().mockResolvedValue(snapshot("Final"));
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it("keeps one feed and cursor across pause, Stop, queue and processing", async () => {
    const { result, rerender } = mount();
    await advance();
    const refresh = result.current.liveTranscript.refresh;
    for (const status of [RecordingStatus.PAUSED, RecordingStatus.UPLOADING, RecordingStatus.QUEUED,
      RecordingStatus.PROCESSING]) {
      rerender({ value: recording({ status, client_status: ClientStatus.IDLE }), localActive: false });
      await advance(3_000);
      expect(mocks.live).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
      expect(result.current.transcriptSegments[0].text).toBe("Live");
      expect(result.current.liveTranscript.refresh).toBe(refresh);
      expect(result.current.usesInFlightTranscript).toBe(true);
    }
    expect(mocks.settled).not.toHaveBeenCalled();
  });

  it.each([RecordingStatus.PROCESSED, RecordingStatus.ERROR])(
    "keeps the feed after a failed %s handoff and retries unchanged metadata", async (status) => {
      const { result, rerender } = mount();
      await advance();
      mocks.settled.mockRejectedValueOnce(new Error("offline"));
      rerender({ value: recording({ status, client_status: ClientStatus.IDLE }), localActive: false });
      await advance();
      expect(result.current.usesInFlightTranscript).toBe(true);
      expect(result.current.transcriptSegments[0].text).toBe("Live");
      await advance(3_000);
      expect(result.current.usesInFlightTranscript).toBe(false);
      expect(result.current.transcriptSegments[0].text).toBe("Final");
      expect(result.current.transcriptSpeakerNames).toEqual({ LIVE_1: "Final" });
      expect(mocks.settled).toHaveBeenCalledTimes(2);
    },
  );

  it("accepts an empty unmarked full handoff despite nonempty feed and metadata fallback", async () => {
    const { result, rerender } = mount();
    await advance();
    mocks.settled.mockResolvedValue(snapshot(""));
    const value = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    value.transcript = {
      id: 1, recording_id: value.id, created_at: value.created_at, updated_at: value.updated_at,
      text: "Stale metadata", segments: [{ start: 0, end: 1, text: "Stale metadata", speaker: "UNKNOWN" }],
    };
    rerender({ value, localActive: false });
    await advance();
    expect(result.current.transcriptSegments).toEqual([]);
    expect(result.current.transcriptIsAuthoritative).toBe(true);
    expect(result.current.usesInFlightTranscript).toBe(false);
  });

  it("rejects an older settled generation after capture resumes", async () => {
    const { result, rerender } = mount();
    await advance();
    const old = deferred<TranscriptUtteranceList>();
    const current = deferred<TranscriptUtteranceList>();
    mocks.settled.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    const settled = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    rerender({ value: settled, localActive: false });
    await advance();
    rerender({ value: recording(), localActive: false });
    await advance();
    rerender({ value: settled, localActive: false });
    await advance();
    await act(async () => old.resolve(snapshot("Obsolete")));
    expect(result.current.usesInFlightTranscript).toBe(true);
    expect(result.current.transcriptIsAuthoritative).toBe(false);
    expect(result.current.transcriptSegments[0].text).toBe("Live");
    await act(async () => current.resolve(snapshot("Current")));
    expect(result.current.usesInFlightTranscript).toBe(false);
    expect(result.current.transcriptSegments[0].text).toBe("Current");
  });

  it("invalidates settled readiness on local resume before server metadata changes", async () => {
    const settled = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    const { result, rerender } = mount(settled);
    await advance();
    expect(result.current.usesInFlightTranscript).toBe(false);
    rerender({ value: settled, localActive: true });
    await advance();
    expect(result.current.usesInFlightTranscript).toBe(true);
    expect(result.current.transcriptSegments[0].text).toBe("Live");
    const final = deferred<TranscriptUtteranceList>();
    mocks.settled.mockReturnValueOnce(final.promise);
    rerender({ value: settled, localActive: false });
    await advance();
    expect(result.current.usesInFlightTranscript).toBe(true);
    await act(async () => final.resolve(snapshot("After stop")));
    expect(result.current.transcriptSegments[0].text).toBe("After stop");
  });

  it("drops late responses and readiness when switching recording IDs", async () => {
    const { result, rerender } = mount();
    await advance();
    const old = deferred<TranscriptUtteranceList>();
    mocks.settled.mockReturnValueOnce(old.promise);
    rerender({ value: recording({ status: RecordingStatus.PROCESSED }), localActive: false });
    await advance();
    const next = deferred<TranscriptUtteranceList>();
    mocks.live.mockReturnValueOnce(next.promise);
    rerender({ value: recording({ id: "rec-2" }), localActive: false });
    expect(result.current.transcriptSegments).toEqual([]);
    await act(async () => old.resolve(snapshot("Wrong meeting")));
    expect(result.current.transcriptIsAuthoritative).toBe(false);
    expect(result.current.transcriptSegments).toEqual([]);
    await act(async () => next.resolve(snapshot("Next meeting", "rec-2")));
    expect(result.current.transcriptSegments[0].text).toBe("Next meeting");
  });

  it("abandons a stalled final full read and ignores its late answer after retry", async () => {
    const { result, rerender } = mount();
    await advance();
    const stalled = deferred<TranscriptUtteranceList>();
    mocks.settled.mockReturnValueOnce(stalled.promise);
    rerender({ value: recording({ status: RecordingStatus.PROCESSED }), localActive: false });
    await advance(SETTLED_TRANSCRIPT_READ_TIMEOUT_MS);
    await advance(3_000);
    expect(result.current.transcriptSegments[0].text).toBe("Final");
    expect(result.current.usesInFlightTranscript).toBe(false);
    await act(async () => stalled.resolve(snapshot("Abandoned")));
    expect(result.current.transcriptSegments[0].text).toBe("Final");
  });
});
