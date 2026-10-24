"use client";

import { useMemo } from "react";

import {
  archiveRecording,
  deleteRecording,
  discardRecordingCapture,
  exportAudio,
  inferSpeakers,
  permanentlyDeleteRecording,
  renameRecording,
  reprocessRecording,
  restoreRecording,
  setRecordingTranscriptionLanguage,
  softDeleteRecording,
} from "@/lib/api";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { getErrorStatus } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import { forgetRecordingNotes } from "@/lib/notes/noteOutboxes";
import { dispatchRecordingRemoved } from "@/lib/recordingEvents";
import { LanguageNotice, RecordingId } from "@/types";

/**
 * Shared action model for the per-recording actions used by the sidebar list
 * (`Sidebar.tsx`), the live view (`RecordingStatusDisplay.tsx`) and the
 * recording page (`useRecordingDetail.ts`, for the audio export, and
 * `LanguageNoticeBanner.tsx`, for the language notice's answers).
 *
 * DEVELOPMENT.md requires these surfaces to stay behaviourally synchronised,
 * and this hook is the single source of truth for that behaviour.
 * {@link RECORDING_ACTION_IDS} pins the shared action set so tests can assert
 * every consumer draws from the same one.
 *
 * Each handler performs only the API call, the user-facing notification and
 * the error logging. Consumer-specific concerns (optimistic list updates, the
 * post-success refresh strategy, `recording-updated` events, confirm()
 * prompts and route redirects) stay in the consumers, wired through the
 * per-call callbacks below.
 */

/** Stable identifiers for the recording actions exposed by this hook. */
export const RECORDING_ACTION_IDS = [
  "rename",
  "inferSpeakers",
  "discard",
  "delete",
  "archive",
  "restore",
  "softDelete",
  "permanentDelete",
  "exportAudio",
  "setTranscriptionLanguage",
  "reprocessInLanguage",
] as const;

export type RecordingActionId = (typeof RECORDING_ACTION_IDS)[number];

/** Hooks a consumer can inject to run its own bookkeeping around an action. */
export interface RecordingActionCallbacks {
  /** Runs after the API call resolves successfully. */
  onSuccess?: () => void;
  /** Runs when the API call rejects, after the error notification is shown. */
  onError?: () => void;
}

export interface RecordingActions {
  rename: (
    id: RecordingId,
    name: string,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  inferSpeakers: (
    id: RecordingId,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  discard: (
    id: RecordingId,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  delete: (
    id: RecordingId,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  archive: (
    id: RecordingId,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  restore: (
    id: RecordingId,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  softDelete: (
    id: RecordingId,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  permanentDelete: (
    id: RecordingId,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  exportAudio: (
    id: RecordingId,
    name: string,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  /** Sets the recording's language without reprocessing ("Keep English"). */
  setTranscriptionLanguage: (
    id: RecordingId,
    language: string | null,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
  /**
   * Reprocesses in the notice's language, with the engine and Whisper model
   * that produced the transcript, setting the recording's language first.
   */
  reprocessInLanguage: (
    id: RecordingId,
    notice: Pick<LanguageNotice, "reprocess_language" | "engine" | "model">,
    callbacks?: RecordingActionCallbacks,
  ) => Promise<void>;
}

/** What to tell the person when the audio export did not produce a file. */
export const audioExportFailureMessage = (error: unknown): string => {
  switch (getErrorStatus(error)) {
    case 202:
      return "The audio is still being prepared. Try exporting it again shortly.";
    case 404:
      return "This recording's audio is not available, so there is nothing to export.";
    default:
      return "Failed to export audio.";
  }
};

export function useRecordingActions(): RecordingActions {
  const { addNotification } = useNotificationStore();
  const {
    cancel: cancelCapture,
    recordingId: captureRecordingId,
    pausedRecording,
    runtimeActive,
  } = useCapture();

  return useMemo<RecordingActions>(() => {
    return {
      rename: async (id, name, callbacks) => {
        try {
          await renameRecording(id, name);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to rename recording", e);
          addNotification({
            message: "Failed to rename recording.",
            type: "error",
          });
          callbacks?.onError?.();
        }
      },

      inferSpeakers: async (id, callbacks) => {
        try {
          await inferSpeakers(id);
          addNotification({
            message:
              "Speaker inference started. Review the suggested names when they are ready.",
            type: "success",
          });
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to infer speakers", e);
          addNotification({
            message: "Failed to infer speakers.",
            type: "error",
          });
          callbacks?.onError?.();
        }
      },

      discard: async (id, callbacks) => {
        try {
          // If this browser owns the live or paused capture for this recording,
          // route through the capture controller so the MediaRecorder, uploader,
          // and persisted paused context/capture lock are torn down before (and
          // as part of) the backend discard. A bare POST /discard would delete
          // the row while the client kept recording to a missing recording or
          // stayed blocked behind a stale paused lock. For a queued/processing
          // recording, or one owned by another tab, a plain discard is correct.
          const ownsLiveCapture =
            runtimeActive && captureRecordingId === id;
          const ownsPausedCapture = pausedRecording?.id === id;
          if (ownsLiveCapture || ownsPausedCapture) {
            // The controller announces the removal itself once the capture is
            // torn down, so this branch must not announce it a second time.
            await cancelCapture(id);
          } else {
            await discardRecordingCapture(id, "user_discarded");
            dispatchRecordingRemoved(id);
          }
          // Notes typed during the meeting can't be saved to it any more.
          void forgetRecordingNotes(id);
          addNotification({
            message: "Recording discarded.",
            type: "success",
          });
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to discard recording", e);
          addNotification({
            message: "Failed to discard recording.",
            type: "error",
          });
          callbacks?.onError?.();
        }
      },

      delete: async (id, callbacks) => {
        try {
          await deleteRecording(id);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to delete recording", e);
          addNotification({
            message: "Failed to delete recording.",
            type: "error",
          });
          callbacks?.onError?.();
        }
      },

      archive: async (id, callbacks) => {
        try {
          await archiveRecording(id);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to archive", e);
          callbacks?.onError?.();
        }
      },

      restore: async (id, callbacks) => {
        try {
          await restoreRecording(id);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to restore", e);
          callbacks?.onError?.();
        }
      },

      softDelete: async (id, callbacks) => {
        try {
          await softDeleteRecording(id);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to delete", e);
          callbacks?.onError?.();
        }
      },

      permanentDelete: async (id, callbacks) => {
        try {
          await permanentlyDeleteRecording(id);
          void forgetRecordingNotes(id);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to permanently delete", e);
          callbacks?.onError?.();
        }
      },

      exportAudio: async (id, name, callbacks) => {
        try {
          await exportAudio(id, name);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to export audio", e);
          addNotification({
            message: audioExportFailureMessage(e),
            type: "error",
          });
          callbacks?.onError?.();
        }
      },

      setTranscriptionLanguage: async (id, language, callbacks) => {
        try {
          await setRecordingTranscriptionLanguage(id, language);
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to set the recording's language", e);
          addNotification({
            message: "Failed to set the recording's language.",
            type: "error",
          });
          callbacks?.onError?.();
        }
      },

      reprocessInLanguage: async (id, notice, callbacks) => {
        try {
          await reprocessRecording(id, {
            transcription_backend: notice.engine,
            transcription_language: notice.reprocess_language,
            whisper_model_size: notice.model ?? undefined,
          });
          addNotification({
            message: "Recording reprocessing started.",
            type: "success",
          });
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to reprocess recording", e);
          addNotification({
            message: "Failed to start reprocessing. Please try again.",
            type: "error",
          });
          callbacks?.onError?.();
        }
      },
    };
  }, [
    addNotification,
    cancelCapture,
    captureRecordingId,
    pausedRecording,
    runtimeActive,
  ]);
}
