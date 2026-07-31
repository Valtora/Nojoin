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

  const heading = isActiveRecording
    ? isPaused
      ? "Meeting recording is paused"
      : "Meeting is being recorded"
    : recording.status === RecordingStatus.QUEUED
      ? "Queued for processing"
      : isFinalisingUpload
        ? "Uploading meeting"
        : "Processing recording";

  const subheading = isActiveRecording
    ? isPaused
      ? "Resume or discard this recording to clear the paused capture state."
      : "Live audio waveform and timer are shown while your meeting is being recorded."
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
    <Workspace
      wrapperClassName="flex-1 overflow-visible"
      backgroundClassName="bg-transparent"
      contentClassName="workspace-shell workspace-shell-feature"
    >
      {showMobileBackButton && onBack ? (
        <div className="pointer-events-none fixed left-4 top-[calc(env(safe-area-inset-top)+0.75rem)] z-40 lg:hidden">
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

      {/* Workspace's workspace-shell supplies the flex gap between its
          children, but everything here is nested one level inside this wrapper,
          so that gap applied to the wrapper alone and the capture card sat flush
          against the panel column below it. Restate it here so the card and the
          panels are separated by the same workspace gap the panels use between
          themselves. */}
      <div
        className={`flex flex-col gap-[var(--workspace-gap)] ${
          showMobileBackButton && onBack
            ? "pt-[calc(env(safe-area-inset-top)+4.75rem)] lg:pt-0"
            : ""
        }`}
      >
      <section className="density-surface mx-auto flex min-w-0 w-full max-w-5xl flex-col border border-surface-border bg-surface-card shadow-card">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="space-y-3">
                <span className="inline-flex items-center gap-2 rounded-full border border-action-border bg-action-tint px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-action-text">
                  {isActiveRecording ? (
                    isPaused ? (
                      <Pause className="h-3.5 w-3.5" />
                    ) : (
                      <Mic className="h-3.5 w-3.5" />
                    )
                  ) : (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  )}
                  {isActiveRecording ? "Live Capture" : "Meeting Processing"}
                </span>
                <div>
                  <h2 className="density-heading-page text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
                    {heading}
                  </h2>
                  <p className="density-body-copy mt-3 max-w-2xl text-sm leading-6 text-contrast-helper md:text-base">
                    {subheading}
                  </p>
                </div>
              </div>

              {!isActiveRecording && progressValue !== null ? (
                <div className="density-surface-panel flex min-h-[4.75rem] min-w-[7.5rem] flex-col items-center justify-center border border-action-border bg-action-tint px-4 py-3 text-center">
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-action-text">
                    Progress
                  </div>
                  <div className="mt-1 text-3xl font-semibold leading-none text-foreground">
                    {Math.round(progressValue)}%
                  </div>
                </div>
              ) : null}
            </div>

            <div className="mt-6 space-y-4">
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
                <>
                  {progressValue !== null ? (
                    <div className="space-y-2">
                      <div className="h-3 overflow-hidden rounded-full bg-action-tint">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${recording.status === RecordingStatus.QUEUED ? "bg-action-tint-fg" : "bg-action"}`}
                          style={{ width: `${progressValue}%` }}
                        />
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-contrast-helper">
                        <span>{recording.status === RecordingStatus.QUEUED ? "Waiting in queue" : "Pipeline progress"}</span>
                        {recording.processing_eta_seconds != null ? (
                          <span className="font-medium text-foreground">
                            {formatEta(recording.processing_eta_seconds)}
                          </span>
                        ) : recording.processing_eta_learning ? (
                          <span className="font-medium text-foreground">
                            Nojoin needs a few more processed recordings on this system before it can estimate time remaining.
                          </span>
                        ) : null}
                      </div>
                    </div>
                  ) : null}

                  <div className="density-surface-panel border border-surface-border bg-surface-card p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-contrast-helper">
                      Recording Length
                    </div>
                    <div className="mt-2 text-2xl font-semibold text-foreground">
                      {formatClock(Math.round(recording.duration_seconds || 0))}
                    </div>
                  </div>

                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={handleProcessingDiscard}
                      disabled={isDiscarding}
                      className="inline-flex items-center justify-center gap-2 rounded-2xl border border-status-danger-border bg-surface-card px-4 py-2.5 text-sm font-semibold text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
                      title="Discard this recording and stop processing"
                    >
                      {isDiscarding ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                      Discard Recording
                    </button>
                  </div>
                </>
              )}

            </div>
      </section>

      <div className="mx-auto w-full max-w-5xl space-y-[var(--workspace-gap)]">
        {isActiveRecording ? (
          <LiveTranscriptPanel
            segments={liveTranscript.segments}
            hasLoaded={liveTranscript.hasLoaded}
            isPaused={isPaused}
          />
        ) : null}
        {showMeetingEdge ? (
          <MeetingEdgePanel
            payload={recording.transcript?.meeting_edge_payload}
            focusText={recording.transcript?.meeting_edge_focus}
            status={recording.transcript?.meeting_edge_status}
            onSaveFocus={onSaveMeetingEdgeFocus}
            contextLevel={meetingEdgeContextLevel}
            onSaveContextLevel={onSaveMeetingEdgeContextLevel}
          />
        ) : null}
        <ProcessingNotesPanel
          value={recording.transcript?.user_notes}
          onSave={onSaveProcessingNotes}
          disabled={notesAreLocked}
          disabledMessage="Your manual notes are now being folded into the generated meeting notes. Editing will unlock again once generation finishes."
        />
        <LiveDocumentsPanel recordingId={recording.id} />
      </div>
      </div>
    </Workspace>
  );
}
