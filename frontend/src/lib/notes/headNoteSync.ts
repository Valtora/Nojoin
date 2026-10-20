import {
  createTimelineNote,
  deleteTimelineNote,
  listTimelineNotes,
  updateTimelineNote,
} from "@/lib/api";
import { getErrorDetail, getErrorStatus, isRecord } from "@/lib/errors";
import type {
  RecordingId,
  TimelineNote,
  TimelineNoteConflictDetail,
} from "@/types";

/**
 * Keeps the processing notes box in step with the recording's earliest head
 * note, the one a migrated free-text note became, across saves and polls.
 *
 * - Typing into an empty box creates the note, with a client id kept until a
 *   create succeeds, so a retried create cannot make a second note. A retry
 *   that finds the note changed since an earlier attempt made it raises a
 *   conflict instead of writing over it, and so does one whose note a poll
 *   showed and the server no longer has: the server keeps no record of
 *   deleted ids, so the retry would bring the deleted note back.
 * - An edit patches the note against the revision the box's text started
 *   from. While the box holds unsaved text, a poll never moves that base, so
 *   an edit made meanwhile on another device comes back as a conflict rather
 *   than being overwritten. A refusal caused by this device's own earlier
 *   save, whose response was lost, is not a conflict.
 * - Clearing the box deletes the note, against the same revision, so it
 *   cannot delete an edit made meanwhile on another device either. A note
 *   already gone counts as deleted. An edit whose note is gone is a conflict,
 *   unless this device's own unanswered delete may have removed it: then the
 *   text becomes new notes, as after any clear.
 * - A poll answered before this device's own write landed cannot put older
 *   text back, or bring back a note this device deleted. Polls can answer
 *   out of order, so when one leaves out the box's note, the store asks the
 *   server again before taking the note as deleted elsewhere, and keeps
 *   asking, less often each time, while that request fails: the page hands
 *   on only polls that changed, so a later poll may never come.
 * - The box stays on the note it edits. If another device made a head note
 *   at the same moment, that one is listed with the other notes.
 * - A conflict (changed or deleted on another device) stops saving until the
 *   user keeps their text or takes the other version.
 */

/** The API's limit, `TIMELINE_NOTE_BODY_MAX_CHARS` in `timeline_note_public.py`. */
export const NOTE_BODY_MAX_CHARS = 50_000;

/** The API's 404 for a missing note, `NOTE_NOT_FOUND_DETAIL` in `routes_timeline_notes.py`. */
const NOTE_NOT_FOUND_DETAIL = "Note not found";

/** Unanswered texts remembered per revision: the one in flight and the two before it. */
const ATTEMPT_MEMORY = 3;

/** How long a failed check for a missing note waits before asking again, doubling. */
const CONFIRM_RETRY_MS = 2_000;
const CONFIRM_RETRY_MAX_MS = 30_000;

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
  | { kind: "changed"; note: TimelineNote }
  | { kind: "deleted" };

export interface HeadNoteSnapshot {
  /** The edited note's text, or "" when there is none. */
  body: string;
  /** Every other head and tail note, read-only here. */
  otherNotes: readonly TimelineNote[];
  conflict: HeadNoteConflict | null;
}

interface KnownNote {
  id: string;
  revision: number;
  body: string;
}

/** Texts sent against one revision of a note (0: its create) without an answer. */
interface Attempts {
  id: string;
  revision: number;
  bodies: string[];
}

const asKnown = (note: TimelineNote): KnownNote => ({
  id: note.id,
  revision: note.revision,
  body: note.body,
});

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

const isConflictDetail = (value: unknown): value is TimelineNoteConflictDetail =>
  isRecord(value) &&
  isRecord(value.note) &&
  typeof value.note.id === "string" &&
  typeof value.note.body === "string" &&
  typeof value.note.revision === "number";

const conflictingNote = (error: unknown): TimelineNote | null => {
  if (getErrorStatus(error) !== 409 || !isRecord(error)) return null;
  const response = error.response;
  const data = isRecord(response) ? response.data : null;
  const detail = isRecord(data) ? data.detail : null;
  return isConflictDetail(detail) ? detail.note : null;
};

/** The note is gone, as against its recording being gone (also a 404). */
const noteGone = (error: unknown) =>
  getErrorStatus(error) === 404 && getErrorDetail(error) === NOTE_NOT_FOUND_DETAIL;

/** No answer, or a server error: the write may have landed. */
const outcomeUnknown = (error: unknown) => {
  const status = getErrorStatus(error);
  return status === null || status >= 500;
};

const UNRESOLVED_CONFLICT_ERROR = "Resolve the conflict with the other device first.";
const TOO_LONG_ERROR = `A note can be at most ${NOTE_BODY_MAX_CHARS} characters.`;

export class HeadNoteSync {
  private polled: TimelineNote[] = [];
  /** The note the box's text is based on. */
  private base: KnownNote | null = null;
  private ownWrite: KnownNote | null = null;
  private dirty = false;
  private conflict: HeadNoteConflict | null = null;
  /** Deleted here, or found deleted elsewhere: a late poll cannot bring them back. */
  private readonly deletedIds = new Set<string>();
  /** The note a poll left out, while the server is asked whether it still exists. */
  private confirmingId: string | null = null;
  private pendingCreateId: string | null = null;
  /** A poll showed the note the pending create made. */
  private pendingSeen = false;
  /** A delete of this note went unanswered, so it may be gone by this device's hand. */
  private maybeDeletedId: string | null = null;
  private confirmRetry: ReturnType<typeof setTimeout> | null = null;
  private confirmDelayMs = CONFIRM_RETRY_MS;
  private attempts: Attempts | null = null;
  private queue: Promise<void> = Promise.resolve();
  private readonly listeners = new Set<() => void>();
  private snapshot: HeadNoteSnapshot = { body: "", otherNotes: [], conflict: null };

  constructor(
    private readonly recordingId: RecordingId,
    notes?: readonly TimelineNote[] | null,
  ) {
    this.ingest(notes);
  }

  subscribe = (listener: () => void) => {
    const first = this.listeners.size === 0;
    this.listeners.add(listener);
    // A check for a missing note stops while nothing listens; take it up.
    if (first) this.confirmAbsence();
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0) this.stopConfirmRetry();
    };
  };

  getSnapshot = () => this.snapshot;

  /** Takes the notes a poll of the recording returned. */
  ingest = (notes: readonly TimelineNote[] | null | undefined) => {
    this.polled = (notes ?? []).filter((note) => !this.deletedIds.has(note.id));
    const pendingId = this.pendingCreateId;
    if (pendingId && this.polled.some((note) => note.id === pendingId)) {
      this.pendingSeen = true;
    }
    const conflict = this.conflict;
    if (conflict?.kind === "changed") {
      // Show the other version as it is now, not as the refusal found it.
      const fresh = this.polled.find((note) => note.id === conflict.note.id);
      if (fresh && fresh.revision > conflict.note.revision) {
        this.conflict = { kind: "changed", note: fresh };
      }
    }
    this.adopt();
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
  save = (text: string): Promise<void> => {
    const run = this.queue.then(() => this.write(text));
    this.queue = run.catch(() => undefined);
    return run;
  };

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
    if (conflict.kind === "deleted") this.ownWrite = null;
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
    const note = polledBase ?? this.polled.find((candidate) => candidate.kind === "head");
    if (!note) return null;
    const own = this.ownWrite;
    return own && own.id === note.id && own.revision > note.revision
      ? own
      : asKnown(note);
  }

  private adopt() {
    if (!this.dirty && !this.conflict) this.base = this.latest();
    // The note a lost create response made: edit it rather than create again.
    if (this.base && this.base.id === this.pendingCreateId) this.setPending(null);
  }

  private setPending(id: string | null) {
    this.pendingCreateId = id;
    this.pendingSeen = false;
  }

  /** A poll left out the box's note: ask the server before taking it as deleted. */
  private confirmAbsence() {
    const base = this.base;
    if (
      !base ||
      this.confirmingId === base.id ||
      this.deletedIds.has(base.id) ||
      this.polled.some((note) => note.id === base.id)
    ) {
      return;
    }
    this.stopConfirmRetry();
    this.confirmingId = base.id;
    void listTimelineNotes(this.recordingId)
      .then(
        (notes) => {
          this.confirmDelayMs = CONFIRM_RETRY_MS;
          if (notes.some((note) => note.id === base.id)) return;
          this.deletedIds.add(base.id);
          this.polled = this.polled.filter((note) => note.id !== base.id);
          this.adopt();
          this.emit();
        },
        // Unanswered: ask again later, while the note is still missing.
        () => this.retryConfirmation(),
      )
      .finally(() => {
        if (this.confirmingId === base.id) this.confirmingId = null;
      });
  }

  private retryConfirmation() {
    if (this.listeners.size === 0) return;
    const delay = this.confirmDelayMs;
    this.confirmDelayMs = Math.min(delay * 2, CONFIRM_RETRY_MAX_MS);
    this.confirmRetry = setTimeout(() => {
      this.confirmRetry = null;
      this.confirmAbsence();
    }, delay);
  }

  private stopConfirmRetry() {
    if (this.confirmRetry === null) return;
    clearTimeout(this.confirmRetry);
    this.confirmRetry = null;
  }

  private emit() {
    const conflictNoteId =
      this.conflict?.kind === "changed" ? this.conflict.note.id : null;
    // The box's note, a create still unconfirmed and the note in a conflict
    // notice are each shown already.
    const shown = new Set([this.base?.id, this.pendingCreateId, conflictNoteId]);
    const otherNotes = this.polled.filter(
      (note) => (note.kind === "head" || note.kind === "tail") && !shown.has(note.id),
    );
    const body = this.base?.body ?? "";
    const previous = this.snapshot;
    const sameOthers = sameNotes(otherNotes, previous.otherNotes);
    if (sameOthers && body === previous.body && this.conflict === previous.conflict) {
      return;
    }
    this.snapshot = {
      body,
      otherNotes: sameOthers ? previous.otherNotes : otherNotes,
      conflict: this.conflict,
    };
    for (const listener of this.listeners) listener();
  }

  private recordWrite(note: TimelineNote) {
    this.ownWrite = asKnown(note);
    this.base = this.ownWrite;
    this.attempts = null;
    if (this.maybeDeletedId === note.id) this.maybeDeletedId = null;
    this.emit();
  }

  /** The note is gone: deleted here, or found deleted. */
  private forget(id: string) {
    this.deletedIds.add(id);
    if (this.pendingCreateId === id) this.setPending(null);
    if (this.attempts?.id === id) this.attempts = null;
    if (this.maybeDeletedId === id) this.maybeDeletedId = null;
    this.polled = this.polled.filter((note) => note.id !== id);
    this.ownWrite = null;
    this.base = null;
    this.emit();
  }

  private noteAttempt(id: string, revision: number, body: string) {
    const attempts = this.attempts;
    if (attempts && attempts.id === id && attempts.revision === revision) {
      const earlier = attempts.bodies.filter((sent) => sent !== body);
      attempts.bodies = [...earlier, body].slice(-ATTEMPT_MEMORY);
    } else {
      this.attempts = { id, revision, bodies: [body] };
    }
  }

  /** Whether the note is what one of this device's unanswered writes made. */
  private madeHere(note: TimelineNote) {
    const attempts = this.attempts;
    return (
      attempts !== null &&
      attempts.id === note.id &&
      note.revision === attempts.revision + 1 &&
      attempts.bodies.includes(note.body)
    );
  }

  private async write(text: string) {
    if (this.conflict) throw new Error(UNRESOLVED_CONFLICT_ERROR);
    const body = stripNote(text);
    if (noteLength(body) > NOTE_BODY_MAX_CHARS) throw new Error(TOO_LONG_ERROR);
    const target = this.base;
    if (!body) {
      if (target) {
        await this.remove(target);
      } else if (this.pendingCreateId) {
        // A create whose answer was lost may have made the note.
        await this.remove({ id: this.pendingCreateId, revision: 1, body: "" }, true);
      }
    } else if (!target) {
      await this.create(body);
    } else if (body !== target.body) {
      await this.patch(target, body);
    }
  }

  /** Records a refusal as a conflict when another device caused it. */
  private refused(error: unknown): never {
    const latest = conflictingNote(error);
    if (latest) {
      this.conflict = { kind: "changed", note: latest };
    } else if (noteGone(error)) {
      this.conflict = { kind: "deleted" };
    }
    this.emit();
    throw error;
  }

  /** Deletes the note; `pending`: the one a create may have made. */
  private async remove(target: KnownNote, pending = false): Promise<void> {
    try {
      // Against its revision, so clearing the box cannot delete an edit
      // another device made meanwhile.
      await deleteTimelineNote(this.recordingId, target.id, target.revision);
    } catch (error) {
      const latest = conflictingNote(error);
      if (latest && this.madeHere(latest)) {
        await this.remove(asKnown(latest));
        return;
      }
      if (!noteGone(error)) {
        if (outcomeUnknown(error)) this.maybeDeletedId = target.id;
        this.refused(error);
      }
      if (pending) {
        // The create has not made the note, or not yet. One it makes later
        // reaches the box on a poll, to be cleared again, so its id is not
        // taken as deleted.
        this.setPending(null);
        this.attempts = null;
        return;
      }
    }
    this.forget(target.id);
  }

  private async patch(target: KnownNote, body: string): Promise<void> {
    this.noteAttempt(target.id, target.revision, body);
    let saved: TimelineNote;
    try {
      saved = await updateTimelineNote(this.recordingId, target.id, {
        expected_revision: target.revision,
        body,
      });
    } catch (error) {
      const latest = conflictingNote(error);
      if (latest && this.madeHere(latest)) {
        // This device's earlier save landed; only its answer was lost.
        this.recordWrite(latest);
        if (latest.body !== body) await this.patch(asKnown(latest), body);
        return;
      }
      if (noteGone(error) && this.maybeDeletedId === target.id) {
        // This device's own delete landed; only its answer was lost. The
        // text typed since is new notes, as after any clear.
        this.forget(target.id);
        await this.create(body);
        return;
      }
      this.refused(error);
    }
    this.recordWrite(saved);
  }

  private async create(body: string, renamed = false): Promise<void> {
    const id = this.pendingCreateId ?? newNoteId();
    if (id !== this.pendingCreateId) this.setPending(id);
    if (this.pendingSeen) await this.ensurePendingExists(id);
    this.noteAttempt(id, 0, body);
    let created: TimelineNote;
    try {
      created = await createTimelineNote(this.recordingId, { id, kind: "head", body });
    } catch (error) {
      if (!renamed && getErrorStatus(error) === 409 && !conflictingNote(error)) {
        // The id is taken by a note elsewhere: once more under a new one.
        this.setPending(null);
        this.attempts = null;
        await this.create(body, true);
        return;
      }
      throw error;
    }
    this.setPending(null);
    if (!this.madeHere(created)) {
      // A retry found the note an earlier attempt made, changed since on
      // another device.
      this.attempts = null;
      this.conflict = { kind: "changed", note: created };
      this.emit();
      throw new Error(UNRESOLVED_CONFLICT_ERROR);
    }
    this.recordWrite(created);
    // A retry gets the note as an earlier attempt made it; this text is newer.
    if (created.body !== body) await this.patch(asKnown(created), body);
  }

  /**
   * A poll showed the note an earlier create made. If the server no longer
   * has it, it was deleted elsewhere, and a retry would make it again.
   */
  private async ensurePendingExists(id: string) {
    const notes = await listTimelineNotes(this.recordingId);
    if (notes.some((note) => note.id === id)) return;
    this.forget(id);
    this.attempts = null;
    this.conflict = { kind: "deleted" };
    this.emit();
    throw new Error(UNRESOLVED_CONFLICT_ERROR);
  }
}
