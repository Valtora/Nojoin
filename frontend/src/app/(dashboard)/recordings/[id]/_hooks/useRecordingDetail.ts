import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";

import {
  getSettings,
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
  updateMeetingEdgeFocus,
  exportContent,
  type ExportSelection,
} from "@/lib/api";
import {
  clampMeetingEdgeContextLevel,
  DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
} from "@/lib/meetingEdgeContext";
import { getErrorDetail, getErrorMessage, getErrorStatus, isAbortError } from "@/lib/errors";
import { notesErrorDetail } from "@/lib/transcriptErrors";
import { subscribeRecordingRemoved } from "@/lib/recordingEvents";
import {
  Recording,
  TranscriptSegment,
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSpeakerAssignment,
} from "@/types";
import { useNotificationStore } from "@/lib/notificationStore";
import { useSessionStore } from "@/lib/session";
import { libraryVoiceprintNotices } from "@/lib/libraryVoiceprint";
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
  sortTranscriptSegments,
} from "@/lib/transcriptSegments";
import { useDragSelectionLock } from "@/lib/useDragSelectionLock";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import { AudioClock } from "@/lib/notes/audioClock";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import { useRecordingNotes } from "@/lib/notes/useNoteOutbox";
import { useViewportDensity } from "@/components/ViewportDensityProvider";
import { useRecordingActions } from "@/components/recordings/_hooks/useRecordingActions";

import {
  cloneTranscriptSegments,
  hasPolledRecordingChanged,
  isAudioUnavailable,
  isRecordingInFlight,
  noteFilesBeingRead,
  pollFailureMeansRemoved,
  recordingPollIntervalMs,
  shouldPollRecordingUpdates,
} from "./recordingDetailUtils";
import { useRecordingNotFound } from "./useRecordingNotFound";
import { useRecordingTranscript } from "./useRecordingTranscript";
import { createRecordingMetadataReader } from "./recordingMetadataReader";

interface UseRecordingDetailParams {
  params: Promise<{ id: string }>;
  capture?: { recordingId: string | null; status: string };
}

// A quiet page still notices a capture started from another tab or device.
export const IDLE_RECORDING_POLL_INTERVAL_MS = 15_000;

/**
 * Owns the entire data-orchestration, live-state, transcript, notes and action
 * logic for the recording detail page, so the page component itself stays
 * focused on rendering.
 */
export function useRecordingDetail({ params, capture }: UseRecordingDetailParams) {
  const [recording, setRecording] = useState<Recording | null>(null);
  // Fed every read of the recording, changed or not: `recording` ignores
  // `live_audio_ms`, which changes on every poll.
  const [audioClock] = useState(() => new AudioClock());
  const metadataReader = useMemo(() => createRecordingMetadataReader(audioClock, params), [audioClock, params]);
  useEffect(() => {
    metadataReader.activate();
    return () => metadataReader.cancel();
  }, [metadataReader]);
  // Fetched when `timeline_notes_list_signature` changes; null while loading.
  // The note box's outbox is kept per recording, here and in every view.
  const timelineNotes = useRecordingNotes(
    recording,
    recording ? shouldPollRecordingUpdates(recording) : false,
  );
  // Edits and deletes of saved notes, shared by the transcript and My notes.
  const noteEdits = useNoteEdits(recording?.id ?? null);
  // A note's file switched on keeps the recording polled until it is read.
  const filesBeingRead = noteFilesBeingRead(timelineNotes);
  const [globalSpeakers, setGlobalSpeakers] = useState<GlobalSpeaker[]>([]);
  const [meetingEdgeEnabled, setMeetingEdgeEnabled] = useState(true);
  const [meetingEdgeContextLevel, setMeetingEdgeContextLevel] = useState(
    DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
  );
  const [loading, setLoading] = useState(true);
  useOnDepsChange([params], () => {
    setRecording(null);
    setLoading(true);
  });
  const router = useRouter();
  const audioRef = useRef<HTMLAudioElement>(null);
  const { addNotification } = useNotificationStore();
  const recordingActions = useRecordingActions();
  const { chatPanelHeight, setChatPanelHeight, activePanel, setActivePanel } = useNavigationStore();

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


  // Mobile State. `isMobile` comes from the shared viewport provider, the single
  // source of truth for the 1024px boundary.
  const [isMobileChatOpen, setIsMobileChatOpen] = useState(false);
  const [isMobileHeaderActionsOpen, setIsMobileHeaderActionsOpen] = useState(false);
  const [isPanelResizing, setIsPanelResizing] = useState(false);
  const { isCompact, isMobile } = useViewportDensity();
  useDragSelectionLock(isPanelResizing);

  // Notes History (separate from transcript history, can include null values)
  const [notesHistory, setNotesHistory] = useState<(string | null)[]>([]);
  const [notesFuture, setNotesFuture] = useState<(string | null)[]>([]);
  // The notes error already showing when a recording opens is not news: only a
  // change after that raises a toast. Keyed by recording so each one primes.
  const lastNotesErrorRef = useRef<{ recordingId: string; error: string | null } | null>(null);
  const lastMeetingEdgeErrorRef = useRef<string | null>(null);
  // When the page first saw the audio as unavailable. It bounds how long the
  // page keeps re-checking for it.
  const audioUnavailableSinceRef = useRef<number | null>(null);
  const isInFlightRecording = isRecordingInFlight(recording);
  const localCaptureActive = !!recording && capture?.recordingId === recording.id &&
    (capture.status === "recording" || capture.status === "paused" || capture.status === "finalizing");
  const canPlayback = !!recording && !isInFlightRecording && !localCaptureActive;
  const canExport = canPlayback;
  const capabilityRef = useRef({ recordingId: recording?.id, canPlayback, canExport });
  useEffect(() => {
    capabilityRef.current = { recordingId: recording?.id, canPlayback, canExport };
  }, [recording?.id, canPlayback, canExport]);
  const {
    transcriptStateRef, setTranscriptState, transcriptSegments, deferredTranscriptUtteranceIds,
    transcriptSpeakerNames, transcriptSpeakers, liveTranscript, usesInFlightTranscript, transcriptIsAuthoritative,
    history, future, isUndoing, syncTranscriptState, repairTranscriptSequence, pushTranscriptHistory,
    setActiveTranscriptEditId, handleUndo, handleRedo,
  } = useRecordingTranscript(recording, setRecording, localCaptureActive);
  const { refresh: refreshInFlightTranscript } = liveTranscript;
  const compactChatPanelHeight = isCompact
    ? Math.min(chatPanelHeight, 42)
    : chatPanelHeight;
  const navigateToRecordings = useCallback(() => {
    router.push("/recordings");
  }, [router]);
  const recordingNotFound = useRecordingNotFound(addNotification, navigateToRecordings);

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

  const fetchRecording = useCallback(async () => {
    let id: string | null = null;
    try {
      ({ id } = await params);
      const speakersAndSettings = Promise.all([getGlobalSpeakers().catch(() => []), getSettings().catch(() => null)]);
      const recData = await metadataReader.read(id);
      if (!recData || !metadataReader.isActive()) return null;
      recordingNotFound.loaded(id);
      recordingNotFound.found();
      setRecording(recData);
      void speakersAndSettings.then(([gsData, settingsData]) => {
        if (!metadataReader.isActive()) return;
        setGlobalSpeakers(gsData);
        if (settingsData) {
          setMeetingEdgeEnabled(settingsData.enable_meeting_edge !== false);
          setMeetingEdgeContextLevel(clampMeetingEdgeContextLevel(settingsData.meeting_edge_context_level));
        }
      });
      // Only set title if not editing, or on first load
      if (!isEditingTitle) {
        setTitleValue(recData.name);
      }
      return recData;

        } catch (e: unknown) {
      if (!metadataReader.isActive()) return null;
      console.error("Failed to fetch recording:", e);
      // A refresh of the recording on screen answered 404: checked as a
      // poll's is, so another account's sign-in doesn't send the page away.
      if (id !== null && recordingNotFound.isLoaded(id) && getErrorStatus(e) === 404) {
        if (!useSessionStore.getState().signInRequired) await recordingNotFound.notFound(id);
        return null;
      }
      addNotification({
        type: "error",
        message: "Failed to load recording.",
      });
      router.push("/recordings");
      return null;
    } finally {
      if (metadataReader.isActive()) setLoading(false);
    }
  }, [addNotification, metadataReader, params, isEditingTitle, router, recordingNotFound]);

  const refreshRecordingView = useCallback(async () => {
    const refreshedRecording = await fetchRecording();

    if (!refreshedRecording?.id || isRecordingInFlight(refreshedRecording)) {
      if (refreshedRecording?.id) refreshInFlightTranscript();
      return;
    }

    await syncTranscriptState("full").catch((e) => {
      console.error("Failed to refresh transcript state:", e);
    });
  }, [fetchRecording, syncTranscriptState, refreshInFlightTranscript]);

  useEffect(() => {
    fetchRecording();
  }, [fetchRecording]);

  useEffect(() => {
    if (!isMobile) {
      setIsMobileHeaderActionsOpen(false);
    }
  }, [isMobile]);

  // The "speakers", "my-notes" and "captures" panels exist only on mobile,
  // where they have tabs; desktop shows them in the side column. Leaving
  // mobile while one is active would blank the main content, so fall back to
  // the transcript.
  useEffect(() => {
    if (
      !isMobile &&
      (activePanel === "speakers" || activePanel === "my-notes" || activePanel === "captures")
    ) {
      setActivePanel("transcript");
    }
  }, [isMobile, activePanel, setActivePanel]);

  useEffect(() => {
    if (!recording) return;

    if (!isAudioUnavailable(recording)) {
      audioUnavailableSinceRef.current = null;
    } else if (audioUnavailableSinceRef.current === null) {
      audioUnavailableSinceRef.current = Date.now();
    }
    const currentPollIntervalMs = () =>
      recordingPollIntervalMs(
        recording,
        audioUnavailableSinceRef.current === null
          ? 0
          : Date.now() - audioUnavailableSinceRef.current,
        filesBeingRead,
      ) ?? (isAudioUnavailable(recording) ? null : IDLE_RECORDING_POLL_INTERVAL_MS);

    const pollIntervalMs = currentPollIntervalMs();
    if (pollIntervalMs === null) {
      return;
    }

    const interval = setInterval(async () => {
      if (currentPollIntervalMs() === null) {
        clearInterval(interval);
        return;
      }
      try {
        const { id } = await params;
        const data = await metadataReader.read(id);
        if (!data) return;
        recordingNotFound.found();

        if (hasPolledRecordingChanged(recording, data)) {
          setRecording(data);
          if (!isEditingTitle) setTitleValue(data.name);
        }
        await repairTranscriptSequence(data);

            } catch (e: unknown) {
        if (!metadataReader.isActive()) return;
        console.error("Polling failed", e);
        if (
          !pollFailureMeansRemoved(
            getErrorStatus(e),
            !useSessionStore.getState().signInRequired,
          )
        ) {
          return;
        }
        const { id } = await params;
        await recordingNotFound.notFound(id);
      }
    }, pollIntervalMs);

    return () => clearInterval(interval);
  }, [params, recording, isEditingTitle, metadataReader, recordingNotFound, filesBeingRead, repairTranscriptSequence]);

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

  // Discarding the open recording deletes it, so this page has nothing left to
  // show. Leaving centrally means every discard surface gets the redirect.
  useEffect(
    () =>
      subscribeRecordingRemoved((removedId) => {
        if (recording && removedId === recording.id) {
          navigateToRecordings();
        }
      }),
    [navigateToRecordings, recording],
  );

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
    // speakerColors is read so existing assignments survive, and it is what
    // this effect writes: listing it would re-run the effect after every write.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording, transcriptSegments]);

  useEffect(() => {
    if (!recording) return;
    const notesError =
      recording.transcript?.notes_status === "error"
        ? notesErrorDetail(recording.transcript) ||
          "Meeting notes could not be generated. Configure an AI provider and model in Settings, then try again."
        : null;

    const last = lastNotesErrorRef.current;
    lastNotesErrorRef.current = { recordingId: recording.id, error: notesError };
    if (!last || last.recordingId !== recording.id) return;
    if (notesError && notesError !== last.error) {
      addNotification({ type: "error", message: notesError });
    }
  }, [addNotification, recording]);

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
    if (!capabilityRef.current.canPlayback || capabilityRef.current.recordingId !== recording?.id) return;
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
    if (!capabilityRef.current.canPlayback || capabilityRef.current.recordingId !== recording?.id) return;
    if (audioRef.current) {
      audioRef.current.play();
    }
  };

  // Transcript Handlers
  const handleRenameSpeaker = async (label: string, newName: string): Promise<boolean> => {
    if (!recording) return false;
    let accepted = false;
    try {
      const renamed = await updateSpeaker(recording.id, label, newName);
      accepted = true;
      const updated = await metadataReader.read(recording.id);
      if (!updated) return true;
      setRecording(updated);
      await syncTranscriptState("full");
      for (const message of libraryVoiceprintNotices(renamed)) {
        addNotification({ type: "info", message });
      }
      return true;
    } catch (error: unknown) {
      console.error("Failed to rename speaker:", error);
      addNotification({
        type: accepted ? "warning" : "error",
        message: accepted ? "Speaker renamed, but the recording could not be refreshed."
          : "Failed to rename speaker. Please try again.",
      });
      return accepted;
    }
  };

  const handleUpdateSegmentSpeaker = async (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ): Promise<boolean> => {
    if (!recording) return false;

    const previousSegments = cloneTranscriptSegments(transcriptSegments);

    const nextAssignment = {
      ...assignment,
      name: assignment.name.trim(),
      scope: assignment.scope || "speaker_everywhere_in_recording",
    };

    if (!nextAssignment.name) {
      return false;
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
      return true;
    }

    let accepted = false;
    try {
      if (segment.id) {
        await updateTranscriptUtteranceSpeaker(
          recording.id,
          segment.id,
          nextAssignment,
          segment.revision,
        );
        accepted = true;
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
          return false;
        }

        await updateTranscriptSegmentSpeaker(
          recording.id,
          segmentIndex,
          nextAssignment,
        );
        accepted = true;
        const updated = await metadataReader.read(recording.id);
        if (!updated) return true;
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
      return true;
    } catch (error: unknown) {
      console.error("Failed to update segment speaker:", error);
      addNotification({
        type: accepted ? "warning" : "error",
        message: accepted ? "Speaker changed, but the transcript could not be refreshed."
          : getErrorDetail(error) ?? "Failed to update segment speaker. Please try again.",
      });
      return accepted;
    }
  };

  const handleUpdateSegmentText = async (
    segment: TranscriptSegment,
    text: string,
  ): Promise<boolean> => {
    if (!recording) return false;

    const previousSegments = cloneTranscriptSegments(transcriptSegments);

    let accepted = false;
    try {
      if (segment.id) {
        await updateTranscriptUtteranceText(
          recording.id,
          segment.id,
          text,
          segment.revision,
        );
        accepted = true;
        const syncResult = await syncTranscriptState("delta", {
          deferActiveEdit: false,
        });
        if (syncResult?.segments) {
          // The undo step holds this line alone. The sync can bring other
          // changes with it, such as a save or Replace All running beside
          // this one, and undoing this edit must not revert those.
          pushTranscriptHistory(
            `Edit text ${segment.id}`,
            previousSegments.filter((previous) => previous.id === segment.id),
            syncResult.segments,
          );
        }
        return true;
      }

      const segmentIndex = transcriptSegments.indexOf(segment);
      if (segmentIndex === -1) {
        return false;
      }

      await updateTranscriptSegmentText(recording.id, segmentIndex, text);
      accepted = true;
      const updated = await metadataReader.read(recording.id);
      if (!updated) return true;
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
      return true;
    } catch (error: unknown) {
      console.error("Failed to update segment text:", error);
      addNotification({ type: accepted ? "warning" : "error", message: accepted
        ? "Text saved, but the transcript could not be refreshed."
        : getErrorDetail(error) ?? "Failed to update segment text." });
      return accepted;
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

      await syncTranscriptState("full");
      const updated = await metadataReader.read(recording.id);
      if (updated) setRecording(updated);

        } catch (error: unknown) {
      console.error("Failed to find and replace:", error);
      addNotification({ type: "error", message: getErrorDetail(error) ?? "Failed to find and replace." });
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
      const updated = await metadataReader.read(recording.id);
      if (!updated) return;
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
        const updated = await metadataReader.read(recording.id);
        if (!updated) return;
        setRecording(updated);

            } catch (e: unknown) {
        console.error("Failed to update speaker color", e);
        addNotification({ type: "error", message: "Failed to update speaker color." });
      }
    }
  };

  // Notes Handlers
  const handleGenerateNotes = async (notesTemplateId?: number | null) => {
    if (!recording || !capabilityRef.current.canExport || capabilityRef.current.recordingId !== recording.id) return;
    setIsGeneratingNotes(true);
    try {
      // Undefined means "use my default"; an id regenerates with that structure
      // for this run only, without changing the user's default.
      await generateNotes(recording.id, notesTemplateId ?? null);
      const updated = await metadataReader.read(recording.id);
      if (!updated) return;
      setRecording(updated);
      setActivePanel("notes"); // Switch to notes panel after generation

        } catch (e: unknown) {
      console.error("Failed to generate notes:", e);
      try {
        const updated = await metadataReader.read(recording.id);
        if (!updated) return;
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
      const updated = await metadataReader.read(recording.id);
      if (!updated) return;
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
      const updated = await metadataReader.read(recording.id);
      if (!updated) return;
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
      const updated = await metadataReader.read(recording.id);
      if (!updated) return;
      setRecording(updated);

        } catch (e: unknown) {
      console.error("Notes redo failed:", e);
      addNotification({ type: "error", message: "Failed to redo notes changes." });
    }
  };

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

  const handleExport = async (selection: ExportSelection) => {
    if (!recording || !capabilityRef.current.canExport || capabilityRef.current.recordingId !== recording.id) return;
    if (selection.kind === "audio") {
      // Reports its own failures, with what went wrong.
      await recordingActions.exportAudio(recording.id, recording.name);
      return;
    }
    try {
      await exportContent(
        recording.id,
        selection.sections,
        selection.format,
        speakerColors,
        selection.includeNotes,
      );
    } catch (error: unknown) {
      console.error("Export failed:", error);
      addNotification({
        type: "error",
        message: "Export failed. Please check the logs.",
      });
    }
  };

  const speakerMap = useMemo(() => {
    const speakers = buildRecordingSpeakerDisplayMap(transcriptSpeakers ? [...transcriptSpeakers] : undefined, globalSpeakerById);
    // A live label has no speaker of the recording, so no name is replaced.
    return { ...transcriptSpeakerNames, ...speakers };
  }, [globalSpeakerById, transcriptSpeakers, transcriptSpeakerNames]);

  return {
    // Core data
    recording,
    setRecording,
    audioClock,
    timelineNotes,
    noteEdits,
    globalSpeakers,
    loading,
    meetingEdgeEnabled,
    meetingEdgeContextLevel,
    // Derived
    isInFlightRecording,
    canPlayback,
    canExport,
    liveTranscript,
    usesInFlightTranscript,
    transcriptIsAuthoritative,
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
    handleMeetingEdgeFocusChange,
    handleMeetingEdgeContextLevelChange,
    handleExport,
    setActiveTranscriptEditId,
  };
}
