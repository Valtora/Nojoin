"use client";

import { useEffect, useState } from "react";

import { useCapture } from "./CaptureProvider";

export function usePausedRecordingGuard() {
  const {
    pausedRecording,
    refreshPausedRecording,
    discardEmptyInterruptedRecording,
  } = useCapture();
  // The modal stays shut until the recovery pass has run, so an interruption
  // that is about to be cleared automatically never flashes a decision at the
  // user on the way past.
  const [recoveredId, setRecoveredId] = useState<string | null>(null);
  const offeredId = pausedRecording?.id ?? null;

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      await refreshPausedRecording().catch(() => null);
      if (cancelled) return;
      await discardEmptyInterruptedRecording().catch(() => {});
      if (!cancelled) {
        setRecoveredId(offeredId);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [discardEmptyInterruptedRecording, refreshPausedRecording, offeredId]);

  return {
    pausedRecording,
    hasPausedRecording: Boolean(pausedRecording) && recoveredId === offeredId,
    refreshPausedRecording,
  };
}
