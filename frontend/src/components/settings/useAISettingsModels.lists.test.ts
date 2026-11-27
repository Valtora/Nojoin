import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Settings, SystemModelStatus } from "@/types";
import { useAISettingsModels } from "./useAISettingsModels";

const mocks = vi.hoisted(() => ({
  listModels: vi.fn(),
  getModelsStatus: vi.fn(),
  getDownloadProgress: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  ...mocks,
  getLanguageOptions: vi.fn().mockResolvedValue({}),
  prepareModels: vi.fn(),
  validateLLM: vi.fn(),
  deleteModel: vi.fn(),
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

// A fresh install: the default provider resolves to Gemini with no key in .env.
const fresh = {
  whisper_model_size: "base",
  llm_provider: "gemini",
  secondary_llm_provider: "openai",
} as unknown as Settings;
const keyed = {
  ...fresh,
  gemini_api_key: "AIz...abcd",
  secondary_openai_api_key: "sk-...wxyz",
} as unknown as Settings;

async function openSettings(settings: Settings, isAdmin = true) {
  renderHook(() => useAISettingsModels({ settings, isAdmin }));
  // Past the one-second debounce on both list effects.
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
}

describe("provider model lists (#971)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.getModelsStatus.mockResolvedValue({} as SystemModelStatus);
    mocks.getDownloadProgress.mockResolvedValue({
      status: "idle", in_progress: false, stage: null, progress: 0, message: "",
    });
    mocks.listModels.mockResolvedValue({ models: ["model-a"] });
  });
  afterEach(() => { vi.useRealTimers(); });

  it("asks for no list while a provider has no key, as on a fresh install", async () => {
    await openSettings(fresh);

    expect(mocks.listModels).not.toHaveBeenCalled();
  });

  it("asks for no list for someone who is not an administrator", async () => {
    await openSettings(keyed, false);

    expect(mocks.listModels).not.toHaveBeenCalled();
  });

  it("lists each provider's models once it has a key", async () => {
    await openSettings(keyed);

    expect(mocks.listModels).toHaveBeenCalledWith("gemini", "", "");
    expect(mocks.listModels).toHaveBeenCalledWith("openai", "", "", undefined, "secondary");
  });
});
