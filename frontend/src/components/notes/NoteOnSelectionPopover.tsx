"use client";

import { TextareaBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { stripNote } from "@/lib/notes/headNoteSync";
import { draftIsUnchanged, type NoteEditDraft } from "@/lib/notes/noteEditDrafts";

import Button from "../ui/Button";
import { useAnchoredPanel } from "../ui/useAnchoredPanel";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";

/** Why a write was refused, or null once it is done or on its way. */
type Refusal = string | null | undefined;

interface NoteOnSelectionPopoverProps {
  /** The words the note is on; null for a note on a point in time. */
  quote: string | null;
  /** Names the popover: "Note on the line at 01:05". */
  title: string;
  notice?: string;
  /** The note's text when it opens: an edit's, or empty for a new note. */
  initialText?: string;
  /** Saved-note edits are owned above layouts; new-note popovers stay local. */
  draft?: NoteEditDraft;
  onTextChange?: (text: string) => void;
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
  onClose: () => void;
}

/**
 * A note in the finished view: the quote it is on, a box and Save, and for a
 * note being edited, Delete. Enter saves and Shift+Enter starts a new line, as
 * in the live note box; Escape and Cancel close it. A press outside closes it
 * only while the text is as it opened, so a stray tap never throws a note away.
 */
export default function NoteOnSelectionPopover({
  quote,
  title,
  notice,
  initialText = "",
  draft,
  onTextChange,
  anchor,
  returnFocus,
  onSubmit,
  onDelete,
  onClose,
}: NoteOnSelectionPopoverProps) {
  // An optimistic body update must not make the unsaved draft appear clean.
  // Keep the same opening baseline as the text state, until this editor closes.
  const [openingText] = useState(initialText);
  const [localText, setText] = useState(initialText);
  const [localError, setError] = useState<string | null>(null);
  const [localBusy, setBusy] = useState(false);
  const text = draft ? draft.text : localText;
  const error = draft ? draft.refusal : localError;
  const busy = draft ? draft.pending !== undefined : localBusy;
  const active = useRef(true);
  const textVersion = useRef(0);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const anchorRef = useMemo(() => ({ current: anchor }), [anchor]);
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLDivElement>(true, anchorRef);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const placed = panelStyle.visibility === "visible";
  const titleId = useId();
  const errorId = useId();
  const isBlank = stripNote(text) === "";
  const isUnchanged = draft ? draftIsUnchanged(draft) : stripNote(text) === stripNote(openingText);

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

  useEffect(() => {
    if (!isUnchanged) return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || anchor.contains(target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", closeOnOutsidePress, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress, true);
  }, [isUnchanged, anchor, onClose, panelRef]);

  // A refused write keeps the popover open. The button pressed was disabled
  // while it ran, which dropped focus to the page, so it goes to the box.
  const finish = (refusal: Refusal) => {
    if (refusal === undefined) return;
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
    const submittedVersion = textVersion.current;
    const outcome = write();
    if (!(outcome instanceof Promise)) {
      finish(outcome);
      return;
    }
    setBusy(true);
    void outcome.then((refusal) => {
      if (!active.current) return;
      setBusy(false);
      if (refusal === null && submittedVersion !== textVersion.current) return;
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

  // Escape from anywhere in it. A Tab out of it, which would go on from the
  // end of the page where it sits, closes it and carries on from the anchor
  // instead, while the box is empty; with a note in the box, Tab wraps.
  const handlePanelKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const stops = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>("textarea, button:not(:disabled)") ?? [],
    );
    const edge = event.shiftKey ? stops[0] : stops[stops.length - 1];
    if (document.activeElement !== edge) return;
    if (isUnchanged) {
      close();
      return;
    }
    event.preventDefault();
    (event.shiftKey ? stops[stops.length - 1] : stops[0])?.focus();
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
      {notice ? <p className="mt-1 text-xs text-contrast-helper">{notice}</p> : null}
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
        onChange={(event) => {
          textVersion.current += 1;
          if (onTextChange) onTextChange(event.target.value);
          else setText(event.target.value);
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
