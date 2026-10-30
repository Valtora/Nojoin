import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { Recording, RecordingStatus, TimelineNote, TranscriptSegment } from "@/types";
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

/**
 * Whether an in-flight recording has lines for the live feed to read: a
 * capture's, from its start until its transcript publishes, or a finished
 * transcript that a later step (speaker inference, notes) runs over. An import
 * has no transcript until processing makes one, and the utterances read
 * answers 404 until then; a reprocess resets its transcript to pending. Neither
 * has lines before its run publishes (#835).
 *
 * Only capture start creates a transcript as "processing"
 * (`routes_batch_init.py`), and only a finished run sets "completed".
 */
export const hasInFlightTranscriptFeed = (recording: Recording | null | undefined) =>
  isLiveCaptureInProgress(recording) ||
  recording?.transcript?.transcript_status === "processing" ||
  recording?.transcript?.transcript_status === "completed";

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
 * How long a page keeps checking. A restore moves the audio into place seconds
 * after it commits the rows, so audio still missing after this is gone, and
 * each check re-reads the whole recording.
 */
export const AUDIO_RECHECK_WINDOW_MS = 10 * 60_000;

/**
 * Whether a note's file switched on is still being read. The detail read's
 * notes signature changes when its parse ends, so the page polls until then.
 */
export const noteFilesBeingRead = (notes: readonly TimelineNote[] | null): boolean =>
  !!notes?.some(
    ({ attachment }) =>
      attachment?.use_in_notes &&
      (attachment.status === "PENDING" || attachment.status === "PROCESSING"),
  );

/**
 * How often the detail page re-reads the recording, or null when nothing on
 * it can change by itself. Unavailable audio is re-checked slowly for a while,
 * so a page opened while a restore is still moving files into place recovers
 * without a reload. `audioUnavailableForMs` is how long the page has seen the
 * audio as unavailable; `filesBeingRead` keeps it reading while a note's
 * file is parsed (`noteFilesBeingRead`).
 */
export const recordingPollIntervalMs = (
  recording: Recording,
  audioUnavailableForMs = 0,
  filesBeingRead = false,
): number | null => {
  if (shouldPollRecordingUpdates(recording) || filesBeingRead) {
    return recording.status === RecordingStatus.UPLOADING ||
      recording.status === RecordingStatus.PAUSED ||
      isWaitingForProxy(recording)
      ? 1000
      : 3000;
  }
  return isAudioUnavailable(recording) &&
    audioUnavailableForMs < AUDIO_RECHECK_WINDOW_MS
    ? AUDIO_RECHECK_INTERVAL_MS
    : null;
};

const meetingEdgeSignature = (recording: Recording) =>
  JSON.stringify({
    focus: recording.transcript?.meeting_edge_focus ?? null,
    status: recording.transcript?.meeting_edge_status ?? null,
    error: recording.transcript?.meeting_edge_error_message ?? null,
    payload: recording.transcript?.meeting_edge_payload ?? null,
  });

/** Whether a polled copy of the recording differs in anything the page shows. */
/**
 * Whether a failed poll means the recording is gone. A 404 does, except while
 * this tab's session cannot be used: tabs share the session cookie, so after a
 * sign-in as another account in another tab, a recording that still exists is
 * refused as a 404 until its owner signs back in (issue #502).
 */
export const pollFailureMeansRemoved = (status: number | null, sessionUsable: boolean) =>
  sessionUsable && status === 404;

/** What a 404 for the page's recording turned out to mean. */
export type NotFoundMeaning =
  | { kind: "removed" }
  | { kind: "signed-in-elsewhere"; username: string };

/**
 * What a 404 for the page's recording says, once the server has named the
 * account the browser is signed in as (`whoIsSignedIn`, which tells the
 * session only where sign-in is held). Tabs share the session cookie, so
 * after a sign-in as another account in another tab, this tab's requests go
 * out as that account, and a recording that still exists answers 404 (issue
 * #583). Only for `account`, the account that first loaded the recording,
 * does a 404 mean it is gone.
 *
 * - "removed": the recording is gone.
 * - "signed-in-elsewhere": the browser is `username`'s now.
 * - null: nothing to act on. `/users/me` failed; or sign-in is held in place
 *   (`signInHeld`, read once the answer is in), and its dialog has opened
 *   instead; or the page never learnt its account, so it can't tell, and
 *   stays rather than claim a removal.
 */
export const notFoundMeans = async (
  account: number | null,
  whoIsSignedIn: () => Promise<{ id: number; username: string }>,
  signInHeld: () => boolean,
): Promise<NotFoundMeaning | null> => {
  let signedIn: { id: number; username: string };
  try {
    signedIn = await whoIsSignedIn();
  } catch {
    return null;
  }
  if (account === null) return null;
  if (signedIn.id === account) return { kind: "removed" };
  return signInHeld() ? null : { kind: "signed-in-elsewhere", username: signedIn.username };
};

export const hasPolledRecordingChanged = (current: Recording, polled: Recording) =>
  polled.status !== current.status ||
  !!polled.previous_publication_available !== !!current.previous_publication_available ||
  !!polled.reprocess_publication_complete !== !!current.reprocess_publication_complete ||
  polled.client_status !== current.client_status ||
  polled.processing_step !== current.processing_step ||
  polled.upload_progress !== current.upload_progress ||
  polled.processing_progress !== current.processing_progress ||
  polled.processing_eta_seconds !== current.processing_eta_seconds ||
  polled.processing_eta_learning !== current.processing_eta_learning ||
  polled.processing_eta_sample_size !== current.processing_eta_sample_size ||
  !!polled.discardable !== !!current.discardable ||
  polled.has_proxy !== current.has_proxy ||
  polled.has_audio !== current.has_audio ||
  // Whether the live feed has anything to read (hasInFlightTranscriptFeed).
  polled.transcript?.transcript_status !== current.transcript?.transcript_status ||
  polled.transcript?.notes_status !== current.transcript?.notes_status ||
  polled.transcript?.notes !== current.transcript?.notes ||
  // Changes with the notes list, which the page then fetches.
  polled.timeline_notes_list_signature !== current.timeline_notes_list_signature ||
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
