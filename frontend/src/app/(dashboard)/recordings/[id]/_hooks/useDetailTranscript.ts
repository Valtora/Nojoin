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
import { RecordingStatus, type Recording, type RecordingSpeaker, type TranscriptSegment } from "@/types";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import { isRecordingInFlight, shouldPollRecordingUpdates, type TranscriptHistoryItem } from "./recordingDetailUtils";
import { createDetailTranscriptReader, type DetailTranscriptRead, type TranscriptSyncResult } from "./detailTranscriptReader";

export const SETTLED_TRANSCRIPT_READ_TIMEOUT_MS = 15_000;

/** Transcript state, edit deferral, history and polling for the detail editor. */
export function useDetailTranscript(
  recording: Recording | null,
  setRecording: Dispatch<SetStateAction<Recording | null>>,
  { authoritativeEmpty = false, inFlight = isRecordingInFlight(recording), retainedPublication = false }:
    { authoritativeEmpty?: boolean; inFlight?: boolean; retainedPublication?: boolean } = {},
) {
  const transcriptStateRef = useRef<LocalTranscriptState | null>(null);
  const acceptedSnapshotRecordingIdRef = useRef<string | null>(null);
  const { addNotification } = useNotificationStore();
  const isInFlightRecording = inFlight;
  const [readerGeneration, setReaderGeneration] = useState(0);
  const [fullReadReady, setFullReadReady] = useState<{
    recordingId: string; generation: number;
  } | null>(null);
  const publicationPending = recording?.previous_publication_available === true;
  useOnDepsChange([recording?.id, isInFlightRecording, publicationPending], () => {
    setReaderGeneration((generation) => generation + 1);
    setFullReadReady(null);
  });
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
  const [publicationSpeakers, setPublicationSpeakers] = useState<{
    recordingId: string; speakers: RecordingSpeaker[];
  } | null>(null);
  const [activeTranscriptEditId, setActiveTranscriptEditId] = useState<
    string | null
  >(null);
  const recordingRef = useRef(recording);
  const inFlightRef = useRef(isInFlightRecording);
  const activeEditRef = useRef(activeTranscriptEditId);
  const readerRef = useRef<ReturnType<typeof createDetailTranscriptReader> | null>(null);
  const fullSequenceRef = useRef<{ recordingId: string; sequence: number; speakers: string } | null>(null);
  const repairRef = useRef<{
    recordingId: string; sequence: number; speakers: string; promise: Promise<TranscriptSyncResult | null>;
  } | null>(null);

  useEffect(() => {
    recordingRef.current = recording;
    inFlightRef.current = isInFlightRecording;
    activeEditRef.current = activeTranscriptEditId;
  }, [recording, isInFlightRecording, activeTranscriptEditId]);

  const transcriptSegments = useMemo(
    () => {
      const segments = transcriptState?.recordingId === recording?.id
        ? transcriptState?.segments ?? []
        : recording?.transcript?.segments ?? [];
      return recording?.transcript?.settling_required
        ? segments.map((segment) => segment.settling_required ? segment : { ...segment, settling_required: true })
        : segments;
    },
    [recording?.id, recording?.transcript?.segments, recording?.transcript?.settling_required,
      transcriptState?.recordingId, transcriptState?.segments],
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

    if (retainedPublication) {
      setPublicationSpeakers((previous) => previous?.recordingId === recording.id ? previous
        : { recordingId: recording.id, speakers: recording.speakers ?? [] });
    }
    setTranscriptState((prev) => {
      // A successful empty snapshot may have revision zero. Metadata remains
      // a fallback only until this recording's reader accepts a snapshot.
      if (prev && prev.recordingId === recording.id &&
        (prev.revision > 0 || acceptedSnapshotRecordingIdRef.current === recording.id || retainedPublication)) {
        transcriptStateRef.current = prev;
        return prev;
      }

      acceptedSnapshotRecordingIdRef.current = null;
      const nextState = createLocalTranscriptState(
        recording.id,
        isInFlightRecording ? [] : (recording.transcript?.segments || []),
        prev?.recordingId === recording.id ? prev.revision : 0,
        prev?.recordingId === recording.id ? prev.deferredById : {},
      );
      transcriptStateRef.current = nextState;
      return nextState;
    });
  }, [recording, isInFlightRecording, retainedPublication]);

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

  const readTranscript = useCallback(
    async (
      { recording, mode, options }: DetailTranscriptRead,
      isCurrent: () => boolean,
    ) => {
      const currentTranscriptState = transcriptStateRef.current;
      const afterRevision =
        mode === "delta" && currentTranscriptState?.recordingId === recording.id
          ? currentTranscriptState.revision
          : undefined;
      let transcriptDelta;
      if (authoritativeEmpty) {
        // A stalled handoff must not hold every retry behind the serial reader.
        // Racing also fences clients which do not honour AbortSignal promptly.
        const controller = new AbortController();
        let timeout: ReturnType<typeof setTimeout> | undefined;
        try {
          transcriptDelta = await Promise.race([
            getTranscriptUtterances(recording.id, afterRevision, controller.signal),
            new Promise<never>((_, reject) => {
              timeout = setTimeout(() => {
                controller.abort();
                reject(new Error("Settled transcript read timed out"));
              }, SETTLED_TRANSCRIPT_READ_TIMEOUT_MS);
            }),
          ]);
        } finally {
          clearTimeout(timeout);
        }
      } else {
        transcriptDelta = await getTranscriptUtterances(recording.id, afterRevision);
      }
      if (!isCurrent() || recordingRef.current?.id !== recording.id) return null;

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
        // Only a handoff treats an empty utterance snapshot as authoritative;
        // direct opens retain the established legacy metadata fallback.
        fallbackSegments: authoritativeEmpty ? [] : recording.transcript?.segments || [],
        delta: transcriptDelta,
        mode,
        activeEditUtteranceId:
          options.deferActiveEdit === false ? null : activeEditRef.current,
      });
      const nextSegments = nextState.segments;
      acceptedSnapshotRecordingIdRef.current = recording.id;
      transcriptStateRef.current = nextState;
      setTranscriptState(nextState);
      setPublicationSpeakers({ recordingId: recording.id, speakers: transcriptDelta.speakers });
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

      if (mode === "full") {
        fullSequenceRef.current = { recordingId: recording.id, sequence: recording.transcript?.change_sequence ?? 0,
          speakers: JSON.stringify(transcriptDelta.speakers) };
        setFullReadReady({ recordingId: recording.id, generation: readerGeneration });
      }

      return {
        revision: transcriptDelta.revision,
        segments: nextSegments,
        speakers: transcriptDelta.speakers,
      };
    },
    [setRecording, authoritativeEmpty, readerGeneration],
  );

  useEffect(() => {
    const reader = createDetailTranscriptReader(readTranscript);
    readerRef.current = reader;
    repairRef.current = null;
    fullSequenceRef.current = null;
    return () => {
      reader.cancel();
      if (readerRef.current === reader) readerRef.current = null;
    };
  }, [recording?.id, isInFlightRecording, publicationPending, readTranscript]);

  const syncTranscriptState = useCallback((
    mode: "full" | "delta" = "delta",
    options: { deferActiveEdit?: boolean } = {},
  ) => {
    const current = recordingRef.current;
    return current && !inFlightRef.current && readerRef.current
      ? readerRef.current.request({ recording: current, mode, options })
      : Promise.resolve(null);
  }, []);

  // The durable sequence is distinct from the utterance event cursor: a full
  // read repairs edits whose event committed behind that cursor. Failed reads
  // leave this sequence unacknowledged so the next existing status poll retries.
  const repairTranscriptSequence = useCallback((candidate: Recording, force = false) => {
    const reprocessPublication = candidate.previous_publication_available === true ||
      candidate.reprocess_publication_complete === true;
    // Marker changes recreate the reader and schedule their own full read.
    if ((!candidate.transcript?.settling_required && !reprocessPublication) ||
      (isRecordingInFlight(candidate) && !reprocessPublication) ||
      !!candidate.previous_publication_available !== !!recordingRef.current?.previous_publication_available ||
      recordingRef.current?.id !== candidate.id || inFlightRef.current ||
      !readerRef.current) return Promise.resolve(null);
    const sequence = candidate.transcript?.change_sequence ?? 0;
    const speakers = JSON.stringify(candidate.speakers ?? []);
    const repaired = fullSequenceRef.current;
    if (!force && repaired?.recordingId === candidate.id && repaired.sequence === sequence &&
      (!reprocessPublication || repaired.speakers === speakers)) return Promise.resolve(null);
    const pending = repairRef.current;
    if (pending?.recordingId === candidate.id && pending.sequence === sequence &&
      (!reprocessPublication || pending.speakers === speakers)) return pending.promise;
    const promise = readerRef.current.request({ recording: candidate, mode: "full", options: {} }).finally(() => {
      if (repairRef.current?.promise === promise) repairRef.current = null;
    });
    repairRef.current = { recordingId: candidate.id, sequence, speakers, promise };
    return promise;
  }, []);

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

    const initialRead = recording.transcript?.settling_required
      ? repairTranscriptSequence(recording, true)
      : syncTranscriptState("full");
    initialRead.catch((e) => {
      console.error("Failed to load transcript utterances:", e);
    });
    // syncTranscriptState intentionally runs once per recording load; later
    // delta polling and explicit mutation refreshes keep this state current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording?.id, isInFlightRecording, publicationPending]);

  useEffect(() => {
    if (!recording || isInFlightRecording || retainedPublication || !shouldPollRecordingUpdates(recording)) {
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
  }, [isInFlightRecording, recording, retainedPublication, syncTranscriptState]);

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

  const isSettledTranscriptReady = !isInFlightRecording &&
    fullReadReady?.recordingId === recording?.id && fullReadReady?.generation === readerGeneration;

  return {
    transcriptStateRef, setTranscriptState, transcriptSegments, deferredTranscriptUtteranceIds,
    liveSpeakerNames, history, future, isUndoing, syncTranscriptState, repairTranscriptSequence, pushTranscriptHistory,
    publicationSpeakers: publicationSpeakers && publicationSpeakers.recordingId === recording?.id
      ? publicationSpeakers.speakers : undefined,
    setActiveTranscriptEditId, handleUndo, handleRedo,
    isSettledTranscriptReady,
  };
}

