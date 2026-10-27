"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorDetail } from "@/lib/errors";
import { TranscriptSegment } from "@/types";
import { lineIsSettling, LINE_SETTLING_REASON } from "@/lib/transcript/settling";

import type { TranscriptSearch } from "./useTranscriptSearch";

export interface UseTranscriptEditingOptions {
  segments: TranscriptSegment[];
  readOnly: boolean;
  allowProvisionalEdits: boolean;
  bulkEditsDisabled?: boolean;
  onRenameSpeaker: (label: string, newName: string) => void | boolean | Promise<void | boolean>;
  onUpdateSegmentText: (
    segment: TranscriptSegment,
    text: string,
  ) => void | boolean | Promise<void | boolean>;
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
   * The value it opened with, then the last value accepted by the server.
   * A refused value remains retryable.
   */
  saved: string;
  /** The mutation route selected when this editor opened. */
  submit: (value: string) => void | boolean | Promise<void | boolean>;
} & ({
  kind: "text";
  segment: TranscriptSegment;
  update: UseTranscriptEditingOptions["onUpdateSegmentText"];
  /** Only a reader snapshot matching an accepted write can advance its revision. */
  acceptedText: string | null;
} | { kind: "rename"; label: string });

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
    bulkEditsDisabled = false,
    onRenameSpeaker,
    onUpdateSegmentText,
    onFindAndReplace,
    onActiveEditUtteranceChange,
    search,
    onFindReplaceDone,
  } = options;
  const { addNotification } = useNotificationStore();

  const [edit, setEdit] = useState<OpenEdit | null>(null);
  if (edit?.kind === "text" && edit.acceptedText !== null && edit.segment.id) {
    const observed = segments.find((segment) => segment.id === edit.segment.id);
    if (observed && observed.text === edit.acceptedText && observed.revision !== undefined &&
      edit.segment.revision !== undefined && observed.revision > edit.segment.revision) {
      // Adjust this component's state during render, before the next event can
      // submit. A refused write or unrelated poll never supplies this evidence.
      const update = edit.update;
      setEdit({ ...edit, segment: observed, acceptedText: null, submit: (text) => update(observed, text) });
    }
  }
  const [activePopover, setActivePopover] = useState<SpeakerPopover | null>(
    null,
  );
  const [savesInFlight, setSavesInFlight] = useState(0);
  const pending = useRef(new Map<symbol, Set<string>>());
  const mutationCallbacks = useRef({ onRenameSpeaker, onUpdateSegmentText });
  useLayoutEffect(() => {
    mutationCallbacks.current = { onRenameSpeaker, onUpdateSegmentText };
  }, [onRenameSpeaker, onUpdateSegmentText]);

  const editingTextId = edit?.kind === "text" ? edit.segmentId : null;
  const editingUtteranceId = edit?.kind === "text" ? edit.segment.id || null : null;
  const isSubmitting = savesInFlight > 0;
  const editInProgress = edit !== null || isSubmitting;

  useEffect(() => {
    onActiveEditUtteranceChange?.(editingUtteranceId);
  }, [editingUtteranceId, onActiveEditUtteranceChange]);

  const trackSave = async <T,>(save: () => T | Promise<T>): Promise<T> => {
    setSavesInFlight((count) => count + 1);
    try {
      return await save();
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
  ) => {
    const requests = pending.current.get(open.key) ?? new Set<string>();
    if (requests.has(value)) return;
    requests.add(value);
    pending.current.set(open.key, requests);
    try {
      const accepted = await trackSave(() => open.submit(value));
      if (accepted !== false) {
        setEdit((now) => {
          if (now?.key !== open.key) return now;
          return valueToSave(now) === value ? null : {
            ...now, saved: value, ...(now.kind === "text" ? { acceptedText: value } : {}),
          };
        });
      }
    } catch (error) {
      console.error("Failed to save transcript changes", error);
      addNotification({ type: "error", message: getErrorDetail(error) ?? "Failed to save transcript changes." });
    } finally {
      requests.delete(value);
      if (requests.size === 0) pending.current.delete(open.key);
    }
  };

  // Enter and blur both submit. A pending duplicate does nothing; an empty
  // or accepted unchanged name closes without another request.
  const handleSpeakerRenameSubmit = async () => {
    if (edit?.kind !== "rename") return;
    const name = valueToSave(edit);
    if (pending.current.get(edit.key)?.has(name)) return;
    if (!name || name === edit.saved) {
      setEdit(null);
      return;
    }
    await saveFromEditor(edit, name);
  };

  // The same for a line's text, which may be empty. It is compared with the
  // text the editor opened with, not the line's text now: a line reopened
  // while its last save runs opens on the old text, and once that save lands,
  // comparing with the line would send the old text back.
  const handleTextSubmit = async () => {
    if (edit?.kind !== "text") return;
    const text = valueToSave(edit);
    if (pending.current.get(edit.key)?.has(text)) return;
    if (text === edit.saved) {
      setEdit(null);
      return;
    }
    await saveFromEditor(edit, text);
  };

  const handleFindReplaceSubmit = async () => {
    const { findText, replaceText, caseSensitive, useRegex } = search;
    if (!findText || isSubmitting || readOnly || bulkEditsDisabled) return;

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
    if (matches.length === 0 || currentMatchIndex === -1 || isSubmitting || readOnly || bulkEditsDisabled)
      return;

    const match = matches[currentMatchIndex];
    const segment = segments[match.orderIndex];
    if (!segment) {
      return;
    }
    if (lineIsSettling(segment)) {
      addNotification({ type: "warning", message: LINE_SETTLING_REASON });
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

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleTextSubmit();
    } else if (e.key === "Escape") {
      setEdit(null);
    }
  };

  const lineActions = useMemo<TranscriptLineActions>(
    () => ({
      startTextEdit: (segment, segmentId) => {
        if (readOnly || lineIsSettling(segment) ||
          (segment.provisional === true && !allowProvisionalEdits && !segment.settling_required)) return;
        const update = mutationCallbacks.current.onUpdateSegmentText;
        setEdit({
          key: Symbol("text edit"),
          kind: "text",
          segmentId,
          value: segment.text,
          saved: segment.text,
          segment,
          update,
          acceptedText: null,
          submit: (text) => update(segment, text),
        });
      },
      toggleSpeakerPopover: (segmentId, anchor) =>
        setActivePopover((open) =>
          open?.segmentId === segmentId ? null : { segmentId, anchor },
        ),
      openSpeakerPopover: (segmentId, anchor) => setActivePopover({ segmentId, anchor }),
      closeSpeakerPopover: () => setActivePopover(null),
      startSpeakerRename: (segmentId, speakerLabel, speakerName) => {
        const rename = mutationCallbacks.current.onRenameSpeaker;
        setEdit({
          key: Symbol("speaker rename"),
          kind: "rename",
          label: speakerLabel,
          segmentId,
          value: speakerName,
          saved: speakerName,
          submit: (name) => rename(speakerLabel, name),
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
