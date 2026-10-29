import { ClientStatus, RecordingStatus } from "@/types";

interface RecordingGist {
  gist?: string | null;
  status: RecordingStatus;
  client_status?: ClientStatus;
}

/** The compact summary shown wherever a recording is linked or named. */
export function recordingGistLabel(recording: RecordingGist): string | undefined {
  const gist = recording.gist?.trim();
  if (!gist) return undefined;
  const live = recording.status === RecordingStatus.UPLOADING ||
    recording.status === RecordingStatus.PAUSED ||
    recording.status === RecordingStatus.QUEUED ||
    recording.status === RecordingStatus.PROCESSING ||
    recording.client_status === ClientStatus.RECORDING ||
    recording.client_status === ClientStatus.PAUSED;
  return live ? `Summary of transcript so far: ${gist}` : gist;
}

/** Keep the title's existing interaction hint alongside its summary. */
export function recordingTitleHint(recording: RecordingGist, hint: string): string {
  const gist = recordingGistLabel(recording);
  return gist ? `${gist}\n\n${hint}` : hint;
}
