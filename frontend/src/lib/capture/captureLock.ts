import type { RecordingId } from "@/types";

/**
 * Marks a recording as captured by a tab of this browser, for as long as that
 * tab's capture runs.
 *
 * The server cannot tell a capture whose tab closed without pausing it from one
 * that is still recording but cannot upload, because its session ended or its
 * connection dropped. Another tab of the same browser can: the browser releases
 * a Web Lock when the tab holding it closes, reloads or crashes, with no timer
 * involved. So a recording whose lock is still held here is never offered for
 * resume or discard by another tab (issue #519). Another device has no such
 * signal and relies on the server's stall threshold alone.
 *
 * Shared mode, so taking the lock never waits on anything, and other tabs only
 * ever query it, never request it.
 */
const lockName = (recordingId: RecordingId) => `nojoin-capture:${recordingId}`;

const lockManager = (): LockManager | null => {
  if (typeof navigator === "undefined" || !navigator.locks) {
    return null;
  }
  return navigator.locks;
};

/** Holds the capture lock until the returned function is called. */
export const holdCaptureLock = (recordingId: RecordingId): (() => void) => {
  const locks = lockManager();
  if (!locks) {
    return () => {};
  }

  let release: () => void = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  void locks
    .request(lockName(recordingId), { mode: "shared" }, () => released)
    .catch((error: unknown) => {
      console.warn("[capture] could not mark the capture as held by this tab", error);
    });
  return release;
};

/** Whether any tab of this browser is capturing the recording right now. */
export const isCaptureHeldInThisBrowser = async (
  recordingId: RecordingId,
): Promise<boolean> => {
  const locks = lockManager();
  if (!locks) {
    return false;
  }

  try {
    const snapshot = await locks.query();
    const name = lockName(recordingId);
    return [...(snapshot.held ?? []), ...(snapshot.pending ?? [])].some(
      (lock) => lock.name === name,
    );
  } catch {
    return false;
  }
};
