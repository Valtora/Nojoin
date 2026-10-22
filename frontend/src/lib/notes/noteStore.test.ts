import { IDBFactory } from "fake-indexeddb";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CLEAR_ON_SIGN_OUT_MS,
  clearStoredNotes,
  IndexedDbNoteStore,
  keepOnlyNotesOf,
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
  sharedNoteStore,
  type NoteStore,
  type StoredOutbox,
} from "./noteStore";

const record = (account: string, recordingId: string, body = "Budget agreed"): StoredOutbox => ({
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
    await store.put(record("alice", "rec-1"));
    await store.put(record("alice", "rec-1", "Budget agreed, in full"));
    await store.put(record("alice", "rec-2"));
    await store.put(record("bob", "rec-1"));

    expect(await store.get("alice", "rec-1")).toEqual(record("alice", "rec-1", "Budget agreed, in full"));
    expect(await store.get("bob", "rec-1")).toEqual(record("bob", "rec-1"));
    expect(await store.get("carol", "rec-1")).toBeUndefined();

    await store.remove("alice", "rec-1");
    expect(await store.get("alice", "rec-1")).toBeUndefined();
    expect(await store.get("alice", "rec-2")).toEqual(record("alice", "rec-2"));
  });

  it("removes every other account's records, and everything on clear", async () => {
    const store = createStore();
    await store.put(record("alice", "rec-1"));
    await store.put(record("alice", "rec-2"));
    await store.put(record("bob", "rec-3"));

    await store.keepOnly("bob");
    expect(await store.get("alice", "rec-1")).toBeUndefined();
    expect(await store.get("alice", "rec-2")).toBeUndefined();
    expect(await store.get("bob", "rec-3")).toEqual(record("bob", "rec-3"));

    await store.clear();
    expect(await store.get("bob", "rec-3")).toBeUndefined();
  });
});

describe("the page's note store", () => {
  afterEach(() => {
    vi.useRealTimers();
    setNoteStoreForTests(null);
  });

  it("removes other accounts' notes when the browser is found signed in, once per account", async () => {
    const store = new MemoryNoteStore();
    const keepOnly = vi.spyOn(store, "keepOnly");
    setNoteStoreForTests(store);
    await store.put(record("alice", "rec-1"));
    await store.put(record("bob", "rec-2"));

    await keepOnlyNotesOf("bob");
    await keepOnlyNotesOf("bob");
    expect(keepOnly).toHaveBeenCalledTimes(1);
    expect(await store.get("alice", "rec-1")).toBeUndefined();
    expect(await store.get("bob", "rec-2")).toEqual(record("bob", "rec-2"));

    await keepOnlyNotesOf("alice");
    expect(keepOnly).toHaveBeenCalledTimes(2);
    expect(await store.get("bob", "rec-2")).toBeUndefined();
  });

  it("tries again on the next sign-in when removing other accounts' notes fails", async () => {
    const store = new MemoryNoteStore();
    const keepOnly = vi.spyOn(store, "keepOnly").mockRejectedValueOnce(new Error("quota"));
    setNoteStoreForTests(store);

    await expect(keepOnlyNotesOf("bob")).resolves.toBeUndefined();
    await keepOnlyNotesOf("bob");
    expect(keepOnly).toHaveBeenCalledTimes(2);
  });

  it("removes every note on sign-out and stores nothing from the page after", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    await store.put(record("alice", "rec-1"));

    await clearStoredNotes();

    expect(await store.get("alice", "rec-1")).toBeUndefined();
    expect(sharedNoteStore()).toBeNull();
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
      await expect(keepOnlyNotesOf("bob")).resolves.toBeUndefined();
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
