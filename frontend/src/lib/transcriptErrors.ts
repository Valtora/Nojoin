import type { Transcript } from "@/types";

/**
 * The notes failure reason carried on a transcript, if any.
 *
 * `error_message` is shared by transcription and notes failures. While the
 * transcription has failed it says why, which is not a notes error, so it is
 * never presented as one.
 */
export const notesErrorDetail = (
  transcript: Transcript | null | undefined,
): string | null =>
  transcript && transcript.transcript_status !== "error"
    ? transcript.error_message || null
    : null;
