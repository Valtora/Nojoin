import {
  createTimelineNote,
  deleteTimelineNote,
  listTimelineNotes,
  updateTimelineNote,
} from "@/lib/api";
import { getErrorStatus, isRecord } from "@/lib/errors";
import {
  TIMELINE_NOTE_ERROR_CODES,
  type RecordingId,
  type TimelineNote,
  type TimelineNoteConflictDetail,
  type TimelineNoteErrorCode,
} from "@/types";

/**
 * Keeps the processing notes box in step with the recording's earliest head
 * note, the one a migrated free-text note became, across saves and polls.
 *
 * The server says what happened to every write, so nothing here is guessed:
 *
 * - Each write carries a write token. The token stays the same until an
 *   answer arrives, so the requests sent while answers were being lost share
 *   it, and a fresh one is used after. The note keeps the token of the write
 *   that made its revision (`last_write_token`): a refusal or a retried
 *   create showing a token this device sent is this device's own write.
 * - A delete leaves a tombstone holding its token, so a write to a deleted
 *   note gets a 410 whose `code` says whether the delete carried this write's
 *   token (`note_deleted_by_you`) or not (`note_deleted_elsewhere`), and a
 *   retried create can never bring the note back.
 * - The store branches on the refusal's `code`, never on its text.
 *
 * State: `base` (the note the box's text is based on), `pending` (a create's
 * id while its answer is unknown), `clearing` (a note whose delete's answer is
 * unknown), `unansweredEdit` (a note whose edit's answer is unknown),
 * `conflict`, and `dirty` (the box holds unsaved text).
 *
 * | Situation | Event | Outcome |
 * | --- | --- | --- |
 * | no base, no pending | save text | create under a new id; `pending` = id until answered |
 * | pending | save text | create again: same id, same token |
 * | pending | create 201, or 200 with own token | `base` = note; newer text is patched on |
 * | pending | create 200 with another token | conflict "changed" (edited elsewhere since) |
 * | pending or base | 410 (or 404 for an edit) while `clearing` that note | its own delete, or one elsewhere that beat it: the text is a new note under a new id |
 * | pending or base | 410 (or 404 for an edit) otherwise | conflict "deleted"; the id is never written again |
 * | pending | create 409 `note_id_in_use` | once more under a new id |
 * | base | save other text | patch against `base.revision` |
 * | base, with `clearing` or `unansweredEdit` naming it | save the text it holds | patch anyway: the note may be gone, or hold the unanswered edit |
 * | base | patch 409 with own token | own earlier write landed: `base` = it, patch again |
 * | base | patch or delete 409 with another token, the note's content (kind, text, anchors) as `base` had it | a write that changed nothing, such as another tab's resend: `base` = it, the write is sent again |
 * | base | patch or delete 409 with another token, the content changed | conflict "changed" |
 * | base or pending | save empty | delete against the revision |
 * | any | delete 204, 410 or 404 | gone; a delete of an id no note has held leaves a tombstone too, so a create still on its way is refused |
 * | any | no answer, or 5xx | nothing changes but `clearing` (for a delete) or `unansweredEdit` (for an edit); the next save retries under the same token |
 * | pending | delete gets no answer, or 5xx | `pending` ends too: the next text is a new note under a new id, out of the late delete's reach |
 * | `clearing` a note other than `base` (a cleared pending create) | save, or a poll showing that note | that delete is sent again first, under the same token while no answer came between; a failed one fails the save. The note is not listed among the others meanwhile |
 * | any | a 200 or 409 showing the note, or a 410 or 404 for it | its `clearing` and `unansweredEdit` end; a conflict ends both |
 * | any | other 4xx (422, recording gone) | save failed; nothing changes |
 * | conflict | save | refused until resolved |
 * | conflict "changed" | Keep mine / Use theirs | `base` = their note; next save goes over it / the box takes it |
 * | conflict "deleted" | Keep mine / Discard mine | `base` = none; next save makes a new note / the box moves to the next head note |
 * | not dirty | poll | `base` = the polled note, unless the poll is older than `base` |
 * | dirty | poll | `base` stays, so an edit made elsewhere meanwhile comes back as a 409 |
 * | any | poll leaves out `base` | ask the server; gone: deleted. A failed ask is asked again from 2 s, doubling to 30 s; a refused ask is not |
 * | any | last listener leaves | the ask and its timer are cancelled; saves already asked for still run |
 */

/** The API's limit, `TIMELINE_NOTE_BODY_MAX_CHARS` in `timeline_note_public.py`. */
export const NOTE_BODY_MAX_CHARS = 50_000;

/** How long a failed check for a missing note waits before asking again, doubling. */
export const CONFIRM_RETRY_MS = 2_000;
export const CONFIRM_RETRY_MAX_MS = 30_000;

// The characters Python's `str.strip()` removes, which is how the API trims a
// note. `String.prototype.trim` differs: it keeps U+001C-U+001F and U+0085,
// and removes U+FEFF.
const isOuterSpace = (code: number) =>
  (code >= 0x09 && code <= 0x0d) ||
  (code >= 0x1c && code <= 0x20) ||
  code === 0x85 ||
  code === 0xa0 ||
  code === 0x1680 ||
  (code >= 0x2000 && code <= 0x200a) ||
  code === 0x2028 ||
  code === 0x2029 ||
  code === 0x202f ||
  code === 0x205f ||
  code === 0x3000;

/** A note's text as the API stores it. */
export const stripNote = (text: string): string => {
  let start = 0;
  let end = text.length;
  while (start < end && isOuterSpace(text.charCodeAt(start))) start += 1;
  while (end > start && isOuterSpace(text.charCodeAt(end - 1))) end -= 1;
  return text.slice(start, end);
};

const SURROGATE_PAIR = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g;

/** A note's length as the API counts it: code points, not UTF-16 units. */
export const noteLength = (text: string): number =>
  text.length - (text.match(SURROGATE_PAIR)?.length ?? 0);

export type HeadNoteConflict =
  { kind: "changed"; note: TimelineNote } | { kind: "deleted" };

export interface HeadNoteSnapshot {
  /** The edited note's text, or "" when there is none. */
  body: string;
  /** Every other head and tail note, read-only here. */
  otherNotes: readonly TimelineNote[];
  conflict: HeadNoteConflict | null;
}

/**
 * What a note says, to the box and to every reader of it: a write that leaves
 * all of these as they were changed nothing that a text based on the note
 * could have missed.
 */
const CONTENT_FIELDS = [
  "kind",
  "body",
  "anchor_ms",
  "start_ms",
  "end_ms",
  "quoted_text",
] as const;

type KnownNote = Pick<
  TimelineNote,
  "id" | "revision" | (typeof CONTENT_FIELDS)[number]
>;

const asKnown = (note: TimelineNote): KnownNote => ({
  id: note.id,
  revision: note.revision,
  kind: note.kind,
  body: note.body,
  anchor_ms: note.anchor_ms,
  start_ms: note.start_ms,
  end_ms: note.end_ms,
  quoted_text: note.quoted_text,
});

/** Whether the note holds what `target` held, at whatever revision. */
const sameContent = (target: KnownNote | null, note: TimelineNote) =>
  target !== null &&
  CONTENT_FIELDS.every(
    (field) => (target[field] ?? null) === (note[field] ?? null),
  );

const sameNotes = (a: readonly TimelineNote[], b: readonly TimelineNote[]) =>
  a.length === b.length &&
  a.every(
    (note, index) =>
      note.id === b[index].id &&
      note.revision === b[index].revision &&
      note.body === b[index].body,
  );

/** A version 4 UUID without `crypto.randomUUID`, which needs a secure context. */
export const newNoteId = (): string => {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
};

/** What an answer, or its absence, says about a write. */
type Outcome =
  | { kind: "unknown" }
  | { kind: "conflict"; note: TimelineNote }
  | { kind: "gone" }
  | { kind: "notFound" }
  | { kind: "idInUse" }
  | { kind: "refused" };

const errorDetail = (error: unknown): Record<string, unknown> | null => {
  if (!isRecord(error) || !isRecord(error.response)) return null;
  const data = error.response.data;
  return isRecord(data) && isRecord(data.detail) ? data.detail : null;
};

const isNoteErrorCode = (value: unknown): value is TimelineNoteErrorCode =>
  typeof value === "string" &&
  (TIMELINE_NOTE_ERROR_CODES as readonly string[]).includes(value);

const isConflictDetail = (
  detail: Record<string, unknown>,
): detail is Record<string, unknown> & TimelineNoteConflictDetail =>
  isRecord(detail.note) &&
  typeof detail.note.id === "string" &&
  typeof detail.note.body === "string" &&
  typeof detail.note.revision === "number";

/** No answer, or a server error: the write may have landed. */
const outcomeUnknown = (error: unknown) => {
  const status = getErrorStatus(error);
  return status === null || status >= 500;
};

const outcomeOf = (error: unknown): Outcome => {
  if (outcomeUnknown(error)) return { kind: "unknown" };
  const detail = errorDetail(error);
  const code = isNoteErrorCode(detail?.code) ? detail.code : null;
  if (code === "revision_conflict" && detail && isConflictDetail(detail)) {
    return { kind: "conflict", note: detail.note };
  }
  // `note_deleted_by_you` follows only this device's own unanswered delete,
  // which `clearingId` records, so the two codes are told apart there.
  if (code === "note_deleted_by_you" || code === "note_deleted_elsewhere") {
    return { kind: "gone" };
  }
  if (code === "note_not_found") return { kind: "notFound" };
  if (code === "note_id_in_use") return { kind: "idInUse" };
  return { kind: "refused" };
};

const UNRESOLVED_CONFLICT_ERROR =
  "Resolve the conflict with the other device first.";
const TOO_LONG_ERROR = `A note can be at most ${NOTE_BODY_MAX_CHARS} characters.`;

export class HeadNoteSync {
  private polled: TimelineNote[] = [];
  /** The note the box's text is based on. */
  private base: KnownNote | null = null;
  private dirty = false;
  private conflict: HeadNoteConflict | null = null;
  /** Deleted here, or found deleted: a late poll cannot bring them back. */
  private readonly deletedIds = new Set<string>();
  /** A create's id while its answer is unknown. */
  private pendingId: string | null = null;
  /** A note whose delete was sent without an answer. */
  private clearingId: string | null = null;
  /** The revision that delete was made against. */
  private clearingRevision = 1;
  /** A poll asked for that delete to be sent again, and it waits in the queue. */
  private clearQueued = false;
  /** A note whose edit was sent without an answer: it may hold that edit. */
  private unansweredEditId: string | null = null;
  /** The token the next write carries; replaced once an answer arrives. */
  private token = newNoteId();
  private readonly sentTokens = new Set<string>();
  /** The note a poll left out, while the server is asked about it. */
  private checkingId: string | null = null;
  private checkAbort: AbortController | null = null;
  private checkTimer: ReturnType<typeof setTimeout> | null = null;
  private checkDelayMs = CONFIRM_RETRY_MS;
  private queue: Promise<void> = Promise.resolve();
  private readonly listeners = new Set<() => void>();
  private snapshot: HeadNoteSnapshot = {
    body: "",
    otherNotes: [],
    conflict: null,
  };

  constructor(
    private readonly recordingId: RecordingId,
    notes?: readonly TimelineNote[] | null,
  ) {
    this.ingest(notes);
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    // Nothing is checked while nothing listens; take it up again.
    if (this.listeners.size === 1) this.confirmAbsence();
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0) this.stopChecking();
    };
  };

  getSnapshot = () => this.snapshot;

  /** Takes the notes a poll of the recording returned. */
  ingest = (notes: readonly TimelineNote[] | null | undefined) => {
    this.polled = (notes ?? []).filter((note) => !this.deletedIds.has(note.id));
    const conflict = this.conflict;
    if (conflict?.kind === "changed") {
      // Show the other version as it is now, not as the refusal found it.
      const fresh = this.polled.find((note) => note.id === conflict.note.id);
      if (fresh && fresh.revision > conflict.note.revision) {
        this.conflict = { kind: "changed", note: fresh };
      }
    }
    this.adopt();
    // A note this device cleared without an answer is still there: nothing
    // else would ever delete it, so the delete is sent again now.
    const clearing = this.outstandingClear();
    if (clearing && this.polled.some((note) => note.id === clearing)) {
      this.queueClear();
    }
    this.confirmAbsence();
    this.emit();
  };

  /** Whether the box holds text that differs from what it last saved or showed. */
  setDirty = (dirty: boolean) => {
    if (dirty === this.dirty) return;
    this.dirty = dirty;
    if (!dirty) this.adopt();
    this.emit();
  };

  /** Saves the box's text; saves run one at a time, in the order asked. */
  save = (text: string): Promise<void> => this.enqueue(() => this.write(text));

  private enqueue(task: () => Promise<void>): Promise<void> {
    const run = this.queue.then(task);
    this.queue = run.catch(() => undefined);
    return run;
  }

  /**
   * Ends a conflict. "mine": the next save goes over the other version, or
   * makes a new note in place of a deleted one. "theirs": the box takes the
   * other version. Returns the text the box shows now: after a delete, that
   * of whichever head note it moves to, which may match what it showed.
   */
  resolveConflict = (choice: "mine" | "theirs"): string => {
    const conflict = this.conflict;
    if (!conflict) return this.snapshot.body;
    this.conflict = null;
    this.base = conflict.kind === "changed" ? asKnown(conflict.note) : null;
    if (choice === "theirs") {
      this.dirty = false;
      this.adopt();
    }
    this.emit();
    return this.snapshot.body;
  };

  private latest(): KnownNote | null {
    const base = this.base;
    const polledBase = base
      ? this.polled.find((note) => note.id === base.id)
      : undefined;
    // Left out of a poll but not known to be deleted: just created here, or
    // the poll answered out of order (see confirmAbsence).
    if (base && !polledBase && !this.deletedIds.has(base.id)) return base;
    // The box stays on the note it edits; with none, it takes the earliest
    // head note.
    const note =
      polledBase ?? this.polled.find((candidate) => candidate.kind === "head");
    if (!note) return null;
    // A poll answered before this device's own write landed.
    if (base && base.id === note.id && base.revision > note.revision) {
      return base;
    }
    return asKnown(note);
  }

  private adopt() {
    if (!this.dirty && !this.conflict) this.base = this.latest();
    // A poll showed the note a create made: edit it rather than create again.
    if (this.base && this.base.id === this.pendingId) this.pendingId = null;
  }

  private emit() {
    const conflictNoteId =
      this.conflict?.kind === "changed" ? this.conflict.note.id : null;
    // The box's note, a create still unanswered and the note in a conflict
    // notice are each shown already; a note being cleared is on its way out.
    const shown = new Set([
      this.base?.id,
      this.pendingId,
      this.clearingId,
      conflictNoteId,
    ]);
    const otherNotes = this.polled.filter(
      (note) =>
        (note.kind === "head" || note.kind === "tail") && !shown.has(note.id),
    );
    const body = this.base?.body ?? "";
    const previous = this.snapshot;
    const sameOthers = sameNotes(otherNotes, previous.otherNotes);
    if (
      sameOthers &&
      body === previous.body &&
      this.conflict === previous.conflict
    ) {
      return;
    }
    this.snapshot = {
      body,
      otherNotes: sameOthers ? previous.otherNotes : otherNotes,
      conflict: this.conflict,
    };
    for (const listener of this.listeners) listener();
  }

  /** A poll left out the box's note: ask the server before taking it as deleted. */
  private confirmAbsence() {
    const base = this.base;
    const missing =
      base !== null &&
      this.listeners.size > 0 &&
      !this.deletedIds.has(base.id) &&
      !this.polled.some((note) => note.id === base.id);
    if (!missing) {
      this.stopChecking();
      return;
    }
    // Already asking about it, or waiting to ask again.
    if (this.checkingId === base.id) return;
    this.stopChecking();
    this.checkingId = base.id;
    this.ask(base.id);
  }

  private ask(id: string) {
    const abort = new AbortController();
    this.checkAbort = abort;
    void listTimelineNotes(this.recordingId, abort.signal).then(
      (notes) => {
        if (abort.signal.aborted) return;
        this.checkAbort = null;
        this.checkingId = null;
        this.checkDelayMs = CONFIRM_RETRY_MS;
        if (notes.some((note) => note.id === id)) return;
        this.deletedIds.add(id);
        this.polled = this.polled.filter((note) => note.id !== id);
        this.adopt();
        this.emit();
      },
      (error: unknown) => {
        if (abort.signal.aborted) return;
        this.checkAbort = null;
        if (!outcomeUnknown(error)) {
          // Refused: asking again would be refused again. A later poll asks anew.
          this.checkingId = null;
          return;
        }
        const delay = this.checkDelayMs;
        this.checkDelayMs = Math.min(delay * 2, CONFIRM_RETRY_MAX_MS);
        this.checkTimer = setTimeout(() => {
          this.checkTimer = null;
          this.ask(id);
        }, delay);
      },
    );
  }

  private stopChecking() {
    this.checkAbort?.abort();
    this.checkAbort = null;
    if (this.checkTimer !== null) clearTimeout(this.checkTimer);
    this.checkTimer = null;
    this.checkingId = null;
    this.checkDelayMs = CONFIRM_RETRY_MS;
  }

  /** The token for a request; the same until an answer arrives. */
  private sendToken(): string {
    this.sentTokens.add(this.token);
    return this.token;
  }

  /** An answer says how the requests sent under this token went. */
  private answered() {
    this.token = newNoteId();
  }

  /** Whether the note is as a write of this device left it. */
  private mine(note: TimelineNote): boolean {
    const token = note.last_write_token;
    return typeof token === "string" && this.sentTokens.has(token);
  }

  /** The server holds this note: the box's text is based on it. */
  private settle(note: TimelineNote) {
    this.base = asKnown(note);
    if (this.pendingId === note.id) this.pendingId = null;
    this.answeredFor(note.id);
    this.emit();
  }

  /** An answer showed the note as it is: no write to it is unanswered. */
  private answeredFor(id: string) {
    if (this.clearingId === id) this.clearingId = null;
    if (this.unansweredEditId === id) this.unansweredEditId = null;
  }

  /** The note is gone: deleted here, or found deleted. */
  private forget(id: string) {
    this.deletedIds.add(id);
    if (this.pendingId === id) this.pendingId = null;
    this.answeredFor(id);
    this.polled = this.polled.filter((note) => note.id !== id);
    if (this.base?.id === id) this.base = null;
    this.emit();
  }

  private raise(conflict: HeadNoteConflict): never {
    // Every conflict follows an answer showing the note as it is, or gone,
    // so no earlier write of this device's is unanswered.
    this.clearingId = null;
    this.unansweredEditId = null;
    this.conflict = conflict;
    this.emit();
    throw new Error(UNRESOLVED_CONFLICT_ERROR);
  }

  private async write(text: string) {
    if (this.conflict) throw new Error(UNRESOLVED_CONFLICT_ERROR);
    const body = stripNote(text);
    if (noteLength(body) > NOTE_BODY_MAX_CHARS) throw new Error(TOO_LONG_ERROR);
    await this.finishClear();
    const base = this.base;
    if (!body) {
      const id = base?.id ?? this.pendingId;
      if (id) await this.remove(id, base?.revision ?? 1, base);
    } else if (!base) {
      await this.create(body);
    } else if (
      body !== base.body ||
      this.clearingId === base.id ||
      this.unansweredEditId === base.id
    ) {
      // After an unanswered delete or edit the note may be gone, or hold the
      // edit, even if the text matches it, so the edit is sent to find out.
      await this.patch(base, body);
    }
  }

  /**
   * The note an unanswered delete was for, when the box no longer edits it:
   * a cleared create whose answer was lost. While the box edits the note, its
   * next write finds out what became of it.
   */
  private outstandingClear(): string | null {
    const id = this.clearingId;
    return id === this.base?.id ? null : id;
  }

  /**
   * Sends that delete again, before anything else is written. It goes under
   * the current token, which is still the one the delete carried unless an
   * answer refused this resend before: the server then takes it as a retry
   * of that delete. A 204, 404 or 410 means the note is gone. Any other
   * failure fails the save that asked for it, and the next one sends it again.
   */
  private async finishClear() {
    const id = this.outstandingClear();
    if (id !== null) await this.remove(id, this.clearingRevision, null);
  }

  private queueClear() {
    if (this.clearQueued) return;
    this.clearQueued = true;
    void this.enqueue(() => {
      this.clearQueued = false;
      return this.finishClear();
    }).catch(() => undefined);
  }

  /**
   * A write found its note deleted, or never there. When this device was
   * clearing it (its own delete landed, or one elsewhere beat it), the box's
   * text becomes a new note, as after any clear; otherwise it is a conflict.
   */
  private async deleted(id: string, body: string): Promise<void> {
    const ours = this.clearingId === id;
    this.forget(id);
    if (!ours) this.raise({ kind: "deleted" });
    await this.create(body);
  }

  private async create(body: string, renamed = false): Promise<void> {
    const id = this.pendingId ?? newNoteId();
    this.pendingId = id;
    let note: TimelineNote;
    try {
      note = await createTimelineNote(this.recordingId, {
        id,
        kind: "head",
        body,
        write_token: this.sendToken(),
      });
    } catch (error) {
      const outcome = outcomeOf(error);
      if (outcome.kind === "unknown") throw error;
      this.answered();
      if (outcome.kind === "gone") return this.deleted(id, body);
      if (outcome.kind === "idInUse" && !renamed) {
        // The id is taken by a note elsewhere: once more under a new one.
        this.pendingId = null;
        return this.create(body, true);
      }
      throw error;
    }
    this.answered();
    this.pendingId = null;
    // A retry found the note an earlier attempt made, changed since elsewhere.
    if (!this.mine(note)) this.raise({ kind: "changed", note });
    this.settle(note);
    // A retry gets the note as an earlier attempt made it; this text is newer.
    if (note.body !== body) await this.patch(asKnown(note), body);
  }

  private async patch(target: KnownNote, body: string): Promise<void> {
    let saved: TimelineNote;
    try {
      saved = await updateTimelineNote(this.recordingId, target.id, {
        expected_revision: target.revision,
        body,
        write_token: this.sendToken(),
      });
    } catch (error) {
      const outcome = outcomeOf(error);
      if (outcome.kind === "unknown") {
        this.unansweredEditId = target.id;
        throw error;
      }
      this.answered();
      if (outcome.kind === "conflict") {
        if (!this.mine(outcome.note) && !sameContent(target, outcome.note)) {
          this.raise({ kind: "changed", note: outcome.note });
        }
        // This device's earlier save landed, only its answer lost; or a write
        // elsewhere left the note as the box's text found it.
        this.settle(outcome.note);
        if (outcome.note.body !== body) {
          await this.patch(asKnown(outcome.note), body);
        }
        return;
      }
      if (outcome.kind === "gone" || outcome.kind === "notFound") {
        return this.deleted(target.id, body);
      }
      throw error;
    }
    this.answered();
    this.settle(saved);
  }

  /**
   * Deletes the note, against the revision the box's text started from.
   * `target` is the note as the box knew it, or null when it never saw it.
   */
  private async remove(
    id: string,
    revision: number,
    target: KnownNote | null,
  ): Promise<void> {
    try {
      await deleteTimelineNote(this.recordingId, id, {
        expectedRevision: revision,
        writeToken: this.sendToken(),
      });
    } catch (error) {
      const outcome = outcomeOf(error);
      if (outcome.kind === "unknown") {
        this.clearingId = id;
        this.clearingRevision = revision;
        // The cleared create is never sent again: text typed next is a new
        // note under a new id, which this delete, landing late, cannot reach.
        // (Before that text is sent, finishClear sends this delete again.)
        if (this.pendingId === id) this.pendingId = null;
        throw error;
      }
      this.answered();
      if (outcome.kind === "conflict") {
        if (!this.mine(outcome.note) && !sameContent(target, outcome.note)) {
          this.raise({ kind: "changed", note: outcome.note });
        }
        // This device's own earlier write made that revision, or one elsewhere
        // left the note as the box knew it: clear it too.
        this.settle(outcome.note);
        return this.remove(id, outcome.note.revision, asKnown(outcome.note));
      }
      if (outcome.kind !== "gone" && outcome.kind !== "notFound") throw error;
      // Deleted already, by anyone, or not on this recording: what clearing
      // the box asked for.
      this.forget(id);
      return;
    }
    this.answered();
    this.forget(id);
  }
}
