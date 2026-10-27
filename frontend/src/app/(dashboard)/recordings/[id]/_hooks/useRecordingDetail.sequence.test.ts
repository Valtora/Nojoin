import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";
import { IDLE_RECORDING_POLL_INTERVAL_MS, useRecordingDetail } from "./useRecordingDetail";

const mocks = vi.hoisted(() => ({
  getRecording: vi.fn(), getTranscript: vi.fn(), notify: vi.fn(),
  router: { push: vi.fn(), refresh: vi.fn() },
  navigation: { activePanel: "transcript", chatPanelHeight: 30, setChatPanelHeight: vi.fn(), setActivePanel: vi.fn() },
  notes: [],
}));

vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getRecording: (...args: unknown[]) => mocks.getRecording(...args),
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
  getGlobalSpeakers: async () => [], getSettings: async () => ({}),
}));
// This suite isolates the settled reader. The independent feed stays unknown
// on resume, without issuing real HTTP requests or changing settled call counts.
vi.mock("@/lib/api/transcript", () => ({
  getTranscriptUtterances: () => new Promise(() => {}),
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("@/lib/store", () => ({ useNavigationStore: () => mocks.navigation }));
vi.mock("@/lib/notes/useNoteOutbox", () => ({ useRecordingNotes: () => mocks.notes }));
vi.mock("@/lib/notes/useNoteEdits", () => ({ useNoteEdits: () => ({}) }));
vi.mock("@/components/recordings/_hooks/useRecordingActions", () => ({ useRecordingActions: () => ({}) }));
vi.mock("@/components/ViewportDensityProvider", () => ({
  useViewportDensity: () => ({ isCompact: false, isMobile: false }),
}));

const recording = (sequence = 4, marked = true, status = RecordingStatus.PROCESSED): Recording => ({
  id: "rec-1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  name: "Meeting", meeting_uid: "meeting-1", audio_path: "/audio.wav", status,
  client_status: ClientStatus.IDLE, has_audio: true, has_proxy: true, speakers: [],
  upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false,
  transcript: {
    id: 1, recording_id: "rec-1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
    change_sequence: sequence, settling_required: marked, segments: [],
  },
});

const transcript = (text = "Original", revision = 4): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision, speakers: [], tombstones: [], settling_required: true,
  utterances: [{ id: "line", start: 0, end: 1, text, speaker: "LIVE_1", revision, provisional: true, locked: false }],
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const params = Promise.resolve({ id: "rec-1" });
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const mount = () => renderHook(() => useRecordingDetail({ params }));

describe("durable quiet transcript repair", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.getRecording.mockReset().mockResolvedValue(recording());
    mocks.getTranscript.mockReset().mockResolvedValue(transcript());
    mocks.notify.mockReset();
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it.each([RecordingStatus.PROCESSED, RecordingStatus.ERROR])("repairs settled text in %s only after a changed sequence", async (status) => {
    mocks.getRecording.mockResolvedValue(recording(4, true, status));
    const { result } = mount();
    await advance(0);
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(1);
    mocks.getRecording.mockResolvedValue(recording(5, true, status));
    mocks.getTranscript.mockResolvedValue(transcript("Remote edit", 4));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS - 1);
    expect(result.current.transcriptSegments[0].text).toBe("Original");
    await advance(1);
    expect(result.current.transcriptSegments[0].text).toBe("Remote edit");
    expect(mocks.getTranscript).toHaveBeenLastCalledWith("rec-1", undefined);
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS * 2);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(2);
  });

  it.each(["final", "tombstone", "names"])("repairs %s changes even without a newer utterance revision", async (kind) => {
    const { result } = mount();
    await advance(0);
    const next = transcript();
    if (kind === "final") next.utterances[0] = { ...next.utterances[0], provisional: false, locked: true };
    if (kind === "tombstone") { next.utterances = []; next.tombstones = ["line"]; }
    if (kind === "names") next.live_speaker_names = { LIVE_1: "Alex" };
    mocks.getTranscript.mockResolvedValue(next);
    mocks.getRecording.mockResolvedValue(recording(5));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    if (kind === "final") expect(result.current.transcriptSegments[0]).toMatchObject({ provisional: false, locked: true });
    if (kind === "tombstone") expect(result.current.transcriptSegments).toEqual([]);
    if (kind === "names") expect(result.current.speakerMap.LIVE_1).toBe("Alex");
    if (kind === "names") {
      mocks.getTranscript.mockResolvedValue({ ...next, live_speaker_names: {} });
      mocks.getRecording.mockResolvedValue(recording(6));
      await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
      expect(result.current.speakerMap.LIVE_1).toBeUndefined();
    }
  });

  it("retries a failed repair on the next existing poll with the same sequence", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { result } = mount();
    await advance(0);
    mocks.getRecording.mockResolvedValue(recording(5));
    mocks.getTranscript.mockRejectedValueOnce(new Error("offline"));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(2);
    expect(result.current.transcriptSegments[0].text).toBe("Original");
    mocks.getTranscript.mockResolvedValue(transcript("Recovered"));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(result.current.transcriptSegments[0].text).toBe("Recovered");
    expect(mocks.getTranscript).toHaveBeenCalledTimes(3);
    vi.restoreAllMocks();
  });

  it("coalesces changed sequences behind the initial read and reads the newest full snapshot serially", async () => {
    const first = deferred<TranscriptUtteranceList>();
    const second = deferred<TranscriptUtteranceList>();
    mocks.getTranscript.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = mount();
    await advance(0);
    mocks.getRecording.mockResolvedValue(recording(5));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    mocks.getRecording.mockResolvedValue(recording(6));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve(transcript("Old")));
    expect(mocks.getTranscript).toHaveBeenCalledTimes(2);
    await act(async () => second.resolve(transcript("Newest")));
    expect(result.current.transcriptSegments[0].text).toBe("Newest");
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(2);
  });

  it("uses edit state when the delayed response applies, then flushes the remote replacement", async () => {
    const { result } = mount();
    await advance(0);
    const pending = deferred<TranscriptUtteranceList>();
    mocks.getTranscript.mockReturnValueOnce(pending.promise);
    mocks.getRecording.mockResolvedValue(recording(5));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    act(() => result.current.setActiveTranscriptEditId("line"));
    await act(async () => pending.resolve(transcript("Remote")));
    expect(result.current.transcriptSegments[0].text).toBe("Original");
    expect(result.current.deferredTranscriptUtteranceIds).toEqual(["line"]);
    act(() => result.current.setActiveTranscriptEditId(null));
    expect(result.current.transcriptSegments[0].text).toBe("Remote");
  });

  it("leaves unmarked idle reads and legacy empty-full fallback at upstream behavior", async () => {
    const legacy = { start: 0, end: 1, text: "Legacy", speaker: "UNKNOWN" };
    const original = recording(4, false);
    original.transcript!.segments = [legacy];
    mocks.getRecording.mockResolvedValue(original);
    mocks.getTranscript.mockResolvedValue({ ...transcript(), settling_required: false, utterances: [] });
    const { result } = mount();
    await advance(0);
    expect(result.current.transcriptSegments).toEqual([legacy]);
    mocks.getRecording.mockResolvedValue({ ...original, transcript: { ...original.transcript!, change_sequence: 5 } });
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS * 2);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(1);
    expect(result.current.transcriptSegments).toEqual([legacy]);
  });

  it("drops a quiet read after capture resumes and performs only one full read when settling again", async () => {
    const { result } = mount();
    await advance(0);
    const pending = deferred<TranscriptUtteranceList>();
    mocks.getRecording.mockResolvedValue(recording(5));
    mocks.getTranscript.mockReturnValueOnce(pending.promise);
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    mocks.getRecording.mockResolvedValue(recording(6, true, RecordingStatus.UPLOADING));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(result.current.recording?.status).toBe(RecordingStatus.UPLOADING);
    await act(async () => pending.resolve(transcript("Stale")));
    expect(result.current.transcriptSegments[0].text).toBe("Original");
    mocks.getRecording.mockResolvedValue(recording(7));
    mocks.getTranscript.mockResolvedValue(transcript("Final"));
    await advance(1_000);
    expect(result.current.transcriptSegments[0].text).toBe("Final");
    expect(mocks.getTranscript).toHaveBeenCalledTimes(3);
  });

  it("flushes immediately when editing ends while the full repair is still in flight", async () => {
    const { result } = mount();
    await advance(0);
    act(() => result.current.setActiveTranscriptEditId("line"));
    const pending = deferred<TranscriptUtteranceList>();
    mocks.getRecording.mockResolvedValue(recording(5));
    mocks.getTranscript.mockReturnValueOnce(pending.promise);
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    act(() => result.current.setActiveTranscriptEditId(null));
    await act(async () => pending.resolve(transcript("Remote")));
    expect(result.current.transcriptSegments[0].text).toBe("Remote");
    expect(result.current.deferredTranscriptUtteranceIds).toEqual([]);
  });
});
