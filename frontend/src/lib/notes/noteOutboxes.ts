import { holdSignInInPlace } from "@/lib/session";
import type { RecordingId } from "@/types";

import { NoteOutbox } from "./noteOutbox";
import { persistNoteOutbox } from "./notePersistence";

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

const outboxes = new Map<RecordingId, { outbox: NoteOutbox; stop: () => void }>();

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

/** The outbox of a recording, made on first use. */
export const noteOutboxFor = (recordingId: RecordingId): NoteOutbox => {
  const known = outboxes.get(recordingId);
  if (known) return known.outbox;
  const outbox = new NoteOutbox(recordingId);
  outbox.subscribe(changed);
  outboxes.set(recordingId, { outbox, stop: persistNoteOutbox(outbox, recordingId) });
  // `useNoteOutbox` makes it during a render, and React refuses to update
  // another component (the recordings rail) in the middle of one.
  queueMicrotask(changed);
  return outbox;
};

/** Tests: forget every recording's outbox. */
export const forgetNoteOutboxes = (): void => {
  for (const { stop } of outboxes.values()) stop();
  outboxes.clear();
  changed();
};
