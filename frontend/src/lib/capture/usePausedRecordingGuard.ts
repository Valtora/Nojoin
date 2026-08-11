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
  const [recovered, setRecovered] = useState(false);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const paused = await refreshPausedRecording().catch(() => null);
      if (paused) {
        await discardEmptyInterruptedRecording().catch(() => {});
      }
      if (!cancelled) {
        setRecovered(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [discardEmptyInterruptedRecording, refreshPausedRecording]);

  return {
    pausedRecording,
    hasPausedRecording: recovered && Boolean(pausedRecording),
    refreshPausedRecording,
  };
}
