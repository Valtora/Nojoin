"use client";

import { useMemo, useRef, useState } from "react";

import { stripNote } from "@/lib/notes/headNoteSync";
import {
  EMPTY_DRAFT,
  noteFromDraft,
  noteSnippet,
  type ComposerDraft,
  type OutboxKind,
} from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import type { RecordingId } from "@/types";

import Button from "../ui/Button";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";
import UnsavedNotesList from "./UnsavedNotesList";

interface RecordingUnsavedNotesProps {
  recordingId: RecordingId;
  /** The kinds to list; another panel may list the rest. Every kind by default. */
  kinds?: readonly OutboxKind[];
  /** Where an "at send" draft goes now that capture is over: its end. */
  readEndMs: () => number;
  /** Where focus goes when the last thing listed here is removed. */
  onEmptied?: () => void;
  className?: string;
}

const draftPlace = (draft: ComposerDraft) => {
  if (draft.selection) return `On the text at ${formatTimelineMs(draft.selection.startMs)}`;
  switch (draft.mode) {
    case "typing":
      return draft.typingStartMs === null
        ? "At the end of the recording"
        : `At ${formatTimelineMs(draft.typingStartMs)}`;
    case "send":
      return "At the end of the recording";
    case "head":
      return "Before the transcript";
    case "tail":
      return "After the transcript";
  }
};

/**
 * Notes from the note box not saved yet, and text left in the box, once the
 * live view and its box are gone: after Stop, through processing and in the
 * finished recording. Nothing typed is dropped when capture ends, here or on
 * another device.
 */
export default function RecordingUnsavedNotes({
  recordingId,
  kinds,
  readEndMs,
  onEmptied,
  className = "",
}: RecordingUnsavedNotesProps) {
  const { outbox, snapshot } = useNoteOutbox(recordingId);
  const [draftError, setDraftError] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const entries = useMemo(
    () =>
      kinds ? snapshot.entries.filter((entry) => kinds.includes(entry.kind)) : snapshot.entries,
    [kinds, snapshot.entries],
  );
  const { draft, notice } = snapshot;
  const hasDraft = stripNote(draft.text) !== "";

  const clearDraft = () => outbox.setDraft({ ...EMPTY_DRAFT, mode: draft.mode });
  const focusAfterDraft = () => {
    if (entries.length > 0) headingRef.current?.focus();
    else onEmptied?.();
  };

  const sendDraft = () => {
    // The note and the box's emptying are one change, so a kept copy of the
    // outbox never holds both.
    const refusal = outbox.submit(noteFromDraft(draft, readEndMs), { clearDraft: true });
    if (refusal) {
      setDraftError(refusal);
      return;
    }
    setDraftError(null);
    headingRef.current?.focus();
  };

  return (
    <>
      {/* Present before anything is said, so it is read out. */}
      <p className="sr-only" aria-live="polite">
        {notice?.text ?? ""}
      </p>
      {entries.length > 0 || hasDraft ? (
        <section
          aria-labelledby={`${recordingId}-unsaved-notes`}
          data-testid="unsaved-notes"
          className={className}
        >
          <h3
            id={`${recordingId}-unsaved-notes`}
            ref={headingRef}
            tabIndex={-1}
            className="text-sm font-semibold text-foreground outline-none"
          >
            Notes not saved yet
          </h3>
          <div className="mt-2 space-y-2">
            {hasDraft ? (
              <div
                data-testid="unsaved-draft"
                className="density-surface-panel bg-surface-inset px-3 py-2"
              >
                <p className="text-xs text-contrast-helper">
                  Not sent: {draftPlace(draft).toLowerCase()}
                </p>
                <p className="line-clamp-2 whitespace-pre-wrap break-words text-sm leading-5 text-foreground">
                  {draft.text}
                </p>
                {draftError ? (
                  <p className="mt-1 text-xs text-status-danger-fg">{draftError}</p>
                ) : null}
                <div className="mt-1.5 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    aria-label={`Add note “${noteSnippet(draft.text)}”`}
                    onClick={sendDraft}
                    className={NOTE_BUTTON_TOUCH}
                  >
                    Add note
                  </Button>
                  <Button
                    size="sm"
                    aria-label={`Discard unsent note “${noteSnippet(draft.text)}”`}
                    onClick={() => {
                      clearDraft();
                      setDraftError(null);
                      focusAfterDraft();
                    }}
                    className={NOTE_BUTTON_TOUCH}
                  >
                    Discard
                  </Button>
                </div>
              </div>
            ) : null}
            <UnsavedNotesList
              entries={entries}
              onRetry={outbox.retry}
              onDiscard={outbox.discard}
              onEmptied={() => (hasDraft ? headingRef.current?.focus() : onEmptied?.())}
            />
          </div>
        </section>
      ) : null}
    </>
  );
}
