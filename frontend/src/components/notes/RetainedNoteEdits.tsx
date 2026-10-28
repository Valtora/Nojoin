"use client";

import { useSyncExternalStore } from "react";

import { draftIsUnchanged, type NoteEditDrafts } from "@/lib/notes/noteEditDrafts";
import type { EditableNote } from "@/lib/notes/useNoteEdits";

import Button from "../ui/Button";
import { NOTE_BUTTON_TOUCH } from "./noteButtons";

/** Makes retained text reachable when an authoritative listing removes its note. */
export default function RetainedNoteEdits({ owner, notes, onEdit }: {
  owner: NoteEditDrafts;
  notes: readonly EditableNote[] | null;
  onEdit: (noteId: string, anchor: HTMLElement) => void;
}) {
  const drafts = useSyncExternalStore(owner.subscribe, owner.getSnapshot, owner.getSnapshot);
  if (notes === null) return null;
  const listed = new Set(notes.map((note) => note.id));
  const missing = [...drafts].filter(([id, draft]) => !listed.has(id) && !draftIsUnchanged(draft));
  if (missing.length === 0) return null;
  return <section aria-label="Unsaved note edits" className="mt-3 space-y-3 border-t border-surface-border pt-3">
    <p className="text-xs text-contrast-helper">These notes are no longer listed. Your unsaved edits are kept here.</p>
    {missing.map(([id, draft]) => <div key={id} className="space-y-2">
      <p className="whitespace-pre-wrap break-words text-sm text-foreground">{draft.text}</p>
      {draft.refusal ? <p className="text-xs text-status-danger-fg">{draft.refusal}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" className={NOTE_BUTTON_TOUCH}
          onClick={(event) => onEdit(id, event.currentTarget)}>Edit retained draft</Button>
        <Button size="sm" className={NOTE_BUTTON_TOUCH}
          onClick={() => owner.discard(id)}>Discard edit draft</Button>
      </div>
    </div>)}
  </section>;
}
