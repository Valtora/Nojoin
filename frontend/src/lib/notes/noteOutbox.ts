import { createTimelineNote, deleteTimelineNote } from "@/lib/api";
import { getErrorStatus, isRecord } from "@/lib/errors";
import type { RecordingId, TimelineNote, TimelineNoteCreate } from "@/types";

import { NOTE_BODY_MAX_CHARS, newNoteId, noteLength, stripNote } from "./headNoteSync";

/**
 * The notes typed into the live note box on their way to the server.
 *
 * A note leaves the box as soon as it is sent, and waits here until the
 * server's answer, or a poll, shows it saved. Each is created under an id and
 * a write token chosen here, and keeps them, its text and its anchor until
 * then: a retry sends exactly the same create, so a create whose answer was
 * lost returns the note it made rather than making a second one (the API
 * returns the existing note for a known id).
 *
 * | Situation | Event | Outcome |
 * | --- | --- | --- |
 * | - | submit | entry `saving`; create sent |
 * | `saving` | 201, or 200 (an earlier attempt landed) | entry gone; the note is shown from here until a poll lists it |
 * | `saving` | no answer for `SLOW_SAVE_MS` | `failed` ("no answer yet"), the request still running; a late 2xx still saves it |
 * | `saving` | no answer, or 5xx | `failed`; Retry sends the same create again |
 * | `saving` | 409 `note_id_in_use`, or 410 | the id can't be used: once more under a new id and token |
 * | `saving` | other refusal | `failed`, saying why |
 * | `failed` | Retry | `saving`; the same create again |
 * | `failed` | Discard | entry gone; a delete under its token removes the note if the lost create landed, or leaves a tombstone so it can't land later |
 * | discarded | delete gets no answer | sent again whenever a poll still lists the note, which stays hidden meanwhile |
 * | any | a poll lists the entry's id | its create landed: entry gone |
 */

/** How long a create may go unanswered before the note offers Retry and Discard. */
export const SLOW_SAVE_MS = 8_000;

export type OutboxKind = "point" | "head" | "tail";

/** What the box hands over: the text as typed, and a point note's anchor. */
export interface OutboxDraft {
  kind: OutboxKind;
  body: string;
  anchorMs: number | null;
}

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

export interface OutboxSnapshot {
  /** Notes not yet known to be saved, in the order they were sent. */
  entries: readonly OutboxEntry[];
  /** Notes the server has answered for that no poll has listed yet. */
  saved: readonly TimelineNote[];
  /** Ids discarded here: a poll still listing one shows a delete in transit. */
  discarded: ReadonlySet<string>;
}

export const NOTE_TOO_LONG_ERROR = `A note can be at most ${NOTE_BODY_MAX_CHARS.toLocaleString()} characters.`;
const NO_ANSWER_ERROR = "Not saved: no answer from Nojoin.";
const NO_ANSWER_YET_ERROR = "No answer from Nojoin yet.";
const RECORDING_GONE_ERROR = "Not saved: this recording no longer exists.";
const REFUSED_ERROR = "Not saved: Nojoin refused this note.";

type Failure = "unknown" | "idUnusable" | "recordingGone" | "refused";

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
  if (code === "note_id_in_use" || status === 410) return "idUnusable";
  if (code === "recording_not_found") return "recordingGone";
  return "refused";
};

const FAILURE_ERRORS: Record<Exclude<Failure, "idUnusable">, string> = {
  unknown: NO_ANSWER_ERROR,
  recordingGone: RECORDING_GONE_ERROR,
  refused: REFUSED_ERROR,
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

const isBodyNote = (
  note: TimelineNote,
): note is TimelineNote & { kind: "point" | "selection" } =>
  note.kind === "point" || note.kind === "selection";

/**
 * The point and selection notes to place among a live transcript's lines:
 * those a poll listed, those the server answered for since, and those still
 * on their way, less any discarded here. Head and tail notes have the notes
 * panel; outbox entries of those kinds are listed by the note box.
 */
export const inlineNotes = (
  listed: readonly TimelineNote[] | null | undefined,
  outbox: OutboxSnapshot,
): InlineNote[] => {
  const shown = new Set<string>();
  const notes: InlineNote[] = [];
  for (const note of [...(listed ?? []), ...outbox.saved]) {
    if (!isBodyNote(note) || shown.has(note.id) || outbox.discarded.has(note.id)) continue;
    shown.add(note.id);
    notes.push({ ...note, key: note.id, status: "saved", error: null });
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

export class NoteOutbox {
  private entries: OutboxEntry[] = [];
  private saved: TimelineNote[] = [];
  private readonly discards = new Map<string, Discard>();
  private listed = new Set<string>();
  /** Each entry's latest attempt: only its outcome sets the entry's state. */
  private readonly attempts = new Map<string, number>();
  private snapshot: OutboxSnapshot = {
    entries: [],
    saved: [],
    discarded: new Set(),
  };
  private readonly listeners = new Set<() => void>();

  constructor(private readonly recordingId: RecordingId) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): OutboxSnapshot => this.snapshot;

  /**
   * Queues a note and sends it. Returns why it was refused before sending (too
   * long, or empty), or null once it is on its way.
   */
  submit = (draft: OutboxDraft): string | null => {
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
    this.discards.set(entry.id, { token: entry.writeToken, inFlight: false });
    this.publish();
    void this.sendDiscard(entry.id);
  };

  /** Takes the notes a poll listed. */
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

  private entry(key: string): OutboxEntry | undefined {
    return this.entries.find((entry) => entry.key === key);
  }

  private patch(key: string, changes: Partial<OutboxEntry>): void {
    this.entries = this.entries.map((entry) =>
      entry.key === key ? { ...entry, ...changes } : entry,
    );
    this.publish();
  }

  private async send(key: string, freshIdsLeft = 1): Promise<void> {
    const entry = this.entry(key);
    if (!entry) return;
    const attempt = (this.attempts.get(key) ?? 0) + 1;
    this.attempts.set(key, attempt);
    const isLatest = () => this.attempts.get(key) === attempt && !!this.entry(key);
    this.patch(key, { status: "saving", error: null });
    const slow = setTimeout(() => {
      if (isLatest() && this.entry(key)?.status === "saving") {
        this.patch(key, { status: "failed", error: NO_ANSWER_YET_ERROR });
      }
    }, SLOW_SAVE_MS);

    try {
      const note = await createTimelineNote(this.recordingId, createOf(entry));
      this.settle(key, note);
    } catch (error) {
      if (!isLatest()) return;
      const failure = failureOf(error);
      if (failure === "idUnusable" && freshIdsLeft > 0) {
        this.patch(key, { id: newNoteId(), writeToken: newNoteId() });
        await this.send(key, freshIdsLeft - 1);
        return;
      }
      this.patch(key, {
        status: "failed",
        error: failure === "idUnusable" ? REFUSED_ERROR : FAILURE_ERRORS[failure],
      });
    } finally {
      clearTimeout(slow);
    }
  }

  /** Any attempt's success saves the note, whatever the entry shows by then. */
  private settle(key: string, note: TimelineNote): void {
    this.entries = this.entries.filter((entry) => entry.key !== key);
    this.attempts.delete(key);
    const known = this.listed.has(note.id) || this.saved.some((saved) => saved.id === note.id);
    if (!known && !this.discards.has(note.id)) this.saved = [...this.saved, note];
    this.publish();
  }

  private async sendDiscard(id: string): Promise<void> {
    const discard = this.discards.get(id);
    if (!discard || discard.inFlight) return;
    discard.inFlight = true;
    try {
      await deleteTimelineNote(this.recordingId, id, { writeToken: discard.token });
      this.discards.delete(id);
    } catch (error) {
      const status = getErrorStatus(error);
      if (status === null || status >= 500) {
        // Sent again when a poll still lists the note.
        discard.inFlight = false;
        return;
      }
      // 404 or 410: no such note, now or later. Anything else won't change
      // on a resend, and the note, if it exists, is shown again.
      this.discards.delete(id);
    }
    this.saved = this.saved.filter((note) => note.id !== id);
    this.publish();
  }

  private publish(): void {
    this.snapshot = {
      entries: this.entries,
      saved: this.saved,
      discarded: new Set(this.discards.keys()),
    };
    for (const listener of this.listeners) listener();
  }
}
