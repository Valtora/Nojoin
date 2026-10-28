"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import type { InlineNote } from "@/lib/notes/noteOutbox";
import { NoteEditDrafts, type NoteEditAnswer, type NoteWriter } from "@/lib/notes/noteEditDrafts";
import type { EditableNote, NoteEditRefusal } from "@/lib/notes/useNoteEdits";
import type { TimelineNote } from "@/types";

import NoteOnSelectionPopover from "../notes/NoteOnSelectionPopover";

import { isSavedNote } from "./_hooks/useNoteHighlights";
import { formatTime } from "./transcriptDisplay";

/** The note being edited, and what its editor sits against. */
export interface EditingNote {
  noteId: string;
  /** The note line's Edit button, or the marked words that were clicked. */
  anchor: HTMLElement;
}

export interface NoteEditHandlers {
  onEditNote: NoteWriter;
  onDeleteNote?: (note: EditableNote, answer?: (result: NoteEditAnswer) => void) => Promise<NoteEditRefusal>;
  noteDrafts?: NoteEditDrafts;
}

interface TranscriptNoteEditorProps extends NoteEditHandlers {
  editing: EditingNote | null;
  /** The shown notes locate the editor; retained drafts own their save baseline. */
  notes: readonly (TimelineNote | InlineNote)[] | null | undefined;
  onClose: () => void;
}

/** Where a note is, for its editor's name: "at 01:05", or on the whole meeting. */
export const noteWhere = (note: EditableNote): string => {
  if (note.kind === "head") return "before the transcript";
  if (note.kind === "tail") return "after the transcript";
  const startMs = note.kind === "selection" ? note.start_ms : note.anchor_ms;
  return `at ${formatTime((startMs ?? 0) / 1000)}`;
};

/**
 * Where focus goes back to: the Edit button pressed, or for marked words,
 * which take no focus, their line's ⋯ button.
 */
const focusAfter = (anchor: HTMLElement) => {
  if (anchor.tagName !== "MARK") {
    anchor.focus();
    return;
  }
  anchor
    .closest("[data-line-actions]")
    ?.querySelector<HTMLElement>('[aria-haspopup="menu"]')
    ?.focus();
};

function Editor({
  note,
  anchor,
  onEditNote,
  onDeleteNote,
  noteDrafts,
  onClose,
  unavailable,
}: NoteEditHandlers & { note: EditableNote; anchor: HTMLElement; onClose: () => void; unavailable: boolean }) {
  // A deleted note's line is going: the caller has moved focus somewhere that stays.
  const removed = useRef(false);
  const owner = noteDrafts!;
  const drafts = useSyncExternalStore(owner.subscribe, owner.getSnapshot, owner.getSnapshot);
  useEffect(() => { owner.open(note); }, [owner, note]);
  return (
    <NoteOnSelectionPopover
      quote={note.kind === "selection" ? (note.quoted_text ?? null) : null}
      title={`Edit the note ${noteWhere(note)}`}
      notice={unavailable ? "This note is no longer listed. Your unsaved edit is kept; you can copy it or cancel." : undefined}
      initialText={note.body}
      draft={drafts.get(note.id)}
      onTextChange={(text) => owner.change(note, text)}
      anchor={anchor}
      returnFocus={() => {
        if (!removed.current) focusAfter(anchor);
      }}
      onSubmit={() => owner.save(note, onEditNote)}
      onDelete={
        onDeleteNote
          ? async () => {
              const refusal = await owner.remove(note, (base, _text, answer) => onDeleteNote(base, answer));
              removed.current = refusal === null;
              return refusal;
            }
          : undefined
      }
      onClose={() => { owner.discard(note.id); onClose(); }}
    />
  );
}

/**
 * The editor of a saved note in the finished view, opened from its note
 * line's Edit button, a click on its marked words, or its row in My notes:
 * its text to change, and
 * Delete. Dirty drafts keep their opening revision; only an own save or a
 * deliberate retry of a shown conflict advances it.
 */
export default function TranscriptNoteEditor({ editing, notes, onClose, ...handlers }: TranscriptNoteEditorProps) {
  const [fallback] = useState(() => new NoteEditDrafts());
  const owner = handlers.noteDrafts ?? fallback;
  const drafts = useSyncExternalStore(owner.subscribe, owner.getSnapshot, owner.getSnapshot);
  useEffect(() => {
    fallback.resume();
    return () => fallback.dispose();
  }, [fallback]);
  const listed = editing ? notes?.find((candidate) => candidate.id === editing.noteId) : undefined;
  const note = listed ?? (editing ? drafts.get(editing.noteId)?.base : undefined);
  if (!editing || !note || note.revision === undefined || (listed && !isSavedNote(listed))) return null;
  return (
    <Editor
      key={note.id}
      note={note}
      anchor={editing.anchor}
      onClose={onClose}
      {...handlers}
      noteDrafts={owner}
      unavailable={notes != null && !listed}
    />
  );
}
