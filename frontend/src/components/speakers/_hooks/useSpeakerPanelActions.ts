"use client";

import { useState } from "react";

import {
  BatchVoiceprintResponse,
  MergedRecordingSpeaker,
  RecordingId,
  VoiceprintExtractResult,
} from "@/types";
import {
  deleteRecordingSpeaker,
  extractVoiceprint,
  mergeRecordingSpeakers,
  promoteToGlobalSpeaker,
  unmergeRecordingSpeaker,
  updateSpeaker,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorMessage } from "@/lib/errors";
import { libraryVoiceprintNotices } from "@/lib/libraryVoiceprint";
import type { SpeakerNameDecision } from "@/lib/speakerNameReview";
import { useSpeakerNameReview } from "@/lib/useSpeakerNameReview";

import type { SpeakerPanelEntry } from "./useSpeakerPanelEntries";

export interface UseSpeakerPanelActionsOptions {
  recordingId: RecordingId;
  onRefresh: () => void;
  onSpeakerRenamed?: (oldName: string, newName: string) => Promise<void> | void;
}

export interface SpeakerPanelActions {
  // Rename
  renamingSpeaker: SpeakerPanelEntry | null;
  setRenamingSpeaker: (entry: SpeakerPanelEntry | null) => void;
  renameValue: string;
  setRenameValue: (value: string) => void;
  startRename: (entry: SpeakerPanelEntry) => void;
  submitRename: () => Promise<void>;

  // Merge
  mergingSpeaker: SpeakerPanelEntry | null;
  setMergingSpeaker: (entry: SpeakerPanelEntry | null) => void;
  mergeTargetLabel: string;
  setMergeTargetLabel: (label: string) => void;
  startMerge: (entry: SpeakerPanelEntry) => void;
  submitMerge: () => Promise<void>;

  // Delete
  deletingSpeaker: SpeakerPanelEntry | null;
  setDeletingSpeaker: (entry: SpeakerPanelEntry | null) => void;
  requestDelete: (entry: SpeakerPanelEntry) => void;
  confirmDelete: () => Promise<void>;

  // Split
  splitModalOpen: boolean;
  setSplitModalOpen: (open: boolean) => void;
  speakerToSplit: SpeakerPanelEntry | null;
  setSpeakerToSplit: (entry: SpeakerPanelEntry | null) => void;
  startSplit: (entry: SpeakerPanelEntry) => void;

  // Voiceprint
  extractingVoiceprint: string | null;
  voiceprintModalOpen: boolean;
  setVoiceprintModalOpen: (open: boolean) => void;
  voiceprintExtractResult: VoiceprintExtractResult | null;
  setVoiceprintExtractResult: (result: VoiceprintExtractResult | null) => void;
  batchVoiceprintResults: BatchVoiceprintResponse | null;
  setBatchVoiceprintResults: (result: BatchVoiceprintResponse | null) => void;
  createVoiceprint: (entry: SpeakerPanelEntry) => Promise<void>;

  // Promote to global
  promoteToGlobal: (entry: SpeakerPanelEntry) => Promise<void>;

  /** Give a cluster processing merged into this speaker back its own lines. */
  undoMerge: (cluster: MergedRecordingSpeaker) => Promise<void>;

  // Suggested names
  /** A confirm or dismiss of a suggested name is in flight. */
  isReviewingName: boolean;
  reviewName: (entry: SpeakerPanelEntry, decision: SpeakerNameDecision) => Promise<void>;
  /** Confirms every entry's suggested name, with one notification. */
  confirmAllNames: (entries: readonly SpeakerPanelEntry[]) => Promise<void>;

  isSubmitting: boolean;

  /** Clears the active context menu before opening an inline editor/modal. */
  closeContextMenu: () => void;
}

/**
 * Owns the speaker mutation state and handlers for {@link SpeakerPanel}:
 * rename, merge, delete, split, voiceprint extraction, promotion to the
 * global library, and confirming or dismissing suggested names, along with
 * their notifications and the `recording-updated` dispatch.
 *
 * The caller supplies `closeContextMenu` so the menu-driven actions clear the
 * menu before opening their editor or modal.
 */
export function useSpeakerPanelActions(
  options: UseSpeakerPanelActionsOptions & { closeContextMenu: () => void },
): SpeakerPanelActions {
  const { recordingId, onRefresh, onSpeakerRenamed, closeContextMenu } = options;
  const { addNotification } = useNotificationStore();

  const [renamingSpeaker, setRenamingSpeaker] =
    useState<SpeakerPanelEntry | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const [mergingSpeaker, setMergingSpeaker] =
    useState<SpeakerPanelEntry | null>(null);
  const [mergeTargetLabel, setMergeTargetLabel] = useState("");

  const [deletingSpeaker, setDeletingSpeaker] =
    useState<SpeakerPanelEntry | null>(null);

  const [isSubmitting, setIsSubmitting] = useState(false);

  const [extractingVoiceprint, setExtractingVoiceprint] = useState<
    string | null
  >(null);
  const [voiceprintModalOpen, setVoiceprintModalOpen] = useState(false);
  const [voiceprintExtractResult, setVoiceprintExtractResult] =
    useState<VoiceprintExtractResult | null>(null);
  const [batchVoiceprintResults, setBatchVoiceprintResults] =
    useState<BatchVoiceprintResponse | null>(null);

  const nameReview = useSpeakerNameReview({ recordingId, onRefresh });

  const [splitModalOpen, setSplitModalOpen] = useState(false);
  const [speakerToSplit, setSpeakerToSplit] =
    useState<SpeakerPanelEntry | null>(null);

  const startRename = (speaker: SpeakerPanelEntry) => {
    setRenamingSpeaker(speaker);
    setRenameValue(speaker.displayName);
    closeContextMenu();
  };

  const startMerge = (speaker: SpeakerPanelEntry) => {
    setMergingSpeaker(speaker);
    setMergeTargetLabel("");
    closeContextMenu();
  };

  const startSplit = (speaker: SpeakerPanelEntry) => {
    setSpeakerToSplit(speaker);
    setSplitModalOpen(true);
    closeContextMenu();
  };

  const submitRename = async () => {
    if (!renamingSpeaker || !renameValue.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      const oldName = renamingSpeaker.displayName;
      const newName = renameValue.trim();

      const renamed = [];
      for (const speaker of renamingSpeaker.members) {
        renamed.push(
          ...(await updateSpeaker(recordingId, speaker.diarization_label, newName)),
        );
      }

      if (oldName !== newName && onSpeakerRenamed) {
        await onSpeakerRenamed(oldName, newName);
      }

      setRenamingSpeaker(null);
      onRefresh();
      for (const message of libraryVoiceprintNotices(renamed)) {
        addNotification({ type: "info", message });
      }
    } catch (e: unknown) {
      console.error("Failed to rename speaker", e);
      addNotification({ type: "error", message: "Failed to rename speaker." });
    } finally {
      setIsSubmitting(false);
    }
  };

  const submitMerge = async () => {
    if (!mergingSpeaker || !mergeTargetLabel || isSubmitting) return;

    setIsSubmitting(true);
    try {
      for (const label of mergingSpeaker.labels) {
        if (label === mergeTargetLabel) {
          continue;
        }

        await mergeRecordingSpeakers(recordingId, mergeTargetLabel, label);
      }
      setMergingSpeaker(null);

      // Dispatch custom event to notify parent components of the merge
      window.dispatchEvent(
        new CustomEvent("recording-updated", { detail: { recordingId } }),
      );

      onRefresh();
    } catch (e: unknown) {
      console.error("Failed to merge speakers", e);
      addNotification({ type: "error", message: "Failed to merge speakers." });
    } finally {
      setIsSubmitting(false);
    }
  };

  const requestDelete = (speaker: SpeakerPanelEntry) => {
    closeContextMenu();
    setDeletingSpeaker(speaker);
  };

  const confirmDelete = async () => {
    if (!deletingSpeaker) return;

    setIsSubmitting(true);
    try {
      for (const label of deletingSpeaker.labels) {
        await deleteRecordingSpeaker(recordingId, label);
      }
      setDeletingSpeaker(null);
      onRefresh();
    } catch (e: unknown) {
      console.error("Failed to delete speaker", e);
      addNotification({ type: "error", message: "Failed to delete speaker." });
    } finally {
      setIsSubmitting(false);
    }
  };

  const createVoiceprint = async (speakerEntry: SpeakerPanelEntry) => {
    closeContextMenu();
    setExtractingVoiceprint(speakerEntry.speaker.diarization_label);

    try {
      const result = await extractVoiceprint(
        recordingId,
        speakerEntry.speaker.diarization_label,
      );
      setVoiceprintExtractResult(result);
      setBatchVoiceprintResults(null);
      setVoiceprintModalOpen(true);
    } catch (e: unknown) {
      console.error("Failed to extract voiceprint", e);
      addNotification({
        type: "error",
        message: getErrorMessage(e, "Failed to extract voiceprint."),
      });
    } finally {
      setExtractingVoiceprint(null);
    }
  };

  const promoteToGlobal = async (speakerEntry: SpeakerPanelEntry) => {
    closeContextMenu();
    setIsSubmitting(true);

    try {
      const promoted = await promoteToGlobalSpeaker(
        recordingId,
        speakerEntry.speaker.diarization_label,
      );
      onRefresh();
      addNotification({
        type: "success",
        message: "Speaker added to Global Library.",
      });
      for (const message of libraryVoiceprintNotices([promoted])) {
        addNotification({ type: "info", message });
      }
    } catch (e: unknown) {
      console.error("Failed to promote speaker", e);
      addNotification({
        type: "error",
        message: getErrorMessage(
          e,
          "Failed to promote speaker to global library.",
        ),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const undoMerge = async (cluster: MergedRecordingSpeaker) => {
    if (isSubmitting) return;
    setIsSubmitting(true);

    try {
      await unmergeRecordingSpeaker(recordingId, cluster.diarization_label);
      window.dispatchEvent(
        new CustomEvent("recording-updated", { detail: { recordingId } }),
      );
      onRefresh();
      addNotification({
        type: "success",
        message: `Gave ${describeLineCount(cluster.restorable_utterance_count)} back to ${cluster.diarization_label}.`,
      });
    } catch (e: unknown) {
      console.error("Failed to undo the speaker merge", e);
      addNotification({
        type: "error",
        message: getErrorMessage(e, "Failed to undo the speaker merge."),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const pendingNameOf = (entry: SpeakerPanelEntry) =>
    entry.pendingName ? { labels: entry.pendingName.labels, name: entry.displayName } : null;

  const reviewName = async (entry: SpeakerPanelEntry, decision: SpeakerNameDecision) => {
    const pending = pendingNameOf(entry);
    if (pending) await nameReview.review(pending, decision);
  };

  const confirmAllNames = (entries: readonly SpeakerPanelEntry[]) =>
    nameReview.confirmAll(
      entries.flatMap((entry) => {
        const pending = pendingNameOf(entry);
        return pending ? [pending] : [];
      }),
    );

  return {
    renamingSpeaker,
    setRenamingSpeaker,
    renameValue,
    setRenameValue,
    startRename,
    submitRename,

    mergingSpeaker,
    setMergingSpeaker,
    mergeTargetLabel,
    setMergeTargetLabel,
    startMerge,
    submitMerge,

    deletingSpeaker,
    setDeletingSpeaker,
    requestDelete,
    confirmDelete,

    splitModalOpen,
    setSplitModalOpen,
    speakerToSplit,
    setSpeakerToSplit,
    startSplit,

    extractingVoiceprint,
    voiceprintModalOpen,
    setVoiceprintModalOpen,
    voiceprintExtractResult,
    setVoiceprintExtractResult,
    batchVoiceprintResults,
    setBatchVoiceprintResults,
    createVoiceprint,

    promoteToGlobal,

    undoMerge,

    isReviewingName: nameReview.isReviewing,
    reviewName,
    confirmAllNames,

    isSubmitting,

    closeContextMenu,
  };
}

/** "1 line" or "12 lines". */
export function describeLineCount(count: number): string {
  return count === 1 ? "1 line" : `${count} lines`;
}
