import { holdSignInInPlace } from "@/lib/session";
import type { RecordingId } from "@/types";

import { NoteOutbox } from "./noteOutbox";
import { persistNoteOutbox } from "./notePersistence";

/**
 * One outbox per recording for the life of the page, so a note on its way and
 * the box's text outlive every view of the recording, each kept in the
 * browser so they outlive a reload too (`persistNoteOutbox`).
 *
 * While any of them holds something unsaved, the page asks before it closes,
 * and it holds sign-in in place as a capture does (`@/lib/session`): an ended
 * session opens the sign-in dialog rather than reloading to the sign-in page,
 * and the notes are sent once the owner signs back in (issue #537).
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

/** The outbox of a recording, made on first use. */
export const noteOutboxFor = (recordingId: RecordingId): NoteOutbox => {
  const known = outboxes.get(recordingId);
  if (known) return known.outbox;
  const outbox = new NoteOutbox(recordingId);
  outbox.subscribe(guardUnsaved);
  outboxes.set(recordingId, { outbox, stop: persistNoteOutbox(outbox, recordingId) });
  return outbox;
};

/** Tests: forget every recording's outbox. */
export const forgetNoteOutboxes = (): void => {
  for (const { stop } of outboxes.values()) stop();
  outboxes.clear();
  guardUnsaved();
};
