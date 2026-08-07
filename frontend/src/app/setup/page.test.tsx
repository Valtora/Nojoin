import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
} from "@/test/renderWithProviders";

const routerPush = vi.fn();

const getCurrentUser = vi.fn();
const checkFFmpeg = vi.fn();
const getInitialConfig = vi.fn();
const validateLLM = vi.fn();
const listModels = vi.fn();
const setupSystem = vi.fn();
const login = vi.fn();
const getDownloadProgress = vi.fn();
const getSettings = vi.fn();
const updateSettings = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock("@/lib/api", () => ({
  getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  checkFFmpeg: (...args: unknown[]) => checkFFmpeg(...args),
  getInitialConfig: (...args: unknown[]) => getInitialConfig(...args),
  validateLLM: (...args: unknown[]) => validateLLM(...args),
  listModels: (...args: unknown[]) => listModels(...args),
  setupSystem: (...args: unknown[]) => setupSystem(...args),
  login: (...args: unknown[]) => login(...args),
  getDownloadProgress: (...args: unknown[]) => getDownloadProgress(...args),
  getSettings: (...args: unknown[]) => getSettings(...args),
  updateSettings: (...args: unknown[]) => updateSettings(...args),
}));

// The connect panel owns its own network calls and OAuth modals; the wizard's
// contract with it is only "report a status up", which the stub exercises.
const cliConnect = vi.fn();
vi.mock("@/components/settings/CliOAuthPanel", () => ({
  default: ({
    onStatusChange,
  }: {
    onStatusChange?: (status: unknown) => void;
  }) => (
    <button
      type="button"
      onClick={() => {
        cliConnect();
        onStatusChange?.({
          providers: [{ provider: "claude_code", connected: true, status: "active" }],
        });
      }}
    >
      Stub connect Claude
    </button>
  ),
}));

import SetupPage from "./page";

function makeUnauthorised() {
  return Object.assign(new Error("unauthorised"), {
    response: { status: 401 },
  });
}

function makeForbidden() {
  return Object.assign(new Error("forbidden"), {
    response: {
      status: 403,
      data: { detail: "First-run setup access denied." },
    },
  });
}

const CONFIGURED_GEMINI = {
  llm_provider: "gemini",
  llm_provider_selected: true,
  gemini_api_key: "AIz...1234",
};

const NO_PROVIDER = {
  llm_provider: "gemini",
  llm_provider_selected: false,
};

async function unlockWizard() {
  const unlockInput = await screen.findByLabelText("First-run setup password");
  fireEvent.change(unlockInput, { target: { value: "first-run-pw" } });
  fireEvent.click(screen.getByText("Unlock Setup"));

  await waitFor(() => {
    expect(screen.getByText("Legal Disclaimer")).toBeInTheDocument();
  });
}

/** Unlock -> legal -> transcription. */
async function advanceToTranscription(
  config: Record<string, unknown> = CONFIGURED_GEMINI,
) {
  getInitialConfig.mockResolvedValue(config);
  await unlockWizard();
  fireEvent.click(screen.getByText("I Accept & Continue"));
  await screen.findByLabelText("Transcription model");
}

/** ... -> account step. */
async function advanceToAccount(
  config: Record<string, unknown> = CONFIGURED_GEMINI,
) {
  await advanceToTranscription(config);
  fireEvent.click(screen.getByText("Next Step"));
  await screen.findByLabelText("Username");
}

function fillAccount(password = "supersecret") {
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "admin" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: password },
  });
  fireEvent.change(screen.getByLabelText("Confirm Password"), {
    target: { value: password },
  });
}

/** ... -> AI step, with the account created. */
async function advanceToAi(config: Record<string, unknown> = CONFIGURED_GEMINI) {
  await advanceToAccount(config);
  fillAccount();
  fireEvent.click(screen.getByText("Create account"));
  await screen.findByText("AI Configuration");
}

describe("SetupPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCurrentUser.mockRejectedValue(makeUnauthorised());
    checkFFmpeg.mockResolvedValue({
      ffmpeg: true,
      ffprobe: true,
      ffmpeg_path: null,
      ffprobe_path: null,
    });
    setupSystem.mockResolvedValue({ initialized: true });
    login.mockResolvedValue({});
    getSettings.mockResolvedValue({ llm_provider: "gemini" });
    updateSettings.mockResolvedValue({});
    getDownloadProgress.mockResolvedValue({
      status: "complete",
      progress: 100,
      message: "Models ready",
      stage: "complete",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("redirects an already-authenticated user away from setup", async () => {
    getCurrentUser.mockResolvedValue({ force_password_change: false });

    renderWithProviders(<SetupPage />);

    await waitFor(() => {
      expect(routerPush).toHaveBeenCalledWith("/");
    });
  });

  it("shows the unlock gate first for an unauthenticated user", async () => {
    renderWithProviders(<SetupPage />);

    await waitFor(() => {
      expect(screen.getByText("First-Run Setup")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("First-run setup password")).toBeInTheDocument();
    // Nothing beyond the gate renders before a successful unlock.
    expect(screen.queryByText("Legal Disclaimer")).not.toBeInTheDocument();
    expect(getInitialConfig).not.toHaveBeenCalled();
  });

  it("shows a generic denial when the unlock password is rejected", async () => {
    getInitialConfig.mockRejectedValue(makeForbidden());

    renderWithProviders(<SetupPage />);

    const unlockInput = await screen.findByLabelText("First-run setup password");
    fireEvent.change(unlockInput, { target: { value: "wrong-guess" } });
    fireEvent.click(screen.getByText("Unlock Setup"));

    await waitFor(() => {
      expect(
        screen.getByText(
          "Setup access denied. Check the first-run password, or sign in normally if this system is already set up.",
        ),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText("Legal Disclaimer")).not.toBeInTheDocument();
  });

  it("advances to the legal step after a successful unlock", async () => {
    getInitialConfig.mockResolvedValue(CONFIGURED_GEMINI);

    renderWithProviders(<SetupPage />);
    await unlockWizard();

    expect(getInitialConfig).toHaveBeenCalledWith("first-run-pw");
  });

  it("blocks account creation when passwords do not match", async () => {
    renderWithProviders(<SetupPage />);
    await advanceToAccount();

    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "supersecret" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "different" },
    });
    fireEvent.click(screen.getByText("Create account"));

    await waitFor(() => {
      expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    });
    expect(setupSystem).not.toHaveBeenCalled();
  });

  it("rejects an all-whitespace password the server would also reject", async () => {
    renderWithProviders(<SetupPage />);
    await advanceToAccount();

    fillAccount("        ");
    fireEvent.click(screen.getByText("Create account"));

    await waitFor(() => {
      expect(
        screen.getByText("Password cannot be all whitespace."),
      ).toBeInTheDocument();
    });
    expect(setupSystem).not.toHaveBeenCalled();
  });

  it("creates the account with the chosen transcription model before the AI step", async () => {
    validateLLM.mockResolvedValue({ message: "Validation successful" });
    listModels.mockResolvedValue({ models: ["gemini-pro-latest"] });

    renderWithProviders(<SetupPage />);
    await advanceToTranscription();

    fireEvent.change(screen.getByLabelText("Transcription model"), {
      target: { value: "small" },
    });
    fireEvent.click(screen.getByText("Next Step"));

    await screen.findByLabelText("Username");
    fillAccount();
    // Opt out of the sample meeting.
    fireEvent.click(screen.getByLabelText(/Include a sample meeting/));
    fireEvent.click(screen.getByText("Create account"));

    await waitFor(() => {
      expect(setupSystem).toHaveBeenCalledWith(
        {
          username: "admin",
          password: "supersecret",
          whisper_model_size: "small",
          include_demo_recording: false,
          // Opt-out: the wizard checkbox defaults to ticked, and that tick is
          // the install's telemetry consent.
          enable_telemetry: true,
        },
        "first-run-pw",
      );
    });
    expect(login).toHaveBeenCalledWith("admin", "supersecret");
  });

  it("validates the server provider and persists the chosen model", async () => {
    validateLLM.mockResolvedValue({ message: "Validation successful" });
    listModels.mockResolvedValue({ models: ["gemini-pro-latest", "gemini-flash"] });

    renderWithProviders(<SetupPage />);
    await advanceToAi();

    await waitFor(() => {
      expect(validateLLM).toHaveBeenCalled();
    });
    const modelSelect = await screen.findByLabelText("Model");
    fireEvent.change(modelSelect, { target: { value: "gemini-flash" } });

    fireEvent.click(screen.getByText("Finish setup"));

    await waitFor(() => {
      expect(updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({ gemini_model: "gemini-flash" }),
      );
    });
    await screen.findByText("Setup Complete");
  });

  it("offers the subscription route without naming a provider nobody chose", async () => {
    renderWithProviders(<SetupPage />);
    await advanceToAi(NO_PROVIDER);

    await waitFor(() => {
      expect(
        screen.getByText("No AI provider is configured on this server"),
      ).toBeInTheDocument();
    });
    // The server default resolves to gemini, but the operator never picked it,
    // so the wizard must not report a missing gemini key.
    expect(
      screen.queryByText(/No Google Gemini credential/),
    ).not.toBeInTheDocument();
    expect(validateLLM).not.toHaveBeenCalled();
    expect(
      screen.getByText("My own Claude or ChatGPT subscription"),
    ).toBeInTheDocument();
  });

  it("routes AI through a connected subscription", async () => {
    renderWithProviders(<SetupPage />);
    await advanceToAi(NO_PROVIDER);

    fireEvent.click(screen.getByText("My own Claude or ChatGPT subscription"));
    fireEvent.click(await screen.findByText("Stub connect Claude"));

    await screen.findByText(/Claude connected\./);
    fireEvent.click(screen.getByText("Finish setup"));

    await waitFor(() => {
      expect(updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          usage_model: "cli_oauth",
          cli_provider: "claude_code",
        }),
      );
    });
    await screen.findByText("Setup Complete");
  });

  it("finishes with no AI configured at all", async () => {
    renderWithProviders(<SetupPage />);
    await advanceToAi(NO_PROVIDER);

    fireEvent.click(screen.getByText("Decide later"));
    fireEvent.click(screen.getByText("Finish setup"));

    await screen.findByText("Setup Complete");
    expect(updateSettings).not.toHaveBeenCalled();
    expect(screen.getByText("Not configured yet")).toBeInTheDocument();
  });

  it("refetches the server config on the first 'check config again' click", async () => {
    renderWithProviders(<SetupPage />);
    await advanceToAi(NO_PROVIDER);

    const callsAfterUnlock = getInitialConfig.mock.calls.length;
    fireEvent.click(screen.getByText("Check config again"));

    // Regression: this used to no-op on the first click, because the guard it
    // consulted was captured from the previous render.
    await waitFor(() => {
      expect(getInitialConfig.mock.calls.length).toBe(callsAfterUnlock + 1);
    });
    // Authenticated by now, so the bootstrap password is no longer sent.
    expect(getInitialConfig).toHaveBeenLastCalledWith(undefined);
  });

  it("carries a telemetry opt-out from the legal step through to setup", async () => {
    getInitialConfig.mockResolvedValue(CONFIGURED_GEMINI);
    validateLLM.mockResolvedValue({ message: "Validation successful" });
    listModels.mockResolvedValue({ models: ["gemini-pro-latest"] });

    renderWithProviders(<SetupPage />);
    await unlockWizard();

    const telemetryCheckbox = screen.getByLabelText(/Share anonymous usage data/);
    expect(telemetryCheckbox).toBeChecked();
    fireEvent.click(telemetryCheckbox);

    fireEvent.click(screen.getByText("I Accept & Continue"));
    await screen.findByLabelText("Transcription model");
    fireEvent.click(screen.getByText("Next Step"));

    await screen.findByLabelText("Username");
    fillAccount();
    fireEvent.click(screen.getByText("Create account"));

    await waitFor(() => {
      expect(setupSystem).toHaveBeenCalledWith(
        expect.objectContaining({ enable_telemetry: false }),
        "first-run-pw",
      );
    });
  });

  it("does not carry an error banner into a later step", async () => {
    renderWithProviders(<SetupPage />);
    await advanceToAccount();

    fillAccount("short");
    fireEvent.click(screen.getByText("Create account"));
    await screen.findByText("Password must be at least 8 characters");

    fireEvent.click(screen.getByText("Back"));
    await screen.findByLabelText("Transcription model");

    expect(
      screen.queryByText("Password must be at least 8 characters"),
    ).not.toBeInTheDocument();
  });
});
