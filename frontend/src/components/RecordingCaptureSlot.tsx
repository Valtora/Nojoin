"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Mic, Pause, Trash2, Upload } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import type { AudioClock } from "@/lib/notes/audioClock";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import DocumentUploadModal from "./DocumentUploadModal";
import LiveAudioWaveform from "./LiveAudioWaveform";
import LiveMeetingControls from "./LiveMeetingControls";
import { useRecordingActions } from "./recordings/_hooks/useRecordingActions";
import { ButtonBase } from "./ui/nativeBases";
import { useResumeRecoveryOffer } from "./useResumeRecoveryOffer";

const DISCARD_CONFIRM_MESSAGE =
  "Discard this recording? This permanently deletes the in-progress meeting and its audio, and cannot be undone.";

function formatClock(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remaining = (whole % 60).toString().padStart(2, "0");
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${remaining}`
    : `${minutes.toString().padStart(2, "0")}:${remaining}`;
}

function formatEta(seconds: number) {
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m remaining`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s remaining`;
  return `${seconds}s remaining`;
}

interface RecordingCaptureSlotProps {
  recording: Recording;
  /** The page's clock of the audio the server has received for the capture. */
  audioClock?: AudioClock;
  onDiscarded?: () => void;
  /** Changes whenever new live transcript text arrives; see LiveAudioWaveform. */
  liveTextMark?: string | null;
}

/**
 * Reads the audio clock once a second while `enabled`. The timer's state change
 * both re-renders and carries the reading: a bare call during render is one
 * the React Compiler may cache.
 */
function useAudioClockSeconds(readAudioMs: () => number | null, enabled: boolean): number | null {
  const [observedMs, setObservedMs] = useState(readAudioMs);
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(() => setObservedMs(readAudioMs()), 1000);
    return () => clearInterval(timer);
  }, [enabled, readAudioMs]);
  return observedMs === null ? null : observedMs / 1000;
}

/** Capture/progress presentation in the common header's audio-player slot. */
export default function RecordingCaptureSlot({
  recording, audioClock, onDiscarded, liveTextMark,
}: RecordingCaptureSlotProps) {
  const capture = useCapture();
  const actions = useRecordingActions();
  const resumeOffer = useResumeRecoveryOffer();
  const [isDiscarding, setIsDiscarding] = useState(false);
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  // The live controls' own test for Stop: this tab's runtime holds the capture
  // (a failed stop's included), or this tab was reloaded during it and holds
  // its recovery offer. The stored ID alone is not ownership, since another
  // tab or device may have taken the capture over since.
  const controlsCapture = capture.recordingId === recording.id &&
    (capture.ownedRecordingId === recording.id || capture.pausedRecording?.id === recording.id);
  const locallyFinalising = controlsCapture && capture.status === "finalizing";
  const locallyCapturing = controlsCapture && (capture.status === "recording" || capture.status === "paused");
  const isCapturing = isLiveCaptureInProgress(recording) || locallyCapturing;
  // The tab capturing goes by its own state, which the page's copy of the
  // recording catches up with on its next poll; every other tab by the server.
  const isPaused = locallyCapturing ? capture.status === "paused"
    : recording.status === RecordingStatus.PAUSED || recording.client_status === ClientStatus.PAUSED;
  const showTransport = controlsCapture && (isCapturing || locallyFinalising);
  const showWaveform = controlsCapture && isCapturing && !locallyFinalising;
  const isPipeline = !isCapturing && !locallyFinalising;
  // Any other tab or device reads "Live", since nothing records here, on the
  // server's audio timeline, which stands still while paused:
  // `duration_seconds` stays 0 until the recording is finalized.
  const watchesCapture = isCapturing && !showTransport;
  const readAudioMs = useCallback(() => audioClock?.audioNow(recording.id) ?? null, [audioClock, recording.id]);
  const liveAudioSeconds = useAudioClockSeconds(readAudioMs, watchesCapture);
  const clockSeconds = watchesCapture ? liveAudioSeconds : recording.duration_seconds || 0;
  const stateLabel = isCapturing ? isPaused ? "Paused" : "Live"
    : recording.status === RecordingStatus.QUEUED ? "Queued"
    : recording.status === RecordingStatus.UPLOADING ? "Uploading" : "Processing";
  const progress = recording.status === RecordingStatus.QUEUED ? 16
    : recording.status === RecordingStatus.UPLOADING ? Math.max(10, recording.upload_progress || 0)
    : Math.max(20, recording.processing_progress || 20);
  const pipelineStep = recording.processing_step || (recording.status === RecordingStatus.QUEUED
    ? "Waiting for a worker to begin processing." : "Preparing your meeting transcript.");

  const discard = async () => {
    if (isDiscarding || !window.confirm(DISCARD_CONFIRM_MESSAGE)) return;
    setIsDiscarding(true);
    await actions.discard(recording.id, {
      onSuccess: onDiscarded,
      onError: () => setIsDiscarding(false),
    });
  };

  if (!isCapturing && !locallyFinalising &&
    (recording.status === RecordingStatus.PROCESSED || recording.status === RecordingStatus.ERROR)) return null;

  const attachDocuments = (
    <ButtonBase
      data-ui-fill="card" data-ui-border="full" chrome="action" type="button"
      onClick={() => setIsUploadOpen(true)}
      className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-control-border bg-surface-card px-4 text-sm font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text"
      title="Attach agendas, decks or notes to this meeting"
    >
      <Upload className="h-4 w-4" />
      Attach Docs
    </ButtonBase>
  );

  return (
    <section aria-label="Recording status and controls" className="flex min-w-0 flex-col gap-3">
      {showWaveform ? (
        <LiveAudioWaveform recordingId={recording.id} enabled={capture.runtimeActive} paused={isPaused} compact
          liveTextMark={liveTextMark} />
      ) : null}
      {showTransport ? (
        <LiveMeetingControls
          size="bar"
          onMeetingEnd={() => window.dispatchEvent(new CustomEvent("recording-updated", { detail: { id: recording.id } }))}
          onMeetingDiscard={onDiscarded}
          barTrailing={attachDocuments}
          onResumeOffer={() => void resumeOffer(recording.id)}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex shrink-0 items-center gap-2 rounded-pill border border-action-border bg-action-tint px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-action-text">
            {isCapturing ? isPaused ? <Pause className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />
              : <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {stateLabel}
          </span>
          {clockSeconds === null ? null : (
            <span className="font-mono text-sm font-semibold tabular-nums text-foreground" title="Recording length">
              {formatClock(clockSeconds)}
            </span>
          )}
          {attachDocuments}
          {/* A finished meeting whose notes regenerate or that is being
              reprocessed is in the pipeline too, and goes to the bin (#833). */}
          {isPipeline && recording.discardable ? (
            <ButtonBase
              data-ui-fill="card" data-ui-hover="status-danger" data-ui-border="full" chrome="action"
              type="button" onClick={discard} disabled={isDiscarding}
              className="ml-auto inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-status-danger-border bg-surface-card px-4 text-sm font-semibold text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
              title="Discard this recording and stop processing"
            >
              {isDiscarding ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              Discard Recording
            </ButtonBase>
          ) : null}
        </div>
      )}
      {isPipeline ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-contrast-helper">
            <span>{recording.status === RecordingStatus.QUEUED ? "Waiting in queue" : "Pipeline progress"}</span>
            <span className="font-semibold tabular-nums text-foreground">{Math.round(progress)}%</span>
          </div>
          <div role="progressbar" aria-label="Recording progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} className="h-3 overflow-hidden rounded-full bg-action-tint">
            <div className="h-full rounded-full bg-action transition-all duration-500" style={{ width: `${progress}%` }} />
          </div>
          <p className="text-sm text-contrast-helper">{pipelineStep}</p>
          {recording.processing_eta_seconds != null ? (
            <p className="text-sm font-medium text-foreground">{formatEta(recording.processing_eta_seconds)}</p>
          ) : recording.processing_eta_learning ? (
            <p className="text-sm text-contrast-helper">Nojoin needs a few more processed recordings on this system before it can estimate time remaining.</p>
          ) : null}
        </div>
      ) : null}
      <DocumentUploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        recordingId={recording.id}
        onSuccess={() => window.dispatchEvent(new CustomEvent("recording-documents-updated", { detail: { recordingId: recording.id } }))}
      />
    </section>
  );
}
