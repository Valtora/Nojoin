"use client";

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
import ChatPanel from "@/components/ChatPanel";
import AudioPlayer from "@/components/AudioPlayer";
import SpeakerPanel from "@/components/SpeakerPanel";
import TranscriptView from "@/components/TranscriptView";
import NotesView from "@/components/NotesView";
import DocumentsView from "@/components/DocumentsView";
import RecordingStatusDisplay from "@/components/RecordingStatusDisplay";
import ExportModal from "@/components/ExportModal";
import ReprocessDialog from "@/components/ReprocessDialog";
import RecordingTagEditor from "@/components/RecordingTagEditor";
import LinkedEventPanel from "@/components/LinkedEventPanel";
import { ArrowLeft, Edit2, MessageSquare, MoreHorizontal, RefreshCw } from "lucide-react";
import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  Recording,
  RecordingStatus,
  TranscriptSegment,
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSpeakerAssignment,
} from "@/types";
import { useRouter } from "next/navigation";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
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

const isDemoRecording = (recording: Recording) =>
  recording.name === "Welcome to Nojoin";

const shouldPollRecordingUpdates = (recording: Recording) => {
  const waitingForProxy =
    recording.status === RecordingStatus.PROCESSED &&
    recording.has_proxy === false &&
    !isDemoRecording(recording);

  return (
    recording.status === RecordingStatus.PROCESSING ||
    recording.status === RecordingStatus.UPLOADING ||
    recording.status === RecordingStatus.PAUSED ||
    recording.status === RecordingStatus.QUEUED ||
    recording.transcript?.notes_status === "generating" ||
    recording.transcript?.meeting_edge_status === "updating" ||
    waitingForProxy
  );
};

const isRecordingInFlight = (recording: Recording | null | undefined) =>
  recording?.status === RecordingStatus.PAUSED ||
  recording?.status === RecordingStatus.UPLOADING ||
  recording?.status === RecordingStatus.PROCESSING ||
  recording?.status === RecordingStatus.QUEUED;

const getAutoSpeakerReplacementName = (speakerName: string) => {
  const trimmedName = speakerName.trim();
  const nameParts = trimmedName.split(/\s+/).filter(Boolean);

  if (nameParts.length > 1) {
    return nameParts[0];
  }

  return trimmedName;
};

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
}

interface TranscriptHistoryItem {
  patches: TranscriptSegmentChange[];
  description: string;
  rollingSpeakerCorrection?: RollingSpeakerCorrectionHistory;
}

const cloneTranscriptSegments = (segments: TranscriptSegment[]): TranscriptSegment[] => {
  return JSON.parse(JSON.stringify(segments)) as TranscriptSegment[];
};

export default function RecordingPage({ params }: PageProps) {
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

  // Reprocess Dialog State
  const [showReprocessDialog, setShowReprocessDialog] = useState(false);

  // Mobile State
  const [isMobile, setIsMobile] = useState(false);
  const [isMobileChatOpen, setIsMobileChatOpen] = useState(false);
  const [isMobileHeaderActionsOpen, setIsMobileHeaderActionsOpen] = useState(false);

  // Notes History (separate from transcript history, can include null values)
  const [notesHistory, setNotesHistory] = useState<(string | null)[]>([]);
  const [notesFuture, setNotesFuture] = useState<(string | null)[]>([]);
  const lastNotesErrorRef = useRef<string | null>(null);
  const lastMeetingEdgeErrorRef = useRef<string | null>(null);
  const isInFlightRecording = isRecordingInFlight(recording);
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
    } catch (e) {
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
      } catch (e: any) {
        console.error("Polling failed", e);
        if (e.response && e.response.status === 404) {
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
      } catch (err: any) {
        // Ignore AbortError which happens when play() is interrupted by another play() or pause()
        if (err.name !== "AbortError") {
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
    } catch (e) {
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
    } catch (e) {
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
    } catch (error) {
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
    } catch (error) {
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
    } catch (error) {
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
    } catch (error) {
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
    } catch (e) {
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
      } catch (e) {
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
    } catch (e: any) {
      console.error("Failed to generate notes:", e);
      try {
        const updated = await getRecording(recording.id);
        setRecording(updated);
        setActivePanel("notes");
      } catch (refreshError) {
        console.error("Failed to refresh recording after notes error:", refreshError);
      }
      addNotification({
        type: "error",
        message:
          e.response?.data?.detail ||
          "Failed to generate notes. Configure an AI provider and model in Settings, then try again.",
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
    } catch (e) {
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
    } catch (e) {
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
    } catch (e) {
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
      } catch (error) {
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
    } catch (error: any) {
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

  const renderMobileHeaderActions = () => (
    <div className="flex flex-wrap items-center gap-2">
      <RecordingTagEditor
        recordingId={recording!.id}
        tags={recording!.tags || []}
        compact
        onTagsUpdated={() => {
          getRecording(recording!.id)
            .then(setRecording)
            .catch(console.error);
        }}
      />
      <LinkedEventPanel
        recordingId={recording!.id}
        linkedEvent={recording!.calendar_event}
        compact
        onLinkChanged={() => {
          getRecording(recording!.id)
            .then(setRecording)
            .catch(console.error);
        }}
      />
      {recording &&
        (recording.status === RecordingStatus.PROCESSED ||
          recording.status === RecordingStatus.ERROR) && (
            <button
              onClick={() => setShowReprocessDialog(true)}
              className="flex items-center gap-2 rounded-xl border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
              title="Reprocess this recording at higher quality"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Reprocess
            </button>
          )}
    </div>
  );

  // View Components
  const renderMainContent = () => (
    <div className="flex-1 flex flex-col min-h-0 h-full">
      {isMobile ? (
        <div className="pointer-events-none fixed inset-x-0 top-0 z-40 flex items-start justify-between px-4 pt-[calc(env(safe-area-inset-top)+0.75rem)] lg:hidden">
          <button
            onClick={() => router.push("/recordings")}
            className="pointer-events-auto inline-flex h-12 shrink-0 items-center gap-2 rounded-2xl border border-gray-200 bg-white/90 px-4 text-sm font-medium text-gray-700 shadow-lg shadow-black/10 backdrop-blur-sm transition-colors hover:bg-white dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-800 dark:shadow-black/30"
            title="Back to Recordings"
            aria-label="Back to Recordings"
          >
            <ArrowLeft className="h-4 w-4" />
            <span>Back</span>
          </button>

          <button
            onClick={() => setIsMobileHeaderActionsOpen((current) => !current)}
            className={`pointer-events-auto inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border shadow-lg shadow-black/10 backdrop-blur-sm transition-colors dark:shadow-black/30 ${isMobileHeaderActionsOpen ? "border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-500/30 dark:bg-orange-500/10 dark:text-orange-300" : "border-gray-200 bg-white/90 text-gray-700 hover:bg-white dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-800"}`}
            title={isMobileHeaderActionsOpen ? "Hide meeting actions" : "Show meeting actions"}
            aria-label={isMobileHeaderActionsOpen ? "Hide meeting actions" : "Show meeting actions"}
          >
            <MoreHorizontal className="h-5 w-5" />
          </button>
        </div>
      ) : null}

      {/* Header (Title, Tags, Audio Player) */}
      <header className={`sticky top-0 z-10 shrink-0 border-b-2 border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900 ${isMobile ? "space-y-3 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+4.75rem)]" : "space-y-4 p-4 md:p-6"}`}>
        {isMobile ? (
          <>
            <div className="rounded-2xl border border-gray-200/80 bg-white/90 px-4 py-3 shadow-sm backdrop-blur dark:border-gray-700/80 dark:bg-gray-800/90">
              <div className="min-w-0 pt-0.5">
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-400 dark:text-gray-500">
                  Meeting Detail
                </div>
                {isEditingTitle ? (
                  <input
                    autoFocus
                    type="text"
                    value={titleValue}
                    onChange={(e) => setTitleValue(e.target.value)}
                    onBlur={handleTitleSubmit}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleTitleSubmit();
                      if (e.key === "Escape") {
                        setIsEditingTitle(false);
                        setTitleValue(recording?.name || "");
                      }
                    }}
                    className="mt-1 w-full border-b-2 border-orange-500 bg-transparent pb-1 text-lg font-bold text-gray-900 focus:outline-none dark:text-white"
                  />
                ) : (
                  <h1
                    className="mt-1 flex cursor-pointer items-start gap-2 text-lg font-bold text-gray-900 hover:text-orange-600 dark:text-white dark:hover:text-orange-400 group"
                    onClick={() => setIsEditingTitle(true)}
                    title="Click to rename"
                  >
                    <span className="min-w-0 break-words">{recording?.name}</span>
                    <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" />
                  </h1>
                )}
              </div>
            </div>

            {isMobileHeaderActionsOpen && (
              <div className="fixed right-4 top-[calc(env(safe-area-inset-top)+4.5rem)] z-40 w-[min(18rem,calc(100vw-2rem))] rounded-2xl border border-orange-100 bg-orange-50/95 p-2.5 shadow-xl shadow-black/10 backdrop-blur dark:border-orange-500/15 dark:bg-orange-500/10 dark:shadow-black/30">
                {renderMobileHeaderActions()}
              </div>
            )}
          </>
        ) : (
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0 flex-1">
              {isEditingTitle ? (
                <input
                  autoFocus
                  type="text"
                  value={titleValue}
                  onChange={(e) => setTitleValue(e.target.value)}
                  onBlur={handleTitleSubmit}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleTitleSubmit();
                    if (e.key === "Escape") {
                      setIsEditingTitle(false);
                      setTitleValue(recording?.name || "");
                    }
                  }}
                  className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white mb-2 w-full bg-transparent border-b-2 border-orange-500 focus:outline-none"
                />
              ) : (
                <h1
                  className="mb-2 flex cursor-pointer items-start gap-2 text-xl font-bold text-gray-900 hover:text-orange-600 dark:text-white dark:hover:text-orange-400 group md:text-2xl"
                  onClick={() => setIsEditingTitle(true)}
                  title="Click to rename"
                >
                  <span className="min-w-0 break-words md:truncate">
                    {recording?.name}
                  </span>
                  <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" />
                </h1>
              )}

              <div className="flex flex-col items-start gap-2">
                <RecordingTagEditor
                  recordingId={recording!.id}
                  tags={recording!.tags || []}
                  onTagsUpdated={() => {
                    getRecording(recording!.id)
                      .then(setRecording)
                      .catch(console.error);
                  }}
                />
                <LinkedEventPanel
                  recordingId={recording!.id}
                  linkedEvent={recording!.calendar_event}
                  onLinkChanged={() => {
                    getRecording(recording!.id)
                      .then(setRecording)
                      .catch(console.error);
                  }}
                />
              </div>
            </div>

            {recording &&
              (recording.status === RecordingStatus.PROCESSED ||
                recording.status === RecordingStatus.ERROR) && (
                  <div className="flex items-center gap-2 shrink-0 md:pt-1">
                  <button
                    onClick={() => setShowReprocessDialog(true)}
                    className="flex items-center gap-2 px-3 py-2 text-sm bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors shadow-sm"
                    title="Reprocess this recording at higher quality"
                  >
                    <RefreshCw className="w-4 h-4" />
                    Reprocess
                  </button>
                </div>
              )}
          </div>
        )}

        {/* Audio Player in Header */}
        {recording && 
          recording.status !== RecordingStatus.PAUSED &&
         recording.status !== RecordingStatus.UPLOADING &&
         recording.status !== RecordingStatus.PROCESSING &&
         recording.status !== RecordingStatus.QUEUED && (
            <AudioPlayer
              recording={recording}
              audioRef={audioRef}
              currentTime={currentTime}
              onTimeUpdate={handleTimeUpdate}
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              compact={isMobile}
            />
          )}
      </header>

      {/* Panel Tabs */}
      <div className="shrink-0 bg-gray-50 dark:bg-gray-900">
        <div className="grid grid-cols-3 border-b-2 border-gray-200 dark:border-gray-700">
          <button
            id="tab-transcript"
            onClick={() => setActivePanel("transcript")}
            className={`flex min-w-0 items-center justify-center border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors md:px-6 md:py-3 md:text-sm ${
              activePanel === "transcript"
                ? "border-orange-500 text-orange-600 dark:text-orange-400 bg-white dark:bg-gray-800"
                : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
          >
            <span className="truncate">Transcript</span>
          </button>
          <button
            id="tab-notes"
            onClick={() => setActivePanel("notes")}
            className={`flex min-w-0 items-center justify-center border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors md:px-6 md:py-3 md:text-sm ${
              activePanel === "notes"
                ? "border-orange-500 text-orange-600 dark:text-orange-400 bg-white dark:bg-gray-800"
                : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
          >
            <span className="truncate">Notes</span>
          </button>
          <button
            id="tab-documents"
            onClick={() => setActivePanel("documents")}
            className={`flex min-w-0 items-center justify-center border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors md:px-6 md:py-3 md:text-sm ${
              activePanel === "documents"
                ? "border-orange-500 text-orange-600 dark:text-orange-400 bg-white dark:bg-gray-800"
                : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
          >
            <span className="truncate">Documents</span>
          </button>
        </div>
      </div>

      {/* Panel Content */}
      <div className="flex-1 flex flex-col bg-white dark:bg-gray-800 overflow-hidden min-h-0 h-full relative">
        <div
          className={`absolute inset-0 flex flex-col ${activePanel === "transcript" ? "z-10 visible" : "z-0 invisible"}`}
        >
          {recording && transcriptSegments.length > 0 ? (
            <TranscriptView
              recordingId={recording.id}
              segments={transcriptSegments}
              currentTime={currentTime}
              onPlaySegment={handlePlaySegment}
              isPlaying={isPlaying}
              onPause={handlePause}
              onResume={handleResume}
              speakerMap={speakerMap}
              speakers={recording.speakers || []}
              globalSpeakers={globalSpeakers}
              onRenameSpeaker={handleRenameSpeaker}
              onUpdateSegmentSpeaker={handleUpdateSegmentSpeaker}
              onUpdateSegmentText={handleUpdateSegmentText}
              onFindAndReplace={handleGlobalFindAndReplace}
              speakerColors={speakerColors}
              onUndo={handleUndo}
              onRedo={handleRedo}
              canUndo={history.length > 0 && !isUndoing}
              canRedo={future.length > 0 && !isUndoing}
              onExport={() => setShowExportModal(true)}
              onActiveEditUtteranceChange={setActiveTranscriptEditId}
              pendingRemoteUtteranceIds={deferredTranscriptUtteranceIds}
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full p-6 text-center space-y-4">
              {recording?.transcript?.text ? (
                <>
                  <div className="p-4 rounded-lg bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 max-w-md">
                    <p className="text-lg font-medium text-gray-700 dark:text-gray-300">
                      {recording.transcript.text.replace(/[\[\]]/g, "")}
                    </p>
                  </div>
                  <p className="text-sm text-gray-500 dark:text-gray-400">
                    The audio file was processed, but no speech segments were
                    generated.
                  </p>
                </>
              ) : (
                <p className="text-gray-500 dark:text-gray-400 italic">
                  No transcript available yet.
                </p>
              )}
            </div>
          )}
        </div>

        <div
          className={`absolute inset-0 flex flex-col ${activePanel === "notes" ? "z-10 visible" : "z-0 invisible"}`}
        >
          {recording && (
            <NotesView
              recordingId={recording.id}
              notes={recording.transcript?.notes || null}
              onNotesChange={handleNotesChange}
              onGenerateNotes={handleGenerateNotes}
              onFindAndReplace={handleGlobalFindAndReplace}
              onUndo={handleNotesUndo}
              onRedo={handleNotesRedo}
              canUndo={notesHistory.length > 0}
              canRedo={notesFuture.length > 0}
              isGenerating={
                isGeneratingNotes ||
                recording.transcript?.notes_status === "generating"
              }
              onExport={() => setShowExportModal(true)}
            />
          )}
        </div>

        <div
          className={`absolute inset-0 flex flex-col ${activePanel === "documents" ? "z-10 visible" : "z-0 invisible"}`}
        >
          {recording && <DocumentsView recordingId={recording.id} />}
        </div>
      </div>
    </div>
  );

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500">
        Loading...
      </div>
    );
  }

  if (!recording) {
    return null;
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {isInFlightRecording ? (
          <div className="h-full flex-1 min-w-0 overflow-y-auto bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.34),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.26),_transparent_36%),linear-gradient(180deg,_#ffedd5_0%,_#fff7ed_45%,_#ffe4c4_100%)] dark:bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.22),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.18),_transparent_34%),linear-gradient(180deg,_#0b1220_0%,_#0a0f1c_50%,_#0b1220_100%)]">
            <RecordingStatusDisplay
              recording={recording}
              onSaveProcessingNotes={handleProcessingNotesChange}
              onSaveMeetingEdgeFocus={handleMeetingEdgeFocusChange}
              meetingEdgeContextLevel={meetingEdgeContextLevel}
              onSaveMeetingEdgeContextLevel={handleMeetingEdgeContextLevelChange}
              showMeetingEdge={meetingEdgeEnabled}
              onBack={navigateToRecordings}
              showMobileBackButton={isMobile}
            />
          </div>
        ) : isMobile ? (
          <div className="flex h-full flex-1 min-w-0 flex-col bg-white dark:bg-gray-900">
            <div className="min-h-0 flex-1">
              {renderMainContent()}
            </div>

            {!isMobileChatOpen && (
              <div className="pointer-events-none fixed bottom-[calc(env(safe-area-inset-bottom)+1rem)] right-4 z-40">
                <button
                  onClick={() => setIsMobileChatOpen(true)}
                  className="pointer-events-auto inline-flex h-14 w-14 items-center justify-center rounded-full bg-orange-600 text-white shadow-lg shadow-orange-950/20 transition-colors hover:bg-orange-700"
                  title="Open Meeting Chat"
                  aria-label="Open Meeting Chat"
                >
                  <MessageSquare className="h-6 w-6" />
                </button>
              </div>
            )}

            {/* Mobile Chat Full-Screen Modal */}
            {isMobileChatOpen && (
              <div className="fixed inset-0 z-50 flex h-dvh flex-col bg-white animate-in slide-in-from-bottom dark:bg-gray-900">
                <header className="flex shrink-0 items-center justify-between border-b-2 border-gray-200 bg-gray-50 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)] dark:border-gray-800 dark:bg-gray-950">
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                    <MessageSquare className="w-5 h-5 text-orange-500" />
                    Meeting Chat
                  </h2>
                <button
                  onClick={() => setIsMobileChatOpen(false)}
                  className="inline-flex items-center gap-2 rounded-lg px-2 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-200 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white"
                  title="Back to meeting"
                  aria-label="Back to meeting"
                >
                    <ArrowLeft className="h-5 w-5" />
                    <span>Back</span>
                  </button>
                </header>
                <div className="flex-1 min-h-0 flex flex-col overflow-hidden pb-[env(safe-area-inset-bottom)]">
                  <ChatPanel onNotesUpdate={fetchRecording} />
                </div>
              </div>
            )}
          </div>
        ) : (
          <PanelGroup
            direction="horizontal"
            autoSaveId="recording-layout-persistence"
            className="h-full flex-1 min-w-0"
          >
            <Panel defaultSize={75} minSize={30}>
              {renderMainContent()}
            </Panel>

            <PanelResizeHandle className="bg-gray-200 dark:bg-gray-900 border-l border-gray-400 dark:border-gray-800 w-2 hover:bg-orange-500 dark:hover:bg-orange-500 transition-colors flex items-center justify-center group">
              <div className="h-8 w-1 bg-gray-400 dark:bg-gray-600 rounded-full group-hover:bg-white transition-colors" />
            </PanelResizeHandle>

            {/* Sidebar: Stacked Speaker and Chat panels */}
            <Panel defaultSize={25} minSize={20}>
              <PanelGroup
                direction="vertical"
                onLayout={(sizes) => {
                  if (sizes.length === 2) {
                    setChatPanelHeight(sizes[1]);
                  }
                }}
              >
                <Panel defaultSize={100 - chatPanelHeight} minSize={20}>
                  <SpeakerPanel
                    speakers={recording.speakers || []}
                    speakerNameSuggestions={
                      recording.transcript?.speaker_name_suggestions || []
                    }
                    segments={transcriptSegments}
                    onPlaySegment={handlePlaySegment}
                    recordingId={recording.id}
                    speakerColors={speakerColors}
                    onColorChange={handleColorChange}
                    currentTime={currentTime}
                    isPlaying={isPlaying}
                    onPause={handlePause}
                    onResume={handleResume}
                    onRefresh={refreshRecordingView}
                    globalSpeakers={globalSpeakers}
                    onSpeakerRenamed={async (oldName, newName) => {
                      if (recording?.transcript?.notes) {
                          await handleGlobalFindAndReplace(
                            oldName,
                            getAutoSpeakerReplacementName(newName),
                            { caseSensitive: true },
                          );
                      }
                    }}
                  />
                </Panel>

                <PanelResizeHandle className="bg-gray-200 dark:bg-gray-900 border-t border-gray-400 dark:border-gray-800 h-2 hover:bg-orange-500 dark:hover:bg-orange-500 transition-colors flex items-center justify-center group">
                  <div className="w-8 h-1 bg-gray-400 dark:bg-gray-600 rounded-full group-hover:bg-white transition-colors" />
                </PanelResizeHandle>

                <Panel defaultSize={chatPanelHeight} minSize={20}>
                  <ChatPanel onNotesUpdate={fetchRecording} />
                </Panel>
              </PanelGroup>
            </Panel>
          </PanelGroup>
        )}
      </div>

      {/* Export Modal */}
      <ExportModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
        onExport={handleExport}
        hasNotes={!!recording?.transcript?.notes}
      />

      {/* Reprocess Dialog */}
      {recording && (
        <ReprocessDialog
          recordingId={recording.id}
          isOpen={showReprocessDialog}
          onClose={() => setShowReprocessDialog(false)}
          onReprocessed={(updatedRecording) => {
            setRecording(updatedRecording);
            window.dispatchEvent(
              new CustomEvent("recording-updated", {
                detail: { id: updatedRecording.id },
              }),
            );
          }}
        />
      )}
    </div>
  );
}
