import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording, type TranscriptSegment } from "@/types";
import { useRecordingDetail } from "./useRecordingDetail";

const mocks = vi.hoisted(() => ({
  read: vi.fn(), transcript: vi.fn(), text: vi.fn(), rename: vi.fn(), assign: vi.fn(), notes: vi.fn(),
  notify: vi.fn(), router: { push: vi.fn() },
  navigation: { activePanel: "transcript", chatPanelHeight: 30, setChatPanelHeight: vi.fn(), setActivePanel: vi.fn() },
}));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  getRecording: (...args: unknown[]) => mocks.read(...args),
  getTranscriptUtterances: (...args: unknown[]) => mocks.transcript(...args),
  updateTranscriptUtteranceText: (...args: unknown[]) => mocks.text(...args),
  updateTranscriptUtteranceSpeaker: (...args: unknown[]) => mocks.assign(...args),
  updateSpeaker: (...args: unknown[]) => mocks.rename(...args),
  updateNotes: (...args: unknown[]) => mocks.notes(...args),
  getSettings: async () => ({}), getGlobalSpeakers: async () => [],
}));
vi.mock("@/lib/api/transcript", () => ({ getTranscriptUtterances: () => new Promise(() => {}) }));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("@/lib/store", () => ({ useNavigationStore: () => mocks.navigation }));
vi.mock("@/lib/notes/useNoteOutbox", () => ({ useRecordingNotes: () => [] }));
vi.mock("@/lib/notes/useNoteEdits", () => ({ useNoteEdits: () => ({}) }));
vi.mock("@/components/recordings/_hooks/useRecordingActions", () => ({ useRecordingActions: () => ({}) }));
vi.mock("@/components/ViewportDensityProvider", () => ({
  useViewportDensity: () => ({ isCompact: false, isMobile: false }),
}));

const line: TranscriptSegment = { id: "u1", revision: 3, speaker: "SPEAKER_1", start: 0, end: 1, text: "Original" };
const recording: Recording = {
  id: "rec-1", created_at: "2026-10-27", updated_at: "2026-10-27", name: "Meeting", meeting_uid: "meeting-1",
  audio_path: "/audio.wav", status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE,
  has_audio: true, has_proxy: true, speakers: [], upload_progress: 100, processing_progress: 100,
  is_archived: false, is_deleted: false,
};
const params = Promise.resolve({ id: "rec-1" });
const mount = async () => {
  const hook = renderHook(() => useRecordingDetail({ params }));
  await act(async () => vi.advanceTimersByTimeAsync(0));
  return hook;
};

describe("finished transcript mutation outcomes", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.read.mockReset().mockResolvedValue(recording);
    mocks.transcript.mockReset().mockResolvedValue({
      recording_id: "rec-1", revision: 3, speakers: [], tombstones: [], utterances: [line],
    });
    mocks.text.mockReset().mockResolvedValue(undefined);
    mocks.assign.mockReset().mockResolvedValue(undefined);
    mocks.rename.mockReset().mockResolvedValue({ diarization_label: "SPEAKER_1", local_name: "Alex" });
    mocks.notes.mockReset().mockResolvedValue(undefined);
    mocks.notify.mockReset();
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it("blocks a debounced notes save captured before reprocessing began", async () => {
    const { result } = await mount();
    const saveNotes = result.current.handleNotesChange;
    act(() => result.current.setRecording({ ...recording, status: RecordingStatus.QUEUED,
      previous_publication_available: true }));
    await act(async () => { await saveNotes("An earlier editor's delayed save"); });
    expect(mocks.notes).not.toHaveBeenCalled();
  });

  it.each(["text", "rename", "assign"] as const)("reports %s refusal instead of resolving as success", async (kind) => {
    const { result } = await mount();
    mocks[kind].mockRejectedValueOnce(new Error("Stale revision"));
    let accepted: unknown;
    await act(async () => {
      if (kind === "text") accepted = await result.current.handleUpdateSegmentText(line, "Changed");
      if (kind === "rename") accepted = await result.current.handleRenameSpeaker("SPEAKER_1", "Alex");
      if (kind === "assign") accepted = await result.current.handleUpdateSegmentSpeaker(line, {
        name: "Alex", scope: "utterance_only",
      });
    });
    expect(accepted).toBe(false);
  });

  it.each(["text", "rename", "assign"] as const)("reports accepted %s writes", async (kind) => {
    const { result } = await mount();
    let accepted: unknown;
    await act(async () => {
      if (kind === "text") accepted = await result.current.handleUpdateSegmentText(line, "Changed");
      if (kind === "rename") accepted = await result.current.handleRenameSpeaker("SPEAKER_1", "Alex");
      if (kind === "assign") accepted = await result.current.handleUpdateSegmentSpeaker(line, {
        name: "Alex", scope: "utterance_only",
      });
    });
    expect(accepted).toBe(true);
  });

  it("keeps a successful rename accepted when its following refresh fails", async () => {
    const { result } = await mount();
    mocks.read.mockRejectedValueOnce(new Error("Refresh offline"));
    let accepted: unknown;
    await act(async () => { accepted = await result.current.handleRenameSpeaker("SPEAKER_1", "Alex"); });
    expect(mocks.rename).toHaveBeenCalledTimes(1);
    expect(accepted).toBe(true);
    expect(mocks.notify).toHaveBeenCalledWith({
      type: "warning", message: "Speaker renamed, but the recording could not be refreshed.",
    });
  });

  it("sends a finished assignment against the revision its picker opened with", async () => {
    const { result } = await mount();
    const assignment = { name: "Alex", scope: "utterance_only" as const };
    await act(async () => { await result.current.handleUpdateSegmentSpeaker(line, assignment); });
    expect(mocks.assign).toHaveBeenCalledWith("rec-1", "u1", assignment, 3);
  });
});
