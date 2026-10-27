"use client";

import { ButtonBase, TextareaBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import {
  memo,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import {
  NOTE_BODY_MAX_CHARS,
  noteLength,
  stripNote,
  type HeadNoteConflict,
} from "@/lib/notes/headNoteSync";
import type { OutboxEntry } from "@/lib/notes/noteOutbox";
import type { TimelineNote } from "@/types";
import type { RetainedHeadNoteDraft } from "@/lib/notes/HeadNoteOwner";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import UnsavedNotesList from "./notes/UnsavedNotesList";

const SAVE_DEBOUNCE_MS = 1800;
// The character count shows from here, so the limit is never a surprise.
const LENGTH_WARNING_CHARS = NOTE_BODY_MAX_CHARS - 5_000;

interface ProcessingNotesPanelProps {
  value?: string | null;
  /** Recording-level draft owner, retained while the panel is absent. */
  retainedDraft?: RetainedHeadNoteDraft | null;
  onDraftTextChange?: (text: string) => void;
  onDraftSavedChange?: (savedText: string | null) => void;
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
  /**
   * Meeting-wide notes from the live note box still on their way, listed with
   * the other notes, where each will stay once saved.
   */
  pendingNotes?: readonly OutboxEntry[];
  onRetryPendingNote?: (key: string) => void;
  onDiscardPendingNote?: (key: string) => void;
  /** Where focus goes when Discard removes the last of them. */
  onPendingNotesEmptied?: () => void;
  /**
   * A lower floor for the box, while the live note box beside it takes the
   * meeting's notes and the transcript needs the height.
   */
  compact?: boolean;
  /**
   * Changes when what failed an earlier save has since cleared up on its own,
   * such as a clear's delete resent in the background: the box then saves
   * again, if its last save failed.
   */
  retrySaves?: number;
}

const NO_OTHER_NOTES: readonly TimelineNote[] = [];
const NO_PENDING_NOTES: readonly OutboxEntry[] = [];

type SaveState = "idle" | "saving" | "saved" | "error";

// What the status region announces. A routine save says nothing, since one
// runs every pause in typing; a failure, and the save that recovers from it,
// are announced. A conflict is announced by its notice alone.
type Announcement = "" | "Save failed" | "Saved";

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
          <ButtonBase
            data-ui-fill="card"
            data-ui-hover="action-tint"
            data-ui-border="full" chrome="action"
            type="button"
            onClick={onKeepMine}
            disabled={locked}
            className="rounded-pill border border-action-border bg-surface-card px-3 py-1 text-xs font-semibold text-action-text transition-colors hover:bg-action-tint disabled:cursor-not-allowed disabled:opacity-60"
          >
            {changed && mineIsBlank ? "Delete anyway" : "Keep mine"}
          </ButtonBase>
        )}
        <ButtonBase
          data-ui-fill="card"
          data-ui-hover="action-tint"
          data-ui-border="full" chrome="action"
          type="button"
          onClick={onUseTheirs}
          className="rounded-pill border border-action-border bg-surface-card px-3 py-1 text-xs font-semibold text-action-text transition-colors hover:bg-action-tint"
        >
          {changed ? "Use theirs" : nothingToKeep ? "Dismiss" : "Discard mine"}
        </ButtonBase>
      </div>
    </div>
  );
}

function ProcessingNotesPanel({
  value,
  retainedDraft,
  onDraftTextChange,
  onDraftSavedChange,
  onSave,
  onDirtyChange,
  conflict = null,
  onResolveConflict,
  disabled = false,
  disabledMessage,
  otherNotes = NO_OTHER_NOTES,
  pendingNotes = NO_PENDING_NOTES,
  onRetryPendingNote,
  onDiscardPendingNote,
  onPendingNotesEmptied,
  compact = false,
  retrySaves = 0,
}: ProcessingNotesPanelProps) {
  const normalisedValue = value ?? "";
  const restoredText = retainedDraft?.text ?? normalisedValue;
  const restoredSaved = retainedDraft ? retainedDraft.lastSaved : normalisedValue;
  const restoredDirty = !isSaved(restoredText, restoredSaved);
  const headingId = useId();
  const countId = useId();
  const otherNotesHeadingId = useId();
  const lockTitleId = useId();
  const lockMessageId = useId();
  const [saveState, setSaveState] = useState<SaveState>(restoredSaved === null ? "error" : "idle");
  const [announcement, setAnnouncement] = useState<Announcement>("");
  // The box's text at mount. Later text is written by the effect below:
  // were `defaultValue` to follow it, React would change an untouched box
  // without its change tracking seeing it, and clearing that box would then
  // fire no change at all.
  const [initialValue] = useState(restoredText);
  // What the box holds, for the character count and the conflict buttons.
  const [draftText, setDraftText] = useState(restoredText);
  const [dirty, setDirty] = useState(restoredDirty);
  useOnDepsChange([retainedDraft?.lastSaved], () => {
    if (!retainedDraft) return;
    setDirty(!isSaved(draftText, retainedDraft.lastSaved));
    setSaveState(retainedDraft.lastSaved === null ? "error"
      : isSaved(draftText, retainedDraft.lastSaved) ? "saved" : "idle");
  });
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
  // Whether the box held focus when the lock took it, and whether the lock
  // holds focus, so focus can be handed on rather than dropped to the page.
  const boxFocusedRef = useRef(false);
  const lockFocusedRef = useRef(false);
  const lastSaveFailedRef = useRef(restoredSaved === null);
  const disabledRef = useRef(disabled);
  const conflictRef = useRef(conflict);
  const draftRef = useRef(restoredText);
  const lastSavedRef = useRef<string | null>(restoredSaved);
  const lastPropValueRef = useRef(normalisedValue);
  const unmountedRef = useRef(false);
  const reportedDirtyRef = useRef(restoredDirty);
  const saveStateRef = useRef<SaveState>(restoredSaved === null ? "error" : "idle");
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
    // A failure stays announced through its retries, so a retry that fails
    // too changes nothing there and is not announced again.
    if (!lastSaveFailedRef.current) {
      setAnnouncement("");
    }
    try {
      await onSave(valueToSave);
      lastSavedRef.current = valueToSave;
      onDraftSavedChange?.(valueToSave);
      lastPropValueRef.current = valueToSave;
      reportDirty();
      setVisibleSaveState(
        draftRef.current === valueToSave ? "saved" : "idle",
      );
      if (lastSaveFailedRef.current) {
        lastSaveFailedRef.current = false;
        setAnnouncement("Saved");
      }

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
      onDraftSavedChange?.(null);
      lastSaveFailedRef.current = true;
      reportDirty();
      setVisibleSaveState("error");
      setAnnouncement("Save failed");
    }
  }, [clearPendingSave, onSave, onDraftSavedChange, reportDirty, setVisibleSaveState]);

  useLayoutEffect(() => {
    if (!retainedDraft) return;
    lastSavedRef.current = retainedDraft.lastSaved;
    lastSaveFailedRef.current = retainedDraft.lastSaved === null;
    saveStateRef.current = retainedDraft.lastSaved === null ? "error"
      : isSaved(draftRef.current, retainedDraft.lastSaved) ? "saved" : "idle";
    reportedDirtyRef.current = !isSaved(draftRef.current, retainedDraft.lastSaved);
  }, [retainedDraft]);

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
    onDraftTextChange?.(normalisedValue);
    onDraftSavedChange?.(normalisedValue);
    if (textareaRef.current && textareaRef.current.value !== normalisedValue) {
      textareaRef.current.value = normalisedValue;
    }
  }, [normalisedValue, onDraftTextChange, onDraftSavedChange]);

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

  // Not on mount: only a change says a failure has cleared up. Text already
  // saved is not sent again, and nothing is saved while the lock holds.
  const retrySavesRef = useRef(retrySaves);
  useEffect(() => {
    if (retrySaves === retrySavesRef.current) {
      return;
    }
    retrySavesRef.current = retrySaves;
    if (!disabledRef.current) {
      void flushSave(draftRef.current);
    }
  }, [retrySaves, flushSave]);

  // Disabling the box drops the caret to the page, and so does removing the
  // lock while it holds focus: hand focus to the lock, then back to the box.
  useLayoutEffect(() => {
    if (disabled) {
      if (boxFocusedRef.current || document.activeElement === textareaRef.current) {
        lockRef.current?.focus();
      }
      return;
    }
    const lostFocus =
      document.activeElement === null || document.activeElement === document.body;
    if (lockFocusedRef.current && lostFocus) {
      textareaRef.current?.focus();
    }
    lockFocusedRef.current = false;
  }, [disabled]);

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
      onDraftTextChange?.(nextValue);
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
    [clearPendingSave, flushSave, onDraftTextChange, reportDirty, setVisibleSaveState],
  );

  const handleBlur = useCallback(
    (event: React.FocusEvent<HTMLTextAreaElement>) => {
      // A blur from being disabled is the lock's doing; the box keeps its
      // claim on focus for when the lock hands it back.
      if (!event.currentTarget.disabled) {
        boxFocusedRef.current = false;
      }
      if (!disabledRef.current) {
        void flushSave(draftRef.current);
      }
    },
    [flushSave],
  );

  const handleFocus = useCallback(() => {
    boxFocusedRef.current = true;
  }, []);

  const handleLockFocus = useCallback(() => {
    lockFocusedRef.current = true;
    boxFocusedRef.current = false;
  }, []);

  const handleLockBlur = useCallback((event: React.FocusEvent<HTMLDivElement>) => {
    if (event.relatedTarget !== null) {
      lockFocusedRef.current = false;
      return;
    }
    // Focus went nowhere. If the lock was removed from the page, the box takes
    // focus back; if the user clicked something that takes no focus, it stays
    // on the page. Which one is known once this event is over: a removed lock
    // has lost its ref by then. A window losing focus leaves the lock focused,
    // so the box keeps its claim.
    queueMicrotask(() => {
      const lock = lockRef.current;
      if (lock !== null && document.activeElement !== lock) {
        lockFocusedRef.current = false;
      }
    });
  }, []);

  // The notice and its focused button go once a conflict is resolved, so
  // focus moves to the box, or to the lock when the box is locked.
  const focusBox = useCallback(() => {
    (disabledRef.current ? lockRef.current : textareaRef.current)?.focus();
  }, []);

  // Only its button calls this, and that button is disabled while notes
  // generation holds the notes, so nothing is saved over their version then.
  const handleKeepMine = useCallback(() => {
    // Read by the save below, before the cleared conflict re-renders.
    conflictRef.current = null;
    setAnnouncement("");
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
    onDraftTextChange?.(theirs);
    onDraftSavedChange?.(theirs);
    lastPropValueRef.current = theirs;
    if (textareaRef.current) {
      textareaRef.current.value = theirs;
    }
    setDraftText(theirs);
    setVisibleSaveState("idle");
    lastSaveFailedRef.current = false;
    setAnnouncement("");
    reportDirty();
    focusBox();
  }, [focusBox, onResolveConflict, onDraftTextChange, onDraftSavedChange, reportDirty, setVisibleSaveState]);

  const shownLength = lengthToShow(draftText);
  const overLimit = shownLength !== null && shownLength > NOTE_BODY_MAX_CHARS;

  // Nothing at rest: only saving, saved, failed, conflicting and locked say
  // anything.
  const saveMessage =
    conflict
      ? conflict.kind === "changed"
        ? "Changed elsewhere"
        : "Deleted elsewhere"
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
    <SurfaceBase data-ui-fill="card" data-ui-border="full" as="section" kind="card" className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      {/* One row: title and save state. */}
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 id={headingId} className="text-base font-semibold text-foreground">
          Notes
        </h3>
        {/* Plain text, read in browse mode; changes are announced by the
            status region below, the conflict notice and the lock. */}
        <span className="ml-auto text-xs font-semibold uppercase tracking-[0.2em] text-contrast-helper">
          {saveMessage}
        </span>
        <span role="status" className="sr-only">
          {conflict ? "" : announcement}
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
        <TextareaBase
          data-ui-fill="control"
          data-ui-border="full"
          ref={textareaRef}
          defaultValue={initialValue}
          onChange={handleChange}
          onFocus={handleFocus}
          onBlur={handleBlur}
          placeholder="Type quick reminders, decisions, or action items here..."
          aria-labelledby={headingId}
          aria-describedby={shownLength !== null ? countId : undefined}
          aria-invalid={overLimit}
          disabled={disabled}
          className={`density-surface-panel ${compact ? "min-h-[6rem]" : "min-h-[10rem]"} w-full flex-1 resize-none border border-control-border bg-control-bg px-4 py-4 text-sm leading-6 text-contrast-muted outline-none transition ${disabled ? "cursor-not-allowed opacity-70" : "focus:border-action focus:ring-2 focus:ring-action"}`}
        />
        {/* Focusable only from code: it takes focus when the lock takes the
            box from under the caret, so the reason is read out, and hands it
            back when the lock ends. Not a tab stop, since it does nothing. */}
        {disabled ? (
          <SurfaceBase
            data-ui-fill="card"
            data-ui-border="full" kind="pane"
            ref={lockRef}
            role="group"
            aria-labelledby={lockTitleId}
            aria-describedby={lockMessageId}
            tabIndex={-1}
            onFocus={handleLockFocus}
            onBlur={handleLockBlur}
            className="density-surface-panel absolute inset-0 flex items-center justify-center border border-surface-border bg-surface-card px-6 text-center outline-none"
          >
            <div>
              <div id={lockTitleId} className="text-sm font-semibold text-foreground">
                Notes are temporarily locked
              </div>
              <p id={lockMessageId} className="mt-2 text-sm leading-6 text-contrast-muted">
                {disabledMessage || "Your manual notes are being incorporated into the generated meeting notes."}
              </p>
            </div>
          </SurfaceBase>
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
      {otherNotes.length > 0 || pendingNotes.length > 0 ? (
        <div className="mt-3">
          <h4
            id={otherNotesHeadingId}
            className="text-xs font-semibold uppercase tracking-[0.2em] text-contrast-helper"
          >
            Other notes
          </h4>
          {/* Saved and unsaved notes share one scroll box, capped as the list
              alone was, and lower while capture is live, so notes piling up
              during an outage cannot take the live transcript's height. */}
          <div
            role="group"
            aria-labelledby={otherNotesHeadingId}
            tabIndex={0}
            className={`mt-2 ${compact ? "max-h-28" : "max-h-40"} space-y-2 overflow-y-auto outline-none focus-visible:ring-2 focus-visible:ring-action`}
          >
            {otherNotes.length > 0 ? (
              <ul aria-labelledby={otherNotesHeadingId} className="space-y-2">
                {otherNotes.map((note) => (
                  <li
                    key={note.id}
                    className="density-surface-panel whitespace-pre-wrap break-words bg-surface-inset px-3 py-2 text-sm leading-6 text-contrast-muted"
                  >
                    {note.body}
                  </li>
                ))}
              </ul>
            ) : null}
            {pendingNotes.length > 0 && onRetryPendingNote && onDiscardPendingNote ? (
              <UnsavedNotesList
                entries={pendingNotes}
                onRetry={onRetryPendingNote}
                onDiscard={onDiscardPendingNote}
                onEmptied={onPendingNotesEmptied}
              />
            ) : null}
          </div>
        </div>
      ) : null}
      <p className="mt-3 text-xs text-contrast-helper">
        These notes are fed into note generation and the final notes will label them as user-authored.
      </p>
    </SurfaceBase>
  );
}

export default memo(ProcessingNotesPanel);
