"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { Pencil, Play } from "lucide-react";

import { noteSnippet, shownNotes } from "@/lib/notes/noteOutbox";
import { placeTimelineNotes, type NotePlacement } from "@/lib/notes/placement";
import type { EditableNote, NoteEdits } from "@/lib/notes/useNoteEdits";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import { cn } from "@/lib/cn";
import type { RecordingId, TimelineNote, TranscriptSegment } from "@/types";

import { useNoteHighlights } from "../transcript/_hooks/useNoteHighlights";
import TranscriptNoteEditor, {
  noteWhere,
  type EditingNote,
} from "../transcript/TranscriptNoteEditor";
import { formatTime } from "../transcript/transcriptDisplay";
import IconButton from "../ui/IconButton";
import { TOUCH_TARGET_SQUARE } from "../ui/touchTarget";

import { scrollTranscriptToNote } from "./transcriptJump";

interface MyNotesListProps {
  recordingId: RecordingId;
  /** The recording's notes as last listed; null while they load. */
  timelineNotes: readonly TimelineNote[] | null;
  /** The transcript's lines, which the notes are placed on as the transcript places them. */
  segments: TranscriptSegment[];
  /** The page's note edits, which the transcript shares. */
  noteEdits: NoteEdits;
  /** Plays the audio from a time in seconds. */
  onPlay: (seconds: number) => void;
  /** Called on a jump to a note, before the transcript scrolls: a phone opens it. */
  onShowTranscript?: () => void;
  className?: string;
}

const startMsOf = (note: TimelineNote) =>
  note.kind === "selection" ? (note.start_ms ?? 0) : (note.anchor_ms ?? 0);

/**
 * Every saved note on a processed meeting, in the transcript's order: notes
 * before the transcript first, then the rest by time, then notes after it.
 * A note at a time scrolls the transcript to it, and plays from there; each
 * note is edited or deleted here as on its line, through the same edits, so
 * a change shows in both. Unsaved notes stay listed above the transcript.
 */
export default function MyNotesList({
  recordingId,
  timelineNotes,
  segments,
  noteEdits,
  onPlay,
  onShowTranscript,
  className,
}: MyNotesListProps) {
  const { snapshot } = useNoteOutbox(recordingId);
  const { apply, edit, remove } = noteEdits;
  const notes = useMemo(
    () => (timelineNotes === null ? null : apply(shownNotes(timelineNotes, snapshot))),
    [apply, timelineNotes, snapshot],
  );
  const placements = useMemo(
    () => (notes === null ? [] : placeTimelineNotes(segments, notes)),
    [segments, notes],
  );
  const { wordsChanged } = useNoteHighlights(segments, notes);
  const [editing, setEditing] = useState<EditingNote | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // A deleted note's row is going, so focus moves to the list.
  const deleteNote = useCallback(
    async (note: EditableNote) => {
      const refusal = await remove(note);
      if (refusal === null) listRef.current?.focus();
      return refusal;
    },
    [remove],
  );

  const showInTranscript = (placement: NotePlacement<TimelineNote>) => {
    onShowTranscript?.();
    scrollTranscriptToNote(placement.note.id, placement.afterIndex);
  };

  const renderRow = (placement: NotePlacement<TimelineNote>) => {
    const { note } = placement;
    const where = noteWhere(note);
    const name = `${where}, “${noteSnippet(note.body)}”`;
    const changed = note.kind === "selection" ? wordsChanged(note.id) : null;
    const isBody = placement.slot === "body";
    const time = isBody ? formatTime(startMsOf(note) / 1000) : null;
    return (
      <li
        key={note.id}
        data-testid="my-note"
        data-note-id={note.id}
        className="relative rounded-sm border-l-2 border-action-border py-1 pl-2 hover:bg-surface-card"
      >
        {/* A note at a time is one press target: the button covers the row,
            and the row's own buttons sit above it. */}
        {isBody ? (
          <button
            type="button"
            data-jump
            aria-label={`Show in the transcript: the note ${name}`}
            onClick={() => showInTranscript(placement)}
            className="absolute inset-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-action"
          />
        ) : null}
        {/* In a narrow column the buttons wrap under the time, and each
            other, rather than leave the column. */}
        <div className="flex min-w-0 flex-wrap items-center gap-x-1">
          {time ? (
            <span className="grow basis-0 font-mono text-xs tabular-nums text-contrast-icon-muted">{time}</span>
          ) : (
            <span className="grow basis-0 text-xs font-medium text-contrast-helper">
              {note.kind === "head" ? "Before the transcript" : "After the transcript"}
            </span>
          )}
          <div className="relative ml-auto flex min-w-0 flex-wrap items-center justify-end">
            {time ? (
              <IconButton
                size="sm"
                aria-label={`Play from ${time}`}
                title="Play from here"
                icon={<Play aria-hidden="true" />}
                onClick={() => onPlay(startMsOf(note) / 1000)}
                className={`-my-1.5 ${TOUCH_TARGET_SQUARE}`}
              />
            ) : null}
            <IconButton
              size="sm"
              aria-label={`Edit note ${name}`}
              title="Edit or delete this note"
              icon={<Pencil aria-hidden="true" />}
              onClick={(event) => setEditing({ noteId: note.id, anchor: event.currentTarget })}
              className={`-my-1.5 ${TOUCH_TARGET_SQUARE}`}
            />
          </div>
        </div>
        {note.kind === "selection" && note.quoted_text ? (
          <blockquote
            className={cn(
              "line-clamp-2 break-words text-sm italic leading-6 text-contrast-helper",
              changed && "line-through",
            )}
          >
            “{note.quoted_text}”
          </blockquote>
        ) : null}
        {changed ? (
          <p className="text-xs leading-5 text-contrast-helper">Words changed since this note.</p>
        ) : null}
        <p className="line-clamp-4 whitespace-pre-wrap break-words pr-1 text-sm leading-6 text-foreground">
          {note.body}
        </p>
      </li>
    );
  };

  return (
    <div
      ref={listRef}
      tabIndex={-1}
      data-testid="my-notes"
      className={cn("min-h-0 overflow-y-auto p-3 pb-clear-floating-3 outline-none", className)}
    >
      {notes === null ? (
        <p className="p-4 text-center text-sm italic text-contrast-helper">Loading notes…</p>
      ) : placements.length === 0 ? (
        <p className="p-4 text-center text-sm text-contrast-helper">
          No notes yet. Add one from a line&apos;s ⋯ menu, or select words in the transcript.
        </p>
      ) : (
        <ul aria-label="My notes" className="space-y-2">
          {placements.map(renderRow)}
        </ul>
      )}
      <TranscriptNoteEditor
        editing={editing}
        notes={notes}
        onEditNote={edit}
        onDeleteNote={deleteNote}
        onClose={() => setEditing(null)}
      />
    </div>
  );
}
