import { act, cleanup, renderHook } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";
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
const mountStateful = (initial: Recording) => renderHook(() => {
  const [value, setValue] = useState<Recording | null>(initial);
  return { ...useRecordingTranscript(value, setValue), setRecording: setValue };
});

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
      const retry = deferred<TranscriptUtteranceList>();
      mocks.settled.mockReturnValueOnce(retry.promise);
      rerender({ value: recording({ status, client_status: ClientStatus.IDLE }), localActive: false });
      await advance();
      expect(result.current.usesInFlightTranscript).toBe(true);
      expect(result.current.transcriptSegments[0].text).toBe("Live");
      const liveReads = mocks.live.mock.calls.length;
      mocks.live.mockResolvedValue(snapshot("Still arriving"));
      await advance(3_000);
      expect(mocks.settled).toHaveBeenCalledTimes(2);
      expect(mocks.live).toHaveBeenCalledTimes(liveReads + 1);
      expect(mocks.live).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
      expect(result.current.transcriptSegments[0].text).toBe("Still arriving");
      expect(result.current.usesInFlightTranscript).toBe(true);
      expect(result.current.transcriptIsAuthoritative).toBe(false);
      await act(async () => retry.resolve(snapshot("Final")));
      expect(result.current.usesInFlightTranscript).toBe(false);
      expect(result.current.transcriptIsAuthoritative).toBe(true);
      expect(result.current.transcriptSegments[0].text).toBe("Final");
      expect(result.current.transcriptSpeakerNames).toEqual({ LIVE_1: "Final" });
      const readsAtSuccess = mocks.live.mock.calls.length;
      await advance(6_000);
      expect(mocks.live).toHaveBeenCalledTimes(readsAtSuccess);
    },
  );

  it.each([0, 4])("preserves an accepted revision-%i empty handoff through recording updates", async (revision) => {
    const { result } = mountStateful(recording());
    await advance();
    mocks.settled.mockResolvedValue({ ...snapshot(""), revision });
    const value = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    value.transcript = {
      id: 1, recording_id: value.id, created_at: value.created_at, updated_at: value.updated_at,
      text: "Stale metadata", segments: [{ start: 0, end: 1, text: "Stale metadata", speaker: "UNKNOWN" }],
    };
    act(() => result.current.setRecording(value));
    await advance();
    expect(result.current.transcriptSegments).toEqual([]);
    expect(result.current.transcriptIsAuthoritative).toBe(true);
    expect(result.current.usesInFlightTranscript).toBe(false);
    act(() => result.current.setRecording({ ...value, name: "Refreshed metadata" }));
    expect(result.current.transcriptSegments).toEqual([]);
    expect(result.current.transcriptIsAuthoritative).toBe(true);
    expect(result.current.usesInFlightTranscript).toBe(false);
  });

  it("accepts delayed legacy metadata before the first successful read", async () => {
    mocks.settled.mockReturnValue(deferred<TranscriptUtteranceList>().promise);
    const value = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    const { result } = mountStateful(value);
    await advance();
    act(() => result.current.setRecording({ ...value, transcript: {
      id: 1, recording_id: value.id, created_at: value.created_at, updated_at: value.updated_at,
      segments: [{ start: 0, end: 1, text: "Delayed legacy metadata", speaker: "UNKNOWN" }],
    } }));
    expect(result.current.transcriptSegments[0].text).toBe("Delayed legacy metadata");
    expect(result.current.isSettledTranscriptReady).toBe(false);
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
    expect(result.current.usesInFlightTranscript).toBe(true);
    expect(result.current.transcriptIsAuthoritative).toBe(false);
    expect(result.current.transcriptSegments[0].text).toBe("Live");
    await act(async () => current.resolve(snapshot("Current")));
    expect(result.current.usesInFlightTranscript).toBe(false);
    expect(result.current.transcriptSegments[0].text).toBe("Current");
    await act(async () => old.resolve(snapshot("Obsolete")));
    expect(result.current.transcriptSegments[0].text).toBe("Current");
    expect(result.current.transcriptSpeakerNames).toEqual({ LIVE_1: "Current" });
    expect(result.current.transcriptIsAuthoritative).toBe(true);
    expect(result.current.usesInFlightTranscript).toBe(false);
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
});
