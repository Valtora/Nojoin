import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSessionForTests } from "@/lib/session";

import {
  CLEAR_ON_SIGN_OUT_MS,
  clearStoredNotes,
  IndexedDbNoteStore,
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
  sharedNoteStore,
  storedNotesSignedIn,
  subscribeStoreEvents,
  type NoteStore,
  type StoredOutbox,
  type StoreEvent,
} from "./noteStore";

const ALICE = 1;
const BOB = 2;

const record = (account: number, recordingId: string, body = "Budget agreed"): StoredOutbox => ({
  version: NOTE_RECORD_VERSION,
  account,
  recordingId,
  notes: [
    { id: `${account}-${recordingId}`, writeToken: "t", kind: "point", body, anchorMs: 1, createdAt: "x" },
  ],
  draft: null,
  discards: [],
});

describe.each([
  ["IndexedDB", () => new IndexedDbNoteStore(new IDBFactory())],
  ["memory", () => new MemoryNoteStore()],
] as [string, () => NoteStore][])("%s note store", (_name, createStore) => {
  it("keeps one record per account and recording, and replaces it on each write", async () => {
    const store = createStore();
    await store.put(record(ALICE, "rec-1"));
    await store.put(record(ALICE, "rec-1", "Budget agreed, in full"));
    await store.put(record(ALICE, "rec-2"));
    await store.put(record(BOB, "rec-1"));

    expect(await store.get(ALICE, "rec-1")).toEqual(record(ALICE, "rec-1", "Budget agreed, in full"));
    expect(await store.get(BOB, "rec-1")).toEqual(record(BOB, "rec-1"));
    expect(await store.get(3, "rec-1")).toBeUndefined();

    await store.remove(ALICE, "rec-1");
    expect(await store.get(ALICE, "rec-1")).toBeUndefined();
    expect(await store.get(ALICE, "rec-2")).toEqual(record(ALICE, "rec-2"));
  });

  it("removes the records of every account not kept, and everything on clear", async () => {
    const store = createStore();
    await store.put(record(ALICE, "rec-1"));
    await store.put(record(ALICE, "rec-2"));
    await store.put(record(BOB, "rec-3"));
    await store.put(record(3, "rec-4"));

    await store.keepOnly([BOB, 3]);
    expect(await store.get(ALICE, "rec-1")).toBeUndefined();
    expect(await store.get(ALICE, "rec-2")).toBeUndefined();
    expect(await store.get(BOB, "rec-3")).toEqual(record(BOB, "rec-3"));
    expect(await store.get(3, "rec-4")).toEqual(record(3, "rec-4"));

    await store.clear();
    expect(await store.get(BOB, "rec-3")).toBeUndefined();
  });
});

describe("the IndexedDB note store and a later version", () => {
  it("closes its connection when another tab opens a later version, and opens afresh after", async () => {
    const factory = new IDBFactory();
    const store = new IndexedDbNoteStore(factory);
    await store.put(record(ALICE, "rec-1"));

    // A later version of the app upgrades the database in another tab: it
    // waits for this connection to close, and would wait for ever otherwise.
    const upgraded = await new Promise<string>((resolve) => {
      const request = factory.open("nojoin-notes", 2);
      request.onblocked = () => resolve("blocked");
      request.onsuccess = () => {
        request.result.close();
        resolve("upgraded");
      };
    });
    expect(upgraded).toBe("upgraded");
  });
});

describe("the page's note store", () => {
  beforeEach(() => {
    resetSessionForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
    setNoteStoreForTests(null);
    resetSessionForTests();
  });

  /** Another tab's end of the channel the page's tabs share. */
  const otherTab = () => {
    const channel = new BroadcastChannel("nojoin-notes");
    const heard: unknown[] = [];
    channel.onmessage = (event: MessageEvent) => heard.push(event.data);
    tabs.push(channel);
    return { channel, heard };
  };
  let tabs: BroadcastChannel[] = [];

  afterEach(() => {
    for (const tab of tabs) tab.close();
    tabs = [];
  });

  it("removes other accounts' notes each time the server names the account, and tells the other tabs", async () => {
    const store = new MemoryNoteStore();
    const keepOnly = vi.spyOn(store, "keepOnly");
    setNoteStoreForTests(store);
    const events: StoreEvent[] = [];
    const stop = subscribeStoreEvents((event) => events.push(event));
    const { heard } = otherTab();
    await store.put(record(ALICE, "rec-1"));
    await store.put(record(BOB, "rec-2"));

    // A sign-in that doesn't name the account by id removes nothing yet.
    await storedNotesSignedIn();
    expect(keepOnly).not.toHaveBeenCalled();

    await storedNotesSignedIn(BOB);
    expect(await store.get(ALICE, "rec-1")).toBeUndefined();
    expect(await store.get(BOB, "rec-2")).toEqual(record(BOB, "rec-2"));
    // Again on the next /users/me: another tab may have written meanwhile.
    await store.put(record(ALICE, "rec-3"));
    await storedNotesSignedIn(BOB);
    expect(await store.get(ALICE, "rec-3")).toBeUndefined();
    expect(keepOnly).toHaveBeenCalledTimes(2);

    expect(events).toEqual([
      { kind: "kept", accounts: [BOB] },
      { kind: "kept", accounts: [BOB] },
    ]);
    await vi.waitFor(() =>
      expect(heard).toEqual([
        { kind: "kept", accounts: [BOB] },
        { kind: "kept", accounts: [BOB] },
      ]),
    );
    stop();
  });

  it("hears another tab's cleanup and sign-out, and nothing else", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    const events: StoreEvent[] = [];
    const stop = subscribeStoreEvents((event) => events.push(event));
    const { channel } = otherTab();

    channel.postMessage({ kind: "kept", accounts: "everyone" });
    channel.postMessage({ kind: "cleared" });
    channel.postMessage({ kind: "kept", accounts: [BOB] });
    await vi.waitFor(() => expect(events).toEqual([{ kind: "kept", accounts: [BOB] }]));
    expect(sharedNoteStore()).toBe(store);

    channel.postMessage({ kind: "signed-out" });
    await vi.waitFor(() => expect(sharedNoteStore()).toBeNull());
    expect(events.at(-1)).toEqual({ kind: "stopped" });

    // Storing resumes only when the server accepts a sign-in in this tab.
    await storedNotesSignedIn();
    expect(sharedNoteStore()).toBe(store);
    expect(events.at(-1)).toEqual({ kind: "resumed" });
    stop();
  });

  it("tries again on the next sign-in when removing other accounts' notes fails", async () => {
    const store = new MemoryNoteStore();
    const keepOnly = vi.spyOn(store, "keepOnly").mockRejectedValueOnce(new Error("quota"));
    setNoteStoreForTests(store);
    const events: StoreEvent[] = [];
    const stop = subscribeStoreEvents((event) => events.push(event));

    await expect(storedNotesSignedIn(BOB)).resolves.toBeUndefined();
    expect(events).toEqual([]);
    await storedNotesSignedIn(BOB);
    expect(keepOnly).toHaveBeenCalledTimes(2);
    expect(events).toEqual([{ kind: "kept", accounts: [BOB] }]);
    stop();
  });

  it("removes every note on sign-out, tells the other tabs, and stores nothing until the server accepts a sign-in again", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    const events: StoreEvent[] = [];
    const stop = subscribeStoreEvents((event) => events.push(event));
    const { heard } = otherTab();
    await store.put(record(ALICE, "rec-1"));

    await clearStoredNotes();
    expect(await store.get(ALICE, "rec-1")).toBeUndefined();
    expect(sharedNoteStore()).toBeNull();
    expect(events).toEqual([{ kind: "stopped" }]);
    await vi.waitFor(() => expect(heard).toEqual([{ kind: "signed-out" }]));

    // The leave prompt was cancelled, and the user signs in again on the page.
    await storedNotesSignedIn();
    expect(sharedNoteStore()).toBe(store);
    expect(events).toEqual([{ kind: "stopped" }, { kind: "resumed" }]);
    stop();
  });

  it("does not hold up sign-out when the storage never answers", async () => {
    vi.useFakeTimers();
    const store = new MemoryNoteStore();
    vi.spyOn(store, "clear").mockReturnValue(new Promise<void>(() => undefined));
    setNoteStoreForTests(store);

    let done = false;
    void clearStoredNotes().then(() => {
      done = true;
    });
    await vi.advanceTimersByTimeAsync(CLEAR_ON_SIGN_OUT_MS - 1);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(done).toBe(true);
  });

  it("signs out cleanly when the storage refuses", async () => {
    const store = new MemoryNoteStore();
    vi.spyOn(store, "clear").mockRejectedValue(new Error("refused"));
    setNoteStoreForTests(store);

    await expect(clearStoredNotes()).resolves.toBeUndefined();
  });

  it("keeps notes in memory only where the browser has no IndexedDB", async () => {
    setNoteStoreForTests();
    vi.stubGlobal("indexedDB", undefined);
    try {
      expect(sharedNoteStore()).toBeNull();
      await expect(storedNotesSignedIn(BOB)).resolves.toBeUndefined();
      await expect(clearStoredNotes()).resolves.toBeUndefined();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps notes in memory only where site data is blocked", () => {
    setNoteStoreForTests();
    Object.defineProperty(globalThis, "indexedDB", {
      configurable: true,
      get() {
        throw new DOMException("The operation is insecure.", "SecurityError");
      },
    });
    try {
      expect(sharedNoteStore()).toBeNull();
    } finally {
      Reflect.deleteProperty(globalThis, "indexedDB");
    }
  });

  it("uses the browser's IndexedDB where there is one", async () => {
    setNoteStoreForTests();
    vi.stubGlobal("indexedDB", new IDBFactory());
    try {
      const store = sharedNoteStore();
      expect(store).toBeInstanceOf(IndexedDbNoteStore);
      expect(sharedNoteStore()).toBe(store);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
