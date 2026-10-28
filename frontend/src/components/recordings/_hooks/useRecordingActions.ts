"use client";

import { useMemo } from "react";
import { transcriptLineActions, type TranscriptRecordingActions } from "./transcriptLineActions";

import {
  batchAddTagToRecordings,
  batchArchiveRecordings,
  batchPermanentlyDeleteRecordings,
  batchRemoveTagFromRecordings,
  batchRestoreRecordings,
  batchSoftDeleteRecordings,
  deleteRecording,
  discardRecordingCapture,
  exportAudio,
  inferSpeakers,
  renameRecording,
  reprocessRecording,
  setRecordingTranscriptionLanguage,
  type BatchRecordingResult,
} from "@/lib/api";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { getErrorDetail, getErrorStatus } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import { forgetRecordingNotes } from "@/lib/notes/noteOutboxes";
import { dispatchRecordingRemoved } from "@/lib/recordingEvents";
import { LanguageNotice, RecordingId } from "@/types";

import {
  NOTHING_TO_UNDO,
  RESULT_TEXT,
  UNDO_FAILED,
  UNDO_NOTIFICATION_MS,
  describeResult,
  movedBackText,
  tagResultText,
  untaggedText,
  type ResultText,
} from "../recordingListActions";

/**
 * Shared action model for the per-recording actions used by the sidebar list
 * (`Sidebar.tsx`), the header's capture slot (`RecordingCaptureSlot.tsx`) and the
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
 *
 * The list actions (archive, unarchive, softDelete, restore, permanentDelete
 * and tag) take a list of ids, so one row and a selection go through the same
 * batch route and say the same thing. Their notification counts what the
 * server changed, and every one but permanentDelete offers Undo, which
 * reverses exactly the recordings the server reported.
 */

/** Stable identifiers for the recording actions exposed by this hook. */
export const RECORDING_ACTION_IDS = [
  "rename",
  "inferSpeakers",
  "discard",
  "delete",
  "archive",
  "unarchive",
  "restore",
  "softDelete",
  "permanentDelete",
  "tag",
  "exportAudio",
  "setTranscriptionLanguage",
  "reprocessInLanguage",
  "editTranscriptLine",
  "assignTranscriptLine",
  "splitTranscriptLine",
] as const;

export type RecordingActionId = (typeof RECORDING_ACTION_IDS)[number];

/** Hooks a consumer can inject to run its own bookkeeping around an action. */
export interface RecordingActionCallbacks {
  /** Runs after the API call resolves successfully. */
  onSuccess?: () => void;
  /** Runs when the API call rejects, after the error notification is shown. */
  onError?: () => void;
}

/** Callbacks for the list actions, which act on several recordings at once. */
export interface RecordingListActionCallbacks {
  /** Runs once the server has answered, with what it changed. */
  onSuccess?: (result: BatchRecordingResult) => void;
  /** Runs when the API call rejects, after the error notification is shown. */
  onError?: () => void;
  /** Runs once an Undo from the notification has been answered. */
  onUndone?: () => void;
}

export interface MoveToBinCallbacks extends RecordingListActionCallbacks {
  /** The view the recordings leave, which an Undo returns them to. */
  from?: "recordings" | "archived";
}

export interface RecordingActions extends TranscriptRecordingActions {
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
    ids: RecordingId[],
    callbacks?: RecordingListActionCallbacks,
  ) => Promise<void>;
  /** Back from Archived to Recordings. */
  unarchive: (
    ids: RecordingId[],
    callbacks?: RecordingListActionCallbacks,
  ) => Promise<void>;
  /** Back from the bin to Recordings, wherever the recordings were binned from. */
  restore: (
    ids: RecordingId[],
    callbacks?: RecordingListActionCallbacks,
  ) => Promise<void>;
  /** Moves the recordings to the bin. */
  softDelete: (
    ids: RecordingId[],
    callbacks?: MoveToBinCallbacks,
  ) => Promise<void>;
  /** Deletes for good the recordings still in the bin. Confirming is the caller's. */
  permanentDelete: (
    ids: RecordingId[],
    callbacks?: RecordingListActionCallbacks,
  ) => Promise<void>;
  tag: (
    ids: RecordingId[],
    tagName: string,
    callbacks?: RecordingListActionCallbacks,
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

interface ListActionOptions {
  callbacks?: RecordingListActionCallbacks;
  /**
   * Reverses what the server changed, given those ids, and returns what to
   * say about it; null for an action that cannot be undone.
   */
  undo: ((changed: RecordingId[]) => Promise<UndoOutcome>) | null;
}

interface UndoOutcome {
  type: "success" | "warning";
  message: string;
}

/** What to say once an undo has been answered, from the count it reversed. */
const undone = (count: number, say: (count: number) => string): UndoOutcome =>
  count === 0
    ? { type: "warning", message: NOTHING_TO_UNDO }
    : { type: "success", message: say(count) };

export function useRecordingActions(): RecordingActions {
  const { addNotification } = useNotificationStore();
  const {
    cancel: cancelCapture,
    recordingId: captureRecordingId,
    pausedRecording,
    runtimeActive,
  } = useCapture();

  return useMemo<RecordingActions>(() => {
    const runUndo = async (
      undo: NonNullable<ListActionOptions["undo"]>,
      changed: RecordingId[],
      callbacks?: RecordingListActionCallbacks,
    ) => {
      try {
        addNotification(await undo(changed));
      } catch (e: unknown) {
        console.error("Failed to undo", e);
        addNotification({ type: "error", message: getErrorDetail(e) ?? UNDO_FAILED });
      }
      callbacks?.onUndone?.();
    };

    const runListAction = async (
      ids: RecordingId[],
      call: () => Promise<BatchRecordingResult>,
      text: ResultText,
      { callbacks, undo }: ListActionOptions,
    ) => {
      if (ids.length === 0) return;
      let result: BatchRecordingResult;
      try {
        result = await call();
      } catch (e: unknown) {
        console.error(text.failed(ids.length), e);
        addNotification({
          type: "error",
          message: getErrorDetail(e) ?? text.failed(ids.length),
        });
        callbacks?.onError?.();
        return;
      }
      const { type, message } = describeResult(text, result);
      const changed = result.recording_ids;
      const undoable = undo !== null && changed.length > 0;
      addNotification({
        type,
        message,
        ...(undoable
          ? {
              durationMs: UNDO_NOTIFICATION_MS,
              actions: [
                {
                  label: "Undo",
                  onClick: () => void runUndo(undo, changed, callbacks),
                },
              ],
            }
          : {}),
      });
      callbacks?.onSuccess?.(result);
    };

    return {
      ...transcriptLineActions((message) => addNotification({ type: "error", message })),
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

      archive: (ids, callbacks) =>
        runListAction(ids, () => batchArchiveRecordings(ids), RESULT_TEXT.archive, {
          callbacks,
          undo: async (changed) =>
            undone((await batchRestoreRecordings(changed)).count, (n) =>
              movedBackText(n, "Recordings"),
            ),
        }),

      unarchive: (ids, callbacks) =>
        runListAction(ids, () => batchRestoreRecordings(ids), RESULT_TEXT.unarchive, {
          callbacks,
          undo: async (changed) =>
            undone((await batchArchiveRecordings(changed)).count, (n) =>
              movedBackText(n, "Archived"),
            ),
        }),

      restore: (ids, callbacks) =>
        runListAction(ids, () => batchRestoreRecordings(ids), RESULT_TEXT.restore, {
          callbacks,
          undo: async (changed) =>
            undone((await batchSoftDeleteRecordings(changed)).count, (n) =>
              movedBackText(n, "the bin"),
            ),
        }),

      softDelete: (ids, callbacks) =>
        runListAction(ids, () => batchSoftDeleteRecordings(ids), RESULT_TEXT.softDelete, {
          callbacks,
          undo: async (changed) => {
            // A restore always lands in Recordings, so a recording binned
            // from Archived is archived again to put it back where it was.
            const restored = await batchRestoreRecordings(changed);
            if (callbacks?.from !== "archived") {
              return undone(restored.count, (n) => movedBackText(n, "Recordings"));
            }
            const archived = await batchArchiveRecordings(restored.recording_ids);
            return undone(archived.count, (n) => movedBackText(n, "Archived"));
          },
        }),

      permanentDelete: (ids, callbacks) =>
        runListAction(
          ids,
          async () => {
            const result = await batchPermanentlyDeleteRecordings(ids);
            // Notes kept for them can't be saved to them any more.
            for (const id of result.recording_ids) void forgetRecordingNotes(id);
            return result;
          },
          RESULT_TEXT.permanentDelete,
          { callbacks, undo: null },
        ),

      tag: (ids, tagName, callbacks) =>
        runListAction(ids, () => batchAddTagToRecordings(ids, tagName), tagResultText(tagName), {
          callbacks,
          undo: async (changed) =>
            undone((await batchRemoveTagFromRecordings(changed, tagName)).count, (n) =>
              untaggedText(n, tagName),
            ),
        }),

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
