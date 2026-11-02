import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording } from "@/types";
import { getAutoSpeakerReplacementName } from "./recordingDetailUtils";
import { useRecordingDetail } from "./useRecordingDetail";

// Edits on the recording page that should not wait on, or end in, a full read
// of the recording and its transcript.
const mocks = vi.hoisted(() => ({
  read: vi.fn(), transcript: vi.fn(), rename: vi.fn(), replace: vi.fn(), notify: vi.fn(),
  router: { push: vi.fn(), refresh: vi.fn() },
  navigation: { activePanel: "transcript", chatPanelHeight: 30, setChatPanelHeight: vi.fn(), setActivePanel: vi.fn() },
}));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  getRecording: (...args: unknown[]) => mocks.read(...args),
  getTranscriptUtterances: (...args: unknown[]) => mocks.transcript(...args),
  renameRecording: (...args: unknown[]) => mocks.rename(...args),
  findAndReplace: (...args: unknown[]) => mocks.replace(...args),
  getSettings: async () => ({}), getGlobalSpeakers: async () => [],
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("@/lib/store", () => ({ useNavigationStore: () => mocks.navigation }));
vi.mock("@/lib/notes/useNoteOutbox", () => ({ useRecordingNotes: () => [] }));
vi.mock("@/lib/notes/useNoteEdits", () => ({ useNoteEdits: () => ({}) }));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ cancel: vi.fn(), ownedRecordingId: null, pausedRecording: null, attentionRecordings: [] }),
}));
vi.mock("@/components/ViewportDensityProvider", () => ({
  useViewportDensity: () => ({ isCompact: false, isMobile: false }),
}));

const recording: Recording = {
  id: "rec-1", created_at: "2026-10-27", updated_at: "2026-10-27", name: "Weekly sync", meeting_uid: "meeting-1",
  audio_path: "/audio.wav", status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE,
  has_audio: true, has_proxy: true, speakers: [], upload_progress: 100, processing_progress: 100,
  is_archived: false, is_deleted: false,
  transcript: {
    id: 1, created_at: "2026-10-27", updated_at: "2026-10-27", recording_id: "rec-1", segments: [],
    notes: "Alice will send it.",
  },
};
const params = Promise.resolve({ id: "rec-1" });
const mount = async () => {
  const hook = renderHook(() => useRecordingDetail({ params }));
  await act(async () => vi.advanceTimersByTimeAsync(0));
  return hook;
};
const pending = () => {
  let settle!: { resolve: () => void; reject: (error: Error) => void };
  const promise = new Promise<void>((resolve, reject) => { settle = { resolve, reject }; });
  return { promise, ...settle };
};

describe("recording page edits", () => {
  let announced: unknown[] = [];
  const listen = (event: Event) => announced.push((event as CustomEvent).detail);

  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.read.mockReset().mockResolvedValue(recording);
    mocks.transcript.mockReset().mockResolvedValue({
      recording_id: "rec-1", revision: 1, speakers: [], tombstones: [], utterances: [],
    });
    mocks.rename.mockReset();
    mocks.replace.mockReset().mockResolvedValue(undefined);
    mocks.notify.mockReset();
    announced = [];
    window.addEventListener("recording-updated", listen);
  });
  afterEach(() => {
    window.removeEventListener("recording-updated", listen);
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  const submitTitle = (result: { current: ReturnType<typeof useRecordingDetail> }, name: string) => {
    act(() => result.current.setIsEditingTitle(true));
    act(() => result.current.setTitleValue(name));
    act(() => result.current.handleTitleSubmit());
  };

  it("keeps a later title when an earlier save fails after it", async () => {
    const first = pending();
    const second = pending();
    mocks.rename.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = await mount();

    submitTitle(result, "Planning");
    submitTitle(result, "Planning review");
    expect(result.current.recording?.name).toBe("Planning review");

    await act(async () => first.reject(new Error("offline")));
    await act(async () => second.resolve());

    expect(result.current.recording?.name).toBe("Planning review");
    expect(announced).toEqual([
      { id: "rec-1", name: "Planning" },
      { id: "rec-1", name: "Planning review" },
    ]);
    expect(mocks.notify).toHaveBeenCalledWith({ type: "error", message: "Failed to rename recording." });
  });

  it("keeps the text being typed when a rollback lands while the field is open", async () => {
    const save = pending();
    mocks.rename.mockReturnValueOnce(save.promise);
    const { result } = await mount();

    submitTitle(result, "Planning");
    act(() => result.current.setIsEditingTitle(true));
    act(() => result.current.setTitleValue("Plann"));
    await act(async () => save.reject(new Error("offline")));

    expect(result.current.recording?.name).toBe("Weekly sync");
    expect(result.current.titleValue).toBe("Plann");
  });

  it("carries a speaker's new name into the notes and leaves the re-read to the speaker panel", async () => {
    const { result } = await mount();
    const reads = mocks.read.mock.calls.length;
    const transcriptReads = mocks.transcript.mock.calls.length;

    await act(async () => result.current.handleSpeakerRenamed("Speaker 1", "Alice Example"));

    expect(mocks.replace).toHaveBeenCalledExactlyOnceWith(
      "rec-1", "Speaker 1", getAutoSpeakerReplacementName("Alice Example"), { caseSensitive: true },
    );
    expect(mocks.read).toHaveBeenCalledTimes(reads);
    expect(mocks.transcript).toHaveBeenCalledTimes(transcriptReads);
  });

  it("reports a failed replacement in the notes", async () => {
    mocks.replace.mockRejectedValueOnce(new Error("offline"));
    const { result } = await mount();

    await act(async () => result.current.handleSpeakerRenamed("Speaker 1", "Alice Example"));

    expect(mocks.notify).toHaveBeenCalledWith({ type: "error", message: "Failed to find and replace." });
  });

  it("leaves notes it does not have alone", async () => {
    mocks.read.mockResolvedValue({ ...recording, transcript: { ...recording.transcript!, notes: undefined } });
    const { result } = await mount();

    await act(async () => result.current.handleSpeakerRenamed("Speaker 1", "Alice Example"));

    expect(mocks.replace).not.toHaveBeenCalled();
  });
});
