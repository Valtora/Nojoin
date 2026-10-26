import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DownloadProgress, ModelStatusInfo, Settings, SystemModelStatus } from "@/types";
import { useAISettingsModels } from "./useAISettingsModels";

const mocks = vi.hoisted(() => ({
  getDownloadProgress: vi.fn(), getModelsStatus: vi.fn(), prepareModels: vi.fn(),
  addNotification: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  ...mocks,
  getLanguageOptions: vi.fn().mockResolvedValue({}),
  listModels: vi.fn(), validateLLM: vi.fn(), deleteModel: vi.fn(),
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: mocks.addNotification }),
}));

const settings = { whisper_model_size: "base" } as Settings;
const status = (state: ModelStatusInfo["state"]): SystemModelStatus => {
  const core = { downloaded: true, path: "/cache/model", checked_paths: [] };
  return {
    whisper: core, pyannote: core, embedding: core,
    nemotron: { downloaded: state === "ready", path: null, checked_paths: [], state },
  };
};
const progress = (state: DownloadProgress["status"]): DownloadProgress => ({
  status: state, in_progress: state === "downloading", stage: "nemotron",
  progress: state === "complete" ? 100 : 0, message: `Preparation ${state}`,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const tick = async (milliseconds = 2000) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
};
const openModels = async () => {
  const hook = renderHook(() => useAISettingsModels({ settings }));
  await act(async () => {});
  return hook;
};

describe("model preparation status transitions", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.getDownloadProgress.mockResolvedValue(progress("idle"));
    mocks.getModelsStatus.mockResolvedValue(status("absent"));
    mocks.prepareModels.mockResolvedValue({ task_id: "task", target: "nemotron", status: "queued" });
  });
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it("reads idle status once on mount without a preparation timer", async () => {
    const intervals = vi.spyOn(globalThis, "setInterval");
    const { result } = await openModels();
    expect(result.current.modelStatus?.nemotron.state).toBe("absent");
    expect(result.current.preparationRunning).toBe(false);
    expect(mocks.getModelsStatus).toHaveBeenCalledExactlyOnceWith("base");
    expect(mocks.getDownloadProgress).toHaveBeenCalledTimes(1);
    expect(intervals).not.toHaveBeenCalled();

    await tick(10_000);
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(1);
    expect(mocks.getDownloadProgress).toHaveBeenCalledTimes(1);
  });

  it.each(["ready", "failed", "absent"] as const)(
    "follows a pending marker despite inactive progress and stops at %s",
    async (terminal) => {
      mocks.getModelsStatus.mockResolvedValue(status("downloading"));
      const { result } = await openModels();
      expect(result.current.preparationRunning).toBe(true);
      await tick();
      expect(mocks.getModelsStatus).toHaveBeenCalledTimes(2);
      expect(result.current.preparationRunning).toBe(true);

      mocks.getModelsStatus.mockResolvedValue(status(terminal));
      await tick();
      expect(result.current.modelStatus?.nemotron.state).toBe(terminal);
      expect(result.current.preparationRunning).toBe(false);
      const reads = mocks.getModelsStatus.mock.calls.length;
      const progressReads = mocks.getDownloadProgress.mock.calls.length;
      await tick(6000);
      expect(mocks.getModelsStatus).toHaveBeenCalledTimes(reads);
      expect(mocks.getDownloadProgress).toHaveBeenCalledTimes(progressReads);
    },
  );

  it("follows active global progress for another model even while Nemotron is absent", async () => {
    mocks.getDownloadProgress.mockResolvedValue({ ...progress("downloading"), stage: "parakeet" });
    const { result } = await openModels();
    expect(result.current.preparationRunning).toBe(true);
    await tick();
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(2);
    mocks.getDownloadProgress.mockResolvedValue(progress("complete"));
    await tick();
    expect(result.current.preparationRunning).toBe(false);
    expect(mocks.addNotification).toHaveBeenCalledWith({ type: "success", message: "Model preparation complete." });
  });

  it("refreshes explicit failure, retry and completion, retaining silent-transfer gating", async () => {
    const { result } = await openModels();
    mocks.getModelsStatus.mockResolvedValue(status("downloading"));
    await act(async () => { await result.current.startPreparation("nemotron"); });
    expect(mocks.prepareModels).toHaveBeenCalledExactlyOnceWith("nemotron");
    mocks.getDownloadProgress.mockResolvedValue({ ...progress("downloading"), in_progress: false });
    await tick(32_000);
    expect(result.current.preparationRunning).toBe(true);
    expect(result.current.preparing).toBe("nemotron");

    mocks.getDownloadProgress.mockResolvedValue(progress("error"));
    mocks.getModelsStatus.mockResolvedValue(status("failed"));
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe("failed");
    expect(result.current.preparationRunning).toBe(false);
    expect(mocks.addNotification).toHaveBeenCalledWith({ type: "error", message: "Preparation error" });

    mocks.getModelsStatus.mockResolvedValue(status("downloading"));
    await act(async () => { await result.current.startPreparation("nemotron"); });
    // A previous terminal progress record must not override the new pending marker.
    await tick();
    expect(result.current.preparationRunning).toBe(true);
    mocks.getDownloadProgress.mockResolvedValue(progress("complete"));
    mocks.getModelsStatus.mockResolvedValue(status("ready"));
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe("ready");
    expect(result.current.preparationRunning).toBe(false);
    expect(result.current.preparing).toBeNull();
    expect(mocks.addNotification).toHaveBeenCalledWith({ type: "success", message: "Model preparation complete." });
    mocks.addNotification.mockClear();
    const reads = mocks.getModelsStatus.mock.calls.length;
    await tick(6000);
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(reads);
    expect(mocks.addNotification).not.toHaveBeenCalled();
  });

  it("keeps controls gated throughout queueing, then clears them on queue failure", async () => {
    const queue = deferred<never>();
    mocks.prepareModels.mockReturnValue(queue.promise);
    const { result } = await openModels();
    let request!: Promise<boolean>;
    act(() => { request = result.current.startPreparation("nemotron"); });
    await tick(6000);
    expect(result.current.preparationRunning).toBe(true);
    expect(result.current.preparing).toBe("nemotron");
    expect(mocks.getDownloadProgress).toHaveBeenCalledTimes(1);

    await act(async () => { queue.reject(new Error("Worker unavailable")); await request; });
    expect(await request).toBe(false);
    expect(result.current.preparationRunning).toBe(false);
    expect(result.current.preparing).toBeNull();
    expect(mocks.addNotification).toHaveBeenCalledWith({
      type: "error", message: "Could not start model preparation: Worker unavailable",
    });
  });

  it("clears local gating when a settled queued task leaves only idle progress and absent status", async () => {
    const { result } = await openModels();
    await act(async () => { await result.current.startPreparation("nemotron"); });
    expect(result.current.preparationRunning).toBe(true);
    await tick();
    expect(result.current.preparationRunning).toBe(false);
    expect(result.current.preparing).toBeNull();
  });

  it("coalesces slow status reads from polling and explicit refreshes", async () => {
    const pending = deferred<SystemModelStatus>();
    mocks.getDownloadProgress.mockResolvedValue(progress("downloading"));
    mocks.getModelsStatus.mockReturnValue(pending.promise);
    const { result } = await openModels();
    let refresh!: Promise<SystemModelStatus | null>;
    act(() => { refresh = result.current.refreshStatus(); });
    await tick(6000);
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(1);
    // One mount progress read plus one poll held on the shared status promise.
    expect(mocks.getDownloadProgress).toHaveBeenCalledTimes(2);
    await act(async () => { pending.resolve(status("downloading")); await refresh; });
    mocks.getModelsStatus.mockResolvedValue(status("ready"));
    mocks.getDownloadProgress.mockResolvedValue(progress("complete"));
    await tick();
    expect(result.current.preparationRunning).toBe(false);
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(2);
  });

  it("ignores a previous Whisper size's response and serialises the next size's read", async () => {
    const base = deferred<SystemModelStatus>();
    const large = deferred<SystemModelStatus>();
    mocks.getModelsStatus.mockReturnValueOnce(base.promise).mockReturnValueOnce(large.promise);
    const { result, rerender } = renderHook(
      ({ size }) => useAISettingsModels({ settings: { ...settings, whisper_model_size: size } }),
      { initialProps: { size: "base" } },
    );
    rerender({ size: "large" });
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(1);
    await act(async () => { base.resolve(status("absent")); });
    expect(result.current.modelStatus).toBeNull();
    expect(mocks.getModelsStatus).toHaveBeenLastCalledWith("large");
    await act(async () => { large.resolve(status("ready")); });
    expect(result.current.modelStatus?.nemotron.state).toBe("ready");
  });

  it("does not start a queued size-change status read after closing Settings", async () => {
    const pending = deferred<SystemModelStatus>();
    mocks.getModelsStatus.mockReturnValueOnce(pending.promise);
    const { rerender, unmount } = renderHook(
      ({ size }) => useAISettingsModels({ settings: { ...settings, whisper_model_size: size } }),
      { initialProps: { size: "base" } },
    );
    rerender({ size: "large" });
    unmount();
    await act(async () => { pending.resolve(status("absent")); });
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(1);
  });

  it("retries a failed status read while preparation is active", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.getModelsStatus.mockResolvedValue(status("downloading"));
    const { result } = await openModels();
    mocks.getModelsStatus.mockRejectedValueOnce(new Error("Temporary API outage"));
    await tick();
    expect(result.current.preparationRunning).toBe(true);
    mocks.getModelsStatus.mockResolvedValue(status("ready"));
    await tick();
    expect(result.current.preparationRunning).toBe(false);
  });

  it.each(["progress", "status"] as const)("does not resume after closing Settings during a %s read", async (kind) => {
    mocks.getModelsStatus.mockResolvedValue(status("downloading"));
    const { unmount } = await openModels();
    const pendingProgress = deferred<DownloadProgress>();
    const pendingStatus = deferred<SystemModelStatus>();
    if (kind === "progress") mocks.getDownloadProgress.mockReturnValueOnce(pendingProgress.promise);
    else mocks.getModelsStatus.mockReturnValueOnce(pendingStatus.promise);
    await tick();
    unmount();
    const reads = mocks.getModelsStatus.mock.calls.length;
    const progressReads = mocks.getDownloadProgress.mock.calls.length;
    await act(async () => {
      pendingProgress.resolve(progress("complete"));
      pendingStatus.resolve(status("ready"));
    });
    await tick(6000);
    expect(mocks.getModelsStatus).toHaveBeenCalledTimes(reads);
    expect(mocks.getDownloadProgress).toHaveBeenCalledTimes(progressReads);
    expect(mocks.addNotification).not.toHaveBeenCalled();
  });
});
