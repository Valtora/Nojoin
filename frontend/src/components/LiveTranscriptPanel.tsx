"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import {
  useCallback,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ArrowDown, Radio } from "lucide-react";

import type { InlineNote, NoteSelection } from "@/lib/notes/noteOutbox";
import {
  bodyNotesAmongLines,
  formatTimelineMs,
  placeTimelineNotes,
  segmentEndMs,
  segmentStartMs,
} from "@/lib/notes/placement";
import { getRecordingSpeakerDisplayName, liveSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import { CaptureSourceChannel, RecordingSpeaker, TranscriptSegment } from "@/types";

import CollapsedLineNotes from "./notes/CollapsedLineNotes";
import TimelineNoteLine from "./notes/TimelineNoteLine";
import LiveLineMenu from "./transcript/LiveLineMenu";
import type { LiveLineHandlers } from "./transcript/LiveLineEditor";
import FinalWordingReview from "./transcript/FinalWordingReview";
import { lineIsSettling, LINE_SETTLING_REASON } from "@/lib/transcript/settling";
import LiveSpeakerChip from "./transcript/LiveSpeakerChip";
import TranscriptSelectionNotes from "./transcript/TranscriptSelectionToolbar";
import TranscriptLineText from "./transcript/TranscriptLineText";
import OverlapIndicator, { indexOverlaps, NO_OVERLAPS } from "./transcript/OverlapIndicator";
import { useNoteMoves, type MoveNoteHandler } from "./transcript/_hooks/useNoteMoves";

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

/**
 * The line's live label (`LIVE_PROVISIONAL_3`), else null. These are not the
 * final pass's speakers: a name given to one reaches the processed speaker who
 * holds that speech.
 */
const liveLabelOf = (segment: TranscriptSegment): string | null =>
  liveSpeakerDisplayName(segment.speaker) === null ? null : segment.speaker;

const speakerLabelOf = (segment: TranscriptSegment): string | null =>
  liveLabelOf(segment) ??
  (segment.settling_required && segment.speaker !== "UNKNOWN" ? segment.speaker : null);

const NO_NAMES: Readonly<Record<string, string>> = {};

interface LiveTranscriptPanelProps {
  lineHandlers?: LiveLineHandlers;
  speakers?: readonly RecordingSpeaker[];
  segments: TranscriptSegment[];
  hasLoaded: boolean;
  isPaused?: boolean;
  /** Point and selection notes, each placed after the line it follows. */
  notes?: readonly InlineNote[];
  onRetryNote?: (key: string) => void;
  onDiscardNote?: (key: string) => void;
  /** Moves saved timeline notes while a marked capture is still in progress. */
  onMoveNote?: MoveNoteHandler;
  /** Rendered under the transcript window: the note box. */
  footer?: ReactNode;
  /**
   * Puts the note box's next note on a line, or on selected text; without it
   * lines have no menu and a selection no toolbar.
   */
  onNoteOnLine?: (selection: NoteSelection) => void;
  /** Puts the note box's next note at a line's start: the note from a line still settling. */
  onNoteAtTime?: (atMs: number) => void;
  /** Live label -> the name the user gave it; others read "Speaker <n>". */
  liveSpeakerNames?: Readonly<Record<string, string>>;
  /** Names a live speaker from its label chip; without it the chip is text. */
  onRenameLiveSpeaker?: (label: string, name: string) => Promise<void>;
  /** The People names the rename offers while typing. */
  loadPeopleNames?: () => Promise<readonly string[]>;
}

const NO_NOTES: readonly InlineNote[] = [];
const isSavedNote = (note: InlineNote) => note.status === "saved";

const noteTimeMs = (note: InlineNote) =>
  note.kind === "selection" ? (note.start_ms ?? 0) : (note.anchor_ms ?? 0);

/**
 * Live transcript for the in-flight recording view.
 *
 * Renders provisional utterances as they finalize during capture, and the
 * user's notes among them, each on its own line after the line it follows
 * (`placeTimelineNotes`, the rule every view and prompt shares). Not a second
 * playback surface. Unmarked recordings keep the note-only line menu;
 * marked lines offer corrections, speaker assignment and word splits once
 * locked. Typed notes remain available throughout. When live speaker labels are
 * on, a line may carry a provisional label ("Speaker 3") from the live lane's
 * clustering. It is italic and muted until the label locks, so the cue does not
 * rest on colour alone. Its chip renames the live speaker; the name stands in
 * for "Speaker 3" here and is carried to the processed speaker holding that
 * speech. Which capture channel carried the audio is shown as well.
 */
export default function LiveTranscriptPanel({
  segments,
  hasLoaded,
  isPaused = false,
  notes = NO_NOTES,
  onRetryNote,
  onDiscardNote,
  onMoveNote,
  footer,
  onNoteOnLine,
  onNoteAtTime,
  liveSpeakerNames = NO_NAMES,
  onRenameLiveSpeaker,
  loadPeopleNames,
  lineHandlers,
  speakers,
}: LiveTranscriptPanelProps) {
  const settlingDescriptionId = useId();
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);
  const overlaps = useMemo(() => indexOverlaps(segments), [segments]);
  const speakerMap = useMemo(() => ({
    ...Object.fromEntries((speakers ?? []).map((speaker) => [
      speaker.diarization_label, getRecordingSpeakerDisplayName(speaker, new Map()),
    ])), ...liveSpeakerNames,
  }), [speakers, liveSpeakerNames]);

  // Every labelled line names its speaker, as the finished transcript does.
  // The source label shows only where it changes, so a run of lines from one
  // channel reads as one block and a switch becomes a visible event.
  const lines = useMemo(
    () =>
      segments.map((segment, index) => {
        const sourceChannel = sourceChannelOf(segment);
        const previous = index > 0 ? segments[index - 1] : undefined;
        const previousChannel = previous ? sourceChannelOf(previous) : undefined;

        return {
          segment,
          key: segment.id ?? `${segment.start}-${index}`,
          sourceChannel,
          showSourceLabel:
            sourceChannel !== null && sourceChannel !== previousChannel,
          speakerLabel: speakerLabelOf(segment),
          speakerLocked: segment.settling_required ? segment.locked === true : segment.speaker_state === "stable",
        };
      }),
    [segments],
  );
  const settlingRequired = segments.some((segment) => segment.settling_required);

  // Placed on the lines as listed, which is also the order they render in.
  const visibleIndexes = useMemo(() => segments.map((_, index) => index), [segments]);
  const placements = useMemo(() => placeTimelineNotes(segments, notes), [segments, notes]);
  const notesAmongLines = useMemo(() => bodyNotesAmongLines(placements, visibleIndexes), [placements, visibleIndexes]);
  const moves = useNoteMoves({ segments, visibleIndexes, placements, regionRef: scrollContainerRef,
    onMoveNote: settlingRequired ? onMoveNote : undefined });

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
    <li key={note.key} data-testid="live-transcript-note" data-note-gap={moves.gapOf(note.id)}>
      <TimelineNoteLine
        noteId={note.id}
        body={note.body}
        quote={note.kind === "selection" ? note.quoted_text : null}
        timeLabel={formatTimelineMs(noteTimeMs(note))}
        status={note.status}
        error={note.error}
        onRetry={onRetryNote ? () => onRetryNote(note.key) : undefined}
        onDiscard={onDiscardNote ? () => onDiscardNote(note.key) : undefined}
        attachment={note.attachment}
        move={moves.controlsFor(note, isSavedNote(note) && note.revision !== undefined)}
      />
    </li>
  );

  return (
    // Clips in the two-column layout, where it has a definite height and the
    // note box sits at its bottom: nothing in it may paint over the Notes card
    // below. Not when stacked, where the page scrolls: a clipping card is a
    // scroll container, and the note box would stick to the card, which never
    // scrolls, rather than to the page above an on-screen keyboard.
    <SurfaceBase data-ui-fill="card" data-ui-border="full" as="section" kind="card" className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card @min-[54rem]:overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex items-center gap-2 rounded-pill border border-action-border bg-action-tint px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-action-text">
          <Radio className="h-3.5 w-3.5" />
          Live Transcript
        </div>
        {!isEmpty ? (
          <span className="text-xs text-contrast-helper">
            {settlingRequired
              ? "Lines settle as the conversation continues. Your corrections are kept."
              : "Provisional. Corrected when the recording is processed."}
          </span>
        ) : null}
      </div>
      {segments.some(lineIsSettling) ? (
        <p id={settlingDescriptionId} hidden>{LINE_SETTLING_REASON}</p>
      ) : null}

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
          <SurfaceBase
            data-ui-fill="card"
            data-ui-border="full" kind="pane"
            aria-hidden
            className="density-surface-panel pointer-events-none absolute inset-y-0 left-0 right-[var(--live-transcript-gutter)] border border-action-border bg-surface-card"
          />
          <div
            ref={scrollContainerRef}
            onScroll={handleScroll}
            data-testid="live-transcript-scroll"
            // Focus lands here when a note file's Remove takes away the button pressed.
            tabIndex={-1}
            // The floor holds only in the stacked layout, where the page scrolls
            // and a short transcript would simply leave a gap. From 54rem the
            // column has a definite height and this module absorbs whatever the
            // notes and documents cards leave, which on a short window is less
            // than 14rem. A floor there would overflow the card and paint across
            // the notes card, so `min-h-0` lets the window take the card exactly.
            className="relative min-h-[14rem] flex-1 overflow-y-auto outline-none @min-[54rem]:min-h-0"
          >
            {moves.drag?.bar ? (
              <div aria-hidden="true" data-testid="note-drop-bar"
                className="pointer-events-none absolute h-0.5 rounded-pill bg-action"
                style={{ top: moves.drag.bar.top, left: moves.drag.bar.left, width: moves.drag.bar.width }} />
            ) : null}
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
                  {lines.map(({ segment, key, sourceChannel, showSourceLabel, speakerLabel, speakerLocked }, index) => [
                    <li
                      key={key}
                      data-testid="live-transcript-line"
                      data-line-index={index}
                      data-order-index={index}
                      data-line-actions
                      aria-describedby={lineIsSettling(segment) ? settlingDescriptionId : undefined}
                      className="group/line"
                    >
                      {speakerLabel ? (
                        <LiveSpeakerChip
                          label={speakerLabel}
                          name={speakerMap[speakerLabel] ?? liveSpeakerDisplayName(speakerLabel) ?? speakerLabel}
                          locked={speakerLocked}
                          onRename={liveLabelOf(segment) ? onRenameLiveSpeaker : undefined}
                          loadPeopleNames={loadPeopleNames}
                        />
                      ) : null}
                      <OverlapIndicator segment={segment} overlaps={overlaps.get(segment) ?? NO_OVERLAPS} speakerMap={speakerMap} />
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
                          settlingRequired={segment.settling_required}
                          settling={lineIsSettling(segment)}
                          words={segment.words}
                          className={`min-w-0 flex-1 text-sm leading-6 ${segment.settling_required
                            ? "text-contrast-muted" : "italic text-contrast-helper"}`}
                        />
                        {onNoteOnLine ? (
                          <LiveLineMenu
                            segment={segment}
                            onNoteOnLine={onNoteOnLine}
                            onNoteAtTime={onNoteAtTime}
                            handlers={lineHandlers}
                            speakers={speakers}
                          />
                        ) : null}
                      </div>
                      <FinalWordingReview segment={segment} />
                    </li>,
                    <CollapsedLineNotes
                      key={`${key}-notes`}
                      as="li"
                      notes={notesAmongLines.afterLine.get(index) ?? NO_NOTES}
                      isSaved={(note) => isSavedNote(note) && !moves.wasMoved(note.id)}
                      renderNote={renderNote}
                      lineTime={() => formatElapsed(segment.start)}
                    />,
                  ])}
                </ol>
              )}
            </div>
          </div>

          {!isEmpty && !isPinnedToBottom ? (
            <ButtonBase
              data-ui-fill="card"
              data-ui-hover="action-tint"
              data-ui-border="full"
              chrome="action"
              type="button"
              onClick={jumpToLatest}
              className="absolute bottom-3 left-[calc(50%-var(--live-transcript-gutter)/2)] inline-flex -translate-x-1/2 items-center gap-1.5 rounded-pill border border-action-border bg-surface-card px-3 py-1.5 text-xs font-semibold text-action-text shadow-float transition-colors hover:bg-action-tint"
            >
              <ArrowDown className="h-3.5 w-3.5" />
              Jump to latest
            </ButtonBase>
          ) : null}
        </div>

        {footer}
      </div>
      <p role="status" className="sr-only">{moves.announcement}</p>
      <TranscriptSelectionNotes root={scrollContainerRef} onNoteOnText={onNoteOnLine} segments={segments} />
    </SurfaceBase>
  );
}
