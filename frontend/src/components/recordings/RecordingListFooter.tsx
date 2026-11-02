"use client";

import { useEffect, useRef, type RefObject } from "react";
import { Loader2 } from "lucide-react";

import { ButtonBase } from "@/components/ui/nativeBases";

interface RecordingListFooterProps {
  /** Rows shown above, to find the first of a page just loaded. */
  rowCount: number;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreFailed: boolean;
  /** How many rows the last page added, announced once it has. */
  lastAdded: number | null;
  onLoadMore: () => void;
  /** The scroller the list sits in: a page loads as its end comes near. */
  scrollRootRef: RefObject<HTMLElement | null>;
  listRef: RefObject<HTMLElement | null>;
  /** Said under the last row once there is nothing further to load. */
  endMessage: string | null;
}

/** How far below the visible rows the next page starts to load. */
const PRELOAD_MARGIN = "0px 0px 400px 0px";

/**
 * The end of the recordings list: a button that loads the next page, which
 * also loads it by itself as it scrolls near, then the line that says the
 * list has ended.
 *
 * The button stays in place while its page loads, so keyboard focus is not
 * lost. When the page arrives, focus moves on to its first recording.
 */
export default function RecordingListFooter({
  rowCount,
  hasMore,
  isLoadingMore,
  loadMoreFailed,
  lastAdded,
  onLoadMore,
  scrollRootRef,
  listRef,
  endMessage,
}: RecordingListFooterProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const loadStart = useRef<{ rowCount: number; focused: boolean } | null>(null);
  const showButton = hasMore && !loadMoreFailed;

  useEffect(() => {
    const button = buttonRef.current;
    if (!showButton || isLoadingMore || !button) return;
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
      },
      { root: scrollRootRef.current, rootMargin: PRELOAD_MARGIN },
    );
    observer.observe(button);
    return () => observer.disconnect();
  }, [showButton, isLoadingMore, onLoadMore, scrollRootRef]);

  useEffect(() => {
    if (isLoadingMore) {
      loadStart.current = {
        rowCount,
        focused: document.activeElement === buttonRef.current,
      };
      return;
    }
    const started = loadStart.current;
    loadStart.current = null;
    if (!started?.focused || rowCount <= started.rowCount) return;
    listRef.current
      ?.querySelectorAll<HTMLElement>("[data-recording-link]")
      [started.rowCount]?.focus();
  }, [isLoadingMore, rowCount, listRef]);

  const announcement = isLoadingMore
    ? "Loading more recordings"
    : lastAdded
      ? `${lastAdded} more ${lastAdded === 1 ? "recording" : "recordings"} loaded`
      : "";

  return (
    <div className="px-2 pb-3">
      {showButton && (
        <ButtonBase
          ref={buttonRef}
          aria-disabled={isLoadingMore || undefined}
          onClick={() => {
            if (!isLoadingMore) onLoadMore();
          }}
          className="flex w-full items-center justify-center gap-2 rounded-lg py-2 text-xs text-rail-fg-muted hover:text-rail-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
        >
          {isLoadingMore && <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />}
          {isLoadingMore ? "Loading more recordings..." : "Load more recordings"}
        </ButtonBase>
      )}
      {loadMoreFailed && (
        <div className="flex flex-col items-center gap-1 py-2 text-xs text-rail-fg-muted">
          <p role="alert">Could not load more recordings.</p>
          <ButtonBase
            onClick={onLoadMore}
            className="rounded px-2 py-1 font-medium text-action-text hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
          >
            Try again
          </ButtonBase>
        </div>
      )}
      {!hasMore && !loadMoreFailed && endMessage && (
        <p className="py-2 text-center text-xs text-rail-fg-muted">{endMessage}</p>
      )}
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </div>
  );
}
