"use client";

import { useState } from "react";

import {
  BatchVoiceprintResponse,
  RecordingId,
  SpeakerNameSuggestion,
  VoiceprintExtractResult,
} from "@/types";
import {
  acceptSpeakerNameSuggestion,
  deleteRecordingSpeaker,
  extractVoiceprint,
  mergeRecordingSpeakers,
  promoteToGlobalSpeaker,
  rejectSpeakerNameSuggestion,
  updateSpeaker,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorMessage } from "@/lib/errors";

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

  // Suggestions
  resolvingSuggestionId: string | null;
  acceptSuggestion: (suggestion: SpeakerNameSuggestion) => Promise<void>;
  rejectSuggestion: (suggestion: SpeakerNameSuggestion) => Promise<void>;

  isSubmitting: boolean;

  /** Clears the active context menu before opening an inline editor/modal. */
  closeContextMenu: () => void;
}

/**
 * Owns the speaker mutation state and handlers for {@link SpeakerPanel}
 * (FE-012): rename, merge, delete, split, voiceprint extraction, promotion to
 * the global library, and suggestion accept/reject. Lifted verbatim from the
 * component so the side effects, notifications, and `recording-updated`
 * dispatch are unchanged.
 *
 * The caller supplies `closeContextMenu` so the menu-driven actions clear the
 * menu exactly as before.
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
  const [resolvingSuggestionId, setResolvingSuggestionId] = useState<
    string | null
  >(null);

  const [extractingVoiceprint, setExtractingVoiceprint] = useState<
    string | null
  >(null);
  const [voiceprintModalOpen, setVoiceprintModalOpen] = useState(false);
  const [voiceprintExtractResult, setVoiceprintExtractResult] =
    useState<VoiceprintExtractResult | null>(null);
  const [batchVoiceprintResults, setBatchVoiceprintResults] =
    useState<BatchVoiceprintResponse | null>(null);

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

      for (const speaker of renamingSpeaker.members) {
        await updateSpeaker(recordingId, speaker.diarization_label, newName);
      }

      if (oldName !== newName && onSpeakerRenamed) {
        await onSpeakerRenamed(oldName, newName);
      }

      setRenamingSpeaker(null);
      onRefresh();
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
      await promoteToGlobalSpeaker(
        recordingId,
        speakerEntry.speaker.diarization_label,
      );
      onRefresh();
      addNotification({
        type: "success",
        message: "Speaker added to Global Library.",
      });
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

  const acceptSuggestion = async (suggestion: SpeakerNameSuggestion) => {
    setResolvingSuggestionId(suggestion.id);
    try {
      await acceptSpeakerNameSuggestion(
        recordingId,
        suggestion.diarization_label,
      );
      addNotification({
        type: "success",
        message: `Accepted suggestion: ${suggestion.suggested_name}.`,
      });
      onRefresh();
    } catch (e: unknown) {
      console.error("Failed to accept speaker suggestion", e);
      addNotification({
        type: "error",
        message: "Failed to accept speaker suggestion.",
      });
    } finally {
      setResolvingSuggestionId(null);
    }
  };

  const rejectSuggestion = async (suggestion: SpeakerNameSuggestion) => {
    setResolvingSuggestionId(suggestion.id);
    try {
      await rejectSpeakerNameSuggestion(
        recordingId,
        suggestion.diarization_label,
      );
      addNotification({
        type: "success",
        message: `Rejected suggestion for ${suggestion.diarization_label}.`,
      });
      onRefresh();
    } catch (e: unknown) {
      console.error("Failed to reject speaker suggestion", e);
      addNotification({
        type: "error",
        message: "Failed to reject speaker suggestion.",
      });
    } finally {
      setResolvingSuggestionId(null);
    }
  };

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

    resolvingSuggestionId,
    acceptSuggestion,
    rejectSuggestion,

    isSubmitting,

    closeContextMenu,
  };
}
