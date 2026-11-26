import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationAction } from "@/lib/notificationStore";
import {
  ClientStatus, RecordingStatus, type Recording, type TimelineNote, type TranscriptUtteranceList,
} from "@/types";
import { useRecordingDetail } from "./useRecordingDetail";

// A shared meeting's notes can be edited by several people. Each save sends
// the revision it started from; a save refused as a conflict is not lost
// silently, and the user picks between the other version and theirs.
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

const recording = (changeSequence: number, overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-11-01T10:00:00Z", updated_at: "2026-11-01T10:00:00Z",
  name: "Weekly sync", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.PROCESSED,
  client_status: ClientStatus.IDLE, has_audio: true, has_proxy: true, speakers: [],
  upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false,
  transcript: {
    id: 1, created_at: "2026-11-01", updated_at: "2026-11-01", recording_id: "rec-1", segments: [],
    change_sequence: changeSequence, notes: "Alice Example will send the plan.", notes_revision: "rev-a",
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

const conflict = (notes: string, revision: string) => Object.assign(new Error("conflict"), {
  response: { status: 409, data: { detail: { code: "notes_conflict", notes, notes_revision: revision } } },
});
const conflictActions = (): NotificationAction[] => {
  const call = mocks.notify.mock.calls.find(([payload]) => payload.persistent);
  return call?.[0].actions ?? [];
};

describe("saving notes someone else may also be editing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.getRecording.mockReset().mockResolvedValue(recording(0));
    mocks.getRecordingIfChanged.mockReset().mockResolvedValue(null);
    mocks.getTranscript.mockReset().mockResolvedValue(utterances("Original", 1));
    mocks.updateNotes.mockReset();
    mocks.notify.mockReset();
    mocks.notes = [];
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("sends each save from the revision the last one left", async () => {
    mocks.updateNotes
      .mockResolvedValueOnce({ notes: "One", notes_revision: "rev-b", status: "ok" })
      .mockResolvedValueOnce({ notes: "Two", notes_revision: "rev-c", status: "ok" });
    const hook = await mount();

    await act(async () => {
      // Two quick saves: the second waits for the first and builds on it.
      void hook.result.current.handleNotesChange("One");
      await hook.result.current.handleNotesChange("Two");
    });

    expect(mocks.updateNotes.mock.calls).toEqual([
      ["rec-1", "One", "rev-a"],
      ["rec-1", "Two", "rev-b"],
    ]);
    expect(mocks.notify).not.toHaveBeenCalled();
  });

  it("offers their version or mine when someone else saved first", async () => {
    mocks.updateNotes
      .mockRejectedValueOnce(conflict("Bob's notes", "rev-bob"))
      .mockResolvedValueOnce({ notes: "Mine", notes_revision: "rev-mine", status: "ok" });
    const hook = await mount();

    await act(async () => { await hook.result.current.handleNotesChange("Mine"); });

    const actions = conflictActions();
    expect(actions.map((action) => action.label)).toEqual(["Show their version", "Keep mine"]);
    expect(mocks.notify).toHaveBeenCalledTimes(1);

    await act(async () => {
      actions[1].onClick();
      await vi.advanceTimersByTimeAsync(0);
    });
    // Keeping mine saves it again, now over the version it was shown.
    expect(mocks.updateNotes).toHaveBeenLastCalledWith("rec-1", "Mine", "rev-bob");
  });

  it("loads the other version when the user chooses it", async () => {
    mocks.updateNotes.mockRejectedValueOnce(conflict("Bob's notes", "rev-bob"));
    const hook = await mount();
    await act(async () => { await hook.result.current.handleNotesChange("Mine"); });
    mocks.getRecording.mockResolvedValue({
      ...recording(1),
      transcript: { ...recording(1).transcript!, notes: "Bob's notes", notes_revision: "rev-bob" },
    });

    await act(async () => {
      conflictActions()[0].onClick();
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(hook.result.current.recording?.transcript?.notes).toBe("Bob's notes");
    expect(mocks.updateNotes).toHaveBeenCalledTimes(1);
  });
});
