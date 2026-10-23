import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SessionSignInDialog from "@/components/SessionSignInDialog";
import api, { leaveForSignIn } from "@/lib/api/client";
import {
  holdSignInInPlace,
  noteSignedIn,
  resetSessionForTests,
  useSessionStore,
} from "@/lib/session";
import { NO_ANSWER, resetNotesServer, server } from "@/test/fakeNotesServer";
import { renderWithProviders } from "@/test/renderWithProviders";

import { EMPTY_DRAFT } from "./noteOutbox";
import {
  forgetNoteOutboxes,
  forgetRecordingNotes,
  holdPendingNoteWrite,
  noteOutboxesHere,
  noteOutboxFor,
} from "./noteOutboxes";
import {
  clearStoredNotes,
  MemoryNoteStore,
  setNoteStoreForTests,
  type StoredOutbox,
} from "./noteStore";

const login = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
  login: (username: string, password: string) => login(username, password),
  getCurrentUser: () => Promise.reject(new Error("Not authenticated")),
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    status: "idle",
    runtimeActive: false,
    stop: vi.fn(),
    uploadQueue: { segments: 0, seconds: 0 },
  }),
}));

const SESSION_ENDED = {
  isAxiosError: true,
  response: { status: 401, data: { detail: "Not authenticated" } },
};

const settle = async () => {
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
};

const leave = () => {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
};

describe("one outbox per recording", () => {
  beforeEach(() => {
    resetNotesServer();
    resetSessionForTests();
    forgetNoteOutboxes();
  });

  afterEach(() => {
    forgetNoteOutboxes();
    resetSessionForTests();
  });

  it("hands every view of a recording the same outbox, and another recording its own", () => {
    expect(noteOutboxFor("rec-a")).toBe(noteOutboxFor("rec-a"));
    expect(noteOutboxFor("rec-b")).not.toBe(noteOutboxFor("rec-a"));
  });

  it("asks before the page closes, and holds sign-in in place, while a note or the box's text is unsaved", async () => {
    const held = () => useSessionStore.getState().heldBy;
    const outbox = noteOutboxFor("rec-a");
    expect(leave()).toBe(false);
    expect(held()).toEqual([]);

    outbox.setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
    expect(outbox.hasUnsaved()).toBe(true);
    expect(leave()).toBe(true);
    expect(held()).toEqual(["notes"]);
    outbox.setDraft(EMPTY_DRAFT);
    expect(leave()).toBe(false);
    expect(held()).toEqual([]);

    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "point", body: "Unsaved", anchorMs: 1 });
    await settle();
    expect(leave()).toBe(true);
    expect(held()).toEqual(["notes"]);
    outbox.retry(outbox.getSnapshot().entries[0].key);
    await settle();
    expect(leave()).toBe(false);
    expect(useSessionStore.getState().heldInPlace).toBe(false);
  });

  it("keeps each recording's outbox in the browser, and stops once forgotten", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    noteSignedIn({ username: "alice", force_password_change: false, id: 1 });
    try {
      noteOutboxFor("rec-a").setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
      await vi.waitFor(async () =>
        expect(await store.get(1, "rec-a")).toMatchObject({
          draft: { text: "Half a thought" },
        }),
      );

      const outbox = noteOutboxFor("rec-a");
      forgetNoteOutboxes();
      outbox.setDraft({ ...EMPTY_DRAFT, text: "Typed after the page let go" });
      await settle();
      expect(await store.get(1, "rec-a")).toMatchObject({
        draft: { text: "Half a thought" },
      });
    } finally {
      setNoteStoreForTests(null);
    }
  });

  it("forgets a recording deleted for good: its outbox, its record, its mark and its hold on the page", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    noteSignedIn({ username: "alice", force_password_change: false, id: 1 });
    try {
      const outbox = noteOutboxFor("rec-a");
      noteOutboxFor("rec-b").setDraft({ ...EMPTY_DRAFT, text: "Another meeting" });
      outbox.setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
      await vi.waitFor(async () => expect(await store.recordingsOf(1)).toHaveLength(2));
      expect(leave()).toBe(true);

      await forgetRecordingNotes("rec-a");

      expect(await store.recordingsOf(1)).toEqual(["rec-b"]);
      expect(noteOutboxesHere()).toEqual({ held: ["rec-b"], unsent: ["rec-b"] });
      // The page asks before closing for rec-b's text alone.
      noteOutboxFor("rec-b").setDraft(EMPTY_DRAFT);
      expect(leave()).toBe(false);
      expect(useSessionStore.getState().heldBy).toEqual([]);
      // The forgotten outbox writes nothing more, and a new one starts empty.
      outbox.setDraft({ ...EMPTY_DRAFT, text: "Typed after the delete" });
      await settle();
      expect(await store.get(1, "rec-a")).toBeUndefined();
      expect(noteOutboxFor("rec-a")).not.toBe(outbox);
      expect(noteOutboxFor("rec-a").getSnapshot().draft.text).toBe("");
    } finally {
      setNoteStoreForTests(null);
    }
  });

  it("doesn't let a write under way bring a forgotten recording's record back", async () => {
    const store = new MemoryNoteStore();
    let finishPut = () => {};
    const put = store.put.bind(store);
    vi.spyOn(store, "put").mockImplementation(
      (stored: StoredOutbox) =>
        new Promise<void>((resolve) => {
          finishPut = () => void put(stored).then(resolve);
        }),
    );
    setNoteStoreForTests(store);
    noteSignedIn({ username: "alice", force_password_change: false, id: 1 });
    try {
      noteOutboxFor("rec-a").setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
      await vi.waitFor(() => expect(store.put).toHaveBeenCalled());

      const forgetting = forgetRecordingNotes("rec-a");
      // Everything else the forget does has run by the time the write lands.
      await new Promise((resolve) => setTimeout(resolve, 0));
      finishPut();
      await forgetting;

      expect(await store.get(1, "rec-a")).toBeUndefined();
    } finally {
      setNoteStoreForTests(null);
    }
  });

  it("drops its outbox when another tab forgets the recording", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    noteSignedIn({ username: "alice", force_password_change: false, id: 1 });
    const otherTab = new BroadcastChannel("nojoin-notes");
    try {
      noteOutboxFor("rec-a").setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
      await vi.waitFor(async () => expect(await store.get(1, "rec-a")).toBeDefined());

      otherTab.postMessage({ kind: "forgot", recordingId: "rec-a" });

      await vi.waitFor(() => expect(noteOutboxesHere().held).toEqual([]));
      await vi.waitFor(async () => expect(await store.get(1, "rec-a")).toBeUndefined());
      expect(leave()).toBe(false);
    } finally {
      otherTab.close();
      setNoteStoreForTests(null);
    }
  });

  it("lets go of its own hold only, never a capture's", () => {
    holdSignInInPlace(true);
    const outbox = noteOutboxFor("rec-a");

    outbox.setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
    expect(useSessionStore.getState().heldBy).toEqual(["capture", "notes"]);
    outbox.setDraft(EMPTY_DRAFT);

    expect(useSessionStore.getState().heldBy).toEqual(["capture"]);
    expect(useSessionStore.getState().heldInPlace).toBe(true);
  });

  it("holds the page and sign-in while an edit of a saved note is unconfirmed, until each hold lets go", () => {
    const edit = holdPendingNoteWrite();
    const remove = holdPendingNoteWrite();
    expect(leave()).toBe(true);
    expect(useSessionStore.getState().heldBy).toEqual(["notes"]);

    edit();
    edit();
    expect(leave()).toBe(true);
    remove();
    expect(leave()).toBe(false);
    expect(useSessionStore.getState().heldBy).toEqual([]);
  });
});

describe("an ended session on a page holding notes not saved yet", () => {
  const originalAdapter = api.defaults.adapter;
  let navigations: string[] = [];

  beforeEach(() => {
    resetNotesServer();
    resetSessionForTests();
    forgetNoteOutboxes();
    noteSignedIn({ username: "alice", force_password_change: false });
    login.mockReset();
    api.defaults.adapter = "fetch";
    navigations = [];
    const navigate = (url: string) => {
      navigations.push(url);
    };
    vi.stubGlobal("location", {
      pathname: "/recordings/rec-a",
      get href() {
        return "https://nojoin.test/recordings/rec-a";
      },
      set href(url: string) {
        navigate(url);
      },
      assign: navigate,
      replace: navigate,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Not authenticated" }), {
            status: 401,
            headers: { "content-type": "application/json" },
          }),
      ),
    );
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    forgetNoteOutboxes();
    resetSessionForTests();
  });

  it("stays on the page and asks for sign-in, where it would otherwise reload to the sign-in page", async () => {
    renderWithProviders(<SessionSignInDialog />);
    act(() => {
      noteOutboxFor("rec-a").setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
    });

    await expect(api.get("/recordings/rec-a")).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(navigations).toEqual([]);
    expect(await screen.findByText("Sign in to save your notes")).toBeInTheDocument();
  });

  it("reloads to the sign-in page as before once nothing is unsaved", async () => {
    const outbox = noteOutboxFor("rec-a");
    outbox.setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
    outbox.setDraft(EMPTY_DRAFT);

    await expect(api.get("/recordings/rec-a")).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(navigations).toEqual(["/login"]);
  });

  describe("leaving for the sign-in page", () => {
    let store: MemoryNoteStore;

    beforeEach(() => {
      store = new MemoryNoteStore();
      setNoteStoreForTests(store);
      noteSignedIn({ username: "alice", force_password_change: false, id: 1 });
    });

    afterEach(() => {
      setNoteStoreForTests(null);
    });

    const typeAndEndSession = async (text: string) => {
      act(() => {
        noteOutboxFor("rec-a").setDraft({ ...EMPTY_DRAFT, text });
      });
      await expect(api.get("/recordings/rec-a")).rejects.toMatchObject({
        response: { status: 401 },
      });
      await screen.findByText("Sign in to save your notes");
    };

    it("is offered, and leaves through the leave warning, which stays armed", async () => {
      renderWithProviders(<SessionSignInDialog />);
      await typeAndEndSession("Half a thought");
      await vi.waitFor(async () => expect(await store.get(1, "rec-a")).toBeDefined());
      expect(
        screen.getByText(/Signing in there as a different account removes the notes kept in this browser/),
      ).toBeInTheDocument();

      // Renamed meanwhile, say: the dialog can only sign in as the old name.
      fireEvent.click(screen.getByRole("button", { name: "Go to the sign-in page" }));

      expect(navigations).toEqual(["/login"]);
      // Stored or not, the browser asks before the page goes.
      expect(leave()).toBe(true);
      expect(useSessionStore.getState().heldBy).toEqual(["notes"]);
    });

    it("is offered when the browser holds none of the notes, as after a sign-out cancelled at the leave prompt", async () => {
      renderWithProviders(<SessionSignInDialog />);
      act(() => {
        noteOutboxFor("rec-a").setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
      });
      await vi.waitFor(async () => expect(await store.get(1, "rec-a")).toBeDefined());
      await act(async () => {
        await clearStoredNotes();
      });

      await typeAndEndSession("Half a thought, and more");

      expect(screen.getByRole("button", { name: "Go to the sign-in page" })).toBeEnabled();
      expect(leave()).toBe(true);
      expect(await store.get(1, "rec-a")).toBeUndefined();
    });

    it("stays put when a sign-in came back meanwhile", () => {
      expect(leaveForSignIn()).toBe(false);
      expect(navigations).toEqual([]);

      useSessionStore.setState({ signInRequired: true });
      expect(leaveForSignIn()).toBe(true);
      expect(navigations).toEqual(["/login"]);
    });
  });

  it("saves a note refused for the ended session once its owner signs back in through the dialog", async () => {
    login.mockImplementation(async (username: string) => {
      noteSignedIn({ username, force_password_change: false });
      return { username, force_password_change: false, is_superuser: false };
    });
    renderWithProviders(<SessionSignInDialog />);
    const outbox = noteOutboxFor("rec-a");
    server.failures.push(SESSION_ENDED);

    act(() => {
      outbox.submit({ kind: "point", body: "Budget agreed", anchorMs: 61_250 });
    });

    fireEvent.change(await screen.findByLabelText("Password"), {
      target: { value: "correct horse" },
    });
    expect(server.creates).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(outbox.getSnapshot().entries).toEqual([]));
    expect(login).toHaveBeenCalledWith("alice", "correct horse");
    expect(server.creates).toHaveLength(2);
    expect(server.creates[1]).toEqual(server.creates[0]);
    expect(server.notes.size).toBe(1);
    expect(navigations).toEqual([]);
    expect(useSessionStore.getState().heldInPlace).toBe(false);
  });
});
