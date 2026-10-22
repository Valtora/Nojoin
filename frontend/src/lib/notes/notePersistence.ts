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
  type NoteStore,
  type StoredDiscard,
  type StoredOutbox,
} from "./noteStore";

/**
 * Keeps a recording's note outbox in the browser (`noteStore`) and brings it
 * back after a reload (issue #537).
 *
 * The outbox is bound to the account the tab is signed in as once the session
 * works, and only that account's record is read or written. Reading it hands
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

/**
 * Reads a stored record: what it restores, "newer" for a record a later
 * version wrote (left alone), or null when there is nothing usable. A note of
 * a kind this version knows but can't read is dropped; one of a kind it does
 * not know is kept for the version that wrote it.
 */
export const readStoredOutbox = (raw: unknown): StoredRead | "newer" | null => {
  if (!isRecord(raw) || typeof raw.version !== "number") return null;
  if (raw.version > NOTE_RECORD_VERSION) return "newer";
  if (raw.version !== NOTE_RECORD_VERSION) return null;
  const notes: RestoredNote[] = [];
  const foreign: unknown[] = [];
  for (const value of Array.isArray(raw.notes) ? raw.notes : []) {
    if (!isRecord(value)) continue;
    if (isText(value.kind) && !isKnownKind(value.kind)) {
      foreign.push(value);
      continue;
    }
    const note = readNote(value);
    if (note) notes.push(note);
  }
  return {
    restored: { notes, draft: readDraft(raw.draft), discards: readDiscards(raw.discards) },
    foreign,
  };
};

/**
 * Persists `outbox` for as long as the page has it. Returns a function that
 * stops. Does nothing where notes can only be kept in memory.
 */
export const persistNoteOutbox = (
  outbox: NoteOutbox,
  recordingId: RecordingId,
  storeOf: () => NoteStore | null = sharedNoteStore,
): (() => void) => {
  if (!storeOf()) return () => undefined;

  let account: string | null = null;
  let ready = false;
  let foreign: unknown[] = [];
  /** The record last written, as JSON; "" when there is none. */
  let written: string | null = null;
  let dirty = false;
  let writing = false;
  let stopped = false;

  const record = (owner: string): StoredOutbox | null => {
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

  // One write at a time, each of the outbox as it is by then, so a burst of
  // changes (typing in the box) costs a write or two rather than one each.
  const flush = async () => {
    if (writing) return;
    writing = true;
    try {
      while (dirty && ready && !stopped && account !== null) {
        dirty = false;
        const store = storeOf();
        if (!store) return;
        const next = record(account);
        const text = next ? JSON.stringify(next) : "";
        if (text === written) continue;
        try {
          if (next) await store.put(next);
          else await store.remove(account, recordingId);
          written = text;
        } catch {
          // Still held in memory; the next change tries again.
          written = null;
        }
      }
    } finally {
      writing = false;
    }
  };

  const changed = () => {
    dirty = true;
    void flush();
  };

  const bind = async (owner: string) => {
    account = owner;
    const store = storeOf();
    if (!store) return;
    let raw: unknown;
    try {
      raw = await store.get(owner, recordingId);
    } catch {
      // Unread, so never overwritten: this outbox stays in memory only.
      return;
    }
    if (stopped) return;
    const read = readStoredOutbox(raw);
    // A later version's record is left for that version.
    if (read === "newer") return;
    if (read) {
      foreign = read.foreign;
      outbox.restore(read.restored);
    }
    written = raw === undefined ? "" : null;
    ready = true;
    changed();
  };

  const watch = (state: SessionState, previous?: SessionState) => {
    if (account === null) {
      if (state.username !== null && !state.signInRequired) void bind(state.username);
      return;
    }
    // Back from an ended session as the same account: write the record again,
    // since a sign-in as another account meanwhile removed it.
    if (previous?.signInRequired && !state.signInRequired && state.username === account) {
      written = null;
      changed();
    }
  };

  const stopOutbox = outbox.subscribe(changed);
  const stopSession = useSessionStore.subscribe(watch);
  watch(useSessionStore.getState());
  return () => {
    stopped = true;
    stopOutbox();
    stopSession();
  };
};
