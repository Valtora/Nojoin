import { useState, useEffect, useRef, useMemo, useCallback, type Dispatch, type SetStateAction } from "react";

import { getTranscriptUtterances, updateTranscriptUtteranceText, updateTranscriptUtteranceSpeaker } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import {
  buildSpeakerHistoryAssignment, diffTranscriptSegments, extendRollingSpeakerHistoryWithSegments,
  type RollingSpeakerCorrectionHistory, type TranscriptSegmentChange,
} from "@/lib/transcriptSegments";
import {
  applyTranscriptDelta, createLocalTranscriptState, flushDeferredTranscriptState, type LocalTranscriptState,
} from "@/lib/transcriptState";
import { supportsTranscriptEvents } from "@/lib/transcriptEvents";
import { serialiseTranscriptReads, transcriptReadWithTimeout } from "@/lib/transcriptReadQueue";
import { useTranscriptEvents } from "@/lib/useTranscriptEvents";
import { RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

import { isRecordingInFlight, shouldPollRecordingUpdates, type TranscriptHistoryItem } from "./recordingDetailUtils";

/** Transcript state, edit deferral and history stay independent of stream ownership. */
export function useDetailTranscript(
  recording: Recording | null,
  setRecording: Dispatch<SetStateAction<Recording | null>>,
) {
  const transcriptStateRef = useRef<LocalTranscriptState | null>(null);
  const viewedIdRef = useRef<string | null>(null);
  const lifecycleAbortRef = useRef<AbortController | null>(null);
  const { addNotification } = useNotificationStore();
  const isInFlightRecording = isRecordingInFlight(recording);
  const hasOtherTranscriptReader = supportsTranscriptEvents()
    ? isLiveCaptureInProgress(recording)
    : isInFlightRecording;
  useEffect(() => {
    const controller = new AbortController();
    lifecycleAbortRef.current = controller;
    viewedIdRef.current = recording?.id ?? null;
    return () => { viewedIdRef.current = null; controller.abort(); };
  }, [recording?.id]);
  // Undo/Redo State
  const [history, setHistory] = useState<TranscriptHistoryItem[]>([]);
  const [future, setFuture] = useState<TranscriptHistoryItem[]>([]);
  const [isUndoing, setIsUndoing] = useState(false);
  // The names given to live labels during capture, which a transcript still
  // made of live lines (processing, or processing failed) shows.
  const [liveSpeakerNames, setLiveSpeakerNames] = useState<{
    recordingId: string;
    names: Record<string, string>;
  } | null>(null);
  const [transcriptState, setTranscriptState] = useState<LocalTranscriptState | null>(
    null,
  );
  const [activeTranscriptEditId, setActiveTranscriptEditId] = useState<
    string | null
  >(null);

  const transcriptSegments = useMemo(
    () => {
      const segments = transcriptState?.segments || recording?.transcript?.segments || [];
      return recording?.transcript?.settling_required
        ? segments.map((segment) => segment.settling_required ? segment : { ...segment, settling_required: true })
        : segments;
    },
    [recording?.transcript?.segments, recording?.transcript?.settling_required, transcriptState?.segments],
  );
  const deferredTranscriptUtteranceIds = useMemo(
    () => Object.keys(transcriptState?.deferredById || {}),
    [transcriptState?.deferredById],
  );

  useEffect(() => {
    transcriptStateRef.current = transcriptState;
  }, [transcriptState]);

  useEffect(() => {
    if (!recording) {
      return;
    }

    setTranscriptState((prev) => {
      if (prev && prev.recordingId === recording.id && prev.revision > 0) {
        transcriptStateRef.current = prev;
        return prev;
      }

      const nextState = createLocalTranscriptState(
        recording.id,
        isRecordingInFlight(recording) ? [] : (recording.transcript?.segments || []),
        prev?.recordingId === recording.id ? prev.revision : 0,
        prev?.recordingId === recording.id ? prev.deferredById : {},
      );
      transcriptStateRef.current = nextState;
      return nextState;
    });
  }, [recording]);

  const pushTranscriptHistory = useCallback(
    (
      description: string,
      previousSegments: TranscriptSegment[],
      nextSegments: TranscriptSegment[],
      rollingSpeakerCorrection?: RollingSpeakerCorrectionHistory,
    ) => {
      const patches = diffTranscriptSegments(previousSegments, nextSegments);

      if (patches.length === 0) {
        return;
      }

      setHistory((prev) => [
        ...prev,
        { patches, description, rollingSpeakerCorrection },
      ]);
      setFuture([]);
    },
    [],
  );

  const syncTranscriptStateNow = useCallback(
    async (
      mode: "full" | "delta" = "delta",
      options: { deferActiveEdit?: boolean } = {},
    ) => {
      if (!recording) {
        return null;
      }

      const currentTranscriptState = transcriptStateRef.current;
      const afterRevision =
        mode === "delta" && currentTranscriptState?.recordingId === recording.id
          ? currentTranscriptState.revision
          : undefined;
      const transcriptDelta = supportsTranscriptEvents()
        ? await transcriptReadWithTimeout(
          (signal) => getTranscriptUtterances(recording.id, afterRevision, signal),
          lifecycleAbortRef.current?.signal,
        )
        : await getTranscriptUtterances(recording.id, afterRevision);
      if (supportsTranscriptEvents() && viewedIdRef.current !== recording.id) return null;

      const latestTranscriptState = transcriptStateRef.current;
      if (
        mode === "delta" &&
        latestTranscriptState?.recordingId === recording.id &&
        transcriptDelta.revision < latestTranscriptState.revision
      ) {
        return {
          revision: latestTranscriptState.revision,
          segments: latestTranscriptState.segments,
          speakers: transcriptDelta.speakers,
        };
      }

      const previousSegments = latestTranscriptState?.segments || [];
      const nextState = applyTranscriptDelta({
        currentState: latestTranscriptState,
        recordingId: recording.id,
        fallbackSegments: recording.transcript?.segments || [],
        delta: transcriptDelta,
        mode,
        activeEditUtteranceId:
          options.deferActiveEdit === false ? null : activeTranscriptEditId,
      });
      const nextSegments = nextState.segments;
      transcriptStateRef.current = nextState;
      setTranscriptState(nextState);
      const liveNames = transcriptDelta.live_speaker_names ?? {};
      setLiveSpeakerNames((current) =>
        current?.recordingId === recording.id &&
        JSON.stringify(current.names) === JSON.stringify(liveNames)
          ? current
          : { recordingId: recording.id, names: liveNames },
      );

      if (mode === "delta" && nextSegments.length > 0) {
        setHistory((currentHistory) =>
          extendRollingSpeakerHistoryWithSegments(
            currentHistory,
            previousSegments,
            nextSegments,
          ),
        );
      }

      setRecording((prev) => {
        if (!prev || prev.id !== recording.id) {
          return prev;
        }

        return {
          ...prev,
          speakers: transcriptDelta.speakers,
        };
      });

      return {
        revision: transcriptDelta.revision,
        segments: nextSegments,
        speakers: transcriptDelta.speakers,
      };
    },
    [activeTranscriptEditId, recording, setRecording],
  );

  useEffect(() => {
    if (activeTranscriptEditId !== null) {
      return;
    }

    setTranscriptState((prev) => {
      if (!prev || Object.keys(prev.deferredById).length === 0) {
        return prev;
      }

      return flushDeferredTranscriptState(prev);
    });
  }, [activeTranscriptEditId]);

  const readRef = useRef(syncTranscriptStateNow);
  useEffect(() => { readRef.current = syncTranscriptStateNow; }, [syncTranscriptStateNow]);
  const recordingId = recording?.id ?? null;
  const serialRead = useMemo(() => serialiseTranscriptReads(
    (mode: "full" | "delta" = "delta", options: { deferActiveEdit?: boolean } = {}) =>
      viewedIdRef.current === recordingId ? readRef.current(mode, options) : Promise.resolve(null),
  ), [recordingId]);
  // Without a stream, preserve the existing reader and polling behaviour.
  const syncTranscriptState = supportsTranscriptEvents() ? serialRead : syncTranscriptStateNow;
  const { healthy: streamHealthy, request: requestStreamRead } = useTranscriptEvents(recordingId, (mode) =>
    hasOtherTranscriptReader ? Promise.resolve(null) : syncTranscriptState(mode),
    () => lifecycleAbortRef.current?.abort(),
  );

  useEffect(() => {
    if (!recording?.id || hasOtherTranscriptReader) {
      return;
    }

    syncTranscriptState("full").catch((e) => {
      console.error("Failed to load transcript utterances:", e);
    });
    // syncTranscriptState intentionally runs once per recording load; later
    // delta polling and explicit mutation refreshes keep this state current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording?.id, hasOtherTranscriptReader, isInFlightRecording]);

  useEffect(() => {
    if (!recording || hasOtherTranscriptReader || streamHealthy ||
      (!supportsTranscriptEvents() && !shouldPollRecordingUpdates(recording))) {
      return;
    }

    const pollIntervalMs =
      !supportsTranscriptEvents() && (
        recording.status === RecordingStatus.UPLOADING || recording.status === RecordingStatus.PAUSED
      )
        ? 1000
        : 3000;

    const interval = setInterval(() => {
      if (supportsTranscriptEvents()) {
        requestStreamRead("delta");
        return;
      }
      syncTranscriptState("delta").catch((e) => {
        console.error("Transcript polling failed", e);
      });
    }, pollIntervalMs);

    return () => clearInterval(interval);
  }, [hasOtherTranscriptReader, recording, requestStreamRead, streamHealthy, syncTranscriptState]);

  // History Management
  const applyTranscriptHistory = useCallback(
    async (patches: TranscriptSegmentChange[], direction: "undo" | "redo") => {
      if (!recording) {
        return;
      }

      for (const patch of patches) {
        const targetSegment = direction === "undo" ? patch.before : patch.after;
        if (!targetSegment.id) {
          continue;
        }

        const currentSegment = transcriptStateRef.current?.segments.find(
          (segment) => segment.id === targetSegment.id,
        );
        if (!currentSegment) {
          continue;
        }

        if (
          patch.changedFields.includes("text") &&
          currentSegment.text !== targetSegment.text
        ) {
          await updateTranscriptUtteranceText(
            recording.id,
            targetSegment.id,
            targetSegment.text,
            currentSegment.revision,
          );
        }

        if (
          patch.changedFields.includes("speaker") &&
          currentSegment.speaker !== targetSegment.speaker
        ) {
          await updateTranscriptUtteranceSpeaker(
            recording.id,
            targetSegment.id,
            buildSpeakerHistoryAssignment(targetSegment),
          );
        }
      }
    },
    [recording],
  );

  const handleUndo = async () => {
    if (history.length === 0 || !recording || isUndoing) return;

    const previousState = history[history.length - 1];

    setIsUndoing(true);
    try {
      await applyTranscriptHistory(previousState.patches, "undo");
      await syncTranscriptState("full");
      setHistory((prev) => prev.slice(0, -1));
      setFuture((prev) => [previousState, ...prev]);

    } catch (e: unknown) {
      console.error("Undo failed", e);
      await syncTranscriptState("full").catch(() => undefined);
      addNotification({ type: "error", message: "Undo failed." });
    } finally {
      setIsUndoing(false);
    }
  };

  const handleRedo = async () => {
    if (future.length === 0 || !recording || isUndoing) return;

    const nextState = future[0];

    setIsUndoing(true);
    try {
      await applyTranscriptHistory(nextState.patches, "redo");
      await syncTranscriptState("full");
      setFuture((prev) => prev.slice(1));
      setHistory((prev) => [...prev, nextState]);

    } catch (e: unknown) {
      console.error("Redo failed", e);
      await syncTranscriptState("full").catch(() => undefined);
      addNotification({ type: "error", message: "Redo failed." });
    } finally {
      setIsUndoing(false);
    }
  };

  return {
    transcriptStateRef, setTranscriptState, transcriptSegments, deferredTranscriptUtteranceIds,
    liveSpeakerNames, history, future, isUndoing, syncTranscriptState, pushTranscriptHistory,
    setActiveTranscriptEditId, handleUndo, handleRedo,
  };
}

