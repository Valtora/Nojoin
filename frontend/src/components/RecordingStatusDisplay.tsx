"use client";

import { Loader2, Mic, Pause } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ClientStatus, Recording, RecordingStatus } from "@/types";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";

import AmbientWorkspace from "./AmbientWorkspace";
import LiveAudioWaveform from "./LiveAudioWaveform";
import LiveMeetingControls from "./LiveMeetingControls";
import ProcessingNotesPanel from "./ProcessingNotesPanel";

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

function LiveRecordingTimer() {
  const { companion, companionStatus, recordingDuration } = useServiceStatusStore();
  const [elapsedTime, setElapsedTime] = useState<number>(0);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    setElapsedTime(recordingDuration);
  }, [recordingDuration]);

  useEffect(() => {
    if (companion && companionStatus === "recording") {
      timerRef.current = setInterval(() => {
        setElapsedTime((current) => current + 1);
      }, 1000);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, [companion, companionStatus]);

  return (
    <div className="text-4xl font-semibold tracking-tight text-gray-950 dark:text-white">
      {formatClock(elapsedTime)}
    </div>
  );
}

interface RecordingStatusDisplayProps {
  recording: Recording;
  onSaveProcessingNotes: (notes: string) => Promise<void>;
}

export default function RecordingStatusDisplay({
  recording,
  onSaveProcessingNotes,
}: RecordingStatusDisplayProps) {
  const isActiveRecording =
    recording.status === RecordingStatus.UPLOADING &&
    recording.client_status !== ClientStatus.UPLOADING;
  const isPaused = recording.client_status === ClientStatus.PAUSED;
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
    ? "Live audio waveform and timer are shown while your meeting is being recorded."
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
    <AmbientWorkspace>
      <section className="mx-auto flex min-w-0 w-full max-w-5xl flex-col rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-2xl shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="space-y-3">
                <span className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
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
                  <h2 className="text-3xl font-semibold tracking-tight text-gray-950 dark:text-white md:text-4xl">
                    {heading}
                  </h2>
                  <p className="mt-3 max-w-2xl text-sm leading-6 text-gray-600 dark:text-gray-300 md:text-base">
                    {subheading}
                  </p>
                </div>
              </div>

              {isActiveRecording ? (
                <LiveRecordingTimer />
              ) : progressValue !== null ? (
                <div className="flex min-h-[4.75rem] min-w-[7.5rem] flex-col items-center justify-center rounded-[1.5rem] border border-orange-200/70 bg-orange-50/85 px-4 py-3 text-center dark:border-orange-500/20 dark:bg-orange-500/10">
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-orange-700 dark:text-orange-300">
                    Progress
                  </div>
                  <div className="mt-1 text-3xl font-semibold leading-none text-gray-950 dark:text-white">
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
                  />
                </>
              ) : (
                <>
                  {progressValue !== null ? (
                    <div className="space-y-2">
                      <div className="h-3 overflow-hidden rounded-full bg-orange-100 dark:bg-gray-800">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${recording.status === RecordingStatus.QUEUED ? "bg-orange-400" : "bg-gradient-to-r from-orange-500 via-orange-500 to-amber-400"}`}
                          style={{ width: `${progressValue}%` }}
                        />
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-600 dark:text-gray-300">
                        <span>{recording.status === RecordingStatus.QUEUED ? "Waiting in queue" : "Pipeline progress"}</span>
                        {recording.processing_eta_seconds != null ? (
                          <span className="font-medium text-gray-900 dark:text-white">
                            {formatEta(recording.processing_eta_seconds)}
                          </span>
                        ) : recording.processing_eta_learning ? (
                          <span className="font-medium text-gray-900 dark:text-white">
                            Nojoin needs a few more processed recordings on this system before it can estimate time remaining.
                          </span>
                        ) : null}
                      </div>
                    </div>
                  ) : null}

                  <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
                      Recording Length
                    </div>
                    <div className="mt-2 text-2xl font-semibold text-gray-950 dark:text-white">
                      {formatClock(Math.round(recording.duration_seconds || 0))}
                    </div>
                  </div>
                </>
              )}
            </div>
      </section>

      <div className="mx-auto w-full max-w-5xl">
        <ProcessingNotesPanel
          value={recording.transcript?.user_notes}
          onSave={onSaveProcessingNotes}
          disabled={notesAreLocked}
          disabledMessage="Your manual notes are now being folded into the generated meeting notes. Editing will unlock again once generation finishes."
        />
      </div>
    </AmbientWorkspace>
  );
}