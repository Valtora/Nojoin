"use client";

import { useMemo } from "react";

import {
  archiveRecording,
  cancelProcessing,
  deleteRecording,
  inferSpeakers,
  permanentlyDeleteRecording,
  renameRecording,
  restoreRecording,
  softDeleteRecording,
} from "@/lib/api";
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
  "cancel",
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
  cancel: (
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

      cancel: async (id, callbacks) => {
        try {
          await cancelProcessing(id);
          addNotification({
            message: "Processing cancelled.",
            type: "success",
          });
          callbacks?.onSuccess?.();
        } catch (e: unknown) {
          console.error("Failed to cancel processing", e);
          addNotification({
            message: "Failed to cancel processing.",
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
  }, [addNotification]);
}
