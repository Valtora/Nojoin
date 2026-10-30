import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNavigationStore } from "@/lib/store";
import { restoreTourPanel, transcriptSteps } from "@/lib/tour-config";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import { useRecordingDetail } from "./useRecordingDetail";

// The real navigation store, which remembers the panel; the network is stubbed.
const mocks = vi.hoisted(() => ({
  getRecording: vi.fn(), getTranscript: vi.fn(), notify: vi.fn(),
  router: { push: vi.fn(), refresh: vi.fn() },
}));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getRecording: (...args: unknown[]) => mocks.getRecording(...args),
  getRecordingIfChanged: async () => null,
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
  getGlobalSpeakers: async () => [], getSettings: async () => ({}),
}));
vi.mock("@/lib/api/transcript", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api/transcript")>(),
  getTranscriptUtterances: (...args: unknown[]) => mocks.getTranscript(...args),
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("@/lib/notes/useNoteOutbox", () => ({ useRecordingNotes: () => [] }));
vi.mock("@/lib/notes/useNoteEdits", () => ({ useNoteEdits: () => ({}) }));
vi.mock("@/components/recordings/_hooks/useRecordingActions", () => ({ useRecordingActions: () => ({}) }));
vi.mock("@/components/ViewportDensityProvider", () => ({
  useViewportDensity: () => ({ isCompact: false, isMobile: false }),
}));

const transcriptRow = (transcript_status: string) => ({
  id: 1, recording_id: "rec-1", created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z",
  segments: [], transcript_status,
});
const finished: Recording = {
  id: "rec-1", created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z",
  name: "Welcome to Nojoin", meeting_uid: "meeting-1", audio_path: "/audio.wav",
  status: RecordingStatus.PROCESSED, client_status: ClientStatus.IDLE, has_audio: true, has_proxy: true,
  speakers: [], upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false,
  transcript: transcriptRow("completed"),
};
const capturing: Recording = {
  ...finished, id: "rec-2", name: "Example standup", status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING, transcript: transcriptRow("processing"),
};
const advance = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const open = (value: Recording, capture?: { ownedRecordingId: string | null; status: string }) => {
  mocks.getRecording.mockResolvedValue(value);
  const params = Promise.resolve({ id: value.id });
  return renderHook(() => useRecordingDetail({ params, capture }));
};
const remembered = () => useNavigationStore.getState().activePanel;

describe("recording page panels (#834)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.getTranscript.mockReset().mockResolvedValue({
      recording_id: "rec-1", revision: 1, speakers: [], tombstones: [], utterances: [],
    });
    useNavigationStore.setState({ activePanel: "transcript" });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it("opens a finished meeting on the panel last chosen", async () => {
    useNavigationStore.setState({ activePanel: "notes" });
    const { result } = open(finished);
    await advance();
    expect(result.current.activePanel).toBe("notes");
  });

  it.each([
    ["started in this tab", { ownedRecordingId: "rec-2", status: "recording" }],
    ["recorded in another tab", undefined],
  ])("opens a capture %s on its transcript, keeping the panel last chosen", async (_, capture) => {
    useNavigationStore.setState({ activePanel: "notes" });
    const { result } = open(capturing, capture);
    await advance();
    expect(result.current.activePanel).toBe("transcript");
    // A finished meeting still opens on Notes afterwards.
    expect(remembered()).toBe("notes");

    // A panel chosen on the capture is shown, and remembered as usual.
    act(() => result.current.setActivePanel("analytics"));
    expect(result.current.activePanel).toBe("analytics");
    expect(remembered()).toBe("analytics");
  });

  it("opens a paused capture on its transcript", async () => {
    useNavigationStore.setState({ activePanel: "documents" });
    const { result } = open({ ...capturing, status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED });
    await advance();
    expect(result.current.activePanel).toBe("transcript");
  });

  it("keeps a capture on its transcript through Stop and processing", async () => {
    useNavigationStore.setState({ activePanel: "notes" });
    const { result } = open(capturing);
    await advance();
    mocks.getRecording.mockResolvedValue({ ...capturing, status: RecordingStatus.QUEUED, client_status: ClientStatus.IDLE });
    await advance(1_000);
    expect(result.current.recording?.status).toBe(RecordingStatus.QUEUED);
    expect(result.current.activePanel).toBe("transcript");
  });

  it("opens each panel the transcript tour describes, then puts back the one it found", async () => {
    useNavigationStore.setState({ activePanel: "analytics" });
    const { result } = open(finished);
    await advance();
    const shown: string[] = [];
    for (const step of transcriptSteps) {
      act(() => step.onHighlightStarted?.(undefined, step, {} as never));
      shown.push(result.current.activePanel);
    }
    expect(shown).toEqual(["transcript", "transcript", "transcript", "notes", "documents", "documents", "documents"]);

    act(() => restoreTourPanel());
    expect(result.current.activePanel).toBe("analytics");
    expect(remembered()).toBe("analytics");
  });

  it("puts the panel back when the page goes in the middle of the tour", async () => {
    const { result, unmount } = open(finished);
    await advance();
    const notesStep = transcriptSteps.find((step) => step.element === "#meeting-notes");
    act(() => notesStep?.onHighlightStarted?.(undefined, notesStep, {} as never));
    expect(result.current.activePanel).toBe("notes");
    unmount();
    expect(remembered()).toBe("transcript");
  });
});
