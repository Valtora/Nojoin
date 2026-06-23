import { Recording, RecordingStatus, TranscriptSegment } from "@/types";
import type {
  RollingSpeakerCorrectionHistory,
  TranscriptSegmentChange,
} from "@/lib/transcriptSegments";

export const isDemoRecording = (recording: Recording) =>
  recording.name === "Welcome to Nojoin";

export const shouldPollRecordingUpdates = (recording: Recording) => {
  const waitingForProxy =
    recording.status === RecordingStatus.PROCESSED &&
    recording.has_proxy === false &&
    !isDemoRecording(recording);

  return (
    recording.status === RecordingStatus.PROCESSING ||
    recording.status === RecordingStatus.UPLOADING ||
    recording.status === RecordingStatus.PAUSED ||
    recording.status === RecordingStatus.QUEUED ||
    recording.transcript?.notes_status === "generating" ||
    recording.transcript?.meeting_edge_status === "updating" ||
    waitingForProxy
  );
};

export const isRecordingInFlight = (recording: Recording | null | undefined) =>
  recording?.status === RecordingStatus.PAUSED ||
  recording?.status === RecordingStatus.UPLOADING ||
  recording?.status === RecordingStatus.PROCESSING ||
  recording?.status === RecordingStatus.QUEUED;

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
