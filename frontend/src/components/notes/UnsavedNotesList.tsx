"use client";

import { useRef } from "react";

import { notePlace, noteSnippet, type OutboxEntry } from "@/lib/notes/noteOutbox";

import Button from "../ui/Button";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";

interface UnsavedNotesListProps {
  entries: readonly OutboxEntry[];
  onRetry: (key: string) => void;
  onDiscard: (key: string) => void;
  /** Where focus goes when Discard removes the last note listed. */
  onEmptied?: () => void;
}

const capitalised = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/**
 * Notes from the note box still on their way, each saying where it goes and
 * what it says, with Retry and Discard once it fails. A button that removes
 * itself hands focus on: Retry to its own note, Discard to the next one.
 */
export default function UnsavedNotesList({
  entries,
  onRetry,
  onDiscard,
  onEmptied,
}: UnsavedNotesListProps) {
  const items = useRef(new Map<string, HTMLLIElement>());
  if (entries.length === 0) return null;

  const discard = (index: number) => {
    const neighbour = entries[index + 1] ?? entries[index - 1];
    onDiscard(entries[index].key);
    const next = neighbour ? items.current.get(neighbour.key) : null;
    if (next) next.focus();
    else onEmptied?.();
  };

  return (
    <ul className="space-y-2" aria-label="Notes not saved yet">
      {entries.map((entry, index) => {
        const name = `${notePlace(entry)}, “${noteSnippet(entry.body)}”`;
        return (
          <li
            key={entry.key}
            ref={(element) => {
              if (element) items.current.set(entry.key, element);
              else items.current.delete(entry.key);
            }}
            tabIndex={-1}
            data-testid="unsaved-note"
            data-note-status={entry.status}
            className="density-surface-panel bg-surface-inset px-3 py-2 outline-none focus-visible:ring-2 focus-visible:ring-action"
          >
            <p className="text-xs text-contrast-helper">{capitalised(notePlace(entry))}</p>
            <p className="line-clamp-2 whitespace-pre-wrap break-words text-sm leading-5 text-foreground">
              {entry.body}
            </p>
            {entry.status === "saving" ? (
              <p className="mt-1 text-xs text-contrast-helper">Saving…</p>
            ) : (
              <>
                <p className="mt-1 text-xs text-status-danger-fg">{entry.error ?? "Not saved."}</p>
                <div className="mt-1.5 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    aria-label={`Retry note ${name}`}
                    onClick={() => {
                      items.current.get(entry.key)?.focus();
                      onRetry(entry.key);
                    }}
                    className={NOTE_BUTTON_TOUCH}
                  >
                    Retry
                  </Button>
                  <Button
                    size="sm"
                    aria-label={`Discard note ${name}`}
                    onClick={() => discard(index)}
                    className={NOTE_BUTTON_TOUCH}
                  >
                    Discard
                  </Button>
                </div>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
