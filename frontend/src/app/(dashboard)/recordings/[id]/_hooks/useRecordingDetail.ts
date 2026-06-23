import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";

import {
  getRecording,
  getSettings,
  getTranscriptUtterances,
  updateSettings,
  updateSpeaker,
  updateTranscriptSegmentSpeaker,
  updateTranscriptUtteranceSpeaker,
  updateTranscriptSegmentText,
  updateTranscriptUtteranceText,
  findAndReplace,
  renameRecording,
  getGlobalSpeakers,
  updateSpeakerColor,
  generateNotes,
  updateNotes,
  updateUserNotes,
  updateMeetingEdgeFocus,
  exportContent,
  exportAudio,
  ExportContentType,
  ExportFormat,
} from "@/lib/api";
import {
  clampMeetingEdgeContextLevel,
  DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
} from "@/lib/meetingEdgeContext";
import { getErrorMessage, getErrorStatus, isAbortError } from "@/lib/errors";
import {
  Recording,
  RecordingStatus,
  TranscriptSegment,
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSpeakerAssignment,
} from "@/types";
import { useNotificationStore } from "@/lib/notificationStore";
import { useNavigationStore } from "@/lib/store";
import {
  buildMeetingSpeakerColors,
  buildGlobalSpeakerById,
  buildRecordingSpeakerDisplayMap,
  getRecordingSpeakerDisplayName,
  getResolvedGlobalSpeakerId,
} from "@/lib/recordingSpeakerUtils";
import {
  buildRollingSpeakerCorrectionHistory,
  buildSpeakerHistoryAssignment,
  diffTranscriptSegments,
  extendRollingSpeakerHistoryWithSegments,
  sortTranscriptSegments,
  type RollingSpeakerCorrectionHistory,
  type TranscriptSegmentChange,
} from "@/lib/transcriptSegments";
import {
  applyTranscriptDelta,
  createLocalTranscriptState,
  flushDeferredTranscriptState,
  type LocalTranscriptState,
} from "@/lib/transcriptState";
import { useDragSelectionLock } from "@/lib/useDragSelectionLock";
import { useViewportDensity } from "@/components/ViewportDensityProvider";

import {
  cloneTranscriptSegments,
  isDemoRecording,
  isRecordingInFlight,
  shouldPollRecordingUpdates,
  type TranscriptHistoryItem,
} from "./recordingDetailUtils";

interface UseRecordingDetailParams {
  params: Promise<{ id: string }>;
}

/**
 * Owns the entire data-orchestration, live-state, transcript, notes and action
 * logic for the recording detail page. Extracted verbatim from the original
 * page component so the page itself can stay focused on rendering. Behaviour is
 * preserved exactly.
 */
export function useRecordingDetail({ params }: UseRecordingDetailParams) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [globalSpeakers, setGlobalSpeakers] = useState<GlobalSpeaker[]>([]);
  const [meetingEdgeEnabled, setMeetingEdgeEnabled] = useState(true);
  const [meetingEdgeContextLevel, setMeetingEdgeContextLevel] = useState(
    DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
  );
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const audioRef = useRef<HTMLAudioElement>(null);
  const transcriptStateRef = useRef<LocalTranscriptState | null>(null);
  const { addNotification } = useNotificationStore();
  const { chatPanelHeight, setChatPanelHeight, activePanel, setActivePanel } = useNavigationStore();

  // Undo/Redo State
  const [history, setHistory] = useState<TranscriptHistoryItem[]>([]);
  const [future, setFuture] = useState<TranscriptHistoryItem[]>([]);
  const [isUndoing, setIsUndoing] = useState(false);
  const [transcriptState, setTranscriptState] = useState<LocalTranscriptState | null>(
    null,
  );
  const [activeTranscriptEditId, setActiveTranscriptEditId] = useState<
    string | null
  >(null);

  // Player State
  const [currentTime, setCurrentTime] = useState(0);
  const [stopTime, setStopTime] = useState<number | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);

  // Title Editing State
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");

  // Speaker Colors State
  const [speakerColors, setSpeakerColors] = useState<Record<string, string>>(
    {},
  );

  const [isGeneratingNotes, setIsGeneratingNotes] = useState(false);

  // Export Modal State
  const [showExportModal, setShowExportModal] = useState(false);


  // Mobile State
  const [isMobile, setIsMobile] = useState(false);
  const [isMobileChatOpen, setIsMobileChatOpen] = useState(false);
  const [isMobileHeaderActionsOpen, setIsMobileHeaderActionsOpen] = useState(false);
  const [isPanelResizing, setIsPanelResizing] = useState(false);
  const { isCompact } = useViewportDensity();
  useDragSelectionLock(isPanelResizing);

  // Notes History (separate from transcript history, can include null values)
  const [notesHistory, setNotesHistory] = useState<(string | null)[]>([]);
  const [notesFuture, setNotesFuture] = useState<(string | null)[]>([]);
  const lastNotesErrorRef = useRef<string | null>(null);
  const lastMeetingEdgeErrorRef = useRef<string | null>(null);
  const isInFlightRecording = isRecordingInFlight(recording);
  const compactChatPanelHeight = isCompact
    ? Math.min(chatPanelHeight, 42)
    : chatPanelHeight;
  const navigateToRecordings = useCallback(() => {
    router.push("/recordings");
  }, [router]);

  const transcriptSegments = useMemo(
    () => transcriptState?.segments || recording?.transcript?.segments || [],
    [recording?.transcript?.segments, transcriptState?.segments],
  );
  const deferredTranscriptUtteranceIds = useMemo(
    () => Object.keys(transcriptState?.deferredById || {}),
    [transcriptState?.deferredById],
  );

  const globalSpeakerById = useMemo(
    () => buildGlobalSpeakerById(globalSpeakers),
    [globalSpeakers],
  );

  const getSpeakerDisplayName = useCallback(
    (speaker: RecordingSpeaker | undefined) => {
      if (!speaker) {
        return "";
      }

      return getRecordingSpeakerDisplayName(speaker, globalSpeakerById);
    },
    [globalSpeakerById],
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
      if (!recording) {
        return null;
      }

      const currentTranscriptState = transcriptStateRef.current;
      const afterRevision =
        mode === "delta" && currentTranscriptState?.recordingId === recording.id
          ? currentTranscriptState.revision
          : undefined;
      const transcriptDelta = await getTranscriptUtterances(
        recording.id,
        afterRevision,
      );

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
    [activeTranscriptEditId, recording],
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

  const fetchRecording = useCallback(async () => {
    try {
      const { id } = await params;
      const [recData, gsData, settingsData] = await Promise.all([
        getRecording(id),
        getGlobalSpeakers(),
        getSettings().catch(() => null),
      ]);
      setRecording(recData);
      setGlobalSpeakers(gsData);
      if (settingsData) {
        setMeetingEdgeEnabled(settingsData.enable_meeting_edge !== false);
        setMeetingEdgeContextLevel(
          clampMeetingEdgeContextLevel(settingsData.meeting_edge_context_level),
        );
      }
      // Only set title if not editing, or on first load
      if (!isEditingTitle) {
        setTitleValue(recData.name);
      }
      return recData;

        } catch (e: unknown) {
      console.error("Failed to fetch recording:", e);
      addNotification({
        type: "error",
        message: "Failed to load recording.",
      });
      router.push("/recordings");
      return null;
    } finally {
      setLoading(false);
    }
  }, [addNotification, params, isEditingTitle, router]);

  const refreshRecordingView = useCallback(async () => {
    const refreshedRecording = await fetchRecording();

    if (!refreshedRecording?.id || isRecordingInFlight(refreshedRecording)) {
      return;
    }

    await syncTranscriptState("full").catch((e) => {
      console.error("Failed to refresh transcript state:", e);
    });
  }, [fetchRecording, syncTranscriptState]);

  useEffect(() => {
    fetchRecording();
  }, [fetchRecording]);

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
    const checkMobile = () => setIsMobile(window.innerWidth < 1024);
    // Initial check
    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, [setActivePanel]);

  useEffect(() => {
    if (!isMobile) {
      setIsMobileHeaderActionsOpen(false);
    }
  }, [isMobile]);

  useEffect(() => {
    if (!recording) return;

    if (!shouldPollRecordingUpdates(recording)) {
      return;
    }

    const pollIntervalMs =
      recording.status === RecordingStatus.UPLOADING ||
      recording.status === RecordingStatus.PAUSED ||
      (recording.status === RecordingStatus.PROCESSED &&
        recording.has_proxy === false &&
        !isDemoRecording(recording))
        ? 1000
        : 3000;

    const interval = setInterval(async () => {
      try {
        const { id } = await params;
        const data = await getRecording(id);

        const meetingEdgeSignature = (rec: Recording | null) =>
          JSON.stringify({
            focus: rec?.transcript?.meeting_edge_focus ?? null,
            status: rec?.transcript?.meeting_edge_status ?? null,
            error: rec?.transcript?.meeting_edge_error_message ?? null,
            payload: rec?.transcript?.meeting_edge_payload ?? null,
          });

        if (
          data.status !== recording.status ||
          data.client_status !== recording.client_status ||
          data.processing_step !== recording.processing_step ||
          data.upload_progress !== recording.upload_progress ||
          data.processing_progress !== recording.processing_progress ||
          data.processing_eta_seconds !== recording.processing_eta_seconds ||
          data.processing_eta_learning !== recording.processing_eta_learning ||
          data.processing_eta_sample_size !== recording.processing_eta_sample_size ||
          data.has_proxy !== recording.has_proxy ||
          data.transcript?.notes_status !== recording.transcript?.notes_status ||
          data.transcript?.notes !== recording.transcript?.notes ||
          data.transcript?.user_notes !== recording.transcript?.user_notes ||
          meetingEdgeSignature(data) !== meetingEdgeSignature(recording) ||
          JSON.stringify(data.speakers) !== JSON.stringify(recording.speakers)
        ) {
          setRecording(data);
          if (!isEditingTitle) setTitleValue(data.name);
        }

            } catch (e: unknown) {
        console.error("Polling failed", e);
        if (getErrorStatus(e) === 404) {
          addNotification({
            type: "info",
            message: "Recording was discarded or deleted.",
          });
          router.push("/recordings");
        }
      }
    }, pollIntervalMs);

    return () => clearInterval(interval);
  }, [params, recording, isEditingTitle, addNotification, router]);

  useEffect(() => {
    if (!recording || isInFlightRecording || !shouldPollRecordingUpdates(recording)) {
      return;
    }

    const pollIntervalMs =
      recording.status === RecordingStatus.UPLOADING ||
      recording.status === RecordingStatus.PAUSED
        ? 1000
        : 3000;

    const interval = setInterval(() => {
      syncTranscriptState("delta").catch((e) => {
        console.error("Transcript polling failed", e);
      });
    }, pollIntervalMs);

    return () => clearInterval(interval);
  }, [isInFlightRecording, recording, syncTranscriptState]);

  // Listen for recording updates (e.g. from Sidebar retry or rename)
  useEffect(() => {
    const handleUpdate = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (recording && customEvent.detail?.id === recording.id) {
        if (customEvent.detail.name) {
          // Optimistic update
          setRecording((prev) =>
            prev ? { ...prev, name: customEvent.detail.name } : null,
          );
          if (!isEditingTitle) setTitleValue(customEvent.detail.name);
        } else {
          // Force refresh for other updates (like status change or speaker inference)
          refreshRecordingView().catch(console.error);
        }
      }
    };

    window.addEventListener("recording-updated", handleUpdate);
    return () => window.removeEventListener("recording-updated", handleUpdate);
  }, [recording, isEditingTitle, refreshRecordingView]);

  // Listen for tour events to switch panels
  useEffect(() => {
    const handleTourSwitch = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (
        customEvent.detail === "notes" ||
        customEvent.detail === "transcript"
      ) {
        setActivePanel(customEvent.detail);
      }
    };

    window.addEventListener("tour:switch-panel", handleTourSwitch);
    return () =>
      window.removeEventListener("tour:switch-panel", handleTourSwitch);
  }, [setActivePanel]);

  // Initialize speaker colors
  useEffect(() => {
    if (!recording) return;

    setSpeakerColors(
      buildMeetingSpeakerColors({
        segments: transcriptSegments,
        speakers: recording.speakers,
        existingColors: speakerColors,
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording, transcriptSegments]);

  useEffect(() => {
    const notesError =
      recording?.transcript?.notes_status === "error"
        ? recording.transcript?.error_message ||
          "Meeting notes could not be generated. Configure an AI provider and model in Settings, then try again."
        : null;

    if (notesError && notesError !== lastNotesErrorRef.current) {
      addNotification({ type: "error", message: notesError });
      lastNotesErrorRef.current = notesError;
    }

    if (!notesError) {
      lastNotesErrorRef.current = null;
    }
  }, [addNotification, recording?.transcript?.error_message, recording?.transcript?.notes_status]);

  useEffect(() => {
    const meetingEdgeError =
      recording?.transcript?.meeting_edge_status === "error"
        ? recording.transcript?.meeting_edge_error_message ||
          "Meeting Edge is temporarily unavailable."
        : null;

    if (meetingEdgeError && meetingEdgeError !== lastMeetingEdgeErrorRef.current) {
      addNotification({ type: "error", message: meetingEdgeError });
      lastMeetingEdgeErrorRef.current = meetingEdgeError;
    }

    if (!meetingEdgeError) {
      lastMeetingEdgeErrorRef.current = null;
    }
  }, [
    addNotification,
    recording?.transcript?.meeting_edge_error_message,
    recording?.transcript?.meeting_edge_status,
  ]);

  // Player Handlers
  const handleTimeUpdate = () => {
    if (audioRef.current) {
      const current = audioRef.current.currentTime;
      setCurrentTime(current);

      if (stopTime !== null && current >= stopTime) {
        audioRef.current.pause();
        setStopTime(null);
      }
    }
  };

  const handlePlaySegment = async (start: number, end?: number) => {
    if (audioRef.current) {
      try {
        // Optimistic update for instant UI response (scrolling)
        setCurrentTime(start);

        // Pause first to interrupt any pending play requests
        audioRef.current.pause();

        audioRef.current.currentTime = start;
        if (end) setStopTime(end);
        else setStopTime(null);

        await audioRef.current.play();

            } catch (err: unknown) {
        // Ignore AbortError which happens when play() is interrupted by another play() or pause()
        if (!isAbortError(err)) {
          console.error("Playback failed:", err);
        }
      }
    }
  };

  const handlePause = () => {
    if (audioRef.current) {
      audioRef.current.pause();
    }
  };

  const handleResume = () => {
    if (audioRef.current) {
      audioRef.current.play();
    }
  };

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

  // Transcript Handlers
  const handleRenameSpeaker = async (label: string, newName: string) => {
    if (!recording) return;
    try {
      await updateSpeaker(recording.id, label, newName);
      const updated = await getRecording(recording.id);
      setRecording(updated);
      await syncTranscriptState("full");

        } catch (error: unknown) {
      console.error("Failed to rename speaker:", error);
      addNotification({
        type: "error",
        message: "Failed to rename speaker. Please try again.",
      });
    }
  };

  const handleUpdateSegmentSpeaker = async (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => {
    if (!recording) return;

    const previousSegments = cloneTranscriptSegments(transcriptSegments);

    const nextAssignment = {
      ...assignment,
      name: assignment.name.trim(),
      scope: assignment.scope || "speaker_everywhere_in_recording",
    };

    if (!nextAssignment.name) {
      return;
    }

    const currentSpeaker = recording.speakers?.find(
      (speaker) => speaker.diarization_label === segment.speaker,
    );
    const currentGlobalSpeakerId = currentSpeaker
      ? getResolvedGlobalSpeakerId(currentSpeaker)
      : undefined;
    const currentSpeakerName = currentSpeaker
      ? getSpeakerDisplayName(currentSpeaker)
      : segment.speaker;

    const isSameSelection =
      nextAssignment.diarizationLabel === segment.speaker ||
      (nextAssignment.globalSpeakerId !== undefined &&
        nextAssignment.globalSpeakerId === currentGlobalSpeakerId) ||
      (nextAssignment.diarizationLabel === undefined &&
        nextAssignment.globalSpeakerId === undefined &&
        nextAssignment.name === currentSpeakerName);

    if (isSameSelection) {
      return;
    }

    try {
      if (segment.id) {
        await updateTranscriptUtteranceSpeaker(
          recording.id,
          segment.id,
          nextAssignment,
        );
        const syncResult = await syncTranscriptState("delta", {
          deferActiveEdit: false,
        });
        if (syncResult?.segments) {
          const updatedSegment = syncResult.segments.find(
            (nextSegment) => nextSegment.id === segment.id,
          );
          pushTranscriptHistory(
            `Change speaker ${segment.id}`,
            previousSegments,
            syncResult.segments,
            nextAssignment.scope === "speaker_everywhere_in_recording"
              ? buildRollingSpeakerCorrectionHistory({
                  previousSegments,
                  sourceSegment: segment,
                  updatedSegment,
                })
              : undefined,
          );
        }
      } else {
        const segmentIndex = transcriptSegments.indexOf(segment);
        if (segmentIndex === -1) {
          return;
        }

        await updateTranscriptSegmentSpeaker(
          recording.id,
          segmentIndex,
          nextAssignment,
        );
        const updated = await getRecording(recording.id);
        setRecording(updated);
        const nextSegments = sortTranscriptSegments(updated.transcript?.segments || []);
        setTranscriptState({
          recordingId: updated.id,
          revision:
            transcriptStateRef.current?.recordingId === updated.id
              ? transcriptStateRef.current.revision
              : 0,
          segments: nextSegments,
          deferredById: {},
        });
        pushTranscriptHistory(
          `Change speaker ${segmentIndex}`,
          previousSegments,
          nextSegments,
        );
      }

        } catch (error: unknown) {
      console.error("Failed to update segment speaker:", error);
      addNotification({
        type: "error",
        message: "Failed to update segment speaker. Please try again.",
      });
    }
  };

  const handleUpdateSegmentText = async (
    segment: TranscriptSegment,
    text: string,
  ) => {
    if (!recording) return;

    const previousSegments = cloneTranscriptSegments(transcriptSegments);

    try {
      if (segment.id) {
        await updateTranscriptUtteranceText(
          recording.id,
          segment.id,
          text,
          segment.revision,
        );
        const syncResult = await syncTranscriptState("delta", {
          deferActiveEdit: false,
        });
        if (syncResult?.segments) {
          pushTranscriptHistory(
            `Edit text ${segment.id}`,
            previousSegments,
            syncResult.segments,
          );
        }
        return;
      }

      const segmentIndex = transcriptSegments.indexOf(segment);
      if (segmentIndex === -1) {
        return;
      }

      await updateTranscriptSegmentText(recording.id, segmentIndex, text);
      const updated = await getRecording(recording.id);
      setRecording(updated);
      const nextSegments = sortTranscriptSegments(updated.transcript?.segments || []);
      setTranscriptState({
        recordingId: updated.id,
        revision:
          transcriptStateRef.current?.recordingId === updated.id
            ? transcriptStateRef.current.revision
            : 0,
        segments: nextSegments,
        deferredById: {},
      });
      pushTranscriptHistory(
        `Edit text ${segmentIndex}`,
        previousSegments,
        nextSegments,
      );

        } catch (error: unknown) {
      console.error("Failed to update segment text:", error);
      addNotification({ type: "error", message: "Failed to update segment text." });
    }
  };

  const handleGlobalFindAndReplace = async (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => {
    if (!recording) return;

    try {
      await findAndReplace(recording.id, find, replace, options);

      const [updated] = await Promise.all([
        getRecording(recording.id),
        syncTranscriptState("full"),
      ]);
      setRecording(updated);

        } catch (error: unknown) {
      console.error("Failed to find and replace:", error);
      addNotification({ type: "error", message: "Failed to find and replace." });
    }
  };

  const handleTitleSubmit = async () => {
    if (!recording || !titleValue.trim()) {
      setIsEditingTitle(false);
      setTitleValue(recording?.name || "");
      return;
    }

    if (titleValue.trim() === recording.name) {
      setIsEditingTitle(false);
      return;
    }

    try {
      await renameRecording(recording.id, titleValue.trim());
      const updated = await getRecording(recording.id);
      setRecording(updated);
      setIsEditingTitle(false);
      router.refresh();

        } catch (e: unknown) {
      console.error("Failed to rename recording:", e);
      addNotification({ type: "error", message: "Failed to rename recording." });
    }
  };

  const handleColorChange = async (speakerLabel: string, colorKey: string) => {
    // Resolve label if it's a name
    let targetLabel = speakerLabel;
    if (recording?.speakers) {
      const speaker = recording.speakers.find(
        (s) =>
          s.diarization_label === speakerLabel ||
          s.name === speakerLabel ||
          s.local_name === speakerLabel ||
          s.global_speaker?.name === speakerLabel,
      );
      if (speaker) {
        targetLabel = speaker.diarization_label;
      }
    }

    setSpeakerColors((prev) => ({
      ...prev,
      [speakerLabel]: colorKey,
      [targetLabel]: colorKey, // Ensure both are updated
    }));

    if (recording) {
      try {
        await updateSpeakerColor(recording.id, targetLabel, colorKey);
        // Refresh recording to get updated speaker data
        const updated = await getRecording(recording.id);
        setRecording(updated);

            } catch (e: unknown) {
        console.error("Failed to update speaker color", e);
        addNotification({ type: "error", message: "Failed to update speaker color." });
      }
    }
  };

  // Notes Handlers
  const handleGenerateNotes = async () => {
    if (!recording) return;
    setIsGeneratingNotes(true);
    try {
      await generateNotes(recording.id);
      const updated = await getRecording(recording.id);
      setRecording(updated);
      setActivePanel("notes"); // Switch to notes panel after generation

        } catch (e: unknown) {
      console.error("Failed to generate notes:", e);
      try {
        const updated = await getRecording(recording.id);
        setRecording(updated);
        setActivePanel("notes");

            } catch (refreshError: unknown) {
        console.error("Failed to refresh recording after notes error:", refreshError);
      }
      addNotification({
        type: "error",
        message:
          getErrorMessage(e, "Failed to generate notes. Configure an AI provider and model in Settings, then try again."),
      });
    } finally {
      setIsGeneratingNotes(false);
    }
  };

  const handleNotesChange = async (notes: string) => {
    if (!recording) return;

    // Always push current notes to history (even if null) before making changes
    setNotesHistory((prev) => [...prev, recording.transcript?.notes ?? null]);
    setNotesFuture([]); // Clear redo stack

    try {
      await updateNotes(recording.id, notes);
      const updated = await getRecording(recording.id);
      setRecording(updated);

        } catch (e: unknown) {
      console.error("Failed to update notes:", e);
      addNotification({ type: "error", message: "Failed to update notes." });
    }
  };

  const handleNotesUndo = async () => {
    if (notesHistory.length === 0 || !recording) return;

    const previousNotes = notesHistory[notesHistory.length - 1];
    const currentNotes = recording.transcript?.notes ?? null;

    setNotesFuture((prev) => [currentNotes, ...prev]);
    setNotesHistory((prev) => prev.slice(0, -1));

    try {
      await updateNotes(recording.id, previousNotes || "");
      const updated = await getRecording(recording.id);
      setRecording(updated);

        } catch (e: unknown) {
      console.error("Notes undo failed:", e);
      addNotification({ type: "error", message: "Failed to undo notes changes." });
    }
  };

  const handleNotesRedo = async () => {
    if (notesFuture.length === 0 || !recording) return;

    const nextNotes = notesFuture[0];
    const currentNotes = recording.transcript?.notes ?? null;

    setNotesHistory((prev) => [...prev, currentNotes]);
    setNotesFuture((prev) => prev.slice(1));

    try {
      await updateNotes(recording.id, nextNotes || "");
      const updated = await getRecording(recording.id);
      setRecording(updated);

        } catch (e: unknown) {
      console.error("Notes redo failed:", e);
      addNotification({ type: "error", message: "Failed to redo notes changes." });
    }
  };

  const handleProcessingNotesChange = useCallback(async (notes: string) => {
    if (!recording?.id) return;

    await updateUserNotes(recording.id, notes);
  }, [recording?.id]);

  const handleMeetingEdgeFocusChange = useCallback(async (focus: string) => {
    if (!recording?.id) return;

    await updateMeetingEdgeFocus(recording.id, focus);
  }, [recording?.id]);

  const handleMeetingEdgeContextLevelChange = useCallback(
    async (level: number) => {
      const previousLevel = meetingEdgeContextLevel;
      setMeetingEdgeContextLevel(level);

      try {
        await updateSettings({ meeting_edge_context_level: level });

            } catch (error: unknown) {
        setMeetingEdgeContextLevel(previousLevel);
        throw error;
      }
    },
    [meetingEdgeContextLevel],
  );

  const handleExport = async (
    contentType: ExportContentType,
    format: ExportFormat,
  ) => {
    if (!recording) return;
    try {
      if (contentType === "audio") {
        await exportAudio(recording.id, recording.name);
      } else {
        await exportContent(recording.id, contentType, format);
      }

        } catch (error: unknown) {
      console.error("Export failed:", error);
      addNotification({
        type: "error",
        message: "Export failed. Please check the logs.",
      });
    }
  };

  const speakerMap = useMemo(() => {
    return buildRecordingSpeakerDisplayMap(recording?.speakers, globalSpeakerById);
  }, [globalSpeakerById, recording?.speakers]);

  return {
    // Core data
    recording,
    setRecording,
    globalSpeakers,
    loading,
    meetingEdgeEnabled,
    meetingEdgeContextLevel,
    // Derived
    isInFlightRecording,
    transcriptSegments,
    deferredTranscriptUtteranceIds,
    speakerMap,
    speakerColors,
    history,
    future,
    isUndoing,
    isGeneratingNotes,
    notesHistory,
    notesFuture,
    // Player
    audioRef,
    currentTime,
    isPlaying,
    setIsPlaying,
    // Title editing
    isEditingTitle,
    setIsEditingTitle,
    titleValue,
    setTitleValue,
    // Panels / layout
    activePanel,
    setActivePanel,
    chatPanelHeight,
    setChatPanelHeight,
    compactChatPanelHeight,
    isCompact,
    showExportModal,
    setShowExportModal,
    // Mobile
    isMobile,
    isMobileChatOpen,
    setIsMobileChatOpen,
    isMobileHeaderActionsOpen,
    setIsMobileHeaderActionsOpen,
    setIsPanelResizing,
    // Navigation
    navigateToRecordings,
    // Handlers
    fetchRecording,
    refreshRecordingView,
    handleTimeUpdate,
    handlePlaySegment,
    handlePause,
    handleResume,
    handleUndo,
    handleRedo,
    handleRenameSpeaker,
    handleUpdateSegmentSpeaker,
    handleUpdateSegmentText,
    handleGlobalFindAndReplace,
    handleTitleSubmit,
    handleColorChange,
    handleGenerateNotes,
    handleNotesChange,
    handleNotesUndo,
    handleNotesRedo,
    handleProcessingNotesChange,
    handleMeetingEdgeFocusChange,
    handleMeetingEdgeContextLevelChange,
    handleExport,
    setActiveTranscriptEditId,
  };
}
