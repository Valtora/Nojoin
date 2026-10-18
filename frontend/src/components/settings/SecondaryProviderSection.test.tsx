import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { Settings } from "@/types";
import SecondaryProviderSection from "./SecondaryProviderSection";
import type { AISettingsModels } from "./useAISettingsModels";

vi.mock("@/lib/api", () => ({
  listModels: vi.fn().mockResolvedValue({ models: [] }),
}));

const models = {
  secondaryAvailableModels: ["qwen-x", "gemma-x"],
  secondaryFetchingModels: false,
  setSecondaryFetchingModels: vi.fn(),
  setSecondaryAvailableModels: vi.fn(),
  handleValidate: vi.fn(),
  validating: null,
  validationMsg: null,
} as unknown as AISettingsModels;

const OLLAMA_FALLBACK: Partial<Settings> = {
  secondary_llm_provider: "ollama",
  secondary_ollama_api_url: "http://ollama:11434",
};

const renderSection = (settings: Partial<Settings>) =>
  render(
    <SecondaryProviderSection
      settings={settings as Settings}
      onUpdate={vi.fn()}
      onPersist={vi.fn()}
      models={models}
    />,
  );

describe("SecondaryProviderSection", () => {
  it("says the fallback is off when it has no model", () => {
    renderSection(OLLAMA_FALLBACK);

    expect(screen.getByText("No model selected")).toBeTruthy();
    expect(screen.queryByText("Configured")).toBeNull();
    expect(screen.getByText("Fallback is off")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Validate fallback connection/ }),
    ).toBeTruthy();
  });

  it("reads as configured once a model is chosen", () => {
    renderSection({ ...OLLAMA_FALLBACK, secondary_ollama_model: "qwen-x" });

    expect(screen.getByText("Configured")).toBeTruthy();
    expect(screen.queryByText("Fallback is off")).toBeNull();
  });

  it("reports a hosted fallback with no key as not configured", () => {
    renderSection({
      secondary_llm_provider: "openai",
      secondary_openai_model: "gpt-x",
    });

    expect(screen.getByText("Not configured")).toBeTruthy();
    expect(screen.queryByText("Fallback is off")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Validate fallback connection/ }),
    ).toBeNull();
  });
});
