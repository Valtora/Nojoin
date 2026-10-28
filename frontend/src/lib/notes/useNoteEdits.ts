"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { deleteTimelineNote, updateTimelineNote } from "@/lib/api";
import { getErrorDetail, getErrorStatus, isRecord } from "@/lib/errors";
import type { RecordingId, TimelineNote, TranscriptSourceReference } from "@/types";

import { NOTE_BODY_MAX_CHARS, noteLength, stripNote } from "./headNoteSync";
import { NOTE_TOO_LONG_ERROR, noteSnippet } from "./noteOutbox";
import { useNoteOutbox } from "./useNoteOutbox";
import { NoteEditDrafts, type NoteEditAnswer } from "./noteEditDrafts";

/** What an edit reads of a note: a saved note has a revision. */
export interface EditableNote {
  id: string;
  body: string;
  revision?: number;
  /** Kept with a draft so its editor remains meaningful if a listing removes it. */
  kind?: TimelineNote["kind"];
  quoted_text?: string | null;
  /** A point note's moment, which a move changes. */
  anchor_ms?: number | null;
  start_ms?: number | null;
  end_ms?: number | null;
}

/** Why an edit or delete was refused, or null once it is done. */
export type NoteEditRefusal = string | null;

const NOT_SAVED_YET = "This note isn't saved yet.";
const DELETED_ELSEWHERE = "This note was deleted elsewhere.";
const NO_ANSWER = "No answer from Nojoin. Try again.";
const REFUSED = "Nojoin refused this change.";

/** A move on its way, shown at once; `seq` tells a later move of the same note from it. */
interface Move {
  anchorMs: number;
  seq: number;
}

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
  return status === null || status >= 500 ? NO_ANSWER : getErrorDetail(error) ?? REFUSED;
};

/**
 * Edits and deletes of a recording's saved notes in every recording state.
 * Body edits appear through the recording's shared outbox while awaiting an
 * answer. A refusal removes that preview and keeps the editor's draft open.
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
 *
 * A move of a point note (`move`) shows at once and settles on what Nojoin
 * answers: the moved note, or on a 409 the note as it is now, which puts it
 * back where it was unless another device moved it. Moves of one note go one
 * at a time, each against the revision the last one made. A refused move
 * takes the moves queued behind it with it, since they stepped on from a
 * place the note never reached.
 */
export function useNoteEdits(recordingId: RecordingId | null) {
  const { outbox, snapshot } = useNoteOutbox(recordingId);
  const [heldByRecording, setHeld] = useState<ReadonlyMap<RecordingId | null, ReadonlyMap<string, TimelineNote | null>>>(() => new Map());
  const held = useMemo(() => heldByRecording.get(recordingId) ?? new Map<string, TimelineNote | null>(), [heldByRecording, recordingId]);
  const hold = useCallback(
    (id: string, note: TimelineNote | null) => setHeld((previous) => {
      const notes = previous.get(recordingId) ?? new Map<string, TimelineNote | null>();
      const last = notes.get(id);
      if (last && note && last.revision > note.revision) return previous;
      return new Map(previous).set(recordingId, new Map(notes).set(id, note));
    }),
    [recordingId],
  );

  const [movingByRecording, setMoves] = useState<ReadonlyMap<RecordingId | null, ReadonlyMap<string, Move>>>(() => new Map());
  const moving = useMemo(() => movingByRecording.get(recordingId) ?? new Map<string, Move>(), [movingByRecording, recordingId]);
  const setMoving = useCallback((change: (previous: ReadonlyMap<string, Move>) => ReadonlyMap<string, Move>) => {
    setMoves((previous) => new Map(previous).set(recordingId, change(previous.get(recordingId) ?? new Map())));
  }, [recordingId]);
  const owners = useRef(new Map<RecordingId | null, {
    drafts: NoteEditDrafts; moveQueue: Map<string, Promise<unknown>>; answeredRevision: Map<string, number>;
    moveSeq: { current: number }; refusedMove: Map<string, { through: number; refusal: string }>;
  }>());
  const owner = useMemo(() => {
    let known = owners.current.get(recordingId);
    if (!known) {
      known = { drafts: new NoteEditDrafts(), moveQueue: new Map(), answeredRevision: new Map(),
        moveSeq: { current: 0 }, refusedMove: new Map() };
      owners.current.set(recordingId, known);
    }
    return known;
  }, [recordingId]);
  useEffect(() => {
    const retained = owners.current;
    for (const value of retained.values()) value.drafts.resume();
    return () => { for (const value of retained.values()) value.drafts.dispose(); };
  }, []);
  // Per note: the last move sent, and the revision the last answer carried.
  const { moveQueue, answeredRevision, moveSeq, refusedMove } = owner;
  // Per note: the last refusal, and the last move asked for when it came.

  const known = useCallback(
    <N extends EditableNote>(note: N): N[] => {
      if (!held.has(note.id)) return [note];
      const last = held.get(note.id);
      if (!last) return [];
      if (note.revision === undefined || note.revision >= last.revision) return [note];
      return [{ ...note, body: last.body, revision: last.revision, anchor_ms: last.anchor_ms,
        start_ms: last.start_ms, end_ms: last.end_ms }];
    },
    [held],
  );

  const apply = useCallback(
    <N extends EditableNote>(notes: readonly N[]): N[] =>
      notes.flatMap(known).map((note) => {
        const move = moving.get(note.id);
        const body = snapshot.bodyEdits?.get(note.id) ?? note.body;
        if (!move) return body === note.body ? note : { ...note, body };
        const delta = move.anchorMs - (note.anchor_ms ?? move.anchorMs);
        return { ...note, body, anchor_ms: move.anchorMs,
          ...(note.anchor_ms != null && note.start_ms != null && note.end_ms != null ? {
            start_ms: note.start_ms + delta, end_ms: note.end_ms + delta,
          } : {}) };
      }),
    [known, moving, snapshot.bodyEdits],
  );

  const edit = useCallback(
    async (note: EditableNote, text: string, answer?: (result: NoteEditAnswer) => void): Promise<NoteEditRefusal> => {
      const body = stripNote(text);
      if (note.revision === undefined || recordingId === null || outbox === null) return NOT_SAVED_YET;
      if (noteLength(body) > NOTE_BODY_MAX_CHARS) return NOTE_TOO_LONG_ERROR;
      // Explicit Save asks the server even for the opening text: a previous
      // unanswered write may have landed with a different body.
      const finish = outbox.beginBodyEdit(note.id, body);
      try {
        const saved = await updateTimelineNote(recordingId, note.id, {
          expected_revision: note.revision,
          body,
        });
        hold(note.id, saved);
        answer?.({ note: saved, conflict: false });
        return null;
      } catch (error) {
        const current = conflictNote(error);
        if (current) {
          hold(note.id, current);
          answer?.({ note: current, conflict: true });
          return current.body === body ? null : changedElsewhere(current, "Save again to keep yours.");
        }
        return isGone(error) ? DELETED_ELSEWHERE : otherFailure(error);
      } finally {
        finish();
      }
    },
    [recordingId, hold, outbox],
  );

  const remove = useCallback(
    async (note: EditableNote, answer?: (result: NoteEditAnswer) => void): Promise<NoteEditRefusal> => {
      if (note.revision === undefined || recordingId === null) return NOT_SAVED_YET;
      try {
        await deleteTimelineNote(recordingId, note.id, { expectedRevision: note.revision });
      } catch (error) {
        const current = conflictNote(error);
        if (current) {
          hold(note.id, current);
          answer?.({ note: current, conflict: true });
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

  const move = useCallback(
    (note: EditableNote, anchorMs: number, target?: TranscriptSourceReference): Promise<NoteEditRefusal> => {
      const revision = note.revision;
      if (revision === undefined || recordingId === null) return Promise.resolve(NOT_SAVED_YET);
      const seq = ++moveSeq.current;
      setMoving((previous) => new Map(previous).set(note.id, { anchorMs, seq }));
      const send = async (): Promise<NoteEditRefusal> => {
        const expected = Math.max(revision, answeredRevision.get(note.id) ?? 0);
        const settle = (answer: TimelineNote) => {
          answeredRevision.set(note.id, answer.revision);
          hold(note.id, answer);
        };
        const refuse = (refusal: string) => {
          refusedMove.set(note.id, { through: moveSeq.current, refusal });
          return refusal;
        };
        try {
          // Asked for before an earlier move was refused: it goes with that one.
          const refused = refusedMove.get(note.id);
          if (refused && seq <= refused.through) return refused.refusal;
          settle(await updateTimelineNote(recordingId, note.id, {
            expected_revision: expected,
            anchor_ms: anchorMs,
            ...(target ? { target_utterance_id: target.id, target_utterance_revision: target.revision } : {}),
          }));
          return null;
        } catch (error) {
          const current = conflictNote(error);
          if (current) {
            settle(current);
            return current.anchor_ms === anchorMs ? null : refuse(changedElsewhere(current, "Move it again if you still want it there."));
          }
          return refuse(isGone(error) ? DELETED_ELSEWHERE : otherFailure(error));
        } finally {
          setMoving((previous) => {
            if (previous.get(note.id)?.seq !== seq) return previous;
            const next = new Map(previous);
            next.delete(note.id);
            return next;
          });
        }
      };
      const sent = (moveQueue.get(note.id) ?? Promise.resolve()).then(send);
      moveQueue.set(note.id, sent);
      return sent;
    },
    [recordingId, hold, setMoving, answeredRevision, moveQueue, moveSeq, refusedMove],
  );

  return { apply, edit, remove, move, drafts: owner.drafts };
}

/** A recording's note edits, shared by the views that show its notes. */
export type NoteEdits = ReturnType<typeof useNoteEdits>;
