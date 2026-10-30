import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  UserRole,
  type ChatMessage,
  type CliOAuthStatus,
  type Settings,
  type User,
} from "@/types";
import ChatPanel from "./ChatPanel";
import { fillMarkerViolations } from "@/test/fillMarkerContracts";

const getSettings = vi.fn();
const getUserMe = vi.fn();
const getCliOAuthStatus = vi.fn();
const addNotification = vi.fn();
let currentRecordingId = "rec-1";
const getChatHistory = vi.fn<() => Promise<unknown[]>>().mockResolvedValue([]);
const streamChatMessage = vi.fn<(...args: unknown[]) => AbortController>(
  () => new AbortController(),
);

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: currentRecordingId }),
}));

vi.mock("@/lib/api", () => ({
  getSettings: () => getSettings(),
  getUserMe: () => getUserMe(),
  getChatHistory: () => getChatHistory(),
  clearChatHistory: () => Promise.resolve({ status: "success" }),
  streamChatMessage: (...args: unknown[]) => streamChatMessage(...args),
  getTags: () => Promise.resolve([]),
}));

vi.mock("@/lib/api/cliOauth", () => ({
  getCliOAuthStatus: () => getCliOAuthStatus(),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

beforeEach(() => {
  currentRecordingId = "rec-1";
  addNotification.mockClear();
});

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

describe("ChatPanel provisional context", () => {
  beforeEach(() => {
    getSettings.mockResolvedValue(SERVER_READY);
    getUserMe.mockResolvedValue(SETUP_ACCOUNT);
    getChatHistory.mockResolvedValue([]);
    streamChatMessage.mockClear();
  });

  it("says answers use the transcript so far only while the recording is in flight", async () => {
    const view = render(<ChatPanel inFlight />);
    expect(await screen.findByText(/Answers use the transcript so far/i)).toBeInTheDocument();
    view.rerender(<ChatPanel inFlight={false} />);
    expect(screen.queryByText(/Answers use the transcript so far/i)).not.toBeInTheDocument();
  });

  it("waits for nonempty initial history before sending and preserves the typed question", async () => {
    let resolveHistory!: (messages: ChatMessage[]) => void;
    getChatHistory.mockImplementationOnce(() => new Promise((resolve) => { resolveHistory = resolve; }));
    render(<ChatPanel inFlight />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText("Ask a question..."), { target: { value: "Now?" } });
    fireEvent.keyDown(screen.getByPlaceholderText("Ask a question..."), { key: "Enter" });
    expect(streamChatMessage).not.toHaveBeenCalled();
    expect(screen.getByText("Loading chat history...")).toBeInTheDocument();
    await act(async () => { resolveHistory([{ id: 12, recording_id: "rec-1", user_id: 1,
      role: "assistant", content: "Earlier answer.", created_at: "2026-10-27T12:00:00Z", updated_at: "2026-10-27T12:00:00Z" }]); });
    expect(screen.getByText("Earlier answer.")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Ask a question...")).toHaveValue("Now?");
    fireEvent.keyDown(screen.getByPlaceholderText("Ask a question..."), { key: "Enter" });
    act(() => (streamChatMessage.mock.calls[0][2] as (token: string) => void)("Current answer."));
    expect(screen.getByText("Current answer.")).toBeInTheDocument();
    expect(screen.getByText("Earlier answer.")).toBeInTheDocument();
  });

  it("names its Send button (issue #840)", async () => {
    render(<ChatPanel inFlight />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Send message" })).toBeInTheDocument();
  });

});

describe("ChatPanel stream lifecycle", () => {
  type StreamArguments = Parameters<typeof import("@/lib/api").streamChatMessage>;
  const callbacks = (index = 0) => streamChatMessage.mock.calls[index] as StreamArguments;
  const send = (text: string) => {
    const input = screen.getByPlaceholderText("Ask a question...");
    fireEvent.change(input, { target: { value: text } });
    fireEvent.keyDown(input, { key: "Enter" });
  };
  const lateEvents = (request: StreamArguments) => act(() => {
    request[2]("Stale answer.");
    request[6]?.();
    request[4]("Stale error");
    request[3]();
  });

  beforeEach(() => {
    getSettings.mockResolvedValue(SERVER_READY);
    getUserMe.mockResolvedValue(SETUP_ACCOUNT);
    getChatHistory.mockReset().mockResolvedValue([]);
    streamChatMessage.mockReset().mockImplementation(() => new AbortController());
  });

  it.each([
    { inFlight: true, departure: "unmount" }, { inFlight: false, departure: "unmount" },
    { inFlight: true, departure: "collapse" }, { inFlight: false, departure: "collapse" },
  ])("keeps generation running on $departure with inFlight=$inFlight", async ({ inFlight, departure }) => {
    const notesUpdated = vi.fn();
    const view = render(<ChatPanel inFlight={inFlight} onNotesUpdate={notesUpdated}
      onCollapse={() => view.rerender(<></>)} />);
    await waitFor(() => expect(screen.queryByText("Loading chat history...")).not.toBeInTheDocument());
    send("Who spoke?");
    const controller = streamChatMessage.mock.results[0].value;
    if (departure === "collapse") fireEvent.click(screen.getByRole("button", { name: "Collapse chat panel" }));
    else view.unmount();
    lateEvents(callbacks());
    expect(controller.signal.aborted).toBe(false);
    expect(notesUpdated).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalled();
  });

  it("aborts only the explicitly stopped request and fences its late callbacks", async () => {
    const notesUpdated = vi.fn();
    render(<ChatPanel onNotesUpdate={notesUpdated} />);
    await waitFor(() => expect(screen.queryByText("Loading chat history...")).not.toBeInTheDocument());
    send("First question");
    const stopped = streamChatMessage.mock.results[0].value;
    fireEvent.click(screen.getByTitle("Stop Generation"));
    expect(stopped.signal.aborted).toBe(true);
    expect(addNotification).toHaveBeenCalledWith({ type: "info", message: "Generation stopped" });
    addNotification.mockClear();
    send("Second question");
    lateEvents(callbacks());
    expect(screen.getByTitle("Stop Generation")).toBeInTheDocument();
    expect(screen.queryByText(/Stale|Only the most recent part/)).not.toBeInTheDocument();
    expect(notesUpdated).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalled();
    expect(streamChatMessage.mock.results[1].value.signal.aborted).toBe(false);
    act(() => { callbacks(1)[2]("Current answer."); callbacks(1)[3](); });
    expect(screen.getByText("Current answer.")).toBeInTheDocument();
    expect(screen.queryByTitle("Stop Generation")).not.toBeInTheDocument();
  });

  it("detaches the old recording stream and accepts a new request without stale callbacks", async () => {
    const notesUpdated = vi.fn();
    const view = render(<ChatPanel onNotesUpdate={notesUpdated} />);
    await waitFor(() => expect(screen.queryByText("Loading chat history...")).not.toBeInTheDocument());
    send("Old recording question");
    const oldController = streamChatMessage.mock.results[0].value;
    currentRecordingId = "rec-2";
    view.rerender(<ChatPanel onNotesUpdate={notesUpdated} />);
    await waitFor(() => expect(screen.queryByText("Loading chat history...")).not.toBeInTheDocument());
    expect(screen.queryByTitle("Stop Generation")).not.toBeInTheDocument();
    send("New recording question");
    expect(streamChatMessage).toHaveBeenCalledTimes(2);
    expect(callbacks(1)[0]).toBe("rec-2");
    lateEvents(callbacks());
    expect(oldController.signal.aborted).toBe(false);
    expect(screen.queryByText(/Stale|Old recording question|Only the most recent part/)).not.toBeInTheDocument();
    expect(screen.getByTitle("Stop Generation")).toBeInTheDocument();
    expect(notesUpdated).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalled();
    act(() => { callbacks(1)[2]("New recording answer."); callbacks(1)[3](); });
    expect(screen.getByText("New recording answer.")).toBeInTheDocument();
  });

  it("reloads history when returning before the intervening recording has loaded", async () => {
    const view = render(<ChatPanel />);
    await waitFor(() => expect(screen.queryByText("Loading chat history...")).not.toBeInTheDocument());
    let finishHistory!: (history: unknown[]) => void;
    getChatHistory.mockImplementation(() => new Promise((resolve) => { finishHistory = resolve; }));
    currentRecordingId = "rec-2";
    view.rerender(<ChatPanel />);
    currentRecordingId = "rec-1";
    view.rerender(<ChatPanel />);
    send("Wait for history");
    expect(streamChatMessage).not.toHaveBeenCalled();
    expect(screen.getByText("Loading chat history...")).toBeInTheDocument();
    await act(async () => { finishHistory([]); });
    fireEvent.keyDown(screen.getByPlaceholderText("Ask a question..."), { key: "Enter" });
    expect(streamChatMessage).toHaveBeenCalledTimes(1);
  });
});

describe("ChatPanel AI availability gate", () => {
  beforeEach(() => {
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
    getSettings.mockReset();
    getUserMe.mockReset();
    getCliOAuthStatus.mockReset();
    getChatHistory.mockReset().mockResolvedValue([]);
  });

  it("checks user and assistant fills after the history arrives", async () => {
    getChatHistory.mockResolvedValue([
      { role: "user", content: "Summarise the meeting" },
      { role: "assistant", content: "The team agreed the agenda." },
    ]);
    const { baseElement } = renderPanel(SERVER_READY);
    const user = await screen.findByText("Summarise the meeting");
    const assistant = await screen.findByText("The team agreed the agenda.");
    expect(user.closest('[data-ui="surface"]')).toHaveClass("bg-action");
    expect(assistant.closest('[data-ui="surface"]')).toHaveClass("bg-surface-card");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
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

  // jsdom has no layout, so each test gives the list a box. The list's own
  // scrollTo moves scrollTop within it and fires the scroll event a browser
  // would, and `resize` reports what a ResizeObserver would see after layout.
  type Box = { scrollHeight: number; clientHeight: number; scrollTop: number };
  let observers: { callback: ResizeObserverCallback; targets: Set<Element> }[];

  const resize = (...targets: Element[]) =>
    act(() => {
      for (const { callback, targets: watched } of observers) {
        const entries = targets
          .filter((target) => watched.has(target))
          .map((target) => ({ target }) as ResizeObserverEntry);
        if (entries.length > 0) callback(entries, {} as ResizeObserver);
      }
    });

  const readerScrollsTo = (list: HTMLElement, top: number) => {
    list.scrollTop = top;
    fireEvent.scroll(list);
  };

  /** Opens the panel on its history and lays the list out at 1000 over 400 px. */
  const openHistory = async () => {
    renderPanel(SERVER_READY);
    await screen.findByText("She agreed to send the draft by Friday.");
    const list = screen.getByTestId("chat-messages");
    const content = list.firstElementChild as HTMLElement;
    const box: Box = { scrollHeight: 1000, clientHeight: 400, scrollTop: 0 };
    const clamp = (top: number) => Math.max(0, Math.min(top, box.scrollHeight - box.clientHeight));
    Object.defineProperties(list, {
      scrollHeight: { configurable: true, get: () => box.scrollHeight },
      clientHeight: { configurable: true, get: () => box.clientHeight },
      scrollTop: {
        configurable: true,
        get: () => box.scrollTop,
        set: (top: number) => {
          box.scrollTop = clamp(top);
        },
      },
    });
    const scrollTo = vi.fn((options: ScrollToOptions) => {
      box.scrollTop = clamp(options.top ?? 0);
      fireEvent.scroll(list);
    });
    list.scrollTo = scrollTo as unknown as HTMLElement["scrollTo"];
    // The first layout after the history renders: the landing.
    resize(list, content);
    return { list, content, box, scrollTo };
  };

  const send = (text: string) => {
    const input = screen.getByPlaceholderText("Ask a question...");
    fireEvent.change(input, { target: { value: text } });
    fireEvent.keyDown(input, { key: "Enter" });
  };

  beforeEach(() => {
    getSettings.mockReset();
    getUserMe.mockReset();
    getCliOAuthStatus.mockReset();
    streamChatMessage.mockClear();
    getChatHistory.mockResolvedValue([
      message(1, "user", "What did Alice Example agree to?"),
      message(2, "assistant", "She agreed to send the draft by Friday."),
    ]);
    // Any scroll other than the list's own would land on these.
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    observers = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        private entry: (typeof observers)[number];
        constructor(callback: ResizeObserverCallback) {
          this.entry = { callback, targets: new Set() };
          observers.push(this.entry);
        }
        observe(target: Element) {
          this.entry.targets.add(target);
        }
        unobserve(target: Element) {
          this.entry.targets.delete(target);
        }
        disconnect() {
          this.entry.targets.clear();
        }
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // A pin that ran before the side column's split and the Markdown bubbles
  // settled left the list hundreds of px short of the newest message.
  it("lands on the newest message and re-pins while the list or its content resizes", async () => {
    const { list, content, box, scrollTo } = await openHistory();
    expect(box.scrollTop).toBe(600);

    // The answers' Markdown renders after mount and adds height.
    box.scrollHeight = 1400;
    resize(content);
    expect(box.scrollTop).toBe(1000);

    // The split settles, or the reader drags it, and the list gets shorter.
    box.clientHeight = 300;
    resize(list);
    expect(box.scrollTop).toBe(1100);

    // Nothing has been sent, so every pin is instant and none can be cut short.
    expect(scrollTo.mock.calls.map(([options]) => options.behavior)).toEqual([
      "instant",
      "instant",
      "instant",
    ]);
    // scrollIntoView would also scroll every ancestor, the app shell included.
    expect(Element.prototype.scrollTo).not.toHaveBeenCalled();
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it("leaves a reader who has scrolled up where they are", async () => {
    const { list, content, box, scrollTo } = await openHistory();

    // Even a nudge up is the reader's own.
    readerScrollsTo(list, 590);
    box.scrollHeight = 1400;
    resize(content);
    box.clientHeight = 350;
    resize(list);
    expect(box.scrollTop).toBe(590);
    expect(scrollTo).toHaveBeenCalledTimes(1);

    // Coming back near the bottom pins it again.
    readerScrollsTo(list, 1030);
    box.scrollHeight = 1500;
    resize(content);
    expect(box.scrollTop).toBe(1150);
  });

  it("follows a sent message and its streamed answer while the reader is at the bottom", async () => {
    const { list, content, box, scrollTo } = await openHistory();

    // Sending brings a reader who had scrolled up back down, and the new
    // message glides in.
    readerScrollsTo(list, 100);
    send("And Bob Example?");
    expect(streamChatMessage).toHaveBeenCalledTimes(1);
    box.scrollHeight = 1200;
    resize(content);
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 1200, behavior: "smooth" });
    expect(box.scrollTop).toBe(800);

    // A glide's own scroll events, part way down, do not release the list.
    box.scrollHeight = 1600;
    readerScrollsTo(list, 900);
    const onToken = streamChatMessage.mock.calls[0][2] as (token: string) => void;
    act(() => onToken("He will book the follow-up."));
    resize(content);
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 1600, behavior: "smooth" });
    expect(box.scrollTop).toBe(1200);

    // The list's own size still jumps rather than glides.
    box.clientHeight = 300;
    resize(list);
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 1600, behavior: "instant" });

    // Scrolling up to read mid-stream stops the following.
    readerScrollsTo(list, 1000);
    const calls = scrollTo.mock.calls.length;
    box.scrollHeight = 1700;
    act(() => onToken(" On Friday."));
    resize(content);
    expect(scrollTo).toHaveBeenCalledTimes(calls);
    expect(box.scrollTop).toBe(1000);
  });
});
