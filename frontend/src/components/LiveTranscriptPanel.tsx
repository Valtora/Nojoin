"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, Radio } from "lucide-react";

import { CaptureSourceChannel, TranscriptSegment } from "@/types";

/**
 * How close to the bottom counts as "following the conversation".
 *
 * Generous enough that a trackpad's inertial overscroll or a sub-pixel layout
 * rounding does not silently unpin the view mid-meeting.
 */
const STICK_TO_BOTTOM_THRESHOLD_PX = 32;

/**
 * Labels describe the capture channel, never a person.
 *
 * A capture with no shared tab audio still carries every voice in the room on
 * the microphone channel, so "Microphone" is always true but says nothing about
 * who spoke. Wording this as "You" would attribute an entire in-person meeting
 * to whoever is holding the laptop.
 */
const SOURCE_CHANNEL_LABELS: Record<CaptureSourceChannel, string> = {
  microphone: "Microphone",
  system: "Shared audio",
};

const formatElapsed = (seconds: number) => {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainder = safeSeconds % 60;
  const paddedMinutes = minutes.toString().padStart(2, "0");
  const paddedSeconds = remainder.toString().padStart(2, "0");

  return hours > 0
    ? `${hours}:${paddedMinutes}:${paddedSeconds}`
    : `${paddedMinutes}:${paddedSeconds}`;
};

const sourceChannelOf = (
  segment: TranscriptSegment,
): CaptureSourceChannel | null => {
  const channel = segment.source_channel;
  return channel === "microphone" || channel === "system" ? channel : null;
};

interface LiveTranscriptPanelProps {
  segments: TranscriptSegment[];
  hasLoaded: boolean;
  isPaused?: boolean;
}

/**
 * Read-only live transcript for the in-flight recording view.
 *
 * Renders provisional utterances as they finalize during capture. Not a second
 * transcript editor: there is nothing to click, nothing to edit, and no
 * playback, because none of that is meaningful before the recording exists as a
 * file. Speaker names are deliberately absent -- diarization only runs at
 * finalize, so every live utterance carries UNKNOWN and there is no identity to
 * show. What can be shown honestly is which capture channel carried the audio.
 */
export default function LiveTranscriptPanel({
  segments,
  hasLoaded,
  isPaused = false,
}: LiveTranscriptPanelProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);

  // Show the source label only where it changes, so a run of consecutive lines
  // from one channel reads as one block and a switch becomes a visible event
  // rather than repetition on every line.
  const lines = useMemo(
    () =>
      segments.map((segment, index) => {
        const sourceChannel = sourceChannelOf(segment);
        const previousChannel =
          index > 0 ? sourceChannelOf(segments[index - 1]) : undefined;

        return {
          segment,
          key: segment.id ?? `${segment.start}-${index}`,
          sourceChannel,
          showSourceLabel:
            sourceChannel !== null && sourceChannel !== previousChannel,
        };
      }),
    [segments],
  );

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, []);

  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }

    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    setIsPinnedToBottom(distanceFromBottom <= STICK_TO_BOTTOM_THRESHOLD_PX);
  }, []);

  // Follow new text only while the user has not scrolled away. Reading back
  // through the meeting must never be yanked out from under them.
  useEffect(() => {
    if (isPinnedToBottom) {
      scrollToBottom();
    }
  }, [lines.length, isPinnedToBottom, scrollToBottom]);

  const jumpToLatest = useCallback(() => {
    setIsPinnedToBottom(true);
    scrollToBottom();
  }, [scrollToBottom]);

  const isEmpty = lines.length === 0;

  return (
    <section className="density-surface border border-white/60 bg-white/84 shadow-xl shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/68 dark:shadow-black/20">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
          <Radio className="h-3.5 w-3.5" />
          Live Transcript
        </div>
        {!isEmpty ? (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            Provisional. Corrected when the recording is processed.
          </span>
        ) : null}
      </div>

      <div className="relative mt-4">
        {/* The rounding, border and clipping live on this wrapper rather than on
            the scrolling element itself. A scroll container draws its scrollbar
            inside its own padding box, so with the 1.5rem panel radius applied
            directly to it the bar ran straight through both right-hand corners.
            Clipping it to the rounded shape from the parent keeps the corner
            intact. */}
        <div className="density-surface-panel overflow-hidden border border-orange-200/70 bg-white dark:border-orange-500/20 dark:bg-gray-900">
          <div
            ref={scrollContainerRef}
            onScroll={handleScroll}
            data-testid="live-transcript-scroll"
            className="h-[24rem] overflow-y-auto px-4 py-4"
          >
            {isEmpty ? (
              <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                <p className="text-sm font-medium text-gray-700 dark:text-gray-200">
                  {isPaused ? "Recording is paused." : "Listening."}
                </p>
                <p className="mt-2 max-w-sm text-sm leading-6 text-gray-500 dark:text-gray-400">
                  {hasLoaded
                    ? "Text appears a few seconds behind the conversation, as each sentence completes."
                    : "Loading the transcript so far."}
                </p>
              </div>
            ) : (
              <ol className="space-y-3">
                {lines.map(({ segment, key, sourceChannel, showSourceLabel }) => (
                  <li key={key} data-testid="live-transcript-line">
                    {showSourceLabel && sourceChannel ? (
                      <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-orange-700 dark:text-orange-300">
                        {SOURCE_CHANNEL_LABELS[sourceChannel]}
                      </div>
                    ) : null}
                    <div className="flex gap-3">
                      <span className="shrink-0 pt-0.5 font-mono text-xs tabular-nums text-gray-400 dark:text-gray-500">
                        {formatElapsed(segment.start)}
                      </span>
                      <p className="min-w-0 flex-1 text-sm italic leading-6 text-gray-600 dark:text-gray-300">
                        {segment.text}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>

        {!isEmpty && !isPinnedToBottom ? (
          <button
            type="button"
            onClick={jumpToLatest}
            className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-orange-200 bg-white px-3 py-1.5 text-xs font-semibold text-orange-700 shadow-lg shadow-orange-950/10 transition-colors hover:bg-orange-50 dark:border-orange-500/30 dark:bg-gray-950 dark:text-orange-300 dark:hover:bg-orange-500/10"
          >
            <ArrowDown className="h-3.5 w-3.5" />
            Jump to latest
          </button>
        ) : null}
      </div>
    </section>
  );
}
