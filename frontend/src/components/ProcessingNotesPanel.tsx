"use client";

import { memo, useCallback, useEffect, useId, useRef, useState } from "react";

import {
  NOTE_BODY_MAX_CHARS,
  noteLength,
  stripNote,
  type HeadNoteConflict,
} from "@/lib/notes/headNoteSync";
import type { TimelineNote } from "@/types";

const SAVE_DEBOUNCE_MS = 1800;
// The character count shows from here, so the limit is never a surprise.
const LENGTH_WARNING_CHARS = NOTE_BODY_MAX_CHARS - 5_000;

interface ProcessingNotesPanelProps {
  value?: string | null;
  onSave: (notes: string) => Promise<void>;
  /** Told whenever the box starts or stops holding unsaved text. */
  onDirtyChange?: (dirty: boolean) => void;
  /** Set when another device changed or deleted the notes under an edit. */
  conflict?: HeadNoteConflict | null;
  /** Ends the conflict; returns the text the box shows now. */
  onResolveConflict?: (choice: "mine" | "theirs") => string;
  disabled?: boolean;
  disabledMessage?: string;
  /** The recording's other meeting-wide notes, shown read-only under the box. */
  otherNotes?: readonly TimelineNote[];
}

const NO_OTHER_NOTES: readonly TimelineNote[] = [];

type SaveState = "idle" | "saving" | "saved" | "error";

// A textarea holds, and so sends, every line end as "\n"; text from the
// server may carry "\r\n", which the box would send, and the API count, as "\n".
const asSent = (text: string) => text.replace(/\r\n?/g, "\n");

const lengthToShow = (text: string) => {
  // A text's UTF-16 length is never below its counted length (code points,
  // line ends normalised), so most text is never counted.
  if (text.length < LENGTH_WARNING_CHARS) return null;
  const length = noteLength(stripNote(asSent(text)));
  return length >= LENGTH_WARNING_CHARS ? length : null;
};

// The server trims a note, so text that differs only in its outer whitespace
// is the same note.
const sameNote = (a: string, b: string) => stripNote(a) === stripNote(b);

// `saved` is null once a save has failed: what the server holds is unknown,
// so the box's text counts as unsaved until a save succeeds.
const isSaved = (text: string, saved: string | null) =>
  saved !== null && sameNote(text, saved);

function ConflictNotice({
  conflict,
  locked,
  mine,
  onKeepMine,
  onUseTheirs,
}: {
  conflict: HeadNoteConflict;
  locked: boolean;
  /** The box's text, shown here while the lock covers the box. */
  mine: string;
  onKeepMine: () => void;
  onUseTheirs: () => void;
}) {
  const changed = conflict.kind === "changed";
  const mineIsBlank = stripNote(mine) === "";
  // Deleted elsewhere with nothing in the box: keeping and discarding both
  // leave no note, so there is nothing to choose.
  const nothingToKeep = !changed && mineIsBlank;
  return (
    <div
      role="alert"
      className="mb-3 rounded-surface-subtle border border-status-warning-border bg-status-warning-bg px-3 py-2 text-sm text-status-warning-fg"
    >
      <p className="font-semibold">
        {changed
          ? "These notes were changed on another device."
          : "These notes were deleted on another device."}
      </p>
      <p className="mt-1">
        {locked
          ? "Notes generation has started with the other device's version, so your text cannot be kept now. Copy it from below to keep it."
          : "Your text is still in the box and has not been saved."}
        {changed ? " Their version:" : ""}
      </p>
      {changed ? (
        <p className="mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-status-warning-border pl-3">
          {conflict.note.body}
        </p>
      ) : null}
      {locked && !mineIsBlank ? (
        <>
          <p className="mt-2 font-semibold">Your text:</p>
          <p className="mt-1 max-h-32 select-text overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-status-warning-border pl-3">
            {mine}
          </p>
        </>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-2">
        {nothingToKeep ? null : (
          <button
            type="button"
            onClick={onKeepMine}
            disabled={locked}
            className="rounded-pill border border-action-border bg-surface-card px-3 py-1 text-xs font-semibold text-action-text transition-colors hover:bg-action-tint disabled:cursor-not-allowed disabled:opacity-60"
          >
            {changed && mineIsBlank ? "Delete anyway" : "Keep mine"}
          </button>
        )}
        <button
          type="button"
          onClick={onUseTheirs}
          className="rounded-pill border border-action-border bg-surface-card px-3 py-1 text-xs font-semibold text-action-text transition-colors hover:bg-action-tint"
        >
          {changed ? "Use theirs" : nothingToKeep ? "Dismiss" : "Discard mine"}
        </button>
      </div>
    </div>
  );
}

function ProcessingNotesPanel({
  value,
  onSave,
  onDirtyChange,
  conflict = null,
  onResolveConflict,
  disabled = false,
  disabledMessage,
  otherNotes = NO_OTHER_NOTES,
}: ProcessingNotesPanelProps) {
  const normalisedValue = value ?? "";
  const headingId = useId();
  const countId = useId();
  const otherNotesHeadingId = useId();
  const [saveState, setSaveState] = useState<SaveState>("idle");
  // The box's text at mount. Later text is written by the effect below:
  // were `defaultValue` to follow it, React would change an untouched box
  // without its change tracking seeing it, and clearing that box would then
  // fire no change at all.
  const [initialValue] = useState(normalisedValue);
  // What the box holds, for the character count and the conflict buttons.
  const [draftText, setDraftText] = useState(normalisedValue);
  const [dirty, setDirty] = useState(false);
  // The box takes new text from the server only while it holds no unsaved
  // text and differs from it (the effect below); the count follows suit.
  const [syncedValue, setSyncedValue] = useState(normalisedValue);
  if (syncedValue !== normalisedValue) {
    setSyncedValue(normalisedValue);
    if (!dirty && !sameNote(draftText, normalisedValue)) {
      setDraftText(normalisedValue);
    }
  }
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const lockRef = useRef<HTMLDivElement | null>(null);
  const disabledRef = useRef(disabled);
  const conflictRef = useRef(conflict);
  const draftRef = useRef(normalisedValue);
  const lastSavedRef = useRef<string | null>(normalisedValue);
  const lastPropValueRef = useRef(normalisedValue);
  const unmountedRef = useRef(false);
  const reportedDirtyRef = useRef(false);
  const saveStateRef = useRef<SaveState>("idle");
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushSaveRef = useRef<
    (valueToSave: string, force?: boolean) => Promise<void>
  >(
    async () => {},
  );

  const setVisibleSaveState = useCallback((nextState: SaveState) => {
    if (saveStateRef.current === nextState) {
      return;
    }

    saveStateRef.current = nextState;
    setSaveState(nextState);
  }, []);

  const reportDirty = useCallback(() => {
    const nowDirty = !isSaved(draftRef.current, lastSavedRef.current);
    if (nowDirty !== reportedDirtyRef.current) {
      reportedDirtyRef.current = nowDirty;
      setDirty(nowDirty);
      onDirtyChange?.(nowDirty);
    }
  }, [onDirtyChange]);

  const clearPendingSave = useCallback(() => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
  }, []);

  const flushSave = useCallback(async (valueToSave: string, force = false) => {
    clearPendingSave();

    // While another device's change is unresolved, nothing is saved over it.
    if (conflictRef.current) {
      return;
    }
    // A save of text already being saved is let through: the store runs
    // saves in turn and sends nothing for text the note already holds, and
    // when the first save fails, this one is its retry.
    if (!force && isSaved(valueToSave, lastSavedRef.current)) {
      return;
    }

    setVisibleSaveState("saving");
    try {
      await onSave(valueToSave);
      lastSavedRef.current = valueToSave;
      lastPropValueRef.current = valueToSave;
      reportDirty();
      setVisibleSaveState(
        draftRef.current === valueToSave ? "saved" : "idle",
      );

      if (
        !unmountedRef.current &&
        !disabledRef.current &&
        !isSaved(draftRef.current, lastSavedRef.current)
      ) {
        saveTimeoutRef.current = setTimeout(() => {
          saveTimeoutRef.current = null;
          void flushSaveRef.current(draftRef.current);
        }, SAVE_DEBOUNCE_MS);
      }
    } catch {
      lastSavedRef.current = null;
      reportDirty();
      setVisibleSaveState("error");
    }
  }, [clearPendingSave, onSave, reportDirty, setVisibleSaveState]);

  useEffect(() => {
    flushSaveRef.current = flushSave;
  }, [flushSave]);

  useEffect(() => {
    disabledRef.current = disabled;
  }, [disabled]);

  useEffect(() => {
    conflictRef.current = conflict;
  }, [conflict]);

  useEffect(() => {
    if (normalisedValue === lastPropValueRef.current) {
      return;
    }

    lastPropValueRef.current = normalisedValue;

    // Unsaved text, or what was just saved as the server stored it
    // (trimmed): leave the box, its trailing space or new line, and the
    // caret alone.
    if (
      !isSaved(draftRef.current, lastSavedRef.current) ||
      sameNote(draftRef.current, normalisedValue)
    ) {
      return;
    }

    draftRef.current = normalisedValue;
    lastSavedRef.current = normalisedValue;
    if (textareaRef.current && textareaRef.current.value !== normalisedValue) {
      textareaRef.current.value = normalisedValue;
    }
  }, [normalisedValue]);

  useEffect(() => {
    if (disabled) {
      return;
    }

    return () => {
      clearPendingSave();
    };
  }, [clearPendingSave, disabled]);

  useEffect(() => {
    if (!disabled) {
      return;
    }

    void flushSave(draftRef.current);
  }, [disabled, flushSave]);

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      clearPendingSave();
      // Unsaved text is saved on the way out, to another recording or page.
      if (!disabledRef.current && !isSaved(draftRef.current, lastSavedRef.current)) {
        void flushSaveRef.current(draftRef.current);
      }
    };
  }, [clearPendingSave]);

  const handleChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      const nextValue = event.target.value;
      draftRef.current = nextValue;
      setDraftText(nextValue);
      reportDirty();

      if (saveStateRef.current !== "idle") {
        setVisibleSaveState("idle");
      }

      if (
        disabledRef.current ||
        conflictRef.current ||
        isSaved(nextValue, lastSavedRef.current)
      ) {
        clearPendingSave();
        return;
      }

      clearPendingSave();
      saveTimeoutRef.current = setTimeout(() => {
        saveTimeoutRef.current = null;
        void flushSave(draftRef.current);
      }, SAVE_DEBOUNCE_MS);
    },
    [clearPendingSave, flushSave, reportDirty, setVisibleSaveState],
  );

  const handleBlur = useCallback(() => {
    if (!disabledRef.current) {
      void flushSave(draftRef.current);
    }
  }, [flushSave]);

  // The notice and its focused button go once a conflict is resolved, so
  // focus moves to the box, or to the lock when the box is locked.
  const focusBox = useCallback(() => {
    (disabledRef.current ? lockRef.current : textareaRef.current)?.focus();
  }, []);

  const handleKeepMine = useCallback(() => {
    // Notes generation has the other version; nothing is saved over it.
    if (disabledRef.current) {
      return;
    }
    // Read by the save below, before the cleared conflict re-renders.
    conflictRef.current = null;
    onResolveConflict?.("mine");
    focusBox();
    // Even when the box holds what it last saved: that text goes over theirs.
    void flushSave(draftRef.current, true);
  }, [flushSave, focusBox, onResolveConflict]);

  const handleUseTheirs = useCallback(() => {
    // After a delete the box moves to another head note, if there is one,
    // whose text may equal what it showed: then no new value arrives to
    // put it back in the box, so the store's answer is written here.
    const theirs =
      onResolveConflict?.("theirs") ??
      (conflictRef.current?.kind === "changed" ? conflictRef.current.note.body : "");
    draftRef.current = theirs;
    lastSavedRef.current = theirs;
    lastPropValueRef.current = theirs;
    if (textareaRef.current) {
      textareaRef.current.value = theirs;
    }
    setDraftText(theirs);
    setVisibleSaveState("idle");
    reportDirty();
    focusBox();
  }, [focusBox, onResolveConflict, reportDirty, setVisibleSaveState]);

  const shownLength = lengthToShow(draftText);
  const overLimit = shownLength !== null && shownLength > NOTE_BODY_MAX_CHARS;

  // Nothing at rest: only saving, saved, failed, conflicting and locked say
  // anything.
  const saveMessage =
    conflict
      ? "Changed elsewhere"
      : disabled
      ? "Locked"
      : saveState === "saving"
      ? "Saving"
      : saveState === "saved"
        ? "Saved"
        : saveState === "error"
          ? "Save failed"
          : "";

  return (
    <section className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      {/* One row: title and save state. */}
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 id={headingId} className="text-base font-semibold text-foreground">
          Notes
        </h3>
        <span
          role="status"
          className="ml-auto text-xs font-semibold uppercase tracking-[0.2em] text-contrast-helper"
        >
          {saveMessage}
        </span>
      </div>
      {conflict ? (
        <ConflictNotice
          conflict={conflict}
          locked={disabled}
          mine={draftText}
          onKeepMine={handleKeepMine}
          onUseTheirs={handleUseTheirs}
        />
      ) : null}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <textarea
          ref={textareaRef}
          defaultValue={initialValue}
          onChange={handleChange}
          onBlur={handleBlur}
          placeholder="Type quick reminders, decisions, or action items here..."
          aria-labelledby={headingId}
          aria-describedby={shownLength !== null ? countId : undefined}
          aria-invalid={overLimit}
          disabled={disabled}
          className={`density-surface-panel min-h-[10rem] w-full flex-1 resize-none border border-control-border bg-control-bg px-4 py-4 text-sm leading-6 text-contrast-muted outline-none transition ${disabled ? "cursor-not-allowed opacity-70" : "focus:border-action focus:ring-2 focus:ring-action"}`}
        />
        {disabled ? (
          <div
            ref={lockRef}
            tabIndex={-1}
            className="density-surface-panel absolute inset-0 flex items-center justify-center border border-surface-border bg-surface-card px-6 text-center outline-none"
          >
            <div>
              <div className="text-sm font-semibold text-foreground">
                Notes are temporarily locked
              </div>
              <p className="mt-2 text-sm leading-6 text-contrast-muted">
                {disabledMessage || "Your manual notes are being incorporated into the generated meeting notes."}
              </p>
            </div>
          </div>
        ) : null}
      </div>
      {shownLength !== null ? (
        <p
          id={countId}
          className={`mt-2 text-xs ${overLimit ? "text-status-danger-fg" : "text-contrast-helper"}`}
        >
          {shownLength.toLocaleString()} / {NOTE_BODY_MAX_CHARS.toLocaleString()} characters
          {overLimit ? ". Shorten the notes to save them." : ""}
        </p>
      ) : null}
      {otherNotes.length > 0 ? (
        <div className="mt-3">
          <h4
            id={otherNotesHeadingId}
            className="text-xs font-semibold uppercase tracking-[0.2em] text-contrast-helper"
          >
            Other notes
          </h4>
          <ul
            aria-labelledby={otherNotesHeadingId}
            tabIndex={0}
            className="mt-2 max-h-40 space-y-2 overflow-y-auto outline-none focus-visible:ring-2 focus-visible:ring-action"
          >
            {otherNotes.map((note) => (
              <li
                key={note.id}
                className="density-surface-panel whitespace-pre-wrap break-words bg-surface-inset px-3 py-2 text-sm leading-6 text-contrast-muted"
              >
                {note.body}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <p className="mt-3 text-xs text-contrast-helper">
        These notes are fed into note generation and the final notes will label them as user-authored.
      </p>
    </section>
  );
}

export default memo(ProcessingNotesPanel);
