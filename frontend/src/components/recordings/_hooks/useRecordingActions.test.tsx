import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import {
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
} from "@/lib/notes/noteStore";
import { noteSignedIn, resetSessionForTests } from "@/lib/session";

import {
  RECORDING_ACTION_IDS,
  useRecordingActions,
} from "./useRecordingActions";

const addNotification = vi.fn();
const api = {
  renameRecording: vi.fn(),
  inferSpeakers: vi.fn(),
  discardRecordingCapture: vi.fn(),
  deleteRecording: vi.fn(),
  batchArchiveRecordings: vi.fn(),
  batchRestoreRecordings: vi.fn(),
  batchSoftDeleteRecordings: vi.fn(),
  batchPermanentlyDeleteRecordings: vi.fn(),
  batchAddTagToRecordings: vi.fn(),
  batchRemoveTagFromRecordings: vi.fn(),
  exportAudio: vi.fn(),
  setRecordingTranscriptionLanguage: vi.fn(),
  reprocessRecording: vi.fn(),
};

vi.mock("@/lib/api", () => ({
  renameRecording: (...a: unknown[]) => api.renameRecording(...a),
  inferSpeakers: (...a: unknown[]) => api.inferSpeakers(...a),
  discardRecordingCapture: (...a: unknown[]) =>
    api.discardRecordingCapture(...a),
  deleteRecording: (...a: unknown[]) => api.deleteRecording(...a),
  batchArchiveRecordings: (...a: unknown[]) => api.batchArchiveRecordings(...a),
  batchRestoreRecordings: (...a: unknown[]) => api.batchRestoreRecordings(...a),
  batchSoftDeleteRecordings: (...a: unknown[]) =>
    api.batchSoftDeleteRecordings(...a),
  batchPermanentlyDeleteRecordings: (...a: unknown[]) =>
    api.batchPermanentlyDeleteRecordings(...a),
  batchAddTagToRecordings: (...a: unknown[]) => api.batchAddTagToRecordings(...a),
  batchRemoveTagFromRecordings: (...a: unknown[]) =>
    api.batchRemoveTagFromRecordings(...a),
  exportAudio: (...a: unknown[]) => api.exportAudio(...a),
  setRecordingTranscriptionLanguage: (...a: unknown[]) =>
    api.setRecordingTranscriptionLanguage(...a),
  reprocessRecording: (...a: unknown[]) => api.reprocessRecording(...a),
}));

const captureState: {
  cancel: ReturnType<typeof vi.fn>;
  recordingId: string | null;
  pausedRecording: { id: string } | null;
  runtimeActive: boolean;
} = {
  cancel: vi.fn(),
  recordingId: null,
  pausedRecording: null,
  runtimeActive: false,
};

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => captureState,
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("useRecordingActions", () => {
  beforeEach(() => {
    addNotification.mockReset();
    Object.values(api).forEach((fn) => fn.mockReset().mockResolvedValue(undefined));
    captureState.cancel.mockReset().mockResolvedValue(undefined);
    captureState.recordingId = null;
    captureState.pausedRecording = null;
    captureState.runtimeActive = false;
  });

  it("exposes exactly the shared recording action set", () => {
    const { result } = renderHook(() => useRecordingActions());
    expect(Object.keys(result.current).sort()).toEqual(
      [...RECORDING_ACTION_IDS].sort(),
    );
  });

  it("calls the rename API and onSuccess, then routes failures to onError + a notification", async () => {
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.rename("rec-1", "New name", { onSuccess, onError });
    });
    expect(api.renameRecording).toHaveBeenCalledWith("rec-1", "New name");
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();

    api.renameRecording.mockRejectedValueOnce(new Error("boom"));
    await act(async () => {
      await result.current.rename("rec-1", "Again", { onSuccess, onError });
    });
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(addNotification).toHaveBeenCalledWith({
      message: "Failed to rename recording.",
      type: "error",
    });
  });

  it("notifies on inferSpeakers and discard success", async () => {
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.inferSpeakers("rec-1");
    });
    expect(api.inferSpeakers).toHaveBeenCalledWith("rec-1");
    expect(addNotification).toHaveBeenCalledWith({
      message:
        "Speaker inference started. Review the suggested names when they are ready.",
      type: "success",
    });

    await act(async () => {
      await result.current.discard("rec-1");
    });
    expect(api.discardRecordingCapture).toHaveBeenCalledWith(
      "rec-1",
      "user_discarded",
    );
    expect(captureState.cancel).not.toHaveBeenCalled();
    expect(addNotification).toHaveBeenCalledWith({
      message: "Recording discarded.",
      type: "success",
    });
  });

  it("routes discard through the capture controller when this browser owns the live capture", async () => {
    captureState.runtimeActive = true;
    captureState.recordingId = "rec-live";
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.discard("rec-live");
    });

    // The controller tears down the recorder/uploader/paused context and still
    // performs the backend discard, so the bare API call must not be used.
    expect(captureState.cancel).toHaveBeenCalledWith("rec-live");
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
    expect(addNotification).toHaveBeenCalledWith({
      message: "Recording discarded.",
      type: "success",
    });
  });

  it("routes discard through the capture controller when this browser owns the paused capture", async () => {
    captureState.pausedRecording = { id: "rec-paused" };
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.discard("rec-paused");
    });

    expect(captureState.cancel).toHaveBeenCalledWith("rec-paused");
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("uses a plain backend discard for a recording owned by another capture session", async () => {
    // An active capture exists for a different recording (e.g. recording A is
    // live in this tab while the user discards queued recording B). Discarding B
    // must not tear down A's runtime.
    captureState.runtimeActive = true;
    captureState.recordingId = "rec-A";
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.discard("rec-B");
    });

    expect(api.discardRecordingCapture).toHaveBeenCalledWith(
      "rec-B",
      "user_discarded",
    );
    expect(captureState.cancel).not.toHaveBeenCalled();
  });

  it("runs onSuccess for the lifecycle actions, the list ones with the server's result", async () => {
    const changed = { count: 1, skipped: 0, recording_ids: ["rec-1"] };
    for (const name of [
      "batchArchiveRecordings",
      "batchRestoreRecordings",
      "batchSoftDeleteRecordings",
      "batchPermanentlyDeleteRecordings",
      "batchAddTagToRecordings",
    ] as const) {
      api[name].mockResolvedValue(changed);
    }
    const { result } = renderHook(() => useRecordingActions());
    const onSuccess = vi.fn();

    await act(async () => {
      await result.current.delete("rec-1", { onSuccess });
      await result.current.archive(["rec-1"], { onSuccess });
      await result.current.unarchive(["rec-1"], { onSuccess });
      await result.current.restore(["rec-1"], { onSuccess });
      await result.current.softDelete(["rec-1"], { onSuccess });
      await result.current.permanentDelete(["rec-1"], { onSuccess });
      await result.current.tag(["rec-1"], "Board", { onSuccess });
    });

    expect(api.deleteRecording).toHaveBeenCalledWith("rec-1");
    expect(api.batchArchiveRecordings).toHaveBeenCalledWith(["rec-1"]);
    // Unarchive and restore are one server action: back to Recordings.
    expect(api.batchRestoreRecordings).toHaveBeenCalledTimes(2);
    expect(api.batchSoftDeleteRecordings).toHaveBeenCalledWith(["rec-1"]);
    expect(api.batchPermanentlyDeleteRecordings).toHaveBeenCalledWith(["rec-1"]);
    expect(api.batchAddTagToRecordings).toHaveBeenCalledWith(["rec-1"], "Board");
    expect(onSuccess).toHaveBeenCalledTimes(7);
    expect(onSuccess).toHaveBeenLastCalledWith(changed);
  });

  it("forgets the notes kept in the browser for a recording it deletes for good or discards", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    noteSignedIn({ username: "alice", force_password_change: false, id: 1 });
    const kept = (recordingId: string) => ({
      version: NOTE_RECORD_VERSION,
      account: 1,
      recordingId,
      notes: [],
      draft: { text: "Half a thought", mode: "typing", typingStartMs: null },
      discards: [],
    });
    try {
      for (const id of ["rec-1", "rec-2", "rec-3", "rec-4", "rec-5"]) {
        await store.put(kept(id));
      }
      const { result } = renderHook(() => useRecordingActions());

      api.batchPermanentlyDeleteRecordings.mockResolvedValueOnce({
        count: 1,
        skipped: 1,
        recording_ids: ["rec-1"],
      });
      await act(async () => {
        // rec-5 was no longer in the bin, so the server kept it.
        await result.current.permanentDelete(["rec-1", "rec-5"]);
        await result.current.discard("rec-2");
      });
      await waitFor(async () =>
        expect(await store.recordingsOf(1)).toEqual(["rec-3", "rec-4", "rec-5"]),
      );

      // Kept when the server refuses, and when the recording only goes to the bin.
      api.batchPermanentlyDeleteRecordings.mockRejectedValueOnce(new Error("offline"));
      api.batchSoftDeleteRecordings.mockResolvedValueOnce({
        count: 1,
        skipped: 0,
        recording_ids: ["rec-4"],
      });
      await act(async () => {
        await result.current.permanentDelete(["rec-3"]);
        await result.current.softDelete(["rec-4"]);
      });
      expect(await store.recordingsOf(1)).toEqual(["rec-3", "rec-4", "rec-5"]);
    } finally {
      setNoteStoreForTests(null);
      resetSessionForTests();
    }
  });

  describe("list action notifications", () => {
    const lastNotification = () =>
      addNotification.mock.calls.at(-1)?.[0] as {
        type: string;
        message: string;
        durationMs?: number;
        actions?: { label: string; onClick: () => void }[];
      };

    // Runs the notification's Undo and waits for the notification it ends with.
    const undo = async () => {
      const action = lastNotification().actions?.find(({ label }) => label === "Undo");
      expect(action).toBeDefined();
      const shown = addNotification.mock.calls.length;
      action?.onClick();
      await waitFor(() =>
        expect(addNotification.mock.calls.length).toBeGreaterThan(shown),
      );
    };

    it("counts what the server changed, with the right plural", async () => {
      const { result } = renderHook(() => useRecordingActions());

      api.batchArchiveRecordings.mockResolvedValueOnce({
        count: 1,
        skipped: 0,
        recording_ids: ["rec-1"],
      });
      await act(() => result.current.archive(["rec-1"]));
      expect(lastNotification()).toMatchObject({
        type: "success",
        message: "Archived 1 recording.",
      });

      // One of three was in the bin, which archive leaves alone.
      api.batchArchiveRecordings.mockResolvedValueOnce({
        count: 2,
        skipped: 1,
        recording_ids: ["rec-1", "rec-2"],
      });
      await act(() => result.current.archive(["rec-1", "rec-2", "rec-3"]));
      expect(lastNotification().message).toBe("Archived 2 of 3 recordings.");

      api.batchPermanentlyDeleteRecordings.mockResolvedValueOnce({
        count: 1,
        skipped: 1,
        recording_ids: ["rec-2"],
      });
      await act(() => result.current.permanentDelete(["rec-1", "rec-2"]));
      expect(lastNotification().message).toBe(
        "Deleted 1 of 2 recordings permanently. 1 was no longer in the bin, so it was kept.",
      );
    });

    it("offers Undo on every list action but permanent delete", async () => {
      const changed = { count: 2, skipped: 0, recording_ids: ["rec-1", "rec-2"] };
      const { result } = renderHook(() => useRecordingActions());

      api.batchSoftDeleteRecordings.mockResolvedValueOnce(changed);
      await act(() => result.current.softDelete(["rec-1", "rec-2"]));
      expect(lastNotification()).toMatchObject({
        message: "Moved 2 recordings to the bin.",
        durationMs: 10000,
      });
      expect(lastNotification().actions?.map(({ label }) => label)).toEqual(["Undo"]);

      api.batchPermanentlyDeleteRecordings.mockResolvedValueOnce(changed);
      await act(() => result.current.permanentDelete(["rec-1", "rec-2"]));
      expect(lastNotification().message).toBe("Deleted 2 recordings permanently.");
      expect(lastNotification().actions).toBeUndefined();
    });

    it.each([
      ["archive", "batchArchiveRecordings", "batchRestoreRecordings", "Recordings"],
      ["unarchive", "batchRestoreRecordings", "batchArchiveRecordings", "Archived"],
      ["restore", "batchRestoreRecordings", "batchSoftDeleteRecordings", "the bin"],
      ["softDelete", "batchSoftDeleteRecordings", "batchRestoreRecordings", "Recordings"],
    ] as const)(
      "undoes %s for exactly what the server changed",
      async (action, call, inverse, destination) => {
        const onUndone = vi.fn();
        const { result } = renderHook(() => useRecordingActions());
        api[call].mockResolvedValueOnce({
          count: 1,
          skipped: 1,
          recording_ids: ["rec-1"],
        });
        api[inverse].mockResolvedValueOnce({
          count: 1,
          skipped: 0,
          recording_ids: ["rec-1"],
        });

        await act(() => result.current[action](["rec-1", "rec-2"], { onUndone }));
        await undo();

        expect(api[inverse]).toHaveBeenLastCalledWith(["rec-1"]);
        expect(lastNotification()).toMatchObject({
          type: "success",
          message: `Moved 1 recording back to ${destination}.`,
        });
        expect(onUndone).toHaveBeenCalledTimes(1);
      },
    );

    it("puts recordings binned from Archived back in Archived", async () => {
      const changed = { count: 2, skipped: 0, recording_ids: ["rec-1", "rec-2"] };
      api.batchSoftDeleteRecordings.mockResolvedValueOnce(changed);
      api.batchRestoreRecordings.mockResolvedValueOnce(changed);
      api.batchArchiveRecordings.mockResolvedValueOnce(changed);
      const { result } = renderHook(() => useRecordingActions());

      await act(() =>
        result.current.softDelete(["rec-1", "rec-2"], { from: "archived" }),
      );
      await undo();

      // A restore lands in Recordings, so the undo archives them again.
      expect(api.batchRestoreRecordings).toHaveBeenCalledWith(["rec-1", "rec-2"]);
      expect(api.batchArchiveRecordings).toHaveBeenCalledWith(["rec-1", "rec-2"]);
      expect(lastNotification().message).toBe("Moved 2 recordings back to Archived.");
    });

    it("removes the tag again only from the recordings that gained it", async () => {
      api.batchAddTagToRecordings.mockResolvedValueOnce({
        count: 1,
        skipped: 1,
        recording_ids: ["rec-2"],
      });
      api.batchRemoveTagFromRecordings.mockResolvedValueOnce({
        count: 1,
        skipped: 0,
        recording_ids: ["rec-2"],
      });
      const { result } = renderHook(() => useRecordingActions());

      await act(() => result.current.tag(["rec-1", "rec-2"], "Board"));
      expect(lastNotification().message).toBe(
        'Added "Board" to 1 of 2 recordings. The other already had it.',
      );
      await undo();

      expect(api.batchRemoveTagFromRecordings).toHaveBeenCalledWith(["rec-2"], "Board");
      expect(lastNotification().message).toBe('Removed "Board" from 1 recording.');
    });

    it("offers no Undo when the server changed nothing, and says so", async () => {
      api.batchRestoreRecordings.mockResolvedValueOnce({
        count: 0,
        skipped: 2,
        recording_ids: [],
      });
      const { result } = renderHook(() => useRecordingActions());

      await act(() => result.current.restore(["rec-1", "rec-2"]));

      expect(lastNotification()).toMatchObject({
        type: "warning",
        message: "Nothing was restored.",
      });
      expect(lastNotification().actions).toBeUndefined();
    });

    it("reports a failed list action with the reason the server gave", async () => {
      const onError = vi.fn();
      api.batchArchiveRecordings.mockRejectedValueOnce(new Error("offline"));
      const { result } = renderHook(() => useRecordingActions());

      await act(() => result.current.archive(["rec-1", "rec-2"], { onError }));

      expect(lastNotification()).toMatchObject({
        type: "error",
        message: "Failed to archive the recordings.",
      });
      expect(onError).toHaveBeenCalledTimes(1);
    });
  });

  it("exports audio and says what went wrong when there is no file to save", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.exportAudio("rec-1", "Weekly sync", { onSuccess });
    });
    expect(api.exportAudio).toHaveBeenCalledWith("rec-1", "Weekly sync");
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(addNotification).not.toHaveBeenCalled();

    const answers: Array<[number, RegExp]> = [
      [202, /still being prepared/],
      [404, /audio is not available/],
      [500, /^Failed to export audio\.$/],
    ];
    for (const [status, message] of answers) {
      addNotification.mockReset();
      api.exportAudio.mockRejectedValueOnce({ response: { status } });
      await act(async () => {
        await result.current.exportAudio("rec-1", "Weekly sync", { onError });
      });
      expect(addNotification).toHaveBeenCalledWith({
        message: expect.stringMatching(message),
        type: "error",
      });
    }
    expect(onError).toHaveBeenCalledTimes(3);
  });
  it("sets the recording's language without reprocessing it", async () => {
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.setTranscriptionLanguage("rec-1", "en", { onSuccess });
    });

    expect(api.setRecordingTranscriptionLanguage).toHaveBeenCalledWith("rec-1", "en");
    expect(api.reprocessRecording).not.toHaveBeenCalled();
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it("reprocesses with the engine and the recording's new language", async () => {
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      // The engine is the notice's, passed through as it is.
      await result.current.reprocessInLanguage(
        "rec-1",
        { reprocess_language: "auto", engine: "canary", model: null },
        { onSuccess },
      );
    });

    expect(api.reprocessRecording).toHaveBeenCalledWith("rec-1", {
      transcription_backend: "canary",
      transcription_language: "auto",
    });
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it("reprocesses with the Whisper model that made the transcript", async () => {
    const { result } = renderHook(() => useRecordingActions());

    await act(async () => {
      await result.current.reprocessInLanguage("rec-1", {
        reprocess_language: "de",
        engine: "whisper",
        model: "large-v3",
      });
    });

    expect(api.reprocessRecording).toHaveBeenCalledWith("rec-1", {
      transcription_backend: "whisper",
      transcription_language: "de",
      whisper_model_size: "large-v3",
    });
  });

  it.each(["setTranscriptionLanguage", "reprocessInLanguage"] as const)(
    "reports a failed %s and leaves the notice to be answered again",
    async (action) => {
      api.setRecordingTranscriptionLanguage.mockRejectedValue(new Error("down"));
      api.reprocessRecording.mockRejectedValue(new Error("down"));
      const onSuccess = vi.fn();
      const onError = vi.fn();
      const { result } = renderHook(() => useRecordingActions());

      await act(async () => {
        if (action === "setTranscriptionLanguage") {
          await result.current.setTranscriptionLanguage("rec-1", "en", {
            onSuccess,
            onError,
          });
        } else {
          await result.current.reprocessInLanguage(
            "rec-1",
            { reprocess_language: "de", engine: "whisper" },
            { onSuccess, onError },
          );
        }
      });

      expect(onError).toHaveBeenCalledTimes(1);
      expect(onSuccess).not.toHaveBeenCalled();
      expect(addNotification).toHaveBeenCalledWith(
        expect.objectContaining({ type: "error" }),
      );
    },
  );
});
