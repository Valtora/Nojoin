"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import { getTranscriptUtterances } from "@/lib/api/transcript";
import { RecordingStatus } from "@/types";
import {
  applyTranscriptDelta,
  LocalTranscriptState,
} from "@/lib/transcriptState";
import { Recording, RecordingSpeaker, TranscriptSegment } from "@/types";

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

/**
 * How long the lane must read as caught up, from the first read that finds it
 * so, before the behind notice clears (issue #812). The lane writes
 * `live/state.json` only when a run ends, so for runs of length T the lag a
 * read measures swings between about T and 2T and stays under the 20 s
 * threshold only for the first 20 s less T after each run ends. Holding on for
 * longer than the slowest run that can raise the notice outlasts that window,
 * so the notice holds steady instead of re-announcing and shifting the
 * transcript every run. Time rather than a count of reads, since a refresh
 * adds reads between polls. The cost is that it can stay up to about 20 s
 * after the lane catches up.
 */
export const LIVE_BEHIND_CLEAR_MS = 20_000;

export interface LiveTranscript {
  speakers: readonly RecordingSpeaker[];
  refresh: () => void;
  segments: TranscriptSegment[];
  /** False until the first fetch resolves, so callers can tell empty from unknown. */
  hasLoaded: boolean;
  /** Live label -> the name the user gave it, as the last read or rename said. */
  liveSpeakerNames: Readonly<Record<string, string>>;
  /** Shows the names a rename answered with, ahead of the next read. */
  applyLiveSpeakerNames: (names: Record<string, string>) => void;
  /**
   * Live label -> the live label the user merged it into. `segments` already
   * read a merged label's lines as the label it joined.
   */
  liveSpeakerMerges?: Readonly<Record<string, string>>;
  /** Shows the merges a merge answered with, ahead of the next read. */
  applyLiveSpeakerMerges?: (merges: Record<string, string>) => void;
  /**
   * A read said live transcription is behind the uploads, and the reads since
   * have not found it caught up for {@link LIVE_BEHIND_CLEAR_MS}.
   */
  liveBehind?: boolean;
}

const NO_NAMES: Readonly<Record<string, string>> = {};

/**
 * `next`, or `previous` when it holds the same names: every poll answers with
 * a new object, and a new one would re-render the panel each time.
 */
function sameOrNext(
  previous: Readonly<Record<string, string>>,
  next: Readonly<Record<string, string>> | undefined,
): Readonly<Record<string, string>> {
  const entries = Object.entries(next ?? {});
  if (entries.length === 0) return NO_NAMES;
  const unchanged =
    entries.length === Object.keys(previous).length &&
    entries.every(([label, name]) => previous[label] === name);
  return unchanged ? previous : (next as Readonly<Record<string, string>>);
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
 * outside the hook. A rename made elsewhere hands its answer to
 * `applyLiveSpeakerNames`, so the new name shows before the next read.
 */
export function useLiveTranscript(
  recording: Recording | null | undefined,
  enabled = recording?.status === RecordingStatus.UPLOADING ||
    recording?.status === RecordingStatus.PAUSED ||
    recording?.status === RecordingStatus.QUEUED ||
    recording?.status === RecordingStatus.PROCESSING,
): LiveTranscript {
  const recordingId = recording?.id ?? null;
  const refreshRef = useRef<(() => void) | null>(null);

  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [speakers, setSpeakers] = useState<RecordingSpeaker[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [liveBehind, setLiveBehind] = useState(false);
  const [liveSpeakerNames, setLiveSpeakerNames] =
    useState<Readonly<Record<string, string>>>(NO_NAMES);
  const [liveSpeakerMerges, setLiveSpeakerMerges] =
    useState<Readonly<Record<string, string>>>(NO_NAMES);
  const stateRef = useRef<LocalTranscriptState | null>(null);

  // A feed that starts, or restarts for another recording, shows nothing until
  // its first fetch lands, so one recording's lines never sit under another's.
  useOnDepsChange([recordingId, enabled], () => {
    if (!recordingId || enabled) {
      setSegments([]);
      setHasLoaded(false);
      setLiveBehind(false);
      setLiveSpeakerNames(NO_NAMES);
      setLiveSpeakerMerges(NO_NAMES);
      setSpeakers([]);
    }
  });

  useEffect(() => {
    if (!recordingId || !enabled) {
      return;
    }

    let cancelled = false;
    stateRef.current = null;
    // When the first of the reads in a row that found the lane caught up came.
    let caughtUpSince: number | null = null;

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
      setSpeakers((previous) => JSON.stringify(previous) === JSON.stringify(delta.speakers) ? previous : delta.speakers);
      setLiveSpeakerNames((previous) =>
        sameOrNext(previous, delta.live_speaker_names),
      );
      setLiveSpeakerMerges((previous) =>
        sameOrNext(previous, delta.live_speaker_merges),
      );
      if (delta.live_behind === true) {
        caughtUpSince = null;
        setLiveBehind(true);
      } else {
        const now = Date.now();
        caughtUpSince ??= now;
        if (now - caughtUpSince >= LIVE_BEHIND_CLEAR_MS) setLiveBehind(false);
      }
      setHasLoaded(true);
    };

    // One read at a time: a slow full read landing after a newer delta would
    // put the older transcript back. A tick that finds one running is skipped.
    let inFlight = false;
    let pendingFullRead = false;
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
          if (!cancelled && pendingFullRead) {
            pendingFullRead = false;
            poll("full");
          }
        });
    };

    refreshRef.current = () => {
      if (inFlight) pendingFullRead = true;
      else poll("full");
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
      refreshRef.current = null;
      clearInterval(interval);
    };
  }, [recordingId, enabled]);

  const applyLiveSpeakerNames = useCallback(
    (names: Record<string, string>) =>
      setLiveSpeakerNames((previous) => sameOrNext(previous, names)),
    [],
  );

  const applyLiveSpeakerMerges = useCallback(
    (merges: Record<string, string>) =>
      setLiveSpeakerMerges((previous) => sameOrNext(previous, merges)),
    [],
  );

  // A merge rewrites no line, so each read's lines are shown under the label
  // their speaker was merged into: one speaker in the lines, the panel and
  // the colours. Each shown line is kept per source line until the merges
  // change: lines redraw by identity, and a poll must redraw only the lines
  // it changed, not every line under a merged label.
  const remapped = useMemo(
    () => ({ merges: liveSpeakerMerges, lines: new WeakMap<TranscriptSegment, TranscriptSegment>() }),
    [liveSpeakerMerges],
  );
  const shown = useMemo(
    () => (Object.keys(remapped.merges).length === 0 ? segments
      : segments.map((segment) => showMerged(segment, remapped.merges, remapped.lines))),
    [segments, remapped],
  );

  const refresh = useCallback(() => refreshRef.current?.(), []);
  return {
    segments: shown, speakers, hasLoaded, liveSpeakerNames, applyLiveSpeakerNames,
    liveSpeakerMerges, applyLiveSpeakerMerges, refresh, liveBehind,
  };
}

/** A line under the label its speaker was merged into; the same object for the same line. */
function showMerged(
  segment: TranscriptSegment,
  merges: Readonly<Record<string, string>>,
  shown: WeakMap<TranscriptSegment, TranscriptSegment>,
): TranscriptSegment {
  const kept = shown.get(segment);
  if (kept) return kept;
  const into = merges[segment.speaker];
  const overlaps = segment.overlapping_speakers;
  const remapsOverlaps = !!overlaps?.length && (!!into || overlaps.some((label) => label in merges));
  if (!into && !remapsOverlaps) return segment;
  const speaker = into ?? segment.speaker;
  const line = {
    ...segment,
    speaker,
    ...(remapsOverlaps ? {
      overlapping_speakers: [...new Set(overlaps!.map((label) => merges[label] ?? label))]
        .filter((label) => label !== speaker),
    } : {}),
  };
  shown.set(segment, line);
  return line;
}
