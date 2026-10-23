"use client";

import { useEffect, useRef, useState } from "react";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

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

/**
 * Every this many reads the feed reads the whole transcript, not the delta.
 *
 * The delta cursor is the highest event id seen. An edit that commits after a
 * later-numbered one, such as a live speaker label written while a person edits
 * another line, falls behind the cursor and no delta returns it. Every 10th read
 * picks it up: 30 s while each read answers within a poll, longer when slow
 * reads make ticks skip, since a skipped tick is not a read.
 */
export const LIVE_TRANSCRIPT_FULL_READ_EVERY_POLLS = 10;

/**
 * A read that has not answered by now is abandoned and aborted.
 *
 * Only one read runs at a time, so a request that never answers (a dropped
 * connection; the API client sets no timeout) would otherwise stop the feed for
 * the rest of the capture. Five poll intervals: a full read of a long meeting
 * on a busy box still fits, and a stall costs at most five skipped ticks.
 */
export const LIVE_TRANSCRIPT_READ_TIMEOUT_MS = 15_000;

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

  // A feed that starts, or restarts for another recording, shows nothing until
  // its first fetch lands, so one recording's lines never sit under another's.
  useOnDepsChange([recordingId, isLive], () => {
    if (recordingId && isLive) {
      setSegments([]);
      setHasLoaded(false);
    }
  });

  useEffect(() => {
    if (!recordingId || !isLive) {
      return;
    }

    let cancelled = false;
    stateRef.current = null;

    const sync = async (mode: "full" | "delta") => {
      const current = stateRef.current;
      const afterRevision =
        mode === "delta" && current?.recordingId === recordingId
          ? current.revision
          : undefined;

      const controller = new AbortController();
      let timer: ReturnType<typeof setTimeout> | undefined;
      const timedOut = new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error("Live transcript read timed out"));
        }, LIVE_TRANSCRIPT_READ_TIMEOUT_MS);
      });
      let delta;
      try {
        // Raced as well as aborted: an answer that comes after the timeout is
        // dropped, so it cannot land over a newer read.
        delta = await Promise.race([
          getTranscriptUtterances(recordingId, afterRevision, controller.signal),
          timedOut,
        ]);
      } finally {
        clearTimeout(timer);
      }
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

    // One read at a time: a slow full read landing after a newer delta would
    // put the older transcript back. A tick that finds one running is skipped.
    let inFlight = false;
    // A poll failure is not worth surfacing over a running meeting: it does not
    // affect the capture or the final transcript, and the next tick retries.
    const poll = (mode: "full" | "delta") => {
      inFlight = true;
      sync(mode)
        .catch((error) => {
          console.error("Live transcript poll failed", error);
        })
        .finally(() => {
          inFlight = false;
        });
    };

    poll("full");
    let polls = 0;
    const interval = setInterval(() => {
      if (inFlight) {
        return;
      }
      polls += 1;
      poll(polls % LIVE_TRANSCRIPT_FULL_READ_EVERY_POLLS === 0 ? "full" : "delta");
    }, LIVE_TRANSCRIPT_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [recordingId, isLive]);

  return { segments, hasLoaded };
}
