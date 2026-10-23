"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { ArrowLeft, Loader2, Mic, Pause, Trash2, Upload } from "lucide-react";

import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import type { AudioClock } from "@/lib/notes/audioClock";
import { inlineNotes, type NoteSelection } from "@/lib/notes/noteOutbox";
import { segmentStartMs } from "@/lib/notes/placement";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import { ClientStatus, Recording, RecordingStatus, type TimelineNote } from "@/types";

import Workspace from "./Workspace";
import DocumentUploadModal from "./DocumentUploadModal";
import LiveAudioWaveform from "./LiveAudioWaveform";
import LiveDocumentsPanel from "./LiveDocumentsPanel";
import LiveMeetingControls from "./LiveMeetingControls";
import LiveTranscriptPanel from "./LiveTranscriptPanel";
import MeetingEdgePanel from "./MeetingEdgePanel";
import HeadNotePanel from "./HeadNotePanel";
import LiveNoteComposer from "./notes/LiveNoteComposer";
import RecordingUnsavedNotes from "./notes/RecordingUnsavedNotes";
import { useRecordingActions } from "./recordings/_hooks/useRecordingActions";
import { useLiveDocuments } from "./useLiveDocuments";
import { useLiveTranscript } from "./transcript/_hooks/useLiveTranscript";

const DISCARD_CONFIRM_MESSAGE =
  "Discard this recording? This permanently deletes the in-progress meeting and its audio, and cannot be undone.";

function formatClock(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, "0")}:${remainingSeconds
      .toString()
      .padStart(2, "0")}`;
  }

  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds
    .toString()
    .padStart(2, "0")}`;
}

function formatEta(seconds: number) {
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return `${hours}h ${minutes}m remaining`;
  }

  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}m ${remainingSeconds}s remaining`;
  }

  return `${seconds}s remaining`;
}

interface RecordingStatusDisplayProps {
  recording: Recording;
  onSaveMeetingEdgeFocus: (focus: string) => Promise<void>;
  meetingEdgeContextLevel?: number;
  onSaveMeetingEdgeContextLevel?: (level: number) => Promise<void>;
  showMeetingEdge?: boolean;
  onBack?: () => void;
  onDiscarded?: () => void;
  showMobileBackButton?: boolean;
  /** Where capture is on the audio timeline, for anchoring live notes. */
  audioClock?: AudioClock;
  /**
   * The recording's notes as the page last listed them. Null while they load,
   * which holds the Notes box back: it must not start a head note before it
   * knows whether there is one.
   */
  timelineNotes?: readonly TimelineNote[] | null;
}

const NO_TIMELINE_NOTES: readonly TimelineNote[] = [];
const BODY_NOTES = ["point", "selection"] as const;

export default function RecordingStatusDisplay({
  recording,
  onSaveMeetingEdgeFocus,
  meetingEdgeContextLevel,
  onSaveMeetingEdgeContextLevel,
  showMeetingEdge = true,
  onBack,
  onDiscarded,
  showMobileBackButton = false,
  audioClock,
  timelineNotes = NO_TIMELINE_NOTES,
}: RecordingStatusDisplayProps) {
  const actions = useRecordingActions();
  const [isDiscarding, setIsDiscarding] = useState(false);
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const liveTranscript = useLiveTranscript(recording);
  const { documents, refresh: refreshDocuments } = useLiveDocuments(recording.id);
  // The recording's outbox, which the page keeps (`useRecordingNotes`): a
  // note still on its way, and the box's text, outlive Stop.
  const { outbox, snapshot: outboxState } = useNoteOutbox(recording.id);
  const { entries: outboxEntries, saved: outboxSaved, discarded: outboxDiscarded } =
    outboxState;
  const liveNotes = useMemo(
    () =>
      inlineNotes(timelineNotes, {
        entries: outboxEntries,
        saved: outboxSaved,
        discarded: outboxDiscarded,
      }),
    [timelineNotes, outboxEntries, outboxSaved, outboxDiscarded],
  );
  const pendingMeetingNotes = useMemo(
    () => outboxEntries.filter((entry) => entry.kind === "head" || entry.kind === "tail"),
    [outboxEntries],
  );
  const liveSegments = liveTranscript.segments;
  // Before the first read of the clock, "now" is past every line heard so
  // far, which is where the clock would place the note anyway.
  const readAudioNow = useCallback(
    () =>
      audioClock?.audioNow(recording.id) ??
      liveSegments.reduce((latest, segment) => Math.max(latest, segmentStartMs(segment)), 0),
    [audioClock, recording.id, liveSegments],
  );
  // Once capture is over, "now" is its end: where the clock stopped, or the
  // recording's length when this page never saw it running.
  const durationSeconds = recording.duration_seconds;
  const readEndMs = useCallback(
    () => audioClock?.audioNow(recording.id) ?? Math.round((durationSeconds ?? 0) * 1000),
    [audioClock, recording.id, durationSeconds],
  );
  // Where focus goes when a note's Discard removes the button pressed.
  const composerBoxRef = useRef<HTMLTextAreaElement>(null);
  const progressHeadingRef = useRef<HTMLHeadingElement>(null);
  const focusNoteHome = useCallback(() => {
    (composerBoxRef.current ?? progressHeadingRef.current)?.focus();
  }, []);
  const discardInlineNote = useCallback(
    (key: string) => {
      outbox.discard(key);
      composerBoxRef.current?.focus();
    },
    [outbox],
  );
  // "Note on this line": the box's next note goes on the line, and the box
  // takes focus to write it.
  const noteOnLine = useCallback(
    (selection: NoteSelection) => {
      outbox.setDraft({ ...outbox.getSnapshot().draft, selection });
      composerBoxRef.current?.focus();
    },
    [outbox],
  );

  // Discard path for a recording shown on the processing/queued screen. Routed
  // through the shared action so that, if this browser still owns the capture
  // (e.g. an upload finalising), the controller tears down the recorder,
  // uploader, and paused context too; otherwise it falls back to a plain
  // server-side discard that revokes the worker task and removes the meeting.
  const handleProcessingDiscard = async () => {
    if (isDiscarding || !window.confirm(DISCARD_CONFIRM_MESSAGE)) {
      return;
    }
    setIsDiscarding(true);
    await actions.discard(recording.id, {
      onSuccess: onDiscarded,
      onError: () => setIsDiscarding(false),
    });
  };

  const isActiveRecording = isLiveCaptureInProgress(recording);
  const isPaused =
    recording.status === RecordingStatus.PAUSED ||
    recording.client_status === ClientStatus.PAUSED;
  const isFinalisingUpload =
    recording.status === RecordingStatus.UPLOADING &&
    recording.client_status === ClientStatus.UPLOADING;
  const notesAreLocked =
    recording.transcript?.notes_status === "generating" ||
    /generating meeting notes/i.test(recording.processing_step || "");

  // One short state word, not a headline: vertical space is what this view is
  // shortest of.
  const stateLabel = isActiveRecording
    ? isPaused
      ? "Paused"
      : "Recording"
    : recording.status === RecordingStatus.QUEUED
      ? "Queued"
      : isFinalisingUpload
        ? "Uploading"
        : "Processing";

  const pipelineStep = isActiveRecording
    ? null
    : recording.processing_step ||
      (recording.status === RecordingStatus.QUEUED
        ? "Waiting for a worker to begin processing."
        : "Preparing your meeting transcript.");

  // Attaching a document is a toolbar action, not a panel one: it is most
  // useful while the meeting is still running, before processing finishes.
  const uploadButton = (
    <button
      type="button"
      onClick={() => setIsUploadOpen(true)}
      className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-control-border bg-surface-card px-4 text-sm font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text"
      title="Attach agendas, decks or notes to this meeting"
    >
      <Upload className="h-4 w-4" />
      Attach Docs
    </button>
  );

  const progressValue = isActiveRecording
    ? null
    : recording.status === RecordingStatus.QUEUED
      ? 16
      : recording.status === RecordingStatus.UPLOADING
        ? Math.max(10, recording.upload_progress || 0)
        : Math.max(20, recording.processing_progress || 20);

  return (
    // The dense shell, not the feature one. This is a console rather than a
    // page of prose: it has a waveform, a transcript, a guidance panel and two
    // editors, and capping it at a reading measure would force all five into
    // one long scroll.
    //
    // `h-full` rather than the dashboard's `min-h-full`, and that one word is
    // what makes the two-column layout below a console at all. `min-h-full`
    // hands down a floor, so the grid row is still sized by whichever column
    // has the most content and the taller one drags the shorter one with it.
    // A definite height means the row is the window's leftover and neither
    // column can push it, which is the only arrangement in which a panel can
    // decide to scroll: a scroll container needs a height to scroll against.
    //
    // The `min-h-0` chain below it is the other half. Without it every flex
    // item keeps `min-height: auto` and refuses to shrink to that height, so
    // the definite height is inherited and then ignored. It stops at the grid
    // section, which takes it only from 54rem: below that this is one stacked
    // column that should overflow and let the page scroll, so its content is
    // allowed to push past the bottom rather than being squeezed into it.
    <Workspace
      wrapperClassName="flex h-full min-h-0 flex-col overflow-visible"
      backgroundClassName="bg-transparent flex grow min-h-0 flex-col"
      contentClassName="workspace-shell workspace-shell-dense grow min-h-0"
    >
      {showMobileBackButton && onBack ? (
        <div className="pointer-events-none fixed left-4 top-[calc(env(safe-area-inset-top)+0.75rem)] z-[var(--z-sticky)] lg:hidden">
          <button
            type="button"
            onClick={onBack}
            className="pointer-events-auto inline-flex h-12 items-center gap-2 rounded-2xl border border-surface-border bg-surface-card px-4 text-sm font-medium text-contrast-muted shadow-float transition-colors hover:bg-surface-card"
            title="Back to Recordings"
            aria-label="Back to Recordings"
          >
            <ArrowLeft className="h-4 w-4" />
            Back
          </button>
        </div>
      ) : null}

      {/* A toolbar, then two columns from 54rem of workspace, one below.
          Measured against the workspace rather than the viewport because this
          view sits beside the recordings rail; the model is the dashboard's and
          the reasoning is written up there.

          Two columns rather than three, because every panel here is dense prose
          and a third column made all three too narrow to read. The meeting's
          record takes the first, the transcript scrolling and the notes under
          it; guidance takes the second and slightly wider one, since it
          subdivides again internally. */}
      <div
        className={`@container flex min-h-0 grow flex-col gap-[var(--workspace-gap)] ${
          showMobileBackButton && onBack
            ? "pt-[calc(env(safe-area-inset-top)+4.75rem)] lg:pt-0"
            : ""
        }`}
      >
        {/* The console is chrome, not a panel: as a strip it costs one row,
            where a card would take a column's width away from the transcript
            and the guidance, both dense text. */}
        <section className="density-surface flex min-w-0 flex-col border border-surface-border bg-surface-card shadow-card">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
            <span className="inline-flex shrink-0 items-center gap-2 rounded-pill border border-action-border bg-action-tint px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-action-text">
              {isActiveRecording ? (
                isPaused ? (
                  <Pause className="h-3.5 w-3.5" />
                ) : (
                  <Mic className="h-3.5 w-3.5" />
                )
              ) : (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              {stateLabel}
            </span>

            {isActiveRecording ? (
              <>
                {/* The meeting's working name. It is the auto-generated one
                    until the recording is processed and renamed, but it is what
                    tells you which meeting this workspace belongs to, and the
                    toolbar had the room. */}
                <span
                  className="min-w-0 max-w-[22rem] truncate text-sm font-semibold text-foreground"
                  title={recording.name}
                >
                  {recording.name}
                </span>
                {/* The waveform is the flexible element: it takes whatever the
                    name and the state pill leave, so the strip stays one row at
                    any width the columns below it are worth having. */}
                <div className="min-w-[12rem] flex-1">
                  <LiveAudioWaveform
                    recordingId={recording.id}
                    enabled
                    paused={isPaused}
                    compact
                  />
                </div>
              </>
            ) : (
              <>
                <span
                  className="font-mono text-xl font-semibold tabular-nums text-foreground"
                  title="Recording length"
                >
                  {formatClock(Math.round(recording.duration_seconds || 0))}
                </span>
                <button
                  type="button"
                  onClick={handleProcessingDiscard}
                  disabled={isDiscarding}
                  className="ml-auto inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-status-danger-border bg-surface-card px-4 text-sm font-semibold text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
                  title="Discard this recording and stop processing"
                >
                  {isDiscarding ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                  Discard Recording
                </button>
              </>
            )}
          </div>

          {isActiveRecording ? (
            <div className="mt-3">
              <LiveMeetingControls
                size="bar"
                onMeetingEnd={() => {
                  window.dispatchEvent(new Event("recording-updated"));
                }}
                onMeetingDiscard={onDiscarded}
                barTrailing={uploadButton}
              />
            </div>
          ) : null}
        </section>

        <section className="flex grow flex-col gap-[var(--workspace-gap)] @min-[54rem]:grid @min-[54rem]:min-h-0 @min-[54rem]:grid-cols-[minmax(0,1fr)_minmax(24rem,1.1fr)] @min-[54rem]:grid-rows-[minmax(0,1fr)] @min-[54rem]:items-stretch">
          {/* The meeting's record: what was said, what you wrote, what you
              attached. */}
          <div className="contents @min-[54rem]:col-start-1 @min-[54rem]:row-start-1 @min-[54rem]:flex @min-[54rem]:min-h-0 @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)]">
          {/* Exactly one module in this column absorbs the leftover height, and
              which one it is depends on the state. While recording it is the
              transcript, because it scrolls and there is always more of it.
              Once recording stops the transcript is replaced by a progress bar
              and two lines of status, which cannot use height at all, so the
              notes editor absorbs instead and the progress card keeps its own
              size.

              A max-height here would break the column model (DESIGN.md): a
              max-height on a module in an items-stretch grid makes one column's
              cards end above the other's. */}
          <div
            className={
              // The floor keeps the live card's header and a full note box in
              // view however much the Notes card below holds: the transcript
              // window gives way first, and past that the column runs on and
              // the page scrolls, rather than the clipping card cutting the
              // note box off.
              isActiveRecording
                ? "order-1 flex min-h-0 flex-1 flex-col @min-[54rem]:min-h-[18rem]"
                : "order-1 flex min-w-0 flex-col"
            }
          >
            {isActiveRecording ? (
              <LiveTranscriptPanel
                segments={liveTranscript.segments}
                hasLoaded={liveTranscript.hasLoaded}
                isPaused={isPaused}
                notes={liveNotes}
                onRetryNote={outbox.retry}
                onDiscardNote={discardInlineNote}
                onNoteOnLine={noteOnLine}
                footer={
                  <LiveNoteComposer
                    readAudioNow={readAudioNow}
                    onSubmit={(note) => outbox.submit(note, { clearDraft: true })}
                    draft={outboxState.draft}
                    onDraftChange={outbox.setDraft}
                    notice={outboxState.notice}
                    boxRef={composerBoxRef}
                  />
                }
              />
            ) : (
              <section className="density-surface flex flex-col border border-surface-border bg-surface-card shadow-card">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <Loader2 className="h-5 w-5 shrink-0 animate-spin text-action-text" />
                  {/* Not "Processing": the console beside this already carries
                      the state word, and repeating it says nothing twice. */}
                  <h2
                    ref={progressHeadingRef}
                    tabIndex={-1}
                    className="text-base font-semibold text-foreground outline-none"
                  >
                    Progress
                  </h2>
                  {progressValue !== null ? (
                    <span className="ml-auto text-lg font-semibold tabular-nums text-foreground">
                      {Math.round(progressValue)}%
                    </span>
                  ) : null}
                </div>

                {progressValue !== null ? (
                  <div className="mt-4 space-y-2">
                    <div className="h-3 overflow-hidden rounded-full bg-action-tint">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${recording.status === RecordingStatus.QUEUED ? "bg-action-tint-fg" : "bg-action"}`}
                        style={{ width: `${progressValue}%` }}
                      />
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-contrast-helper">
                      <span>
                        {recording.status === RecordingStatus.QUEUED
                          ? "Waiting in queue"
                          : "Pipeline progress"}
                      </span>
                      {recording.processing_eta_seconds != null ? (
                        <span className="font-medium text-foreground">
                          {formatEta(recording.processing_eta_seconds)}
                        </span>
                      ) : recording.processing_eta_learning ? (
                        <span className="font-medium text-foreground">
                          Nojoin needs a few more processed recordings on this
                          system before it can estimate time remaining.
                        </span>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {pipelineStep ? (
                  <p className="mt-4 text-sm text-contrast-helper">{pipelineStep}</p>
                ) : null}

                {/* The live view and its note box are gone once capture stops,
                    here or on another device: a note still on its way, and
                    text left in the box, stay here until they are saved or
                    discarded. Meeting-wide notes are with the Notes box. */}
                <RecordingUnsavedNotes
                  recordingId={recording.id}
                  kinds={BODY_NOTES}
                  readEndMs={readEndMs}
                  onEmptied={focusNoteHome}
                  className="mt-4 border-t border-surface-divider pt-3"
                />
              </section>
            )}
          </div>

            {/* The other half of the swap above: the editor (`h-full min-h-0`
                on the card, `flex-1` on the textarea) absorbs the height while a
                meeting processes. A taller box to type into is worth having; a
                taller progress bar is not. */}
            <div
              className={
                isActiveRecording
                  ? "order-2 flex min-w-0 flex-col"
                  : "order-2 flex min-h-0 min-w-0 flex-1 flex-col"
              }
            >
              {timelineNotes !== null ? (
                <HeadNotePanel
                  key={recording.id}
                  recordingId={recording.id}
                  timelineNotes={timelineNotes}
                  pendingNotes={pendingMeetingNotes}
                  onRetryPendingNote={outbox.retry}
                  onDiscardPendingNote={outbox.discard}
                  onPendingNotesEmptied={focusNoteHome}
                  // While recording, the note box takes the meeting's notes
                  // and the transcript above needs the height.
                  compact={isActiveRecording}
                  disabled={notesAreLocked}
                  disabledMessage="Your manual notes are now being folded into the generated meeting notes. Editing will unlock again once generation finishes."
                />
              ) : null}
            </div>

            {/* No empty state: with the upload action on the toolbar there is
                nothing for an empty panel to offer. */}
            {documents.length > 0 ? (
              <div className="order-3 flex min-w-0 flex-col">
                <LiveDocumentsPanel documents={documents} />
              </div>
            ) : null}
          </div>

          {/* Guidance takes the wider column. It is two lists of prose that
              subdivide again internally, so it is the panel that suffers most
              from a narrow one.

              It is also this column's absorbing module: given a definite height
              it scrolls inside its own cell, so the row height comes from the
              window rather than from how much guidance has accumulated, and
              collapsing its sections never resizes the transcript.

              Only from 54rem, where the two columns exist. Below that this is
              one item in a stack and the page scrolls, so a scroll box inside
              a scrolling page would be the wrong shape. */}
          {showMeetingEdge ? (
            <div className="order-4 flex min-w-0 flex-col @min-[54rem]:col-start-2 @min-[54rem]:row-start-1 @min-[54rem]:min-h-0 @min-[54rem]:flex-1">
              <MeetingEdgePanel
                payload={recording.transcript?.meeting_edge_payload}
                focusText={recording.transcript?.meeting_edge_focus}
                status={recording.transcript?.meeting_edge_status}
                onSaveFocus={onSaveMeetingEdgeFocus}
                contextLevel={meetingEdgeContextLevel}
                onSaveContextLevel={onSaveMeetingEdgeContextLevel}
              />
            </div>
          ) : null}
        </section>
      </div>

      <DocumentUploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        recordingId={recording.id}
        onSuccess={refreshDocuments}
      />
    </Workspace>
  );
}
