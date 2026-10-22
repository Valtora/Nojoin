import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  holdSignInInPlace,
  noteSignedIn,
  resetSessionForTests,
  useSessionStore,
} from "@/lib/session";
import {
  coded,
  createTimelineNote,
  deleteTimelineNote,
  NO_ANSWER,
  server,
} from "@/test/fakeNotesServer";
import type { TimelineNote } from "@/types";

import { NOTE_BODY_MAX_CHARS } from "./headNoteSync";
import { inlineNotes, noteFromDraft, NoteOutbox, SLOW_SAVE_MS } from "./noteOutbox";
import { forgetNoteOutboxes } from "./noteOutboxes";

const RECORDING = "rec-1";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
}));

const flush = () => vi.waitFor(() => Promise.resolve());

const settle = async () => {
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
};

describe("NoteOutbox", () => {
  beforeEach(() => {
    server.notes.clear();
    server.tombstones.clear();
    server.creates = [];
    server.loseNextAnswer = false;
    server.failures = [];
    forgetNoteOutboxes();
    createTimelineNote.mockClear();
    deleteTimelineNote.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keys a restored note's line by its id before and after it saves, so the line is not remounted", async () => {
    const outbox = new NoteOutbox(RECORDING);
    outbox.restore({
      notes: [
        {
          id: "n-1",
          writeToken: "t-1",
          kind: "point",
          body: "Restored",
          anchorMs: 10,
          createdAt: "2026-10-22T10:00:00Z",
        },
      ],
      draft: null,
      discards: [],
    });
    const before = inlineNotes([], outbox.getSnapshot()).map((note) => note.key);
    await vi.waitFor(() => expect(outbox.getSnapshot().entries).toHaveLength(0));
    const after = inlineNotes([], outbox.getSnapshot()).map((note) => note.key);

    // A changed key would remount the line, and focus on its Retry or Discard would be lost.
    expect(before).toEqual(["n-1"]);
    expect(after).toEqual(["n-1"]);
  });

  it("keeps a note whose create failed, with its anchor, and retries the very same create", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(NO_ANSWER);

    expect(outbox.submit({ kind: "point", body: "  Budget agreed  ", anchorMs: 61_250 })).toBeNull();
    await settle();

    const [failed] = outbox.getSnapshot().entries;
    expect(failed).toMatchObject({
      status: "failed",
      body: "Budget agreed",
      anchorMs: 61_250,
      error: "Not saved: no answer from Nojoin.",
    });
    expect(server.notes.size).toBe(0);

    outbox.retry(failed.key);
    await settle();

    expect(server.creates).toHaveLength(2);
    expect(server.creates[1]).toEqual(server.creates[0]);
    expect(server.creates[1]).toEqual({
      id: failed.id,
      write_token: failed.writeToken,
      kind: "point",
      body: "Budget agreed",
      anchor_ms: 61_250,
    });
    const snapshot = outbox.getSnapshot();
    expect(snapshot.entries).toEqual([]);
    expect(snapshot.saved.map((note) => note.id)).toEqual([failed.id]);
  });

  it("saves a note once when the create landed and only its answer was lost", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.loseNextAnswer = true;

    outbox.submit({ kind: "point", body: "Action: send the deck", anchorMs: 5_000 });
    await settle();
    const [failed] = outbox.getSnapshot().entries;
    expect(failed.status).toBe("failed");

    outbox.retry(failed.key);
    await settle();

    expect(server.notes.size).toBe(1);
    const shown = inlineNotes([], outbox.getSnapshot());
    expect(shown.map((note) => [note.id, note.status])).toEqual([[failed.id, "saved"]]);

    // The next poll lists it: it is shown from the poll alone, once.
    outbox.ingest([...server.notes.values()]);
    expect(outbox.getSnapshot().saved).toEqual([]);
    expect(inlineNotes([...server.notes.values()], outbox.getSnapshot())).toHaveLength(1);
  });

  it("clears a failed note that a poll shows saved, without sending it again", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.loseNextAnswer = true;

    outbox.submit({ kind: "point", body: "Lost answer", anchorMs: 1_000 });
    await settle();
    expect(outbox.getSnapshot().entries).toHaveLength(1);

    outbox.ingest([...server.notes.values()]);

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(server.creates).toHaveLength(1);
  });

  it("offers Retry once a create goes unanswered, and still saves on its late answer", async () => {
    vi.useFakeTimers();
    const outbox = new NoteOutbox(RECORDING);
    let answer: (note: TimelineNote) => void = () => undefined;
    createTimelineNote.mockImplementationOnce(
      (_recording, payload) =>
        new Promise<TimelineNote>((resolve) => {
          server.creates.push(payload);
          answer = resolve;
        }),
    );

    outbox.submit({ kind: "point", body: "Slow network", anchorMs: 2_000 });
    expect(outbox.getSnapshot().entries[0].status).toBe("saving");

    await vi.advanceTimersByTimeAsync(SLOW_SAVE_MS);
    const [waiting] = outbox.getSnapshot().entries;
    expect(waiting).toMatchObject({ status: "failed", error: "No answer from Nojoin yet." });

    answer({
      id: waiting.id,
      recording_id: RECORDING,
      kind: "point",
      body: "Slow network",
      anchor_ms: 2_000,
      revision: 1,
      created_at: "2026-10-21T10:00:00",
      updated_at: "2026-10-21T10:00:00",
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(outbox.getSnapshot().saved.map((note) => note.id)).toEqual([waiting.id]);
  });

  it("discards a failed note with a delete under its token, so a create still on its way can't land", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "point", body: "Never mind", anchorMs: 3_000 });
    await settle();
    const [failed] = outbox.getSnapshot().entries;

    outbox.discard(failed.key);
    await settle();

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(deleteTimelineNote).toHaveBeenCalledWith(RECORDING, failed.id, {
      writeToken: failed.writeToken,
    });
    // The create arriving after the delete is refused by its tombstone.
    await expect(
      createTimelineNote(RECORDING, server.creates[0]),
    ).rejects.toMatchObject({ response: { status: 410 } });
  });

  it("hides a discarded note a poll still lists, and deletes it again", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.loseNextAnswer = true;
    outbox.submit({ kind: "point", body: "Landed after all", anchorMs: 4_000 });
    await settle();
    const [failed] = outbox.getSnapshot().entries;

    server.failures.push(NO_ANSWER);
    outbox.discard(failed.key);
    await settle();
    const listed = [...server.notes.values()];
    expect(listed.map((note) => note.id)).toEqual([failed.id]);

    outbox.ingest(listed);
    expect(inlineNotes(listed, outbox.getSnapshot())).toEqual([]);
    await settle();

    expect(deleteTimelineNote).toHaveBeenCalledTimes(2);
    expect(server.notes.size).toBe(0);
  });

  it("sends a note again under a new id when the server can't take its id", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(coded(409, "note_id_in_use"));

    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });
    await settle();
    await flush();

    expect(server.creates).toHaveLength(2);
    expect(server.creates[1].id).not.toBe(server.creates[0].id);
    expect(server.creates[1].write_token).not.toBe(server.creates[0].write_token);
    expect(server.creates[1]).toMatchObject({ kind: "tail", body: "Wrap-up" });
    expect(server.creates[1]).not.toHaveProperty("anchor_ms");
    expect(outbox.getSnapshot().entries).toEqual([]);
  });

  it("says why a refused create failed", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(coded(404, "recording_not_found"));

    outbox.submit({ kind: "point", body: "Too late", anchorMs: 1 });
    await settle();

    expect(outbox.getSnapshot().entries[0]).toMatchObject({
      status: "failed",
      error: "Not saved: this recording no longer exists.",
    });
  });

  it("refuses an empty or too long note before sending it", () => {
    const outbox = new NoteOutbox(RECORDING);

    expect(outbox.submit({ kind: "point", body: " \n ", anchorMs: 0 })).not.toBeNull();
    expect(
      outbox.submit({ kind: "point", body: "x".repeat(NOTE_BODY_MAX_CHARS + 1), anchorMs: 0 }),
    ).toMatch(/at most/);
    expect(createTimelineNote).not.toHaveBeenCalled();
    expect(outbox.getSnapshot().entries).toEqual([]);
  });
});

describe("NoteOutbox: notes that can't be made again", () => {
  beforeEach(() => {
    server.notes.clear();
    server.tombstones.clear();
    server.creates = [];
    server.loseNextAnswer = false;
    server.failures = [];
    createTimelineNote.mockClear();
    deleteTimelineNote.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("never makes a note again that another device deleted, and says so", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.loseNextAnswer = true;
    outbox.submit({ kind: "point", body: "Landed unseen", anchorMs: 5_000 });
    await settle();
    const [failed] = outbox.getSnapshot().entries;
    // The desktop sees the note this phone never heard back about, and deletes it.
    server.deleteElsewhere(failed.id);

    outbox.retry(failed.key);
    await settle();

    expect(server.creates).toHaveLength(2);
    expect(server.creates[1].id).toBe(failed.id);
    expect(server.notes.size).toBe(0);
    const snapshot = outbox.getSnapshot();
    expect(snapshot.entries).toEqual([]);
    expect(snapshot.saved).toEqual([]);
    expect(snapshot.notice).toMatchObject({
      tone: "info",
      text: "Note at 00:05 was deleted on another device, so it was not added again.",
    });
  });

  it("drops a note quietly when its own delete got there first", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(coded(410, "note_deleted_by_you"));
    outbox.submit({ kind: "head", body: "Agenda", anchorMs: null });
    await settle();

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(outbox.getSnapshot().notice).toBeNull();
    expect(server.creates).toHaveLength(1);
  });

  it("tries a new id only once when the server can't take the note's id", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(coded(409, "note_id_in_use"), coded(409, "note_id_in_use"));

    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });
    await settle();
    await flush();

    expect(server.creates).toHaveLength(2);
    expect(outbox.getSnapshot().entries[0]).toMatchObject({
      status: "failed",
      error: "Not saved: Nojoin refused this note.",
    });
  });

  it("does not show a discarded note when its create's answer arrives after the delete's", async () => {
    vi.useFakeTimers();
    let answer: () => void = () => undefined;
    // The create commits at once, but its answer is held back (a slow proxy,
    // a dispatch blocking after the commit, a mobile link).
    createTimelineNote.mockImplementationOnce(async (_recording, payload) => {
      server.creates.push(payload);
      const note: TimelineNote = {
        id: payload.id!,
        recording_id: RECORDING,
        kind: payload.kind,
        body: payload.body,
        anchor_ms: payload.anchor_ms ?? null,
        revision: 1,
        created_at: "2026-10-21T10:00:00",
        updated_at: "2026-10-21T10:00:00",
      };
      server.notes.set(note.id, note);
      await new Promise<void>((resolve) => {
        answer = resolve;
      });
      return note;
    });
    const outbox = new NoteOutbox(RECORDING);
    outbox.submit({ kind: "point", body: "Never mind", anchorMs: 3_000 });
    await vi.advanceTimersByTimeAsync(SLOW_SAVE_MS);
    const [waiting] = outbox.getSnapshot().entries;
    expect(waiting.status).toBe("failed");

    outbox.discard(waiting.key); // Its delete is answered first: the note is gone.
    await vi.advanceTimersByTimeAsync(0);
    expect(server.notes.size).toBe(0);

    answer(); // Then the create's 201 arrives.
    await vi.advanceTimersByTimeAsync(0);
    outbox.ingest([...server.notes.values()]);

    expect(outbox.getSnapshot().saved).toEqual([]);
    expect(inlineNotes([...server.notes.values()], outbox.getSnapshot())).toEqual([]);
  });

  it("sends nothing under a new id once an earlier send saved the note while the kept copy was checked", async () => {
    vi.useFakeTimers();
    let answer!: () => void;
    // The first create commits, but its answer is held back.
    createTimelineNote.mockImplementationOnce(async (_recording, payload) => {
      server.creates.push(payload);
      const note: TimelineNote = {
        id: payload.id!,
        recording_id: RECORDING,
        kind: payload.kind,
        body: payload.body,
        anchor_ms: payload.anchor_ms ?? null,
        revision: 1,
        created_at: "2026-10-22T10:00:00",
        updated_at: "2026-10-22T10:00:00",
      };
      server.notes.set(note.id, note);
      await new Promise<void>((resolve) => {
        answer = resolve;
      });
      return note;
    });
    const outbox = new NoteOutbox(RECORDING);
    let checked!: (safe: boolean) => void;
    outbox.setWriteBarrier(
      () =>
        new Promise<boolean>((resolve) => {
          checked = resolve;
        }),
    );
    outbox.submit({ kind: "point", body: "Once", anchorMs: 3_000 });
    await vi.advanceTimersByTimeAsync(SLOW_SAVE_MS);
    const [waiting] = outbox.getSnapshot().entries;
    expect(waiting.status).toBe("failed");

    // Retry is refused for its id: the note takes a new one, and waits on the kept copy.
    server.failures.push(coded(409, "note_id_in_use"));
    outbox.retry(waiting.key);
    await vi.advanceTimersByTimeAsync(0);
    expect(outbox.getSnapshot().entries[0].id).not.toBe(waiting.id);

    answer(); // The first create's 201 arrives: the note is saved.
    await vi.advanceTimersByTimeAsync(0);
    checked(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(server.notes.size).toBe(1);
  });

  it("offers Retry for a note held up on the kept copy, as for one held up on the network", async () => {
    vi.useFakeTimers();
    const outbox = new NoteOutbox(RECORDING);
    let barrier = () => new Promise<boolean>(() => undefined);
    outbox.setWriteBarrier(() => barrier());
    server.failures.push(coded(409, "note_id_in_use"));
    outbox.submit({ kind: "tail", body: "Wrap-up", anchorMs: null });

    await vi.advanceTimersByTimeAsync(SLOW_SAVE_MS);
    const [held] = outbox.getSnapshot().entries;
    expect(held).toMatchObject({ status: "failed", error: "No answer from Nojoin yet." });

    barrier = async () => true;
    outbox.retry(held.key);
    await vi.advanceTimersByTimeAsync(0);
    expect(outbox.getSnapshot().entries).toEqual([]);
    expect(server.notes.size).toBe(1);
  });

  it("names the note a failure is about, for the note box to say", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "point", body: "Budget", anchorMs: 3_723_000 });
    await settle();

    expect(outbox.getSnapshot().notice).toMatchObject({
      tone: "error",
      text: "Note at 1:02:03 not saved.",
    });
  });

  it("tells a page that does not poll when one of its notes was saved or deleted", async () => {
    const outbox = new NoteOutbox(RECORDING);
    const wrote = vi.fn();
    outbox.subscribeWrites(wrote);
    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "point", body: "One", anchorMs: 1 });
    await settle();
    expect(wrote).not.toHaveBeenCalled();

    outbox.retry(outbox.getSnapshot().entries[0].key);
    await settle();
    expect(wrote).toHaveBeenCalledTimes(1);

    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "point", body: "Two", anchorMs: 2 });
    await settle();
    outbox.discard(outbox.getSnapshot().entries[0].key);
    await settle();
    expect(wrote).toHaveBeenCalledTimes(2);
  });
});

describe("NoteOutbox: a sign-in that ends during a capture", () => {
  // While a capture holds sign-in in place (#518), the page stays put on a
  // 401 and a dialog asks the recording's owner to sign back in.
  const SESSION_ENDED = {
    isAxiosError: true,
    response: { status: 401, data: { detail: "Not authenticated" } },
  };
  const signedIn = (username: string) =>
    noteSignedIn({ username, force_password_change: false });

  beforeEach(() => {
    server.notes.clear();
    server.tombstones.clear();
    server.creates = [];
    server.loseNextAnswer = false;
    server.failures = [];
    createTimelineNote.mockClear();
    deleteTimelineNote.mockClear();
    resetSessionForTests();
    signedIn("alice");
    holdSignInInPlace(true);
  });

  afterEach(() => {
    resetSessionForTests();
  });

  it("saves a note refused for an ended session once its owner signs back in, never as another account", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(SESSION_ENDED);

    outbox.submit({ kind: "point", body: "Budget agreed", anchorMs: 61_250 });
    await settle();

    const [waiting] = outbox.getSnapshot().entries;
    expect(waiting).toMatchObject({
      status: "failed",
      error: "Not saved yet: it saves once you sign in again.",
    });
    expect(useSessionStore.getState().signInRequired).toBe(true);

    // The browser is now signed in as someone else: nothing goes under them.
    signedIn("bob");
    await settle();
    expect(server.creates).toHaveLength(1);

    signedIn("alice");
    await vi.waitFor(() => expect(outbox.getSnapshot().entries).toEqual([]));
    expect(server.creates).toHaveLength(2);
    expect(server.creates[1]).toEqual(server.creates[0]);
    expect(server.notes.size).toBe(1);
    expect(outbox.getSnapshot().saved.map((note) => note.id)).toEqual([waiting.id]);
  });

  it("sends a discard refused for an ended session again once its owner signs back in", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(NO_ANSWER);
    outbox.submit({ kind: "point", body: "Scrap this", anchorMs: 1_000 });
    await settle();
    const [failed] = outbox.getSnapshot().entries;

    server.failures.push(SESSION_ENDED);
    outbox.discard(failed.key);
    await settle();
    expect(deleteTimelineNote).toHaveBeenCalledTimes(1);

    signedIn("alice");
    await vi.waitFor(() => expect(deleteTimelineNote).toHaveBeenCalledTimes(2));
    await settle();
    expect(server.tombstones.get(failed.id)).toBe(failed.writeToken);
    expect(outbox.getSnapshot().discarded.size).toBe(0);
  });

  it("fails as before when no capture holds sign-in", async () => {
    holdSignInInPlace(false);
    const outbox = new NoteOutbox(RECORDING);
    server.failures.push(SESSION_ENDED);

    outbox.submit({ kind: "point", body: "Budget agreed", anchorMs: 61_250 });
    await settle();

    expect(outbox.getSnapshot().entries[0]).toMatchObject({
      status: "failed",
      error: "Not saved: Nojoin refused this note.",
    });
  });
});

describe("noteFromDraft", () => {
  const now = vi.fn(() => 9_000);

  it("anchors at typing start, or where the recording is at send, and meeting-wide notes nowhere", () => {
    expect(
      noteFromDraft({ text: "A", mode: "typing", typingStartMs: 1_000 }, now),
    ).toEqual({ kind: "point", body: "A", anchorMs: 1_000 });
    expect(noteFromDraft({ text: "B", mode: "send", typingStartMs: 1_000 }, now)).toEqual({
      kind: "point",
      body: "B",
      anchorMs: 9_000,
    });
    expect(noteFromDraft({ text: "C", mode: "tail", typingStartMs: 1_000 }, now)).toEqual({
      kind: "tail",
      body: "C",
      anchorMs: null,
    });
  });
});

describe("inlineNotes", () => {
  it("places point and selection notes only, saved ones once, and outbox ones with their state", () => {
    const listed: TimelineNote[] = [
      {
        id: "head",
        recording_id: RECORDING,
        kind: "head",
        body: "Agenda",
        revision: 1,
        created_at: "2026-10-21T10:00:00",
        updated_at: "2026-10-21T10:00:00",
      },
      {
        id: "point",
        recording_id: RECORDING,
        kind: "point",
        body: "Saved",
        anchor_ms: 1_000,
        revision: 1,
        created_at: "2026-10-21T10:00:00",
        updated_at: "2026-10-21T10:00:00",
      },
    ];
    const shown = inlineNotes(listed, {
      entries: [
        {
          key: "k1",
          id: "pending",
          writeToken: "t1",
          kind: "point",
          body: "On its way",
          anchorMs: 2_000,
          createdAt: "2026-10-21T10:00:01Z",
          status: "saving",
          error: null,
        },
        {
          key: "k2",
          id: "tail",
          writeToken: "t2",
          kind: "tail",
          body: "Listed by the box",
          anchorMs: null,
          createdAt: "2026-10-21T10:00:01Z",
          status: "failed",
          error: "Not saved.",
        },
      ],
      saved: [listed[1]],
      discarded: new Set(),
    });

    expect(shown.map((note) => [note.id, note.status])).toEqual([
      ["point", "saved"],
      ["pending", "saving"],
    ]);
  });
});
