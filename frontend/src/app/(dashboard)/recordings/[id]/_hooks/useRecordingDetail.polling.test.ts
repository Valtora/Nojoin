import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLiveTranscript } from "@/components/transcript/_hooks/useLiveTranscript";
import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";
import { IDLE_RECORDING_POLL_INTERVAL_MS, useRecordingDetail } from "./useRecordingDetail";

const mocks = vi.hoisted(() => ({
  getRecording: vi.fn(), getRecordingIfChanged: vi.fn(), getTranscript: vi.fn(), notify: vi.fn(),
  router: { push: vi.fn(), refresh: vi.fn() },
  navigation: { activePanel: "transcript", chatPanelHeight: 30, setChatPanelHeight: vi.fn(), setActivePanel: vi.fn() },
  notes: [],
}));

vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getRecording: (...args: unknown[]) => mocks.getRecording(...args),
  getRecordingIfChanged: (...args: unknown[]) => mocks.getRecordingIfChanged(...args),
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
  getGlobalSpeakers: async () => [], getSettings: async () => ({}),
}));
vi.mock("@/lib/api/transcript", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api/transcript")>(),
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("@/lib/store", () => ({ useNavigationStore: () => mocks.navigation }));
vi.mock("@/lib/notes/useNoteOutbox", () => ({ useRecordingNotes: () => mocks.notes }));
vi.mock("@/lib/notes/useNoteEdits", () => ({ useNoteEdits: () => ({}) }));
vi.mock("@/components/recordings/_hooks/useRecordingActions", () => ({ useRecordingActions: () => ({}) }));
vi.mock("@/components/ViewportDensityProvider", () => ({
  useViewportDensity: () => ({ isCompact: false, isMobile: false }),
}));

const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  name: "Meeting", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.PROCESSED,
  client_status: ClientStatus.IDLE, has_audio: true, has_proxy: true, speakers: [],
  upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false, ...overrides,
});

const delta = (revision = 4, tombstones: string[] = []): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision, speakers: [], tombstones, settling_required: true,
  utterances: tombstones.length ? [] : [{
    id: "line", start: 0, end: 1, text: "Live words", speaker: "UNKNOWN", revision,
    provisional: true, locked: false,
  }],
});

const params = Promise.resolve({ id: "rec-1" });
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const mount = () => renderHook(() => {
  const detail = useRecordingDetail({ params });
  const live = useLiveTranscript(detail.recording);
  return { detail, live };
});

describe("recording detail polling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.getRecording.mockReset().mockResolvedValue(recording());
    mocks.getRecordingIfChanged.mockReset().mockResolvedValue(null);
    mocks.getTranscript.mockReset().mockResolvedValue(delta());
    mocks.notify.mockReset();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("checks a finished recording with its ETag and re-reads the transcript only when it moved", async () => {
    const { result } = mount();
    await advance(0);
    expect(result.current.detail.loading).toBe(false);
    mocks.getRecordingIfChanged.mockResolvedValueOnce({ recording: recording(), etag: 'W/"PROCESSED-0"' });
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    // The first check has no ETag yet; its answer matches what the page shows.
    expect(mocks.getRecordingIfChanged).toHaveBeenLastCalledWith("rec-1", null);
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getRecordingIfChanged).toHaveBeenLastCalledWith("rec-1", 'W/"PROCESSED-0"');
    expect(mocks.getTranscript).toHaveBeenCalledTimes(1);

    mocks.getRecordingIfChanged.mockResolvedValueOnce({
      recording: recording({ transcript: { change_sequence: 1 } as Recording["transcript"] }),
      etag: 'W/"PROCESSED-1"',
    });
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(2);
    expect(mocks.getTranscript).toHaveBeenLastCalledWith("rec-1", undefined);
    expect(mocks.getRecording).toHaveBeenCalledTimes(1);
  });

  it("keeps the old ETag when the transcript re-read fails, so the next check tries again", async () => {
    mount();
    await advance(0);
    const moved = {
      recording: recording({ transcript: { change_sequence: 1 } as Recording["transcript"] }),
      etag: 'W/"PROCESSED-1-"',
    };
    mocks.getRecordingIfChanged.mockResolvedValue(moved);
    mocks.getTranscript.mockRejectedValueOnce(new Error("offline"));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(2);
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getRecordingIfChanged).toHaveBeenLastCalledWith("rec-1", null);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(3);
  });

  it("discovers a live capture on the slow check and starts three-second delta reads", async () => {
    const { result } = mount();
    await advance(0);
    mocks.getRecordingIfChanged.mockResolvedValueOnce({
      recording: recording({ status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING }),
      etag: 'W/"UPLOADING-0"',
    });
    mocks.getRecording.mockResolvedValue(recording({
      status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING,
    }));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(result.current.detail.recording?.client_status).toBe(ClientStatus.RECORDING);
    const reads = mocks.getTranscript.mock.calls.length;
    await advance(3_000);
    expect(mocks.getTranscript.mock.calls.length).toBeGreaterThan(reads);
    expect(mocks.getTranscript).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
  });

  it.each([
    [RecordingStatus.UPLOADING, ClientStatus.RECORDING],
    [RecordingStatus.PAUSED, ClientStatus.PAUSED],
  ])("reads live transcript deltas every three seconds during %s and applies tombstones", async (status, clientStatus) => {
    mocks.getRecording.mockResolvedValue(recording({ status, client_status: clientStatus }));
    const { result } = mount();
    await advance(0);
    expect(result.current.live.segments.map((segment) => segment.id)).toEqual(["line"]);
    mocks.getTranscript.mockResolvedValue(delta(5, ["line"]));
    await advance(2_999);
    expect(mocks.getRecording).toHaveBeenCalledTimes(3);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(mocks.getTranscript).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
    expect(result.current.live.segments).toEqual([]);
  });
});
