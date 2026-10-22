"use client";

import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ArrowDown, Radio } from "lucide-react";

import type { InlineNote } from "@/lib/notes/noteOutbox";
import {
  bodyNotesAmongLines,
  formatTimelineMs,
  placeTimelineNotes,
  segmentEndMs,
  segmentStartMs,
} from "@/lib/notes/placement";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import { CaptureSourceChannel, TranscriptSegment } from "@/types";

import TimelineNoteLine from "./notes/TimelineNoteLine";
import TranscriptLineText from "./transcript/TranscriptLineText";

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

/** A live speaker label, `LIVE_<n>`, written by the live lane's clustering. */
const LIVE_SPEAKER_LABEL = /^LIVE_(\d+)$/;

/**
 * "Speaker 3" for a line the live lane labelled `LIVE_3`, else null.
 *
 * Live labels number voices in the order they were first heard. They are not
 * the final pass's speakers, which are named when the recording is processed.
 */
const liveSpeakerOf = (segment: TranscriptSegment): string | null => {
  const match = LIVE_SPEAKER_LABEL.exec(segment.speaker ?? "");
  return match ? `Speaker ${Number(match[1])}` : null;
};

interface LiveTranscriptPanelProps {
  segments: TranscriptSegment[];
  hasLoaded: boolean;
  isPaused?: boolean;
  /** Point and selection notes, each placed after the line it follows. */
  notes?: readonly InlineNote[];
  onRetryNote?: (key: string) => void;
  onDiscardNote?: (key: string) => void;
  /** Rendered under the transcript window: the note box. */
  footer?: ReactNode;
}

const NO_NOTES: readonly InlineNote[] = [];

const noteTimeMs = (note: InlineNote) =>
  note.kind === "selection" ? (note.start_ms ?? 0) : (note.anchor_ms ?? 0);

/**
 * Read-only live transcript for the in-flight recording view.
 *
 * Renders provisional utterances as they finalize during capture, and the
 * user's notes among them, each on its own line after the line it follows
 * (`placeTimelineNotes`, the rule every view and prompt shares). Not a second
 * transcript editor: there is nothing to click, nothing to edit, and no
 * playback, because none of that is meaningful before the recording exists as a
 * file. Names are deliberately absent: identity is decided at finalize. When
 * live speaker labels are on, a line may carry a provisional label ("Speaker
 * 3") from the live lane's clustering. It is italic and muted until the label
 * locks, so the cue does not rest on colour alone. Which capture channel
 * carried the audio is shown as well.
 */
export default function LiveTranscriptPanel({
  segments,
  hasLoaded,
  isPaused = false,
  notes = NO_NOTES,
  onRetryNote,
  onDiscardNote,
  footer,
}: LiveTranscriptPanelProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);

  // Show the source and speaker labels only where they change, so a run of
  // consecutive lines from one channel or voice reads as one block and a
  // switch becomes a visible event rather than repetition on every line.
  const lines = useMemo(
    () =>
      segments.map((segment, index) => {
        const sourceChannel = sourceChannelOf(segment);
        const previous = index > 0 ? segments[index - 1] : undefined;
        const previousChannel = previous ? sourceChannelOf(previous) : undefined;
        const speaker = liveSpeakerOf(segment);

        return {
          segment,
          key: segment.id ?? `${segment.start}-${index}`,
          sourceChannel,
          showSourceLabel:
            sourceChannel !== null && sourceChannel !== previousChannel,
          speaker:
            speaker !== null &&
            (previous === undefined || speaker !== liveSpeakerOf(previous))
              ? speaker
              : null,
          speakerLocked: segment.speaker_state === "stable",
        };
      }),
    [segments],
  );

  // Placed on the lines as listed, which is also the order they render in.
  const notesAmongLines = useMemo(
    () =>
      bodyNotesAmongLines(
        placeTimelineNotes(segments, notes),
        segments.map((_, index) => index),
      ),
    [segments, notes],
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
  //
  // Before the paint, not after it: on a plain effect the browser paints the
  // grown transcript at the old scroll position and corrects it a frame later,
  // which reads as the text spilling past the window and snapping back. The
  // finished transcript's scroll hook uses a layout effect for the same reason.
  //
  // Keyed on `lines` rather than `lines.length`, because a provisional
  // utterance is rewritten in place as it finalises: the text grows and wraps
  // while the count stays the same. `lines` is memoised on `segments`, so a
  // poll that returned nothing new still does nothing here. A note added at
  // the bottom is followed the same way.
  useLayoutEffect(() => {
    if (isPinnedToBottom) {
      scrollToBottom();
    }
  }, [lines, notesAmongLines, isPinnedToBottom, scrollToBottom]);

  const jumpToLatest = useCallback(() => {
    setIsPinnedToBottom(true);
    scrollToBottom();
  }, [scrollToBottom]);

  const isEmpty = lines.length === 0 && notes.length === 0;

  const renderNote = (note: InlineNote) => (
    <li key={note.key} data-testid="live-transcript-note">
      <TimelineNoteLine
        noteId={note.id}
        body={note.body}
        timeLabel={formatTimelineMs(noteTimeMs(note))}
        status={note.status}
        error={note.error}
        onRetry={onRetryNote ? () => onRetryNote(note.key) : undefined}
        onDiscard={onDiscardNote ? () => onDiscardNote(note.key) : undefined}
      />
    </li>
  );

  return (
    // Clips in the two-column layout, where it has a definite height and the
    // note box sits at its bottom: nothing in it may paint over the Notes card
    // below. Not when stacked, where the page scrolls: a clipping card is a
    // scroll container, and the note box would stick to the card, which never
    // scrolls, rather than to the page above an on-screen keyboard.
    <section className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card @min-[54rem]:overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex items-center gap-2 rounded-pill border border-action-border bg-action-tint px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-action-text">
          <Radio className="h-3.5 w-3.5" />
          Live Transcript
        </div>
        {!isEmpty ? (
          <span className="text-xs text-contrast-helper">
            Provisional. Corrected when the recording is processed.
          </span>
        ) : null}
      </div>

      {/* The window fills the card, and the card fills what the column has
          left, with no ceiling: a capped module in an items-stretch grid only
          moves the dead space around. Meeting Edge scrolls inside its own cell,
          so the row height comes from the window rather than from either panel.

          The window clips its own lines in every layout; the card clips only
          from 54rem (above). The note box's wrapper starts at the window, so
          stuck above a keyboard it rides over transcript lines and never over
          the header. */}
      <div className="mt-4 flex min-h-0 flex-1 flex-col">
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          {/* The transcript window is painted by this overlay rather than by the
              scrolling element, and stops short of the right edge by
              SCROLLBAR_GUTTER. A scroll container always draws its scrollbar
              inside its own border box, so any framing applied to the scroller
              itself puts the bar within the frame and across its rounded corners.
              Drawing the frame behind the content and ending it before the gutter
              leaves the bar outside the window entirely. */}
          <div
            aria-hidden
            className="density-surface-panel pointer-events-none absolute inset-y-0 left-0 right-[var(--live-transcript-gutter)] border border-action-border bg-surface-card"
          />
          <div
            ref={scrollContainerRef}
            onScroll={handleScroll}
            data-testid="live-transcript-scroll"
            // The floor holds only in the stacked layout, where the page scrolls
            // and a short transcript would simply leave a gap. From 54rem the
            // column has a definite height and this module absorbs whatever the
            // notes and documents cards leave, which on a short window is less
            // than 14rem. A floor there would overflow the card and paint across
            // the notes card, so `min-h-0` lets the window take the card exactly.
            className="relative min-h-[14rem] flex-1 overflow-y-auto @min-[54rem]:min-h-0"
          >
            <div className="flex min-h-full flex-col py-4 pl-4 pr-8">
              {isEmpty ? (
                <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
                  <p className="text-sm font-medium text-contrast-muted">
                    {isPaused ? "Recording is paused." : "Listening."}
                  </p>
                  <p className="mt-2 max-w-sm text-sm leading-6 text-contrast-helper">
                    {hasLoaded
                      ? "Text appears a few seconds behind the conversation, as each sentence completes."
                      : "Loading the transcript so far."}
                  </p>
                </div>
              ) : (
                <ol className="space-y-3">
                  {notesAmongLines.opening.map(renderNote)}
                  {lines.map(({ segment, key, sourceChannel, showSourceLabel, speaker, speakerLocked }, index) => [
                    <li key={key} data-testid="live-transcript-line" data-line-index={index}>
                      {speaker ? (
                        <div
                          data-testid="live-transcript-speaker"
                          data-locked={speakerLocked ? "true" : "false"}
                          className={
                            speakerLocked
                              ? "mb-1 text-xs font-semibold text-contrast-muted"
                              : "mb-1 text-xs italic text-contrast-helper"
                          }
                        >
                          {speaker}
                        </div>
                      ) : null}
                      {showSourceLabel && sourceChannel ? (
                        <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-action-text">
                          {SOURCE_CHANNEL_LABELS[sourceChannel]}
                        </div>
                      ) : null}
                      <div className="flex gap-3">
                        <span className="shrink-0 pt-0.5 font-mono text-xs tabular-nums text-contrast-icon-muted">
                          {formatElapsed(segment.start)}
                        </span>
                        <TranscriptLineText
                          text={segment.text}
                          segmentId={getTranscriptSegmentKey(segment, index)}
                          startMs={segmentStartMs(segment)}
                          endMs={segmentEndMs(segment)}
                          className="min-w-0 flex-1 text-sm italic leading-6 text-contrast-helper"
                        />
                      </div>
                    </li>,
                    ...(notesAmongLines.afterLine.get(index) ?? []).map(renderNote),
                  ])}
                </ol>
              )}
            </div>
          </div>

          {!isEmpty && !isPinnedToBottom ? (
            <button
              type="button"
              onClick={jumpToLatest}
              className="absolute bottom-3 left-[calc(50%-var(--live-transcript-gutter)/2)] inline-flex -translate-x-1/2 items-center gap-1.5 rounded-pill border border-action-border bg-surface-card px-3 py-1.5 text-xs font-semibold text-action-text shadow-float transition-colors hover:bg-action-tint"
            >
              <ArrowDown className="h-3.5 w-3.5" />
              Jump to latest
            </button>
          ) : null}
        </div>

        {footer}
      </div>
    </section>
  );
}
