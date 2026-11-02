"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { useSessionStore } from "@/lib/session";
import type { Recording, RecordingId } from "@/types";

import { EMPTY_DRAFT, type NoteOutbox, type OutboxSnapshot } from "./noteOutbox";
import { noteOutboxesHere, noteOutboxFor, subscribeNoteOutboxes } from "./noteOutboxes";
import { sharedNoteStore } from "./noteStore";
import { useTimelineNotes } from "./useTimelineNotes";

const EMPTY_SNAPSHOT: OutboxSnapshot = {
  entries: [],
  saved: [],
  discarded: new Set(),
  draft: EMPTY_DRAFT,
  draftFile: null,
  uploads: new Map(),
  notice: null,
};
const subscribeToNothing = () => () => undefined;
const emptySnapshot = () => EMPTY_SNAPSHOT;
const selectAll = (snapshot: OutboxSnapshot) => snapshot;

/** The fields read by note lists, independent of the composer's draft. */
export const selectNoteList = ({ entries, saved, discarded }: OutboxSnapshot) => ({ entries, saved, discarded });
export const selectSavedNotes = ({ saved, discarded }: OutboxSnapshot) => ({ saved, discarded });
const selectOutboxActions = () => null;
export const selectComposer = ({ draft, draftFile, notice }: OutboxSnapshot) => ({ draft, draftFile, notice });
export const selectUnsentNotes = ({ entries, uploads, notice, draft, draftFile }: OutboxSnapshot) =>
  ({ entries, uploads, notice, draft, draftFile });
export const selectRecovery = (snapshot: OutboxSnapshot): ReturnType<typeof selectUnsentNotes> =>
  ({ ...selectUnsentNotes(snapshot), draft: EMPTY_DRAFT, draftFile: null });

function sameSelection<T>(left: T, right: T): boolean {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Object.getPrototypeOf(left) !== Object.prototype || Object.getPrototypeOf(right) !== Object.prototype) return false;
  const keys = Object.keys(right) as (keyof T)[];
  return keys.length === Object.keys(left).length && keys.every((key) => Object.is(left[key], right[key]));
}

function selectedSnapshot<T>(read: () => OutboxSnapshot, select: (snapshot: OutboxSnapshot) => T) {
  let selected = select(read());
  return () => {
    const next = select(read());
    if (!sameSelection(selected, next)) selected = next;
    return selected;
  };
}

/**
 * A recording's note outbox and its state (see `NoteOutbox`). Every view of
 * the recording gets the same one, so a note still on its way and the note
 * box's text outlive the view that made them.
 * Select immutable values or a shallow record of the fields a view displays;
 * equal selections keep their identity and do not schedule a render.
 */
export function useNoteOutbox<T = OutboxSnapshot>(
  recordingId: RecordingId, select?: (snapshot: OutboxSnapshot) => T,
): {
  outbox: NoteOutbox;
  snapshot: T;
};
export function useNoteOutbox<T = OutboxSnapshot>(
  recordingId: RecordingId | null, select?: (snapshot: OutboxSnapshot) => T,
): {
  outbox: NoteOutbox | null;
  snapshot: T;
};
export function useNoteOutbox<T>(recordingId: RecordingId | null, select?: (snapshot: OutboxSnapshot) => T) {
  const outbox = recordingId ? noteOutboxFor(recordingId) : null;
  // Cache the selection inside the subscription, so an unrelated publication
  // never schedules a render. The cache belongs to this outbox and selector.
  const getSnapshot = useMemo(() => selectedSnapshot<T | OutboxSnapshot>(
    outbox?.getSnapshot ?? emptySnapshot, select ?? selectAll,
  ), [outbox, select]);
  const snapshot = useSyncExternalStore(
    outbox?.subscribe ?? subscribeToNothing,
    getSnapshot,
    getSnapshot,
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
  const { outbox } = useNoteOutbox(recordingId, selectOutboxActions);
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

/**
 * The recordings with notes, discards or box text not saved yet in this
 * browser, for the signed-in account (issue #558). A recording this page holds
 * an outbox for goes by that outbox; any other by the record the browser kept
 * for it, read when the page loads and again whenever this page's outboxes
 * change. Nothing is sent from here: opening the recording does that.
 */
export function useUnsentRecordings(): readonly RecordingId[] {
  const outboxes = useSyncExternalStore(subscribeNoteOutboxes, noteOutboxesHere, noteOutboxesHere);
  const account = useSessionStore((state) => state.userId);
  const [stored, setStored] = useState<{ account: number; recordings: RecordingId[] } | null>(null);

  useEffect(() => {
    const store = account === null ? null : sharedNoteStore();
    if (account === null || !store) return;
    let current = true;
    store.recordingsOf(account).then(
      (recordings) => {
        if (current) setStored({ account, recordings });
      },
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [account, outboxes]);

  return useMemo(() => {
    const kept = stored !== null && stored.account === account ? stored.recordings : [];
    return [...kept.filter((id) => !outboxes.held.includes(id)), ...outboxes.unsent];
  }, [stored, account, outboxes]);
}
