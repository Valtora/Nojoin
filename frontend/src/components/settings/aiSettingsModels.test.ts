import { describe, expect, it } from "vitest";

import type { Settings } from "@/types";
import {
  DEFAULT_OLLAMA_CONTEXT_WINDOW,
  checkLlmConfigured,
  getModelOptionsForProvider,
  getSecondaryProviderApiKey,
  getSecondaryProviderLiveModel,
  getSecondaryProviderModel,
  getSelectedModelForProvider,
  parseContextWindow,
  withSecondaryProviderLiveModel,
  withSecondaryProviderModel,
  withSelectedModelForProvider,
} from "./aiSettingsModels";

const base = (overrides: Partial<Settings> = {}): Settings =>
  ({ ...overrides }) as Settings;

describe("aiSettingsModels", () => {
  describe("checkLlmConfigured", () => {
    it("defaults to gemini and checks its key", () => {
      expect(checkLlmConfigured(base())).toBe(false);
      expect(checkLlmConfigured(base({ gemini_api_key: "k" }))).toBe(true);
    });

    it("checks the credential for the active provider", () => {
      expect(
        checkLlmConfigured(base({ llm_provider: "openai", openai_api_key: "k" })),
      ).toBe(true);
      expect(
        checkLlmConfigured(
          base({ llm_provider: "ollama", ollama_api_url: "http://x" }),
        ),
      ).toBe(true);
      expect(checkLlmConfigured(base({ llm_provider: "ollama" }))).toBe(false);
    });
  });

  describe("getSelectedModelForProvider", () => {
    it("reads the main and live model for the active provider", () => {
      const settings = base({
        llm_provider: "anthropic",
        anthropic_model: "claude-x",
        anthropic_live_model: "claude-live",
      });
      expect(getSelectedModelForProvider(settings, "main")).toBe("claude-x");
      expect(getSelectedModelForProvider(settings, "live")).toBe("claude-live");
    });

    it("falls back to an empty string when unset", () => {
      expect(getSelectedModelForProvider(base({ llm_provider: "openai" }), "main")).toBe(
        "",
      );
    });
  });

  describe("withSelectedModelForProvider", () => {
    it("updates the main model and clears live with null on empty", () => {
      const settings = base({ llm_provider: "openai" });
      expect(withSelectedModelForProvider(settings, "main", "gpt").openai_model).toBe(
        "gpt",
      );
      expect(
        withSelectedModelForProvider(settings, "live", "").openai_live_model,
      ).toBeNull();
      // original is not mutated
      expect(settings.openai_model).toBeUndefined();
    });
  });

  describe("parseContextWindow", () => {
    it("clamps to a minimum and falls back to the default on NaN", () => {
      expect(parseContextWindow("2048")).toBe(2048);
      expect(parseContextWindow("100")).toBe(1024);
      expect(parseContextWindow("abc")).toBe(DEFAULT_OLLAMA_CONTEXT_WINDOW);
    });
  });

  describe("secondary provider accessors", () => {
    const settings = base({
      secondary_openai_api_key: "sk",
      secondary_openai_model: "gpt-s",
      secondary_openai_live_model: "gpt-s-live",
    });

    it("reads keys and models per provider", () => {
      expect(getSecondaryProviderApiKey(settings, "openai")).toBe("sk");
      expect(getSecondaryProviderModel(settings, "openai")).toBe("gpt-s");
      expect(getSecondaryProviderLiveModel(settings, "openai")).toBe("gpt-s-live");
      expect(getSecondaryProviderApiKey(settings, "ollama")).toBe("");
    });

    it("returns null updates when no provider is given", () => {
      expect(withSecondaryProviderModel(settings, null, "x")).toBeNull();
      expect(withSecondaryProviderLiveModel(settings, undefined, "x")).toBeNull();
    });

    it("updates the secondary model and clears live with null on empty", () => {
      expect(
        withSecondaryProviderModel(settings, "anthropic", "claude")
          ?.secondary_anthropic_model,
      ).toBe("claude");
      expect(
        withSecondaryProviderLiveModel(settings, "anthropic", "")
          ?.secondary_anthropic_live_model,
      ).toBeNull();
    });
  });

  describe("getModelOptionsForProvider", () => {
    it("moves the selected model to the front without duplicating it", () => {
      const settings = base({ llm_provider: "openai", openai_model: "b" });
      expect(getModelOptionsForProvider(settings, ["a", "b", "c"], "main")).toEqual([
        "b",
        "a",
        "c",
      ]);
    });

    it("returns the list unchanged when nothing is selected", () => {
      const settings = base({ llm_provider: "openai" });
      expect(getModelOptionsForProvider(settings, ["a", "b"], "main")).toEqual([
        "a",
        "b",
      ]);
    });
  });
});
