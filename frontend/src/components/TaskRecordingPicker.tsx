"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Check, FileAudio, Loader2, Search, X } from "lucide-react";

import { useRecordingListPages } from "@/components/recordings/_hooks/useRecordingListPages";
import { ButtonBase, InputBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { parseServerTimestamp } from "@/lib/serverTimestamp";
import { formatTimeZoneDay } from "@/lib/timezone";
import { SEARCH_DEBOUNCE_MS, useDebouncedValue } from "@/lib/useDebouncedValue";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import type { Recording, RecordingId, UserTaskRecordingLink } from "@/types";

/**
 * A recording linked to a task, as much of it as the picker shows. A task's
 * own `linked_recordings` carry it, so a linked recording keeps its name
 * whether or not the current search returned it.
 */
export type TaskRecordingChoice = Pick<
  UserTaskRecordingLink,
  "id" | "name" | "created_at" | "is_archived" | "is_deleted"
>;

const toChoice = (recording: Recording): TaskRecordingChoice => ({
  id: recording.id,
  name: recording.name,
  created_at: recording.created_at,
  is_archived: recording.is_archived,
  is_deleted: recording.is_deleted,
});

interface TaskRecordingPickerProps {
  selected: TaskRecordingChoice[];
  onChange: (selected: TaskRecordingChoice[]) => void;
  timeZone: string;
}

/**
 * Links recordings to a task (issue #906). The newest recordings show until
 * something is typed, then the list route's search answers, a page at a time,
 * so any recording in the library can be found. Archived recordings can be
 * linked; recordings in the bin cannot.
 *
 * Keyboard: the arrow keys move through the results from the search box and
 * Enter links or unlinks the highlighted one, without submitting the form.
 */
export default function TaskRecordingPicker({
  selected,
  onChange,
  timeZone,
}: TaskRecordingPickerProps) {
  const baseId = useId();
  const listId = `${baseId}-list`;
  const optionId = (id: RecordingId) => `${baseId}-option-${id}`;

  const [query, setQuery] = useState("");
  const search = useDebouncedValue(query.trim(), SEARCH_DEBOUNCE_MS);
  const list = useRecordingListPages(
    search ? { include_archived: true, q: search } : { include_archived: true },
  );
  // Rows that answer an earlier search are not offered as answers to one that failed.
  const rows = list.loadFailed ? [] : list.recordings;
  const selectedIds = new Set(selected.map((recording) => recording.id));

  const [active, setActive] = useState(-1);
  useOnDepsChange([search], () => setActive(-1));
  const activeIndex = active < rows.length ? active : -1;
  const activeId = activeIndex >= 0 ? optionId(rows[activeIndex].id) : undefined;

  const inputRef = useRef<HTMLInputElement>(null);
  const linkedRef = useRef<HTMLUListElement>(null);
  const loadMoreRef = useRef<HTMLButtonElement>(null);
  const loadStartedFromButton = useRef(false);

  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: "nearest" });
  }, [activeId]);

  // The button goes once the last page is in. Focus it held goes back to the
  // search box rather than to the page.
  useEffect(() => {
    if (list.isLoadingMore || !loadStartedFromButton.current) return;
    loadStartedFromButton.current = false;
    if (!loadMoreRef.current) inputRef.current?.focus();
  }, [list.isLoadingMore]);

  const toggle = (recording: Recording) => {
    onChange(
      selectedIds.has(recording.id)
        ? selected.filter((choice) => choice.id !== recording.id)
        : [...selected, toChoice(recording)],
    );
  };

  const unlink = (id: RecordingId, event: MouseEvent<HTMLButtonElement>) => {
    // From the keyboard (a click with no pointer), focus moves to the next
    // linked recording, or the one before, or the search box, rather than
    // being lost with the button.
    if (event.detail === 0) {
      const buttons = [...(linkedRef.current?.querySelectorAll("button") ?? [])];
      const index = buttons.indexOf(event.currentTarget);
      (buttons[index + 1] ?? buttons[index - 1] ?? inputRef.current)?.focus();
    }
    onChange(selected.filter((choice) => choice.id !== id));
  };

  const loadMore = () => {
    if (list.isLoadingMore) return;
    loadStartedFromButton.current = document.activeElement === loadMoreRef.current;
    list.loadMore();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (rows.length > 0) setActive(Math.min(activeIndex + 1, rows.length - 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        if (rows.length > 0) setActive(Math.max(activeIndex - 1, 0));
        break;
      case "Enter":
        // Enter never submits the task form from here.
        event.preventDefault();
        if (activeIndex >= 0) toggle(rows[activeIndex]);
        break;
      case "Escape":
        if (query) {
          event.preventDefault();
          event.stopPropagation();
          setQuery("");
        }
        break;
    }
  };

  const count = rows.length;
  const isSearching = list.isLoadingQuery && list.hasLoaded;
  const searchStatus = !search || list.loadFailed
    ? ""
    : list.isLoadingQuery
      ? "Searching..."
      : count === 0
        ? "No recordings match."
        : list.hasMore
          ? `${count} matches shown. Load more for older ones.`
          : `${count} ${count === 1 ? "match" : "matches"}.`;
  const status = list.isLoadingMore
    ? "Loading more recordings..."
    : list.lastAdded
      ? `${list.lastAdded} more ${list.lastAdded === 1 ? "recording" : "recordings"} loaded.`
      : searchStatus;

  const describe = (recording: Recording) => {
    const started = parseServerTimestamp(recording.created_at);
    const day = started ? formatTimeZoneDay(started, timeZone) : "";
    return recording.is_archived ? [day, "Archived"].filter(Boolean).join(" · ") : day;
  };

  return (
    <div className="space-y-2">
      {selected.length > 0 && (
        <ul ref={linkedRef} aria-label="Linked recordings" className="flex flex-wrap gap-2">
          {selected.map((recording) => (
            <li
              key={recording.id}
              className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-pill border border-status-info-border bg-status-info-bg py-0.5 pl-3 pr-0.5 text-xs font-semibold text-status-info-fg"
            >
              <FileAudio aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{recording.name}</span>
              <ButtonBase
                type="button"
                onClick={(event) => unlink(recording.id, event)}
                aria-label={`Unlink ${recording.name}`}
                title="Unlink"
                className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-surface-card focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring"
              >
                <X aria-hidden="true" className="h-3.5 w-3.5" />
              </ButtonBase>
            </li>
          ))}
        </ul>
      )}

      <div className="relative">
        {isSearching ? (
          <Loader2
            aria-hidden="true"
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-contrast-icon-muted"
          />
        ) : (
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-contrast-icon-muted"
          />
        )}
        <InputBase
          data-ui-fill="card"
          data-ui-border="full"
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label="Search recordings to link"
          aria-controls={listId}
          aria-expanded="true"
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          autoComplete="off"
          placeholder="Search all recordings"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          className="h-10 w-full rounded-2xl border border-action-border bg-surface-card pl-10 pr-4 text-sm text-foreground outline-none transition-colors placeholder:text-contrast-icon-muted focus:border-action focus:ring-2 focus:ring-action"
        />
      </div>
      <p aria-live="polite" className="sr-only">
        {status}
      </p>

      <SurfaceBase data-ui-fill="card" data-ui-border="full" kind="card" className="max-h-48 overflow-y-auto rounded-2xl border border-surface-border bg-surface-card p-2">
        <ul
          id={listId}
          role="listbox"
          aria-label={search ? "Matching recordings" : "Recent recordings"}
          aria-multiselectable="true"
          aria-busy={list.isLoadingQuery || undefined}
          className="space-y-1"
        >
          {rows.map((recording, index) => {
            const isSelected = selectedIds.has(recording.id);
            const detail = describe(recording);

            return (
              <li
                key={recording.id}
                id={optionId(recording.id)}
                role="option"
                // The name and date are separate spans, which a screen reader
                // would otherwise run together ("Weekly sync 330 Oct 2026").
                aria-label={detail ? `${recording.name}, ${detail}` : recording.name}
                aria-selected={isSelected}
                // Keeps focus in the search box, where the arrow keys work.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => toggle(recording)}
                className={`flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold transition-colors ${
                  isSelected
                    ? "bg-action-tint text-action-text"
                    : "text-contrast-muted hover:bg-surface-inset"
                } ${index === activeIndex ? "outline-2 -outline-offset-2 outline-focus-ring" : ""}`}
              >
                <FileAudio aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{recording.name}</span>
                {detail && (
                  <span className={`shrink-0 font-normal ${isSelected ? "" : "text-contrast-helper"}`}>
                    {detail}
                  </span>
                )}
                <Check
                  aria-hidden="true"
                  className={`h-3.5 w-3.5 shrink-0 ${isSelected ? "" : "invisible"}`}
                />
              </li>
            );
          })}
        </ul>

        {rows.length === 0 && (
          <div className="px-3 py-2 text-sm text-contrast-helper">
            {list.loadFailed ? (
              <>
                <p role="alert">Could not load recordings.</p>
                <ButtonBase
                  type="button"
                  onClick={() => list.refresh("all")}
                  className="mt-1 rounded text-xs font-semibold text-action-text hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  Try again
                </ButtonBase>
              </>
            ) : list.isLoadingQuery ? (
              <p className="flex items-center gap-2">
                <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                Loading recordings...
              </p>
            ) : search ? (
              <p>No recordings match.</p>
            ) : (
              <p>No recordings available to link yet.</p>
            )}
          </div>
        )}

        {rows.length > 0 && list.hasMore && !list.loadMoreFailed && (
          <ButtonBase
            ref={loadMoreRef}
            type="button"
            aria-disabled={list.isLoadingMore || undefined}
            onClick={loadMore}
            className="mt-1 flex w-full items-center justify-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold text-action-text transition-colors hover:bg-surface-inset focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring"
          >
            {list.isLoadingMore && <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />}
            {list.isLoadingMore ? "Loading more recordings..." : "Load more recordings"}
          </ButtonBase>
        )}
        {rows.length > 0 && list.loadMoreFailed && (
          <div className="flex flex-col items-center gap-1 px-3 py-2 text-xs text-contrast-helper">
            <p role="alert">Could not load more recordings.</p>
            <ButtonBase
              type="button"
              onClick={loadMore}
              className="rounded font-semibold text-action-text hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
            >
              Try again
            </ButtonBase>
          </div>
        )}
      </SurfaceBase>
    </div>
  );
}
