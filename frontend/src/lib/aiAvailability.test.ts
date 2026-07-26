import { describe, expect, it } from "vitest";

import type { CliOAuthStatus, Settings } from "@/types";
import {
  isServerProviderConfigured,
  resolveAiAvailability,
} from "./aiAvailability";

const base = (overrides: Partial<Settings> = {}): Settings =>
  ({ ...overrides }) as Settings;

const SERVER_READY: Partial<Settings> = {
  llm_provider: "gemini",
  gemini_api_key: "k",
  gemini_model: "gemini-x",
};

const cliStatus = (connected: boolean): CliOAuthStatus => ({
  providers: [
    { provider: "claude_code", connected: false, status: "not_connected" },
    {
      provider: "codex",
      connected,
      status: connected ? "active" : "not_connected",
    },
  ],
});

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

describe("resolveAiAvailability", () => {
  it("follows the server provider when the user is not on cli_oauth", () => {
    expect(resolveAiAvailability(base(SERVER_READY), null)).toEqual({
      available: true,
    });
    expect(resolveAiAvailability(base(), null)).toEqual({
      available: false,
      reason: "server_unconfigured",
    });
  });

  it("ignores a connected subscription when the user is on the server default", () => {
    expect(resolveAiAvailability(base(), cliStatus(true))).toEqual({
      available: false,
      reason: "server_unconfigured",
    });
  });

  it("enables a connected subscription with no server provider (issue #138)", () => {
    expect(
      resolveAiAvailability(
        base({ usage_model: "cli_oauth" }),
        cliStatus(true),
      ),
    ).toEqual({ available: true });
  });

  it("enables a disconnected subscription that can fall back to the server", () => {
    expect(
      resolveAiAvailability(
        base({ ...SERVER_READY, usage_model: "cli_oauth" }),
        cliStatus(false),
      ),
    ).toEqual({ available: true });
  });

  it("blocks only when neither the subscription nor the server can answer", () => {
    expect(
      resolveAiAvailability(
        base({ usage_model: "cli_oauth" }),
        cliStatus(false),
      ),
    ).toEqual({ available: false, reason: "subscription_disconnected" });
  });

  it("reports a missing subscription status as disconnected, not unconfigured", () => {
    expect(
      resolveAiAvailability(base({ usage_model: "cli_oauth" }), null),
    ).toEqual({ available: false, reason: "subscription_disconnected" });
  });

  it("leaves the legacy no-op usage models on the server path", () => {
    for (const usage_model of ["ollama", "byok"] as const) {
      expect(resolveAiAvailability(base({ usage_model }), null)).toEqual({
        available: false,
        reason: "server_unconfigured",
      });
      expect(
        resolveAiAvailability(base({ ...SERVER_READY, usage_model }), null),
      ).toEqual({ available: true });
    }
  });
});
