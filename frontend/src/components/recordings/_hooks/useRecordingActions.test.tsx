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
  cancelProcessing: vi.fn(),
  deleteRecording: vi.fn(),
  archiveRecording: vi.fn(),
  restoreRecording: vi.fn(),
  softDeleteRecording: vi.fn(),
  permanentlyDeleteRecording: vi.fn(),
};

vi.mock("@/lib/api", () => ({
  renameRecording: (...a: unknown[]) => api.renameRecording(...a),
  inferSpeakers: (...a: unknown[]) => api.inferSpeakers(...a),
  cancelProcessing: (...a: unknown[]) => api.cancelProcessing(...a),
  deleteRecording: (...a: unknown[]) => api.deleteRecording(...a),
  archiveRecording: (...a: unknown[]) => api.archiveRecording(...a),
  restoreRecording: (...a: unknown[]) => api.restoreRecording(...a),
  softDeleteRecording: (...a: unknown[]) => api.softDeleteRecording(...a),
  permanentlyDeleteRecording: (...a: unknown[]) =>
    api.permanentlyDeleteRecording(...a),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("useRecordingActions", () => {
  beforeEach(() => {
    addNotification.mockReset();
    Object.values(api).forEach((fn) => fn.mockReset().mockResolvedValue(undefined));
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

  it("notifies on inferSpeakers and cancel success", async () => {
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
      await result.current.cancel("rec-1");
    });
    expect(api.cancelProcessing).toHaveBeenCalledWith("rec-1");
    expect(addNotification).toHaveBeenCalledWith({
      message: "Processing cancelled.",
      type: "success",
    });
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
