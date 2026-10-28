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

/** The one edit open: a line's text, or a rename of the line's speaker. */
type OpenEdit = {
  /** Tells this edit from one opened later on the same line. */
  key: symbol;
  /** The line it was opened on. */
  segmentId: string;
  value: string;
  /**
   * The value it opened with, then the value it sent: submitting that again
   * saves nothing.
   */
  saved: string;
} & ({ kind: "text" } | { kind: "rename"; label: string });

/** What a submit would save: a name is trimmed, a line's text is kept as typed. */
const valueToSave = (edit: OpenEdit) =>
  edit.kind === "rename" ? edit.value.trim() : edit.value;

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
  /** A save is in flight: Replace and Replace All are held back until none is. */
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
 * writes. One edit is open at a time, but saves run side by side: a line or a
 * name can be saved while another save runs, and Replace and Replace All are
 * held back until none is running.
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

  const [edit, setEdit] = useState<OpenEdit | null>(null);
  const [activePopover, setActivePopover] = useState<SpeakerPopover | null>(
    null,
  );
  const [savesInFlight, setSavesInFlight] = useState(0);

  const editingTextId = edit?.kind === "text" ? edit.segmentId : null;
  const isSubmitting = savesInFlight > 0;
  const editInProgress = edit !== null || isSubmitting;

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

  const trackSave = async (save: () => void | Promise<void>) => {
    setSavesInFlight((count) => count + 1);
    try {
      await save();
    } finally {
      setSavesInFlight((count) => count - 1);
    }
  };

  // The editor stays open while it saves, showing what was sent. It closes
  // then only if it is still the editor the save came from and still holds
  // that value, so an edit opened or typed meanwhile stays open.
  const saveFromEditor = async (
    open: OpenEdit,
    value: string,
    save: () => void | Promise<void>,
  ) => {
    setEdit({ ...open, saved: value });
    try {
      await trackSave(save);
    } finally {
      setEdit((now) =>
        now?.key === open.key && valueToSave(now) === value ? null : now,
      );
    }
  };

  // Enter and the input's blur both submit, so an empty or unchanged name, or
  // the same name submitted again while it saves, closes the input without a
  // request.
  const handleSpeakerRenameSubmit = async () => {
    if (edit?.kind !== "rename") return;
    const name = valueToSave(edit);
    if (!name || name === edit.saved) {
      setEdit(null);
      return;
    }
    const { label } = edit;
    await saveFromEditor(edit, name, () => onRenameSpeaker(label, name));
  };

  // The same for a line's text, which may be empty. It is compared with the
  // text the editor opened with, not the line's text now: a line reopened
  // while its last save runs opens on the old text, and once that save lands,
  // comparing with the line would send the old text back.
  const handleTextSubmit = async (segment: TranscriptSegment) => {
    if (edit?.kind !== "text") return;
    const text = valueToSave(edit);
    if (text === edit.saved) {
      setEdit(null);
      return;
    }
    await saveFromEditor(edit, text, () => onUpdateSegmentText(segment, text));
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

    await trackSave(async () => {
      await onFindAndReplace(findText, replaceText, {
        caseSensitive,
        useRegex,
      });
      search.resetFindReplace();
      onFindReplaceDone();
    });
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

    await trackSave(async () => {
      try {
        await onUpdateSegmentText(segment, newText);
      } catch (e: unknown) {
        console.error("Failed to replace text", e);
      }
    });
  };

  const handleKeyDown = (e: KeyboardEvent, segment: TranscriptSegment) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleTextSubmit(segment);
    } else if (e.key === "Escape") {
      setEdit(null);
    }
  };

  const lineActions = useMemo<TranscriptLineActions>(
    () => ({
      startTextEdit: (segment, segmentId) => {
        if (readOnly ||
          (segment.provisional === true && !allowProvisionalEdits && !segment.settling_required)) return;
        setEdit({
          key: Symbol("text edit"),
          kind: "text",
          segmentId,
          value: segment.text,
          saved: segment.text,
        });
      },
      toggleSpeakerPopover: (segmentId, anchor) =>
        setActivePopover((open) =>
          open?.segmentId === segmentId ? null : { segmentId, anchor },
        ),
      openSpeakerPopover: (segmentId, anchor) => setActivePopover({ segmentId, anchor }),
      closeSpeakerPopover: () => setActivePopover(null),
      startSpeakerRename: (segmentId, speakerLabel, speakerName) => {
        setEdit({
          key: Symbol("speaker rename"),
          kind: "rename",
          label: speakerLabel,
          segmentId,
          value: speakerName,
          saved: speakerName,
        });
        setActivePopover(null);
      },
    }),
    [readOnly, allowProvisionalEdits],
  );

  return {
    editingSpeakerLineId: edit?.kind === "rename" ? edit.segmentId : null,
    editingTextId,
    activePopover,
    isSubmitting,
    editInProgress,
    lineActions,
    editor: {
      value: edit?.value ?? "",
      setValue: (value) => setEdit((open) => (open ? { ...open, value } : open)),
      handleTextSubmit,
      handleKeyDown,
      handleSpeakerRenameSubmit,
      cancelSpeakerRename: () => setEdit(null),
    },
    handleFindReplaceSubmit,
    handleReplaceCurrent,
  };
}
