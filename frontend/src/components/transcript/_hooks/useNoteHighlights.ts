"use client";

import { useCallback, useMemo } from "react";

import type { InlineNote } from "@/lib/notes/noteOutbox";
import { segmentEndMs, segmentStartMs } from "@/lib/notes/placement";
import type { DrawnLine } from "@/lib/notes/selectionRange";
import type { TimelineNote, TranscriptSegment } from "@/types";

import { noteHighlights, textOfRanges } from "../noteHighlights";
import type { TextDecoration } from "../textDecorations";
import { indexSegments } from "../transcriptGroups";

const NO_MARKS: readonly TextDecoration[] = [];

/** A note the server holds; one still on its way, or refused, is not. */
export const isSavedNote = (note: TimelineNote | InlineNote) =>
  !("status" in note) || note.status === "saved";

/**
 * The saved selection notes' words, found in the lines as they read now: the
 * marks for each line, and for a note whose words were not found, what its
 * span reads now. Resolved over every line, hidden ones too, so hiding a
 * line never makes a note's words look changed. Found again only when the
 * lines or the notes change, never on a playback tick.
 */
export function useNoteHighlights(
  segments: TranscriptSegment[],
  notes: readonly (TimelineNote | InlineNote)[] | null | undefined,
) {
  const lines = useMemo<DrawnLine[]>(
    () =>
      indexSegments(segments).map(({ segment, segmentId }) => ({
        segmentId,
        text: segment.text,
        startMs: segmentStartMs(segment),
        endMs: segmentEndMs(segment),
      })),
    [segments],
  );
  const highlights = useMemo(
    () => noteHighlights((notes ?? []).filter(isSavedNote), lines),
    [notes, lines],
  );

  const noteDecorations = useCallback(
    (segmentId: string): readonly TextDecoration[] => highlights.byLine.get(segmentId) ?? NO_MARKS,
    [highlights],
  );

  /** For a note whose words aren't in the transcript now, what its span reads; else null. */
  const wordsChanged = useCallback(
    (noteId: string): { nowReads: string } | null => {
      const resolution = highlights.byNote.get(noteId);
      if (resolution?.state !== "unanchored") return null;
      return { nowReads: textOfRanges(resolution.bestGuess, lines) };
    },
    [highlights, lines],
  );

  return { noteDecorations, wordsChanged };
}
