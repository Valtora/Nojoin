/** Passive recovery discovery; querying a browser lock never takes ownership. */
import { RecordingStatus, type Recording, type RecordingId } from "@/types";
import { isCaptureHeldInThisBrowser } from "./captureLock";
import { readPausedCaptureContext } from "./shared";

export interface RecoveryRequest {
  onDemand?: boolean;
  recordingId?: RecordingId;
  /** Route-entry requests expire when navigation leaves that recording. */
  isCurrent?: () => boolean;
}

/**
 * The capture this tab was running when its page unloaded, which it may take
 * back at once rather than after the server judges it stalled (issue #778).
 * Its context is in per-tab storage, so only this tab after a reload, or a
 * duplicate of it, has one.
 */
export function reclaimableRecordingId(): RecordingId | null {
  const context = readPausedCaptureContext();
  return context?.epoch === undefined ? null : context.recordingId;
}

/** All recoverable captures, excluding this tab's runtime and other held tabs. */
export async function findAttentionRecordings(
  captures: Recording[],
  ownedRecordingId: RecordingId | null,
  reclaimableId: RecordingId | null = null,
): Promise<Recording[]> {
  const eligibility = await Promise.all(captures.map(async (recording) => {
    const recoverable = recording.status === RecordingStatus.PAUSED || recording.capture_stalled ||
      recording.id === reclaimableId;
    return recoverable && recording.id !== ownedRecordingId &&
      !await isCaptureHeldInThisBrowser(recording.id);
  }));
  return captures.filter((_, index) => eligibility[index]);
}

/** A passive refresh may retain an offer, but cannot introduce a new one. */
export function selectRecoveryOffer(
  attention: Recording[],
  requestedId: RecordingId | null,
  request: RecoveryRequest,
): Recording | null {
  if (request.recordingId) {
    return attention.find((recording) => recording.id === request.recordingId) ?? null;
  }
  if (request.onDemand) {
    return attention.find((recording) => recording.status === RecordingStatus.PAUSED) ?? attention[0] ?? null;
  }
  return attention.find((recording) => recording.id === requestedId) ?? null;
}
