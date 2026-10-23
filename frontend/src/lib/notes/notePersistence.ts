import { isRecord } from "@/lib/errors";
import { useSessionStore, type SessionState } from "@/lib/session";
import type { RecordingId } from "@/types";

import { stripNote } from "./headNoteSync";
import {
  NOTE_INSERT_MODES,
  OUTBOX_KINDS,
  type ComposerDraft,
  type NoteOutbox,
  type RestoredNote,
  type RestoredOutbox,
} from "./noteOutbox";
import {
  NOTE_RECORD_VERSION,
  sharedNoteStore,
  storingStopped,
  subscribeStoreEvents,
  type NoteStore,
  type StoreEvent,
  type StoredDiscard,
  type StoredOutbox,
} from "./noteStore";

/**
 * Keeps a recording's note outbox in the browser (`noteStore`) and brings it
 * back after a reload (issue #537).
 *
 * The outbox is bound to the account (by id) the tab is signed in as once the
 * session works, and only that account's record is read or written. Reading it hands
 * its notes back to the outbox, which sends each again under its own id and
 * write token, so a note whose create landed before the reload is not made
 * twice. From then on, every change to the outbox rewrites the record.
 */

const isText = (value: unknown): value is string => typeof value === "string";
const isId = (value: unknown): value is string => isText(value) && value !== "";
const isTime = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

const isKnownKind = (kind: unknown): kind is RestoredNote["kind"] =>
  (OUTBOX_KINDS as readonly unknown[]).includes(kind);
const isKnownMode = (mode: unknown): mode is ComposerDraft["mode"] =>
  (NOTE_INSERT_MODES as readonly unknown[]).includes(mode);

const readNote = (value: Record<string, unknown>): RestoredNote | null => {
  const { id, writeToken, kind, body, anchorMs, createdAt } = value;
  if (!isId(id) || !isId(writeToken) || !isKnownKind(kind) || !isText(body) || !isText(createdAt)) {
    return null;
  }
  if (kind === "point") {
    return isTime(anchorMs) ? { id, writeToken, kind, body, anchorMs, createdAt } : null;
  }
  return { id, writeToken, kind, body, anchorMs: null, createdAt };
};

const readDraft = (value: unknown): ComposerDraft | null => {
  if (!isRecord(value) || !isText(value.text)) return null;
  return {
    text: value.text,
    mode: isKnownMode(value.mode) ? value.mode : "typing",
    typingStartMs: isTime(value.typingStartMs) ? value.typingStartMs : null,
  };
};

const readDiscards = (value: unknown): StoredDiscard[] =>
  Array.isArray(value)
    ? value.flatMap((discard) =>
        isRecord(discard) && isId(discard.id) && isId(discard.writeToken)
          ? [{ id: discard.id, writeToken: discard.writeToken }]
          : [],
      )
    : [];

export interface StoredRead {
  restored: RestoredOutbox;
  /** Notes of a kind this version does not know, to be written back as they were. */
  foreign: unknown[];
}

/** Upgrades a stored record from one version of the layout to the next. */
export type RecordMigration = (record: Record<string, unknown>) => Record<string, unknown>;

/**
 * The upgrade from each earlier layout version to the one after it. Version 1
 * is the first, so there are none yet; a change to the layout (B3's selection
 * notes, say) bumps `NOTE_RECORD_VERSION` and adds the step from the old one.
 */
export const RECORD_MIGRATIONS: Readonly<Record<number, RecordMigration>> = {};

/**
 * Reads a stored record: what it restores; "newer" for a record a later
 * version wrote, or "older" for an earlier one with no upgrade to this
 * version (both left alone); or null when there is nothing usable. A note of
 * a kind this version knows but can't read is dropped; one of a kind it does
 * not know is kept for the version that wrote it.
 */
export const readStoredOutbox = (
  raw: unknown,
  migrations: Readonly<Record<number, RecordMigration>> = RECORD_MIGRATIONS,
): StoredRead | "newer" | "older" | null => {
  if (!isRecord(raw) || !Number.isInteger(raw.version)) return null;
  let record = raw;
  let version = raw.version as number;
  if (version > NOTE_RECORD_VERSION) return "newer";
  while (version < NOTE_RECORD_VERSION) {
    const upgrade = migrations[version];
    if (!upgrade) return "older";
    version += 1;
    record = { ...upgrade(record), version };
  }
  const notes: RestoredNote[] = [];
  const foreign: unknown[] = [];
  for (const value of Array.isArray(record.notes) ? record.notes : []) {
    if (!isRecord(value)) continue;
    if (isText(value.kind) && !isKnownKind(value.kind)) {
      foreign.push(value);
      continue;
    }
    const note = readNote(value);
    if (note) notes.push(note);
  }
  return {
    restored: { notes, draft: readDraft(record.draft), discards: readDiscards(record.discards) },
    foreign,
  };
};

/**
 * How long a note given a new id waits on a write under way before it goes
 * out anyway. A healthy write takes milliseconds; as for signing out
 * (`CLEAR_ON_SIGN_OUT_MS`), a store that hasn't answered in 2 s is treated as
 * not answering. It is well under `SLOW_SAVE_MS`, so a stalled store alone
 * doesn't make the note look unsaved.
 */
export const NEW_ID_WAIT_MS = 2_000;

/**
 * Persists `outbox` for as long as the page has it, and returns a function
 * that stops, whose promise settles once a write already under way has
 * finished. Where notes can only be kept in memory it never reads or writes.
 *
 * The stored copy is a stopgap for a crash, a killed tab or an ended session.
 * It never stands in for the leave warning (`noteOutboxes`), and two tabs on
 * one recording share it: the last to write wins.
 *
 * The record is written only while the session is the account the outbox was
 * bound to. A recording belongs to one account, so while the browser turns out
 * to be signed in as another, nothing is stored. Nor is it once another tab's
 * cleanup has removed this account's records, or a sign-out in any tab has
 * stopped storing. Once the owner is back, the record is written afresh.
 */
export const persistNoteOutbox = (
  outbox: NoteOutbox,
  recordingId: RecordingId,
  storeOf: () => NoteStore | null = sharedNoteStore,
): (() => Promise<void>) => {
  let account: number | null = null;
  let ready = false;
  /** Set when a cleanup removed this account's records, until the account is back. */
  let evicted = false;
  let foreign: unknown[] = [];
  /** The record last written, as JSON; "" when there is none. */
  let written: string | null = null;
  let dirty = false;
  let flushing: Promise<void> | null = null;
  let stopped = false;
  const record = (owner: number): StoredOutbox | null => {
    const { entries, draft } = outbox.getSnapshot();
    const notes: unknown[] = [
      ...entries.map(({ id, writeToken, kind, body, anchorMs, createdAt }) => ({
        id,
        writeToken,
        kind,
        body,
        anchorMs,
        createdAt,
      })),
      ...foreign,
    ];
    const discards = outbox.pendingDiscards();
    const hasDraft = stripNote(draft.text) !== "";
    if (notes.length === 0 && discards.length === 0 && !hasDraft) return null;
    return {
      version: NOTE_RECORD_VERSION,
      account: owner,
      recordingId,
      notes,
      draft: hasDraft ? draft : null,
      discards,
    };
  };

  const textOf = (owner: number) => {
    const next = record(owner);
    return next ? JSON.stringify(next) : "";
  };

  const writable = () =>
    ready &&
    !stopped &&
    !evicted &&
    !storingStopped() &&
    account !== null &&
    useSessionStore.getState().userId === account;

  // One write at a time, each of the outbox as it is by then, so a burst of
  // changes (typing in the box) costs a write or two rather than one each.
  const flush = async () => {
    while (dirty && writable()) {
      const store = storeOf();
      // Signed out: still dirty, so written once storing resumes.
      if (!store) return;
      dirty = false;
      const owner = account as number;
      const next = record(owner);
      const text = next ? JSON.stringify(next) : "";
      if (text === written) continue;
      try {
        if (next) await store.put(next);
        else await store.remove(owner, recordingId);
        written = text;
      } catch {
        // Still held in memory; the next change tries again.
        written = null;
      }
    }
  };

  const startFlush = () => {
    if (flushing) return;
    flushing = flush().finally(() => {
      flushing = null;
      // A change that came in as the last write finished.
      if (dirty && writable() && storeOf()) startFlush();
    });
  };

  const changed = () => {
    dirty = true;
    startFlush();
  };

  const rewrite = () => {
    written = null;
    changed();
  };

  /**
   * Whether a note may go out under a new id: once any write under way is
   * done, no stored copy can still hold its old one. That is so when the
   * record has the outbox as it is, and when this page never wrote one, was
   * stopped, or lost its record to a sign-out or another account's cleanup.
   *
   * A write that goes unanswered for `NEW_ID_WAIT_MS` leaves the page in
   * memory only until it answers, and the note goes out regardless: the
   * stored copy is a stopgap, and must never hold a note back for good. Once
   * that write answers, the outbox as it is by then is written after it. A
   * tab that closes or crashes before that write answers can leave the old id
   * stored, and the next visit sends the note a second time (ARCHITECTURE.md,
   * "Unsaved notes in the browser"). A close no longer asks first, as the note
   * has gone out.
   */
  const newIdSafe = async () => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const gaveUp = new Promise<"gave up">((resolve) => {
      timer = setTimeout(() => resolve("gave up"), NEW_ID_WAIT_MS);
    });
    const drained = (async () => {
      while (flushing) await flushing;
      return "drained" as const;
    })();
    const outcome = await Promise.race([drained, gaveUp]);
    clearTimeout(timer);
    if (outcome === "gave up") return true;
    if (!ready || stopped || evicted || storingStopped()) return true;
    return written === textOf(account as number);
  };

  const bind = async (owner: number) => {
    const store = storeOf();
    if (!store || account !== null) return;
    account = owner;
    let raw: unknown;
    try {
      raw = await store.get(owner, recordingId);
    } catch {
      // Unread, so never overwritten: this outbox stays in memory only.
      return;
    }
    if (stopped) return;
    const read = readStoredOutbox(raw);
    // Another version's record is left for a version that can read it.
    if (read === "newer" || read === "older") return;
    if (read) {
      foreign = read.foreign;
      outbox.restore(read.restored);
    }
    written = raw === undefined ? "" : null;
    ready = true;
    changed();
  };

  const isOwner = (state: SessionState | undefined) =>
    state !== undefined && state.userId === account && !state.signInRequired;

  const backAsOwner = () => {
    evicted = false;
    rewrite();
  };

  const watch = (state: SessionState, previous?: SessionState) => {
    if (account === null) {
      if (state.userId !== null && !state.signInRequired) void bind(state.userId);
      return;
    }
    // Back as the owner, from an ended session or from another account.
    if (isOwner(state) && !isOwner(previous)) backAsOwner();
  };

  const onStoreEvent = (event: StoreEvent) => {
    if (event.kind === "resumed") {
      if (account === null) watch(useSessionStore.getState());
      else rewrite();
      return;
    }
    if (event.kind === "kept" && account !== null) {
      const ours = event.accounts.includes(account);
      // This account's records are gone, and the browser is another's now;
      // or the browser is this account's again.
      if (!ours) evicted = true;
      else if (evicted) backAsOwner();
    }
  };

  // The outbox's own changes rewrite the record; the barrier keeps a note
  // from going out under a new id that only memory holds.
  const stopOutbox = outbox.subscribe(changed);
  const stopSession = useSessionStore.subscribe(watch);
  const stopEvents = subscribeStoreEvents(onStoreEvent);
  outbox.setWriteBarrier(newIdSafe);
  watch(useSessionStore.getState());
  return async () => {
    stopped = true;
    stopOutbox();
    stopSession();
    stopEvents();
    outbox.setWriteBarrier(null);
    await flushing?.catch(() => undefined);
  };
};
