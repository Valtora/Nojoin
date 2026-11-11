import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelProbeResult } from "@/lib/api";
import type { Settings, SystemModelStatus } from "@/types";
import { TestConnection } from "./aiProviderControls";
import {
  useAISettingsModels,
  validationKey,
  type ValidationMessage,
} from "./useAISettingsModels";

const mocks = vi.hoisted(() => ({
  validateLLM: vi.fn(),
  listModels: vi.fn(),
  getModelsStatus: vi.fn(),
  getDownloadProgress: vi.fn(),
  prepareModels: vi.fn(),
  addNotification: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  ...mocks,
  getLanguageOptions: vi.fn().mockResolvedValue({}),
  deleteModel: vi.fn(),
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: mocks.addNotification }),
}));

const pass: ModelProbeResult = {
  model: "gemini-flash-latest",
  role: "main",
  ok: true,
  kind: "ok",
  message: "gemini-flash-latest answered.",
};
const missing: ModelProbeResult = {
  model: "gemini-old",
  role: "meeting_edge",
  ok: false,
  kind: "unknown_model",
  message: "Gemini does not serve gemini-old. Choose another model.",
};

const message = (
  overrides: Partial<ValidationMessage> = {},
): ValidationMessage => ({
  type: "error",
  msg: missing.message,
  provider: "gemini",
  key: validationKey("gemini", "primary"),
  results: [pass, missing],
  ...overrides,
});

describe("TestConnection's result (issue #991)", () => {
  it("lists each model's outcome in a live region", () => {
    render(
      <TestConnection
        id="t"
        provider="gemini"
        validating={null}
        message={message()}
        onTest={vi.fn()}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(
      "Default model passed: gemini-flash-latest answered.",
    );
    expect(status).toHaveTextContent(
      "Meeting Edge model failed: Gemini does not serve gemini-old.",
    );
  });

  it("shows a check that ran no model in the neutral tone (issue #1005)", () => {
    const reachable = "Gemini is reachable. Choose a model to test it.";
    const { rerender } = render(
      <TestConnection
        id="t"
        provider="gemini"
        validating={null}
        message={message({ type: "success", msg: reachable, results: [] })}
        onTest={vi.fn()}
      />,
    );

    const summary = screen.getByText(reachable);
    expect(summary).toHaveClass("text-contrast-helper");
    expect(summary).not.toHaveClass("text-status-success-fg");
    expect(screen.queryByRole("list", { name: "Models tested" })).toBeNull();

    // A run in which every model answered still reads as a pass.
    rerender(
      <TestConnection
        id="t"
        provider="gemini"
        validating={null}
        message={message({
          type: "success",
          msg: "Gemini works: gemini-flash-latest answered.",
          results: [pass],
        })}
        onTest={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Gemini works: gemini-flash-latest answered."),
    ).toHaveClass("text-status-success-fg");

    // A refused key has no rows either, and is still a failure.
    rerender(
      <TestConnection
        id="t"
        provider="gemini"
        validating={null}
        message={message({ msg: "Validation failed", results: [] })}
        onTest={vi.fn()}
      />,
    );
    expect(screen.getByText("Validation failed")).toHaveClass(
      "text-status-danger-fg",
    );
  });

  it("keeps the fallback's result off the default card when they share a provider", () => {
    render(
      <TestConnection
        id="t"
        provider="gemini"
        role="primary"
        validating={null}
        message={message({ key: validationKey("gemini", "secondary") })}
        onTest={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("reads as busy while its test runs, keeps focus, and ignores a second click", () => {
    const onTest = vi.fn();
    render(
      <TestConnection
        id="t"
        provider="gemini"
        validating={validationKey("gemini", "primary")}
        message={null}
        onTest={onTest}
      />,
    );

    const button = screen.getByRole("button", { name: /Testing/ });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
    button.focus();
    button.click();
    expect(onTest).not.toHaveBeenCalled();
    expect(button).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Running a short test on each selected model...",
    );
  });
});

describe("handleValidate (issue #991)", () => {
  const settings = {
    whisper_model_size: "base",
    llm_provider: "gemini",
    gemini_model: "gemini-flash-latest",
    gemini_live_model: "gemini-old",
    secondary_llm_provider: "gemini",
    secondary_gemini_model: "gemini-pro-latest",
    secondary_gemini_live_model: null,
  } as unknown as Settings;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getModelsStatus.mockResolvedValue({} as SystemModelStatus);
    mocks.getDownloadProgress.mockResolvedValue({
      status: "idle",
      in_progress: false,
      stage: null,
      progress: 0,
      message: "",
    });
    mocks.listModels.mockResolvedValue({ models: [] });
  });

  it("probes nothing until asked", () => {
    renderHook(() => useAISettingsModels({ settings }));

    expect(mocks.validateLLM).not.toHaveBeenCalled();
  });

  it("sends the default and Meeting Edge models, and reports a failure without throwing", async () => {
    mocks.validateLLM.mockResolvedValue({
      valid: false,
      message: missing.message,
      results: [pass, missing],
    });
    const { result } = renderHook(() => useAISettingsModels({ settings }));

    await act(async () => {
      await result.current.handleValidate("gemini");
    });

    expect(mocks.validateLLM).toHaveBeenCalledWith(
      "gemini",
      "",
      "",
      undefined,
      undefined,
      {
        providerRole: "primary",
        probeModels: [
          { model: "gemini-flash-latest", role: "main" },
          { model: "gemini-old", role: "meeting_edge" },
        ],
      },
    );
    expect(result.current.validationMsg).toMatchObject({
      type: "error",
      key: "primary:gemini",
      results: [pass, missing],
    });
  });

  it("tests the fallback with its own models", async () => {
    mocks.validateLLM.mockResolvedValue({ valid: true, results: [] });
    const { result } = renderHook(() => useAISettingsModels({ settings }));

    await act(async () => {
      await result.current.handleValidate("gemini", "secondary");
    });

    expect(mocks.validateLLM.mock.calls[0][5]).toEqual({
      providerRole: "secondary",
      probeModels: [{ model: "gemini-pro-latest", role: "main" }],
    });
    expect(result.current.validationMsg?.key).toBe("secondary:gemini");
    // The list refresh after the test reads the fallback's key too.
    expect(mocks.listModels).toHaveBeenLastCalledWith(
      "gemini",
      "",
      "",
      undefined,
      "secondary",
    );
  });

  it("keeps the per-model results when the list refresh fails", async () => {
    mocks.validateLLM.mockResolvedValue({
      valid: false,
      message: missing.message,
      results: [pass, missing],
    });
    const { result } = renderHook(() => useAISettingsModels({ settings }));
    await act(async () => {
      await Promise.resolve();
    });
    mocks.listModels.mockRejectedValue(new Error("400 from list-models"));

    await act(async () => {
      await result.current.handleValidate("gemini", "secondary");
    });

    expect(result.current.validationMsg).toMatchObject({
      type: "error",
      key: "secondary:gemini",
      results: [pass, missing],
    });
    render(
      <TestConnection
        id="t"
        provider="gemini"
        role="secondary"
        validating={null}
        message={result.current.validationMsg}
        onTest={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Meeting Edge model failed: Gemini does not serve gemini-old.",
    );
  });

  it("loads the fallback's models with the fallback's key", async () => {
    renderHook(() => useAISettingsModels({ settings }));

    await waitFor(
      () =>
        expect(mocks.listModels).toHaveBeenCalledWith(
          "gemini",
          "",
          "",
          undefined,
          "secondary",
        ),
      { timeout: 3000 },
    );
  });

  it("starts one test however often it is asked while one runs", async () => {
    let finish!: (value: unknown) => void;
    mocks.validateLLM.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { result } = renderHook(() => useAISettingsModels({ settings }));

    let first!: Promise<void>;
    act(() => {
      first = result.current.handleValidate("gemini");
      void result.current.handleValidate("gemini");
      void result.current.handleValidate("gemini", "secondary");
    });
    expect(result.current.validating).toBe("primary:gemini");
    await act(async () => {
      finish({ valid: true, results: [] });
      await first;
    });

    expect(mocks.validateLLM).toHaveBeenCalledTimes(1);
    expect(result.current.validating).toBeNull();
  });
});
