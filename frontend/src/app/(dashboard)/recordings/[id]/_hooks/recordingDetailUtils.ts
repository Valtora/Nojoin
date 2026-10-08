import { Recording, RecordingStatus, TranscriptSegment } from "@/types";
import type {
  RollingSpeakerCorrectionHistory,
  TranscriptSegmentChange,
} from "@/lib/transcriptSegments";

export const isDemoRecording = (recording: Recording) =>
  recording.name === "Welcome to Nojoin";

export const isRecordingInFlight = (recording: Recording | null | undefined) =>
  recording?.status === RecordingStatus.PAUSED ||
  recording?.status === RecordingStatus.UPLOADING ||
  recording?.status === RecordingStatus.PROCESSING ||
  recording?.status === RecordingStatus.QUEUED;

// Processed, with audio a proxy can be made from, and no proxy yet.
const isWaitingForProxy = (recording: Recording) =>
  recording.status === RecordingStatus.PROCESSED &&
  recording.has_proxy === false &&
  recording.has_audio !== false &&
  !isDemoRecording(recording);

/**
 * A settled recording with neither its audio nor a proxy on disk. That is
 * usually final, except during a restore: it commits the recording rows first
 * and moves their audio into place afterwards.
 */
export const isAudioUnavailable = (recording: Recording) =>
  recording.has_audio === false &&
  !isRecordingInFlight(recording) &&
  !isDemoRecording(recording);

export const shouldPollRecordingUpdates = (recording: Recording) =>
  recording.status === RecordingStatus.PROCESSING ||
  recording.status === RecordingStatus.UPLOADING ||
  recording.status === RecordingStatus.PAUSED ||
  recording.status === RecordingStatus.QUEUED ||
  recording.transcript?.notes_status === "generating" ||
  recording.transcript?.meeting_edge_status === "updating" ||
  isWaitingForProxy(recording);

/** How often a page with unavailable audio checks whether it has arrived. */
export const AUDIO_RECHECK_INTERVAL_MS = 15_000;

/**
 * How often the detail page re-reads the recording, or null when nothing on
 * it can change by itself. Unavailable audio is re-checked slowly, so a page
 * opened while a restore is still moving files into place recovers without a
 * reload.
 */
export const recordingPollIntervalMs = (recording: Recording): number | null => {
  if (shouldPollRecordingUpdates(recording)) {
    return recording.status === RecordingStatus.UPLOADING ||
      recording.status === RecordingStatus.PAUSED ||
      isWaitingForProxy(recording)
      ? 1000
      : 3000;
  }
  return isAudioUnavailable(recording) ? AUDIO_RECHECK_INTERVAL_MS : null;
};

const meetingEdgeSignature = (recording: Recording) =>
  JSON.stringify({
    focus: recording.transcript?.meeting_edge_focus ?? null,
    status: recording.transcript?.meeting_edge_status ?? null,
    error: recording.transcript?.meeting_edge_error_message ?? null,
    payload: recording.transcript?.meeting_edge_payload ?? null,
  });

/** Whether a polled copy of the recording differs in anything the page shows. */
export const hasPolledRecordingChanged = (current: Recording, polled: Recording) =>
  polled.status !== current.status ||
  polled.client_status !== current.client_status ||
  polled.processing_step !== current.processing_step ||
  polled.upload_progress !== current.upload_progress ||
  polled.processing_progress !== current.processing_progress ||
  polled.processing_eta_seconds !== current.processing_eta_seconds ||
  polled.processing_eta_learning !== current.processing_eta_learning ||
  polled.processing_eta_sample_size !== current.processing_eta_sample_size ||
  polled.has_proxy !== current.has_proxy ||
  polled.has_audio !== current.has_audio ||
  polled.transcript?.notes_status !== current.transcript?.notes_status ||
  polled.transcript?.notes !== current.transcript?.notes ||
  polled.transcript?.user_notes !== current.transcript?.user_notes ||
  meetingEdgeSignature(polled) !== meetingEdgeSignature(current) ||
  JSON.stringify(polled.speakers) !== JSON.stringify(current.speakers);

export const getAutoSpeakerReplacementName = (speakerName: string) => {
  const trimmedName = speakerName.trim();
  const nameParts = trimmedName.split(/\s+/).filter(Boolean);

  if (nameParts.length > 1) {
    return nameParts[0];
  }

  return trimmedName;
};

export interface TranscriptHistoryItem {
  patches: TranscriptSegmentChange[];
  description: string;
  rollingSpeakerCorrection?: RollingSpeakerCorrectionHistory;
}

export const cloneTranscriptSegments = (
  segments: TranscriptSegment[],
): TranscriptSegment[] => {
  return JSON.parse(JSON.stringify(segments)) as TranscriptSegment[];
};
