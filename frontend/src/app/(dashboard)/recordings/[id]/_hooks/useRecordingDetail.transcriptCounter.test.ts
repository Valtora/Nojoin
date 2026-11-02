import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RECORDING_UPDATED_EVENT } from "@/lib/recordingEvents";
import {
  ClientStatus, RecordingStatus, type Recording, type Tag, type TimelineNote, type TranscriptUtteranceList,
} from "@/types";
import { IDLE_RECORDING_POLL_INTERVAL_MS, useRecordingDetail } from "./useRecordingDetail";

// The transcript change counter the page holds is the one its transcript has
// caught up with: a read of the recording takes a newer one only once the
// transcript has been loaded for it, or the idle check never loads the change.
const mocks = vi.hoisted(() => ({
  getRecording: vi.fn(), getRecordingIfChanged: vi.fn(), getTranscript: vi.fn(),
  updateSpeaker: vi.fn(), updateNotes: vi.fn(), updateSpeakerColor: vi.fn(), notify: vi.fn(),
  router: { push: vi.fn(), refresh: vi.fn() },
  navigation: { activePanel: "transcript", chatPanelHeight: 30, setChatPanelHeight: vi.fn(), setActivePanel: vi.fn() },
  notes: [] as TimelineNote[],
}));

vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getRecording: (...args: unknown[]) => mocks.getRecording(...args),
  getRecordingIfChanged: (...args: unknown[]) => mocks.getRecordingIfChanged(...args),
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
  updateSpeaker: (...args: unknown[]) => mocks.updateSpeaker(...args),
  updateNotes: (...args: unknown[]) => mocks.updateNotes(...args),
  updateSpeakerColor: (...args: unknown[]) => mocks.updateSpeakerColor(...args),
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

const tag: Tag = { id: 3, created_at: "2026-11-01", updated_at: "2026-11-01", name: "Planning" };

const recording = (changeSequence: number, overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-11-01T10:00:00Z", updated_at: "2026-11-01T10:00:00Z",
  name: "Weekly sync", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.PROCESSED,
  client_status: ClientStatus.IDLE, has_audio: true, has_proxy: true, speakers: [],
  upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false,
  transcript: {
    id: 1, created_at: "2026-11-01", updated_at: "2026-11-01", recording_id: "rec-1", segments: [],
    change_sequence: changeSequence, notes: "Alice Example will send the plan.",
  },
  ...overrides,
});

const utterances = (text: string, revision: number): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision, speakers: [], tombstones: [],
  utterances: [{ id: "u1", start: 0, end: 1, text, speaker: "SPEAKER_00", revision, provisional: false, locked: false }],
});

const params = Promise.resolve({ id: "rec-1" });
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const mount = async () => {
  const hook = renderHook(() => useRecordingDetail({ params }));
  await advance(0);
  return hook;
};
type Hook = Awaited<ReturnType<typeof mount>>;
const lines = ({ result }: Hook) => result.current.transcriptSegments.map((segment) => segment.text);
const nextCheckFinds = (read: Recording) =>
  mocks.getRecordingIfChanged.mockResolvedValue({ recording: read, etag: `W/"PROCESSED-${read.transcript?.change_sequence}-"` });

describe("recording reads and the transcript change counter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.getRecording.mockReset().mockResolvedValue(recording(0));
    mocks.getRecordingIfChanged.mockReset().mockResolvedValue(null);
    mocks.getTranscript.mockReset().mockResolvedValue(utterances("Original", 1));
    mocks.updateSpeaker.mockReset().mockResolvedValue([]);
    mocks.updateNotes.mockReset().mockResolvedValue(undefined);
    mocks.updateSpeakerColor.mockReset().mockResolvedValue(undefined);
    mocks.notify.mockReset();
    mocks.notes = [];
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("loads a transcript edited elsewhere before it takes a tag change", async () => {
    const hook = await mount();
    expect(lines(hook)).toEqual(["Original"]);
    mocks.getRecording.mockResolvedValue(recording(1, { tags: [tag] }));
    mocks.getTranscript.mockResolvedValue(utterances("Edited elsewhere", 2));

    await act(() => hook.result.current.refreshRecordingMetadata());

    expect(mocks.getTranscript).toHaveBeenLastCalledWith("rec-1", undefined);
    expect(lines(hook)).toEqual(["Edited elsewhere"]);
    expect(hook.result.current.recording?.tags).toEqual([tag]);
    expect(hook.result.current.recording?.transcript?.change_sequence).toBe(1);

    // Taken as seen: the next check reads the transcript no more.
    const transcriptReads = mocks.getTranscript.mock.calls.length;
    nextCheckFinds(recording(1, { tags: [tag] }));
    await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
    expect(mocks.getTranscript).toHaveBeenCalledTimes(transcriptReads);
  });

  it("reads no transcript after a tag change when the transcript has not moved", async () => {
    const hook = await mount();
    const transcriptReads = mocks.getTranscript.mock.calls.length;
    mocks.getRecording.mockResolvedValue(recording(0, { tags: [tag] }));

    await act(() => hook.result.current.refreshRecordingMetadata());

    expect(mocks.getTranscript).toHaveBeenCalledTimes(transcriptReads);
    expect(hook.result.current.recording?.tags).toEqual([tag]);
  });

  it.each(["tag change", "speaker rename", "recording refresh"] as const)(
    "keeps the old counter when the transcript read after a %s fails, so the next check loads it",
    async (action) => {
      const hook = await mount();
      mocks.getRecording.mockResolvedValue(recording(1, { tags: [tag] }));
      mocks.getTranscript.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(utterances("Edited elsewhere", 2));

      await act(async () => {
        if (action === "tag change") await hook.result.current.refreshRecordingMetadata();
        if (action === "speaker rename") await hook.result.current.handleRenameSpeaker("SPEAKER_00", "Alice Example");
        if (action === "recording refresh") {
          window.dispatchEvent(new CustomEvent(RECORDING_UPDATED_EVENT, { detail: { id: "rec-1" } }));
          await vi.advanceTimersByTimeAsync(0);
        }
      });

      expect(lines(hook)).toEqual(["Original"]);
      expect(hook.result.current.recording?.tags).toEqual([tag]);
      expect(hook.result.current.recording?.transcript?.change_sequence).toBe(0);

      nextCheckFinds(recording(1, { tags: [tag] }));
      await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
      expect(lines(hook)).toEqual(["Edited elsewhere"]);
      expect(hook.result.current.recording?.transcript?.change_sequence).toBe(1);
    },
  );

  it.each(["notes save", "speaker colour"] as const)(
    "keeps the old counter after a %s, which moves it, and loads the transcript once on the next check",
    async (action) => {
      const hook = await mount();
      const transcriptReads = mocks.getTranscript.mock.calls.length;
      const saved = recording(1, { tags: [tag] });
      mocks.getRecording.mockResolvedValue(saved);

      await act(async () => {
        if (action === "notes save") await hook.result.current.handleNotesChange("Alice Example sends the plan.");
        if (action === "speaker colour") await hook.result.current.handleColorChange("SPEAKER_00", "blue");
      });

      expect(mocks.getTranscript).toHaveBeenCalledTimes(transcriptReads);
      expect(hook.result.current.recording?.tags).toEqual([tag]);
      expect(hook.result.current.recording?.transcript?.change_sequence).toBe(0);

      mocks.getTranscript.mockResolvedValue(utterances("Edited elsewhere", 2));
      nextCheckFinds(saved);
      await advance(IDLE_RECORDING_POLL_INTERVAL_MS);
      expect(lines(hook)).toEqual(["Edited elsewhere"]);
      expect(mocks.getTranscript).toHaveBeenCalledTimes(transcriptReads + 1);
    },
  );

  it("loads a transcript edited elsewhere while a note's file is being read", async () => {
    mocks.notes = [{ attachment: { use_in_notes: true, status: "PENDING" } } as unknown as TimelineNote];
    const hook = await mount();
    mocks.getRecording.mockResolvedValue(recording(1));
    mocks.getTranscript.mockResolvedValue(utterances("Edited elsewhere", 2));

    await advance(3_000);

    expect(lines(hook)).toEqual(["Edited elsewhere"]);
    expect(hook.result.current.recording?.transcript?.change_sequence).toBe(1);
  });
});
