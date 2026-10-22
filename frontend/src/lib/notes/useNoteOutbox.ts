"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";

import type { Recording, RecordingId } from "@/types";

import { EMPTY_DRAFT, type NoteOutbox, type OutboxSnapshot } from "./noteOutbox";
import {
  goneNotes,
  noteOutboxFor,
  subscribeNoteOutboxes,
  unsentRecordings,
  type GoneNotes,
} from "./noteOutboxes";
import { useTimelineNotes } from "./useTimelineNotes";

const EMPTY_SNAPSHOT: OutboxSnapshot = {
  entries: [],
  saved: [],
  discarded: new Set(),
  draft: EMPTY_DRAFT,
  notice: null,
};
const subscribeToNothing = () => () => undefined;
const emptySnapshot = () => EMPTY_SNAPSHOT;

/**
 * A recording's note outbox and its state (see `NoteOutbox`). Every view of
 * the recording gets the same one, so a note still on its way and the note
 * box's text outlive the view that made them.
 */
export function useNoteOutbox(recordingId: RecordingId): {
  outbox: NoteOutbox;
  snapshot: OutboxSnapshot;
};
export function useNoteOutbox(recordingId: RecordingId | null): {
  outbox: NoteOutbox | null;
  snapshot: OutboxSnapshot;
};
export function useNoteOutbox(recordingId: RecordingId | null) {
  const outbox = recordingId ? noteOutboxFor(recordingId) : null;
  const snapshot = useSyncExternalStore(
    outbox?.subscribe ?? subscribeToNothing,
    outbox?.getSnapshot ?? emptySnapshot,
    outbox?.getSnapshot ?? emptySnapshot,
  );
  return { outbox, snapshot };
}

/**
 * The recording page's notes: the list (`useTimelineNotes`), handed to the
 * outbox as it arrives, and fetched again after the outbox saves or deletes a
 * note while nothing polls the recording to say so.
 */
export function useRecordingNotes(
  recording: Pick<Recording, "id" | "timeline_notes_list_signature"> | null,
  polled: boolean,
) {
  const recordingId = recording?.id ?? null;
  const { notes, refresh } = useTimelineNotes(
    recordingId,
    recording?.timeline_notes_list_signature,
  );
  const { outbox } = useNoteOutbox(recordingId);
  const polledRef = useRef(polled);

  useEffect(() => {
    polledRef.current = polled;
  }, [polled]);

  useEffect(() => {
    if (outbox && notes) outbox.ingest(notes);
  }, [outbox, notes]);

  useEffect(() => {
    if (!outbox) return;
    const stop = outbox.subscribeWrites(() => {
      if (!polledRef.current) refresh();
    });
    return () => {
      stop();
    };
  }, [outbox, refresh]);

  return notes;
}

/** The recordings whose notes, discards or box text this page has not saved yet. */
export function useUnsentRecordings(): readonly RecordingId[] {
  return useSyncExternalStore(subscribeNoteOutboxes, unsentRecordings, unsentRecordings);
}

/** The notes of recordings found gone, waiting to be copied or dismissed. */
export function useGoneNotes(): readonly GoneNotes[] {
  return useSyncExternalStore(subscribeNoteOutboxes, goneNotes, goneNotes);
}
