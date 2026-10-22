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

import { NoteOutbox, type ComposerDraft } from "./noteOutbox";
import { persistNoteOutbox, readStoredOutbox } from "./notePersistence";
import {
  IndexedDbNoteStore,
  keepOnlyNotesOf,
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
  type NoteStore,
  type StoredOutbox,
} from "./noteStore";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
}));

const RECORDING = "rec-1";
const DRAFT: ComposerDraft = { text: "Half a thought", mode: "typing", typingStartMs: 70_000 };

const signedIn = (username: string) => noteSignedIn({ username, force_password_change: false });

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

const stored = async (store: NoteStore, account = "alice") =>
  (await store.get(account, RECORDING)) as StoredOutbox | undefined;

const storedNoteIds = async (store: NoteStore, account = "alice") =>
  ((await stored(store, account))?.notes ?? []).map((note) => (note as { id: string }).id);

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
    signedIn("alice");
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

    signedIn("bob");
    await keepOnlyNotesOf("bob");
    const outbox = await openPageAndRead(store);

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(outbox.getSnapshot().draft.text).toBe("");
    expect(server.creates).toHaveLength(creates);
    expect(await stored(store, "alice")).toBeUndefined();

    // Back as alice: what bob's sign-in removed stays gone.
    closePages();
    signedIn("alice");
    const again = await openPageAndRead(store);
    expect(again.getSnapshot().entries).toEqual([]);
    expect(again.getSnapshot().draft.text).toBe("");
    expect(server.creates).toHaveLength(creates);
  });

  it("reads nothing until the session says who is signed in", async () => {
    await leaveUnsavedNotes(store);
    closePages();
    resetSessionForTests();
    const get = vi.spyOn(store, "get");

    const outbox = openPage(store);
    useSessionStore.setState({ signInRequired: true, username: "alice" });
    await settle();
    expect(get).not.toHaveBeenCalled();
    expect(outbox.getSnapshot().entries).toEqual([]);

    signedIn("alice");
    await vi.waitFor(() => expect(outbox.getSnapshot().draft).toEqual(DRAFT));
    expect(get).toHaveBeenCalledWith("alice", RECORDING);
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
    await store.keepOnly("bob");
    signedIn("alice");

    await vi.waitFor(async () => expect((await stored(store))?.draft).toEqual(DRAFT));
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
    signedIn("alice");
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
    expect(await store.get("alice", RECORDING)).toBeUndefined();

    // Any change tries again, even one that leaves the record as it was.
    outbox.setDraft({ ...DRAFT });
    await vi.waitFor(async () =>
      expect(((await store.get("alice", RECORDING)) as StoredOutbox | undefined)?.draft).toEqual(DRAFT),
    );

    outbox.setDraft({ ...DRAFT, text: "Half a thought, then more" });
    await vi.waitFor(async () =>
      expect(((await store.get("alice", RECORDING)) as StoredOutbox | undefined)?.draft).toEqual({
        ...DRAFT,
        text: "Half a thought, then more",
      }),
    );
  });
});

describe("records of another version", () => {
  const NEWER = {
    version: NOTE_RECORD_VERSION + 1,
    account: "alice",
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

  beforeEach(() => {
    resetNotesServer();
    resetSessionForTests();
    signedIn("alice");
  });

  afterEach(() => {
    closePages();
    resetSessionForTests();
  });

  it("leaves a later version's record alone, and never writes over it", async () => {
    const store = new MemoryNoteStore();
    await store.put(NEWER as unknown as StoredOutbox);

    const put = vi.spyOn(store, "put");
    const outbox = await openPageAndRead(store);
    outbox.setDraft(DRAFT);
    await settle();

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(put).not.toHaveBeenCalled();
    expect(await store.get("alice", RECORDING)).toEqual(NEWER);
  });

  it("keeps a note of a kind it can't send, and writes it back as it was", async () => {
    const store = new MemoryNoteStore();
    await store.put({
      version: NOTE_RECORD_VERSION,
      account: "alice",
      recordingId: RECORDING,
      notes: [SELECTION, POINT],
      draft: null,
      discards: [],
    });

    const outbox = openPage(store);
    await vi.waitFor(() => expect(server.notes.size).toBe(1));

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(server.creates.map((create) => create.id)).toEqual(["pt-1"]);
    await vi.waitFor(async () =>
      expect(((await store.get("alice", RECORDING)) as StoredOutbox).notes).toEqual([SELECTION]),
    );
  });

  it.each([
    ["a later version", { version: NOTE_RECORD_VERSION + 1 }, "newer"],
    ["an earlier version", { version: NOTE_RECORD_VERSION - 1, notes: [POINT] }, null],
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

    expect(read).not.toBe("newer");
    expect(read && read !== "newer" ? read.restored : null).toEqual({
      notes: [POINT, { ...POINT, id: "head-1", kind: "head", anchorMs: null }],
      draft: { text: "Half", mode: "typing", typingStartMs: null },
      discards: [{ id: "d-1", writeToken: "d-token" }],
    });
  });

  it("reads a record with no draft and no discards", () => {
    const read = readStoredOutbox({ version: NOTE_RECORD_VERSION, notes: [POINT] });

    expect(read && read !== "newer" ? read.restored : null).toEqual({
      notes: [POINT],
      draft: null,
      discards: [],
    });
  });

  it("keeps each draft mode it knows", () => {
    for (const mode of ["typing", "send", "head", "tail"] as const) {
      const read = readStoredOutbox({
        version: NOTE_RECORD_VERSION,
        draft: { text: "x", mode, typingStartMs: 5 },
      });
      expect(read && read !== "newer" ? read.restored.draft : null).toEqual({
        text: "x",
        mode,
        typingStartMs: 5,
      });
    }
  });

  it("sends a restored note's discard to the server under its own token only", async () => {
    const store = new MemoryNoteStore();
    await store.put({
      version: NOTE_RECORD_VERSION,
      account: "alice",
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
