interface RecordingGist {
  gist?: string | null;
  /** The gist was written during capture, from the transcript so far. */
  gist_live?: boolean;
}

/** The compact summary shown wherever a recording is linked or named. */
export function recordingGistLabel(recording: RecordingGist): string | undefined {
  const gist = recording.gist?.trim();
  if (!gist) return undefined;
  return recording.gist_live ? `Summary of transcript so far: ${gist}` : gist;
}

/** Keep the title's existing interaction hint alongside its summary. */
export function recordingTitleHint(recording: RecordingGist, hint: string): string {
  const gist = recordingGistLabel(recording);
  return gist ? `${gist}\n\n${hint}` : hint;
}
