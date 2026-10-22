"use client";

import { useCallback, useMemo, useRef } from "react";
import { AlertCircle } from "lucide-react";

import TranscriptView from "@/components/TranscriptView";
import RecordingUnsavedNotes from "@/components/notes/RecordingUnsavedNotes";
import { shownNotes } from "@/lib/notes/noteOutbox";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import {
  RecordingStatus,
  type GlobalSpeaker,
  type Recording,
  type RecordingSpeaker,
  type TimelineNote,
  type TranscriptSegment,
  type TranscriptSpeakerAssignment,
} from "@/types";

// One empty list for every render, so the transcript's memoised lines hold.
const NO_SPEAKERS: readonly RecordingSpeaker[] = Object.freeze([]);

interface TranscriptSectionProps {
  active: boolean;
  recording: Recording;
  /** The recording's notes as last listed; null while they load. */
  timelineNotes?: readonly TimelineNote[] | null;
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
  timelineNotes,
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
  // The listed notes and those saved from this page since, less any it
  // discarded: the outbox outlives the live view, and its notes land here.
  const { snapshot: outboxState } = useNoteOutbox(recording.id);
  const { saved: outboxSaved, discarded: outboxDiscarded } = outboxState;
  const notes = useMemo(
    () => shownNotes(timelineNotes, { saved: outboxSaved, discarded: outboxDiscarded }),
    [timelineNotes, outboxSaved, outboxDiscarded],
  );
  const sectionRef = useRef<HTMLDivElement>(null);
  const durationSeconds = recording.duration_seconds;
  const readEndMs = useCallback(
    () => Math.round((durationSeconds ?? 0) * 1000),
    [durationSeconds],
  );
  const focusSection = useCallback(() => sectionRef.current?.focus(), []);
  // A failed transcription is reported, never shown as an empty transcript,
  // which would let a crashed ASR run pass for a silent meeting.
  // Only while the recording is in ERROR: a retry or speaker-inference run
  // moves it back to processing, and the old failure no longer applies then.
  const transcriptionError =
    transcriptStatus === "error" && recording.status === RecordingStatus.ERROR
      ? recording.transcript?.error_message || "Transcription failed."
      : null;

  return (
    <div
      ref={sectionRef}
      tabIndex={-1}
      className={`absolute inset-0 flex flex-col outline-none ${active ? "z-10 visible" : "z-0 invisible"}`}
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
      {/* A note from the live note box that never saved, or text left in it,
          outlives processing: listed here until saved or discarded. */}
      <RecordingUnsavedNotes
        recordingId={recording.id}
        readEndMs={readEndMs}
        onEmptied={focusSection}
        className="mx-4 mt-4 max-h-[40%] shrink-0 overflow-y-auto border-b border-surface-divider pb-3"
      />
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
            speakers={recording.speakers ?? NO_SPEAKERS}
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
            timelineNotes={notes}
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
