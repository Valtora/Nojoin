"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { useMemo } from "react";
import { FileText } from "lucide-react";

import { documentPreviewUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { shownNotes } from "@/lib/notes/noteOutbox";
import { formatTimelineMs, placeTimelineNotes, type NotePlacement } from "@/lib/notes/placement";
import type { NoteEdits } from "@/lib/notes/useNoteEdits";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import type { RecordingId, TimelineNote, TranscriptSegment } from "@/types";

import { noteFileTimeMs, useOpenNoteFile } from "./NoteFiles";
import { scrollTranscriptToNote } from "./transcriptJump";

interface CapturesGalleryProps {
  recordingId: RecordingId;
  /** The recording's notes as last listed; null while they load. */
  timelineNotes: readonly TimelineNote[] | null;
  /** The transcript's lines, which order the notes as the transcript does. */
  segments: TranscriptSegment[];
  noteEdits: NoteEdits;
  /** Called on a jump to a note, before the transcript scrolls: a phone opens it. */
  onShowTranscript?: () => void;
  className?: string;
}

/**
 * The Captures tab: every note's file in timeline order, a gallery of the
 * notes rather than a list of its own. A file opens the viewer, and its
 * note's time scrolls the transcript to the note.
 */
export default function CapturesGallery({
  recordingId,
  timelineNotes,
  segments,
  noteEdits,
  onShowTranscript,
  className,
}: CapturesGalleryProps) {
  const { snapshot } = useNoteOutbox(recordingId);
  const { apply } = noteEdits;
  const open = useOpenNoteFile();
  const placements = useMemo(
    () =>
      timelineNotes === null
        ? null
        : placeTimelineNotes(segments, apply(shownNotes(timelineNotes, snapshot))).filter(
            (placement) => placement.note.attachment,
          ),
    [timelineNotes, segments, apply, snapshot],
  );

  const renderItem = ({ note, afterIndex }: NotePlacement<TimelineNote>) => {
    const file = note.attachment;
    if (!file) return null;
    const atMs = noteFileTimeMs(note);
    const time = atMs === null ? "Meeting" : formatTimelineMs(atMs);
    return (
      <li key={note.id} data-testid="capture-item" className="min-w-0">
        <ButtonBase
          type="button"
          onClick={(event) => open?.(note.id, event.currentTarget)}
          aria-label={`Open ${file.title}, on the note at ${time}`}
          className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-md border border-surface-border bg-surface-inset outline-none transition-colors hover:border-action-border focus-visible:ring-2 focus-visible:ring-action"
        >
          {file.is_image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={documentPreviewUrl(file.id)}
              alt=""
              loading="lazy"
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="flex min-w-0 flex-col items-center gap-1 px-2 text-xs text-contrast-helper">
              <FileText aria-hidden="true" className="h-6 w-6 text-contrast-icon-muted" />
              <span className="max-w-full truncate">{file.title}</span>
            </span>
          )}
        </ButtonBase>
        <div className="mt-1 flex min-w-0 items-baseline gap-2">
          <ButtonBase
            type="button"
            onClick={() => {
              onShowTranscript?.();
              scrollTranscriptToNote(note.id, afterIndex);
            }}
            aria-label={`Show the note at ${time} in the transcript`}
            className="shrink-0 rounded-sm font-mono text-xs tabular-nums text-action-text outline-none hover:underline focus-visible:ring-2 focus-visible:ring-action"
          >
            {time}
          </ButtonBase>
          <span className="min-w-0 truncate text-xs text-contrast-helper">
            {file.use_in_notes ? "In notes · " : ""}
            {note.body}
          </span>
        </div>
      </li>
    );
  };

  return (
    <SurfaceBase kind="pane"
      data-testid="captures-gallery"
      // Focus lands here when a capture's Remove takes away the button pressed.
      tabIndex={-1}
      className={cn("min-h-0 overflow-y-auto p-3 pb-clear-floating-3 outline-none", className)}
    >
      {placements === null ? (
        <p className="p-4 text-center text-sm italic text-contrast-helper">Loading captures…</p>
      ) : placements.length === 0 ? (
        <p className="p-4 text-center text-sm text-contrast-helper">
          No captures yet. While recording, take one with the camera button, or paste or drop
          a file into the note box.
        </p>
      ) : (
        <ul
          aria-label="Captures"
          className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-3"
        >
          {placements.map(renderItem)}
        </ul>
      )}
    </SurfaceBase>
  );
}
