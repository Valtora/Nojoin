import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { noteSignedIn, resetSessionForTests, useSessionStore } from "@/lib/session";
import {
  coded,
  createTimelineNote,
  deleteTimelineNote,
  NO_ANSWER,
  resetNotesServer,
  server,
} from "@/test/fakeNotesServer";

import { NoteOutbox, shownNotes, type ComposerDraft } from "./noteOutbox";
import { persistNoteOutbox, readStoredOutbox } from "./notePersistence";
import {
  clearStoredNotes,
  IndexedDbNoteStore,
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
  sharedNoteStore,
  storedNotesSignedIn,
  storingStopped,
  type NoteStore,
  type StoredOutbox,
} from "./noteStore";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
}));

const RECORDING = "rec-1";
const DRAFT: ComposerDraft = { text: "Half a thought", mode: "typing", typingStartMs: 70_000 };
const ALICE = 1;
const BOB = 2;

const signedIn = (username: string, id: number) =>
  noteSignedIn({ username, force_password_change: false, id });

const settle = async () => {
  for (let index = 0; index < 10; index += 1) await Promise.resolve();
};

/** The pages opened in a test; each "reload" stops the one before. */
let pages: Array<() => void> = [];

/** Opens the recording on a page whose outbox is kept in `store`. */
const openPage = (store: NoteStore | null) => {
  const outbox = new NoteOutbox(RECORDING);
  pages.push(persistNoteOutbox(outbox, RECORDING, () => store));
  return outbox;
};

/** Opens the recording, and returns once the page has read what `store` kept. */
const openPageAndRead = async (store: NoteStore) => {
  const get = vi.spyOn(store, "get");
  const outbox = openPage(store);
  await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  await get.mock.results[0].value;
  await settle();
  get.mockRestore();
  return outbox;
};

/** The page goes away, as a reload or a crash takes it; nothing more is written. */
const closePages = () => {
  for (const stop of pages) stop();
  pages = [];
};

const stored = async (store: NoteStore, account = ALICE) =>
  (await store.get(account, RECORDING)) as StoredOutbox | undefined;

const storedNoteIds = async (store: NoteStore, account = ALICE) =>
  ((await stored(store, account))?.notes ?? []).map((note) => (note as { id: string }).id);

/** Records every write to `store`, and how many were under way at once. */
const watchWrites = (store: NoteStore) => {
  const writes: StoredOutbox[] = [];
  let running = 0;
  const seen = { most: 0 };
  const put = store.put.bind(store);
  vi.spyOn(store, "put").mockImplementation(async (record) => {
    writes.push(structuredClone(record));
    running += 1;
    seen.most = Math.max(seen.most, running);
    try {
      await new Promise((resolve) => setTimeout(resolve, 5));
      await put(record);
    } finally {
      running -= 1;
    }
  });
  return { writes, seen };
};

/** A page as alice with one note whose create failed, one whose answer was lost, and text in the box. */
const leaveUnsavedNotes = async (store: NoteStore) => {
  const outbox = openPage(store);
  server.failures.push(NO_ANSWER);
  outbox.submit({ kind: "point", body: "Budget agreed", anchorMs: 61_250 });
  await settle();
  server.loseNextAnswer = true;
  outbox.submit({ kind: "head", body: "Agenda", anchorMs: null });
  await settle();
  outbox.setDraft(DRAFT);
  const entries = outbox.getSnapshot().entries;
  expect(entries.map((entry) => entry.status)).toEqual(["failed", "failed"]);
  await vi.waitFor(async () => {
    expect(await storedNoteIds(store)).toEqual(entries.map((entry) => entry.id));
    expect((await stored(store))?.draft).toEqual(DRAFT);
  });
  return entries;
};

describe("a note outbox kept in the browser", () => {
  let store: NoteStore;

  beforeEach(() => {
    resetNotesServer();
    resetSessionForTests();
    signedIn("alice", ALICE);
    store = new IndexedDbNoteStore(new IDBFactory());
  });

  afterEach(() => {
    closePages();
    setNoteStoreForTests(null);
    resetSessionForTests();
  });

  it("brings notes and the box's text back after a reload, and sends each once under its own id and token", async () => {
    const before = await leaveUnsavedNotes(store);
    expect(server.notes.size).toBe(1);
    const createsBefore = server.creates.length;
    closePages();

    const outbox = openPage(store);
    await vi.waitFor(() => expect(outbox.getSnapshot().draft).toEqual(DRAFT));
    await vi.waitFor(() => expect(outbox.getSnapshot().entries).toEqual([]));

    const resent = server.creates.slice(createsBefore);
    expect(resent.map((create) => [create.id, create.write_token])).toEqual(
      before.map((entry) => [entry.id, entry.writeToken]),
    );
    // The landed one came back as the note it made: no second note.
    expect(server.notes.size).toBe(2);
    expect(outbox.getSnapshot().saved.map((note) => note.id)).toEqual(before.map((entry) => entry.id));
    // Only the box's text is left to keep.
    await vi.waitFor(async () => expect(await storedNoteIds(store)).toEqual([]));
    expect((await stored(store))?.draft).toEqual(DRAFT);
  });

  it("removes the record once nothing is left unsaved", async () => {
    const outbox = openPage(store);
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await stored(store)).toBeDefined());

    outbox.setDraft({ ...DRAFT, text: "  \n " });
    await vi.waitFor(async () => expect(await stored(store)).toBeUndefined());
  });

  it("never shows or sends one account's notes to another", async () => {
    setNoteStoreForTests(store);
    await leaveUnsavedNotes(store);
    const creates = server.creates.length;
    closePages();

    signedIn("bob", BOB);
    await storedNotesSignedIn(BOB);
    const outbox = await openPageAndRead(store);

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(outbox.getSnapshot().draft.text).toBe("");
    expect(server.creates).toHaveLength(creates);
    expect(await stored(store, ALICE)).toBeUndefined();

    // Back as alice: what bob's sign-in removed stays gone.
    closePages();
    signedIn("alice", ALICE);
    await storedNotesSignedIn(ALICE);
    const again = await openPageAndRead(store);
    expect(again.getSnapshot().entries).toEqual([]);
    expect(again.getSnapshot().draft.text).toBe("");
    expect(server.creates).toHaveLength(creates);
  });

  it("keeps an account's notes through a rename, and sends them after it signs in under the new name", async () => {
    setNoteStoreForTests(store);
    const before = await leaveUnsavedNotes(store);
    closePages();

    // alice is renamed alice2 (by herself, or by an administrator) and signs in again.
    resetSessionForTests();
    signedIn("alice2", ALICE);
    await storedNotesSignedIn(ALICE);
    const outbox = openPage(store);

    await vi.waitFor(() => expect(outbox.getSnapshot().draft).toEqual(DRAFT));
    await vi.waitFor(() => expect(server.notes.has(before[0].id)).toBe(true));
    expect(server.notes.size).toBe(2);
  });

  it("stores nothing while the browser is signed in as another account, and writes afresh once the owner is back", async () => {
    setNoteStoreForTests(store);
    await storedNotesSignedIn(ALICE);
    const tab = await openPageAndRead(store);

    // Another tab signs in as bob; with nothing unsaved, nothing holds sign-in,
    // and this tab finds out from /users/me.
    signedIn("bob", BOB);
    await storedNotesSignedIn(BOB);
    tab.setDraft({ ...DRAFT, text: "bob's private text" });
    await settle();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await stored(store, ALICE)).toBeUndefined();
    expect(await stored(store, BOB)).toBeUndefined();

    // bob's reload finds nothing; alice's later sign-in finds nothing of bob's.
    closePages();
    const bobPage = await openPageAndRead(store);
    expect(bobPage.getSnapshot().draft.text).toBe("");
    closePages();
    resetSessionForTests();
    signedIn("alice", ALICE);
    await storedNotesSignedIn(ALICE);
    const alicePage = await openPageAndRead(store);
    expect(alicePage.getSnapshot().draft.text).toBe("");

    // The owner back on a page that stayed open: its text is written afresh.
    const owned = openPage(store);
    await vi.waitFor(() => expect(pages).toHaveLength(2));
    owned.setDraft(DRAFT);
    signedIn("bob", BOB);
    owned.setDraft({ ...DRAFT, text: "typed meanwhile" });
    signedIn("alice", ALICE);
    await vi.waitFor(async () =>
      expect((await stored(store, ALICE))?.draft).toEqual({ ...DRAFT, text: "typed meanwhile" }),
    );
  });

  it("stores nothing for the owner while another account is signed in, though that sign-in's cleanup failed", async () => {
    setNoteStoreForTests(store);
    await storedNotesSignedIn(ALICE);
    const tab = await openPageAndRead(store);
    vi.spyOn(store, "keepOnly").mockRejectedValue(new Error("refused"));

    signedIn("bob", BOB);
    await storedNotesSignedIn(BOB);
    tab.setDraft({ ...DRAFT, text: "typed while bob is signed in" });
    await settle();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await stored(store, ALICE)).toBeUndefined();

    signedIn("alice", ALICE);
    await vi.waitFor(async () =>
      expect((await stored(store, ALICE))?.draft).toEqual({ ...DRAFT, text: "typed while bob is signed in" }),
    );
  });

  it("reads nothing until the session says which account is signed in", async () => {
    await leaveUnsavedNotes(store);
    closePages();
    resetSessionForTests();
    const get = vi.spyOn(store, "get");

    const outbox = openPage(store);
    useSessionStore.setState({ username: "alice" });
    await settle();
    useSessionStore.setState({ signInRequired: true, userId: ALICE });
    await settle();
    expect(get).not.toHaveBeenCalled();
    expect(outbox.getSnapshot().entries).toEqual([]);

    signedIn("alice", ALICE);
    await vi.waitFor(() => expect(outbox.getSnapshot().draft).toEqual(DRAFT));
    expect(get).toHaveBeenCalledWith(ALICE, RECORDING);
  });

  it("restores nothing to a page that went away before its read finished", async () => {
    await leaveUnsavedNotes(store);
    const creates = server.creates.length;
    closePages();
    const get = vi.spyOn(store, "get");

    const outbox = openPage(store);
    closePages();
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    await get.mock.results[0].value;
    await settle();

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(server.creates).toHaveLength(creates);
  });

  it("writes the record again when its owner signs back in after another account removed it", async () => {
    const outbox = openPage(store);
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await stored(store)).toBeDefined());

    useSessionStore.setState({ signInRequired: true });
    await store.keepOnly([BOB]);
    signedIn("alice", ALICE);

    await vi.waitFor(async () => expect((await stored(store))?.draft).toEqual(DRAFT));
  });

  it("stores nothing after signing out, and writes again once the page signs back in", async () => {
    setNoteStoreForTests(store);
    const outbox = new NoteOutbox(RECORDING);
    pages.push(persistNoteOutbox(outbox, RECORDING));
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await stored(store)).toBeDefined());

    await clearStoredNotes();
    expect(await stored(store)).toBeUndefined();
    outbox.setDraft({ ...DRAFT, text: "after signing out" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(await stored(store)).toBeUndefined();

    // The leave prompt was cancelled; the user signs in on the page.
    await storedNotesSignedIn();
    await vi.waitFor(async () =>
      expect((await stored(store))?.draft).toEqual({ ...DRAFT, text: "after signing out" }),
    );
  });

  it("never keeps a sent note together with the text it was sent from", async () => {
    const { writes } = watchWrites(store);
    const outbox = await openPageAndRead(store);
    outbox.setDraft({ ...DRAFT, text: "Budget agreed", mode: "send" });
    await vi.waitFor(() => expect(writes).toHaveLength(1));

    outbox.submit({ kind: "point", body: "Budget agreed", anchorMs: 1_000 }, { clearDraft: true });
    expect(outbox.getSnapshot().draft).toEqual({ text: "", mode: "send", typingStartMs: null });
    await vi.waitFor(() => expect(server.notes.size).toBe(1));
    await vi.waitFor(async () => expect(await stored(store)).toBeUndefined());

    expect(writes.some((record) => record.notes.length > 0 && record.draft !== null)).toBe(false);
  });

  it("writes one record at a time, and an unchanged one not at all", async () => {
    const { writes, seen } = watchWrites(store);
    const outbox = await openPageAndRead(store);
    for (const text of ["H", "Ha", "Hal", "Half", "Half a"]) {
      outbox.setDraft({ ...DRAFT, text });
      await new Promise((resolve) => setTimeout(resolve, 2));
    }
    await vi.waitFor(async () => expect((await stored(store))?.draft).toMatchObject({ text: "Half a" }));
    expect(seen.most).toBe(1);

    const count = writes.length;
    outbox.setDraft({ ...DRAFT, text: "Half a" });
    outbox.setDraft({ ...DRAFT, text: "Half a" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(writes).toHaveLength(count);
  });

  it("does not make a note again that another device deleted after its create landed", async () => {
    const outbox = openPage(store);
    server.loseNextAnswer = true;
    outbox.submit({ kind: "point", body: "Landed unseen", anchorMs: 5_000 });
    await settle();
    const [entry] = outbox.getSnapshot().entries;
    await vi.waitFor(async () => expect(await storedNoteIds(store)).toEqual([entry.id]));
    closePages();

    server.deleteElsewhere(entry.id);
    const reloaded = openPage(store);

    await vi.waitFor(() =>
      expect(reloaded.getSnapshot().notice?.text).toBe(
        "Note at 00:05 was deleted on another device, so it was not added again.",
      ),
    );
    expect(reloaded.getSnapshot().entries).toEqual([]);
    expect(server.notes.size).toBe(0);
    await vi.waitFor(async () => expect(await stored(store)).toBeUndefined());
  });

  it("sends a note again under a new id once when the server has its id for another note", async () => {
    const outbox = openPage(store);
    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });
    await settle();
    const [entry] = outbox.getSnapshot().entries;
    await vi.waitFor(async () => expect(await storedNoteIds(store)).toEqual([entry.id]));
    closePages();

    server.failures.push(coded(409, "note_id_in_use"));
    const reloaded = openPage(store);

    await vi.waitFor(() => expect(server.notes.size).toBe(1));
    expect(reloaded.getSnapshot().entries).toEqual([]);
    const [, refused, fresh] = server.creates;
    expect(refused.id).toBe(entry.id);
    expect(fresh.id).not.toBe(entry.id);
    expect(fresh.write_token).not.toBe(entry.writeToken);
    expect([...server.notes.keys()]).toEqual([fresh.id]);
  });

  it("keeps a note's new id before sending it under that id", async () => {
    const memory = new MemoryNoteStore();
    const events: string[] = [];
    const put = memory.put.bind(memory);
    vi.spyOn(memory, "put").mockImplementation(async (record) => {
      await new Promise((resolve) => setTimeout(resolve, 10));
      await put(record);
      const ids = (record.notes as Array<{ id: string }>).map((note) => note.id).join(",");
      events.push(`kept ${ids} after ${server.creates.length} creates`);
    });
    createTimelineNote.mockImplementationOnce(async (_recording, payload) => {
      server.creates.push(payload);
      throw coded(409, "note_id_in_use");
    });
    const outbox = await openPageAndRead(memory);

    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });
    await vi.waitFor(() => expect(server.creates).toHaveLength(2));
    const fresh = server.creates[1].id!;
    // A kept copy had the new id before the create under it went out.
    expect(events).toContain(`kept ${fresh} after 1 creates`);
  });

  it("sends a discard the server never confirmed again after a reload", async () => {
    const outbox = openPage(store);
    server.loseNextAnswer = true;
    outbox.submit({ kind: "point", body: "Scrap this", anchorMs: 1_000 });
    await settle();
    const [entry] = outbox.getSnapshot().entries;
    server.failures.push(NO_ANSWER);
    outbox.discard(entry.key);
    await settle();
    await vi.waitFor(async () =>
      expect((await stored(store))?.discards).toEqual([{ id: entry.id, writeToken: entry.writeToken }]),
    );
    closePages();

    const reloaded = openPage(store);

    await vi.waitFor(() => expect(server.tombstones.get(entry.id)).toBe(entry.writeToken));
    expect(server.notes.size).toBe(0);
    expect(reloaded.getSnapshot().entries).toEqual([]);
    await vi.waitFor(async () => expect(await stored(store)).toBeUndefined());
  });

  it("keeps a listed note hidden after a reload while its discard is still on its way", async () => {
    const outbox = openPage(store);
    server.loseNextAnswer = true;
    outbox.submit({ kind: "point", body: "Scrap this", anchorMs: 1_000 });
    await settle();
    const [entry] = outbox.getSnapshot().entries;
    server.failures.push(NO_ANSWER);
    outbox.discard(entry.key);
    await vi.waitFor(async () => expect((await stored(store))?.discards).toHaveLength(1));
    closePages();

    // The delete fails again after the reload, and a listing still has the note.
    server.failures.push(NO_ANSWER);
    const reloaded = await openPageAndRead(store);
    await vi.waitFor(() => expect(deleteTimelineNote).toHaveBeenCalledTimes(2));
    const listed = [...server.notes.values()];
    expect(listed.map((note) => note.id)).toEqual([entry.id]);

    expect(shownNotes(listed, reloaded.getSnapshot())).toEqual([]);
  });

  it("leaves a note a listing already has, and the box's text typed since", async () => {
    const before = await leaveUnsavedNotes(store);
    closePages();

    const outbox = new NoteOutbox(RECORDING);
    outbox.ingest([...server.notes.values()]);
    outbox.setDraft({ ...DRAFT, text: "Typed first" });
    pages.push(persistNoteOutbox(outbox, RECORDING, () => store));

    await vi.waitFor(() => expect(server.notes.size).toBe(2));
    expect(outbox.getSnapshot().entries).toEqual([]);
    // The landed note is listed, so only the failed one is sent again.
    expect(server.creates.at(-1)?.id).toBe(before[0].id);
    expect(server.creates.filter((create) => create.id === before[1].id)).toHaveLength(1);
    expect(outbox.getSnapshot().draft.text).toBe("Typed first");
  });
});

describe("a note outbox where the browser keeps nothing", () => {
  beforeEach(() => {
    resetNotesServer();
    resetSessionForTests();
    signedIn("alice", ALICE);
  });

  afterEach(() => {
    closePages();
    resetSessionForTests();
  });

  it("works from memory where there is no IndexedDB", async () => {
    const outbox = openPage(null);
    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "point", body: "Budget agreed", anchorMs: 1 });
    await settle();

    expect(outbox.getSnapshot().entries[0].status).toBe("failed");
    outbox.retry(outbox.getSnapshot().entries[0].key);
    await vi.waitFor(() => expect(outbox.getSnapshot().entries).toEqual([]));
  });

  it("never writes over a record it could not read, and works from memory", async () => {
    const store = new MemoryNoteStore();
    const get = vi.spyOn(store, "get").mockRejectedValue(new DOMException("broken", "UnknownError"));
    const put = vi.spyOn(store, "put");
    const remove = vi.spyOn(store, "remove");

    const outbox = openPage(store);
    outbox.setDraft(DRAFT);
    outbox.submit({ kind: "point", body: "Budget agreed", anchorMs: 1 });
    await vi.waitFor(() => expect(outbox.getSnapshot().entries).toEqual([]));
    expect(get).toHaveBeenCalledTimes(1);
    outbox.setDraft({ ...DRAFT, text: "More" });
    await settle();

    expect(put).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
    expect(server.notes.size).toBe(1);
  });

  it("carries on when the browser refuses a write, and writes again on the next change", async () => {
    const store = new MemoryNoteStore();
    const put = vi
      .spyOn(store, "put")
      .mockRejectedValueOnce(new DOMException("full", "QuotaExceededError"));

    const outbox = openPage(store);
    outbox.setDraft(DRAFT);
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(await store.get(ALICE, RECORDING)).toBeUndefined();

    // Any change tries again, even one that leaves the record as it was.
    outbox.setDraft({ ...DRAFT });
    await vi.waitFor(async () =>
      expect(((await store.get(ALICE, RECORDING)) as StoredOutbox | undefined)?.draft).toEqual(DRAFT),
    );

    outbox.setDraft({ ...DRAFT, text: "Half a thought, then more" });
    await vi.waitFor(async () =>
      expect(((await store.get(ALICE, RECORDING)) as StoredOutbox | undefined)?.draft).toEqual({
        ...DRAFT,
        text: "Half a thought, then more",
      }),
    );
  });
});

describe("records of another version", () => {
  const NEWER = {
    version: NOTE_RECORD_VERSION + 1,
    account: ALICE,
    recordingId: RECORDING,
    shape: "whatever a later version writes",
  };
  const SELECTION = {
    id: "sel-1",
    writeToken: "sel-token",
    kind: "selection",
    body: "On this passage",
    startMs: 1_000,
    endMs: 4_000,
    quotedText: "the budget",
    createdAt: "2026-10-22T09:00:00.000Z",
  };
  const POINT = {
    id: "pt-1",
    writeToken: "pt-token",
    kind: "point",
    body: "Budget agreed",
    anchorMs: 61_250,
    createdAt: "2026-10-22T09:00:00.000Z",
  };
  const restoredOf = (read: ReturnType<typeof readStoredOutbox>) =>
    read && typeof read === "object" ? read.restored : read;

  beforeEach(() => {
    resetNotesServer();
    resetSessionForTests();
    signedIn("alice", ALICE);
  });

  afterEach(() => {
    closePages();
    resetSessionForTests();
  });

  it.each([
    ["a later version's", NEWER],
    [
      "an earlier version's",
      { ...NEWER, version: NOTE_RECORD_VERSION - 1, notes: [POINT], draft: null, discards: [] },
    ],
  ])("leaves %s record alone: nothing sent, nothing written over it", async (_name, raw) => {
    const store = new MemoryNoteStore();
    await store.put(raw as unknown as StoredOutbox);

    const put = vi.spyOn(store, "put");
    const remove = vi.spyOn(store, "remove");
    const outbox = await openPageAndRead(store);
    outbox.setDraft(DRAFT);
    await settle();

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(server.creates).toHaveLength(0);
    expect(put).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
    expect(await store.get(ALICE, RECORDING)).toEqual(raw);
  });

  it("upgrades an earlier version's record step by step, and reads the result", () => {
    const upgrades = {
      [NOTE_RECORD_VERSION - 2]: (record: Record<string, unknown>) => ({
        ...record,
        notes: [record.note],
      }),
      [NOTE_RECORD_VERSION - 1]: (record: Record<string, unknown>) => ({
        ...record,
        draft: { text: record.box, mode: "typing", typingStartMs: null },
      }),
    };

    const read = readStoredOutbox(
      { version: NOTE_RECORD_VERSION - 2, note: POINT, box: "Half" },
      upgrades,
    );

    expect(restoredOf(read)).toEqual({
      notes: [POINT],
      draft: { text: "Half", mode: "typing", typingStartMs: null },
      discards: [],
    });
    // A gap in the steps leaves the record for a version that can read it.
    expect(
      readStoredOutbox({ version: NOTE_RECORD_VERSION - 2 }, { [NOTE_RECORD_VERSION - 2]: (r) => r }),
    ).toBe("older");
  });

  it("keeps a note of a kind it can't send, and writes it back as it was", async () => {
    const store = new IndexedDbNoteStore(new IDBFactory());
    const selection = { ...SELECTION, start_ms: 1_000, future: { nested: [1, 2, 3] } };
    await store.put({
      version: NOTE_RECORD_VERSION,
      account: ALICE,
      recordingId: RECORDING,
      notes: [selection, POINT],
      draft: null,
      discards: [],
    });

    const outbox = openPage(store);
    await vi.waitFor(() => expect(server.notes.size).toBe(1));

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(server.creates.map((create) => create.id)).toEqual(["pt-1"]);
    await vi.waitFor(async () =>
      expect(((await store.get(ALICE, RECORDING)) as StoredOutbox).notes).toEqual([selection]),
    );
  });

  it.each([
    ["a later version", { version: NOTE_RECORD_VERSION + 1 }, "newer"],
    ["an earlier version with no upgrade", { version: NOTE_RECORD_VERSION - 1, notes: [POINT] }, "older"],
    ["a version that isn't a whole number", { version: 0.5, notes: [POINT] }, null],
    ["no version", { notes: [POINT] }, null],
    ["not a record", "corrupt", null],
    ["nothing", undefined, null],
  ])("reads %s as %s", (_name, raw, expected) => {
    expect(readStoredOutbox(raw)).toBe(expected);
  });

  it("drops what it can't read and keeps the rest", () => {
    const read = readStoredOutbox({
      version: NOTE_RECORD_VERSION,
      notes: [
        POINT,
        { ...POINT, id: "" },
        { ...POINT, id: "no-token", writeToken: 7 },
        { ...POINT, id: "no-anchor", anchorMs: null },
        { ...POINT, id: "negative", anchorMs: -1 },
        { ...POINT, id: "no-body", body: undefined },
        { ...POINT, id: "no-time", createdAt: 0 },
        { ...POINT, id: "no-kind", kind: undefined },
        { ...POINT, id: "head-1", kind: "head", anchorMs: 3 },
        "not a note",
      ],
      draft: { text: "Half", mode: "sideways", typingStartMs: "soon" },
      discards: [{ id: "d-1", writeToken: "d-token" }, { id: "d-2" }, null],
    });

    expect(restoredOf(read)).toEqual({
      notes: [POINT, { ...POINT, id: "head-1", kind: "head", anchorMs: null }],
      draft: { text: "Half", mode: "typing", typingStartMs: null },
      discards: [{ id: "d-1", writeToken: "d-token" }],
    });
  });

  it("reads a record with no draft and no discards", () => {
    const read = readStoredOutbox({ version: NOTE_RECORD_VERSION, notes: [POINT] });

    expect(restoredOf(read)).toEqual({ notes: [POINT], draft: null, discards: [] });
  });

  it("keeps each draft mode it knows", () => {
    for (const mode of ["typing", "send", "head", "tail"] as const) {
      const read = readStoredOutbox({
        version: NOTE_RECORD_VERSION,
        draft: { text: "x", mode, typingStartMs: 5 },
      });
      expect(restoredOf(read)).toMatchObject({ draft: { text: "x", mode, typingStartMs: 5 } });
    }
  });

  it("sends a restored note's discard to the server under its own token only", async () => {
    const store = new MemoryNoteStore();
    await store.put({
      version: NOTE_RECORD_VERSION,
      account: ALICE,
      recordingId: RECORDING,
      notes: [],
      draft: null,
      discards: [{ id: "d-1", writeToken: "d-token" }],
    });

    openPage(store);

    await vi.waitFor(() =>
      expect(deleteTimelineNote).toHaveBeenCalledWith(RECORDING, "d-1", { writeToken: "d-token" }),
    );
    expect(createTimelineNote).not.toHaveBeenCalled();
  });
});

describe("storing beside other tabs and sign-outs", () => {
  let store: NoteStore;
  let tabs: BroadcastChannel[] = [];

  /** Another tab of this browser, on the channel the tabs share. */
  const otherTab = () => {
    const channel = new BroadcastChannel("nojoin-notes");
    tabs.push(channel);
    return channel;
  };

  /** Opens the recording on a page writing to `storeOf()`. */
  const openStoringPage = (storeOf: () => NoteStore | null = () => store) => {
    const outbox = new NoteOutbox(RECORDING);
    pages.push(persistNoteOutbox(outbox, RECORDING, storeOf));
    return outbox;
  };

  const storedDraft = async () => ((await stored(store))?.draft as ComposerDraft | null) ?? null;

  /** Waits a little, for a write that should not happen. */
  const noWrite = () => new Promise((resolve) => setTimeout(resolve, 20));

  /** The next create is refused: its id is another note's. */
  const idInUseOnce = () =>
    createTimelineNote.mockImplementationOnce(async (_recording, payload) => {
      server.creates.push(payload);
      throw coded(409, "note_id_in_use");
    });

  beforeEach(() => {
    resetNotesServer();
    resetSessionForTests();
    signedIn("alice", ALICE);
    store = new MemoryNoteStore();
    setNoteStoreForTests(store);
  });

  afterEach(() => {
    closePages();
    for (const tab of tabs) tab.close();
    tabs = [];
    setNoteStoreForTests(null);
    resetSessionForTests();
  });

  it("stops storing when another tab signs out, until a sign-in is accepted here", async () => {
    // Whichever store the page was handed, not only the page's shared one.
    const outbox = openStoringPage();
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));

    // The other tab removed every record, and says so.
    await store.clear();
    otherTab().postMessage({ kind: "signed-out" });
    await vi.waitFor(() => expect(storingStopped()).toBe(true));
    outbox.setDraft({ ...DRAFT, text: "typed after the other tab signed out" });
    await noWrite();
    expect(await stored(store)).toBeUndefined();

    await storedNotesSignedIn(ALICE);
    await vi.waitFor(async () =>
      expect(await storedDraft()).toMatchObject({ text: "typed after the other tab signed out" }),
    );
  });

  it("stores again once another tab signs in as this tab's account after a sign-out", async () => {
    const outbox = openStoringPage(sharedNoteStore);
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));
    await store.clear();
    otherTab().postMessage({ kind: "signed-out" });
    await vi.waitFor(() => expect(storingStopped()).toBe(true));

    // The other tab signs back in as alice; its cleanup keeps alice.
    otherTab().postMessage({ kind: "kept", accounts: [ALICE] });

    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));
    expect(storingStopped()).toBe(false);
  });

  it("stays stopped when another tab signs in as another account after a sign-out", async () => {
    const outbox = openStoringPage(sharedNoteStore);
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));
    await store.clear();
    otherTab().postMessage({ kind: "signed-out" });
    otherTab().postMessage({ kind: "kept", accounts: [BOB] });
    await noWrite();
    await noWrite();

    expect(storingStopped()).toBe(true);
    expect(await stored(store)).toBeUndefined();
  });

  it("writes the outbox again after a sign-out cancelled at the leave prompt and a sign-in here, though nothing changed", async () => {
    const outbox = openStoringPage(sharedNoteStore);
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));

    await clearStoredNotes();
    expect(await stored(store)).toBeUndefined();
    // The dialog's sign-in is accepted; nothing was typed meanwhile.
    await storedNotesSignedIn(ALICE);

    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));
  });

  it("stops storing an account another tab's cleanup removed, until that account is back", async () => {
    const outbox = openStoringPage(sharedNoteStore);
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));

    // bob signed in in another tab, and its cleanup removed alice's record.
    await store.keepOnly([BOB]);
    otherTab().postMessage({ kind: "kept", accounts: [BOB] });
    await noWrite();
    outbox.setDraft({ ...DRAFT, text: "alice's, typed while bob is signed in" });
    await noWrite();
    expect(await stored(store)).toBeUndefined();

    // A cleanup that keeps alice: the browser is hers again.
    otherTab().postMessage({ kind: "kept", accounts: [ALICE] });
    await vi.waitFor(async () =>
      expect(await storedDraft()).toMatchObject({ text: "alice's, typed while bob is signed in" }),
    );
  });

  it("leaves the record alone when another tab's sign-in keeps this account", async () => {
    const outbox = openStoringPage();
    outbox.setDraft(DRAFT);
    await vi.waitFor(async () => expect(await storedDraft()).toEqual(DRAFT));
    // Another tab on the recording wrote last.
    const theirs = { ...DRAFT, text: "the other tab's text" };
    await store.put({
      version: NOTE_RECORD_VERSION,
      account: ALICE,
      recordingId: RECORDING,
      notes: [],
      draft: theirs,
      discards: [],
    });

    otherTab().postMessage({ kind: "kept", accounts: [ALICE] });
    await noWrite();
    await noWrite();

    expect(await storedDraft()).toEqual(theirs);
  });

  it("writes nothing once the page is stopped, though a write was under way", async () => {
    const put = store.put.bind(store);
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const putSpy = vi.spyOn(store, "put").mockImplementation(async (record) => {
      await held;
      await put(record);
    });
    const outbox = openStoringPage();
    await settle();
    outbox.setDraft(DRAFT);
    await vi.waitFor(() => expect(putSpy).toHaveBeenCalledTimes(1));
    outbox.setDraft({ ...DRAFT, text: "typed as the page went away" });

    pages.pop()?.();
    release();
    await noWrite();

    expect(putSpy).toHaveBeenCalledTimes(1);
    expect(await storedDraft()).toEqual(DRAFT);
  });

  it("no longer holds back a note's new id once the page is stopped", async () => {
    const put = store.put.bind(store);
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.spyOn(store, "put").mockImplementation(async (record) => {
      await held;
      await put(record);
    });
    const outbox = openStoringPage();
    await settle();
    idInUseOnce();
    // The first write, of the note under its first id, is under way when the
    // create is refused; the note takes a new id, which waits on that write.
    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });
    await vi.waitFor(() => expect(server.creates).toHaveLength(1));
    await settle();

    pages.pop()?.();
    release();

    await vi.waitFor(() => expect(server.notes.size).toBe(1));
    expect(outbox.getSnapshot().entries).toEqual([]);
  });

  it("sends a note under its new id at once where the browser keeps nothing", async () => {
    const outbox = openStoringPage(() => null);
    idInUseOnce();

    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });

    await vi.waitFor(() => expect(server.notes.size).toBe(1));
    expect(outbox.getSnapshot().entries).toEqual([]);
  });

  it.each([
    ["a sign-out stopped storing", () => clearStoredNotes()],
    [
      "another account's cleanup removed the record",
      async () => {
        await store.keepOnly([BOB]);
        otherTab().postMessage({ kind: "kept", accounts: [BOB] });
        await noWrite();
      },
    ],
  ])("sends a note under its new id once %s, as no stored copy has the old one", async (_name, lose) => {
    const outbox = openStoringPage(sharedNoteStore);
    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });
    await vi.waitFor(async () => expect(await storedNoteIds(store)).toHaveLength(1));
    const [entry] = outbox.getSnapshot().entries;
    await lose();
    expect(await stored(store)).toBeUndefined();

    idInUseOnce();
    outbox.retry(entry.key);

    await vi.waitFor(() => expect(server.notes.size).toBe(1));
    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(await stored(store)).toBeUndefined();
  });

  it("does not send a note under its new id while the browser refuses to keep that id", async () => {
    const outbox = openStoringPage(sharedNoteStore);
    await settle();
    const put = store.put.bind(store);
    let refusing = false;
    vi.spyOn(store, "put").mockImplementation(async (record) => {
      if (refusing) throw new DOMException("full", "QuotaExceededError");
      await put(record);
    });
    createTimelineNote.mockImplementationOnce(async (_recording, payload) => {
      server.creates.push(payload);
      // The record has the note under its first id; from now on the browser refuses.
      await vi.waitFor(async () => expect(await storedNoteIds(store)).toHaveLength(1));
      refusing = true;
      throw coded(409, "note_id_in_use");
    });
    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });

    await vi.waitFor(() =>
      expect(outbox.getSnapshot().entries[0]?.error).toBe(
        "Not saved: this browser couldn't keep a copy to send it safely.",
      ),
    );
    expect(server.creates).toHaveLength(1);
    const [entry] = outbox.getSnapshot().entries;
    expect(entry.id).not.toBe(server.creates[0].id);

    // Once the browser keeps it, Retry sends it under the new id.
    refusing = false;
    outbox.retry(entry.key);
    await vi.waitFor(() => expect(server.notes.size).toBe(1));
    expect(server.creates.map((create) => create.id)).toEqual([server.creates[0].id, entry.id]);
  });
});
