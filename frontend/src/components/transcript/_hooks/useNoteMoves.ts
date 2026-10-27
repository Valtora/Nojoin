"use client";

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";

import { moveTargets, targetBeside, type MoveTarget } from "@/lib/notes/noteMoves";
import { holdPendingNoteWrite } from "@/lib/notes/noteOutboxes";
import { shownLineAt, type NotePlacement, type PlacementNote, type PlacementSegment } from "@/lib/notes/placement";
import type { EditableNote, NoteEditRefusal } from "@/lib/notes/useNoteEdits";
import type { TranscriptSegment, TranscriptSourceReference } from "@/types";

import type { NoteLineMove } from "../../notes/TimelineNoteLine";
import { formatTime } from "../transcriptDisplay";

import { useNoteDrag } from "./useNoteDrag";

/** Saves a point note's new moment; resolves to why it was refused, or null. */
export type MoveNoteHandler = (note: EditableNote, anchorMs: number, target?: TranscriptSourceReference) => Promise<NoteEditRefusal>;

type MovableNote = EditableNote & PlacementNote;

interface UseNoteMovesOptions<N extends MovableNote> {
  /** Every line, hidden ones included, as the notes were placed on them. */
  segments: readonly (PlacementSegment & Partial<TranscriptSegment> & { start: number })[];
  /** The list indexes of the shown lines, ascending. */
  visibleIndexes: readonly number[];
  placements: readonly NotePlacement<N>[];
  regionRef: RefObject<HTMLElement | null>;
  onMoveNote?: MoveNoteHandler;
}

type Direction = "up" | "down";

/**
 * Moving point notes between lines in the finished or marked live transcript: Move up and
 * Move down on each, a step to the next gap a note can go to, and a drag of
 * its grip (`useNoteDrag`). Each move anchors the note at the start of the
 * line it now follows (`moveTargets`). After a step, focus stays on the same
 * button of the note in its new place, so pressing it again carries on. A
 * refused move puts the note back, says why on it, and announces it.
 */
export function useNoteMoves<N extends MovableNote>({
  segments,
  visibleIndexes,
  placements,
  regionRef,
  onMoveNote,
}: UseNoteMovesOptions<N>) {
  const targets = useMemo(() => moveTargets(segments, visibleIndexes), [segments, visibleIndexes]);
  const targetByLine = useMemo(() => new Map(targets.map((target) => [target.afterIndex, target])), [targets]);
  // The shown line each body note follows, -1 before every line.
  const shownAfter = useMemo(
    () =>
      new Map(
        placements
          .filter((placement) => placement.slot === "body")
          .map((placement) => [placement.note.id, shownLineAt(visibleIndexes, placement.afterIndex)]),
      ),
    [placements, visibleIndexes],
  );
  const [refusal, setRefusal] = useState<{ noteId: string; message: string } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  // Notes moved here, which never fold behind a line's count from under the
  // person moving them.
  const [moved, setMoved] = useState<ReadonlySet<string>>(() => new Set());
  const pendingFocus = useRef<{ noteId: string; direction: Direction } | null>(null);

  // After a step, or a refusal that put the note back, the note's line is a
  // new one: focus goes to the same button on it, else one that still works.
  useEffect(() => {
    const pending = pendingFocus.current;
    const region = regionRef.current;
    if (!pending || !region) return;
    pendingFocus.current = null;
    const line = region.querySelector<HTMLElement>(
      `[data-testid="timeline-note"][data-note-id="${CSS.escape(pending.noteId)}"]`,
    );
    const button =
      line?.querySelector<HTMLElement>(`[data-note-move="${pending.direction}"]:not(:disabled)`) ??
      line?.querySelector<HTMLElement>("[data-note-move]:not(:disabled)");
    button?.focus();
  });

  const moveTo = async (note: N, target: MoveTarget, direction: Direction | null) => {
    if (!onMoveNote) return;
    setRefusal(null);
    setMoved((previous) => (previous.has(note.id) ? previous : new Set(previous).add(note.id)));
    if (direction) pendingFocus.current = { noteId: note.id, direction };
    const release = holdPendingNoteWrite();
    try {
      const refused = await (target.source
        ? onMoveNote(note, target.anchorMs, target.source)
        : onMoveNote(note, target.anchorMs));
      if (refused) {
        setRefusal({ noteId: note.id, message: refused });
        setAnnouncement(`Note not moved. ${refused}`);
        if (direction) pendingFocus.current = { noteId: note.id, direction };
        return;
      }
      setAnnouncement(`Note moved after the line at ${formatTime(segments[target.afterIndex].start)}.`);
    } finally {
      release();
    }
  };

  const { drag, start } = useNoteDrag({
    regionRef,
    targetAfter: (lineIndex) => targetByLine.get(lineIndex) ?? null,
    onDrop: (noteId, target) => {
      const placement = placements.find((candidate) => candidate.note.id === noteId);
      if (placement) void moveTo(placement.note, target, null);
    },
  });

  /** The Move controls for a note, or undefined for one that can't move here. */
  const controlsFor = (note: N, canMove: boolean): NoteLineMove | undefined => {
    const after = shownAfter.get(note.id);
    if (!onMoveNote || !canMove || note.kind !== "point" || after === undefined) return undefined;
    const step = (direction: Direction) => {
      const target = targetBeside(targets, after, direction === "up" ? -1 : 1);
      return target ? () => void moveTo(note, target, direction) : null;
    };
    return {
      onUp: step("up"),
      onDown: step("down"),
      onGripPointerDown: (event) => start(event, note.id, after),
      dragging: drag?.noteId === note.id,
      refusal: refusal?.noteId === note.id ? refusal.message : null,
    };
  };

  /** The gap a note shows in, for a drag over it: the shown line it follows. */
  const gapOf = (noteId: string) => shownAfter.get(noteId);

  return { controlsFor, gapOf, wasMoved: (noteId: string) => moved.has(noteId), drag, announcement };
}
