import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";
import { useRecordingDetail } from "./useRecordingDetail";

const mocks = vi.hoisted(() => ({
  getRecording: vi.fn(), getRecordingIfChanged: vi.fn(), getTranscript: vi.fn(), getSettings: vi.fn(),
  notify: vi.fn(), exportAudio: vi.fn(), exportContent: vi.fn(), generateNotes: vi.fn(),
  router: { push: vi.fn(), refresh: vi.fn() },
  navigation: { activePanel: "transcript", chatPanelHeight: 30, setChatPanelHeight: vi.fn(), setActivePanel: vi.fn() },
}));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getRecording: (...args: unknown[]) => mocks.getRecording(...args),
  getRecordingIfChanged: (...args: unknown[]) => mocks.getRecordingIfChanged(...args),
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
  exportContent: (...args: unknown[]) => mocks.exportContent(...args),
  generateNotes: (...args: unknown[]) => mocks.generateNotes(...args),
  getGlobalSpeakers: async () => [], getSettings: () => mocks.getSettings(),
}));
vi.mock("@/lib/api/transcript", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api/transcript")>(),
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("@/lib/store", () => ({ useNavigationStore: () => mocks.navigation }));
vi.mock("@/lib/notes/useNoteOutbox", () => ({ useRecordingNotes: () => [] }));
vi.mock("@/lib/notes/useNoteEdits", () => ({ useNoteEdits: () => ({}) }));
vi.mock("@/components/recordings/_hooks/useRecordingActions", () => ({
  useRecordingActions: () => ({ exportAudio: mocks.exportAudio }),
}));
vi.mock("@/components/ViewportDensityProvider", () => ({
  useViewportDensity: () => ({ isCompact: false, isMobile: false }),
}));

const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  name: "Meeting", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.PROCESSED,
  client_status: ClientStatus.IDLE, has_audio: true, has_proxy: true, speakers: [],
  upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false, ...overrides,
});
const transcript = (): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision: 4, speakers: [], tombstones: [], utterances: [],
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const advance = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const params = Promise.resolve({ id: "rec-1" });

describe("recording detail current metadata and capabilities", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.getRecording.mockReset().mockResolvedValue(recording());
    mocks.getTranscript.mockReset().mockResolvedValue(transcript());
    mocks.getSettings.mockReset().mockResolvedValue({});
    mocks.exportAudio.mockReset(); mocks.exportContent.mockReset(); mocks.generateNotes.mockReset();
    mocks.router.push.mockReset();
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it("feeds unchanged metadata reads into the audio clock before comparing state", async () => {
    const live = recording({ status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING, live_audio_ms: 2_000 });
    mocks.getRecording.mockResolvedValue(live);
    const { result } = renderHook(() => useRecordingDetail({ params }));
    await advance();
    const initial = result.current.recording;
    mocks.getRecording.mockResolvedValue({ ...live, live_audio_ms: 4_000 });
    await advance(1_000);
    expect(result.current.recording).toBe(initial);
    expect(result.current.audioClock.audioNow("rec-1")).toBe(4_000);
  });

  it("does not delay metadata and the clock behind slow settings", async () => {
    const settings = deferred<object>();
    mocks.getSettings.mockReturnValueOnce(settings.promise);
    mocks.getRecording.mockResolvedValue(recording({ live_audio_ms: 7_000 }));
    const { result } = renderHook(() => useRecordingDetail({ params }));
    await advance();
    expect(result.current.loading).toBe(false);
    expect(result.current.audioClock.audioNow("rec-1")).toBe(7_000);
    // A finished page's check is a conditional read; this one has changed.
    mocks.getRecordingIfChanged.mockResolvedValue({
      recording: recording({ name: "Newer metadata", processing_progress: 99 }), etag: 'W/"PROCESSED-1"' });
    await advance(15_000);
    await act(async () => settings.resolve({}));
    expect(result.current.recording?.name).toBe("Newer metadata");
  });

  it("does not replace a new route or its clock with a previous recording's late load", async () => {
    const old = deferred<Recording>();
    mocks.getRecording.mockReturnValueOnce(old.promise).mockResolvedValueOnce(recording({ id: "rec-2", live_audio_ms: 8_000 }));
    const { result, rerender } = renderHook(({ route }) => useRecordingDetail({ params: route }), {
      initialProps: { route: params },
    });
    await advance();
    rerender({ route: Promise.resolve({ id: "rec-2" }) });
    await advance();
    await act(async () => old.resolve(recording({ live_audio_ms: 1_000 })));
    expect(result.current.recording?.id).toBe("rec-2");
    expect(result.current.audioClock.audioNow("rec-2")).toBe(8_000);
    expect(mocks.router.push).not.toHaveBeenCalled();
  });

  it("keeps settled playback and export available while another recording captures", async () => {
    const { result } = renderHook(() => useRecordingDetail({
      params, capture: { recordingId: "another", status: "recording" },
    }));
    await advance();
    expect(result.current.canPlayback).toBe(true);
    expect(result.current.canExport).toBe(true);
    await result.current.handleExport({ kind: "audio" });
    expect(mocks.exportAudio).toHaveBeenCalledWith("rec-1", "Meeting");
  });

  it.each([RecordingStatus.UPLOADING, RecordingStatus.PAUSED, RecordingStatus.PROCESSING])(
    "enables watching playback while %s without enabling export", async (status) => {
      mocks.getRecording.mockResolvedValue(recording({ status, has_proxy: false, capture_playback_sequence: 0 }));
      const { result } = renderHook(() => useRecordingDetail({ params }));
      await advance();
      expect(result.current.canPlayback).toBe(true);
      expect(result.current.canExport).toBe(false);
    },
  );

  it("notices newly playable chunks without another metadata change", async () => {
    const before = recording({
      status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING,
      has_proxy: false, capture_playback_sequence: -1,
    });
    mocks.getRecording.mockResolvedValue(before);
    const { result } = renderHook(() => useRecordingDetail({ params }));
    await advance();
    expect(result.current.canPlayback).toBe(false);
    mocks.getRecording.mockResolvedValue({ ...before, capture_playback_sequence: 0 });
    await advance(1_000);
    expect(result.current.recording?.capture_playback_sequence).toBe(0);
    expect(result.current.canPlayback).toBe(true);
  });
});
