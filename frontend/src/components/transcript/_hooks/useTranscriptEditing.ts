"use client";

import { useEffect, useMemo, useState } from "react";
import type { KeyboardEvent } from "react";

import { useNotificationStore } from "@/lib/notificationStore";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import { TranscriptSegment } from "@/types";

import type { TranscriptSearch } from "./useTranscriptSearch";

export interface UseTranscriptEditingOptions {
  segments: TranscriptSegment[];
  readOnly: boolean;
  allowProvisionalEdits: boolean;
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
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
  /**
   * What the popover sits against, and what a click may land on without
   * closing it: the speaker's name, or a menu's button.
   *
   * It must stay mounted while the popover is open, such as a menu's ⋯
   * trigger, never an item inside a menu that closes: the popover is placed
   * against it and would be left pointing at a detached node.
   */
  anchor: HTMLElement;
}

/**
 * What any line, or a menu on it, can start. These keep their identity while
 * `readOnly` and `allowProvisionalEdits` hold, so handing them to every line
 * never redraws one.
 */
export interface TranscriptLineActions {
  /** Opens the line's text in the editor, unless the line is read-only. */
  startTextEdit: (segment: TranscriptSegment, segmentId: string) => void;
  /** Opens the line's speaker popover against `anchor`, or closes it if open. */
  toggleSpeakerPopover: (segmentId: string, anchor: HTMLElement) => void;
  /** Opens the line's speaker popover against `anchor`, even if it is open already. */
  openSpeakerPopover: (segmentId: string, anchor: HTMLElement) => void;
  closeSpeakerPopover: () => void;
  /** Opens the rename of the line's speaker, on that line alone. */
  startSpeakerRename: (segmentId: string, speakerLabel: string, speakerName: string) => void;
}

/** A speaker rename, open on the line it was started from. */
interface SpeakerRename {
  label: string;
  segmentId: string;
  /**
   * The name it opened with, then the name it sent: submitting that again
   * saves nothing.
   */
  savedName: string;
}

/** The open editor's value and handlers, for the lines that show it. */
export interface TranscriptOpenEditor {
  value: string;
  setValue: (value: string) => void;
  handleTextSubmit: (segment: TranscriptSegment) => Promise<void>;
  /** The text editor's keys: Enter saves, Escape closes. */
  handleKeyDown: (e: KeyboardEvent, segment: TranscriptSegment) => void;
  handleSpeakerRenameSubmit: () => Promise<void>;
  cancelSpeakerRename: () => void;
}

export interface TranscriptEditing {
  /**
   * The line a speaker rename was opened from, the only one that shows its
   * input. The new name applies to every line of that speaker.
   */
  editingSpeakerLineId: string | null;
  /** The line whose text is open in the editor. */
  editingTextId: string | null;
  activePopover: SpeakerPopover | null;
  isSubmitting: boolean;
  /** An edit is open or saving: export waits for it. */
  editInProgress: boolean;
  lineActions: TranscriptLineActions;
  editor: TranscriptOpenEditor;
  handleFindReplaceSubmit: () => Promise<void>;
  handleReplaceCurrent: () => Promise<void>;
}

/**
 * The finished transcript's edit state for {@link TranscriptView}: renaming a
 * speaker, editing a line's text, the speaker popover, and the find/replace
 * writes. One edit is open at a time, and `isSubmitting` holds every other
 * write back while one is saving.
 */
export function useTranscriptEditing(
  options: UseTranscriptEditingOptions,
): TranscriptEditing {
  const {
    segments,
    readOnly,
    allowProvisionalEdits,
    onRenameSpeaker,
    onUpdateSegmentText,
    onFindAndReplace,
    onActiveEditUtteranceChange,
    search,
    onFindReplaceDone,
  } = options;
  const { addNotification } = useNotificationStore();

  const [speakerRename, setSpeakerRename] = useState<SpeakerRename | null>(null);
  const [editingTextId, setEditingTextId] = useState<string | null>(null);
  const [activePopover, setActivePopover] = useState<SpeakerPopover | null>(
    null,
  );
  const [editValue, setEditValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const editInProgress = Boolean(speakerRename || editingTextId || isSubmitting);

  useEffect(() => {
    if (!onActiveEditUtteranceChange) {
      return;
    }

    if (!editingTextId) {
      onActiveEditUtteranceChange(null);
      return;
    }

    const activeSegment = segments.find((segment, index) => {
      return getTranscriptSegmentKey(segment, index) === editingTextId;
    });

    onActiveEditUtteranceChange(activeSegment?.id || null);
  }, [editingTextId, onActiveEditUtteranceChange, segments]);

  // Enter and the input's blur both submit, so an unchanged name, or the same
  // name submitted again while it saves, closes the input without a request.
  const handleSpeakerRenameSubmit = async () => {
    const name = editValue.trim();
    if (speakerRename && name && name !== speakerRename.savedName) {
      setSpeakerRename({ ...speakerRename, savedName: name });
      setIsSubmitting(true);
      try {
        await onRenameSpeaker(speakerRename.label, name);
      } finally {
        setIsSubmitting(false);
        setSpeakerRename(null);
      }
    } else {
      setSpeakerRename(null);
    }
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

  const handleKeyDown = (e: KeyboardEvent, segment: TranscriptSegment) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleTextSubmit(segment);
    } else if (e.key === "Escape") {
      setSpeakerRename(null);
      setEditingTextId(null);
    }
  };

  const lineActions = useMemo<TranscriptLineActions>(
    () => ({
      startTextEdit: (segment, segmentId) => {
        if (readOnly || (segment.provisional === true && !allowProvisionalEdits)) return;
        setEditingTextId(segmentId);
        setEditValue(segment.text);
        setSpeakerRename(null);
      },
      toggleSpeakerPopover: (segmentId, anchor) =>
        setActivePopover((open) =>
          open?.segmentId === segmentId ? null : { segmentId, anchor },
        ),
      openSpeakerPopover: (segmentId, anchor) => setActivePopover({ segmentId, anchor }),
      closeSpeakerPopover: () => setActivePopover(null),
      startSpeakerRename: (segmentId, speakerLabel, speakerName) => {
        setSpeakerRename({ label: speakerLabel, segmentId, savedName: speakerName });
        setEditValue(speakerName);
        setActivePopover(null);
      },
    }),
    [readOnly, allowProvisionalEdits],
  );

  return {
    editingSpeakerLineId: speakerRename?.segmentId ?? null,
    editingTextId,
    activePopover,
    isSubmitting,
    editInProgress,
    lineActions,
    editor: {
      value: editValue,
      setValue: setEditValue,
      handleTextSubmit,
      handleKeyDown,
      handleSpeakerRenameSubmit,
      cancelSpeakerRename: () => setSpeakerRename(null),
    },
    handleFindReplaceSubmit,
    handleReplaceCurrent,
  };
}
