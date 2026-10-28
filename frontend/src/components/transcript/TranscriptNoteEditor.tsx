"use client";

import { useEffect, useRef } from "react";

import type { InlineNote } from "@/lib/notes/noteOutbox";
import { holdPendingNoteWrite } from "@/lib/notes/noteOutboxes";
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
  onEditNote: (note: EditableNote, body: string) => Promise<NoteEditRefusal>;
  onDeleteNote?: (note: EditableNote) => Promise<NoteEditRefusal>;
}

interface TranscriptNoteEditorProps extends NoteEditHandlers {
  editing: EditingNote | null;
  /** The notes as shown, so the editor always writes against the latest revision. */
  notes: readonly (TimelineNote | InlineNote)[] | null | undefined;
  onClose: () => void;
}

/** Where a note is, for its editor's name: "at 01:05", or on the whole meeting. */
export const noteWhere = (note: TimelineNote | InlineNote): string => {
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
  onClose,
}: NoteEditHandlers & { note: TimelineNote | InlineNote; anchor: HTMLElement; onClose: () => void }) {
  // A deleted note's line is going: the caller has moved focus somewhere that stays.
  const removed = useRef(false);
  // From the first Save or Delete until the editor closes, saved or given up:
  // a write on its way, or refused and still open, holds the page.
  const release = useRef<(() => void) | null>(null);
  useEffect(() => () => release.current?.(), []);
  const holding = <T,>(write: () => Promise<T>) => {
    release.current ??= holdPendingNoteWrite();
    return write();
  };
  return (
    <NoteOnSelectionPopover
      quote={note.kind === "selection" ? (note.quoted_text ?? null) : null}
      title={`Edit the note ${noteWhere(note)}`}
      initialText={note.body}
      anchor={anchor}
      returnFocus={() => {
        if (!removed.current) focusAfter(anchor);
      }}
      onSubmit={(body) => holding(() => onEditNote(note, body))}
      onDelete={
        onDeleteNote
          ? async () => {
              const refusal = await holding(() => onDeleteNote(note));
              removed.current = refusal === null;
              return refusal;
            }
          : undefined
      }
      onClose={onClose}
    />
  );
}

/**
 * The editor of a saved note in the finished view, opened from its note
 * line's Edit button, a click on its marked words, or its row in My notes:
 * its text to change, and
 * Delete. Writes go against the revision shown now, so a change made
 * elsewhere meanwhile is refused rather than overwritten.
 */
export default function TranscriptNoteEditor({ editing, notes, onClose, ...handlers }: TranscriptNoteEditorProps) {
  const note = editing ? notes?.find((candidate) => candidate.id === editing.noteId) : undefined;
  if (!editing || !note || !isSavedNote(note)) return null;
  return (
    <Editor
      key={note.id}
      note={note}
      anchor={editing.anchor}
      onClose={onClose}
      {...handlers}
    />
  );
}
