import { useCallback, useEffect, useRef, useState } from "react";

import { coalesceTranscriptReads } from "./transcriptReadQueue";
import { subscribeTranscriptEvents, type TranscriptReadMode } from "./transcriptEvents";

/** The recording detail owns this subscription even when its panels swap. */
export function useTranscriptEvents(
  recordingId: string | null,
  read: (mode: TranscriptReadMode) => Promise<unknown>,
  onClose?: () => void,
): { healthy: boolean; request: (mode: TranscriptReadMode) => void } {
  const readRef = useRef(read);
  const closeRef = useRef(onClose);
  const queueRef = useRef<ReturnType<typeof coalesceTranscriptReads> | null>(null);
  const [healthyId, setHealthyId] = useState<string | null>(null);
  useEffect(() => { readRef.current = read; }, [read]);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!recordingId) return;
    const queue = coalesceTranscriptReads(
      (mode) => readRef.current(mode),
      (error) => console.error("Transcript event read failed", error),
    );
    queueRef.current = queue;
    const unsubscribe = subscribeTranscriptEvents(recordingId, {
      read: queue.request,
      health: (healthy) => setHealthyId(healthy ? recordingId : null),
      close: () => { queue.close(); closeRef.current?.(); },
    });
    return () => {
      queueRef.current = null;
      queue.close();
      unsubscribe?.();
    };
  }, [recordingId]);
  const request = useCallback((mode: TranscriptReadMode) => queueRef.current?.request(mode), []);
  return { healthy: recordingId !== null && healthyId === recordingId, request };
}
