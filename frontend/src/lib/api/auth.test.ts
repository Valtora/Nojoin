import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearStoredNotes,
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
  sharedNoteStore,
  type StoredOutbox,
} from "@/lib/notes/noteStore";
import { resetSessionForTests } from "@/lib/session";

import { getCurrentUser, login, logout } from "./auth";
import api from "./client";

// The real axios instance runs on the fetch adapter against a stubbed fetch.
// jsdom cannot navigate, so location is a stand-in that logs every
// navigation, whether by href, assign or replace.
const originalAdapter = api.defaults.adapter;

let navigations: string[] = [];

const navigate = (url: string) => {
  navigations.push(url);
};

const serverAnswers = (answer: () => Promise<Response>) => {
  vi.stubGlobal("fetch", vi.fn(answer));
};

describe("logout", () => {
  beforeEach(() => {
    api.defaults.adapter = "fetch";
    navigations = [];
    vi.stubGlobal("location", {
      pathname: "/recordings",
      get href() {
        return "https://nojoin.test/recordings";
      },
      set href(url: string) {
        navigate(url);
      },
      assign: navigate,
      replace: navigate,
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("leaves for the sign-in page once the server ends the session", async () => {
    serverAnswers(async () => new Response(null, { status: 204 }));

    await logout();

    expect(navigations).toEqual(["/login"]);
  });

  it("still leaves for the sign-in page when the server cannot be reached", async () => {
    serverAnswers(async () => {
      throw new TypeError("Failed to fetch");
    });

    await logout();

    expect(navigations).toEqual(["/login"]);
  });
});

describe("notes kept in the browser", () => {
  const ALICE = 1;
  const BOB = 2;
  const alicesNotes: StoredOutbox = {
    version: NOTE_RECORD_VERSION,
    account: ALICE,
    recordingId: "rec-1",
    notes: [],
    draft: { text: "Half a thought", mode: "typing", typingStartMs: null },
    discards: [],
  };
  let store: MemoryNoteStore;

  // `/users/me` names the account by id; the sign-in endpoint does not.
  const signedInAnswer = (username: string, id?: number) => async () =>
    new Response(JSON.stringify({ id, username, force_password_change: false }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

  beforeEach(async () => {
    api.defaults.adapter = "fetch";
    navigations = [];
    vi.stubGlobal("location", {
      pathname: "/recordings",
      get href() {
        return "https://nojoin.test/recordings";
      },
      set href(url: string) {
        navigate(url);
      },
      assign: navigate,
      replace: navigate,
    });
    resetSessionForTests();
    store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    await store.put(alicesNotes);
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    setNoteStoreForTests(null);
    resetSessionForTests();
  });

  it("removes them before leaving for the sign-in page on sign-out", async () => {
    // A storage that takes its time, as a real one does.
    const clear = store.clear.bind(store);
    vi.spyOn(store, "clear").mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      await clear();
    });
    let storedAtNavigation: unknown = "unread";
    vi.stubGlobal("location", {
      pathname: "/recordings",
      set href(url: string) {
        navigations.push(url);
        void store.get(ALICE, "rec-1").then((found) => {
          storedAtNavigation = found;
        });
      },
    });
    serverAnswers(async () => new Response(null, { status: 204 }));

    await logout();

    expect(navigations).toEqual(["/login"]);
    await vi.waitFor(() => expect(storedAtNavigation).toBeUndefined());
  });

  it("removes another account's notes once the server names who signed in", async () => {
    serverAnswers(signedInAnswer("bob"));
    await login("bob", "secret");
    // The sign-in alone doesn't name the account by id, so nothing goes yet.
    expect(await store.get(ALICE, "rec-1")).toEqual(alicesNotes);

    serverAnswers(signedInAnswer("bob", BOB));
    await getCurrentUser();

    await vi.waitFor(async () => expect(await store.get(ALICE, "rec-1")).toBeUndefined());
  });

  it("removes another account's notes when the browser turns out to be signed in as someone else", async () => {
    serverAnswers(signedInAnswer("bob", BOB));

    await getCurrentUser();

    await vi.waitFor(async () => expect(await store.get(ALICE, "rec-1")).toBeUndefined());
  });

  it("stores notes again once a sign-in is accepted after a sign-out that stayed on the page", async () => {
    await clearStoredNotes();
    expect(sharedNoteStore()).toBeNull();

    // The leave prompt was cancelled, and the user signs in in the dialog.
    serverAnswers(signedInAnswer("alice"));
    await login("alice", "secret");

    expect(sharedNoteStore()).toBe(store);
  });

  it("keeps the account's own notes when it signs in again, under its old name or a new one", async () => {
    serverAnswers(signedInAnswer("alice", ALICE));
    await login("alice", "secret");
    await getCurrentUser();
    expect(await store.get(ALICE, "rec-1")).toEqual(alicesNotes);

    serverAnswers(signedInAnswer("alice-renamed", ALICE));
    await login("alice-renamed", "secret");
    await getCurrentUser();
    expect(await store.get(ALICE, "rec-1")).toEqual(alicesNotes);
  });
});
