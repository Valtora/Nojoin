import { useState, useEffect, useRef, useMemo, useCallback, type Dispatch, type SetStateAction } from "react";

import { getTranscriptUtterances, updateTranscriptUtteranceText, updateTranscriptUtteranceSpeaker } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import {
  buildSpeakerHistoryAssignment, diffTranscriptSegments, extendRollingSpeakerHistoryWithSegments,
  type RollingSpeakerCorrectionHistory, type TranscriptSegmentChange,
} from "@/lib/transcriptSegments";
import {
  applyTranscriptDelta, createLocalTranscriptState, flushDeferredTranscriptState, type LocalTranscriptState,
} from "@/lib/transcriptState";
import { RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

import { isRecordingInFlight, shouldPollRecordingUpdates, type TranscriptHistoryItem } from "./recordingDetailUtils";

/** Transcript state, edit deferral, history and polling for the detail editor. */
export function useDetailTranscript(
  recording: Recording | null,
  setRecording: Dispatch<SetStateAction<Recording | null>>,
) {
  const transcriptStateRef = useRef<LocalTranscriptState | null>(null);
  const { addNotification } = useNotificationStore();
  const isInFlightRecording = isRecordingInFlight(recording);
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
  const recordingRef = useRef(recording);
  const activeEditRef = useRef(activeTranscriptEditId);

  useEffect(() => {
    recordingRef.current = recording;
    activeEditRef.current = activeTranscriptEditId;
  }, [recording, activeTranscriptEditId]);

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

  const syncTranscriptState = useCallback(
    async (
      mode: "full" | "delta" = "delta",
      options: { deferActiveEdit?: boolean } = {},
    ) => {
      const recording = recordingRef.current;
      if (!recording) {
        return null;
      }

      const currentTranscriptState = transcriptStateRef.current;
      const afterRevision =
        mode === "delta" && currentTranscriptState?.recordingId === recording.id
          ? currentTranscriptState.revision
          : undefined;
      const transcriptDelta = await getTranscriptUtterances(recording.id, afterRevision);
      if (recordingRef.current?.id !== recording.id) return null;

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
          options.deferActiveEdit === false ? null : activeEditRef.current,
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
    [setRecording],
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

  useEffect(() => {
    if (!recording?.id || isInFlightRecording) {
      return;
    }

    syncTranscriptState("full").catch((e) => {
      console.error("Failed to load transcript utterances:", e);
    });
    // syncTranscriptState intentionally runs once per recording load; later
    // delta polling and explicit mutation refreshes keep this state current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording?.id, isInFlightRecording]);

  useEffect(() => {
    if (!recording || isInFlightRecording || !shouldPollRecordingUpdates(recording)) {
      return;
    }

    const pollIntervalMs =
      recording.status === RecordingStatus.UPLOADING || recording.status === RecordingStatus.PAUSED
        ? 1000
        : 3000;

    const interval = setInterval(() => {
      syncTranscriptState("delta").catch((e) => {
        console.error("Transcript polling failed", e);
      });
    }, pollIntervalMs);

    return () => clearInterval(interval);
  }, [isInFlightRecording, recording, syncTranscriptState]);

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
