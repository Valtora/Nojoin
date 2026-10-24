"use client";

import { useRef } from "react";
import { Camera, ImageOff, Loader2 } from "lucide-react";

import { capturePreviewUrl, type Capture } from "@/lib/api";

import { NOTE_TIME_COLUMN } from "../notes/TimelineNoteLine";
import { NOTE_BUTTON_TOUCH } from "../notes/noteButtons";
import Button from "../ui/Button";

import type { CaptureItem } from "./captureItems";

interface CaptureMarkerProps {
  item: CaptureItem;
  /** The capture's time, written the way the surrounding lines write theirs. */
  timeLabel: string;
  timeColumnClassName?: string;
  onOpen: (capture: Capture) => void;
  onRetry?: (key: string) => void;
  /** Removes the marker: the caller moves focus somewhere that stays. */
  onDiscard?: (key: string) => void;
}

/** What a saved capture's parse has come to, in a few words. */
export const captureReadState = (capture: Capture): string | null => {
  if (capture.status === "PENDING" || capture.status === "PROCESSING") return "Reading…";
  if (capture.status === "ERROR") return "Couldn't be read";
  return null;
};

/**
 * A capture in the transcript, on its own line after the line in progress
 * when it was taken, the way a note at that time sits. Its picture opens the
 * capture; one still uploading shows its own copy until the server has it.
 */
export default function CaptureMarker({
  item,
  timeLabel,
  timeColumnClassName = NOTE_TIME_COLUMN,
  onOpen,
  onRetry,
  onDiscard,
}: CaptureMarkerProps) {
  const lineRef = useRef<HTMLDivElement>(null);
  const { capture, pending } = item;
  const state = capture ? captureReadState(capture) : null;
  const failed = pending?.status === "failed";
  const picture = capture ? capturePreviewUrl(capture.id) : (pending?.preview ?? null);
  const thumbnail = picture ? (
    // A preview from the API or a data URL of the image itself: next/image
    // would proxy neither.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={picture}
      alt=""
      loading="lazy"
      className="block h-24 w-auto max-w-full object-contain"
    />
  ) : (
    <span className="flex h-24 w-40 items-center justify-center text-contrast-icon-muted">
      <ImageOff aria-hidden="true" className="h-5 w-5" />
    </span>
  );

  return (
    <div
      ref={lineRef}
      tabIndex={failed ? -1 : undefined}
      data-testid="capture-marker"
      data-capture-key={item.key}
      data-capture-id={capture?.id}
      className="flex gap-3 outline-none focus-visible:ring-2 focus-visible:ring-action"
    >
      <span className={timeColumnClassName}>{timeLabel}</span>
      <div className="min-w-0 flex-1 border-l-2 border-action-border pl-3">
        <div className="flex flex-wrap items-center gap-x-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-action-text">
          <span className="inline-flex items-center gap-1.5">
            <Camera className="h-3.5 w-3.5" aria-hidden="true" />
            Capture
          </span>
          {capture?.use_in_notes ? (
            <span className="font-normal normal-case tracking-normal text-contrast-helper">
              Used in notes
            </span>
          ) : null}
          {state ? (
            <span className="font-normal normal-case tracking-normal text-contrast-helper">
              {state}
            </span>
          ) : null}
          {pending && !failed ? (
            <span className="inline-flex items-center gap-1 font-normal normal-case tracking-normal text-contrast-helper">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
              Saving…
            </span>
          ) : null}
        </div>
        {capture ? (
          <button
            type="button"
            onClick={() => onOpen(capture)}
            aria-label={`Open the capture at ${timeLabel}`}
            className="mt-1 inline-block max-w-full overflow-hidden rounded-md border border-surface-border bg-surface-inset transition-colors hover:border-action-border focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
          >
            {thumbnail}
          </button>
        ) : (
          <div className="mt-1 inline-block max-w-full overflow-hidden rounded-md border border-surface-border bg-surface-inset opacity-80">
            {thumbnail}
          </div>
        )}
        {failed ? (
          <>
            <p className="mt-1 text-xs text-status-danger-fg">
              {pending?.error ?? "Not saved."}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              {onRetry ? (
                <Button
                  size="sm"
                  aria-label={`Retry the capture at ${timeLabel}`}
                  onClick={() => {
                    lineRef.current?.focus();
                    onRetry(item.key);
                  }}
                  className={NOTE_BUTTON_TOUCH}
                >
                  Retry
                </Button>
              ) : null}
              {onDiscard ? (
                <Button
                  size="sm"
                  aria-label={`Discard the capture at ${timeLabel}`}
                  onClick={() => onDiscard(item.key)}
                  className={NOTE_BUTTON_TOUCH}
                >
                  Discard
                </Button>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
