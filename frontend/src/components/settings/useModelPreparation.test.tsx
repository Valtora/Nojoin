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
const tick = async () => {
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
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
  afterEach(() => { vi.useRealTimers(); });

  it("discovers first-use preparation after opening and refreshes failure, retry and completion", async () => {
    const { result } = await openModels();
    expect(result.current.modelStatus?.nemotron.state).toBe("absent");
    expect(result.current.preparationRunning).toBe(false);

    mocks.getDownloadProgress.mockResolvedValue(progress("downloading"));
    mocks.getModelsStatus.mockResolvedValue(status("downloading"));
    await tick();
    expect(result.current.preparationRunning).toBe(true);
    expect(result.current.modelStatus?.nemotron.state).toBe("downloading");

    mocks.getDownloadProgress.mockResolvedValue(progress("error"));
    mocks.getModelsStatus.mockResolvedValue(status("failed"));
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe("failed");
    expect(result.current.preparationRunning).toBe(false);
    expect(mocks.addNotification).toHaveBeenCalledWith({ type: "error", message: "Preparation error" });

    mocks.getDownloadProgress.mockResolvedValue(progress("downloading"));
    mocks.getModelsStatus.mockResolvedValue(status("downloading"));
    await act(async () => { await result.current.startPreparation("nemotron"); });
    expect(mocks.prepareModels).toHaveBeenCalledExactlyOnceWith("nemotron");
    expect(result.current.preparationRunning).toBe(true);
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe("downloading");

    mocks.getDownloadProgress.mockResolvedValue(progress("complete"));
    mocks.getModelsStatus.mockResolvedValue(status("ready"));
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe("ready");
    expect(result.current.preparationRunning).toBe(false);
    expect(result.current.preparing).toBeNull();
    expect(mocks.addNotification).toHaveBeenCalledWith({ type: "success", message: "Model preparation complete." });
    mocks.addNotification.mockClear();
    await tick();
    expect(mocks.addNotification).not.toHaveBeenCalled();
  });

  it("gates controls immediately while queueing and clears them if the request fails", async () => {
    let rejectQueue!: (error: Error) => void;
    mocks.prepareModels.mockImplementation(() => new Promise((_, reject) => { rejectQueue = reject; }));
    const { result } = await openModels();
    let request!: Promise<boolean>;
    act(() => { request = result.current.startPreparation("nemotron"); });
    expect(result.current.preparationRunning).toBe(true);

    await act(async () => { rejectQueue(new Error("Worker unavailable")); await request; });

    expect(await request).toBe(false);
    expect(result.current.preparationRunning).toBe(false);
    expect(result.current.preparing).toBeNull();
    expect(mocks.addNotification).toHaveBeenCalledWith({
      type: "error", message: "Could not start model preparation: Worker unavailable",
    });
  });

  it("discovers a queued first-use marker and its expiry while global progress stays idle", async () => {
    const { result } = await openModels();
    mocks.getModelsStatus.mockResolvedValue(status("downloading"));
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe("downloading");

    mocks.getModelsStatus.mockResolvedValue(status("absent"));
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe("absent");
  });

  it.each([
    ["complete", "ready"], ["error", "failed"],
  ] as const)("refreshes %s even if the entire preparation fell between polls", async (terminal, state) => {
    const { result } = await openModels();
    mocks.getDownloadProgress.mockResolvedValue(progress(terminal));
    mocks.getModelsStatus.mockResolvedValue(status(state));
    await tick();
    expect(result.current.modelStatus?.nemotron.state).toBe(state);
    expect(result.current.preparationRunning).toBe(false);
  });

  it("clears local preparation gating when a queued task leaves only idle progress", async () => {
    const { result } = await openModels();
    await act(async () => { await result.current.startPreparation("nemotron"); });
    expect(result.current.preparationRunning).toBe(true);
    await tick();
    expect(result.current.preparationRunning).toBe(false);
    expect(result.current.preparing).toBeNull();
  });

  it("stops polling when Settings is closed", async () => {
    const { unmount } = await openModels();
    unmount();
    mocks.getDownloadProgress.mockClear();
    await tick();
    expect(mocks.getDownloadProgress).not.toHaveBeenCalled();
  });
});
