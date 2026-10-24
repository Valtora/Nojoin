"use client";

import { useId, useMemo, useRef } from "react";
import { Loader2, Play } from "lucide-react";

import { capturePreviewUrl, type Capture } from "@/lib/api";
import type { CapturesState } from "@/lib/captures/useCaptures";
import { CAPTURE_SHORTCUT_LABEL, CAPTURE_SHORTCUT_LABEL_MAC } from "@/lib/captures/useCaptureActions";
import { cn } from "@/lib/cn";
import { formatTimelineMs } from "@/lib/notes/placement";

import { NOTE_BUTTON_TOUCH } from "../notes/noteButtons";
import Button from "../ui/Button";
import IconButton from "../ui/IconButton";
import { Switch } from "../ui/Switch";
import { TOUCH_TARGET_SQUARE } from "../ui/touchTarget";

import { captureItems, type CaptureItem } from "./captureItems";
import { captureReadState } from "./CaptureMarker";

interface CapturesListProps {
  state: CapturesState;
  /** Opens a saved capture full size. */
  onOpen: (capture: Capture) => void;
  /** Plays the audio from a time in seconds; without it, no Play buttons. */
  onPlay?: (seconds: number) => void;
  /** Scrolls the transcript to a capture's marker; without it, times are plain text. */
  onShowInTranscript?: (capture: Capture) => void;
  className?: string;
}

/**
 * Every capture on a meeting in timeline order, each with its notes switch:
 * the side column's Captures tab, and the phone's. A capture still uploading
 * from this page is listed with it, and one that failed offers Retry and
 * Discard.
 */
export default function CapturesList({
  state,
  onOpen,
  onPlay,
  onShowInTranscript,
  className,
}: CapturesListProps) {
  const { captures, pending, setUseInNotes, retry, discard } = state;
  const items = useMemo(() => captureItems(captures, pending), [captures, pending]);
  const listRef = useRef<HTMLDivElement>(null);
  const summaryId = useId();
  const used = captures?.filter((capture) => capture.use_in_notes).length ?? 0;

  const renderRow = (item: CaptureItem) => {
    const time = formatTimelineMs(item.captureMs);
    const { capture, pending: unsent } = item;
    const switchId = `capture-switch-${item.key}`;
    const picture = capture ? capturePreviewUrl(capture.id) : unsent?.preview;
    const readState = capture ? captureReadState(capture) : null;
    return (
      <li
        key={item.key}
        data-testid="capture-row"
        data-capture-key={item.key}
        // The picture above its controls in a narrow column, beside them
        // where the list has room.
        className="flex flex-col gap-2 rounded-sm border-l-2 border-action-border py-1 pl-2 @min-[22rem]:flex-row @min-[22rem]:gap-3"
      >
        <button
          type="button"
          disabled={!capture}
          onClick={() => capture && onOpen(capture)}
          aria-label={capture ? `Open the capture at ${time}` : `The capture at ${time}, not saved yet`}
          className="aspect-video w-full max-w-72 shrink-0 overflow-hidden rounded-md border border-surface-border bg-surface-inset transition-colors enabled:hover:border-action-border focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring @min-[22rem]:w-32"
        >
          {picture ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={picture} alt="" loading="lazy" className="h-full w-full object-contain" />
          ) : null}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-1">
            {capture && onShowInTranscript ? (
              <button
                type="button"
                onClick={() => onShowInTranscript(capture)}
                aria-label={`Show the capture at ${time} in the transcript`}
                className="rounded-sm font-mono text-xs tabular-nums text-action-text underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-focus-ring"
              >
                {time}
              </button>
            ) : (
              <span className="font-mono text-xs tabular-nums text-contrast-icon-muted">{time}</span>
            )}
            {readState ? <span className="text-xs text-contrast-helper">{readState}</span> : null}
            {unsent?.status === "sending" ? (
              <span className="inline-flex items-center gap-1 text-xs text-contrast-helper">
                <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
                Saving…
              </span>
            ) : null}
            {capture && onPlay ? (
              <IconButton
                size="sm"
                aria-label={`Play from ${time}`}
                title="Play from here"
                icon={<Play aria-hidden="true" />}
                onClick={() => onPlay(capture.capture_ms / 1000)}
                className={`-my-1.5 ml-auto ${TOUCH_TARGET_SQUARE}`}
              />
            ) : null}
          </div>
          {capture ? (
            <div className="mt-1 flex items-center gap-2">
              <Switch
                id={switchId}
                checked={capture.use_in_notes}
                onCheckedChange={(checked) => void setUseInNotes(capture, checked)}
                aria-labelledby={`${switchId}-label`}
              />
              <label
                id={`${switchId}-label`}
                htmlFor={switchId}
                className="cursor-pointer whitespace-nowrap text-sm text-foreground"
              >
                Use in notes
              </label>
            </div>
          ) : null}
          {unsent?.status === "failed" ? (
            <>
              <p className="mt-1 text-xs text-status-danger-fg">{unsent.error ?? "Not saved."}</p>
              <div className="mt-1.5 flex flex-wrap gap-2">
                <Button
                  size="sm"
                  aria-label={`Retry the capture at ${time}`}
                  onClick={() => retry(item.key)}
                  className={NOTE_BUTTON_TOUCH}
                >
                  Retry
                </Button>
                <Button
                  size="sm"
                  aria-label={`Discard the capture at ${time}`}
                  onClick={() => {
                    discard(item.key);
                    listRef.current?.focus();
                  }}
                  className={NOTE_BUTTON_TOUCH}
                >
                  Discard
                </Button>
              </div>
            </>
          ) : null}
        </div>
      </li>
    );
  };

  return (
    <div
      ref={listRef}
      tabIndex={-1}
      data-testid="captures"
      className={cn("@container min-h-0 overflow-y-auto p-3 pb-clear-floating-3 outline-none", className)}
    >
      {captures === null && pending.length === 0 ? (
        <p className="p-4 text-center text-sm italic text-contrast-helper">Loading captures…</p>
      ) : items.length === 0 ? (
        <p className="p-4 text-center text-sm text-contrast-helper">
          No captures yet. While you record with a screen shared, Capture or{" "}
          {CAPTURE_SHORTCUT_LABEL} ({CAPTURE_SHORTCUT_LABEL_MAC} on a Mac) saves what it shows.
          Pasting an image into the note box does the same.
        </p>
      ) : (
        <>
          <p id={summaryId} className="mb-2 px-1 text-xs text-contrast-helper">
            {used === 0
              ? "Searchable once read. Turn on Use in notes for the AI notes to read one."
              : `${used} of ${captures?.length ?? 0} used in notes.`}
          </p>
          <ul aria-label="Captures" aria-describedby={summaryId} className="space-y-2">
            {items.map(renderRow)}
          </ul>
        </>
      )}
    </div>
  );
}
