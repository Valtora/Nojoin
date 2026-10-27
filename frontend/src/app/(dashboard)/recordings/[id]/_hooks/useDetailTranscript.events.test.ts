import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLiveTranscript } from "@/components/transcript/_hooks/useLiveTranscript";
import {
  closeTranscriptEventStreams, TRANSCRIPT_HEARTBEAT_TIMEOUT_MS, TRANSCRIPT_REPAIR_MS,
} from "@/lib/transcriptEvents";
import { resetSessionForTests } from "@/lib/session";
import { TestTranscriptEventSource as Source } from "@/testTranscriptEventSource";
import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";
import { useDetailTranscript } from "./useDetailTranscript";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getTranscriptUtterances: (...args: unknown[]) => get(...args),
  updateTranscriptUtteranceText: vi.fn(),
  updateTranscriptUtteranceSpeaker: vi.fn(),
}));
vi.mock("@/lib/api/transcript", () => ({
  getTranscriptUtterances: (...args: unknown[]) => get(...args),
}));

const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-10-26T10:00:00Z", updated_at: "2026-10-26T10:00:00Z",
  name: "Meeting", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING, upload_progress: 10, processing_progress: 0,
  is_archived: false, is_deleted: false, ...overrides,
});
const delta = (revision = 4, text = "first", tombstones: string[] = []): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision, speakers: [], tombstones,
  utterances: text ? [{ id: "line", start: 0, end: 1, text, speaker: "UNKNOWN", revision }] : [],
});
const setRecording = vi.fn();
const finalisingStates = [
  [RecordingStatus.QUEUED, ClientStatus.IDLE],
  [RecordingStatus.PROCESSING, ClientStatus.IDLE],
  [RecordingStatus.UPLOADING, ClientStatus.UPLOADING],
] as const;
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe("recording-scoped transcript SSE", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", Source);
    Source.instances = [];
    resetSessionForTests();
    get.mockReset().mockResolvedValue(delta());
    setRecording.mockReset();
  });
  afterEach(() => {
    closeTranscriptEventStreams();
    resetSessionForTests();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it.each([RecordingStatus.PROCESSED, RecordingStatus.ERROR])(
    "keeps one shared stream through pause, finalisation and %s, then applies later edits",
    async (terminalStatus) => {
      const { result, rerender, unmount } = renderHook(({ rec }) => ({
        detail: useDetailTranscript(rec, setRecording),
        live: useLiveTranscript(rec),
      }), { initialProps: { rec: recording() } });
      await flush();
      const stream = Source.instances[0];
      expect(Source.instances).toHaveLength(1);
      act(() => stream.open());
      await flush();
      rerender({ rec: recording({ status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED }) });
      await flush();
      rerender({ rec: recording({ status: RecordingStatus.QUEUED, client_status: ClientStatus.IDLE }) });
      await flush();
      get.mockResolvedValue(delta(7, "published final"));
      rerender({ rec: recording({ status: terminalStatus, client_status: ClientStatus.IDLE }) });
      await flush();
      expect(result.current.detail.transcriptSegments[0].text).toBe("published final");
      get.mockResolvedValue(delta(8, "edited in another tab"));
      act(() => stream.hint(9000));
      await flush();
      expect(get).toHaveBeenLastCalledWith("rec-1", 7, expect.any(AbortSignal));
      expect(result.current.detail.transcriptSegments[0].text).toBe("edited in another tab");
      expect(Source.instances).toHaveLength(1);
      expect(stream.closed).toBe(false);
      unmount();
      expect(stream.closed).toBe(true);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("coalesces hints during a live read and advances only the delta cursor", async () => {
    const { result } = renderHook(() => useLiveTranscript(recording()));
    await flush();
    const stream = Source.instances[0];
    act(() => stream.open());
    await flush();
    let finish!: (value: TranscriptUtteranceList) => void;
    get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(delta(8, "newest"));
    get.mockClear();
    act(() => stream.hint(5000));
    await flush();
    act(() => { stream.hint(5001); stream.hint(5002); stream.hint(5002); });
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => finish(delta(5, "older")));
    await flush();
    expect(get.mock.calls.map((call) => call[1])).toEqual([4, 5]);
    expect(result.current.segments[0].text).toBe("newest");
  });

  it.each(finalisingStates)(
    "reads hints, healthy repairs and outage polls during %s without replacing the stream",
    async (status, clientStatus) => {
      const { result, rerender } = renderHook(({ rec }) => ({
        detail: useDetailTranscript(rec, setRecording), live: useLiveTranscript(rec),
      }), { initialProps: { rec: recording() } });
      await flush();
      const stream = Source.instances[0];
      act(() => stream.open());
      await flush();
      get.mockClear();
      rerender({ rec: recording({ status, client_status: clientStatus }) });
      await flush();
      expect(get).toHaveBeenCalledExactlyOnceWith("rec-1", undefined, expect.any(AbortSignal));
      get.mockResolvedValue(delta(5, "finalisation hint"));
      act(() => stream.hint(5));
      await flush();
      expect(get).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
      expect(result.current.detail.transcriptSegments[0].text).toBe("finalisation hint");
      get.mockResolvedValue(delta(6, "repair found missed update"));
      await advance(TRANSCRIPT_REPAIR_MS);
      expect(get).toHaveBeenLastCalledWith("rec-1", undefined, expect.any(AbortSignal));
      expect(result.current.detail.transcriptSegments[0].text).toBe("repair found missed update");
      expect(Source.instances).toHaveLength(1);
      const beforeFailure = get.mock.calls.length;
      act(() => stream.fail());
      await flush();
      expect(get).toHaveBeenCalledTimes(beforeFailure + 1);
      expect(get).toHaveBeenLastCalledWith("rec-1", 6, expect.any(AbortSignal));
      await advance(2999);
      expect(get).toHaveBeenCalledTimes(beforeFailure + 1);
      await advance(1);
      expect(get).toHaveBeenCalledTimes(beforeFailure + 2);
    },
  );

  it("keeps all live and paused hint reads with the live owner", async () => {
    const { result, rerender } = renderHook(({ rec }) => ({
      detail: useDetailTranscript(rec, setRecording), live: useLiveTranscript(rec),
    }), { initialProps: { rec: recording() } });
    await flush();
    const stream = Source.instances[0];
    act(() => stream.open());
    await flush();
    get.mockClear().mockResolvedValue(delta(5, "still recording"));
    act(() => stream.hint(5));
    await flush();
    expect(get).toHaveBeenCalledTimes(1);
    expect(result.current.live.segments[0].text).toBe("still recording");
    expect(result.current.detail.transcriptSegments).toEqual([]);
    rerender({ rec: recording({ status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED }) });
    await flush();
    get.mockClear().mockResolvedValue(delta(6, "paused publication"));
    act(() => stream.hint(6));
    await flush();
    expect(get).toHaveBeenCalledTimes(1);
    expect(result.current.live.segments[0].text).toBe("paused publication");
    expect(result.current.detail.transcriptSegments).toEqual([]);
    expect(Source.instances).toHaveLength(1);
  });

  it.each([[RecordingStatus.PROCESSED, ClientStatus.IDLE] as const, ...finalisingStates])(
    "falls back immediately and keeps three-second polling after %s stream timeout", async (status, clientStatus) => {
    const rec = recording({ status, client_status: clientStatus });
    renderHook(() => useDetailTranscript(rec, setRecording));
    await flush();
    const stream = Source.instances[0];
    act(() => { stream.open(); stream.hint(4); });
    await flush();
    await advance(TRANSCRIPT_HEARTBEAT_TIMEOUT_MS - 1);
    const beforeTimeout = get.mock.calls.length;
    await advance(1);
    expect(stream.closed).toBe(true);
    expect(get.mock.calls.length).toBe(beforeTimeout + 1);
    expect(get).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
    await advance(2999);
    expect(get.mock.calls.length).toBe(beforeTimeout + 1);
    await advance(1);
    expect(get.mock.calls.length).toBe(beforeTimeout + 2);
    // A replacement that opens requests a full read, not a cursor guessed from SSE.
    act(() => Source.instances.at(-1)!.open());
    await flush();
    expect(get).toHaveBeenLastCalledWith("rec-1", undefined, expect.any(AbortSignal));
  });

  it.each(finalisingStates)(
    "preserves polling-only behaviour during %s without a stream", async (status, clientStatus) => {
      vi.stubGlobal("EventSource", undefined);
      const rec = recording({ status, client_status: clientStatus });
      renderHook(() => useDetailTranscript(rec, setRecording));
      await flush();
      await advance(TRANSCRIPT_REPAIR_MS);
      expect(get).not.toHaveBeenCalled();
      expect(Source.instances).toHaveLength(0);
    },
  );

  it("preserves the ordinary delta tombstones and complete name map", async () => {
    const { result } = renderHook(() => useLiveTranscript(recording()));
    await flush();
    const stream = Source.instances[0];
    act(() => stream.open());
    await flush();
    get.mockResolvedValue({ ...delta(5, "", ["line"]), live_speaker_names: { LIVE_1: "Alex" } });
    act(() => stream.hint(5));
    await flush();
    expect(result.current.segments).toEqual([]);
    expect(result.current.liveSpeakerNames).toEqual({ LIVE_1: "Alex" });
    get.mockResolvedValue({ ...delta(5, ""), live_speaker_names: {} });
    act(() => stream.hint(6));
    await flush();
    expect(result.current.liveSpeakerNames).toEqual({});
  });

  it("defers an event delta for the line being edited until editing ends", async () => {
    const rec = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    const { result } = renderHook(() => useDetailTranscript(rec, setRecording));
    await flush();
    const stream = Source.instances[0];
    act(() => stream.open());
    await flush();
    act(() => result.current.setActiveTranscriptEditId("line"));
    get.mockResolvedValue(delta(5, "remote edit"));
    act(() => stream.hint(5));
    await flush();
    expect(result.current.transcriptSegments[0].text).toBe("first");
    expect(result.current.deferredTranscriptUtteranceIds).toEqual(["line"]);
    act(() => result.current.setActiveTranscriptEditId(null));
    await flush();
    expect(result.current.transcriptSegments[0].text).toBe("remote edit");
  });

  it("aborts a live read at finalisation and ignores its late answer", async () => {
    const { result, rerender } = renderHook(({ rec }) => ({
      detail: useDetailTranscript(rec, setRecording), live: useLiveTranscript(rec),
    }), { initialProps: { rec: recording() } });
    await flush();
    const stream = Source.instances[0];
    act(() => stream.open());
    await flush();
    let late!: (value: TranscriptUtteranceList) => void;
    get.mockReturnValueOnce(new Promise((resolve) => { late = resolve; }));
    act(() => stream.hint(5));
    await flush();
    const liveSignal = get.mock.lastCall![2] as AbortSignal;
    get.mockResolvedValue(delta(6, "final"));
    rerender({ rec: recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE }) });
    await flush();
    expect(liveSignal.aborted).toBe(true);
    await act(async () => late(delta(5, "late live answer")));
    await flush();
    expect(result.current.detail.transcriptSegments[0].text).toBe("final");
    expect(Source.instances).toHaveLength(1);
  });

  it("aborts the old recording read and prevents its late data landing in a new recording", async () => {
    let late!: (value: TranscriptUtteranceList) => void;
    get.mockReturnValueOnce(new Promise((resolve) => { late = resolve; }));
    const { result, rerender, unmount } = renderHook(({ rec }) => useDetailTranscript(rec, setRecording), {
      initialProps: { rec: recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE }) },
    });
    await flush();
    const oldSignal = get.mock.lastCall![2] as AbortSignal;
    get.mockResolvedValue({ ...delta(8, "new recording"), recording_id: "rec-2" });
    rerender({ rec: recording({ id: "rec-2", status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE }) });
    await flush();
    expect(oldSignal.aborted).toBe(true);
    await act(async () => late(delta(4, "old recording")));
    await flush();
    expect(result.current.transcriptSegments[0].text).toBe("new recording");
    expect(Source.instances[0].closed).toBe(true);
    unmount();
    expect(Source.instances[1].closed).toBe(true);
  });

  it("aborts an in-flight detail read and drops it when logout closes the feed", async () => {
    const rec = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    const { result } = renderHook(() => useDetailTranscript(rec, setRecording));
    await flush();
    const stream = Source.instances[0];
    act(() => stream.open());
    await flush();
    let late!: (value: TranscriptUtteranceList) => void;
    get.mockReturnValueOnce(new Promise((resolve) => { late = resolve; }));
    act(() => stream.hint(5));
    await flush();
    const signal = get.mock.lastCall![2] as AbortSignal;
    act(() => closeTranscriptEventStreams());
    expect(signal.aborted).toBe(true);
    expect(stream.closed).toBe(true);
    await act(async () => late(delta(8, "answer after logout")));
    await flush();
    expect(result.current.transcriptSegments[0].text).toBe("first");
  });

  it("does not restart a closed live feed through a late action refresh", async () => {
    const { result } = renderHook(() => useLiveTranscript(recording()));
    await flush();
    const beforeClose = get.mock.calls.length;
    act(() => closeTranscriptEventStreams());
    act(() => result.current.refresh());
    await flush();
    await advance(3000);
    expect(get).toHaveBeenCalledTimes(beforeClose);
    expect(Source.instances[0].closed).toBe(true);
  });

  it("serialises a mutation full refresh behind an event read instead of overwriting a newer delta", async () => {
    const rec = recording({ status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE });
    const { result } = renderHook(() => useDetailTranscript(rec, setRecording));
    await flush();
    const stream = Source.instances[0];
    act(() => stream.open());
    await flush();
    let finish!: (value: TranscriptUtteranceList) => void;
    get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(delta(7, "after action"))
      .mockResolvedValueOnce(delta(8, "after another event"));
    get.mockClear();
    act(() => stream.hint(5));
    await flush();
    let action!: Promise<unknown>;
    act(() => {
      action = result.current.syncTranscriptState("full");
      stream.hint(6);
      stream.hint(7);
    });
    await flush();
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => { finish(delta(5, "event")); await action; });
    await flush();
    expect(get.mock.calls.map((call) => call[1])).toEqual([4, undefined, 7]);
    expect(result.current.transcriptSegments[0].text).toBe("after another event");
  });
});
