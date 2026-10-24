"use client";

import { useCallback, useState } from "react";

import { deleteTimelineNote, updateTimelineNote } from "@/lib/api";
import { getErrorStatus, isRecord } from "@/lib/errors";
import type { RecordingId, TimelineNote } from "@/types";

import { NOTE_BODY_MAX_CHARS, noteLength, stripNote } from "./headNoteSync";
import { NOTE_TOO_LONG_ERROR, noteSnippet } from "./noteOutbox";

/** What an edit reads of a note: a saved note has a revision. */
export interface EditableNote {
  id: string;
  body: string;
  revision?: number;
}

/** Why an edit or delete was refused, or null once it is done. */
export type NoteEditRefusal = string | null;

const NOT_SAVED_YET = "This note isn't saved yet.";
const DELETED_ELSEWHERE = "This note was deleted elsewhere.";
const NO_ANSWER = "No answer from Nojoin. Try again.";
const REFUSED = "Nojoin refused this change.";

const changedElsewhere = (note: TimelineNote, then: string) =>
  `This note was changed elsewhere. It now reads “${noteSnippet(note.body, 80)}”. ${then}`;

/** The note a 409 carries: the note as it is now on the server. */
const conflictNote = (error: unknown): TimelineNote | null => {
  if (getErrorStatus(error) !== 409 || !isRecord(error) || !isRecord(error.response)) return null;
  const data = error.response.data;
  if (!isRecord(data) || !isRecord(data.detail) || data.detail.code !== "revision_conflict") return null;
  return isRecord(data.detail.note) ? (data.detail.note as unknown as TimelineNote) : null;
};

const isGone = (error: unknown) => {
  const status = getErrorStatus(error);
  return status === 404 || status === 410;
};

const otherFailure = (error: unknown): string => {
  const status = getErrorStatus(error);
  return status === null || status >= 500 ? NO_ANSWER : REFUSED;
};

/**
 * Edits and deletes of a recording's saved notes from the finished view.
 *
 * Each write names the revision it was made against, so another device's
 * change since is a 409 rather than overwritten; the 409 carries the note as
 * it is now, which is shown from then on, and the editor keeps the text typed
 * so a second Save replaces it knowingly. A write whose answer was lost and is
 * sent again finds its own change there, and counts as done.
 *
 * A note's last known state is held here, over the listed notes (`apply`),
 * since a finished recording isn't polled and its list isn't fetched again: a
 * listing at a later revision wins, and a deleted note stays hidden. The
 * recording page holds one, which its transcript and its My notes list share,
 * so an edit made in either shows in both.
 */
export function useNoteEdits(recordingId: RecordingId | null) {
  const [held, setHeld] = useState<ReadonlyMap<string, TimelineNote | null>>(() => new Map());
  const hold = useCallback(
    (id: string, note: TimelineNote | null) => setHeld((previous) => new Map(previous).set(id, note)),
    [],
  );

  const apply = useCallback(
    <N extends EditableNote>(notes: readonly N[]): N[] =>
      notes.flatMap((note) => {
        if (!held.has(note.id)) return [note];
        const known = held.get(note.id);
        if (!known) return [];
        if (note.revision === undefined || note.revision >= known.revision) return [note];
        return [{ ...note, body: known.body, revision: known.revision }];
      }),
    [held],
  );

  const edit = useCallback(
    async (note: EditableNote, text: string): Promise<NoteEditRefusal> => {
      const body = stripNote(text);
      if (note.revision === undefined || recordingId === null) return NOT_SAVED_YET;
      if (noteLength(body) > NOTE_BODY_MAX_CHARS) return NOTE_TOO_LONG_ERROR;
      if (body === note.body) return null;
      try {
        hold(note.id, await updateTimelineNote(recordingId, note.id, {
          expected_revision: note.revision,
          body,
        }));
        return null;
      } catch (error) {
        const current = conflictNote(error);
        if (current) {
          hold(note.id, current);
          return current.body === body ? null : changedElsewhere(current, "Save again to keep yours.");
        }
        return isGone(error) ? DELETED_ELSEWHERE : otherFailure(error);
      }
    },
    [recordingId, hold],
  );

  const remove = useCallback(
    async (note: EditableNote): Promise<NoteEditRefusal> => {
      if (note.revision === undefined || recordingId === null) return NOT_SAVED_YET;
      try {
        await deleteTimelineNote(recordingId, note.id, { expectedRevision: note.revision });
      } catch (error) {
        const current = conflictNote(error);
        if (current) {
          hold(note.id, current);
          return changedElsewhere(current, "Delete again to remove it anyway.");
        }
        // Gone already: deleted elsewhere, or by this delete's lost answer.
        if (!isGone(error)) return otherFailure(error);
      }
      hold(note.id, null);
      return null;
    },
    [recordingId, hold],
  );

  return { apply, edit, remove };
}

/** A recording's note edits, shared by the views that show its notes. */
export type NoteEdits = ReturnType<typeof useNoteEdits>;
