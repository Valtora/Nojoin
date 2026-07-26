import { describe, expect, it } from "vitest";

import type { Settings } from "@/types";
import { isServerProviderConfigured } from "./aiAvailability";

const base = (overrides: Partial<Settings> = {}): Settings =>
  ({ ...overrides }) as Settings;

describe("isServerProviderConfigured", () => {
  it("defaults to gemini when no provider is set", () => {
    expect(isServerProviderConfigured(base())).toBe(false);
    expect(
      isServerProviderConfigured(
        base({ gemini_api_key: "k", gemini_model: "gemini-x" }),
      ),
    ).toBe(true);
  });

  it("checks the credential for the active provider", () => {
    expect(
      isServerProviderConfigured(
        base({
          llm_provider: "openai",
          openai_api_key: "k",
          openai_model: "gpt-x",
        }),
      ),
    ).toBe(true);
    expect(
      isServerProviderConfigured(
        base({ llm_provider: "openai", openai_model: "gpt-x" }),
      ),
    ).toBe(false);
    expect(
      isServerProviderConfigured(
        base({
          llm_provider: "anthropic",
          anthropic_api_key: "k",
          anthropic_model: "claude-x",
        }),
      ),
    ).toBe(true);
  });

  it("requires a model as well as a credential", () => {
    expect(
      isServerProviderConfigured(base({ gemini_api_key: "k" })),
    ).toBe(false);
  });

  it("requires both a URL and a model for ollama", () => {
    expect(
      isServerProviderConfigured(
        base({
          llm_provider: "ollama",
          ollama_api_url: "http://x",
          ollama_model: "llama3",
        }),
      ),
    ).toBe(true);
    // A URL alone is not usable: the request still has to name a model.
    expect(
      isServerProviderConfigured(
        base({ llm_provider: "ollama", ollama_api_url: "http://x" }),
      ),
    ).toBe(false);
    expect(isServerProviderConfigured(base({ llm_provider: "ollama" }))).toBe(
      false,
    );
  });

  it("treats an unrecognised provider as unconfigured", () => {
    expect(
      isServerProviderConfigured(
        base({ llm_provider: "mystery", gemini_api_key: "k" }),
      ),
    ).toBe(false);
  });
});
