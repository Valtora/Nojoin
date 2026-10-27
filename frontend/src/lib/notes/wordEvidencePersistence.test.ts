import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { noteSignedIn, resetSessionForTests } from "@/lib/session";
import { NO_ANSWER, resetNotesServer, server } from "@/test/fakeNotesServer";
import { NoteOutbox, type ComposerDraft } from "./noteOutbox";
import { persistNoteOutbox } from "./notePersistence";
import { MemoryNoteStore, type StoredOutbox } from "./noteStore";
import { selectionFromPicks } from "./selectionRange";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
}));

const store = new MemoryNoteStore();
let stop: (() => Promise<void>) | undefined;

const open = async () => {
  const outbox = new NoteOutbox("rec-1");
  const get = vi.spyOn(store, "get");
  stop = persistNoteOutbox(outbox, "rec-1", () => store);
  await vi.waitFor(() => expect(get).toHaveBeenCalled());
  await get.mock.results[0].value;
  get.mockRestore();
  return outbox;
};

beforeEach(async () => {
  resetNotesServer();
  resetSessionForTests();
  await store.clear();
  noteSignedIn({ username: "alice", id: 1, force_password_change: false });
});
afterEach(async () => {
  await stop?.();
  resetSessionForTests();
});

it("restores complete captured wording in the composer and retries the same partial quote after reload", async () => {
  const selection = selectionFromPicks([{ from: 1, to: 3, line: {
    segmentId: "u1", revision: 7, text: "cat", startMs: 1200, endMs: 4500,
    words: [{ id: "w1", text: "cat", from: 0, to: 3, startMs: 1200, endMs: 4500, locked: true }],
  } }])!;
  const outbox = await open();
  server.failures.push(NO_ANSWER);
  outbox.submit({ kind: "selection", body: "Partial quote", anchorMs: null, selection });
  const draft: ComposerDraft = { text: "Another thought", mode: "typing", typingStartMs: null, selection };
  outbox.setDraft(draft);
  await vi.waitFor(async () => {
    const stored = await store.get(1, "rec-1") as StoredOutbox | undefined;
    expect(stored?.notes).toHaveLength(1);
    expect(stored?.draft).toEqual(draft);
  });
  await stop?.();

  const restored = await open();
  await vi.waitFor(() => expect(server.creates).toHaveLength(2));

  expect(server.creates[1]).toEqual(server.creates[0]);
  expect(server.creates[1]).toMatchObject({
    quoted_text: "at", start_ms: 1200, end_ms: 4500,
    source_utterances: [{ id: "u1", revision: 7, word_ids: ["w1"], word_texts: { w1: "cat" } }],
  });
  expect(restored.getSnapshot().draft).toEqual(draft);
});
