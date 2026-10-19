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
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.getByText("The fallback is off")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeTruthy();
  });

  it("reads as ready once a model is chosen", () => {
    renderSection({ ...OLLAMA_FALLBACK, secondary_ollama_model: "qwen-x" });

    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.queryByText("The fallback is off")).toBeNull();
  });

  it("explains that a hosted fallback with no key is off", () => {
    renderSection({
      secondary_llm_provider: "openai",
      secondary_openai_model: "gpt-x",
    });

    expect(screen.getByText("Not set up")).toBeTruthy();
    expect(screen.getByText(/OpenAI has no key in \.env/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Test connection" })).toBeNull();
  });

  it("lets the Meeting Edge model fall back to the fallback's own model", () => {
    renderSection({ ...OLLAMA_FALLBACK, secondary_ollama_model: "qwen-x" });

    const live = screen.getByRole("combobox", { name: "Meeting Edge model" });
    const empty = live.querySelector('option[value=""]') as HTMLOptionElement;
    expect(empty.disabled).toBe(false);

    const main = screen.getByRole("combobox", { name: "Model" });
    const required = main.querySelector('option[value=""]') as HTMLOptionElement;
    expect(required.disabled).toBe(true);
  });
});
