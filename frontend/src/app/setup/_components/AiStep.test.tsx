import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/settings/CliOAuthPanel", () => ({ default: () => null }));

import AiStep from "./AiStep";

const props = (overrides: Partial<ComponentProps<typeof AiStep>> = {}): ComponentProps<typeof AiStep> => ({
  aiRoute: "server",
  onRouteChange: vi.fn(),
  serverProvider: "ollama",
  serverProviderSelected: true,
  serverCredentialPresent: true,
  secondaryProvider: null,
  secondaryProviderConfigured: false,
  validatingLLM: false,
  llmValidationMsg: null,
  availableModels: [],
  selectedModel: "",
  reloadingConfig: false,
  savingAi: false,
  connectedCliProvider: undefined,
  onCliStatusChange: vi.fn(),
  onInputChange: vi.fn(),
  onReloadConfig: vi.fn(),
  onSubmit: vi.fn(),
  ...overrides,
});

describe("the AI step's server route (issue #841)", () => {
  it("calls Ollama's address a server URL, not a credential", () => {
    render(<AiStep {...props()} />);

    expect(screen.getByText(
      "Uses the Ollama server URL found in the server's .env, shared by every account.",
    )).toBeInTheDocument();
    expect(screen.queryByText(/credential/)).not.toBeInTheDocument();
  });

  it("says Ollama's URL is missing when the .env selects Ollama without one", () => {
    render(<AiStep {...props({ serverCredentialPresent: false })} />);

    expect(screen.getByText("No Ollama server URL was found in the server's .env.")).toBeInTheDocument();
    expect(screen.getByText("No Ollama server URL in the server environment")).toBeInTheDocument();
    expect(screen.getByText("The server's .env selects Ollama but carries no server URL for it."))
      .toBeInTheDocument();
  });

  it("asks to check Ollama's URL when it cannot be reached", () => {
    render(<AiStep {...props({ llmValidationMsg: { valid: false, msg: "Connection refused" } })} />);

    expect(screen.getByText(/Check the server URL in the server's/)).toBeInTheDocument();
  });

  it("keeps calling an API key a credential", () => {
    render(<AiStep {...props({ serverProvider: "anthropic" })} />);

    expect(screen.getByText(
      "Uses the Anthropic credential found in the server's .env, shared by every account.",
    )).toBeInTheDocument();
  });
});
