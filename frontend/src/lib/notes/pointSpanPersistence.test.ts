import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { noteSignedIn, resetSessionForTests } from "@/lib/session";
import { NO_ANSWER, resetNotesServer, server } from "@/test/fakeNotesServer";
import { NoteOutbox, type ComposerDraft } from "./noteOutbox";
import { persistNoteOutbox } from "./notePersistence";
import { MemoryNoteStore, type StoredOutbox } from "./noteStore";

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
const stored = async () => await store.get(1, "rec-1") as StoredOutbox | undefined;
beforeEach(async () => {
  resetNotesServer(); resetSessionForTests(); await store.clear();
  noteSignedIn({ username: "alice", id: 1, force_password_change: false });
});
afterEach(async () => { await stop?.(); resetSessionForTests(); });

it("restores a line placement draft and retries a point span unchanged after reload", async () => {
  const outbox = await open();
  server.failures.push(NO_ANSWER);
  outbox.submit({ kind: "point", body: "Keep span", anchorMs: 1200, span: { startMs: 1200, endMs: 4500 } });
  const draft: ComposerDraft = { text: "Half a thought", mode: "typing", typingStartMs: 1000,
    lineSpan: { startMs: 6000, endMs: 9000 } };
  outbox.setDraft(draft);
  await vi.waitFor(async () => expect((await stored())?.draft).toEqual(draft));
  await stop?.();
  const restored = await open();
  await vi.waitFor(() => expect(server.creates).toHaveLength(2));
  expect(server.creates[1]).toEqual(server.creates[0]);
  expect(restored.getSnapshot().draft).toEqual(draft);
  expect(server.creates[1]).toMatchObject({ kind: "point", start_ms: 1200, end_ms: 4500 });
});

it("retains selected word identities with the original source after reload", async () => {
  const outbox = await open();
  server.failures.push(NO_ANSWER);
  outbox.submit({ kind: "selection", body: "Locked words", anchorMs: null, selection: {
    startMs: 1200, endMs: 4500, quotedText: "chosen words",
    sourceUtterances: [{ id: "u1", revision: 7, word_ids: ["w1", "w2"] }],
  } });
  await vi.waitFor(async () => expect((await stored())?.notes).toHaveLength(1));
  await stop?.();
  await open();
  await vi.waitFor(() => expect(server.creates).toHaveLength(2));
  expect(server.creates[1]).toEqual(server.creates[0]);
  expect(server.creates[1]).toMatchObject({ source_utterances: [{ id: "u1", revision: 7, word_ids: ["w1", "w2"] }] });
});
