import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { Settings } from "@/types";
import ServerProviderSection from "./ServerProviderSection";
import type { AISettingsModels } from "./useAISettingsModels";

vi.mock("@/lib/api", () => ({
  listModels: vi.fn().mockResolvedValue({ models: [] }),
}));

const models = (availableModels: string[]): AISettingsModels =>
  ({
    availableModels,
    fetchingModels: false,
    setFetchingModels: vi.fn(),
    setAvailableModels: vi.fn(),
    handleValidate: vi.fn(),
    validating: null,
    validationMsg: null,
  }) as unknown as AISettingsModels;

const renderSection = (settings: Partial<Settings>, availableModels: string[]) =>
  render(
    <ServerProviderSection
      settings={settings as Settings}
      onUpdate={vi.fn()}
      onPersist={vi.fn()}
      models={models(availableModels)}
    />,
  );

const modelSelect = () =>
  screen.getByRole("combobox", { name: "Model" }) as HTMLSelectElement;
const refreshButton = () =>
  screen.getByRole("button", { name: "Reload the model list" }) as HTMLButtonElement;

describe("ServerProviderSection", () => {
  it("warns when the key is set but no model is selected, and still lets one be chosen", () => {
    renderSection({ llm_provider: "gemini", gemini_api_key: "k" }, [
      "gemini-a",
      "gemini-b",
    ]);

    expect(screen.getByText("No model selected")).toBeTruthy();
    expect(screen.getByText("AI is off until a model is chosen")).toBeTruthy();
    // The model list needs only the key, so the controls stay usable.
    expect(modelSelect().disabled).toBe(false);
    expect(refreshButton().disabled).toBe(false);
    expect(screen.getByRole("button", { name: "Test connection" })).toBeTruthy();
  });

  it("names the provider properly rather than capitalising its id", () => {
    renderSection({ llm_provider: "openai", openai_api_key: "k" }, []);

    expect(screen.getByText("OpenAI")).toBeTruthy();
  });

  it("points at the reload button when the model list is empty", () => {
    renderSection({ llm_provider: "gemini", gemini_api_key: "k" }, []);

    expect(screen.getByText(/If the list is empty, reload it/)).toBeTruthy();
  });

  it("shows no warning once a model is selected", () => {
    renderSection(
      { llm_provider: "gemini", gemini_api_key: "k", gemini_model: "gemini-a" },
      ["gemini-a"],
    );

    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.queryByText("No model selected")).toBeNull();
    expect(modelSelect().value).toBe("gemini-a");
  });

  it("keeps the model controls off without a key", () => {
    renderSection({ llm_provider: "openai" }, []);

    expect(screen.getByText("Not set up")).toBeTruthy();
    expect(screen.getByText("OpenAI is not set up")).toBeTruthy();
    expect(modelSelect().disabled).toBe(true);
    expect(refreshButton().disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Test connection" })).toBeNull();
  });

  it("shows how much GPU memory the Ollama context window needs", () => {
    renderSection(
      { llm_provider: "ollama", ollama_api_url: "http://ollama.test:11434", ollama_context_window: 131072 },
      [],
    );

    const contextWindow = screen.getByRole("spinbutton", { name: "Context window" });
    expect(contextWindow.getAttribute("aria-describedby")).toBe(
      "ai-primary-context-window-help",
    );
    expect(screen.getByText("23.8 GB")).toBeTruthy();
  });
});
