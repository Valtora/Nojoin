"use client";

import { useCallback, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";

import { moveTargets, targetBeside, type MoveTarget } from "@/lib/notes/noteMoves";
import { holdPendingNoteWrite } from "@/lib/notes/noteOutboxes";
import { shownLineAt, type NotePlacement, type PlacementNote, type PlacementSegment } from "@/lib/notes/placement";
import type { EditableNote, NoteEditRefusal } from "@/lib/notes/useNoteEdits";

import type { NoteLineMove } from "../../notes/TimelineNoteLine";
import { formatTime } from "../transcriptDisplay";

import { useNoteDrag } from "./useNoteDrag";
import { useStableCallback } from "./useStableCallback";

/** Saves a point note's new moment; resolves to why it was refused, or null. */
export type MoveNoteHandler = (note: EditableNote, anchorMs: number) => Promise<NoteEditRefusal>;

type MovableNote = EditableNote & PlacementNote;

interface UseNoteMovesOptions<N extends MovableNote> {
  /** Every line, hidden ones included, as the notes were placed on them. */
  segments: readonly (PlacementSegment & { start: number })[];
  /** The list indexes of the shown lines, ascending. */
  visibleIndexes: readonly number[];
  placements: readonly NotePlacement<N>[];
  regionRef: RefObject<HTMLElement | null>;
  onMoveNote?: MoveNoteHandler;
  /**
   * A step from the note's editor has put the note in a new place: the Edit
   * button of its line there, for the editor to sit against.
   */
  onEditorMoved?: (noteId: string, editButton: HTMLElement) => void;
}

type Direction = "up" | "down";

/**
 * Moving the transcript's point notes between lines: a drag of the note's
 * grip (`useNoteDrag`), and Move up and Move down in its editor, a step to the
 * next gap a note can go to, for the keyboard and touch. Each move anchors the
 * note at the start of the line it now follows (`moveTargets`). After a step
 * the editor follows the note to its new place and keeps focus, so pressing
 * the same button again carries on. A refused move puts the note back, says
 * why on it, and announces it.
 */
export function useNoteMoves<N extends MovableNote>({
  segments,
  visibleIndexes,
  placements,
  regionRef,
  onMoveNote,
  onEditorMoved,
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
  // A step from the editor, the editor closing, or a Retry, to follow up once
  // the note shows in its new place.
  const pending = useRef<{ noteId: string; from: "editor" | "close" | "retry" } | null>(null);

  // Before paint, so the editor never shows against a line that has gone.
  // After a step (or a refusal that put the note back) the editor keeps focus
  // and moves to the note's line in its new place. Closing the editor gave
  // focus back to the note's Edit button; if the note has since folded into
  // its line's chip, which a moved note joining others does, focus goes to
  // the chip. After a Retry, focus goes to the note's line, or that chip.
  useLayoutEffect(() => {
    const next = pending.current;
    const region = regionRef.current;
    if (!next || !region) return;
    pending.current = null;
    const line = region.querySelector<HTMLElement>(
      `[data-testid="timeline-note"][data-note-id="${CSS.escape(next.noteId)}"]`,
    );
    if (next.from === "editor") {
      const editButton = line?.querySelector<HTMLElement>("[data-note-edit]");
      if (editButton) onEditorMoved?.(next.noteId, editButton);
      return;
    }
    const chip = region.querySelector<HTMLElement>(
      `[data-testid="line-notes-chip"][data-note-gap="${shownAfter.get(next.noteId)}"] button`,
    );
    if (next.from === "close") {
      const active = document.activeElement;
      if (active && active !== document.body && active.isConnected) return;
      (line?.querySelector<HTMLElement>("[data-note-edit]") ?? chip ?? region).focus();
      return;
    }
    (line ?? chip ?? region).focus();
  });

  const moveTo = useCallback(async (note: N, target: MoveTarget, fromEditor: boolean) => {
    if (!onMoveNote) return;
    setRefusal(null);
    if (fromEditor) pending.current = { noteId: note.id, from: "editor" };
    const release = holdPendingNoteWrite();
    try {
      const refused = await onMoveNote(note, target.anchorMs);
      if (refused) {
        setRefusal({ noteId: note.id, message: refused });
        setAnnouncement(`Note not moved. ${refused}`);
        if (fromEditor) pending.current = { noteId: note.id, from: "editor" };
        return;
      }
      setAnnouncement(`Note moved after the line at ${formatTime(segments[target.afterIndex].start)}.`);
    } finally {
      release();
    }
  }, [onMoveNote, segments]);

  const { drag, start } = useNoteDrag({
    regionRef,
    targetAfter: (lineIndex) => targetByLine.get(lineIndex) ?? null,
    onDrop: (noteId, target) => {
      const placement = placements.find((candidate) => candidate.note.id === noteId);
      if (placement) void moveTo(placement.note, target, false);
    },
  });
  const startDrag = useStableCallback(start);

  /**
   * The Move controls for a note, or undefined for one that can't move here:
   * its grip on the line, and the steps its editor offers.
   */
  const controlsFor = useCallback((note: N, canMove: boolean): NoteLineMove | undefined => {
    const after = shownAfter.get(note.id);
    if (!onMoveNote || !canMove || note.kind !== "point" || after === undefined) return undefined;
    const step = (direction: Direction) => {
      const target = targetBeside(targets, after, direction === "up" ? -1 : 1);
      return target ? () => void moveTo(note, target, true) : null;
    };
    return {
      onUp: step("up"),
      onDown: step("down"),
      onGripPointerDown: (event) => startDrag(event, note.id, after),
      dragging: drag?.noteId === note.id,
      refusal: refusal?.noteId === note.id ? refusal.message : null,
    };
  }, [shownAfter, onMoveNote, targets, moveTo, startDrag, drag, refusal]);

  /** The gap a note shows in, for a drag over it: the shown line it follows. */
  const gapOf = useCallback((noteId: string) => shownAfter.get(noteId), [shownAfter]);
  // Retrying can now fold the focused recovery row into the same chip.
  const focusAfterRetry = useCallback((noteId: string) => { pending.current = { noteId, from: "retry" }; }, []);
  // The editor gave focus back to the note's Edit button, which may fold away.
  const focusAfterEditorClose = useCallback((noteId: string) => { pending.current = { noteId, from: "close" }; }, []);
  const hasRefusal = useCallback((noteId: string) => refusal?.noteId === noteId, [refusal]);

  return { controlsFor, gapOf, focusAfterRetry, focusAfterEditorClose,
    hasRefusal, drag, announcement };
}
