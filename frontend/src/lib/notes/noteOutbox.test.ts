import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TimelineNote, TimelineNoteCreate } from "@/types";

import { NOTE_BODY_MAX_CHARS } from "./headNoteSync";
import { inlineNotes, NoteOutbox, SLOW_SAVE_MS } from "./noteOutbox";

const RECORDING = "rec-1";

/**
 * A fake of the notes routes as the outbox meets them: a create under a known
 * id returns the note it made (200) rather than a second one; a delete under
 * a token removes the note or, for an id no note holds, leaves a tombstone a
 * later create of that id is refused by (410). `loseNextAnswer` commits a
 * write and then throws as a dropped connection does; `failNext` refuses one
 * before it runs.
 */
const server = {
  notes: new Map<string, TimelineNote>(),
  tombstones: new Set<string>(),
  creates: [] as TimelineNoteCreate[],
  loseNextAnswer: false,
  failNext: null as unknown,
};

const NO_ANSWER = { isAxiosError: true, message: "Network Error" };
const coded = (status: number, code: string) => ({
  isAxiosError: true,
  response: { status, data: { detail: { code, message: code } } },
});

const takeFailure = () => {
  const failure = server.failNext;
  server.failNext = null;
  return failure;
};

const createTimelineNote = vi.fn(async (_recording: string, payload: TimelineNoteCreate) => {
  server.creates.push(payload);
  const failure = takeFailure();
  if (failure) throw failure;
  const id = payload.id!;
  if (server.tombstones.has(id)) throw coded(410, "note_deleted_by_you");
  const existing = server.notes.get(id);
  const note: TimelineNote = existing ?? {
    id,
    recording_id: RECORDING,
    kind: payload.kind,
    body: payload.body,
    anchor_ms: payload.anchor_ms ?? null,
    revision: 1,
    last_write_token: payload.write_token ?? null,
    created_at: "2026-10-21T10:00:00",
    updated_at: "2026-10-21T10:00:00",
  };
  server.notes.set(id, note);
  if (server.loseNextAnswer) {
    server.loseNextAnswer = false;
    throw NO_ANSWER;
  }
  return note;
});

const deleteTimelineNote = vi.fn<
  (recording: string, id: string, conditions?: { writeToken?: string }) => Promise<void>
>(async (_recording, id) => {
  const failure = takeFailure();
  if (failure) throw failure;
  if (!server.notes.delete(id)) server.tombstones.add(id);
});

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createTimelineNote: (recording: string, payload: TimelineNoteCreate) =>
    createTimelineNote(recording, payload),
  deleteTimelineNote: (recording: string, id: string, conditions?: { writeToken?: string }) =>
    deleteTimelineNote(recording, id, conditions),
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
    server.failNext = null;
    createTimelineNote.mockClear();
    deleteTimelineNote.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps a note whose create failed, with its anchor, and retries the very same create", async () => {
    const outbox = new NoteOutbox(RECORDING);
    server.failNext = NO_ANSWER;

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
    server.failNext = NO_ANSWER;
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

    server.failNext = NO_ANSWER;
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
    server.failNext = coded(409, "note_id_in_use");

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
    server.failNext = coded(404, "recording_not_found");

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
