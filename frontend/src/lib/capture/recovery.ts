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

export interface ReclaimableCapture {
  recordingId: RecordingId;
  epoch: number;
}

/**
 * The capture this tab was running when its page unloaded, and the epoch it
 * held, so it may take the capture back at once rather than after the server
 * judges it stalled (issue #778). Its context is in per-tab storage, so only
 * this tab after a reload, or a duplicate of it, has one.
 */
export function reclaimableCapture(): ReclaimableCapture | null {
  const context = readPausedCaptureContext();
  return context?.epoch === undefined ? null : { recordingId: context.recordingId, epoch: context.epoch };
}

/** All recoverable captures, excluding this tab's runtime and other held tabs. */
export async function findAttentionRecordings(
  captures: Recording[],
  ownedRecordingId: RecordingId | null,
  reclaimable: ReclaimableCapture | null = null,
): Promise<Recording[]> {
  const eligibility = await Promise.all(captures.map(async (recording) => {
    // Only while the epoch is still the one this tab held: once the capture
    // was taken over elsewhere, it is running there.
    const reclaimed = recording.id === reclaimable?.recordingId &&
      recording.capture_epoch === reclaimable.epoch;
    const recoverable = recording.status === RecordingStatus.PAUSED || recording.capture_stalled || reclaimed;
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
