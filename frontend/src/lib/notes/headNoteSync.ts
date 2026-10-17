import {
  createTimelineNote,
  deleteTimelineNote,
  updateTimelineNote,
} from "@/lib/api";
import { getErrorStatus, isRecord } from "@/lib/errors";
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
 *   create succeeds, so a retried create cannot make a second note.
 * - An edit patches the note against the revision the box's text started
 *   from. While the box holds unsaved text, a poll never moves that base, so
 *   an edit made meanwhile on another device comes back as a conflict rather
 *   than being overwritten.
 * - Clearing the box deletes the note, against the same revision, so it
 *   cannot delete an edit made meanwhile on another device either.
 * - A poll answered before this device's own write landed cannot put older
 *   text back, or bring back a note this device deleted. A note is taken as
 *   deleted elsewhere only when two polls running leave it out, since polls
 *   can answer out of order.
 * - The box stays on the note it edits. If another device made a head note
 *   at the same moment, that one is listed with the other notes.
 * - A conflict (changed or deleted on another device) stops saving until the
 *   user keeps their text or takes the other version.
 */

/** The API's limit, `TIMELINE_NOTE_BODY_MAX_CHARS` in `timeline_note_public.py`. */
export const NOTE_BODY_MAX_CHARS = 50_000;

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

const asKnown = (note: TimelineNote): KnownNote => ({
  id: note.id,
  revision: note.revision,
  body: note.body,
});

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

const NO_CONFLICT_ERROR = "Resolve the conflict with the other device first.";

export class HeadNoteSync {
  private polled: TimelineNote[] = [];
  /** The note the box's text is based on. */
  private base: KnownNote | null = null;
  private ownWrite: KnownNote | null = null;
  /** Every note id a poll has shown. */
  private readonly seenIds = new Set<string>();
  /** Polls running that have not shown `base`, once one had. */
  private absentPolls = 0;
  private dirty = false;
  private conflict: HeadNoteConflict | null = null;
  private readonly deletedIds = new Set<string>();
  private pendingCreateId: string | null = null;
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
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.snapshot;

  /** Takes the notes a poll of the recording returned. */
  ingest = (notes: readonly TimelineNote[] | null | undefined) => {
    this.polled = (notes ?? []).filter((note) => !this.deletedIds.has(note.id));
    const base = this.base;
    this.absentPolls =
      base && this.seenIds.has(base.id) && !this.polled.some((note) => note.id === base.id)
        ? this.absentPolls + 1
        : 0;
    for (const note of this.polled) this.seenIds.add(note.id);
    const conflict = this.conflict;
    if (conflict?.kind === "changed") {
      // Show the other version as it is now, not as the refusal found it.
      const fresh = this.polled.find((note) => note.id === conflict.note.id);
      if (fresh && fresh.revision > conflict.note.revision) {
        this.conflict = { kind: "changed", note: fresh };
      }
    }
    this.adopt();
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
   * other version.
   */
  resolveConflict = (choice: "mine" | "theirs") => {
    const conflict = this.conflict;
    if (!conflict) return;
    this.conflict = null;
    this.base = conflict.kind === "changed" ? asKnown(conflict.note) : null;
    this.absentPolls = 0;
    if (conflict.kind === "deleted") this.ownWrite = null;
    if (choice === "theirs") {
      this.dirty = false;
      this.adopt();
    }
    this.emit();
  };

  private latest(): KnownNote | null {
    const base = this.base;
    const polledBase = base
      ? this.polled.find((note) => note.id === base.id)
      : undefined;
    // Missing from a poll: just created here, so no poll has shown it yet, or
    // one poll answered out of order. Missing from two polls running: deleted
    // on another device.
    if (base && !polledBase && (!this.seenIds.has(base.id) || this.absentPolls < 2)) {
      return base;
    }
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
    if (!this.dirty && !this.conflict) {
      const next = this.latest();
      if (next?.id !== this.base?.id) this.absentPolls = 0;
      this.base = next;
    }
    // The note a lost create response made: edit it rather than create again.
    if (this.base && this.base.id === this.pendingCreateId) this.pendingCreateId = null;
  }

  private emit() {
    const baseId = this.base?.id;
    this.snapshot = {
      body: this.base?.body ?? "",
      otherNotes: this.polled.filter(
        (note) => (note.kind === "head" || note.kind === "tail") && note.id !== baseId,
      ),
      conflict: this.conflict,
    };
    for (const listener of this.listeners) listener();
  }

  private recordWrite(note: TimelineNote) {
    this.ownWrite = asKnown(note);
    this.base = this.ownWrite;
    this.absentPolls = 0;
    this.emit();
  }

  private async write(text: string) {
    if (this.conflict) throw new Error(NO_CONFLICT_ERROR);
    const body = text.trim();
    const target = this.base;
    if (!body) {
      if (target) await this.remove(target);
    } else if (target) {
      await this.patch(target, body);
    } else {
      await this.create(body);
    }
  }

  /** Records a refusal as a conflict when another device caused it. */
  private refused(error: unknown): never {
    const latest = conflictingNote(error);
    if (latest) {
      this.conflict = { kind: "changed", note: latest };
    } else if (getErrorStatus(error) === 404) {
      this.conflict = { kind: "deleted" };
    }
    this.emit();
    throw error;
  }

  private async remove(target: KnownNote) {
    try {
      // Against its revision, so clearing the box cannot delete an edit
      // another device made meanwhile.
      await deleteTimelineNote(this.recordingId, target.id, target.revision);
    } catch (error) {
      if (getErrorStatus(error) !== 404) this.refused(error);
    }
    this.deletedIds.add(target.id);
    if (this.pendingCreateId === target.id) this.pendingCreateId = null;
    this.polled = this.polled.filter((note) => note.id !== target.id);
    this.ownWrite = null;
    this.base = null;
    this.absentPolls = 0;
    this.emit();
  }

  private async patch(target: KnownNote, body: string) {
    let saved: TimelineNote;
    try {
      saved = await updateTimelineNote(this.recordingId, target.id, {
        expected_revision: target.revision,
        body,
      });
    } catch (error) {
      this.refused(error);
    }
    this.recordWrite(saved);
  }

  private async create(body: string) {
    this.pendingCreateId ??= newNoteId();
    const created = await createTimelineNote(this.recordingId, {
      id: this.pendingCreateId,
      kind: "head",
      body,
    });
    this.pendingCreateId = null;
    if (created.body !== body) {
      // A retry gets the note as the first attempt made it, whose response
      // was lost; this text is newer.
      await this.patch(asKnown(created), body);
      return;
    }
    this.recordWrite(created);
  }
}
