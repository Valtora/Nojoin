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

describe("ServerProviderSection", () => {
  it("warns when the key is set but no model is selected, and still lets one be chosen", () => {
    renderSection({ llm_provider: "gemini", gemini_api_key: "k" }, [
      "gemini-a",
      "gemini-b",
    ]);

    expect(screen.getAllByText("No model selected")).toHaveLength(2);
    expect(screen.getByText(/fail\s+until you choose a model below/)).toBeTruthy();
    // The model list needs only the key, so the controls stay usable.
    expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(false);
    // Found by text: the surrounding <label> gives the button its accessible name.
    expect(screen.getByText("Refresh").closest("button")?.disabled).toBe(false);
    expect(screen.getByRole("button", { name: /Validate API connection/ })).toBeTruthy();
  });

  it("points at Refresh when the model list is empty", () => {
    renderSection({ llm_provider: "gemini", gemini_api_key: "k" }, []);

    expect(screen.getByText(/If the list is empty, press Refresh/)).toBeTruthy();
  });

  it("shows no warning once a model is selected", () => {
    renderSection(
      { llm_provider: "gemini", gemini_api_key: "k", gemini_model: "gemini-a" },
      ["gemini-a"],
    );

    expect(screen.getByText("Configured via server (.env)")).toBeTruthy();
    expect(screen.queryByText("No model selected")).toBeNull();
  });

  it("keeps the model controls off without a key", () => {
    renderSection({ llm_provider: "openai" }, []);

    expect(screen.getByText("Not configured")).toBeTruthy();
    expect(screen.queryByText("No model selected")).toBeNull();
    expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /Validate API connection/ })).toBeNull();
  });
});
