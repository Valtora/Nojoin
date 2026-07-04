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

async function unlockWizard() {
  const unlockInput = await screen.findByLabelText("First-run setup password");
  fireEvent.change(unlockInput, { target: { value: "first-run-pw" } });
  fireEvent.click(screen.getByText("Unlock Setup"));

  await waitFor(() => {
    expect(screen.getByText("Legal Disclaimer")).toBeInTheDocument();
  });
}

async function advanceToAccountStep(provider: Record<string, unknown> = {}) {
  getInitialConfig.mockResolvedValue({ llm_provider: "gemini", ...provider });

  await unlockWizard();

  // Legal -> Account
  fireEvent.click(screen.getByText("I Accept & Continue"));
  await screen.findByLabelText("Username");
}

async function advanceToLlmStep(provider: Record<string, unknown> = {}) {
  await advanceToAccountStep(provider);

  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "admin" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "supersecret" },
  });
  fireEvent.change(screen.getByLabelText("Confirm Password"), {
    target: { value: "supersecret" },
  });

  fireEvent.click(screen.getByText(/Next Step/));
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
    expect(
      screen.getByLabelText("First-run setup password"),
    ).toBeInTheDocument();
    // Nothing beyond the gate renders before a successful unlock.
    expect(screen.queryByText("Legal Disclaimer")).not.toBeInTheDocument();
    expect(getInitialConfig).not.toHaveBeenCalled();
  });

  it("shows a generic denial when the unlock password is rejected", async () => {
    getInitialConfig.mockRejectedValue(makeForbidden());

    renderWithProviders(<SetupPage />);

    const unlockInput = await screen.findByLabelText(
      "First-run setup password",
    );
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
    getInitialConfig.mockResolvedValue({ llm_provider: "gemini" });

    renderWithProviders(<SetupPage />);

    await unlockWizard();

    expect(getInitialConfig).toHaveBeenCalledWith("first-run-pw");
  });

  it("blocks account submission when passwords do not match", async () => {
    renderWithProviders(<SetupPage />);

    await advanceToAccountStep();

    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "supersecret" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "different" },
    });
    fireEvent.click(screen.getByText(/Next Step/));

    await waitFor(() => {
      expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    });
    expect(setupSystem).not.toHaveBeenCalled();
  });

  it("validates the provider and lists models on reaching the LLM step", async () => {
    validateLLM.mockResolvedValue({ message: "Validation successful" });
    listModels.mockResolvedValue({ models: ["gemini-pro-latest"] });

    renderWithProviders(<SetupPage />);

    await advanceToLlmStep({ gemini_api_key: "key-123" });

    await waitFor(() => {
      expect(validateLLM).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByText("Select Model")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("option", { name: "gemini-pro-latest" }),
    ).toBeInTheDocument();
  });

  it("shows the missing-provider state when no key is configured", async () => {
    renderWithProviders(<SetupPage />);

    await advanceToLlmStep();

    await waitFor(() => {
      expect(
        screen.getByText("AI Provider Configuration Missing"),
      ).toBeInTheDocument();
    });
    expect(validateLLM).not.toHaveBeenCalled();
  });

  it("completes setup with the chosen transcription model and demo opt-out", async () => {
    validateLLM.mockResolvedValue({ message: "Validation successful" });
    listModels.mockResolvedValue({ models: ["gemini-pro-latest"] });
    setupSystem.mockResolvedValue({ initialized: true });
    login.mockResolvedValue({});
    checkFFmpeg.mockResolvedValue({
      ffmpeg: false,
      ffprobe: false,
      ffmpeg_path: null,
      ffprobe_path: null,
    });
    getDownloadProgress.mockResolvedValue({
      status: "complete",
      progress: 100,
      message: "Models ready",
      stage: "complete",
    });

    renderWithProviders(<SetupPage />);

    await advanceToAccountStep({ gemini_api_key: "key-123" });

    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "supersecret" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "supersecret" },
    });
    fireEvent.click(screen.getByRole("checkbox"));

    fireEvent.click(screen.getByText(/Next Step/));

    await waitFor(() => {
      expect(screen.getByText("Select Model")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText(/Next Step/));

    const whisperSelect = await screen.findByLabelText("Transcription model");
    fireEvent.change(whisperSelect, { target: { value: "small" } });
    fireEvent.click(screen.getByText(/Finish Setup/));

    await waitFor(() => {
      expect(setupSystem).toHaveBeenCalledWith(
        {
          username: "admin",
          password: "supersecret",
          selected_model: "gemini-pro-latest",
          whisper_model_size: "small",
          include_demo_recording: false,
        },
        "first-run-pw",
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Setup Complete")).toBeInTheDocument();
    });
    expect(screen.getByText("FFmpeg not detected")).toBeInTheDocument();
  });
});
