"use client";

import { TextareaBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { ArrowDown, ArrowUp } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { stripNote } from "@/lib/notes/headNoteSync";
import { useUnsavedNoteWarning } from "@/lib/notes/useUnsavedNoteWarning";

import Button from "../ui/Button";
import { useAnchoredPanel } from "../ui/useAnchoredPanel";
import { usePanelDismissal } from "../ui/usePanelDismissal";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";

/** Why a write was refused, or null once it is done or on its way. */
type Refusal = string | null;

/** Moving the note being edited a line at a time, for a point note in the transcript. */
export interface NoteEditorMove {
  /** A step to the gap above or below; null where there is none. */
  onUp: (() => void) | null;
  onDown: (() => void) | null;
  /** Why the last move was refused, until the next one. */
  refusal: string | null;
  /**
   * Where the note is now, which changes with each step: the popover places
   * itself against its anchor again when it does.
   */
  place: number | null;
}

interface NoteOnSelectionPopoverProps {
  /** The words the note is on; null for a note on a point in time. */
  quote: string | null;
  /** Names the popover: "Note on the line at 01:05". */
  title: string;
  /** The note's text when it opens: an edit's, or empty for a new note. */
  initialText?: string;
  /**
   * What it sits against, which stays mounted while it is open: a line's ⋯
   * button, or the place of a text selection. Focus goes back there when it
   * closes, unless `returnFocus` puts it somewhere else.
   */
  anchor: HTMLElement;
  /** Puts focus back when it closes, for an anchor that can't take focus. */
  returnFocus?: () => void;
  /**
   * Queues or saves the note. Returns, or resolves to, why it was refused, and
   * the popover stays open with the text as typed; null closes it.
   */
  onSubmit: (body: string) => Refusal | Promise<Refusal>;
  /** Deletes the note, after a second press to confirm; null closes the popover. */
  onDelete?: () => Promise<Refusal>;
  /** Move up and Move down, for a note that can move; the popover stays open as it moves. */
  move?: NoteEditorMove;
  onClose: () => void;
}

/**
 * A note in the finished view: the quote it is on, a box and Save, and for a
 * note being edited, Delete, and Move up and Move down for one that can move.
 * Enter saves and Shift+Enter starts a new line, as
 * in the live note box; Escape and Cancel close it. A press outside closes it
 * only while the text is as it opened, so a stray tap never throws a note away,
 * and until it is saved, leaving the page asks first (`useUnsavedNoteWarning`).
 */
export default function NoteOnSelectionPopover({
  quote,
  title,
  initialText = "",
  anchor,
  returnFocus,
  onSubmit,
  onDelete,
  move,
  onClose,
}: NoteOnSelectionPopoverProps) {
  const [text, setText] = useState(initialText);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const anchorRef = useMemo(() => ({ current: anchor }), [anchor]);
  const { panelRef, panelStyle, reposition } = useAnchoredPanel<HTMLDivElement>(true, anchorRef);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const upRef = useRef<HTMLButtonElement>(null);
  const downRef = useRef<HTMLButtonElement>(null);
  const lastStep = useRef<"up" | "down" | null>(null);
  const placed = panelStyle.visibility === "visible";
  const titleId = useId();
  const errorId = useId();
  const isBlank = stripNote(text) === "";
  const isUnchanged = stripNote(text) === stripNote(initialText);
  useUnsavedNoteWarning(!isUnchanged);

  const close = () => {
    onClose();
    if (returnFocus) returnFocus();
    else anchor.focus();
  };

  // The box takes focus once the popover is placed: before that it is
  // hidden, and a hidden box can't be focused.
  useEffect(() => {
    if (placed) boxRef.current?.focus();
  }, [placed]);

  // A step moved the note, and with it the anchor: the popover follows.
  const place = move?.place;
  useLayoutEffect(() => {
    if (place !== undefined) reposition();
  }, [place, reposition]);

  // A step that reached the first or last place disables its own button,
  // which drops focus: it goes to the step that still works, else the box.
  const canUp = Boolean(move?.onUp);
  const canDown = Boolean(move?.onDown);
  useEffect(() => {
    const pressed = lastStep.current === "up" ? upRef.current : lastStep.current === "down" ? downRef.current : null;
    if (!pressed || !pressed.disabled) return;
    const active = document.activeElement;
    if (active !== pressed && active !== document.body && active !== null) return;
    const other = lastStep.current === "up" ? downRef.current : upRef.current;
    (other && !other.disabled ? other : boxRef.current)?.focus();
  }, [canUp, canDown]);

  const step = (direction: "up" | "down") => {
    lastStep.current = direction;
    (direction === "up" ? move?.onUp : move?.onDown)?.();
  };

  // Escape, a press outside and a Tab out of it, while the box is as it
  // opened; with a note typed in the box, Tab wraps.
  const handlePanelKeyDown = usePanelDismissal({
    panelRef, anchor, unchanged: isUnchanged, dismiss: onClose, close,
  });

  // A refused write keeps the popover open. The button pressed was disabled
  // while it ran, which dropped focus to the page, so it goes to the box.
  const finish = (refusal: Refusal) => {
    if (!refusal) {
      close();
      return;
    }
    setError(refusal);
    boxRef.current?.focus();
  };

  /**
   * Runs a write; closes once it is done or on its way, else says why and
   * keeps the text. A queued note answers at once; a save waits for Nojoin.
   */
  const settle = (write: () => Refusal | Promise<Refusal>) => {
    const outcome = write();
    if (!(outcome instanceof Promise)) {
      finish(outcome);
      return;
    }
    setBusy(true);
    void outcome.then((refusal) => {
      setBusy(false);
      finish(refusal);
    });
  };

  const save = () => {
    if (isBlank || busy) return;
    settle(() => onSubmit(text));
  };

  const remove = () => {
    if (!onDelete || busy) return;
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    settle(onDelete);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      save();
    }
  };

  // On the body, so no translucent transcript group (a stacking context)
  // keeps it under the lines after it.
  return createPortal(
    <SurfaceBase
      data-ui-fill="card"
      data-ui-border="full" kind="float"
      ref={panelRef}
      role="dialog"
      aria-labelledby={titleId}
      style={panelStyle}
      data-testid="note-on-selection"
      // A press in here keeps the text selection it is a note on.
      data-selection-ui
      className="z-[var(--z-dropdown)] w-80 max-w-[calc(100vw-1rem)] rounded-lg border border-surface-border bg-surface-card p-3 shadow-float"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={handlePanelKeyDown}
    >
      <p id={titleId} className="text-xs font-semibold text-contrast-helper">
        {title}
      </p>
      {quote ? (
        <blockquote className="mt-1 line-clamp-3 border-l-2 border-action-border pl-2 text-sm italic leading-5 text-contrast-muted">
          {quote}
        </blockquote>
      ) : null}
      <TextareaBase
        data-ui-fill="control"
        data-ui-border="full"
        ref={boxRef}
        rows={3}
        value={text}
        // Text typed while a save is on its way would not be in it.
        readOnly={busy}
        onChange={(event) => {
          setText(event.target.value);
          setError(null);
          setConfirmingDelete(false);
        }}
        onKeyDown={handleKeyDown}
        aria-label="Note"
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
        placeholder="Add a note…"
        className="density-surface-panel mt-2 w-full resize-none border border-control-border bg-control-bg px-3 py-2 text-sm leading-5 text-contrast-muted outline-none focus:border-action focus:ring-2 focus:ring-action"
      />
      {error ? (
        <p id={errorId} role="alert" className="mt-1 text-xs text-status-danger-fg">
          {error}
        </p>
      ) : null}
      {move ? (
        <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="Move this note">
          <Button
            ref={upRef}
            size="sm"
            variant="ghost"
            data-note-move="up"
            iconLeft={<ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />}
            disabled={!move.onUp}
            onClick={() => step("up")}
            className={NOTE_BUTTON_TOUCH}
          >
            Move up
          </Button>
          <Button
            ref={downRef}
            size="sm"
            variant="ghost"
            data-note-move="down"
            iconLeft={<ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />}
            disabled={!move.onDown}
            onClick={() => step("down")}
            className={NOTE_BUTTON_TOUCH}
          >
            Move down
          </Button>
          {/* The announcement is the page's; this is for the eye. */}
          {move.refusal ? (
            <p data-testid="note-editor-move-refused" className="basis-full text-xs text-status-danger-fg">
              {move.refusal}
            </p>
          ) : null}
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
        {onDelete ? (
          <Button
            size="sm"
            variant={confirmingDelete ? "danger" : "ghost"}
            disabled={busy}
            onClick={remove}
            className={`${NOTE_BUTTON_TOUCH} mr-auto`}
          >
            {confirmingDelete ? "Confirm delete" : "Delete"}
          </Button>
        ) : null}
        <Button size="sm" onClick={close} className={NOTE_BUTTON_TOUCH}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={isBlank || busy}
          onClick={save}
          className={NOTE_BUTTON_TOUCH}
        >
          Save note
        </Button>
      </div>
    </SurfaceBase>,
    document.body,
  );
}
