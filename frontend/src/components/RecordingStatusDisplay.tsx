"use client";

import { useState } from "react";
import { ArrowLeft, Loader2, Mic, Pause, Trash2 } from "lucide-react";

import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

import Workspace from "./Workspace";
import LiveAudioWaveform from "./LiveAudioWaveform";
import LiveDocumentsPanel from "./LiveDocumentsPanel";
import LiveMeetingControls from "./LiveMeetingControls";
import LiveTranscriptPanel from "./LiveTranscriptPanel";
import MeetingEdgePanel from "./MeetingEdgePanel";
import ProcessingNotesPanel from "./ProcessingNotesPanel";
import { useRecordingActions } from "./recordings/_hooks/useRecordingActions";
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
  const liveTranscript = useLiveTranscript(recording);

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

      {/* Three columns from 74rem of workspace, two from 54rem, one below,
          measured against the workspace rather than the viewport because this
          view sits beside the recordings rail. Same model as the dashboard, and
          the reasoning is written up there.

          Columns group by job. The console owns the first, with documents under
          it, since attaching a deck is something you do once. The middle column
          is the meeting's output: the live transcript while recording, the
          pipeline's progress once it is not. Guidance and your own notes share
          the third.

          At one column the wrappers are `display: contents`, so the five panels
          become direct children of one flex column and `order-*` sequences
          them: controls first, then transcript, guidance, notes, documents. */}
      <div
        className={`@container flex grow flex-col ${
          showMobileBackButton && onBack
            ? "pt-[calc(env(safe-area-inset-top)+4.75rem)] lg:pt-0"
            : ""
        }`}
      >
        <section className="flex grow flex-col gap-[var(--workspace-gap)] @min-[54rem]:grid @min-[54rem]:grid-cols-[minmax(0,1fr)_minmax(20rem,1.25fr)] @min-[54rem]:items-stretch @min-[74rem]:grid-cols-[minmax(0,0.95fr)_minmax(20rem,1.3fr)_minmax(18rem,1.05fr)]">
          {/* The console, and the things you touch rather than read. */}
          <div className="contents @min-[54rem]:col-start-1 @min-[54rem]:row-start-1 @min-[54rem]:row-span-2 @min-[54rem]:flex @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)] @min-[74rem]:row-span-1">
            <section className="density-surface order-1 flex min-w-0 flex-col border border-surface-border bg-surface-card shadow-card">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <span className="inline-flex items-center gap-2 rounded-full border border-action-border bg-action-tint px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-action-text">
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
                {!isActiveRecording ? (
                  <span
                    className="ml-auto font-mono text-lg font-semibold tabular-nums text-foreground"
                    title="Recording length"
                  >
                    {formatClock(Math.round(recording.duration_seconds || 0))}
                  </span>
                ) : null}
              </div>

              <div className="mt-4 space-y-4">
                {isActiveRecording ? (
                  <>
                    <LiveAudioWaveform
                      recordingId={recording.id}
                      enabled
                      paused={isPaused}
                    />
                    <LiveMeetingControls
                      size="full"
                      onMeetingEnd={() => {
                        window.dispatchEvent(new Event("recording-updated"));
                      }}
                      onMeetingDiscard={onDiscarded}
                    />
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={handleProcessingDiscard}
                    disabled={isDiscarding}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-status-danger-border bg-surface-card px-4 py-2.5 text-sm font-semibold text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
                    title="Discard this recording and stop processing"
                  >
                    {isDiscarding ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4" />
                    )}
                    Discard Recording
                  </button>
                )}
              </div>
            </section>

            <div className="order-5 flex min-h-0 flex-1 flex-col">
              <LiveDocumentsPanel recordingId={recording.id} />
            </div>
          </div>

          {/* The meeting's output: what was said, or how far along it is. */}
          <div className="order-2 flex min-h-0 flex-1 flex-col @min-[54rem]:col-start-2 @min-[54rem]:row-start-1">
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

          {/* Guidance, and what you write yourself. */}
          <div className="contents @min-[54rem]:col-start-2 @min-[54rem]:row-start-2 @min-[54rem]:flex @min-[54rem]:min-w-0 @min-[54rem]:flex-col @min-[54rem]:gap-[var(--workspace-gap)] @min-[74rem]:col-start-3 @min-[74rem]:row-start-1">
            {showMeetingEdge ? (
              <div className="order-3 flex min-w-0 flex-col">
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

            <div className="order-4 flex min-h-0 flex-1 flex-col">
              <ProcessingNotesPanel
                value={recording.transcript?.user_notes}
                onSave={onSaveProcessingNotes}
                disabled={notesAreLocked}
                disabledMessage="Your manual notes are now being folded into the generated meeting notes. Editing will unlock again once generation finishes."
              />
            </div>
          </div>
        </section>
      </div>
    </Workspace>
  );
}
