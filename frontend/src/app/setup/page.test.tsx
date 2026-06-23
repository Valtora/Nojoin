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

async function advanceToStep2(provider: Record<string, unknown> = {}) {
  // Step 0 -> Step 1
  fireEvent.click(screen.getByText("I Accept & Continue"));

  const bootstrap = await screen.findByLabelText("Bootstrap Password");
  fireEvent.change(bootstrap, { target: { value: "first-run-pw" } });
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "admin" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "supersecret" },
  });
  fireEvent.change(screen.getByLabelText("Confirm Password"), {
    target: { value: "supersecret" },
  });

  getInitialConfig.mockResolvedValue({ llm_provider: "gemini", ...provider });

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

  it("shows the legal disclaimer first for an unauthenticated user", async () => {
    renderWithProviders(<SetupPage />);

    await waitFor(() => {
      expect(screen.getByText("Legal Disclaimer")).toBeInTheDocument();
    });
  });

  it("warns when FFmpeg is missing", async () => {
    checkFFmpeg.mockResolvedValue({
      ffmpeg: false,
      ffprobe: false,
      ffmpeg_path: null,
      ffprobe_path: null,
    });

    renderWithProviders(<SetupPage />);

    await waitFor(() => {
      expect(screen.getByText("FFmpeg not detected")).toBeInTheDocument();
    });
  });

  it("blocks account submission when passwords do not match", async () => {
    renderWithProviders(<SetupPage />);

    await waitFor(() => {
      expect(screen.getByText("Legal Disclaimer")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("I Accept & Continue"));

    const bootstrap = await screen.findByLabelText("Bootstrap Password");
    fireEvent.change(bootstrap, { target: { value: "pw" } });
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
    expect(getInitialConfig).not.toHaveBeenCalled();
  });

  it("validates the provider and lists models on reaching the LLM step", async () => {
    validateLLM.mockResolvedValue({ message: "Validation successful" });
    listModels.mockResolvedValue({ models: ["gemini-pro-latest"] });

    renderWithProviders(<SetupPage />);

    await waitFor(() => {
      expect(screen.getByText("Legal Disclaimer")).toBeInTheDocument();
    });
    await advanceToStep2({ gemini_api_key: "key-123" });

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

    await waitFor(() => {
      expect(screen.getByText("Legal Disclaimer")).toBeInTheDocument();
    });
    await advanceToStep2();

    await waitFor(() => {
      expect(
        screen.getByText("AI Provider Configuration Missing"),
      ).toBeInTheDocument();
    });
    expect(validateLLM).not.toHaveBeenCalled();
  });
});
