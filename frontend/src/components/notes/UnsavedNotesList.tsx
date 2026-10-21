"use client";

import type { OutboxEntry } from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";

interface UnsavedNotesListProps {
  entries: readonly OutboxEntry[];
  onRetry: (key: string) => void;
  onDiscard: (key: string) => void;
}

const BUTTON =
  "inline-flex h-[44px] items-center rounded-lg border border-control-border px-3 font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text";

const placeOf = (entry: OutboxEntry) =>
  entry.kind === "head"
    ? "Before the transcript"
    : entry.kind === "tail"
      ? "After the transcript"
      : `At ${formatTimelineMs(entry.anchorMs ?? 0)}`;

/** Notes from the note box still on their way, with Retry and Discard once one fails. */
export default function UnsavedNotesList({
  entries,
  onRetry,
  onDiscard,
}: UnsavedNotesListProps) {
  if (entries.length === 0) return null;
  return (
    <ul className="space-y-1" aria-label="Notes not saved yet">
      {entries.map((entry) => (
        <li
          key={entry.key}
          data-testid="unsaved-note"
          className="flex flex-wrap items-center gap-2 text-xs text-contrast-helper"
        >
          <span className="min-w-0 flex-1 truncate">
            {placeOf(entry)}: {entry.body}
          </span>
          {entry.status === "saving" ? (
            <span>Saving…</span>
          ) : (
            <>
              <span role="alert" className="text-status-danger-fg">
                {entry.error ?? "Not saved."}
              </span>
              <button type="button" onClick={() => onRetry(entry.key)} className={BUTTON}>
                Retry
              </button>
              <button type="button" onClick={() => onDiscard(entry.key)} className={BUTTON}>
                Discard
              </button>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
