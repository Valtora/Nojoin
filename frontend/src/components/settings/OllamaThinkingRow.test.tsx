import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { getThinkingSupport } from "@/lib/api";
import type { Settings } from "@/types";
import OllamaThinkingRow from "./OllamaThinkingRow";
import SecondaryProviderSection from "./SecondaryProviderSection";
import ServerProviderSection from "./ServerProviderSection";
import type { AISettingsModels } from "./useAISettingsModels";

vi.mock("@/lib/api", () => ({
  listModels: vi.fn().mockResolvedValue({ models: [] }),
  getThinkingSupport: vi.fn(),
}));

const ask = vi.mocked(getThinkingSupport);

const answers = (table: Record<string, boolean | null | "fail">) => {
  ask.mockImplementation(async (model) => {
    const answer = table[model];
    if (answer === "fail") throw new Error("Network Error");
    return { model, supported: answer ?? null };
  });
};

const switchControl = () =>
  screen.getByRole("switch", { name: "Thinking" }) as HTMLButtonElement;

const renderRow = (props: Partial<Parameters<typeof OllamaThinkingRow>[0]> = {}) =>
  render(
    <OllamaThinkingRow
      idPrefix="test"
      model="qwen3.5:4b"
      checked={false}
      onCheckedChange={vi.fn()}
      {...props}
    />,
  );

beforeEach(() => {
  ask.mockReset();
});

describe("OllamaThinkingRow", () => {
  it("says the model supports thinking and leaves the switch usable", async () => {
    answers({ "qwen3.5:4b": true });
    renderRow();

    expect(await screen.findByText("This model supports thinking.")).toBeTruthy();
    expect(switchControl().disabled).toBe(false);
    expect(ask).toHaveBeenCalledWith("qwen3.5:4b", false);
  });

  it("disables the switch for a model that cannot think, and keeps its value", async () => {
    answers({ "llama3.1:latest": false });
    renderRow({ model: "llama3.1:latest", checked: true });

    expect(await screen.findByText(/doesn't support thinking/)).toBeTruthy();
    expect(switchControl().disabled).toBe(true);
    expect(switchControl().getAttribute("aria-checked")).toBe("true");
  });

  it("keeps the switch usable when support is unknown, and warns only while it is on", async () => {
    answers({ "mystery:1b": "fail" });
    const { rerender } = renderRow({ model: "mystery:1b", checked: true });

    expect(
      await screen.findByText("Nojoin couldn't check whether this model supports thinking."),
    ).toBeTruthy();
    expect(switchControl().disabled).toBe(false);
    expect(switchControl().getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/may refuse requests with an error \(HTTP 400\)/)).toBeTruthy();

    rerender(
      <OllamaThinkingRow
        idPrefix="test"
        model="mystery:1b"
        checked={false}
        onCheckedChange={vi.fn()}
      />,
    );
    expect(screen.queryByText(/HTTP 400/)).toBeNull();
    expect(switchControl().disabled).toBe(false);
  });

  it("treats an answer of null from the server as unknown too", async () => {
    answers({ "mystery:1b": null });
    renderRow({ model: "mystery:1b" });

    expect(await screen.findByText(/couldn't check/)).toBeTruthy();
    expect(switchControl().disabled).toBe(false);
  });

  it("follows the model as it changes, with nothing saved", async () => {
    answers({ "qwen3.5:4b": true, "llama3.1:latest": false, "mystery:1b": null });
    const onCheckedChange = vi.fn();
    const { rerender } = renderRow({ onCheckedChange });
    await screen.findByText("This model supports thinking.");

    const withModel = (model: string) => (
      <OllamaThinkingRow
        idPrefix="test"
        model={model}
        checked
        onCheckedChange={onCheckedChange}
      />
    );
    rerender(withModel("llama3.1:latest"));
    expect(await screen.findByText(/doesn't support thinking/)).toBeTruthy();
    expect(switchControl().disabled).toBe(true);

    rerender(withModel("mystery:1b"));
    expect(await screen.findByText(/couldn't check/)).toBeTruthy();
    expect(switchControl().disabled).toBe(false);

    rerender(withModel("qwen3.5:4b"));
    expect(await screen.findByText("This model supports thinking.")).toBeTruthy();
    expect(screen.queryByText(/HTTP 400/)).toBeNull();
    expect(onCheckedChange).not.toHaveBeenCalled();
  });

  it("does not show the last model's answer while the next one is checked", async () => {
    answers({ "qwen3.5:4b": true });
    const { rerender } = renderRow();
    await screen.findByText("This model supports thinking.");

    ask.mockImplementation(() => new Promise(() => {}));
    rerender(
      <OllamaThinkingRow
        idPrefix="test"
        model="slow:7b"
        checked={false}
        onCheckedChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/Checking whether this model supports thinking/)).toBeTruthy();
    expect(screen.queryByText("This model supports thinking.")).toBeNull();
  });

  it("asks nothing before a model is chosen", () => {
    renderRow({ model: "" });

    expect(ask).not.toHaveBeenCalled();
    expect(screen.getByText(/Choose a model to see whether it supports thinking/)).toBeTruthy();
    expect(switchControl().disabled).toBe(false);
  });

  it("asks the fallback's server for the fallback", async () => {
    answers({ "qwen3.5:4b": true });
    renderRow({ secondary: true });

    await waitFor(() => expect(ask).toHaveBeenCalledWith("qwen3.5:4b", true));
  });

  it("reports the new value when the switch is used", async () => {
    answers({ "qwen3.5:4b": true });
    const onCheckedChange = vi.fn();
    renderRow({ onCheckedChange });
    await screen.findByText("This model supports thinking.");

    fireEvent.click(switchControl());

    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });
});

const models = {
  availableModels: ["qwen3.5:4b", "llama3.1:latest"],
  fetchingModels: false,
  setFetchingModels: vi.fn(),
  setAvailableModels: vi.fn(),
  secondaryAvailableModels: ["qwen3.5:4b", "llama3.1:latest"],
  secondaryFetchingModels: false,
  setSecondaryFetchingModels: vi.fn(),
  setSecondaryAvailableModels: vi.fn(),
  handleValidate: vi.fn(),
  validating: null,
  validationMsg: null,
} as unknown as AISettingsModels;

const OLLAMA: Partial<Settings> = {
  llm_provider: "ollama",
  ollama_api_url: "http://ollama.test:11434",
  ollama_model: "qwen3.5:4b",
};

describe("Thinking on the provider cards", () => {
  it("saves the default provider's choice at once, over the rest of its settings", async () => {
    answers({ "qwen3.5:4b": true });
    const onPersist = vi.fn();
    render(
      <ServerProviderSection
        settings={OLLAMA as Settings}
        onUpdate={vi.fn()}
        onPersist={onPersist}
        models={models}
      />,
    );
    await screen.findByText("This model supports thinking.");

    fireEvent.click(switchControl());

    expect(onPersist).toHaveBeenCalledWith({ ...OLLAMA, ollama_think: true });
  });

  it("tracks the default provider's model picker without a save", async () => {
    answers({ "qwen3.5:4b": true, "llama3.1:latest": false });
    const view = (settings: Partial<Settings>) => (
      <ServerProviderSection
        settings={settings as Settings}
        onUpdate={vi.fn()}
        onPersist={vi.fn()}
        models={models}
      />
    );
    const { rerender } = render(view(OLLAMA));
    await screen.findByText("This model supports thinking.");

    rerender(view({ ...OLLAMA, ollama_model: "llama3.1:latest" }));

    expect(await screen.findByText(/doesn't support thinking/)).toBeTruthy();
    expect(switchControl().disabled).toBe(true);
  });

  it("is absent for a hosted provider", () => {
    render(
      <ServerProviderSection
        settings={{ llm_provider: "openai", openai_api_key: "k" } as Settings}
        onUpdate={vi.fn()}
        onPersist={vi.fn()}
        models={models}
      />,
    );

    expect(screen.queryByRole("switch", { name: "Thinking" })).toBeNull();
    expect(ask).not.toHaveBeenCalled();
  });

  it("gives the fallback its own switch, saved under its own key and asked of its own server", async () => {
    answers({ "llama3.1:latest": true });
    const onPersist = vi.fn();
    const settings: Partial<Settings> = {
      secondary_llm_provider: "ollama",
      secondary_ollama_api_url: "http://ollama:11434",
      secondary_ollama_model: "llama3.1:latest",
      ollama_think: true,
    };
    render(
      <SecondaryProviderSection
        settings={settings as Settings}
        onUpdate={vi.fn()}
        onPersist={onPersist}
        models={models}
      />,
    );
    await screen.findByText("This model supports thinking.");
    expect(ask).toHaveBeenCalledWith("llama3.1:latest", true);
    // The default provider's choice is not the fallback's.
    expect(switchControl().getAttribute("aria-checked")).toBe("false");

    fireEvent.click(switchControl());

    expect(onPersist).toHaveBeenCalledWith({
      ...settings,
      secondary_ollama_think: true,
    });
  });
});
