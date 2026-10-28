import { useCallback, useEffect, useState } from "react";

import { getLiveRecordingAnalytics } from "@/lib/api";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import type { LiveRecordingAnalytics, RecordingId } from "@/types";

const POLL_MS = 3000;

/** Polls one read at a time while enabled; disabling keeps the last snapshot. */
export function useLiveAnalytics(recordingId: RecordingId, enabled = true) {
  const [analytics, setAnalytics] = useState<LiveRecordingAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useOnDepsChange([recordingId], () => {
    setAnalytics(null);
    setError(null);
  });

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let reading = false;

    const read = () => {
      if (reading) return;
      reading = true;
      void getLiveRecordingAnalytics(recordingId, controller.signal)
        .then((next) => {
          if (controller.signal.aborted) return;
          setAnalytics(next);
          setError(null);
        })
        .catch(() => {
          if (!controller.signal.aborted) setError("Analytics could not be refreshed. Retrying shortly.");
        })
        .finally(() => {
          reading = false;
        });
    };

    read();
    const timer = setInterval(read, POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [recordingId, enabled]);

  const clearAnalytics = useCallback(() => {
    setAnalytics(null);
    setError(null);
  }, []);

  return { analytics, error, clearAnalytics };
}
