import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

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
  archiveRecording: vi.fn(),
  restoreRecording: vi.fn(),
  softDeleteRecording: vi.fn(),
  permanentlyDeleteRecording: vi.fn(),
};

vi.mock("@/lib/api", () => ({
  renameRecording: (...a: unknown[]) => api.renameRecording(...a),
  inferSpeakers: (...a: unknown[]) => api.inferSpeakers(...a),
  discardRecordingCapture: (...a: unknown[]) =>
    api.discardRecordingCapture(...a),
  deleteRecording: (...a: unknown[]) => api.deleteRecording(...a),
  archiveRecording: (...a: unknown[]) => api.archiveRecording(...a),
  restoreRecording: (...a: unknown[]) => api.restoreRecording(...a),
  softDeleteRecording: (...a: unknown[]) => api.softDeleteRecording(...a),
  permanentlyDeleteRecording: (...a: unknown[]) =>
    api.permanentlyDeleteRecording(...a),
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

  it("runs onSuccess for the lifecycle actions (delete/archive/restore/softDelete/permanentDelete)", async () => {
    const { result } = renderHook(() => useRecordingActions());
    const onSuccess = vi.fn();

    await act(async () => {
      await result.current.delete("rec-1", { onSuccess });
      await result.current.archive("rec-1", { onSuccess });
      await result.current.restore("rec-1", { onSuccess });
      await result.current.softDelete("rec-1", { onSuccess });
      await result.current.permanentDelete("rec-1", { onSuccess });
    });

    expect(api.deleteRecording).toHaveBeenCalledWith("rec-1");
    expect(api.archiveRecording).toHaveBeenCalledWith("rec-1");
    expect(api.restoreRecording).toHaveBeenCalledWith("rec-1");
    expect(api.softDeleteRecording).toHaveBeenCalledWith("rec-1");
    expect(api.permanentlyDeleteRecording).toHaveBeenCalledWith("rec-1");
    expect(onSuccess).toHaveBeenCalledTimes(5);
  });
});
