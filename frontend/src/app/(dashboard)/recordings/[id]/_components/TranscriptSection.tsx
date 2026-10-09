"use client";

import { AlertCircle } from "lucide-react";

import TranscriptView from "@/components/TranscriptView";
import {
  RecordingStatus,
  type GlobalSpeaker,
  type Recording,
  type TranscriptSegment,
  type TranscriptSpeakerAssignment,
} from "@/types";

interface TranscriptSectionProps {
  active: boolean;
  recording: Recording;
  transcriptSegments: TranscriptSegment[];
  currentTime: number;
  isPlaying: boolean;
  speakerMap: Record<string, string>;
  speakerColors: Record<string, string>;
  globalSpeakers: GlobalSpeaker[];
  canUndo: boolean;
  canRedo: boolean;
  deferredTranscriptUtteranceIds: string[];
  onPlaySegment: (start: number, end?: number) => void | Promise<void>;
  onPause: () => void;
  onResume: () => void;
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
  onUpdateSegmentText: (
    segment: TranscriptSegment,
    text: string,
  ) => void | Promise<void>;
  onFindAndReplace: (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => void | Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  onExport: () => void;
  onActiveEditUtteranceChange: (id: string | null) => void;
}

export default function TranscriptSection({
  active,
  recording,
  transcriptSegments,
  currentTime,
  isPlaying,
  speakerMap,
  speakerColors,
  globalSpeakers,
  canUndo,
  canRedo,
  deferredTranscriptUtteranceIds,
  onPlaySegment,
  onPause,
  onResume,
  onRenameSpeaker,
  onUpdateSegmentSpeaker,
  onUpdateSegmentText,
  onFindAndReplace,
  onUndo,
  onRedo,
  onExport,
  onActiveEditUtteranceChange,
}: TranscriptSectionProps) {
  const transcriptStatus = recording.transcript?.transcript_status;
  // A failed transcription is reported, never shown as an empty transcript:
  // that is exactly how a crashed ASR run used to pass for a silent meeting.
  // Only while the recording is in ERROR: a retry or speaker-inference run
  // moves it back to processing, and the old failure no longer applies then.
  const transcriptionError =
    transcriptStatus === "error" && recording.status === RecordingStatus.ERROR
      ? recording.transcript?.error_message || "Transcription failed."
      : null;

  return (
    <div
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      {transcriptionError ? (
        <div
          role="alert"
          className="m-4 flex shrink-0 items-start gap-3 rounded-lg border border-status-danger-border bg-status-danger-bg p-4 text-sm text-status-danger-fg"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div className="space-y-1">
            <p className="font-semibold">Transcription failed</p>
            <p>{transcriptionError}</p>
            <p>
              Use <strong>Retry Processing</strong> from this recording&apos;s
              menu in the recordings list to try again.
            </p>
          </div>
        </div>
      ) : null}
      {transcriptSegments.length > 0 ? (
        <div className="min-h-0 flex-1">
          <TranscriptView
            recordingId={recording.id}
            segments={transcriptSegments}
            currentTime={currentTime}
            onPlaySegment={onPlaySegment}
            isPlaying={isPlaying}
            onPause={onPause}
            onResume={onResume}
            speakerMap={speakerMap}
            speakers={recording.speakers || []}
            globalSpeakers={globalSpeakers}
            onRenameSpeaker={onRenameSpeaker}
            onUpdateSegmentSpeaker={onUpdateSegmentSpeaker}
            onUpdateSegmentText={onUpdateSegmentText}
            onFindAndReplace={onFindAndReplace}
            speakerColors={speakerColors}
            onUndo={onUndo}
            onRedo={onRedo}
            canUndo={canUndo}
            canRedo={canRedo}
            onExport={onExport}
            onActiveEditUtteranceChange={onActiveEditUtteranceChange}
            pendingRemoteUtteranceIds={deferredTranscriptUtteranceIds}
          />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center p-6 text-center space-y-4">
          {recording.transcript?.text ? (
            <>
              <div className="p-4 rounded-lg bg-surface-inset border border-surface-border max-w-md">
                <p className="text-lg font-medium text-contrast-muted">
                  {recording.transcript.text.replace(/[\[\]]/g, "")}
                </p>
              </div>
              {transcriptionError ? null : (
                <p className="text-sm text-contrast-helper">
                  The audio file was processed, but no speech segments were
                  generated.
                </p>
              )}
            </>
          ) : transcriptionError ? null : transcriptStatus === "completed" ? (
            // Transcription finished and heard nothing: a result, not a wait.
            <p className="text-contrast-helper italic">
              No speech was detected in this recording.
            </p>
          ) : (
            <p className="text-contrast-helper italic">
              No transcript available yet.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
