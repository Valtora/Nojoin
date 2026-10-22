import { createTimelineNote, deleteTimelineNote } from "@/lib/api";
import { getErrorStatus, isRecord } from "@/lib/errors";
import { useSessionStore, waitForSignInAfter } from "@/lib/session";
import type { RecordingId, TimelineNote, TimelineNoteCreate } from "@/types";

import { NOTE_BODY_MAX_CHARS, newNoteId, noteLength, stripNote } from "./headNoteSync";
import { formatTimelineMs } from "./placement";

/**
 * The notes typed into the note box on their way to the server, and the text
 * still in the box: one per recording (`noteOutboxFor`), so both outlive the
 * live view, the end of processing and a move to another page, and kept in
 * the browser (`notePersistence`) so they outlive a reload too.
 *
 * A note leaves the box as soon as it is sent, and waits here until the
 * server's answer, or a listing, shows it saved. Each is created under an id
 * and a write token chosen here, and keeps them, its text and its anchor until
 * then: a retry sends exactly the same create, so a create whose answer was
 * lost returns the note it made rather than making a second one (the API
 * returns the existing note for a known id).
 *
 * | Situation | Event | Outcome |
 * | --- | --- | --- |
 * | - | submit | entry `saving`; create sent |
 * | `saving` | 201, or 200 (an earlier attempt landed) | entry gone; the note is shown from here until a listing has it |
 * | `saving` | no answer for `SLOW_SAVE_MS` | `failed` ("no answer yet"), the request still running; a late 2xx still saves it |
 * | `saving` | no answer, or 5xx | `failed`; Retry sends the same create again |
 * | `saving` | 409 `note_id_in_use` | the id is another note's: once more under a new id and token once a kept copy has them, or once a write has gone unanswered for `NEW_ID_WAIT_MS`, then `failed`; `failed` ("couldn't keep") where the browser refuses the copy, and Retry tries again |
 * | `saving` | 410 `note_deleted_by_you` | discarded here while on its way: entry gone |
 * | `saving` | 410 `note_deleted_elsewhere` | a lost create landed and another device deleted it: entry gone, with a notice; never made again |
 * | `saving` | refused while sign-in is held in place (`@/lib/session`) | `failed` ("sign in again"); sent again once the owner signs back in |
 * | `saving` | other refusal | `failed`, saying why |
 * | `failed` | Retry | `saving`; the same create again |
 * | `failed` | Discard | entry gone; a delete under its token removes the note if the lost create landed, or leaves a tombstone so it can't land later |
 * | discarded | the create's 2xx arrives after all | ignored: the delete removes or fences that note |
 * | discarded | delete gets no answer | sent again whenever a listing still has the note, which stays hidden meanwhile |
 * | any | a listing has the entry's id | its create landed: entry gone |
 * | stored (`noteStore`) | the page reloads and the recording opens | `saving`; the same create again |
 */

/** How long a create may go unanswered before the note offers Retry and Discard. */
export const SLOW_SAVE_MS = 8_000;

export const OUTBOX_KINDS = ["point", "head", "tail"] as const;
export type OutboxKind = (typeof OUTBOX_KINDS)[number];

export const NOTE_INSERT_MODES = ["typing", "send", "head", "tail"] as const;
/** Where the note box puts a note. */
export type NoteInsertMode = (typeof NOTE_INSERT_MODES)[number];

/** What the box hands over: the text as typed, and a point note's anchor. */
export interface OutboxDraft {
  kind: OutboxKind;
  body: string;
  anchorMs: number | null;
}

/** The text in the note box, kept here so it outlives the box. */
export interface ComposerDraft {
  text: string;
  mode: NoteInsertMode;
  /** Where the recording was when the first character went into the empty box. */
  typingStartMs: number | null;
}

export const EMPTY_DRAFT: ComposerDraft = { text: "", mode: "typing", typingStartMs: null };

/**
 * The note a draft makes: "at typing start" anchors it where the box's first
 * character went in, "at send" where the recording is now.
 */
export const noteFromDraft = (
  draft: ComposerDraft,
  readAudioNow: () => number,
): OutboxDraft => {
  const { mode } = draft;
  if (mode === "head" || mode === "tail") {
    return { kind: mode, body: draft.text, anchorMs: null };
  }
  const anchorMs =
    mode === "typing" ? (draft.typingStartMs ?? readAudioNow()) : readAudioNow();
  return { kind: "point", body: draft.text, anchorMs };
};

export interface OutboxEntry {
  /** Stable while the entry lives, even if its server id is replaced. */
  key: string;
  /** The note's id on the server. */
  id: string;
  writeToken: string;
  kind: OutboxKind;
  /** The text as the API stores it. */
  body: string;
  anchorMs: number | null;
  /** When it was sent; stands in for the server's creation time until listed. */
  createdAt: string;
  status: "saving" | "failed";
  error: string | null;
}

/** A note not saved yet, as a stored copy of the outbox keeps it (`noteStore`). */
export type RestoredNote = Pick<
  OutboxEntry,
  "id" | "writeToken" | "kind" | "body" | "anchorMs" | "createdAt"
>;

/** What a stored copy of an outbox hands back after a reload. */
export interface RestoredOutbox {
  notes: readonly RestoredNote[];
  draft: ComposerDraft | null;
  discards: readonly { id: string; writeToken: string }[];
}

/** Something to tell the user that happened away from what they did last. */
export interface OutboxNotice {
  /** Increases with each notice, so a view can tell a new one from one it showed. */
  seq: number;
  text: string;
  tone: "error" | "info";
}

export interface OutboxSnapshot {
  /** Notes not yet known to be saved, in the order they were sent. */
  entries: readonly OutboxEntry[];
  /** Notes the server has answered for that no listing has had yet. */
  saved: readonly TimelineNote[];
  /** Ids discarded here: a listing still having one shows a delete in transit. */
  discarded: ReadonlySet<string>;
  draft: ComposerDraft;
  notice: OutboxNotice | null;
}

export const NOTE_TOO_LONG_ERROR = `A note can be at most ${NOTE_BODY_MAX_CHARS.toLocaleString()} characters.`;
const NO_ANSWER_ERROR = "Not saved: no answer from Nojoin.";
const NO_ANSWER_YET_ERROR = "No answer from Nojoin yet.";
const RECORDING_GONE_ERROR = "Not saved: this recording no longer exists.";
const REFUSED_ERROR = "Not saved: Nojoin refused this note.";
const SIGN_IN_ERROR = "Not saved yet: it saves once you sign in again.";
const NOT_KEPT_ERROR = "Not saved: this browser couldn't keep a copy to send it safely.";

/**
 * Whether a failed request waits for sign-in (`waitForSignInAfter`) rather
 * than failing: a capture holds sign-in in place and the session is unusable.
 */
const signInPending = () => {
  const { heldInPlace, signInRequired } = useSessionStore.getState();
  return heldInPlace && signInRequired;
};

type Failure =
  | "unknown"
  | "idInUse"
  | "deletedByYou"
  | "deletedElsewhere"
  | "recordingGone"
  | "refused";

const refusalCode = (error: unknown): string | null => {
  if (!isRecord(error) || !isRecord(error.response)) return null;
  const data = error.response.data;
  if (!isRecord(data) || !isRecord(data.detail)) return null;
  return typeof data.detail.code === "string" ? data.detail.code : null;
};

const failureOf = (error: unknown): Failure => {
  const status = getErrorStatus(error);
  if (status === null || status >= 500) return "unknown";
  const code = refusalCode(error);
  if (code === "note_id_in_use") return "idInUse";
  // A tombstone: the id was a note's and is gone. Never made again.
  if (status === 410) return code === "note_deleted_elsewhere" ? "deletedElsewhere" : "deletedByYou";
  if (code === "recording_not_found") return "recordingGone";
  return "refused";
};

const FAILURE_ERRORS: Record<"unknown" | "idInUse" | "recordingGone" | "refused", string> = {
  unknown: NO_ANSWER_ERROR,
  idInUse: REFUSED_ERROR,
  recordingGone: RECORDING_GONE_ERROR,
  refused: REFUSED_ERROR,
};

/** Where a note goes, as a phrase: "at 1:02:03", "before the transcript". */
export const notePlace = (entry: Pick<OutboxEntry, "kind" | "anchorMs">): string =>
  entry.kind === "head"
    ? "before the transcript"
    : entry.kind === "tail"
      ? "after the transcript"
      : `at ${formatTimelineMs(entry.anchorMs ?? 0)}`;

/** The start of a note's text, to tell one note's buttons from another's. */
export const noteSnippet = (body: string, maxChars = 40): string => {
  const flat = body.replace(/\s+/g, " ").trim();
  return flat.length > maxChars ? `${flat.slice(0, maxChars - 1)}…` : flat;
};

const createOf = (entry: OutboxEntry): TimelineNoteCreate => ({
  id: entry.id,
  write_token: entry.writeToken,
  kind: entry.kind,
  body: entry.body,
  ...(entry.kind === "point" && entry.anchorMs !== null
    ? { anchor_ms: entry.anchorMs }
    : {}),
});

interface Discard {
  token: string;
  inFlight: boolean;
}

/** A note shown among transcript lines: saved, or still in the outbox. */
export interface InlineNote {
  /** Stable across the note leaving the outbox. */
  key: string;
  id: string;
  kind: "point" | "selection";
  body: string;
  anchor_ms?: number | null;
  start_ms?: number | null;
  end_ms?: number | null;
  quoted_text?: string | null;
  created_at: string;
  status: "saved" | "saving" | "failed";
  error: string | null;
}

/**
 * The saved notes to show: those listed, and those the server answered for
 * since, less any discarded here.
 */
export const shownNotes = (
  listed: readonly TimelineNote[] | null | undefined,
  outbox: Pick<OutboxSnapshot, "saved" | "discarded">,
): TimelineNote[] => {
  const seen = new Set<string>();
  const notes: TimelineNote[] = [];
  for (const note of [...(listed ?? []), ...outbox.saved]) {
    if (seen.has(note.id) || outbox.discarded.has(note.id)) continue;
    seen.add(note.id);
    notes.push(note);
  }
  return notes;
};

/**
 * The point and selection notes to place among a live transcript's lines:
 * the saved ones (`shownNotes`) and those still on their way. Head and tail
 * notes belong to the Notes box, which lists its own.
 */
export const inlineNotes = (
  listed: readonly TimelineNote[] | null | undefined,
  outbox: Pick<OutboxSnapshot, "entries" | "saved" | "discarded">,
): InlineNote[] => {
  const notes: InlineNote[] = [];
  const shown = new Set<string>();
  for (const note of shownNotes(listed, outbox)) {
    if (note.kind !== "point" && note.kind !== "selection") continue;
    shown.add(note.id);
    notes.push({ ...note, kind: note.kind, key: note.id, status: "saved", error: null });
  }
  for (const entry of outbox.entries) {
    if (entry.kind !== "point" || shown.has(entry.id)) continue;
    notes.push({
      key: entry.key,
      id: entry.id,
      kind: "point",
      body: entry.body,
      anchor_ms: entry.anchorMs,
      created_at: entry.createdAt,
      status: entry.status,
      error: entry.error,
    });
  }
  return notes;
};

const isBlankDraft = (draft: ComposerDraft) => stripNote(draft.text) === "";

export class NoteOutbox {
  private entries: OutboxEntry[] = [];
  private saved: TimelineNote[] = [];
  private readonly discards = new Map<string, Discard>();
  /** The ids in `discards`, as last published: a new set only when they change. */
  private discardedIds: ReadonlySet<string> = new Set();
  private listed = new Set<string>();
  /** Each entry's latest attempt: only its outcome sets the entry's state. */
  private readonly attempts = new Map<string, number>();
  private draft: ComposerDraft = EMPTY_DRAFT;
  private notice: OutboxNotice | null = null;
  private snapshot: OutboxSnapshot = {
    entries: [],
    saved: [],
    discarded: new Set(),
    draft: EMPTY_DRAFT,
    notice: null,
  };
  private readonly listeners = new Set<() => void>();
  private readonly writeListeners = new Set<() => void>();
  private writeBarrier: (() => Promise<boolean>) | null = null;
  /** Entries given a new id that a kept copy of the outbox may not have yet. */
  private readonly unkeptIds = new Set<string>();

  constructor(private readonly recordingId: RecordingId) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** Called after a note of this outbox is saved or deleted on the server. */
  subscribeWrites = (listener: () => void): (() => void) => {
    this.writeListeners.add(listener);
    return () => this.writeListeners.delete(listener);
  };

  getSnapshot = (): OutboxSnapshot => this.snapshot;

  /** Whether leaving the page now would lose a note or the box's text. */
  hasUnsaved = (): boolean => this.entries.length > 0 || !isBlankDraft(this.draft);

  setDraft = (draft: ComposerDraft): void => {
    this.draft = draft;
    this.publish();
  };

  /**
   * Waits, before a note goes out under a new id, until a kept copy of the
   * outbox has that id (`persistNoteOutbox`), so a reload can't send the note
   * a second time under the old one. A write unanswered for `NEW_ID_WAIT_MS`
   * ends the wait, and the note goes out. Resolves false where the copy could
   * not be written, and the note is not sent.
   */
  setWriteBarrier = (barrier: (() => Promise<boolean>) | null): void => {
    this.writeBarrier = barrier;
  };

  /**
   * Queues a note and sends it. Returns why it was refused before sending (too
   * long, or empty), or null once it is on its way. With `clearDraft`, the box's
   * text is emptied in the same change, so no kept copy of the outbox ever
   * holds both the note and the text it was sent from.
   */
  submit = (draft: OutboxDraft, options: { clearDraft?: boolean } = {}): string | null => {
    const body = stripNote(draft.body);
    if (!body) return "Type a note first.";
    if (noteLength(body) > NOTE_BODY_MAX_CHARS) return NOTE_TOO_LONG_ERROR;
    if (draft.kind === "point" && (draft.anchorMs === null || draft.anchorMs < 0)) {
      return "There is no point in the recording to anchor this note to yet.";
    }
    // The key starts as the id, so the line keeps its identity once saved.
    const id = newNoteId();
    const entry: OutboxEntry = {
      key: id,
      id,
      writeToken: newNoteId(),
      kind: draft.kind,
      body,
      anchorMs: draft.kind === "point" ? Math.round(draft.anchorMs ?? 0) : null,
      createdAt: new Date().toISOString(),
      status: "saving",
      error: null,
    };
    this.entries = [...this.entries, entry];
    if (options.clearDraft) this.draft = { ...EMPTY_DRAFT, mode: this.draft.mode };
    this.publish();
    void this.send(entry.key);
    return null;
  };

  /** Sends a failed note again: the same id, token, text and anchor. */
  retry = (key: string): void => {
    const entry = this.entry(key);
    if (!entry || entry.status !== "failed") return;
    void this.send(key);
  };

  /** Drops a failed note, and makes sure a create of it that is still on its way cannot land. */
  discard = (key: string): void => {
    const entry = this.entry(key);
    if (!entry || entry.status !== "failed") return;
    this.entries = this.entries.filter((candidate) => candidate.key !== key);
    this.attempts.delete(key);
    this.unkeptIds.delete(key);
    this.discards.set(entry.id, { token: entry.writeToken, inFlight: false });
    this.discardedIds = new Set(this.discards.keys());
    this.publish();
    void this.sendDiscard(entry.id);
  };

  /** Takes the recording's notes as last listed. */
  ingest = (notes: readonly TimelineNote[] | null | undefined): void => {
    this.listed = new Set((notes ?? []).map((note) => note.id));
    const entries = this.entries.filter((entry) => !this.listed.has(entry.id));
    const saved = this.saved.filter((note) => !this.listed.has(note.id));
    const changed =
      entries.length !== this.entries.length || saved.length !== this.saved.length;
    this.entries = entries;
    this.saved = saved;
    for (const [id, discard] of this.discards) {
      if (this.listed.has(id) && !discard.inFlight) void this.sendDiscard(id);
    }
    if (changed) this.publish();
  };

  /**
   * Takes back what this page kept of the outbox before a reload. Each note is
   * sent again under its own id and token, so one whose create landed is not
   * made twice, and each discard's delete is sent again. A note this outbox
   * already knows of is left alone, as is the text in the box unless it is empty.
   */
  restore = (stored: RestoredOutbox): void => {
    const known = new Set([
      ...this.entries.flatMap((entry) => [entry.key, entry.id]),
      ...this.saved.map((note) => note.id),
      ...this.listed,
      ...this.discards.keys(),
    ]);
    const restored = stored.notes
      .filter((note) => !known.has(note.id))
      .map((note): OutboxEntry => ({ ...note, key: note.id, status: "saving", error: null }));
    const discards = stored.discards.filter(({ id }) => !this.discards.has(id));
    for (const { id, writeToken } of discards) {
      this.discards.set(id, { token: writeToken, inFlight: false });
    }
    if (discards.length > 0) this.discardedIds = new Set(this.discards.keys());
    this.entries = [...this.entries, ...restored];
    if (stored.draft && isBlankDraft(this.draft)) this.draft = stored.draft;
    this.publish();
    for (const entry of restored) void this.send(entry.key);
    for (const { id } of discards) void this.sendDiscard(id);
  };

  /** The discards whose delete the server has not confirmed yet. */
  pendingDiscards = (): { id: string; writeToken: string }[] =>
    [...this.discards].map(([id, discard]) => ({ id, writeToken: discard.token }));

  private entry(key: string): OutboxEntry | undefined {
    return this.entries.find((entry) => entry.key === key);
  }

  private patch(key: string, changes: Partial<OutboxEntry>): void {
    this.entries = this.entries.map((entry) =>
      entry.key === key ? { ...entry, ...changes } : entry,
    );
    this.publish();
  }

  private fail(key: string, error: string): void {
    const entry = this.entry(key);
    if (!entry) return;
    // The note's own line says why; this says which note.
    const still = error === NO_ANSWER_YET_ERROR ? " yet" : "";
    this.notice = {
      seq: (this.notice?.seq ?? 0) + 1,
      text: `Note ${notePlace(entry)} not saved${still}.`,
      tone: "error",
    };
    this.patch(key, { status: "failed", error });
  }

  private drop(key: string, notice: string | null): void {
    this.entries = this.entries.filter((entry) => entry.key !== key);
    this.attempts.delete(key);
    this.unkeptIds.delete(key);
    if (notice) this.notice = { seq: (this.notice?.seq ?? 0) + 1, text: notice, tone: "info" };
    this.publish();
  }

  private async send(key: string, freshIdsLeft = 1): Promise<void> {
    const entry = this.entry(key);
    if (!entry) return;
    const attempt = (this.attempts.get(key) ?? 0) + 1;
    this.attempts.set(key, attempt);
    const isLatest = () => this.attempts.get(key) === attempt && !!this.entry(key);
    this.patch(key, { status: "saving", error: null });
    // Started before any wait on the kept copy, so a note held up there
    // offers Retry and Discard as one held up on the network does.
    const slow = setTimeout(() => {
      if (isLatest() && this.entry(key)?.status === "saving") {
        this.fail(key, NO_ANSWER_YET_ERROR);
      }
    }, SLOW_SAVE_MS);

    try {
      if (this.unkeptIds.has(key)) {
        // A kept copy still has the refused id: a reload would send it again
        // as well, making the note twice.
        const safe = (await this.writeBarrier?.()) ?? true;
        if (!isLatest()) return;
        if (!safe) {
          this.fail(key, NOT_KEPT_ERROR);
          return;
        }
        this.unkeptIds.delete(key);
      }
      const note = await createTimelineNote(this.recordingId, createOf(entry));
      this.settle(key, note);
    } catch (error) {
      if (!isLatest()) return;
      // While a capture holds sign-in in place, a create refused for an ended
      // session, or sent while the browser is signed in as someone else, waits
      // for the recording's owner to sign back in and goes again, as the
      // capture's uploads do. Same id and token, so it can't add a second note.
      const waiting = waitForSignInAfter(error);
      if (signInPending()) this.patch(key, { status: "failed", error: SIGN_IN_ERROR });
      if (await waiting) {
        clearTimeout(slow);
        if (isLatest()) await this.send(key, freshIdsLeft);
        return;
      }
      if (!isLatest()) return;
      const failure = failureOf(error);
      if (failure === "idInUse" && freshIdsLeft > 0) {
        this.patch(key, { id: newNoteId(), writeToken: newNoteId() });
        this.unkeptIds.add(key);
        await this.send(key, freshIdsLeft - 1);
        return;
      }
      if (failure === "deletedByYou") {
        this.drop(key, null);
        return;
      }
      if (failure === "deletedElsewhere") {
        this.drop(
          key,
          `Note ${notePlace(entry)} was deleted on another device, so it was not added again.`,
        );
        return;
      }
      this.fail(key, FAILURE_ERRORS[failure]);
    } finally {
      clearTimeout(slow);
    }
  }

  /** Any attempt's success saves the note, unless it was discarded meanwhile. */
  private settle(key: string, note: TimelineNote): void {
    // Gone: discarded, whose delete removes or fences this very note, or
    // already listed.
    if (!this.entry(key)) return;
    this.entries = this.entries.filter((entry) => entry.key !== key);
    this.attempts.delete(key);
    this.unkeptIds.delete(key);
    const known = this.listed.has(note.id) || this.saved.some((saved) => saved.id === note.id);
    if (!known) this.saved = [...this.saved, note];
    this.publish();
    this.wrote();
  }

  private async sendDiscard(id: string): Promise<void> {
    const discard = this.discards.get(id);
    if (!discard || discard.inFlight) return;
    discard.inFlight = true;
    try {
      await deleteTimelineNote(this.recordingId, id, { writeToken: discard.token });
      this.discards.delete(id);
      this.discardedIds = new Set(this.discards.keys());
    } catch (error) {
      if (await waitForSignInAfter(error)) {
        discard.inFlight = false;
        await this.sendDiscard(id);
        return;
      }
      const status = getErrorStatus(error);
      if (status === null || status >= 500) {
        // Sent again when a listing still has the note.
        discard.inFlight = false;
        return;
      }
      // 404 or 410: no such note, now or later. Anything else won't change
      // on a resend, and the note, if it exists, is shown again.
      this.discards.delete(id);
      this.discardedIds = new Set(this.discards.keys());
    }
    this.saved = this.saved.filter((note) => note.id !== id);
    this.publish();
    this.wrote();
  }

  private wrote(): void {
    for (const listener of this.writeListeners) listener();
  }

  private publish(): void {
    this.snapshot = {
      entries: this.entries,
      saved: this.saved,
      discarded: this.discardedIds,
      draft: this.draft,
      notice: this.notice,
    };
    for (const listener of this.listeners) listener();
  }
}
