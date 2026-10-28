import { stripNote } from "./headNoteSync";
import { holdPendingNoteWrite } from "./noteOutboxes";
import type { EditableNote, NoteEditRefusal } from "./useNoteEdits";

export interface NoteEditAnswer {
  note: EditableNote;
  conflict: boolean;
}

export type NoteWriter = (
  note: EditableNote,
  text: string,
  answer?: (result: NoteEditAnswer) => void,
) => Promise<NoteEditRefusal>;

export interface NoteEditDraft {
  base: EditableNote;
  text: string;
  refusal: string | null;
  candidate?: EditableNote;
  pending?: symbol;
  version: number;
}

export const draftIsUnchanged = (draft: NoteEditDraft) =>
  !draft.refusal && !draft.candidate && !draft.pending && stripNote(draft.text) === stripNote(draft.base.body);

/** One recording's saved-note drafts, retained above every presentation. */
export class NoteEditDrafts {
  private snapshot: ReadonlyMap<string, NoteEditDraft> = new Map();
  private readonly listeners = new Set<() => void>();
  private release: (() => void) | null = null;
  private active = true;

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  private update(id: string, draft: NoteEditDraft | null) {
    const next = new Map(this.snapshot);
    if (draft) next.set(id, draft);
    else next.delete(id);
    this.snapshot = next;
    this.guard();
    for (const listener of this.listeners) listener();
  }

  /** A clean reopened editor may follow the server; a dirty one keeps its base. */
  open = (note: EditableNote) => {
    const draft = this.snapshot.get(note.id);
    if (draft && (!draftIsUnchanged(draft) || (draft.base.revision ?? 0) >= (note.revision ?? 0))) return;
    this.update(note.id, { base: { ...note }, text: note.body, refusal: null, version: 0 });
  };

  change = (note: EditableNote, text: string) => {
    this.open(note);
    const draft = this.snapshot.get(note.id)!;
    this.update(note.id, { ...draft, text, version: draft.version + 1 });
  };

  discard = (id: string) => { this.update(id, null); };

  /** Reacquires after React's development effect replay, without dropping drafts. */
  resume = () => {
    this.active = true;
    this.guard();
  };

  private guard() {
    if (!this.active) return;
    const needed = [...this.snapshot.values()].some((draft) => !draftIsUnchanged(draft));
    if (needed) this.release ??= holdPendingNoteWrite();
    else this.releaseHold();
  }

  dispose = () => {
    this.active = false;
    this.releaseHold();
  };

  private releaseHold() {
    this.release?.();
    this.release = null;
  }

  /** Undefined means newer text remains; null permits closing this editor. */
  save = (note: EditableNote, write: NoteWriter): Promise<NoteEditRefusal | undefined> =>
    this.submit(note, write, false);

  remove = (note: EditableNote, write: NoteWriter): Promise<NoteEditRefusal | undefined> =>
    this.submit(note, write, true);

  private async submit(note: EditableNote, write: NoteWriter, deleting: boolean) {
    this.open(note);
    const draft = this.snapshot.get(note.id)!;
    if (draft.pending) return undefined;
    const pending = Symbol("note draft write");
    this.update(note.id, { ...draft, pending });
    let answer: NoteEditAnswer | undefined;
    let refusal: NoteEditRefusal;
    try {
      refusal = await write(draft.candidate ?? draft.base, draft.text, (result) => { answer = result; });
    } catch {
      refusal = "No answer from Nojoin. Try again.";
    }
    const current = this.snapshot.get(note.id);
    // A discarded/replaced draft cannot be resurrected by an old completion.
    if (!current || current.pending !== pending) return undefined;
    if (deleting && refusal === null) {
      this.discard(note.id);
      return null;
    }
    const base = answer && (!answer.conflict || refusal === null) ? answer.note : current.base;
    const next = { ...current, base, pending: undefined, refusal,
      candidate: answer?.conflict && refusal !== null ? answer.note : refusal ? current.candidate : undefined };
    this.update(note.id, next);
    return refusal ?? (current.version === draft.version ? null : undefined);
  }
}
