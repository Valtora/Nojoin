import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  UserRole,
  type ChatMessage,
  type CliOAuthStatus,
  type Settings,
  type User,
} from "@/types";
import ChatPanel from "./ChatPanel";

const getSettings = vi.fn();
const getUserMe = vi.fn();
const getCliOAuthStatus = vi.fn();
const getChatHistory = vi.fn((): Promise<ChatMessage[]> => Promise.resolve([]));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "rec-1" }),
}));

vi.mock("@/lib/api", () => ({
  getSettings: () => getSettings(),
  getUserMe: () => getUserMe(),
  getChatHistory: () => getChatHistory(),
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

const SETUP_ACCOUNT: Pick<User, "is_superuser" | "role"> = {
  is_superuser: true,
  role: UserRole.OWNER,
};

const renderPanel = (
  settings: Partial<Settings>,
  user: Pick<User, "is_superuser" | "role"> = SETUP_ACCOUNT,
) => {
  getSettings.mockResolvedValue(settings as Settings);
  getUserMe.mockResolvedValue(user);
  return render(<ChatPanel />);
};

describe("ChatPanel AI availability gate", () => {
  beforeEach(() => {
    // jsdom has no layout engine, so the auto-scroll effect needs a stub.
    Element.prototype.scrollTo = vi.fn();
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
    renderPanel(
      { llm_provider: "gemini" },
      { is_superuser: false, role: UserRole.USER },
    );

    expect(
      await screen.findByText(/contact your administrator/),
    ).toBeInTheDocument();
  });

  it.each([UserRole.OWNER, UserRole.ADMIN])(
    "sends an %s who is not the setup account to Settings",
    async (role) => {
      // Any administrator can choose the model in Settings > AI providers.
      renderPanel({ llm_provider: "gemini" }, { is_superuser: false, role });

      expect(await screen.findByText(API_KEY_MESSAGE)).toBeInTheDocument();
    },
  );

  it("enables chat for a configured server default", async () => {
    renderPanel(SERVER_READY);

    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    expect(screen.queryByText(DISABLED)).not.toBeInTheDocument();
  });
});

describe("ChatPanel empty state", () => {
  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn();
    getSettings.mockReset();
    getUserMe.mockReset();
    getCliOAuthStatus.mockReset();
  });

  // The helper text token is pitched to clear 4.5:1 on the card on its own.
  // Any opacity on the way up composites it below that (opacity-60 measured
  // 2.79:1), and the contrast gate cannot see opacity, so assert it here.
  it("does not dim the guidance text with opacity", async () => {
    const { container } = renderPanel(SERVER_READY);

    const guidance = await screen.findByText(/Ask questions about the transcript/);
    for (let node: Element | null = guidance; node && node !== container; node = node.parentElement) {
      expect(node.className).not.toMatch(/(^|\s)opacity-/);
    }
  });
});

describe("ChatPanel auto-scroll", () => {
  const message = (id: number, role: ChatMessage["role"], content: string): ChatMessage => ({
    id,
    recording_id: "rec-1",
    user_id: 1,
    role,
    content,
    created_at: "2026-05-25T11:54:00Z",
    updated_at: "2026-05-25T11:54:00Z",
  });

  beforeEach(() => {
    getSettings.mockReset();
    getUserMe.mockReset();
    getCliOAuthStatus.mockReset();
    getChatHistory.mockResolvedValue([
      message(1, "user", "What did Alice Example agree to?"),
      message(2, "assistant", "She agreed to send the draft by Friday."),
    ]);
  });

  // scrollIntoView scrolls every ancestor with overflow as well, so when the
  // app shell had a few hundred stray pixels of it, opening a recording slid
  // every column but the nav off screen. Only the message list may move.
  it("scrolls the message list itself and never calls scrollIntoView", async () => {
    const scrollTo = vi.fn();
    const scrollIntoView = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    Element.prototype.scrollIntoView = scrollIntoView;

    renderPanel(SERVER_READY);
    await screen.findByText("She agreed to send the draft by Friday.");

    const list = screen.getByTestId("chat-messages");
    await waitFor(() => expect(scrollTo.mock.contexts).toContain(list));
    expect(scrollTo.mock.contexts.every((context) => context === list)).toBe(true);
    expect(scrollTo).toHaveBeenLastCalledWith({ top: list.scrollHeight, behavior: "smooth" });
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
