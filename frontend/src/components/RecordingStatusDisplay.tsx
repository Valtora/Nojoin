"use client";

import { useState } from "react";
import { ArrowLeft, Loader2, Mic, Pause, Trash2, Upload } from "lucide-react";

import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

import Workspace from "./Workspace";
import DocumentUploadModal from "./DocumentUploadModal";
import LiveAudioWaveform from "./LiveAudioWaveform";
import LiveDocumentsPanel from "./LiveDocumentsPanel";
import LiveMeetingControls from "./LiveMeetingControls";
import LiveTranscriptPanel from "./LiveTranscriptPanel";
import MeetingEdgePanel from "./MeetingEdgePanel";
import ProcessingNotesPanel from "./ProcessingNotesPanel";
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
  onSaveProcessingNotes: (notes: string) => Promise<void>;
  onSaveMeetingEdgeFocus: (focus: string) => Promise<void>;
  meetingEdgeContextLevel?: number;
  onSaveMeetingEdgeContextLevel?: (level: number) => Promise<void>;
  showMeetingEdge?: boolean;
  onBack?: () => void;
  onDiscarded?: () => void;
  showMobileBackButton?: boolean;
}

export default function RecordingStatusDisplay({
  recording,
  onSaveProcessingNotes,
  onSaveMeetingEdgeFocus,
  meetingEdgeContextLevel,
  onSaveMeetingEdgeContextLevel,
  showMeetingEdge = true,
  onBack,
  onDiscarded,
  showMobileBackButton = false,
}: RecordingStatusDisplayProps) {
  const actions = useRecordingActions();
  const [isDiscarding, setIsDiscarding] = useState(false);
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const liveTranscript = useLiveTranscript(recording);
  const { documents, refresh: refreshDocuments } = useLiveDocuments(recording.id);

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

  // One short state word, not a headline. The heading this replaced was
  // `text-4xl` over a sentence explaining that a waveform appears while
  // recording, on a view whose entire problem is vertical space.
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

  // Attaching a document is a toolbar action, not a panel one. At the bottom of
  // the last column it was the least findable control on the page, and the
  // window for using it usefully closes when processing finishes.
  const uploadButton = (
    <button
      type="button"
      onClick={() => setIsUploadOpen(true)}
      className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-control-border bg-surface-card px-4 text-sm font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text"
      title="Attach an agenda or a deck to this meeting"
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
    // editors, and capping it at a reading measure is what forced all five into
    // one long scroll. The `grow` chain hands the window's leftover height down
    // to the grid, exactly as the dashboard does.
    <Workspace
      wrapperClassName="flex min-h-full flex-col overflow-visible"
      backgroundClassName="bg-transparent flex grow flex-col"
      contentClassName="workspace-shell workspace-shell-dense grow"
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
        className={`@container flex grow flex-col gap-[var(--workspace-gap)] ${
          showMobileBackButton && onBack
            ? "pt-[calc(env(safe-area-inset-top)+4.75rem)] lg:pt-0"
            : ""
        }`}
      >
        {/* The console is chrome, not a panel. As a card in the first column it
            took a third of the width to show a waveform and four buttons, and
            charged it to the transcript and the guidance beside it, both of
            which are dense text that wraps to three words in a narrow column.
            As a strip it costs one row and gives that width back. */}
        <section className="density-surface flex min-w-0 flex-col border border-surface-border bg-surface-card shadow-card">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
            <span className="inline-flex shrink-0 items-center gap-2 rounded-full border border-action-border bg-action-tint px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-action-text">
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

        {/* Two columns, not three. Every panel here is dense prose, and a third
            column bought findability for the notes at the cost of making all
            three too narrow to read comfortably: Meeting Edge subdivides again
            internally, so it was carrying four columns of text inside a third
            of the page. Notes moves under the transcript, which scrolls. */}
        <section className="flex grow flex-col gap-[var(--workspace-gap)] @min-[54rem]:grid @min-[54rem]:grid-cols-[minmax(0,1fr)_minmax(24rem,1.1fr)] @min-[54rem]:items-stretch">
          {/* The meeting's record: what was said, what you wrote, what you
              attached. The transcript takes the height and scrolls; the notes
              sit under it at their own size. */}
          <div className="contents @min-[54rem]:col-start-1 @min-[54rem]:row-start-1 @min-[54rem]:flex @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)]">
          {/* The transcript card's viewport-relative ceiling. It sits here
              rather than inside the panel so the card stops growing at the same
              point its contents do: capping only the inner window let the card
              follow the grid row down past it whenever the guidance column
              outgrew the viewport, and the difference showed as dead space
              between the transcript and the notes. 18rem covers the toolbar
              above, the workspace padding and the gap, so the notes card below
              stays on screen. */}
          <div className="order-1 flex min-h-0 flex-1 flex-col @min-[54rem]:max-h-[calc(100dvh-18rem)]">
            {isActiveRecording ? (
              <LiveTranscriptPanel
                segments={liveTranscript.segments}
                hasLoaded={liveTranscript.hasLoaded}
                isPaused={isPaused}
              />
            ) : (
              <section className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <Loader2 className="h-5 w-5 shrink-0 animate-spin text-action-text" />
                  {/* Not "Processing": the console beside this already carries
                      the state word, and repeating it says nothing twice. */}
                  <h2 className="text-base font-semibold text-foreground">
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
              </section>
            )}
          </div>

            <div className="order-2 flex min-w-0 flex-col">
              <ProcessingNotesPanel
                value={recording.transcript?.user_notes}
                onSave={onSaveProcessingNotes}
                disabled={notesAreLocked}
                disabledMessage="Your manual notes are now being folded into the generated meeting notes. Editing will unlock again once generation finishes."
              />
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
              from a narrow one. */}
          {showMeetingEdge ? (
            <div className="order-4 flex min-h-0 flex-1 flex-col @min-[54rem]:col-start-2 @min-[54rem]:row-start-1">
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
