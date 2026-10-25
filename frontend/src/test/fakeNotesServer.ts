import { vi } from "vitest";

import type { NoteAttachment, TimelineNote, TimelineNoteCreate } from "@/types";

/**
 * A fake of the notes routes as the note outbox meets them: a create under a
 * known id returns the note it made (200) rather than a second one; a delete
 * leaves a tombstone holding its token, and a later create of that id is
 * refused by it (410), saying whether the delete was this writer's.
 * `loseNextAnswer` commits a write and then throws as a dropped connection
 * does; each of `failures` refuses one request before it runs.
 *
 * A test file routes `@/lib/api` here:
 *
 *     vi.mock("@/lib/api", async (importOriginal) => ({
 *       ...(await importOriginal<typeof import("@/lib/api")>()),
 *       ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
 *     }));
 */
export const server = {
  notes: new Map<string, TimelineNote>(),
  tombstones: new Map<string, string | undefined>(),
  creates: [] as TimelineNoteCreate[],
  loseNextAnswer: false,
  failures: [] as unknown[],
  /** Each note's file, by note id; and refusals for the next uploads only. */
  files: new Map<string, string>(),
  uploadFailures: [] as unknown[],
  /** Another device deletes a note it can see. */
  deleteElsewhere(id: string) {
    this.notes.delete(id);
    this.tombstones.set(id, "another-device");
  },
};

export const NO_ANSWER = { isAxiosError: true, message: "Network Error" };

export const coded = (status: number, code: string) => ({
  isAxiosError: true,
  response: { status, data: { detail: { code, message: code } } },
});

const takeFailure = () => server.failures.shift() ?? null;

export const createTimelineNote = vi.fn(
  async (recording: string, payload: TimelineNoteCreate) => {
    server.creates.push(payload);
    const failure = takeFailure();
    if (failure) throw failure;
    const id = payload.id!;
    if (server.tombstones.has(id)) {
      const yours = server.tombstones.get(id) === payload.write_token;
      throw coded(410, yours ? "note_deleted_by_you" : "note_deleted_elsewhere");
    }
    const existing = server.notes.get(id);
    const note: TimelineNote = existing ?? {
      id,
      recording_id: recording,
      kind: payload.kind,
      body: payload.body,
      anchor_ms: payload.anchor_ms ?? null,
      start_ms: payload.start_ms ?? null,
      end_ms: payload.end_ms ?? null,
      quoted_text: payload.quoted_text ?? null,
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
  },
);

export const deleteTimelineNote = vi.fn<
  (recording: string, id: string, conditions?: { writeToken?: string }) => Promise<void>
>(async (_recording, id, conditions) => {
  const failure = takeFailure();
  if (failure) throw failure;
  server.notes.delete(id);
  server.tombstones.set(id, conditions?.writeToken);
});

export const attachFileToNote = vi.fn(
  async (_recording: string, noteId: string, file: File): Promise<NoteAttachment> => {
    const failure = server.uploadFailures.shift();
    if (failure) throw failure;
    if (!server.notes.has(noteId)) throw coded(404, "note_not_found");
    if (server.files.has(noteId)) throw coded(409, "attachment_exists");
    server.files.set(noteId, file.name);
    return {
      id: server.files.size,
      title: file.name,
      file_type: file.type,
      status: "PENDING",
      use_in_notes: false,
      readable: true,
      is_image: file.type.startsWith("image/"),
    };
  },
);

export const fakeNotesApi = {
  attachFileToNote: (recording: string, noteId: string, file: File) =>
    attachFileToNote(recording, noteId, file),
  createTimelineNote: (recording: string, payload: TimelineNoteCreate) =>
    createTimelineNote(recording, payload),
  deleteTimelineNote: (recording: string, id: string, conditions?: { writeToken?: string }) =>
    deleteTimelineNote(recording, id, conditions),
};

/** Empties the server and forgets every call. */
export const resetNotesServer = (): void => {
  server.notes.clear();
  server.tombstones.clear();
  server.creates = [];
  server.loseNextAnswer = false;
  server.failures = [];
  server.files.clear();
  server.uploadFailures = [];
  createTimelineNote.mockClear();
  deleteTimelineNote.mockClear();
  attachFileToNote.mockClear();
};
