import { holdSignInInPlace, useSessionStore } from "@/lib/session";
import type { RecordingId } from "@/types";

import { NoteOutbox } from "./noteOutbox";
import { persistNoteOutbox } from "./notePersistence";
import {
  forgetStoredRecording,
  sharedNoteStore,
  subscribeStoreEvents,
  type StoreEvent,
} from "./noteStore";

/**
 * One outbox per recording for the life of the page, so a note on its way and
 * the box's text outlive every view of the recording. Each is also kept in
 * the browser where it can be (`persistNoteOutbox`), which covers a crash, a
 * killed tab or an ended session, never a page the user chose to leave.
 *
 * While any of them holds something unsent, the page asks before it closes,
 * whatever the browser has stored, and it holds sign-in in place as a capture
 * does (`@/lib/session`): an ended session opens the sign-in dialog rather
 * than reloading to the sign-in page, and the notes are sent once the owner
 * signs back in (issue #537).
 */

const outboxes = new Map<RecordingId, { outbox: NoteOutbox; stop: () => Promise<void> }>();

const anyUnsaved = () => [...outboxes.values()].some(({ outbox }) => outbox.hasUnsaved());

const warnBeforeUnload = (event: BeforeUnloadEvent) => {
  if (!anyUnsaved()) return;
  event.preventDefault();
  // Older browsers show the prompt only when this is set.
  event.returnValue = "";
};

let guarded = false;

// Listening only while something is unsaved: a page with a beforeunload
// listener can be kept out of the back/forward cache.
const guardUnsaved = () => {
  if (typeof window === "undefined") return;
  const needed = anyUnsaved();
  if (needed === guarded) return;
  guarded = needed;
  holdSignInInPlace(needed, "notes");
  if (needed) window.addEventListener("beforeunload", warnBeforeUnload);
  else window.removeEventListener("beforeunload", warnBeforeUnload);
};

/** The recordings this page holds an outbox for, and those of them with something unsent. */
export interface NoteOutboxesHere {
  held: readonly RecordingId[];
  unsent: readonly RecordingId[];
}

let here: NoteOutboxesHere = { held: [], unsent: [] };
const hereListeners = new Set<() => void>();

const sameIds = (a: readonly RecordingId[], b: readonly RecordingId[]) =>
  a.length === b.length && a.every((id, index) => id === b[index]);

const changed = () => {
  guardUnsaved();
  const held = [...outboxes.keys()];
  const unsent = [...outboxes]
    .filter(([, { outbox }]) => outbox.hasUnsaved() || outbox.pendingDiscards().length > 0)
    .map(([recordingId]) => recordingId);
  if (sameIds(held, here.held) && sameIds(unsent, here.unsent)) return;
  here = { held, unsent };
  for (const listener of hereListeners) listener();
};

/** Calls `listener` whenever `noteOutboxesHere` changes. */
export const subscribeNoteOutboxes = (listener: () => void): (() => void) => {
  hereListeners.add(listener);
  return () => hereListeners.delete(listener);
};

export const noteOutboxesHere = (): NoteOutboxesHere => here;

/**
 * Lets go of a recording's outbox, once a write of it already under way has
 * finished. Returns whether the page held one.
 */
const dropOutbox = async (recordingId: RecordingId): Promise<boolean> => {
  const held = outboxes.get(recordingId);
  if (!held) return false;
  outboxes.delete(recordingId);
  changed();
  await held.stop();
  return true;
};

// Another tab deleted a recording for good: this page drops its outbox for
// it, and removes the record again in case a write of its own landed after.
const onStoreEvent = (event: StoreEvent) => {
  if (event.kind !== "forgot") return;
  void dropOutbox(event.recordingId).then(async (held) => {
    const { userId } = useSessionStore.getState();
    const store = sharedNoteStore();
    if (!held || userId === null || !store) return;
    await store.remove(userId, event.recordingId).catch(() => undefined);
  });
};

let listening = false;

/** The outbox of a recording, made on first use. */
export const noteOutboxFor = (recordingId: RecordingId): NoteOutbox => {
  const known = outboxes.get(recordingId);
  if (known) return known.outbox;
  if (!listening) {
    listening = true;
    subscribeStoreEvents(onStoreEvent);
  }
  const outbox = new NoteOutbox(recordingId);
  const stopListening = outbox.subscribe(changed);
  const stopPersisting = persistNoteOutbox(outbox, recordingId);
  outboxes.set(recordingId, {
    outbox,
    stop: () => {
      stopListening();
      return stopPersisting();
    },
  });
  // `useNoteOutbox` makes it during a render, and React refuses to update
  // another component (the recordings rail) in the middle of one.
  queueMicrotask(changed);
  return outbox;
};

/**
 * Forgets the notes of a recording deleted for good (issue #610): this page's
 * outbox for it, the signed-in account's record of it, and every other tab's
 * outbox for it. None of them can be sent any more, so none of them is kept,
 * marked in the recordings list or allowed to hold the page.
 */
export const forgetRecordingNotes = async (recordingId: RecordingId): Promise<void> => {
  await dropOutbox(recordingId);
  await forgetStoredRecording(recordingId);
  // The recordings list reads the store again.
  here = { ...here };
  for (const listener of hereListeners) listener();
};

/** Tests: forget every recording's outbox. */
export const forgetNoteOutboxes = (): void => {
  for (const { stop } of outboxes.values()) void stop();
  outboxes.clear();
  changed();
};
