"use client";

import { useMemo } from "react";

import {
  archiveRecording,
  deleteRecording,
  discardRecordingCapture,
  inferSpeakers,
  permanentlyDeleteRecording,
  renameRecording,
  restoreRecording,
  softDeleteRecording,
} from "@/lib/api";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";
import { RecordingId } from "@/types";

/**
 * Shared action model for the per-recording menu actions used by both the
 * sidebar list (`Sidebar.tsx`) and the recordings-grid card (`RecordingCard.tsx`).
 *
 * Both surfaces previously duplicated the asynchronous action bodies (rename,
 * infer speakers, cancel, delete, archive, restore, permanent delete) verbatim.
 * DEVELOPMENT.md records that the two menus must stay behaviourally
 * synchronised; this hook is the single source of truth for that behaviour so
 * the duplication cannot drift. {@link RECORDING_ACTION_IDS} pins the shared
 * action set so tests can assert both consumers expose the same actions.
 *
 * The handlers are behaviour-preserving. Each only performs the API call,
 * user-facing notification, and error logging that the two components shared
 * identically. Consumer-specific concerns — optimistic list updates, post-success
 * refresh strategy (`router.refresh()` vs a re-fetch), `recording-updated`
 * events, confirm() prompts, route redirects, and the card's delayed second
 * refresh — stay in the consumers, wired through the per-call callbacks below.
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
}

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
            await cancelCapture(id);
          } else {
            await discardRecordingCapture(id, "user_discarded");
          }
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
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to permanently delete", e);
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
