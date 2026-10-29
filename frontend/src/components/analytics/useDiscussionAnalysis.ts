import { useCallback, useEffect, useState } from "react";

import { generateRecordingAiAnalytics, getRecordingAiAnalytics, type RecordingAiAnalytics } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import type { RecordingId } from "@/types";

/** Reads discussion as the finished Analytics tab does: once, then every 3 s while a run is in progress. */
export function useDiscussionAnalysis(recordingId: RecordingId) {
  const [analysis, setAnalysis] = useState<RecordingAiAnalytics | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Held from the click until a read after the accepted request, which the
  // API has already marked generating, so polling never stops in between.
  const [starting, setStarting] = useState(false);
  const generating = starting || analysis?.ai_status === "generating";

  const read = useCallback((signal?: AbortSignal) => getRecordingAiAnalytics(recordingId, signal).then(
    (next) => {
      if (signal?.aborted) return undefined;
      setAnalysis(next);
      setReadError(null);
      return next;
    },
    () => {
      if (!signal?.aborted) setReadError("Discussion analysis could not be refreshed. Retrying shortly.");
      return undefined;
    },
  ), [recordingId]);

  useEffect(() => {
    const controller = new AbortController();
    void read(controller.signal);
    return () => controller.abort();
  }, [read]);

  useEffect(() => {
    if (!generating) return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      void read(controller.signal).then((next) => { if (next) setStarting(false); });
    }, 3000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [generating, read]);

  const generate = useCallback(async () => {
    setStarting(true);
    setActionError(null);
    try {
      await generateRecordingAiAnalytics(recordingId);
    } catch (cause) {
      setActionError(getErrorMessage(cause, "The discussion analysis could not be started."));
      setStarting(false);
      return;
    }
    if (await read()) setStarting(false);
  }, [recordingId, read]);

  return { analysis, readError, actionError, generate, generating };
}
