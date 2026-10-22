import { holdSignInInPlace } from "@/lib/session";
import type { RecordingId } from "@/types";

import { NoteOutbox } from "./noteOutbox";
import { persistNoteOutbox, type PersistedOutbox } from "./notePersistence";

/**
 * One outbox per recording for the life of the page, so a note on its way and
 * the box's text outlive every view of the recording, each kept in the
 * browser where it can be so they outlive a reload too (`persistNoteOutbox`).
 *
 * While any of them holds something unsaved, the page asks before it closes,
 * and it holds sign-in in place as a capture does (`@/lib/session`): an ended
 * session opens the sign-in dialog rather than reloading to the sign-in page,
 * and the notes are sent once the owner signs back in (issue #537).
 */

const outboxes = new Map<RecordingId, { outbox: NoteOutbox; persisted: PersistedOutbox }>();
const keptListeners = new Set<() => void>();

const anyUnsaved = () => [...outboxes.values()].some(({ outbox }) => outbox.hasUnsaved());

/** Whether the browser holds everything unsaved on this page, so a reload brings it back. */
export const allUnsavedKept = (): boolean =>
  [...outboxes.values()].every(({ outbox, persisted }) => !outbox.hasUnsaved() || persisted.kept());

/** Calls `listener` whenever `allUnsavedKept()` may have changed. */
export const subscribeUnsavedKept = (listener: () => void): (() => void) => {
  keptListeners.add(listener);
  return () => keptListeners.delete(listener);
};

let guarded = false;
let holding = false;
/** Set when the user leaves for the sign-in page from the dialog. */
let released = false;

/** Asks before the page closes, unless it is leaving for the sign-in page with everything kept. */
const needsLeavePrompt = () => anyUnsaved() && !(released && allUnsavedKept());

const warnBeforeUnload = (event: BeforeUnloadEvent) => {
  if (!needsLeavePrompt()) return;
  event.preventDefault();
  // Older browsers show the prompt only when this is set.
  event.returnValue = "";
};

// Listening only while something is unsaved: a page with a beforeunload
// listener can be kept out of the back/forward cache.
const guardUnsaved = () => {
  if (typeof window === "undefined") return;
  const hold = anyUnsaved() && !released;
  if (hold !== holding) {
    holding = hold;
    holdSignInInPlace(hold, "notes");
  }
  const needed = needsLeavePrompt();
  if (needed === guarded) return;
  guarded = needed;
  if (needed) window.addEventListener("beforeunload", warnBeforeUnload);
  else window.removeEventListener("beforeunload", warnBeforeUnload);
};

const keptChanged = () => {
  guardUnsaved();
  for (const listener of keptListeners) listener();
};

/** The outbox of a recording, made on first use. */
export const noteOutboxFor = (recordingId: RecordingId): NoteOutbox => {
  const known = outboxes.get(recordingId);
  if (known) return known.outbox;
  const outbox = new NoteOutbox(recordingId);
  outbox.subscribe(keptChanged);
  const persisted = persistNoteOutbox(outbox, recordingId);
  persisted.subscribeKept(keptChanged);
  outboxes.set(recordingId, { outbox, persisted });
  return outbox;
};

/**
 * Lets go of sign-in for good on this page, which is leaving for the sign-in
 * page: the way out of the dialog when the account can't sign in here, say
 * after a rename. Only once every write under way is done, and only while the
 * browser then holds everything unsaved, for the account to find on its next
 * sign-in; resolves false, holding on, otherwise.
 */
export const releaseSignInForNotes = async (): Promise<boolean> => {
  await Promise.all([...outboxes.values()].map(({ persisted }) => persisted.whenWritten()));
  if (!allUnsavedKept()) return false;
  released = true;
  guardUnsaved();
  return true;
};

/** Tests: forget every recording's outbox, and any release. */
export const forgetNoteOutboxes = (): void => {
  for (const { persisted } of outboxes.values()) persisted.stop();
  outboxes.clear();
  released = false;
  guardUnsaved();
};
