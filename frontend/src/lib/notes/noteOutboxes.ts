import { getCurrentUser } from "@/lib/api";
import { notFoundMeans } from "@/lib/notFoundMeans";
import { holdSignInInPlace, useSessionStore } from "@/lib/session";
import type { RecordingId } from "@/types";

import { isBlankDraft, NoteOutbox, SLOW_SAVE_MS } from "./noteOutbox";
import { persistNoteOutbox } from "./notePersistence";
import { sharedNoteStore, type NoteStore } from "./noteStore";

/**
 * One outbox per recording for the life of the page, so a note on its way and
 * the box's text outlive every view of the recording. Each is also kept in
 * the browser where it can be (`persistNoteOutbox`), which covers a crash, a
 * killed tab or an ended session, never a page the user chose to leave.
 *
 * An outbox is the page's once a view of its recording has shown it
 * (`noteOutboxFor`). While any of the page's outboxes holds something unsent,
 * the page asks before it closes, whatever the browser has stored, and it
 * holds sign-in in place as a capture does (`@/lib/session`): an ended
 * session opens the sign-in dialog rather than reloading to the sign-in page,
 * and the notes are sent once the owner signs back in (issue #537).
 *
 * The notes the browser kept for recordings this page has not shown are sent
 * by senders (`sweepStoredNotes`, issue #558). A sender's notes came from the
 * browser's store and stay there until the server has them, so closing the
 * page loses nothing, and the next page sends them again: a sender neither
 * asks before the page closes nor holds sign-in. Once a view shows its
 * recording, the sender is that page's outbox.
 */

interface Kept {
  outbox: NoteOutbox;
  stop: () => void;
  /** Shown by a view of its recording on this page; a sender until then. */
  shown: boolean;
  /** The account its record was read for, once read. */
  account: number | null;
  /** Resolved once its record has been read, or could not be. */
  read: Promise<void>;
  /** The recording turned out to be gone: its notes wait in `goneNotes` to be copied or dismissed. */
  gone: boolean;
}

const outboxes = new Map<RecordingId, Kept>();

/** Whether leaving now would lose something only this page holds. */
const anyUnsaved = () =>
  [...outboxes.values()].some(({ outbox, shown, gone }) => shown && !gone && outbox.hasUnsaved());

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

/** The notes and box text of a recording that turned out to be gone. */
export interface GoneNotes {
  recordingId: RecordingId;
  /** Each note's full text, then the box's, in the order written. */
  texts: readonly string[];
}

const registryListeners = new Set<() => void>();
let unsent: readonly RecordingId[] = [];
let gone: readonly GoneNotes[] = [];

const sameIds = (a: readonly RecordingId[], b: readonly RecordingId[]) =>
  a.length === b.length && a.every((id, index) => id === b[index]);

const textsOf = (outbox: NoteOutbox): string[] => {
  const { entries, draft } = outbox.getSnapshot();
  return [...entries.map((entry) => entry.body), ...(isBlankDraft(draft) ? [] : [draft.text])];
};

const changed = () => {
  guardUnsaved();
  const nextUnsent = [...outboxes]
    .filter(([, kept]) => !kept.gone && (kept.outbox.hasUnsaved() || kept.outbox.pendingDiscards().length > 0))
    .map(([recordingId]) => recordingId);
  const goneKept = [...outboxes].filter(([, kept]) => kept.gone);
  const nextGone = goneKept.map(([recordingId, kept]) => ({ recordingId, texts: textsOf(kept.outbox) }));
  const goneChanged =
    nextGone.length !== gone.length ||
    nextGone.some((next, index) => JSON.stringify(next) !== JSON.stringify(gone[index]));
  if (sameIds(nextUnsent, unsent) && !goneChanged) return;
  unsent = nextUnsent;
  if (goneChanged) gone = nextGone;
  for (const listener of registryListeners) listener();
};

/** Calls `listener` whenever `unsentRecordings` or `goneNotes` may have changed. */
export const subscribeNoteOutboxes = (listener: () => void): (() => void) => {
  registryListeners.add(listener);
  return () => registryListeners.delete(listener);
};

/** The recordings whose notes, discards or box text this page has not saved yet. */
export const unsentRecordings = (): readonly RecordingId[] => unsent;

/** The notes of recordings found gone, until copied or dismissed. */
export const goneNotes = (): readonly GoneNotes[] => gone;

const checking = new Set<RecordingId>();

/**
 * The server said a recording does not exist. Only once `/users/me` names the
 * account the record was read for is it gone: under another account's
 * session, every recording answers so (issue #583). A recording in the bin
 * still takes notes, so it never gets here.
 */
const checkGone = async (recordingId: RecordingId) => {
  const kept = outboxes.get(recordingId);
  if (!kept || kept.gone || checking.has(recordingId)) return;
  checking.add(recordingId);
  try {
    const account = kept.account ?? useSessionStore.getState().userId;
    const answer = await notFoundMeans(
      account,
      getCurrentUser,
      () => useSessionStore.getState().signInRequired,
    );
    if (answer?.kind === "removed") markGone(recordingId);
  } finally {
    checking.delete(recordingId);
  }
};

const markGone = (recordingId: RecordingId) => {
  const kept = outboxes.get(recordingId);
  if (!kept || kept.gone) return;
  kept.gone = true;
  changed();
};

const make = (recordingId: RecordingId, shown: boolean): Kept => {
  const outbox = new NoteOutbox(recordingId);
  let settleRead = () => undefined as void;
  const read = new Promise<void>((resolve) => {
    settleRead = resolve;
  });
  const kept: Kept = { outbox, stop: () => undefined, shown, account: null, read, gone: false };
  outboxes.set(recordingId, kept);
  outbox.subscribe(changed);
  outbox.subscribeRecordingGone(() => void checkGone(recordingId));
  kept.stop = persistNoteOutbox(outbox, recordingId, sharedNoteStore, (account) => {
    kept.account = account;
    settleRead();
  });
  return kept;
};

/** The outbox of a recording a view shows, made on first use; a sender of it becomes the page's. */
export const noteOutboxFor = (recordingId: RecordingId): NoteOutbox => {
  const known = outboxes.get(recordingId);
  if (!known) return make(recordingId, true).outbox;
  if (!known.shown) {
    known.shown = true;
    changed();
  }
  return known.outbox;
};

/**
 * A recording this page shows, or holds notes for, was deleted: here, or as a
 * poll found under the account it was read as. Its notes cannot be saved, so
 * they wait in `goneNotes` to be copied or dismissed, kept in the browser
 * until then.
 */
export const noteRecordingRemoved = (recordingId: RecordingId): void => {
  const kept = outboxes.get(recordingId);
  if (!kept) return;
  markGone(recordingId);
  // Nothing to copy: only discards of its notes, or nothing at all.
  if (!kept.outbox.hasUnsaved()) void forgetGoneNotes(recordingId);
};

/**
 * Removes a gone recording's notes from the page and the browser, once the
 * user has copied or dismissed them.
 */
export const forgetGoneNotes = async (recordingId: RecordingId): Promise<void> => {
  const kept = outboxes.get(recordingId);
  if (!kept?.gone) return;
  kept.stop();
  outboxes.delete(recordingId);
  changed();
  const store = sharedNoteStore();
  if (!store || kept.account === null) return;
  try {
    await store.remove(kept.account, recordingId);
  } catch {
    // Still stored: it is found gone again on a later page, and shown again.
  }
};

/** How long the sweep waits for one recording's record to be read. */
const READ_WAIT_MS = 5_000;

/** Resolves once no note of `outbox` is still on its way: each saved, or failed with Retry. */
const sent = (outbox: NoteOutbox) =>
  new Promise<void>((resolve) => {
    const done = () => !outbox.getSnapshot().entries.some((entry) => entry.status === "saving");
    if (done()) {
      resolve();
      return;
    }
    const stop = outbox.subscribe(() => {
      if (!done()) return;
      stop();
      resolve();
    });
  });

const within = (promise: Promise<void>, ms: number) =>
  Promise.race([promise, new Promise<void>((resolve) => setTimeout(resolve, ms))]);

let sweptFor: number | null = null;

/**
 * Sends the notes `account` left in this browser for recordings this page has
 * not shown, as opening each would (issue #558): one recording at a time,
 * each note under the id and write token it was stored with, so a note the
 * server already has is not made twice. Once per account per page; another
 * tab's later records are its own to send.
 */
export const sweepStoredNotes = async (
  account: number,
  storeOf: () => NoteStore | null = sharedNoteStore,
): Promise<void> => {
  if (sweptFor === account) return;
  sweptFor = account;
  const store = storeOf();
  if (!store) return;
  let recordings: RecordingId[];
  try {
    recordings = await store.recordingsOf(account);
  } catch {
    // Unread: the next page tries again.
    return;
  }
  for (const recordingId of recordings) {
    const { userId, signInRequired } = useSessionStore.getState();
    if (userId !== account || signInRequired) return;
    if (outboxes.has(recordingId)) continue;
    const kept = make(recordingId, false);
    await within(kept.read, READ_WAIT_MS);
    // A note that gets no answer fails after SLOW_SAVE_MS, so this ends.
    await within(sent(kept.outbox), SLOW_SAVE_MS * 2);
  }
};

/** Tests: forget every recording's outbox, and that this page swept. */
export const forgetNoteOutboxes = (): void => {
  for (const { stop } of outboxes.values()) stop();
  outboxes.clear();
  sweptFor = null;
  changed();
};
