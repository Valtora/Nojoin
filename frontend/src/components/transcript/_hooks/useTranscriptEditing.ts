"use client";

import { useEffect, useState } from "react";
import type { KeyboardEvent, MouseEvent } from "react";

import { useNotificationStore } from "@/lib/notificationStore";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import { TranscriptSegment, TranscriptSpeakerAssignment } from "@/types";

import type { TranscriptSearch } from "./useTranscriptSearch";

export interface UseTranscriptEditingOptions {
  segments: TranscriptSegment[];
  readOnly: boolean;
  allowProvisionalEdits: boolean;
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
  onUpdateSegmentText: (
    segment: TranscriptSegment,
    text: string,
  ) => void | Promise<void>;
  onFindAndReplace: (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => void | Promise<void>;
  onActiveEditUtteranceChange?: (utteranceId: string | null) => void;
  search: TranscriptSearch;
  /** Called after Replace All completes, to close the find bar. */
  onFindReplaceDone: () => void;
}

export interface SpeakerPopover {
  segmentId: string;
  target: HTMLElement;
}

export interface TranscriptEditing {
  /** The speaker label being renamed everywhere. */
  editingSpeaker: string | null;
  /** The line whose speaker is being retyped. */
  editingSegmentSpeakerId: string | null;
  /** The line whose text is open in the editor. */
  editingTextId: string | null;
  activePopover: SpeakerPopover | null;
  editValue: string;
  setEditValue: (value: string) => void;
  isSubmitting: boolean;
  /** An edit is open or saving: export waits for it. */
  editInProgress: boolean;
  toggleSpeakerPopover: (segmentId: string, target: HTMLElement) => void;
  closeSpeakerPopover: () => void;
  startSpeakerRename: (speakerLabel: string, speakerName: string) => void;
  cancelSpeakerRename: () => void;
  handleSpeakerRenameSubmit: () => Promise<void>;
  handleSegmentSpeakerSubmit: (segment: TranscriptSegment) => Promise<void>;
  handleTextClick: (
    segment: TranscriptSegment,
    segmentId: string,
    e: MouseEvent,
  ) => void;
  handleTextSubmit: (segment: TranscriptSegment) => Promise<void>;
  handleKeyDown: (
    e: KeyboardEvent,
    type: "segmentSpeaker" | "text",
    segment: TranscriptSegment,
  ) => void;
  handleFindReplaceSubmit: () => Promise<void>;
  handleReplaceCurrent: () => Promise<void>;
}

/**
 * The finished transcript's edit state for {@link TranscriptView}: renaming a
 * speaker, retyping one line's speaker, editing a line's text, and the
 * find/replace writes. One edit is open at a time, and `isSubmitting` holds
 * every other write back while one is saving.
 */
export function useTranscriptEditing(
  options: UseTranscriptEditingOptions,
): TranscriptEditing {
  const {
    segments,
    readOnly,
    allowProvisionalEdits,
    onRenameSpeaker,
    onUpdateSegmentSpeaker,
    onUpdateSegmentText,
    onFindAndReplace,
    onActiveEditUtteranceChange,
    search,
    onFindReplaceDone,
  } = options;
  const { addNotification } = useNotificationStore();

  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [editingSegmentSpeakerId, setEditingSegmentSpeakerId] = useState<
    string | null
  >(null);
  const [editingTextId, setEditingTextId] = useState<string | null>(null);
  const [activePopover, setActivePopover] = useState<SpeakerPopover | null>(
    null,
  );
  const [editValue, setEditValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const editInProgress = Boolean(
    editingSpeaker || editingSegmentSpeakerId || editingTextId || isSubmitting,
  );

  useEffect(() => {
    if (!onActiveEditUtteranceChange) {
      return;
    }

    const activeEditKey = editingTextId || editingSegmentSpeakerId;
    if (!activeEditKey) {
      onActiveEditUtteranceChange(null);
      return;
    }

    const activeSegment = segments.find((segment, index) => {
      return getTranscriptSegmentKey(segment, index) === activeEditKey;
    });

    onActiveEditUtteranceChange(activeSegment?.id || null);
  }, [
    editingSegmentSpeakerId,
    editingTextId,
    onActiveEditUtteranceChange,
    segments,
  ]);

  const toggleSpeakerPopover = (segmentId: string, target: HTMLElement) => {
    if (activePopover?.segmentId === segmentId) {
      setActivePopover(null);
    } else {
      setActivePopover({ segmentId, target });
    }
  };

  const closeSpeakerPopover = () => setActivePopover(null);

  const startSpeakerRename = (speakerLabel: string, speakerName: string) => {
    setEditingSpeaker(speakerLabel);
    setEditValue(speakerName);
    setActivePopover(null);
  };

  const cancelSpeakerRename = () => setEditingSpeaker(null);

  const handleSpeakerRenameSubmit = async () => {
    if (editingSpeaker && editValue.trim()) {
      setIsSubmitting(true);
      try {
        await onRenameSpeaker(editingSpeaker, editValue.trim());
      } finally {
        setIsSubmitting(false);
        setEditingSpeaker(null);
      }
    } else {
      setEditingSpeaker(null);
    }
  };

  const handleSegmentSpeakerSubmit = async (segment: TranscriptSegment) => {
    if (editValue.trim() && !isSubmitting) {
      setIsSubmitting(true);
      try {
        await onUpdateSegmentSpeaker(segment, {
          name: editValue.trim(),
          scope: "utterance_only",
        });
      } finally {
        setIsSubmitting(false);
        setEditingSegmentSpeakerId(null);
      }
    } else if (!editValue.trim()) {
      setEditingSegmentSpeakerId(null);
    }
  };

  const handleTextClick = (
    segment: TranscriptSegment,
    segmentId: string,
    e: MouseEvent,
  ) => {
    e.stopPropagation();
    if (readOnly || (segment.provisional === true && !allowProvisionalEdits)) return;
    setEditingTextId(segmentId);
    setEditValue(segment.text);
    setEditingSpeaker(null);
    setEditingSegmentSpeakerId(null);
  };

  const handleTextSubmit = async (segment: TranscriptSegment) => {
    if (editValue !== segment.text && !isSubmitting) {
      setIsSubmitting(true);
      try {
        await onUpdateSegmentText(segment, editValue);
      } finally {
        setIsSubmitting(false);
        setEditingTextId(null);
      }
    } else {
      setEditingTextId(null);
    }
  };

  const handleFindReplaceSubmit = async () => {
    const { findText, replaceText, caseSensitive, useRegex } = search;
    if (!findText || isSubmitting) return;

    if (findText.length > 1000) {
      addNotification({
        type: 'warning',
        message: 'Search pattern is too long (max 1000 characters).',
      });
      return;
    }

    setIsSubmitting(true);
    try {
      await onFindAndReplace(findText, replaceText, {
        caseSensitive,
        useRegex,
      });
      search.resetFindReplace();
      onFindReplaceDone();
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReplaceCurrent = async () => {
    const { matches, currentMatchIndex, replaceText } = search;
    if (matches.length === 0 || currentMatchIndex === -1 || isSubmitting)
      return;

    const match = matches[currentMatchIndex];
    const segment = segments[match.orderIndex];
    if (!segment) {
      return;
    }

    const prefix = segment.text.substring(0, match.startIndex);
    const suffix = segment.text.substring(match.startIndex + match.length);
    const newText = prefix + replaceText + suffix;

    setIsSubmitting(true);
    try {
      await onUpdateSegmentText(segment, newText);
    } catch (e: unknown) {
      console.error("Failed to replace text", e);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleKeyDown = (
    e: KeyboardEvent,
    type: "segmentSpeaker" | "text",
    segment: TranscriptSegment,
  ) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (type === "segmentSpeaker") {
        handleSegmentSpeakerSubmit(segment);
      } else if (type === "text") {
        handleTextSubmit(segment);
      }
    } else if (e.key === "Escape") {
      setEditingSpeaker(null);
      setEditingSegmentSpeakerId(null);
      setEditingTextId(null);
    }
  };

  return {
    editingSpeaker,
    editingSegmentSpeakerId,
    editingTextId,
    activePopover,
    editValue,
    setEditValue,
    isSubmitting,
    editInProgress,
    toggleSpeakerPopover,
    closeSpeakerPopover,
    startSpeakerRename,
    cancelSpeakerRename,
    handleSpeakerRenameSubmit,
    handleSegmentSpeakerSubmit,
    handleTextClick,
    handleTextSubmit,
    handleKeyDown,
    handleFindReplaceSubmit,
    handleReplaceCurrent,
  };
}
