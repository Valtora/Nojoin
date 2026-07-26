"use client";

import { useEffect, useRef, useState } from "react";

import { getTranscriptUtterances } from "@/lib/api/transcript";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import {
  applyTranscriptDelta,
  LocalTranscriptState,
} from "@/lib/transcriptState";
import { Recording, TranscriptSegment } from "@/types";

/**
 * Poll interval while a capture is running.
 *
 * Deliberately slower than the recording poll's 1000ms. End-to-end latency is
 * already several seconds -- 2s upload segments, then waiting for the speech
 * region to complete or hit the forced cut, then queue and ASR time -- so a
 * faster poll cannot make text appear sooner. It only adds requests to a box
 * that is simultaneously running transcription.
 */
export const LIVE_TRANSCRIPT_POLL_INTERVAL_MS = 3000;

export interface LiveTranscript {
  segments: TranscriptSegment[];
  /** False until the first fetch resolves, so callers can tell empty from unknown. */
  hasLoaded: boolean;
}

/**
 * Live transcript feed for the in-flight recording view.
 *
 * Owns its own revision cursor and transcript state rather than sharing the
 * recording detail hook's. That hook's syncTranscriptState is a writer, not a
 * read: it pushes rolling speaker history and replaces the recording object on
 * every tick, and `recording` is a dependency of both of its poll effects, so
 * driving it during capture would tear down and recreate those intervals and
 * re-render the waveform subtree once per poll. Reading straight from the
 * utterances endpoint keeps this panel entirely out of that machinery, and
 * leaves the editor's undo history untouched.
 *
 * Read-only by construction: nothing here writes to the server or to any state
 * outside the hook.
 */
export function useLiveTranscript(
  recording: Recording | null | undefined,
): LiveTranscript {
  const recordingId = recording?.id ?? null;
  const isLive = isLiveCaptureInProgress(recording);

  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const stateRef = useRef<LocalTranscriptState | null>(null);

  useEffect(() => {
    if (!recordingId || !isLive) {
      return;
    }

    let cancelled = false;
    stateRef.current = null;
    setSegments([]);
    setHasLoaded(false);

    const sync = async (mode: "full" | "delta") => {
      const current = stateRef.current;
      const afterRevision =
        mode === "delta" && current?.recordingId === recordingId
          ? current.revision
          : undefined;

      const delta = await getTranscriptUtterances(recordingId, afterRevision);
      if (cancelled) {
        return;
      }

      const nextState = applyTranscriptDelta({
        currentState: stateRef.current,
        recordingId,
        fallbackSegments: [],
        delta,
        mode,
      });

      stateRef.current = nextState;
      setSegments(nextState.segments);
      setHasLoaded(true);
    };

    // A poll failure is not worth surfacing over a running meeting: it does not
    // affect the capture or the final transcript, and the next tick retries.
    const poll = (mode: "full" | "delta") => {
      sync(mode).catch((error) => {
        console.error("Live transcript poll failed", error);
      });
    };

    poll("full");
    const interval = setInterval(
      () => poll("delta"),
      LIVE_TRANSCRIPT_POLL_INTERVAL_MS,
    );

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [recordingId, isLive]);

  return { segments, hasLoaded };
}
