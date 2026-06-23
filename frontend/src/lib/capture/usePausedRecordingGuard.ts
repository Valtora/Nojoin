"use client";

import { useEffect } from "react";

import { useCapture } from "./CaptureProvider";

export function usePausedRecordingGuard() {
  const { pausedRecording, refreshPausedRecording } = useCapture();

  useEffect(() => {
    void refreshPausedRecording().catch(() => {});
  }, [refreshPausedRecording]);

  return {
    pausedRecording,
    hasPausedRecording: Boolean(pausedRecording),
    refreshPausedRecording,
  };
}
