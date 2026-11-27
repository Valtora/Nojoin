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
  testingModel: false,
  modelUnderTest: null,
  modelTest: null,
  onTestModel: vi.fn(),
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

describe("the AI step's Test connection (issue #991)", () => {
  const ready = {
    llmValidationMsg: { valid: true, msg: "Connected to Ollama successfully." },
    availableModels: ["good-model", "missing-model"],
    selectedModel: "good-model",
  };

  it("runs the chosen model only when the button is pressed", () => {
    const onTestModel = vi.fn();
    render(<AiStep {...props({ ...ready, onTestModel })} />);

    expect(onTestModel).not.toHaveBeenCalled();
    screen.getByRole("button", { name: "Test connection" }).click();
    expect(onTestModel).toHaveBeenCalledTimes(1);
  });

  it("shows a pass that names the model in a live region", () => {
    render(
      <AiStep
        {...props({
          ...ready,
          modelTest: {
            valid: true,
            msg: "Ollama works: good-model answered.",
            results: [
              { model: "good-model", role: "main", ok: true, kind: "ok", message: "good-model answered." },
            ],
          },
        })}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Ollama works: good-model answered.");
    expect(status).toHaveTextContent("Default model passed: good-model answered.");
  });

  it("shows a failure beside its model and still lets setup finish", () => {
    render(
      <AiStep
        {...props({
          ...ready,
          selectedModel: "missing-model",
          modelTest: {
            valid: false,
            msg: "missing-model is not on the Ollama server. Pull it, or choose another model.",
            results: [
              {
                model: "missing-model",
                role: "main",
                ok: false,
                kind: "unknown_model",
                message: "missing-model is not on the Ollama server. Pull it, or choose another model.",
              },
            ],
          },
        })}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Default model failed: missing-model is not on the Ollama server.",
    );
    expect(screen.getByRole("button", { name: /Finish setup/ })).not.toBeDisabled();
  });

  it("reads as busy while a test runs, and a click starts nothing", () => {
    const onTestModel = vi.fn();
    render(<AiStep {...props({ ...ready, testingModel: true, onTestModel })} />);

    const button = screen.getByRole("button", { name: /Testing/ });
    expect(button).toHaveAttribute("aria-busy", "true");
    button.click();
    expect(onTestModel).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Running a short test on good-model...");
  });

  it("holds the model select while a test runs, and names the model tested", () => {
    render(
      <AiStep
        {...props({
          ...ready,
          selectedModel: "missing-model",
          testingModel: true,
          modelUnderTest: "good-model",
        })}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Model" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Running a short test on good-model...",
    );
  });
});
