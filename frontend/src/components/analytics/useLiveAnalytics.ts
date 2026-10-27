import { useEffect, useState } from "react";

import { getLiveRecordingAnalytics } from "@/lib/api";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import type { LiveRecordingAnalytics, RecordingId } from "@/types";

const POLL_MS = 3000;
const TIMEOUT_MS = 15000;

/** One bounded read at a time; a refresh replaces the reader's entire generation. */
export function useLiveAnalytics(recordingId: RecordingId, refreshKey: string) {
  const [analytics, setAnalytics] = useState<LiveRecordingAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useOnDepsChange([recordingId], () => {
    setAnalytics(null);
    setError(null);
  });

  useEffect(() => {
    let disposed = false;
    let request: { controller: AbortController; timeout: ReturnType<typeof setTimeout> } | null = null;

    const read = () => {
      if (request) return;
      const controller = new AbortController();
      const current = {
        controller,
        timeout: setTimeout(() => {
          controller.abort();
          if (disposed || request !== current) return;
          request = null;
          setError("Analytics refresh timed out. Retrying shortly.");
        }, TIMEOUT_MS),
      };
      request = current;
      void getLiveRecordingAnalytics(recordingId, controller.signal)
        .then((next) => {
          if (disposed || request !== current) return;
          setAnalytics(next);
          setError(null);
        })
        .catch(() => {
          if (!disposed && request === current) {
            setError("Analytics could not be refreshed. Retrying shortly.");
          }
        })
        .finally(() => {
          clearTimeout(current.timeout);
          if (request === current) request = null;
        });
    };

    read();
    const timer = setInterval(read, POLL_MS);
    return () => {
      disposed = true;
      clearInterval(timer);
      if (request) {
        clearTimeout(request.timeout);
        request.controller.abort();
      }
    };
  }, [recordingId, refreshKey]);

  return { analytics, error };
}
