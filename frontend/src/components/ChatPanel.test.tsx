import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CliOAuthStatus, Settings } from "@/types";
import ChatPanel from "./ChatPanel";

const getSettings = vi.fn();
const getUserMe = vi.fn();
const getCliOAuthStatus = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "rec-1" }),
}));

vi.mock("@/lib/api", () => ({
  getSettings: () => getSettings(),
  getUserMe: () => getUserMe(),
  getChatHistory: () => Promise.resolve([]),
  clearChatHistory: () => Promise.resolve({ status: "success" }),
  streamChatMessage: () => new AbortController(),
  getTags: () => Promise.resolve([]),
}));

vi.mock("@/lib/api/cliOauth", () => ({
  getCliOAuthStatus: () => getCliOAuthStatus(),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const SERVER_READY: Partial<Settings> = {
  llm_provider: "gemini",
  gemini_api_key: "abc...wxyz",
  gemini_model: "gemini-x",
};

const cliStatus = (connected: boolean): CliOAuthStatus => ({
  providers: [
    {
      provider: "codex",
      connected,
      status: connected ? "active" : "not_connected",
    },
  ],
});

const DISABLED = /Chat is disabled/;
const SUBSCRIPTION_MESSAGE = /Your AI subscription is not connected/;
const API_KEY_MESSAGE = /configure an API key and select a model/;

const renderPanel = (settings: Partial<Settings>, isSuperuser = true) => {
  getSettings.mockResolvedValue(settings as Settings);
  getUserMe.mockResolvedValue({ is_superuser: isSuperuser });
  return render(<ChatPanel />);
};

describe("ChatPanel AI availability gate", () => {
  beforeEach(() => {
    // jsdom has no layout engine, so the auto-scroll effect needs a stub.
    Element.prototype.scrollIntoView = vi.fn();
    getSettings.mockReset();
    getUserMe.mockReset();
    getCliOAuthStatus.mockReset();
  });

  it("enables chat for a connected subscription with no server API key", async () => {
    getCliOAuthStatus.mockResolvedValue(cliStatus(true));
    renderPanel({ usage_model: "cli_oauth" });

    await waitFor(() => expect(getCliOAuthStatus).toHaveBeenCalled());
    expect(screen.queryByText(DISABLED)).not.toBeInTheDocument();
  });

  it("enables chat for a disconnected subscription that falls back to the server", async () => {
    getCliOAuthStatus.mockResolvedValue(cliStatus(false));
    renderPanel({ ...SERVER_READY, usage_model: "cli_oauth" });

    await waitFor(() => expect(getCliOAuthStatus).toHaveBeenCalled());
    expect(screen.queryByText(DISABLED)).not.toBeInTheDocument();
  });

  it("names the subscription when neither it nor the server can answer", async () => {
    getCliOAuthStatus.mockResolvedValue(cliStatus(false));
    renderPanel({ usage_model: "cli_oauth" });

    expect(await screen.findByText(SUBSCRIPTION_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText(API_KEY_MESSAGE)).not.toBeInTheDocument();
  });

  it("fails open when the subscription status cannot be fetched", async () => {
    getCliOAuthStatus.mockRejectedValue(new Error("boom"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderPanel({ usage_model: "cli_oauth" });

    await waitFor(() => expect(getCliOAuthStatus).toHaveBeenCalled());
    expect(screen.queryByText(DISABLED)).not.toBeInTheDocument();
  });

  it("still blocks an unconfigured server default and skips the status call", async () => {
    renderPanel({ llm_provider: "gemini" });

    expect(await screen.findByText(API_KEY_MESSAGE)).toBeInTheDocument();
    expect(getCliOAuthStatus).not.toHaveBeenCalled();
  });

  it("tells a non-admin to contact their administrator", async () => {
    renderPanel({ llm_provider: "gemini" }, false);

    expect(
      await screen.findByText(/contact your administrator/),
    ).toBeInTheDocument();
  });

  it("enables chat for a configured server default", async () => {
    renderPanel(SERVER_READY);

    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    expect(screen.queryByText(DISABLED)).not.toBeInTheDocument();
  });
});
