import { ClientStatus, Recording, RecordingStatus } from "@/types";

/**
 * True only for a live browser capture, running or paused.
 *
 * Recording status alone cannot identify a capture. File imports also sit in
 * UPLOADING while their bytes are being sent, so a test of "UPLOADING and not
 * finalising" matches an import just as well as a capture. Only the capture
 * endpoints set a client status of RECORDING or PAUSED, and only a capture ever
 * reaches the PAUSED recording status at all.
 *
 * The PAUSED status is checked alongside the client status so that a capture
 * paused before capture start began recording a client status -- leaving it
 * NULL -- still resolves correctly.
 */
export const isLiveCaptureInProgress = (
  recording: Recording | null | undefined,
): boolean =>
  recording?.status === RecordingStatus.PAUSED ||
  recording?.client_status === ClientStatus.RECORDING ||
  recording?.client_status === ClientStatus.PAUSED;
