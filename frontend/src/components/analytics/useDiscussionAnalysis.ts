import { useCallback, useEffect, useRef, useState } from "react";

import { generateRecordingAiAnalytics, getRecordingAiAnalytics, type RecordingAiAnalytics } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import type { RecordingId } from "@/types";

/** Reads discussion independently of audio analytics, preserving the last usable result. */
export function useDiscussionAnalysis(recordingId: RecordingId) {
  const [analysis, setAnalysis] = useState<RecordingAiAnalytics | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const posting = useRef(false);
  const readEpoch = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    const read = async () => {
      if (reading || posting.current) return;
      reading = true;
      const epoch = readEpoch.current;
      const deadline = new AbortController();
      const timeout = setTimeout(() => deadline.abort(), 15000);
      try {
        const next = await getRecordingAiAnalytics(recordingId,
          AbortSignal.any([controller.signal, deadline.signal]));
        if (controller.signal.aborted || epoch !== readEpoch.current) return;
        setAnalysis(next);
        setReadError(null);
        setGenerating(false);
      } catch {
        if (!controller.signal.aborted && epoch === readEpoch.current) {
          setReadError("Discussion analysis could not be refreshed. Retrying shortly.");
        }
      } finally {
        clearTimeout(timeout);
        reading = false;
      }
    };
    void read();
    const timer = setInterval(() => void read(), 3000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [recordingId, refresh]);

  const generate = useCallback(async () => {
    if (posting.current || generating || analysis?.ai_status === "generating") return;
    posting.current = true;
    readEpoch.current += 1;
    setGenerating(true);
    setActionError(null);
    try {
      await generateRecordingAiAnalytics(recordingId);
      // The accepted request has persisted its own generating state. Read it
      // immediately, invalidating any read launched before that acceptance.
    } catch (cause) {
      setActionError(getErrorMessage(cause, "The discussion analysis could not be started."));
      setGenerating(false);
    } finally {
      posting.current = false;
      setRefresh((previous) => previous + 1);
    }
  }, [recordingId, generating, analysis?.ai_status]);

  return { analysis, readError, actionError, generate,
    generating: generating || analysis?.ai_status === "generating" };
}
